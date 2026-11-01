"""
Vanilla triggerbot for engine-raycast and overlay-assisted hit tests. Mouse buttons still register through SendInput/mouse_event even when Raw Input owns view movement.
Timing uses reaction delay, jitter, a global click floor, and per-target cooldowns; wall checks come from m_iIDEntIndex or overlay modes.
"""

import ctypes
import math
from pathlib import Path
from random import uniform
from time import perf_counter, strftime

from cs2esp.offsets import Offsets, Netvars
from cs2esp.ui.hotkeys import is_feature_active
from cs2esp.entity import Player, read_game_time
from cs2esp.visuals.bone_extensions import iter_targets, iter_segments
from cs2esp.visuals.math_utils import world_to_screen
from cs2esp.visuals.chams_pixel_probe import get_alpha as get_chams_probe_alpha, get_last_probe_age_s as get_chams_probe_age_s
from cs2esp.visuals.skeleton import compute_head_outline_polygon


# Per-segment world half-widths for the fat-skeleton silhouette test
_SILHOUETTE_SEGMENTS: tuple[tuple[int, int, float], ...] = (
	(Netvars.BONE_CHEST, Netvars.BONE_SPINE, 11.0),
	(Netvars.BONE_SPINE, Netvars.BONE_PELVIS, 10.0),
	(Netvars.BONE_L_CLAV, Netvars.BONE_R_CLAV, 7.0),
	(Netvars.BONE_L_THIGH, Netvars.BONE_R_THIGH, 7.0),
	(Netvars.BONE_CHEST, Netvars.BONE_NECK, 5.0),
	(Netvars.BONE_NECK, Netvars.BONE_HEAD, 4.0),
	(Netvars.BONE_L_CLAV, Netvars.BONE_L_SHOULDER, 5.0),
	(Netvars.BONE_L_SHOULDER, Netvars.BONE_L_ELBOW, 4.0),
	(Netvars.BONE_L_ELBOW, Netvars.BONE_L_HAND, 3.5),
	(Netvars.BONE_R_CLAV, Netvars.BONE_R_SHOULDER, 5.0),
	(Netvars.BONE_R_SHOULDER, Netvars.BONE_R_ELBOW, 4.0),
	(Netvars.BONE_R_ELBOW, Netvars.BONE_R_HAND, 3.5),
	(Netvars.BONE_PELVIS, Netvars.BONE_L_THIGH, 5.0),
	(Netvars.BONE_L_THIGH, Netvars.BONE_L_KNEE, 5.5),
	(Netvars.BONE_L_KNEE, Netvars.BONE_L_FOOT, 4.0),
	(Netvars.BONE_PELVIS, Netvars.BONE_R_THIGH, 5.0),
	(Netvars.BONE_R_THIGH, Netvars.BONE_R_KNEE, 5.5),
	(Netvars.BONE_R_KNEE, Netvars.BONE_R_FOOT, 4.0),
)


def crosshair_on_fat_skeleton(
	bones: dict,
	view_matrix,
	screen_w: int,
	screen_h: int,
	eye: tuple[float, float, float],
	cx_px: float,
	cy_px: float,
	extra_pad_px: float = 0.0,
	tightness: float = 1.0,
) -> bool:
	'''True iff (cx_px, cy_px) sits inside the painted body silhouette.'''
	if tightness <= 0.0:
		tightness = 0.0
	focal = screen_w * 0.5
	for start_bone, end_bone, half_width_world in _SILHOUETTE_SEGMENTS:
		start_world = bones.get(start_bone)
		end_world = bones.get(end_bone)
		if start_world is None or end_world is None:
			continue
		start_screen = world_to_screen(start_world, view_matrix, screen_w, screen_h)
		end_screen = world_to_screen(end_world, view_matrix, screen_w, screen_h)
		if start_screen is None or end_screen is None:
			continue
		midpoint_x = (start_world[0] + end_world[0]) * 0.5
		midpoint_y = (start_world[1] + end_world[1]) * 0.5
		midpoint_z = (start_world[2] + end_world[2]) * 0.5
		delta_x = midpoint_x - eye[0]
		delta_y = midpoint_y - eye[1]
		delta_z = midpoint_z - eye[2]
		depth = math.sqrt(delta_x * delta_x + delta_y * delta_y + delta_z * delta_z)
		if depth < 1.0:
			depth = 1.0
		pad_px = (half_width_world * tightness) * focal / depth + extra_pad_px
		segment_x = end_screen[0] - start_screen[0]
		segment_y = end_screen[1] - start_screen[1]
		segment_length_sq = segment_x**2 + segment_y**2
		if segment_length_sq < 1e-6:
			closest_x, closest_y = start_screen
		else:
			along = ((cx_px - start_screen[0]) * segment_x + (cy_px - start_screen[1]) * segment_y) / segment_length_sq
			along = max(0.0, min(1.0, along))
			closest_x = start_screen[0] + segment_x * along
			closest_y = start_screen[1] + segment_y * along
		distance_px = math.hypot(cx_px - closest_x, cy_px - closest_y)
		if distance_px < pad_px:
			return True
	return False


