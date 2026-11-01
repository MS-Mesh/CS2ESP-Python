"""
ImDrawList vector icons, ambient shadows, radiant bloom and event-card rendering.
Pure geometric primitives keep the HUD pixel-sharp at every scale without image assets; one theme accent colours the chrome.
"""

import re
from functools import lru_cache
from operator import index
from typing import NamedTuple

from cs2esp.features.event_system import EventNotification, EventType
from cs2esp.ui.fonts import pop as pop_font, push_sized, text_size
from cs2esp.visuals.theme import color_u32, with_alpha
from cs2esp.visuals.weapon_icons import PUBLIC_FONT_ICONS, draw_font_icon, event_weapon_layout


class ThemeColors(NamedTuple):
	accent: tuple[float, float, float, float]
	safe: tuple[float, float, float, float]
	warn: tuple[float, float, float, float]
	danger: tuple[float, float, float, float]
	window_bg: tuple[float, float, float, float]
	child_bg: tuple[float, float, float, float]
	border: tuple[float, float, float, float]
	text: tuple[float, float, float, float]
	text_dim: tuple[float, float, float, float]


THEME_PALETTES: dict[str, ThemeColors] = {
	"slate": ThemeColors(
		accent=(0.486, 0.659, 0.769, 1.0), safe=(0.486, 0.659, 0.769, 1.0), warn=(0.486, 0.659, 0.769, 1.0), danger=(0.486, 0.659, 0.769, 1.0),
		window_bg=(0.063, 0.078, 0.102, 0.94), child_bg=(0.094, 0.125, 0.165, 0.90), border=(0.486, 0.659, 0.769, 0.35),
		text=(0.941, 0.953, 0.965, 1.0), text_dim=(0.659, 0.698, 0.745, 1.0),
	),
	"midnight": ThemeColors(
		accent=(0.659, 0.333, 0.969, 1.0), safe=(0.659, 0.333, 0.969, 1.0), warn=(0.659, 0.333, 0.969, 1.0), danger=(0.659, 0.333, 0.969, 1.0),
		window_bg=(0.043, 0.051, 0.078, 0.95), child_bg=(0.086, 0.102, 0.165, 0.92), border=(0.659, 0.333, 0.969, 0.35),
		text=(0.953, 0.957, 0.973, 1.0), text_dim=(0.647, 0.678, 0.725, 1.0),
	),
	"neon": ThemeColors(
		accent=(0.0, 0.941, 1.0, 1.0), safe=(0.0, 0.941, 1.0, 1.0), warn=(0.0, 0.941, 1.0, 1.0), danger=(0.0, 0.941, 1.0, 1.0),
		window_bg=(0.020, 0.035, 0.059, 0.95), child_bg=(0.051, 0.086, 0.137, 0.92), border=(0.0, 0.941, 1.0, 0.40),
		text=(1.0, 1.0, 1.0, 1.0), text_dim=(0.580, 0.639, 0.722, 1.0),
	),
	"daylight": ThemeColors(
		accent=(0.259, 0.427, 0.525, 1.0), safe=(0.224, 0.486, 0.380, 1.0), warn=(0.604, 0.408, 0.180, 1.0), danger=(0.678, 0.310, 0.310, 1.0),
		window_bg=(0.953, 0.945, 0.925, 0.95), child_bg=(0.984, 0.980, 0.969, 0.92), border=(0.259, 0.427, 0.525, 0.30),
		text=(0.106, 0.133, 0.161, 1.0), text_dim=(0.337, 0.380, 0.424, 1.0),
	),
}


def parse_color_rgba(c) -> tuple[float, float, float, float] | None:
	'''Parse color from list/tuple of floats [0..1] or hex string #rrggbb/#rrggbbaa.'''
	if c is None:
		return None
	if isinstance(c, (tuple, list)) and len(c) >= 3:
		try:
			red = float(c[0])
			green = float(c[1])
			blue = float(c[2])
			alpha = float(c[3]) if len(c) >= 4 else 1.0
			return (max(0.0, min(1.0, red)), max(0.0, min(1.0, green)), max(0.0, min(1.0, blue)), max(0.0, min(1.0, alpha)))
		except (ValueError, TypeError):
			return None
	if isinstance(c, str):
		hex_value = c.strip().lstrip("#")
		if len(hex_value) == 3:
			hex_value = "".join([character * 2 for character in hex_value])
		if len(hex_value) == 6:
			try:
				red = int(hex_value[0:2], 16) / 255.0
				green = int(hex_value[2:4], 16) / 255.0
				blue = int(hex_value[4:6], 16) / 255.0
				return (round(red, 4), round(green, 4), round(blue, 4), 1.0)
			except ValueError:
				return None
		if len(hex_value) == 8:
			try:
				red = int(hex_value[0:2], 16) / 255.0
				green = int(hex_value[2:4], 16) / 255.0
				blue = int(hex_value[4:6], 16) / 255.0
				alpha = int(hex_value[6:8], 16) / 255.0
				return (round(red, 4), round(green, 4), round(blue, 4), round(alpha, 4))
			except ValueError:
				return None
	return None


def get_theme_palette(theme_name: str | None = None, custom_color=None) -> ThemeColors:
	'''Return ThemeColors with background glass determined by theme_name, and icons tinted by custom_color.'''
	theme_key = str(theme_name or "").lower().strip()
	if custom_color is None and (theme_key.startswith("#") or (len(theme_key) in (3, 6, 8) and all(character in "0123456789abcdef" for character in theme_key))):
		custom_color = theme_key

	if "midnight" in theme_key:
		base_palette = THEME_PALETTES["midnight"]
	elif "daylight" in theme_key:
		base_palette = THEME_PALETTES["daylight"]
	elif "neon" in theme_key or "velocity" in theme_key:
		base_palette = THEME_PALETTES.get("neon", THEME_PALETTES["slate"])
	else:
		base_palette = THEME_PALETTES["slate"]

	parsed_color = parse_color_rgba(custom_color)
	if parsed_color is not None:
		red, green, blue, alpha = parsed_color
		return ThemeColors(
			accent=(red, green, blue, alpha), safe=(red, green, blue, alpha), warn=(red, green, blue, alpha), danger=(red, green, blue, alpha),
			window_bg=base_palette.window_bg, child_bg=base_palette.child_bg, border=(red, green, blue, 0.38),
			text=base_palette.text, text_dim=base_palette.text_dim,
		)
	return base_palette


