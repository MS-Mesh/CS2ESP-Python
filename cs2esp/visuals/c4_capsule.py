"""
Floating C4 capsule with the plant site, defuse state and countdown.
Damage is estimated from the local pawn's distance to the planted bomb.
"""

import math
from time import perf_counter

from cs2esp.features.event_system import EventSystem, EventType
from cs2esp.entity import read_game_time
from cs2esp.offsets import Netvars, Offsets
from cs2esp.ui.fonts import get_fonts_for_current_context, pop as pop_font, push_sized, snap_size, text_size
from cs2esp.visuals.hud_manager import HudPositionManager
from cs2esp.visuals.icons import draw_ambient_shadow, draw_icon_outline, get_hud_corner_radius, icon_text_color, resolve_hud_theme, resolve_icon_style
from cs2esp.visuals.theme import color_u32


def calculate_bomb_damage(c4_pos: tuple[float, float, float], player_pos: tuple[float, float, float], armor: int = 0) -> int:
	'''Calculate CS2 C4 explosion damage using standard Falloff formula.'''
	dx = c4_pos[0] - player_pos[0]
	dy = c4_pos[1] - player_pos[1]
	dz = c4_pos[2] - player_pos[2]
	distance = math.sqrt(dx * dx + dy * dy + dz * dz)

	default_damage = 650.0
	default_radius = 2275.0
	sigma = default_radius / 3.0

	raw_damage = default_damage * math.exp(-(distance * distance) / (2.0 * sigma * sigma))

	if armor > 0:
		armor_ratio = 0.5
		armor_bonus = 0.5
		armor_absorbed = raw_damage * armor_ratio
		armor_cost = (raw_damage - armor_absorbed) * armor_bonus

		if armor_cost > float(armor):
			armor_cost = float(armor) * (1.0 / armor_bonus)
			armor_absorbed = raw_damage - armor_cost

		damage = armor_absorbed
	else:
		damage = raw_damage

	return max(0, int(math.floor(damage)))


class C4Tracker:
	"""Stateful detector for planted C4 events (planted, defusing, defused, exploded)."""

	def __init__(self) -> None:
		self.last_planted_state: bool = False
		self.last_defusing_state: bool = False

	def update_events(self, is_planted: bool, is_defusing: bool, time_remaining: float, site_str: str, exploded: bool = False, defused: bool = False) -> None:
		events = EventSystem.get()
		if is_planted and not self.last_planted_state:
			events.emit(EventType.BOMB_PLANT, title="C4 PLANTED", detail=f"Planted at site {site_str}")
		elif not is_planted and self.last_planted_state:
			if exploded:
				events.emit(EventType.BOMB_EXPLODED, title="C4 DETONATED", detail=f"Site {site_str} destroyed")
			elif defused:
				events.emit(EventType.BOMB_DEFUSED, title="C4 DEFUSED", detail="Bomb safely defused")

		if is_planted and is_defusing and not self.last_defusing_state:
			events.emit(EventType.BOMB_DEFUSING, title="C4 DEFUSING", detail=f"Counter-Terrorists defusing {site_str}")

		self.last_planted_state = is_planted
		self.last_defusing_state = is_defusing


_c4_tracker = C4Tracker()

# Local-clock countdown anchor, used only when CGlobalVars is unreadable
_bomb_anchor_local = 0.0
_bomb_anchor_rem = 40.0


