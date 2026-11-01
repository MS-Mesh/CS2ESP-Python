"""
Feedback-driven compensation from live aim punch and shots fired through mouse_event. Optional held-weapon profiles override strength and start bullet;
global sensitivity and the 2x-punch delta math remain unchanged. Fractional counts stay within one valid pawn/weapon/profile context.
This module does not write game memory.
"""

from __future__ import annotations

import ctypes
import math
import os
import re
from pathlib import Path
from struct import unpack_from
from time import perf_counter

from cs2esp.features.recoil_profiles import resolve_recoil_profile
from cs2esp.offsets import Netvars
from cs2esp.recoil_runtime import HeldWeapon

_MOUSEEVENTF_MOVE = 0x0001
# How many bullets in before compensation starts (first shot is accurate)
_DEFAULT_START_BULLET = 2
# Punch magnitudes beyond this are garbage reads, not recoil
_MAX_PUNCH_DEG = 30.0


def _send_mouse_move(dx: int, dy: int) -> None:
	'''Relative mouse move through mouse_event; patchable test boundary.'''
	ctypes.windll.user32.mouse_event(_MOUSEEVENTF_MOVE, int(dx), int(dy), 0, 0)


def _steam_roots() -> list[Path]:
	roots = []
	try:
		# Keep Windows-only sensitivity discovery lazy; importing this module does not access the registry
		import winreg

		with winreg.OpenKey(winreg.HKEY_CURRENT_USER, r"Software\Valve\Steam") as key:
			roots.append(Path(str(winreg.QueryValueEx(key, "SteamPath")[0])))
	except Exception:
		pass
	roots += [Path(r"C:\Program Files (x86)\Steam"), Path(r"D:\Program Files (x86)\Steam")]
	return roots


def read_game_sensitivity() -> tuple[float, float, float] | None:
	'''(sensitivity, m_yaw, m_pitch) from the newest CS2 user convars file.'''
	newest = None
	for root in _steam_roots():
		userdata = root / "userdata"
		if not userdata.is_dir():
			continue
		for path in userdata.glob("*/730/local/cfg/cs2_user_convars*.vcfg"):
			try:
				mtime = path.stat().st_mtime
			except OSError:
				continue
			if newest is None or mtime > newest[0]:
				newest = (mtime, path)
	if newest is None:
		return None
	try:
		text = newest[1].read_text(encoding="utf-8", errors="replace")
	except OSError:
		return None

	def value(name: str, default: float) -> float:
		match = re.search(r'"%s"\s+"([-0-9.eE]+)"' % re.escape(name), text)
		return float(match.group(1)) if match else default

	return value("sensitivity", 1.25), value("m_yaw", 0.022), value("m_pitch", 0.022)


