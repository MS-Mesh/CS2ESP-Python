"""
TOML config loader with nested attribute access and live saving.
"""

import ctypes
import sys
import tomllib
from contextlib import contextmanager
from copy import deepcopy
from ctypes import wintypes
from hashlib import sha256
from os import environ, fdopen, fsync, replace, unlink
from pathlib import Path
from shutil import copy2, copytree
from tempfile import mkstemp
from time import monotonic, sleep
from typing import Any

_MISSING = object()
_KERNEL32 = ctypes.WinDLL("kernel32", use_last_error=True)
_KERNEL32.CreateMutexW.argtypes = [ctypes.c_void_p, wintypes.BOOL, wintypes.LPCWSTR]
_KERNEL32.CreateMutexW.restype = wintypes.HANDLE
_KERNEL32.WaitForSingleObject.argtypes = [wintypes.HANDLE, wintypes.DWORD]
_KERNEL32.WaitForSingleObject.restype = wintypes.DWORD
_KERNEL32.ReleaseMutex.argtypes = [wintypes.HANDLE]
_KERNEL32.ReleaseMutex.restype = wintypes.BOOL
_KERNEL32.CloseHandle.argtypes = [wintypes.HANDLE]
_KERNEL32.CloseHandle.restype = wintypes.BOOL

try:
	import tomli_w
except ImportError:
	tomli_w = None


if getattr(sys, "frozen", False):
	CONFIG_DIR = Path(sys.executable).resolve().parent / "config"
	bundled_config_dir = Path(getattr(sys, "_MEIPASS", "")) / "config"
	try:
		if not (CONFIG_DIR / "default.toml").exists() and bundled_config_dir.exists():
			CONFIG_DIR.mkdir(parents=True, exist_ok=True)
			for source in bundled_config_dir.iterdir():
				destination = CONFIG_DIR / source.name
				if destination.exists():
					continue
				if source.is_dir():
					copytree(source, destination)
				else:
					copy2(source, destination)
			print(f'[config] Seeded {CONFIG_DIR} from bundled defaults')
	except Exception as error:
		print(f'[config] Could not seed config dir: {error}')
		if bundled_config_dir.exists():
			CONFIG_DIR = bundled_config_dir
else:
	CONFIG_DIR = Path(__file__).resolve().parent.parent / "config"

# Overlay and spawned Qt processes share the sandbox override for config, IPC and offsets
if environ.get("CS2ESP_CONFIG_DIR"):
	CONFIG_DIR = Path(environ["CS2ESP_CONFIG_DIR"]).resolve()

DEFAULT_CONFIG = CONFIG_DIR / "default.toml"


def _bundled_default_path() -> Path | None:
	'''Return the bundled or in-tree default.toml path.'''
	if getattr(sys, "frozen", False):
		default_path = Path(getattr(sys, "_MEIPASS", "")) / "config" / "default.toml"
		return default_path if default_path.exists() else None
	default_path = Path(__file__).resolve().parent.parent / "config" / "default.toml"
	return default_path if default_path.exists() else None


def _deep_merge_missing(target: dict, source: dict, added_paths: list, prefix: str = "") -> None:
	'''Copy missing keys and record their dotted paths.'''
	for key, source_value in source.items():
		dotted_path = f"{prefix}{key}"
		if key not in target:
			target[key] = source_value
			added_paths.append(dotted_path)
		elif isinstance(source_value, dict) and isinstance(target[key], dict):
			_deep_merge_missing(target[key], source_value, added_paths, dotted_path + ".")


def _set_dotted(target: dict, dotted: str, value: Any) -> bool:
	'''Set a dotted-path leaf and return whether it changed.'''
	parts = dotted.split(".")
	current = target
	for part in parts[:-1]:
		next_value = current.get(part)
		if not isinstance(next_value, dict):
			return False
		current = next_value
	leaf = parts[-1]
	if current.get(leaf) == value:
		return False
	current[leaf] = value
	return True


def _get_dotted(target: dict, dotted: str) -> Any:
	'''Resolve a dotted path or return _MISSING.'''
	current = target
	for part in dotted.split("."):
		if not isinstance(current, dict) or part not in current:
			return _MISSING
		current = current[part]
	return current


def _merge_changes(target: dict, previous: dict, current: dict) -> None:
	'''Apply only local edits to the latest disk state, preserving changes from the other process.'''
	for key in previous.keys() - current.keys():
		target.pop(key, None)
	for key, value in current.items():
		before = previous.get(key, _MISSING)
		if isinstance(value, dict) and isinstance(before, dict):
			if not isinstance(target.get(key), dict):
				if value == before:
					continue
				target[key] = {}
			_merge_changes(target[key], before, value)
		elif before is _MISSING or value != before:
			target[key] = deepcopy(value)


