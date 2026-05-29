"""
Hotkey detection, keybind callbacks, and feature-gate helpers.

Hold keys are polled by features; toggle keys need edge detection so one press
flips one config flag once. The keyboard package is optional at import time, so
missing hooks, bad key names, or non-Windows hosts simply read as not pressed.
"""

from collections.abc import Iterable

try:
    import keyboard as _kb  # type: ignore
except Exception:  # pragma: no cover
    _kb = None


def _is_pressed(key: str) -> bool:
    """Safe keyboard.is_pressed wrapper that returns False on any error."""
    if not key or _kb is None:
        return False
    try:
        return bool(_kb.is_pressed(key.strip().lower()))
    except Exception:
        return False


class ToggleHotkeyDetector:
    """Rising-edge detector for hotkey_toggle bindings."""

    def __init__(self) -> None:
        self._prev: dict[str, bool] = {}

    def poll(self, bindings: Iterable[tuple[str, object, str]]) -> int:
        """Flip target attrs on rising edges; return the flip count
        so callers can force an immediate config.save()."""
        flips = 0
        for key, section, attr in bindings:
            if not key:
                continue
            key_norm = key.strip().lower()
            if not key_norm:
                continue
            pressed = _is_pressed(key_norm)
            was_pressed = self._prev.get(key_norm, False)
            if pressed and not was_pressed:
                try:
                    cur = bool(getattr(section, attr, False))
                    setattr(section, attr, not cur)
                    flips += 1
                    print(f"[hotkey] toggle {key_norm!r} -> "
                          f"{section.__class__.__name__}.{attr} = {not cur}")
                except Exception as e:
                    print(f"[hotkey] toggle {key_norm!r} failed: {e!r}")
            self._prev[key_norm] = pressed
        return flips


class KeybindManager:
    """Thin wrapper around keyboard callbacks, polling, and edge detection."""

    def __init__(self) -> None:
        self._handles: list = []
        self._was_down: dict[str, bool] = {}

    def bind(self, key: str, callback) -> None:
        if _kb is None:
            return
        try:
            self._handles.append(_kb.add_hotkey(key, callback, suppress=False))
        except Exception as e:
            print(f"[keys] Could not bind {key!r}: {e}")

    def is_pressed(self, key: str) -> bool:
        return _is_pressed(key)

    def just_pressed(self, key: str) -> bool:
        """Edge trigger: True only on the frame the key transitions to down."""
        down = self.is_pressed(key)
        was = self._was_down.get(key, False)
        self._was_down[key] = down
        return down and not was

    def unbind_all(self) -> None:
        if _kb is None:
            self._handles.clear()
            return
        for h in self._handles:
            try:
                _kb.remove_hotkey(h)
            except Exception:
                pass
        self._handles.clear()


def is_feature_active(section) -> bool:
    """Return True when the feature is enabled and its hold key is satisfied."""
    if not bool(getattr(section, "enabled", False)):
        return False
    hold = str(getattr(section, "hotkey", "") or "").strip()
    if not hold:
        return True
    return _is_pressed(hold)


__all__ = ["ToggleHotkeyDetector", "KeybindManager", "is_feature_active"]
