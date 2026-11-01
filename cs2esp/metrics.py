"""
Observed game, process and system metrics for the overlay HUD.
PresentMon supplies displayed game FPS when its ETW session succeeds; otherwise the separately identified engine-frame counter is used.
Neither is the overlay FPS, network latency or physical photon latency.
"""
from __future__ import annotations

import math
import struct
from threading import Event, Lock, Thread
from time import perf_counter

import cs2esp.metrics_win32 as _w32
from cs2esp.offsets import Netvars, Offsets
from cs2esp.presentmon import PresentMonSampler


_GV_FRAME_COUNT = 0x04  # uint32, CGlobalVars frame counter
_ENGINE_FPS_TTL = 1.5
_GAME_PING_TTL = 1.5
_RESOURCE_TTL = 1.5
_DISPLAY_SAMPLE_INTERVAL = 0.2
_TREE_REFRESH_S = 2.0
_MAX_ENGINE_FPS = 5000.0
_MAX_GAME_PING_MS = 10_000


def _read_u32(mem, address: int) -> int | None:
	'''Unlike MemoryReader.read_uint, distinguish a valid zero from a failed read.'''
	try:
		raw = mem.read_bytes(address, 4)
		return struct.unpack("<I", raw)[0] if len(raw) == 4 else None
	except Exception:
		return None


class _ProcessTreeWorker:
	"""Discover tracked children away from rendering; keep one pending request/result."""
	def __init__(self, *, discover=None, clock=None) -> None:
		self._discover = _w32.process_tree if discover is None else discover
		self._clock = perf_counter if clock is None else clock
		self._lock = Lock()
		self._wake = Event()
		self._thread: Thread | None = None
		self._roots: tuple[int, ...] = ()
		self._generation = 0
		self._pending: tuple[int, tuple[int, ...]] | None = None
		self._inflight_generation: int | None = None
		self._result: tuple[int, tuple[int, ...] | None, float] | None = None
		self._requested_at: float | None = None
		self._closed = False

	def invalidate(self, roots: tuple[int, ...]) -> None:
		with self._lock:
			if roots != self._roots:
				self._roots = roots
				self._generation += 1
				self._pending = None
				self._result = None
				self._requested_at = None

	def latest(self, roots: tuple[int, ...], now: float) -> tuple[int, ...] | None:
		self.invalidate(roots)
		with self._lock:
			if self._closed:
				return None
			if not roots:
				return ()
			pending_current = self._pending is not None and self._pending[0] == self._generation
			if (not pending_current and self._inflight_generation != self._generation
					and (self._requested_at is None or now - self._requested_at >= _TREE_REFRESH_S)):
				self._pending = (self._generation, roots)
				self._requested_at = now
				self._wake.set()
				if self._thread is None:
					self._thread = Thread(target=self._run, name="metrics-tree", daemon=True)
					self._thread.start()
			result = self._result
			if result is not None and result[0] == self._generation and 0 <= now - result[2] <= _TREE_REFRESH_S:
				return result[1]
			return None

	def _run(self) -> None:
		while True:
			self._wake.wait()
			with self._lock:
				if self._closed:
					return
				request = self._pending
				self._pending = None
				if request is None:
					self._wake.clear()
					continue
				generation, roots = request
				self._inflight_generation = generation
			try:
				discovered = self._discover(roots)
				tree = tuple(discovered) if discovered is not None else None
			except Exception:
				tree = None
			completed_at = self._clock()
			with self._lock:
				self._inflight_generation = None
				if self._closed:
					return
				if generation == self._generation:
					self._result = (generation, tree, completed_at)
				if self._pending is None:
					self._wake.clear()

	def close(self, timeout: float = 0.25) -> bool:
		'''Stop accepting work; return False when a discovery has not yet returned.'''
		with self._lock:
			self._closed = True
			self._generation += 1
			self._pending = None
			self._result = None
			self._wake.set()
			thread = self._thread
		if thread is not None:
			timeout = float(timeout)
			thread.join(timeout=max(0.0, min(1.0, timeout)) if math.isfinite(timeout) else 0.0)
		return thread is None or not thread.is_alive()


