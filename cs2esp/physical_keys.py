"""
Physical key state that ignores synthetic SendInput events.
GetAsyncKeyState cannot tell a real keypress from an injected one, so a feature that synthesizes its own hotkey corrupts its own gate:
BHop releases SPACE mid-air, the async state reads "up", and the feature shuts itself off after one jump.
A WH_KEYBOARD_LL hook exposes LLKHF_INJECTED, the only reliable split between hardware and SendInput traffic.
It runs a message loop on a dedicated thread whose callback only updates a small VK set.
"""

import ctypes
import ctypes.wintypes as wintypes
import threading

_user32 = ctypes.windll.user32
_kernel32 = ctypes.windll.kernel32

WH_KEYBOARD_LL = 13
WM_QUIT = 0x0012
WM_KEYDOWN = 0x0100
WM_KEYUP = 0x0101
WM_SYSKEYDOWN = 0x0104
WM_SYSKEYUP = 0x0105

# LLKHF_INJECTED: SendInput / keybd_event. LLKHF_LOWER_IL_INJECTED: injected by
# a lower-integrity process; both are synthetic for our purposes
_LLKHF_SYNTHETIC = 0x10 | 0x02

_LRESULT = ctypes.c_ssize_t
_HOOKPROC = ctypes.WINFUNCTYPE(_LRESULT, ctypes.c_int, wintypes.WPARAM, wintypes.LPARAM)


class _KBDLLHOOKSTRUCT(ctypes.Structure):
	_fields_ = (("vkCode", wintypes.DWORD),
		("scanCode", wintypes.DWORD), ("flags", wintypes.DWORD), ("time", wintypes.DWORD), ("dwExtraInfo", ctypes.POINTER(wintypes.ULONG)))


class _PhysicalKeyState:
	"""Lazily-started LL keyboard hook tracking non-injected keydowns."""
	def __init__(self) -> None:
		self._down: set[int] = set()
		self._lock = threading.Lock()
		self._start_lock = threading.Lock()
		self._hook = None
		self._hook_proc = None
		self._thread: threading.Thread | None = None
		self._thread_id: int | None = None
		self._available = False
		self._started = False

	@property
	def available(self) -> bool:
		'''True once the hook is installed and feeding state.'''
		return self._available

	def ensure_started(self) -> None:
		if self._started:
			return
		with self._start_lock:
			if self._started:
				return
			self._started = True
			self._thread = threading.Thread(target=self._run, name="physical-keys", daemon=True)
			self._thread.start()

	def _run(self) -> None:
		try:
			self._hook_proc = _HOOKPROC(self._callback)
			_user32.SetWindowsHookExW.restype = ctypes.c_void_p
			_user32.SetWindowsHookExW.argtypes = [ctypes.c_int, _HOOKPROC, ctypes.c_void_p, wintypes.DWORD]
			_user32.CallNextHookEx.restype = _LRESULT
			_user32.CallNextHookEx.argtypes = [ctypes.c_void_p, ctypes.c_int, wintypes.WPARAM, wintypes.LPARAM]
			_user32.UnhookWindowsHookEx.argtypes = [ctypes.c_void_p]

			# hMod is NULL when the hook procedure lives in this process;
			# GetModuleHandleW is DWORD-truncated without a restype and would
			# hand SetWindowsHookEx a bogus HINSTANCE
			self._hook = _user32.SetWindowsHookExW(WH_KEYBOARD_LL, self._hook_proc, None, 0)
			self._thread_id = _kernel32.GetCurrentThreadId()
			if not self._hook:
				print(f"[keys] LL keyboard hook failed: {ctypes.GetLastError()}")
				return
			self._available = True
			print("[keys] physical key tracker running")

			message = wintypes.MSG()
			while _user32.GetMessageW(ctypes.byref(message), None, 0, 0) > 0:
				_user32.TranslateMessage(ctypes.byref(message))
				_user32.DispatchMessageW(ctypes.byref(message))
		except Exception as error:
			print(f"[keys] physical key tracker died: {error!r}")
		finally:
			self._available = False
			if self._hook:
				try:
					_user32.UnhookWindowsHookEx(self._hook)
				except Exception:
					pass
				self._hook = None

	def _callback(self, ncode, wparam, lparam):
		try:
			if ncode >= 0:
				info = ctypes.cast(lparam, ctypes.POINTER(_KBDLLHOOKSTRUCT)).contents
				if not (info.flags & _LLKHF_SYNTHETIC):
					vk = int(info.vkCode)
					with self._lock:
						if wparam in (WM_KEYDOWN, WM_SYSKEYDOWN):
							self._down.add(vk)
						elif wparam in (WM_KEYUP, WM_SYSKEYUP):
							self._down.discard(vk)
		except Exception:
			pass
		try:
			return _user32.CallNextHookEx(None, ncode, wparam, lparam)
		except Exception:
			return 0

	def is_down(self, vk: int) -> bool:
		# Set membership is atomic under the GIL; the hook thread only adds and
		# discards, so no lock is needed on this per-frame read path
		return vk in self._down

	def stop(self) -> None:
		'''Post WM_QUIT to the hook thread; safe to call at shutdown.'''
		if self._thread_id:
			try:
				_user32.PostThreadMessageW(self._thread_id, WM_QUIT, 0, 0)
			except Exception:
				pass


physical_keys = _PhysicalKeyState()

__all__ = ["physical_keys"]