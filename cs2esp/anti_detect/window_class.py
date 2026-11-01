"""
Host the Qt menu inside an app-owned native window registered under the selected class name; only the top-level host uses that alias.
Embedded Qt child windows keep their real Qt classes.
"""

import ctypes
import ctypes.wintypes as wintypes
import sys


_TARGET_CLASSES = {"Explorer": "CabinetWClass"}
_QT_HOST_TYPE = None
_WM_NCCALCSIZE = 0x0083
_WM_SIZE = 0x0005
_WM_GETMINMAXINFO = 0x0024
_WM_DPICHANGED = 0x02E0
_WM_SETFOCUS = 0x0007
_WM_ACTIVATE = 0x0006
_WM_CLOSE = 0x0010
_LOWORD_MASK = 0xFFFF
_HOST_WINDOW_STYLE = 0x820F0000  # WS_POPUP | WS_CLIPCHILDREN | WS_SYSMENU | WS_THICKFRAME | WS_MINIMIZEBOX | WS_MAXIMIZEBOX
_SWP_NOZORDER_NOACTIVATE = 0x0014


def is_supported() -> bool:
	'''Return whether native Windows hosting is available on this platform.'''
	return sys.platform.startswith("win")


def _set_status(window, requested: str, applied: str | None = None, *, handle: int = 0, child_class: str | None = None, error: str | None = None) -> None:
	'''Publish the host result on the Qt window and its dynamic properties.'''
	try:
		window.window_class_status = {"requested_class": requested, "applied_class": applied, "host_hwnd": handle, "child_class": child_class, "error": error}
	except (AttributeError, RuntimeError):
		return
	for name, value in (("windowClassRequested", requested), ("windowClassApplied", applied or ""), ("windowClassHost", handle)):
		if callable(getattr(window, "setProperty", None)):
			window.setProperty(name, value)


