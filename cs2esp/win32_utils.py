"""
Win32 helpers for overlay windows, foreground handoff, capture flags, and ping.
The wrappers keep unsupported Windows attributes quiet so overlay and menu code can call them without platform/version branching.
"""

import ctypes
import ctypes.wintypes as wintypes
from socket import gethostbyname, inet_aton
from threading import Event, Thread


GWL_EXSTYLE = -20
WS_EX_TRANSPARENT = 0x00000020
WS_EX_LAYERED = 0x00080000
WS_EX_TOPMOST = 0x00000008
WS_EX_TOOLWINDOW = 0x00000080
WS_EX_NOACTIVATE = 0x08000000
HWND_TOPMOST = -1
HWND_NOTOPMOST = -2
SWP_NOSIZE = 0x0001
SWP_NOMOVE = 0x0002
SWP_NOACTIVATE = 0x0010
SWP_SHOWWINDOW = 0x0040

user32 = ctypes.windll.user32
dwmapi = ctypes.windll.dwmapi

# Declare pointer-sized HWND arguments to avoid truncation on x64
user32.SetWindowPos.argtypes = [wintypes.HWND, wintypes.HWND, ctypes.c_int, ctypes.c_int, ctypes.c_int, ctypes.c_int, wintypes.UINT]
user32.SetWindowPos.restype = wintypes.BOOL
user32.BringWindowToTop.argtypes = [wintypes.HWND]
user32.BringWindowToTop.restype = wintypes.BOOL


def make_overlay_clickthrough(hwnd: int, click_through: bool) -> None:
	'''Enable or disable WS_EX_TRANSPARENT on an existing layered window.'''
	style = user32.GetWindowLongW(hwnd, GWL_EXSTYLE)
	# Adding WS_EX_LAYERED here breaks GLFW per-pixel alpha under DWM
	if click_through:
		style |= WS_EX_TRANSPARENT
	else:
		style &= ~WS_EX_TRANSPARENT
	user32.SetWindowLongW(hwnd, GWL_EXSTYLE, style)


# GLFW passthrough can leave colorkey state behind; reset it before explicit transparency
LWA_COLORKEY = 0x00000001


def set_colorkey_transparency(hwnd: int, key_r: int = 0, key_g: int = 0, key_b: int = 0) -> bool:
	'''Force LWA_COLORKEY compositing with the given transparent RGB key.'''
	if not hwnd:
		return False
	try:
		colour_ref = ((int(key_r) & 0xFF) | ((int(key_g) & 0xFF) << 8) | ((int(key_b) & 0xFF) << 16))
		ok = user32.SetLayeredWindowAttributes(hwnd, ctypes.c_uint(colour_ref), 0, LWA_COLORKEY)
		return bool(ok)
	except Exception:
		return False


DWMWA_CLOAKED = 14


def _is_cloaked(hwnd: int) -> bool:
	'''True for DWM-cloaked windows (other virtual desktops, suspended UWP).'''
	try:
		val = ctypes.c_int(0)
		if dwmapi.DwmGetWindowAttribute(wintypes.HWND(hwnd), ctypes.c_uint(DWMWA_CLOAKED), ctypes.byref(val), ctypes.sizeof(val)) == 0:
			return bool(val.value)
	except Exception:
		pass
	return False


def window_rect(hwnd: int) -> tuple[int, int, int, int] | None:
	'''(left, top, w, h) in screen px, or None when unavailable.'''
	if not hwnd:
		return None
	try:
		rect = wintypes.RECT()
		if not user32.GetWindowRect(wintypes.HWND(hwnd), ctypes.byref(rect)):
			return None
		width = rect.right - rect.left
		height = rect.bottom - rect.top
		if width <= 0 or height <= 0:
			return None
		return (rect.left, rect.top, width, height)
	except Exception:
		return None


def windows_covering(game_hwnd: int, skip_hwnds: tuple[int, ...] = ()) -> list[tuple[int, int, int, int]]:
	'''Screen rects of every top-level window above game_hwnd in the z-order.'''
	# Clip windows above the game instead of painting the topmost overlay across them
	if not game_hwnd:
		return []
	skip = set(int(skip_hwnd) for skip_hwnd in skip_hwnds if skip_hwnd)
	rects: list[tuple[int, int, int, int]] = []
	try:
		hwnd = user32.GetTopWindow(None)
		guard = 0
		while hwnd and guard < 512:
			guard += 1
			current = int(hwnd)
			if current == int(game_hwnd):
				break
			if current not in skip and user32.IsWindowVisible(hwnd) and not _is_cloaked(current):
				exstyle = user32.GetWindowLongW(hwnd, GWL_EXSTYLE)
				if not (exstyle & WS_EX_TOOLWINDOW):
					rect = window_rect(current)
					if rect:
						rects.append(rect)
			hwnd = user32.GetWindow(hwnd, 2)  # GW_HWNDNEXT
	except Exception:
		pass
	return rects


