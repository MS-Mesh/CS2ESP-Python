"""
Autoclicker for full-auto pistol and rapid-fire presets.

Sends mouse_event left-click pairs at a configurable cadence while the hotkey is held. [rapidfire] is the faster generic preset; [autopistol] is tuned for semi-auto pistol cycle rate. No memory writes are performed.
"""

import ctypes
from random import uniform
from time import perf_counter

_user32 = ctypes.windll.user32
_MOUSEEVENTF_LEFTDOWN = 0x0002
_MOUSEEVENTF_LEFTUP = 0x0004


def _mouse_down() -> None:
    """Send a left-button press; kept as a patchable test boundary."""
    _user32.mouse_event(_MOUSEEVENTF_LEFTDOWN, 0, 0, 0, 0)


def _mouse_up() -> None:
    """Send a left-button release; kept as a patchable test boundary."""
    _user32.mouse_event(_MOUSEEVENTF_LEFTUP, 0, 0, 0, 0)


class _ClickerBase:
    """Shared cadence + jitter logic. Subclass picks its config section."""

    cfg_section: str = ""

    def __init__(self, config):
        self.config = config
        self._next_at: float = 0.0
        self._release_at: float = 0.0

    def _cfg(self):
        """Return this clicker's live config section (also a test seam)."""
        return getattr(self.config, self.cfg_section)

    def has_pending_release(self) -> bool:
        """True while a click's mouse-up is still owed (see main.py gating)."""
        return bool(self._release_at)

    def update(self) -> None:
        now = perf_counter()

        # Deferred mouse-up; click never blocks the frame.
        if self._release_at and now >= self._release_at:
            _mouse_up()
            self._release_at = 0.0

        clicker_config = self._cfg()
        if not getattr(clicker_config, "enabled", False):
            return
        if now < self._next_at or self._release_at:
            return

        cps = float(getattr(clicker_config, "cps", 10.0))
        cps = max(1.0, min(25.0, cps))
        interval = 1.0 / cps
        # Jitter breaks constant-frequency timing.
        jitter = float(getattr(clicker_config, "jitter_pct", 0.25))
        jitter = max(0.0, min(0.5, jitter))
        interval *= 1.0 + uniform(-jitter, jitter)

        _mouse_down()
        self._release_at = now + uniform(0.010, 0.020)
        self._next_at = now + interval


class RapidFire(_ClickerBase):
    """Generic fast clicker. Use for AWP/Scout quickswitch or M4 burst."""
    cfg_section = "rapidfire"


class AutoPistol(_ClickerBase):
    """Slower clicker tuned for semi-auto pistol cycle rate (~6-7 cps)."""
    cfg_section = "autopistol"
