"""
OpenGL low-poly humanoid chams rendered before the imgui overlay pass; players are queued per frame and drawn straight into the framebuffer.
"""

import math
from array import array
from collections.abc import Iterable
from ctypes import c_void_p

import OpenGL.GL as gl
import OpenGL.GL.shaders as gl_shaders
import numpy as np
from OpenGL import platform as gl_platform

from cs2esp.offsets import Netvars
from cs2esp.visuals.math_utils import TEAM_CT


if array("f").itemsize != 4 or array("I").itemsize != 4:
	raise RuntimeError("chams requires 32-bit C float and unsigned-int arrays")


_UNSET_CONTEXT = object()


def _current_context_token():
	'''Return the native OpenGL context handle for cache invalidation.'''
	try:
		return gl_platform.PLATFORM.GetCurrentContext()
	except Exception:
		return None


class _Vec3(tuple):
	"""Small immutable vector supporting renderer arithmetic."""
	__slots__ = ()

	def __new__(cls, values: Iterable[float]):
		x, y, z = values
		return super().__new__(cls, (float(x), float(y), float(z)))

	def __add__(self, other) -> "_Vec3":
		return _Vec3((self[0] + other[0], self[1] + other[1], self[2] + other[2]))

	def __sub__(self, other) -> "_Vec3":
		return _Vec3((self[0] - other[0], self[1] - other[1], self[2] - other[2]))

	def __mul__(self, scalar: float) -> "_Vec3":
		scalar = float(scalar)
		return _Vec3((self[0] * scalar, self[1] * scalar, self[2] * scalar))

	__rmul__ = __mul__

	def __truediv__(self, scalar: float) -> "_Vec3":
		scalar = float(scalar)
		return _Vec3((self[0] / scalar, self[1] / scalar, self[2] / scalar))

	def __neg__(self) -> "_Vec3":
		return _Vec3((-self[0], -self[1], -self[2]))


class _Mat4(array):
	"""Row-major float32 matrix with tuple indexing and multiplication."""
	__slots__ = ()

	def __new__(cls, values: Iterable[float]):
		values = tuple(values)
		if len(values) != 16:
			raise ValueError("a 4x4 matrix requires exactly 16 values")
		return super().__new__(cls, "f", values)

	def __init__(self, values: Iterable[float]) -> None:
		pass

	def __getitem__(self, key):
		if isinstance(key, tuple):
			row, column = key
			return super().__getitem__(row * 4 + column)
		return super().__getitem__(key)

	def __matmul__(self, other):
		if not isinstance(other, _Mat4):
			return NotImplemented
		return _Mat4(sum(self[row, inner] * other[inner, column] for inner in range(4)) for row in range(4) for column in range(4))


def _vec3(values) -> _Vec3:
	return values if isinstance(values, _Vec3) else _Vec3(values)


def _dot(first: _Vec3, second: _Vec3) -> float:
	return first[0] * second[0] + first[1] * second[1] + first[2] * second[2]


def _cross(first: _Vec3, second: _Vec3) -> _Vec3:
	return _Vec3((first[1] * second[2] - first[2] * second[1], first[2] * second[0] - first[0] * second[2], first[0] * second[1] - first[1] * second[0]))


def _norm(vector: _Vec3) -> float:
	return math.sqrt(_dot(vector, vector))


def _pack_mesh(positions: list[tuple[float, float, float]], normals: list[tuple[float, float, float]], indices: list[int]) -> tuple[array, array]:
	vertices = array("f", (component for position, normal in zip(positions, normals) for component in (*position, *normal)))
	return vertices, array("I", indices)


BONE_HEAD = Netvars.BONE_HEAD
BONE_NECK = Netvars.BONE_NECK
BONE_CHEST = Netvars.BONE_CHEST
BONE_SPINE = Netvars.BONE_SPINE
BONE_PELVIS = Netvars.BONE_PELVIS
BONE_L_CLAV = Netvars.BONE_L_CLAV
BONE_L_SHOULDER = Netvars.BONE_L_SHOULDER
BONE_L_ELBOW = Netvars.BONE_L_ELBOW
BONE_L_HAND = Netvars.BONE_L_HAND
BONE_R_CLAV = Netvars.BONE_R_CLAV
BONE_R_SHOULDER = Netvars.BONE_R_SHOULDER
BONE_R_ELBOW = Netvars.BONE_R_ELBOW
BONE_R_HAND = Netvars.BONE_R_HAND
BONE_L_THIGH = Netvars.BONE_L_THIGH
BONE_L_KNEE = Netvars.BONE_L_KNEE
BONE_L_FOOT = Netvars.BONE_L_FOOT
BONE_R_THIGH = Netvars.BONE_R_THIGH
BONE_R_KNEE = Netvars.BONE_R_KNEE
BONE_R_FOOT = Netvars.BONE_R_FOOT

# Width tracks segment length rather than player height: value = target radius / segment length
_LIMB_LENGTH_RATIO: dict[str, float] = {
	"l_uarm": 0.28,
	"r_uarm": 0.28,
	"l_larm": 0.24,
	"r_larm": 0.24,
	"l_thigh": 0.30,
	"r_thigh": 0.30,
	"l_shin": 0.22,
	"r_shin": 0.22,
}

_SYMMETRIC_RADIUS_KEYS = {
	"l_uarm": "upper_arm",
	"r_uarm": "upper_arm",
	"l_larm": "lower_arm",
	"r_larm": "lower_arm",
	"l_hand": "hand",
	"r_hand": "hand",
	"l_thigh": "thigh",
	"r_thigh": "thigh",
	"l_knee": "knee",
	"r_knee": "knee",
	"l_shin": "shin",
	"r_shin": "shin",
	"l_foot": "foot",
	"r_foot": "foot",
	"l_elbow": "elbow",
	"r_elbow": "elbow",
	"l_fingers": "fingers",
	"r_fingers": "fingers",
	"l_shoulder_box": "shoulder_box",
	"r_shoulder_box": "shoulder_box",
	"l_shoulder_sphere": "shoulder_sphere",
	"r_shoulder_sphere": "shoulder_sphere",
}


def _dynamic_limb_size(name: str, seg_length: float, height_size: float) -> float:
	'''Blend height and segment radii equally; preserve height_size for non-limbs or degenerate segments.'''
	ratio = _LIMB_LENGTH_RATIO.get(name)
	if ratio is None or seg_length <= 1e-3:
		return height_size
	return 0.5 * height_size + 0.5 * (seg_length * ratio)