class RecoilControl:
	"""Compensate live aim-punch deltas with the existing relative mouse input boundary."""
	def __init__(self, mem, config) -> None:
		self.mem = mem
		self.config = config
		self._last_punch: tuple[float, float] | None = None
		self._carry_x = 0.0
		self._carry_y = 0.0
		self._context = None
		self._last_shots: int | None = None
		self._sens_cache: tuple[float, tuple[float, float, float] | None] = (-1e9, None)
		self._debug = bool(os.environ.get("CS2ESP_RCS_DEBUG"))
		self._diag_at = 0.0
		self._diag_sent = 0
		self._diag_shots = 0

	def _cfg(self):
		return getattr(self.config, "recoil_control", None)

	def _sensitivity(self, cfg) -> tuple[float, float, float]:
		manual = float(getattr(cfg, "sensitivity", 0.0) or 0.0)
		now = perf_counter()
		stamp, cached = self._sens_cache
		if now - stamp > 10.0:
			cached = read_game_sensitivity()
			self._sens_cache = (now, cached)
		sens, m_yaw, m_pitch = cached or (1.25, 0.022, 0.022)
		if manual > 0.0:
			sens = manual
		return sens, m_yaw, m_pitch

	def read_punch(self, local_pawn: int) -> tuple[float, float, int] | None:
		'''(punch pitch, punch yaw, shots fired) or None when unreadable.'''
		if not local_pawn:
			return None
		try:
			services = self.mem.read_long(local_pawn + Netvars.m_pAimPunchServices)
			if not (0x10000 <= services <= 0x7FFFFFFFFFFF):
				return None
			raw = self.mem.read_bytes(services + Netvars.m_predictableBaseAngle, 8)
			if len(raw) != 8:
				return None
			pitch, yaw = unpack_from("<2f", raw)
			if not (math.isfinite(pitch) and math.isfinite(yaw)):
				return None
			if abs(pitch) > _MAX_PUNCH_DEG or abs(yaw) > _MAX_PUNCH_DEG:
				return None
			shots = self.mem.read_int(local_pawn + Netvars.m_iShotsFired)
			return pitch, yaw, shots
		except Exception:
			return None

	def reset(self) -> None:
		self._last_punch = None
		self._carry_x = self._carry_y = 0.0
		self._context = None
		self._last_shots = None

	def update(self, local_pawn: int, *, active: bool, held_weapon: HeldWeapon | None = None) -> tuple[int, int]:
		'''Cancel live punch deltas with optional held-weapon overrides; invalid telemetry or a context change resets first.'''
		recoil_config = self._cfg()
		if recoil_config is None or not active:
			self.reset()
			return (0, 0)
		if held_weapon is not None and (not held_weapon.valid or not held_weapon.is_firearm or held_weapon.pawn_addr != local_pawn):
			self.reset()
			return (0, 0)
		profile = resolve_recoil_profile(recoil_config, held_weapon.weapon_key if held_weapon is not None else None)
		sample = self.read_punch(local_pawn)
		if sample is None:
			self.reset()
			return (0, 0)
		pitch, yaw, shots = sample
		context = (held_weapon.identity, profile, float(getattr(recoil_config, "sensitivity", 0.0) or 0.0)) if held_weapon is not None else (local_pawn,)
		shot_rollback = held_weapon is not None and self._last_shots is not None and shots < self._last_shots
		if context != self._context or shot_rollback:
			self.reset()
			self._context = context
		self._last_shots = shots
		start_bullet = profile.start_bullet
		if shots < start_bullet:
			# Not spraying (or first bullet): follow the punch so the next delta starts from the current recovery state
			self._last_punch = (pitch, yaw)
			self._carry_x = self._carry_y = 0.0
			return (0, 0)
		if self._last_punch is None:
			self._last_punch = (pitch, yaw)
			return (0, 0)
		last_pitch, last_yaw = self._last_punch
		self._last_punch = (pitch, yaw)

		strength_x = profile.strength_x
		strength_y = profile.strength_y
		# View kick = 2 x punch. Cancel it: punch pitch going negative (gun climbing) needs +dy,
		# punch yaw growing (view drifting left) needs +dx
		d_pitch = (pitch - last_pitch) * 2.0 * strength_y
		d_yaw = (yaw - last_yaw) * 2.0 * strength_x
		sens, m_yaw, m_pitch = self._sensitivity(recoil_config)
		counts_y = -d_pitch / max(1e-6, sens * m_pitch)
		counts_x = d_yaw / max(1e-6, sens * m_yaw)

		self._carry_x += counts_x
		self._carry_y += counts_y
		dx = int(round(self._carry_x))
		dy = int(round(self._carry_y))
		self._carry_x -= dx
		self._carry_y -= dy
		if dx or dy:
			_send_mouse_move(dx, dy)
		if self._debug:
			self._diag_sent += abs(dx) + abs(dy)
			self._diag_shots = shots
			now = perf_counter()
			if now - self._diag_at >= 0.5:
				if self._diag_sent:
					sens, m_yaw, _ = self._sensitivity(recoil_config)
					print(f"[rcs] shots={shots} punch=({pitch:+.2f},{yaw:+.2f}) counts={self._diag_sent} sens={sens:.2f} yaw_deg={m_yaw}")
				self._diag_sent = 0
				self._diag_at = now
		return (dx, dy)


__all__ = ["RecoilControl", "read_game_sensitivity"]
