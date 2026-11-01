"""
Player ESP box geometry derived from live CS2 data.
"""

from cs2esp.visuals.math_utils import world_to_screen

Box2D = tuple[float, float, float, float]


def compute_bounds_box(player, view_matrix, w: int, h: int) -> Box2D | None:
	'''Screen AABB from the entity's collision hull.'''
	hull_min = getattr(player, "bbox_mins", None)
	hull_max = getattr(player, "bbox_maxs", None)
	if hull_min is None or hull_max is None:
		return None
	origin_x, origin_y, origin_z = player.position
	screen_xs: list[float] = []
	screen_ys: list[float] = []
	for corner_x in (hull_min[0], hull_max[0]):
		for corner_y in (hull_min[1], hull_max[1]):
			for corner_z in (hull_min[2], hull_max[2]):
				screen_position = world_to_screen((origin_x + corner_x, origin_y + corner_y, origin_z + corner_z), view_matrix, w, h)
				if screen_position is not None:
					screen_xs.append(screen_position[0])
					screen_ys.append(screen_position[1])
	if len(screen_xs) < 2:
		return None
	return (min(screen_xs), min(screen_ys), max(screen_xs), max(screen_ys))


def compute_bone_box(player, view_matrix, w: int, h: int, side_pad_frac: float = 0.09, crown_frac: float = 0.14, foot_frac: float = 0.16) -> Box2D | None:
	'''Padded screen AABB around all projected bones.'''
	screen_xs: list[float] = []
	screen_ys: list[float] = []
	for world_position in (getattr(player, "bones", None) or {}).values():
		screen_position = world_to_screen(world_position, view_matrix, w, h)
		if screen_position is not None:
			screen_xs.append(screen_position[0])
			screen_ys.append(screen_position[1])
	if len(screen_xs) < 4:
		return None
	min_x, max_x = min(screen_xs), max(screen_xs)
	min_y, max_y = min(screen_ys), max(screen_ys)
	height = max(2.0, max_y - min_y)
	side_padding = height * side_pad_frac
	return (min_x - side_padding, min_y - height * crown_frac, max_x + side_padding, max_y + height * foot_frac)
