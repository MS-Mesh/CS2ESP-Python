"""
Skeleton, bone-extension, and head-outline drawing.
"""

import math

from imgui_bundle import imgui

from cs2esp.offsets import Netvars
from cs2esp.visuals.bone_extensions import iter_extensions
from cs2esp.visuals.theme import color_u32


_RING_SAMPLES = 32
_RING_UNIT = tuple((math.cos(2.0 * math.pi * i / _RING_SAMPLES), math.sin(2.0 * math.pi * i / _RING_SAMPLES)) for i in range(_RING_SAMPLES))


def _length_2d(x: float, y: float) -> float:
	'''Return the sqrt length of a 2D vector.'''
	return math.sqrt(x * x + y * y)


def _unit_2d(x: float, y: float) -> tuple[float, float] | None:
	'''Return the unit vector, or None when its length is not above 1e-3.'''
	length = math.sqrt(x * x + y * y)
	if not length > 1e-3:
		return None
	inverse = 1.0 / length
	return x * inverse, y * inverse


def _unit_3d(x: float, y: float, z: float) -> tuple[float, float, float] | None:
	'''Return the unit vector, or None when its length is not above 1e-3.'''
	length = math.sqrt(x * x + y * y + z * z)
	if not length > 1e-3:
		return None
	inverse = 1.0 / length
	return x * inverse, y * inverse, z * inverse


def compute_head_sphere(player, cfg, camera_pos=None):
	'''Return the shared hit-test head sphere as ``(center, radius)``.'''
	head = player.bones.get(Netvars.BONE_HEAD)
	if head is None:
		return None
	radius = float(getattr(cfg, "head_outline_radius_units", 9.0))
	if radius <= 0.0:
		return None
	center = _head_center_world(player, cfg, head, camera_pos)
	width_mul = float(getattr(cfg, "head_outline_width_mul", 1.0))
	height_mul = float(getattr(cfg, "head_outline_height_mul", 1.0))
	radius_world = radius * 0.5 * (width_mul + height_mul)
	return center, radius_world


def _head_center_world(player, cfg, head, camera_pos):
	'''Return the shared world-space centre of the head outline and sphere.'''
	off_up = float(getattr(cfg, "head_outline_offset_up", 0.0))
	off_right = float(getattr(cfg, "head_outline_offset_right", 0.0))
	anchor_screen = bool(getattr(cfg, "head_outline_anchor_to_screen", False))
	left_clavicle = player.bones.get(Netvars.BONE_L_CLAV)
	right_clavicle = player.bones.get(Netvars.BONE_R_CLAV)

	# Derive the ring offset from the camera angle instead of changing its configured magnitude
	auto_mode = bool(getattr(cfg, "head_outline_offset_right_auto", True))
	if auto_mode and camera_pos is not None:
		magnitude = float(getattr(cfg, "head_outline_offset_right_magnitude", 1.0))
		if left_clavicle is not None and right_clavicle is not None:
			sr_x_world = right_clavicle[0] - left_clavicle[0]
			sr_y_world = right_clavicle[1] - left_clavicle[1]
			sr_mag_world = _length_2d(sr_x_world, sr_y_world)
			v_x_world = head[0] - camera_pos[0]
			v_y_world = head[1] - camera_pos[1]
			v_mag_world = _length_2d(v_x_world, v_y_world)
			if sr_mag_world > 1e-3 and v_mag_world > 1e-3:
				inv_sr = 1.0 / sr_mag_world
				inv_v = 1.0 / v_mag_world
				cos_theta = sr_x_world * v_x_world * inv_sr * inv_v + sr_y_world * v_y_world * inv_sr * inv_v
				off_right = -cos_theta * magnitude
				anchor_screen = True

	# Correct the neck-base head bone toward the cranium; screen anchoring uses a horizontal offset
	enemy_backward_axis = None
	if left_clavicle is not None and right_clavicle is not None:
		rx = right_clavicle[0] - left_clavicle[0]
		ry = right_clavicle[1] - left_clavicle[1]
		axis = _unit_2d(ry, -rx)
		if axis is not None:
			enemy_backward_axis = (axis[0], axis[1], 0.0)

	if anchor_screen and camera_pos is not None:
		dx = head[0] - camera_pos[0]
		dy = head[1] - camera_pos[1]
		dz = head[2] - camera_pos[2]
		forward_axis = _unit_3d(dx, dy, dz)
		if forward_axis is not None:
			right_axis_2d = _unit_2d(forward_axis[1], -forward_axis[0])
			offset_right_axis = (right_axis_2d[0], right_axis_2d[1], 0.0) if right_axis_2d is not None else (1.0, 0.0, 0.0)
		else:
			offset_right_axis = (1.0, 0.0, 0.0)
	elif enemy_backward_axis is not None:
		offset_right_axis = enemy_backward_axis
	else:
		offset_right_axis = (1.0, 0.0, 0.0)

	return (head[0] + offset_right_axis[0] * off_right, head[1] + offset_right_axis[1] * off_right, head[2] + offset_right_axis[2] * off_right + off_up)