class _NativeApi:
	"""Keep pointer-sized Win32 signatures and class ownership together."""
	def __init__(self) -> None:
		self.user = ctypes.WinDLL("user32", use_last_error=True)
		kernel = ctypes.WinDLL("kernel32", use_last_error=True)
		self.callback_type = ctypes.WINFUNCTYPE(ctypes.c_ssize_t, wintypes.HWND, wintypes.UINT, ctypes.c_size_t, ctypes.c_ssize_t)
		callback_type = self.callback_type

		class WindowClass(ctypes.Structure):
			_fields_ = [("size", wintypes.UINT), ("style", wintypes.UINT), ("callback", callback_type), ("class_extra", ctypes.c_int),
				("window_extra", ctypes.c_int), ("instance", wintypes.HINSTANCE), ("icon", wintypes.HICON), ("cursor", wintypes.HANDLE),
				("background", wintypes.HBRUSH), ("menu_name", wintypes.LPCWSTR), ("class_name", wintypes.LPCWSTR), ("small_icon", wintypes.HICON)]

		class MinMaxInfo(ctypes.Structure):
			_fields_ = [("reserved", wintypes.POINT), ("max_size", wintypes.POINT), ("max_position", wintypes.POINT),
				("min_track", wintypes.POINT), ("max_track", wintypes.POINT)]

		class MonitorInfo(ctypes.Structure):
			_fields_ = [("size", wintypes.DWORD), ("monitor", wintypes.RECT), ("work", wintypes.RECT), ("flags", wintypes.DWORD)]

		self.window_class_type, self.minmax_type, self.monitor_info_type = WindowClass, MinMaxInfo, MonitorInfo
		signatures = {
			"RegisterClassExW": (wintypes.WORD, [ctypes.POINTER(WindowClass)]),
			"UnregisterClassW": (wintypes.BOOL, [wintypes.LPCWSTR, wintypes.HINSTANCE]),
			"CreateWindowExW": (wintypes.HWND, [wintypes.DWORD, wintypes.LPCWSTR, wintypes.LPCWSTR, wintypes.DWORD, ctypes.c_int, ctypes.c_int,
				ctypes.c_int, ctypes.c_int, wintypes.HWND, wintypes.HMENU, wintypes.HINSTANCE, ctypes.c_void_p]),
			"DefWindowProcW": (ctypes.c_ssize_t, [wintypes.HWND, wintypes.UINT, ctypes.c_size_t, ctypes.c_ssize_t]),
			"GetClassNameW": (ctypes.c_int, [wintypes.HWND, wintypes.LPWSTR, ctypes.c_int]),
			"GetWindowRect": (wintypes.BOOL, [wintypes.HWND, ctypes.POINTER(wintypes.RECT)]),
			"GetClientRect": (wintypes.BOOL, [wintypes.HWND, ctypes.POINTER(wintypes.RECT)]),
			"GetPhysicalCursorPos": (wintypes.BOOL, [ctypes.POINTER(wintypes.POINT)]),
			"SetWindowPos": (wintypes.BOOL, [wintypes.HWND, wintypes.HWND, ctypes.c_int, ctypes.c_int, ctypes.c_int, ctypes.c_int, wintypes.UINT]),
			"ShowWindow": (wintypes.BOOL, [wintypes.HWND, ctypes.c_int]),
			"DestroyWindow": (wintypes.BOOL, [wintypes.HWND]),
			"IsWindow": (wintypes.BOOL, [wintypes.HWND]),
			"IsIconic": (wintypes.BOOL, [wintypes.HWND]),
			"IsZoomed": (wintypes.BOOL, [wintypes.HWND]),
			"SetFocus": (wintypes.HWND, [wintypes.HWND]),
			"GetDpiForWindow": (wintypes.UINT, [wintypes.HWND]),
			"MonitorFromWindow": (wintypes.HANDLE, [wintypes.HWND, wintypes.DWORD]),
			"GetMonitorInfoW": (wintypes.BOOL, [wintypes.HANDLE, ctypes.POINTER(MonitorInfo)]),
		}
		for name, (result_type, argument_types) in signatures.items():
			function = getattr(self.user, name)
			function.restype, function.argtypes = result_type, argument_types
		kernel.GetModuleHandleW.argtypes, kernel.GetModuleHandleW.restype = [wintypes.LPCWSTR], wintypes.HMODULE
		self.instance = kernel.GetModuleHandleW(None)

	def register(self, name: str, callback) -> None:
		window_class = self.window_class_type()
		window_class.size = ctypes.sizeof(window_class)
		window_class.callback, window_class.instance, window_class.class_name = callback, self.instance, name
		if not self.user.RegisterClassExW(ctypes.byref(window_class)):
			raise ctypes.WinError(ctypes.get_last_error())

	def class_name(self, handle: int) -> str | None:
		buffer = ctypes.create_unicode_buffer(256)
		return buffer.value if self.user.GetClassNameW(handle, buffer, len(buffer)) else None

	def rect(self, handle: int, *, client: bool = False) -> tuple[int, int, int, int]:
		bounds = wintypes.RECT()
		if not (self.user.GetClientRect if client else self.user.GetWindowRect)(handle, ctypes.byref(bounds)):
			raise ctypes.WinError(ctypes.get_last_error())
		return bounds.left, bounds.top, bounds.right - bounds.left, bounds.bottom - bounds.top

	def place(self, handle: int, bounds: tuple[int, int, int, int]) -> None:
		if not self.user.SetWindowPos(handle, None, *bounds, _SWP_NOZORDER_NOACTIVATE):
			raise ctypes.WinError(ctypes.get_last_error())

	def cursor_position(self) -> tuple[int, int]:
		point = wintypes.POINT()
		if not self.user.GetPhysicalCursorPos(ctypes.byref(point)):
			raise ctypes.WinError(ctypes.get_last_error())
		return point.x, point.y


def _foreign_window(handle: int):
	'''Load Qt only for the optional standalone menu host.'''
	from PySide6.QtGui import QWindow
	return QWindow.fromWinId(handle)


