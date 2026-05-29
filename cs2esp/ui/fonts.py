"""
Custom TTF font loading and per-ImGui-context font cache.

Overlay and external menu windows own separate ImGui contexts, so each needs its
own font atlas and cached ImFont pointers. If no usable system TTF is found, the
helpers quietly fall back to ImGui's default font for smoke tests.
"""

import os
from dataclasses import dataclass

from imgui_bundle import imgui

BODY_PX: float = 18.0
H2_PX: float = 28.0
H1_PX: float = 38.0

_FONT_CANDIDATES: tuple[tuple[str, str], ...] = (
    (r"C:\Windows\Fonts\segoeui.ttf", r"C:\Windows\Fonts\segoeuib.ttf"),
    (r"C:\Windows\Fonts\arial.ttf", r"C:\Windows\Fonts\arialbd.ttf"),
    (r"C:\Windows\Fonts\tahoma.ttf", r"C:\Windows\Fonts\tahomabd.ttf"),
    (r"C:\Windows\Fonts\verdana.ttf", r"C:\Windows\Fonts\verdanab.ttf"),
)


@dataclass(slots=True)
class Fonts:
    """Three ImFont handles for one ImGui context."""
    body: object
    h2: object
    h1: object


_CACHE: dict[int, Fonts] = {}


def _ctx_key() -> int:
    """Integer identity of the current ImGui context.

    Returns 0 if no context is current, which lets callers
    no-op cleanly instead of raising in headless smoke tests.
    """
    try:
        ctx = imgui.get_current_context()
        return id(ctx) if ctx else 0
    except Exception:
        return 0


def load_fonts_for_current_context(impl=None) -> Fonts | None:
    """Populate the active ImGui context's font atlas.

    Idempotent: re-calling on an already-loaded context returns cached Fonts.

    Older renderer backends expose ``refresh_font_texture``; current
    imgui_bundle backends upload changed atlas textures during rendering.
    """
    key = _ctx_key()
    if key == 0:
        return None
    if key in _CACHE:
        return _CACHE[key]

    font_paths = None
    for regular_path, bold_path in _FONT_CANDIDATES:
        if os.path.exists(regular_path):
            font_paths = (
                regular_path,
                bold_path if os.path.exists(bold_path) else regular_path,
            )
            break
    if font_paths is None:
        print("[fonts] No system TTF found; staying on ImGui default")
        return None
    reg_path, bold_path = font_paths

    try:
        atlas = imgui.get_io().fonts
        body_ptr = atlas.add_font_from_file_ttf(reg_path, BODY_PX)
        h2_ptr = atlas.add_font_from_file_ttf(bold_path, H2_PX)
        h1_ptr = atlas.add_font_from_file_ttf(bold_path, H1_PX)
    except Exception as e:
        print(f"[fonts] add_font_from_file_ttf failed: {e!r}")
        return None

    refresh_font_texture = getattr(impl, "refresh_font_texture", None)
    if callable(refresh_font_texture):
        try:
            refresh_font_texture()
        except Exception as e:
            print(f"[fonts] refresh_font_texture failed: {e!r}")

    fonts = Fonts(body=body_ptr, h2=h2_ptr, h1=h1_ptr)
    _CACHE[key] = fonts
    print(
        f"[fonts] ctx={key:#x}: loaded {os.path.basename(reg_path)!r} "
        f"body={BODY_PX} h2={H2_PX} h1={H1_PX} px"
    )
    return fonts


def get_fonts_for_current_context() -> Fonts | None:
    """Cheap cache lookup; None if fonts are not loaded yet."""
    return _CACHE.get(_ctx_key())


def forget_fonts_for_current_context() -> None:
    """Drop cached ImFont handles before the owning context is destroyed."""
    key = _ctx_key()
    if key:
        _CACHE.pop(key, None)


def push(font_ptr) -> bool:
    """Push a font pointer onto the imgui font stack.

    Hides the two-argument push_font signature this imgui_bundle build requires.
    Returns True only when callers should later call pop().
    """
    if font_ptr is None:
        return False
    try:
        imgui.push_font(font_ptr, 0.0)
        return True
    except Exception as e:
        print(f"[fonts] push_font failed: {e!r}")
        return False


def push_sized(font_ptr, size_px: float) -> bool:
    """Push a font at an arbitrary pixel size; size <= 0 keeps the baked size."""
    if font_ptr is None:
        return False
    try:
        imgui.push_font(font_ptr, float(max(0.0, size_px)))
        return True
    except Exception as e:
        print(f"[fonts] push_sized failed: {e!r}")
        return False


def pop(pushed: bool) -> None:
    """Mirror of push; only pops when push succeeded."""
    if not pushed:
        return
    try:
        imgui.pop_font()
    except Exception:
        pass