@contextmanager
def _config_write_lock(path: Path):
	'''Serialize the menu and overlay's read/merge/replace transaction for this config file.'''
	identity = sha256(str(path.resolve()).casefold().encode("utf-8")).hexdigest()
	handle = _KERNEL32.CreateMutexW(None, False, "Local\\MSMeshConfig-" + identity)
	if not handle:
		raise ctypes.WinError(ctypes.get_last_error())
	try:
		result = _KERNEL32.WaitForSingleObject(handle, 2000)
		if result not in (0, 0x80):
			raise OSError(f'Config write lock failed: {result}')
		try:
			yield
		finally:
			_KERNEL32.ReleaseMutex(handle)
	finally:
		_KERNEL32.CloseHandle(handle)


def _reload_into(section: "Section", fresh: dict, root: "Config") -> None:
	'''Reload data while preserving existing Section object identities.'''
	object.__setattr__(section, "_data", fresh)
	object.__setattr__(section, "_root", root)
	for key, existing in tuple(section.__dict__.items()):
		if not key.startswith("_") and isinstance(existing, Section) and not isinstance(fresh.get(key), dict):
			object.__delattr__(section, key)
	for key, value in fresh.items():
		if not isinstance(value, dict):
			continue
		existing = section.__dict__.get(key)
		if isinstance(existing, Section):
			_reload_into(existing, value, root=root)
		else:
			object.__setattr__(section, key, Section(value, root=root))


class Section:
	"""Attribute-access wrapper around a dict; mutations reach the underlying dict."""
	def __init__(self, data: dict, root: "Config | None" = None):
		object.__setattr__(self, "_data", data)
		object.__setattr__(self, "_root", root)
		for key, value in data.items():
			if isinstance(value, dict):
				object.__setattr__(self, key, Section(value, root=root or self))

	def __setattr__(self, name: str, value: Any) -> None:
		if name.startswith("_"):
			object.__setattr__(self, name, value)
			return
		if isinstance(value, Section):
			# Cached child sections keep their Python identity across TOML reloads
			object.__setattr__(self, name, value)
			return
		# Scalars stay only in _data so reloads cannot leave stale mirrors in __dict__
		previous_value = self._data.get(name, _MISSING)
		self._data[name] = value
		if previous_value != value:
			root = getattr(self, "_root", None)
			if root is not None:
				object.__setattr__(root, "_dirty_at", monotonic())
				# Bool/string controls save immediately; numeric sliders remain debounced
				if isinstance(value, (bool, str)):
					try:
						root.save()
					except Exception as error:
						print(f'[config] live-save failed: {error!r}')

	def __getattr__(self, name: str) -> Any:
		if name.startswith("_"):
			raise AttributeError(name)
		try:
			return self._data[name]
		except KeyError as error:
			raise AttributeError(name) from error

	def __contains__(self, key: str) -> bool:
		return key in self._data