def resolve_hud_theme(config, specific_theme: str | None = None) -> ThemeColors:
	'''Resolve the active HUD theme and custom color dynamically from config.'''
	_sync_effect_intensities(config)
	if specific_theme and specific_theme != "auto":
		return get_theme_palette(specific_theme)

	event_visuals = getattr(config, "event_visuals", None)
	theme_name = getattr(event_visuals, "theme", "auto") if event_visuals is not None else "auto"
	custom_color = getattr(event_visuals, "custom_color", None) if event_visuals is not None else None
	if theme_name == "auto" or not theme_name:
		general = getattr(config, "general", None)
		theme_name = getattr(general, "ui_theme", "midnight") if general is not None else "midnight"
		if custom_color is None and general is not None:
			custom_color = getattr(general, "custom_theme_color", None)
	return get_theme_palette(theme_name, custom_color=custom_color)


_SHADOW_BASE = 0.55
_BLOOM_BASE = 0.35
_shadow_scale = 1.0
_bloom_scale = 1.0


def _sync_effect_intensities(config) -> None:
	global _shadow_scale, _bloom_scale
	event_visuals = getattr(config, "event_visuals", None)
	if event_visuals is None:
		return
	try:
		_shadow_scale = max(0.0, float(getattr(event_visuals, "shadow_intensity", _SHADOW_BASE))) / _SHADOW_BASE
		_bloom_scale = max(0.0, float(getattr(event_visuals, "bloom_intensity", _BLOOM_BASE))) / _BLOOM_BASE
	except (TypeError, ValueError):
		pass


def get_hud_corner_radius(cfg, default: float = 6.0) -> float:
	'''Read the uniform corner radius for all HUD widgets.'''
	event_visuals = getattr(cfg, "event_visuals", None)
	if event_visuals is not None:
		radius_value = getattr(event_visuals, "corner_radius", default)
		try:
			return max(0.0, float(radius_value))
		except (ValueError, TypeError):
			return default
	return default


class HudIconStyle(NamedTuple):
	"""Global chrome for HUD icon pills; text is None when each widget keeps its own palette."""
	accent: tuple[float, float, float, float]
	text: tuple[float, float, float, float] | None
	outline_px: float
	glow: float


def resolve_icon_style(config, theme: ThemeColors | None = None) -> HudIconStyle:
	'''Resolve the shared icon outline/glow/text settings; empty overrides follow the active theme.'''
	if theme is None:
		theme = resolve_hud_theme(config)
	event_visuals = getattr(config, "event_visuals", None)
	accent_override = parse_color_rgba(getattr(event_visuals, "icon_accent_color", None) if event_visuals is not None else None)
	text_override = parse_color_rgba(getattr(event_visuals, "icon_text_color", None) if event_visuals is not None else None)
	accent = accent_override or theme.accent
	try:
		outline_px = float(getattr(event_visuals, "icon_outline_width", 1.5))
	except (TypeError, ValueError):
		outline_px = 1.5
	outline_px = max(0.0, min(4.0, outline_px))
	try:
		glow = float(getattr(event_visuals, "bloom_intensity", _BLOOM_BASE))
	except (TypeError, ValueError):
		glow = _BLOOM_BASE
	glow = max(0.0, min(1.0, glow))
	return HudIconStyle(accent=accent, text=text_override, outline_px=outline_px, glow=glow)


def icon_text_color(style: HudIconStyle, default: tuple[float, float, float, float]) -> tuple[float, float, float, float]:
	'''Text/glyph colour: custom override when set, else the widget default.'''
	return style.text or default


def icon_dim_color(style: HudIconStyle, default: tuple[float, float, float, float]) -> tuple[float, float, float, float]:
	'''Dimmed text/glyph colour for inactive rows.'''
	if style.text is None:
		return default
	red, green, blue, alpha = style.text
	return (red, green, blue, max(0.0, min(1.0, alpha * 0.55)))


def icon_border_color(style: HudIconStyle, active: bool = False) -> tuple[float, float, float, float]:
	'''Outline colour with the designed alpha (0.55 idle / 0.78 active).'''
	red, green, blue, alpha = style.accent
	if alpha >= 0.99:
		alpha = 0.78 if active else 0.55
	return (red, green, blue, alpha)


def _shadow_layers(layers, spread, opacity):
	for layer_index in range(layers, 0, -1):
		padding = (layer_index / layers) * spread
		y_offset = (layer_index / layers) * 1.5
		alpha = opacity * (1.0 - (layer_index / (layers + 1.2))) * 0.35
		yield padding, y_offset, color_u32((0.0, 0.0, 0.0, alpha))


@lru_cache(maxsize=256)
def _cached_shadow_layers(layers, spread, opacity):
	return tuple(_shadow_layers(layers, spread, opacity))


def _bloom_layers(passes, spread, intensity, red, green, blue, base_alpha):
	for pass_index in range(passes, 0, -1):
		padding = (pass_index / passes) * spread
		layer_alpha = base_alpha * intensity * (1.0 - (pass_index / (passes + 1))) ** 1.4
		yield padding, color_u32((red, green, blue, min(1.0, layer_alpha)))


@lru_cache(maxsize=256)
def _cached_bloom_layers(passes, spread, intensity, red, green, blue, base_alpha):
	return tuple(_bloom_layers(passes, spread, intensity, red, green, blue, base_alpha))


