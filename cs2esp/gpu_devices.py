"""
Discover render devices and prepare the selected OpenGL backend before window creation.
GPU selection is advisory; CPU rendering loads Mesa. Registry entries also cover adapters omitted by the display API.
"""

from __future__ import annotations

import ctypes
import hashlib
import os
import re
import shutil
import sys
from dataclasses import dataclass
from pathlib import Path

_DISPLAY_CLASS = r"SYSTEM\CurrentControlSet\Control\Class\{4d36e968-e325-11ce-bfc1-08002be10318}"
# Legacy compatibility flag; device discovery does not currently set it
FORCE_COLORKEY = False
_DISPLAY_DEVICE_ACTIVE = 0x1
_INTEGRATED_HINTS = (
	"intel(r) uhd", "intel uhd", "intel(r) iris", "intel iris", "intel(r) hd graphics", "intel hd graphics", "radeon(tm) graphics",
	"radeon graphics", "radeon vega", "780m", "680m", "760m",
)


@dataclass(frozen=True, slots=True)
class RenderDevice:
	"""One selectable render device and its menu label."""
	key: str          # "auto" | "cpu" | "gpu:<Windows adapter identity>"
	label: str
	kind: str         # "auto" | "cpu" | "integrated" | "discrete"
	name: str = ""
	memory_mb: int = 0
	# None means display attachment is unknown, e.g. a registry-only adapter
	on_display: bool | None = None


def _cpu_name() -> str:
	try:
		import winreg
		with winreg.OpenKey(winreg.HKEY_LOCAL_MACHINE, r"HARDWARE\DESCRIPTION\System\CentralProcessor\0") as key:
			return str(winreg.QueryValueEx(key, "ProcessorNameString")[0]).strip()
	except Exception:
		return "CPU"


