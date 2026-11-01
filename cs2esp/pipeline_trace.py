"""
Optional, bounded asynchronous per-frame pipeline telemetry.
Timestamps are perf_counter seconds; they measure application and driver-call boundaries, not GPU completion, compositor presentation or emitted photons.
"""
from __future__ import annotations

import json
from pathlib import Path
from queue import Empty, Full, Queue
import threading


class PipelineTrace:
	"""Bounded queue writer for per-frame event records."""
	def __init__(self, path: str, *, capacity: int = 2048) -> None:
		self._path = Path(path)
		self._queue = Queue(maxsize=capacity)
		self._dropped = 0
		self._stop = threading.Event()
		self._thread = threading.Thread(target=self._write, name="pipeline-trace", daemon=True)
		self._thread.start()

	def emit(self, event: dict) -> None:
		'''Queue one event without blocking; count drops when the queue is full.'''
		try:
			self._queue.put_nowait(event)
		except Full:
			self._dropped += 1

	def _write(self) -> None:
		try:
			with self._path.open("w", encoding="utf-8") as output:
				while not self._stop.is_set() or not self._queue.empty():
					try:
						event = self._queue.get(timeout=0.1)
					except Empty:
						continue
					output.write(json.dumps(event, separators=(",", ":"), allow_nan=False) + "\n")
				output.write(json.dumps({"event": "summary", "dropped": self._dropped}) + "\n")
		except (OSError, ValueError, TypeError) as exc:
			print(f"[pipeline-trace] recording failed: {exc!r}")

	def close(self) -> None:
		'''Stop the writer after the queue drains.'''
		self._stop.set()
		self._thread.join(timeout=3.0)
		if self._thread.is_alive():
			print("[pipeline-trace] writer did not stop within 3 seconds")


__all__ = ["PipelineTrace"]
