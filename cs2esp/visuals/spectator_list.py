"""
Spectator list HUD rows for players currently observing the local pawn.
Rows mirror the header styling, and the newest name emits a spectator event.
"""

from typing import NamedTuple

from cs2esp.features.event_system import EventSystem, EventType
from cs2esp.ui.fonts import get_fonts_for_current_context, pop as pop_font, push_sized, snap_size, text_size
from cs2esp.visuals.hud_manager import HudPositionManager
from cs2esp.visuals.icons import draw_ambient_shadow, draw_eye_glyph, draw_icon_outline, draw_user_glyph, icon_text_color, resolve_hud_theme, resolve_icon_style
from cs2esp.visuals.theme import color_u32


class SpectatorEntry(NamedTuple):
	name: str
	is_friend: bool = False


class _SpectatorMetrics(NamedTuple):
	header_width: float
	header_text_width: float
	header_text_height: float
	count_text_width: float
	count_text_height: float
	rows: list[tuple[str, float]]
	widest: float


class SpectatorTracker:
	"""Stateful detector for newly arriving spectators to emit event notifications."""

	def __init__(self) -> None:
		self.prev_names: set[str] = set()

	def update(self, current_names: list[str]) -> None:
		events = EventSystem.get()
		curr_set = set(current_names)
		newbies = curr_set - self.prev_names
		for name in newbies:
			events.emit(EventType.SPECTATOR_NEW, title="SPECTATOR", detail=f"{name} is now watching you", data={"name": name})
		self.prev_names = curr_set


_spec_tracker = SpectatorTracker()


