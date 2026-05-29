"""TOML config loader with nested attribute access and live saving."""

import sys
import tomllib
from os import fdopen, fsync, replace, unlink
from pathlib import Path
from shutil import copy2, copytree
from tempfile import mkstemp
from time import monotonic
from typing import Any

_MISSING = object()

try:
    import tomli_w
except ImportError:
    tomli_w = None


if getattr(sys, "frozen", False):
    CONFIG_DIR = Path(sys.executable).resolve().parent / "config"
    bundled_config_dir = Path(getattr(sys, "_MEIPASS", "")) / "config"
    try:
        if (not (CONFIG_DIR / "default.toml").exists()
                and bundled_config_dir.exists()):
            CONFIG_DIR.mkdir(parents=True, exist_ok=True)
            for source in bundled_config_dir.iterdir():
                destination = CONFIG_DIR / source.name
                if destination.exists():
                    continue
                if source.is_dir():
                    copytree(source, destination)
                else:
                    copy2(source, destination)
            print(f"[config] Seeded {CONFIG_DIR} from bundled defaults")
    except Exception as error:
        print(f"[config] Could not seed config dir: {error}")
        if bundled_config_dir.exists():
            CONFIG_DIR = bundled_config_dir
else:
    CONFIG_DIR = Path(__file__).resolve().parent.parent / "config"

DEFAULT_CONFIG = CONFIG_DIR / "default.toml"


def _bundled_default_path() -> Path | None:
    """Return the bundled or in-tree default.toml path."""
    if getattr(sys, "frozen", False):
        default_path = Path(getattr(sys, "_MEIPASS", "")) / "config" / "default.toml"
        return default_path if default_path.exists() else None
    default_path = Path(__file__).resolve().parent.parent / "config" / "default.toml"
    return default_path if default_path.exists() else None


def _deep_merge_missing(target: dict, source: dict, added_paths: list,
                        prefix: str = "") -> None:
    """Copy missing keys and record their dotted paths."""
    for key, source_value in source.items():
        dotted_path = f"{prefix}{key}"
        if key not in target:
            target[key] = source_value
            added_paths.append(dotted_path)
        elif isinstance(source_value, dict) and isinstance(target[key], dict):
            _deep_merge_missing(target[key], source_value, added_paths, dotted_path + ".")


def _set_dotted(target: dict, dotted: str, value: Any) -> bool:
    """Set a dotted-path leaf and return whether it changed."""
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
    current = target
    for part in dotted.split("."):
        if not isinstance(current, dict) or part not in current:
            return _MISSING
        current = current[part]
    return current


def _reload_into(section: "Section", fresh: dict, root: "Config") -> None:
    """Reload data while preserving existing Section object identities."""
    object.__setattr__(section, "_data", fresh)
    object.__setattr__(section, "_root", root)
    for key, existing in tuple(section.__dict__.items()):
        if (
            not key.startswith("_")
            and isinstance(existing, Section)
            and not isinstance(fresh.get(key), dict)
        ):
            object.__delattr__(section, key)
    for key, val in fresh.items():
        if not isinstance(val, dict):
            continue
        existing = section.__dict__.get(key)
        if isinstance(existing, Section):
            _reload_into(existing, val, root=root)
        else:
            object.__setattr__(section, key, Section(val, root=root))


class Section:
    """Attribute-access wrapper around a dict. Mutations propagate to underlying dict."""

    def __init__(self, data: dict, root: "Config | None" = None):
        object.__setattr__(self, "_data", data)
        object.__setattr__(self, "_root", root)
        for key, val in data.items():
            if isinstance(val, dict):
                object.__setattr__(self, key, Section(val, root=root or self))

    def __setattr__(self, name: str, value: Any) -> None:
        if name.startswith("_"):
            object.__setattr__(self, name, value)
            return
        if isinstance(value, Section):
            # Child sections live in ``__dict__`` so they keep Python identity across reloads - code that cached ``config.triggerbot`` still sees the same object after a TOML edit.
            object.__setattr__(self, name, value)
            return
        # Scalar writes go to _data only, so reload_if_changed cannot leave a stale mirror in __dict__.
        prev = self._data.get(name, _MISSING)
        self._data[name] = value
        if prev != value:
            root = getattr(self, "_root", None)
            if root is not None:
                object.__setattr__(root, "_dirty_at", monotonic())
                # Save bool/string controls immediately across processes; numeric sliders remain debounced because bool is checked before its int superclass.
                if isinstance(value, (bool, str)):
                    try:
                        root.save()
                    except Exception as e:
                        print(f"[config] live-save failed: {e!r}")

    def __getattr__(self, name: str) -> Any:
        if name.startswith("_"):
            raise AttributeError(name)
        try:
            return self._data[name]
        except KeyError as exc:
            raise AttributeError(name) from exc

    def __contains__(self, key: str) -> bool:
        return key in self._data


