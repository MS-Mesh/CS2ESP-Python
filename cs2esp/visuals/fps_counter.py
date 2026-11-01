"""
Configurable FPS, ping, jitter, CPU and RAM overlay in horizontal capsule pills.
The saved frame width drives the content scale; each pill fits its live text.
"""

import math
from time import strftime

from cs2esp.visuals.icons import (
	draw_ambient_shadow, draw_bars_glyph, draw_clock_glyph, draw_icon_outline, draw_user_glyph,
	draw_waveform_glyph, draw_wifi_waves_glyph, resolve_hud_theme, resolve_icon_style)
from cs2esp.ui.fonts import get_fonts_for_current_context, pop as pop_font, push_sized, snap_size, text_size
from cs2esp.visuals.hud_manager import HudPositionManager
from cs2esp.visuals.theme import color_u32


def _available_number(value):
	'''Return a finite nonnegative measurement, or None when unavailable.'''
	if value is None:
		return None
	try:
		number = float(value)
	except (TypeError, ValueError, OverflowError):
		return None
	return number if math.isfinite(number) and number >= 0.0 else None


def draw_fps_counter(
	draw_list,
	screen_w: int,
	screen_h: int,
	cfg,
	fps: float | None,
	ping_ms: int | None = None,
	jitter_ms: int | None = None,
	latency_ms: float | None = None,
	cpu_percent: float | None = None,
	sys_cpu_percent: float | None = None,
	ram_mb: float | None = None,
	ram_pair: tuple[float, float] | None = None,
	hud_manager: HudPositionManager | None = None,
	interactive: bool = False,
	theme_name: str | None = None,
	root_config=None,
	fps_source: str | None = None,
	overlay_fps: float | None = None,
) -> None:
	'''Draw whichever of FPS, ping, latency, and jitter the user has enabled.'''
	if not bool(getattr(cfg, "enabled", False)):
		return

	show_fps = bool(getattr(cfg, "show_fps", True))
	show_ping = bool(getattr(cfg, "show_ping", True))
	show_jitter = bool(getattr(cfg, "show_jitter", False))

	style = str(getattr(cfg, "style", "capsule") or "capsule").lower()

	fps_value = _available_number(fps)
	ping_value = _available_number(ping_ms)
	fps_text = f"{int(round(fps_value))} fps" if fps_value is not None else "-- fps"
	ping_text = f"{int(ping_value)} ms" if ping_value is not None else "-- ms"

	fonts = get_fonts_for_current_context()
	body_font = getattr(fonts, "body", None) if fonts is not None else None

	theme = resolve_hud_theme(root_config) if root_config is not None else None
	icon_style = resolve_icon_style(root_config, theme=theme) if root_config is not None else None

	show_time = bool(getattr(cfg, "show_time", False))

	items_to_render: list[tuple[str, str, int, str]] = []
	if bool(getattr(cfg, "show_brand", False)):
		brand_name = str(getattr(cfg, "brand_text", "") or "msmesh").strip()
		if brand_name:
			items_to_render.append(("triangle", brand_name, 0xFFFFFFFF, brand_name))
	if bool(getattr(cfg, "show_user", False)):
		user_name = str(getattr(cfg, "user_name", "") or "user").strip()
		if user_name:
			items_to_render.append(("user", user_name, 0xFFD0D6E2, user_name))

	# Reference widths preserve the saved font scale
	if show_fps:
		items_to_render.append(("bars", fps_text, 0xFFFFFFFF, "999 fps"))
	if bool(getattr(cfg, "show_overlay_fps", False)):
		loop_fps = _available_number(overlay_fps)
		loop_text = f"loop {int(round(loop_fps))} fps" if loop_fps is not None else "loop -- fps"
		items_to_render.append(("loop_bars", loop_text, 0xFFFFFFFF, "loop 999 fps"))
	if show_ping:
		items_to_render.append(("wifi", ping_text, 0xFFD0D6E2, "999 ms"))
	if bool(getattr(cfg, "show_latency", True)):
		# Present-to-display timing is not input-to-display latency
		latency = _available_number(latency_ms)
		latency_text = f"{int(round(latency))} ms" if latency is not None else "-- ms"
		items_to_render.append(("waveform", latency_text, 0xFF88A8FF, "999 ms"))
	if bool(getattr(cfg, "show_cpu", True)):
		# Real system-wide CPU load, the same number Task Manager shows
		cpu_src = _available_number(sys_cpu_percent)
		cpu_text = f"cpu {int(round(cpu_src))}%" if cpu_src is not None else "cpu --%"
		items_to_render.append(("chip", cpu_text, 0xFF88A8FF, "cpu 100%"))
	if show_jitter:
		jitter = _available_number(jitter_ms)
		jitter_text = f"±{int(round(jitter))} ms" if jitter is not None else "±-- ms"
		items_to_render.append(("bars", jitter_text, 0xFF9CA3AF, "±999 ms"))
	if bool(getattr(cfg, "show_ram", False)):
		ram_text = "--/-- GB"
		if isinstance(ram_pair, (tuple, list)) and len(ram_pair) >= 2:
			used, total = (_available_number(ram_pair[0]), _available_number(ram_pair[1]))
			if used is not None and total is not None and total > 0.0:
				ram_text = f"{used:.1f}/{total:.1f} GB"
		items_to_render.append(("memory", ram_text, 0xFF9CA3AF, "99.9/99.9 GB"))

	# Optional clock, off by default
	if show_time:
		items_to_render.append(("clock", strftime("%H:%M"), 0xFFD0D6E2, "00:00"))

	if not items_to_render:
		return

	# A custom override wins; otherwise the theme text colour keeps every widget consistent
	if icon_style is not None and theme is not None:
		text_rgba = icon_style.text or theme.text
		unified = color_u32(text_rgba)
		items_to_render = [(glyph_type, text, unified, ref) for glyph_type, text, _col, ref in items_to_render]

	if style == "capsule":
		# Design metrics at scale 1.0
		pad_x = 15.0
		base_h = 40.0
		r = float(getattr(cfg, "corner_radius", 8.0) or 8.0)
		glyph_w = 19.0
		gap = 8.0
		base_font_size = float(getattr(cfg, "hud_font_size", 17.0) or 17.0)

		# Measure at base size to recover the design width
		pushed = push_sized(body_font, base_font_size)
		legacy_items = [item for item in items_to_render if item[0] != "loop_bars"] or items_to_render
		design_w = gap * max(0, len(legacy_items) - 1)
		try:
			for _g, text, _c, ref in legacy_items:
				# Preserve the font size implied by an existing saved width
				design_w += pad_x * 2.0 + glyph_w + text_size(ref).x
		finally:
			pop_font(pushed)

		# The stored frame size maps to a content scale factor
		scale = 1.0
		if hud_manager is not None:
			saved_w, saved_h = hud_manager.get_size("fps_counter", design_w, base_h)
			scale = HudPositionManager.content_scale(saved_w, design_w)

		h = base_h * scale
		font_size = snap_size(base_font_size * scale)
		s_glyph_w = glyph_w * scale
		s_gap = gap * scale

		def _measure(measured_scale: float, measured_font_size: float):
			measured: list[tuple[str, str, int, float, float]] = []
			pushed = push_sized(body_font, measured_font_size)
			try:
				for glyph_type, text, col, ref in items_to_render:
					bounds = text_size(text)
					pill_width = (pad_x * 2.0 + glyph_w + 7.0) * measured_scale + bounds.x
					measured.append((glyph_type, text, col, bounds.x, pill_width))
			finally:
				pop_font(pushed)
			return measured, sum(item[4] for item in measured) + gap * measured_scale * (len(measured) - 1)

		measured_items, total_w = _measure(scale, font_size)
		default_x = screen_w - total_w - 16.0
		default_y = 14.0

		if hud_manager is not None:
			x, y = hud_manager.get_pos("fps_counter", default_x, default_y, screen_w, screen_h)
			# Enforce strict boundary clamping so it never clips off-screen
			if x + total_w > screen_w - 16.0:
				x = screen_w - total_w - 16.0
			x = max(16.0, x)
			hud_manager.set_max_bounds(total_w / scale * 2.5, base_h * 2.5)
			x, y, total_w, h, changed = hud_manager.handle_interaction(
				widget_id="fps_counter", x=x, y=y, width=total_w, height=h, interactive=interactive, draw_list=draw_list, label="Watermark",
				min_w=design_w * 0.6, min_h=base_h * 0.6, reference_size=(design_w * scale, saved_h), reference_edge="top", preserve_aspect=True,
			)
			if changed:
				new_width, _new_height = hud_manager.get_size("fps_counter", design_w * scale, saved_h)
				new_scale = HudPositionManager.content_scale(new_width, design_w)
				if new_scale != scale:
					scale = new_scale
					h, font_size = base_h * scale, snap_size(base_font_size * scale)
					s_glyph_w, s_gap = glyph_w * scale, gap * scale
					measured_items, total_w = _measure(scale, font_size)
			if x + total_w > screen_w - 16.0:
				x = screen_w - total_w - 16.0
			x = max(16.0, x)
		else:
			x, y = default_x, default_y

		r *= scale
		col_bg = color_u32((theme.window_bg[0], theme.window_bg[1], theme.window_bg[2], 1.0)) if theme is not None else color_u32((0.05, 0.06, 0.09, 1.0))
		col_border = color_u32((1.0, 1.0, 1.0, 0.08))

		glyph_drawers = {
			"user": draw_user_glyph,
			"bars": draw_bars_glyph,
			"loop_bars": draw_bars_glyph,
			"wifi": draw_wifi_waves_glyph,
			"waveform": draw_waveform_glyph,
			"clock": draw_clock_glyph,
		}
		cur_x = x
		for glyph_type, text, col, tw, pw in measured_items:
			draw_ambient_shadow(draw_list, cur_x, y, pw, h, r, layers=2, spread=2.5, opacity=0.35)
			draw_list.add_rect_filled((cur_x, y), (cur_x + pw, y + h), col_bg, r)
			if icon_style is not None:
				draw_icon_outline(draw_list, cur_x, y, pw, h, r, icon_style)
			else:
				draw_list.add_rect((cur_x, y), (cur_x + pw, y + h), col_border, rounding=r, thickness=1.0)

			content_w = s_glyph_w + 7.0 * scale + tw
			start_x = cur_x + (pw - content_w) * 0.5
			gx = start_x + s_glyph_w * 0.5 - scale
			gy = y + h * 0.5
			glyph_sz = 19.0 * scale
			draw_glyph = glyph_drawers.get(glyph_type)
			if glyph_type == "triangle":
				draw_list.add_triangle_filled((gx, gy - 4.0 * scale), (gx - 3.5 * scale, gy + 3.0 * scale), (gx + 3.5 * scale, gy + 3.0 * scale), col)
			elif draw_glyph is not None:
				draw_glyph(draw_list, gx, gy, glyph_sz, col)
			elif glyph_type == "memory":
				draw_list.add_rect((gx - 7.0 * scale, gy - 3.5 * scale), (gx + 7.0 * scale, gy + 2.5 * scale), col, rounding=0.7 * scale, thickness=1.2 * scale)
				for slot in range(3):
					cell_x = gx + (slot - 1) * 4.0 * scale
					draw_list.add_rect_filled((cell_x - scale, gy - 1.8 * scale), (cell_x + scale, gy + 0.8 * scale), col)
				for contact in range(5):
					pin_x = gx + (contact - 2) * 2.4 * scale
					draw_list.add_line((pin_x, gy + 2.5 * scale), (pin_x, gy + 4.5 * scale), col, 1.2 * scale)
			elif glyph_type == "chip":
				draw_list.add_rect((gx - 5.0 * scale, gy - 4.0 * scale), (gx + 5.0 * scale, gy + 4.0 * scale), col, rounding=1.5 * scale, thickness=1.4 * scale)
				for pin_index in range(-2, 3):
					pin_x = gx + pin_index * 2.5 * scale
					draw_list.add_line((pin_x, gy - 4.0 * scale), (pin_x, gy - 6.0 * scale), col, 1.2 * scale)
					draw_list.add_line((pin_x, gy + 4.0 * scale), (pin_x, gy + 6.0 * scale), col, 1.2 * scale)

			tx = start_x + s_glyph_w + 7.0 * scale
			pushed = push_sized(body_font, font_size)
			try:
				ts_y = text_size(text).y
				ty = y + (h - ts_y) * 0.5
				draw_list.add_text((tx, ty), col, text)
			finally:
				pop_font(pushed)

			cur_x += pw + s_gap

		return


__all__ = ["draw_fps_counter"]
