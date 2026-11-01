"""
Exact-model chams: real CS2 agent meshes loaded from pak01_dir.vpk and skinned on the GPU.
Meshes upload once per model, the inverse bind uploads once per model switch, and the CPU packs one vectorised quaternion pass per frame.
Without the game's depth buffer "occluded" cannot mean behind walls, so the body is one shaded pass per player.
"""

from ctypes import c_void_p
from weakref import ref

import numpy as np
import OpenGL.GL as gl
import OpenGL.GL.shaders as gl_shaders

from cs2esp.assets.vpk_models import CT_DEFAULT, T_DEFAULT, AgentMesh, VERTEX_DTYPE, library
from cs2esp.visuals.math_utils import TEAM_CT

MAX_BONES = 128
# Later LOD bones may be stale and require sanitisation
_LOD_START_BONE = 28
_MAX_ROOT_DIST_SQ = 120.0 * 120.0
_MAX_DIVERGENCE_SQ = 60.0 * 60.0

_VERT_SHADER = """
#version 330 core
layout(location = 0) in vec3 in_pos;
layout(location = 1) in vec3 in_normal;
layout(location = 2) in uvec4 in_joints;
layout(location = 3) in vec4 in_weights;
uniform mat4 u_view_proj;
uniform vec4 u_bones[384];      // bone world 3x4 per bone
uniform vec4 u_inv_bind[384];   // model inverse-bind 3x4 per bone
out vec3 v_normal;
out vec3 v_world;
void main() {
    vec3 pos = vec3(0.0);
    vec3 nrm = vec3(0.0);
    float total = 0.0;
    for (int i = 0; i < 4; ++i) {
        float w = in_weights[i];
        if (w < 0.0001) continue;
        int base = int(min(in_joints[i], 127u)) * 3;
        vec4 w0 = u_bones[base];
        vec4 w1 = u_bones[base + 1];
        vec4 w2 = u_bones[base + 2];
        vec4 p4 = vec4(in_pos, 1.0);
        // Bind space -> local bone space, then to world.
        vec4 lp = vec4(dot(p4, u_inv_bind[base]),
                       dot(p4, u_inv_bind[base + 1]),
                       dot(p4, u_inv_bind[base + 2]), 1.0);
        pos += vec3(dot(lp, w0), dot(lp, w1), dot(lp, w2)) * w;
        nrm += vec3(dot(in_normal, w0.xyz), dot(in_normal, w1.xyz),
                    dot(in_normal, w2.xyz)) * w;
        total += w;
    }
    if (total > 0.0001) {
        pos /= total;
        nrm /= total;
    } else {
        pos = in_pos;
        nrm = in_normal;
    }
    v_normal = nrm;
    v_world = pos;
    gl_Position = u_view_proj * vec4(pos, 1.0);
}
"""

_FRAG_SHADER = """
#version 330 core
in vec3 v_normal;
in vec3 v_world;
out vec4 frag;
uniform vec4 u_color;
uniform vec3 u_cam;
uniform float u_rim;
void main() {
    vec3 n = normalize(v_normal);
    vec3 view = normalize(u_cam - v_world);
    float ndv = clamp(abs(dot(n, view)), 0.0, 1.0);
    float lambert = clamp(dot(n, normalize(vec3(0.3, 0.2, 1.0))) * 0.5 + 0.5, 0.0, 1.0);
    float rim = pow(1.0 - ndv, 2.0);
    float shade = mix(1.0, 0.55 + 0.45 * lambert + 0.35 * rim, u_rim);
    frag = vec4(u_color.rgb * clamp(shade, 0.0, 1.4), u_color.a);
}
"""


