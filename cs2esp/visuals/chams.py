"""OpenGL low-poly humanoid chams rendered before the imgui overlay pass."""

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
    """Return the native OpenGL context handle for cache invalidation."""
    try:
        return gl_platform.PLATFORM.GetCurrentContext()
    except Exception:
        return None


class _Vec3(tuple):
    """Small immutable vector supporting the arithmetic used by the renderer."""

    __slots__ = ()

    def __new__(cls, values: Iterable[float]):
        x, y, z = values
        return super().__new__(cls, (float(x), float(y), float(z)))

    def __add__(self, other) -> "_Vec3":
        return _Vec3((self[0] + other[0], self[1] + other[1],
                      self[2] + other[2]))

    def __sub__(self, other) -> "_Vec3":
        return _Vec3((self[0] - other[0], self[1] - other[1],
                      self[2] - other[2]))

    def __mul__(self, scalar: float) -> "_Vec3":
        scalar = float(scalar)
        return _Vec3((self[0] * scalar, self[1] * scalar,
                      self[2] * scalar))

    __rmul__ = __mul__

    def __truediv__(self, scalar: float) -> "_Vec3":
        scalar = float(scalar)
        return _Vec3((self[0] / scalar, self[1] / scalar,
                      self[2] / scalar))

    def __neg__(self) -> "_Vec3":
        return _Vec3((-self[0], -self[1], -self[2]))


class _Mat4(array):
    """Row-major float32 4x4 matrix with tuple indexing and multiplication."""

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
        return _Mat4(
            sum(self[row, k] * other[k, column] for k in range(4))
            for row in range(4)
            for column in range(4)
        )


def _vec3(values) -> _Vec3:
    return values if isinstance(values, _Vec3) else _Vec3(values)


def _dot(first: _Vec3, second: _Vec3) -> float:
    return (first[0] * second[0] + first[1] * second[1]
            + first[2] * second[2])


def _cross(first: _Vec3, second: _Vec3) -> _Vec3:
    return _Vec3((
        first[1] * second[2] - first[2] * second[1],
        first[2] * second[0] - first[0] * second[2],
        first[0] * second[1] - first[1] * second[0],
    ))


def _norm(vector: _Vec3) -> float:
    return math.sqrt(_dot(vector, vector))


def _pack_mesh(positions: list[tuple[float, float, float]],
               normals: list[tuple[float, float, float]],
               indices: list[int]) -> tuple[array, array]:
    vertices = array(
        "f",
        (component
         for position, normal in zip(positions, normals)
         for component in (*position, *normal)),
    )
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

# Width tracks segment length rather than player height: value = target radius / segment length.
_LIMB_LENGTH_RATIO: dict[str, float] = {
    "l_uarm": 0.28, "r_uarm": 0.28,
    "l_larm": 0.24, "r_larm": 0.24,
    "l_thigh": 0.30, "r_thigh": 0.30,
    "l_shin": 0.22, "r_shin": 0.22,
}

_SYMMETRIC_RADIUS_KEYS = {
    "l_uarm": "upper_arm", "r_uarm": "upper_arm",
    "l_larm": "lower_arm", "r_larm": "lower_arm",
    "l_hand": "hand", "r_hand": "hand",
    "l_thigh": "thigh", "r_thigh": "thigh",
    "l_knee": "knee", "r_knee": "knee",
    "l_shin": "shin", "r_shin": "shin",
    "l_foot": "foot", "r_foot": "foot",
    "l_elbow": "elbow", "r_elbow": "elbow",
    "l_fingers": "fingers", "r_fingers": "fingers",
    "l_shoulder_box": "shoulder_box", "r_shoulder_box": "shoulder_box",
    "l_shoulder_sphere": "shoulder_sphere", "r_shoulder_sphere": "shoulder_sphere",
}


def _dynamic_limb_size(name: str, seg_length: float, height_size: float) -> float:
    """50/50 blend of the height-derived radius and a segment-length one.

    Keeps the radius slider meaningful while letting limb width follow the
    live pose. Non-limbs and degenerate segments return height_size as-is.
    """
    ratio = _LIMB_LENGTH_RATIO.get(name)
    if ratio is None or seg_length <= 1e-3:
        return height_size
    return 0.5 * height_size + 0.5 * (seg_length * ratio)


# Parts are (name, primitive, first bone, second bone, player-height size factor).
_BODY_PARTS: list[tuple[str, str, int, int | None, float]] = [
    ("head",                "head_oval",        BONE_HEAD,        None,            0.075),
    ("neck",                "cylinder",         BONE_NECK,        BONE_HEAD,       0.040),
    ("trap",                "torso_prism",      BONE_CHEST,       BONE_NECK,       0.140),
    ("chest",               "torso_prism",      BONE_CHEST,       BONE_SPINE,      0.155),
    ("spine",               "torso_prism",      BONE_SPINE,       BONE_PELVIS,     0.135),
    ("butt",                "butt_sphere",      BONE_PELVIS,      None,            0.085),
    ("shoulders",           "aniso_cylinder_x", BONE_L_CLAV,      BONE_R_CLAV,     0.107),
    ("hips",                "aniso_cylinder_x", BONE_L_THIGH,     BONE_R_THIGH,    0.101),
    ("belly",               "front_prism",      BONE_SPINE,       BONE_PELVIS,     0.130),
    ("back_fill",           "back_prism",       BONE_NECK,        BONE_HEAD,       0.060),
    ("l_uarm",              "cylinder",         BONE_L_SHOULDER,  BONE_L_ELBOW,    0.052),
    ("l_larm",              "cylinder",         BONE_L_ELBOW,     BONE_L_HAND,     0.046),
    ("l_elbow",             "knee_sphere",      BONE_L_ELBOW,     None,            0.045),
    ("l_shoulder_box",      "shoulder_box",     BONE_L_CLAV,      BONE_L_SHOULDER, 0.060),
    ("l_shoulder_sphere",   "knee_sphere",      BONE_L_SHOULDER,  None,            0.045),
    ("l_hand",              "hand_box",         BONE_L_ELBOW,     BONE_L_HAND,     0.105),
    ("l_fingers",           "fingers_box",      BONE_L_ELBOW,     BONE_L_HAND,     0.040),
    ("r_uarm",              "cylinder",         BONE_R_SHOULDER,  BONE_R_ELBOW,    0.052),
    ("r_larm",              "cylinder",         BONE_R_ELBOW,     BONE_R_HAND,     0.046),
    ("r_elbow",             "knee_sphere",      BONE_R_ELBOW,     None,            0.045),
    ("r_shoulder_box",      "shoulder_box",     BONE_R_CLAV,      BONE_R_SHOULDER, 0.060),
    ("r_shoulder_sphere",   "knee_sphere",      BONE_R_SHOULDER,  None,            0.045),
    ("r_hand",              "hand_box",         BONE_R_ELBOW,     BONE_R_HAND,     0.105),
    ("r_fingers",           "fingers_box",      BONE_R_ELBOW,     BONE_R_HAND,     0.040),
    ("vest",                "vest_box",         BONE_PELVIS,      BONE_CHEST,      0.090),
    ("l_thigh",             "cylinder",         BONE_L_THIGH,     BONE_L_KNEE,     0.080),
    ("l_knee",              "knee_sphere",      BONE_L_KNEE,      None,            0.062),
    ("l_shin",              "cylinder",         BONE_L_KNEE,      BONE_L_FOOT,     0.065),
    ("l_foot",              "foot_box",         BONE_L_FOOT,      None,            0.110),
    ("r_thigh",             "cylinder",         BONE_R_THIGH,     BONE_R_KNEE,     0.080),
    ("r_knee",              "knee_sphere",      BONE_R_KNEE,      None,            0.062),
    ("r_shin",              "cylinder",         BONE_R_KNEE,      BONE_R_FOOT,     0.065),
    ("r_foot",              "foot_box",         BONE_R_FOOT,      None,            0.110),
]