# Parts are (name, primitive, first bone, second bone, player-height size factor)
_BODY_PARTS: list[tuple[str, str, int, int | None, float]] = [
	("head", "head_oval", BONE_HEAD, None, 0.075),
	("neck", "cylinder", BONE_NECK, BONE_HEAD, 0.040),
	("trap", "torso_prism", BONE_CHEST, BONE_NECK, 0.140),
	("chest", "torso_prism", BONE_CHEST, BONE_SPINE, 0.155),
	("spine", "torso_prism", BONE_SPINE, BONE_PELVIS, 0.135),
	("butt", "butt_sphere", BONE_PELVIS, None, 0.085),
	("shoulders", "aniso_cylinder_x", BONE_L_CLAV, BONE_R_CLAV, 0.107),
	("hips", "aniso_cylinder_x", BONE_L_THIGH, BONE_R_THIGH, 0.101),
	("belly", "front_prism", BONE_SPINE, BONE_PELVIS, 0.130),
	("back_fill", "back_prism", BONE_NECK, BONE_HEAD, 0.060),
	("l_uarm", "cylinder", BONE_L_SHOULDER, BONE_L_ELBOW, 0.052),
	("l_larm", "cylinder", BONE_L_ELBOW, BONE_L_HAND, 0.046),
	("l_elbow", "knee_sphere", BONE_L_ELBOW, None, 0.045),
	("l_shoulder_box", "shoulder_box", BONE_L_CLAV, BONE_L_SHOULDER, 0.060),
	("l_shoulder_sphere", "knee_sphere", BONE_L_SHOULDER, None, 0.045),
	("l_hand", "hand_box", BONE_L_ELBOW, BONE_L_HAND, 0.105),
	("l_fingers", "fingers_box", BONE_L_ELBOW, BONE_L_HAND, 0.040),
	("r_uarm", "cylinder", BONE_R_SHOULDER, BONE_R_ELBOW, 0.052),
	("r_larm", "cylinder", BONE_R_ELBOW, BONE_R_HAND, 0.046),
	("r_elbow", "knee_sphere", BONE_R_ELBOW, None, 0.045),
	("r_shoulder_box", "shoulder_box", BONE_R_CLAV, BONE_R_SHOULDER, 0.060),
	("r_shoulder_sphere", "knee_sphere", BONE_R_SHOULDER, None, 0.045),
	("r_hand", "hand_box", BONE_R_ELBOW, BONE_R_HAND, 0.105),
	("r_fingers", "fingers_box", BONE_R_ELBOW, BONE_R_HAND, 0.040),
	("vest", "vest_box", BONE_PELVIS, BONE_CHEST, 0.090),
	("l_thigh", "cylinder", BONE_L_THIGH, BONE_L_KNEE, 0.080),
	("l_knee", "knee_sphere", BONE_L_KNEE, None, 0.062),
	("l_shin", "cylinder", BONE_L_KNEE, BONE_L_FOOT, 0.065),
	("l_foot", "foot_box", BONE_L_FOOT, None, 0.110),
	("r_thigh", "cylinder", BONE_R_THIGH, BONE_R_KNEE, 0.080),
	("r_knee", "knee_sphere", BONE_R_KNEE, None, 0.062),
	("r_shin", "cylinder", BONE_R_KNEE, BONE_R_FOOT, 0.065),
	("r_foot", "foot_box", BONE_R_FOOT, None, 0.110),
]

_QUALITY = {
	"low": {"sphere_lat": 6, "sphere_lon": 8, "cyl_sides": 6, "prism_sides": 6},
	"medium": {"sphere_lat": 10, "sphere_lon": 12, "cyl_sides": 10, "prism_sides": 8},
	"high": {"sphere_lat": 14, "sphere_lon": 16, "cyl_sides": 14, "prism_sides": 10},
}

# Triangle buckets share cached vertex arrays and buffers across players
_TRI_BUCKETS: list[tuple[int, dict]] = [
	(200, {"sphere_lat": 4, "sphere_lon": 6, "cyl_sides": 4, "prism_sides": 4}),
	(500, {"sphere_lat": 6, "sphere_lon": 8, "cyl_sides": 6, "prism_sides": 6}),
	(1000, {"sphere_lat": 8, "sphere_lon": 10, "cyl_sides": 8, "prism_sides": 7}),
	(2000, {"sphere_lat": 10, "sphere_lon": 14, "cyl_sides": 10, "prism_sides": 8}),
	(4000, {"sphere_lat": 14, "sphere_lon": 18, "cyl_sides": 14, "prism_sides": 10}),
	(8000, {"sphere_lat": 18, "sphere_lon": 24, "cyl_sides": 18, "prism_sides": 12}),
	(16000, {"sphere_lat": 24, "sphere_lon": 32, "cyl_sides": 24, "prism_sides": 16}),
]


_REQUIRED_BONES = (BONE_HEAD, BONE_PELVIS)


def _tri_bucket(target_tris: int) -> tuple[int, dict]:
	'''Smallest tri bucket that fits target_tris, or the largest bucket.'''
	return next(((cap, knobs) for cap, knobs in _TRI_BUCKETS if target_tris <= cap), _TRI_BUCKETS[-1])


# Mesh vertices interleave position and analytic normal: [px, py, pz, nx, ny, nz]
def _make_sphere(lat: int, lon: int) -> tuple[array, array]:
	'''Unit sphere at origin, radius 1 (normal == position per vertex).'''
	positions = []
	normals = []
	# +1 so the poles get their own ring at phi=0 and phi=pi
	for latitude_index in range(lat + 1):
		phi = math.pi * latitude_index / lat
		sin_phi = math.sin(phi)
		cos_phi = math.cos(phi)
		for longitude_index in range(lon):
			theta = 2.0 * math.pi * longitude_index / lon
			cos_theta = math.cos(theta)
			sin_theta = math.sin(theta)
			x = sin_phi * cos_theta
			y = cos_phi
			z = sin_phi * sin_theta
			positions.append((x, y, z))
			normals.append((x, y, z))

	indices: list[int] = []
	for latitude_index in range(lat):
		for longitude_index in range(lon):
			lower_left = latitude_index * lon + longitude_index
			lower_right = latitude_index * lon + (longitude_index + 1) % lon
			upper_left = (latitude_index + 1) * lon + longitude_index
			upper_right = (latitude_index + 1) * lon + (longitude_index + 1) % lon
			# Two tris per quad. Winding: CCW from outside
			indices.extend([lower_left, lower_right, upper_left])
			indices.extend([lower_right, upper_right, upper_left])

	return _pack_mesh(positions, normals, indices)


def _make_cylinder(sides: int) -> tuple[array, array]:
	'''Unit cylinder along Y, height 1 (y=-0.5..0.5), radius 1; side and cap vertices are duplicated so each gets its own normal.'''
	positions: list[tuple[float, float, float]] = []
	normals: list[tuple[float, float, float]] = []
	indices: list[int] = []

	# side: 2 verts per side index (bot, top), each with radial normal
	for side_index in range(sides):
		theta = 2.0 * math.pi * side_index / sides
		cos_theta = math.cos(theta)
		sin_theta = math.sin(theta)
		positions.append((cos_theta, -0.5, sin_theta))
		normals.append((cos_theta, 0.0, sin_theta))
		positions.append((cos_theta, 0.5, sin_theta))
		normals.append((cos_theta, 0.0, sin_theta))

	for side_index in range(sides):
		first_lower = side_index * 2
		first_upper = side_index * 2 + 1
		next_lower = ((side_index + 1) % sides) * 2
		next_upper = ((side_index + 1) % sides) * 2 + 1
		# CCW from outside: (a, c, b) + (b, c, d)
		indices.extend([first_lower, next_lower, first_upper])
		indices.extend([first_upper, next_lower, next_upper])

	# bottom cap: center vert + ring with -Y normal
	bottom_center = len(positions)
	positions.append((0.0, -0.5, 0.0))
	normals.append((0.0, -1.0, 0.0))
	bottom_ring = len(positions)
	for side_index in range(sides):
		theta = 2.0 * math.pi * side_index / sides
		positions.append((math.cos(theta), -0.5, math.sin(theta)))
		normals.append((0.0, -1.0, 0.0))
	for side_index in range(sides):
		first_lower = bottom_ring + side_index
		first_upper = bottom_ring + (side_index + 1) % sides
		# center, b, a: CW from above = CCW from below
		indices.extend([bottom_center, first_upper, first_lower])

	# top cap: center vert + ring with +Y normal
	top_center = len(positions)
	positions.append((0.0, 0.5, 0.0))
	normals.append((0.0, 1.0, 0.0))
	top_ring = len(positions)
	for side_index in range(sides):
		theta = 2.0 * math.pi * side_index / sides
		positions.append((math.cos(theta), 0.5, math.sin(theta)))
		normals.append((0.0, 1.0, 0.0))
	for side_index in range(sides):
		first_lower = top_ring + side_index
		first_upper = top_ring + (side_index + 1) % sides
		# CCW from above: center, a, b
		indices.extend([top_center, first_lower, first_upper])

	return _pack_mesh(positions, normals, indices)


