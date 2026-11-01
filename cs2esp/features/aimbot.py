"""
Smooth dwViewAngles writer with guarded WPM output. The aimbot selects the closest in-cone enemy bone, eases each flick over configured write steps and, when humanized,
bends the path: a multi-harmonic zigzag, uneven speed, an optional overshoot the tracking pass corrects, plus reaction/target-switch delays and adaptive smoothing.
All writes honor the live read-only safety gate.
"""

import math
import os
from dataclasses import dataclass
from random import choice, uniform
from struct import pack
from time import perf_counter

from cs2esp.entity import Player
from cs2esp.memory import MemoryReader
from cs2esp.offsets import Netvars, Offsets
from cs2esp.visuals.skeleton import compute_head_sphere

_DEBUG = bool(os.environ.get("CS2ESP_AIM_DEBUG"))


def _debug_choice(frame: "AimFrame", chosen: "_AimTarget") -> None:
	'''One line per second: who was in the cone and who won.'''
	now = perf_counter()
	if now - _debug_choice.last < 1.0:
		return
	_debug_choice.last = now
	rows = []
	for enemy in frame.enemies:
		point = enemy.bones.get(Netvars.BONE_HEAD)
		if point is None:
			continue
		pitch, yaw = _angles_to(frame.local_eye, point)
		rows.append((_angular_distance(frame.local_view_angles, (pitch, yaw)), enemy.name))
	rows.sort()
	print(
		f"[aim] chosen={chosen.pawn_addr:X}/{chosen.bone_label} d={chosen.delta_deg:.2f} cone=" + ", ".join(f"{name}:{distance:.1f}" for distance, name in rows[:4])
	)


_debug_choice.last = 0.0

_TRACK_DEADZONE_DEG = 0.05
_SLOT_REFRESH_S = 2.0


@dataclass(frozen=True, slots=True)
class AimFrame:
	"""Per-frame immutable adapter consumed by WPMAimbot."""
	local_pawn: int
	local_view_angles: tuple[float, float]
	local_eye: tuple[float, float, float]
	enemies: list[Player]


# Use approximate bone heights only until real bone data becomes available
_FALLBACK_BONE_HEIGHTS: dict[int, float] = {Netvars.BONE_HEAD: 64.0, Netvars.BONE_CHEST: 54.0, Netvars.BONE_PELVIS: 38.0}


@dataclass(frozen=True, slots=True)
class _AimTarget:
	"""Per-tick selected target."""
	pawn_addr: int
	bone_label: str
	angles: tuple[float, float]
	delta_deg: float


_BONE_PRIORITY: dict[str, tuple[tuple[int, str], ...]] = {
	"head": ((Netvars.BONE_HEAD, "head"), (Netvars.BONE_CHEST, "chest"), (Netvars.BONE_PELVIS, "pelvis")),
	"body": ((Netvars.BONE_CHEST, "chest"), (Netvars.BONE_PELVIS, "pelvis")),
}


