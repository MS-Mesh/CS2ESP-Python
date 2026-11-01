"""
Per-process crash traces for the menu and overlay, including Python exceptions and Windows native faults.
The handle stays open for faulthandler; no sampling or file writes run in the frame loop.
"""

import faulthandler
import os
from pathlib import Path
import sys
import threading
from time import strftime
from traceback import print_exception


_LOG_FILE = None
_LOG_PATH: Path | None = None
_MAX_LOGS_PER_ROLE = 8


def configure_crash_logging(role: str) -> Path | None:
	'''Install once before GUI/native imports; a logging failure must not prevent startup.'''
	global _LOG_FILE, _LOG_PATH
	if _LOG_FILE is not None:
		return _LOG_PATH
	if role not in ("menu", "overlay"):
		raise ValueError(f"Unknown process role: {role!r}")
	portable = Path(sys.executable).resolve().parent if getattr(sys, "frozen", False) else Path(__file__).resolve().parent.parent
	config_dir = Path(os.environ.get("CS2ESP_CONFIG_DIR", portable / "config"))
	fallback = Path(os.environ.get("LOCALAPPDATA", portable)) / "MS Mesh" / "CS2ESP" / "logs"
	filename = f"{role}-{strftime('%Y%m%d-%H%M%S')}-{os.getpid()}.log"
	for directory in (config_dir / "logs", fallback):
		try:
			directory.mkdir(parents=True, exist_ok=True)
			stream = (directory / filename).open("a", encoding="utf-8", buffering=1)
		except OSError:
			continue
		_LOG_FILE, _LOG_PATH = stream, directory / filename
		break
	if _LOG_FILE is None:
		return None

	_LOG_FILE.write(f"role={role} pid={os.getpid()} parent={os.getppid()} python={sys.version.split()[0]} frozen={bool(getattr(sys, 'frozen', False))}\n")
	faulthandler.enable(file=_LOG_FILE, all_threads=True)
	previous_exception_hook = sys.excepthook
	previous_thread_hook = threading.excepthook

	def process_exception(error_type, error, traceback) -> None:
		try:
			_LOG_FILE.write("Unhandled process exception\n")
			print_exception(error_type, error, traceback, file=_LOG_FILE)
			_LOG_FILE.flush()
		finally:
			previous_exception_hook(error_type, error, traceback)

	def thread_exception(arguments) -> None:
		try:
			name = arguments.thread.name if arguments.thread is not None else "unknown"
			_LOG_FILE.write(f"Unhandled thread exception: {name}\n")
			print_exception(arguments.exc_type, arguments.exc_value, arguments.exc_traceback, file=_LOG_FILE)
			_LOG_FILE.flush()
		finally:
			previous_thread_hook(arguments)

	sys.excepthook = process_exception
	threading.excepthook = thread_exception
	directory = _LOG_PATH.parent.resolve()
	logs = []
	for path in directory.glob(f"{role}-*.log"):
		try:
			logs.append((path.stat().st_mtime, path))
		except OSError:
			continue
	for _mtime, old in sorted(logs, reverse=True)[_MAX_LOGS_PER_ROLE:]:
		try:
			if old.parent.resolve() == directory and old.resolve() != _LOG_PATH.resolve():
				old.unlink()
		except OSError:
			pass
	return _LOG_PATH