def bone_matrices(raw: np.ndarray, valid: int) -> np.ndarray:
	'''CTransform slab (N x 8 floats: pos, pad, quat xyzw) -> world 3x4.'''
	pos = raw[:valid, 0:3]
	quat = raw[:valid, 4:8].astype(np.float64)
	quat /= np.linalg.norm(quat, axis=1, keepdims=True)
	x, y, z, w = quat.T
	out = np.empty((valid, 3, 4), dtype=np.float32)
	out[:, 0, 0] = 1 - 2 * (y * y + z * z)
	out[:, 0, 1] = 2 * (x * y - w * z)
	out[:, 0, 2] = 2 * (x * z + w * y)
	out[:, 1, 0] = 2 * (x * y + w * z)
	out[:, 1, 1] = 1 - 2 * (x * x + z * z)
	out[:, 1, 2] = 2 * (y * z - w * x)
	out[:, 2, 0] = 2 * (x * z - w * y)
	out[:, 2, 1] = 2 * (y * z + w * x)
	out[:, 2, 2] = 1 - 2 * (x * x + y * y)
	out[:, :, 3] = pos
	return out


def valid_bone_count(raw: np.ndarray) -> int:
	'''Leading run of finite entries with a sane quaternion length.'''
	finite = np.isfinite(raw).all(axis=1)
	qlen = np.einsum("ij,ij->i", raw[:, 4:8], raw[:, 4:8])
	ok = finite & (qlen >= 0.25) & (qlen <= 4.0)
	bad = np.flatnonzero(~ok)
	return int(bad[0]) if bad.size else int(raw.shape[0])


def _bind_positions(inv_bind: np.ndarray) -> np.ndarray:
	rot = inv_bind[:, :, :3]
	trans = inv_bind[:, :, 3]
	return -np.einsum("bji,bj->bi", rot, trans)


_LOCAL_OFFSETS: dict[int, tuple] = {}


def _lod_tables(mesh: AgentMesh) -> tuple[np.ndarray, np.ndarray, np.ndarray]:
	'''Per-model (bone ids, parent ids, parent-local bind offsets), cached.'''
	key = id(mesh)
	cached = _LOCAL_OFFSETS.get(key)
	if cached is not None and cached[0]() is mesh:
		return cached[1:]
	count = mesh.bone_count
	ids = np.arange(_LOD_START_BONE, count)
	parents = mesh.parents[_LOD_START_BONE:count].astype(np.int64)
	keep = (parents >= 0) & (parents != ids) & (parents < count)
	ids, parents = ids[keep], parents[keep]
	bind_pos = _bind_positions(mesh.inv_bind)
	local = np.einsum("bij,bj->bi", mesh.inv_bind[parents, :, :3], bind_pos[ids] - bind_pos[parents]).astype(np.float32)
	if len(_LOCAL_OFFSETS) >= 128:
		_LOCAL_OFFSETS.pop(next(iter(_LOCAL_OFFSETS)))
	_LOCAL_OFFSETS[key] = (ref(mesh), ids, parents, local)
	return ids, parents, local


def sanitize_lod_bones(world: np.ndarray, mesh: AgentMesh) -> np.ndarray:
	'''Replace stale LOD-streamed bones with their parent + bind reconstruction.'''
	limit = min(world.shape[0], mesh.bone_count)
	if limit <= _LOD_START_BONE:
		return world
	ids, parents, local = _lod_tables(mesh)
	usable = (ids < limit) & (parents < limit)
	ids, parents, local = ids[usable], parents[usable], local[usable]
	if ids.size == 0:
		return world

	root = world[0, :, 3]
	pos = world[ids, :, 3]
	recon = world[parents, :, 3] + np.einsum("bij,bj->bi", world[parents, :, :3], local)
	bad = np.einsum("bi,bi->b", pos - root, pos - root) > _MAX_ROOT_DIST_SQ
	bad |= np.einsum("bi,bi->b", pos - recon, pos - recon) > _MAX_DIVERGENCE_SQ
	if float(root @ root) > 500.0 * 500.0:
		bad |= np.einsum("bi,bi->b", pos, pos) < 50.0 * 50.0
	if not bad.any():
		return world

	world = world.copy()
	repaired = np.zeros(limit, dtype=bool)
	# Parent-first order propagates repaired transforms through descendants in one pass
	for row in range(int(np.flatnonzero(bad)[0]), ids.size):
		bone_index, parent = int(ids[row]), int(parents[row])
		if not (bad[row] or repaired[parent]):
			continue
		parent_world = world[parent]
		world[bone_index] = parent_world
		world[bone_index, :, 3] = parent_world[:, 3] + parent_world[:, :3] @ local[row]
		repaired[bone_index] = True
	return world


