"""
Theme colour conversion helpers.
"""


def color_u32(rgba) -> int:
	'''Convert a 4-tuple of floats in [0,1] to packed uint32 (R | G<<8 | B<<16 | A<<24).'''
	red, green, blue, alpha = rgba
	packed_red = int(max(0.0, min(1.0, float(red))) * 255.0 + 0.5)
	packed_green = int(max(0.0, min(1.0, float(green))) * 255.0 + 0.5)
	packed_blue = int(max(0.0, min(1.0, float(blue))) * 255.0 + 0.5)
	packed_alpha = int(max(0.0, min(1.0, float(alpha))) * 255.0 + 0.5)
	return packed_red | (packed_green << 8) | (packed_blue << 16) | (packed_alpha << 24)


def with_alpha(rgba, alpha: float) -> tuple:
	'''Return rgba with alpha replaced (clamped 0..1).'''
	red, green, blue, _ = rgba
	return (red, green, blue, max(0.0, min(1.0, alpha)))


def health_color(hp: int, high_col, mid_col, low_col):
	'''Pick the configured colour bucket for a health value.'''
	if hp > 60:
		return high_col
	if hp > 25:
		return mid_col
	return low_col