def set_exclusion_region(hwnd: int, rects: list[tuple[int, int, int, int]]) -> bool:
	'''Clip the window to everything except the given screen-space rects.'''
	# Cut the menu out of the overlay region; an empty list restores the full rectangle
	if not hwnd:
		return False
	try:
		gdi32 = ctypes.windll.gdi32
		gdi32.CreateRectRgn.restype = ctypes.c_void_p
		gdi32.CreateRectRgn.argtypes = [ctypes.c_int] * 4
		gdi32.CombineRgn.restype = ctypes.c_int
		gdi32.CombineRgn.argtypes = [ctypes.c_void_p] * 3 + [ctypes.c_int]
		gdi32.DeleteObject.argtypes = [ctypes.c_void_p]
		user32.SetWindowRgn.argtypes = [wintypes.HWND, ctypes.c_void_p, wintypes.BOOL]
		user32.SetWindowRgn.restype = ctypes.c_int

		win = wintypes.RECT()
		if not user32.GetWindowRect(hwnd, ctypes.byref(win)):
			return False
		w_span = win.right - win.left
		h_span = win.bottom - win.top
		if w_span <= 0 or h_span <= 0:
			return False
		region = gdi32.CreateRectRgn(0, 0, w_span, h_span)
		if not region:
			return False

		origin_x, origin_y = win.left, win.top
		for (left, top, width, height) in rects:
			if width <= 0 or height <= 0:
				continue
			hole = gdi32.CreateRectRgn(int(left - origin_x), int(top - origin_y), int(left - origin_x) + int(width), int(top - origin_y) + int(height))
			if hole:
				gdi32.CombineRgn(region, region, hole, 4)  # RGN_DIFF
				gdi32.DeleteObject(hole)

		ok = bool(user32.SetWindowRgn(wintypes.HWND(hwnd), region, True))
		if not ok:
			gdi32.DeleteObject(region)
		return ok
	except Exception:
		return False


def reset_window_region(hwnd: int) -> None:
	'''Restore the window to its full rectangle.'''
	if not hwnd:
		return
	try:
		user32.SetWindowRgn.argtypes = [wintypes.HWND, ctypes.c_void_p, wintypes.BOOL]
		user32.SetWindowRgn(wintypes.HWND(hwnd), None, True)
	except Exception:
		pass


def set_topmost(hwnd: int, topmost: bool = True) -> None:
	if not hwnd:
		return
	user32.SetWindowPos(
		hwnd, HWND_TOPMOST if topmost else HWND_NOTOPMOST,
		0, 0, 0, 0, SWP_NOMOVE | SWP_NOSIZE | SWP_NOACTIVATE)


def bring_window_to_front(hwnd: int) -> None:
	'''Move hwnd to the front of the topmost band without stealing focus.'''
	# Cycle the topmost band because an already-topmost window cannot be reordered with the same flag
	if not hwnd:
		return
	flags = SWP_NOMOVE | SWP_NOSIZE | SWP_NOACTIVATE
	try:
		user32.SetWindowPos(hwnd, HWND_NOTOPMOST, 0, 0, 0, 0, flags)
		user32.SetWindowPos(hwnd, HWND_TOPMOST, 0, 0, 0, 0, flags)
	except Exception:
		pass


def set_noactivate(hwnd: int, noactivate: bool) -> None:
	'''Add or remove WS_EX_NOACTIVATE on an existing window.'''
	style = user32.GetWindowLongW(hwnd, GWL_EXSTYLE)
	if noactivate:
		style |= WS_EX_NOACTIVATE
	else:
		style &= ~WS_EX_NOACTIVATE
	user32.SetWindowLongW(hwnd, GWL_EXSTYLE, style)


def extend_frame_into_client(hwnd: int) -> None:
	margins = (ctypes.c_int * 4)(-1, -1, -1, -1)
	try:
		dwmapi.DwmExtendFrameIntoClientArea(hwnd, ctypes.byref(margins))
	except Exception:
		pass


DWMWA_DISALLOW_PEEK = 11
DWMWA_EXCLUDED_FROM_PEEK = 12
DWMWA_BORDER_COLOR = 34
DWMWA_CAPTION_COLOR = 35
DWMWA_COLOR_NONE = 0xFFFFFFFE


