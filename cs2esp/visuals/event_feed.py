"""
Event cards grow from the nearest vertical edge toward the screen centre.
Saved reference geometry remains independent of the visible notification count.
"""

from cs2esp.features.event_system import EventNotification, EventSystem, EventType
from cs2esp.ui.fonts import get_fonts_for_current_context, pop as pop_font, push_sized, snap_size, text_size
from cs2esp.visuals.hud_manager import HudPositionManager
from cs2esp.visuals.icons import (
	_BADGE_BASE_RADIUS, _CARD_GAP, _CARD_HEIGHT, _CARD_PAD_LEFT, _CARD_TEXT_GAP, _CARD_TRAILING_PAD, EventStyle,
	_apply_label_prefix, _tokenize_event_detail, draw_v_log_card, get_event_style, resolve_hud_theme, resolve_icon_style)
from cs2esp.visuals.weapon_icons import event_weapon_layout


_EVENT_GROUPS: tuple[tuple[str, tuple[EventType, ...]], ...] = (
	("filter_combat", (EventType.HIT, EventType.MISS, EventType.KILL, EventType.HEADSHOT, EventType.KNIFE, EventType.ZEUS, EventType.GRENADE_THROW)),
	("filter_modules", (EventType.TRIGGERBOT, EventType.BHOP)),
	("filter_c4", (EventType.BOMB_PLANT, EventType.BOMB_DEFUSING, EventType.BOMB_DEFUSED, EventType.BOMB_EXPLODED)),
	("filter_tactical", (EventType.SPECTATOR_NEW, EventType.REAR_THREAT)),
	# Bind ON/OFF notifications belong to the Hotkeys card rather than the feed
	("filter_system", (EventType.OFFSETS_UPDATED, EventType.CONFIG_LOADED)),
)


def _allowed_types(event_vis) -> frozenset[EventType]:
	'''Resolve the enabled event groups without including bind toggles.'''
	allowed: set[EventType] = set()
	for group_key, event_types in _EVENT_GROUPS:
		if event_vis is None or bool(getattr(event_vis, group_key, True)):
			allowed.update(event_types)
	return frozenset(allowed)


def _stack_size(events: list[EventNotification], styles: dict[EventType, EventStyle], font, font_size: float, scale: float) -> tuple[float, float]:
	'''Measure the unchanged card geometry, excluding the final inter-card gap.'''
	stack_width = 0.0
	pushed = push_sized(font, font_size)
	try:
		for event in events:
			style = styles[event.event_type]
			detail = _apply_label_prefix(event.detail, style)
			text_width = sum(text_size(chunk).x for chunk, _is_bold in _tokenize_event_detail(detail))
			weapon_key, weapon_width = event_weapon_layout(event, style.glyph, scale)
			weapon_space = weapon_width + _CARD_TEXT_GAP * scale if weapon_key else 0.0
			stack_width = max(stack_width, _CARD_PAD_LEFT * scale + _BADGE_BASE_RADIUS * 2.0 * scale
				+ _CARD_TEXT_GAP * scale + weapon_space + text_width + _CARD_TRAILING_PAD * scale)
	finally:
		pop_font(pushed)
	return stack_width, (len(events) * _CARD_HEIGHT + max(0, len(events) - 1) * _CARD_GAP) * scale