def _make_box() -> tuple[array, array]:
	'''Unit box (-0.5..0.5)^3. Per-face normals (flat shading).'''
	faces = [
		# (normal, four corners CCW from outside)
		((0, 0, -1), [(-0.5, -0.5, -0.5), (0.5, -0.5, -0.5), (0.5, 0.5, -0.5), (-0.5, 0.5, -0.5)]),
		((0, 0, 1), [(-0.5, -0.5, 0.5), (-0.5, 0.5, 0.5), (0.5, 0.5, 0.5), (0.5, -0.5, 0.5)]),
		((-1, 0, 0), [(-0.5, -0.5, -0.5), (-0.5, 0.5, -0.5), (-0.5, 0.5, 0.5), (-0.5, -0.5, 0.5)]),
		((1, 0, 0), [(0.5, -0.5, -0.5), (0.5, -0.5, 0.5), (0.5, 0.5, 0.5), (0.5, 0.5, -0.5)]),
		((0, -1, 0), [(-0.5, -0.5, -0.5), (-0.5, -0.5, 0.5), (0.5, -0.5, 0.5), (0.5, -0.5, -0.5)]),
		((0, 1, 0), [(-0.5, 0.5, -0.5), (0.5, 0.5, -0.5), (0.5, 0.5, 0.5), (-0.5, 0.5, 0.5)]),
	]
	positions: list[tuple[float, float, float]] = []
	normals: list[tuple[float, float, float]] = []
	indices: list[int] = []
	for (nx, ny, nz), corners in faces:
		base = len(positions)
		for corner in corners:
			positions.append(corner)
			normals.append((nx, ny, nz))
		indices.extend([base, base + 1, base + 2])
		indices.extend([base, base + 2, base + 3])

	return _pack_mesh(positions, normals, indices)


# Matrix helpers use row-major float32 storage; GL_TRUE transposes it on upload
def _mat_identity() -> _Mat4:
	return _Mat4((1.0, 0.0, 0.0, 0.0, 0.0, 1.0, 0.0, 0.0, 0.0, 0.0, 1.0, 0.0, 0.0, 0.0, 0.0, 1.0))


def _mat_translation(tx: float, ty: float, tz: float) -> _Mat4:
	return _Mat4((1.0, 0.0, 0.0, tx, 0.0, 1.0, 0.0, ty, 0.0, 0.0, 1.0, tz, 0.0, 0.0, 0.0, 1.0))


def _mat_scale(sx: float, sy: float, sz: float) -> _Mat4:
	return _Mat4((sx, 0.0, 0.0, 0.0, 0.0, sy, 0.0, 0.0, 0.0, 0.0, sz, 0.0, 0.0, 0.0, 0.0, 1.0))


def _mat_align_y_to(direction) -> _Mat4:
	'''Rotation sending local +Y to direction; the reference flips from +Z to +X near-parallel so the cross product never degenerates.'''
	direction = _vec3(direction)
	norm = _norm(direction)
	if norm < 1e-6:
		return _mat_identity()
	up = direction / norm
	reference_axis = _Vec3((0.0, 0.0, 1.0))
	if abs(_dot(up, reference_axis)) > 0.999:
		reference_axis = _Vec3((1.0, 0.0, 0.0))
	right = _cross(reference_axis, up)
	right /= _norm(right)
	forward_axis = _cross(up, right)

	return _Mat4((right[0], up[0], forward_axis[0], 0.0, right[1], up[1], forward_axis[1], 0.0, right[2], up[2], forward_axis[2], 0.0, 0.0, 0.0, 0.0, 1.0))


def _mat_oriented_basis(right, up, fwd) -> _Mat4:
	'''Rotation mapping local +X/+Y/+Z to right/up/fwd; inputs must already be orthonormal.'''
	return _Mat4((right[0], up[0], fwd[0], 0.0, right[1], up[1], fwd[1], 0.0, right[2], up[2], fwd[2], 0.0, 0.0, 0.0, 0.0, 1.0))


def _mat_rotation_around_axis(axis, angle_rad: float) -> _Mat4:
	'''Rodrigues rotation matrix around a unit axis.'''
	x, y, z = float(axis[0]), float(axis[1]), float(axis[2])
	cos_angle = math.cos(angle_rad)
	sin_angle = math.sin(angle_rad)
	one_minus_cos = 1.0 - cos_angle
	return _Mat4((
		cos_angle + x * x * one_minus_cos, x * y * one_minus_cos - z * sin_angle, x * z * one_minus_cos + y * sin_angle, 0.0,
		y * x * one_minus_cos + z * sin_angle, cos_angle + y * y * one_minus_cos, y * z * one_minus_cos - x * sin_angle, 0.0,
		z * x * one_minus_cos - y * sin_angle, z * y * one_minus_cos + x * sin_angle, cos_angle + z * z * one_minus_cos, 0.0,
		0.0, 0.0, 0.0, 1.0,
	))


def _mat_align_torso(bone_dir, player_right) -> _Mat4:
	'''Align local +Y to bone_dir while keeping +X near player_right, so +Z becomes the player's forward.'''
	bone_dir = _vec3(bone_dir)
	player_right = _vec3(player_right)
	bone_length = _norm(bone_dir)
	if bone_length < 1e-6:
		return _mat_identity()
	bone_axis = bone_dir / bone_length
	# Project the shoulder direction off the spine to keep the torso basis orthogonal
	projected_right = player_right - bone_axis * _dot(player_right, bone_axis)
	projected_right_norm = _norm(projected_right)
	if projected_right_norm < 1e-3:
		return _mat_align_y_to(bone_dir)
	projected_right = projected_right / projected_right_norm
	forward_axis = _cross(projected_right, bone_axis)  # local +Z = right x up = forward
	forward_norm = _norm(forward_axis)
	if forward_norm < 1e-3:
		return _mat_align_y_to(bone_dir)
	forward_axis = forward_axis / forward_norm
	return _mat_oriented_basis(projected_right, bone_axis, forward_axis)