_QUALITY = {
    "low": {"sphere_lat": 6, "sphere_lon": 8, "cyl_sides": 6, "prism_sides": 6},
    "medium": {"sphere_lat": 10, "sphere_lon": 12, "cyl_sides": 10, "prism_sides": 8},
    "high": {"sphere_lat": 14, "sphere_lon": 16, "cyl_sides": 14, "prism_sides": 10},
}

# Tri-count slider snaps to one of seven buckets so players sharing a bucket share one VAO/VBO/IBO. Defaults: 200..16000, 2000 default.
_TRI_BUCKETS: list[tuple[int, dict]] = [
    ( 200, {"sphere_lat": 4, "sphere_lon": 6, "cyl_sides": 4, "prism_sides": 4}),
    ( 500, {"sphere_lat": 6, "sphere_lon": 8, "cyl_sides": 6, "prism_sides": 6}),
    ( 1000, {"sphere_lat": 8, "sphere_lon": 10, "cyl_sides": 8, "prism_sides": 7}),
    ( 2000, {"sphere_lat": 10, "sphere_lon": 14, "cyl_sides": 10, "prism_sides": 8}),
    ( 4000, {"sphere_lat": 14, "sphere_lon": 18, "cyl_sides": 14, "prism_sides": 10}),
    ( 8000, {"sphere_lat": 18, "sphere_lon": 24, "cyl_sides": 18, "prism_sides": 12}),
    ( 16000, {"sphere_lat": 24, "sphere_lon": 32, "cyl_sides": 24, "prism_sides": 16}),
]


_REQUIRED_BONES = (BONE_HEAD, BONE_PELVIS)


# Primitive generators return (interleaved_vertices, indices) with [px, py, pz, nx, ny, nz] per vertex and analytic normals for the rim-light shader.
def _make_sphere(lat: int, lon: int) -> tuple[array, array]:
    """Unit sphere at origin, radius 1 (normal == position per vertex)."""
    verts = []
    norms = []
    # +1 so the poles get their own ring at phi=0 and phi=pi.
    for i in range(lat + 1):
        phi = math.pi * i / lat # 0 .. pi
        sp = math.sin(phi)
        cp = math.cos(phi)
        for j in range(lon):
            theta = 2.0 * math.pi * j / lon
            ct = math.cos(theta)
            st = math.sin(theta)
            x = sp * ct
            y = cp
            z = sp * st
            verts.append((x, y, z))
            norms.append((x, y, z))

    inds: list[int] = []
    for i in range(lat):
        for j in range(lon):
            a = i * lon + j
            b = i * lon + (j + 1) % lon
            c = (i + 1) * lon + j
            d = (i + 1) * lon + (j + 1) % lon
            # Two tris per quad. Winding: CCW from outside.
            inds.extend([a, b, c])
            inds.extend([b, d, c])

    return _pack_mesh(verts, norms, inds)


def _make_cylinder(sides: int) -> tuple[array, array]:
    """Unit cylinder along Y axis, height 1 (y=-0.5 to y=0.5), radius 1.

    Side and cap vertices are DUPLICATED so each gets the correct
    normal (radial outward for side, +/-Y for caps).
    """
    pos: list[tuple[float, float, float]] = []
    norm: list[tuple[float, float, float]] = []
    inds: list[int] = []

    # --- side: 2 verts per side index (bot, top), each with radial normal
    for j in range(sides):
        theta = 2.0 * math.pi * j / sides
        ct = math.cos(theta)
        st = math.sin(theta)
        pos.append((ct, -0.5, st))
        norm.append((ct, 0.0, st))
        pos.append((ct, 0.5, st))
        norm.append((ct, 0.0, st))

    for j in range(sides):
        a = j * 2
        b = j * 2 + 1
        c = ((j + 1) % sides) * 2
        d = ((j + 1) % sides) * 2 + 1
        # CCW from outside: (a, c, b) + (b, c, d)
        inds.extend([a, c, b])
        inds.extend([b, c, d])

    # --- bottom cap: center vert + ring with -Y normal
    bot_c = len(pos)
    pos.append((0.0, -0.5, 0.0))
    norm.append((0.0, -1.0, 0.0))
    bot_ring0 = len(pos)
    for j in range(sides):
        theta = 2.0 * math.pi * j / sides
        pos.append((math.cos(theta), -0.5, math.sin(theta)))
        norm.append((0.0, -1.0, 0.0))
    for j in range(sides):
        a = bot_ring0 + j
        b = bot_ring0 + (j + 1) % sides
        # center, b, a: CW from above = CCW from below.
        inds.extend([bot_c, b, a])

    # --- top cap: center vert + ring with +Y normal
    top_c = len(pos)
    pos.append((0.0, 0.5, 0.0))
    norm.append((0.0, 1.0, 0.0))
    top_ring0 = len(pos)
    for j in range(sides):
        theta = 2.0 * math.pi * j / sides
        pos.append((math.cos(theta), 0.5, math.sin(theta)))
        norm.append((0.0, 1.0, 0.0))
    for j in range(sides):
        a = top_ring0 + j
        b = top_ring0 + (j + 1) % sides
        # CCW from above: center, a, b.
        inds.extend([top_c, a, b])

    return _pack_mesh(pos, norm, inds)


