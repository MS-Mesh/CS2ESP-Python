"""
Cross-process status channel.

Runtime 'main.py' and the Qt menu run in separate processes. TOML handles
settings, while status.json carries live values: FPS, CS2 attach state, and
the master switch. Writes use a temp file and atomic replace so menu polling
never sees half-written JSON.
"""

import json
from os import fdopen, replace, unlink
from pathlib import Path
from tempfile import mkstemp
from time import time

from cs2esp.config import CONFIG_DIR

STATUS_PATH: Path = CONFIG_DIR / "status.json"


def write_status(
    *,
    fps: float | None,
    cs2_attached: bool,
    master_enabled: bool,
    cs2_monitor: tuple[int, int, int, int] | None = None,
) -> bool:
    """Write runtime status atomically; log failures and return False.

    cs2_monitor is CS2's monitor rect (left, top, w, h) in physical px so
    the out-of-process Qt menu can open centered over the game display.
    """
    payload = {
        "fps": None if fps is None else float(fps),
        "cs2_attached": bool(cs2_attached),
        "master_enabled": bool(master_enabled),
        "cs2_monitor": list(cs2_monitor) if cs2_monitor else None,
        "ts": time(),
    }
    target = STATUS_PATH
    try:
        target.parent.mkdir(parents=True, exist_ok=True)
        temp_fd, temp_path = mkstemp(
            prefix=".status-", suffix=".json.tmp", dir=str(target.parent))
        try:
            with fdopen(temp_fd, "w", encoding="utf-8") as status_file:
                json.dump(payload, status_file)
            replace(temp_path, target)
        except Exception:
            try:
                unlink(temp_path)
            except OSError:
                pass
            raise
        return True
    except Exception as e:
        print(f"[ipc] write_status failed: {e!r}")
        return False


def read_status() -> dict | None:
    """Read runtime status, or None when missing or unreadable."""
    try:
        with STATUS_PATH.open("r", encoding="utf-8") as f:
            data = json.load(f)
    except (OSError, json.JSONDecodeError):
        return None
    if not isinstance(data, dict):
        return None
    return data


def is_stale(status: dict, max_age_sec: float = 2.0) -> bool:
    """Return True when status is older than max_age_sec."""
    try:
        return (time() - float(status.get("ts", 0.0))) > max_age_sec
    except (TypeError, ValueError):
        return True


__all__ = ["STATUS_PATH", "write_status", "read_status", "is_stale"]
