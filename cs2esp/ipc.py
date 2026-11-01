"""
Cross-process status channel between the runtime and the Qt menu: TOML carries settings while status.json carries live values such as FPS, CS2 attach
state and the master switch. Writes use a temp file and atomic replace so menu polling never sees half-written JSON.
"""

import json
from math import isfinite
from os import fdopen, getpid, replace, unlink
from pathlib import Path
from tempfile import mkstemp
from threading import Event, Lock, Thread, current_thread
from time import monotonic, sleep, time

from cs2esp.config import CONFIG_DIR

STATUS_PATH: Path = CONFIG_DIR / "status.json"


def _replace_with_retry(temp_path: str, target: Path, attempts: int = 6) -> None:
	'''Atomic replace with backoff for transient Windows sharing violations.'''
	for attempt in range(attempts):
		try:
			replace(temp_path, target)
			return
		except PermissionError:
			if attempt == attempts - 1:
				raise
			sleep(0.02 * (attempt + 1))


def _status_payload(*, fps: float | None, cs2_attached: bool, master_enabled: bool, fps_source: str | None = None,
	cs2_monitor: tuple[int, int, int, int] | None = None, renderer: str | None = None, render_device_requested: str | None = None,
	render_device_warning: str | None = None, hud_editor: dict | None = None, recoil_runtime: dict | None = None) -> dict:
	'''Own the submitted values and timestamp them before any disk work.'''
	editor_notice = None
	if isinstance(hud_editor, dict):
		widget_id = hud_editor.get('widget_id')
		revision = hud_editor.get('revision')
		allowed, interacting = hud_editor.get('allowed'), hud_editor.get('interacting')
		if (widget_id in ('fps_counter', 'event_feed', 'hotkeys', 'status_pills', 'spectators', 'c4_capsule')
			and type(revision) is int and revision > 0 and type(allowed) is bool and type(interacting) is bool):
			editor_notice = {'widget_id': widget_id, 'revision': revision, 'allowed': allowed, 'interacting': interacting}

	weapon_notice = None
	if isinstance(recoil_runtime, dict):
		fields = ('item_definition', 'weapon_key', 'weapon_name', 'is_firearm', 'scope_capable', 'is_scoped', 'valid', 'reason')
		item_definition = recoil_runtime.get('item_definition')
		if (type(item_definition) is int and item_definition >= 0 and isinstance(recoil_runtime.get('weapon_name'), str)
			and (recoil_runtime.get('weapon_key') is None or isinstance(recoil_runtime.get('weapon_key'), str))
			and all(type(recoil_runtime.get(field)) is bool for field in ('is_firearm', 'scope_capable', 'valid'))
			and (recoil_runtime.get('is_scoped') is None or type(recoil_runtime.get('is_scoped')) is bool)
			and isinstance(recoil_runtime.get('reason'), str)):
			weapon_notice = {field: recoil_runtime.get(field) for field in fields}

	return {
		"fps": None if fps is None else float(fps),
		"fps_source": fps_source if fps is not None and fps_source in ("engine", "presentmon") else None,
		"cs2_attached": bool(cs2_attached),
		"master_enabled": bool(master_enabled),
		"cs2_monitor": list(cs2_monitor) if cs2_monitor else None,
		"renderer": renderer,
		"render_device_requested": render_device_requested,
		"render_device_warning": render_device_warning,
		"hud_editor": editor_notice,
		"recoil_runtime": weapon_notice,
		"ts": time(),
	}


def _atomic_write_json(target: Path, payload: dict, *, prefix: str, log_label: str) -> bool:
	'''Write payload through a temp file and atomic replace, removing the temp on failure.'''
	try:
		target.parent.mkdir(parents=True, exist_ok=True)
		temp_fd, temp_path = mkstemp(prefix=prefix, suffix=".json.tmp", dir=str(target.parent))
		try:
			with fdopen(temp_fd, "w", encoding="utf-8") as handle:
				json.dump(payload, handle)
			# Windows menu reads can briefly lock the destination during atomic replace
			_replace_with_retry(temp_path, target)
		except Exception:
			try:
				unlink(temp_path)
			except OSError:
				pass
			raise
		return True
	except Exception as error:
		print(f'[ipc] {log_label} failed: {error!r}')
		return False


def _write_status_payload(payload: dict) -> bool:
	'''Write the status file atomically; False when the write fails.'''
	return _atomic_write_json(STATUS_PATH, payload, prefix=".status-", log_label="write_status")