def _convex_hull_2d(points: list[tuple[float, float]]) -> list[tuple[float, float]]:
	'''Andrew's monotone-chain CCW hull. <3 points returns input.'''
	unique_points = sorted(set(points))
	if len(unique_points) < 3:
		return unique_points

	def cross(o, a, b):
		return (a[0] - o[0]) * (b[1] - o[1]) - (a[1] - o[1]) * (b[0] - o[0])

	lower = []
	for point in unique_points:
		while len(lower) >= 2 and cross(lower[-2], lower[-1], point) <= 0:
			lower.pop()
		lower.append(point)
	upper = []
	for point in reversed(unique_points):
		while len(upper) >= 2 and cross(upper[-2], upper[-1], point) <= 0:
			upper.pop()
		upper.append(point)
	return lower[:-1] + upper[:-1]


def _point_in_convex_polygon(px: float, py: float, polygon: list[tuple[float, float]]) -> bool:
	'''Cross-product point-in-polygon test for a CCW convex polygon. Edge cases (cross == 0) count as inside.'''
	point_count = len(polygon)
	if point_count < 3:
		return False
	for point_index in range(point_count):
		start_x, start_y = polygon[point_index]
		end_x, end_y = polygon[(point_index + 1) % point_count]
		cross = (end_x - start_x) * (py - start_y) - (end_y - start_y) * (px - start_x)
		if cross < 0.0:
			return False
	return True


def _ring_contains(polygon, px: float, py: float) -> bool:
	'''True when (px, py) lies inside the cleaned convex hull of a head-ring polygon.'''
	if polygon is None:
		return False
	clean_points = [point for point in polygon if point is not None]
	if len(clean_points) < 3:
		return False
	hull = _convex_hull_2d(clean_points)
	return len(hull) >= 3 and _point_in_convex_polygon(px, py, hull)


def _select_wh_mode(config) -> str:
	'''Pick which hit-test the WH-compliant triggerbot uses.'''
	if not bool(getattr(config.wallhack, "enabled", True)):
		return "default"
	glow_sec = getattr(config, "glow", None)
	if glow_sec is not None and bool(getattr(glow_sec, "enabled", False)):
		return "glow"
	visuals = getattr(config, "visuals", None)
	if visuals is None:
		return "default"
	if bool(getattr(getattr(visuals, "chams", None), "enabled", False)):
		return "chams"
	skeleton = getattr(visuals, "skeleton", None)
	if skeleton is not None and bool(getattr(skeleton, "enabled", False)) and bool(getattr(skeleton, "head_outline_enabled", False)):
		return "skel_head"
	return "default"


# Bolt-action rifles use item identity; AUG and SG553 remain eligible for unscoped fire
_SCOPED_WEAPON_IDS = frozenset((9, 40))  # AWP, SSG08

_WEAPON_CLASS_IDS: dict[str, frozenset[int]] = {
	"knife": frozenset(set(range(500, 527)) | {42, 59, 80}),
	"zeus": frozenset({31}),
	"grenade": frozenset(range(43, 49)),
	"shotgun": frozenset({25, 27, 29, 35}),  # Nova, XM1014, MAG-7, Sawed-Off
	"smg": frozenset({17, 19, 23, 24, 26, 34}),  # MAC-10, MP9, MP5, UMP, P90, MP7
	"rifle": frozenset({7, 8, 10, 13, 16, 60}),  # AK, M4s, Galil, FAMAS, SG553
	"sniper": frozenset({9, 11, 38, 40}),  # AWP, G3SG1, SCAR-20, SSG08
	"pistol": frozenset({1, 2, 3, 4, 30, 32, 36, 61, 63, 64}),
	"lmg": frozenset({14, 28}),  # M249, Negev
}

