"""
Shared MS Mesh application artwork and native Windows window identity; icon handles stay cached for the process lifetime, including overlay HWND recreation.
"""

import ctypes
import ctypes.wintypes as wintypes
import sys
from pathlib import Path


WM_SETICON = 0x0080
IMAGE_ICON = 1
LR_LOADFROMFILE = 0x0010

APP_ID = 'MSMesh.CS2ESP'
APP_PNG = Path(__file__).resolve().parent / 'ui' / 'qt' / 'assets' / 'app.png'
APP_ICO = APP_PNG.with_suffix('.ico')
_icons = {}


def set_app_identity() -> None:
	'''Use one taskbar identity for the menu and overlay processes.'''
	if sys.platform != 'win32':
		return
	function = ctypes.windll.shell32.SetCurrentProcessExplicitAppUserModelID
	function.argtypes, function.restype = [wintypes.LPCWSTR], ctypes.c_long
	function(APP_ID)


def apply_window_icon(hwnd: int) -> bool:
	'''Assign both native sizes without changing styles or visibility.'''
	if sys.platform != 'win32' or not hwnd:
		return False
	user = ctypes.WinDLL('user32', use_last_error=True)
	user.LoadImageW.argtypes = [wintypes.HINSTANCE, wintypes.LPCWSTR, wintypes.UINT, ctypes.c_int, ctypes.c_int, wintypes.UINT]
	user.LoadImageW.restype = wintypes.HANDLE
	user.SendMessageW.argtypes = [wintypes.HWND, wintypes.UINT, wintypes.WPARAM, wintypes.LPARAM]
	user.SendMessageW.restype = ctypes.c_ssize_t
	for kind, size in ((0, 16), (1, 32)):
		if size not in _icons:
			_icons[size] = user.LoadImageW(None, str(APP_ICO), IMAGE_ICON, size, size, LR_LOADFROMFILE)
		if not _icons[size]:
			return False
		user.SendMessageW(hwnd, WM_SETICON, kind, _icons[size])
	return True