class RealMetrics:
	"""Sample game FPS and scoreboard RTT plus actual process/system usage."""
	def __init__(self, pids: tuple[int, ...] = (), *, presentmon=None, tree_worker=None) -> None:
		self._pids = tuple(int(pid) for pid in pids if pid)
		self._tree: tuple[int, ...] | None = None
		self._tree_worker = tree_worker if tree_worker is not None else _ProcessTreeWorker()
		self._cpu = _w32.CpuSampler()
		self._presentmon = presentmon if presentmon is not None else PresentMonSampler()
		self._display_sample: tuple[float | None, float | None] = (None, None)
		self._display_sample_at: float | None = None
		self._game_pid = 0
		self._globals_ptr = 0
		self._frame_count: int | None = None
		self._frame_count_at = 0.0
		self._engine_fps: float | None = None
		self._engine_fps_at = 0.0
		self._game_ping_ms: int | None = None
		self._game_ping_at = 0.0
		self._sys_ram: tuple[float, float] | None = None
		self._resources_at: float | None = None
		self._frame_work_ms: float | None = None

	def set_pids(self, pids: tuple[int, ...]) -> None:
		roots = tuple(dict.fromkeys(int(pid) for pid in pids if int(pid) > 0))
		if roots != self._pids:
			self._pids = roots
			self._tree = None
			self._tree_worker.invalidate(roots)
			self._cpu.process_percent = None
			self._cpu.process_ram_mb = None

	def update(self, mem=None, *, in_match: bool | None = None) -> None:
		'''Refresh metrics. A detached process or unsuccessful read clears values.'''
		now = perf_counter()
		pid = int(getattr(getattr(mem, "pm", None), "process_id", 0) or 0) if mem is not None else 0
		if pid != self._game_pid:
			self._game_pid = pid
			self._globals_ptr = 0
			self._frame_count = None
			self._engine_fps = None
			self._engine_fps_at = 0.0
			self._game_ping_ms = None
			self._display_sample = (None, None)
			self._display_sample_at = None
		self._presentmon.set_pid(pid)
		if mem is not None and pid:
			self._sample_fps(mem, now)
			self._sample_game_ping(mem, now=now, in_match=in_match)
		else:
			self._engine_fps = None
			self._engine_fps_at = 0.0
			self._frame_count = None
			self._game_ping_ms = None

		self._tree = self._tree_worker.latest(self._pids, now)
		try:
			self._cpu.sample(list(self._tree) if self._tree is not None else [])
			if self._tree is None:
				self._cpu.process_percent = None
				self._cpu.process_ram_mb = None
		except Exception:
			self._cpu.system_percent = None
			self._cpu.process_percent = None
			self._cpu.process_ram_mb = None
		try:
			self._sys_ram = _w32.system_ram_gb()
		except Exception:
			self._sys_ram = None
		self._resources_at = now

	def _resources_current(self) -> bool:
		return self._resources_at is not None and perf_counter() - self._resources_at <= _RESOURCE_TTL

	def _sample_fps(self, mem, now: float | None = None) -> None:
		'''Estimate engine FPS from counter deltas, never a stale instant reciprocal.'''
		now = perf_counter() if now is None else now
		if not Offsets.dwGlobalVars:
			self._expire_engine_fps(now)
			return
		try:
			globals_ptr = mem.read_long(mem.client(Offsets.dwGlobalVars))
			if not 0x10000 < globals_ptr < 0x7FFFFFFFFFFF:
				self._expire_engine_fps(now)
				return
			count = _read_u32(mem, globals_ptr + _GV_FRAME_COUNT)
		except Exception:
			self._expire_engine_fps(now)
			return
		if count is None:
			self._expire_engine_fps(now)
			return
		if globals_ptr != self._globals_ptr:
			self._globals_ptr = globals_ptr
			self._frame_count = None
			self._engine_fps = None
		if self._frame_count is None:
			self._frame_count, self._frame_count_at = count, now
			return
		elapsed = now - self._frame_count_at
		if elapsed > _ENGINE_FPS_TTL:
			# A long pause / unreadable interval is not the current frame rate
			self._frame_count, self._frame_count_at = count, now
			self._engine_fps = None
			self._engine_fps_at = 0.0
			return
		delta = (count - self._frame_count) & 0xFFFFFFFF
		if delta and elapsed > 0.0:
			fps = delta / elapsed
			self._frame_count, self._frame_count_at = count, now
			if math.isfinite(fps) and 0.0 < fps <= _MAX_ENGINE_FPS:
				self._engine_fps, self._engine_fps_at = fps, now
			else:
				self._engine_fps = None
				self._engine_fps_at = 0.0
		else:
			self._expire_engine_fps(now)

	def _expire_engine_fps(self, now: float) -> None:
		if self._engine_fps is not None and now - self._engine_fps_at > _ENGINE_FPS_TTL:
			self._engine_fps = None

	def _sample_game_ping(self, mem, *, now: float, in_match: bool | None) -> None:
		self._game_ping_ms = None
		if in_match is False or not Offsets.dwLocalPlayerController or not Netvars.m_iPing:
			return
		try:
			controller = mem.read_long(mem.client(Offsets.dwLocalPlayerController))
			if not 0x10000 < controller < 0x7FFFFFFFFFFF:
				return
			# A local controller can survive into lobby/loading; a valid player
			# pawn handle is required before presenting its scoreboard ping
			if not Netvars.m_hPlayerPawn:
				return
			handle = _read_u32(mem, controller + Netvars.m_hPlayerPawn)
			if handle is None or not (handle & 0x7FFF) or (handle & 0x7FFF) == 0x7FFF:
				return
			ping = _read_u32(mem, controller + Netvars.m_iPing)
			if ping is not None and ping <= _MAX_GAME_PING_MS:
				self._game_ping_ms = ping
				self._game_ping_at = now
		except Exception:
			return

	def _current_fps(self) -> tuple[float | None, str | None]:
		if not self._game_pid:
			return None, None
		displayed, _ = self._capture_sample()
		if displayed is not None:
			return displayed, "presentmon"
		if self._engine_fps is not None and perf_counter() - self._engine_fps_at <= _ENGINE_FPS_TTL:
			return self._engine_fps, "engine"
		return None, None

	def _capture_sample(self) -> tuple[float | None, float | None]:
		# FPS/source/latency can all be drawn each frame: aggregate the ETW
		# records at most five times a second, not once per HUD property
		now = perf_counter()
		if self._display_sample_at is None or now - self._display_sample_at >= _DISPLAY_SAMPLE_INTERVAL:
			self._display_sample = self._presentmon.sample()
			self._display_sample_at = now
		return self._display_sample

	@property
	def fps(self) -> float | None:
		value, _ = self._current_fps()
		return round(value) if value is not None else None

	@property
	def fps_source(self) -> str | None:
		return self._current_fps()[1]

	@property
	def present_to_display_ms(self) -> float | None:
		return self._capture_sample()[1] if self._game_pid else None

	@property
	def game_ping_ms(self) -> int | None:
		if self._game_ping_ms is None or perf_counter() - self._game_ping_at > _GAME_PING_TTL:
			return None
		return self._game_ping_ms

	@property
	def overlay_cpu_percent(self) -> float | None:
		return self._cpu.process_percent if self._resources_current() else None

	@property
	def overlay_ram_mb(self) -> float | None:
		return self._cpu.process_ram_mb if self._resources_current() else None

	@property
	def system_ram(self) -> tuple[float, float] | None:
		return self._sys_ram if self._resources_current() else None

	def note_frame_latency(self, ms: float) -> None:
		'''Record render-loop work only, not game, network or photon latency.'''
		try:
			value = float(ms)
		except (TypeError, ValueError):
			self._frame_work_ms = None
			return
		if not math.isfinite(value) or value < 0.0:
			self._frame_work_ms = None
		elif self._frame_work_ms is None:
			self._frame_work_ms = value
		else:
			self._frame_work_ms = self._frame_work_ms * 0.9 + value * 0.1

	@property
	def latency_ms(self) -> float | None:
		'''Smoothed overlay frame-work duration, retained for compatibility.'''
		return self._frame_work_ms

	@property
	def cpu_percent(self) -> float | None:
		if not self._resources_current():
			return None
		return self._cpu.system_percent

	def close(self, tree_timeout: float = 0.25) -> bool:
		tree_stopped = self._tree_worker.close(timeout=tree_timeout)
		self._presentmon.close()
		cpu_close = getattr(self._cpu, "close", None)
		cpu_stopped = cpu_close() if cpu_close is not None else True
		return tree_stopped and cpu_stopped
