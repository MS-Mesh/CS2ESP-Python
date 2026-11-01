"""
Non-blocking firearm clickers: Rapid-fire supports continuous taps or fixed-size bursts; Auto-pistol follows supported semi-auto handguns and weapon readiness.
Both use the shared held-weapon snapshot. CPS sets the nominal input cadence, not the game's weapon fire rate.
"""

import ctypes
from random import uniform
from time import perf_counter

from cs2esp.entity import read_game_time
from cs2esp.offsets import Netvars
from cs2esp.ui.hotkeys import is_feature_active

_user32 = ctypes.windll.user32
_MOUSEEVENTF_LEFTDOWN = 0x0002
_MOUSEEVENTF_LEFTUP = 0x0004

_DEFAULT_CPS = 10.0
_MIN_CPS = 1.0
_MAX_CPS = 25.0
_DEFAULT_JITTER_PCT = 0.25
_MAX_JITTER_PCT = 0.5
_RELEASE_MIN_S = 0.010
_RELEASE_MAX_S = 0.020
_SEMI_AUTO_PISTOLS = frozenset((1, 2, 3, 4, 30, 32, 36, 61))


def _mouse_down() -> None:
	'''Send a left-button press; kept as a patchable test boundary.'''
	_user32.mouse_event(_MOUSEEVENTF_LEFTDOWN, 0, 0, 0, 0)


def _mouse_up() -> None:
	'''Send a left-button release; kept as a patchable test boundary.'''
	_user32.mouse_event(_MOUSEEVENTF_LEFTUP, 0, 0, 0, 0)


class _ClickerBase:
	"""Shared press/release cadence with subclass-specific weapon eligibility."""
	cfg_section: str = ""
	max_cps: float = _MAX_CPS

	def __init__(self, config, mem=None):
		self.config = config
		self.mem = mem
		self._next_at: float = 0.0
		self._release_at: float = 0.0
		self._burst_clicks = 0
		self._weapon_identity = None

	def _cfg(self):
		'''Return this clicker's live config section (also a test seam).'''
		return getattr(self.config, self.cfg_section)

	def has_pending_release(self) -> bool:
		'''True while a click's mouse-up is still owed (see main.py gating).'''
		return bool(self._release_at)

	def release(self) -> None:
		'''End an owned press and discard a partial burst when activation or weapon context changes.'''
		if self._release_at:
			_mouse_up()
			self._release_at = 0.0
		self._next_at = 0.0
		self._burst_clicks = 0
		self._weapon_identity = None

	def accepts_weapon(self, held_weapon) -> bool:
		'''Generic taps accept a validated firearm; None retains the standalone Rapid-fire call contract.'''
		return held_weapon is None or bool(held_weapon.valid and held_weapon.is_firearm)

	def _weapon_ready(self, held_weapon) -> bool:
		return True

	def update(self, *, held_weapon=None) -> None:
		'''Advance the click state machine by one frame.'''
		now = perf_counter()

		# Deferred mouse-up; click never blocks the frame
		if self._release_at and now >= self._release_at:
			_mouse_up()
			self._release_at = 0.0

		clicker_config = self._cfg()
		if not is_feature_active(clicker_config) or not self.accepts_weapon(held_weapon):
			self.release()
			return
		identity = held_weapon.identity if held_weapon is not None else None
		if identity != self._weapon_identity:
			self.release()
			self._weapon_identity = identity
		if now < self._next_at or self._release_at:
			return
		if not self._weapon_ready(held_weapon):
			return

		click_rate = float(getattr(clicker_config, "cps", _DEFAULT_CPS))
		click_rate = max(_MIN_CPS, min(self.max_cps, click_rate))
		interval = 1.0 / click_rate
		jitter = float(getattr(clicker_config, "jitter_pct", _DEFAULT_JITTER_PCT))
		jitter = max(0.0, min(_MAX_JITTER_PCT, jitter))
		interval *= 1.0 + uniform(-jitter, jitter)

		_mouse_down()
		self._release_at = now + uniform(_RELEASE_MIN_S, _RELEASE_MAX_S)
		self._next_at = now + interval
		if self.cfg_section == "rapidfire" and str(getattr(clicker_config, "pattern", "continuous")) == "burst":
			self._burst_clicks += 1
			if self._burst_clicks >= max(2, min(10, int(getattr(clicker_config, "burst_size", 3)))):
				pause = max(0.0, min(1.0, float(getattr(clicker_config, "burst_pause_ms", 150.0)) / 1000.0))
				self._next_at = max(self._next_at, self._release_at + pause)
				self._burst_clicks = 0
		else:
			self._burst_clicks = 0


class RapidFire(_ClickerBase):
	"""Cadenced firearm taps with an optional burst count and pause."""
	cfg_section = "rapidfire"


class AutoPistol(_ClickerBase):
	"""Semi-auto handguns only; CZ75-Auto and the R8's charge action are deliberately excluded."""
	cfg_section = "autopistol"
	max_cps = 12.0

	def accepts_weapon(self, held_weapon) -> bool:
		return bool(held_weapon is not None and held_weapon.valid and held_weapon.is_firearm and held_weapon.item_definition in _SEMI_AUTO_PISTOLS)

	def _weapon_ready(self, held_weapon) -> bool:
		'''Use the same primary-attack tick contract as the existing trigger; retain CPS pacing when the game clock is unavailable.'''
		if self.mem is None:
			return True
		game_time = read_game_time(self.mem)
		if game_time is None:
			return True
		try:
			next_tick = self.mem.read_int(held_weapon.weapon_addr + Netvars.m_nNextPrimaryAttackTick)
		except (OSError, ValueError, TypeError):
			return False
		return 0 <= next_tick < 64_000_000 and next_tick / 64.0 <= game_time + 0.002