_WEAPON_TOGGLE_GROUPS: tuple[tuple[str, tuple[str, ...]], ...] = (
	("allow_grenades", ("grenade",)),
	("allow_knife_zeus", ("knife", "zeus")),
	("allow_shotguns", ("shotgun",)),
	("allow_snipers", ("sniper",)),
	("allow_rifles", ("rifle",)),
	("allow_smgs", ("smg",)),
	("allow_pistols", ("pistol",)),
	("allow_lmg", ("lmg",)),
)


def weapon_class_allowed(cfg, item_def: int) -> bool:
	'''True when the active weapon's class is whitelisted for the triggerbot. Unknown item-defs fail open, matching the behavior before these gates.'''
	item_def = int(item_def) & 0xFFFF
	if item_def <= 0:
		return True
	for toggle_attr, classes in _WEAPON_TOGGLE_GROUPS:
		for cls in classes:
			if item_def in _WEAPON_CLASS_IDS.get(cls, frozenset()):
				return bool(getattr(cfg, toggle_attr, True))
	return True


_user32 = ctypes.windll.user32

# Use one SendInput click path; a second mouse_event call would duplicate input
_INPUT_MOUSE = 0
_MOUSEEVENTF_LEFTDOWN = 0x0002
_MOUSEEVENTF_LEFTUP = 0x0004


class _MOUSEINPUT(ctypes.Structure):
	_fields_ = [
		("dx", ctypes.c_long),
		("dy", ctypes.c_long),
		("mouseData", ctypes.c_uint32),
		("dwFlags", ctypes.c_uint32),
		("time", ctypes.c_uint32),
		("dwExtraInfo", ctypes.c_ulonglong),
	]


class _INPUT(ctypes.Structure):
	_fields_ = [("type", ctypes.c_uint32), ("mi", _MOUSEINPUT)]


def _click() -> None:
	'''Fire one left-click through SendInput; Raw Input visible and no mouse_event duplicate click.'''
	press = _INPUT()
	press.type = _INPUT_MOUSE
	press.mi = _MOUSEINPUT(0, 0, 0, _MOUSEEVENTF_LEFTDOWN, 0, 0)
	release = _INPUT()
	release.type = _INPUT_MOUSE
	release.mi = _MOUSEINPUT(0, 0, 0, _MOUSEEVENTF_LEFTUP, 0, 0)
	arr = (_INPUT * 2)(press, release)
	_user32.SendInput(2, arr, ctypes.sizeof(_INPUT))


def _angle_to(target, origin, view_yaw_deg: float, view_pitch_deg: float) -> float:
	'''Combined yaw/pitch angle from view direction to target.'''
	delta_x = target[0] - origin[0]
	delta_y = target[1] - origin[1]
	delta_z = target[2] - origin[2]
	flat_distance = math.sqrt(delta_x * delta_x + delta_y * delta_y)
	if flat_distance < 0.0001:
		return _ANGLE_MISS_DEG
	target_yaw = math.degrees(math.atan2(delta_y, delta_x))
	target_pitch = -math.degrees(math.atan2(delta_z, flat_distance))
	yaw_diff = (target_yaw - view_yaw_deg + 540.0) % 360.0 - 180.0
	pitch_diff = target_pitch - view_pitch_deg
	return math.sqrt(yaw_diff * yaw_diff + pitch_diff * pitch_diff)


def _ray_hits_sphere(origin: tuple[float, float, float], direction: tuple[float, float, float], center: tuple[float, float, float], radius: float) -> bool:
	'''True iff the ray from ``origin`` along unit ``direction`` pierces the sphere at ``center`` with ``radius``. A sphere behind the view never hits.'''
	if radius <= 0.0:
		return False
	offset_x = center[0] - origin[0]
	offset_y = center[1] - origin[1]
	offset_z = center[2] - origin[2]
	along_ray = offset_x * direction[0] + offset_y * direction[1] + offset_z * direction[2]
	if along_ray < 0.0:
		return False
	closest_x = origin[0] + direction[0] * along_ray
	closest_y = origin[1] + direction[1] * along_ray
	closest_z = origin[2] + direction[2] * along_ray
	delta_x = center[0] - closest_x
	delta_y = center[1] - closest_y
	delta_z = center[2] - closest_z
	return (delta_x * delta_x + delta_y * delta_y + delta_z * delta_z) <= radius * radius