def _dwm_set_attr(hwnd: int, attr: int, value: int) -> bool:
	'''Set one DWM attribute and return False on unsupported calls.'''
	if not hwnd:
		return False
	try:
		attr_value = ctypes.c_int(int(value))
		result = dwmapi.DwmSetWindowAttribute(wintypes.HWND(hwnd), ctypes.c_uint(int(attr)), ctypes.byref(attr_value), ctypes.sizeof(attr_value))
		return result == 0
	except Exception:
		return False


def apply_taskbar_invisibility(hwnd: int) -> None:
	'''Hide overlay thumbnails and borders from shell preview surfaces.'''
	_dwm_set_attr(hwnd, DWMWA_DISALLOW_PEEK, 1)
	_dwm_set_attr(hwnd, DWMWA_EXCLUDED_FROM_PEEK, 1)
	_dwm_set_attr(hwnd, DWMWA_BORDER_COLOR, DWMWA_COLOR_NONE)
	_dwm_set_attr(hwnd, DWMWA_CAPTION_COLOR, DWMWA_COLOR_NONE)


WDA_NONE = 0x00
WDA_EXCLUDEFROMCAPTURE = 0x11


class _ACCENT_POLICY(ctypes.Structure):
	_fields_ = [("AccentState", ctypes.c_int), ("AccentFlags", ctypes.c_int), ("GradientColor", ctypes.c_uint), ("AnimationId", ctypes.c_int)]


class _WINCOMPATTRDATA(ctypes.Structure):
	_fields_ = [("Attribute", ctypes.c_int), ("Data", ctypes.c_void_p), ("SizeOfData", ctypes.c_size_t)]


def set_blur_behind(hwnd: int, enabled: bool) -> bool:
	'''Toggle DWM blur-behind on hwnd, falling back quietly when unsupported.'''
	# Blur the background without blurring this window's own pixels
	if not hwnd:
		return False
	try:
		accent = _ACCENT_POLICY(3 if enabled else 0, 2, 0, 0)
		data = _WINCOMPATTRDATA(
			19,  # WCA_ACCENT_POLICY
			ctypes.cast(ctypes.byref(accent), ctypes.c_void_p), ctypes.sizeof(accent))
		return bool(user32.SetWindowCompositionAttribute(hwnd, ctypes.byref(data)))
	except Exception:
		return False


def set_capture_protect(hwnd: int, enabled: bool) -> bool:
	affinity = WDA_EXCLUDEFROMCAPTURE if enabled else WDA_NONE
	try:
		ok = user32.SetWindowDisplayAffinity(hwnd, affinity)
		return bool(ok)
	except Exception:
		return False


def get_client_rect(hwnd: int) -> tuple[int, int, int, int] | None:
	'''(left, top, w, h) of hwnd's client area in screen px, or None.'''
	# Return physical client bounds; let the caller handle DPI-virtualised games through its monitor fallback
	if not hwnd:
		return None
	try:
		client = wintypes.RECT()
		if not user32.GetClientRect(ctypes.c_void_p(int(hwnd)), ctypes.byref(client)):
			return None
		width = int(client.right - client.left)
		height = int(client.bottom - client.top)
		if width <= 0 or height <= 0:
			return None
		origin = wintypes.POINT(client.left, client.top)
		user32.ClientToScreen(ctypes.c_void_p(int(hwnd)), ctypes.byref(origin))
		return (int(origin.x), int(origin.y), width, height)
	except Exception:
		return None


_ENUM_WINDOWS_PROC = ctypes.WINFUNCTYPE(ctypes.c_bool, ctypes.c_void_p, ctypes.c_void_p)


def _enum_windows(callback) -> None:
	'''Call callback for each top-level window; returning False stops enumeration.'''
	try:
		user32.EnumWindows(_ENUM_WINDOWS_PROC(callback), 0)
	except Exception:
		pass


def find_window_by_pid(pid: int) -> int:
	'''Return the first visible titled top-level window owned by pid, or 0.'''
	if not pid:
		return 0
	found = [0]

	def _callback(hwnd, _lparam):
		if not user32.IsWindowVisible(hwnd):
			return True
		owner = ctypes.c_ulong(0)
		user32.GetWindowThreadProcessId(hwnd, ctypes.byref(owner))
		if owner.value == pid and user32.GetWindowTextLengthW(hwnd) > 0:
			found[0] = hwnd
			return False
		return True

	_enum_windows(_callback)
	return int(found[0])