class Config(Section):
	"""Top-level config — wraps the entire TOML file."""
	@classmethod
	def load(cls, path: Path | None = None) -> "Config":
		'''Load a TOML file, applying bundled-default upgrades and newly seeded keys.'''
		path = path or DEFAULT_CONFIG
		with open(path, "rb") as config_file:
			data = tomllib.load(config_file)
		previous = deepcopy(data)

		# Older configs receive missing defaults; only the schema's explicit reset/remove paths change existing values
		bundled_path = _bundled_default_path()
		added: list[str] = []
		upgraded: list[str] = []
		removed: list[str] = []
		if bundled_path is not None and bundled_path != path:
			try:
				with open(bundled_path, "rb") as bundled_file:
					bundled = tomllib.load(bundled_file)
				# Apply versioned resets before merging new keys so the upgrade cannot overwrite newly seeded values
				bundled_meta = bundled.get("meta", {})
				user_meta = data.get("meta", {})
				bundled_version = int(bundled_meta.get("schema_version", 0) or 0)
				user_version = int(user_meta.get("schema_version", 0) or 0)
				if bundled_version > user_version:
					for dotted_key in bundled_meta.get("remove_on_upgrade") or []:
						path_parts = dotted_key.split(".")
						parent = data
						for path_part in path_parts[:-1]:
							next_parent = parent.get(path_part)
							if not isinstance(next_parent, dict):
								break
							parent = next_parent
						else:
							if path_parts[-1] in parent:
								del parent[path_parts[-1]]
								removed.append(dotted_key)
					for dotted_key in bundled_meta.get("reset_on_upgrade") or []:
						bundled_value = _get_dotted(bundled, dotted_key)
						if bundled_value is not _MISSING and _set_dotted(data, dotted_key, bundled_value):
							upgraded.append(dotted_key)
					data.setdefault("meta", {})["schema_version"] = bundled_version
				if upgraded:
					print(f"[config] Schema upgrade reset {len(upgraded)} key(s) to new defaults: {', '.join(upgraded)}")
				if removed:
					print(f"[config] Removed {len(removed)} retired key(s): {', '.join(removed)}")
				_deep_merge_missing(data, bundled, added)
				if added:
					print(f"[config] Merged {len(added)} missing key(s) from bundled defaults: {', '.join(added[:8])}{'\u2026' if len(added) > 8 else ''}")
			except Exception as error:
				print(f'[config] Could not merge bundled defaults: {error}')

		config = cls(data)
		object.__setattr__(config, "_path", path)
		object.__setattr__(config, "_dirty_at", 0.0)
		object.__setattr__(config, "_root", config)
		object.__setattr__(config, "_base_data", previous)
		# Seed the mtime cache to avoid an unnecessary first-poll reload
		try:
			object.__setattr__(config, "_last_mtime", path.stat().st_mtime)
		except Exception:
			object.__setattr__(config, "_last_mtime", 0.0)
		if added or upgraded or removed:
			try:
				config.save()
			except Exception:
				pass
		print(f'[config] Loaded settings from {path}')
		return config

	def autosave_if_due(self) -> bool:
		'''Save iff dirty and the autosave delay elapsed since the last edit.'''
		try:
			general = self._data.get("general", {})
			if not general.get("autosave", False):
				return False
			interval = float(general.get("autosave_interval_sec", 1.0))
		except Exception:
			return False
		dirty_at = getattr(self, "_dirty_at", 0.0)
		if dirty_at <= 0.0:
			return False
		if monotonic() - dirty_at < interval:
			return False
		saved = self.save()
		if saved:
			object.__setattr__(self, "_dirty_at", 0.0)
		return saved

	def flush_pending(self) -> bool:
		'''Persist edits before shutdown without waiting for the autosave debounce.'''
		return self.save() if getattr(self, "_dirty_at", 0.0) > 0.0 else True

	def save(self, path: Path | None = None) -> bool:
		'''Merge local edits under a process-shared lock, then atomically replace the config file.'''
		if tomli_w is None:
			print('[config] tomli_w not installed — pip install tomli-w to enable save')
			return False
		target = path or getattr(self, "_path", DEFAULT_CONFIG)
		try:
			target = Path(target)
			target.parent.mkdir(parents=True, exist_ok=True)
			with _config_write_lock(target):
				saved_data = deepcopy(self._data)
				current_path = Path(getattr(self, "_path", target)).resolve() == target.resolve()
				previous = getattr(self, "_base_data", None)
				if current_path and previous is not None and target.exists():
					with target.open("rb") as config_file:
						saved_data = tomllib.load(config_file)
					_merge_changes(saved_data, previous, self._data)
				temp_fd, temp_path = mkstemp(prefix=".default-", suffix=".toml.tmp", dir=str(target.parent))
				try:
					with fdopen(temp_fd, "wb") as config_file:
						tomli_w.dump(saved_data, config_file)
						try:
							config_file.flush()
							fsync(config_file.fileno())
						except OSError:
							pass
					# Windows readers may briefly deny the atomic replace
					for attempt in range(6):
						try:
							replace(temp_path, target)
							break
						except PermissionError:
							if attempt == 5:
								raise
							sleep(0.02 * (attempt + 1))
				except Exception:
					try:
						unlink(temp_path)
					except OSError:
						pass
					raise
				if current_path:
					_reload_into(self, saved_data, root=self)
					object.__setattr__(self, "_base_data", deepcopy(saved_data))
					object.__setattr__(self, "_dirty_at", 0.0)
					try:
						object.__setattr__(self, "_last_mtime", target.stat().st_mtime)
					except OSError:
						pass
			print(f'[config] Saved to {target}')
			return True
		except Exception as error:
			print(f'[config] Save failed: {error}')
			return False

	def reload_if_changed(self) -> bool:
		'''Reload from disk when the TOML mtime advances.'''
		path = getattr(self, "_path", None)
		if path is None:
			return False
		try:
			mtime = path.stat().st_mtime
		except OSError:
			return False
		last_mtime = getattr(self, "_last_mtime", 0.0)
		if mtime <= last_mtime:
			return False
		try:
			with open(path, "rb") as config_file:
				fresh = tomllib.load(config_file)
		except Exception as error:
			print(f'[config] Reload skipped (parse failed): {error!r}')
			return False
		previous = deepcopy(fresh)
		object.__setattr__(self, "_last_mtime", mtime)
		bundled_path = _bundled_default_path()
		if bundled_path is not None and bundled_path != path:
			try:
				with open(bundled_path, "rb") as bundled_file:
					bundled = tomllib.load(bundled_file)
				added: list[str] = []
				_deep_merge_missing(fresh, bundled, added)
				if added:
					print(f"[config] Reload self-healed {len(added)} missing key(s) from bundled defaults: {', '.join(added[:8])}{'\u2026' if len(added) > 8 else ''}")
			except Exception as error:
				print(f'[config] Reload self-heal skipped: {error!r}')
		_merge_changes(fresh, getattr(self, "_base_data", self._data), self._data)
		_reload_into(self, fresh, root=self)
		object.__setattr__(self, "_base_data", previous)
		return True
