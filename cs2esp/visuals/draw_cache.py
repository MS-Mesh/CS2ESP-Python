"""
Reuse unchanged HUD triangles without caching input polling or layout.
Only this backend's 32-bit indices and a single texture/clip command qualify; other draw lists keep their ordinary renderer.
"""

from __future__ import annotations

import ctypes
import os

import numpy as np
from imgui_bundle import imgui


_ENABLED = not os.environ.get("CS2ESP_NO_HUD_GEOMETRY_CACHE")


class DrawGeometryCache:
	"""One bounded, render-thread-owned geometry entry for one widget."""

	def __init__(self):
		self._key = None
		self._vertices = b""
		self._indices = None
		self._texture = None
		self._clip = None
		self.hits = 0
		self.misses = 0

	@staticmethod
	def signature(draw_list, fonts, key):
		'''Include atlas relocation, font lifetime, clipping and tessellation.'''
		if not _ENABLED or fonts is None or imgui.INDEX_SIZE != 4 or not isinstance(draw_list, imgui.ImDrawList):
			return None
		atlas = imgui.get_io().fonts
		texture = atlas.tex_data
		shared = draw_list._data
		# Keep the Fonts object in the key, rather than a recyclable id alone
		return (
			fonts,
			key,
			int(draw_list.flags),
			draw_list._fringe_scale,
			tuple(draw_list._cmd_header.clip_rect),
			shared.curve_tessellation_tol,
			shared.circle_segment_max_error,
			tuple(shared.tex_uv_white_pixel),
			None if texture is None else (texture.unique_id, texture.width, texture.height, texture.get_tex_id()),
		)

	def replay(self, draw_list, signature) -> bool:
		'''Append the cached triangles when every signature component still matches.'''
		if signature is None or self._key is None or self._key[0] is not signature[0] or self._key != signature or self._indices is None:
			self.misses += 1
			return False
		vertex_count = len(self._vertices) // imgui.VERTEX_SIZE
		index_count = self._indices.size
		draw_list.push_clip_rect(self._clip[:2], self._clip[2:], False)
		draw_list.push_texture(self._texture)
		vertex_start = draw_list.vtx_buffer.size()
		index_start = draw_list.idx_buffer.size()
		draw_list.prim_reserve(index_count, vertex_count)
		# PrimReserve may reset the local index for a new VtxOffset command
		index_base = draw_list._vtx_current_idx
		ctypes.memmove(draw_list.vtx_buffer.data_address() + vertex_start * imgui.VERTEX_SIZE, self._vertices, len(self._vertices))
		destination = np.ctypeslib.as_array((ctypes.c_uint32 * index_count).from_address(draw_list.idx_buffer.data_address() + index_start * 4))
		np.add(self._indices, np.uint32(index_base), out=destination)
		draw_list._vtx_current_idx += vertex_count
		# Reset both native write pointers to the new buffer ends; no pointer or NumPy view survives a later reallocation
		del destination
		draw_list.prim_reserve(0, 0)
		draw_list.pop_texture()
		draw_list.pop_clip_rect()
		self.hits += 1
		return True

	@staticmethod
	def start(draw_list, signature):
		'''Record the buffer sizes before the widget draws.'''
		if signature is None:
			return None
		return draw_list.vtx_buffer.size(), draw_list.idx_buffer.size()

	def capture(self, draw_list, signature, start) -> None:
		'''Store the widget's triangles when they form one replayable command.'''
		self._key = None
		self._vertices = b""
		self._indices = None
		self._texture = None
		if signature is None or start is None:
			return
		first_vertex, first_index = start
		vertex_count = draw_list.vtx_buffer.size() - first_vertex
		index_count = draw_list.idx_buffer.size() - first_index
		if vertex_count <= 0 or index_count <= 0:
			return
		# Fonts may allocate/relocate their atlas while drawing a new string
		if self.signature(draw_list, signature[0], signature[1]) != signature:
			return
		commands = [
			command
			for command in draw_list.cmd_buffer
			if command.elem_count and command.idx_offset < first_index + index_count and command.idx_offset + command.elem_count > first_index
		]
		if len(commands) != 1:
			return
		command = commands[0]
		indices = np.ctypeslib.as_array((ctypes.c_uint32 * index_count).from_address(draw_list.idx_buffer.data_address() + first_index * 4)).copy()
		absolute = indices.astype(np.int64) + command.vtx_offset
		if absolute.min() < first_vertex or absolute.max() >= first_vertex + vertex_count:
			return
		self._indices = (absolute - first_vertex).astype(np.uint32)
		self._vertices = ctypes.string_at(draw_list.vtx_buffer.data_address() + first_vertex * imgui.VERTEX_SIZE, vertex_count * imgui.VERTEX_SIZE)
		texture = imgui.get_io().fonts.tex_data
		if texture is None or command.tex_ref.get_tex_id() != texture.get_tex_id():
			self._indices = None
			self._vertices = b""
			return
		# GetTexRef returns an owned value, not a view into the command buffer that ImGui may overwrite
		self._texture = texture.get_tex_ref()
		self._clip = tuple(command.clip_rect)
		self._key = signature