def find_visible_window_by_title(title: str) -> int:
	'''First visible top-level window with this exact title, otherwise a prefix match.'''
	# Ignore hidden Qt helper windows that share the visible menu title
	exact = [0]
	prefix = [0]
	stem = title.split(" \u00b7 ")[0]

	def _callback(hwnd, _lparam):
		if not user32.IsWindowVisible(hwnd):
			return True
		length = user32.GetWindowTextLengthW(hwnd)
		if length <= 0:
			return True
		buffer = ctypes.create_unicode_buffer(length + 1)
		user32.GetWindowTextW(hwnd, buffer, length + 1)
		text = buffer.value
		if text == title:
			exact[0] = hwnd
			return False
		if not prefix[0] and stem and text.startswith(stem):
			prefix[0] = hwnd
		return True

	_enum_windows(_callback)
	return int(exact[0] or prefix[0])


def focus_window(hwnd: int) -> bool:
	'''Bring hwnd to the foreground while restoring Win32 focus state.'''
	if not hwnd:
		return False
	SPI_GETFOREGROUNDLOCKTIMEOUT = 0x2000
	SPI_SETFOREGROUNDLOCKTIMEOUT = 0x2001
	SPIF_SENDCHANGE = 0x0002
	SW_RESTORE = 9
	VK_MENU = 0x12
	KEYEVENTF_KEYUP = 0x0002
	saved_timeout = ctypes.c_uint(0)
	timeout_changed = False
	attached_foreground = False
	attached_target = False
	current_thread_id = 0
	foreground_thread_id = 0
	target_thread_id = 0
	try:
		try:
			user32.ShowWindow(hwnd, SW_RESTORE)
		except Exception:
			pass
		try:
			user32.SystemParametersInfoW(SPI_GETFOREGROUNDLOCKTIMEOUT, 0, ctypes.byref(saved_timeout), 0)
			ok = user32.SystemParametersInfoW(SPI_SETFOREGROUNDLOCKTIMEOUT, 0, 0, SPIF_SENDCHANGE)
			timeout_changed = bool(ok)
		except Exception:
			pass
		try:
			user32.keybd_event(VK_MENU, 0, 0, 0)
			user32.keybd_event(VK_MENU, 0, KEYEVENTF_KEYUP, 0)
		except Exception:
			pass
		try:
			kernel32 = ctypes.windll.kernel32
			current_thread_id = kernel32.GetCurrentThreadId()
			foreground_hwnd = user32.GetForegroundWindow()
			foreground_thread_id = (user32.GetWindowThreadProcessId(foreground_hwnd, None) if foreground_hwnd else 0)
			target_thread_id = user32.GetWindowThreadProcessId(hwnd, None)
			if foreground_thread_id and foreground_thread_id != current_thread_id:
				attached_foreground = bool(user32.AttachThreadInput(current_thread_id, foreground_thread_id, True))
			if (target_thread_id and target_thread_id != current_thread_id and target_thread_id != foreground_thread_id):
				attached_target = bool(user32.AttachThreadInput(current_thread_id, target_thread_id, True))
		except Exception:
			pass
		try:
			user32.BringWindowToTop(hwnd)
		except Exception:
			pass
		try:
			ok = bool(user32.SetForegroundWindow(hwnd))
		except Exception:
			ok = False
		try:
			user32.SetActiveWindow(hwnd)
		except Exception:
			pass
		try:
			user32.SetFocus(hwnd)
		except Exception:
			pass
		return ok
	finally:
		try:
			if attached_target:
				user32.AttachThreadInput(current_thread_id, target_thread_id, False)
			if attached_foreground:
				user32.AttachThreadInput(current_thread_id, foreground_thread_id, False)
		except Exception:
			pass
		if timeout_changed:
			try:
				user32.SystemParametersInfoW(SPI_SETFOREGROUNDLOCKTIMEOUT, 0, ctypes.c_void_p(saved_timeout.value), SPIF_SENDCHANGE)
			except Exception:
				pass


_iphlp = ctypes.windll.iphlpapi
_iphlp.IcmpCreateFile.restype = wintypes.HANDLE
_iphlp.IcmpCloseHandle.argtypes = [wintypes.HANDLE]
_iphlp.IcmpCloseHandle.restype = wintypes.BOOL


