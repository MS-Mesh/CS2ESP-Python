"""
World-to-screen projection, angle math, and team constants.
"""


def world_to_screen(pos: tuple[float, float, float], view_matrix: list[list[float]], screen_width: int, screen_height: int) -> tuple[float, float] | None:
	'''Project a 3D world position to 2D screen coords. Returns None if behind camera.'''
	world_x, world_y, world_z = pos
	clip_w = view_matrix[3][0] * world_x + view_matrix[3][1] * world_y + view_matrix[3][2] * world_z + view_matrix[3][3]
	if clip_w < 0.001:
		return None
	clip_x = view_matrix[0][0] * world_x + view_matrix[0][1] * world_y + view_matrix[0][2] * world_z + view_matrix[0][3]
	clip_y = view_matrix[1][0] * world_x + view_matrix[1][1] * world_y + view_matrix[1][2] * world_z + view_matrix[1][3]
	inv_w = 1.0 / clip_w
	screen_x = (screen_width / 2.0) + (clip_x * inv_w) * (screen_width / 2.0)
	screen_y = (screen_height / 2.0) - (clip_y * inv_w) * (screen_height / 2.0)
	return (screen_x, screen_y)


def angle_diff_deg(a: float, b: float) -> float:
	'''Return the smallest signed difference in degrees, in ``[-180, 180)``.'''
	return ((a - b + 540.0) % 360.0) - 180.0


TEAM_CT = 3