class Config(Section):
    """Top-level config — wraps the entire TOML file."""

    @classmethod
    def load(cls, path: Path | None = None) -> "Config":
        path = path or DEFAULT_CONFIG
        with open(path, "rb") as f:
            data = tomllib.load(f)

        # Forward-compat: merge bundled-default sections/keys the user's config is missing, so adding a feature section (e.g. [rapidfire]) doesn't AttributeError-crash an older default.toml. Existing user values are preserved; we only fill in absentees.
        bundled_path = _bundled_default_path()
        added: list[str] = []
        upgraded: list[str] = []
        removed: list[str] = []
        if bundled_path is not None and bundled_path != path:
            try:
                with open(bundled_path, "rb") as bf:
                    bundled = tomllib.load(bf)
                # Force-reset stale defaults FIRST (before merge-missing, so newly-added schema keys aren't overwritten by the upgrade).
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
                        if (
                            bundled_value is not _MISSING
                            and _set_dotted(data, dotted_key, bundled_value)
                        ):
                            upgraded.append(dotted_key)
                    data.setdefault("meta", {})["schema_version"] = bundled_version
                if upgraded:
                    print(f"[config] Schema upgrade reset {len(upgraded)} "
                          f"key(s) to new defaults: {', '.join(upgraded)}")
                if removed:
                    print(f"[config] Removed {len(removed)} retired key(s): "
                          f"{', '.join(removed)}")
                _deep_merge_missing(data, bundled, added)
                if added:
                    print(f"[config] Merged {len(added)} missing key(s) "
                          f"from bundled defaults: {', '.join(added[:8])}"
                          f"{'\u2026' if len(added) > 8 else ''}")
            except Exception as e:
                print(f"[config] Could not merge bundled defaults: {e}")

        cfg = cls(data)
        object.__setattr__(cfg, "_path", path)
        object.__setattr__(cfg, "_dirty_at", 0.0)
        object.__setattr__(cfg, "_root", cfg)
        # Seed the mtime cache so reload_if_changed() has a baseline; without it the first poll always reloads (mtime > 0 vs cached 0), thrashing the parser.
        try:
            object.__setattr__(cfg, "_last_mtime", path.stat().st_mtime)
        except Exception:
            object.__setattr__(cfg, "_last_mtime", 0.0)
        # If anything was added, persist the merged file so next launch starts clean and the user can see/edit the new keys.
        if added or upgraded or removed:
            try:
                cfg.save()
            except Exception:
                pass
        return cfg

    def autosave_if_due(self) -> bool:
        """Save iff dirty and the autosave delay elapsed since the last edit."""
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
        ok = self.save()
        object.__setattr__(self, "_dirty_at", 0.0)
        return ok

    def save(self, path: Path | None = None) -> bool:
        if tomli_w is None:
            print("[config] tomli_w not installed — pip install tomli-w to enable save")
            return False
        target = path or getattr(self, "_path", DEFAULT_CONFIG)
        try:
            target = Path(target)
            target.parent.mkdir(parents=True, exist_ok=True)
            temp_fd, temp_path = mkstemp(
                prefix=".default-", suffix=".toml.tmp",
                dir=str(target.parent),
            )
            try:
                with fdopen(temp_fd, "wb") as config_file:
                    tomli_w.dump(self._data, config_file)
                    try:
                        config_file.flush()
                        fsync(config_file.fileno())
                    except OSError:
                        pass
                replace(temp_path, target)
            except Exception:
                try:
                    unlink(temp_path)
                except OSError:
                    pass
                raise
            try:
                object.__setattr__(self, "_last_mtime", target.stat().st_mtime)
            except Exception:
                pass
            print(f"[config] Saved to {target}")
            return True
        except Exception as e:
            print(f"[config] Save failed: {e}")
            return False

    def reload_if_changed(self) -> bool:
        """Reload from disk when the TOML mtime advances."""
        path = getattr(self, "_path", None)
        if path is None:
            return False
        try:
            mtime = path.stat().st_mtime
        except OSError:
            return False
        last = getattr(self, "_last_mtime", 0.0)
        if mtime <= last:
            return False
        try:
            with open(path, "rb") as f:
                fresh = tomllib.load(f)
        except Exception as e:
            print(f"[config] Reload skipped (parse failed): {e!r}")
            return False
        object.__setattr__(self, "_last_mtime", mtime)
        bundled_path = _bundled_default_path()
        if bundled_path is not None and bundled_path != path:
            try:
                with open(bundled_path, "rb") as bf:
                    bundled = tomllib.load(bf)
                added: list[str] = []
                _deep_merge_missing(fresh, bundled, added)
                if added:
                    print(f"[config] Reload self-healed {len(added)} "
                          f"missing key(s) from bundled defaults: "
                          f"{', '.join(added[:8])}"
                          f"{'\u2026' if len(added) > 8 else ''}")
            except Exception as e:
                print(f"[config] Reload self-heal skipped: {e!r}")
        _reload_into(self, fresh, root=self)
        return True