def _make_box() -> tuple[array, array]:
    """Unit box (-0.5..0.5)^3. Per-face normals (flat shading)."""
    # 6 faces x 4 verts/face = 24 verts; 6 faces x 2 tris.
    faces = [
        # (normal, four corners CCW from outside)
        ((0, 0, -1), [(-0.5, -0.5, -0.5), ( 0.5, -0.5, -0.5),
                       ( 0.5, 0.5, -0.5), (-0.5, 0.5, -0.5)]),
        ((0, 0, 1), [(-0.5, -0.5, 0.5), (-0.5, 0.5, 0.5),
                       ( 0.5, 0.5, 0.5), ( 0.5, -0.5, 0.5)]),
        ((-1, 0, 0), [(-0.5, -0.5, -0.5), (-0.5, 0.5, -0.5),
                       (-0.5, 0.5, 0.5), (-0.5, -0.5, 0.5)]),
        ((1, 0, 0), [( 0.5, -0.5, -0.5), ( 0.5, -0.5, 0.5),
                       ( 0.5, 0.5, 0.5), ( 0.5, 0.5, -0.5)]),
        ((0, -1, 0), [(-0.5, -0.5, -0.5), (-0.5, -0.5, 0.5),
                       ( 0.5, -0.5, 0.5), ( 0.5, -0.5, -0.5)]),
        ((0, 1, 0), [(-0.5, 0.5, -0.5), ( 0.5, 0.5, -0.5),
                       ( 0.5, 0.5, 0.5), (-0.5, 0.5, 0.5)]),
    ]
    pos: list[tuple[float, float, float]] = []
    norm: list[tuple[float, float, float]] = []
    inds: list[int] = []
    for (nx, ny, nz), corners in faces:
        base = len(pos)
        for c in corners:
            pos.append(c)
            norm.append((nx, ny, nz))
        inds.extend([base, base + 1, base + 2])
        inds.extend([base, base + 2, base + 3])

    return _pack_mesh(pos, norm, inds)


# Matrix helpers use row-major float32 storage; GL_TRUE transposes it on upload.
def _mat_identity() -> _Mat4:
    return _Mat4((
        1.0, 0.0, 0.0, 0.0,
        0.0, 1.0, 0.0, 0.0,
        0.0, 0.0, 1.0, 0.0,
        0.0, 0.0, 0.0, 1.0,
    ))


def _mat_translation(tx: float, ty: float, tz: float) -> _Mat4:
    return _Mat4((
        1.0, 0.0, 0.0, tx,
        0.0, 1.0, 0.0, ty,
        0.0, 0.0, 1.0, tz,
        0.0, 0.0, 0.0, 1.0,
    ))


def _mat_scale(sx: float, sy: float, sz: float) -> _Mat4:
    return _Mat4((
        sx, 0.0, 0.0, 0.0,
        0.0, sy, 0.0, 0.0,
        0.0, 0.0, sz, 0.0,
        0.0, 0.0, 0.0, 1.0,
    ))


def _mat_align_y_to(direction) -> _Mat4:
    """Rotation sending local +Y to direction.

    +X/+Z are filled via a stable reference (flips world +Z -> +X when
    the bone is near-parallel to +Z) so the cross product never
    degenerates.
    """
    direction = _vec3(direction)
    norm = _norm(direction)
    if norm < 1e-6:
        return _mat_identity()
    up = direction / norm
    ref = _Vec3((0.0, 0.0, 1.0))
    if abs(_dot(up, ref)) > 0.999:
        ref = _Vec3((1.0, 0.0, 0.0))
    right = _cross(ref, up)
    right /= _norm(right)
    fwd = _cross(up, right)

    return _Mat4((
        right[0], up[0], fwd[0], 0.0,
        right[1], up[1], fwd[1], 0.0,
        right[2], up[2], fwd[2], 0.0,
        0.0, 0.0, 0.0, 1.0,
    ))


def _mat_oriented_basis(right, up, fwd) -> _Mat4:
    """Rotation from an orthonormal basis: local +X/+Y/+Z -> right/up/fwd.
    Inputs must already be orthonormal. Used for parts aligned to the
    player's own frame (head, feet, butt) rather than a single bone."""
    return _Mat4((
        right[0], up[0], fwd[0], 0.0,
        right[1], up[1], fwd[1], 0.0,
        right[2], up[2], fwd[2], 0.0,
        0.0, 0.0, 0.0, 1.0,
    ))


def _mat_rotation_around_axis(axis,
                              angle_rad: float) -> _Mat4:
    """Build a 4x4 rotation matrix around an arbitrary unit axis.

    Rodrigues' formula. Axis must be unit-length; callers pass in
    one of the player basis vectors (which are unit by
    construction).
    """
    x, y, z = float(axis[0]), float(axis[1]), float(axis[2])
    c = math.cos(angle_rad)
    s = math.sin(angle_rad)
    omc = 1.0 - c
    return _Mat4((
        c + x*x*omc, x*y*omc - z*s, x*z*omc + y*s, 0.0,
        y*x*omc + z*s, c + y*y*omc, y*z*omc - x*s, 0.0,
        z*x*omc - y*s, z*y*omc + x*s, c + z*z*omc, 0.0,
        0.0, 0.0, 0.0, 1.0,
    ))


def _mat_align_torso(bone_dir, player_right) -> _Mat4:
    """Align local +Y to bone_dir while keeping +X near player_right.

    +Z becomes the player's forward, so
    an anisotropic X>>Z scale shapes the torso into a slab, not a barrel.
    """
    bone_dir = _vec3(bone_dir)
    player_right = _vec3(player_right)
    bone_length = _norm(bone_dir)
    if bone_length < 1e-6:
        return _mat_identity()
    bone_axis = bone_dir / bone_length
    # Gram-Schmidt re-projects player_right perpendicular to bone_axis so the basis stays orthogonal even if the spine is not perpendicular to the shoulders.
    projected_right = player_right - bone_axis * _dot(player_right, bone_axis)
    projected_right_norm = _norm(projected_right)
    if projected_right_norm < 1e-3:
        return _mat_align_y_to(bone_dir) # degenerate; safe fallback
    projected_right = projected_right / projected_right_norm
    forward_axis = _cross(projected_right, bone_axis) # local +Z = right x up = forward
    forward_norm = _norm(forward_axis)
    if forward_norm < 1e-3:
        return _mat_align_y_to(bone_dir)
    forward_axis = forward_axis / forward_norm
    return _mat_oriented_basis(projected_right, bone_axis, forward_axis)