def world_matrices(slab: np.ndarray, mesh: AgentMesh) -> np.ndarray | None:
	'''(128, 3, 4) bone world matrices ready for the vertex shader.'''
	valid = valid_bone_count(slab)
	if valid < min(28, mesh.bone_count):
		return None
	world = sanitize_lod_bones(bone_matrices(slab, valid), mesh)
	return world


def skin_palette(world: np.ndarray, mesh: AgentMesh) -> np.ndarray | None:
	'''skin = bone_world x inv_bind, packed as 128 x 3 vec4 rows.'''
	count = mesh.bone_count
	if count <= 0 or count > MAX_BONES or world.shape[0] < count:
		return None
	w = world[:count]
	inv = mesh.inv_bind[:count]
	rot = np.einsum("bij,bjk->bik", w[:, :, :3], inv[:, :, :3])
	trans = np.einsum("bij,bj->bi", w[:, :, :3], inv[:, :, 3]) + w[:, :, 3]
	palette = np.zeros((MAX_BONES, 3, 4), dtype=np.float32)
	palette[:, 0, 0] = palette[:, 1, 1] = palette[:, 2, 2] = 1.0
	palette[:count, :, :3] = rot
	palette[:count, :, 3] = trans
	return palette


def _quat_mats(raw: np.ndarray) -> np.ndarray:
	'''(P, N, 8) CTransform slabs -> (P, N, 3, 4) world matrices, one pass.'''
	from cs2esp.visuals import native_math

	if native_math.available():
		return native_math.quat_world(raw)
	quat = raw[..., 4:8]
	norm_sq = np.einsum("...i,...i->...", quat, quat)
	# 2/|q|^2 folds the normalisation into the standard formula
	s = np.divide(2.0, norm_sq, out=np.zeros_like(norm_sq), where=norm_sq > 0)
	x, y, z, w = quat[..., 0], quat[..., 1], quat[..., 2], quat[..., 3]
	xs, ys, zs = x * s, y * s, z * s
	wx, wy, wz = w * xs, w * ys, w * zs
	xx, xy, xz = x * xs, x * ys, x * zs
	yy, yz, zz = y * ys, y * zs, z * zs
	out = np.empty(raw.shape[:-1] + (3, 4), dtype=np.float32)
	out[..., 0, 0] = 1.0 - (yy + zz)
	out[..., 0, 1] = xy - wz
	out[..., 0, 2] = xz + wy
	out[..., 1, 0] = xy + wz
	out[..., 1, 1] = 1.0 - (xx + zz)
	out[..., 1, 2] = yz - wx
	out[..., 2, 0] = xz - wy
	out[..., 2, 1] = yz + wx
	out[..., 2, 2] = 1.0 - (xx + yy)
	out[..., :, 3] = raw[..., 0:3]
	return out


def _valid_counts(raw: np.ndarray) -> np.ndarray:
	'''Per-slab leading run of sane bones, for a (P, N, 8) stack.'''
	finite = np.isfinite(raw).all(axis=2)
	qlen = np.einsum("pij,pij->pi", raw[..., 4:8], raw[..., 4:8])
	ok = finite & (qlen >= 0.25) & (qlen <= 4.0)
	bad = ~ok
	first_bad = np.where(bad.any(axis=1), bad.argmax(axis=1), raw.shape[1])
	return first_bad


def _lod_needs_repair(world: np.ndarray, mesh: AgentMesh, limit: np.ndarray) -> np.ndarray:
	'''Vectorised version of sanitize_lod_bones' detection, (P,) bool.'''
	ids, parents, local = _lod_tables(mesh)
	if ids.size == 0:
		return np.zeros(world.shape[0], dtype=bool)
	origin = world[..., 3]
	root = origin[:, 0]
	pos = origin[:, ids]
	recon = origin[:, parents] + np.einsum("pbij,bj->pbi", world[:, parents, :, :3], local)
	d_root = pos - root[:, None, :]
	d_recon = pos - recon
	bad = np.einsum("pbi,pbi->pb", d_root, d_root) > _MAX_ROOT_DIST_SQ
	bad |= np.einsum("pbi,pbi->pb", d_recon, d_recon) > _MAX_DIVERGENCE_SQ
	far = np.einsum("pi,pi->p", root, root) > 500.0 * 500.0
	bad |= far[:, None] & (np.einsum("pbi,pbi->pb", pos, pos) < 50.0 * 50.0)
	usable = (ids[None, :] < limit[:, None]) & (parents[None, :] < limit[:, None])
	return (bad & usable).any(axis=1)


