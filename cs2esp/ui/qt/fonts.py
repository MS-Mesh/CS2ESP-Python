"""Registration for fonts bundled with the Qt menu."""

from pathlib import Path

from PySide6.QtGui import QFontDatabase

ASSETS_DIR = Path(__file__).resolve().parent / "assets"
SPACE_GROTESK_PATH = ASSETS_DIR / "SpaceGrotesk-Variable.ttf"


def register_space_grotesk() -> str | None:
	"""Register Space Grotesk and return its Qt family name."""
	font_id = QFontDatabase.addApplicationFont(str(SPACE_GROTESK_PATH))
	if font_id < 0:
		print(f"[menu] Could not load bundled font: {SPACE_GROTESK_PATH}")
		return None

	families = QFontDatabase.applicationFontFamilies(font_id)
	if not families:
		print("[menu] Bundled Space Grotesk font has no family metadata.")
		return None
	return families[0]
