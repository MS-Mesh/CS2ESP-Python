"""
Compact status pills for every enabled MS Mesh feature.
Toggle-bound modules stay above hold-bound ones; a hold-bound feature is lit only while its key is down.
"""

from typing import NamedTuple

from cs2esp.ui.fonts import get_fonts_for_current_context, pop as pop_font, push_sized, snap_size, text_size
from cs2esp.ui.hotkeys import _is_pressed
from cs2esp.visuals import icons as hud_icons
from cs2esp.visuals.draw_cache import DrawGeometryCache
from cs2esp.visuals.hud_manager import HudPositionManager
from cs2esp.visuals.icons import (
	draw_ambient_shadow, draw_arrows_glyph, draw_bolt_glyph, draw_bunny_glyph, draw_eye_glyph, draw_ghost_glyph, draw_icon_outline,
	draw_radar_glyph, draw_shield_glyph, draw_target_glyph, draw_waveform_glyph, icon_text_color, resolve_hud_theme, resolve_icon_style)
from cs2esp.visuals.theme import color_u32

_BASE_PILL_HEIGHT = 30.0
_BASE_GAP = 5.0
_BASE_PAD_X = 11.0
_BASE_FONT_SIZE = 13.0
_GLYPH_SIZE = 14.0
_CORNER_RADIUS = 8.0
_MIN_STACK_WIDTH = 48.0
_DEFAULT_X = 24.0
_DEFAULT_Y_FLOOR = 60.0
_STACK_BOTTOM_MARGIN = 130.0
_MAX_SIZE_SCALE = 2.5
_MIN_WIDTH_SCALE = 0.6
_MIN_HEIGHT_SCALE = 0.6


class ModuleStatus(NamedTuple):
	code: str
	name: str
	is_active: bool
	glyph_type: str


_GEOMETRY = DrawGeometryCache()


def _bottom_anchored_y(y: float, anchor_height: float, total_height: float, screen_h: int) -> float:
	'''Keep a growing stack's old bottom edge instead of pushing it off-screen.'''
	anchored = y + anchor_height - total_height
	return max(0.0, min(anchored, float(screen_h) - total_height))


def get_current_module_statuses(config) -> list[ModuleStatus]:
	'''Return every feature; hold binds are active only while held.'''

	def _resolve(*path):
		current = config
		for part in path:
			current = getattr(current, part, None)
			if current is None:
				return None
		return current

	specs = [
		(_resolve("triggerbot"), "TB", "Triggerbot", "bolt"),
		(_resolve("aimbot"), "AB", "Aimbot", "target"),
		(_resolve("rapidfire"), "RF", "Rapid-fire", "arrows"),
		(_resolve("autopistol"), "AP", "Auto-pistol", "waveform"),
		(_resolve("recoil_control"), "RC", "Recoil control", "target"),
		(_resolve("bhop"), "BH", "Bunny Hop", "bunny"),
		(_resolve("wallhack"), "WH", "Wallhack", "eye"),
		(_resolve("visuals", "name"), "LB", "Names", "eye"),
		(_resolve("visuals", "snapline"), "SL", "Snapline", "arrows"),
		(_resolve("visuals", "crosshair"), "CH", "Crosshair", "target"),
		(_resolve("fps_counter"), "FPS", "FPS counter", "waveform"),
		(_resolve("radar"), "RD", "Radar", "radar"),
		(_resolve("awareness"), "AW", "Awareness", "shield"),
	]
	ordered = sorted(specs, key=lambda spec: 1 if (spec[0] is not None and str(getattr(spec[0], "hotkey", "") or "").strip()) else 0)

	def _is_active(section) -> bool:
		if section is None:
			return False
		hold_key = str(getattr(section, "hotkey", "") or "").strip()
		if hold_key:
			# Hold bind is the live control, matching is_feature_active(); it works even when the master toggle is off
			return _is_pressed(hold_key)
		return bool(getattr(section, "enabled", False))

	return [ModuleStatus(code, name, _is_active(section), glyph) for section, code, name, glyph in ordered]