def _basis_from_points(left_point, right_point, head_point, pelvis_point) -> tuple[_Vec3, _Vec3, _Vec3] | None:
	'''Build an orthonormal body basis from left/right and head/pelvis points; return None for degenerate input.'''
	right = _vec3(right_point) - _vec3(left_point)
	right_length = _norm(right)
	if right_length < 1e-3:
		return None
	right = right / right_length

	up = _vec3(head_point) - _vec3(pelvis_point)
	up_length = _norm(up)
	if up_length < 1e-3:
		return None
	up = up / up_length

	forward_axis = _cross(up, right)
	forward_length = _norm(forward_axis)
	if forward_length < 1e-3:
		return None
	forward_axis = forward_axis / forward_length

	# Re-orthogonalise right against (fwd, up) so the basis is exact
	right = _cross(forward_axis, up)
	right_length = _norm(right)
	if right_length < 1e-3:
		return None
	right = right / right_length
	return right, up, forward_axis


_VERT_SHADER = """
#version 330 core
layout(location = 0) in vec3 in_pos;
layout(location = 1) in vec3 in_normal;
uniform mat4 u_view_proj;
uniform mat4 u_model;
out vec3 v_normal_local;
out vec3 v_pos_local;
void main() {
    v_normal_local = in_normal;
    v_pos_local = in_pos;
    gl_Position = u_view_proj * u_model * vec4(in_pos, 1.0);
}
"""

_FRAG_SHADER = """
#version 330 core
in vec3 v_normal_local;
in vec3 v_pos_local;
out vec4 frag;
uniform vec4 u_color;
uniform float u_rim;
void main() {
    // Pseudo-light from local +Y (bone axis for cylinders, world-up for spheres/boxes) -> "top brighter" toon look.
    vec3 n = normalize(v_normal_local);
    float lambert = clamp(n.y * 0.5 + 0.5, 0.0, 1.0);

    // Rim term brightens silhouette edges so each capsule reads as 3D.
    float rim = 1.0 - abs(n.y);
    float rim_term = pow(clamp(rim, 0.0, 1.0), 2.0);

    float shade = mix(1.0, 0.45 + 0.55 * lambert + 0.30 * rim_term, u_rim);
    shade = clamp(shade, 0.0, 1.4);

    frag = vec4(u_color.rgb * shade, u_color.a);
}
"""