def view_direction(view_yaw_deg: float, view_pitch_deg: float) -> tuple[float, float, float]:
	'''Unit forward vector for CS2 view angles (dwViewAngles convention).'''
	pitch = math.radians(view_pitch_deg)
	yaw = math.radians(view_yaw_deg)
	cos_pitch = math.cos(pitch)
	return (cos_pitch * math.cos(yaw), cos_pitch * math.sin(yaw), -math.sin(pitch))


# Feet-to-eye rise for a standing pawn (m_vecAbsOrigin sits at the feet)
_UNITS_PER_METRE = 39.37
_EYE_Z = 60.0

_ANGLE_MISS_DEG = 999.0
_ANGLE_UNSET_DEG = 9999.0

# Fallback distance bands when cfg.distance_bands is missing/empty (min_m, max_m, fov_deg, skeleton_fov_deg)
_FALLBACK_BANDS: tuple[tuple[float, float, float, float], ...] = (
	(4.0, 9999.0, 0.5, 0.5),
	(2.0, 4.0, 2.0, 1.0),
	(0.5, 2.0, 4.0, 2.0),
	(0.0, 0.5, 8.0, 4.0),
)


def _scale_fov_for_distance(cfg, base_pt: float, base_seg: float, dist_units: float, is_scoped: bool = False) -> tuple[float, float]:
	'''Distance-banded auto-scaling of trigger FOV gates.'''
	distance_m = dist_units / _UNITS_PER_METRE

	pt_band: float | None = None
	seg_band: float | None = None
	bands = getattr(cfg, "distance_bands", None)
	if isinstance(bands, list):
		for band in bands:
			try:
				minimum = float(band.get("min_m", 0.0))
				maximum = float(band.get("max_m", 9999.0))
				if minimum <= distance_m < maximum:
					pt_band = float(band.get("fov_deg", base_pt))
					seg_band = float(band.get("skeleton_fov_deg", base_seg))
					break
			except (TypeError, ValueError, AttributeError):
				continue
	if pt_band is None or seg_band is None:
		for minimum, maximum, point_fov, segment_fov in _FALLBACK_BANDS:
			if minimum <= distance_m < maximum:
				pt_band = point_fov
				seg_band = segment_fov
				break
	if pt_band is None or seg_band is None:
		pt_band, seg_band = base_pt, base_seg

	if not is_scoped:
		return pt_band, seg_band

	mode = str(getattr(cfg, "scoped_band_mode", "off")).lower()
	if mode == "off":
		return pt_band, seg_band

	multiplier = float(getattr(cfg, "scoped_band_multiplier", 0.25))
	if mode == "segment_only":
		return pt_band, base_seg
	if mode == "point_only":
		return base_pt, seg_band
	if mode == "both_off":
		return base_pt, base_seg
	if mode == "both_scaled":
		return pt_band * multiplier, seg_band * multiplier
	return pt_band, seg_band


