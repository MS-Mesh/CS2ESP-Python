"""Theme colour conversion helpers."""

from imgui_bundle import imgui


def color_u32(rgba) -> int:
    """Convert a 4-tuple of floats in [0,1] to imgui packed uint32."""
    r, g, b, a = rgba
    return imgui.get_color_u32(imgui.ImVec4(float(r), float(g), float(b), float(a)))


def with_alpha(rgba, alpha: float) -> tuple:
    """Return rgba with alpha replaced (clamped 0..1)."""
    r, g, b, _ = rgba
    return (r, g, b, max(0.0, min(1.0, alpha)))


def health_color(hp: int, high_col, mid_col, low_col):
    """Pick the configured colour bucket for a health value."""
    if hp > 60:
        return high_col
    if hp > 25:
        return mid_col
    return low_col
