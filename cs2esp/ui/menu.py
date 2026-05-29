"""
Legacy ImGui menu with sidebar, cards, controls, and keybind rows.

PySide6 is the primary UI, but this remains the in-overlay fallback when Qt is
unavailable or a pure ImGui surface is needed. It mirrors the Qt feature panels
while keeping its own palettes, immediate-mode layout, and context-local fonts.
"""

import ctypes
from time import monotonic
from typing import Callable

from imgui_bundle import imgui

from cs2esp.ui import fonts as ui_fonts

try:
    import keyboard as _kb # type: ignore
except Exception: # pragma: no cover
    _kb = None


def _hex(s: str, a: float = 1.0) -> imgui.ImVec4:
    """Parse #rrggbb into an ImVec4 with the given alpha."""
    s = s.lstrip("#")
    r = int(s[0:2], 16) / 255.0
    g = int(s[2:4], 16) / 255.0
    b = int(s[4:6], 16) / 255.0
    return imgui.ImVec4(r, g, b, a)


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

def _safe_combo(label: str, current_idx: int, items: list[str]) -> tuple[bool, int]:
    """Normalise imgui.combo to (changed, new_idx)."""
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
        self._show_advanced: dict[str, bool] = {}
        self._show_why: dict[str, bool] = {}

        self._ui_scale: float = self._compute_ui_scale()

        name = str(getattr(self.config.general, "ui_theme", "midnight") or "midnight")
        self._theme_name = name if name in _PALETTES else "midnight"
        self._advanced_default = bool(
            getattr(self.config.general, "menu_show_advanced_default", False)
        )
        self._apply_theme()

    def _compute_ui_scale(self) -> float:
        """Pick a UI scale from monitor height (user override wins).

        Auto: 1080p -> 1.55, 1440p -> 1.30, 4K -> 1.10.
        """
        try:
            user_scale = float(getattr(self.config.general, "ui_scale", 0.0) or 0.0)
            if user_scale > 0.05:
                return user_scale
            try:
                io = imgui.get_io()
                h = float(io.display_size.y)
            except Exception:
                h = 0.0
            if h <= 0:
                try:
                    h = float(ctypes.windll.user32.GetSystemMetrics(1))
                except Exception:
                    h = 1080.0
            if h >= 2160 - 100:
                return 1.10
            if h >= 1440 - 100:
                return 1.30
            return 1.55
        except Exception:
            return 1.55

    def _theme(self) -> dict[str, imgui.ImVec4]:
        """Return the active palette dict."""
        return _PALETTES.get(self._theme_name, _PALETTES["midnight"])

    def _apply_theme(self) -> None:
        """Paint the active palette onto the current ImGui context.

        Called at the top of every render entry point. Each context
        (overlay + external menu window) owns its own style array, so
        a theme applied to one is invisible in the other until we
        paint it there too. ``scale_all_sizes`` is the one operation
        that compounds, so we apply it exactly once per context via
        ``_scaled_ctxs``.
        """
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

            t = self._theme()

            def _set(col_enum, vec):
                try:
                    style.set_color_(col_enum.value, vec)
                except Exception:
                    pass

            _set(imgui.Col_.window_bg, t["bg_app"])
            _set(imgui.Col_.child_bg, t["surface_1"])
            _set(imgui.Col_.popup_bg, t["surface_1"])
            _set(imgui.Col_.menu_bar_bg, t["surface_1"])
            _set(imgui.Col_.title_bg, t["bg_sidebar"])
            _set(imgui.Col_.title_bg_active, t["bg_sidebar"])
            _set(imgui.Col_.title_bg_collapsed, t["bg_sidebar"])
            _set(imgui.Col_.scrollbar_bg, t["bg_sidebar"])
            _set(imgui.Col_.scrollbar_grab, t["border"])
            _set(imgui.Col_.scrollbar_grab_hovered, t["text_dim"])
            _set(imgui.Col_.scrollbar_grab_active, t["accent"])

            _set(imgui.Col_.border, t["border"])
            _set(imgui.Col_.separator, t["border"])
            _set(imgui.Col_.separator_hovered, t["accent"])
            _set(imgui.Col_.separator_active, t["accent_hi"])

            _set(imgui.Col_.text, t["text"])
            _set(imgui.Col_.text_disabled, t["text_dim"])

            _set(imgui.Col_.frame_bg, t["surface_2"])
            _set(imgui.Col_.frame_bg_hovered, self._mix(t["surface_2"], t["accent"], 0.18))
            _set(imgui.Col_.frame_bg_active, self._mix(t["surface_2"], t["accent"], 0.28))

            _set(imgui.Col_.button, t["surface_2"])
            _set(imgui.Col_.button_hovered, self._mix(t["surface_2"], t["accent"], 0.30))
            _set(imgui.Col_.button_active, t["accent"])

            _set(imgui.Col_.header, self._mix(t["surface_1"], t["accent"], 0.20))
            _set(imgui.Col_.header_hovered, self._mix(t["surface_1"], t["accent"], 0.35))
            _set(imgui.Col_.header_active, self._mix(t["surface_1"], t["accent"], 0.45))

            _set(imgui.Col_.slider_grab, t["accent"])
            _set(imgui.Col_.slider_grab_active, t["accent_hi"])
            _set(imgui.Col_.check_mark, t["accent"])

            _set(imgui.Col_.tab, t["surface_1"])
            _set(imgui.Col_.tab_hovered, self._mix(t["surface_1"], t["accent"], 0.30))
            _set(imgui.Col_.tab_selected, t["surface_2"])
            _set(imgui.Col_.tab_dimmed, t["surface_1"])
            _set(imgui.Col_.tab_dimmed_selected, t["surface_2"])

            _set(imgui.Col_.nav_cursor, t["accent"])
            _set(imgui.Col_.nav_windowing_highlight, t["accent"])
            _set(imgui.Col_.drag_drop_target, t["accent"])

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
        except Exception as e: # pragma: no cover
            print(f"[menu] _apply_theme failed: {e!r}")

    @staticmethod
    def _mix(a: imgui.ImVec4, b: imgui.ImVec4, t: float) -> imgui.ImVec4:
        """Linear blend two ImVec4 colours, ``t=0`` -> a, ``t=1`` -> b."""
        return imgui.ImVec4(
            a.x + (b.x - a.x) * t,
            a.y + (b.y - a.y) * t,
            a.z + (b.z - a.z) * t,
            a.w + (b.w - a.w) * t,
        )

    def _set_theme(self, name: str) -> None:
        """Switch to a named palette and persist the choice to config."""
        if name not in _PALETTES:
            return
        self._theme_name = name
        try:
            self.config.general.ui_theme = name
        except Exception:
            pass
        self._apply_theme()

    def invalidate_current_context_state(self) -> None:
        """Forget one-shot style state after the overlay context is rebuilt."""
        try:
            context_id = id(imgui.get_current_context())
        except Exception:
            return
        if hasattr(self, "_scaled_ctxs"):
            self._scaled_ctxs.discard(context_id)

    def _push_theme_colors(self) -> int:
        """Push the active palette as imgui style overrides.

        Returns the count to feed back into :meth:`_pop_theme_colors`.
        """
        t = self._theme()
        pushes = [
            (imgui.Col_.text, t["text"]),
            (imgui.Col_.text_disabled, t["text_dim"]),
            (imgui.Col_.window_bg, t["bg_app"]),
            (imgui.Col_.child_bg, t["surface_1"]),
            (imgui.Col_.popup_bg, t["surface_1"]),
            (imgui.Col_.border, t["border"]),
            (imgui.Col_.separator, t["border"]),
            (imgui.Col_.frame_bg, t["surface_2"]),
            (imgui.Col_.frame_bg_hovered, self._mix(t["surface_2"], t["accent"], 0.18)),
            (imgui.Col_.frame_bg_active, self._mix(t["surface_2"], t["accent"], 0.28)),
            (imgui.Col_.button, t["surface_2"]),
            (imgui.Col_.button_hovered, self._mix(t["surface_2"], t["accent"], 0.30)),
            (imgui.Col_.button_active, t["accent"]),
            (imgui.Col_.slider_grab, t["accent"]),
            (imgui.Col_.slider_grab_active, t["accent_hi"]),
            (imgui.Col_.check_mark, t["accent"]),
            (imgui.Col_.header, self._mix(t["surface_1"], t["accent"], 0.20)),
            (imgui.Col_.header_hovered, self._mix(t["surface_1"], t["accent"], 0.35)),
            (imgui.Col_.header_active, self._mix(t["surface_1"], t["accent"], 0.45)),
            (imgui.Col_.scrollbar_bg, t["bg_sidebar"]),
            (imgui.Col_.scrollbar_grab, t["border"]),
            (imgui.Col_.scrollbar_grab_hovered, t["text_dim"]),
            (imgui.Col_.scrollbar_grab_active, t["accent"]),
        ]
        n = 0
        for col, vec in pushes:
            try:
                imgui.push_style_color(col.value, vec)
                n += 1
            except Exception:
                pass
        return n

    def _pop_theme_colors(self, n: int) -> None:
        if n > 0:
            try:
                imgui.pop_style_color(n)
            except Exception:
                pass

    def _push_font_kind(self, kind: str) -> bool:
        """Push the body / h2 / h1 font for the current ImGui context.

        Returns True on success; feed that back to _pop_font_kind.
        """
        cached = ui_fonts.get_fonts_for_current_context()
        if cached is None:
            return False
        font_ptr = {
            "body": cached.body,
            "h2": cached.h2,
            "h1": cached.h1,
        }.get(kind)
        return ui_fonts.push(font_ptr)

    def _pop_font_kind(self, pushed: bool) -> None:
        ui_fonts.pop(pushed)

    def _h1(self, text: str) -> None:
        """Big, bold panel-level title (~28 px)."""
        t = self._theme()
        pushed = self._push_font_kind("h1")
        imgui.push_style_color(imgui.Col_.text.value, t["text"])
        imgui.text(text)
        imgui.pop_style_color()
        self._pop_font_kind(pushed)
        try:
            avail = imgui.get_content_region_avail().x
            p = imgui.get_cursor_screen_pos()
            dl = imgui.get_window_draw_list()
            dl.add_rect_filled(
                imgui.ImVec2(p.x, p.y - 4.0),
                imgui.ImVec2(p.x + min(avail, 96.0), p.y - 2.0),
                imgui.color_convert_float4_to_u32(t["accent"]),
            )
        except Exception:
            pass
        imgui.dummy(imgui.ImVec2(1, 6))

    def _h2(self, text: str) -> None:
        """Section title inside a panel (~22 px, bold)."""
        t = self._theme()
        imgui.dummy(imgui.ImVec2(1, 4))
        pushed = self._push_font_kind("h2")
        imgui.push_style_color(imgui.Col_.text.value, t["accent_hi"])
        imgui.text(text)
        imgui.pop_style_color()
        self._pop_font_kind(pushed)
        imgui.dummy(imgui.ImVec2(1, 2))

    def _body(self, text: str, *, dim: bool = False) -> None:
        """Plain body text. ``dim=True`` uses ``text_dim`` for tertiary copy."""
        t = self._theme()
        pushed = self._push_font_kind("body")
        col = t["text_dim"] if dim else t["text"]
        imgui.push_style_color(imgui.Col_.text.value, col)
        imgui.text_wrapped(text)
        imgui.pop_style_color()
        self._pop_font_kind(pushed)

    def toggle(self) -> None:
        """Flip the menu's visibility. Called by the global hotkey."""
        new_state = not self.visible
        if new_state:
            self.visible = True
            use_external = bool(getattr(
                self.config.general, "menu_external_window", False,
            ))
            try:
                self.overlay.set_click_through(True if use_external else False)
            except Exception:
                pass
        else:
            self._on_menu_closed()

    def _on_menu_closed(self) -> None:
        """On close: re-enable click-through, cancel any rebind
        capture, and best-effort focus CS2 so the player can shoot."""
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
        """Draw the menu for this frame.

        ``main.py`` calls this twice per frame from two ImGui contexts
        (overlay + external menu window). Only one of them paints,
        depending on ``general.menu_external_window``; painting in
        both would duplicate the menu and leak an unfinished frame.
        """
        if not self.visible:
            return

        use_external = bool(getattr(self.config.general,
                                    "menu_external_window", False))
        if use_external:
            if in_external_window:
                self._draw_menu_external()
        else:
            if not in_external_window:
                self._draw_menu_embedded()

    def _draw_menu_embedded(self) -> None:
        """Draw the menu as an ImGui window inside the overlay surface."""
        self._apply_theme()
        n_theme = self._push_theme_colors()
        body_pushed = self._push_font_kind("body")
        try:
            io = imgui.get_io()
            display_size = io.display_size
            w = max(880.0, min(1100.0, display_size.x * 0.62))
            h = max(560.0, min(720.0, display_size.y * 0.78))
            x = (display_size.x - w) * 0.5
            y = (display_size.y - h) * 0.5
            imgui.set_next_window_pos(imgui.ImVec2(x, y), imgui.Cond_.appearing.value)
            imgui.set_next_window_size(imgui.ImVec2(w, h), imgui.Cond_.appearing.value)

            flags = (
                imgui.WindowFlags_.no_collapse.value
                | imgui.WindowFlags_.no_scrollbar.value
                | imgui.WindowFlags_.no_scroll_with_mouse.value
            )
            _, visible = imgui.begin("MS Mesh · CS2 ESP", True, flags)
            if not visible:
                self._on_menu_closed()
            else:
                self._draw_menu_body()
            imgui.end()
        except Exception as e: # pragma: no cover
            print(f"[menu] embedded render failed: {e!r}")
        finally:
            self._pop_font_kind(body_pushed)
            self._pop_theme_colors(n_theme)

    def _draw_menu_external(self) -> None:
        """Draw the menu as the entire content of the external window.

        The OS window owns chrome; we paint a full-window ImGui panel
        without a title bar (the top strip is our own).
        """
        self._apply_theme()
        n_theme = self._push_theme_colors()
        body_pushed = self._push_font_kind("body")
        try:
            io = imgui.get_io()
            imgui.set_next_window_pos(imgui.ImVec2(0, 0))
            imgui.set_next_window_size(io.display_size)
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
        except Exception as e: # pragma: no cover
            print(f"[menu] external render failed: {e!r}")
        finally:
            self._pop_font_kind(body_pushed)
            self._pop_theme_colors(n_theme)

    def _draw_menu_body(self) -> None:
        """Render the inside of the menu window: chrome + content."""
        avail = imgui.get_content_region_avail()
        scale = self._ui_scale
        top_h = self._TOPSTRIP_H * scale
        bot_h = self._BOTSTRIP_H * scale
        side_w = self._SIDEBAR_W * scale
        body_h = avail.y - top_h - bot_h

        self._draw_top_strip(top_h)
        imgui.dummy(imgui.ImVec2(1, 4))

        imgui.begin_child("##sidebar", imgui.ImVec2(side_w, body_h),
                          imgui.ChildFlags_.borders.value, 0)
        self._draw_sidebar()
        imgui.end_child()

        imgui.same_line()
        imgui.begin_child("##content", imgui.ImVec2(0, body_h),
                          imgui.ChildFlags_.borders.value, 0)
        for key, _label, method_name in self._CATEGORIES:
            if key != self._active_category:
                continue
            panel = getattr(self, method_name, None)
            if panel is not None:
                try:
                    panel()
                except Exception as exc:  # pragma: no cover
                    imgui.text_colored(
                        self._theme()["danger"], f"panel {key} crashed: {exc!r}",
                    )
            break
        else:
            imgui.text("?? unknown panel")
        imgui.end_child()

        self._draw_bottom_strip(bot_h)

    def _draw_top_strip(self, height: float) -> None:
        """Compact header with brand, attached-process pill, theme
        switcher and master-toggle indicator."""
        t = self._theme()
        scale = self._ui_scale
        imgui.push_style_color(imgui.Col_.child_bg.value, t["bg_sidebar"])
        imgui.push_style_var(imgui.StyleVar_.child_rounding.value, 8.0)
        imgui.push_style_var(imgui.StyleVar_.window_padding.value,
                             imgui.ImVec2(12, 6))
        imgui.begin_child("##topstrip", imgui.ImVec2(0, height),
                          imgui.ChildFlags_.borders.value,
                          imgui.WindowFlags_.no_scrollbar.value)

        imgui.push_style_color(imgui.Col_.text.value, t["accent"])
        imgui.text("MS Mesh")
        imgui.pop_style_color()
        imgui.same_line()
        imgui.push_style_color(imgui.Col_.text.value, t["text_dim"])
        imgui.text("· CS2 ESP")
        imgui.pop_style_color()

        imgui.same_line()
        imgui.dummy(imgui.ImVec2(20 * scale, 1))
        imgui.same_line()
        attached = "cs2.exe" if self.cs2_hwnd else "(no attach)"
        ok = bool(self.cs2_hwnd)
        self._badge_pill(("● " + attached) if ok else ("○ " + attached),
                         t["safe"] if ok else t["danger"])

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
            t = self._theme()
            avail = imgui.get_content_region_avail()
            p = imgui.get_cursor_screen_pos()
            dl = imgui.get_window_draw_list()
            dl.add_rect_filled(
                imgui.ImVec2(p.x, p.y - 2.0),
                imgui.ImVec2(p.x + avail.x, p.y - 1.0),
                imgui.color_convert_float4_to_u32(t["accent_glow"]),
            )
        except Exception:
            pass

    def _badge_pill(self, text: str, bg: imgui.ImVec4,
                    fg: imgui.ImVec4 | None = None) -> None:
        """Inline rounded-rect pill behind label text.

        Non-clickable (renders into the draw list around an invisible
        button so layout still flows correctly).
        """
        t = self._theme()
        if fg is None:
            fg = _hex("#0a0c12") if bg.x + bg.y + bg.z > 1.5 else t["text"]
        scale = self._ui_scale
        pad_x = 10 * scale
        pad_y = 3 * scale
        size = imgui.calc_text_size(text)
        w = size.x + pad_x * 2
        h = size.y + pad_y * 2
        p = imgui.get_cursor_screen_pos()
        dl = imgui.get_window_draw_list()
        dl.add_rect_filled(
            imgui.ImVec2(p.x, p.y),
            imgui.ImVec2(p.x + w, p.y + h),
            imgui.color_convert_float4_to_u32(bg),
            6.0,
        )
        dl.add_text(imgui.ImVec2(p.x + pad_x, p.y + pad_y),
                    imgui.color_convert_float4_to_u32(fg), text)
        imgui.dummy(imgui.ImVec2(w, h))

    def _draw_theme_switcher(self) -> None:
        """Theme pill - click cycles, right-click opens a picker."""
        t = self._theme()
        glyph = _THEME_GLYPH[self._theme_name]
        label = _THEME_LABEL[self._theme_name]
        text = f" {glyph} {label} ▾ "

        imgui.push_style_color(imgui.Col_.button.value, t["surface_2"])
        imgui.push_style_color(imgui.Col_.button_hovered.value,
                               self._mix(t["surface_2"], t["accent"], 0.30))
        imgui.push_style_color(imgui.Col_.button_active.value, t["accent"])
        imgui.push_style_color(imgui.Col_.text.value, t["accent"])
        clicked = imgui.button(text + "##theme_switcher")
        imgui.pop_style_color(4)

        if imgui.is_item_hovered() and imgui.is_mouse_clicked(1):
            imgui.open_popup("##theme_picker")

        if clicked:
            cur = _THEME_ORDER.index(self._theme_name)
            self._set_theme(_THEME_ORDER[(cur + 1) % len(_THEME_ORDER)])

        if imgui.begin_popup("##theme_picker"):
            imgui.text("Theme")
            imgui.separator()
            for tname in _THEME_ORDER:
                label_text = f"{_THEME_GLYPH[tname]} {_THEME_LABEL[tname]}"
                selected = (self._theme_name == tname)
                if imgui.menu_item(label_text, "", selected)[0]:
                    self._set_theme(tname)
            imgui.end_popup()

        if imgui.is_item_hovered():
            self._tooltip(
                "Click to cycle themes · Right-click for direct pick\n"
                "Persists across launches via autosave."
            )

    def set_master_status_provider(self, fn: Callable[[], bool]) -> None:
        """Register a getter that returns the live master-toggle state.

        ``main.py`` owns ``master_enabled`` as a closure; this is how
        the top-strip pill reads it.
        """
        self._master_status_fn = fn

    def _draw_master_toggle_pill(self) -> None:
        """Read-only ON/OFF indicator for the master switch.

        Falls back to a neutral 'MASTER' label if main.py hasn't
        plumbed the callback yet.
        """
        t = self._theme()
        fn = getattr(self, "_master_status_fn", None)
        if fn is None:
            self._badge_pill(
                f" MASTER ({str(getattr(self.config.general, 'master_toggle_key', 'k')).upper()}) ",
                t["surface_2"], t["accent"])
            return
        try:
            master_on = bool(fn())
        except Exception:
            master_on = True
        bg = t["safe"] if master_on else t["danger"]
        fg = _hex("#0a0c12")
        text = f" MASTER: {'ON' if master_on else 'OFF'} "
        self._badge_pill(text, bg, fg)

    def _draw_bottom_strip(self, height: float) -> None:
        """One-line cheat sheet of the most-used keybinds + version tag."""
        t = self._theme()
        imgui.push_style_color(imgui.Col_.child_bg.value, t["bg_sidebar"])
        imgui.push_style_var(imgui.StyleVar_.child_rounding.value, 8.0)
        imgui.push_style_var(imgui.StyleVar_.window_padding.value,
                             imgui.ImVec2(12, 4))
        imgui.begin_child("##botstrip", imgui.ImVec2(0, height),
                          imgui.ChildFlags_.borders.value,
                          imgui.WindowFlags_.no_scrollbar.value)
        g = self.config.general
        bind_str = (
            f" {str(getattr(g, 'toggle_menu_key', 'j')).upper()} menu"
            f" · {str(getattr(g, 'master_toggle_key', 'k')).upper()} master"
            f" · {str(getattr(g, 'exit_key', 'l')).upper()} exit"
            f" · ESC cancel rebind"
        )
        imgui.push_style_color(imgui.Col_.text.value, t["text_dim"])
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
        """Vertical navigation: STATUS block + 8 category buttons."""
        t = self._theme()

        self._sidebar_section_label("STATUS")
        try:
            io = imgui.get_io()
            fps = io.framerate
        except Exception:
            fps = 0.0
        imgui.push_style_color(imgui.Col_.text.value, t["text_dim"])
        imgui.text(f" {fps:5.0f} fps")
        attach_state = "attached" if self.cs2_hwnd else "no attach"
        imgui.text(f" cs2 {attach_state}")
        imgui.pop_style_color()

        imgui.dummy(imgui.ImVec2(1, 12))

        self._sidebar_section_label("NAVIGATION")
        for key, label, _method in self._CATEGORIES:
            self._sidebar_nav_item(key, label)

    def _sidebar_section_label(self, label: str) -> None:
        """Accent bar + uppercase title (sub-divides STATUS / NAV)."""
        t = self._theme()
        scale = self._ui_scale
        p = imgui.get_cursor_screen_pos()
        dl = imgui.get_window_draw_list()
        bar_h = imgui.get_text_line_height()
        dl.add_rect_filled(
            imgui.ImVec2(p.x, p.y + 2 * scale),
            imgui.ImVec2(p.x + 3 * scale, p.y + bar_h),
            imgui.color_convert_float4_to_u32(t["accent"]),
        )
        imgui.dummy(imgui.ImVec2(8 * scale, 1))
        imgui.same_line()
        imgui.push_style_color(imgui.Col_.text.value, t["text_dim"])
        imgui.text(label)
        imgui.pop_style_color()

    def _sidebar_nav_item(self, key: str, label: str) -> None:
        """One nav row.

        Active state stacks three layers: translucent accent_glow
        rectangle, accent-tinted fill, then a bright 4 px accent
        stripe on the left edge. Hover rows get a subtle surface_2
        fill so the cursor always has something to land on.
        """
        t = self._theme()
        scale = self._ui_scale
        is_active = (self._active_category == key)

        avail = imgui.get_content_region_avail()
        row_h = (32.0 * scale)
        p = imgui.get_cursor_screen_pos()
        dl = imgui.get_window_draw_list()

        imgui.invisible_button(f"##nav_{key}", imgui.ImVec2(avail.x, row_h))
        hovered = imgui.is_item_hovered()
        clicked = imgui.is_item_clicked(0)
        if clicked:
            self._active_category = key

        if is_active:
            dl.add_rect_filled(
                imgui.ImVec2(p.x - 4 * scale, p.y - 2 * scale),
                imgui.ImVec2(p.x + avail.x + 4 * scale, p.y + row_h + 2 * scale),
                imgui.color_convert_float4_to_u32(t["accent_glow"]),
                6.0,
            )
        if is_active or hovered:
            if is_active:
                fill = self._mix(t["surface_1"], t["accent"], 0.22)
            else:
                fill = self._mix(t["surface_1"], t["text_dim"], 0.10)
            dl.add_rect_filled(
                imgui.ImVec2(p.x, p.y),
                imgui.ImVec2(p.x + avail.x, p.y + row_h),
                imgui.color_convert_float4_to_u32(fill),
                4.0,
            )

        if is_active:
            dl.add_rect_filled(
                imgui.ImVec2(p.x, p.y + 4 * scale),
                imgui.ImVec2(p.x + 4 * scale, p.y + row_h - 4 * scale),
                imgui.color_convert_float4_to_u32(t["accent"]),
                1.5,
            )

        text_y_offset = (row_h - imgui.get_text_line_height()) * 0.5
        text_color = t["accent_hi"] if is_active else t["text"]
        dl.add_text(
            imgui.ImVec2(p.x + 16 * scale, p.y + text_y_offset),
            imgui.color_convert_float4_to_u32(text_color),
            label,
        )

    def _begin_card(self, key: str, title: str, *,
                    risk: str | None = None,
                    help_text: str | None = None,
                    caveat: str | None = None) -> None:
        """Open a feature card; the caller must finish it with `_end_card`."""
        t = self._theme()
        imgui.push_style_color(imgui.Col_.child_bg.value, t["surface_2"])
        imgui.push_style_color(
            imgui.Col_.border.value,
            self._mix(t["border"], t["accent"], 0.18),
        )
        imgui.push_style_var(imgui.StyleVar_.child_rounding.value, 12.0)
        imgui.push_style_var(imgui.StyleVar_.child_border_size.value, 1.0)
        imgui.push_style_var(imgui.StyleVar_.window_padding.value,
                             imgui.ImVec2(16, 14))
        imgui.begin_child(f"##card_{key}", imgui.ImVec2(0, 0),
                          imgui.ChildFlags_.borders.value
                          | imgui.ChildFlags_.auto_resize_y.value,
                          imgui.WindowFlags_.no_scrollbar.value)

        scale = self._ui_scale
        position = imgui.get_cursor_screen_pos()
        draw_list = imgui.get_window_draw_list()
        bar_height = imgui.get_text_line_height()
        draw_list.add_rect_filled(
            imgui.ImVec2(position.x, position.y + 2 * scale),
            imgui.ImVec2(position.x + 3 * scale, position.y + bar_height),
            imgui.color_convert_float4_to_u32(t["accent"]),
        )
        imgui.dummy(imgui.ImVec2(8 * scale, 1))
        imgui.same_line()
        imgui.push_style_color(imgui.Col_.text.value, t["text"])
        imgui.text(title)
        imgui.pop_style_color()

        full_text = help_text or "No details for this feature yet."
        if risk == "medium":
            full_text = "RISK · MEDIUM — elevated detection profile.\n\n" + full_text
            icon_colour = t["warn"]
        elif risk == "high":
            full_text = "RISK · HIGH — known-flagged technique.\n\n" + full_text
            icon_colour = t["danger"]
        elif risk == "safe":
            full_text = "SAFE — no known detection vector for this feature.\n\n" + full_text
            icon_colour = t["accent_hi"]
        else:
            icon_colour = t["text"]

        help_width = imgui.calc_text_size(" (?) ").x + 20 * scale
        available_width = imgui.get_content_region_avail().x
        imgui.same_line()
        imgui.dummy(imgui.ImVec2(
            max(0.0, available_width - help_width - 4 * scale), 1,
        ))
        imgui.same_line()
        imgui.push_style_color(imgui.Col_.text.value, icon_colour)
        imgui.text(" (?) ")
        imgui.pop_style_color()
        if imgui.is_item_hovered():
            self._tooltip(full_text)
        imgui.dummy(imgui.ImVec2(1, 4))

        if caveat:
            imgui.push_style_color(imgui.Col_.text.value, t["warn"])
            imgui.text_wrapped(f"⚠ {caveat}")
            imgui.pop_style_color()
            imgui.dummy(imgui.ImVec2(1, 6))

    def _end_card(self) -> None:
        """Close a card opened with ``_begin_card``."""
        imgui.end_child()
        imgui.pop_style_var(3)
        imgui.pop_style_color(2)
        imgui.dummy(imgui.ImVec2(1, 12))

    def _tooltip(self, text: str) -> None:
        """Rich tooltip with theme styling."""
        t = self._theme()
        imgui.push_style_color(imgui.Col_.popup_bg.value, t["surface_1"])
        imgui.push_style_color(imgui.Col_.border.value, t["border"])
        imgui.push_style_color(imgui.Col_.text.value, t["text"])
        imgui.begin_tooltip()
        imgui.push_text_wrap_pos(420 * self._ui_scale)
        imgui.text_wrapped(text)
        imgui.pop_text_wrap_pos()
        imgui.end_tooltip()
        imgui.pop_style_color(3)

    def _advanced(self, key: str, label: str = "Show advanced") -> bool:
        """Render a collapsible header and indent its open body."""
        if key not in self._show_advanced:
            self._show_advanced[key] = self._advanced_default
        is_open = self._show_advanced[key]
        arrow = "▾" if is_open else "▸"
        if imgui.selectable(f" {arrow} {label}", False,
                            imgui.SelectableFlags_.no_auto_close_popups.value)[0]:
            self._show_advanced[key] = not is_open
        is_open = self._show_advanced.get(key, False)
        if is_open:
            imgui.indent(12 * self._ui_scale)
        return is_open

    def _toggle(self, label: str, section, attr: str) -> bool:
        """Pill-style toggle switch.

        Mutates ``getattr(section, attr)`` in place; returns ``True``
        if the value changed this frame.
        """
        t = self._theme()
        scale = self._ui_scale
        cur = bool(getattr(section, attr, False))
        pill_w = 36 * scale
        pill_h = 18 * scale

        p = imgui.get_cursor_screen_pos()
        imgui.invisible_button(f"##tg_{label}_{attr}", imgui.ImVec2(pill_w, pill_h))
        clicked = imgui.is_item_clicked(0)
        if clicked:
            cur = not cur
            try:
                setattr(section, attr, cur)
            except Exception:
                pass
        hovered = imgui.is_item_hovered()
        dl = imgui.get_window_draw_list()
        bg = t["accent"] if cur else t["surface_1"]
        if hovered:
            bg = t["accent_hi"] if cur else self._mix(t["surface_1"], t["accent"], 0.20)
        dl.add_rect_filled(
            imgui.ImVec2(p.x, p.y),
            imgui.ImVec2(p.x + pill_w, p.y + pill_h),
            imgui.color_convert_float4_to_u32(bg),
            pill_h * 0.5,
        )
        knob_r = (pill_h * 0.5) - (2 * scale)
        knob_x = p.x + (pill_w - pill_h * 0.5 - 2 * scale) if cur else (p.x + pill_h * 0.5 + 2 * scale)
        knob_y = p.y + pill_h * 0.5
        knob_color = _hex("#0a0c12") if (cur and self._theme_name != "daylight") else _hex("#ffffff")
        dl.add_circle_filled(
            imgui.ImVec2(knob_x, knob_y),
            knob_r,
            imgui.color_convert_float4_to_u32(knob_color),
        )
        imgui.same_line()
        imgui.text(label)
        return clicked

    _SLIDER_W = 260.0

    def _slider_float(self, label: str, section, attr: str,
                      lo: float, hi: float, fmt: str = "%.2f") -> bool:
        cur = float(getattr(section, attr, 0.0))
        imgui.set_next_item_width(self._SLIDER_W * self._ui_scale)
        changed, new = imgui.slider_float(label, cur, lo, hi, fmt)
        if changed:
            try:
                setattr(section, attr, float(new))
            except Exception:
                pass
        return bool(changed)

    def _slider_int(self, label: str, section, attr: str,
                    lo: int, hi: int) -> bool:
        cur = int(getattr(section, attr, 0))
        imgui.set_next_item_width(self._SLIDER_W * self._ui_scale)
        changed, new = imgui.slider_int(label, cur, lo, hi)
        if changed:
            try:
                setattr(section, attr, int(new))
            except Exception:
                pass
        return bool(changed)

    def _color_edit(self, label: str, section, attr: str) -> bool:
        cur = list(getattr(section, attr, [1.0, 1.0, 1.0, 1.0]))
        cur = (cur + [1.0, 1.0, 1.0, 1.0])[:4]
        changed, new = imgui.color_edit4(
            label, cur,
            imgui.ColorEditFlags_.alpha_bar.value
            | imgui.ColorEditFlags_.no_inputs.value,
        )
        if changed:
            try:
                setattr(section, attr, list(new))
            except Exception:
                pass
        return bool(changed)

    def _capture_pump(self) -> str | None:
        """Poll keys while a capture is active.

        Returns the lowercase key name on the next up->down edge, or
        ``None``. ESC cancels.
        """
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
            + ["space", "tab", "enter", "shift", "ctrl", "alt",
               "left", "right", "up", "down",
               "home", "end", "page up", "page down", "insert", "delete",
               "[", "]", ";", "'", ",", ".", "/", "\\", "`", "-", "="]
            + [f"f{i}" for i in range(1, 13)]
        )
        now_down: set[str] = set()
        for k in candidates:
            try:
                if _kb.is_pressed(k):
                    now_down.add(k)
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

    def _keybind_row_aligned(self, label: str, section, attr: str,
                             *, capture_id: str | None = None) -> None:
        """Generic 3-column keybind row.

        ``capture_id`` defaults to ``attr`` and disambiguates rows
        that share an attribute name on different sections (e.g.
        ``triggerbot.hotkey`` vs ``bhop.hotkey``).
        """
        t = self._theme()
        scale = self._ui_scale
        if capture_id is None:
            capture_id = attr
        current = str(getattr(section, attr, ""))
        is_capturing = (self._capturing == capture_id)

        row_start_x = imgui.get_cursor_pos_x()

        imgui.text(label)
        imgui.same_line()
        imgui.set_cursor_pos_x(row_start_x + self._KEYBIND_LABEL_W * scale)

        if is_capturing:
            elapsed = monotonic() - self._capture_started_at
            imgui.push_style_color(imgui.Col_.text.value, t["warn"])
            imgui.text(f"press… {elapsed:0.1f}s")
            imgui.pop_style_color()
        else:
            imgui.push_style_color(
                imgui.Col_.text.value,
                t["accent_hi"] if current else t["text_dim"],
            )
            imgui.text(current if current else "<unset>")
            imgui.pop_style_color()

        imgui.same_line()
        imgui.set_cursor_pos_x(
            row_start_x
            + (self._KEYBIND_LABEL_W + self._KEYBIND_KEY_W + self._KEYBIND_GAP) * scale
        )

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
                    print(f"[menu] master_toggle_key -> {captured!r} "
                          "(restart to re-bind the global hotkey)")
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
            "kb_app", "App controls",
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
            "kb_features", "Feature hotkeys",
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
        self._body(
            "HOLD bindings run the feature only while the key is held. "
            "TOGGLE bindings flip enabled state on each press.",
            dim=True,
        )

        self._h2("Combat")
        self._keybind_row_aligned("Triggerbot · hold", self.config.triggerbot, "hotkey", capture_id="triggerbot_hold")
        self._keybind_row_aligned("Triggerbot · toggle", self.config.triggerbot, "hotkey_toggle", capture_id="triggerbot_toggle")
        self._keybind_row_aligned("Rapid-fire · hold", self.config.rapidfire, "hotkey", capture_id="rapidfire_hold")
        self._keybind_row_aligned("Rapid-fire · toggle", self.config.rapidfire, "hotkey_toggle", capture_id="rapidfire_toggle")
        self._keybind_row_aligned("Auto-pistol · hold", self.config.autopistol, "hotkey", capture_id="autopistol_hold")
        self._keybind_row_aligned("Auto-pistol · toggle",self.config.autopistol, "hotkey_toggle", capture_id="autopistol_toggle")
        self._keybind_row_aligned("BHop · hold", self.config.bhop, "hotkey", capture_id="bhop_hold")
        self._keybind_row_aligned("BHop · toggle", self.config.bhop, "hotkey_toggle", capture_id="bhop_toggle")

        self._h2("Visuals")
        self._keybind_row_aligned("Radar · toggle", self.config.radar, "hotkey_toggle", capture_id="radar_toggle")

        self._end_card()

    def _panel_esp(self) -> None:
        v = self.config.visuals
        self._h1("ESP")
        self._body(
            "World-space rendering of enemy data on top of CS2. All ESP "
            "primitives are read-only memory access except Glow, which "
            "writes into a CS2 array (flagged below).",
            dim=True,
        )
        imgui.dummy(imgui.ImVec2(1, 8))
        self._h2("Wallhack preset")
        self._begin_card(
            "wallhack_mode", "Wallhack mode",
            risk="safe",
            help_text=(
                "Quick preset selector — flips on the right combination "
                "of Box / Skeleton / Glow with one click. Glow modes are "
                "marked NOT SAFE because Glow patches the engine via a "
                "cross-process write; the read-only modes touch nothing."
            ),
        )
        modes = [
            "None (off)",
            "Skeleton + Box (default, safe)",
            "Box only",
            "Skeleton only",
            "Glow (NOT SAFE)",
            "Box + Glow (NOT SAFE)",
            "Skeleton + Glow (NOT SAFE)",
            "All on (NOT SAFE)",
        ]
        current_preset = (
            bool(v.box.enabled),
            bool(v.skeleton.enabled),
            bool(self.config.glow.enabled),
        )
        try:
            cur_idx = _WALLHACK_PRESETS.index(current_preset)
        except ValueError:
            cur_idx = 0
        changed, new_idx = _safe_combo("##wallhack_mode", cur_idx, modes)
        if changed:
            box_enabled, skeleton_enabled, glow_enabled = _WALLHACK_PRESETS[
                max(0, min(new_idx, len(_WALLHACK_PRESETS) - 1))
            ]
            v.box.enabled = box_enabled
            v.skeleton.enabled = skeleton_enabled
            self.config.glow.enabled = glow_enabled
        self._end_card()

        b = v.box
        self._begin_card(
            "esp_box", "Box",
            risk="safe",
            help_text=(
                "World-space AABB drawn around each visible enemy pawn. "
                "Read-only — uses the same memory reads as the radar. "
                "Style options: outline (1 px line), filled (translucent "
                "fill), corners (just the four L-shaped brackets)."
            ),
        )
        self._toggle("Enabled", b, "enabled")
        styles = ["outline", "filled", "corners"]
        cur_s = b.style if b.style in styles else "outline"
        changed, new_idx = _safe_combo("Style##box_style",
                                       styles.index(cur_s), styles)
        if changed:
            b.style = styles[new_idx]
        self._slider_float("Thickness", b, "thickness", 0.5, 4.0)
        self._color_edit("Colour", b, "color")
        if self._advanced("box_adv"):
            self._toggle("Team-coloured (CT/T)", b, "team_colored")
            self._color_edit("CT colour", b, "ct_color")
            self._color_edit("T colour", b, "t_color")
            imgui.unindent(12 * self._ui_scale)
        self._end_card()

        s = v.skeleton
        self._begin_card(
            "esp_skel", "Skeleton",
            risk="safe",
            help_text=(
                "Per-bone wireframe. Reads the bone matrix array each "
                "frame, projects bone heads to screen, draws line "
                "segments between connected bones."
            ),
        )
        self._toggle("Enabled", s, "enabled")
        self._slider_float("Thickness", s, "thickness", 0.5, 3.0)
        self._color_edit("Colour", s, "color")
        self._end_card()

        sl = v.snapline
        self._begin_card(
            "esp_snap", "Snapline",
            risk="safe",
            help_text=(
                "Thin line drawn from the bottom-centre of the screen to "
                "each enemy's feet. Useful for instantly seeing "
                "direction-of-threat in your peripheral vision."
            ),
        )
        self._toggle("Enabled", sl, "enabled")
        self._color_edit("Colour##snap", sl, "color")
        self._end_card()

        ch = v.crosshair
        self._begin_card(
            "esp_xhair", "Crosshair",
            risk="safe",
            help_text=(
                "Static centre-screen crosshair drawn by the overlay. "
                "Independent of CS2's in-game crosshair so you can keep "
                "yours invisible in-game and use this one instead."
            ),
        )
        self._toggle("Enabled", ch, "enabled")
        self._slider_float("Size (px)", ch, "size", 2.0, 40.0)
        self._slider_float("Gap (px)", ch, "gap", 0.0, 20.0)
        self._slider_float("Thickness##xhair", ch, "thickness", 1.0, 4.0)
        self._color_edit("Colour##xhair", ch, "color")
        self._end_card()

        self._begin_card(
            "esp_text", "Name · Distance · Health",
            risk="safe",
            help_text=(
                "Per-enemy text labels. All three render above the box "
                "head with the same font — you can mix and match."
            ),
        )
        n = v.name
        self._toggle("Show names", n, "enabled")
        self._color_edit("Name colour", n, "color")
        d = v.distance
        self._toggle("Show distance (m)", d, "enabled")
        self._color_edit("Distance colour", d, "color")
        h = v.health
        modes = ["off", "bar", "percent", "both"]
        cur_m = h.mode if h.mode in modes else "bar"
        changed, new_idx = _safe_combo("Health mode", modes.index(cur_m), modes)
        if changed:
            h.mode = modes[new_idx]
        self._slider_float("Health bar width (px)", h, "bar_width", 1.0, 20.0)
        self._slider_float("Health bar offset (px)", h, "bar_offset", 0.0, 30.0)
        self._color_edit("Health — high", h, "high_color")
        self._color_edit("Health — mid", h, "mid_color")
        self._color_edit("Health — low", h, "low_color")
        self._end_card()

        g = self.config.glow
        self._begin_card(
            "esp_glow", "Glow",
            risk="high",
            caveat=(
                "Glow performs a cross-process write into client.dll's "
                "glow object array. Detection profile is higher than the "
                "rest of the ESP suite. Use sparingly."
            ),
            help_text=(
                "Source-engine glow effect: enemy silhouettes shine "
                "through walls. The implementation writes 4-byte glow "
                "indices into client.dll. Most CS2 anti-cheats inspect "
                "client.dll's writable regions, so this is the single "
                "feature we mark HIGH risk."
            ),
        )
        self._toggle("Glow enabled", g, "enabled")
        self._slider_int("Type", g, "type", 0, 3)
        self._slider_float("Opacity", g, "opacity", 0.0, 1.0)
        self._slider_float("Distance fade (units)", g, "radius", 0.0, 4000.0)
        self._color_edit("CT colour", g, "ct_color")
        self._color_edit("T colour", g, "t_color")
        self._slider_int("Miss-grace frames", g, "miss_grace_frames", 1, 60)
        why_open = self._show_why.get("esp_glow_why", False)
        arrow = "▾" if why_open else "▸"
        if imgui.selectable(
            f" {arrow} Why?", False,
            imgui.SelectableFlags_.no_auto_close_popups.value,
        )[0]:
            why_open = not why_open
            self._show_why["esp_glow_why"] = why_open
        if why_open:
            imgui.indent(12 * self._ui_scale)
            imgui.text_wrapped(
                "Anti-cheats can detect cross-process writes via "
                "kernel-mode driver hooks (VAC's hat-mode, Faceit AC). "
                "Pure-read features (box, skeleton, name, etc.) don't "
                "trigger those hooks because they only read from CS2's "
                "memory. Glow needs to *patch* engine state, which is the "
                "category of behaviour that gets flagged."
            )
            imgui.unindent(12 * self._ui_scale)
        self._end_card()

    def _panel_radar(self) -> None:
        r = self.config.radar
        self._h1("Radar")
        self._body(
            "Corner mini-map of every enemy within range, rotated to "
            "your view. Pure overlay rendering — no game writes.",
            dim=True,
        )
        imgui.dummy(imgui.ImVec2(1, 8))
        self._h2("Display")
        self._begin_card(
            "radar_main", "Radar",
            risk="safe",
            help_text=(
                "Corner mini-map showing enemies relative to your view. "
                "Pure overlay rendering — reads positions/angles from "
                "CS2 memory but never writes back. The radar always "
                "rotates with your view (classic CS minimap behaviour) "
                "so whatever you're facing is at the top."
            ),
        )
        self._toggle("Enabled", r, "enabled")
        shapes = ["round", "square"]
        cur_sh = r.shape if r.shape in shapes else "round"
        changed, new_idx = _safe_combo("Shape", shapes.index(cur_sh), shapes)
        if changed:
            r.shape = shapes[new_idx]
        anchors = ["top_left", "top_right", "bottom_left", "bottom_right"]
        cur_an = getattr(r, "anchor", "top_left") or "top_left"
        if cur_an not in anchors:
            cur_an = "top_left"
        changed, new_idx = _safe_combo("Anchor", anchors.index(cur_an), anchors)
        if changed:
            r.anchor = anchors[new_idx]
        self._slider_float("Size (fraction of screen, 0=px)",
                           r, "size_fraction", 0.0, 0.50)
        self._slider_float("Size (px, used if fraction=0)",
                           r, "size", 80.0, 600.0)
        self._slider_float("Range (units)", r, "range", 500.0, 10000.0)
        self._slider_float("Opacity", r, "opacity", 0.10, 1.0)
        self._color_edit("Enemy colour", r, "enemy_color")
        self._color_edit("Teammate colour", r, "teammate_color")
        if self._advanced("radar_adv"):
            self._toggle("Show teammates", r, "show_teammates")
            self._color_edit("Background", r, "background_color")
            self._color_edit("Border", r, "border_color")
            self._color_edit("Local player", r, "local_color")
            imgui.unindent(12 * self._ui_scale)
        self._end_card()

    def _panel_awareness(self) -> None:
        a = self.config.awareness
        self._h1("Awareness")
        self._body(
            "A peripheral-vision aid — the screen edges pulse coloured "
            "when an enemy is looking at you (and ideally close enough "
            "to be a threat).",
            dim=True,
        )
        imgui.dummy(imgui.ImVec2(1, 8))
        self._h2("Vignette")
        self._begin_card(
            "aware_main", "Awareness vignette",
            risk="safe",
            help_text=(
                "Pulses a coloured vignette at the screen edges when an "
                "enemy is looking at you. Intensity scales with proximity "
                "and how directly their crosshair is on you. Pure "
                "read-only overlay rendering."
            ),
        )
        self._toggle("Enabled", a, "enabled")
        self._slider_float("Cone (degrees)", a, "cone_degrees", 1.0, 30.0)
        self._slider_float("Max distance (units)", a, "max_distance", 500.0, 8000.0)
        self._slider_float("Fade speed", a, "fade_speed", 0.5, 12.0)
        self._slider_float("Edge thickness (px)", a, "edge_thickness", 20.0, 400.0)
        self._color_edit("Colour", a, "color")
        if self._advanced("aware_adv"):
            self._toggle("Rear-only (only show enemies looking at our back)",
                         a, "rear_only")
            self._slider_float("Rear FOV (deg)", a, "rear_fov_deg", 60.0, 180.0)
            imgui.unindent(12 * self._ui_scale)
        self._end_card()

    def _panel_combat(self) -> None:
        self._h1("Combat")
        self._body(
            "All synthetic-input features. Each one runs only when its "
            "hold-key is held *or* its toggle-key has flipped its enabled "
            "state. None of these write into CS2 — they all post user-mode "
            "mouse / keyboard events that VAC routes through the same path "
            "as your real input.",
            dim=True,
        )
        imgui.dummy(imgui.ImVec2(1, 8))
        self._h2("Triggerbot")
        tb = self.config.triggerbot
        self._begin_card(
            "comb_trigger", "Triggerbot",
            risk="safe",
            help_text=(
                "Auto-fires when your crosshair is on an enemy hitbox. "
                "Pure user-mode left-mouse synthesis — no memory writes. "
                "Distance bands let you tune trigger reaction time per "
                "engagement range so close-quarters fights snap quickly "
                "while long-range checks are slower (more human-looking)."
            ),
        )
        self._toggle("Enabled", tb, "enabled")
        self._toggle("Allow scoped weapons", tb, "allow_scoped")
        self._slider_int("Refire cooldown (ms)", tb, "refire_cooldown_ms", 0, 2000)
        self._slider_int("Reaction delay (ms)", tb, "delay_ms", 0, 500)
        self._slider_int("Delay jitter (±ms)", tb, "delay_jitter_ms", 0, 200)
        self._slider_int("Min reaction (ms)", tb, "min_delay_ms", 0, 500)
        if self._advanced("comb_trigger_adv"):
            self._slider_float("Fallback FOV (deg, point-gate)",
                               tb, "fov_deg", 0.1, 12.0)
            self._slider_float("Fallback skeleton FOV (deg)",
                               tb, "skeleton_fov_deg", 0.1, 12.0)
            imgui.dummy(imgui.ImVec2(1, 4))
            self._slider_float(
                "Scoped band damping ×mult (scoped only)",
                tb, "scoped_band_multiplier", 0.05, 1.0,
            )
            imgui.dummy(imgui.ImVec2(1, 4))
            self._draw_distance_bands()
            imgui.unindent(12 * self._ui_scale)
        self._end_card()

        rf = self.config.rapidfire
        self._begin_card(
            "comb_rapid", "Rapid-fire",
            risk="safe",
            help_text=(
                "Auto-clicks left mouse at a configurable rate while the "
                "hotkey is held. Lets semi-auto rifles (e.g. Deagle) "
                "fire at the absolute server-side cooldown limit."
            ),
        )
        self._toggle("Enabled", rf, "enabled")
        self._slider_float("Clicks/sec##rf", rf, "cps", 1.0, 25.0)
        self._slider_float("Jitter (±)##rf", rf, "jitter_pct", 0.0, 0.50)
        self._end_card()

        ap = self.config.autopistol
        self._begin_card(
            "comb_autopistol", "Auto-pistol",
            risk="safe",
            help_text=(
                "Same as rapid-fire but wired to your pistol so you don't "
                "have to spam the click yourself. Glock / Deagle become "
                "effectively full-auto without going past CS2's "
                "server-enforced per-shot cooldown."
            ),
        )
        self._toggle("Enabled", ap, "enabled")
        self._slider_float("Clicks/sec##ap", ap, "cps", 1.0, 12.0)
        self._slider_float("Jitter (±)##ap", ap, "jitter_pct", 0.0, 0.50)
        self._end_card()

        b = self.config.bhop
        self._begin_card(
            "comb_bhop", "BHop (frame-perfect rejump)",
            risk="safe",
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
        self._toggle("Enabled", b, "enabled")
        self._slider_int("Min gap between jumps (ms)", b, "min_gap_ms", 20, 300)
        self._slider_int("Press jitter (±ms)", b, "delay_jitter_ms", 0, 30)
        self._toggle("Humanize (occasionally skip a bounce)", b, "humanize")
        if self._advanced("comb_bhop_adv"):
            self._slider_int("Skip every N — minimum N", b, "humanize_skip_min", 1, 30)
            self._slider_int("Skip every N — maximum N", b, "humanize_skip_max", 1, 30)
            self._slider_int("Skip-resume delay (ms)", b, "skip_resume_delay_ms", 20, 1000)
            imgui.unindent(12 * self._ui_scale)
        if int(getattr(b, "humanize_skip_min", 3)) > int(getattr(b, "humanize_skip_max", 15)):
            imgui.push_style_color(imgui.Col_.text.value, self._theme()["warn"])
            imgui.text_wrapped("⚠ min > max — will be auto-clamped at runtime.")
            imgui.pop_style_color()
        self._end_card()

    def _draw_distance_bands(self) -> None:
        """Editor for the triggerbot's distance-banded FOV gates.

        Each band is a dict with keys ``min_m, max_m, fov_deg,
        skeleton_fov_deg, label`` (matching the array-of-tables in
        ``default.toml``: ``[[triggerbot.distance_bands]]``). The
        triggerbot walks the bands top-to-bottom and uses the first
        one whose half-open ``[min_m, max_m)`` range contains the
        current eye-to-target distance.
        """
        tb = self.config.triggerbot
        bands = list(getattr(tb, "distance_bands", []) or [])

        def _default_bands() -> list[dict]:
            return [
                {"min_m": 4.0, "max_m": 9999.0, "fov_deg": 0.5, "skeleton_fov_deg": 0.5, "label": "long"},
                {"min_m": 2.0, "max_m": 4.0, "fov_deg": 2.0, "skeleton_fov_deg": 1.0, "label": "mid"},
                {"min_m": 0.5, "max_m": 2.0, "fov_deg": 4.0, "skeleton_fov_deg": 2.0, "label": "close"},
                {"min_m": 0.0, "max_m": 0.5, "fov_deg": 8.0, "skeleton_fov_deg": 4.0, "label": "point_blank"},
            ]

        if not bands:
            bands = _default_bands()
            tb.distance_bands = bands

        imgui.text("Distance bands — [min_m, max_m) → (fov_deg, skel_fov)")
        imgui.same_line()
        if imgui.button("+ band##tbb"):
            new_max = max((float(b.get("max_m", 0.0)) for b in bands), default=10.0) + 10.0
            bands.append({"min_m": new_max - 5.0, "max_m": new_max,
                          "fov_deg": 1.0, "skeleton_fov_deg": 1.0, "label": "new"})
        imgui.same_line()
        if imgui.button("reset##tbb"):
            bands = _default_bands()

        new_bands: list[dict] = []
        to_remove = -1
        for i, band in enumerate(bands):
            imgui.push_id(i)
            label = str(band.get("label", "?"))
            t = self._theme()
            imgui.push_style_color(imgui.Col_.text.value, t["accent"])
            imgui.text(f" band {i} ({label})")
            imgui.pop_style_color()
            imgui.same_line()
            if imgui.button(f"X##b{i}"):
                to_remove = i
            min_m = float(band.get("min_m", 0.0))
            max_m = float(band.get("max_m", 9999.0))
            fov = float(band.get("fov_deg", 1.0))
            skfov = float(band.get("skeleton_fov_deg", 1.0))
            _, new_min = imgui.slider_float(f"min_m##b{i}", min_m, 0.0, 200.0, "%.1f")
            _, new_max = imgui.slider_float(f"max_m##b{i}", max_m, 0.0, 9999.0, "%.1f")
            _, new_fov = imgui.slider_float(f"fov_deg##b{i}", fov, 0.05, 12.0, "%.2f")
            _, new_skfov = imgui.slider_float(
                f"skeleton_fov_deg##b{i}", skfov, 0.05, 12.0, "%.2f")
            new_bands.append({
                "min_m": float(new_min),
                "max_m": float(new_max),
                "fov_deg": float(new_fov),
                "skeleton_fov_deg": float(new_skfov),
                "label": label,
            })
            imgui.pop_id()
        if to_remove >= 0 and len(new_bands) > 1:
            new_bands.pop(to_remove)
        try:
            tb.distance_bands = new_bands
        except Exception:
            pass

    def _panel_anti_detection(self) -> None:
        ad = self.config.anti_detection
        self._h1("Anti-Detection")
        self._body(
            "Surface-level overlay concealment, read-pattern shaping, and a "
            "global safety rail for cross-process writes.",
            dim=True,
        )
        imgui.dummy(imgui.ImVec2(1, 8))
        self._h2("Overlay & process")
        self._begin_card(
            "ad_overlay", "Overlay & process hiding",
            risk="safe",
            help_text=(
                "Surface-level concealment: stop the overlay from "
                "appearing in screen-capture / OBS / Discord, drop the "
                "taskbar entry, and randomise the OS window title so "
                "window-name signature scans don't latch on."
            ),
        )
        self._toggle("Hide overlay from screen capture",
                     ad, "hide_overlay_from_capture")
        self._toggle("Hide from taskbar",
                     ad, "hide_from_taskbar")
        self._toggle("Randomise window title each launch",
                     ad, "randomize_window_title")
        self._toggle("Auto-protect capture on focus loss",
                     ad, "auto_capture_protect_on_focus_loss")
        self._end_card()

        self._begin_card(
            "ad_reads", "Read pattern shaping",
            risk="safe",
            help_text=(
                "Adds 0–5 ms of jitter to the per-frame memory-read "
                "cadence so the access pattern doesn't look like a "
                "perfect periodic burst. Useful against heuristic "
                "pattern-detectors that fingerprint constant-rate IO."
            ),
        )
        self._slider_int("Read jitter (ms)",
                         ad, "randomize_read_jitter_ms", 0, 10)
        self._end_card()

        self._begin_card(
            "ad_writes", "Write safety",
            risk="safe",
            help_text=(
                "The only feature that performs cross-process WRITES is "
                "Glow. Read-only mode is the global kill switch for all "
                "writes."
            ),
        )
        self._toggle("Read-only mode (block ALL writes globally)",
                     ad, "read_only_mode")
        self._end_card()

    def _panel_settings(self) -> None:
        g = self.config.general
        self._h1("Settings")
        self._body(
            "App-wide preferences: window behaviour, theme, autosave, "
            "and developer toggles.",
            dim=True,
        )
        imgui.dummy(imgui.ImVec2(1, 8))

        self._h2("Display")
        self._begin_card(
            "set_display", "Display",
            help_text=(
                "Theme + UI scale. Theme picker and the sidebar pill "
                "share the same config field."
            ),
        )
        imgui.text("Theme")
        for tname in _THEME_ORDER:
            if imgui.radio_button(f" {_THEME_GLYPH[tname]} {_THEME_LABEL[tname]}",
                                  self._theme_name == tname):
                self._set_theme(tname)
        imgui.dummy(imgui.ImVec2(1, 4))
        cur_scale = float(getattr(g, "ui_scale", 0.0) or 0.0)
        imgui.text("UI scale (0 = auto)")
        changed, new = imgui.slider_float("##uiscale",
                                          cur_scale, 0.0, 2.5, "%.2f")
        if changed:
            try:
                g.ui_scale = float(new)
                self._ui_scale = self._compute_ui_scale()
                self._apply_theme()
            except Exception:
                pass
        self._toggle("Open 'Show advanced' sections by default",
                     g, "menu_show_advanced_default")
        self._advanced_default = bool(getattr(g, "menu_show_advanced_default", False))
        self._end_card()

        self._begin_card(
            "set_window", "Window behaviour",
            risk="safe",
            help_text=(
                "Controls how the menu surfaces on screen and whether CS2 "
                "receives focus on launch."
            ),
        )
        self._toggle("External window (separate OS window)",
                     g, "menu_external_window")
        self._toggle("Auto-focus CS2 on launch",
                     g, "focus_cs2_on_launch")
        self._toggle("Start with menu visible",
                     g, "start_menu_visible")
        self._end_card()

        self._begin_card(
            "set_persist", "Persistence",
            risk="safe",
            help_text=(
                "Autosave writes config.toml on every change after a "
                "small debounce window. Disable if you want to test "
                "settings without committing them to disk."
            ),
        )
        self._toggle("Autosave config on change", g, "autosave")
        self._slider_float("Autosave interval (s)", g, "autosave_interval_sec",
                           0.1, 10.0)
        self._slider_int("Target FPS", g, "target_fps", 60, 240)
        self._end_card()

        self._begin_card(
            "set_diag", "Diagnostics",
            risk="safe",
            help_text=(
                "Read-only summary of the keys that affect global "
                "behaviour. Useful when reporting bugs."
            ),
        )
        imgui.push_style_color(imgui.Col_.text.value, self._theme()["text_dim"])
        imgui.text(f"toggle_menu_key = {getattr(g, 'toggle_menu_key', '?')!r}")
        imgui.text(f"master_toggle_key = {getattr(g, 'master_toggle_key', '?')!r}")
        imgui.text(f"exit_key = {getattr(g, 'exit_key', '?')!r}")
        imgui.text(f"target_fps = {getattr(g, 'target_fps', '?')}")
        imgui.text(f"ui_scale (auto) = {self._ui_scale:.2f}")
        imgui.text(f"theme = {self._theme_name!r}")
        imgui.text(f"cs2 hwnd = {self.cs2_hwnd}")
        imgui.pop_style_color()
        self._end_card()