def skin_palettes_batch(slabs: list, meshes: list) -> list:
	'''skin_palette for many players at once; players sharing a model are skinned together in one numpy pass.'''
	results: list = [None] * len(slabs)
	if not slabs:
		return results
	stack = np.stack([np.asarray(slab, dtype=np.float32) for slab in slabs])
	valid = _valid_counts(stack)
	world_all = _quat_mats(stack)
	groups: dict[int, list[int]] = {}
	for index, mesh in enumerate(meshes):
		if mesh is None:
			continue
		count = mesh.bone_count
		if count <= 0 or count > MAX_BONES or valid[index] < min(28, count):
			continue
		groups.setdefault(id(mesh), []).append(index)
	for members in groups.values():
		mesh = meshes[members[0]]
		count = mesh.bone_count
		rows = np.asarray(members)
		world = world_all[rows]
		limits = np.minimum(valid[rows], count)
		repair = _lod_needs_repair(world[:, :count], mesh, limits) if count > _LOD_START_BONE else np.zeros(rows.size, dtype=bool)
		complete = valid[rows] >= count
		for member_index, index in enumerate(members):
			if repair[member_index] or not complete[member_index]:
				valid_count = int(valid[index])
				fixed = sanitize_lod_bones(world[member_index, :valid_count].copy(), mesh)
				results[index] = skin_palette(fixed, mesh)
		fast = [member_index for member_index in range(rows.size) if not (repair[member_index] or not complete[member_index])]
		if not fast:
			continue
		bone_world = world[fast, :count]
		inverse_bind = mesh.inv_bind[:count]
		rot = np.einsum("pbij,bjk->pbik", bone_world[..., :3], inverse_bind[:, :, :3])
		trans = np.einsum("pbij,bj->pbi", bone_world[..., :3], inverse_bind[:, :, 3]) + bone_world[..., 3]
		palette = np.zeros((len(fast), MAX_BONES, 3, 4), dtype=np.float32)
		palette[:, :, 0, 0] = palette[:, :, 1, 1] = palette[:, :, 2, 2] = 1.0
		palette[:, :count, :, :3] = rot
		palette[:, :count, :, 3] = trans
		for palette_index, member_index in enumerate(fast):
			results[members[member_index]] = palette[palette_index]
	return results


def world_matrices_batch(slabs: list, meshes: list) -> list:
	'''(128, 3, 4) bone world rows for many players in one pass; always runs _quat_mats, then repairs stale LOD bones per player.'''
	results: list = [None] * len(slabs)
	if not slabs:
		return results
	stack = np.stack([np.asarray(slab, dtype=np.float32) for slab in slabs])
	valid = _valid_counts(stack)
	world_all = _quat_mats(stack)
	groups: dict[int, list[int]] = {}
	for index, mesh in enumerate(meshes):
		if mesh is None:
			continue
		count = mesh.bone_count
		if count <= 0 or count > MAX_BONES or valid[index] < min(28, count):
			continue
		groups.setdefault(id(mesh), []).append(index)
	for members in groups.values():
		mesh = meshes[members[0]]
		count = mesh.bone_count
		rows = np.asarray(members)
		world = world_all[rows]
		limits = np.minimum(valid[rows], count)
		repair = _lod_needs_repair(world[:, :count], mesh, limits) if count > _LOD_START_BONE else np.zeros(rows.size, dtype=bool)
		for member_index, index in enumerate(members):
			bone_world = world[member_index]
			if repair[member_index] or valid[index] < count:
				bone_world = sanitize_lod_bones(bone_world[: int(valid[index])].copy(), mesh)
			padded = np.zeros((MAX_BONES, 3, 4), dtype=np.float32)
			padded[: bone_world.shape[0]] = bone_world
			results[index] = padded
	return results


