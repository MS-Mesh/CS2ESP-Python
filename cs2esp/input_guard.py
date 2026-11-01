"""
Keep Mouse2 aim holds from invoking silencer changes on USP-S/M4A1-S. Filtering stays on a native input thread; other weapons and buttons are untouched.
"""

from atexit import register
from ctypes import CDLL, WinDLL, c_int, c_size_t, c_uint
from pathlib import Path


_RMB_NAMES = frozenset(("mouse2", "right mouse", "rmb"))
_MODIFIERS = {"shift": 1, "ctrl": 2, "control": 2, "alt": 4}
_SILENCED_WEAPONS = frozenset((60, 61))
_release_mouse = WinDLL("user32").mouse_event
_release_mouse.argtypes, _release_mouse.restype = [c_uint, c_uint, c_uint, c_uint, c_size_t], None


class RightButtonGuard:
	"""Lazily loaded, foreground-scoped suppression with physical hold-state continuity."""
	def __init__(self) -> None:
		self._library = None
		self._failed = False
		self._policy = None
		self._secondary_released = False

	def configure(self, aim_config, held_weapon, game_hwnd: int, *, enabled: bool) -> None:
		'''Arm only a Mouse2 aim bind on a validated silencer-capable weapon.'''
		tokens = str(getattr(aim_config, "hotkey", "") or "").strip().lower().split("+")
		tokens = [token.strip() for token in tokens if token.strip()]
		eligible = bool(enabled and game_hwnd and held_weapon is not None and held_weapon.valid
			and held_weapon.item_definition in _SILENCED_WEAPONS and any(token in _RMB_NAMES for token in tokens)
			and all(token in _RMB_NAMES or token in _MODIFIERS for token in tokens))
		mask = 0
		for token in tokens:
			mask |= _MODIFIERS.get(token, 0)
		policy = (int(game_hwnd), mask, eligible)
		if self._library is not None and self._library.ig_status() < 0:
			print('[input] Mouse2 silencer guard could not install its input filter')
			self.close()
			self._failed = True
		if self._failed:
			return
		if policy == self._policy:
			self._release_secondary(eligible)
			return
		if self._library is None:
			if not eligible:
				return
			try:
				library = CDLL(str(Path(__file__).parent / "assets/bin/inputguard.dll"))
				library.ig_start.argtypes, library.ig_start.restype = [], c_int
				library.ig_configure.argtypes, library.ig_configure.restype = [c_size_t, c_uint, c_int], None
				library.ig_captured.argtypes, library.ig_captured.restype = [], c_int
				library.ig_status.argtypes, library.ig_status.restype = [], c_int
				library.ig_stop.argtypes, library.ig_stop.restype = [], None
				if not library.ig_start():
					library.ig_stop()
					raise OSError("input guard thread did not start")
				self._library = library
			except OSError as error:
				self._failed = True
				print(f'[input] Mouse2 silencer guard unavailable: {error}')
				return
		self._library.ig_configure(*policy)
		self._policy = policy
		self._release_secondary(eligible)

	def _release_secondary(self, eligible: bool) -> None:
		'''Release the game's secondary action once; the native capture retains the physical aim hold.'''
		captured = self.is_down()
		if not captured:
			self._secondary_released = False
		elif eligible and not self._secondary_released:
			# Raw Input may receive the hardware down despite low-level suppression
			_release_mouse(0x0010, 0, 0, 0, 0)
			self._secondary_released = True

	def is_down(self) -> bool:
		return bool(self._library is not None and self._library.ig_captured())

	def close(self) -> None:
		if self._library is not None:
			self._library.ig_stop()
			self._library = None
		self._policy = None
		self._secondary_released = False


right_button_guard = RightButtonGuard()
register(right_button_guard.close)
