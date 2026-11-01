"""
Display refresh-rate, DPI, and monitor geometry helpers.
Rects are physical pixels so the GLFW framebuffer stays 1:1 with the display. make_process_dpi_aware must run before the first window,
or Dynamic Window Manager(DWM) stretches the layered overlay into a blank black surface. Non-Windows callers get documented fallbacks.
"""

import ctypes
import ctypes.wintypes as wt
from dataclasses import dataclass
from sys import platform


# Win32 plumbing; non-Windows callers get the 60 Hz fallback
if platform == "win32":
	_user32 = ctypes.windll.user32
	_shcore: object | None
	try:
		_shcore = ctypes.windll.shcore
	except OSError:
		_shcore = None
	_dwmapi: object | None
	try:
		_dwmapi = ctypes.windll.dwmapi
	except OSError:
		_dwmapi = None
else:
	_user32 = None
	_shcore = None
	_dwmapi = None


_ENUM_CURRENT_SETTINGS = -1
_CCHDEVICENAME = 32
_CCHFORMNAME = 32
_PER_MONITOR_AWARE = 2  # PROCESS_DPI_AWARENESS
_DPI_CONTEXT_PER_MONITOR_V2 = -4
_E_ACCESS_DENIED = -2147024891  # already-aware is success for us
_MONITOR_DEFAULTTONEAREST = 2


def make_process_dpi_aware() -> bool:
	'''Mark the process per-monitor True when DPI-aware. Must run before the first GLFW window.'''
	if platform != "win32" or _user32 is None:
		return False
	try:
		_user32.SetProcessDpiAwarenessContext.restype = ctypes.c_int
		if _user32.SetProcessDpiAwarenessContext(_DPI_CONTEXT_PER_MONITOR_V2):
			return True
	except (AttributeError, OSError):
		pass
	if _shcore is not None:
		try:
			result = _shcore.SetProcessDpiAwareness(_PER_MONITOR_AWARE)
			if result in (0, _E_ACCESS_DENIED):
				return True
		except (AttributeError, OSError):
			pass
	try:
		return bool(_user32.SetProcessDPIAware())
	except (AttributeError, OSError):
		return False


def is_process_dpi_aware() -> bool:
	'''True when the process is already DPI-aware.'''
	if platform != "win32" or _user32 is None:
		return False
	try:
		return bool(_user32.IsProcessDPIAware())
	except (AttributeError, OSError):
		return False


def dpi_scale_for_hwnd(hwnd: int = 0) -> float:
	'''UI scale of the monitor owning HWND (1.0 = 96 DPI); awareness changes geometry units, not this factor.'''
	if platform != "win32" or _user32 is None:
		return 1.0
	if hwnd:
		try:
			_user32.GetDpiForWindow.restype = ctypes.c_uint
			dpi = int(_user32.GetDpiForWindow(ctypes.c_void_p(int(hwnd))))
			if dpi > 0:
				return dpi / 96.0
		except (AttributeError, OSError):
			pass
	if _shcore is not None:
		try:
			monitor = _user32.MonitorFromWindow(ctypes.c_void_p(int(hwnd) or 0), _MONITOR_DEFAULTTONEAREST)
			if monitor:
				dpi_x = ctypes.c_uint(0)
				dpi_y = ctypes.c_uint(0)
				result = _shcore.GetDpiForMonitor(ctypes.c_void_p(monitor), 0, ctypes.byref(dpi_x), ctypes.byref(dpi_y))
				if result == 0 and dpi_x.value > 0:
					return dpi_x.value / 96.0
		except (AttributeError, OSError):
			pass
	return 1.0


class _DEVMODEW(ctypes.Structure):
	_fields_ = [("dmDeviceName", wt.WCHAR * _CCHDEVICENAME),
		("dmSpecVersion", wt.WORD), ("dmDriverVersion", wt.WORD), ("dmSize", wt.WORD), ("dmDriverExtra", wt.WORD), ("dmFields", wt.DWORD),
		("_pad_union", ctypes.c_byte * 16), ("dmColor", wt.SHORT), ("dmDuplex", wt.SHORT),
		("dmYResolution", wt.SHORT), ("dmTTOption", wt.SHORT), ("dmCollate", wt.SHORT), ("dmFormName", wt.WCHAR * _CCHFORMNAME), ("dmLogPixels", wt.WORD),
		("dmBitsPerPel", wt.DWORD), ("dmPelsWidth", wt.DWORD), ("dmPelsHeight", wt.DWORD), ("dmDisplayFlags", wt.DWORD), ("dmDisplayFrequency", wt.DWORD),
		# Tail of the struct (icon, panning, mux state) is irrelevant
	]


