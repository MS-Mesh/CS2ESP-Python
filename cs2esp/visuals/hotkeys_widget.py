"""
Every feature with a real keybind gets a row of [name][key][HOLD/TOGGLE] pills; hold binds dim unless the key is held, toggle binds stay lit while enabled.
The three system hotkeys (menu, master toggle, exit) are excluded, and icon chrome comes from resolve_icon_style.
"""

from typing import NamedTuple

from cs2esp.ui.fonts import get_fonts_for_current_context, pop as pop_font, push_sized, snap_pos, snap_size, text_size
from cs2esp.ui.hotkeys import _is_pressed
from cs2esp.visuals.hud_manager import HudPositionManager
from cs2esp.visuals.draw_cache import DrawGeometryCache
from cs2esp.visuals import icons as hud_icons
from cs2esp.visuals.icons import draw_ambient_shadow, draw_icon_outline, icon_dim_color, icon_text_color, resolve_hud_theme, resolve_icon_style
from cs2esp.visuals.theme import color_u32


class HotkeyDisplay(NamedTuple):
	name: str
	key: str
	mode: str  # "hold" | "toggle"
	is_active: bool


SYSTEM_KEYS = {"ctrl+j", "ctrl+k", "ctrl+l", "ctrl+shift+j", "ctrl+shift+k", "ctrl+shift+l"}

_GEOMETRY = DrawGeometryCache()

_BASE_HEADER_FONT = 18.0
_BASE_HEADER_HEIGHT = 32.0
_BASE_ROW_FONT = 14.0
_BASE_ROW_HEIGHT = 26.0
_BASE_MODE_FONT = 11.0
_BASE_MODE_HEIGHT = 26.0
_BASE_PAD = 8.0
_BASE_GAP = 4.0
_MIN_STACK_WIDTH = 90.0


def _mode_padding(mode_font: float, base_mode_font: float = _BASE_MODE_FONT) -> float:
	'''Scale the mode pill padding with the mode font.'''
	return 6.0 * (mode_font / base_mode_font)


def get_active_hotkey_list(config) -> list[HotkeyDisplay]:
	'''Every feature with a real keybind; hold binds report the live key state, toggle binds the enabled flag.'''
	items: list[HotkeyDisplay] = []

	def _sec(*path):
		cur = config
		for part in path:
			cur = getattr(cur, part, None)
			if cur is None:
				return None
		return cur

	mappings = [
		("Triggerbot", _sec("triggerbot"), "hotkey", "hotkey_toggle"),
		("Aimbot", _sec("aimbot"), "hotkey", "hotkey_toggle"),
		("Rapid-fire", _sec("rapidfire"), "hotkey", "hotkey_toggle"),
		("Auto-pistol", _sec("autopistol"), "hotkey", "hotkey_toggle"),
		("Recoil control", _sec("recoil_control"), "hotkey", "hotkey_toggle"),
		("Bunny Hop", _sec("bhop"), "hotkey", "hotkey_toggle"),
		("Wallhack", _sec("wallhack"), "", "hotkey_toggle"),
		("Names", _sec("visuals", "name"), "", "hotkey_toggle"),
		("Snapline", _sec("visuals", "snapline"), "", "hotkey_toggle"),
		("Crosshair", _sec("visuals", "crosshair"), "", "hotkey_toggle"),
		("FPS counter", _sec("fps_counter"), "", "hotkey_toggle"),
		("Radar", _sec("radar"), "", "hotkey_toggle"),
		("Awareness", _sec("awareness"), "", "hotkey_toggle"),
	]

	for name, sec, hold_k, tog_k in mappings:
		if sec is None:
			continue
		enabled = bool(getattr(sec, "enabled", False))

		hold_val = str(getattr(sec, hold_k, "") or "").strip().lower() if hold_k else ""
		tog_val = str(getattr(sec, tog_k, "") or "").strip().lower() if tog_k else ""

		if hold_val and hold_val not in SYSTEM_KEYS:
			items.append(HotkeyDisplay(name=name, key=hold_val.upper(), mode="hold", is_active=_is_pressed(hold_val)))
		elif tog_val and tog_val not in SYSTEM_KEYS:
			items.append(HotkeyDisplay(name=name, key=tog_val.upper(), mode="toggle", is_active=enabled))

	return items