def draw_ambient_shadow(
	draw_list, x: float, y: float, w: float, h: float, radius: float, layers: int = 4, spread: float = 6.0, opacity: float = 0.55,
) -> None:
	'''Draw a soft multi-layer ambient shadow around the widget rectangle.'''
	opacity *= _shadow_scale
	if opacity <= 0.001 or layers <= 0:
		return
	layers = index(layers)
	effects = _cached_shadow_layers(layers, spread, opacity) if layers <= 16 else _shadow_layers(layers, spread, opacity)
	for padding, y_offset, layer_color in effects:
		layer_radius = radius + padding * 0.5
		draw_list.add_rect_filled((x - padding, y - padding + y_offset), (x + w + padding, y + h + padding + y_offset), layer_color, layer_radius)


def draw_bloom_rect(
	draw_list, x: float, y: float, w: float, h: float, radius: float, bloom_rgba: tuple[float, float, float, float],
	passes: int = 3, spread: float = 4.0, intensity: float = 0.30,
) -> None:
	'''Draw a radiant atmospheric bloom halo around illuminated elements.'''
	intensity *= _bloom_scale
	if intensity <= 0.001 or passes <= 0:
		return
	red, green, blue, base_alpha = bloom_rgba
	passes = index(passes)
	effects = (
		_cached_bloom_layers(passes, spread, intensity, red, green, blue, base_alpha) if passes <= 16
		else _bloom_layers(passes, spread, intensity, red, green, blue, base_alpha)
	)
	for padding, layer_color in effects:
		layer_radius = radius + padding * 0.5
		draw_list.add_rect((x - padding, y - padding), (x + w + padding, y + h + padding), layer_color, rounding=layer_radius, thickness=1.2)


def draw_icon_outline(
	draw_list, x: float, y: float, w: float, h: float, radius: float, style: HudIconStyle, active: bool = False, glow_scale: float = 1.0, alpha: float = 1.0,
) -> None:
	'''Outline stays accent-coloured; active only brightens it, and alpha fades outline and glow together.'''
	alpha = max(0.0, min(1.0, float(alpha)))
	if alpha <= 0.001:
		return
	if style.outline_px > 0.01:
		red, green, blue, border_alpha = icon_border_color(style, active)
		draw_list.add_rect((x, y), (x + w, y + h), color_u32((red, green, blue, border_alpha * alpha)), rounding=radius, thickness=style.outline_px)
	if style.glow > 0.001:
		draw_bloom_rect(
			draw_list, x, y, w, h, radius, style.accent, passes=3, spread=4.0, intensity=(0.42 if active else 0.34) * glow_scale * alpha,
		)


def draw_crosshair_glyph(draw_list, cx: float, cy: float, size: float, col: int) -> None:
	'''Target / Crosshair glyph.'''
	radius = size * 0.42
	draw_list.add_circle((cx, cy), radius, col, 16, 1.4)
	tick = size * 0.22
	draw_list.add_line((cx - radius - tick, cy), (cx - radius + 1, cy), col, 1.4)
	draw_list.add_line((cx + radius - 1, cy), (cx + radius + tick, cy), col, 1.4)
	draw_list.add_line((cx, cy - radius - tick), (cx, cy - radius + 1), col, 1.4)
	draw_list.add_line((cx, cy + radius - 1), (cx, cy + radius + tick), col, 1.4)


def draw_skull_glyph(draw_list, cx: float, cy: float, size: float, col: int) -> None:
	'''Kill / Skull glyph.'''
	radius = size * 0.34
	head_cy = cy - size * 0.06
	draw_list.add_circle_filled((cx, head_cy), radius, col, 16)
	jaw_width = size * 0.36
	jaw_height = size * 0.32
	draw_list.add_rect_filled((cx - jaw_width * 0.5, head_cy + radius * 0.2), (cx + jaw_width * 0.5, head_cy + radius * 0.2 + jaw_height), col, 2.0)
	eye_color = color_u32((0.0, 0.0, 0.0, 0.95))
	eye_offset = radius * 0.45
	eye_radius = max(1.2, radius * 0.25)
	draw_list.add_circle_filled((cx - eye_offset, head_cy), eye_radius, eye_color, 10)
	draw_list.add_circle_filled((cx + eye_offset, head_cy), eye_radius, eye_color, 10)


def draw_bomb_glyph(draw_list, cx: float, cy: float, size: float, col: int) -> None:
	'''Bomb / C4 glyph.'''
	radius = size * 0.36
	body_cy = cy + size * 0.08
	draw_list.add_circle_filled((cx, body_cy), radius, col, 16)
	cap_width = size * 0.28
	cap_height = size * 0.16
	draw_list.add_rect_filled((cx - cap_width * 0.5, body_cy - radius - cap_height * 0.6), (cx + cap_width * 0.5, body_cy - radius + cap_height * 0.4), col, 1.5)
	fuse_x = cx + cap_width * 0.25
	fuse_y = body_cy - radius - cap_height * 0.6
	draw_list.add_line((fuse_x, fuse_y), (fuse_x + size * 0.15, fuse_y - size * 0.18), col, 1.4)


def draw_bolt_glyph(draw_list, cx: float, cy: float, size: float, col: int) -> None:
	'''Lightning bolt glyph for Triggerbot.'''
	half = size * 0.46
	top_point = (cx + half * 0.3, cy - half)
	left_point = (cx - half * 0.4, cy + half * 0.05)
	right_point = (cx + half * 0.1, cy + half * 0.05)
	bottom_point = (cx - half * 0.3, cy + half)
	draw_list.add_line(top_point, left_point, col, 2.0)
	draw_list.add_line(left_point, right_point, col, 2.0)
	draw_list.add_line(right_point, bottom_point, col, 2.0)