def write_status(*, fps: float | None, cs2_attached: bool, master_enabled: bool, fps_source: str | None = None,
	cs2_monitor: tuple[int, int, int, int] | None = None, renderer: str | None = None, render_device_requested: str | None = None,
	render_device_warning: str | None = None, hud_editor: dict | None = None, recoil_runtime: dict | None = None) -> bool:
	'''Write one status atomically, retaining the synchronous public API.'''
	return _write_status_payload(_status_payload(
		fps=fps, cs2_attached=cs2_attached, master_enabled=master_enabled, fps_source=fps_source, cs2_monitor=cs2_monitor,
		renderer=renderer, render_device_requested=render_device_requested, render_device_warning=render_device_warning,
		hud_editor=hud_editor, recoil_runtime=recoil_runtime))


class StatusPublisher:
	"""Write status off the frame thread, keeping only the newest pending value."""
	def __init__(self, *, writer=None) -> None:
		self._writer = _write_status_payload if writer is None else writer
		self._lock = Lock()
		self._wake = Event()
		self._done = Event()
		self._pending = None
		self._sequence = 0
		self._closing = False
		self._final_sequence = None
		self._last_attempt_sequence = None
		self._last_write_ok = None
		self._last_error = None
		self._thread = Thread(target=self._run, name="status-io", daemon=True)
		self._thread.start()

	def publish(self, **status) -> bool:
		'''Copy and enqueue status without filesystem calls, retries or waits.'''
		payload = _status_payload(**status)
		with self._lock:
			if self._closing:
				return False
			self._sequence += 1
			self._pending = (self._sequence, payload)
			self._wake.set()
		return True

	def _run(self) -> None:
		while True:
			self._wake.wait()
			with self._lock:
				pending, self._pending = self._pending, None
				if pending is None:
					if self._closing:
						self._done.set()
						return
					self._wake.clear()
					continue
			sequence, payload = pending
			error = None
			try:
				succeeded = bool(self._writer(payload))
				if not succeeded:
					error = "status writer returned False"
			except Exception as exception:
				succeeded = False
				error = repr(exception)
			with self._lock:
				self._last_attempt_sequence = sequence
				self._last_write_ok = succeeded
				self._last_error = error

	def close(self, *, final_payload: dict | None = None, timeout: float = 1.0) -> bool:
		'''Stop submissions and wait a bounded time for the final status attempt.'''
		# A timed-out close may let the in-flight write finish, but no older value can land after the final one
		timeout = max(0.0, float(timeout))
		deadline = monotonic() + timeout
		payload = _status_payload(**final_payload) if final_payload is not None else None
		with self._lock:
			if not self._closing:
				self._closing = True
				if payload is not None:
					self._sequence += 1
					self._final_sequence = self._sequence
					self._pending = (self._sequence, payload)
				self._wake.set()
		if current_thread() is self._thread or not self._done.wait(timeout):
			return False
		self._thread.join(timeout=max(0.0, deadline - monotonic()))
		if self._thread.is_alive():
			return False
		with self._lock:
			if self._final_sequence is not None:
				return self._last_attempt_sequence == self._final_sequence and self._last_write_ok is True
			return self._last_write_ok is not False

	@property
	def last_error(self) -> str | None:
		with self._lock:
			return self._last_error

	@property
	def is_alive(self) -> bool:
		return self._thread.is_alive()


def read_status() -> dict | None:
	'''Read runtime status, or None when missing or unreadable.'''
	try:
		with STATUS_PATH.open("r", encoding="utf-8") as status_file:
			data = json.load(status_file)
	except (OSError, json.JSONDecodeError):
		return None
	if not isinstance(data, dict):
		return None
	return data


def is_stale(status: dict, max_age_sec: float = 2.0) -> bool:
	'''Return True when status is older than max_age_sec.'''
	try:
		return (time() - float(status.get("ts", 0.0))) > max_age_sec
	except (TypeError, ValueError):
		return True


OFFSET_CMD_PATH: Path = CONFIG_DIR / "offset_cmd.json"
OFFSET_ACK_PATH: Path = CONFIG_DIR / "offset_ack.json"


def _write_json_atomic(target: Path, payload: dict) -> bool:
	'''Write a control or offset JSON file atomically; False when the write fails.'''
	return _atomic_write_json(target, payload, prefix=f".{target.stem}-", log_label=f"write {target.name}")


def request_offset_refresh(*, cache_sha256: str | None = None) -> float | bool:
	'''Signal the runtime to reload and heal offsets live; returns the request timestamp or False.'''
	try:
		payload = {"action": "refresh", "ts": time()}
		if cache_sha256 is not None:
			if len(cache_sha256) != 64 or any(character not in "0123456789abcdef" for character in cache_sha256):
				return False
			payload["cache_sha256"] = cache_sha256
		return float(payload["ts"]) if _write_json_atomic(OFFSET_CMD_PATH, payload) else False
	except Exception as error:
		print(f'[ipc] request_offset_refresh failed: {error!r}')
		return False