def _read_bomb_state(mem):
	'''Poll the planted C4 entity; returns the eight-field state tuple, or None while it is absent.'''
	global _bomb_anchor_local, _bomb_anchor_rem
	if not Offsets.dwPlantedC4:
		return None
	try:
		planted = mem.read_long(mem.client(Offsets.dwPlantedC4))
	except Exception:
		return None
	if not planted or planted < 0x10000 or planted > 0x7FFFFFFFFFFF:
		return None

	ticking = bool(mem.read_byte(planted + Netvars.m_bBombTicking))
	exploded = bool(mem.read_byte(planted + Netvars.m_bHasExploded))
	defused = bool(mem.read_byte(planted + Netvars.m_bBombDefused))
	is_planted = ticking and not exploded and not defused

	site_idx = mem.read_int(planted + Netvars.m_nBombSite)
	site_letter = "A" if site_idx == 0 else ("B" if site_idx == 1 else "?")

	blow_time = mem.read_float(planted + Netvars.m_flC4Blow)
	game_time = read_game_time(mem)
	now = perf_counter()
	if game_time is not None:
		time_rem = max(0.0, blow_time - game_time)
		_bomb_anchor_local = now
		_bomb_anchor_rem = time_rem
	elif is_planted:
		# No game clock: hold a local countdown seeded at the standard fuse
		if not _bomb_anchor_local:
			_bomb_anchor_local = now
			_bomb_anchor_rem = 40.0
		time_rem = max(0.0, _bomb_anchor_rem - (now - _bomb_anchor_local))
	else:
		_bomb_anchor_local = 0.0
		time_rem = 0.0

	defusing = bool(mem.read_byte(planted + Netvars.m_bBeingDefused))
	defuse_rem = 0.0
	if defusing and game_time is not None:
		defuse_rem = max(0.0, mem.read_float(planted + Netvars.m_flDefuseCountDown) - game_time)

	c4_origin = (0.0, 0.0, 0.0)
	try:
		scene_node = mem.read_long(planted + Netvars.m_pGameSceneNode)
		if scene_node and 0x10000 <= scene_node <= 0x7FFFFFFFFFFF:
			c4_origin = mem.read_vec3(scene_node + Netvars.m_vecAbsOrigin)
	except Exception:
		pass

	return (is_planted, site_letter, time_rem, defusing, defuse_rem, exploded, defused, c4_origin)