def render_status_pills(draw_list, screen_w: int, screen_h: int, config, hud_manager: HudPositionManager | None = None, interactive: bool = False) -> None:
	'''Render the vertical stack of module status pills.'''
	event_visuals = getattr(config, "event_visuals", None)
	if event_visuals is not None and not bool(getattr(event_visuals, "show_status_pills", True)):
		return

	theme = resolve_hud_theme(config)  # also syncs bloom/shadow intensities
	style = resolve_icon_style(config, theme=theme)

	modules = get_current_module_statuses(config)
	displayed = [module_status for module_status in modules if module_status.is_active]
	if not displayed:
		if not bool(getattr(event_visuals, "edit_mode", False)):
			return
		displayed = [ModuleStatus("PREVIEW", "Preview", True, "bolt")]

	fonts = get_fonts_for_current_context()
	body_font = getattr(fonts, "body", None) if fonts is not None else None

	def _measure(font_size: float, padding: float):
		pushed = push_sized(body_font, font_size)
		rows: list[tuple[ModuleStatus, float, float]] = []
		widest = _MIN_STACK_WIDTH * (font_size / _BASE_FONT_SIZE)
		try:
			# Hold-key transitions may change rows, never the scale reference
			for module_status in modules:
				item_width = padding * 3.0 + _GLYPH_SIZE * (font_size / _BASE_FONT_SIZE) + text_size(module_status.code).x
				widest = max(widest, item_width)
			for module_status in displayed:
				text_extent = text_size(module_status.code)
				item_width = padding * 3.0 + _GLYPH_SIZE * (font_size / _BASE_FONT_SIZE) + text_extent.x
				rows.append((module_status, text_extent.x, item_width))
				widest = max(widest, item_width)
		finally:
			pop_font(pushed)
		return rows, widest

	measured_pills, design_width = _measure(_BASE_FONT_SIZE, _BASE_PAD_X)
	design_height = len(displayed) * (_BASE_PILL_HEIGHT + _BASE_GAP) - _BASE_GAP

	scale = 1.0
	anchor_height = design_height
	if hud_manager is not None:
		saved_width, saved_height = hud_manager.get_size("status_pills", design_width, design_height)
		scale = HudPositionManager.content_scale(saved_width, design_width)
		anchor_height = saved_height

	pill_height = _BASE_PILL_HEIGHT * scale
	corner_radius = _CORNER_RADIUS * scale
	gap = _BASE_GAP * scale
	pad_x = _BASE_PAD_X * scale
	font_size = snap_size(_BASE_FONT_SIZE * scale)
	if scale != 1.0:
		measured_pills, max_width = _measure(font_size, pad_x)
	else:
		max_width = design_width

	total_height = len(displayed) * (pill_height + gap) - gap
	default_x = _DEFAULT_X
	default_y = max(_DEFAULT_Y_FLOOR, screen_h - total_height - _STACK_BOTTOM_MARGIN)

	if hud_manager is not None:
		x, y = hud_manager.get_pos("status_pills", default_x, default_y, screen_w, screen_h)
		y = _bottom_anchored_y(y, anchor_height, total_height, screen_h)
		hud_manager.set_max_bounds(design_width * _MAX_SIZE_SCALE, design_height * _MAX_SIZE_SCALE)
		x, y, max_width, total_height, changed = hud_manager.handle_interaction(
			widget_id="status_pills",
			x=x,
			y=y,
			width=max_width,
			height=total_height,
			interactive=interactive,
			draw_list=draw_list,
			label="Status Pills",
			min_w=design_width * _MIN_WIDTH_SCALE,
			min_h=_BASE_PILL_HEIGHT * _MIN_HEIGHT_SCALE,
			reference_size=(design_width * scale, anchor_height),
			preserve_aspect=True,
		)
		if changed:
			new_width, _new_height = hud_manager.get_size("status_pills", design_width * scale, anchor_height)
			new_scale = HudPositionManager.content_scale(new_width, design_width)
			if new_scale != scale:
				scale = new_scale
				pill_height, gap = _BASE_PILL_HEIGHT * scale, _BASE_GAP * scale
				pad_x, corner_radius = _BASE_PAD_X * scale, _CORNER_RADIUS * scale
				font_size = snap_size(_BASE_FONT_SIZE * scale)
				measured_pills, max_width = _measure(font_size, pad_x)
				new_height = len(displayed) * (pill_height + gap) - gap
				y = _bottom_anchored_y(y, total_height, new_height, screen_h)
				total_height = new_height
	else:
		x, y = default_x, default_y

	column_background = color_u32((theme.window_bg[0], theme.window_bg[1], theme.window_bg[2], 1.0))
	column_text = color_u32(icon_text_color(style, theme.text))

	geometry_key = (tuple(displayed), theme, style, screen_w, screen_h, x, y, max_width, total_height, scale, font_size,
		hud_icons._shadow_scale, hud_icons._bloom_scale)
	geometry_signature = None if interactive else _GEOMETRY.signature(draw_list, fonts, geometry_key)
	if _GEOMETRY.replay(draw_list, geometry_signature):
		return
	geometry_start = _GEOMETRY.start(draw_list, geometry_signature)

	glyph_drawers = {
		"bolt": draw_bolt_glyph,
		"bunny": draw_bunny_glyph,
		"eye": draw_eye_glyph,
		"radar": draw_radar_glyph,
		"shield": draw_shield_glyph,
		"ghost": draw_ghost_glyph,
		"target": draw_target_glyph,
		"arrows": draw_arrows_glyph,
		"waveform": draw_waveform_glyph,
	}
	pill_y = y
	for module_status, text_width, _item_width in measured_pills:
		# Every pill uses the same width so the stack never changes shape
		draw_ambient_shadow(draw_list, x, pill_y, max_width, pill_height, corner_radius, layers=2, spread=3.0, opacity=0.45)
		draw_list.add_rect_filled((x, pill_y), (x + max_width, pill_y + pill_height), column_background, corner_radius)
		draw_icon_outline(draw_list, x, pill_y, max_width, pill_height, corner_radius, style, active=True)

		glyph_size = _GLYPH_SIZE * scale
		content_width = glyph_size + pad_x + text_width
		start_x = x + (max_width - content_width) * 0.5
		glyph_x = start_x + glyph_size * 0.5
		glyph_y = pill_y + pill_height * 0.5
		draw_glyph = glyph_drawers.get(module_status.glyph_type)
		if draw_glyph is not None:
			draw_glyph(draw_list, glyph_x, glyph_y, glyph_size, column_text)

		pushed = push_sized(body_font, font_size)
		try:
			text_x = start_x + glyph_size + pad_x
			text_y = pill_y + (pill_height - font_size) * 0.5
			draw_list.add_text((text_x, text_y), column_text, module_status.code)
		finally:
			pop_font(pushed)

		pill_y += pill_height + gap

	_GEOMETRY.capture(draw_list, geometry_signature, geometry_start)