def _basis_from_points(left_point, right_point, head_point, pelvis_point
                       ) -> tuple[_Vec3, _Vec3, _Vec3] | None:
    """Build (right, up, fwd) from a left/right pair plus a
    head/pelvis pair. Shared core used by both the upper- and lower-
    body basis helpers."""
    right = _vec3(right_point) - _vec3(left_point)
    rn = _norm(right)
    if rn < 1e-3:
        return None
    right = right / rn

    up = _vec3(head_point) - _vec3(pelvis_point)
    un = _norm(up)
    if un < 1e-3:
        return None
    up = up / un

    fwd = _cross(up, right)
    fn = _norm(fwd)
    if fn < 1e-3:
        return None
    fwd = fwd / fn

    # Re-orthogonalise right against (fwd, up) so the basis is exact.
    right = _cross(fwd, up)
    rn = _norm(right)
    if rn < 1e-3:
        return None
    right = right / rn
    return right, up, fwd


# GLSL shaders.
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
        """Forget object names without deleting them from a replacement context."""
        self._gl_ready = False
        self._program = 0
        self._uloc_view_proj = -1
        self._uloc_model = -1
        self._uloc_color = -1
        self._uloc_rim = -1
        self._meshes_by_quality.clear()
        self._context_token = context_token

    def _sync_context(self) -> None:
        """Forget object names when GLFW has replaced the GL context."""
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

        # Resolve quality once; players in the same bucket share one mesh set.
        cfg0 = self._queue[0][2]
        target_tris = int(getattr(cfg0, "tri_count", 0) or 0)
        if target_tris > 0:
            cap, knobs = next(
                ((cap, values) for cap, values in _TRI_BUCKETS if target_tris <= cap),
                _TRI_BUCKETS[-1],
            )
            quality_key = f"tris_{cap}"
        else:
            legacy_quality = str(getattr(cfg0, "quality", "medium")).lower()
            if legacy_quality in _QUALITY:
                quality_key = legacy_quality
                knobs = _QUALITY[legacy_quality]
            else:
                cap, knobs = next(
                    ((cap, values) for cap, values in _TRI_BUCKETS if 2000 <= cap),
                    _TRI_BUCKETS[-1],
                )
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
                gl.glBufferData(
                    gl.GL_ARRAY_BUFFER, len(vertices) * vertices.itemsize,
                    np.frombuffer(vertices, dtype=np.float32), gl.GL_STATIC_DRAW,
                )

                ibo = int(gl.glGenBuffers(1))
                gl.glBindBuffer(gl.GL_ELEMENT_ARRAY_BUFFER, ibo)
                gl.glBufferData(
                    gl.GL_ELEMENT_ARRAY_BUFFER, len(indices) * indices.itemsize,
                    np.frombuffer(indices, dtype=np.uint32), gl.GL_STATIC_DRAW,
                )

                stride = 6 * 4
                gl.glEnableVertexAttribArray(0)
                gl.glVertexAttribPointer(
                    0, 3, gl.GL_FLOAT, gl.GL_FALSE, stride, c_void_p(0),
                )
                gl.glEnableVertexAttribArray(1)
                gl.glVertexAttribPointer(
                    1, 3, gl.GL_FLOAT, gl.GL_FALSE, stride, c_void_p(3 * 4),
                )
                meshes[name] = (vao, len(indices), vbo, ibo)

            # Unbind the VAO before its captured IBO binding.
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
        # Depth buffer CLEARED per-player so parts self-occlude without needing player-order depth sorting.
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
                    raise ValueError(
                        f"expected a 4x4 view matrix, got {len(view_values)} values"
                    )
                view_buffer = array("f", view_values)
                # Source matrices are row-major, so GL transposes them on upload.
                gl.glUniformMatrix4fv(
                    self._uloc_view_proj, 1, gl.GL_TRUE,
                    np.frombuffer(view_buffer, dtype=np.float32),
                )
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

    def _draw_player(self, player, cfg,
                     meshes: dict[str, tuple[int, int, int, int]]) -> None:
        bones = player.bones
        if not bones:
            return
        if not all(b in bones for b in _REQUIRED_BONES):
            return

        head = _vec3(bones[BONE_HEAD])
        pel = _vec3(bones[BONE_PELVIS])
        foot_pos = None
        if BONE_L_FOOT in bones:
            foot_pos = _vec3(bones[BONE_L_FOOT])
        elif BONE_R_FOOT in bones:
            foot_pos = _vec3(bones[BONE_R_FOOT])

        if foot_pos is not None:
            height = _norm(head - foot_pos)
        else:
            height = _norm(head - pel) * 2.0
        if height < 5.0 or height > 500.0:
            return # Bogus pose; skip this player.

        col = cfg.ct_color if player.team == TEAM_CT else cfg.t_color
        opacity = float(getattr(cfg, "opacity", 0.7))
        opacity = max(0.0, min(1.0, opacity))
        color = array("f", (float(col[0]), float(col[1]),
                            float(col[2]), opacity))
        rim = float(getattr(cfg, "rim_strength", 1.0))
        rim = max(0.0, min(4.0, rim))

        # Per-player depth clear: parts self-occlude, world geometry never wins (through-walls look for free).
        gl.glClear(gl.GL_DEPTH_BUFFER_BIT)

        gl.glUniform4fv(
            self._uloc_color, 1, np.frombuffer(color, dtype=np.float32),
        )
        gl.glUniform1f(self._uloc_rim, rim)

        radii_cfg = getattr(cfg, "radii", None)
        if radii_cfg is not None and not isinstance(radii_cfg, dict):
            # Config wraps dicts in Section objects; pull the raw dict.
            radii_cfg = getattr(radii_cfg, "_data", None) or {}

        # Upper and lower bases let the torso twist independently of the legs.
        upper_bones = (BONE_L_CLAV, BONE_R_CLAV, BONE_HEAD, BONE_PELVIS)
        basis = (_basis_from_points(
            bones[BONE_L_CLAV], bones[BONE_R_CLAV],
            bones[BONE_HEAD], bones[BONE_PELVIS],
        ) if all(bone in bones for bone in upper_bones) else None)
        lower_bones = (BONE_L_THIGH, BONE_R_THIGH, BONE_HEAD, BONE_PELVIS)
        basis_legs = (_basis_from_points(
            bones[BONE_L_THIGH], bones[BONE_R_THIGH],
            bones[BONE_HEAD], bones[BONE_PELVIS],
        ) if all(bone in bones for bone in lower_bones) else None) or basis

        aspects_cfg = getattr(cfg, "aspects", None)

        def _aspect_value(key: str, default: float, lo: float, hi: float) -> float:
            value = default
            if aspects_cfg is not None:
                try:
                    value = float(getattr(aspects_cfg, key, default))
                except (TypeError, ValueError):
                    value = default
            return max(lo, min(hi, value))

        HEAD_Y_ASPECT = _aspect_value("head_height", 1.45, 0.50, 3.00)
        TORSO_Z_ASPECT = _aspect_value("torso_depth", 0.70, 0.20, 1.50)
        FOOT_W_ASPECT = _aspect_value("foot_width", 0.55, 0.10, 1.50)
        FOOT_H_ASPECT = _aspect_value("foot_height", 0.50, 0.10, 1.50)
        FOOT_FWD_OFF = _aspect_value("foot_forward", 0.30, 0.00, 1.00)
        FOOT_DOWN_OFF = _aspect_value("foot_down", 0.50, 0.00, 1.50)
        HAND_W_ASPECT = _aspect_value("hand_width", 0.50, 0.10, 1.50)
        HAND_T_ASPECT = _aspect_value("hand_thickness", 0.40, 0.10, 1.50)
        HAND_PAST_WRIST = _aspect_value("hand_extend", 0.50, 0.00, 1.50)
        BUTT_Y_ASPECT = _aspect_value("butt_height", 0.70, 0.20, 1.50)
        BUTT_Z_ASPECT = _aspect_value("butt_depth", 0.95, 0.20, 1.50)
        BUTT_BACK_OFF = _aspect_value("butt_back", 0.40, 0.00, 1.50)
        HEAD_FWD_OFF = _aspect_value("head_forward", 0.00, 0.00, 1.50)
        CHEST_LEAN = _aspect_value("chest_lean", 0.077, 0.00, 1.00)
        FOOT_TOE_OUT_DEG = _aspect_value("foot_toe_out", 15.0, 0.00, 60.0)
        SHOULDER_Z_ASPECT = _aspect_value("shoulder_depth", 0.55, 0.10, 1.50)
        HIP_Z_ASPECT = _aspect_value("hip_depth", 0.85, 0.10, 1.50)
        # head_pitch tilts the head ellipsoid around basis.right to pin the chin; trap_back pushes the trap's neck end onto the upper-back muscle mass.
        HEAD_PITCH_DEG = _aspect_value("head_pitch", 0.0, -60.0, 60.0)
        TRAP_BACK = _aspect_value("trap_back", 0.40, 0.00, 1.00)
        # trap_pitch moves the top forward(+)/back(-), foot_pitch moves the toe down(+)/up(-), and finger_* positions the finger-box centre relative to the wrist.
        TRAP_PITCH_DEG = _aspect_value("trap_pitch", 22.0, -60.0, 60.0)
        FOOT_PITCH_DEG = _aspect_value("foot_pitch", 22.0, -60.0, 60.0)
        FINGER_FWD_OFF = _aspect_value("finger_forward", 0.55, -1.00, 2.00)
        FINGER_DOWN_OFF = _aspect_value("finger_down", 0.45, -1.00, 2.00)
        # head_up moves the ellipsoid centre along basis.up, vest_lerp selects a PELVIS(0)..CHEST(1) anchor, and shoulder_extend reaches past the shoulder bone for the deltoid box.
        HEAD_UP_OFF = _aspect_value("head_up", 0.45, -1.00, 2.00)
        VEST_LERP = _aspect_value("vest_lerp", 0.50, 0.00, 1.50)
        SHOULDER_EXTEND = _aspect_value("shoulder_extend", 0.30, -0.50, 1.00)
        UPPER_BACK_DEPTH = _aspect_value("upper_back_depth", 1.40, 0.50, 3.00)
        UPPER_BACK_FWD = _aspect_value("upper_back_forward", 0.0, -1.5, 1.5)
        UPPER_BACK_UP_OFF = _aspect_value("upper_back_up", -0.05, -1.5, 1.5)
        SHOULDER_OFFSET_X = _aspect_value("shoulder_offset_x", 0.0, -2.0, 2.0)
        SHOULDER_OFFSET_Y = _aspect_value("shoulder_offset_y", 0.0, -2.0, 2.0)
        SHOULDER_OFFSET_Z = _aspect_value("shoulder_offset_z", 0.0, -2.0, 2.0)
        VEST_W = _aspect_value("vest_width", 2.5, 0.5, 4.0)
        VEST_H = _aspect_value("vest_height", 0.6, 0.1, 2.0)
        VEST_D = _aspect_value("vest_depth", 0.7, 0.1, 2.0)
        HEAD_W_ASPECT = _aspect_value("head_width", 1.0, 0.5, 2.0)
        HEAD_D_ASPECT = _aspect_value("head_depth", 1.0, 0.5, 2.0)
        HEAD_YAW_DEG = _aspect_value("head_yaw", 0.0, -60.0, 60.0)
        TRAP_W_ASPECT = _aspect_value("trap_width", 1.0, 0.5, 2.0)
        TRAP_H_ASPECT = _aspect_value("trap_height", 1.0, 0.5, 2.0)
        TRAP_YAW_DEG = _aspect_value("trap_yaw", 0.0, -60.0, 60.0)

        hand_factor = 0.105
        if radii_cfg:
            hand_radius_override = radii_cfg.get("hand")
            if hand_radius_override is not None:
                try:
                    hand_factor = float(hand_radius_override)
                except (TypeError, ValueError):
                    pass
        hand_base_size = height * hand_factor

        for name, ptype, bone_a, bone_b, default_factor in _BODY_PARTS:
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

            if ptype == "head_oval":
                if bone_a not in bones:
                    continue
                pos = _vec3(bones[bone_a])
                if basis is not None:
                    right_w, up_w, fwd_w = basis
                    # head_pitch pins the chin at local (0, -0.5, 0) by shifting the centre by 0.5 * head_h * (new_up - up_w).
                    if HEAD_PITCH_DEG != 0.0:
                        ang = math.radians(HEAD_PITCH_DEG)
                        ca = math.cos(ang)
                        sa = math.sin(ang)
                        new_up = up_w * ca + fwd_w * sa
                        new_fwd = -up_w * sa + fwd_w * ca
                        head_h_half = base_size * HEAD_Y_ASPECT * 0.5
                        pos = pos + (new_up - up_w) * head_h_half
                        up_w, fwd_w = new_up, new_fwd
                    if HEAD_FWD_OFF > 0.0:
                        pos = pos + fwd_w * (base_size * HEAD_FWD_OFF)
                    # 3x lifts the ellipsoid from the neck-base bone onto the skull at the default slider value.
                    if HEAD_UP_OFF != 0.0:
                        pos = pos + up_w * (base_size * HEAD_UP_OFF * 3.0)
                    rot = _mat_oriented_basis(right_w, up_w, fwd_w)
                    # head_yaw twists around basis.up, stacked on top of head_pitch.
                    if HEAD_YAW_DEG != 0.0:
                        ang = math.radians(HEAD_YAW_DEG)
                        R_yaw = _mat_rotation_around_axis(up_w, ang)
                        rot = R_yaw @ rot
                else:
                    rot = _mat_identity()
                # Default 1.0/1.45/1.0 (width/height/depth) gives a tall-Y oval.
                scl = _mat_scale(base_size * HEAD_W_ASPECT,
                                 base_size * HEAD_Y_ASPECT,
                                 base_size * HEAD_D_ASPECT)
                model = _mat_translation(*pos) @ rot @ scl
                entry = meshes.get("sphere")

            elif ptype == "knee_sphere":
                if bone_a not in bones:
                    continue
                pos = _vec3(bones[bone_a])
                # Per-axis offsets apply only to outer-shoulder spheres; X is mirrored so positive shoulder_offset_x pushes both sides outward while knees and elbows stay bone-anchored.
                if (basis is not None
                        and name in ("l_shoulder_sphere",
                                     "r_shoulder_sphere")
                        and (SHOULDER_OFFSET_X != 0.0
                             or SHOULDER_OFFSET_Y != 0.0
                             or SHOULDER_OFFSET_Z != 0.0)):
                    sign_x = -1.0 if name == "l_shoulder_sphere" else 1.0
                    pos = (pos
                           + basis[0] * (base_size * SHOULDER_OFFSET_X * sign_x)
                           + basis[1] * (base_size * SHOULDER_OFFSET_Y)
                           + basis[2] * (base_size * SHOULDER_OFFSET_Z))
                scl = _mat_scale(base_size, base_size, base_size)
                model = _mat_translation(*pos) @ scl
                entry = meshes.get("sphere")

            elif ptype == "butt_sphere":
                if bone_a not in bones:
                    continue
                pos = _vec3(bones[bone_a])
                # Lower-body basis so glutes follow run direction, not upper-torso aim.
                if basis_legs is not None:
                    right, up, fwd = basis_legs
                    pos = pos - fwd * (base_size * BUTT_BACK_OFF)
                    rot = _mat_oriented_basis(right, up, fwd)
                else:
                    rot = _mat_identity()
                scl = _mat_scale(base_size,
                                 base_size * BUTT_Y_ASPECT,
                                 base_size * BUTT_Z_ASPECT)
                model = _mat_translation(*pos) @ rot @ scl
                entry = meshes.get("sphere")

            elif ptype == "foot_box":
                if bone_a not in bones:
                    continue
                pos = _vec3(bones[bone_a])
                length = base_size
                width = base_size * FOOT_W_ASPECT
                tall = base_size * FOOT_H_ASPECT
                # Lower-body basis so boots track the run direction.
                if basis_legs is not None:
                    right, up, fwd = basis_legs
                    # Toe-out around basis_legs.up: left foot toes left, right toes right (Charlie Chaplin).
                    if FOOT_TOE_OUT_DEG != 0.0:
                        sign = 1.0 if name == "l_foot" else -1.0
                        ang = math.radians(FOOT_TOE_OUT_DEG) * sign
                        ca = math.cos(ang)
                        sa = math.sin(ang)
                        r_new = right * ca + fwd * sa
                        f_new = -right * sa + fwd * ca
                        right, fwd = r_new, f_new
                    # Pitch around basis_legs.right (toe down +).
                    if FOOT_PITCH_DEG != 0.0:
                        ang = math.radians(FOOT_PITCH_DEG)
                        ca = math.cos(ang)
                        sa = math.sin(ang)
                        u_new = up * ca + fwd * sa
                        f_new = -up * sa + fwd * ca
                        up, fwd = u_new, f_new
                    # Forward shift -> toe ahead of ankle. Down shift -> ankle sits at boot's top.
                    pos = (pos
                           + fwd * (length * FOOT_FWD_OFF)
                           - up * (tall * FOOT_DOWN_OFF))
                    rot = _mat_oriented_basis(right, up, fwd)
                else:
                    rot = _mat_identity()
                scl = _mat_scale(width, tall, length)
                model = _mat_translation(*pos) @ rot @ scl
                entry = meshes.get("box")

            elif ptype == "cylinder":
                if bone_a not in bones or bone_b not in bones:
                    continue
                a = _vec3(bones[bone_a])
                b = _vec3(bones[bone_b])
                direction = b - a
                length = _norm(direction)
                if length < 1e-3:
                    continue
                mid = (a + b) * 0.5
                rot = _mat_align_y_to(direction)
                limb_size = _dynamic_limb_size(name, length, base_size)
                scl = _mat_scale(limb_size, length, limb_size)
                model = _mat_translation(*mid) @ rot @ scl
                entry = meshes.get("cylinder")

            elif ptype == "torso_prism":
                if bone_a not in bones or bone_b not in bones:
                    continue
                a = _vec3(bones[bone_a])
                b = _vec3(bones[bone_b])
                # The chest leans its top into the neck line, while the trap pushes its neck end into the upper-back muscle mass instead of folding toward the throat.
                if (name == "chest" and basis is not None
                        and CHEST_LEAN > 0.0):
                    a = a + basis[2] * (base_size * CHEST_LEAN)
                if (name == "trap" and basis is not None
                        and TRAP_BACK > 0.0):
                    b = b - basis[2] * (base_size * TRAP_BACK)
                direction = b - a
                length = _norm(direction)
                if length < 1e-3:
                    continue
                mid = (a + b) * 0.5
                if basis is not None:
                    rot = _mat_align_torso(direction, basis[0])
                else:
                    rot = _mat_align_y_to(direction)
                # The trap-only transform pitches around basis.right, yaws around basis.up, then shifts the midpoint along basis.fwd and basis.up.
                if (name == "trap" and basis is not None
                        and TRAP_PITCH_DEG != 0.0):
                    R_pitch = _mat_rotation_around_axis(
                        basis[0], math.radians(TRAP_PITCH_DEG))
                    rot = R_pitch @ rot
                if (name == "trap" and basis is not None
                        and TRAP_YAW_DEG != 0.0):
                    R_yaw = _mat_rotation_around_axis(
                        basis[1], math.radians(TRAP_YAW_DEG))
                    rot = R_yaw @ rot
                if (name == "trap" and basis is not None
                        and (UPPER_BACK_FWD != 0.0
                             or UPPER_BACK_UP_OFF != 0.0)):
                    mid = (mid
                           + basis[2] * (length * UPPER_BACK_FWD)
                           + basis[1] * (length * UPPER_BACK_UP_OFF))
                # X(width) >> Z(depth) so the side view reads as a slab. Trap gets extra width/height/depth knobs.
                width = base_size
                depth = base_size * TORSO_Z_ASPECT
                eff_length = length
                if name == "trap":
                    width = width * TRAP_W_ASPECT
                    depth = depth * UPPER_BACK_DEPTH
                    eff_length = length * TRAP_H_ASPECT
                scl = _mat_scale(width, eff_length, depth)
                model = _mat_translation(*mid) @ rot @ scl
                entry = meshes.get("prism")

            elif ptype == "hand_box":
                # With bone_a=elbow and bone_b=wrist, the hand extends past the wrist along the lower-arm direction and inherits its orientation without the player basis.
                if bone_a not in bones or bone_b not in bones:
                    continue
                a = _vec3(bones[bone_a])
                b = _vec3(bones[bone_b])
                direction = b - a
                length_arm = _norm(direction)
                if length_arm < 1e-3:
                    continue
                hand_len = base_size
                hand_w = base_size * HAND_W_ASPECT
                hand_t = base_size * HAND_T_ASPECT
                # Centre past the wrist by half its length so the proximal edge lines up with the wrist bone.
                dir_n = direction / length_arm
                centre = b + dir_n * (hand_len * HAND_PAST_WRIST)
                rot = _mat_align_y_to(direction)
                scl = _mat_scale(hand_w, hand_len, hand_t)
                model = _mat_translation(*centre) @ rot @ scl
                entry = meshes.get("box")

            elif ptype == "fingers_box":
                # Fingers hang below the wrist as if curled around a grip, using -basis.up projected perpendicular to the arm as a gravity proxy.
                if bone_a not in bones or bone_b not in bones:
                    continue
                a = _vec3(bones[bone_a])
                b = _vec3(bones[bone_b])
                direction = b - a
                length_arm = _norm(direction)
                if length_arm < 1e-3:
                    continue
                dir_n = direction / length_arm
                # Finger dimensions map to the along-arm, downward-curl, and lateral axes.
                fingers_w = base_size * 1.6
                fingers_l = base_size
                fingers_t = base_size
                placed = False
                if basis is not None:
                    down = -basis[1]
                    # Project gravity onto the plane perpendicular to the arm, falling back to the along-arm orientation when a near-vertical arm collapses the projection.
                    d_proj = down - _dot(down, dir_n) * dir_n
                    nd = _norm(d_proj)
                    if nd > 1e-3:
                        y_ax = d_proj / nd
                        x_ax = dir_n
                        z_ax = _cross(x_ax, y_ax)
                        # Small forward tilt: rotate (x_ax, y_ax) around z_ax by ~15 deg so the box leans into the grip curl.
                        tilt = math.radians(15.0)
                        ct = math.cos(tilt)
                        st = math.sin(tilt)
                        new_x = x_ax * ct + y_ax * st
                        new_y = -x_ax * st + y_ax * ct
                        x_ax, y_ax = new_x, new_y
                        rot = _mat_oriented_basis(x_ax, y_ax, z_ax)
                        # Position from finger_forward/finger_down sliders: forward in hand-lengths, down in fingers-lengths.
                        centre = (b
                                  + x_ax * (hand_base_size *
                                            FINGER_FWD_OFF)
                                  + y_ax * (fingers_l *
                                            FINGER_DOWN_OFF))
                        scl = _mat_scale(fingers_w, fingers_l,
                                         fingers_t)
                        model = _mat_translation(*centre) @ rot @ scl
                        entry = meshes.get("box")
                        placed = True
                if not placed:
                    rot = _mat_align_y_to(direction)
                    centre = (b
                              + dir_n * (hand_base_size *
                                         FINGER_FWD_OFF))
                    scl = _mat_scale(fingers_w, fingers_l, fingers_t)
                    model = _mat_translation(*centre) @ rot @ scl
                    entry = meshes.get("box")

            elif ptype == "aniso_cylinder_x":
                # Shoulders and hips use a horizontal-bar cylinder with separate Y thickness and Z depth radii so the side cap does not resemble a wide disk.
                if bone_a not in bones or bone_b not in bones:
                    continue
                a = _vec3(bones[bone_a])
                b = _vec3(bones[bone_b])
                direction = b - a
                length = _norm(direction)
                if length < 1e-3:
                    continue
                mid = (a + b) * 0.5
                # The player-up X hint maps local +Y to bone direction, +X to vertical thickness, and +Z to front-to-back depth via X cross Y.
                if basis is not None:
                    rot = _mat_align_torso(direction, basis[1])
                else:
                    rot = _mat_align_y_to(direction)
                depth_aspect = (HIP_Z_ASPECT
                                if name == "hips"
                                else SHOULDER_Z_ASPECT)
                # Mesh axes map X to vertical, Y to bar span, and Z to depth.
                scl = _mat_scale(base_size, length, base_size * depth_aspect)
                model = _mat_translation(*mid) @ rot @ scl
                entry = meshes.get("cylinder")

            elif ptype == "front_prism":
                # Belly slab between SPINE and PELVIS, offset forward of the spine prism.
                if bone_a not in bones or bone_b not in bones:
                    continue
                a = _vec3(bones[bone_a])
                b = _vec3(bones[bone_b])
                direction = b - a
                length = _norm(direction)
                if length < 1e-3:
                    continue
                mid = (a + b) * 0.5
                if basis is not None:
                    rot = _mat_align_torso(direction, basis[0])
                    mid = mid + basis[2] * (base_size * 0.65)
                else:
                    rot = _mat_align_y_to(direction)
                # Width follows the belly-radius slider while a thin depth keeps the silhouette close to the spine prism instead of resembling a sphere.
                width = base_size
                depth = base_size * TORSO_Z_ASPECT * 0.6
                scl = _mat_scale(width, length, depth)
                model = _mat_translation(*mid) @ rot @ scl
                entry = meshes.get("prism")

            elif ptype == "back_prism":
                # Back-of-neck fill: prism NECK->HEAD offset backward, closing the trap-top / neck / head-bottom gap.
                if bone_a not in bones or bone_b not in bones:
                    continue
                a = _vec3(bones[bone_a])
                b = _vec3(bones[bone_b])
                direction = b - a
                length = _norm(direction)
                if length < 1e-3:
                    continue
                mid = (a + b) * 0.5
                if basis is not None:
                    rot = _mat_align_torso(direction, basis[0])
                    mid = mid - basis[2] * (base_size * 1.0)
                else:
                    rot = _mat_align_y_to(direction)
                width = base_size
                depth = base_size * 0.8
                scl = _mat_scale(width, length, depth)
                model = _mat_translation(*mid) @ rot @ scl
                entry = meshes.get("prism")

            elif ptype == "shoulder_box":
                # The mirrored CLAV-to-SHOULDER deltoid slab maps mesh +Y to bone direction, +X to basis.up thickness, and +Z to depth; SHOULDER_EXTEND lengthens its outer end past the shoulder bone.
                if bone_a not in bones or bone_b not in bones:
                    continue
                a = _vec3(bones[bone_a])
                b = _vec3(bones[bone_b])
                direction = b - a
                length = _norm(direction)
                if length < 1e-3:
                    continue
                if SHOULDER_EXTEND != 0.0:
                    b = b + direction * SHOULDER_EXTEND
                    direction = b - a
                    length = _norm(direction)
                mid = (a + b) * 0.5
                if basis is not None:
                    rot = _mat_align_torso(direction, basis[1])
                else:
                    rot = _mat_align_y_to(direction)
                width = base_size * 1.4
                depth = base_size * 0.9
                scl = _mat_scale(width, length, depth)
                model = _mat_translation(*mid) @ rot @ scl
                entry = meshes.get("box")

            elif ptype == "vest_box":
                # Vest box, position LERPED PELVIS<->CHEST (vest_lerp), forward-offset to sit in front of the spine prism.
                if bone_a not in bones or bone_b not in bones:
                    continue
                pelvis = _vec3(bones[bone_a])
                spine = _vec3(bones[bone_b])
                t = float(VEST_LERP)
                base_pos = pelvis * (1.0 - t) + spine * t
                if basis is not None:
                    right_w, up_w, fwd_w = basis
                    centre = base_pos + fwd_w * (base_size * 0.3)
                    rot = _mat_oriented_basis(right_w, up_w, fwd_w)
                else:
                    centre = base_pos
                    rot = _mat_identity()
                # Wide-X / thin-Y / thin-Z slab; aspects decouple vest dimensions from torso width.
                width_x = base_size * VEST_W
                height_y = base_size * VEST_H
                depth_z = base_size * VEST_D
                scl = _mat_scale(width_x, height_y, depth_z)
                model = _mat_translation(*centre) @ rot @ scl
                entry = meshes.get("box")

            else:
                continue

            if model is None or entry is None:
                continue
            vao, idx_count, _, ibo = entry
            gl.glUniformMatrix4fv(self._uloc_model, 1, gl.GL_TRUE,
                                  np.frombuffer(model, dtype=np.float32))
            gl.glBindVertexArray(vao)
            # Belt-and-suspenders IBO rebind so a state wipe can never crash glDrawElements(..., None) on NULL.
            gl.glBindBuffer(gl.GL_ELEMENT_ARRAY_BUFFER, ibo)
            gl.glDrawElements(gl.GL_TRIANGLES, idx_count,
                              gl.GL_UNSIGNED_INT, None)

    def shutdown(self) -> None:
        # GL may already be gone if the overlay was torn down first.
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
                    for fn, target in (
                        (gl.glDeleteVertexArrays, vao),
                        (gl.glDeleteBuffers, vbo),
                        (gl.glDeleteBuffers, ibo),
                    ):
                        try:
                            fn(1, [target])
                        except Exception:
                            pass
        self._meshes_by_quality.clear()
        self._gl_ready = False
        self._context_token = _UNSET_CONTEXT


