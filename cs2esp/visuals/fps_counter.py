"""Configurable FPS, ping, and jitter overlay widget."""

from imgui_bundle import imgui

from cs2esp.ui.fonts import get_fonts_for_current_context, pop as pop_font, push_sized
from cs2esp.visuals.theme import color_u32, with_alpha


def draw_fps_counter(draw_list, screen_w: int, screen_h: int, cfg,
                     fps: float | None,
                     ping_ms: int | None = None,
                     jitter_ms: int | None = None) -> None:
    """Draw whichever of FPS, ping, and jitter the user has enabled."""
    if not bool(getattr(cfg, "enabled", False)):
        return

    show_fps = bool(getattr(cfg, "show_fps", True))
    show_ping = bool(getattr(cfg, "show_ping", False))
    show_jitter = bool(getattr(cfg, "show_jitter", False))

    def _rgba(name: str, default, alpha_name: str) -> tuple[float, float, float, float]:
        color = tuple(getattr(cfg, name, default))
        alpha = float(getattr(cfg, alpha_name, -1.0))
        if alpha >= 0.0:
            color = with_alpha(color, alpha)
        return color

    fps_col = _rgba("text_color", (0.0, 0.85, 0.85, 1.0), "text_alpha")
    ping_col = _rgba("ping_color", (0.4, 0.85, 0.4, 1.0), "ping_alpha")
    jitter_col = _rgba("jitter_color", (0.4, 0.85, 0.4, 1.0), "jitter_alpha")

    # Measurement and rendering both run inside push_sized(font, user_px) so calc_text_size returns the pixel height add_text uses - slider value is the literal rendered height.
    entries: list[tuple[str, str, tuple[float, float, float, float], object, float]] = []

    fonts = get_fonts_for_current_context()
    main_font = getattr(fonts, "h2", None) if fonts is not None else None
    sub_font = getattr(fonts, "body", None) if fonts is not None else None
    # Baked sizes are the fallback when user config is unset/0; panel slider defaults to them so first-launch matches the look.
    main_baked = float(getattr(main_font, "font_size", 28.0) or 28.0) if main_font else 28.0
    sub_baked = float(getattr(sub_font, "font_size", 18.0) or 18.0) if sub_font else 18.0
    user_main_px = float(getattr(cfg, "font_size", main_baked) or main_baked)
    user_sub_px = float(getattr(cfg, "ping_font_size",
                                  getattr(cfg, "sub_font_size", sub_baked))
                        or sub_baked)
    user_jitter_px = float(getattr(cfg, "jitter_font_size",
                                     getattr(cfg, "sub_font_size", sub_baked))
                           or sub_baked)

    if show_fps and fps is not None:
        entries.append(("fps", f"{int(round(fps))} fps", fps_col,
                        main_font, user_main_px))
    if show_ping and ping_ms is not None and ping_ms >= 0:
        entries.append(("ping", f"{int(ping_ms)} ms", ping_col,
                        sub_font, user_sub_px))
    if show_jitter and jitter_ms is not None and jitter_ms >= 0:
        entries.append(("jitter", f"\u00b1{int(jitter_ms)} ms",
                        jitter_col, sub_font, user_jitter_px))
    if not entries:
        return

    line_widths: list[float] = []
    line_heights: list[float] = []
    for _kind, text, _color, font, size_px in entries:
        pushed = push_sized(font, size_px)
        try:
            text_size = imgui.calc_text_size(text)
        finally:
            pop_font(pushed)
        line_widths.append(text_size.x)
        line_heights.append(text_size.y)
    text_w = max(line_widths)

    # Per-gap spacing scales with FPS line height: fps<->ping gap = 1.5x base, else 1.0x. Floor 4px so tiny fonts don't collapse lines together.
    fps_line_h = line_heights[0] if entries[0][0] == "fps" else (
        next((line_heights[i] for i, e in enumerate(entries) if e[0] == "fps"),
             line_heights[0])
    )
    base_gap = max(4.0, fps_line_h * 0.22)
    gaps: list[float] = []
    for i in range(1, len(entries)):
        prev_kind = entries[i - 1][0]
        gaps.append(base_gap * (1.5 if prev_kind == "fps" else 1.0))

    text_h = sum(line_heights) + sum(gaps)

    # Text is inset only when the background box is enabled.
    position = str(getattr(cfg, "position", "top_left") or "top_left")
    right_aligned = position in ("top_right", "bottom_right")
    bottom_aligned = position in ("bottom_left", "bottom_right")
    margin_x = float(getattr(cfg, "margin_x", 12.0))
    margin_y = float(getattr(cfg, "margin_y", 12.0))
    anchor_x = screen_w - margin_x if right_aligned else margin_x
    anchor_y = screen_h - margin_y if bottom_aligned else margin_y
    pad = max(0.0, float(getattr(cfg, "box_padding", 6.0)))
    box_on = bool(getattr(cfg, "box_enabled", False))
    inner_pad = pad if box_on else 0.0

    text_x0 = (anchor_x - text_w - inner_pad
               if right_aligned else anchor_x + inner_pad)
    text_y0 = (anchor_y - text_h - inner_pad
               if bottom_aligned else anchor_y + inner_pad)

    if box_on:
        box_col = tuple(getattr(cfg, "box_color", (0.0, 0.0, 0.0, 0.7)))
        box_alpha = float(getattr(cfg, "box_alpha", -1.0))
        if box_alpha >= 0.0:
            box_col = with_alpha(box_col, box_alpha)
        draw_list.add_rect_filled(
            (text_x0 - pad, text_y0 - pad),
            (text_x0 + text_w + pad, text_y0 + text_h + pad),
            color_u32(box_col), 4.0,
        )

    # Shadow first at +1px offset, then the coloured line on top.
    shadow_col = color_u32((0.0, 0.0, 0.0, 0.85))

    cursor_y = text_y0
    for i, (_kind, txt, col_rgba, font, size_px) in enumerate(entries):
        # Centre each line within the maximum-width text block.
        line_x = text_x0 + max(0.0, (text_w - line_widths[i]) / 2.0)
        col = color_u32(col_rgba)
        pushed = push_sized(font, size_px)
        try:
            draw_list.add_text((line_x + 1, cursor_y + 1), shadow_col, txt)
            draw_list.add_text((line_x, cursor_y), col, txt)
        finally:
            pop_font(pushed)
        cursor_y += line_heights[i]
        if i < len(gaps):
            cursor_y += gaps[i]


__all__ = ["draw_fps_counter"]
