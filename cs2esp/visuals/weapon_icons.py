"""
Equipment silhouettes share one small font atlas with the HUD.
Metadata carries each glyph's actual ink centre, so the frame loop never parses SVG, downloads files or builds textures.
"""

from json import loads
from pathlib import Path

from cs2esp.features.recoil_profiles import weapon_info
from cs2esp.ui.fonts import get_fonts_for_current_context, pop as pop_font, push_sized


ICON_DIRECTORY = Path(__file__).resolve().parents[1] / "assets" / "fonts"
ICON_FONT = ICON_DIRECTORY / "EventWeapons.ttf"
_METADATA = loads((ICON_DIRECTORY / "EventWeapons.json").read_text(encoding="utf-8"))
FONT_ICONS = _METADATA["icons"]
PUBLIC_FONT_ICONS = tuple(key for key in FONT_ICONS if key.startswith("weapon_") or key.startswith("event_bomb_"))
_ALIASES = {"p2000": "hkp2000", "sg553": "sg556", "incendiary": "incgrenade", "he": "hegrenade", "zeus": "taser", "planted_c4": "c4"}
_DISPLAY_NAMES = {"ak47": "AK-47", "awp": "AWP", "c4": "C4", "deagle": "Desert Eagle", "g3sg1": "G3SG1", "hkp2000": "P2000",
	"m4a1": "M4A4", "m4a1_silencer": "M4A1-S", "usp_silencer": "USP-S", "ssg08": "SSG 08", "sg556": "SG 553", "glock": "Glock-18",
	"flashbang": "Flashbang", "hegrenade": "HE grenade", "smokegrenade": "Smoke grenade", "molotov": "Molotov", "incgrenade": "Incendiary grenade",
	"decoy": "Decoy grenade", "tagrenade": "Tactical awareness grenade", "taser": "Zeus x27", "knife": "Knife (CT)", "knife_t": "Knife (T)"}
_KNIFE_ITEMS = {500: "bayonet", 503: "knife_css", 505: "knife_flip", 506: "knife_gut", 507: "knife_karambit", 508: "knife_m9_bayonet",
	509: "knife_tactical", 512: "knife_falchion", 514: "knife_survival_bowie", 515: "knife_butterfly", 516: "knife_push", 517: "knife_cord",
	518: "knife_canis", 519: "knife_ursus", 520: "knife_gypsy_jackknife", 521: "knife_outdoor", 522: "knife_stiletto", 523: "knife_widowmaker",
	525: "knife_skeleton", 526: "knife_kukri"}


def weapon_icon_key(name: str, item_definition: int = 0) -> str:
	'''Return only a real bundled silhouette; unknown source names remain unknown.'''
	if not name and item_definition:
		info = weapon_info(item_definition)
		name = info.key if info is not None else ""
	name = _KNIFE_ITEMS.get(item_definition, str(name or "").lower().removeprefix("weapon_").removesuffix("_projectile"))
	name = _ALIASES.get(name, name)
	key = "weapon_" + name
	return key if key in FONT_ICONS else ""


def glyph_label(name: str) -> str:
	'''Keep persisted icon keys out of human-facing selector labels.'''
	entry = FONT_ICONS.get(name)
	if entry is not None:
		return _DISPLAY_NAMES.get(name.removeprefix("weapon_"), entry["label"].replace("_", " ").title())
	return name.replace("badge_", "").replace("_", " ").title()


def draw_font_icon(draw_list, name: str, cx: float, cy: float, color: int, max_w: float = 24.0, max_h: float = 18.0, *, em_size: float | None = None) -> bool:
	'''Position the visible outline, not the font advance box, at the supplied centre.'''
	entry = FONT_ICONS.get(name)
	fonts = get_fonts_for_current_context()
	font = getattr(fonts, "event_icons", None) if fonts is not None else None
	if entry is None or font is None:
		return False
	left, bottom, right, top = entry["bounds"]
	units = float(_METADATA["units_per_em"])
	size = em_size if em_size is not None else min(max_w * units / max(1, right - left), max_h * units / max(1, top - bottom))
	x = cx - (left + right) * 0.5 / units * size
	y = cy - (_METADATA["ascent"] - (bottom + top) * 0.5) / units * size
	pushed = push_sized(font, size)
	if not pushed:
		return False
	try:
		draw_list.add_text((round(x * 2.0) * 0.5, round(y * 2.0) * 0.5), color, entry["char"])
	finally:
		pop_font(pushed)
	return True


def event_weapon_key(item) -> str:
	'''Only recorded weapon metadata selects a secondary weapon icon.'''
	data = getattr(item, "data", None) or {}
	return weapon_icon_key(data.get("weapon_key", ""), int(data.get("item_definition", 0) or 0))


def event_weapon_layout(item, main_glyph: str, scale: float) -> tuple[str, float]:
	'''Reserve the actual silhouette width; both visible gaps use the same eight-pixel spacing.'''
	key = "" if main_glyph.startswith("weapon_") else event_weapon_key(item)
	if not key:
		return "", 0.0
	left, bottom, right, top = FONT_ICONS[key]["bounds"]
	fit = min(26.0 / max(1, right - left), 18.0 / max(1, top - bottom))
	return key, (right - left) * fit * scale
