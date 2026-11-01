"""
BHop / auto-jump. CS2 applies a jump-velocity penalty whenever +jump is still held on touchdown, so a naive "keydown on landing" loses speed on every bounce.
The fix releases SPACE in the air so the landing frame sees an unheld key, then sends a fresh keydown before touchdown; CS2 buffers it onto the landing tick.
Polling at ~120 Hz cannot hit a 64-tick landing frame, so the press is timed off the predicted touchdown from z and vertical velocity. No memory writes.
"""

import ctypes
import ctypes.wintypes as wintypes
import math
from pathlib import Path
from random import randint, uniform
from time import perf_counter, strftime

from cs2esp.offsets import Netvars
from cs2esp.ui.hotkeys import is_feature_active


_user32 = ctypes.windll.user32

_VK_SPACE = 0x20
_SC_SPACE = 0x39

_INPUT_KEYBOARD = 1
_KEYEVENTF_KEYUP = 0x0002
_KEYEVENTF_SCANCODE = 0x0008

_FL_ONGROUND = 0x01

_MOVETYPE_NOCLIP = 8
_MOVETYPE_LADDER = 9

# Use stock gravity only for landing prediction; the landing-edge path handles missed predictions
_DEFAULT_GRAVITY = 800.0


class _KEYBDINPUT(ctypes.Structure):
	_fields_ = (("wVk", wintypes.WORD), ("wScan", wintypes.WORD), ("dwFlags", wintypes.DWORD), ("time", wintypes.DWORD), ("dwExtraInfo", ctypes.c_void_p))


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
	_fields_ = (("ki", _KEYBDINPUT), ("mi", _MOUSEINPUT))


class _INPUT(ctypes.Structure):
	_anonymous_ = ("u",)
	_fields_ = (("type", wintypes.DWORD), ("u", _INPUT_UNION))


def _make_kb_input(vk: int, scan: int, flags: int) -> _INPUT:
	'''Build a single keyboard INPUT event ready for SendInput.'''
	keyboard_input = _INPUT()
	keyboard_input.type = _INPUT_KEYBOARD
	keyboard_input.ki.wVk = vk
	keyboard_input.ki.wScan = scan
	keyboard_input.ki.dwFlags = flags
	keyboard_input.ki.time = 0
	keyboard_input.ki.dwExtraInfo = None
	return keyboard_input


def _send_space_down() -> None:
	'''One SPACE keydown — the jump edge itself.'''
	event = _make_kb_input(_VK_SPACE, _SC_SPACE, _KEYEVENTF_SCANCODE)
	events = (_INPUT * 1)(event)
	_user32.SendInput(1, ctypes.byref(events), ctypes.sizeof(_INPUT))


def _send_space_up() -> None:
	'''One SPACE keyup — must land BEFORE the next ground contact.'''
	event = _make_kb_input(_VK_SPACE, _SC_SPACE, _KEYEVENTF_KEYUP | _KEYEVENTF_SCANCODE)
	events = (_INPUT * 1)(event)
	_user32.SendInput(1, ctypes.byref(events), ctypes.sizeof(_INPUT))


def _send_space_up_down() -> None:
	'''Both edges in one syscall (fallback when the pre-release was missed).'''
	events = (_INPUT * 2)(_make_kb_input(_VK_SPACE, _SC_SPACE, _KEYEVENTF_KEYUP | _KEYEVENTF_SCANCODE), _make_kb_input(_VK_SPACE, _SC_SPACE, _KEYEVENTF_SCANCODE))
	_user32.SendInput(2, ctypes.byref(events), ctypes.sizeof(_INPUT))


