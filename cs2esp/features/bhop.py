"""
BHop / auto-jump.

On each landing we emit a SPACE key-up/key-down burst via SendInput so +jump gets a fresh edge. Humaniser skips release SPACE for one bounce, then kickstarts the chain again. No memory writes are performed.
"""

import ctypes
import ctypes.wintypes as wintypes
from random import randint, uniform
from time import perf_counter

from cs2esp.offsets import Netvars


_user32 = ctypes.windll.user32

# SPACE virtual-key + PS/2 set-1 scancode.
_VK_SPACE = 0x20
_SC_SPACE = 0x39

_INPUT_KEYBOARD = 1
_KEYEVENTF_KEYUP = 0x0002
_KEYEVENTF_SCANCODE = 0x0008

_FL_ONGROUND = 0x01


# ctypes scaffolding for SendInput.
class _KEYBDINPUT(ctypes.Structure):
    _fields_ = (
        ("wVk", wintypes.WORD),
        ("wScan", wintypes.WORD),
        ("dwFlags", wintypes.DWORD),
        ("time", wintypes.DWORD),
        ("dwExtraInfo", ctypes.c_void_p),
    )


class _MOUSEINPUT(ctypes.Structure):
    _fields_ = (
        ("dx", wintypes.LONG),
        ("dy", wintypes.LONG),
        ("mouseData", wintypes.DWORD),
        ("dwFlags", wintypes.DWORD),
        ("time", wintypes.DWORD),
        ("dwExtraInfo", ctypes.c_void_p),
    )


class _INPUT_UNION(ctypes.Union):
    _fields_ = (
        ("ki", _KEYBDINPUT),
        ("mi", _MOUSEINPUT),
    )


class _INPUT(ctypes.Structure):
    _anonymous_ = ("u",)
    _fields_ = (
        ("type", wintypes.DWORD),
        ("u", _INPUT_UNION),
    )


def _make_kb_input(vk: int, scan: int, flags: int) -> _INPUT:
    """Build a single keyboard INPUT event ready for SendInput."""
    keyboard_input = _INPUT()
    keyboard_input.type = _INPUT_KEYBOARD
    keyboard_input.ki.wVk = vk
    keyboard_input.ki.wScan = scan
    keyboard_input.ki.dwFlags = flags
    keyboard_input.ki.time = 0
    keyboard_input.ki.dwExtraInfo = None
    return keyboard_input


def _send_rejump_burst() -> None:
    """Force CS2 to re-fire +jump while SPACE is physically held."""
    flags_up = _KEYEVENTF_KEYUP | _KEYEVENTF_SCANCODE
    flags_down = _KEYEVENTF_SCANCODE
    events = (_INPUT * 2)(
        _make_kb_input(_VK_SPACE, _SC_SPACE, flags_up),
        _make_kb_input(_VK_SPACE, _SC_SPACE, flags_down),
    )
    _user32.SendInput(2, ctypes.byref(events), ctypes.sizeof(_INPUT))


def _send_space_event(flags: int) -> None:
    """Send one SPACE keyboard event."""
    event = _make_kb_input(_VK_SPACE, _SC_SPACE, flags)
    events = (_INPUT * 1)(event)
    _user32.SendInput(1, ctypes.byref(events), ctypes.sizeof(_INPUT))


class BHop:
    """Auto-rejump on every landing while the bhop hotkey is held."""

    def __init__(self, mem, entity_system, config):
        self.mem = mem
        self.entities = entity_system
        self.config = config
        self._was_on_ground: bool = False
        self._jumps_since_skip: int = 0
        self._next_skip_at: int = self._roll_next_skip()
        self._last_jump_at: float = 0.0
        self._pending_jump_at: float = 0.0
        self._skip_resume_at: float = 0.0
        self._post_kickstart_grace_until: float = 0.0

    def has_pending_resume(self) -> bool:
        """True while a jump is still owed (queued dispatch, skip, or grace).

        main.py gates update() on this, so it must cover every deferred state
        or releasing a hold hotkey mid-window drops the pending rejump.
        """
        return bool(
            self._pending_jump_at
            or self._skip_resume_at
            or (
                self._post_kickstart_grace_until
                and perf_counter() < self._post_kickstart_grace_until
            )
        )

    def _roll_next_skip(self) -> int:
        """Pick the next skip target from the configured 3..15 range."""
        bhop_config = self.config.bhop
        try:
            lower_bound = int(getattr(bhop_config, "humanize_skip_min", 3))
            upper_bound = int(getattr(bhop_config, "humanize_skip_max", 15))
        except Exception:
            lower_bound, upper_bound = 3, 15
        lower_bound = max(1, lower_bound)
        upper_bound = max(lower_bound, upper_bound)
        return randint(lower_bound, upper_bound)

    def update(self) -> None:
        bhop_config = self.config.bhop
        if not bhop_config.enabled:
            self._was_on_ground = False
            self._pending_jump_at = 0.0
            self._skip_resume_at = 0.0
            return

        now = perf_counter()

        # Kickstart: re-arm +jump after a humaniser skip.
        if self._skip_resume_at and now >= self._skip_resume_at:
            _send_space_event(_KEYEVENTF_SCANCODE)
            self._skip_resume_at = 0.0
            self._last_jump_at = now
            self._was_on_ground = False
            # Keeps main.py's gate open while keyboard state catches up.
            self._post_kickstart_grace_until = now + 0.25

        # Deferred jitter dispatch; ESP keeps drawing.
        if self._pending_jump_at and now >= self._pending_jump_at:
            _send_rejump_burst()
            self._last_jump_at = now
            self._pending_jump_at = 0.0

        local_pawn = self.entities.get_local_pawn()
        if not local_pawn:
            return
        try:
            flags = self.mem.read_int(local_pawn + Netvars.m_fFlags)
        except Exception:
            return

        on_ground = bool(flags & _FL_ONGROUND)

        # The in-air-to-ground edge starts exactly one rejump cycle.
        if on_ground and not self._was_on_ground:
            min_gap = float(getattr(bhop_config, "min_gap_ms", 80)) / 1000.0
            already_pending = bool(self._pending_jump_at or self._skip_resume_at)
            if (now - self._last_jump_at) >= min_gap and not already_pending:
                self._jumps_since_skip += 1
                humanize = bool(getattr(bhop_config, "humanize", True))
                if humanize and self._jumps_since_skip >= self._next_skip_at:
                    # Humaniser skip: KEYUP breaks the chain for one bounce.
                    _send_space_event(_KEYEVENTF_KEYUP | _KEYEVENTF_SCANCODE)
                    resume_ms = float(getattr(bhop_config, "skip_resume_delay_ms", 80))
                    resume_ms = max(20.0, min(1000.0, resume_ms))
                    self._skip_resume_at = now + (resume_ms / 1000.0)
                    self._jumps_since_skip = 0
                    self._next_skip_at = self._roll_next_skip()
                else:
                    delay_jitter = float(getattr(bhop_config, "delay_jitter_ms", 8))
                    if delay_jitter > 0:
                        self._pending_jump_at = now + uniform(
                            0.0, delay_jitter / 1000.0,
                        )
                    else:
                        _send_rejump_burst()
                        self._last_jump_at = now

        self._was_on_ground = on_ground