def render_c4_capsule(
	draw_list,
	screen_w: int,
	screen_h: int,
	config,
	mem=None,
	local_pos: tuple[float, float, float] | None = None,
	local_armor: int = 0,
	hud_manager: HudPositionManager | None = None,
	interactive: bool = False,
) -> None:
	'''Render the floating C4 capsule widget at top-center under the scoreboard.'''
	cfg = getattr(config, "event_visuals", None)
	if cfg is not None and not bool(getattr(cfg, "show_c4_timer", True)):
		return

	theme = resolve_hud_theme(config)  # side effect: syncs bloom/shadow intensities
	icon_style = resolve_icon_style(config, theme=theme)
	r = get_hud_corner_radius(config, default=6.0)
	inner_r = max(2.0, r - 2.0)

	is_planted = False
	site_letter = "A"
	time_rem = 0.0
	defusing = False
	defuse_rem = 0.0
	exploded = False
	defused = False
	c4_origin = (0.0, 0.0, 0.0)

	if mem is not None:
		try:
			state = _read_bomb_state(mem)
		except Exception:
			state = None
		if state is not None:
			(is_planted, site_letter, time_rem, defusing, defuse_rem, exploded, defused, c4_origin) = state

	is_standby = not is_planted
	# Feed the tracker every frame, even in standby, so edge detection works
	_c4_tracker.update_events(is_planted, defusing, time_rem, site_letter, exploded=exploded, defused=defused)

	if is_standby:
		# Standby renders only as an edit-mode preview or while dragging
		edit_mode = bool(getattr(cfg, "edit_mode", False)) if cfg is not None else False
		active_interaction = False
		if hud_manager is not None:
			state = getattr(hud_manager, "_states", {}).get("c4_capsule")
			if state and (state.is_dragging or state.is_resizing):
				active_interaction = True
		if not (active_interaction or edit_mode):
			return
		time_rem = 40.0
		health_dmg = 0
		site_letter = "A"
	else:
		health_dmg = calculate_bomb_damage(c4_origin, local_pos or (0, 0, 0), local_armor)

	default_x = (screen_w - 270.0) * 0.5
	default_y = 62.0
	default_w = 270.0
	default_h = 26.0

	scale = 1.0
	if hud_manager is not None:
		saved_w, saved_h = hud_manager.get_size("c4_capsule", default_w, default_h)
		scale = HudPositionManager.content_scale(saved_w, default_w)

	total_w = default_w * scale
	h = default_h * scale
	x, y = default_x, default_y
	if hud_manager is not None:
		x, y = hud_manager.get_pos("c4_capsule", default_x, default_y, screen_w, screen_h)
		hud_manager.set_max_bounds(default_w * 2.5, default_h * 2.5)
		x, y, total_w, h, changed = hud_manager.handle_interaction(
			widget_id="c4_capsule", x=x, y=y, width=total_w, height=h, interactive=interactive, draw_list=draw_list, label="C4 Capsule",
			min_w=default_w * 0.6, min_h=default_h * 0.6, reference_size=(default_w * scale, saved_h), reference_edge="top", preserve_aspect=True,
		)
		if changed:
			new_width, _new_height = hud_manager.get_size("c4_capsule", default_w * scale, saved_h)
			scale = HudPositionManager.content_scale(new_width, default_w)
			total_w, h = default_w * scale, default_h * scale

	r *= scale
	inner_r *= scale
	fonts = get_fonts_for_current_context()
	body_font = getattr(fonts, "body", None) if fonts is not None else None
	font_size = snap_size(13.0 * scale)

	pulse = 0.6 + 0.4 * abs(math.sin(perf_counter() * 3.5))

	col_bg = color_u32((theme.window_bg[0], theme.window_bg[1], theme.window_bg[2], 1.0))
	col_dmg_badge = color_u32((theme.child_bg[0], theme.child_bg[1], theme.child_bg[2], 1.0))
	col_dmg_border = color_u32((1.0, 1.0, 1.0, 0.08))
	col_site_badge = color_u32((theme.child_bg[0], theme.child_bg[1], theme.child_bg[2], 1.0))
	col_pulse_dot = color_u32((1.0, 0.3, 0.3, pulse)) if not is_standby else color_u32((0.2, 0.8, 0.4, 0.9))
	col_accent_text = color_u32(icon_text_color(icon_style, theme.text))
	col_text = col_accent_text

	draw_ambient_shadow(draw_list, x, y, total_w, h, r, layers=2, spread=3.0, opacity=0.35)
	draw_list.add_rect_filled((x, y), (x + total_w, y + h), col_bg, r)
	draw_icon_outline(draw_list, x, y, total_w, h, r, icon_style)

	site_w = 84.0 * scale
	draw_list.add_rect_filled((x + 2 * scale, y + 2 * scale), (x + 2 * scale + site_w, y + h - 2 * scale), col_site_badge, inner_r)
	draw_list.add_circle_filled((x + 12 * scale, y + h * 0.5), 3.5 * scale, col_pulse_dot, 10)
	site_txt = f"{site_letter} standby" if is_standby else f"{site_letter} plant"
	pushed = push_sized(body_font, font_size)
	try:
		draw_list.add_text((x + 20 * scale, y + 5 * scale), col_text, site_txt)
	finally:
		pop_font(pushed)

	dmg_w = 82.0 * scale
	dmg_x = x + site_w + 5 * scale
	draw_list.add_rect_filled((dmg_x, y + 2 * scale), (dmg_x + dmg_w, y + h - 2 * scale), col_dmg_badge, inner_r)
	draw_list.add_rect((dmg_x, y + 2 * scale), (dmg_x + dmg_w, y + h - 2 * scale), col_dmg_border, rounding=inner_r, thickness=scale)
	dmg_txt = "safe 100" if is_standby else f"-{health_dmg} health"
	pushed = push_sized(body_font, font_size)
	try:
		bounds = text_size(dmg_txt)
		draw_list.add_text((dmg_x + (dmg_w - bounds.x) * 0.5, y + (h - bounds.y) * 0.5), col_accent_text, dmg_txt)
	finally:
		pop_font(pushed)

	time_x = dmg_x + dmg_w + 3 * scale
	time_w = max(70.0 * scale, total_w - (time_x - x) - 2 * scale)
	draw_list.add_rect_filled((time_x, y + 2 * scale), (time_x + time_w, y + h - 2 * scale), col_site_badge, inner_r)
	if is_standby:
		timer_txt = "40.0s round"
	elif defusing:
		timer_txt = f"{defuse_rem:.1f}s defusing" if defuse_rem > 0.0 else "defusing"
	else:
		timer_txt = f"{time_rem:.1f}s explosion"
	pushed = push_sized(body_font, font_size)
	try:
		bounds = text_size(timer_txt)
		draw_list.add_text((time_x + (time_w - bounds.x) * 0.5, y + (h - bounds.y) * 0.5), col_text, timer_txt)
	finally:
		pop_font(pushed)