class BHop:
	"""Auto-rejump on every landing while the bhop hotkey is held."""
	def __init__(self, mem, entity_system, config):
		self.mem = mem
		self.entities = entity_system
		self.config = config
		self._was_on_ground: bool = False
		self._released_in_air: bool = False
		self._jumps: int = 0
		self._jumps_since_skip: int = 0
		self._next_skip_at: int = self._roll_next_skip()
		self._last_jump_at: float = 0.0
		self._pending_jump_at: float = 0.0
		self._skip_resume_at: float = 0.0
		self._post_kickstart_grace_until: float = 0.0
		self._released_at: float = 0.0
		self._diag_last: float = 0.0
		self._ground_z: float | None = None
		self._press_held: bool = False
		self._release_pending: bool = False
		self._buffered_press: bool = False
		self._landing_check_at: float = 0.0

	def has_pending_resume(self) -> bool:
		'''True while a jump is owed or a synthetic key is held; must cover every deferred state main.py gates on.'''
		return bool(
			self._pending_jump_at
			or self._skip_resume_at
			or self._press_held
			or self._release_pending
			or (self._post_kickstart_grace_until and perf_counter() < self._post_kickstart_grace_until)
		)

	def _reset_pending(self, *, on_ground: bool = False, deferred: bool = True) -> None:
		'''Drop press/release state; deferred also clears queued jumps and humanizer skips.'''
		self._press_held = False
		self._release_pending = False
		self._buffered_press = False
		self._landing_check_at = 0.0
		self._was_on_ground = on_ground
		if deferred:
			self._released_in_air = False
			self._pending_jump_at = 0.0
			self._skip_resume_at = 0.0

	def _roll_next_skip(self) -> int:
		'''Pick the next skip target from the configured 3..15 range.'''
		bhop_config = self.config.bhop
		try:
			lower_bound = int(getattr(bhop_config, "humanize_skip_min", 3))
			upper_bound = int(getattr(bhop_config, "humanize_skip_max", 15))
		except Exception:
			lower_bound, upper_bound = 3, 15
		lower_bound = max(1, lower_bound)
		upper_bound = max(lower_bound, upper_bound)
		return randint(lower_bound, upper_bound)

	def _read_vertical(self, local_pawn: int) -> tuple[float | None, float | None]:
		'''Return (z, vz) for the landing prediction; None on read failure.'''
		try:
			scene_node = self.mem.read_long(local_pawn + Netvars.m_pGameSceneNode)
			if not scene_node:
				return None, None
			height = self.mem.read_float(scene_node + Netvars.m_vecAbsOrigin + 8)
			vertical_velocity = self.mem.read_float(local_pawn + Netvars.m_vecAbsVelocity + 8)
		except Exception:
			return None, None
		if not math.isfinite(height) or not math.isfinite(vertical_velocity):
			return None, None
		return height, vertical_velocity

	def _effective_gravity(self, local_pawn: int) -> float:
		'''Configured gravity multiplied by the pawn's live gravity scale.'''
		base = float(getattr(self.config.bhop, "gravity", _DEFAULT_GRAVITY))
		try:
			scale = self.mem.read_float(local_pawn + Netvars.m_flGravityScale)
		except Exception:
			return base
		if math.isfinite(scale) and 0.05 <= scale <= 4.0:
			return base * scale
		return base

	@staticmethod
	def _time_to_land(z: float, vz: float, ground_z: float, gravity: float) -> float | None:
		'''Seconds until the pawn falls back to the sampled ground height, or None when already below it.'''
		height = z - ground_z
		if height <= 0.0 or gravity <= 0.0:
			return None
		discriminant = vz * vz + 2.0 * gravity * height
		if discriminant < 0.0:
			return None
		return (vz + math.sqrt(discriminant)) / gravity

	def _diag(self, on_ground: bool, flags: int, z: float | None = None, vz: float | None = None, gravity: float = _DEFAULT_GRAVITY) -> None:
		'''Append one line per update to temp/bhop_diag.log when enabled.'''
		if not bool(getattr(self.config.bhop, "debug_log", False)):
			return
		try:
			now = perf_counter()
			if now - self._diag_last < 0.05:
				return
			self._diag_last = now
			local_pawn = self.entities.get_local_pawn()
			if not local_pawn:
				return
			if z is None or vz is None:
				z, vz = self._read_vertical(local_pawn)
			if z is None or vz is None:
				return
			velocity_x = self.mem.read_float(local_pawn + Netvars.m_vecAbsVelocity)
			velocity_y = self.mem.read_float(local_pawn + Netvars.m_vecAbsVelocity + 4)
			speed = math.hypot(velocity_x, velocity_y)
			land_in = self._time_to_land(z, vz, self._ground_z, gravity) if self._ground_z is not None else None
			out = Path(__file__).resolve().parents[2] / "temp" / "bhop_diag.log"
			out.parent.mkdir(parents=True, exist_ok=True)
			with out.open("a", encoding="utf-8") as log_file:
				log_file.write(
					f"{strftime('%H:%M:%S')} z={z:8.2f} vz={vz:7.2f} "
					f"speed={speed:6.1f} g={gravity:.0f} land_in="
					f"{'   n/a' if land_in is None else f'{land_in * 1000:6.1f}'} "
					f"on_ground={int(on_ground)} flags=0x{flags:X} "
					f"jumps={self._jumps} buffered={int(self._buffered_press)} "
					f"held={int(self._press_held)}\n"
				)
		except Exception:
			pass

	def release(self) -> None:
		'''Drop every deferred jump and let SPACE up (master off / shutdown).'''
		if self._press_held:
			_send_space_up()
		self._reset_pending()
		self._post_kickstart_grace_until = 0.0

	def update(self) -> None:
		'''Run one frame of landing prediction and jump dispatch.'''
		bhop_config = self.config.bhop
		if not is_feature_active(bhop_config):
			if self._press_held:
				_send_space_up()
			self._reset_pending()
			return

		now = perf_counter()

		if self._skip_resume_at and now >= self._skip_resume_at:
			_send_space_up_down()
			self._skip_resume_at = 0.0
			self._last_jump_at = now
			self._was_on_ground = False
			self._post_kickstart_grace_until = now + 0.25
			self._press_held = True
			self._release_pending = True
			self._buffered_press = False

		if self._pending_jump_at and now >= self._pending_jump_at:
			_send_space_down()
			self._last_jump_at = now
			self._pending_jump_at = 0.0
			self._jumps += 1
			self._press_held = True
			self._release_pending = True

		local_pawn = self.entities.get_local_pawn()
		if not local_pawn:
			return
		try:
			flags = self.mem.read_int(local_pawn + Netvars.m_fFlags)
		except Exception:
			return

		on_ground = bool(flags & _FL_ONGROUND)
		try:
			move_type = self.mem.read_byte(local_pawn + Netvars.m_nActualMoveType)
		except Exception:
			move_type = 0
		if move_type in (_MOVETYPE_NOCLIP, _MOVETYPE_LADDER):
			if self._press_held:
				_send_space_up()
			self._reset_pending(on_ground=on_ground, deferred=False)
			return
		gravity = self._effective_gravity(local_pawn)
		height, vertical_velocity = self._read_vertical(local_pawn)
		self._diag(on_ground, flags, height, vertical_velocity, gravity)

		if on_ground and height is not None:
			self._ground_z = height

		# Release SPACE before touchdown so the engine sees a fresh jump edge
		if not on_ground and self._release_pending:
			_send_space_up()
			self._release_pending = False
			self._press_held = False
			self._released_in_air = True
			self._released_at = now
			self._landing_check_at = 0.0

		# Buffer the press shortly before predicted touchdown to tolerate reader sampling boundaries
		if (
			not on_ground
			and not self._press_held
			and not self._buffered_press
			and not self._pending_jump_at
			and not self._skip_resume_at
			and height is not None
			and vertical_velocity is not None
			and self._ground_z is not None
		):
			land_in = self._time_to_land(height, vertical_velocity, self._ground_z, gravity)
			buffer_window = max(0.0, float(getattr(bhop_config, "buffer_ms", 40)) / 1000.0)
			min_gap = float(getattr(bhop_config, "min_gap_ms", 15)) / 1000.0
			if land_in is not None and land_in <= buffer_window and (now - self._last_jump_at) >= min_gap:
				self._jumps_since_skip += 1
				humanize = bool(getattr(bhop_config, "humanize", True))
				if humanize and self._jumps_since_skip >= self._next_skip_at:
					self._skip_resume_at = now + max(0.02, float(getattr(bhop_config, "skip_resume_delay_ms", 60)) / 1000.0)
					self._jumps_since_skip = 0
					self._next_skip_at = self._roll_next_skip()
				else:
					_send_space_down()
					self._last_jump_at = now
					self._press_held = True
					self._buffered_press = True
					self._jumps += 1

		if on_ground and not self._was_on_ground:
			self._released_in_air = False
			if self._buffered_press:
				# If the buffered press did not leave the ground, send a fresh landing edge
				self._buffered_press = False
				self._release_pending = True
				self._landing_check_at = now + 0.025
			else:
				release_settle = max(0.0, float(getattr(bhop_config, "release_settle_ms", 25)) / 1000.0)
				if self._released_at and (now - self._released_at) < release_settle:
					self._was_on_ground = on_ground
					return
				min_gap = float(getattr(bhop_config, "min_gap_ms", 15)) / 1000.0
				already_pending = bool(self._pending_jump_at or self._skip_resume_at)
				if (now - self._last_jump_at) >= min_gap and not already_pending:
					self._jumps_since_skip += 1
					humanize = bool(getattr(bhop_config, "humanize", True))
					if humanize and self._jumps_since_skip >= self._next_skip_at:
						self._skip_resume_at = now + max(0.02, float(getattr(bhop_config, "skip_resume_delay_ms", 60)) / 1000.0)
						self._jumps_since_skip = 0
						self._next_skip_at = self._roll_next_skip()
					else:
						delay_jitter = float(getattr(bhop_config, "delay_jitter_ms", 8))
						if delay_jitter > 0:
							self._pending_jump_at = now + uniform(0.0, delay_jitter / 1000.0)
						else:
							_send_space_down()
							self._last_jump_at = now
							self._jumps += 1
							self._press_held = True
							self._release_pending = True

		if on_ground and self._release_pending and self._landing_check_at and now >= self._landing_check_at:
			_send_space_up_down()
			self._press_held = True
			self._release_pending = True
			self._landing_check_at = 0.0
			self._last_jump_at = now
			self._jumps += 1

		self._was_on_ground = on_ground