def _qt_host_type():
	'''Define the host class on first use so Qt imports stay inside the standalone menu path.'''
	global _QT_HOST_TYPE
	if _QT_HOST_TYPE is not None:
		return _QT_HOST_TYPE
	from PySide6.QtCore import QEvent, QObject, Qt
	from PySide6.QtWidgets import QApplication

	class NativeWindowHost(QObject):
		"""Own a native top-level, its foreign Qt wrapper, and the embedded menu's lifetime."""
		def __init__(self, window, alias: str) -> None:
			super().__init__(window)
			self.window, self.alias = window, alias
			self.api = _NativeApi()
			self.handle, self.child = 0, int(window.winId())
			self.foreign, self.pointer = None, None
			self.registered, self.closed, self.connected = False, False, False
			self.initial_geometry, self.was_visible = window.geometry(), window.isVisible()
			self.callback = self.api.callback_type(self._window_proc)
			window._window_class_host = self
			try:
				bounds = self.api.rect(self.child)
				window.hide()
				self.api.register(alias, self.callback)
				self.registered = True
				self.handle = int(self.api.user.CreateWindowExW(0, alias, window.windowTitle(), _HOST_WINDOW_STYLE, *bounds, None, None, self.api.instance, None) or 0)
				if not self.handle:
					raise ctypes.WinError(ctypes.get_last_error())
				self.foreign = _foreign_window(self.handle)
				if self.foreign is None:
					raise RuntimeError("Qt foreign-window embedding is unavailable")
				window.windowHandle().setParent(self.foreign)
				self._resize_child()
				window.destroyed.connect(self._window_destroyed)
				self.connected = True
				QApplication.instance().installEventFilter(self)
				if self.was_visible:
					window.show()
			except Exception:
				self.close(restore=True)
				raise

		def _window_proc(self, handle, message, first, second):
			try:
				if message == _WM_NCCALCSIZE:
					return 0
				if message == _WM_SIZE and self.handle:
					self._resize_child()
				elif message == _WM_GETMINMAXINFO:
					limits = ctypes.cast(second, ctypes.POINTER(self.api.minmax_type)).contents
					scale = self.scale()
					limits.min_track.x, limits.min_track.y = round(self.window.minimumWidth() * scale), round(self.window.minimumHeight() * scale)
					monitor = self.api.monitor_info_type()
					monitor.size = ctypes.sizeof(monitor)
					if self.api.user.GetMonitorInfoW(self.api.user.MonitorFromWindow(handle, 2), ctypes.byref(monitor)):
						limits.max_position.x, limits.max_position.y = monitor.work.left - monitor.monitor.left, monitor.work.top - monitor.monitor.top
						limits.max_size.x, limits.max_size.y = monitor.work.right - monitor.work.left, monitor.work.bottom - monitor.work.top
					return 0
				elif message == _WM_DPICHANGED:
					bounds = ctypes.cast(second, ctypes.POINTER(wintypes.RECT)).contents
					native_bounds = (bounds.left, bounds.top, bounds.right - bounds.left, bounds.bottom - bounds.top)
					self.api.place(handle, native_bounds)
					self._resize_child()
					if self.pointer is not None:
						try:
							self.pointer = (self.api.cursor_position(), native_bounds, self.pointer[2])
						except OSError:
							self.pointer = None
					return 0
				elif message == _WM_SETFOCUS and self.child:
					self.api.user.SetFocus(self.child)
				elif message == _WM_ACTIVATE:
					QApplication.setActiveWindow(self.window if first & _LOWORD_MASK else None)
				elif message == _WM_CLOSE:
					self.window.close()
					return 0
			except Exception as error:
				print(f'[menu-host] native message {message:#x} failed: {error!r}')
			return self.api.user.DefWindowProcW(handle, message, first, second)

		def scale(self) -> float:
			return (self.api.user.GetDpiForWindow(self.handle) or 96) / 96 if self.handle else self.window.devicePixelRatioF()

		def _resize_child(self) -> None:
			if self.child and self.handle:
				_left, _top, width, height = self.api.rect(self.handle, client=True)
				self.api.place(self.child, (0, 0, width, height))

		def show(self) -> None:
			if self.handle and not self.closed:
				self.api.user.ShowWindow(self.handle, 4)

		def is_maximized(self) -> bool:
			return bool(self.api.user.IsZoomed(self.handle))

		def is_minimized(self) -> bool:
			return bool(self.api.user.IsIconic(self.handle))

		def current_class(self) -> str | None:
			return self.api.class_name(self.handle) if self.handle and not self.closed else None

		def window_action(self, role: str) -> None:
			if role == "min":
				self.api.user.ShowWindow(self.handle, 6)
			elif role == "max":
				self.api.user.ShowWindow(self.handle, 9 if self.is_maximized() else 3)
			elif role == "close":
				self.window.close()

		def pointer_press(self, event) -> bool:
			if event.button() != Qt.LeftButton:
				return False
			edges = self.window._hit_test_edge(event.position().toPoint())
			if not edges and not self.window._resize_margin < event.position().y() < self.window._titlebar.height():
				return False
			if self.is_maximized():
				return False
			try:
				self.pointer = (self.api.cursor_position(), self.api.rect(self.handle), edges)
			except OSError:
				self.pointer = None
				return False
			event.accept()
			return True

		def pointer_move(self, event) -> bool:
			if self.pointer is None or not event.buttons() & Qt.LeftButton:
				self.pointer = None
				return False
			origin, bounds, edges = self.pointer
			try:
				cursor_x, cursor_y = self.api.cursor_position()
			except OSError:
				self.pointer = None
				return False
			# Native window bounds and drag deltas must stay physical across mixed-DPI screens
			delta_x, delta_y = cursor_x - origin[0], cursor_y - origin[1]
			scale = self.scale()
			left, top, width, height = bounds
			if not edges:
				left, top = left + delta_x, top + delta_y
			else:
				if edges & Qt.LeftEdge:
					left, width = left + delta_x, width - delta_x
				if edges & Qt.RightEdge:
					width += delta_x
				if edges & Qt.TopEdge:
					top, height = top + delta_y, height - delta_y
				if edges & Qt.BottomEdge:
					height += delta_y
				minimum_width, minimum_height = round(self.window.minimumWidth() * scale), round(self.window.minimumHeight() * scale)
				if width < minimum_width:
					left, width = bounds[0] + bounds[2] - minimum_width if edges & Qt.LeftEdge else left, minimum_width
				if height < minimum_height:
					top, height = bounds[1] + bounds[3] - minimum_height if edges & Qt.TopEdge else top, minimum_height
			self.api.place(self.handle, (left, top, width, height))
			event.accept()
			return True

		def logical_size(self) -> tuple[int, int]:
			_left, _top, width, height = self.api.rect(self.handle, client=True)
			scale = self.scale()
			return round(width / scale), round(height / scale)

		def _window_destroyed(self, _object=None) -> None:
			self.close()

		def eventFilter(self, watched, event) -> bool:  # noqa: N802
			if watched is QApplication.instance() and event.type() == QEvent.Quit:
				self.close(quit_app=True)
				return True
			return super().eventFilter(watched, event)

		def close(self, *, restore: bool = False, quit_app: bool = False) -> None:
			if self.closed:
				return
			self.closed, self.pointer = True, None
			if QApplication.instance() is not None:
				QApplication.instance().removeEventFilter(self)
			try:
				if self.connected:
					self.window.destroyed.disconnect(self._window_destroyed)
					self.connected = False
				self.window.hide()
				if self.foreign is not None and self.window.windowHandle() is not None:
					self.window.windowHandle().setParent(None)
			except RuntimeError:
				pass
			if self.handle:
				self.child = 0
				self.api.user.DestroyWindow(self.handle)
				self.handle = 0
			if self.foreign is not None:
				self.foreign.deleteLater()
				self.foreign = None
			if self.registered:
				self.api.user.UnregisterClassW(self.alias, self.api.instance)
				self.registered = False
			self.callback = None
			try:
				self.window._window_class_host = None
				status = getattr(self.window, "window_class_status", {})
				_set_status(self.window, status.get("requested_class", self.alias), error="Host closed")
				if restore:
					self.window.setGeometry(self.initial_geometry)
					self.window.setVisible(self.was_visible)
			except RuntimeError:
				pass
			if quit_app and QApplication.instance() is not None:
				QApplication.instance().exit(0)

	_QT_HOST_TYPE = NativeWindowHost
	return NativeWindowHost