def compute_head_outline_polygon(player, view_matrix, screen_w: int, screen_h: int, cfg, camera_pos=None):
	'''Build the camera-facing 32-point head outline shared with hit testing.'''
	head = player.bones.get(Netvars.BONE_HEAD)
	if head is None:
		return None

	radius = float(getattr(cfg, "head_outline_radius_units", 9.0))
	if radius <= 0.0:
		return None

	# The camera-facing ring is normal to head - camera; the fallback plane uses player-right and world-up axes
	if camera_pos is not None:
		dx = head[0] - camera_pos[0]
		dy = head[1] - camera_pos[1]
		dz = head[2] - camera_pos[2]
		dist = math.sqrt(dx * dx + dy * dy + dz * dz)
		if dist > 1e-3:
			inv = 1.0 / dist
			fwd = (dx * inv, dy * inv, dz * inv)
			rx, ry, rz = -fwd[1], fwd[0], 0.0
			rmag = math.sqrt(rx * rx + ry * ry + rz * rz)
			if rmag < 1e-3:
				right_axis = (1.0, 0.0, 0.0)
			else:
				inv_r = 1.0 / rmag
				right_axis = (rx * inv_r, ry * inv_r, rz * inv_r)
			up_axis = (
				fwd[1] * right_axis[2] - fwd[2] * right_axis[1],
				fwd[2] * right_axis[0] - fwd[0] * right_axis[2],
				fwd[0] * right_axis[1] - fwd[1] * right_axis[0],
			)
		else:
			right_axis = (1.0, 0.0, 0.0)
			up_axis = (0.0, 0.0, 1.0)
	else:
		l_clav = player.bones.get(Netvars.BONE_L_CLAV)
		r_clav = player.bones.get(Netvars.BONE_R_CLAV)
		if l_clav is not None and r_clav is not None:
			rx = r_clav[0] - l_clav[0]
			ry = r_clav[1] - l_clav[1]
			rz = r_clav[2] - l_clav[2]
			rmag = math.sqrt(rx * rx + ry * ry + rz * rz)
			if rmag > 1e-3:
				inv_r = 1.0 / rmag
				right_axis = (rx * inv_r, ry * inv_r, rz * inv_r)
			else:
				right_axis = (1.0, 0.0, 0.0)
		else:
			right_axis = (1.0, 0.0, 0.0)
		up_axis = (0.0, 0.0, 1.0)

	width_mul = float(getattr(cfg, "head_outline_width_mul", 1.0))
	height_mul = float(getattr(cfg, "head_outline_height_mul", 1.0))
	rot_deg = float(getattr(cfg, "head_outline_rotation_deg", 0.0))

	cos_rotation = math.cos(math.radians(rot_deg))
	sin_rotation = math.sin(math.radians(rot_deg))
	radius_x = radius * width_mul
	radius_y = radius * height_mul

	cx_w, cy_w, cz_w = _head_center_world(player, cfg, head, camera_pos)

	# Inline projection for the 32 ring samples
	row0, row1, row3 = view_matrix[0], view_matrix[1], view_matrix[3]
	half_w = screen_w * 0.5
	half_h = screen_h * 0.5
	rax, ray, raz = right_axis
	uax, uay, uaz = up_axis
	points = []
	append = points.append
	for unit_cos, unit_sin in _RING_UNIT:
		ellipse_x = unit_cos * radius_x
		ellipse_y = unit_sin * radius_y
		rotated_x = ellipse_x * cos_rotation - ellipse_y * sin_rotation
		rotated_y = ellipse_x * sin_rotation + ellipse_y * cos_rotation
		world_x = cx_w + rax * rotated_x + uax * rotated_y
		world_y = cy_w + ray * rotated_x + uay * rotated_y
		world_z = cz_w + raz * rotated_x + uaz * rotated_y
		w = row3[0] * world_x + row3[1] * world_y + row3[2] * world_z + row3[3]
		if w < 0.001:
			append(None)
			continue
		inv_w = 1.0 / w
		append(
			(
				half_w + (row0[0] * world_x + row0[1] * world_y + row0[2] * world_z + row0[3]) * inv_w * half_w,
				half_h - (row1[0] * world_x + row1[1] * world_y + row1[2] * world_z + row1[3]) * inv_w * half_h,
			)
		)
	return points