def draw_bunny_glyph(draw_list, cx: float, cy: float, size: float, col: int) -> None:
	'''Rabbit ears / Jump glyph for Bhop.'''
	half = size * 0.45
	ear_width = half * 0.5
	draw_list.add_line((cx - ear_width, cy + half * 0.5), (cx - ear_width * 1.3, cy - half), col, 1.8)
	draw_list.add_line((cx + ear_width, cy + half * 0.5), (cx + ear_width * 1.3, cy - half), col, 1.8)
	draw_list.add_circle_filled((cx, cy + half * 0.3), half * 0.42, col, 12)


def draw_eye_glyph(draw_list, cx: float, cy: float, size: float, col: int) -> None:
	'''Eye / Wallhack glyph.'''
	half_width = size * 0.48
	half_height = size * 0.28
	draw_list.add_line((cx - half_width, cy), (cx, cy - half_height), col, 1.4)
	draw_list.add_line((cx, cy - half_height), (cx + half_width, cy), col, 1.4)
	draw_list.add_line((cx + half_width, cy), (cx, cy + half_height), col, 1.4)
	draw_list.add_line((cx, cy + half_height), (cx - half_width, cy), col, 1.4)
	draw_list.add_circle_filled((cx, cy), size * 0.15, col, 10)


def draw_radar_glyph(draw_list, cx: float, cy: float, size: float, col: int) -> None:
	'''Radar dish glyph.'''
	radius = size * 0.42
	draw_list.add_circle((cx, cy), radius, col, 16, 1.4)
	draw_list.add_line((cx, cy), (cx + radius * 0.707, cy - radius * 0.707), col, 1.6)
	draw_list.add_circle_filled((cx, cy), 1.6, col, 8)


def draw_shield_glyph(draw_list, cx: float, cy: float, size: float, col: int) -> None:
	'''Shield / Awareness glyph.'''
	half_width = size * 0.40
	half_height = size * 0.44
	top_y = cy - half_height * 0.8
	mid_y = cy + half_height * 0.1
	bottom_y = cy + half_height * 0.9
	draw_list.add_line((cx - half_width, top_y), (cx + half_width, top_y), col, 1.6)
	draw_list.add_line((cx - half_width, top_y), (cx - half_width, mid_y), col, 1.6)
	draw_list.add_line((cx + half_width, top_y), (cx + half_width, mid_y), col, 1.6)
	draw_list.add_line((cx - half_width, mid_y), (cx, bottom_y), col, 1.6)
	draw_list.add_line((cx + half_width, mid_y), (cx, bottom_y), col, 1.6)


def draw_wrench_glyph(draw_list, cx: float, cy: float, size: float, col: int) -> None:
	'''Wrench / Update offsets glyph.'''
	half = size * 0.42
	draw_list.add_line((cx - half, cy + half), (cx + half * 0.2, cy - half * 0.2), col, 2.0)
	draw_list.add_circle((cx + half * 0.4, cy - half * 0.4), half * 0.45, col, 12, 1.6)


def draw_user_glyph(draw_list, cx: float, cy: float, size: float, col: int) -> None:
	'''User / Spectator profile glyph.'''
	radius = size * 0.24
	head_cy = cy - size * 0.16
	draw_list.add_circle_filled((cx, head_cy), radius, col, 12)
	shoulder_width = size * 0.42
	shoulder_y = cy + size * 0.22
	draw_list.add_rect_filled((cx - shoulder_width, shoulder_y), (cx + shoulder_width, shoulder_y + size * 0.20), col, 3.0)


def draw_target_glyph(draw_list, cx: float, cy: float, size: float, col: int) -> None:
	'''Target / Bullseye glyph for Min Damage.'''
	radius = size * 0.42
	draw_list.add_circle((cx, cy), radius, col, 16, 1.4)
	draw_list.add_circle_filled((cx, cy), size * 0.16, col, 8)


def draw_arrows_glyph(draw_list, cx: float, cy: float, size: float, col: int) -> None:
	'''Opposing horizontal arrows for Freestanding (FS).'''
	half = size * 0.42
	draw_list.add_line((cx + half * 0.8, cy - half * 0.35), (cx - half * 0.8, cy - half * 0.35), col, 1.4)
	draw_list.add_line((cx - half * 0.8, cy - half * 0.35), (cx - half * 0.3, cy - half * 0.75), col, 1.4)
	draw_list.add_line((cx - half * 0.8, cy + half * 0.35), (cx + half * 0.8, cy + half * 0.35), col, 1.4)
	draw_list.add_line((cx + half * 0.8, cy + half * 0.35), (cx + half * 0.3, cy + half * 0.75), col, 1.4)


def draw_signal_glyph(draw_list, cx: float, cy: float, size: float, col: int) -> None:
	'''Signal / WiFi / Ping bars glyph.'''
	bar_width = 2.0
	half = size * 0.40
	draw_list.add_rect_filled((cx - half * 0.9, cy + half * 0.2), (cx - half * 0.9 + bar_width, cy + half), col, 1.0)
	draw_list.add_rect_filled((cx - half * 0.1, cy - half * 0.2), (cx - half * 0.1 + bar_width, cy + half), col, 1.0)
	draw_list.add_rect_filled((cx + half * 0.7, cy - half * 0.8), (cx + half * 0.7 + bar_width, cy + half), col, 1.0)


def draw_speedo_glyph(draw_list, cx: float, cy: float, size: float, col: int) -> None:
	'''Speedometer / Gauge glyph for FPS counter.'''
	radius = size * 0.42
	draw_list.add_circle((cx, cy), radius, col, 16, 1.3)
	draw_list.add_line((cx, cy), (cx + radius * 0.5, cy - radius * 0.4), col, 1.5)
	draw_list.add_circle_filled((cx, cy), 1.5, col, 8)