def render_hotkeys_widget(
	draw_list,
	screen_w: int,
	screen_h: int,
	config,
	hud_manager: HudPositionManager | None = None,
	interactive: bool = False,
	anchor_right: float | None = None,
	anchor_y: float | None = None,
) -> tuple[float, float]:
	'''Render the bound-hotkeys card; anchor_right pins it flush to the right edge for the layout engine.'''
	cfg = getattr(config, "event_visuals", None)
	if cfg is not None and not bool(getattr(cfg, "show_hotkeys", True)):
		return (0.0, 0.0)

	theme = resolve_hud_theme(config)  # side effect: syncs bloom/shadow scales
	style = resolve_icon_style(config, theme=theme)
	items = get_active_hotkey_list(config)
	if not items:
		edit_mode = bool(getattr(cfg, "edit_mode", False)) if cfg is not None else False
		if not edit_mode:
			return (0.0, 0.0)
		items = [HotkeyDisplay(name="Preview Bind", key="EDIT MODE", mode="toggle", is_active=True)]

	fonts = get_fonts_for_current_context()
	body_font = getattr(fonts, "body", None) if fonts is not None else None
	base_header_h = _BASE_HEADER_HEIGHT

	def _measure(header_fs, row_fs, mode_fs, pad, gap):
		rows: list[tuple[HotkeyDisplay, float, float, float, float, float, float]] = []
		widest = _MIN_STACK_WIDTH * (header_fs / _BASE_HEADER_FONT)
		pushed = push_sized(body_font, header_fs)
		try:
			ts_hdr = text_size("Hotkeys")
		finally:
			pop_font(pushed)
		hdr_w = pad * 2.0 + 18.0 * (header_fs / _BASE_HEADER_FONT) + ts_hdr.x
		hdr_h = ts_hdr.y
		dot = 5.0 * (row_fs / _BASE_ROW_FONT)
		dot_gap = 5.0 * (row_fs / _BASE_ROW_FONT)
		mode_pad = _mode_padding(mode_fs)
		pushed = push_sized(body_font, row_fs)
		try:
			for it in items:
				ts_n = text_size(it.name)
				ts_k = text_size(it.key)
				name_w = pad * 2.0 + dot + dot_gap + ts_n.x
				key_w = pad * 2.0 + ts_k.x
				pushed_mode = push_sized(body_font, mode_fs)
				try:
					ts_m = text_size(it.mode.upper())
				finally:
					pop_font(pushed_mode)
				mode_w = mode_pad * 2.0 + ts_m.x
				rows.append((it, name_w, key_w, mode_w, ts_n.y, ts_k.y, ts_m.y))
				widest = max(widest, name_w + gap + key_w + gap + mode_w)
		finally:
			pop_font(pushed)
		return hdr_w, hdr_h, rows, widest

	hdr_w, ts_hdr_y, measured_rows, design_w = _measure(_BASE_HEADER_FONT, _BASE_ROW_FONT, _BASE_MODE_FONT, _BASE_PAD, _BASE_GAP)
	design_h = base_header_h + _BASE_GAP + len(items) * (_BASE_ROW_HEIGHT + _BASE_GAP)

	scale = 1.0
	if hud_manager is not None:
		saved_w, saved_h = hud_manager.get_size("hotkeys", design_w, design_h)
		scale = HudPositionManager.content_scale(saved_w, design_w)

	header_font = snap_size(_BASE_HEADER_FONT * scale)
	header_h = base_header_h * scale
	row_font = snap_size(_BASE_ROW_FONT * scale)
	row_h = _BASE_ROW_HEIGHT * scale
	mode_font = snap_size(_BASE_MODE_FONT * scale)
	mode_h = _BASE_MODE_HEIGHT * scale
	pad_x = _BASE_PAD * scale
	col_gap = _BASE_GAP * scale
	row_gap = _BASE_GAP * scale
	r_pill = 6.0 * scale

	if scale != 1.0:
		hdr_w, ts_hdr_y, measured_rows, max_total_w = _measure(header_font, row_font, mode_font, pad_x, col_gap)
	else:
		max_total_w = design_w

	total_h = header_h + row_gap + len(items) * (row_h + row_gap)
	default_x = 24.0
	default_y = 440.0

	if anchor_right is not None:
		default_x = anchor_right - max_total_w
		default_y = 140.0
	if anchor_y is not None:
		default_y = anchor_y
	if hud_manager is not None:
		x, y = hud_manager.get_pos("hotkeys", default_x, default_y, screen_w, screen_h)
		hud_manager.set_max_bounds(design_w * 2.5, design_h * 2.5)
		x, y, max_total_w, total_h, changed = hud_manager.handle_interaction(
			widget_id="hotkeys", x=x, y=y, width=max_total_w, height=total_h, interactive=interactive, draw_list=draw_list, label="Hotkeys",
			min_w=design_w * 0.6, min_h=base_header_h * 0.6, reference_size=(design_w * scale, saved_h), reference_edge="top", preserve_aspect=True,
		)
		if changed:
			new_width, _new_height = hud_manager.get_size("hotkeys", design_w * scale, saved_h)
			new_scale = HudPositionManager.content_scale(new_width, design_w)
			if new_scale != scale:
				scale = new_scale
				header_font, row_font, mode_font = snap_size(_BASE_HEADER_FONT * scale), snap_size(_BASE_ROW_FONT * scale), snap_size(_BASE_MODE_FONT * scale)
				header_h, row_h, mode_h = base_header_h * scale, _BASE_ROW_HEIGHT * scale, _BASE_MODE_HEIGHT * scale
				pad_x, col_gap, row_gap, r_pill = _BASE_PAD * scale, _BASE_GAP * scale, _BASE_GAP * scale, 6.0 * scale
				hdr_w, ts_hdr_y, measured_rows, max_total_w = _measure(header_font, row_font, mode_font, pad_x, col_gap)
				total_h = header_h + row_gap + len(items) * (row_h + row_gap)
	else:
		x, y = default_x, default_y

	col_bg = color_u32((theme.window_bg[0], theme.window_bg[1], theme.window_bg[2], 1.0))
	col_text = color_u32(icon_text_color(style, theme.text))
	col_dim = color_u32(icon_dim_color(style, theme.text_dim))
	col_dot_off = 0xFF3A3A42
	accent_r, accent_g, accent_b, _accent_a = style.accent
	col_accent = color_u32(style.accent)

	geometry_key = (
		tuple(items),
		theme,
		style,
		screen_w,
		screen_h,
		x,
		y,
		max_total_w,
		total_h,
		scale,
		header_font,
		row_font,
		mode_font,
		hud_icons._shadow_scale,
		hud_icons._bloom_scale,
	)
	geometry_signature = None if interactive else _GEOMETRY.signature(draw_list, fonts, geometry_key)
	if _GEOMETRY.replay(draw_list, geometry_signature):
		return (max_total_w, total_h)
	geometry_start = _GEOMETRY.start(draw_list, geometry_signature)

	def _text(x0, y0, col, text, size):
		x0 = snap_pos(x0)
		y0 = snap_pos(y0)
		pushed = push_sized(body_font, size)
		try:
			draw_list.add_text((x0, y0), col, text)
		finally:
			pop_font(pushed)

	# Content hugs the nearest screen edge: right-aligned on the right half
	align_right = (x + max_total_w * 0.5) > (screen_w * 0.5)
	hdr_x = x + max_total_w - hdr_w if align_right else x

	draw_ambient_shadow(draw_list, hdr_x, y, hdr_w, header_h, 8.0 * scale, layers=3, spread=3.0, opacity=0.45)
	draw_list.add_rect_filled((hdr_x, y), (hdr_x + hdr_w, y + header_h), col_bg, 8.0 * scale)
	draw_icon_outline(draw_list, hdr_x, y, hdr_w, header_h, 8.0 * scale, style)

	sl_x = hdr_x + pad_x + 5.0 * scale
	sl_y = y + header_h * 0.5
	draw_list.add_line((sl_x - 4.5 * scale, sl_y - 2.0 * scale), (sl_x + 4.5 * scale, sl_y - 2.0 * scale), col_text, 1.2 * scale)
	draw_list.add_circle_filled((sl_x - 1.5 * scale, sl_y - 2.0 * scale), 1.4 * scale, col_text, 8)
	draw_list.add_line((sl_x - 4.5 * scale, sl_y + 2.0 * scale), (sl_x + 4.5 * scale, sl_y + 2.0 * scale), col_text, 1.2 * scale)
	draw_list.add_circle_filled((sl_x + 1.5 * scale, sl_y + 2.0 * scale), 1.4 * scale, col_text, 8)

	ty = y + (header_h - ts_hdr_y) * 0.5
	tx = hdr_x + pad_x + 14.0 * scale
	_text(tx, ty, col_text, "Hotkeys", header_font)

	cur_y = y + header_h + row_gap
	for it, name_w, key_w, mode_w, ny, ky, my in measured_rows:
		row_w = name_w + col_gap + key_w + col_gap + mode_w
		row_x = x + max_total_w - row_w if align_right else x
		name_col = col_text if it.is_active else col_dim
		key_col = col_text if it.is_active else col_dim
		mode_col = col_text if it.is_active else col_dim
		dot_col = col_accent if it.is_active else col_dot_off

		draw_ambient_shadow(draw_list, row_x, cur_y, name_w, row_h, r_pill, layers=2, spread=2.5, opacity=0.4)
		draw_list.add_rect_filled((row_x, cur_y), (row_x + name_w, cur_y + row_h), col_bg, r_pill)
		draw_icon_outline(draw_list, row_x, cur_y, name_w, row_h, r_pill, style, active=it.is_active)
		dot_x = row_x + pad_x + 2.5 * scale
		dot_y = cur_y + row_h * 0.5
		if it.is_active:
			for ring, alpha in ((4.5, 0.16), (7.0, 0.08)):
				draw_list.add_circle_filled((dot_x, dot_y), ring * scale, color_u32((accent_r, accent_g, accent_b, alpha)), 12)
		draw_list.add_circle_filled((dot_x, dot_y), 2.5 * scale, dot_col, 10)
		name_x = dot_x + 2.5 * scale + 5.0 * scale
		_text(name_x, cur_y + (row_h - ny) * 0.5, name_col, it.name, row_font)

		kx = row_x + name_w + col_gap
		draw_ambient_shadow(draw_list, kx, cur_y, key_w, row_h, r_pill, layers=2, spread=2.5, opacity=0.4)
		draw_list.add_rect_filled((kx, cur_y), (kx + key_w, cur_y + row_h), col_bg, r_pill)
		draw_icon_outline(draw_list, kx, cur_y, key_w, row_h, r_pill, style, active=it.is_active)
		_text(kx + pad_x, cur_y + (row_h - ky) * 0.5, key_col, it.key, row_font)

		mx = kx + key_w + col_gap
		my_y = cur_y
		draw_ambient_shadow(draw_list, mx, my_y, mode_w, mode_h, r_pill, layers=2, spread=2.0, opacity=0.35)
		draw_list.add_rect_filled((mx, my_y), (mx + mode_w, my_y + mode_h), col_bg, r_pill)
		draw_icon_outline(draw_list, mx, my_y, mode_w, mode_h, r_pill, style, active=it.is_active, glow_scale=0.75)
		mode_pad = _mode_padding(mode_font)
		_text(mx + mode_pad, my_y + (mode_h - my) * 0.5, mode_col, it.mode.upper(), mode_font)

		cur_y += row_h + row_gap

	_GEOMETRY.capture(draw_list, geometry_signature, geometry_start)
	return (max_total_w, total_h)
