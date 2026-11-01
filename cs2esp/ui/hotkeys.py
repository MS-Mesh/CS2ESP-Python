"""
Hotkey detection, keybind callbacks, and feature-gate helpers.
Hold keys are polled by features; toggle keys need edge detection so one press flips one config flag once. The keyboard package is optional at import
time, so missing hooks, bad key names, or non-Windows hosts simply read as not pressed.
"""

from collections.abc import Callable, Iterable

from cs2esp.input_guard import right_button_guard

try:
	import keyboard as _kb  # type: ignore
except Exception:
	_kb = None

try:
	import ctypes as _ct

	_user32 = _ct.windll.user32
except Exception:
	_user32 = None

try:
	from cs2esp.physical_keys import physical_keys as _physical_keys
except Exception:
	_physical_keys = None

try:
	from cs2esp.physical_mouse import physical_mouse as _physical_mouse
except Exception:
	_physical_mouse = None


# Mouse buttons the keyboard package can't see; VK codes polled directly. Most mice report side buttons as XBUTTON1/2, but some vendor
# drivers remap them to browser Back/Forward, so accept both
_MOUSE_VK = {
	"mouse1": (0x01,),
	"left mouse": (0x01,),
	"lmb": (0x01,),
	"mouse2": (0x02,),
	"right mouse": (0x02,),
	"rmb": (0x02,),
	"mouse3": (0x04,),
	"middle mouse": (0x04,),
	"mmb": (0x04,),
	"mouse4": (0x05, 0xA6),
	"xbutton1": (0x05,),
	"mouse5": (0x06, 0xA7),
	"xbutton2": (0x06,),
}

_vk_cache: dict[str, tuple[int, ...]] = {}
_group_cache: dict[str, tuple[tuple[str, tuple[int, ...]], ...]] = {}


def _vk_codes(key: str) -> tuple[int, ...]:
	'''Resolve a key name to cached Win32 VK codes; GetAsyncKeyState on a cached VK is one syscall, unlike keyboard.is_pressed rebuilding its map every call.'''
	cached = _vk_cache.get(key)
	if cached is not None:
		return cached
	codes: list[int] = []
	if key in _MOUSE_VK:
		codes.extend(_MOUSE_VK[key])
	elif _user32 is not None and _kb is not None:
		try:
			for scan_code in _kb.key_to_scan_codes(key):
				virtual_key = _user32.MapVirtualKeyW(scan_code, 3)  # MAPVK_SCANCODE_TO_VK
				if virtual_key:
					codes.append(int(virtual_key))
		except Exception:
			codes = []
	result = tuple(codes)
	_vk_cache[key] = result
	return result


def _key_groups(key: str) -> tuple[tuple[str, tuple[int, ...]], ...]:
	'''Split a bind into AND-ed key groups: ``ctrl+j`` -> ctrl AND j.'''
	cached = _group_cache.get(key)
	if cached is not None:
		return cached
	groups: list[tuple[str, tuple[int, ...]]] = []
	for token in (part.strip().lower() for part in key.split("+")):
		if not token:
			continue
		codes = _vk_codes(token)
		if not codes:
			groups = []
			break
		groups.append((token, codes))
	result = tuple(groups)
	_group_cache[key] = result
	return result


def _group_down(name: str, codes: tuple[int, ...]) -> bool:
	try:
		if name in _MOUSE_VK:
			if name in ("mouse2", "right mouse", "rmb") and right_button_guard.is_down():
				return True
			if _physical_mouse is not None:
				_physical_mouse.ensure_started()
				if _physical_mouse.available and any(_physical_mouse.is_down(virtual_key) for virtual_key in codes):
					return True
		elif _physical_keys is not None:
			_physical_keys.ensure_started()
			if _physical_keys.available:
				return any(_physical_keys.is_down(virtual_key) for virtual_key in codes)
		return any(bool(_user32.GetAsyncKeyState(virtual_key) & 0x8000) for virtual_key in codes)
	except Exception:
		return False


def is_key_pressed(key: str) -> bool:
	'''Fast VK-polled key state with chord support and keyboard fallback; physical trackers keep our own SendInput and vendor mouse drivers from gating features.'''
	if not key:
		return False
	key = key.strip().lower()
	if _user32 is not None:
		groups = _key_groups(key)
		if groups:
			return all(_group_down(name, codes) for name, codes in groups)
	if _kb is None:
		return False
	try:
		return bool(_kb.is_pressed(key))
	except Exception:
		return False


_is_pressed = is_key_pressed


class ToggleHotkeyDetector:
	"""Rising-edge detector that treats one key as one switch group; a press moves every feature sharing the key to the same state."""

	def __init__(self, on_flip: Callable[[str, str, bool], None] | None = None) -> None:
		self._prev: dict[str, bool] = {}
		self._on_flip = on_flip

	def poll(self, bindings: Iterable[tuple]) -> int:
		'''Flip every target sharing a key on one rising edge; labelled bindings report once per press to on_flip.'''
		groups: dict[str, list[tuple[object, str, str]]] = {}
		for binding in bindings:
			key, section, attr = binding[:3]
			label = str(binding[3]) if len(binding) > 3 else ""
			if not key:
				continue
			key_norm = key.strip().lower()
			if not key_norm:
				continue
			groups.setdefault(key_norm, []).append((section, attr, label))

		flips = 0
		for key_norm, targets in groups.items():
			pressed = _is_pressed(key_norm)
			was_pressed = self._prev.get(key_norm, False)
			if pressed and not was_pressed:
				try:
					target_state = not all(bool(getattr(section, attr, False)) for section, attr, _label in targets)
				except Exception:
					target_state = True
				reported: set[str] = set()
				for section, attr, label in targets:
					try:
						setattr(section, attr, target_state)
						flips += 1
						print(f"[hotkey] toggle {key_norm!r} -> {section.__class__.__name__}.{attr} = {target_state}")
					except Exception as e:
						print(f"[hotkey] toggle {key_norm!r} failed: {e!r}")
						continue
					if label and label not in reported and self._on_flip is not None:
						reported.add(label)
						try:
							self._on_flip(label, key_norm, target_state)
						except Exception:
							pass
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
		'''Edge trigger: True only on the frame the key transitions to down.'''
		down = self.is_pressed(key)
		was = self._was_down.get(key, False)
		self._was_down[key] = down
		return down and not was

	def unbind_all(self) -> None:
		if _kb is None:
			self._handles.clear()
			return
		for handle in self._handles:
			try:
				_kb.remove_hotkey(handle)
			except Exception:
				pass
		self._handles.clear()


def is_feature_active(section) -> bool:
	'''True when the feature should run now; a hold bind overrides the master toggle, otherwise the master toggle decides.'''
	if section is None:
		return False
	hold = str(getattr(section, "hotkey", "") or "").strip()
	toggle = str(getattr(section, "hotkey_toggle", "") or "").strip()
	enabled = bool(getattr(section, "enabled", False))
	if hold:
		# With a toggle bind as well, toggled-on runs it without holding; otherwise the hold key alone triggers
		if toggle and enabled:
			return True
		return _is_pressed(hold)
	return enabled


__all__ = ["ToggleHotkeyDetector", "KeybindManager", "is_feature_active"]
