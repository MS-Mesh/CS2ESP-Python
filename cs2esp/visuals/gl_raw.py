"""
Raw GL entry points for the per-player chams uniform uploads; PyOpenGL's wrapper converts every argument and re-resolves the function on each call.
Resolving once through ctypes avoids that per-call conversion.
"""

from __future__ import annotations

import ctypes
import os

import numpy as np

_GL_FLOAT = 0x1406
_BATCH_ENABLED = os.environ.get("CS2ESP_NATIVE_CHAMS_BATCH", "1") != "0"
_TRUE = 1  # GL_TRUE: numpy matrices are row-major


class RawUniforms:
	def __init__(self) -> None:
		# Resolve PyOpenGL's active platform only after the renderer creates its context
		from OpenGL.platform import PLATFORM

		lib = PLATFORM.GL
		self._gl_lib = lib
		self._batch = None
		self._batch_tried = False
		self._wgl = lib.wglGetProcAddress
		self._wgl.argtypes = [ctypes.c_char_p]
		self._wgl.restype = ctypes.c_void_p
		proto4fv = ctypes.WINFUNCTYPE(None, ctypes.c_int, ctypes.c_int, ctypes.c_void_p)
		protomat4 = ctypes.WINFUNCTYPE(None, ctypes.c_int, ctypes.c_int, ctypes.c_ubyte, ctypes.c_void_p)
		proto4f = ctypes.WINFUNCTYPE(None, ctypes.c_int, ctypes.c_float, ctypes.c_float, ctypes.c_float, ctypes.c_float)
		proto3f = ctypes.WINFUNCTYPE(None, ctypes.c_int, ctypes.c_float, ctypes.c_float, ctypes.c_float)
		proto1f = ctypes.WINFUNCTYPE(None, ctypes.c_int, ctypes.c_float)
		self.uniform4fv = self._resolve(proto4fv, b"glUniform4fv")
		self.uniform_matrix4fv = self._resolve(protomat4, b"glUniformMatrix4fv")
		self.uniform4f = self._resolve(proto4f, b"glUniform4f")
		self.uniform3f = self._resolve(proto3f, b"glUniform3f")
		self.uniform1f = self._resolve(proto1f, b"glUniform1f")

	def _resolve(self, proto, name: bytes):
		address = self._wgl(name)
		if not address:
			raise OSError(f"{name!r} unavailable")
		return proto(address)

	def _address(self, name):
		address = self._wgl(name)
		if address in (None, 0, 1, 2, 3, ctypes.c_void_p(-1).value):
			address = ctypes.cast(getattr(self._gl_lib, name.decode("ascii")), ctypes.c_void_p).value
		if not address:
			raise OSError(f"{name!r} unavailable")
		return address

	@property
	def supports_batch(self):
		if not _BATCH_ENABLED:
			return False
		if not self._batch_tried:
			self._batch_tried = True
			try:
				# Optional DLL packet support loads on first use; failures keep the ordinary path
				from cs2esp.visuals.native_gl_batch import NativeDrawBatch

				self._batch = NativeDrawBatch(self._address)
			except (OSError, AttributeError, ValueError) as error:
				print(f"[gl] native chams batch unavailable ({error!r}); using ordinary submission")
		return self._batch is not None

	def draw_batch(self, program, locations, jobs):
		return self._batch.submit(program, locations, jobs)

	@staticmethod
	def _ptr(array: np.ndarray) -> int:
		return int(array.ctypes.data)

	def upload_matrices(self, location: int, array: np.ndarray) -> None:
		'''Count = rows/3 (each bone is 3 consecutive vec4 rows).'''
		array = np.ascontiguousarray(array, dtype=np.float32)
		self.uniform4fv(int(location), int(array.size // 4), self._ptr(array))

	def upload_matrix4(self, location: int, matrix: np.ndarray) -> None:
		matrix = np.ascontiguousarray(matrix, dtype=np.float32).reshape(4, 4)
		self.uniform_matrix4fv(int(location), 1, _TRUE, self._ptr(matrix))


def create() -> RawUniforms | None:
	try:
		return RawUniforms()
	except Exception as exc:
		print(f"[gl] raw uniform path unavailable ({exc!r}); using PyOpenGL")
		return None


__all__ = ["RawUniforms", "create"]