class _IcmpEchoReply(ctypes.Structure):
	"""Mirrors Win32's ICMP_ECHO_REPLY."""
	_fields_ = [("Address", ctypes.c_uint32),
		("Status", ctypes.c_uint32), ("RoundTripTime", ctypes.c_uint32), ("DataSize", ctypes.c_uint16), ("Reserved", ctypes.c_uint16), ("Data", ctypes.c_void_p),
		("Ttl", ctypes.c_ubyte), ("Tos", ctypes.c_ubyte), ("Flags", ctypes.c_ubyte), ("OptionsSize", ctypes.c_ubyte), ("OptionsData", ctypes.c_void_p)]


_iphlp.IcmpSendEcho.argtypes = [
	wintypes.HANDLE, ctypes.c_uint32, ctypes.c_void_p, ctypes.c_uint16, ctypes.c_void_p, ctypes.c_void_p, ctypes.c_uint32, ctypes.c_uint32]
_iphlp.IcmpSendEcho.restype = ctypes.c_uint32

DEFAULT_PING_TARGET = "1.1.1.1"


def icmp_ping_ms(host: str = DEFAULT_PING_TARGET, timeout_ms: int = 1000) -> int | None:
	'''Send one ICMP echo and return RTT in ms, or None on failure.'''
	try:
		addr_str = gethostbyname(host)
	except OSError:
		return None
	try:
		addr_bytes = inet_aton(addr_str)
		dest_u32 = int.from_bytes(addr_bytes, "little")
	except OSError:
		return None
	handle = _iphlp.IcmpCreateFile()
	if not handle or handle == wintypes.HANDLE(-1).value:
		return None
	try:
		payload = b"A" * 32
		reply_size = ctypes.sizeof(_IcmpEchoReply) + len(payload) + 8
		reply_buffer = (ctypes.c_ubyte * reply_size)()
		replies = _iphlp.IcmpSendEcho(handle, ctypes.c_uint32(dest_u32),
			payload, ctypes.c_uint16(len(payload)), None, ctypes.cast(reply_buffer, ctypes.c_void_p), ctypes.c_uint32(reply_size), ctypes.c_uint32(timeout_ms))
		if replies == 0:
			return None
		reply = ctypes.cast(reply_buffer, ctypes.POINTER(_IcmpEchoReply)).contents
		if reply.Status != 0:
			return None
		return int(reply.RoundTripTime)
	finally:
		_iphlp.IcmpCloseHandle(handle)


class PingPoller:
	"""Daemon thread that keeps the most recent RTT cached."""
	def __init__(self, host: str = DEFAULT_PING_TARGET, interval_s: float = 2.0, timeout_ms: int = 1000) -> None:
		self._host = host
		self._interval = max(0.5, float(interval_s))
		self._timeout_ms = int(timeout_ms)
		self._last_ms: int | None = None
		self._stop = Event()
		self._thread: Thread | None = None

	def start(self) -> None:
		if self._thread is not None and self._thread.is_alive():
			return
		self._stop.clear()
		self._thread = Thread(target=self._run, name="cs2esp.ping-poller", daemon=True)
		self._thread.start()

	def stop(self, join_timeout_s: float = 0.5) -> None:
		self._stop.set()
		if self._thread is not None:
			self._thread.join(timeout=join_timeout_s)

	def get_ms(self) -> int | None:
		'''Last successful RTT in ms, or None if no ping has answered yet.'''
		return self._last_ms

	def _run(self) -> None:
		while not self._stop.is_set():
			ping_ms = icmp_ping_ms(self._host, timeout_ms=self._timeout_ms)
			if ping_ms is not None:
				self._last_ms = ping_ms
			self._stop.wait(self._interval)


ERROR_ALREADY_EXISTS = 183


def create_single_instance_mutex(name: str = "Local\\CS2_ESP_Antigravity_Singleton") -> tuple[int, bool]:
	'''Create a named Win32 mutex; returns (handle, another_instance_is_running).'''
	try:
		kernel32 = ctypes.windll.kernel32
		handle = kernel32.CreateMutexW(None, False, str(name))
		if not handle:
			return 0, False
		already_exists = (kernel32.GetLastError() == ERROR_ALREADY_EXISTS)
		return int(handle), already_exists
	except Exception:
		return 0, False


def is_window_minimized(hwnd: int) -> bool:
	'''Return True if hwnd is iconic (minimized to taskbar).'''
	if not hwnd:
		return False
	try:
		return bool(user32.IsIconic(ctypes.c_void_p(hwnd)))
	except Exception:
		return False


def is_window_valid(hwnd: int) -> bool:
	'''Return True if hwnd is an existing Win32 window.'''
	if not hwnd:
		return False
	try:
		return bool(user32.IsWindow(ctypes.c_void_p(hwnd)))
	except Exception:
		return False
