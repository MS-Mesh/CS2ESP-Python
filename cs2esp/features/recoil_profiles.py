"""
Stable held-weapon identities and optional live-recoil parameter overrides. Profiles contain user settings, not spray patterns or weapon calibration;
omitted fields follow the existing global values.
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from math import isfinite


@dataclass(frozen=True, slots=True)
class WeaponInfo:
	"""Economy identity shared by the recoil runtime and profile editor."""
	item_definition: int
	key: str
	name: str
	is_firearm: bool = True
	scope_capable: bool = False


RECOIL_WEAPONS = (
	WeaponInfo(7, "weapon_ak47", "AK-47"),
	WeaponInfo(16, "weapon_m4a1", "M4A4"),
	WeaponInfo(60, "weapon_m4a1_silencer", "M4A1-S"),
	WeaponInfo(8, "weapon_aug", "AUG", scope_capable=True),
	WeaponInfo(10, "weapon_famas", "FAMAS"),
	WeaponInfo(13, "weapon_galilar", "Galil AR"),
	WeaponInfo(39, "weapon_sg556", "SG 553", scope_capable=True),
	WeaponInfo(17, "weapon_mac10", "MAC-10"),
	WeaponInfo(23, "weapon_mp5sd", "MP5-SD"),
	WeaponInfo(33, "weapon_mp7", "MP7"),
	WeaponInfo(34, "weapon_mp9", "MP9"),
	WeaponInfo(19, "weapon_p90", "P90"),
	WeaponInfo(26, "weapon_bizon", "PP-Bizon"),
	WeaponInfo(24, "weapon_ump45", "UMP-45"),
	WeaponInfo(14, "weapon_m249", "M249"),
	WeaponInfo(28, "weapon_negev", "Negev"),
	WeaponInfo(1, "weapon_deagle", "Desert Eagle"),
	WeaponInfo(2, "weapon_elite", "Dual Berettas"),
	WeaponInfo(3, "weapon_fiveseven", "Five-SeveN"),
	WeaponInfo(4, "weapon_glock", "Glock-18"),
	WeaponInfo(32, "weapon_hkp2000", "P2000"),
	WeaponInfo(36, "weapon_p250", "P250"),
	WeaponInfo(30, "weapon_tec9", "Tec-9"),
	WeaponInfo(61, "weapon_usp_silencer", "USP-S"),
	WeaponInfo(63, "weapon_cz75a", "CZ75-Auto"),
	WeaponInfo(64, "weapon_revolver", "R8 Revolver"),
	WeaponInfo(9, "weapon_awp", "AWP", scope_capable=True),
	WeaponInfo(11, "weapon_g3sg1", "G3SG1", scope_capable=True),
	WeaponInfo(38, "weapon_scar20", "SCAR-20", scope_capable=True),
	WeaponInfo(40, "weapon_ssg08", "SSG 08", scope_capable=True),
	WeaponInfo(27, "weapon_mag7", "MAG-7"),
	WeaponInfo(35, "weapon_nova", "Nova"),
	WeaponInfo(29, "weapon_sawedoff", "Sawed-Off"),
	WeaponInfo(25, "weapon_xm1014", "XM1014"),
)

_NON_FIREARMS = (
	WeaponInfo(31, "weapon_taser", "Zeus x27", False),
	WeaponInfo(42, "weapon_knife", "Knife", False),
	WeaponInfo(43, "weapon_flashbang", "Flashbang", False),
	WeaponInfo(44, "weapon_hegrenade", "HE grenade", False),
	WeaponInfo(45, "weapon_smokegrenade", "Smoke grenade", False),
	WeaponInfo(46, "weapon_molotov", "Molotov", False),
	WeaponInfo(47, "weapon_decoy", "Decoy grenade", False),
	WeaponInfo(48, "weapon_incgrenade", "Incendiary grenade", False),
	WeaponInfo(49, "weapon_c4", "C4", False),
	WeaponInfo(57, "weapon_healthshot", "Healthshot", False),
	WeaponInfo(68, "weapon_tagrenade", "Tactical awareness grenade", False),
	WeaponInfo(69, "weapon_fists", "Fists", False),
	WeaponInfo(70, "weapon_breachcharge", "Breach charge", False),
	WeaponInfo(72, "weapon_tablet", "Tablet", False),
	WeaponInfo(74, "weapon_melee", "Melee", False),
	WeaponInfo(75, "weapon_axe", "Axe", False),
	WeaponInfo(76, "weapon_hammer", "Hammer", False),
	WeaponInfo(78, "weapon_spanner", "Spanner", False),
	WeaponInfo(81, "weapon_firebomb", "Fire bomb", False),
	WeaponInfo(82, "weapon_diversion", "Diversion grenade", False),
	WeaponInfo(83, "weapon_frag_grenade", "Frag grenade", False),
	WeaponInfo(84, "weapon_snowball", "Snowball", False),
	WeaponInfo(85, "weapon_bumpmine", "Bump mine", False),
)

_WEAPONS_BY_ITEM = {weapon.item_definition: weapon for weapon in (*RECOIL_WEAPONS, *_NON_FIREARMS)}
_WEAPONS_BY_KEY = {weapon.key: weapon for weapon in RECOIL_WEAPONS}
_PROFILE_FIELDS = ("strength_x", "strength_y", "start_bullet")


@dataclass(frozen=True, slots=True)
class RecoilProfile:
	"""Effective parameters for the existing live aim-punch delta calculation."""
	strength_x: float = 1.0
	strength_y: float = 1.0
	start_bullet: int = 2


def weapon_info(item_definition: int) -> WeaponInfo | None:
	'''Resolve a stable item definition; knife finishes share one non-firearm identity.'''
	if isinstance(item_definition, bool) or not isinstance(item_definition, int):
		return None
	if item_definition in (59, 80) or 500 <= item_definition <= 526:
		return _WEAPONS_BY_ITEM[42]
	return _WEAPONS_BY_ITEM.get(item_definition)


def _field(section, name: str, default=None):
	data = getattr(section, "_data", None)
	if isinstance(data, Mapping):
		return data.get(name, default)
	if isinstance(section, Mapping):
		return section.get(name, default)
	return getattr(section, name, default)


def _profile_table(config) -> Mapping:
	profiles = _field(config, "weapon_profiles", {})
	if isinstance(profiles, Mapping):
		return profiles
	data = getattr(profiles, "_data", None)
	return data if isinstance(data, Mapping) else {}


def _parameter(value, field: str, fallback):
	if isinstance(value, bool):
		return fallback
	try:
		numeric = float(value)
	except (TypeError, ValueError, OverflowError):
		return fallback
	if not isfinite(numeric) or numeric < (1 if field == "start_bullet" else 0):
		return fallback
	if field == "start_bullet":
		return int(numeric) if numeric.is_integer() else fallback
	return numeric


def resolve_recoil_profile(config, weapon_key: str | None = None) -> RecoilProfile:
	'''Read field-wise overrides without creating profiles, writing settings, or reading game sensitivity.'''
	defaults = RecoilProfile()
	global_values = {}
	for field in _PROFILE_FIELDS:
		try:
			value = _field(config, field, getattr(defaults, field))
			global_values[field] = max(1, int(value)) if field == "start_bullet" else max(0.0, float(value))
			if not isfinite(global_values[field]):
				global_values[field] = getattr(defaults, field)
		except (TypeError, ValueError, OverflowError):
			global_values[field] = getattr(defaults, field)
	profile = _profile_table(config).get(weapon_key, {}) if weapon_key in _WEAPONS_BY_KEY else {}
	values = {field: _parameter(_field(profile, field, global_values[field]), field, global_values[field]) for field in _PROFILE_FIELDS}
	return RecoilProfile(**values)


def has_recoil_profile(config, weapon_key: str | None) -> bool:
	'''Whether this registered firearm has an authored profile table, including a partial override.'''
	profile = _profile_table(config).get(weapon_key)
	return weapon_key in _WEAPONS_BY_KEY and isinstance(profile, Mapping)


def set_recoil_profile_value(config, weapon_key: str | None, field: str, value: float | int) -> bool:
	'''Edit one field by copying the profile table; section caches child attributes, so readers use its backing data.'''
	if field not in _PROFILE_FIELDS or (weapon_key is not None and weapon_key not in _WEAPONS_BY_KEY):
		raise ValueError("Only registered firearm strength and start-bullet overrides can be edited")
	validated = _parameter(value, field, None)
	if validated is None:
		raise ValueError(f"Invalid recoil parameter: {field}")
	if weapon_key is None:
		if _field(config, field) == validated:
			return False
		if isinstance(config, dict):
			config[field] = validated
		else:
			setattr(config, field, validated)
		return True
	profiles = dict(_profile_table(config))
	previous = profiles.get(weapon_key, {})
	profile = dict(previous) if isinstance(previous, Mapping) else {}
	if profile.get(field) == validated:
		return False
	profile[field] = validated
	profiles[weapon_key] = profile
	if isinstance(config, dict):
		config["weapon_profiles"] = profiles
	else:
		setattr(config, "weapon_profiles", profiles)
	return True


def remove_recoil_profile(config, weapon_key: str) -> bool:
	'''Restore field-wise global fallback for just this firearm.'''
	if weapon_key not in _WEAPONS_BY_KEY:
		raise ValueError("Unknown recoil profile key")
	profiles = dict(_profile_table(config))
	if weapon_key not in profiles:
		return False
	del profiles[weapon_key]
	if isinstance(config, dict):
		config["weapon_profiles"] = profiles
	else:
		setattr(config, "weapon_profiles", profiles)
	return True


__all__ = [
	"RECOIL_WEAPONS",
	"WeaponInfo",
	"RecoilProfile",
	"weapon_info",
	"resolve_recoil_profile",
	"has_recoil_profile",
	"set_recoil_profile_value",
	"remove_recoil_profile",
]
