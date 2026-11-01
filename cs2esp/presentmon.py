"""
Optional, non-blocking PresentMon capture of CS2 display intervals.
The signed Console executable runs with its own random ETW session; it does not request elevation, install a service or touch other ETW sessions.
The capture runs continuously and is stopped by targeting only its own session.
"""
from __future__ import annotations

import csv
import math
import os
from pathlib import Path
import subprocess
import threading
from collections import deque
from time import perf_counter
from uuid import uuid4


_PRESENTMON = Path(__file__).resolve().parent / "assets" / "bin" / "presentmon.exe"
_RUN_SECONDS = 120  # Fail-safe if the parent is killed without cleanup
_TTL_SECONDS = 2.5
_WINDOW_SECONDS = 1.5
_MAX_SAMPLES = 512


def _number(value: object, *, low: float, high: float) -> float | None:
	try:
		number = float(value)
	except (TypeError, ValueError):
		return None
	return number if math.isfinite(number) and low <= number <= high else None


class PresentMonSampler:
	"""Capture one process at a time; never block the render loop on ETW."""
	def __init__(self, executable: Path | None = None) -> None:
		self.executable = Path(executable) if executable is not None else _PRESENTMON
		self._disabled = os.environ.get("CS2ESP_DISABLE_PRESENTMON") == "1"
		self._lock = threading.Lock()
		self._wake = threading.Event()
		self._stop = threading.Event()
		self._thread: threading.Thread | None = None
		self._pid = 0
		self._generation = 0
		self._samples: deque[tuple[float, str, float, float | None]] = deque(maxlen=_MAX_SAMPLES)
		self._child: subprocess.Popen | None = None
		self._session: str | None = None

	def set_pid(self, pid: int | None) -> None:
		pid = max(0, int(pid or 0))
		with self._lock:
			if pid == self._pid:
				return
			self._pid = pid
			self._generation += 1
			self._samples.clear()
			if pid and not self._disabled and self._thread is None and not self._stop.is_set() and self.executable.is_file():
				self._thread = threading.Thread(target=self._run, name="presentmon", daemon=True)
				self._thread.start()
		self._wake.set()

	def _record(self, row: dict, pid: int, generation: int) -> None:
		try:
			if int(row.get("ProcessID", 0)) != pid:
				return
		except (TypeError, ValueError):
			return
		# Only a valid display-change interval counts for displayed FPS; the
		# independently optional present-to-display field may itself be NA
		display_ms = _number(row.get("MsBetweenDisplayChange"), low=0.01, high=10_000.0)
		until_ms = _number(row.get("MsUntilDisplayed"), low=0.0, high=10_000.0)
		chain = row.get("SwapChainAddress")
		if display_ms is None or not chain:
			return
		with self._lock:
			if not self._stop.is_set() and self._pid == pid and self._generation == generation:
				self._samples.append((perf_counter(), chain, display_ms, until_ms))

	def sample(self) -> tuple[float | None, float | None]:
		'''Return displayed FPS and present-to-display ms, or None if stale.'''
		now = perf_counter()
		with self._lock:
			samples = list(self._samples)
		if not samples or now - samples[-1][0] > _TTL_SECONDS:
			return None, None
		recent = [item for item in samples if now - item[0] <= _WINDOW_SECONDS]
		if not recent:
			return None, None
		chains = {item[1] for item in recent}
		# Without an authoritative HWND-to-swapchain mapping, choosing one among parallel displayed chains could misreport a different window
		if len(chains) != 1:
			return None, None
		selected = next(iter(chains))
		frames = [item for item in recent if item[1] == selected]
		if len(frames) < 3:
			return None, None
		elapsed_ms = sum(item[2] for item in frames)
		latencies = [item[3] for item in frames if item[3] is not None]
		return 1000.0 * len(frames) / elapsed_ms, (sum(latencies) / len(latencies) if latencies else None)

	def _run(self) -> None:
		while not self._stop.is_set():
			with self._lock:
				pid, generation = self._pid, self._generation
			if not pid:
				self._wake.wait(0.25)
				self._wake.clear()
				continue
			session = "CS2ESP_" + uuid4().hex
			command = [str(self.executable), "--process_id", str(pid), "--session_name", session,
					   "--timed", str(_RUN_SECONDS), "--terminate_after_timed", "--output_stdout",
					   "--no_console_stats", "--no_track_input", "--no_track_gpu", "--v1_metrics", "--terminate_on_proc_exit"]
			proc = None
			reader = None
			retry_error = False
			try:
				proc = subprocess.Popen(command, stdin=subprocess.DEVNULL, stdout=subprocess.PIPE, stderr=subprocess.DEVNULL, text=True, encoding="utf-8-sig",
										errors="replace", bufsize=1, creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
				with self._lock:
					self._child, self._session = proc, session
				assert proc.stdout is not None
				def read_rows(stream=proc.stdout, source_pid=pid, source_generation=generation) -> None:
					try:
						for row in csv.DictReader(stream):
							self._record(row, source_pid, source_generation)
					except (OSError, ValueError, csv.Error):
						pass
				reader = threading.Thread(target=read_rows, name="presentmon-reader", daemon=True)
				reader.start()
				while not self._stop.is_set():
					with self._lock:
						current = self._pid == pid and self._generation == generation
					if not current or proc.poll() is not None or not reader.is_alive():
						break
					self._wake.wait(0.2)
					self._wake.clear()
				if proc.poll() is None:
					self._stop_own_session(session)
				try:
					proc.wait(timeout=3.0)
				except subprocess.TimeoutExpired:
					# The ETW stop was requested above; only our own child is
					# forcibly closed if it did not respond
					proc.terminate()
					proc.wait(timeout=1.0)
					self._stop_own_session(session)
				if proc.returncode not in (0, None):
					self._stop_own_session(session)
					retry_error = True
			except (OSError, subprocess.SubprocessError, csv.Error, ValueError):
				# Keep stale data out of the HUD; engine FPS remains available
				retry_error = True
			finally:
				if proc is not None:
					try:
						if proc.poll() is None:
							self._stop_own_session(session)
							proc.wait(timeout=3.0)
						if reader is not None:
							reader.join(timeout=1.0)
						if proc.stdout is not None:
							proc.stdout.close()
					except (OSError, subprocess.SubprocessError):
						self._terminate_own_session(proc, session)
				with self._lock:
					self._child, self._session = None, None
			if retry_error and not self._stop.is_set():
				# A denied ETW session must not create a process-spawn storm
				self._wake.wait(5.0)
				self._wake.clear()
		with self._lock:
			self._samples.clear()

	def close(self) -> None:
		'''Stop the collector and its uniquely named ETW session.'''
		self._stop.set()
		self._wake.set()
		thread = self._thread
		if thread is None:
			return
		thread.join(timeout=5.0)
		if thread.is_alive():
			with self._lock:
				child, session = self._child, self._session
			if child is not None:
				self._terminate_own_session(child, session)
			thread.join(timeout=1.0)

	def _terminate_own_session(self, child: subprocess.Popen, session: str | None) -> None:
		if session:
			self._stop_own_session(session)
		try:
			child.terminate()
			child.wait(timeout=1.0)
		except (OSError, subprocess.SubprocessError):
			pass
		if session:
			self._stop_own_session(session)

	def _stop_own_session(self, session: str) -> None:
		# Only the session name generated by this instance is targeted
		try:
			subprocess.run([str(self.executable), "--session_name", session, "--terminate_existing_session"], stdin=subprocess.DEVNULL,
						   stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, timeout=3.0, creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
		except (OSError, subprocess.SubprocessError):
			pass


__all__ = ["PresentMonSampler"]