def render_spectator_list(
	draw_list,
	screen_w: int,
	screen_h: int,
	config,
	current_spectators: list[str] | None = None,
	hud_manager: HudPositionManager | None = None,
	interactive: bool = False,
	anchor_right: float | None = None,
	anchor_y: float | None = None,
) -> tuple[float, float]:
	'''Render the spectator list HUD widget; returns (width, height).'''
	cfg = getattr(config, "event_visuals", None)
	if cfg is not None and not bool(getattr(cfg, "show_spectators", True)):
		return (0.0, 0.0)

	theme = resolve_hud_theme(config)  # side effect: syncs bloom/shadow intensities
	icon_style = resolve_icon_style(config, theme=theme)

	names = list(current_spectators or [])
	_spec_tracker.update(names)

	if not names:
		edit_mode = bool(getattr(cfg, "edit_mode", False)) if cfg is not None else False
		if not edit_mode:
			return (0.0, 0.0)
		names = ["1st preview"]

	# Metrics scale with the stored frame width
	fonts = get_fonts_for_current_context()
	body_font = getattr(fonts, "body", None) if fonts is not None else None
	base_font_size = 15.0
	base_h_pill = 28.0
	count_txt = str(len(names))

	def _measure(fs: float, pad: float) -> _SpectatorMetrics:
		'''Measure the header, count badge and every row at one font scale.'''
		pushed = push_sized(body_font, fs)
		rows: list[tuple[str, float]] = []
		ratio = fs / 15.0
		widest = 110.0 * ratio
		try:
			ts_hdr = text_size("Spectators")
			ts_cnt = text_size(count_txt)
			hdr = pad * 2.0 + 16.0 * ratio + ts_hdr.x + 16.0 * ratio + ts_cnt.x
			widest = max(widest, hdr)
			for name in names:
				ts_n = text_size(name)
				nw = pad * 2.0 + 22.0 * ratio + ts_n.x
				rows.append((name, nw))
				widest = max(widest, nw)
		finally:
			pop_font(pushed)
		return _SpectatorMetrics(hdr, ts_hdr.x, ts_hdr.y, ts_cnt.x, ts_cnt.y, rows, widest)

	hdr_w, ts_hdr_x, ts_hdr_y, ts_cnt_x, ts_cnt_y, measured_rows, design_w = _measure(base_font_size, 10.0)
	design_h = base_h_pill + 5.0 + len(names) * (base_h_pill + 5.0)

	scale = 1.0
	if hud_manager is not None:
		saved_w, saved_h = hud_manager.get_size("spectators", design_w, design_h)
		scale = HudPositionManager.content_scale(saved_w, design_w)

	h_pill = base_h_pill * scale
	r_pill = 8.0 * scale
	pad_x = 10.0 * scale
	row_gap = 5.0 * scale
	font_size = snap_size(base_font_size * scale)

	if scale != 1.0:
		hdr_w, ts_hdr_x, ts_hdr_y, ts_cnt_x, ts_cnt_y, measured_rows, max_w = _measure(font_size, pad_x)
	else:
		max_w = design_w

	total_h = h_pill + row_gap + len(names) * (h_pill + row_gap)
	default_x = screen_w - max_w - 20.0
	default_y = 65.0

	if anchor_right is not None:
		default_x = anchor_right - max_w
	if anchor_y is not None:
		default_y = anchor_y
	if hud_manager is not None:
		x, y = hud_manager.get_pos("spectators", default_x, default_y, screen_w, screen_h)
		hud_manager.set_max_bounds(design_w * 2.5, design_h * 2.5)
		x, y, max_w, total_h, changed = hud_manager.handle_interaction(
			widget_id="spectators", x=x, y=y, width=max_w, height=total_h, interactive=interactive, draw_list=draw_list, label="Spectators",
			min_w=design_w * 0.6, min_h=base_h_pill * 0.6, reference_size=(design_w * scale, saved_h), reference_edge="top", preserve_aspect=True,
		)
		if changed:
			new_width, _new_height = hud_manager.get_size("spectators", design_w * scale, saved_h)
			new_scale = HudPositionManager.content_scale(new_width, design_w)
			if new_scale != scale:
				scale = new_scale
				h_pill, r_pill, pad_x, row_gap = base_h_pill * scale, 8.0 * scale, 10.0 * scale, 5.0 * scale
				font_size = snap_size(base_font_size * scale)
				hdr_w, ts_hdr_x, ts_hdr_y, ts_cnt_x, ts_cnt_y, measured_rows, max_w = _measure(font_size, pad_x)
				total_h = h_pill + row_gap + len(names) * (h_pill + row_gap)
	else:
		x, y = default_x, default_y

	col_bg = color_u32((theme.window_bg[0], theme.window_bg[1], theme.window_bg[2], 1.0))
	col_text = color_u32(icon_text_color(icon_style, theme.text))
	col_count = color_u32(icon_text_color(icon_style, theme.text_dim))
	col_count_bg = color_u32((theme.child_bg[0], theme.child_bg[1], theme.child_bg[2], 1.0))

	align_right = (x + max_w * 0.5) > (screen_w * 0.5)
	hdr_x = x + max_w - hdr_w if align_right else x
	draw_ambient_shadow(draw_list, hdr_x, y, hdr_w, h_pill, r_pill, layers=2, spread=3.0, opacity=0.40)
	draw_list.add_rect_filled((hdr_x, y), (hdr_x + hdr_w, y + h_pill), col_bg, r_pill)
	draw_icon_outline(draw_list, hdr_x, y, hdr_w, h_pill, r_pill, icon_style, active=True)

	draw_eye_glyph(draw_list, hdr_x + pad_x + 7.0 * scale, y + h_pill * 0.5, 13.0 * scale, col_text)

	pushed = push_sized(body_font, font_size)
	try:
		text_y = y + (h_pill - ts_hdr_y) * 0.5
		draw_list.add_text((hdr_x + pad_x + 18.0 * scale, text_y), col_text, "Spectators")

		cb_x = hdr_x + pad_x + 18.0 * scale + ts_hdr_x + 6.0 * scale
		cb_w = ts_cnt_x + 8.0 * scale
		cb_h = 16.0 * scale
		cb_y = y + (h_pill - cb_h) * 0.5
		draw_list.add_rect_filled((cb_x, cb_y), (cb_x + cb_w, cb_y + cb_h), col_count_bg, 4.0 * scale)
		draw_list.add_text((cb_x + 4.0 * scale, cb_y + (cb_h - ts_cnt_y) * 0.5), col_count, count_txt)
	finally:
		pop_font(pushed)

	cur_y = y + h_pill + row_gap
	for name, nw in measured_rows:
		row_x = x + max_w - nw if align_right else x
		draw_ambient_shadow(draw_list, row_x, cur_y, nw, h_pill, r_pill, layers=2, spread=3.0, opacity=0.40)
		draw_list.add_rect_filled((row_x, cur_y), (row_x + nw, cur_y + h_pill), col_bg, r_pill)
		draw_icon_outline(draw_list, row_x, cur_y, nw, h_pill, r_pill, icon_style, active=True)

		draw_user_glyph(draw_list, row_x + pad_x + 7.0 * scale, cur_y + h_pill * 0.5, 12.0 * scale, col_text)

		pushed = push_sized(body_font, font_size)
		try:
			ts_n = text_size(name)
			name_y = cur_y + (h_pill - ts_n.y) * 0.5
			draw_list.add_text((row_x + pad_x + 18.0 * scale, name_y), col_text, name)
		finally:
			pop_font(pushed)

		cur_y += h_pill + row_gap

	return (max_w, total_h)