def draw_hitmark_glyph(draw_list, cx: float, cy: float, size: float, col: int) -> None:
	'''Hitmark 4-tick diagonal glyph matching cheat hitmarker.'''
	gap = size * 0.16
	arm = size * 0.42
	draw_list.add_line((cx - arm, cy - arm), (cx - gap, cy - gap), col, 1.4)
	draw_list.add_line((cx + gap, cy - gap), (cx + arm, cy - arm), col, 1.4)
	draw_list.add_line((cx - arm, cy + arm), (cx - gap, cy + gap), col, 1.4)
	draw_list.add_line((cx + gap, cy + gap), (cx + arm, cy + arm), col, 1.4)


def draw_ghost_glyph(draw_list, cx: float, cy: float, size: float, col: int) -> None:
	'''Ghost / Ninja mask glyph for HS.'''
	radius = size * 0.36
	draw_list.add_circle((cx, cy), radius, col, 14, 1.3)
	draw_list.add_line((cx - radius * 0.55, cy - 0.5), (cx - radius * 0.15, cy - 0.5), col, 1.3)
	draw_list.add_line((cx + radius * 0.15, cy - 0.5), (cx + radius * 0.55, cy - 0.5), col, 1.3)


def draw_waveform_glyph(draw_list, cx: float, cy: float, size: float, col: int) -> None:
	'''Waveform / ECG line for ping latency.'''
	half = size * 0.42
	points = [
		(cx - half, cy), (cx - half * 0.45, cy), (cx - half * 0.15, cy - half * 0.75),
		(cx + half * 0.15, cy + half * 0.75), (cx + half * 0.45, cy - half * 0.30), (cx + half, cy),
	]
	for point_index in range(len(points) - 1):
		draw_list.add_line(points[point_index], points[point_index + 1], col, 1.3)


def draw_clock_glyph(draw_list, cx: float, cy: float, size: float, col: int) -> None:
	'''Minimal vector clock glyph with circle and hour/minute hands.'''
	radius = size * 0.38
	draw_list.add_circle((cx, cy), radius, col, 16, 1.2)
	draw_list.add_circle_filled((cx, cy), 1.0, col, 8)
	draw_list.add_line((cx, cy), (cx, cy - radius * 0.55), col, 1.2)
	draw_list.add_line((cx, cy), (cx + radius * 0.45, cy), col, 1.2)


def draw_bars_glyph(draw_list, cx: float, cy: float, size: float, col: int) -> None:
	'''3 ascending vertical bars glyph for FPS.'''
	bar_width = 2.0
	half = size * 0.38
	draw_list.add_rect_filled((cx - half * 0.85, cy + half * 0.25), (cx - half * 0.85 + bar_width, cy + half), col, 0.5)
	draw_list.add_rect_filled((cx - half * 0.05, cy - half * 0.25), (cx - half * 0.05 + bar_width, cy + half), col, 0.5)
	draw_list.add_rect_filled((cx + half * 0.75, cy - half * 0.85), (cx + half * 0.75 + bar_width, cy + half), col, 0.5)


def draw_wifi_waves_glyph(draw_list, cx: float, cy: float, size: float, col: int) -> None:
	'''Curved radio/wifi signal arcs for ping latency.'''
	dot_y = cy + size * 0.32
	draw_list.add_circle_filled((cx, dot_y), 1.2, col, 8)
	draw_list.add_line((cx - size * 0.24, dot_y - size * 0.32), (cx + size * 0.24, dot_y - size * 0.32), col, 1.3)
	draw_list.add_line((cx - size * 0.42, dot_y - size * 0.62), (cx + size * 0.42, dot_y - size * 0.62), col, 1.3)


GLYPH_REGISTRY: dict[str, object] = {}




# These keys and their order are persisted event-style choices
EVENT_STYLE_DEFAULTS: dict[str, str] = {
	"hit": "badge_hit", "miss": "badge_miss", "kill": "badge_kill", "headshot": "badge_headshot", "knife": "weapon_knife", "zeus": "weapon_taser",
	"grenade_throw": "arrows", "triggerbot": "badge_hit", "bhop": "bunny", "bomb_plant": "event_bomb_plant", "bomb_defusing": "event_bomb_defusing",
	"bomb_defused": "event_bomb_defused", "bomb_exploded": "event_bomb_exploded",
	"spectator_new": "user", "rear_threat": "shield", "offsets_updated": "wrench", "hotkey_toggled": "bolt", "config_loaded": "wrench",
}
GLYPH_CHOICES: tuple[str, ...] = (
	"badge_hit", "badge_miss", "badge_kill", "badge_headshot", "hitmark", "skull", "bomb", "bolt", "bunny", "eye", "radar", "shield", "wrench", "user",
	"target", "arrows", "signal", "clock", "speedo", "ghost", "waveform", "bars", "wifi", "crosshair",
) + PUBLIC_FONT_ICONS
GLYPH_REGISTRY.update({
	"hitmark": draw_hitmark_glyph, "skull": draw_skull_glyph, "bomb": draw_bomb_glyph, "bolt": draw_bolt_glyph, "bunny": draw_bunny_glyph,
	"eye": draw_eye_glyph, "radar": draw_radar_glyph, "shield": draw_shield_glyph, "wrench": draw_wrench_glyph, "user": draw_user_glyph,
	"target": draw_target_glyph, "arrows": draw_arrows_glyph, "signal": draw_signal_glyph, "clock": draw_clock_glyph, "speedo": draw_speedo_glyph,
	"ghost": draw_ghost_glyph, "waveform": draw_waveform_glyph, "bars": draw_bars_glyph, "wifi": draw_wifi_waves_glyph, "crosshair": draw_crosshair_glyph,
})


class EventStyle(NamedTuple):
	enabled: bool
	glyph: str
	label: str
	color: tuple[float, float, float, float] | None