class Chams3DRenderer:
	"""Per-context shader and mesh cache that flushes one chams pass."""
	def __init__(self) -> None:
		self._gl_ready: bool = False
		self._program: int = 0
		self._uloc_view_proj: int = -1
		self._uloc_model: int = -1
		self._uloc_color: int = -1
		self._uloc_rim: int = -1
		self._meshes_by_quality: dict[str, dict[str, tuple[int, int, int, int]]] = {}
		self._queue: list[tuple] = []
		self._context_token = _UNSET_CONTEXT

	def invalidate_context(self, context_token=_UNSET_CONTEXT) -> None:
		'''Forget object names without deleting them from a replacement context.'''
		self._gl_ready = False
		self._program = 0
		self._uloc_view_proj = -1
		self._uloc_model = -1
		self._uloc_color = -1
		self._uloc_rim = -1
		self._meshes_by_quality.clear()
		self._context_token = context_token

	def _sync_context(self) -> None:
		'''Forget object names when GLFW has replaced the GL context.'''
		context_token = _current_context_token()
		if context_token != self._context_token:
			self.invalidate_context(context_token)

	def begin_frame(self) -> None:
		self._queue.clear()

	def queue(self, player, view_matrix, cfg) -> None:
		if not getattr(cfg, "enabled", False):
			return
		if not getattr(player, "bones", None):
			return
		self._queue.append((player, view_matrix, cfg))

	def end_frame(self) -> None:
		'''Draw the queued players and restore the previous GL state.'''
		if not self._queue:
			return
		self._sync_context()
		if not self._gl_ready:
			try:
				vertex_shader = gl_shaders.compileShader(_VERT_SHADER, gl.GL_VERTEX_SHADER)
				fragment_shader = gl_shaders.compileShader(_FRAG_SHADER, gl.GL_FRAGMENT_SHADER)
				self._program = gl_shaders.compileProgram(vertex_shader, fragment_shader)
				self._uloc_view_proj = gl.glGetUniformLocation(self._program, "u_view_proj")
				self._uloc_model = gl.glGetUniformLocation(self._program, "u_model")
				self._uloc_color = gl.glGetUniformLocation(self._program, "u_color")
				self._uloc_rim = gl.glGetUniformLocation(self._program, "u_rim")
				self._gl_ready = True
			except Exception as exc:
				print(f"[chams3d] shader compile failed: {exc!r}")
				self._queue.clear()
				return

		# Resolve quality once; players in the same bucket share one mesh set
		frame_config = self._queue[0][2]
		target_tris = int(getattr(frame_config, "tri_count", 0) or 0)
		if target_tris > 0:
			cap, knobs = _tri_bucket(target_tris)
			quality_key = f"tris_{cap}"
		else:
			legacy_quality = str(getattr(frame_config, "quality", "medium")).lower()
			if legacy_quality in _QUALITY:
				quality_key = legacy_quality
				knobs = _QUALITY[legacy_quality]
			else:
				cap, knobs = _tri_bucket(2000)
				quality_key = f"tris_{cap}"
		meshes = self._meshes_by_quality.get(quality_key)
		if meshes is None:
			meshes = {}
			primitives = {
				"sphere": _make_sphere(knobs["sphere_lat"], knobs["sphere_lon"]),
				"cylinder": _make_cylinder(knobs["cyl_sides"]),
				"prism": _make_cylinder(knobs["prism_sides"]),
				"box": _make_box(),
			}
			for name, (vertices, indices) in primitives.items():
				vao = int(gl.glGenVertexArrays(1))
				gl.glBindVertexArray(vao)

				vbo = int(gl.glGenBuffers(1))
				gl.glBindBuffer(gl.GL_ARRAY_BUFFER, vbo)
				gl.glBufferData(gl.GL_ARRAY_BUFFER, len(vertices) * vertices.itemsize, np.frombuffer(vertices, dtype=np.float32), gl.GL_STATIC_DRAW)

				ibo = int(gl.glGenBuffers(1))
				gl.glBindBuffer(gl.GL_ELEMENT_ARRAY_BUFFER, ibo)
				gl.glBufferData(gl.GL_ELEMENT_ARRAY_BUFFER, len(indices) * indices.itemsize, np.frombuffer(indices, dtype=np.uint32), gl.GL_STATIC_DRAW)

				stride = 6 * 4
				gl.glEnableVertexAttribArray(0)
				gl.glVertexAttribPointer(0, 3, gl.GL_FLOAT, gl.GL_FALSE, stride, c_void_p(0))
				gl.glEnableVertexAttribArray(1)
				gl.glVertexAttribPointer(1, 3, gl.GL_FLOAT, gl.GL_FALSE, stride, c_void_p(3 * 4))
				meshes[name] = (vao, len(indices), vbo, ibo)

			# Unbind the VAO before its captured IBO binding
			gl.glBindVertexArray(0)
			gl.glBindBuffer(gl.GL_ELEMENT_ARRAY_BUFFER, 0)
			gl.glBindBuffer(gl.GL_ARRAY_BUFFER, 0)
			self._meshes_by_quality[quality_key] = meshes

		prev_program = int(gl.glGetIntegerv(gl.GL_CURRENT_PROGRAM))
		prev_vao = int(gl.glGetIntegerv(gl.GL_VERTEX_ARRAY_BINDING))
		prev_blend = bool(gl.glIsEnabled(gl.GL_BLEND))
		prev_depth_test = bool(gl.glIsEnabled(gl.GL_DEPTH_TEST))
		prev_depth_write = bool(gl.glGetBooleanv(gl.GL_DEPTH_WRITEMASK))
		prev_cull = bool(gl.glIsEnabled(gl.GL_CULL_FACE))
		prev_blend_src = int(gl.glGetIntegerv(gl.GL_BLEND_SRC_ALPHA))
		prev_blend_dst = int(gl.glGetIntegerv(gl.GL_BLEND_DST_ALPHA))

		gl.glUseProgram(self._program)
		gl.glEnable(gl.GL_BLEND)
		gl.glBlendFunc(gl.GL_SRC_ALPHA, gl.GL_ONE_MINUS_SRC_ALPHA)
		# Each player clears depth so body parts self-occlude independently of player order
		gl.glEnable(gl.GL_DEPTH_TEST)
		gl.glDepthMask(gl.GL_TRUE)
		gl.glDepthFunc(gl.GL_LESS)
		gl.glEnable(gl.GL_CULL_FACE)
		gl.glCullFace(gl.GL_BACK)
		gl.glFrontFace(gl.GL_CCW)

		active_view_matrix = None
		for player, view_matrix, cfg in self._queue:
			if view_matrix is not active_view_matrix:
				view_values: list[float] = []
				for value in view_matrix:
					try:
						view_values.append(float(value))
					except (TypeError, ValueError):
						view_values.extend(float(component) for component in value)
				if len(view_values) != 16:
					raise ValueError(f"expected a 4x4 view matrix, got {len(view_values)} values")
				view_buffer = array("f", view_values)
				# Source matrices are row-major, so GL transposes them on upload
				gl.glUniformMatrix4fv(self._uloc_view_proj, 1, gl.GL_TRUE, np.frombuffer(view_buffer, dtype=np.float32))
				active_view_matrix = view_matrix
			self._draw_player(player, cfg, meshes)

		gl.glBindVertexArray(prev_vao)
		gl.glUseProgram(prev_program)
		if prev_blend:
			gl.glBlendFunc(prev_blend_src, prev_blend_dst)
		else:
			gl.glDisable(gl.GL_BLEND)
		if not prev_depth_test:
			gl.glDisable(gl.GL_DEPTH_TEST)
		gl.glDepthMask(gl.GL_TRUE if prev_depth_write else gl.GL_FALSE)
		if not prev_cull:
			gl.glDisable(gl.GL_CULL_FACE)

		self._queue.clear()

	def _draw_player(self, player, cfg, meshes: dict[str, tuple[int, int, int, int]]) -> None:
		"""Draw available body parts in list order, with an independent depth clear for each player."""
		bones = player.bones
		if not bones:
			return
		if not all(end_point in bones for end_point in _REQUIRED_BONES):
			return

		head = _vec3(bones[BONE_HEAD])
		pelvis_position = _vec3(bones[BONE_PELVIS])
		foot_pos = None
		if BONE_L_FOOT in bones:
			foot_pos = _vec3(bones[BONE_L_FOOT])
		elif BONE_R_FOOT in bones:
			foot_pos = _vec3(bones[BONE_R_FOOT])

		if foot_pos is not None:
			height = _norm(head - foot_pos)
		else:
			height = _norm(head - pelvis_position) * 2.0
		if height < 5.0 or height > 500.0:
			return

		team_colour = cfg.ct_color if player.team == TEAM_CT else cfg.t_color
		opacity = float(getattr(cfg, "opacity", 0.7))
		opacity = max(0.0, min(1.0, opacity))
		color = array("f", (float(team_colour[0]), float(team_colour[1]), float(team_colour[2]), opacity))
		rim = float(getattr(cfg, "rim_strength", 1.0))
		rim = max(0.0, min(4.0, rim))

		# Keep body-part occlusion independent of world depth and previously drawn players
		gl.glClear(gl.GL_DEPTH_BUFFER_BIT)

		gl.glUniform4fv(self._uloc_color, 1, np.frombuffer(color, dtype=np.float32))
		gl.glUniform1f(self._uloc_rim, rim)

		radii_cfg = getattr(cfg, "radii", None)
		if radii_cfg is not None and not isinstance(radii_cfg, dict):
			# Section-backed settings expose their radius mapping through _data
			radii_cfg = getattr(radii_cfg, "_data", None) or {}

		# Upper and lower bases let the torso twist independently of the legs
		upper_bones = (BONE_L_CLAV, BONE_R_CLAV, BONE_HEAD, BONE_PELVIS)
		basis = (
			_basis_from_points(bones[BONE_L_CLAV], bones[BONE_R_CLAV], bones[BONE_HEAD], bones[BONE_PELVIS]) if all(bone in bones for bone in upper_bones) else None
		)
		lower_bones = (BONE_L_THIGH, BONE_R_THIGH, BONE_HEAD, BONE_PELVIS)
		basis_legs = (
			_basis_from_points(bones[BONE_L_THIGH], bones[BONE_R_THIGH], bones[BONE_HEAD], bones[BONE_PELVIS]) if all(bone in bones for bone in lower_bones) else None
		) or basis

		aspects_cfg = getattr(cfg, "aspects", None)

		def _aspect_value(key: str, default: float, lo: float, hi: float) -> float:
			value = default
			if aspects_cfg is not None:
				try:
					value = float(getattr(aspects_cfg, key, default))
				except (TypeError, ValueError):
					value = default
			return max(lo, min(hi, value))

		head_y_aspect = _aspect_value("head_height", 1.45, 0.50, 3.00)
		torso_z_aspect = _aspect_value("torso_depth", 0.70, 0.20, 1.50)
		foot_w_aspect = _aspect_value("foot_width", 0.55, 0.10, 1.50)
		foot_h_aspect = _aspect_value("foot_height", 0.50, 0.10, 1.50)
		foot_fwd_off = _aspect_value("foot_forward", 0.30, 0.00, 1.00)
		foot_down_off = _aspect_value("foot_down", 0.50, 0.00, 1.50)
		hand_w_aspect = _aspect_value("hand_width", 0.50, 0.10, 1.50)
		hand_t_aspect = _aspect_value("hand_thickness", 0.40, 0.10, 1.50)
		hand_past_wrist = _aspect_value("hand_extend", 0.50, 0.00, 1.50)
		butt_y_aspect = _aspect_value("butt_height", 0.70, 0.20, 1.50)
		butt_z_aspect = _aspect_value("butt_depth", 0.95, 0.20, 1.50)
		butt_back_off = _aspect_value("butt_back", 0.40, 0.00, 1.50)
		head_fwd_off = _aspect_value("head_forward", 0.00, 0.00, 1.50)
		chest_lean = _aspect_value("chest_lean", 0.077, 0.00, 1.00)
		foot_toe_out_deg = _aspect_value("foot_toe_out", 15.0, 0.00, 60.0)
		shoulder_z_aspect = _aspect_value("shoulder_depth", 0.55, 0.10, 1.50)
		hip_z_aspect = _aspect_value("hip_depth", 0.85, 0.10, 1.50)
		# Head pitch pivots around the chin; trap_back offsets the neck end toward the upper back
		head_pitch_deg = _aspect_value("head_pitch", 0.0, -60.0, 60.0)
		trap_back = _aspect_value("trap_back", 0.40, 0.00, 1.00)
		# Positive pitch moves the trap forward and toes down; finger offsets are relative to the wrist
		trap_pitch_deg = _aspect_value("trap_pitch", 22.0, -60.0, 60.0)
		foot_pitch_deg = _aspect_value("foot_pitch", 22.0, -60.0, 60.0)
		finger_fwd_off = _aspect_value("finger_forward", 0.55, -1.00, 2.00)
		finger_down_off = _aspect_value("finger_down", 0.45, -1.00, 2.00)
		# The vest interpolates between pelvis and chest; shoulder extension reaches beyond the shoulder bone
		head_up_off = _aspect_value("head_up", 0.45, -1.00, 2.00)
		vest_lerp = _aspect_value("vest_lerp", 0.50, 0.00, 1.50)
		shoulder_extend = _aspect_value("shoulder_extend", 0.30, -0.50, 1.00)
		upper_back_depth = _aspect_value("upper_back_depth", 1.40, 0.50, 3.00)
		upper_back_fwd = _aspect_value("upper_back_forward", 0.0, -1.5, 1.5)
		upper_back_up_off = _aspect_value("upper_back_up", -0.05, -1.5, 1.5)
		shoulder_offset_x = _aspect_value("shoulder_offset_x", 0.0, -2.0, 2.0)
		shoulder_offset_y = _aspect_value("shoulder_offset_y", 0.0, -2.0, 2.0)
		shoulder_offset_z = _aspect_value("shoulder_offset_z", 0.0, -2.0, 2.0)
		vest_w = _aspect_value("vest_width", 2.5, 0.5, 4.0)
		vest_h = _aspect_value("vest_height", 0.6, 0.1, 2.0)
		vest_d = _aspect_value("vest_depth", 0.7, 0.1, 2.0)
		head_w_aspect = _aspect_value("head_width", 1.0, 0.5, 2.0)
		head_d_aspect = _aspect_value("head_depth", 1.0, 0.5, 2.0)
		head_yaw_deg = _aspect_value("head_yaw", 0.0, -60.0, 60.0)
		trap_w_aspect = _aspect_value("trap_width", 1.0, 0.5, 2.0)
		trap_h_aspect = _aspect_value("trap_height", 1.0, 0.5, 2.0)
		trap_yaw_deg = _aspect_value("trap_yaw", 0.0, -60.0, 60.0)

		hand_factor = 0.105
		if radii_cfg:
			hand_radius_override = radii_cfg.get("hand")
			if hand_radius_override is not None:
				try:
					hand_factor = float(hand_radius_override)
				except (TypeError, ValueError):
					pass
		hand_base_size = height * hand_factor

		for name, primitive_type, bone_a, bone_b, default_factor in _BODY_PARTS:
			factor = default_factor
			if radii_cfg:
				radius_override = radii_cfg.get(name)
				if radius_override is None:
					shared_key = _SYMMETRIC_RADIUS_KEYS.get(name)
					if shared_key is not None:
						radius_override = radii_cfg.get(shared_key)
				if radius_override is not None:
					factor = float(radius_override)
			base_size = height * factor

			model = None
			entry = None

			if primitive_type == "head_oval":
				if bone_a not in bones:
					continue
				position = _vec3(bones[bone_a])
				if basis is not None:
					right_w, up_w, fwd_w = basis
					# head_pitch pins the chin at local (0, -0.5, 0) by shifting the centre by 0.5 * head_h * (new_up - up_w)
					if head_pitch_deg != 0.0:
						angle_rad = math.radians(head_pitch_deg)
						cos_angle = math.cos(angle_rad)
						sin_angle = math.sin(angle_rad)
						new_up = up_w * cos_angle + fwd_w * sin_angle
						new_fwd = -up_w * sin_angle + fwd_w * cos_angle
						head_h_half = base_size * head_y_aspect * 0.5
						position = position + (new_up - up_w) * head_h_half
						up_w, fwd_w = new_up, new_fwd
					if head_fwd_off > 0.0:
						position = position + fwd_w * (base_size * head_fwd_off)
					# 3x lifts the ellipsoid from the neck-base bone onto the skull at the default slider value
					if head_up_off != 0.0:
						position = position + up_w * (base_size * head_up_off * 3.0)
					rotation = _mat_oriented_basis(right_w, up_w, fwd_w)
					# head_yaw twists around basis.up, stacked on top of head_pitch
					if head_yaw_deg != 0.0:
						angle_rad = math.radians(head_yaw_deg)
						yaw_rotation = _mat_rotation_around_axis(up_w, angle_rad)
						rotation = yaw_rotation @ rotation
				else:
					rotation = _mat_identity()
				# Default 1.0/1.45/1.0 (width/height/depth) gives a tall-Y oval
				scale = _mat_scale(base_size * head_w_aspect, base_size * head_y_aspect, base_size * head_d_aspect)
				model = _mat_translation(*position) @ rotation @ scale
				entry = meshes.get("sphere")

			elif primitive_type == "knee_sphere":
				if bone_a not in bones:
					continue
				position = _vec3(bones[bone_a])
				# Outer-shoulder offsets mirror X outward on both sides; knees and elbows remain bone-anchored
				if (
					basis is not None
					and name in ("l_shoulder_sphere", "r_shoulder_sphere")
					and (shoulder_offset_x != 0.0 or shoulder_offset_y != 0.0 or shoulder_offset_z != 0.0)
				):
					sign_x = -1.0 if name == "l_shoulder_sphere" else 1.0
					position = (
						position
						+ basis[0] * (base_size * shoulder_offset_x * sign_x)
						+ basis[1] * (base_size * shoulder_offset_y)
						+ basis[2] * (base_size * shoulder_offset_z)
					)
				scale = _mat_scale(base_size, base_size, base_size)
				model = _mat_translation(*position) @ scale
				entry = meshes.get("sphere")

			elif primitive_type == "butt_sphere":
				if bone_a not in bones:
					continue
				position = _vec3(bones[bone_a])
				# Lower-body basis so glutes follow run direction, not upper-torso aim
				if basis_legs is not None:
					right, up, fwd = basis_legs
					position = position - fwd * (base_size * butt_back_off)
					rotation = _mat_oriented_basis(right, up, fwd)
				else:
					rotation = _mat_identity()
				scale = _mat_scale(base_size, base_size * butt_y_aspect, base_size * butt_z_aspect)
				model = _mat_translation(*position) @ rotation @ scale
				entry = meshes.get("sphere")

			elif primitive_type == "foot_box":
				if bone_a not in bones:
					continue
				position = _vec3(bones[bone_a])
				length = base_size
				width = base_size * foot_w_aspect
				foot_height = base_size * foot_h_aspect
				# Lower-body basis so boots track the run direction
				if basis_legs is not None:
					right, up, fwd = basis_legs
					# Mirror toe-out around the lower-body up axis
					if foot_toe_out_deg != 0.0:
						sign = 1.0 if name == "l_foot" else -1.0
						angle_rad = math.radians(foot_toe_out_deg) * sign
						cos_angle = math.cos(angle_rad)
						sin_angle = math.sin(angle_rad)
						r_new = right * cos_angle + fwd * sin_angle
						f_new = -right * sin_angle + fwd * cos_angle
						right, fwd = r_new, f_new
					# Pitch around basis_legs.right (toe down +)
					if foot_pitch_deg != 0.0:
						angle_rad = math.radians(foot_pitch_deg)
						cos_angle = math.cos(angle_rad)
						sin_angle = math.sin(angle_rad)
						u_new = up * cos_angle + fwd * sin_angle
						f_new = -up * sin_angle + fwd * cos_angle
						up, fwd = u_new, f_new
					# Anchor the ankle at the back and top of the boot
					position = position + fwd * (length * foot_fwd_off) - up * (foot_height * foot_down_off)
					rotation = _mat_oriented_basis(right, up, fwd)
				else:
					rotation = _mat_identity()
				scale = _mat_scale(width, foot_height, length)
				model = _mat_translation(*position) @ rotation @ scale
				entry = meshes.get("box")

			elif primitive_type == "cylinder":
				if bone_a not in bones or bone_b not in bones:
					continue
				start_point = _vec3(bones[bone_a])
				end_point = _vec3(bones[bone_b])
				direction = end_point - start_point
				length = _norm(direction)
				if length < 1e-3:
					continue
				midpoint = (start_point + end_point) * 0.5
				rotation = _mat_align_y_to(direction)
				limb_size = _dynamic_limb_size(name, length, base_size)
				scale = _mat_scale(limb_size, length, limb_size)
				model = _mat_translation(*midpoint) @ rotation @ scale
				entry = meshes.get("cylinder")

			elif primitive_type == "torso_prism":
				if bone_a not in bones or bone_b not in bones:
					continue
				start_point = _vec3(bones[bone_a])
				end_point = _vec3(bones[bone_b])
				# Chest and trap offsets move opposite endpoints toward the neck and upper back
				if name == "chest" and basis is not None and chest_lean > 0.0:
					start_point = start_point + basis[2] * (base_size * chest_lean)
				if name == "trap" and basis is not None and trap_back > 0.0:
					end_point = end_point - basis[2] * (base_size * trap_back)
				direction = end_point - start_point
				length = _norm(direction)
				if length < 1e-3:
					continue
				midpoint = (start_point + end_point) * 0.5
				if basis is not None:
					rotation = _mat_align_torso(direction, basis[0])
				else:
					rotation = _mat_align_y_to(direction)
				# Apply trap pitch, yaw and midpoint offsets in that order
				if name == "trap" and basis is not None and trap_pitch_deg != 0.0:
					pitch_rotation = _mat_rotation_around_axis(basis[0], math.radians(trap_pitch_deg))
					rotation = pitch_rotation @ rotation
				if name == "trap" and basis is not None and trap_yaw_deg != 0.0:
					yaw_rotation = _mat_rotation_around_axis(basis[1], math.radians(trap_yaw_deg))
					rotation = yaw_rotation @ rotation
				if name == "trap" and basis is not None and (upper_back_fwd != 0.0 or upper_back_up_off != 0.0):
					midpoint = midpoint + basis[2] * (length * upper_back_fwd) + basis[1] * (length * upper_back_up_off)
				# Keep torso depth thinner than width; trap dimensions have independent multipliers
				width = base_size
				depth = base_size * torso_z_aspect
				effective_length = length
				if name == "trap":
					width = width * trap_w_aspect
					depth = depth * upper_back_depth
					effective_length = length * trap_h_aspect
				scale = _mat_scale(width, effective_length, depth)
				model = _mat_translation(*midpoint) @ rotation @ scale
				entry = meshes.get("prism")

			elif primitive_type == "hand_box":
				# Extend the hand from the wrist along the forearm without requiring a torso basis
				if bone_a not in bones or bone_b not in bones:
					continue
				start_point = _vec3(bones[bone_a])
				end_point = _vec3(bones[bone_b])
				direction = end_point - start_point
				arm_length = _norm(direction)
				if arm_length < 1e-3:
					continue
				hand_length = base_size
				hand_width = base_size * hand_w_aspect
				hand_thickness = base_size * hand_t_aspect
				# Default half-length extension keeps the proximal edge at the wrist
				arm_direction = direction / arm_length
				centre = end_point + arm_direction * (hand_length * hand_past_wrist)
				rotation = _mat_align_y_to(direction)
				scale = _mat_scale(hand_width, hand_length, hand_thickness)
				model = _mat_translation(*centre) @ rotation @ scale
				entry = meshes.get("box")

			elif primitive_type == "fingers_box":
				# Project downward motion off the forearm to orient the curled fingers
				if bone_a not in bones or bone_b not in bones:
					continue
				start_point = _vec3(bones[bone_a])
				end_point = _vec3(bones[bone_b])
				direction = end_point - start_point
				arm_length = _norm(direction)
				if arm_length < 1e-3:
					continue
				arm_direction = direction / arm_length
				# Finger dimensions map to the along-arm, downward-curl, and lateral axes
				finger_width = base_size * 1.6
				finger_length = base_size
				finger_thickness = base_size
				placed = False
				if basis is not None:
					down = -basis[1]
					# Project gravity perpendicular to the arm; a near-vertical arm falls back to its along-arm orientation
					projected_down = down - _dot(down, arm_direction) * arm_direction
					down_length = _norm(projected_down)
					if down_length > 1e-3:
						down_axis = projected_down / down_length
						along_axis = arm_direction
						side_axis = _cross(along_axis, down_axis)
						# Tilt the finger basis 15 degrees into the grip
						tilt = math.radians(15.0)
						cos_tilt = math.cos(tilt)
						sin_tilt = math.sin(tilt)
						new_x = along_axis * cos_tilt + down_axis * sin_tilt
						new_y = -along_axis * sin_tilt + down_axis * cos_tilt
						along_axis, down_axis = new_x, new_y
						rotation = _mat_oriented_basis(along_axis, down_axis, side_axis)
						# Forward offset uses hand length; downward offset uses finger length
						centre = end_point + along_axis * (hand_base_size * finger_fwd_off) + down_axis * (finger_length * finger_down_off)
						scale = _mat_scale(finger_width, finger_length, finger_thickness)
						model = _mat_translation(*centre) @ rotation @ scale
						entry = meshes.get("box")
						placed = True
				if not placed:
					rotation = _mat_align_y_to(direction)
					centre = end_point + arm_direction * (hand_base_size * finger_fwd_off)
					scale = _mat_scale(finger_width, finger_length, finger_thickness)
					model = _mat_translation(*centre) @ rotation @ scale
					entry = meshes.get("box")

			elif primitive_type == "aniso_cylinder_x":
				# Shoulder and hip bars use independent thickness and depth
				if bone_a not in bones or bone_b not in bones:
					continue
				start_point = _vec3(bones[bone_a])
				end_point = _vec3(bones[bone_b])
				direction = end_point - start_point
				length = _norm(direction)
				if length < 1e-3:
					continue
				midpoint = (start_point + end_point) * 0.5
				# Use player up as the thickness axis while local Y spans the bone pair
				if basis is not None:
					rotation = _mat_align_torso(direction, basis[1])
				else:
					rotation = _mat_align_y_to(direction)
				depth_aspect = hip_z_aspect if name == "hips" else shoulder_z_aspect
				# Mesh axes map X to vertical, Y to bar span, and Z to depth
				scale = _mat_scale(base_size, length, base_size * depth_aspect)
				model = _mat_translation(*midpoint) @ rotation @ scale
				entry = meshes.get("cylinder")

			elif primitive_type == "front_prism":
				# Belly slab between SPINE and PELVIS, offset forward of the spine prism
				if bone_a not in bones or bone_b not in bones:
					continue
				start_point = _vec3(bones[bone_a])
				end_point = _vec3(bones[bone_b])
				direction = end_point - start_point
				length = _norm(direction)
				if length < 1e-3:
					continue
				midpoint = (start_point + end_point) * 0.5
				if basis is not None:
					rotation = _mat_align_torso(direction, basis[0])
					midpoint = midpoint + basis[2] * (base_size * 0.65)
				else:
					rotation = _mat_align_y_to(direction)
				# Belly width follows its radius setting; reduced depth keeps it close to the spine
				width = base_size
				depth = base_size * torso_z_aspect * 0.6
				scale = _mat_scale(width, length, depth)
				model = _mat_translation(*midpoint) @ rotation @ scale
				entry = meshes.get("prism")

			elif primitive_type == "back_prism":
				# Offset the neck-to-head prism backward to close the gap above the trap
				if bone_a not in bones or bone_b not in bones:
					continue
				start_point = _vec3(bones[bone_a])
				end_point = _vec3(bones[bone_b])
				direction = end_point - start_point
				length = _norm(direction)
				if length < 1e-3:
					continue
				midpoint = (start_point + end_point) * 0.5
				if basis is not None:
					rotation = _mat_align_torso(direction, basis[0])
					midpoint = midpoint - basis[2] * (base_size * 1.0)
				else:
					rotation = _mat_align_y_to(direction)
				width = base_size
				depth = base_size * 0.8
				scale = _mat_scale(width, length, depth)
				model = _mat_translation(*midpoint) @ rotation @ scale
				entry = meshes.get("prism")

			elif primitive_type == "shoulder_box":
				# The deltoid maps +Y along the bone, +X to basis.up and +Z to depth; shoulder_extend lengthens its outer end
				if bone_a not in bones or bone_b not in bones:
					continue
				start_point = _vec3(bones[bone_a])
				end_point = _vec3(bones[bone_b])
				direction = end_point - start_point
				length = _norm(direction)
				if length < 1e-3:
					continue
				if shoulder_extend != 0.0:
					end_point = end_point + direction * shoulder_extend
					direction = end_point - start_point
					length = _norm(direction)
				midpoint = (start_point + end_point) * 0.5
				if basis is not None:
					rotation = _mat_align_torso(direction, basis[1])
				else:
					rotation = _mat_align_y_to(direction)
				width = base_size * 1.4
				depth = base_size * 0.9
				scale = _mat_scale(width, length, depth)
				model = _mat_translation(*midpoint) @ rotation @ scale
				entry = meshes.get("box")

			elif primitive_type == "vest_box":
				# Interpolate the vest anchor between pelvis and chest, then offset it ahead of the spine
				if bone_a not in bones or bone_b not in bones:
					continue
				pelvis = _vec3(bones[bone_a])
				spine = _vec3(bones[bone_b])
				blend_weight = float(vest_lerp)
				base_pos = pelvis * (1.0 - blend_weight) + spine * blend_weight
				if basis is not None:
					right_w, up_w, fwd_w = basis
					centre = base_pos + fwd_w * (base_size * 0.3)
					rotation = _mat_oriented_basis(right_w, up_w, fwd_w)
				else:
					centre = base_pos
					rotation = _mat_identity()
				# Wide-X / thin-Y / thin-Z slab; aspects decouple vest dimensions from torso width
				width_x = base_size * vest_w
				height_y = base_size * vest_h
				depth_z = base_size * vest_d
				scale = _mat_scale(width_x, height_y, depth_z)
				model = _mat_translation(*centre) @ rotation @ scale
				entry = meshes.get("box")

			else:
				continue

			if model is None or entry is None:
				continue
			vao, index_count, _, ibo = entry
			gl.glUniformMatrix4fv(self._uloc_model, 1, gl.GL_TRUE, np.frombuffer(model, dtype=np.float32))
			gl.glBindVertexArray(vao)
			# Rebind the index buffer because another draw pass may have cleared the VAO binding
			gl.glBindBuffer(gl.GL_ELEMENT_ARRAY_BUFFER, ibo)
			gl.glDrawElements(gl.GL_TRIANGLES, index_count, gl.GL_UNSIGNED_INT, None)

	def shutdown(self) -> None:
		# GL may already be gone if the overlay was torn down first
		owns_current_context = self._context_token == _current_context_token()
		if owns_current_context:
			try:
				if self._program:
					gl.glDeleteProgram(self._program)
			except Exception:
				pass
		self._program = 0
		if owns_current_context:
			for mesh_set in self._meshes_by_quality.values():
				for vao, _, vbo, ibo in mesh_set.values():
					for fn, target in ((gl.glDeleteVertexArrays, vao), (gl.glDeleteBuffers, vbo), (gl.glDeleteBuffers, ibo)):
						try:
							fn(1, [target])
						except Exception:
							pass
		self._meshes_by_quality.clear()
		self._gl_ready = False
		self._context_token = _UNSET_CONTEXT


