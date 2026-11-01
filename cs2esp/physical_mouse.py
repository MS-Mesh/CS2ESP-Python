"""
Physical mouse-button state via Raw Input.
Some vendor drivers never update the async XBUTTON state, so GetAsyncKeyState alone misses Mouse4/Mouse5.
A WH_MOUSE_LL hook would catch them but sits in the system input chain and adds latency to every mouse-move, which is unacceptable for a game overlay.
Raw Input with RIDEV_INPUTSINK receives background button events without intercepting the input stream.
"""

import ctypes
import ctypes.wintypes as wintypes
import threading

_user32 = ctypes.windll.user32
_kernel32 = ctypes.windll.kernel32

WM_INPUT = 0x00FF
WM_CLOSE = 0x0010
WM_DESTROY = 0x0002

RIDEV_INPUTSINK = 0x00000100
RID_INPUT = 0x10000003
RIM_TYPEMOUSE = 0
MOUSE_USAGE_PAGE = 0x01
MOUSE_USAGE = 0x02

RI_MOUSE_BUTTON_1_DOWN = 0x0001
RI_MOUSE_BUTTON_1_UP = 0x0002
RI_MOUSE_BUTTON_2_DOWN = 0x0004
RI_MOUSE_BUTTON_2_UP = 0x0008
RI_MOUSE_BUTTON_3_DOWN = 0x0010
RI_MOUSE_BUTTON_3_UP = 0x0020
RI_MOUSE_BUTTON_4_DOWN = 0x0040
RI_MOUSE_BUTTON_4_UP = 0x0080
RI_MOUSE_BUTTON_5_DOWN = 0x0100
RI_MOUSE_BUTTON_5_UP = 0x0200

VK_LBUTTON = 0x01
VK_RBUTTON = 0x02
VK_MBUTTON = 0x04
VK_XBUTTON1 = 0x05
VK_XBUTTON2 = 0x06

_RAWINPUT_ERROR = 0xFFFFFFFF
_CLASS_NAME = "CS2EspRawMouse"

_LRESULT = ctypes.c_ssize_t
_WNDPROC = ctypes.WINFUNCTYPE(_LRESULT, wintypes.HWND, wintypes.UINT, wintypes.WPARAM, wintypes.LPARAM)


class _RAWINPUTDEVICE(ctypes.Structure):
	_fields_ = (("usUsagePage", wintypes.USHORT), ("usUsage", wintypes.USHORT), ("dwFlags", wintypes.DWORD), ("hwndTarget", wintypes.HWND))


class _RAWINPUTHEADER(ctypes.Structure):
	_fields_ = (("dwType", wintypes.DWORD), ("dwSize", wintypes.DWORD), ("hDevice", wintypes.HANDLE), ("wParam", wintypes.WPARAM))


class _RAWMOUSE(ctypes.Structure):
	_fields_ = (
		# usButtonFlags/usButtonData overlay the ulButtons union member
		("usFlags", wintypes.USHORT), ("_pad", wintypes.USHORT), ("usButtonFlags", wintypes.USHORT), ("usButtonData", wintypes.USHORT),
		("ulRawButtons", wintypes.ULONG), ("lLastX", wintypes.LONG), ("lLastY", wintypes.LONG), ("ulExtraInformation", wintypes.ULONG))


class _RAWINPUT(ctypes.Structure):
	_fields_ = (("header", _RAWINPUTHEADER), ("mouse", _RAWMOUSE))