def write_offset_ack(*, request_ts: float, applied: int, netvars: int, attached: bool, success: bool | None = None,
	cache_loaded: bool | None = None, cache_saved: bool | None = None, live_verified: bool | None = None,
	cache_sha256: str | None = None, applied_cache_sha256: str | None = None, error: str | None = None) -> bool:
	'''Runtime confirmation that an offset refresh was actually applied.'''
	return _write_json_atomic(OFFSET_ACK_PATH, {
		"request_ts": float(request_ts),
		"applied": int(applied),
		"netvars": int(netvars),
		"attached": bool(attached),
		"success": success,
		"cache_loaded": cache_loaded,
		"cache_saved": cache_saved,
		"live_verified": live_verified,
		"cache_sha256": cache_sha256,
		"applied_cache_sha256": applied_cache_sha256,
		"error": error,
		"pid": getpid(),
		"ts": time(),
	})


def read_offset_ack() -> dict | None:
	'''Read the last runtime offset-refresh acknowledgement.'''
	try:
		with OFFSET_ACK_PATH.open("r", encoding="utf-8") as handle:
			data = json.load(handle)
	except (OSError, json.JSONDecodeError):
		return None
	return data if isinstance(data, dict) else None


def read_offset_refresh_request(last_handled_ts: float) -> dict | None:
	'''Read one newer, finite refresh request with optional expected cache identity.'''
	try:
		with OFFSET_CMD_PATH.open("r", encoding="utf-8") as handle:
			data = json.load(handle)
		if not isinstance(data, dict) or data.get("action") != "refresh":
			return None
		request_ts = float(data.get("ts", 0.0))
		if not isfinite(request_ts) or request_ts <= last_handled_ts:
			return None
		digest = data.get("cache_sha256")
		if digest is not None and (not isinstance(digest, str) or len(digest) != 64 or any(char not in "0123456789abcdef" for char in digest)):
			return None
		return {"ts": request_ts, "cache_sha256": digest}
	except (OSError, ValueError, TypeError):
		return None


def check_offset_refresh_request(last_handled_ts: float) -> tuple[bool, float]:
	'''Return whether a newer refresh request exists and the timestamp to acknowledge.'''
	request = read_offset_refresh_request(last_handled_ts)
	return (True, request["ts"]) if request else (False, last_handled_ts)


CONTROL_DIR: Path = CONFIG_DIR / "control"
INSTANCE_PATH: Path = CONFIG_DIR / "instance.json"


def send_control(action: str, **fields) -> float | bool:
	'''Queue a command for the runtime; one file per command keeps quick clicks from overwriting each other.'''
	timestamp = time()
	payload = {"action": str(action), "ts": timestamp, **fields}
	target = CONTROL_DIR / f"{timestamp:.6f}-{action}.json"
	return timestamp if _write_json_atomic(target, payload) else False


_control_seen_mtime: list = [None]


def drain_control(newer_than: float = 0.0) -> list[dict]:
	'''Pop every queued command (oldest first); stale ones are discarded.'''
	# A single directory stat is enough when idle; file creation/removal changes mtime
	try:
		mtime = CONTROL_DIR.stat().st_mtime_ns
	except OSError:
		return []
	if mtime == _control_seen_mtime[0]:
		return []
	try:
		entries = sorted(entry for entry in CONTROL_DIR.iterdir() if entry.suffix == ".json" and not entry.name.startswith("."))
	except OSError:
		return []
	commands: list[dict] = []
	for entry in entries:
		try:
			with entry.open("r", encoding="utf-8") as handle:
				data = json.load(handle)
		except (OSError, json.JSONDecodeError):
			data = None
		try:
			unlink(entry)
		except OSError:
			pass
		if isinstance(data, dict) and float(data.get("ts", 0.0) or 0.0) > newer_than:
			commands.append(data)
	# Pre-listing mtime ensures files arriving mid-drain are picked up on the next poll
	_control_seen_mtime[0] = mtime
	return commands


def write_instance(pid: int) -> bool:
	'''Record this process as the running instance.'''
	return _write_json_atomic(INSTANCE_PATH, {"pid": int(pid), "ts": time()})


def read_instance_pid() -> int:
	'''PID of the recorded instance, or 0 when missing or unreadable.'''
	try:
		with INSTANCE_PATH.open("r", encoding="utf-8") as handle:
			return int(json.load(handle).get("pid", 0) or 0)
	except (OSError, ValueError, AttributeError, json.JSONDecodeError):
		return 0


__all__ = [
	"STATUS_PATH", "CONTROL_DIR", "INSTANCE_PATH", "send_control", "drain_control", "write_instance", "read_instance_pid",
	"OFFSET_CMD_PATH", "OFFSET_ACK_PATH", "write_status", "StatusPublisher", "read_status", "is_stale", "request_offset_refresh",
	"check_offset_refresh_request", "read_offset_refresh_request", "write_offset_ack", "read_offset_ack",
]