class _MONITORINFOEXW(ctypes.Structure):
	_fields_ = [("cbSize", wt.DWORD), ("rcMonitor", wt.RECT), ("rcWork", wt.RECT), ("dwFlags", wt.DWORD), ("szDevice", wt.WCHAR * _CCHDEVICENAME)]


class _DWM_TIMING_INFO(ctypes.Structure):
	_fields_ = [("cbSize", wt.DWORD), ("rateRefresh_uiNumerator", wt.UINT), ("rateRefresh_uiDenominator", wt.UINT),	("_pad_tail", ctypes.c_byte * (312 - 12))]


def get_display_refresh_hz(hwnd: int = 0, default: float = 60.0) -> float:
	'''Return DWM's precise refresh rate, then the display-mode fallback.'''
	if platform != "win32":
		return default

	if _dwmapi is not None:
		try:
			timing = _DWM_TIMING_INFO()
			timing.cbSize = ctypes.sizeof(_DWM_TIMING_INFO)
			if _dwmapi.DwmGetCompositionTimingInfo(0, ctypes.byref(timing)) == 0:
				numerator = int(timing.rateRefresh_uiNumerator)
				denominator = int(timing.rateRefresh_uiDenominator)
				if numerator > 0 and denominator > 0:
					return numerator / denominator
		except Exception:
			pass

	if _user32 is not None:
		try:
			# Let a zero HWND use the main display
			monitor_handle = _user32.MonitorFromWindow(ctypes.c_void_p(int(hwnd) or 0), 1)
			monitor = _MONITORINFOEXW()
			monitor.cbSize = ctypes.sizeof(_MONITORINFOEXW)
			if (monitor_handle and _user32.GetMonitorInfoW(ctypes.c_void_p(monitor_handle), ctypes.byref(monitor))):
				display_mode = _DEVMODEW()
				display_mode.dmSize = ctypes.sizeof(_DEVMODEW)
				if _user32.EnumDisplaySettingsW(monitor.szDevice, _ENUM_CURRENT_SETTINGS, ctypes.byref(display_mode)):
					refresh_hz = int(display_mode.dmDisplayFrequency)
					if refresh_hz > 1:
						return float(refresh_hz)
		except Exception:
			pass
	return default


def clamp_target_fps(user_target: int, hwnd: int = 0, floor: int = 30) -> int:
	'''Clamp the user's target_fps to [floor, display_hz].'''
	try:
		refresh_hz = get_display_refresh_hz(hwnd)
		ceiling = max(floor, int(round(refresh_hz)))
		return max(floor, min(int(user_target), ceiling))
	except Exception:
		return max(floor, int(user_target))


_MONITOR_DEFAULTTONULL = 0
_MONITORINFOF_PRIMARY = 0x1


def _physical_rect(logical_rect: tuple[int, int, int, int], hwnd: int = 0) -> tuple[int, int, int, int]:
	'''Scale an unaware caller's virtualised size to physical pixels; aware rects and the desktop origin pass through.'''
	left, top, width, height = logical_rect
	if is_process_dpi_aware():
		return (left, top, width, height)
	scale = dpi_scale_for_hwnd(hwnd)
	if scale <= 1.0 or width <= 0 or height <= 0:
		return (left, top, width, height)
	return (left, top, int(round(width * scale)), int(round(height * scale)))


def _monitor_rect(info: "_MONITORINFOEXW") -> tuple[int, int, int, int]:
	'''(left, top, w, h) from a filled MONITORINFOEXW's rcMonitor.'''
	rect = info.rcMonitor
	return (int(rect.left), int(rect.top), int(rect.right - rect.left), int(rect.bottom - rect.top))


def _query_monitor(handle) -> _MONITORINFOEXW | None:
	'''Fill and return a MONITORINFOEXW for handle, or None on failure.'''
	info = _MONITORINFOEXW()
	info.cbSize = ctypes.sizeof(_MONITORINFOEXW)
	if not _user32.GetMonitorInfoW(ctypes.c_void_p(handle), ctypes.byref(info)):
		return None
	return info


