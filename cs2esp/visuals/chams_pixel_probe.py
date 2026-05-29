"""Thread-safe centre-pixel alpha probe for rendered chams hit testing."""

from threading import Lock
from time import perf_counter

try:
    import OpenGL.GL as gl
except Exception:
    gl = None


_lock = Lock()
_alpha = 0
_last_probe_at = 0.0


def probe_center_pixel(screen_w: int, screen_h: int) -> int:
    """Read and latch centre-pixel alpha from the current GL framebuffer."""
    global _alpha, _last_probe_at
    if gl is None:
        return 0
    cx = int(screen_w * 0.5)
    cy_gl = screen_h - 1 - int(screen_h * 0.5)
    try:
        buf = (gl.GLubyte * 4)()
        gl.glReadPixels(
            cx, cy_gl, 1, 1,
            gl.GL_RGBA, gl.GL_UNSIGNED_BYTE, buf,
        )
        a = int(buf[3])
    except Exception:
        return 0
    with _lock:
        _alpha = a
        _last_probe_at = perf_counter()
    return a


def get_alpha() -> int:
    """Thread-safe read of the latched alpha."""
    with _lock:
        return _alpha


def get_last_probe_age_s() -> float:
    """Seconds since the last successful probe (large = stale)."""
    with _lock:
        last_probe_at = _last_probe_at
    if last_probe_at <= 0.0:
        return float("inf")
    return perf_counter() - last_probe_at