def _project_bones(bones, view_matrix, screen_w: int, screen_h: int) -> dict:
	'''Project every bone once; joints shared by several segments reuse it.'''
	from cs2esp.visuals import native_math

	native_available = native_math.available()

	def project_native():
		import numpy as np

		keys = list(bones)
		pts = np.array([bones[k] for k in keys], dtype=np.float32)
		if pts.size == 0:
			return {}
		xy = native_math.project(pts, view_matrix, screen_w, screen_h)
		return {k: (float(xy[i, 0]), float(xy[i, 1])) for i, k in enumerate(keys) if np.isfinite(xy[i, 0])}

	if native_available:
		if not bones:
			return {}
		try:
			standard_matrix = len(view_matrix) == 4 and all(len(row) == 4 and all(isinstance(v, (int, float)) for v in row) for row in view_matrix)
		except TypeError:
			standard_matrix = False
		# Keep small batches in Python to avoid numpy/ctypes marshalling overhead
		if len(bones) >= 64 or not standard_matrix or not isinstance(screen_w, (int, float)) or not isinstance(screen_h, (int, float)):
			return project_native()
	row0, row1, row3 = view_matrix[0], view_matrix[1], view_matrix[3]
	m00, m01, m02, m03 = row0[0], row0[1], row0[2], row0[3]
	m10, m11, m12, m13 = row1[0], row1[1], row1[2], row1[3]
	m30, m31, m32, m33 = row3[0], row3[1], row3[2], row3[3]
	half_w = screen_w * 0.5
	half_h = screen_h * 0.5
	projected = {}
	for index, (x, y, z) in bones.items():
		if native_available and not (isinstance(x, (int, float)) and isinstance(y, (int, float)) and isinstance(z, (int, float))):
			return project_native()
		w = m30 * x + m31 * y + m32 * z + m33
		# Preserve float32 clipping decisions in the native path
		if native_available and 0.0009999 <= w <= 0.0010001:
			return project_native()
		if w < 0.001:
			continue
		inv_w = 1.0 / w
		screen_x = half_w + (m00 * x + m01 * y + m02 * z + m03) * inv_w * half_w
		if native_available and not math.isfinite(screen_x):
			continue
		projected[index] = (screen_x, half_h - (m10 * x + m11 * y + m12 * z + m13) * inv_w * half_h)
	return projected


def project_players(players, view_matrix, screen_w: int, screen_h: int):
	'''Project every player's bones in one native call; None = native backend unavailable.'''
	from cs2esp.visuals import native_math

	if not native_math.available():
		return None
	keys = []
	points = []
	for player in players:
		for bone_index, position in player.bones.items():
			keys.append((player.pawn_addr, bone_index))
			points.append(position)
	if not keys:
		return {}
	import numpy as np

	xy = native_math.project(np.asarray(points, dtype=np.float32), view_matrix, screen_w, screen_h)
	out: dict[int, dict[int, tuple[float, float]]] = {}
	for (pawn, bone_index), (x, y) in zip(keys, xy.tolist()):
		if math.isfinite(x):
			out.setdefault(pawn, {})[bone_index] = (x, y)
	return out


def draw_skeleton(draw_list, player, view_matrix, screen_w: int, screen_h: int, cfg, camera_pos=None, screen=None) -> None:
	'''Draw skeleton chains, extension stubs and the optional head outline.'''
	if not cfg.enabled or not player.bones:
		return
	skeleton_color = color_u32(cfg.color)
	thickness = cfg.thickness
	if screen is None:
		screen = _project_bones(player.bones, view_matrix, screen_w, screen_h)
	add_line = draw_list.add_line
	add_polyline = draw_list.add_polyline
	for chain in Netvars.SKELETON_CHAINS:
		points = [screen.get(bone) for bone in chain]
		if any(point is None for point in points):
			for first, second in zip(points, points[1:]):
				if first is not None and second is not None:
					add_line(first, second, skeleton_color, thickness)
			continue
		add_polyline([imgui.ImVec2(px, py) for px, py in points], skeleton_color, thickness, 0)

	# Extension stubs: synthetic gun/toe/head-crown lines show where the triggerbot's silhouette starts and ends; thinner than main bones
	ext_thickness = max(cfg.thickness * 0.7, 0.5)
	stubs = list(iter_extensions(player.bones))
	if stubs:
		ends = {}
		for slot, (tip_world, ext_world, _label) in enumerate(stubs):
			ends[2 * slot] = tip_world
			ends[2 * slot + 1] = ext_world
		ends_screen = _project_bones(ends, view_matrix, screen_w, screen_h)
		for slot in range(len(stubs)):
			tip_screen = ends_screen.get(2 * slot)
			extension_screen = ends_screen.get(2 * slot + 1)
			if tip_screen is None or extension_screen is None:
				continue
			add_line(tip_screen, extension_screen, skeleton_color, ext_thickness)

	if getattr(cfg, "head_outline_enabled", False):
		points = compute_head_outline_polygon(player, view_matrix, screen_w, screen_h, cfg, camera_pos)
		if points is not None:
			outline_color = color_u32(getattr(cfg, "head_outline_color", cfg.color))
			thickness = max(float(getattr(cfg, "head_outline_thickness_px", 1.5)), 0.5)
			if None not in points:
				draw_list.add_polyline([imgui.ImVec2(px, py) for px, py in points], outline_color, thickness, int(imgui.ImDrawFlags_.closed))
				return
			for point_index, start_point in enumerate(points):
				end_point = points[(point_index + 1) % len(points)]
				if start_point is None or end_point is None:
					continue
				add_line(start_point, end_point, outline_color, thickness)