# Module-level functional API retained for main.py compatibility

_renderer: Chams3DRenderer | None = None


def _get_renderer() -> Chams3DRenderer:
	global _renderer
	if _renderer is None:
		_renderer = Chams3DRenderer()
	return _renderer


def chams_begin_frame() -> None:
	'''Reset the player queue before the wallhack draw loop.'''
	_get_renderer().begin_frame()


def chams_end_frame() -> None:
	'''Flush queued bodies after player collection and before imgui so ESP lines remain in front.'''
	_get_renderer().end_frame()


def chams_shutdown() -> None:
	'''Release the shader and cached vertex arrays and buffers.'''
	global _renderer
	if _renderer is not None:
		_renderer.shutdown()
		_renderer = None


def chams_context_recreated() -> None:
	'''Invalidate cached GL names after Overlay replaces its GLFW context.'''
	if _renderer is not None:
		_renderer.invalidate_context()


def draw_chams(draw_list, player, view_matrix, screen_w: int, screen_h: int, cfg) -> None:
	'''Queue a player for the 3D pass; draw_list, screen_w and screen_h are accepted for API compatibility only.'''
	_get_renderer().queue(player, view_matrix, cfg)


__all__ = ["draw_chams", "chams_begin_frame", "chams_end_frame", "chams_shutdown", "chams_context_recreated"]
