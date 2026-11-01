"""
Player ESP primitives: boxes, labels, health, distance, snaplines, and crosshair.
"""

from imgui_bundle import imgui  # test seam: tests patch imgui.calc_text_size

from cs2esp.visuals.bbox import compute_bone_box, compute_bounds_box
from cs2esp.visuals.math_utils import TEAM_CT, world_to_screen
from cs2esp.visuals.theme import color_u32, health_color
from cs2esp.visuals.weapon_icons import FONT_ICONS, draw_font_icon, weapon_icon_key


_UNITS_PER_METER = 52.49


def draw_player_esp(draw_list, player, view_matrix, screen_w: int, screen_h: int, vis_cfg) -> None:
	'''Draw one player's overlay elements; skeleton is handled separately.'''

	feet_screen = world_to_screen(player.position, view_matrix, screen_w, screen_h)
	if feet_screen is None:
		return
	head_world = (player.position[0], player.position[1], player.position[2] + 75.0)
	head_screen = world_to_screen(head_world, view_matrix, screen_w, screen_h)
	if head_screen is None:
		return
	head_x, head_y = head_screen
	feet_x, feet_y = feet_screen

	box_config = vis_cfg.box
	box_mode = getattr(box_config, "box_mode", "bones")

	box_bounds = None
	if box_mode == "bones":
		if getattr(player, "bones", None):
			box_bounds = compute_bone_box(player, view_matrix, screen_w, screen_h)
		if box_bounds is None:
			box_bounds = compute_bounds_box(player, view_matrix, screen_w, screen_h)
	elif box_mode == "bounds":
		box_bounds = compute_bounds_box(player, view_matrix, screen_w, screen_h)
		if box_bounds is None and getattr(player, "bones", None):
			box_bounds = compute_bone_box(player, view_matrix, screen_w, screen_h)

	if box_bounds is not None:
		box_left, box_top, box_right, box_bottom = box_bounds
		head_x = (box_left + box_right) * 0.5
		head_y = box_top
	else:
		box_height = abs(feet_screen[1] - head_screen[1])
		box_width = max(box_height * 0.42, 4.0)
		box_left = head_x - box_width * 0.5
		box_right = head_x + box_width * 0.5
		box_top = head_y
		box_bottom = feet_y

	box_width = max(box_right - box_left, 1.0)
	panel_black = color_u32((0.0, 0.0, 0.0, 0.55))
	shadow_black = color_u32((0.0, 0.0, 0.0, 0.85))

	if box_config.enabled:
		box_rgba = (box_config.ct_color if player.team == TEAM_CT else box_config.t_color) if box_config.team_colored else box_config.color
		box_color = color_u32(box_rgba)
		if box_config.style == "filled":
			draw_list.add_rect_filled((box_left, box_top), (box_right, box_bottom), box_color)
		elif box_config.style == "corners":
			corner = box_width * 0.3
			line_thickness = box_config.thickness
			draw_list.add_line((box_left, box_top), (box_left + corner, box_top), box_color, line_thickness)
			draw_list.add_line((box_left, box_top), (box_left, box_top + corner), box_color, line_thickness)
			draw_list.add_line((box_right, box_top), (box_right - corner, box_top), box_color, line_thickness)
			draw_list.add_line((box_right, box_top), (box_right, box_top + corner), box_color, line_thickness)
			draw_list.add_line((box_left, box_bottom), (box_left + corner, box_bottom), box_color, line_thickness)
			draw_list.add_line((box_left, box_bottom), (box_left, box_bottom - corner), box_color, line_thickness)
			draw_list.add_line((box_right, box_bottom), (box_right - corner, box_bottom), box_color, line_thickness)
			draw_list.add_line((box_right, box_bottom), (box_right, box_bottom - corner), box_color, line_thickness)
		else:
			# imgui-bundle 1.92.801 reordered add_rect's tail parameters; keywords preserve the intended thickness and flags
			draw_list.add_rect((box_left, box_top), (box_right, box_bottom), box_color, rounding=0.0, thickness=box_config.thickness, flags=0)
			draw_list.add_rect((box_left - 1, box_top - 1), (box_right + 1, box_bottom + 1), panel_black, rounding=0.0, thickness=1.0, flags=0)

	health_config = vis_cfg.health
	health_clamped = max(0, min(player.health, 100))
	health_display_color = None
	if health_config.mode in ("bar", "percent", "both"):
		health_display_color = color_u32(health_color(player.health, health_config.high_color, health_config.mid_color, health_config.low_color))
	if health_config.mode in ("bar", "both"):
		bar_x = box_left - health_config.bar_width - health_config.bar_offset
		bar_top = box_top
		bar_bottom = box_bottom
		bar_height = bar_bottom - bar_top
		filled_height = bar_height * (health_clamped / 100.0)
		draw_list.add_rect_filled((bar_x, bar_top), (bar_x + health_config.bar_width, bar_bottom), panel_black)
		draw_list.add_rect_filled((bar_x, bar_bottom - filled_height), (bar_x + health_config.bar_width, bar_bottom), health_display_color)
		draw_list.add_rect((bar_x, bar_top), (bar_x + health_config.bar_width, bar_bottom), shadow_black, rounding=0.0, thickness=1.0, flags=0)

	name_config = vis_cfg.name
	if name_config.enabled:
		name = player.name[:18] if player.name else ""
		text_size = imgui.calc_text_size(name)
		weapon_key = weapon_icon_key("", getattr(player, "weapon_item_definition", 0)) if getattr(name_config, "weapon_enabled", False) else ""
		icon_width = icon_height = 0.0
		if weapon_key:
			left, bottom, right, top = FONT_ICONS[weapon_key]["bounds"]
			maximum = max(16.0, min(64.0, float(getattr(name_config, "weapon_size", 32.0))))
			fit = min(maximum / max(1, right - left), 20.0 / max(1, top - bottom))
			icon_width, icon_height = (right - left) * fit, (top - bottom) * fit
		gap = 6.0 if name and weapon_key else 0.0
		label_height = max(text_size.y if name else 0.0, icon_height)
		label_left = head_x - (text_size.x + gap + icon_width) * 0.5
		label_top = box_top - label_height - 2.0
		if name:
			name_y = label_top + (label_height - text_size.y) * 0.5
			draw_list.add_text((label_left + 1, name_y + 1), shadow_black, name)
			draw_list.add_text((label_left, name_y), color_u32(name_config.color), name)
		if weapon_key:
			draw_font_icon(draw_list, weapon_key, label_left + text_size.x + gap + icon_width * 0.5,
				label_top + label_height * 0.5, color_u32(name_config.color), maximum, 20.0)

	if health_config.mode in ("percent", "both"):
		hp_text = f"{health_clamped}%"
		text_size = imgui.calc_text_size(hp_text)
		health_x = head_x - text_size.x / 2
		health_y = box_top + 1
		draw_list.add_text((health_x + 1, health_y + 1), shadow_black, hp_text)
		draw_list.add_text((health_x, health_y), health_display_color, hp_text)

	distance_config = vis_cfg.distance
	if distance_config.enabled:
		distance_m = player.distance / _UNITS_PER_METER
		distance_text = f"{distance_m:.0f}m"
		text_size = imgui.calc_text_size(distance_text)
		distance_x = head_x - text_size.x / 2
		distance_y = box_bottom + 2
		draw_list.add_text((distance_x + 1, distance_y + 1), shadow_black, distance_text)
		draw_list.add_text((distance_x, distance_y), color_u32(distance_config.color), distance_text)

	snapline_config = vis_cfg.snapline
	if snapline_config.enabled:
		draw_list.add_line((screen_w / 2, screen_h), (feet_x, feet_y), color_u32(snapline_config.color), 1.0)


def draw_crosshair(draw_list, screen_w: int, screen_h: int, cfg) -> None:
	'''Draw the static crosshair from visuals.crosshair.'''
	if not cfg.enabled:
		return
	center_x = screen_w / 2
	center_y = screen_h / 2
	crosshair_color = color_u32(cfg.color)
	size = cfg.size
	gap = cfg.gap
	thickness = cfg.thickness
	draw_list.add_line((center_x - gap - size, center_y), (center_x - gap, center_y), crosshair_color, thickness)
	draw_list.add_line((center_x + gap, center_y), (center_x + gap + size, center_y), crosshair_color, thickness)
	draw_list.add_line((center_x, center_y - gap - size), (center_x, center_y - gap), crosshair_color, thickness)
	draw_list.add_line((center_x, center_y + gap), (center_x, center_y + gap + size), crosshair_color, thickness)
