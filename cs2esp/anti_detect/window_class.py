"""Masquerade a Qt window under an existing benign Win32 class atom."""

import ctypes
import sys
from ctypes import wintypes


def is_supported() -> bool:
    """Returns True on Windows hosts only."""
    return sys.platform.startswith("win")


# Default masquerade candidates in preference order; first with a live registered atom wins. Notepad ships in System32 everywhere; Calculator and the UWP photo host are common fallbacks.
_DEFAULT_TARGETS = (
    "Notepad",
    "CalcFrame",
    "ApplicationFrameWindow",
)


def masquerade_qt_window(
    qwindow: object,
    *,
    target_class: str | None = None,
) -> str | None:
    """Apply a target class atom and return its name on success."""
    if not is_supported():
        return None

    user32 = ctypes.windll.user32

    try:
        hwnd = int(qwindow.winId())
    except Exception:
        return None
    if not hwnd:
        return None

    class_atom_index = -32
    find_window = user32.FindWindowExW
    find_window.argtypes = [
        wintypes.HWND,
        wintypes.HWND,
        wintypes.LPCWSTR,
        wintypes.LPCWSTR,
    ]
    find_window.restype = wintypes.HWND
    if ctypes.sizeof(ctypes.c_void_p) == 8:
        get_class_long = user32.GetClassLongPtrW
        get_class_long.argtypes = [wintypes.HWND, ctypes.c_int]
        get_class_long.restype = ctypes.c_void_p
    else:
        get_class_long = user32.GetClassLongW
        get_class_long.argtypes = [wintypes.HWND, ctypes.c_int]
        get_class_long.restype = wintypes.DWORD

    candidates = (target_class,) if target_class else _DEFAULT_TARGETS
    chosen_name: str | None = None
    chosen_atom: int = 0
    for name in candidates:
        if not name:
            continue
        target_window = find_window(None, None, name, None)
        raw_atom = (
            get_class_long(target_window, class_atom_index)
            if target_window
            else 0
        )
        atom = int(raw_atom) & 0xFFFF if raw_atom else 0
        if atom:
            chosen_name = name
            chosen_atom = atom
            break
    if chosen_name is None or chosen_atom == 0:
        return None

    if ctypes.sizeof(ctypes.c_void_p) == 8:
        set_class_long = user32.SetClassLongPtrW
        set_class_long.argtypes = [
            wintypes.HWND, ctypes.c_int, ctypes.c_void_p,
        ]
        set_class_long.restype = ctypes.c_void_p
    else:
        set_class_long = user32.SetClassLongW
        set_class_long.argtypes = [
            wintypes.HWND, ctypes.c_int, wintypes.LONG,
        ]
        set_class_long.restype = wintypes.LONG

    try:
        set_class_long(hwnd, class_atom_index, chosen_atom)
    except Exception:
        return None

    buf = ctypes.create_unicode_buffer(256)
    user32.GetClassNameW(hwnd, buf, 256)
    if buf.value == chosen_name:
        return chosen_name
    return None