def render_event_feed(draw_list, screen_w: int, screen_h: int, config, hud_manager: HudPositionManager | None = None, interactive: bool = False) -> None:
	"""Render newest cards at the automatic anchor; changing live counts never changes persisted geometry."""
	event_vis = getattr(config, "event_visuals", None)
	if event_vis is not None and not bool(getattr(event_vis, "enabled", True)):
		return
	theme = resolve_hud_theme(config)
	icon_style = resolve_icon_style(config, theme=theme)
	max_visible = int(getattr(event_vis, "max_events", 6) if event_vis else 6)
	allowed = _allowed_types(event_vis)
	active_events = [event for event in EventSystem.get().get_active_notifications(max_visible=None) if event.event_type in allowed]
	styles = {event.event_type: get_event_style(config, event.event_type) for event in active_events}
	active_events = [event for event in active_events if styles[event.event_type].enabled and event.alpha > 0.01]
	if not active_events:
		edit_mode = bool(getattr(event_vis, "edit_mode", False))
		editable = edit_mode and (hud_manager is None or hud_manager.editing_allowed(interactive))
		if not editable:
			return
		active_events = [EventNotification(EventType.HIT, "HIT", "Hit preview in the head for 100 (0 remaining)", alpha_override=1.0)]
		styles = {EventType.HIT: get_event_style(config, EventType.HIT)}
	else:
		active_events = active_events[:max_visible]

	default_width, default_height = 270.0, 140.0
	default_x, default_y = 24.0, max(0.0, float(screen_h) - 400.0)
	reference_width, reference_height = default_width, default_height
	reference_x, reference_y = default_x, default_y
	if hud_manager is not None:
		reference_width, reference_height = hud_manager.get_size("event_feed", default_width, default_height)
		reference_x, reference_y = hud_manager.get_pos("event_feed", default_x, default_y, screen_w, screen_h)
	reference_edge = hud_manager.event_reference_edge(reference_y, reference_height, screen_h) if hud_manager else (
		"top" if reference_y + reference_height * 0.5 < screen_h * 0.5 else "bottom")

	fonts = get_fonts_for_current_context()
	body_font = getattr(fonts, "body", None) if fonts is not None else None
	base_font_size = float(getattr(event_vis, "font_size", 16.0) if event_vis else 16.0)
	scale = HudPositionManager.content_scale_2d(reference_width, reference_height, default_width, default_height)
	font_size = snap_size(base_font_size * scale)
	stack_width, stack_height = _stack_size(active_events, styles, body_font, font_size, scale)
	stack_x = max(0.0, min(reference_x, float(screen_w) - stack_width))
	stack_y = reference_y if reference_edge == "top" else reference_y + reference_height - stack_height
	stack_y = max(0.0, min(stack_y, float(screen_h) - stack_height))
	if hud_manager is not None:
		hud_manager.set_max_bounds(stack_width / scale * 2.5, stack_height / scale * 2.5)
		stack_x, stack_y, live_width, live_height, changed = hud_manager.handle_interaction(
			widget_id="event_feed", x=stack_x, y=stack_y, width=stack_width, height=stack_height, interactive=interactive,
			draw_list=draw_list, label="Event Feed", min_w=160.0, min_h=24.0, reference_size=(reference_width, reference_height),
			reference_edge=reference_edge, preserve_aspect=True,
		)
		if changed:
			new_width, new_height = hud_manager.get_size("event_feed", reference_width, reference_height)
			new_scale = HudPositionManager.content_scale_2d(new_width, new_height, default_width, default_height)
			if new_scale != scale:
				scale = new_scale
				font_size = snap_size(base_font_size * scale)
				stack_width, stack_height = _stack_size(active_events, styles, body_font, font_size, scale)
				stack_x = max(0.0, min(stack_x, float(screen_w) - stack_width))
				stack_y = stack_y if reference_edge == "top" else stack_y + live_height - stack_height
				stack_y = max(0.0, min(stack_y, float(screen_h) - stack_height))

	corner_radius = float(getattr(event_vis, "corner_radius", 8.0) or 8.0)
	cursor_y = stack_y
	for event in (active_events if reference_edge == "top" else reversed(active_events)):
		card_height = draw_v_log_card(
			draw_list=draw_list, x=stack_x, y=cursor_y, item=event, theme=theme, font=body_font, font_size=font_size,
			corner_radius=corner_radius, style=styles.get(event.event_type), card_scale=scale, icon_style=icon_style,
		)
		cursor_y += card_height