# Module-level functional API retained for main.py compatibility.

_renderer: Chams3DRenderer | None = None


def _get_renderer() -> Chams3DRenderer:
    global _renderer
    if _renderer is None:
        _renderer = Chams3DRenderer()
    return _renderer


def chams_begin_frame() -> None:
    """Reset the per-frame queue. Call BEFORE the wallhack draw loop."""
    _get_renderer().begin_frame()


def chams_end_frame() -> None:
    """Flush the queued GL draws. Call AFTER the wallhack draw loop
    and BEFORE imgui's draw_data flushes, so the chams body fill
    sits behind the ESP overlays."""
    _get_renderer().end_frame()


def chams_shutdown() -> None:
    """Free shader / VAOs / VBOs / IBOs."""
    global _renderer
    if _renderer is not None:
        _renderer.shutdown()
        _renderer = None


def chams_context_recreated() -> None:
    """Invalidate cached GL names after Overlay replaces its GLFW context."""
    if _renderer is not None:
        _renderer.invalidate_context()


def draw_chams(draw_list, player, view_matrix, screen_w: int, screen_h: int, cfg) -> None:
    """Queue a player for 3D chams render.

    draw_list, screen_w, and screen_h are kept for API compatibility but unused; the
    3D pass writes directly to the framebuffer in chams_end_frame."""
    _get_renderer().queue(player, view_matrix, cfg)


__all__ = [
    "draw_chams",
    "chams_begin_frame",
    "chams_end_frame",
    "chams_shutdown",
    "chams_context_recreated",
]