class _PhysicalMouseState:
	"""Lazily-started raw-input tracker for hardware button transitions."""
	def __init__(self) -> None:
		self._down: set[int] = set()
		self._lock = threading.Lock()
		self._start_lock = threading.Lock()
		self._thread: threading.Thread | None = None
		self._thread_id: int | None = None
		self._hwnd = 0
		self._available = False
		self._started = False

	@property
	def available(self) -> bool:
		return self._available

	def ensure_started(self) -> None:
		if self._started:
			return
		with self._start_lock:
			if self._started:
				return
			self._started = True
			self._thread = threading.Thread(target=self._run, name="physical-mouse", daemon=True)
			self._thread.start()

	def _run(self) -> None:
		try:
			self._thread_id = _kernel32.GetCurrentThreadId()
			_kernel32.GetModuleHandleW.restype = ctypes.c_void_p
			instance = _kernel32.GetModuleHandleW(None)
			_user32.CreateWindowExW.restype = ctypes.c_void_p
			_user32.CreateWindowExW.argtypes = [wintypes.DWORD, wintypes.LPCWSTR, wintypes.LPCWSTR,
				wintypes.DWORD, ctypes.c_int, ctypes.c_int, ctypes.c_int, ctypes.c_int, ctypes.c_void_p, ctypes.c_void_p, ctypes.c_void_p, ctypes.c_void_p]
			_user32.DefWindowProcW.restype = _LRESULT
			_user32.DefWindowProcW.argtypes = [wintypes.HWND, wintypes.UINT, wintypes.WPARAM, wintypes.LPARAM]
			_user32.RegisterRawInputDevices.argtypes = [ctypes.POINTER(_RAWINPUTDEVICE), wintypes.UINT, wintypes.UINT]
			_user32.GetRawInputData.argtypes = [wintypes.HANDLE, wintypes.UINT, ctypes.c_void_p, ctypes.POINTER(wintypes.UINT), wintypes.UINT]

			self._wndproc = _WNDPROC(self._window_proc)

			class _WNDCLASS(ctypes.Structure):
				_fields_ = (("style", wintypes.UINT), ("lpfnWndProc", _WNDPROC),
					("cbClsExtra", ctypes.c_int), ("cbWndExtra", ctypes.c_int), ("hInstance", wintypes.HINSTANCE), ("hIcon", wintypes.HICON),
					("hCursor", wintypes.HANDLE), ("hbrBackground", wintypes.HBRUSH), ("lpszMenuName", wintypes.LPCWSTR), ("lpszClassName", wintypes.LPCWSTR))

			window_class = _WNDCLASS()
			window_class.lpfnWndProc = self._wndproc
			window_class.hInstance = instance
			window_class.lpszClassName = _CLASS_NAME
			atom = _user32.RegisterClassW(ctypes.byref(window_class))
			if not atom and ctypes.GetLastError() != 1410:  # already registered
				print(f"[mouse] RegisterClass failed: {ctypes.GetLastError()}")
				return

			self._hwnd = int(_user32.CreateWindowExW(0, _CLASS_NAME, "raw-mouse", 0, 0, 0, 0, 0, None, None, instance, None) or 0)
			if not self._hwnd:
				print(f"[mouse] CreateWindow failed: {ctypes.GetLastError()}")
				return

			device = _RAWINPUTDEVICE(MOUSE_USAGE_PAGE, MOUSE_USAGE, RIDEV_INPUTSINK, self._hwnd)
			if not _user32.RegisterRawInputDevices(ctypes.byref(device), 1, ctypes.sizeof(_RAWINPUTDEVICE)):
				print(f"[mouse] RegisterRawInputDevices failed: {ctypes.GetLastError()}")
				return

			self._available = True
			print("[mouse] raw input button tracker running")

			message = wintypes.MSG()
			while _user32.GetMessageW(ctypes.byref(message), None, 0, 0) > 0:
				_user32.TranslateMessage(ctypes.byref(message))
				_user32.DispatchMessageW(ctypes.byref(message))
		except Exception as error:
			print(f"[mouse] raw input tracker died: {error!r}")
		finally:
			self._available = False
			with self._lock:
				self._down.clear()
			if self._hwnd:
				try:
					_user32.DestroyWindow(self._hwnd)
				except Exception:
					pass
				self._hwnd = 0

	def _window_proc(self, hwnd, message, wparam, lparam):
		try:
			if message == WM_INPUT:
				self._consume_raw_input(lparam)
			elif message == WM_CLOSE:
				_user32.DestroyWindow(hwnd)
				return 0
			elif message == WM_DESTROY:
				_user32.PostQuitMessage(0)
				return 0
		except Exception:
			pass
		return _user32.DefWindowProcW(hwnd, message, wparam, lparam)

	def _consume_raw_input(self, lparam: int) -> None:
		buffer = ctypes.create_string_buffer(ctypes.sizeof(_RAWINPUT))
		size = wintypes.UINT(ctypes.sizeof(buffer))
		read = _user32.GetRawInputData(ctypes.c_void_p(int(lparam)), RID_INPUT, buffer, ctypes.byref(size), ctypes.sizeof(_RAWINPUTHEADER))
		if read in (_RAWINPUT_ERROR, 0):
			return
		raw = ctypes.cast(buffer, ctypes.POINTER(_RAWINPUT)).contents
		if raw.header.dwType != RIM_TYPEMOUSE:
			return
		self._update_from_flags(int(raw.mouse.usButtonFlags))

	def _update_from_flags(self, flags: int) -> None:
		for mask, vk, down in ((RI_MOUSE_BUTTON_1_DOWN, VK_LBUTTON, True),
			(RI_MOUSE_BUTTON_1_UP, VK_LBUTTON, False), (RI_MOUSE_BUTTON_2_DOWN, VK_RBUTTON, True), (RI_MOUSE_BUTTON_2_UP, VK_RBUTTON, False),
			(RI_MOUSE_BUTTON_3_DOWN, VK_MBUTTON, True), (RI_MOUSE_BUTTON_3_UP, VK_MBUTTON, False), (RI_MOUSE_BUTTON_4_DOWN, VK_XBUTTON1, True),
			(RI_MOUSE_BUTTON_4_UP, VK_XBUTTON1, False), (RI_MOUSE_BUTTON_5_DOWN, VK_XBUTTON2, True), (RI_MOUSE_BUTTON_5_UP, VK_XBUTTON2, False)):
			if flags & mask:
				with self._lock:
					if down:
						self._down.add(vk)
					else:
						self._down.discard(vk)

	def is_down(self, vk: int) -> bool:
		# Set membership is atomic under the GIL; the hook thread only adds and
		# discards, so no lock is needed on this per-frame read path
		return int(vk) in self._down

	def stop(self) -> None:
		if self._hwnd:
			try:
				_user32.PostMessageW(self._hwnd, WM_CLOSE, 0, 0)
			except Exception:
				pass


physical_mouse = _PhysicalMouseState()

__all__ = ["physical_mouse"]