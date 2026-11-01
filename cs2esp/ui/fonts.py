"""
Custom TTF font loading and per-ImGui-context font cache.
Overlay and external menu windows own separate ImGui contexts, so each needs its own font atlas and cached ImFont pointers; if no usable system TTF is
found, the helpers quietly fall back to ImGui's default font for smoke tests.
"""

import os
from collections import namedtuple
from dataclasses import dataclass
from pathlib import Path

from imgui_bundle import imgui

BODY_PX: float = 20.0
H2_PX: float = 28.0
H1_PX: float = 38.0

_ASSETS_FONTS = Path(__file__).resolve().parents[1] / "assets" / "fonts"

_FONT_CANDIDATES: tuple[tuple[str, str], ...] = (
	(str(_ASSETS_FONTS / "Inter-Regular.ttf"), str(_ASSETS_FONTS / "Inter-SemiBold.ttf")),
	(r"C:\Windows\Fonts\segoeui.ttf", r"C:\Windows\Fonts\segoeuib.ttf"),
	(r"C:\Windows\Fonts\arial.ttf", r"C:\Windows\Fonts\arialbd.ttf"),
	(r"C:\Windows\Fonts\tahoma.ttf", r"C:\Windows\Fonts\tahomabd.ttf"),
	(r"C:\Windows\Fonts\verdana.ttf", r"C:\Windows\Fonts\verdanab.ttf"),
)


@dataclass(slots=True)
class Fonts:
	"""ImFont handles for one ImGui context, plus pre-baked HUD sizes."""

	body: object
	h2: object
	h1: object
	baked: dict[int, object]
	event_icons: object | None = None


# Small sizes HUD widgets request through push_sized(); baking them rasterizes text at its real pixel size instead of scaling the soft 20 px body face
HUD_BAKED_SIZES: tuple[int, ...] = (10, 11, 12, 13, 14, 15, 16, 17, 18)


_CACHE: dict[int, Fonts] = {}
# Font stack mirror plus measured-text cache; HUD widgets measure the same strings every frame and calc_text_size walks the glyph atlas each time
_FONT_STACK: list[tuple[int, float]] = []
_TEXT_CACHE: dict[tuple[int, int, float, str], "TextSize"] = {}
_FONT_SIZES: dict[int, float] = {}
_ACTIVE_KEY: int = 0


def _ctx_key(ctx=None) -> int:
	'''Cache key for the active renderer context; contexts must pass their long-lived object because get_current_context returns a fresh wrapper each call.'''
	if ctx is not None:
		try:
			return id(ctx)
		except Exception:
			return 0
	return _ACTIVE_KEY


def set_font_context(ctx) -> None:
	'''Bind the font cache to the renderer's long-lived imgui context.'''
	global _ACTIVE_KEY
	try:
		_ACTIVE_KEY = id(ctx) if ctx is not None else 0
	except Exception:
		_ACTIVE_KEY = 0


def load_fonts_for_current_context(impl=None, ctx=None) -> Fonts | None:
	'''Populate the active ImGui context's font atlas; re-calling on a loaded context returns the cached Fonts.'''
	global _ACTIVE_KEY
	raw = ctx
	if raw is None:
		try:
			raw = imgui.get_current_context()
		except Exception:
			raw = None
	key = id(raw) if raw is not None else 0
	if key == 0:
		return None
	_ACTIVE_KEY = key
	if key in _CACHE:
		return _CACHE[key]

	font_paths = None
	for regular_path, bold_path in _FONT_CANDIDATES:
		if os.path.exists(regular_path):
			font_paths = (regular_path, bold_path if os.path.exists(bold_path) else regular_path)
			break
	if font_paths is None:
		print("[fonts] No system TTF found; staying on ImGui default")
		return None
	regular_path, bold_path = font_paths

	try:
		atlas = imgui.get_io().fonts
		# HUD text is weight-600 in the reference design, so the body slot takes the SemiBold face and headings reuse it at larger sizes
		body_font = atlas.add_font_from_file_ttf(bold_path, BODY_PX)
		heading2_font = atlas.add_font_from_file_ttf(bold_path, H2_PX)
		heading1_font = atlas.add_font_from_file_ttf(bold_path, H1_PX)
		baked = {pixel_size: atlas.add_font_from_file_ttf(bold_path, float(pixel_size)) for pixel_size in HUD_BAKED_SIZES}
		_FONT_SIZES[id(body_font)] = BODY_PX
		_FONT_SIZES[id(heading2_font)] = H2_PX
		_FONT_SIZES[id(heading1_font)] = H1_PX
		for pixel_size, font_ptr in baked.items():
			_FONT_SIZES[id(font_ptr)] = float(pixel_size)
	except Exception as e:
		print(f"[fonts] add_font_from_file_ttf failed: {e!r}")
		return None

	event_icons = None
	icon_path = _ASSETS_FONTS / "EventWeapons.ttf"
	if icon_path.is_file():
		try:
			event_icons = atlas.add_font_from_file_ttf(str(icon_path), 20.0)
			_FONT_SIZES[id(event_icons)] = 20.0
		except Exception as error:
			print(f"[fonts] equipment font failed: {error!r}")

	# Older renderer backends expose refresh_font_texture; current imgui_bundle uploads changed atlas textures during rendering
	refresh_font_texture = getattr(impl, "refresh_font_texture", None)
	if callable(refresh_font_texture):
		try:
			refresh_font_texture()
		except Exception as e:
			print(f"[fonts] refresh_font_texture failed: {e!r}")

	fonts = Fonts(body=body_font, h2=heading2_font, h1=heading1_font, baked=baked, event_icons=event_icons)
	_CACHE[key] = fonts
	print(
		f"[fonts] ctx={key:#x}: loaded {os.path.basename(regular_path)!r} "
		f"body={BODY_PX} h2={H2_PX} h1={H1_PX} px "
		f"baked={','.join(str(pixel_size) for pixel_size in HUD_BAKED_SIZES)}"
	)
	return fonts


