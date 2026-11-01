"""
One native submission for an exact-model chams pass; overlayperf.dll replays the same uniform, VAO and draw calls the Python loop would issue.
"""

from __future__ import annotations

import ctypes
from pathlib import Path

import numpy as np

# Packet flag bits consumed by op_chams_draw_batch in overlayperf.dll
_FLAG_VIEW = 1
_FLAG_INVERSE = 2
_FLAG_COLOUR = 4
_FLAG_CAMERA = 8
_FLAG_RIM = 16
_FLAG_VAO = 32


class _Api(ctypes.Structure):
	"""Function-pointer table passed to op_chams_draw_batch; field order is the ABI."""
	_fields_ = [
		(name, ctypes.c_void_p)
		for name in (
			"matrix4",
			"vectors4",
			"vector4",
			"vector3",
			"scalar1",
			"bind_vao",
			"draw",
			"program",
			"enable",
			"blend",
			"depth_mask",
			"depth_func",
			"disable",
			"clear",
		)
	]


class _Draw(ctypes.Structure):
	"""One op_chams_draw_batch packet; field order and types are the ABI."""
	_fields_ = [
		("view", ctypes.c_void_p),
		("bones", ctypes.c_void_p),
		("inverse", ctypes.c_void_p),
		("vao", ctypes.c_uint32),
		("index_count", ctypes.c_int32),
		("flags", ctypes.c_uint32),
		("colour", ctypes.c_float * 4),
		("camera", ctypes.c_float * 3),
		("rim", ctypes.c_float),
	]


class NativeDrawBatch:
	"""ctypes wrapper around the DLL batch export and its layout check."""
	def __init__(self, resolve) -> None:
		path = Path(__file__).resolve().parents[1] / "assets/bin/overlayperf.dll"
		self._dll = ctypes.CDLL(str(path))
		self._dll.op_chams_draw_size.restype = ctypes.c_int
		self._dll.op_chams_api_size.restype = ctypes.c_int
		if self._dll.op_chams_draw_size() != ctypes.sizeof(_Draw) or self._dll.op_chams_api_size() != ctypes.sizeof(_Api):
			raise OSError("Native chams packet layout does not match")
		self._api = _Api(
			*(
				resolve(name)
				for name in (
					b"glUniformMatrix4fv",
					b"glUniform4fv",
					b"glUniform4f",
					b"glUniform3f",
					b"glUniform1f",
					b"glBindVertexArray",
					b"glDrawElements",
					b"glUseProgram",
					b"glEnable",
					b"glBlendFunc",
					b"glDepthMask",
					b"glDepthFunc",
					b"glDisable",
					b"glClear",
				)
			)
		)
		self._submit = self._dll.op_chams_draw_batch
		self._submit.argtypes = [ctypes.POINTER(_Api), ctypes.c_uint32, ctypes.POINTER(ctypes.c_int), ctypes.POINTER(_Draw), ctypes.c_int]
		self._submit.restype = ctypes.c_int

	def submit(self, program, locations, jobs) -> bool:
		'''Fill one _Draw per job and submit; False for empty jobs or a rejected batch.'''
		if not jobs:
			return False
		packets = (_Draw * len(jobs))()
		keepalive = []
		active_view = None
		active_colour = active_camera = active_rim = active_vao = None
		for packet, (view, bones, inverse, vao, count, colour, camera, rim) in zip(packets, jobs):
			flags = 0
			if view is not active_view:
				matrix = np.ascontiguousarray(view, dtype=np.float32).reshape(4, 4)
				keepalive.append(matrix)
				packet.view = int(matrix.ctypes.data)
				active_view = view
				flags |= _FLAG_VIEW
			if inverse is not None:
				inverse = np.ascontiguousarray(inverse, dtype=np.float32).reshape(128, 3, 4)
				keepalive.append(inverse)
				packet.inverse = int(inverse.ctypes.data)
				flags |= _FLAG_INVERSE
			bones = np.ascontiguousarray(bones, dtype=np.float32).reshape(128, 3, 4)
			keepalive.append(bones)
			packet.bones = int(bones.ctypes.data)
			packet.vao, packet.index_count = int(vao), int(count)
			if colour != active_colour:
				packet.colour[:] = colour
				active_colour = colour
				flags |= _FLAG_COLOUR
			if camera != active_camera:
				packet.camera[:] = camera
				active_camera = camera
				flags |= _FLAG_CAMERA
			if rim != active_rim:
				packet.rim = rim
				active_rim = rim
				flags |= _FLAG_RIM
			if vao != active_vao:
				active_vao = vao
				flags |= _FLAG_VAO
			packet.flags = flags
		uniform_locations = (ctypes.c_int * 6)(*(locations[name] for name in ("u_view_proj", "u_bones", "u_inv_bind", "u_color", "u_cam", "u_rim")))
		drawn = self._submit(ctypes.byref(self._api), int(program), uniform_locations, packets, len(jobs))
		if drawn < 0:
			return False
		if drawn != len(jobs):
			raise RuntimeError(f"Native chams submitted {drawn}/{len(jobs)} players")
		return True
