"""
Legacy ImGui menu with sidebar, cards, controls, and keybind rows.
PySide6 is the primary UI, but this remains the in-overlay fallback when Qt is unavailable or a pure ImGui surface is needed; it mirrors the Qt feature
panels while keeping its own palettes, immediate-mode layout, and context-local fonts.
"""

import ctypes
from time import monotonic
from typing import Callable

from imgui_bundle import imgui

from cs2esp.risk import RiskInput, bind_risk, resolve_risk
from cs2esp.ui import fonts as ui_fonts

try:
	import keyboard as _kb  # type: ignore
except Exception:
	_kb = None


def _hex(s: str, a: float = 1.0) -> imgui.ImVec4:
	'''Parse #rrggbb into an ImVec4 with the given alpha.'''
	s = s.lstrip("#")
	red = int(s[0:2], 16) / 255.0
	green = int(s[2:4], 16) / 255.0
	blue = int(s[4:6], 16) / 255.0
	return imgui.ImVec4(red, green, blue, a)


_PALETTES: dict[str, dict[str, imgui.ImVec4]] = {
	"midnight": {
		"bg_app": _hex("#0a0c12"),
		"bg_sidebar": _hex("#0d1018"),
		"surface_1": _hex("#14171f"),
		"surface_2": _hex("#1a1e28"),
		"border": _hex("#252a36"),
		"accent": _hex("#6ce0ff"),
		"accent_hi": _hex("#9bebff"),
		"accent_glow": _hex("#6ce0ff", 0.27),
		"text": _hex("#e8eaf0"),
		"text_dim": _hex("#8990a3"),
		"safe": _hex("#6cd97c"),
		"warn": _hex("#ffba6c"),
		"danger": _hex("#ff7878"),
	},
	"slate": {
		"bg_app": _hex("#1d2230"),
		"bg_sidebar": _hex("#222838"),
		"surface_1": _hex("#262c3a"),
		"surface_2": _hex("#2e3548"),
		"border": _hex("#3a4357"),
		"accent": _hex("#6ce0ff"),
		"accent_hi": _hex("#9bebff"),
		"accent_glow": _hex("#6ce0ff", 0.22),
		"text": _hex("#eef0f6"),
		"text_dim": _hex("#9aa1b4"),
		"safe": _hex("#6cd97c"),
		"warn": _hex("#ffba6c"),
		"danger": _hex("#ff7878"),
	},
	"daylight": {
		"bg_app": _hex("#eef1f7"),
		"bg_sidebar": _hex("#dde3ee"),
		"surface_1": _hex("#ffffff"),
		"surface_2": _hex("#f3f6fb"),
		"border": _hex("#b9c2d1"),
		"accent": _hex("#006d96"),
		"accent_hi": _hex("#0091c6"),
		"accent_glow": _hex("#006d96", 0.20),
		"text": _hex("#0d111a"),
		"text_dim": _hex("#3f4858"),
		"safe": _hex("#177a2c"),
		"warn": _hex("#a16500"),
		"danger": _hex("#b32226"),
	},
}

_THEME_ORDER = ["midnight", "slate", "daylight"]
_THEME_LABEL = {"midnight": "Midnight", "slate": "Slate", "daylight": "Daylight"}
_THEME_GLYPH = {"midnight": "[N]", "slate": "[S]", "daylight": "[D]"}
_WALLHACK_PRESETS = (
	(False, False, False),
	(True, True, False),
	(True, False, False),
	(False, True, False),
	(False, False, True),
	(True, False, True),
	(False, True, True),
	(True, True, True),
)

# Style colour -> (palette key, tint key, tint amount); insertion order is the _apply_theme order
_THEME_COLOR_FIELDS: dict[imgui.Col_, tuple[str, str, float]] = {
	imgui.Col_.window_bg: ("bg_app", "", 0.0),
	imgui.Col_.child_bg: ("surface_1", "", 0.0),
	imgui.Col_.popup_bg: ("surface_1", "", 0.0),
	imgui.Col_.menu_bar_bg: ("surface_1", "", 0.0),
	imgui.Col_.title_bg: ("bg_sidebar", "", 0.0),
	imgui.Col_.title_bg_active: ("bg_sidebar", "", 0.0),
	imgui.Col_.title_bg_collapsed: ("bg_sidebar", "", 0.0),
	imgui.Col_.scrollbar_bg: ("bg_sidebar", "", 0.0),
	imgui.Col_.scrollbar_grab: ("border", "", 0.0),
	imgui.Col_.scrollbar_grab_hovered: ("text_dim", "", 0.0),
	imgui.Col_.scrollbar_grab_active: ("accent", "", 0.0),
	imgui.Col_.border: ("border", "", 0.0),
	imgui.Col_.separator: ("border", "", 0.0),
	imgui.Col_.separator_hovered: ("accent", "", 0.0),
	imgui.Col_.separator_active: ("accent_hi", "", 0.0),
	imgui.Col_.text: ("text", "", 0.0),
	imgui.Col_.text_disabled: ("text_dim", "", 0.0),
	imgui.Col_.frame_bg: ("surface_2", "", 0.0),
	imgui.Col_.frame_bg_hovered: ("surface_2", "accent", 0.18),
	imgui.Col_.frame_bg_active: ("surface_2", "accent", 0.28),
	imgui.Col_.button: ("surface_2", "", 0.0),
	imgui.Col_.button_hovered: ("surface_2", "accent", 0.30),
	imgui.Col_.button_active: ("accent", "", 0.0),
	imgui.Col_.header: ("surface_1", "accent", 0.20),
	imgui.Col_.header_hovered: ("surface_1", "accent", 0.35),
	imgui.Col_.header_active: ("surface_1", "accent", 0.45),
	imgui.Col_.slider_grab: ("accent", "", 0.0),
	imgui.Col_.slider_grab_active: ("accent_hi", "", 0.0),
	imgui.Col_.check_mark: ("accent", "", 0.0),
	imgui.Col_.tab: ("surface_1", "", 0.0),
	imgui.Col_.tab_hovered: ("surface_1", "accent", 0.30),
	imgui.Col_.tab_selected: ("surface_2", "", 0.0),
	imgui.Col_.tab_dimmed: ("surface_1", "", 0.0),
	imgui.Col_.tab_dimmed_selected: ("surface_2", "", 0.0),
	imgui.Col_.nav_cursor: ("accent", "", 0.0),
	imgui.Col_.nav_windowing_highlight: ("accent", "", 0.0),
	imgui.Col_.drag_drop_target: ("accent", "", 0.0),
}

_PUSHED_THEME_COLORS: tuple[imgui.Col_, ...] = (
	imgui.Col_.text, imgui.Col_.text_disabled, imgui.Col_.window_bg, imgui.Col_.child_bg, imgui.Col_.popup_bg,
	imgui.Col_.border, imgui.Col_.separator, imgui.Col_.frame_bg, imgui.Col_.frame_bg_hovered, imgui.Col_.frame_bg_active,
	imgui.Col_.button, imgui.Col_.button_hovered, imgui.Col_.button_active, imgui.Col_.slider_grab, imgui.Col_.slider_grab_active,
	imgui.Col_.check_mark, imgui.Col_.header, imgui.Col_.header_hovered, imgui.Col_.header_active, imgui.Col_.scrollbar_bg,
	imgui.Col_.scrollbar_grab, imgui.Col_.scrollbar_grab_hovered, imgui.Col_.scrollbar_grab_active,
)


def _safe_combo(label: str, current_idx: int, items: list[str]) -> tuple[bool, int]:
	'''Normalise imgui.combo to (changed, new_idx).'''
	result = imgui.combo(label, int(current_idx), items)
	if isinstance(result, tuple) and len(result) == 2:
		return bool(result[0]), int(result[1])
	return (int(result) != int(current_idx)), int(result)


