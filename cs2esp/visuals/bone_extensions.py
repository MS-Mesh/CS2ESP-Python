"""
Synthetic skeleton extension points shared by drawing and hit testing.
"""

from collections.abc import Iterable

from cs2esp.offsets import Netvars

Vec3 = tuple[float, float, float]


EXTENSION_SPECS: tuple[tuple[int, int, float, str], ...] = (
	(Netvars.BONE_L_ELBOW, Netvars.BONE_L_HAND, 5.0, "l_gun"),
	(Netvars.BONE_R_ELBOW, Netvars.BONE_R_HAND, 5.0, "r_gun"),
	(Netvars.BONE_L_KNEE, Netvars.BONE_L_FOOT, 3.0, "l_toe"),
	(Netvars.BONE_R_KNEE, Netvars.BONE_R_FOOT, 3.0, "r_toe"),
	(Netvars.BONE_NECK, Netvars.BONE_HEAD, 2.0, "crown"),
)


def iter_extensions(bones: dict[int, Vec3]) -> Iterable[tuple[Vec3, Vec3, str]]:
	'''Yield synthetic extension points for a single player.'''
	if not bones:
		return
	for parent_index, tip_index, distance, label in EXTENSION_SPECS:
		parent = bones.get(parent_index)
		tip = bones.get(tip_index)
		if parent is None or tip is None:
			continue
		delta_x = tip[0] - parent[0]
		delta_y = tip[1] - parent[1]
		delta_z = tip[2] - parent[2]
		length = (delta_x * delta_x + delta_y * delta_y + delta_z * delta_z) ** 0.5
		if length < 1e-6:
			extended = tip
		else:
			scale = distance / length
			extended = (tip[0] + delta_x * scale, tip[1] + delta_y * scale, tip[2] + delta_z * scale)
		yield tip, extended, label


def iter_targets(bones: dict[int, Vec3], interpolation_steps: int = 1) -> Iterable[Vec3]:
	'''Yield every aim-able world-space point for one player.'''
	if not bones:
		return
	for bone_index, position in bones.items():
		if bone_index == 0:
			continue
		yield position
	for _, extended_position, _ in iter_extensions(bones):
		yield extended_position

	steps = max(1, int(interpolation_steps or 1))
	if steps > 1:
		for start_index, end_index in Netvars.SKELETON_PAIRS:
			start = bones.get(start_index)
			end = bones.get(end_index)
			if start is None or end is None:
				continue
			for step in range(1, steps):
				ratio = step / steps
				yield (start[0] + (end[0] - start[0]) * ratio, start[1] + (end[1] - start[1]) * ratio, start[2] + (end[2] - start[2]) * ratio)


def iter_segments(bones: dict[int, Vec3]) -> Iterable[tuple[Vec3, Vec3]]:
	'''Yield every drawn skeleton line segment as a (start, end) pair.'''
	if not bones:
		return
	for start_index, end_index in Netvars.SKELETON_PAIRS:
		start = bones.get(start_index)
		end = bones.get(end_index)
		if start is None or end is None:
			continue
		yield start, end
	for tip_world, extended_world, _label in iter_extensions(bones):
		yield tip_world, extended_world