class Triggerbot:
	'''Engine-raycast and overlay-assisted triggerbot delivering at most one click per frame.'''
	def __init__(self, mem, entity_system, config):
		self.mem = mem
		self.entities = entity_system
		self.config = config
		self._sighted_at: float | None = None
		self._sighted_idx: int = 0
		self._fired_at: float = 0.0
		self._last_fired_idx: int = 0
		# Cached scope state so transient read failures don't flip the band damper mid-fire
		self._last_scoped: bool = False
		self._scope_drift_warned = False
		self._last_item_def = -1
		self._weapon_walk_warned = False
		self._glow_last_msg: str = ""
		self._log_path: Path | None = None
		try:
			log_dir = Path(__file__).resolve().parents[2] / "temp"
			log_dir.mkdir(parents=True, exist_ok=True)
			self._log_path = log_dir / "trigger.log"
			self._log_path.write_text(f"# trigger log started {strftime('%Y-%m-%d %H:%M:%S')}\n", encoding="utf-8")
		except Exception:
			self._log_path = None

	def _glow_diag(self, msg: str) -> None:
		'''Append a deduped state-transition line to temp/trigger.log.'''
		if msg == self._glow_last_msg:
			return
		self._glow_last_msg = msg
		if self._log_path is not None:
			try:
				with self._log_path.open("a", encoding="utf-8") as log_file:
					log_file.write(f"{strftime('%H:%M:%S')} {msg}\n")
			except Exception:
				pass

	def _reset_sight_state(self) -> None:
		self._sighted_at = None
		self._sighted_idx = 0

	def update(self, players=None, local_pos=None, view_matrix=None, screen_w: int = 0, screen_h: int = 0) -> None:
		'''Select a hit-test path and deliver at most one click.'''
		trigger_config = self.config.triggerbot
		if not is_feature_active(trigger_config):
			self._reset_sight_state()
			self._glow_diag("disabled (no hold key and master off)")
			return

		if bool(_user32.GetAsyncKeyState(0x01) & 0x8000):
			self._reset_sight_state()
			return

		local_pawn = self.entities.get_local_pawn()
		if not local_pawn:
			self._glow_diag("no local_pawn (not in match)")
			return

		# Gate on stable weapon identity and retain the last scope flag across transient read failures
		holding_scoped = False
		weapon = 0
		try:
			weapon_services = self.mem.read_long(local_pawn + Netvars.m_pWeaponServices)
			active_weapon = self.mem.read_uint(weapon_services + Netvars.m_hActiveWeapon) if weapon_services else 0
			entity_list = self.mem.read_long(self.mem.client(Offsets.dwEntityList)) if active_weapon else 0
			weapon_list_entry = self.mem.read_long(entity_list + 0x8 * ((active_weapon & 0x7FFF) >> 9) + 0x10) if entity_list else 0
			weapon = self.mem.read_long(weapon_list_entry + 0x70 * (active_weapon & 0x1FF)) if weapon_list_entry else 0
			if weapon:
				item_definition = self.mem.read_int(weapon + Netvars.m_AttributeManager + Netvars.m_Item + Netvars.m_iItemDefinitionIndex) & 0xFFFF
				if item_definition != self._last_item_def:
					self._last_item_def = item_definition
					print(f"[trigger] weapon=0x{weapon:X} item_def={item_definition} scoped={item_definition in _SCOPED_WEAPON_IDS} (handle=0x{active_weapon:X})")
				holding_scoped = item_definition in _SCOPED_WEAPON_IDS
				if not weapon_class_allowed(trigger_config, item_definition):
					self._reset_sight_state()
					self._glow_diag(f"weapon class blocked (item_def={item_definition})")
					return
		except Exception as error:
			if not self._weapon_walk_warned:
				print(f"[trigger] scoped-weapon walk failed: {error!r}")
				self._weapon_walk_warned = True

		if holding_scoped and bool(getattr(trigger_config, "scoped_require_scope", True)):
			try:
				scoped_now = bool(self.mem.read_byte(local_pawn + Netvars.m_bIsScoped))
				self._last_scoped = scoped_now
			except Exception:
				scoped_now = getattr(self, "_last_scoped", False)
			if not scoped_now:
				self._reset_sight_state()
				self._glow_diag("scoped weapon, not scoped in (blocked)")
				return

		fire_condition = str(getattr(trigger_config, "fire_condition", "always") or "always").lower()
		if fire_condition == "shift":
			if not bool(_user32.GetAsyncKeyState(0x10) & 0x8000):  # VK_SHIFT
				self._reset_sight_state()
				return
		elif fire_condition == "ctrl":
			if not bool(_user32.GetAsyncKeyState(0x11) & 0x8000):  # VK_CONTROL
				self._reset_sight_state()
				return
		elif fire_condition == "no_move":
			try:
				velocity_x, velocity_y, velocity_z = self.mem.read_vec3(local_pawn + Netvars.m_vecAbsVelocity)
				if (velocity_x * velocity_x + velocity_y * velocity_y + velocity_z * velocity_z) ** 0.5 > 30.0:
					self._reset_sight_state()
					return
			except Exception:
				pass

		if weapon:
			try:
				next_tick = self.mem.read_int(weapon + Netvars.m_nNextPrimaryAttackTick)
				game_time = read_game_time(self.mem)
				if game_time is not None and next_tick > 0 and (next_tick / 64.0) > game_time + 0.02:
					self._reset_sight_state()
					return
			except Exception:
				pass
		# Painted-silhouette modes bypass the legacy crosshair-entity gate
		if bool(getattr(trigger_config, "wh_compliant", True)):
			wh_mode = _select_wh_mode(self.config)
		else:
			wh_mode = "default"
		self._glow_diag(f"mode={wh_mode}")
		glow_mode = wh_mode == "glow"
		silhouette_mode = wh_mode == "chams"

		if not (silhouette_mode or glow_mode):
			try:
				target_idx = self.mem.read_int(local_pawn + Netvars.m_iIDEntIndex)
			except Exception:
				return
			if target_idx <= 0:
				self._reset_sight_state()
				return

		if local_pos is None:
			local_pos = self.entities.get_local_position()
		if not local_pos or all(component == 0.0 for component in local_pos):
			return

		local_team = self.entities.get_local_team()
		if players is None:
			try:
				players = self.entities.get_players(read_bones=False)
			except Exception:
				return

		eye = (local_pos[0], local_pos[1], local_pos[2] + _EYE_Z)

		# Glow uses the engine crosshair result
		if glow_mode:
			try:
				target_idx = self.mem.read_int(local_pawn + Netvars.m_iIDEntIndex)
			except Exception:
				self._glow_diag("read_idx_failed")
				return
			if target_idx <= 0:
				self._reset_sight_state()
				self._glow_diag(f"idx={target_idx} (no entity)")
				return
			target_pawn = self.entities.entity_index_to_pawn(target_idx)
			if not target_pawn:
				self._reset_sight_state()
				self._glow_diag(f"idx={target_idx} resolve_failed")
				return
			matched = next((player for player in players if player.pawn_addr == target_pawn), None)
			if matched is None:
				try:
					health = self.mem.read_int(target_pawn + Netvars.m_iHealth)
					life_state = self.mem.read_byte(target_pawn + Netvars.m_lifeState)
					team = self.mem.read_byte(target_pawn + Netvars.m_iTeamNum)
					if team not in (2, 3) or health <= 0 or life_state != 0:
						self._reset_sight_state()
						return
					matched = Player(pawn_addr=target_pawn, health=health, team=team, life_state=life_state, is_alive=True)
				except Exception:
					self._reset_sight_state()
					self._glow_diag(f"idx={target_idx} pawn=0x{target_pawn:X} not_in_players (n={len(players)})")
					return
			if matched.team == local_team:
				self._reset_sight_state()
				self._glow_diag(f"idx={target_idx} teammate")
				return
			if matched.health <= 0 or matched.life_state != 0:
				self._reset_sight_state()
				self._glow_diag(f"idx={target_idx} dead (hp={matched.health} ls={matched.life_state})")
				return
			self._glow_diag(f"idx={target_idx} FIRE (hp={matched.health})")
			if self._head_only_blocks(trigger_config, target_pawn, players, view_matrix, screen_w, screen_h, eye):
				self._reset_sight_state()
				self._glow_diag(f"idx={target_idx} head_only: off head")
				return
			self._dispatch_fire(trigger_config, target_pawn, players)
			return

		# Chams uses the latest centre-pixel alpha from the overlay framebuffer
		if silhouette_mode and view_matrix is not None and screen_w > 0 and screen_h > 0:
			center_x = screen_w * 0.5
			center_y = screen_h * 0.5
			probe_age = get_chams_probe_age_s()
			probe_fresh = probe_age < 0.250
			pixel_hit = probe_fresh and get_chams_probe_alpha() >= int(getattr(trigger_config, "pixel_alpha_threshold", 24))
			best_idx = 0
			best_dist = 0.0
			if pixel_hit:
				best_dist = float("inf")
				for player in players:
					if player.team == local_team or player.health <= 0 or player.life_state != 0:
						continue
					distance = math.dist(player.position, eye)
					if distance < best_dist:
						best_dist = distance
						best_idx = player.pawn_addr
			elif not probe_fresh:
				# Use geometry only before the first fresh framebuffer probe
				inflate_pixels = float(getattr(trigger_config, "silhouette_inflate_px", 0.0))
				try:
					skeleton_config = self.config.visuals.skeleton
				except AttributeError:
					skeleton_config = None
				head_ring_enabled = bool(skeleton_config and getattr(skeleton_config, "head_outline_enabled", False))
				for player in players:
					if player.team == local_team or player.health <= 0 or player.life_state != 0 or not player.bones:
						continue
					hit = crosshair_on_fat_skeleton(player.bones, view_matrix, screen_w, screen_h, eye, center_x, center_y, inflate_pixels)
					if not hit and head_ring_enabled:
						try:
							polygon = compute_head_outline_polygon(player, view_matrix, screen_w, screen_h, skeleton_config, eye)
						except Exception:
							polygon = None
						hit = _ring_contains(polygon, center_x, center_y)
					if hit:
						best_idx = player.pawn_addr
						best_dist = math.dist(player.position, eye)
						break
			if best_idx == 0:
				self._reset_sight_state()
				self._glow_diag("chams: crosshair off body")
				return
			self._glow_diag(f"chams: pawn=0x{best_idx:X} dist={best_dist:.0f}")
			if self._head_only_blocks(trigger_config, best_idx, players, view_matrix, screen_w, screen_h, eye):
				self._reset_sight_state()
				self._glow_diag("chams: head_only off head")
				return
			self._dispatch_fire(trigger_config, best_idx, players)
			return

		try:
			view = self.mem.read_vec3(self.mem.client(Offsets.dwViewAngles))
		except Exception:
			return
		view_pitch, view_yaw = float(view[0]), float(view[1])
		try:
			scope_byte = self.mem.read_int(local_pawn + Netvars.m_bIsScoped) & 0xFF
			if scope_byte not in (0, 1):
				if not self._scope_drift_warned:
					print(f"[trigger] m_bIsScoped @ +0x{Netvars.m_bIsScoped:X} reads 0x{scope_byte:02X}; treating it as unscoped")
					self._scope_drift_warned = True
				self._last_scoped = False
			else:
				self._last_scoped = bool(scope_byte)
		except Exception:
			pass
		is_scoped = self._last_scoped

		best_pt_diff = _ANGLE_UNSET_DEG
		best_pt_idx = 0
		best_pt_dist = 0.0
		best_seg_diff = _ANGLE_UNSET_DEG
		best_seg_idx = 0
		best_seg_dist = 0.0
		for player in players:
			if player.team == local_team or player.health <= 0 or player.life_state != 0:
				continue

			position_x, position_y, position_z = player.position
			offset_x, offset_y, offset_z = position_x - eye[0], position_y - eye[1], position_z - eye[2]
			player_distance = math.sqrt(offset_x * offset_x + offset_y * offset_y + offset_z * offset_z)

			if player.bones:
				# Point mode samples joints and extension tips; polygon density optionally adds intermediate samples
				density = 1
				try:
					chams_cfg = self.config.visuals.chams
					density = max(1, min(6, int(getattr(chams_cfg, "polygon_density", 1))))
				except AttributeError:
					pass
				for target in iter_targets(player.bones, density):
					angle_difference = _angle_to(target, eye, view_yaw, view_pitch)
					if angle_difference < best_pt_diff:
						best_pt_diff = angle_difference
						best_pt_idx = player.pawn_addr
						best_pt_dist = player_distance
				for segment_start, segment_end in iter_segments(player.bones):
					segment_delta = tuple(segment_end[index] - segment_start[index] for index in range(3))
					angle_difference = min(
						_angle_to(tuple(segment_start[index] + sample / 11.0 * segment_delta[index] for index in range(3)), eye, view_yaw, view_pitch)
						for sample in range(12)
					)
					if angle_difference < best_seg_diff:
						best_seg_diff = angle_difference
						best_seg_idx = player.pawn_addr
						best_seg_dist = player_distance
			else:
				target_x, target_y, target_z = player.position
				for height_offset in (60.0, 50.0, 35.0):
					angle_difference = _angle_to((target_x, target_y, target_z + height_offset), eye, view_yaw, view_pitch)
					if angle_difference < best_pt_diff:
						best_pt_diff = angle_difference
						best_pt_idx = player.pawn_addr
						best_pt_dist = player_distance

		# Resolve distance bands separately because joint and segment candidates may belong to different players
		base_pt_fov = float(getattr(trigger_config, "fov_deg", 0.5))
		base_seg_fov = float(getattr(trigger_config, "skeleton_fov_deg", 0.5))
		pt_fov, _ = _scale_fov_for_distance(trigger_config, base_pt_fov, base_seg_fov, best_pt_dist, is_scoped)
		_, seg_fov = _scale_fov_for_distance(trigger_config, base_pt_fov, base_seg_fov, best_seg_dist, is_scoped)
		pt_hit = best_pt_idx != 0 and best_pt_diff <= pt_fov
		seg_hit = best_seg_idx != 0 and best_seg_diff <= seg_fov

		# The painted head ring wins ties with other candidate gates
		head_idx = 0
		head_pawn_distance = 0.0
		if wh_mode == "skel_head" and view_matrix and screen_w > 0 and screen_h > 0:
			try:
				skeleton_config = self.config.visuals.skeleton
			except AttributeError:
				skeleton_config = None
			if skeleton_config is not None and bool(getattr(skeleton_config, "head_outline_enabled", False)):
				center_x = screen_w * 0.5
				center_y = screen_h * 0.5
				closest_head_distance_sq = float("inf")
				for player in players:
					if player.team == local_team or player.health <= 0 or player.life_state != 0 or not player.bones:
						continue
					polygon = compute_head_outline_polygon(player, view_matrix, screen_w, screen_h, skeleton_config, eye)
					if not _ring_contains(polygon, center_x, center_y):
						continue
					head = player.bones.get(Netvars.BONE_HEAD)
					if head is None:
						continue
					head_distance_sq = sum((head[index] - eye[index]) ** 2 for index in range(3))
					if head_distance_sq < closest_head_distance_sq:
						closest_head_distance_sq = head_distance_sq
						head_idx = player.pawn_addr
						head_pawn_distance = math.dist(player.position, eye)
		if head_idx:
			best_pt_idx = head_idx
			best_pt_diff = 0.0
			best_pt_dist = head_pawn_distance
			pt_hit = True

		if not (pt_hit or seg_hit):
			self._reset_sight_state()
			self._glow_diag(f"legacy: miss (pt={best_pt_diff:.2f}/{pt_fov:.2f} seg={best_seg_diff:.2f}/{seg_fov:.2f})")
			return

		if head_idx:
			best_idx = head_idx
			kind = "head"
		elif seg_hit:
			best_idx = best_seg_idx
			kind = "seg"
		else:
			best_idx = best_pt_idx
			kind = "pt"
		self._glow_diag(f"legacy {kind}: pawn=0x{best_idx:X}")
		if self._head_only_blocks(trigger_config, best_idx, players, view_matrix, screen_w, screen_h, eye):
			self._reset_sight_state()
			self._glow_diag(f"legacy {kind}: head_only off head")
			return
		self._dispatch_fire(trigger_config, best_idx, players)

	def _head_only_blocks(self, cfg, pawn_addr: int, players, view_matrix, screen_w: int, screen_h: int, eye) -> bool:
		'''True when head-only is on and screen-centre is outside the target's green head ring; fails safe on missing geometry.'''
		if not bool(getattr(cfg, "head_only", False)):
			return False
		if view_matrix is None or screen_w <= 0 or screen_h <= 0:
			return True
		try:
			skeleton_config = self.config.visuals.skeleton
		except AttributeError:
			return True
		target = next((player for player in players if player.pawn_addr == pawn_addr), None)
		if target is None or not target.bones:
			return True
		polygon = compute_head_outline_polygon(target, view_matrix, screen_w, screen_h, skeleton_config, eye)
		center_x = screen_w * 0.5
		center_y = screen_h * 0.5
		return not _ring_contains(polygon, center_x, center_y)

	def _dispatch_fire(self, cfg, best_idx: int, players=None) -> None:
		'''Shared reaction-delay + cooldown + click flow.'''
		now = perf_counter()

		if best_idx != self._sighted_idx:
			self._sighted_idx = best_idx
			self._sighted_at = now
			self._glow_diag(f"sighted new target 0x{best_idx:X} (waiting)")
			return
		if self._sighted_at is None:
			self._sighted_at = now
			return

		delay_ms = float(getattr(cfg, "delay_ms", 25))
		jitter_ms = float(getattr(cfg, "delay_jitter_ms", 15))
		min_delay_ms = float(getattr(cfg, "min_delay_ms", 40))
		reaction_wait = max(min_delay_ms, delay_ms + uniform(-jitter_ms, jitter_ms)) / 1000.0
		if (now - self._sighted_at) < reaction_wait:
			return

		cooldown_ms = float(getattr(cfg, "refire_cooldown_ms", 350))
		if best_idx == self._last_fired_idx and (now - self._fired_at) * 1000.0 < cooldown_ms:
			self._glow_diag(f"per-target cooldown ({cooldown_ms:.0f} ms) on 0x{best_idx:X}")
			return

		_click()
		self._fired_at = now
		self._last_fired_idx = best_idx
		self._glow_diag(f"CLICK on 0x{best_idx:X}")