class Menu:
	"""Top-level menu UI: a pure view over self.config."""

	_CATEGORIES: tuple[tuple[str, str, str], ...] = (
		("keybinds", "Keybinds", "_panel_keybinds"),
		("esp", "ESP", "_panel_esp"),
		("radar", "Radar", "_panel_radar"),
		("awareness", "Awareness", "_panel_awareness"),
		("combat", "Combat", "_panel_combat"),
		("anti_detection", "Anti-Detection", "_panel_anti_detection"),
		("settings", "Settings", "_panel_settings"),
	)

	_SIDEBAR_W = 220.0
	_TOPSTRIP_H = 36.0
	_BOTSTRIP_H = 28.0

	def __init__(self, config, overlay, cs2_hwnd: int = 0) -> None:
		self.config = config
		self.overlay = overlay
		self.cs2_hwnd = int(cs2_hwnd or 0)

		self.visible: bool = bool(getattr(config.general, "start_menu_visible", False))

		self._capturing: str | None = None
		self._capture_started_at: float = 0.0
		self._prev_keys_down: set[str] = set()

		self._active_category: str = "keybinds"
		self._hud_component: str = ""
		self._hud_editor_revision: int = 0
		self._hud_editor_scroll_pending: bool = False
		self._show_advanced: dict[str, bool] = {}
		self._show_why: dict[str, bool] = {}

		self._ui_scale: float = self._compute_ui_scale()

		name = str(getattr(self.config.general, "ui_theme", "midnight") or "midnight")
		self._theme_name = name if name in _PALETTES else "midnight"
		self._advanced_default = bool(getattr(self.config.general, "menu_show_advanced_default", False))
		self._apply_theme()

	def select_hud_component(self, widget_id: str) -> bool:
		'''Reveal the existing capsule fields without toggling a visible menu or mutating HUD settings.'''
		components = ("fps_counter", "event_feed", "hotkeys", "status_pills", "spectators", "c4_capsule")
		event_visuals = getattr(self.config, "event_visuals", None)
		if widget_id not in components or event_visuals is None:
			return False
		if bool(getattr(event_visuals, "edit_lock", True)) and not bool(getattr(event_visuals, "edit_mode", False)):
			return False
		self._hud_component = widget_id
		self._active_category = "esp"
		self._active_capsule_group_idx = components.index(widget_id)
		self._hud_editor_scroll_pending = True
		if not self.visible:
			self.visible = True
			try:
				self.overlay.set_click_through(bool(getattr(self.config.general, "menu_external_window", False)))
			except Exception:
				pass
		return True

	def sync_hud_editor(self, selection: dict | None) -> bool:
		'''Consume one allowed, released HUD selection revision; repeated frame notifications leave focus alone.'''
		if not isinstance(selection, dict) or not selection.get("allowed") or selection.get("interacting"):
			return False
		try:
			revision = int(selection.get("revision", 0))
		except (TypeError, ValueError):
			return False
		if revision <= self._hud_editor_revision:
			return False
		if not self.select_hud_component(str(selection.get("widget_id", ""))):
			return False
		self._hud_editor_revision = revision
		return True

	def _compute_ui_scale(self) -> float:
		'''Pick a UI scale from monitor height unless the user override wins; 1080p -> 1.55, 1440p -> 1.30, 4K -> 1.10.'''
		try:
			user_scale = float(getattr(self.config.general, "ui_scale", 0.0) or 0.0)
			if user_scale > 0.05:
				return user_scale
			try:
				input_state = imgui.get_io()
				screen_height = float(input_state.display_size.y)
			except Exception:
				screen_height = 0.0
			if screen_height <= 0:
				try:
					screen_height = float(ctypes.windll.user32.GetSystemMetrics(1))
				except Exception:
					screen_height = 1080.0
			if screen_height >= 2160 - 100:
				return 1.10
			if screen_height >= 1440 - 100:
				return 1.30
			return 1.55
		except Exception:
			return 1.55

	def _theme(self) -> dict[str, imgui.ImVec4]:
		'''Return the active palette dict.'''
		return _PALETTES.get(self._theme_name, _PALETTES["midnight"])

	def _apply_theme(self) -> None:
		'''Paint the active palette onto the current ImGui context; scale_all_sizes compounds, so apply it once per context.'''
		try:
			try:
				ctx = imgui.get_current_context()
				ctx_id = id(ctx)
			except Exception:
				ctx_id = 0

			if not hasattr(self, "_scaled_ctxs"):
				self._scaled_ctxs: set[int] = set()

			style = imgui.get_style()
			if ctx_id and ctx_id not in self._scaled_ctxs:
				style.scale_all_sizes(min(1.5, self._ui_scale))
				self._scaled_ctxs.add(ctx_id)

			palette = self._theme()

			def _set(col_enum, vec):
				try:
					style.set_color_(col_enum.value, vec)
				except Exception:
					pass

			for col_enum in _THEME_COLOR_FIELDS:
				_set(col_enum, self._theme_color(palette, col_enum))

			style.window_rounding = 8.0
			style.child_rounding = 10.0
			style.frame_rounding = 6.0
			style.popup_rounding = 8.0
			style.tab_rounding = 6.0
			style.grab_rounding = 6.0
			style.scrollbar_rounding = 6.0
			style.window_border_size = 1.0
			style.child_border_size = 1.0
			style.frame_border_size = 0.0
			style.frame_padding = imgui.ImVec2(8, 5)
			style.item_spacing = imgui.ImVec2(8, 6)
			style.item_inner_spacing = imgui.ImVec2(6, 4)
			style.window_padding = imgui.ImVec2(10, 10)
		except Exception as e:
			print(f"[menu] _apply_theme failed: {e!r}")

	@staticmethod
	def _mix(a: imgui.ImVec4, b: imgui.ImVec4, t: float) -> imgui.ImVec4:
		'''Linear blend two ImVec4 colours, ``t=0`` -> a, ``t=1`` -> b.'''
		return imgui.ImVec4(a.x + (b.x - a.x) * t, a.y + (b.y - a.y) * t, a.z + (b.z - a.z) * t, a.w + (b.w - a.w) * t)

	def _theme_color(self, palette: dict[str, imgui.ImVec4], col_enum: imgui.Col_) -> imgui.ImVec4:
		'''Resolve one style colour from the active palette.'''
		base_key, tint_key, amount = _THEME_COLOR_FIELDS[col_enum]
		if not tint_key:
			return palette[base_key]
		return self._mix(palette[base_key], palette[tint_key], amount)

	def _set_theme(self, name: str) -> None:
		'''Switch to a named palette and persist the choice to config.'''
		if name not in _PALETTES:
			return
		self._theme_name = name
		try:
			self.config.general.ui_theme = name
		except Exception:
			pass
		self._apply_theme()

	def invalidate_current_context_state(self) -> None:
		'''Forget one-shot style state after the overlay context is rebuilt.'''
		try:
			context_id = id(imgui.get_current_context())
		except Exception:
			return
		if hasattr(self, "_scaled_ctxs"):
			self._scaled_ctxs.discard(context_id)

	def _push_theme_colors(self) -> int:
		'''Push the active palette as style overrides; return the count for _pop_theme_colors.'''
		palette = self._theme()
		pushes = [(col_enum, self._theme_color(palette, col_enum)) for col_enum in _PUSHED_THEME_COLORS]
		color_count = 0
		for col, vec in pushes:
			try:
				imgui.push_style_color(col.value, vec)
				color_count += 1
			except Exception:
				pass
		return color_count

	def _pop_theme_colors(self, n: int) -> None:
		if n > 0:
			try:
				imgui.pop_style_color(n)
			except Exception:
				pass

	def _push_font_kind(self, kind: str) -> bool:
		'''Push the body / h2 / h1 font for the current ImGui context; feed the result to _pop_font_kind.'''
		cached = ui_fonts.get_fonts_for_current_context()
		if cached is None:
			return False
		font_ptr = {"body": cached.body, "h2": cached.h2, "h1": cached.h1}.get(kind)
		return ui_fonts.push(font_ptr)

	def _pop_font_kind(self, pushed: bool) -> None:
		ui_fonts.pop(pushed)

	def _h1(self, text: str) -> None:
		'''Big, bold panel-level title (~28 px).'''
		palette = self._theme()
		pushed = self._push_font_kind("h1")
		imgui.push_style_color(imgui.Col_.text.value, palette["text"])
		imgui.text(text)
		imgui.pop_style_color()
		self._pop_font_kind(pushed)
		try:
			avail = imgui.get_content_region_avail().x
			position = imgui.get_cursor_screen_pos()
			draw_list = imgui.get_window_draw_list()
			draw_list.add_rect_filled(
				imgui.ImVec2(position.x, position.y - 4.0),
				imgui.ImVec2(position.x + min(avail, 96.0), position.y - 2.0),
				imgui.color_convert_float4_to_u32(palette["accent"]),
			)
		except Exception:
			pass
		imgui.dummy(imgui.ImVec2(1, 6))

	def _h2(self, text: str) -> None:
		'''Section title inside a panel (~22 px, bold).'''
		palette = self._theme()
		imgui.dummy(imgui.ImVec2(1, 4))
		pushed = self._push_font_kind("h2")
		imgui.push_style_color(imgui.Col_.text.value, palette["accent_hi"])
		imgui.text(text)
		imgui.pop_style_color()
		self._pop_font_kind(pushed)
		imgui.dummy(imgui.ImVec2(1, 2))

	def _body(self, text: str, *, dim: bool = False) -> None:
		'''Plain body text. ``dim=True`` uses ``text_dim`` for tertiary copy.'''
		palette = self._theme()
		pushed = self._push_font_kind("body")
		col = palette["text_dim"] if dim else palette["text"]
		imgui.push_style_color(imgui.Col_.text.value, col)
		imgui.text_wrapped(text)
		imgui.pop_style_color()
		self._pop_font_kind(pushed)

	def toggle(self) -> None:
		'''Flip the menu's visibility. Called by the global hotkey.'''
		new_state = not self.visible
		if new_state:
			self.visible = True
			use_external = bool(getattr(self.config.general, "menu_external_window", False))
			try:
				self.overlay.set_click_through(True if use_external else False)
			except Exception:
				pass
		else:
			self._on_menu_closed()

	def _on_menu_closed(self) -> None:
		'''Re-enable click-through, cancel any rebind capture, and best-effort focus CS2.'''
		self.visible = False
		self._capturing = None
		try:
			self.overlay.set_click_through(True)
		except Exception:
			pass
		if self.cs2_hwnd:
			try:
				ctypes.windll.user32.SetForegroundWindow(self.cs2_hwnd)
			except Exception:
				pass

	def render(self, in_external_window: bool = False) -> None:
		'''Draw the menu for this frame; only the overlay or the external menu context paints, according to general.menu_external_window.'''
		if not self.visible:
			return

		use_external = bool(getattr(self.config.general, "menu_external_window", False))
		if use_external:
			if in_external_window:
				self._draw_menu_external()
		else:
			if not in_external_window:
				self._draw_menu_embedded()

	def _draw_menu_embedded(self) -> None:
		'''Draw the menu as an ImGui window inside the overlay surface.'''
		self._apply_theme()
		theme_count = self._push_theme_colors()
		body_pushed = self._push_font_kind("body")
		try:
			input_state = imgui.get_io()
			display_size = input_state.display_size
			window_width = max(880.0, min(1100.0, display_size.x * 0.62))
			window_height = max(560.0, min(720.0, display_size.y * 0.78))
			window_x = (display_size.x - window_width) * 0.5
			window_y = (display_size.y - window_height) * 0.5
			imgui.set_next_window_pos(imgui.ImVec2(window_x, window_y), imgui.Cond_.appearing.value)
			imgui.set_next_window_size(imgui.ImVec2(window_width, window_height), imgui.Cond_.appearing.value)

			flags = imgui.WindowFlags_.no_collapse.value | imgui.WindowFlags_.no_scrollbar.value | imgui.WindowFlags_.no_scroll_with_mouse.value
			_, visible = imgui.begin("MS Mesh · CS2 ESP", True, flags)
			if not visible:
				self._on_menu_closed()
			else:
				self._draw_menu_body()
			imgui.end()
		except Exception as e:
			print(f"[menu] embedded render failed: {e!r}")
		finally:
			self._pop_font_kind(body_pushed)
			self._pop_theme_colors(theme_count)

	def _draw_menu_external(self) -> None:
		'''Draw the menu as the entire content of the external window; the OS window owns chrome.'''
		self._apply_theme()
		theme_count = self._push_theme_colors()
		body_pushed = self._push_font_kind("body")
		try:
			input_state = imgui.get_io()
			imgui.set_next_window_pos(imgui.ImVec2(0, 0))
			imgui.set_next_window_size(input_state.display_size)
			flags = (
				imgui.WindowFlags_.no_title_bar.value
				| imgui.WindowFlags_.no_resize.value
				| imgui.WindowFlags_.no_move.value
				| imgui.WindowFlags_.no_collapse.value
				| imgui.WindowFlags_.no_bring_to_front_on_focus.value
				| imgui.WindowFlags_.no_scrollbar.value
				| imgui.WindowFlags_.no_scroll_with_mouse.value
			)
			imgui.begin("##eni_menu_external", None, flags)
			self._draw_menu_body()
			imgui.end()
		except Exception as e:
			print(f"[menu] external render failed: {e!r}")
		finally:
			self._pop_font_kind(body_pushed)
			self._pop_theme_colors(theme_count)

	def _draw_menu_body(self) -> None:
		'''Render the inside of the menu window: chrome and content.'''
		avail = imgui.get_content_region_avail()
		scale = self._ui_scale
		top_height = self._TOPSTRIP_H * scale
		bottom_height = self._BOTSTRIP_H * scale
		sidebar_width = self._SIDEBAR_W * scale
		body_height = avail.y - top_height - bottom_height

		self._draw_top_strip(top_height)
		imgui.dummy(imgui.ImVec2(1, 4))

		imgui.begin_child("##sidebar", imgui.ImVec2(sidebar_width, body_height), imgui.ChildFlags_.borders.value, 0)
		self._draw_sidebar()
		imgui.end_child()

		imgui.same_line()
		imgui.begin_child("##content", imgui.ImVec2(0, body_height), imgui.ChildFlags_.borders.value, 0)
		for key, _label, method_name in self._CATEGORIES:
			if key != self._active_category:
				continue
			panel = getattr(self, method_name, None)
			if panel is not None:
				try:
					panel()
				except Exception as exc:
					imgui.text_colored(self._theme()["danger"], f"panel {key} crashed: {exc!r}")
			break
		else:
			imgui.text("?? unknown panel")
		imgui.end_child()

		self._draw_bottom_strip(bottom_height)

	def _draw_top_strip(self, height: float) -> None:
		'''Compact header with brand, attached-process pill, theme switcher, and master-toggle indicator.'''
		palette = self._theme()
		scale = self._ui_scale
		imgui.push_style_color(imgui.Col_.child_bg.value, palette["bg_sidebar"])
		imgui.push_style_var(imgui.StyleVar_.child_rounding.value, 8.0)
		imgui.push_style_var(imgui.StyleVar_.window_padding.value, imgui.ImVec2(12, 6))
		imgui.begin_child("##topstrip", imgui.ImVec2(0, height), imgui.ChildFlags_.borders.value, imgui.WindowFlags_.no_scrollbar.value)

		imgui.push_style_color(imgui.Col_.text.value, palette["accent"])
		imgui.text("MS Mesh")
		imgui.pop_style_color()
		imgui.same_line()
		imgui.push_style_color(imgui.Col_.text.value, palette["text_dim"])
		imgui.text("· CS2 ESP")
		imgui.pop_style_color()

		imgui.same_line()
		imgui.dummy(imgui.ImVec2(20 * scale, 1))
		imgui.same_line()
		attached = "cs2.exe" if self.cs2_hwnd else "(no attach)"
		has_attachment = bool(self.cs2_hwnd)
		self._badge_pill(("● " + attached) if has_attachment else ("○ " + attached), palette["safe"] if has_attachment else palette["danger"])

		right_w = (170.0 + 130.0) * scale
		imgui.same_line()
		avail_x = imgui.get_content_region_avail().x
		imgui.dummy(imgui.ImVec2(max(0.0, avail_x - right_w), 1))
		imgui.same_line()
		self._draw_theme_switcher()
		imgui.same_line()
		imgui.dummy(imgui.ImVec2(8 * scale, 1))
		imgui.same_line()
		self._draw_master_toggle_pill()

		imgui.end_child()
		imgui.pop_style_var(2)
		imgui.pop_style_color()

		try:
			palette = self._theme()
			avail = imgui.get_content_region_avail()
			position = imgui.get_cursor_screen_pos()
			draw_list = imgui.get_window_draw_list()
			draw_list.add_rect_filled(
				imgui.ImVec2(position.x, position.y - 2.0),
				imgui.ImVec2(position.x + avail.x, position.y - 1.0),
				imgui.color_convert_float4_to_u32(palette["accent_glow"]),
			)
		except Exception:
			pass

	def _badge_pill(self, text: str, bg: imgui.ImVec4, fg: imgui.ImVec4 | None = None) -> None:
		'''Draw an inline rounded-rect pill behind label text; non-clickable, around an invisible button so layout still flows.'''
		palette = self._theme()
		if fg is None:
			fg = _hex("#0a0c12") if bg.x + bg.y + bg.z > 1.5 else palette["text"]
		scale = self._ui_scale
		pad_x = 10 * scale
		pad_y = 3 * scale
		size = imgui.calc_text_size(text)
		pill_width = size.x + pad_x * 2
		pill_height = size.y + pad_y * 2
		position = imgui.get_cursor_screen_pos()
		draw_list = imgui.get_window_draw_list()
		draw_list.add_rect_filled(
			imgui.ImVec2(position.x, position.y), imgui.ImVec2(position.x + pill_width, position.y + pill_height), imgui.color_convert_float4_to_u32(bg), 6.0
		)
		draw_list.add_text(imgui.ImVec2(position.x + pad_x, position.y + pad_y), imgui.color_convert_float4_to_u32(fg), text)
		imgui.dummy(imgui.ImVec2(pill_width, pill_height))

	def _draw_theme_switcher(self) -> None:
		'''Theme pill: click cycles, right-click opens a picker.'''
		palette = self._theme()
		glyph = _THEME_GLYPH[self._theme_name]
		label = _THEME_LABEL[self._theme_name]
		text = f" {glyph} {label} ▾ "

		imgui.push_style_color(imgui.Col_.button.value, palette["surface_2"])
		imgui.push_style_color(imgui.Col_.button_hovered.value, self._mix(palette["surface_2"], palette["accent"], 0.30))
		imgui.push_style_color(imgui.Col_.button_active.value, palette["accent"])
		imgui.push_style_color(imgui.Col_.text.value, palette["accent"])
		clicked = imgui.button(text + "##theme_switcher")
		imgui.pop_style_color(4)

		if imgui.is_item_hovered() and imgui.is_mouse_clicked(1):
			imgui.open_popup("##theme_picker")

		if clicked:
			current = _THEME_ORDER.index(self._theme_name)
			self._set_theme(_THEME_ORDER[(current + 1) % len(_THEME_ORDER)])

		if imgui.begin_popup("##theme_picker"):
			imgui.text("Theme")
			imgui.separator()
			for theme_name in _THEME_ORDER:
				label_text = f"{_THEME_GLYPH[theme_name]} {_THEME_LABEL[theme_name]}"
				selected = self._theme_name == theme_name
				if imgui.menu_item(label_text, "", selected)[0]:
					self._set_theme(theme_name)
			imgui.end_popup()

		if imgui.is_item_hovered():
			self._tooltip("Click to cycle themes · Right-click for direct pick\nPersists across launches via autosave.")

	def set_master_status_provider(self, fn: Callable[[], bool]) -> None:
		'''Register a getter for the live master-toggle state used by the top-strip pill.'''
		self._master_status_fn = fn

	def _draw_master_toggle_pill(self) -> None:
		'''Read-only ON/OFF indicator for the master switch; falls back to a neutral MASTER label without a provider.'''
		palette = self._theme()
		provider = getattr(self, "_master_status_fn", None)
		if provider is None:
			self._badge_pill(f" MASTER ({str(getattr(self.config.general, 'master_toggle_key', 'k')).upper()}) ", palette["surface_2"], palette["accent"])
			return
		try:
			master_on = bool(provider())
		except Exception:
			master_on = True
		background = palette["safe"] if master_on else palette["danger"]
		foreground = _hex("#0a0c12")
		text = f" MASTER: {'ON' if master_on else 'OFF'} "
		self._badge_pill(text, background, foreground)

	def _draw_bottom_strip(self, height: float) -> None:
		'''One-line cheat sheet of the most-used keybinds plus a version tag.'''
		palette = self._theme()
		imgui.push_style_color(imgui.Col_.child_bg.value, palette["bg_sidebar"])
		imgui.push_style_var(imgui.StyleVar_.child_rounding.value, 8.0)
		imgui.push_style_var(imgui.StyleVar_.window_padding.value, imgui.ImVec2(12, 4))
		imgui.begin_child("##botstrip", imgui.ImVec2(0, height), imgui.ChildFlags_.borders.value, imgui.WindowFlags_.no_scrollbar.value)
		general_config = self.config.general
		bind_str = (
			f" {str(getattr(general_config, 'toggle_menu_key', 'j')).upper()} menu"
			f" · {str(getattr(general_config, 'master_toggle_key', 'k')).upper()} master"
			f" · {str(getattr(general_config, 'exit_key', 'l')).upper()} exit"
			f" · ESC cancel rebind"
		)
		imgui.push_style_color(imgui.Col_.text.value, palette["text_dim"])
		imgui.text(bind_str)
		imgui.same_line()
		imgui.dummy(imgui.ImVec2(40, 1))
		imgui.same_line()
		imgui.text("v0.x · MS Mesh build")
		imgui.pop_style_color()
		imgui.end_child()
		imgui.pop_style_var(2)
		imgui.pop_style_color()

	def _draw_sidebar(self) -> None:
		'''Vertical navigation: STATUS block and one button per _CATEGORIES entry.'''
		palette = self._theme()

		self._sidebar_section_label("STATUS")
		try:
			input_state = imgui.get_io()
			fps = input_state.framerate
		except Exception:
			fps = 0.0
		imgui.push_style_color(imgui.Col_.text.value, palette["text_dim"])
		imgui.text(f" {fps:5.0f} fps")
		attach_state = "attached" if self.cs2_hwnd else "no attach"
		imgui.text(f" cs2 {attach_state}")
		imgui.pop_style_color()

		imgui.dummy(imgui.ImVec2(1, 12))

		self._sidebar_section_label("NAVIGATION")
		for key, label, _method in self._CATEGORIES:
			self._sidebar_nav_item(key, label)

	def _sidebar_section_label(self, label: str) -> None:
		'''Accent bar and uppercase title that subdivides STATUS / NAV.'''
		palette = self._theme()
		scale = self._ui_scale
		position = imgui.get_cursor_screen_pos()
		draw_list = imgui.get_window_draw_list()
		bar_h = imgui.get_text_line_height()
		draw_list.add_rect_filled(
			imgui.ImVec2(position.x, position.y + 2 * scale),
			imgui.ImVec2(position.x + 3 * scale, position.y + bar_h),
			imgui.color_convert_float4_to_u32(palette["accent"]),
		)
		imgui.dummy(imgui.ImVec2(8 * scale, 1))
		imgui.same_line()
		imgui.push_style_color(imgui.Col_.text.value, palette["text_dim"])
		imgui.text(label)
		imgui.pop_style_color()

	def _sidebar_nav_item(self, key: str, label: str) -> None:
		'''One nav row; the active state stacks a glow, a tinted fill, and a left accent stripe.'''
		palette = self._theme()
		scale = self._ui_scale
		is_active = self._active_category == key

		avail = imgui.get_content_region_avail()
		row_h = 32.0 * scale
		position = imgui.get_cursor_screen_pos()
		draw_list = imgui.get_window_draw_list()

		imgui.invisible_button(f"##nav_{key}", imgui.ImVec2(avail.x, row_h))
		hovered = imgui.is_item_hovered()
		clicked = imgui.is_item_clicked(0)
		if clicked:
			self._active_category = key

		if is_active:
			draw_list.add_rect_filled(
				imgui.ImVec2(position.x - 4 * scale, position.y - 2 * scale),
				imgui.ImVec2(position.x + avail.x + 4 * scale, position.y + row_h + 2 * scale),
				imgui.color_convert_float4_to_u32(palette["accent_glow"]),
				6.0,
			)
		if is_active or hovered:
			if is_active:
				fill = self._mix(palette["surface_1"], palette["accent"], 0.22)
			else:
				fill = self._mix(palette["surface_1"], palette["text_dim"], 0.10)
			draw_list.add_rect_filled(
				imgui.ImVec2(position.x, position.y), imgui.ImVec2(position.x + avail.x, position.y + row_h), imgui.color_convert_float4_to_u32(fill), 4.0
			)

		if is_active:
			draw_list.add_rect_filled(
				imgui.ImVec2(position.x, position.y + 4 * scale),
				imgui.ImVec2(position.x + 4 * scale, position.y + row_h - 4 * scale),
				imgui.color_convert_float4_to_u32(palette["accent"]),
				1.5,
			)

		text_y_offset = (row_h - imgui.get_text_line_height()) * 0.5
		text_color = palette["accent_hi"] if is_active else palette["text"]
		draw_list.add_text(imgui.ImVec2(position.x + 16 * scale, position.y + text_y_offset), imgui.color_convert_float4_to_u32(text_color), label)

	def _begin_card(self, key: str, title: str, *, risk: RiskInput = None, help_text: str | None = None, caveat: str | None = None) -> None:
		'''Open a feature card; the caller must finish it with `_end_card`.'''
		palette = self._theme()
		imgui.push_style_color(imgui.Col_.child_bg.value, palette["surface_2"])
		imgui.push_style_color(imgui.Col_.border.value, self._mix(palette["border"], palette["accent"], 0.18))
		imgui.push_style_var(imgui.StyleVar_.child_rounding.value, 12.0)
		imgui.push_style_var(imgui.StyleVar_.child_border_size.value, 1.0)
		imgui.push_style_var(imgui.StyleVar_.window_padding.value, imgui.ImVec2(16, 14))
		imgui.begin_child(
			f"##card_{key}", imgui.ImVec2(0, 0), imgui.ChildFlags_.borders.value | imgui.ChildFlags_.auto_resize_y.value, imgui.WindowFlags_.no_scrollbar.value
		)

		scale = self._ui_scale
		position = imgui.get_cursor_screen_pos()
		draw_list = imgui.get_window_draw_list()
		bar_height = imgui.get_text_line_height()
		draw_list.add_rect_filled(
			imgui.ImVec2(position.x, position.y + 2 * scale),
			imgui.ImVec2(position.x + 3 * scale, position.y + bar_height),
			imgui.color_convert_float4_to_u32(palette["accent"]),
		)
		imgui.dummy(imgui.ImVec2(8 * scale, 1))
		imgui.same_line()
		imgui.push_style_color(imgui.Col_.text.value, palette["text"])
		imgui.text(title)
		imgui.pop_style_color()

		assessment = resolve_risk(risk)
		full_text = assessment.plain_text
		if help_text:
			full_text += "\n\n" + help_text
		icon_colour = palette[{"critical": "danger", "high": "danger", "medium": "warn", "low": "accent", "safe": "accent"}.get(assessment.bucket, "text_dim")]
		if assessment.badge:
			imgui.same_line()
			self._badge_pill(assessment.badge, icon_colour)

		help_width = imgui.calc_text_size(" (?) ").x + 20 * scale
		available_width = imgui.get_content_region_avail().x
		imgui.same_line()
		imgui.dummy(imgui.ImVec2(max(0.0, available_width - help_width - 4 * scale), 1))
		imgui.same_line()
		imgui.push_style_color(imgui.Col_.text.value, icon_colour)
		imgui.text(" (?) ")
		imgui.pop_style_color()
		if imgui.is_item_hovered():
			self._tooltip(full_text)
		imgui.dummy(imgui.ImVec2(1, 4))

		if caveat:
			imgui.push_style_color(imgui.Col_.text.value, palette["warn"])
			imgui.text_wrapped(f"⚠ {caveat}")
			imgui.pop_style_color()
			imgui.dummy(imgui.ImVec2(1, 6))

	def _end_card(self) -> None:
		'''Close a card opened with ``_begin_card``.'''
		imgui.end_child()
		imgui.pop_style_var(3)
		imgui.pop_style_color(2)
		imgui.dummy(imgui.ImVec2(1, 12))

	def _tooltip(self, text: str) -> None:
		'''Rich tooltip with theme styling.'''
		palette = self._theme()
		imgui.push_style_color(imgui.Col_.popup_bg.value, palette["surface_1"])
		imgui.push_style_color(imgui.Col_.border.value, palette["border"])
		imgui.push_style_color(imgui.Col_.text.value, palette["text"])
		imgui.begin_tooltip()
		imgui.push_text_wrap_pos(420 * self._ui_scale)
		imgui.text_wrapped(text)
		imgui.pop_text_wrap_pos()
		imgui.end_tooltip()
		imgui.pop_style_color(3)

	def _advanced(self, key: str, label: str = "Show advanced") -> bool:
		'''Render a collapsible header and indent its open body.'''
		if key not in self._show_advanced:
			self._show_advanced[key] = self._advanced_default
		is_open = self._show_advanced[key]
		arrow = "▾" if is_open else "▸"
		if imgui.selectable(f" {arrow} {label}", False, imgui.SelectableFlags_.no_auto_close_popups.value)[0]:
			self._show_advanced[key] = not is_open
		is_open = self._show_advanced.get(key, False)
		if is_open:
			imgui.indent(12 * self._ui_scale)
		return is_open

	def _toggle(self, label: str, section, attr: str) -> bool:
		'''Pill-style toggle switch that mutates getattr(section, attr) and reports whether the value changed this frame.'''
		palette = self._theme()
		scale = self._ui_scale
		current = bool(getattr(section, attr, False))
		pill_w = 36 * scale
		pill_h = 18 * scale

		position = imgui.get_cursor_screen_pos()
		imgui.invisible_button(f"##tg_{label}_{attr}", imgui.ImVec2(pill_w, pill_h))
		clicked = imgui.is_item_clicked(0)
		if clicked:
			current = not current
			try:
				setattr(section, attr, current)
			except Exception:
				pass
		hovered = imgui.is_item_hovered()
		draw_list = imgui.get_window_draw_list()
		background = palette["accent"] if current else palette["surface_1"]
		if hovered:
			background = palette["accent_hi"] if current else self._mix(palette["surface_1"], palette["accent"], 0.20)
		draw_list.add_rect_filled(
			imgui.ImVec2(position.x, position.y), imgui.ImVec2(position.x + pill_w, position.y + pill_h), imgui.color_convert_float4_to_u32(background), pill_h * 0.5
		)
		knob_r = (pill_h * 0.5) - (2 * scale)
		knob_x = position.x + (pill_w - pill_h * 0.5 - 2 * scale) if current else (position.x + pill_h * 0.5 + 2 * scale)
		knob_y = position.y + pill_h * 0.5
		knob_color = _hex("#0a0c12") if (current and self._theme_name != "daylight") else _hex("#ffffff")
		draw_list.add_circle_filled(imgui.ImVec2(knob_x, knob_y), knob_r, imgui.color_convert_float4_to_u32(knob_color))
		imgui.same_line()
		imgui.text(label)
		return clicked

	_SLIDER_W = 260.0

	def _slider_float(self, label: str, section, attr: str, lo: float, hi: float, fmt: str = "%.2f") -> bool:
		current = float(getattr(section, attr, 0.0))
		imgui.set_next_item_width(self._SLIDER_W * self._ui_scale)
		changed, new = imgui.slider_float(label, current, lo, hi, fmt)
		if changed:
			try:
				setattr(section, attr, float(new))
			except Exception:
				pass
		return bool(changed)

	def _slider_int(self, label: str, section, attr: str, lo: int, hi: int) -> bool:
		current = int(getattr(section, attr, 0))
		imgui.set_next_item_width(self._SLIDER_W * self._ui_scale)
		changed, new = imgui.slider_int(label, current, lo, hi)
		if changed:
			try:
				setattr(section, attr, int(new))
			except Exception:
				pass
		return bool(changed)

	def _color_edit(self, label: str, section, attr: str) -> bool:
		current = list(getattr(section, attr, [1.0, 1.0, 1.0, 1.0]))
		current = (current + [1.0, 1.0, 1.0, 1.0])[:4]
		changed, new = imgui.color_edit4(label, current, imgui.ColorEditFlags_.alpha_bar.value | imgui.ColorEditFlags_.no_inputs.value)
		if changed:
			try:
				setattr(section, attr, list(new))
			except Exception:
				pass
		return bool(changed)

	def _capture_pump(self) -> str | None:
		'''Poll keys during an active capture; return the next up->down key name, or None. ESC cancels.'''
		if not self._capturing or _kb is None:
			return None
		try:
			if _kb.is_pressed("esc"):
				self._capturing = None
				return None
		except Exception:
			pass

		candidates = (
			list("abcdefghijklmnopqrstuvwxyz0123456789")
			+ [
				"space",
				"tab",
				"enter",
				"shift",
				"ctrl",
				"alt",
				"left",
				"right",
				"up",
				"down",
				"home",
				"end",
				"page up",
				"page down",
				"insert",
				"delete",
				"[",
				"]",
				";",
				"'",
				",",
				".",
				"/",
				"\\",
				"`",
				"-",
				"=",
			]
			+ [f"f{index}" for index in range(1, 13)]
		)
		now_down: set[str] = set()
		for key in candidates:
			try:
				if _kb.is_pressed(key):
					now_down.add(key)
			except Exception:
				continue
		new_keys = now_down - self._prev_keys_down
		self._prev_keys_down = now_down
		if not new_keys:
			return None
		return sorted(new_keys, key=lambda x: (-len(x), x))[0]

	_KEYBIND_LABEL_W = 220.0
	_KEYBIND_KEY_W = 110.0
	_KEYBIND_GAP = 12.0

	def _keybind_row_aligned(self, label: str, section, attr: str, *, capture_id: str | None = None) -> None:
		'''Generic 3-column keybind row; capture_id defaults to attr and disambiguates rows sharing a field name.'''
		palette = self._theme()
		scale = self._ui_scale
		if capture_id is None:
			capture_id = attr
		current = str(getattr(section, attr, ""))
		is_capturing = self._capturing == capture_id

		row_start_x = imgui.get_cursor_pos_x()

		imgui.text(label)
		imgui.same_line()
		imgui.set_cursor_pos_x(row_start_x + self._KEYBIND_LABEL_W * scale)

		if is_capturing:
			elapsed = monotonic() - self._capture_started_at
			imgui.push_style_color(imgui.Col_.text.value, palette["warn"])
			imgui.text(f"press… {elapsed:0.1f}s")
			imgui.pop_style_color()
		else:
			imgui.push_style_color(imgui.Col_.text.value, palette["accent_hi"] if current else palette["text_dim"])
			imgui.text(current if current else "<unset>")
			imgui.pop_style_color()

		imgui.same_line()
		imgui.set_cursor_pos_x(row_start_x + (self._KEYBIND_LABEL_W + self._KEYBIND_KEY_W + self._KEYBIND_GAP) * scale)

		if is_capturing:
			if imgui.button(f"Cancel##{capture_id}"):
				self._capturing = None
			captured = self._capture_pump()
			if captured and captured != "esc":
				try:
					setattr(section, attr, captured)
				except Exception:
					pass
				self._capturing = None
				if capture_id == "master_toggle_key":
					print(f"[menu] master_toggle_key -> {captured!r} (restart to re-bind the global hotkey)")
			imgui.same_line()
			if imgui.button(f"Clear##{capture_id}"):
				try:
					setattr(section, attr, "")
				except Exception:
					pass
				self._capturing = None
		else:
			if imgui.button(f"Rebind##{capture_id}"):
				self._capturing = capture_id
				self._capture_started_at = monotonic()
				self._prev_keys_down = set()
			if current:
				imgui.same_line()
				if imgui.button(f"Clear##{capture_id}"):
					try:
						setattr(section, attr, "")
					except Exception:
						pass

	def _panel_keybinds(self) -> None:
		self._h1("Keybinds")
		self._body(
			"Click Rebind, then press the key you want to assign. ESC "
			"cancels the capture. Click Clear next to a binding to unbind "
			"it. Letters, digits, modifiers (shift/ctrl/alt), arrows, "
			"F1–F12, brackets and punctuation are all supported.",
			dim=True,
		)
		imgui.dummy(imgui.ImVec2(1, 8))

		self._begin_card(
			"kb_app",
			"App controls",
			risk=bind_risk('keybinds', lambda: self.config),
			help_text=(
				"Global hotkeys for the menu/master/exit. Changing the "
				"master toggle key requires a restart for the global "
				"hotkey to re-bind — the others take effect immediately."
			),
		)
		self._keybind_row_aligned("Toggle menu", self.config.general, "toggle_menu_key")
		self._keybind_row_aligned("Master on/off", self.config.general, "master_toggle_key")
		self._keybind_row_aligned("Exit app", self.config.general, "exit_key")
		self._end_card()

		self._begin_card(
			"kb_features",
			"Feature hotkeys",
			risk=bind_risk('keybinds', lambda: self.config),
			help_text=(
				"Every feature has two independent bindings:\n\n"
				" HOLD — the feature runs only while the key is held "
				"down. Best for triggerbot / bhop where you want fine "
				"control over when synthesis fires.\n\n"
				" TOGGLE — one press flips the feature on/off. Best for "
				"persistent helpers like radar or wall-hack ESP.\n\n"
				"Either or both can be bound; an empty cell means unbound."
			),
		)
		self._body("HOLD bindings run the feature only while the key is held. TOGGLE bindings flip enabled state on each press.", dim=True)

		self._h2("Combat")
		self._keybind_row_aligned("Triggerbot · hold", self.config.triggerbot, "hotkey", capture_id="triggerbot_hold")
		self._keybind_row_aligned("Triggerbot · toggle", self.config.triggerbot, "hotkey_toggle", capture_id="triggerbot_toggle")
		self._keybind_row_aligned("Rapid-fire · hold", self.config.rapidfire, "hotkey", capture_id="rapidfire_hold")
		self._keybind_row_aligned("Rapid-fire · toggle", self.config.rapidfire, "hotkey_toggle", capture_id="rapidfire_toggle")
		self._keybind_row_aligned("Auto-pistol · hold", self.config.autopistol, "hotkey", capture_id="autopistol_hold")
		self._keybind_row_aligned("Auto-pistol · toggle", self.config.autopistol, "hotkey_toggle", capture_id="autopistol_toggle")
		self._keybind_row_aligned("BHop · hold", self.config.bhop, "hotkey", capture_id="bhop_hold")
		self._keybind_row_aligned("BHop · toggle", self.config.bhop, "hotkey_toggle", capture_id="bhop_toggle")

		self._h2("Visuals")
		self._keybind_row_aligned("Radar · toggle", self.config.radar, "hotkey_toggle", capture_id="radar_toggle")

		self._end_card()

	def _panel_esp(self) -> None:
		visuals_config = self.config.visuals
		self._h1("ESP")
		self._body(
			(
				'Player visuals use external game-state reads. Native Glow also writes game memory. The badges describe policy and technical '
				'behavior; read-only rendering is not a ban guarantee.'
			),
			dim=True,
		)
		imgui.dummy(imgui.ImVec2(1, 8))
		self._h2("Wallhack preset")
		self._begin_card(
			"wallhack_mode",
			"Wallhack mode",
			risk=bind_risk('wallhack', lambda: self.config),
			help_text=(
				'Selects the existing Box/Skeleton/Glow preset. Glow writes native game fields; the other primitives render from external '
				'reads. All extra enemy-information modes remain prohibited in competitive use.'
			),
		)
		modes = [
			"None (off)",
			'Skeleton + Box (default)',
			"Box only",
			"Skeleton only",
			'Glow (game-memory writes)',
			'Box + Glow (game-memory writes)',
			'Skeleton + Glow (game-memory writes)',
			'All on (includes game-memory writes)',
		]
		current_preset = (bool(visuals_config.box.enabled), bool(visuals_config.skeleton.enabled), bool(self.config.glow.enabled))
		try:
			current_index = _WALLHACK_PRESETS.index(current_preset)
		except ValueError:
			current_index = 0
		changed, new_index = _safe_combo("##wallhack_mode", current_index, modes)
		if changed:
			box_enabled, skeleton_enabled, glow_enabled = _WALLHACK_PRESETS[max(0, min(new_index, len(_WALLHACK_PRESETS) - 1))]
			visuals_config.box.enabled = box_enabled
			visuals_config.skeleton.enabled = skeleton_enabled
			self.config.glow.enabled = glow_enabled
		self._end_card()

		box_config = visuals_config.box
		self._begin_card(
			"esp_box",
			"Box",
			risk=bind_risk('box', lambda: self.config),
			help_text=(
				"World-space AABB drawn around each visible enemy pawn. "
				"Read-only — uses the same memory reads as the radar. "
				"Style options: outline (1 px line), filled (translucent "
				"fill), corners (just the four L-shaped brackets)."
			),
		)
		self._toggle("Enabled", box_config, "enabled")
		styles = ["outline", "filled", "corners"]
		cur_s = box_config.style if box_config.style in styles else "outline"
		changed, new_index = _safe_combo("Style##box_style", styles.index(cur_s), styles)
		if changed:
			box_config.style = styles[new_index]
		self._slider_float("Thickness", box_config, "thickness", 0.5, 4.0)
		self._color_edit("Colour", box_config, "color")
		if self._advanced("box_adv"):
			self._toggle("Team-coloured (CT/T)", box_config, "team_colored")
			self._color_edit("CT colour", box_config, "ct_color")
			self._color_edit("T colour", box_config, "t_color")
			imgui.unindent(12 * self._ui_scale)
		self._end_card()

		skeleton_config = visuals_config.skeleton
		self._begin_card(
			"esp_skel",
			"Skeleton",
			risk=bind_risk('skeleton', lambda: self.config),
			help_text=("Per-bone wireframe. Reads the bone matrix array each frame, projects bone heads to screen, draws line segments between connected bones."),
		)
		self._toggle("Enabled", skeleton_config, "enabled")
		self._slider_float("Thickness", skeleton_config, "thickness", 0.5, 3.0)
		self._color_edit("Colour", skeleton_config, "color")
		self._end_card()

		snapline_config = visuals_config.snapline
		self._begin_card(
			"esp_snap",
			"Snapline",
			risk=bind_risk('snapline', lambda: self.config),
			help_text=(
				"Thin line drawn from the bottom-centre of the screen to each enemy's feet. Useful for instantly seeing direction-of-threat in your peripheral vision."
			),
		)
		self._toggle("Enabled", snapline_config, "enabled")
		self._color_edit("Colour##snap", snapline_config, "color")
		self._end_card()

		crosshair_config = visuals_config.crosshair
		self._begin_card(
			"esp_xhair",
			"Crosshair",
			risk=bind_risk('crosshair', lambda: self.config),
			help_text=(
				"Static centre-screen crosshair drawn by the overlay. "
				"Independent of CS2's in-game crosshair so you can keep "
				"yours invisible in-game and use this one instead."
			),
		)
		self._toggle("Enabled", crosshair_config, "enabled")
		self._slider_float("Size (px)", crosshair_config, "size", 2.0, 40.0)
		self._slider_float("Gap (px)", crosshair_config, "gap", 0.0, 20.0)
		self._slider_float("Thickness##xhair", crosshair_config, "thickness", 1.0, 4.0)
		self._color_edit("Colour##xhair", crosshair_config, "color")
		self._end_card()

		self._begin_card(
			"esp_text",
			"Name · Distance · Health",
			risk=bind_risk('player_labels', lambda: self.config),
			help_text=("Per-enemy text labels. All three render above the box head with the same font — you can mix and match."),
		)
		name_config = visuals_config.name
		self._toggle("Show names", name_config, "enabled")
		self._color_edit("Name colour", name_config, "color")
		self._toggle("Active weapon icon", name_config, "weapon_enabled")
		self._slider_float("Weapon icon width (px)", name_config, "weapon_size", 16.0, 64.0, "%.0f")
		distance_config = visuals_config.distance
		self._toggle("Show distance (m)", distance_config, "enabled")
		self._color_edit("Distance colour", distance_config, "color")
		health_config = visuals_config.health
		modes = ["off", "bar", "percent", "both"]
		cur_m = health_config.mode if health_config.mode in modes else "bar"
		changed, new_index = _safe_combo("Health mode", modes.index(cur_m), modes)
		if changed:
			health_config.mode = modes[new_index]
		self._slider_float("Health bar width (px)", health_config, "bar_width", 1.0, 20.0)
		self._slider_float("Health bar offset (px)", health_config, "bar_offset", 0.0, 30.0)
		self._color_edit("Health — high", health_config, "high_color")
		self._color_edit("Health — mid", health_config, "mid_color")
		self._color_edit("Health — low", health_config, "low_color")
		self._end_card()

		glow_config = self.config.glow
		self._begin_card(
			"esp_glow",
			"Glow",
			risk=bind_risk('glow', lambda: self.config),
			caveat=('Glow writes game-memory highlight fields. This differs from overlay rendering, but no comparative detector probability is available.'),
			help_text=(
				'Writes pawn CGlowProperty color, timers, type and highlight flags. Normal scheduling is gated; cleanup can still write. '
				'Neither writes nor reads establish permitted use.'
			),
		)
		self._toggle("Glow enabled", glow_config, "enabled")
		self._slider_int("Type", glow_config, "type", 0, 3)
		self._slider_float("Opacity", glow_config, "opacity", 0.0, 1.0)
		self._slider_float("Distance fade (units)", glow_config, "radius", 0.0, 4000.0)
		self._color_edit("CT colour", glow_config, "ct_color")
		self._color_edit("T colour", glow_config, "t_color")
		self._slider_int("Miss-grace frames", glow_config, "miss_grace_frames", 1, 60)
		why_open = self._show_why.get("esp_glow_why", False)
		arrow = "▾" if why_open else "▸"
		if imgui.selectable(f" {arrow} Why?", False, imgui.SelectableFlags_.no_auto_close_popups.value)[0]:
			why_open = not why_open
			self._show_why["esp_glow_why"] = why_open
		if why_open:
			imgui.indent(12 * self._ui_scale)
			imgui.text_wrapped(
				(
					'Native Glow and the legacy Aimbot write game memory; other visuals read game state, and input features automate actions. '
					'Valve does not disclose all detector details. No app mode is certified by the absence of one write path.'
				)
			)
			imgui.unindent(12 * self._ui_scale)
		self._end_card()

		event_visuals = getattr(self.config, "event_visuals", None)
		if event_visuals is not None:
			self._begin_card(
				"hud_capsules",
				"HUD & Event Capsules Suite",
				risk=bind_risk('hud_suite', lambda: self.config),
				help_text=(
					"Floating HUD capsules (Velocity/Ascend style): Watermark, Event Feed, "
					"Active Hotkeys, Module Status Pills, Spectators, C4 Bomb Timer.\n"
					"Interactive repositioning & sizing is available when the menu is active on screen."
				),
			)
			self._toggle("Master HUD Enabled", event_visuals, "enabled")
			if imgui.button("Enter HUD Edit Mode"):
				setattr(event_visuals, "edit_mode", True)
			self._toggle("Widget editing only in Edit Mode", event_visuals, "edit_lock")
			ov_sec = getattr(self.config, "overlay", None)
			if ov_sec is not None:
				self._toggle("Overlay only while CS2 focused", ov_sec, "only_when_cs2_focused")

			groups = ["Watermark / Telemetry", "Combat Event Feed", "Active Hotkeys", "Module Status Pills", "Spectator List", "C4 Bomb Timer", "Event Sounds"]
			if not hasattr(self, "_active_capsule_group_idx"):
				self._active_capsule_group_idx = 0

			changed, new_grp = _safe_combo("Capsule Group", self._active_capsule_group_idx, groups)
			if changed:
				self._active_capsule_group_idx = new_grp

			imgui.dummy(imgui.ImVec2(1, 4))
			selected_group = groups[self._active_capsule_group_idx]

			if selected_group == "Watermark / Telemetry":
				fps_config = getattr(self.config, "fps_counter", None)
				if fps_config is not None:
					self._toggle("Show Watermark & FPS", fps_config, "enabled")
					self._toggle("Show Ping", fps_config, "show_ping")
					self._toggle("Show CPU load", fps_config, "show_latency")
					self._toggle("Show RAM usage", fps_config, "show_ram")
					self._toggle("Show Frame Jitter", fps_config, "show_jitter")
				self._color_edit("Accent Colour", event_visuals, "custom_color")

			elif selected_group == "Combat Event Feed":
				self._slider_int("Max Visible Events", event_visuals, "max_events", 1, 10)
				self._slider_float("Event Duration (s)", event_visuals, "event_duration_sec", 1.0, 10.0)
				self._slider_float("Font Size (px)", event_visuals, "font_size", 10.0, 20.0)

				from cs2esp.visuals.icons import EVENT_STYLE_DEFAULTS, GLYPH_CHOICES
				from cs2esp.visuals.weapon_icons import glyph_label

				ev_keys = list(EVENT_STYLE_DEFAULTS.keys())
				if not hasattr(self, "_ev_style_idx"):
					self._ev_style_idx = 0
				self._ev_style_idx = min(self._ev_style_idx, len(ev_keys) - 1)
				_chg, self._ev_style_idx = _safe_combo("Event Type", self._ev_style_idx, ev_keys)
				ev_key = ev_keys[self._ev_style_idx]
				self._toggle("Show This Event", event_visuals, f"ev_{ev_key}_enabled")
				glyph_list = list(GLYPH_CHOICES)
				glyph_cur = str(getattr(event_visuals, f"ev_{ev_key}_glyph", "") or EVENT_STYLE_DEFAULTS[ev_key])
				glyph_idx = glyph_list.index(glyph_cur) if glyph_cur in glyph_list else 0
				glyph_changed, glyph_new = _safe_combo("Icon", glyph_idx, [glyph_label(key) for key in glyph_list])
				if glyph_changed:
					setattr(event_visuals, f"ev_{ev_key}_glyph", glyph_list[glyph_new])
				self._color_edit("Icon Colour", event_visuals, f"ev_{ev_key}_color")
				if imgui.button("Use Theme Colour"):
					setattr(event_visuals, f"ev_{ev_key}_color", [])
				cur_label = str(getattr(event_visuals, f"ev_{ev_key}_label", "") or "")
				label_changed, label_new = imgui.input_text("Custom Label", cur_label, 64)
				if label_changed:
					setattr(event_visuals, f"ev_{ev_key}_label", label_new.strip())

			elif selected_group == "Active Hotkeys":
				self._toggle("Show Hotkeys Card", event_visuals, "show_hotkeys")

			elif selected_group == "Module Status Pills":
				self._toggle("Show Status Pills", event_visuals, "show_status_pills")

			elif selected_group == "Spectator List":
				self._toggle("Show Spectator List", event_visuals, "show_spectators")

			elif selected_group == "C4 Bomb Timer":
				self._toggle("Show C4 Timer Capsule", event_visuals, "show_c4_timer")

			elif selected_group == "Event Sounds":
				snd = getattr(self.config, "event_sounds", None)
				if snd is not None:
					self._toggle("Enable Event Sounds", snd, "enabled")
					self._slider_float("Volume", snd, "volume", 0.0, 1.0)
					for label, attr in [
						("Kill sound", "kill_enabled"),
						("Headshot sound", "headshot_enabled"),
						("Hit sound", "hit_enabled"),
						("Miss sound", "miss_enabled"),
						("Bomb sound", "bomb_enabled"),
						("Spectator sound", "spectator_enabled"),
						("System sound", "system_enabled"),
						("Hotkey toggle sound", "toggle_enabled"),
					]:
						self._toggle(label, snd, attr)

			self._end_card()
			if self._hud_editor_scroll_pending:
				imgui.set_scroll_here_y(0.5)
				self._hud_editor_scroll_pending = False

	def _panel_radar(self) -> None:
		radar_config = self.config.radar
		self._h1("Radar")
		self._body("Corner mini-map of every enemy within range, rotated to your view. Pure overlay rendering — no game writes.", dim=True)
		imgui.dummy(imgui.ImVec2(1, 8))
		self._h2("Display")
		self._begin_card(
			"radar_main",
			"Radar",
			risk=bind_risk('radar', lambda: self.config),
			help_text=(
				"Corner mini-map showing enemies relative to your view. "
				"Pure overlay rendering — reads positions/angles from "
				"CS2 memory but never writes back. The radar always "
				"rotates with your view (classic CS minimap behaviour) "
				"so whatever you're facing is at the top."
			),
		)
		self._toggle("Enabled", radar_config, "enabled")
		shapes = ["round", "square"]
		cur_sh = radar_config.shape if radar_config.shape in shapes else "round"
		changed, new_index = _safe_combo("Shape", shapes.index(cur_sh), shapes)
		if changed:
			radar_config.shape = shapes[new_index]
		anchors = ["top_left", "top_right", "bottom_left", "bottom_right"]
		cur_an = getattr(radar_config, "anchor", "top_left") or "top_left"
		if cur_an not in anchors:
			cur_an = "top_left"
		changed, new_index = _safe_combo("Anchor", anchors.index(cur_an), anchors)
		if changed:
			radar_config.anchor = anchors[new_index]
		self._slider_float("Size (fraction of screen, 0=px)", radar_config, "size_fraction", 0.0, 0.50)
		self._slider_float("Size (px, used if fraction=0)", radar_config, "size", 80.0, 600.0)
		self._slider_float("Range (units)", radar_config, "range", 500.0, 10000.0)
		self._slider_float("Opacity", radar_config, "opacity", 0.10, 1.0)
		self._color_edit("Enemy colour", radar_config, "enemy_color")
		self._color_edit("Teammate colour", radar_config, "teammate_color")
		if self._advanced("radar_adv"):
			self._toggle("Show teammates", radar_config, "show_teammates")
			self._color_edit("Background", radar_config, "background_color")
			self._color_edit("Border", radar_config, "border_color")
			self._color_edit("Local player", radar_config, "local_color")
			imgui.unindent(12 * self._ui_scale)
		self._end_card()

	def _panel_awareness(self) -> None:
		awareness_config = self.config.awareness
		self._h1("Awareness")
		self._body("A peripheral-vision aid — the screen edges pulse coloured when an enemy is looking at you (and ideally close enough to be a threat).", dim=True)
		imgui.dummy(imgui.ImVec2(1, 8))
		self._h2("Vignette")
		self._begin_card(
			"aware_main",
			"Awareness vignette",
			risk=bind_risk('awareness', lambda: self.config),
			help_text=(
				"Pulses a coloured vignette at the screen edges when an "
				"enemy is looking at you. Intensity scales with proximity "
				"and how directly their crosshair is on you. Pure "
				"read-only overlay rendering."
			),
		)
		self._toggle("Enabled", awareness_config, "enabled")
		self._slider_float("Cone (degrees)", awareness_config, "cone_degrees", 1.0, 30.0)
		self._slider_float("Max distance (units)", awareness_config, "max_distance", 500.0, 8000.0)
		self._slider_float("Fade speed", awareness_config, "fade_speed", 0.5, 12.0)
		self._slider_float("Edge thickness (px)", awareness_config, "edge_thickness", 20.0, 400.0)
		self._color_edit("Colour", awareness_config, "color")
		if self._advanced("aware_adv"):
			self._toggle("Rear-only (only show enemies looking at our back)", awareness_config, "rear_only")
			self._slider_float("Rear FOV (deg)", awareness_config, "rear_fov_deg", 60.0, 180.0)
			imgui.unindent(12 * self._ui_scale)
		self._end_card()

	def _panel_combat(self) -> None:
		self._h1("Combat")
		self._body(
			(
				'Feature hotkeys control the existing firing and movement automation. Synthetic input and absence of game-memory writes do '
				'not establish competitive permission or a VAC/FACEIT detection probability.'
			),
			dim=True,
		)
		imgui.dummy(imgui.ImVec2(1, 8))
		self._h2("Triggerbot")
		trigger_config = self.config.triggerbot
		self._begin_card(
			"comb_trigger",
			"Triggerbot",
			risk=bind_risk('triggerbot', lambda: self.config),
			help_text=(
				"Auto-fires when your crosshair is on an enemy hitbox. "
				"Pure user-mode left-mouse synthesis — no memory writes. "
				"Distance bands let you tune trigger reaction time per "
				"engagement range so close-quarters fights snap quickly "
				"while long-range checks are slower (more human-looking)."
			),
		)
		self._toggle("Enabled", trigger_config, "enabled")
		self._toggle("Scoped weapons: fire only when scoped", trigger_config, "scoped_require_scope")
		imgui.dummy(imgui.ImVec2(1, 4))
		for label, attr in [
			("Fire on rifles & SMGs", "allow_rifles"),
			("Fire on SMGs", "allow_smgs"),
			("Fire on shotguns", "allow_shotguns"),
			("Fire on sniper rifles", "allow_snipers"),
			("Fire on pistols", "allow_pistols"),
			("Fire on LMGs", "allow_lmg"),
			("Fire on grenades", "allow_grenades"),
			("Fire on knife & Zeus", "allow_knife_zeus"),
		]:
			self._toggle(label, trigger_config, attr)
		fire_conds = ["always", "shift", "ctrl", "no_move"]
		cur_cond = str(getattr(trigger_config, "fire_condition", "always") or "always")
		cond_idx = fire_conds.index(cur_cond) if cur_cond in fire_conds else 0
		cond_changed, cond_new = _safe_combo("Fire condition", cond_idx, fire_conds)
		if cond_changed:
			setattr(trigger_config, "fire_condition", fire_conds[cond_new])
		self._slider_int("Refire cooldown (ms)", trigger_config, "refire_cooldown_ms", 0, 2000)
		self._slider_int("Reaction delay (ms)", trigger_config, "delay_ms", 0, 500)
		self._slider_int("Delay jitter (±ms)", trigger_config, "delay_jitter_ms", 0, 200)
		self._slider_int("Min reaction (ms)", trigger_config, "min_delay_ms", 0, 500)
		if self._advanced("comb_trigger_adv"):
			self._slider_float("Fallback FOV (deg, point-gate)", trigger_config, "fov_deg", 0.1, 12.0)
			self._slider_float("Fallback skeleton FOV (deg)", trigger_config, "skeleton_fov_deg", 0.1, 12.0)
			imgui.dummy(imgui.ImVec2(1, 4))
			self._slider_float("Scoped band damping ×mult (scoped only)", trigger_config, "scoped_band_multiplier", 0.05, 1.0)
			imgui.dummy(imgui.ImVec2(1, 4))
			self._draw_distance_bands()
			imgui.unindent(12 * self._ui_scale)
		self._end_card()

		rapidfire_config = self.config.rapidfire
		self._begin_card(
			"comb_rapid",
			"Rapid-fire",
			risk=bind_risk('rapidfire', lambda: self.config),
			help_text=(
				"Auto-clicks left mouse at a configurable rate while the "
				"hotkey is held. Lets semi-auto rifles (e.g. Deagle) "
				"fire at the absolute server-side cooldown limit."
			),
		)
		self._toggle("Enabled", rapidfire_config, "enabled")
		self._slider_float("Clicks/sec##rf", rapidfire_config, "cps", 1.0, 25.0)
		self._slider_float("Jitter (±)##rf", rapidfire_config, "jitter_pct", 0.0, 0.50)
		self._end_card()

		autopistol_config = self.config.autopistol
		self._begin_card(
			"comb_autopistol",
			"Auto-pistol",
			risk=bind_risk('autopistol', lambda: self.config),
			help_text=(
				"Same as rapid-fire but wired to your pistol so you don't "
				"have to spam the click yourself. Glock / Deagle become "
				"effectively full-auto without going past CS2's "
				"server-enforced per-shot cooldown."
			),
		)
		self._toggle("Enabled", autopistol_config, "enabled")
		self._slider_float("Clicks/sec##ap", autopistol_config, "cps", 1.0, 12.0)
		self._slider_float("Jitter (±)##ap", autopistol_config, "jitter_pct", 0.0, 0.50)
		self._end_card()

		bhop_config = self.config.bhop
		self._begin_card(
			"comb_bhop",
			"BHop (frame-perfect rejump)",
			risk=bind_risk('bhop', lambda: self.config),
			help_text=(
				"Polls m_fFlags & FL_ONGROUND each frame and injects a "
				"synthetic SPACE tap the instant you touch the ground.\n\n"
				"Server-side caveat: CS2's sv_autobunnyhopping cvar "
				"controls held-SPACE behaviour. Default 0 (Premier, "
				"matchmaking, Faceit) means held SPACE jumps once — our "
				"taps chain the jumps. Value 1 (training/community) "
				"means CS2 chains for you natively; our taps are "
				"redundant but harmless.\n\n"
				"Real bhop speed comes from manual air-strafing "
				"(mouse-swipe + opposite strafe key), which this script "
				"intentionally does not automate."
			),
		)
		self._toggle("Enabled", bhop_config, "enabled")
		self._slider_int("Min gap between jumps (ms)", bhop_config, "min_gap_ms", 20, 300)
		self._slider_int("Press jitter (±ms)", bhop_config, "delay_jitter_ms", 0, 30)
		self._toggle("Humanize (occasionally skip a bounce)", bhop_config, "humanize")
		if self._advanced("comb_bhop_adv"):
			self._slider_int("Skip every N — minimum N", bhop_config, "humanize_skip_min", 1, 30)
			self._slider_int("Skip every N — maximum N", bhop_config, "humanize_skip_max", 1, 30)
			self._slider_int("Skip-resume delay (ms)", bhop_config, "skip_resume_delay_ms", 20, 1000)
			imgui.unindent(12 * self._ui_scale)
		if int(getattr(bhop_config, "humanize_skip_min", 3)) > int(getattr(bhop_config, "humanize_skip_max", 15)):
			imgui.push_style_color(imgui.Col_.text.value, self._theme()["warn"])
			imgui.text_wrapped("⚠ min > max — will be auto-clamped at runtime.")
			imgui.pop_style_color()
		self._end_card()

	def _draw_distance_bands(self) -> None:
		'''Editor for the triggerbot distance-banded FOV gates; bands are dicts from [[triggerbot.distance_bands]].'''
		trigger_config = self.config.triggerbot
		bands = list(getattr(trigger_config, "distance_bands", []) or [])

		def _default_bands() -> list[dict]:
			return [
				{"min_m": 4.0, "max_m": 9999.0, "fov_deg": 0.5, "skeleton_fov_deg": 0.5, "label": "long"},
				{"min_m": 2.0, "max_m": 4.0, "fov_deg": 2.0, "skeleton_fov_deg": 1.0, "label": "mid"},
				{"min_m": 0.5, "max_m": 2.0, "fov_deg": 4.0, "skeleton_fov_deg": 2.0, "label": "close"},
				{"min_m": 0.0, "max_m": 0.5, "fov_deg": 8.0, "skeleton_fov_deg": 4.0, "label": "point_blank"},
			]

		if not bands:
			bands = _default_bands()
			trigger_config.distance_bands = bands

		imgui.text("Distance bands — [min_m, max_m) → (fov_deg, skel_fov)")
		imgui.same_line()
		if imgui.button("+ band##tbb"):
			new_max = max((float(band.get("max_m", 0.0)) for band in bands), default=10.0) + 10.0
			bands.append({"min_m": new_max - 5.0, "max_m": new_max, "fov_deg": 1.0, "skeleton_fov_deg": 1.0, "label": "new"})
		imgui.same_line()
		if imgui.button("reset##tbb"):
			bands = _default_bands()

		new_bands: list[dict] = []
		to_remove = -1
		for index, band in enumerate(bands):
			imgui.push_id(index)
			label = str(band.get("label", "?"))
			palette = self._theme()
			imgui.push_style_color(imgui.Col_.text.value, palette["accent"])
			imgui.text(f" band {index} ({label})")
			imgui.pop_style_color()
			imgui.same_line()
			if imgui.button(f"X##b{index}"):
				to_remove = index
			min_m = float(band.get("min_m", 0.0))
			max_m = float(band.get("max_m", 9999.0))
			fov_degrees = float(band.get("fov_deg", 1.0))
			skeleton_fov = float(band.get("skeleton_fov_deg", 1.0))
			_, new_min = imgui.slider_float(f"min_m##b{index}", min_m, 0.0, 200.0, "%.1f")
			_, new_max = imgui.slider_float(f"max_m##b{index}", max_m, 0.0, 9999.0, "%.1f")
			_, new_fov = imgui.slider_float(f"fov_deg##b{index}", fov_degrees, 0.05, 12.0, "%.2f")
			_, new_skeleton_fov = imgui.slider_float(f"skeleton_fov_deg##b{index}", skeleton_fov, 0.05, 12.0, "%.2f")
			new_bands.append({"min_m": float(new_min), "max_m": float(new_max), "fov_deg": float(new_fov), "skeleton_fov_deg": float(new_skeleton_fov), "label": label})
			imgui.pop_id()
		if to_remove >= 0 and len(new_bands) > 1:
			new_bands.pop(to_remove)
		try:
			trigger_config.distance_bands = new_bands
		except Exception:
			pass

	def _panel_anti_detection(self) -> None:
		anti_detection = self.config.anti_detection
		self._h1("Anti-Detection")
		self._body(
			(
				'Window presentation and read scheduling options are not anti-cheat bypasses. The read-only setting gates selected normal '
				'calls, not every writer or input path.'
			),
			dim=True,
		)
		imgui.dummy(imgui.ImVec2(1, 8))
		self._h2("Overlay & process")
		self._begin_card(
			"ad_overlay",
			"Overlay & process hiding",
			risk=bind_risk('capture_exclusion', lambda: self.config),
			help_text=(
				'Requests own-window capture exclusion, taskbar presentation changes and title variation. The capture API covers only '
				'specific OS paths; these settings do not hide every process or capture record.'
			),
		)
		self._toggle("Hide overlay from screen capture", anti_detection, "hide_overlay_from_capture")
		self._toggle("Hide from taskbar", anti_detection, "hide_from_taskbar")
		self._toggle("Randomise window title each launch", anti_detection, "randomize_window_title")
		self._toggle("Auto-protect capture on focus loss", anti_detection, "auto_capture_protect_on_focus_loss")
		self._end_card()

		self._begin_card(
			"ad_reads",
			"Read pattern shaping",
			risk=bind_risk('read_jitter', lambda: self.config),
			help_text=(
				'Adds random sleep to the normal render/read-loop tail. The background reader may have a different cadence. No detector '
				'benefit or approval is established.'
			),
		)
		self._slider_int("Read jitter (ms)", anti_detection, "randomize_read_jitter_ms", 0, 10)
		self._end_card()

		self._begin_card(
			"ad_writes",
			"Write safety",
			risk=bind_risk('write_guard', lambda: self.config),
			help_text=(
				'The read-only scheduling flag gates normal Aimbot/Glow and selected input calls. The handle remains write-capable; Glow '
				'cleanup and recoil input are not globally blocked.'
			),
		)
		self._toggle('Read-only scheduling guard', anti_detection, "read_only_mode")
		self._end_card()

	def _update_offsets_clicked(self) -> None:
		'''Handler for the Update Offsets button.'''
		try:
			from cs2esp.offsets import refresh_offsets_live

			report = refresh_offsets_live()
			try:
				from cs2esp.ipc import request_offset_refresh

				request_offset_refresh()
			except Exception:
				pass
			if report.get("success"):
				print("[menu] Offsets updated and healed successfully!")
			else:
				print(f"[menu] Offset update partial/failed: {report.get('error')}")
		except Exception as e:
			print(f"[menu] Offset update raised: {e!r}")

	def _panel_settings(self) -> None:
		general_config = self.config.general
		self._h1("Settings")
		self._body("App-wide preferences: window behaviour, theme, autosave, and developer toggles.", dim=True)
		imgui.dummy(imgui.ImVec2(1, 8))

		self._h2("Display")
		self._begin_card(
			"set_display",
			"Display",
			risk=bind_risk('appearance', lambda: self.config),
			help_text=("Theme + UI scale. Theme picker and the sidebar pill share the same config field."),
		)
		imgui.text("Theme")
		for theme_name in _THEME_ORDER:
			if imgui.radio_button(f" {_THEME_GLYPH[theme_name]} {_THEME_LABEL[theme_name]}", self._theme_name == theme_name):
				self._set_theme(theme_name)
		imgui.dummy(imgui.ImVec2(1, 4))
		cur_scale = float(getattr(general_config, "ui_scale", 0.0) or 0.0)
		imgui.text("UI scale (0 = auto)")
		changed, new = imgui.slider_float("##uiscale", cur_scale, 0.0, 2.5, "%.2f")
		if changed:
			try:
				general_config.ui_scale = float(new)
				self._ui_scale = self._compute_ui_scale()
				self._apply_theme()
			except Exception:
				pass
		self._toggle("Open 'Show advanced' sections by default", general_config, "menu_show_advanced_default")
		self._advanced_default = bool(getattr(general_config, "menu_show_advanced_default", False))
		self._end_card()

		self._begin_card(
			"set_window",
			"Window behaviour",
			risk=bind_risk('window_behavior', lambda: self.config),
			help_text=("Controls how the menu surfaces on screen and whether CS2 receives focus on launch."),
		)
		self._toggle("External window (separate OS window)", general_config, "menu_external_window")
		self._toggle("Auto-focus CS2 on launch", general_config, "focus_cs2_on_launch")
		self._toggle("Start with menu visible", general_config, "start_menu_visible")
		self._end_card()

		self._begin_card(
			"set_persist",
			"Persistence",
			risk=bind_risk('persistence', lambda: self.config),
			help_text=(
				"Autosave writes config.toml on every change after a small debounce window. Disable if you want to test settings without committing them to disk."
			),
		)
		self._toggle("Autosave config on change", general_config, "autosave")
		self._slider_float("Autosave interval (s)", general_config, "autosave_interval_sec", 0.1, 10.0)
		self._slider_int("Target FPS", general_config, "target_fps", 60, 240)
		overlay_sec = getattr(self.config, "overlay", None)
		if overlay_sec is not None:
			self._toggle('V-Sync (presentation synchronization)', overlay_sec, "vsync")
		self._end_card()

		self._begin_card(
			"set_offsets",
			"Offsets",
			risk=bind_risk('offsets', lambda: self.config),
			help_text=(
				"Update game offsets from a2x/cs2-dumper. CS2 patches shift "
				"memory layouts; click this button after a game update to pull "
				"the latest offsets. The app auto-updates on startup, but this "
				"button lets you refresh mid-session without restarting."
			),
		)
		if imgui.button("Update Offsets Now"):
			self._update_offsets_clicked()
		imgui.same_line()
		imgui.push_style_color(imgui.Col_.text.value, self._theme()["text_dim"])
		imgui.text("(fetches from GitHub)")
		imgui.pop_style_color()
		self._end_card()

		self._begin_card(
			"set_diag",
			"Diagnostics",
			risk=bind_risk('persistence', lambda: self.config),
			help_text=("Read-only summary of the keys that affect global behaviour. Useful when reporting bugs."),
		)
		imgui.push_style_color(imgui.Col_.text.value, self._theme()["text_dim"])
		imgui.text(f"toggle_menu_key = {getattr(general_config, 'toggle_menu_key', '?')!r}")
		imgui.text(f"master_toggle_key = {getattr(general_config, 'master_toggle_key', '?')!r}")
		imgui.text(f"exit_key = {getattr(general_config, 'exit_key', '?')!r}")
		imgui.text(f"target_fps = {getattr(general_config, 'target_fps', '?')}")
		imgui.text(f"ui_scale (auto) = {self._ui_scale:.2f}")
		imgui.text(f"theme = {self._theme_name!r}")
		imgui.text(f"cs2 hwnd = {self.cs2_hwnd}")
		imgui.pop_style_color()
		self._end_card()