class _GpuMesh:
	"""VAO/VBO/IBO and index count for one agent mesh."""
	__slots__ = ("vao", "vbo", "ibo", "index_count")

	def __init__(self, mesh: AgentMesh) -> None:
		self.vao = int(gl.glGenVertexArrays(1))
		gl.glBindVertexArray(self.vao)
		self.vbo = int(gl.glGenBuffers(1))
		gl.glBindBuffer(gl.GL_ARRAY_BUFFER, self.vbo)
		gl.glBufferData(gl.GL_ARRAY_BUFFER, mesh.vertices.nbytes, mesh.vertices.view(np.uint8), gl.GL_STATIC_DRAW)
		self.ibo = int(gl.glGenBuffers(1))
		gl.glBindBuffer(gl.GL_ELEMENT_ARRAY_BUFFER, self.ibo)
		gl.glBufferData(gl.GL_ELEMENT_ARRAY_BUFFER, mesh.indices.nbytes, mesh.indices, gl.GL_STATIC_DRAW)
		stride = VERTEX_DTYPE.itemsize
		offsets = VERTEX_DTYPE.fields
		gl.glEnableVertexAttribArray(0)
		gl.glVertexAttribPointer(0, 3, gl.GL_FLOAT, gl.GL_FALSE, stride, c_void_p(offsets["pos"][1]))
		gl.glEnableVertexAttribArray(1)
		gl.glVertexAttribPointer(1, 3, gl.GL_FLOAT, gl.GL_FALSE, stride, c_void_p(offsets["normal"][1]))
		gl.glEnableVertexAttribArray(2)
		gl.glVertexAttribIPointer(2, 4, gl.GL_UNSIGNED_SHORT, stride, c_void_p(offsets["joints"][1]))
		gl.glEnableVertexAttribArray(3)
		gl.glVertexAttribPointer(3, 4, gl.GL_FLOAT, gl.GL_FALSE, stride, c_void_p(offsets["weights"][1]))
		gl.glBindVertexArray(0)
		gl.glBindBuffer(gl.GL_ARRAY_BUFFER, 0)
		gl.glBindBuffer(gl.GL_ELEMENT_ARRAY_BUFFER, 0)
		self.index_count = int(mesh.indices.shape[0])

	def delete(self) -> None:
		for delete_function, name in ((gl.glDeleteVertexArrays, self.vao), (gl.glDeleteBuffers, self.vbo), (gl.glDeleteBuffers, self.ibo)):
			try:
				delete_function(1, [name])
			except Exception:
				pass