def get_event_style(config, event_type) -> "EventStyle":
	'''Resolve per-event style from event_visuals.ev_<type>_* keys.'''
	event_visuals = getattr(config, "event_visuals", None)
	event_key = getattr(event_type, "value", str(event_type))
	default_glyph = EVENT_STYLE_DEFAULTS.get(event_key, "hitmark")
	if event_visuals is None:
		return EventStyle(True, default_glyph, "", None)
	enabled = bool(getattr(event_visuals, f"ev_{event_key}_enabled", True))
	glyph = str(getattr(event_visuals, f"ev_{event_key}_glyph", "") or default_glyph)
	if glyph not in GLYPH_CHOICES:
		glyph = default_glyph
	label = str(getattr(event_visuals, f"ev_{event_key}_label", "") or "").strip()
	raw_color = getattr(event_visuals, f"ev_{event_key}_color", None)
	color = parse_color_rgba(raw_color) if raw_color else None
	return EventStyle(enabled, glyph, label, color)


def draw_named_glyph(draw_list, name: str, cx: float, cy: float, size: float, col: int) -> None:
	'''Draw a registry glyph or any of the four combat badges.'''
	alpha = ((col >> 24) & 0xFF) / 255.0
	if name in PUBLIC_FONT_ICONS and draw_font_icon(draw_list, name, cx, cy, col, size, size):
		return
	draw_badge = _BADGE_DRAWERS.get(name)
	if draw_badge is not None:
		draw_badge(draw_list, cx, cy, radius=size * 0.75, alpha=alpha)
		return
	GLYPH_REGISTRY.get(name, draw_hitmark_glyph)(draw_list, cx, cy, size, col)


def draw_event_glyph(draw_list, event_type: EventType, cx: float, cy: float, size: float, color_packed: int) -> None:
	'''Render the default glyph for the given event type (no config overrides).'''
	event_key = getattr(event_type, "value", str(event_type))
	draw_named_glyph(draw_list, EVENT_STYLE_DEFAULTS.get(event_key, "hitmark"), cx, cy, size, color_packed)


_BADGE_BASE_RADIUS = 9.0
_CARD_HEIGHT = 30.0
_CARD_PAD_LEFT = 11.0
_CARD_TEXT_GAP = 8.0
_CARD_TRAILING_PAD = 10.0
_CARD_GAP = 6.0


def draw_ascend_kill_badge(
	draw_list, cx: float, cy: float, radius: float = _BADGE_BASE_RADIUS, alpha: float = 1.0, rgb: tuple[float, float, float] | None = None,
) -> None:
	'''Solid circle with a clean silhouetted skull — kill card badge.'''
	base_rgb = rgb if rgb is not None else (1.0, 1.0, 1.0)
	background_color = color_u32(with_alpha((base_rgb[0], base_rgb[1], base_rgb[2], 1.0), alpha))
	ink_color = color_u32(with_alpha((0.04, 0.04, 0.05, 1.0), alpha))
	draw_list.add_circle_filled((cx, cy), radius, background_color, 28)
	head_radius = radius * 0.52
	head_cy = cy - head_radius * 0.04
	draw_list.add_circle_filled((cx, head_cy), head_radius, ink_color, 20)
	draw_list.add_rect_filled(
		(cx - head_radius * 0.62, head_cy + head_radius * 0.42), (cx + head_radius * 0.62, head_cy + head_radius * 1.08), ink_color, head_radius * 0.22,
	)
	eye_radius = head_radius * 0.26
	draw_list.add_circle_filled((cx - head_radius * 0.42, head_cy - head_radius * 0.06), eye_radius, background_color, 10)
	draw_list.add_circle_filled((cx + head_radius * 0.42, head_cy - head_radius * 0.06), eye_radius, background_color, 10)


def draw_ascend_headshot_badge(
	draw_list, cx: float, cy: float, radius: float = _BADGE_BASE_RADIUS, alpha: float = 1.0, rgb: tuple[float, float, float] | None = None,
) -> None:
	'''Solid circle with a crisp, perfectly centred crosshair (headshot).'''
	base_rgb = rgb if rgb is not None else (1.0, 1.0, 1.0)
	background_color = color_u32(with_alpha((base_rgb[0], base_rgb[1], base_rgb[2], 1.0), alpha))
	ink_color = color_u32(with_alpha((0.04, 0.04, 0.05, 1.0), alpha))
	font_size = radius * 2.0 / 0.9
	if draw_font_icon(draw_list, "event_disc", cx, cy, background_color, em_size=font_size):
		draw_font_icon(draw_list, "event_headshot_ink", cx, cy, ink_color, em_size=font_size)
		return
	draw_list.add_circle_filled((cx, cy), radius, background_color, 28)
	tick = radius * 0.60
	arm = radius * 0.26
	thickness = radius * 0.16
	draw_list.add_line((cx, cy - tick), (cx, cy - arm), ink_color, thickness)
	draw_list.add_line((cx, cy + arm), (cx, cy + tick), ink_color, thickness)
	draw_list.add_line((cx - tick, cy), (cx - arm, cy), ink_color, thickness)
	draw_list.add_line((cx + arm, cy), (cx + tick, cy), ink_color, thickness)
	draw_list.add_circle_filled((cx, cy), radius * 0.10, ink_color, 8)


def draw_ascend_hit_badge(
	draw_list, cx: float, cy: float, radius: float = _BADGE_BASE_RADIUS, alpha: float = 1.0, rgb: tuple[float, float, float] | None = None,
) -> None:
	'''Solid circle (18px) with crisp dark checkmark. Default white, tintable.'''
	base_rgb = rgb if rgb is not None else (1.0, 1.0, 1.0)
	background_color = color_u32(with_alpha((base_rgb[0], base_rgb[1], base_rgb[2], 1.0), alpha))
	check_color = color_u32(with_alpha((0.04, 0.04, 0.05, 1.0), alpha))
	draw_list.add_circle_filled((cx, cy), radius, background_color, 24)
	scale = radius / _BADGE_BASE_RADIUS
	draw_list.add_line((cx - 3.8 * scale, cy), (cx - scale, cy + 2.9 * scale), check_color, 1.8 * scale)
	draw_list.add_line((cx - scale, cy + 2.9 * scale), (cx + 3.8 * scale, cy - 2.9 * scale), check_color, 1.8 * scale)