class WPMAimbot:
	'''Smooth dwViewAngles aimbot with guarded, humanized flick interpolation.'''
	def __init__(self, config, mem: MemoryReader) -> None:
		self.config = config
		self.cfg = config.aimbot
		self.mem = mem
		self._cursor: int = 0
		self._flick_start: tuple[float, float] | None = None
		self._d_pitch_full: float = 0.0
		self._d_yaw_full: float = 0.0
		self._delta_mag: float = 0.0
		self._flick_perp_sign: int = 1
		self._flick_curv_mag: float = 0.0
		self._flick_waves: tuple[tuple[int, float], ...] = ((1, 1.0),)
		self._flick_warp: float = 0.0
		self._flick_overshoot: float = 0.0
		self._flick_steps: int = 0
		self._hold_until: float = 0.0
		self._last_target_id: tuple[int, str] = (0, "")
		self._was_active: bool = False
		self._last_visible: dict[int, float] = {}
		self._local_slot: int = 0
		self._local_slot_t: float = -_SLOT_REFRESH_S

	def _resolve_local_slot(self) -> int:
		'''Entity-list slot of the local controller, cached for a few seconds.'''
		now = perf_counter()
		if now - self._local_slot_t < _SLOT_REFRESH_S:
			return self._local_slot
		self._local_slot_t = now
		slot_found = 0
		try:
			local_controller = self.mem.read_long(self.mem.client(Offsets.dwLocalPlayerController))
			entity_list = self.mem.read_long(self.mem.client(Offsets.dwEntityList))
		except Exception:
			local_controller = entity_list = 0
		if local_controller and entity_list:
			for slot in range(1, 65):
				try:
					list_entry = self.mem.read_long(entity_list + 0x8 * ((slot & 0x7FFF) >> 9) + 0x10)
					if not list_entry:
						continue
					if self.mem.read_long(list_entry + 0x70 * (slot & 0x1FF)) == local_controller:
						slot_found = slot
						break
				except Exception:
					continue
		self._local_slot = slot_found
		return slot_found

	def _is_visible(self, pawn_addr: int) -> bool:
		'''Engine line-of-sight: m_bSpotted or our bit in m_bSpottedByMask; an unresolved slot skips the mask filter, a failed read on a resolved slot rejects.'''
		base = pawn_addr + Netvars.m_entitySpottedState
		# Prefer the team spotted flag; retain per-slot visibility as a secondary signal
		spotted = 0
		mask = 0
		try:
			raw = self.mem.read_bytes(base + Netvars.m_bSpotted, 8)
		except Exception:
			raw = b""
		if len(raw) >= 8:
			spotted = raw[0]
			mask = int.from_bytes(raw[4:8], "little")
		else:
			try:
				spotted = self.mem.read_byte(base + Netvars.m_bSpotted)
			except Exception:
				spotted = 0
			try:
				mask = self.mem.read_uint(base + Netvars.m_bSpottedByMask)
			except Exception:
				mask = 0
		if spotted:
			return True
		slot = self._resolve_local_slot()
		if slot <= 0:
			return True
		return bool((mask >> (slot - 1)) & 1)

	def update(self, players: list[Player], local_pawn: int, local_team: int, local_pos: tuple[float, float, float], active: bool) -> None:
		'''Run one frame of target selection and smooth interpolation.'''
		if not active or local_pawn == 0:
			self.reset()
			return
		self._was_active = True

		try:
			view_angles = self.mem.read_vec3(self.mem.client(Offsets.dwViewAngles))
		except Exception:
			view_angles = (0.0, 0.0, 0.0)

		eye: tuple[float, float, float] = local_pos
		try:
			scene_node = self.mem.read_long(local_pawn + Netvars.m_pGameSceneNode)
			if scene_node:
				origin = self.mem.read_vec3(scene_node + Netvars.m_vecAbsOrigin)
				view_offset = self.mem.read_vec3(local_pawn + Netvars.m_vecViewOffset)
				eye = tuple(origin[index] + view_offset[index] for index in range(3))
		except Exception:
			pass

		visibility = str(getattr(self.cfg, "visibility", "visible") or "visible")
		grace_s = max(0.0, float(getattr(self.cfg, "visible_grace_ms", 250.0))) / 1000.0
		now = perf_counter()
		enemies: list[Player] = []
		for player in players:
			if not (player.pawn_addr and player.team != local_team and player.health > 0 and player.life_state == 0):
				continue
			# Brief visibility grace absorbs update gaps; through-wall selection still requires explicit opt-in
			if visibility != "any":
				pawn = int(player.pawn_addr)
				if self._is_visible(pawn):
					self._last_visible[pawn] = now
				else:
					flicker = 0.12 if visibility == "visible" else grace_s
					if now - self._last_visible.get(pawn, -1e9) > flicker:
						continue
			if not player.bones:
				if not any(player.position):
					continue
				player.bones = {bone: (player.position[0], player.position[1], player.position[2] + height) for bone, height in _FALLBACK_BONE_HEIGHTS.items()}
			enemies.append(player)

		aim_frame = AimFrame(local_pawn, (view_angles[0], view_angles[1]), eye, enemies)
		target = self._select_target(aim_frame)
		if target is None:
			return
		if _DEBUG:
			_debug_choice(aim_frame, target)

		target_id = (target.pawn_addr, target.bone_label)
		if target_id != self._last_target_id:
			previous_pawn = self._last_target_id[0]
			self._cursor = 0
			self._last_target_id = target_id
			if target.pawn_addr != previous_pawn:
				self._hold_until = now + self._acquire_delay_s(previous_pawn)

		if now < self._hold_until:
			return
		self._smooth_write(aim_frame, target)

	def reset(self) -> None:
		'''Forget this activation's reaction and trajectory without changing settings or the visibility cache.'''
		self._cursor = 0
		self._last_target_id = (0, "")
		self._was_active = False
		self._flick_start = None
		self._flick_steps = 0
		self._hold_until = 0.0

	def _acquire_delay_s(self, previous_pawn: int) -> float:
		'''Human pause before the first write: reaction time on a fresh press, re-target delay on an enemy switch, none on a bone change.'''
		if previous_pawn:
			return max(0.0, float(getattr(self.cfg, "switch_delay_ms", 0.0))) / 1000.0
		low = max(0.0, float(getattr(self.cfg, "reaction_min_ms", 0.0)))
		high = max(low, float(getattr(self.cfg, "reaction_max_ms", low)))
		return uniform(low, high) / 1000.0 if high > 0.0 else 0.0

	def _select_target(self, frame: AimFrame) -> _AimTarget | None:
		'''Select the closest in-cone target from the bone-priority table.'''
		current_view = frame.local_view_angles
		max_fov = float(getattr(self.cfg, "max_fov_deg", 30.0))
		priority = _BONE_PRIORITY.get(str(getattr(self.cfg, "bone_priority", "head")), _BONE_PRIORITY["head"])

		if priority and priority[0][1] == "head":
			head_bone = priority[0][0]
			best_head: _AimTarget | None = None
			for enemy in frame.enemies:
				bone_position = self._head_point(enemy, frame.local_eye)
				if bone_position is None:
					bone_position = enemy.bones.get(head_bone)
				if bone_position is None:
					continue
				pitch, yaw = _angles_to(frame.local_eye, bone_position)
				angular_distance = _angular_distance(current_view, (pitch, yaw))
				if angular_distance > max_fov:
					continue
				if best_head is None or angular_distance < best_head.delta_deg:
					best_head = _AimTarget(pawn_addr=int(enemy.pawn_addr), bone_label="head", angles=(pitch, yaw), delta_deg=angular_distance)
			if best_head is not None:
				return best_head
			scan = priority[1:]
		else:
			scan = priority

		best: _AimTarget | None = None
		for enemy in frame.enemies:
			for bone_index, label in scan:
				bone_position = enemy.bones.get(bone_index)
				if bone_position is None:
					continue
				pitch, yaw = _angles_to(frame.local_eye, bone_position)
				angular_distance = _angular_distance(current_view, (pitch, yaw))
				if angular_distance > max_fov:
					continue
				if best is None or angular_distance < best.delta_deg:
					best = _AimTarget(pawn_addr=int(enemy.pawn_addr), bone_label=label, angles=(pitch, yaw), delta_deg=angular_distance)
		return best

	def _head_point(self, enemy, eye) -> tuple[float, float, float] | None:
		'''Cranium centre shared with the skeleton ring and triggerbot; BONE_HEAD is the neck joint, so the calibrated head sphere is the aim point.'''
		visuals = getattr(self.config, "visuals", None)
		skeleton_cfg = getattr(visuals, "skeleton", None) if visuals is not None else None
		if skeleton_cfg is None or not getattr(enemy, "bones", None):
			return None
		try:
			sphere = compute_head_sphere(enemy, skeleton_cfg, camera_pos=eye)
		except Exception:
			return None
		if sphere is None:
			return None
		center, _radius = sphere
		return (float(center[0]), float(center[1]), float(center[2]))

	def _flick_step_count(self, base_steps: int) -> int:
		'''Steps for this flick; adaptive mode spends more on bigger turns.'''
		if not bool(getattr(self.cfg, "adaptive_steps", False)):
			return base_steps
		max_fov = max(1.0, float(getattr(self.cfg, "max_fov_deg", 30.0)))
		share = min(1.0, self._delta_mag / max_fov)
		return max(1, int(round(base_steps * (0.6 + 0.9 * share))))

	def _roll_humanizer(self) -> None:
		'''Per-flick random path shape (fixed for the whole flick).'''
		humanize = bool(getattr(self.cfg, "humanize_path", True))
		curvature = float(getattr(self.cfg, "curvature", 0.0))
		self._flick_waves = ((1, 1.0),)
		self._flick_warp = 0.0
		self._flick_overshoot = 0.0
		if not humanize:
			self._flick_curv_mag = 0.0
			return
		if curvature > 0.0:
			self._flick_perp_sign = choice((-1, 1))
			self._flick_curv_mag = curvature * self._delta_mag * uniform(0.5, 1.0)
			# sin(k*pi*t) is zero at both endpoints, so mixed harmonics bend the path without moving them
			waves = max(1, min(5, int(getattr(self.cfg, "zigzag_waves", 1))))
			mix = [(1, 1.0)]
			for harmonic in range(2, waves + 1):
				mix.append((harmonic, choice((-1.0, 1.0)) * uniform(0.25, 0.6) / harmonic**0.5))
			norm = sum(abs(weight) for harmonic_index, weight in mix)
			self._flick_waves = tuple((harmonic, weight / norm) for harmonic, weight in mix)
		else:
			self._flick_curv_mag = 0.0
		variance = max(0.0, min(0.9, float(getattr(self.cfg, "speed_variance", 0.0))))
		if variance > 0.0:
			self._flick_warp = uniform(-variance, variance)
		overshoot = max(0.0, float(getattr(self.cfg, "overshoot_pct", 0.0))) / 100.0
		if overshoot > 0.0 and self._delta_mag > 1.0:
			self._flick_overshoot = overshoot * uniform(0.4, 1.0)

	def _smooth_write(self, frame: AimFrame, target: _AimTarget) -> None:
		'''Eased flick toward target.angles, humanized per the config.'''
		base_steps = max(1, int(getattr(self.cfg, "smooth_steps", 6)))
		steps = self._flick_steps if self._cursor > 0 and self._flick_steps else base_steps
		if self._cursor >= steps:
			# Track after the flick so moving targets and overshoot still converge
			self._track_write(frame, target, steps)
			return

		current_pitch, current_yaw = frame.local_view_angles
		tgt_pitch, tgt_yaw = target.angles

		if self._cursor == 0:
			self._flick_start = (current_pitch, current_yaw)
			self._d_pitch_full = tgt_pitch - current_pitch
			self._d_yaw_full = _wrap_yaw(tgt_yaw - current_yaw)
			self._delta_mag = math.hypot(self._d_pitch_full, self._d_yaw_full)
			self._roll_humanizer()
			steps = self._flick_steps = self._flick_step_count(base_steps)

		assert self._flick_start is not None
		start_pitch, start_yaw = self._flick_start
		reach = 1.0 + self._flick_overshoot
		full_pitch_delta = self._d_pitch_full * reach
		full_yaw_delta = self._d_yaw_full * reach
		end_pitch = start_pitch + full_pitch_delta
		end_yaw = start_yaw + full_yaw_delta

		step_number = self._cursor + 1
		eased = 0.5 * (1.0 - math.cos(math.pi * step_number / steps))
		if self._flick_warp:
			# Monotone for |warp| < 1 (derivative 1 + warp*cos >= 0): uneven speed with fixed endpoints
			eased += self._flick_warp / (2.0 * math.pi) * math.sin(2.0 * math.pi * eased)

		pos_pitch = start_pitch + full_pitch_delta * eased
		pos_yaw = start_yaw + full_yaw_delta * eased

		# Zero at both endpoints; one hump for a single wave, a zigzag for more
		shape = 0.0
		for harmonic, weight in self._flick_waves:
			shape += weight * math.sin(harmonic * math.pi * eased)
		bump = self._flick_perp_sign * self._flick_curv_mag * shape
		if self._delta_mag > 1e-6:
			perp_pitch = (-self._d_yaw_full / self._delta_mag) * bump
			perp_yaw = (self._d_pitch_full / self._delta_mag) * bump
		else:
			perp_pitch = 0.0
			perp_yaw = 0.0

		jitter_deg = float(getattr(self.cfg, "jitter_deg", 0.05))
		pitch_jitter = uniform(-jitter_deg, jitter_deg)
		yaw_jitter = uniform(-jitter_deg, jitter_deg)

		cand_pitch = pos_pitch + perp_pitch + pitch_jitter
		cand_yaw = pos_yaw + perp_yaw + yaw_jitter

		if len(self._flick_waves) > 1 and self._delta_mag > 1e-6:
			# Clamp forward progress without removing lateral motion
			unit_p = self._d_pitch_full / self._delta_mag
			unit_y = self._d_yaw_full / self._delta_mag
			rel_p = cand_pitch - start_pitch
			rel_y = _wrap_yaw(cand_yaw - start_yaw)
			along = rel_p * unit_p + rel_y * unit_y
			clamped = min(max(along, 0.0), self._delta_mag * reach)
			rel_p += (clamped - along) * unit_p
			rel_y += (clamped - along) * unit_y
			cand_pitch = max(-89.0, min(89.0, start_pitch + rel_p))
			cand_yaw = _wrap_yaw(start_yaw + rel_y)
		else:
			# Clamps against fixed flick-start geometry, not the drifting committed view
			cand_pitch = _clamp_no_overshoot(start_pitch, end_pitch, cand_pitch)
			cand_pitch = max(cand_pitch, start_pitch) if full_pitch_delta >= 0 else min(cand_pitch, start_pitch)
			cand_pitch = max(-89.0, min(89.0, cand_pitch))
			cand_yaw = _clamp_no_overshoot_yaw(start_yaw, end_yaw, cand_yaw)

		if not self._write_view_angles(cand_pitch, cand_yaw):
			return
		self._cursor += 1

	def _write_view_angles(self, pitch: float, yaw: float) -> bool:
		'''Commit one dwViewAngles payload; False when the read-only guard or a write error blocks it.'''
		if bool(getattr(self.config.anti_detection, "read_only_mode", False)):
			return False
		try:
			payload = pack("<3f", float(pitch), float(yaw), 0.0)
			self.mem.pm.write_bytes(self.mem.client(Offsets.dwViewAngles), payload, 12)
		except Exception:
			return False
		return True

	def _track_write(self, frame: AimFrame, target: _AimTarget, steps: int) -> None:
		'''Follow the remaining error after the flick, with optional configured tremor.'''
		current_pitch, current_yaw = frame.local_view_angles
		tgt_pitch, tgt_yaw = target.angles
		d_pitch = tgt_pitch - current_pitch
		d_yaw = _wrap_yaw(tgt_yaw - current_yaw)
		if math.hypot(d_pitch, d_yaw) < _TRACK_DEADZONE_DEG:
			return
		gain = 1.0 / max(1, int(steps) // 2)
		tremor = max(0.0, float(getattr(self.cfg, "tremor_deg", 0.0)))
		tremor_pitch = uniform(-tremor, tremor) if tremor else 0.0
		tremor_yaw = uniform(-tremor, tremor) if tremor else 0.0
		cand_pitch = max(-89.0, min(89.0, current_pitch + d_pitch * gain + tremor_pitch))
		cand_yaw = _wrap_yaw(current_yaw + d_yaw * gain + tremor_yaw)
		self._write_view_angles(cand_pitch, cand_yaw)


def _wrap_yaw(angle: float) -> float:
	'''Shortest signed yaw arc in [-180, +180] degrees.'''
	return (angle + 540.0) % 360.0 - 180.0


def _angles_to(eye: tuple[float, float, float], point: tuple[float, float, float]) -> tuple[float, float]:
	'''Pitch/yaw in degrees from eye to point under the dwViewAngles convention.'''
	delta_x = point[0] - eye[0]
	delta_y = point[1] - eye[1]
	delta_z = point[2] - eye[2]
	flat_distance = math.hypot(delta_x, delta_y)
	if flat_distance < 1e-6:
		return (-90.0 if delta_z > 0 else 90.0, 0.0)
	pitch = -math.degrees(math.atan2(delta_z, flat_distance))
	yaw = math.degrees(math.atan2(delta_y, delta_x))
	return (pitch, yaw)


def _angular_distance(cur: tuple[float, float], target: tuple[float, float]) -> float:
	'''Combined yaw/pitch angular distance (degrees) with yaw seam wrap.'''
	pitch_delta = target[0] - cur[0]
	yaw_delta = _wrap_yaw(target[1] - cur[1])
	return math.hypot(pitch_delta, yaw_delta)


def _clamp_no_overshoot(cur: float, tgt: float, candidate: float) -> float:
	'''Project candidate onto [cur, tgt] so pitch never crosses past tgt.'''
	if tgt >= cur:
		return min(candidate, tgt)
	return max(candidate, tgt)


def _clamp_no_overshoot_yaw(cur: float, tgt: float, candidate: float) -> float:
	'''Yaw-aware no-overshoot clamp using the shortest-arc direction.'''
	arc_signed = _wrap_yaw(tgt - cur)
	if arc_signed == 0.0:
		return tgt
	# Anchored on target so diametric 180-degree flicks clamp correctly
	past_signed = _wrap_yaw(candidate - tgt)
	if (arc_signed > 0.0 and past_signed >= 0.0) or (arc_signed < 0.0 and past_signed <= 0.0):
		return tgt
	candidate_signed = _wrap_yaw(candidate - cur)
	if arc_signed > 0.0:
		clamped = max(0.0, candidate_signed)
	else:
		clamped = min(0.0, candidate_signed)
	return cur + clamped
