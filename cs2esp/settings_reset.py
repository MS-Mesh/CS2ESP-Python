"""
Restore explicitly owned parameters from the frozen user defaults without changing switches or keybinds.
The complete candidate is saved once; existing Section objects keep their identity for live controls.
"""

from copy import deepcopy
from functools import lru_cache
from json import loads
from pathlib import Path

from cs2esp.config import Config, _reload_into


RESET_DEFAULTS_PATH = Path(__file__).resolve().parent / "assets" / "reset_defaults.json"
_MISSING = object()
_BIND_FIELDS = frozenset(("hotkey", "hotkey_toggle", "toggle_menu_key", "master_toggle_key", "exit_key"))


@lru_cache(maxsize=1)
def _saved_defaults() -> dict:
	'''Read the shipped reference once, never the user's mutable live config.'''
	payload = loads(RESET_DEFAULTS_PATH.read_text(encoding="utf-8"))
	if not isinstance(payload.get("settings"), dict):
		raise ValueError("Saved reset defaults are missing their settings table")
	return payload["settings"]


def _restore_parameters(current, default, path: str, changed: list[str]):
	'''Keep every switch and binding while restoring only fields owned by the selected scope.'''
	field = path.rsplit(".", 1)[-1]
	if field in _BIND_FIELDS or field.startswith("hotkey") or isinstance(current, bool) or isinstance(default, bool):
		return current
	if isinstance(default, dict):
		result = deepcopy(current) if isinstance(current, dict) else {}
		for key, value in default.items():
			restored = _restore_parameters(result.get(key, _MISSING), value, f"{path}.{key}", changed)
			if restored is not _MISSING:
				result[key] = restored
		if path == "recoil_control.weapon_profiles":
			for weapon, profile in tuple(result.items()):
				if not isinstance(profile, dict):
					continue
				for key in ("strength_x", "strength_y", "start_bullet"):
					if key in profile and key not in default.get(weapon, {}):
						del profile[key]
						changed.append(f"{path}.{weapon}.{key}")
				if not profile:
					del result[weapon]
		return result
	if current is _MISSING or current != default:
		changed.append(path)
	return deepcopy(default)


def reset_parameters(config: Config, scopes: tuple[str, ...]) -> tuple[str, ...]:
	'''Restore selected parameter paths atomically, preserving bindings, switches and Section identities.'''
	reference = _saved_defaults()
	original = deepcopy(config._data)
	candidate = deepcopy(original)
	changed: list[str] = []
	for scope in scopes:
		parts = scope.split(".")
		if not scope or any(not part or part.startswith("_") for part in parts) or parts[0] == "meta":
			raise ValueError(f"Invalid reset scope: {scope!r}")
		defaults = reference
		parent = candidate
		for part in parts[:-1]:
			if not isinstance(defaults.get(part), dict) or not isinstance(parent.get(part), dict):
				break
			defaults, parent = defaults[part], parent[part]
		else:
			field = parts[-1]
			if field in defaults:
				restored = _restore_parameters(parent.get(field, _MISSING), defaults[field], scope, changed)
				if restored is not _MISSING:
					parent[field] = restored
	if not changed:
		return ()
	_reload_into(config, candidate, root=config)
	try:
		if not config.save():
			raise OSError("The reset could not be saved; existing settings were retained")
	except Exception:
		_reload_into(config, original, root=config)
		raise
	return tuple(dict.fromkeys(changed))