def masquerade_qt_window(qwindow: object, *, target_class: str | None = None) -> str | None:
	"""Return the native host's verified class, or None after restoring the unhosted Qt window on failure."""
	requested = target_class or "Notepad"
	alias = _TARGET_CLASSES.get(requested, requested) if isinstance(requested, str) else None
	_set_status(qwindow, requested)
	if not is_supported() or not isinstance(alias, str) or not alias or len(alias) > 255 or "\0" in alias:
		_set_status(qwindow, requested, error="Unsupported platform or invalid class name")
		return None
	previous = getattr(qwindow, "_window_class_host", None)
	if previous is not None and not previous.closed:
		if previous.alias == alias and previous.api.class_name(previous.handle) == alias:
			_set_status(qwindow, requested, alias, handle=previous.handle, child_class=previous.api.class_name(previous.child))
			return alias
		previous.close(restore=True)
	host = None
	try:
		host = _qt_host_type()(qwindow, alias)
		actual = host.api.class_name(host.handle)
		if actual != alias:
			raise RuntimeError(f"Native class readback mismatch: {actual!r}")
		_set_status(qwindow, requested, actual, handle=host.handle, child_class=host.api.class_name(host.child))
		return actual
	except Exception as error:
		if host is not None:
			host.close(restore=True)
		_set_status(qwindow, requested, error=str(error))
		return None
