"""
Per-frame awareness feedback drawn directly onto the overlay.
"""

import math
from time import perf_counter

from cs2esp.visuals.math_utils import angle_diff_deg
from cs2esp.visuals.theme import color_u32


class Awareness:
	"""Pulse a red vignette when an enemy is aiming near the local player."""

	def __init__(self, config):
		self.config = config
		self._intensity = 0.0
		self._last_frame = perf_counter()

	def update(self, local_pos, local_yaw_deg: float, enemies) -> None:
		'''Update vignette intensity from visible enemy aim direction.'''
		awareness_config = self.config.awareness
		now = perf_counter()
		elapsed = max(now - self._last_frame, 0.001)
		self._last_frame = now

		if not awareness_config.enabled:
			self._intensity = 0.0
			return

		rear_only = bool(getattr(awareness_config, "rear_only", True))
		rear_fov = float(getattr(awareness_config, "rear_fov_deg", 100.0))
		half_rear_fov = rear_fov * 0.5
		cone_degrees = max(1.0, float(awareness_config.cone_degrees))

		target_intensity = 0.0
		for enemy in enemies:
			if enemy.distance > awareness_config.max_distance:
				continue

			dx_to_me = local_pos[0] - enemy.position[0]
			dy_to_me = local_pos[1] - enemy.position[1]
			angle_enemy_to_me = math.degrees(math.atan2(dy_to_me, dx_to_me))

			enemy_yaw = enemy.eye_angles[1]
			aim_off = abs(angle_diff_deg(angle_enemy_to_me, enemy_yaw))
			if aim_off >= cone_degrees:
				continue

			if rear_only:
				yaw_us_to_enemy = math.degrees(math.atan2(enemy.position[1] - local_pos[1], enemy.position[0] - local_pos[0]))
				fwd_diff = abs(angle_diff_deg(yaw_us_to_enemy, local_yaw_deg))
				if fwd_diff <= half_rear_fov:
					continue

			proximity = max(0.0, 1.0 - (enemy.distance / awareness_config.max_distance) ** 0.5)
			centeredness = (1.0 - aim_off / cone_degrees) ** 0.5
			strength = min(0.8, max(0.0, proximity * centeredness) * 1.2)
			if strength > target_intensity:
				target_intensity = strength

		if target_intensity > self._intensity:
			self._intensity = target_intensity
		else:
			self._intensity = max(0.0, self._intensity - elapsed * awareness_config.fade_speed)

	def render(self, draw_list, screen_w: int, screen_h: int) -> None:
		'''Draw the vignette edges at the current intensity.'''
		if self._intensity <= 0.005:
			return
		awareness_config = self.config.awareness
		red, green, blue, alpha = awareness_config.color
		alpha = alpha * self._intensity
		base = color_u32((red, green, blue, alpha))
		clear = color_u32((red, green, blue, 0.0))
		edge_thickness = awareness_config.edge_thickness

		draw_list.add_rect_filled_multi_color((0, 0), (screen_w, edge_thickness), base, base, clear, clear)
		draw_list.add_rect_filled_multi_color((0, screen_h - edge_thickness), (screen_w, screen_h), clear, clear, base, base)
		draw_list.add_rect_filled_multi_color((0, 0), (edge_thickness, screen_h), base, clear, clear, base)
		draw_list.add_rect_filled_multi_color((screen_w - edge_thickness, 0), (screen_w, screen_h), clear, base, base, clear)