def draw_ascend_miss_badge(
	draw_list, cx: float, cy: float, radius: float = _BADGE_BASE_RADIUS, alpha: float = 1.0, rgb: tuple[float, float, float] | None = None,
) -> None:
	'''Solid circle (18px) with a crisp opaque cross. Default white, tintable.'''
	base_rgb = rgb if rgb is not None else (1.0, 1.0, 1.0)
	background_color = color_u32(with_alpha((base_rgb[0], base_rgb[1], base_rgb[2], 1.0), alpha))
	cross_color = color_u32(with_alpha((0.04, 0.04, 0.05, 1.0), alpha))
	draw_list.add_circle_filled((cx, cy), radius, background_color, 24)
	arm_offset = radius * (2.8 / _BADGE_BASE_RADIUS)
	draw_list.add_line((cx - arm_offset, cy - arm_offset), (cx + arm_offset, cy + arm_offset), cross_color, radius * (1.6 / _BADGE_BASE_RADIUS))
	draw_list.add_line((cx + arm_offset, cy - arm_offset), (cx - arm_offset, cy + arm_offset), cross_color, radius * (1.6 / _BADGE_BASE_RADIUS))


_BADGE_DRAWERS = {
	"badge_hit": draw_ascend_hit_badge,
	"badge_miss": draw_ascend_miss_badge,
	"badge_kill": draw_ascend_kill_badge,
	"badge_headshot": draw_ascend_headshot_badge,
}


def _apply_label_prefix(detail: str, style: EventStyle | None) -> str:
	'''Replace the leading word of a detail with the configured event label.'''
	if style is None or not style.label:
		return detail
	first_space = detail.find(" ")
	return style.label + detail[first_space:] if first_space > 0 else style.label


def _tokenize_event_detail(detail: str) -> list[tuple[str, bool]]:
	'''Split a detail into (text, bold) chunks; the leading verb stays generic for custom labels.'''
	hit_match = re.match(r"^(\S+\s+)(.*?)(\s+(?:in|near)(?:\s+the)?\s+)(.*?)(\s+for\s+)(\d+)(\s+\(\d+\s+remaining\))?$", detail, re.IGNORECASE)
	if hit_match:
		return [
			(hit_match.group(1), False), (hit_match.group(2), True), (hit_match.group(3), False), (hit_match.group(4), True),
			(hit_match.group(5), False), (hit_match.group(6), True), (hit_match.group(7) or "", False),
		]
	miss_match = re.match(r"^(\S+\s+)(.*?)(\s+due to\s+)(.*)$", detail, re.IGNORECASE)
	if miss_match:
		return [(miss_match.group(1), False), (miss_match.group(2), True), (miss_match.group(3), False), (miss_match.group(4), True)]
	return [(detail, True)]


@lru_cache(maxsize=32)
def _event_glyph_geometry(name: str) -> tuple:
	'''Cache visible primitive bounds so asymmetric glyphs share the same event-card centre.'''
	commands = []
	class Recorder:
		def __getattr__(self, method):
			return lambda *args: commands.append((method, args))
	draw_glyph = GLYPH_REGISTRY.get(name, draw_hitmark_glyph)
	draw_glyph(Recorder(), 0.0, 0.0, 12.0, 0)
	left = top = float('inf')
	right = bottom = float('-inf')
	for method, args in commands:
		if method in ("add_circle", "add_circle_filled"):
			point, radius = args[0], args[1]
			padding = args[4] * 0.5 if method == "add_circle" else 0.0
			low, high = (point[0] - radius, point[1] - radius), (point[0] + radius, point[1] + radius)
		else:
			low = (min(args[0][0], args[1][0]), min(args[0][1], args[1][1]))
			high = (max(args[0][0], args[1][0]), max(args[0][1], args[1][1]))
			padding = args[3] * 0.5 if method == "add_line" else 0.0
		left, top = min(left, low[0] - padding), min(top, low[1] - padding)
		right, bottom = max(right, high[0] + padding), max(bottom, high[1] + padding)
	return tuple(commands), (left + right) * 0.5, (top + bottom) * 0.5


def _draw_centered_event_glyph(draw_list, name: str, cx: float, cy: float, scale: float, color: int) -> None:
	'''Replay cached glyph geometry; stroke widths and rounding follow the card scale too.'''
	commands, centre_x, centre_y = _event_glyph_geometry(name)
	for method, args in commands:
		primitive_color = color if args[2] == 0 else (args[2] & 0x00FFFFFF) | (int((args[2] >> 24) * (color >> 24) / 255.0) << 24)
		first = (cx + (args[0][0] - centre_x) * scale, cy + (args[0][1] - centre_y) * scale)
		if method in ("add_circle", "add_circle_filled"):
			if method == "add_circle":
				draw_list.add_circle(first, args[1] * scale, primitive_color, args[3], args[4] * scale)
			else:
				draw_list.add_circle_filled(first, args[1] * scale, primitive_color, args[3])
		else:
			second = (cx + (args[1][0] - centre_x) * scale, cy + (args[1][1] - centre_y) * scale)
			getattr(draw_list, method)(first, second, primitive_color, args[3] * scale)