class VpkChamsRenderer:
	"""Per-context program, GPU mesh and palette cache for the exact-model pass."""
	def __init__(self) -> None:
		self._program = 0
		self._loc: dict[str, int] = {}
		self._gpu: dict[str, _GpuMesh] = {}
		self._queue: list[tuple] = []
		self._context = None
		self._started = False
		self._palettes: dict[tuple[int, str], tuple[bytes, np.ndarray]] = {}
		self._bound_model: str = ""
		self._inv_rows: dict[str, np.ndarray] = {}
		self._raw = None
		self._raw_tried = False

	def invalidate_context(self) -> None:
		'''Forget GL names after the overlay rebuilt its context.'''
		self._program = 0
		self._loc.clear()
		self._gpu.clear()
		self._raw = None
		self._raw_tried = False
		self._bound_model = ""

	def begin_frame(self) -> None:
		self._queue.clear()
		if len(self._palettes) > 64:
			self._palettes.clear()
		if not self._started:
			library().start()
			self._started = True

	def queue(self, player, view_matrix, cfg, camera_pos=None) -> None:
		bones = getattr(player, "bone_slab", None)
		if bones is None:
			return
		self._queue.append((player, view_matrix, cfg, camera_pos))

	def _ensure_program(self) -> bool:
		if self._program:
			return True
		try:
			vertex_shader = gl_shaders.compileShader(_VERT_SHADER, gl.GL_VERTEX_SHADER)
			fragment_shader = gl_shaders.compileShader(_FRAG_SHADER, gl.GL_FRAGMENT_SHADER)
			self._program = gl_shaders.compileProgram(vertex_shader, fragment_shader)
		except Exception as error:
			print(f"[vpk-chams] shader compile failed: {error!r}")
			self._program = 0
			return False
		for name in ("u_view_proj", "u_bones", "u_inv_bind", "u_color", "u_cam", "u_rim"):
			self._loc[name] = gl.glGetUniformLocation(self._program, name)
		self._bound_model = ""
		if not self._raw_tried:
			self._raw_tried = True
			# Resolve raw GL pointers only after the active context compiles its program
			from cs2esp.visuals import gl_raw

			self._raw = gl_raw.create()
		return True

	def _apply_draw_state(self) -> None:
		'''Load the shader and the pass baseline the overlay leaves between frames.'''
		gl.glUseProgram(self._program)
		gl.glEnable(gl.GL_BLEND)
		gl.glBlendFunc(gl.GL_SRC_ALPHA, gl.GL_ONE_MINUS_SRC_ALPHA)
		gl.glEnable(gl.GL_DEPTH_TEST)
		gl.glDepthMask(gl.GL_TRUE)
		gl.glDepthFunc(gl.GL_LESS)
		gl.glDisable(gl.GL_CULL_FACE)
		gl.glClear(gl.GL_DEPTH_BUFFER_BIT)

	def _inverse_rows(self, mesh):
		rows = self._inv_rows.get(mesh.model_path)
		if rows is None:
			rows = np.zeros((MAX_BONES, 3, 4), dtype=np.float32)
			count = min(MAX_BONES, mesh.bone_count)
			rows[:count] = mesh.inv_bind[:count]
			self._inv_rows[mesh.model_path] = rows
		return rows

	def _bind_model(self, mesh) -> None:
		'''Upload the model's inverse bind once; GPU does the skin multiply.'''
		if mesh.model_path == self._bound_model:
			return
		rows = self._inverse_rows(mesh)
		if self._raw is not None:
			self._raw.upload_matrices(self._loc["u_inv_bind"], rows)
		else:
			gl.glUniform4fv(self._loc["u_inv_bind"], MAX_BONES * 3, rows)
		self._bound_model = mesh.model_path

	def _submit_native(self, jobs):
		packets = []
		bound_model = self._bound_model
		for player, view, cfg, camera, mesh, gpu, palette in jobs:
			if palette is None:
				continue
			inverse = None
			if mesh.model_path != bound_model:
				inverse = self._inverse_rows(mesh)
				bound_model = mesh.model_path
			colour = cfg.ct_color if player.team == TEAM_CT else cfg.t_color
			alpha = float(colour[3]) * float(getattr(cfg, "opacity", 0.85))
			rgba = (float(colour[0]), float(colour[1]), float(colour[2]), max(0.0, min(1.0, alpha)))
			camera = camera or (0.0, 0.0, 0.0)
			camera = (float(camera[0]), float(camera[1]), float(camera[2]))
			rim = float(getattr(cfg, "rim_strength", 1.0))
			packets.append((view, palette, inverse, gpu.vao, gpu.index_count, rgba, camera, rim))
		if not packets:
			return None
		if self._raw.draw_batch(self._program, self._loc, packets):
			self._bound_model = bound_model
			self._queue.clear()
			return True
		return None

	def _mesh_for(self, player) -> tuple[AgentMesh, _GpuMesh] | None:
		lib = library()
		path = getattr(player, "model_path", "") or ""
		mesh = lib.get(path) if path else None
		if mesh is None:
			mesh = lib.get(CT_DEFAULT if player.team == TEAM_CT else T_DEFAULT)
		if mesh is None:
			return None
		gpu = self._gpu.get(mesh.model_path)
		if gpu is None:
			gpu = _GpuMesh(mesh)
			self._gpu[mesh.model_path] = gpu
		return mesh, gpu

	def end_frame(self) -> bool:
		'''Draw queued players; False when nothing could be drawn yet.'''
		if not self._queue:
			return True
		if not self._ensure_program():
			self._queue.clear()
			return False

		# Avoid synchronous state queries; restore the known imgui-frame baseline after this pass
		native_batch = bool(self._raw is not None and getattr(self._raw, "supports_batch", False))
		if not native_batch:
			self._apply_draw_state()

		# Skin cache misses together after resolving their meshes and palettes
		jobs = []
		misses = []
		for player, view_matrix, cfg, camera_pos in self._queue:
			resolved = self._mesh_for(player)
			if resolved is None:
				continue
			mesh, gpu = resolved
			key = (player.pawn_addr, mesh.model_path)
			raw = player.bone_slab.tobytes()
			cached = self._palettes.get(key)
			palette = cached[1] if cached is not None and cached[0] == raw else None
			job = [player, view_matrix, cfg, camera_pos, mesh, gpu, palette]
			if palette is None:
				misses.append((job, key, raw))
			jobs.append(job)
		if misses:
			fresh = world_matrices_batch([job[0].bone_slab for job, _, _ in misses], [job[4] for job, _, _ in misses])
			for (job, key, raw), palette in zip(misses, fresh):
				job[6] = palette
				if palette is not None:
					self._palettes[key] = (raw, palette)

		if native_batch:
			submitted = self._submit_native(jobs)
			if submitted is not None:
				return submitted
			self._apply_draw_state()

		drew_any = False
		active_view = None
		active_colour = active_camera = active_rim = active_vao = None
		for player, view_matrix, cfg, camera_pos, mesh, gpu, palette in jobs:
			if palette is None:
				continue
			raw = self._raw
			if view_matrix is not active_view:
				vm = np.asarray(view_matrix, dtype=np.float32).reshape(4, 4)
				if raw is not None:
					raw.upload_matrix4(self._loc["u_view_proj"], vm)
				else:
					gl.glUniformMatrix4fv(self._loc["u_view_proj"], 1, gl.GL_TRUE, vm)
				active_view = view_matrix
			self._bind_model(mesh)
			if raw is not None:
				raw.upload_matrices(self._loc["u_bones"], palette)
			else:
				gl.glUniform4fv(self._loc["u_bones"], MAX_BONES * 3, palette)
			colour = cfg.ct_color if player.team == TEAM_CT else cfg.t_color
			alpha = float(colour[3]) * float(getattr(cfg, "opacity", 0.85))
			rim = float(getattr(cfg, "rim_strength", 1.0))
			cam = camera_pos or (0.0, 0.0, 0.0)
			colour_value = (float(colour[0]), float(colour[1]), float(colour[2]), max(0.0, min(1.0, alpha)))
			camera_value = (float(cam[0]), float(cam[1]), float(cam[2]))
			# Uniforms persist across VAO changes; only reuse them within the current pass
			if colour_value != active_colour:
				(raw.uniform4f if raw is not None else gl.glUniform4f)(self._loc["u_color"], *colour_value)
				active_colour = colour_value
			if camera_value != active_camera:
				(raw.uniform3f if raw is not None else gl.glUniform3f)(self._loc["u_cam"], *camera_value)
				active_camera = camera_value
			if rim != active_rim:
				(raw.uniform1f if raw is not None else gl.glUniform1f)(self._loc["u_rim"], rim)
				active_rim = rim
			if gpu.vao != active_vao:
				gl.glBindVertexArray(gpu.vao)
				active_vao = gpu.vao
			gl.glDrawElements(gl.GL_TRIANGLES, gpu.index_count, gl.GL_UNSIGNED_INT, None)
			drew_any = True

		gl.glBindVertexArray(0)
		gl.glUseProgram(0)
		gl.glDisable(gl.GL_DEPTH_TEST)
		gl.glDepthMask(gl.GL_TRUE)
		self._queue.clear()
		return drew_any

	def shutdown(self) -> None:
		for gpu in self._gpu.values():
			gpu.delete()
		self._gpu.clear()
		if self._program:
			try:
				gl.glDeleteProgram(self._program)
			except Exception:
				pass
		self._program = 0


_renderer: VpkChamsRenderer | None = None


def renderer() -> VpkChamsRenderer:
	global _renderer
	if _renderer is None:
		_renderer = VpkChamsRenderer()
	return _renderer
