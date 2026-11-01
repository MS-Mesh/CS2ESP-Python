"""
Corner-anchored mini-map with a local-player-centred world projection.
"""

import math

from cs2esp.visuals.theme import color_u32, with_alpha


def radar_size(radar_config, screen_h: int) -> float:
	'''Resolve the radar diameter in physical pixels for either sizing mode.'''
	fraction = float(getattr(radar_config, "size_fraction", 0.0) or 0.0)
	return max(80.0, screen_h * fraction if fraction > 0.0 else float(getattr(radar_config, "size", 200.0) or 200.0))


class Radar:
	"""Corner-anchored mini-map with a local-player-centred world projection."""

	def __init__(self, config):
		self.config = config

	def _u32(self, rgba) -> int:
		'''Pack an RGBA color with the configured radar opacity.'''
		red, green, blue, alpha = rgba
		return color_u32((red, green, blue, alpha * self.config.radar.opacity))

	def render(
		self,
		draw_list,
		local_pos,
		local_yaw_deg: float,
		enemies: list[tuple[tuple, int]],
		teammates: list[tuple[tuple, int]] | None = None,
		screen_w: int = 1920,
		screen_h: int = 1080,
	) -> None:
		'''Draw the map background, local arrow, and enemy/teammate dots.'''
		radar_config = self.config.radar
		if not radar_config.enabled:
			return

		size = radar_size(radar_config, screen_h)
		offset_x = float(radar_config.position[0])
		offset_y = float(radar_config.position[1])
		anchor = (getattr(radar_config, "anchor", "top_left") or "top_left").lower()
		if anchor == "top_right":
			top_left_x, top_left_y = screen_w - offset_x - size, offset_y
		elif anchor == "bottom_left":
			top_left_x, top_left_y = offset_x, screen_h - offset_y - size
		elif anchor == "bottom_right":
			top_left_x, top_left_y = screen_w - offset_x - size, screen_h - offset_y - size
		else:
			top_left_x, top_left_y = offset_x, offset_y
		centre_x = top_left_x + size / 2
		centre_y = top_left_y + size / 2
		radius = size / 2
		scale = radius / radar_config.range

		shape = (getattr(radar_config, "shape", "round") or "round").lower()
		is_round = shape == "round"

		background_color = self._u32(radar_config.background_color)
		border_color = self._u32(radar_config.border_color)

		if is_round:
			draw_list.add_circle_filled((centre_x, centre_y), radius, background_color, 64)
			draw_list.add_circle((centre_x, centre_y), radius, border_color, 64, 1.5)
			faint = self._u32(with_alpha(radar_config.border_color, 0.22))
			draw_list.add_circle((centre_x, centre_y), radius * 0.66, faint, 48, 1.0)
			draw_list.add_circle((centre_x, centre_y), radius * 0.33, faint, 48, 1.0)
		else:
			bottom_right_x = top_left_x + size
			bottom_right_y = top_left_y + size
			draw_list.add_rect_filled((top_left_x, top_left_y), (bottom_right_x, bottom_right_y), background_color, 6.0)
			draw_list.add_rect((top_left_x, top_left_y), (bottom_right_x, bottom_right_y), border_color, rounding=6.0, thickness=1.5, flags=0)

		cross_color = self._u32(with_alpha(radar_config.border_color, 0.30))
		draw_list.add_line((centre_x - radius, centre_y), (centre_x + radius, centre_y), cross_color, 1.0)
		draw_list.add_line((centre_x, centre_y - radius), (centre_x, centre_y + radius), cross_color, 1.0)

		arrow = max(5.0, size * 0.030)
		local_color = self._u32(radar_config.local_color)
		tip = (centre_x, centre_y - arrow * 1.2)
		left = (centre_x - arrow * 0.8, centre_y + arrow * 0.8)
		right = (centre_x + arrow * 0.8, centre_y + arrow * 0.8)
		draw_list.add_triangle_filled(tip, left, right, local_color)
		draw_list.add_triangle(tip, left, right, self._u32((0.0, 0.0, 0.0, 0.95)), 1.5)

		yaw_radians = math.radians(90.0 - local_yaw_deg)
		cos_yaw = math.cos(yaw_radians)
		sin_yaw = math.sin(yaw_radians)

		dot_radius = max(3.5, size * 0.018)
		edge = 2.0
		outline_color = self._u32((0.0, 0.0, 0.0, 0.9))
		max_radius = radius - edge

		def project_dot(world_pos, color):
			delta_x = world_pos[0] - local_pos[0]
			delta_y = world_pos[1] - local_pos[1]
			radar_x = delta_x * cos_yaw - delta_y * sin_yaw
			radar_y = delta_x * sin_yaw + delta_y * cos_yaw
			screen_x = centre_x + radar_x * scale
			screen_y = centre_y - radar_y * scale

			if is_round:
				from_centre_x = screen_x - centre_x
				from_centre_y = screen_y - centre_y
				distance = math.hypot(from_centre_x, from_centre_y)
				if distance > max_radius:
					clamp_ratio = max_radius / distance
					screen_x = centre_x + from_centre_x * clamp_ratio
					screen_y = centre_y + from_centre_y * clamp_ratio
			else:
				screen_x = max(top_left_x + edge, min(top_left_x + size - edge, screen_x))
				screen_y = max(top_left_y + edge, min(top_left_y + size - edge, screen_y))

			draw_list.add_circle_filled((screen_x, screen_y), dot_radius, color, 12)
			draw_list.add_circle((screen_x, screen_y), dot_radius, outline_color, 12, 1.0)

		enemy_color = self._u32(radar_config.enemy_color)
		for position, _team in enemies:
			if position == (0.0, 0.0, 0.0):
				continue
			project_dot(position, enemy_color)

		if radar_config.show_teammates and teammates:
			teammate_color = self._u32(with_alpha(radar_config.teammate_color, 0.65))
			for position, _team in teammates:
				if position == (0.0, 0.0, 0.0):
					continue
				project_dot(position, teammate_color)
