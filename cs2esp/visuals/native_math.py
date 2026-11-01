"""
Optional native (OpenMP) math for the per-frame hot paths; callers fall back to numpy/Python when overlayperf.dll is unavailable or CS2ESP_NO_NATIVE_MATH is set.
Build with ``cs2esp/native/overlayperf/build.bat``.
"""

from __future__ import annotations

import ctypes
import os
from pathlib import Path

import numpy as np

_DLL_PATH = Path(__file__).resolve().parents[1] / "assets" / "bin" / "overlayperf.dll"
_lib = None
if os.environ.get("CS2ESP_NO_NATIVE_MATH"):
	_lib = None
else:
	try:
		_lib = ctypes.CDLL(str(_DLL_PATH))
		_lib.op_quat_world.argtypes = [ctypes.c_void_p, ctypes.c_int, ctypes.c_int, ctypes.c_void_p]
		_lib.op_quat_world.restype = None
		_lib.op_project.argtypes = [ctypes.c_void_p, ctypes.c_int, ctypes.c_void_p, ctypes.c_float, ctypes.c_float, ctypes.c_void_p]
		_lib.op_project.restype = None
		_lib.op_skin.argtypes = [ctypes.c_void_p, ctypes.c_void_p, ctypes.c_int, ctypes.c_int, ctypes.c_void_p]
		_lib.op_skin.restype = None
	except OSError:
		_lib = None


def available() -> bool:
	return _lib is not None


if _lib is not None:
	print("[perf] native math active (parallelism depends on batch size and DLL build)")


def _ptr(array: np.ndarray) -> int:
	return int(array.ctypes.data)


def quat_world(raw: np.ndarray) -> np.ndarray:
	'''(P, N, 8) slabs -> (P, N, 3, 4) world matrices.'''
	raw = np.ascontiguousarray(raw, dtype=np.float32)
	players, bones = raw.shape[0], raw.shape[1]
	matrices = np.empty((players, bones, 3, 4), dtype=np.float32)
	_lib.op_quat_world(_ptr(raw), players, bones, _ptr(matrices))
	return matrices


def project(points: np.ndarray, view_matrix, screen_w: float, screen_h: float) -> np.ndarray:
	'''(N, 3) world points -> (N, 2) screen xy; NaN rows are behind the camera.'''
	world_points = np.ascontiguousarray(points, dtype=np.float32).reshape(-1, 3)
	view = np.ascontiguousarray(view_matrix, dtype=np.float32).reshape(16)
	screen_points = np.empty((world_points.shape[0], 2), dtype=np.float32)
	_lib.op_project(_ptr(world_points), world_points.shape[0], _ptr(view), float(screen_w) * 0.5, float(screen_h) * 0.5, _ptr(screen_points))
	return screen_points


def skin(world: np.ndarray, inv_bind: np.ndarray) -> np.ndarray:
	'''(P, B, 3, 4) world x (B, 3, 4) inverse bind -> (P, B, 3, 4) skin.'''
	world = np.ascontiguousarray(world, dtype=np.float32)
	inverse_bind = np.ascontiguousarray(inv_bind, dtype=np.float32)
	players, bones = world.shape[0], world.shape[1]
	palettes = np.empty_like(world)
	_lib.op_skin(_ptr(world), _ptr(inverse_bind), players, bones, _ptr(palettes))
	return palettes


__all__ = ["available", "quat_world", "project", "skin"]