def draw_v_log_card(
	draw_list, x: float, y: float, item: EventNotification, theme: ThemeColors, font=None, font_size: float = 15.0, corner_radius: float = 8.0,
	style: "EventStyle | None" = None, card_scale: float = 1.0, icon_style: HudIconStyle | None = None,
) -> float:
	'''Draw one event card and return its height.'''
	alpha = item.alpha
	if alpha <= 0.01:
		return 0.0

	detail = _apply_label_prefix(item.detail, style)

	slide_x = item.slide_offset
	card_x = x + slide_x
	card_y = y
	card_height = _CARD_HEIGHT * card_scale
	card_radius = corner_radius * card_scale
	pad_left = _CARD_PAD_LEFT * card_scale
	badge_radius = _BADGE_BASE_RADIUS * card_scale
	badge_width = badge_radius * 2.0
	text_gap = _CARD_TEXT_GAP * card_scale
	glyph_name = style.glyph if style is not None else EVENT_STYLE_DEFAULTS.get(getattr(item.event_type, "value", ""), "hitmark")
	weapon_key, weapon_width = event_weapon_layout(item, glyph_name, card_scale)
	weapon_space = weapon_width + text_gap if weapon_key else 0.0
	tokens = _tokenize_event_detail(detail)

	pushed = push_sized(font, font_size)
	measured_tokens: list[tuple[str, bool, float]] = []
	total_text_width = 0.0
	text_height = 0.0
	try:
		for chunk, is_white in tokens:
			chunk_size = text_size(chunk)
			measured_tokens.append((chunk, is_white, chunk_size.x))
			total_text_width += chunk_size.x
			text_height = max(text_height, chunk_size.y)
	finally:
		pop_font(pushed)

	card_width = pad_left + badge_width + text_gap + weapon_space + total_text_width + _CARD_TRAILING_PAD * card_scale
	background_rgba = getattr(theme, "window_bg", (0.043, 0.043, 0.051, 0.85)) if theme else (0.043, 0.043, 0.051, 0.85)
	background_color = color_u32(with_alpha((background_rgba[0], background_rgba[1], background_rgba[2], 1.0), alpha))
	border_color = color_u32(with_alpha((1.0, 1.0, 1.0, 0.055), 0.055 * alpha))
	base_text = getattr(theme, "text", (0.93, 0.93, 0.94, 1.0)) if theme else (0.93, 0.93, 0.94, 1.0)
	base_dim = getattr(theme, "text_dim", (0.54, 0.54, 0.57, 1.0)) if theme else (0.54, 0.54, 0.57, 1.0)
	bright_text_color = color_u32((*base_text[:3], alpha))
	dim_text_color = color_u32((*base_dim[:3], alpha))
	if icon_style is not None:
		if icon_style.text is not None:
			text_rgba = icon_style.text
			dim_rgba = icon_dim_color(icon_style, text_rgba)
			bright_text_color = color_u32((*text_rgba[:3], alpha))
			dim_text_color = color_u32((*dim_rgba[:3], alpha))

	draw_ambient_shadow(draw_list, card_x, card_y, card_width, card_height, card_radius, layers=3, spread=3.5, opacity=0.45 * alpha)
	draw_list.add_rect_filled((card_x, card_y), (card_x + card_width, card_y + card_height), background_color, card_radius)
	if icon_style is not None:
		draw_icon_outline(draw_list, card_x, card_y, card_width, card_height, card_radius, icon_style, active=True, glow_scale=0.8, alpha=alpha)
	else:
		draw_list.add_rect((card_x, card_y), (card_x + card_width, card_y + card_height), border_color, rounding=card_radius, thickness=1.0)

	badge_cx = card_x + pad_left + badge_radius
	badge_cy = card_y + card_height * 0.5
	glyph_rgb = icon_style.text if icon_style is not None and icon_style.text is not None else base_text
	glyph_ink = color_u32((*glyph_rgb[:3], alpha))
	tint_rgb = (style.color[0], style.color[1], style.color[2]) if (style is not None and style.color) else None
	if icon_style is not None and icon_style.text is not None:
		tint_rgb = (icon_style.text[0], icon_style.text[1], icon_style.text[2])
	if glyph_name == "badge_hit":
		draw_ascend_hit_badge(draw_list, badge_cx, badge_cy, radius=badge_radius, alpha=alpha, rgb=tint_rgb)
	elif glyph_name == "badge_miss":
		draw_ascend_miss_badge(draw_list, badge_cx, badge_cy, radius=badge_radius, alpha=alpha, rgb=tint_rgb)
	elif glyph_name == "badge_kill":
		draw_ascend_kill_badge(draw_list, badge_cx, badge_cy, radius=badge_radius, alpha=alpha, rgb=tint_rgb)
	elif glyph_name == "badge_headshot":
		draw_ascend_headshot_badge(draw_list, badge_cx, badge_cy, radius=badge_radius, alpha=alpha, rgb=tint_rgb)
	elif glyph_name in PUBLIC_FONT_ICONS:
		draw_font_icon(draw_list, glyph_name, badge_cx, badge_cy, glyph_ink, badge_width, badge_width)
	else:
		# Thin line glyphs retain neutral text colour unless the user explicitly overrides it
		if icon_style is not None and icon_style.text is not None:
			glyph_ink = color_u32((*icon_style.text[:3], alpha))
		else:
			glyph_ink = color_u32((*base_text[:3], alpha))
		_draw_centered_event_glyph(draw_list, glyph_name, badge_cx, badge_cy, card_scale, glyph_ink)
	if weapon_key:
		draw_font_icon(draw_list, weapon_key, badge_cx + badge_radius + text_gap + weapon_width * 0.5,
			badge_cy, glyph_ink, 26.0 * card_scale, 18.0 * card_scale)

	text_x = card_x + pad_left + badge_width + text_gap + weapon_space
	text_y = card_y + (card_height - text_height) * 0.5
	pushed = push_sized(font, font_size)
	try:
		cursor_x = text_x
		for chunk, is_white, chunk_width in measured_tokens:
			text_color = bright_text_color if is_white else dim_text_color
			draw_list.add_text((cursor_x, text_y), text_color, chunk)
			cursor_x += chunk_width
	finally:
		pop_font(pushed)
	return card_height + _CARD_GAP * card_scale