def _enum_monitor_handles() -> list[int]:
	'''Return every HMONITOR via EnumDisplayMonitors, [] on failure.'''
	handles: list[int] = []
	try:
		proc_type = ctypes.WINFUNCTYPE(ctypes.c_int, ctypes.c_void_p, ctypes.c_void_p, ctypes.POINTER(wt.RECT), ctypes.c_void_p)

		def _collect(handle, _dc, _rect, _lparam):
			handles.append(int(handle or 0))
			return 1

		_user32.EnumDisplayMonitors(None, None, proc_type(_collect), 0)
	except Exception:
		pass
	return handles


def pick_overlay_monitor(target_hwnd: int = 0) -> tuple[int, int, int, int]:
	'''Physical-pixel rect to cover: the target's monitor, the largest display, system metrics, then 1920x1080.'''
	if platform != "win32" or _user32 is None:
		return (0, 0, 1920, 1080)

	if target_hwnd:
		try:
			handle = _user32.MonitorFromWindow(ctypes.c_void_p(int(target_hwnd)), _MONITOR_DEFAULTTONULL)
			info = _query_monitor(handle) if handle else None
			if info is not None:
				return _physical_rect(_monitor_rect(info), target_hwnd)
		except Exception:
			pass

	best: tuple[int, int, int, int] | None = None
	best_area = 0
	for handle in _enum_monitor_handles():
		try:
			info = _query_monitor(handle)
			if info is None:
				continue
			rect = _monitor_rect(info)
			if rect[2] * rect[3] > best_area:
				best_area = rect[2] * rect[3]
				best = rect
		except Exception:
			continue

	if best is not None:
		return _physical_rect(best, target_hwnd)

	try:
		width = int(_user32.GetSystemMetrics(0))  # SM_CXSCREEN
		height = int(_user32.GetSystemMetrics(1))  # SM_CYSCREEN
		if width > 0 and height > 0:
			return _physical_rect((0, 0, width, height), target_hwnd)
	except Exception:
		pass

	return (0, 0, 1920, 1080)


@dataclass(slots=True)
class MonitorInfo:
	"""Physical-pixel monitor descriptor for the reposition loop."""
	handle: int
	left: int
	top: int
	width: int
	height: int
	is_primary: bool
	device_name: str = ""

	@property
	def rect(self) -> tuple[int, int, int, int]:
		return (self.left, self.top, self.width, self.height)

	def contains(self, x: int, y: int) -> bool:
		return (self.left <= x < self.left + self.width and self.top <= y < self.top + self.height)


def _monitor_info(handle, hwnd: int = 0) -> MonitorInfo | None:
	'''Build a MonitorInfo (physical px) from an HMONITOR, or None.'''
	info = _query_monitor(handle)
	if info is None:
		return None
	rect = _physical_rect(_monitor_rect(info), hwnd)
	return MonitorInfo(int(handle or 0), rect[0], rect[1], rect[2], rect[3], bool(int(info.dwFlags) & _MONITORINFOF_PRIMARY), str(info.szDevice))


def enum_monitors() -> list[MonitorInfo]:
	'''Every display in physical pixels; empty on failure. Lets the overlay follow CS2 across displays.'''
	if platform != "win32" or _user32 is None:
		return []
	monitors: list[MonitorInfo] = []
	for handle in _enum_monitor_handles():
		info = _monitor_info(handle)
		if info is not None:
			monitors.append(info)
	return monitors


def monitor_for_hwnd(hwnd: int = 0) -> MonitorInfo | None:
	'''MonitorInfo owning hwnd, or None when unresolved; DEFAULTTONULL avoids snapping to the primary display.'''
	if platform != "win32" or _user32 is None or not hwnd:
		return None
	try:
		handle = _user32.MonitorFromWindow(ctypes.c_void_p(int(hwnd)), _MONITOR_DEFAULTTONULL)
		return _monitor_info(handle, hwnd) if handle else None
	except Exception:
		return None


def rects_differ(a: tuple[int, int, int, int], b: tuple[int, int, int, int], tolerance: int = 0) -> bool:
	'''True when the rects differ beyond tolerance, so 1-px DPI jitter does not recreate the overlay.'''
	return any(abs(a[index] - b[index]) > tolerance for index in range(4))


__all__ = ["get_display_refresh_hz", "clamp_target_fps", "pick_overlay_monitor", "make_process_dpi_aware",
	"is_process_dpi_aware", "dpi_scale_for_hwnd", "enum_monitors", "monitor_for_hwnd", "rects_differ", "MonitorInfo"]