def _adapters() -> list[tuple[str, str, int]]:
	'''(registry slot, name, dedicated memory MB); may include stale drivers.'''
	found: list[tuple[str, str, int]] = []
	try:
		import winreg
		with winreg.OpenKey(winreg.HKEY_LOCAL_MACHINE, _DISPLAY_CLASS) as root:
			index = 0
			while True:
				try:
					sub = winreg.EnumKey(root, index)
				except OSError:
					break
				index += 1
				if not sub.isdigit():
					continue
				try:
					with winreg.OpenKey(root, sub) as key:
						name = str(winreg.QueryValueEx(key, "DriverDesc")[0]).strip()
						memory = 0
						for value_name in ("HardwareInformation.qwMemorySize", "HardwareInformation.MemorySize"):
							try:
								raw = winreg.QueryValueEx(key, value_name)[0]
								if isinstance(raw, bytes):
									raw = int.from_bytes(raw[:8], "little")
								memory = int(raw)
								break
							except OSError:
								continue
				except OSError:
					continue
				lowered = name.lower()
				if (not name or "basic display" in lowered or "remote" in lowered
						or "virtual" in lowered or "parsec" in lowered):
					continue
				found.append((sub, name, memory // (1024 * 1024)))
	except Exception:
		pass
	return found


def _classify(name: str) -> str:
	'''Conservative hints only: smaller VRAM does not imply integrated GPU.'''
	lowered = name.lower()
	if any(hint in lowered for hint in _INTEGRATED_HINTS):
		return "integrated"
	if "nvidia" in lowered or "geforce" in lowered or "radeon rx" in lowered:
		return "discrete"
	return "unknown"


class _DISPLAY_DEVICE(ctypes.Structure):
	_fields_ = [("cb", ctypes.c_ulong), ("DeviceName", ctypes.c_wchar * 32),
		("DeviceString", ctypes.c_wchar * 128), ("StateFlags", ctypes.c_ulong), ("DeviceID", ctypes.c_wchar * 128), ("DeviceKey", ctypes.c_wchar * 128)]


def _iter_display_devices(limit: int | None = 256):
	'''Yield display devices until EnumDisplayDevicesW stops reporting them or limit attempts pass.'''
	user32 = ctypes.windll.user32
	index = 0
	while limit is None or index < limit:
		display_device = _DISPLAY_DEVICE()
		display_device.cb = ctypes.sizeof(_DISPLAY_DEVICE)
		if not user32.EnumDisplayDevicesW(None, index, ctypes.byref(display_device), 0):
			break
		yield display_device
		index += 1


def _enumerate_video_adapters() -> list[tuple[str, str, bool]]:
	'''(adapter ID, name, drives an active display), grouped by Video GUID.'''
	# Group output ports by adapter identity; drivers may omit it and reinstalling can change it
	found: dict[str, tuple[str, bool]] = {}
	try:
		for display_device in _iter_display_devices():
			name = display_device.DeviceString.strip()
			lowered = name.lower()
			if not name or any(word in lowered for word in ("basic display", "remote", "virtual", "parsec")):
				continue
			match = re.search(r"(?i)\\video\\\{([0-9a-f-]+)\}", display_device.DeviceKey)
			if match:
				identity = match.group(1).lower()
			else:
				fallback = display_device.DeviceKey or display_device.DeviceName
				if not fallback:
					continue
				identity = "unstable-" + hashlib.sha256(fallback.lower().encode()).hexdigest()[:16]
			was_active = found.get(identity, (name, False))[1]
			found[identity] = (name, was_active or bool(display_device.StateFlags & _DISPLAY_DEVICE_ACTIVE))
	except Exception:
		pass
	return [(identity, name, active) for identity, (name, active) in found.items()]


def display_gpu_names() -> set[str]:
	'''Names of adapters that have an active display attached right now.'''
	names: set[str] = set()
	try:
		for display_device in _iter_display_devices(None):
			if display_device.StateFlags & _DISPLAY_DEVICE_ACTIVE:
				names.add(display_device.DeviceString.strip())
	except Exception:
		pass
	return names


def list_render_devices() -> list[RenderDevice]:
	'''Every selectable device, with Auto first and the CPU fallback last.'''
	devices = [RenderDevice("auto", "Auto — Windows default", "auto")]
	adapters = _enumerate_video_adapters()
	registry = _adapters()
	# Registry-only adapters remain selectable without assuming display connectivity
	enum_counts: dict[str, int] = {}
	for _, name, _ in adapters:
		enum_counts[name.casefold()] = enum_counts.get(name.casefold(), 0) + 1
	seen_registry: dict[str, int] = {}
	for slot, name, _ in registry:
		folded = name.casefold()
		seen_registry[folded] = seen_registry.get(folded, 0) + 1
		if seen_registry[folded] > enum_counts.get(folded, 0):
			adapters.append((f"registry-{slot}", name, None))
	gpu_number = 0
	for identity, name, on_display in adapters:
		kind = _classify(name)
		memory = max((adapter_memory for _, adapter_name, adapter_memory in registry if adapter_name.casefold() == name.casefold()), default=0)
		if kind == "integrated":
			label = f"Integrated graphics — {name}"
		else:
			gpu_number += 1
			label = f"GPU {gpu_number} — {name}"
		if memory:
			label += f" ({memory / 1024:.1f} GB)" if memory >= 1024 else f" ({memory} MB)"
		if on_display is None:
			label += " · availability unverified"
		else:
			label += " · drives display" if on_display else " · no display"
		devices.append(RenderDevice(f"gpu:{identity}", label, kind, name, memory, on_display))
	devices.append(RenderDevice("cpu", f"CPU — {_cpu_name()} (software, slow)", "cpu", _cpu_name()))
	return devices


def resolve(key: str) -> RenderDevice:
	'''Device for key, falling back to Auto for unknown or ambiguous legacy names.'''
	key = str(key or "auto").strip()
	devices = list_render_devices()
	for device in devices:
		if device.key == key:
			return device
	# Legacy gpu:<name> configuration migrates only when unambiguous
	if key.startswith("gpu:"):
		matches = [device for device in devices if device.kind in ("integrated", "discrete", "unknown")
				   and device.name and device.name.casefold() == key[4:].casefold()]
		if len(matches) == 1:
			return matches[0]
		if len(matches) > 1:
			print(f"[gpu] legacy device name {key[4:]!r} is ambiguous; using Auto.")
	return devices[0]


def interpreter_path() -> str:
	'''The frozen app executable or the base Python used for a venv.'''
	if getattr(sys, "frozen", False):
		return str(Path(sys.executable).resolve())
	return str(Path(getattr(sys, "_base_executable", "") or sys.executable).resolve())


def _load_software_gl() -> bool:
	'''Make Mesa llvmpipe the process' opengl32.dll (call before GLFW init).'''
	try:
		import PySide6
		source = Path(PySide6.__file__).with_name("opengl32sw.dll")
	except Exception:
		return False
	if not source.is_file():
		print("[gpu] opengl32sw.dll not found — CPU rendering unavailable.")
		return False
	from cs2esp.config import CONFIG_DIR
	target_dir = Path(CONFIG_DIR) / "softgl"
	target = target_dir / "opengl32.dll"
	try:
		target_dir.mkdir(parents=True, exist_ok=True)
		if not target.is_file() or target.stat().st_size != source.stat().st_size:
			shutil.copy2(source, target)
		# GLFW and PyOpenGL use different lookup paths; both must resolve the same library
		ctypes.windll.kernel32.SetDllDirectoryW(str(target_dir))
		os.environ["PATH"] = str(target_dir) + os.pathsep + os.environ.get("PATH", "")
		ctypes.WinDLL(str(target))
		os.environ["GALLIUM_DRIVER"] = "llvmpipe"
		# Preserve an explicit Mesa thread limit; otherwise use the logical CPU count
		os.environ.setdefault("LP_NUM_THREADS", str(os.cpu_count() or 4))
		return True
	except Exception as exc:
		print(f"[gpu] software OpenGL load failed: {exc!r}")
		return False


def read_configured_key(config_path) -> str:
	'''[overlay].render_device straight from the TOML (no Config import).'''
	try:
		import tomllib
		with open(config_path, "rb") as handle:
			data = tomllib.load(handle)
		return str(data.get("overlay", {}).get("render_device", "auto") or "auto")
	except Exception:
		return "auto"


def apply_render_device(key: str) -> RenderDevice:
	'''Choose software GL or leave hardware choice to Windows, without registry writes.'''
	global ACTIVE_DEVICE, SELECTION_WARNING
	SELECTION_WARNING = None
	device = resolve(key)
	requested_key = str(key or "auto").strip()
	if device.kind == "auto" and requested_key != "auto":
		SELECTION_WARNING = "Requested renderer was not found or its legacy name is ambiguous; using Auto."
	if device.kind == "cpu":
		if not _load_software_gl():
			SELECTION_WARNING = "CPU software renderer could not be loaded; using Auto."
			device = list_render_devices()[0]
	elif device.kind in ("integrated", "discrete", "unknown"):
		SELECTION_WARNING = (f"{device.name} was requested, but direct GPU selection is not supported yet. "
							 f"Windows chooses the renderer; verify Active renderer after startup.")
		device = list_render_devices()[0]
	if SELECTION_WARNING:
		print(f"[gpu] {SELECTION_WARNING}")
	print(f"[gpu] render device: {device.label}")
	ACTIVE_DEVICE = device
	return device


__all__ = ["RenderDevice", "list_render_devices", "resolve", "apply_render_device", "interpreter_path", "read_configured_key", "ACTIVE_DEVICE", "SELECTION_WARNING"]

ACTIVE_DEVICE: RenderDevice | None = None
SELECTION_WARNING: str | None = None