def get_fonts_for_current_context(ctx=None) -> Fonts | None:
	'''Cheap cache lookup; None if fonts are not loaded yet.'''
	key = id(ctx) if ctx is not None else _ACTIVE_KEY
	return _CACHE.get(key)


def forget_fonts_for_current_context(ctx=None) -> None:
	'''Drop cached ImFont handles before the owning context is destroyed.'''
	global _ACTIVE_KEY
	key = id(ctx) if ctx is not None else _ACTIVE_KEY
	if key:
		_CACHE.pop(key, None)
	if key == _ACTIVE_KEY:
		_ACTIVE_KEY = 0


def push(font_ptr) -> bool:
	'''Push a font pointer with its real baked size; returns True only when callers should later call pop.'''
	if font_ptr is None:
		return False
	try:
		imgui.push_font(font_ptr, float(_FONT_SIZES.get(id(font_ptr), 0.0)))
		return True
	except Exception as e:
		print(f"[fonts] push_font failed: {e!r}")
		return False


def push_sized(font_ptr, size_px: float) -> bool:
	'''Push a font at an arbitrary pixel size; a whole-pixel body size uses the pre-baked atlas entry instead of scaling the 20 px face.'''
	if font_ptr is None:
		return False
	try:
		size = float(max(0.0, size_px))
	except (TypeError, ValueError):
		size = 0.0
	fonts = _CACHE.get(_ctx_key())
	if fonts is not None and size >= 9.5 and font_ptr is fonts.body:
		rounded = int(round(size))
		if abs(size - rounded) < 0.05:
			baked = fonts.baked.get(rounded)
			if baked is not None:
				try:
					# Pass the explicit size: 0.0 would render at 1 px
					imgui.push_font(baked, float(rounded))
					_FONT_STACK.append((id(font_ptr), float(rounded)))
					return True
				except Exception as e:
					print(f"[fonts] push baked failed: {e!r}")
					return False
	try:
		imgui.push_font(font_ptr, size)
		_FONT_STACK.append((id(font_ptr), size))
		return True
	except Exception as e:
		print(f"[fonts] push_sized failed: {e!r}")
		return False


def snap_size(size_px: float) -> float:
	'''Round a text size to whole pixels; fractional sizes rasterize soft.'''
	try:
		return float(max(1, int(round(float(size_px)))))
	except (TypeError, ValueError):
		return 1.0


def snap_pos(value: float) -> float:
	'''Snap a draw coordinate to a whole pixel to keep glyphs crisp.'''
	try:
		return float(round(float(value)))
	except (TypeError, ValueError):
		return 0.0


def pop(pushed: bool) -> None:
	'''Mirror of push; only pops when push succeeded.'''
	if not pushed:
		return
	if _FONT_STACK:
		_FONT_STACK.pop()
	try:
		imgui.pop_font()
	except Exception:
		pass


TextSize = namedtuple("TextSize", "x y")


def text_size(text: str) -> TextSize:
	'''Cached imgui.calc_text_size for the font last pushed via this module.'''
	if not _FONT_STACK:
		# Nothing pushed here: keep the plain imgui call so headless imgui stubs behave as before
		measured = imgui.calc_text_size(text)
		return TextSize(float(measured.x), float(measured.y))
	font_id, size = _FONT_STACK[-1]
	key = (_ACTIVE_KEY, font_id, size, text)
	hit = _TEXT_CACHE.get(key)
	if hit is None:
		measured = imgui.calc_text_size(text)
		hit = TextSize(float(measured.x), float(measured.y))
		if len(_TEXT_CACHE) > 8192:
			_TEXT_CACHE.clear()
		_TEXT_CACHE[key] = hit
	return hit
