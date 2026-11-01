"""
Lean replacement for imgui_bundle's Python OpenGL render pass; the stock pass backs up ~17 GL states with synchronous glGet* queries and wraps every draw call.
The overlay owns its context and leaves it in a known state, so this pass skips the backup and sends bind, scissor and draw straight to the driver through ctypes.
"""

from __future__ import annotations

import ctypes

import OpenGL.GL as gl
from imgui_bundle import imgui

_GL_TEXTURE_2D = 0x0DE1
_GL_TRIANGLES = 0x0004
_GL_UNSIGNED_SHORT = 0x1403
_GL_UNSIGNED_INT = 0x1405
_GL_ARRAY_BUFFER = 0x8892
_GL_ELEMENT_ARRAY_BUFFER = 0x8893
_GL_STREAM_DRAW = 0x88E0


class _RawGL:
	def __init__(self) -> None:
		# The renderer has now selected its driver or Mesa platform; preserve that startup order
		from OpenGL.platform import PLATFORM

		lib = PLATFORM.GL
		# GL 1.1 entry points are exported by opengl32 itself
		self.bind_texture = lib.glBindTexture
		self.bind_texture.argtypes = [ctypes.c_uint, ctypes.c_uint]
		self.bind_texture.restype = None
		self.scissor = lib.glScissor
		self.scissor.argtypes = [ctypes.c_int] * 4
		self.scissor.restype = None
		self.draw_elements = lib.glDrawElements
		self.draw_elements.argtypes = [ctypes.c_uint, ctypes.c_int, ctypes.c_uint, ctypes.c_void_p]
		self.draw_elements.restype = None
		# glBufferData is a 1.5 extension pointer: resolve via wglGetProcAddress
		wgl = lib.wglGetProcAddress
		wgl.argtypes = [ctypes.c_char_p]
		wgl.restype = ctypes.c_void_p
		address = wgl(b"glBufferData")
		if not address:
			raise OSError("glBufferData unavailable")
		proto = ctypes.WINFUNCTYPE(None, ctypes.c_uint, ctypes.c_ssize_t, ctypes.c_void_p, ctypes.c_uint)
		self.buffer_data = proto(address)


def install(renderer) -> bool:
	'''Swap ``renderer.render`` for the lean pass. False = keep the stock one.'''
	try:
		raw = _RawGL()
	except Exception as exc:
		print(f"[overlay] fast imgui renderer unavailable ({exc!r}); using stock")
		return False

	index_type = _GL_UNSIGNED_SHORT if imgui.INDEX_SIZE == 2 else _GL_UNSIGNED_INT
	vertex_size = imgui.VERTEX_SIZE
	index_size = imgui.INDEX_SIZE
	bind_texture = raw.bind_texture
	scissor = raw.scissor
	draw_elements = raw.draw_elements
	buffer_data = raw.buffer_data
	ortho = (ctypes.c_float * 16)()
	last_size = [None]

	def render(draw_data) -> None:
		io = renderer.io
		display_width, display_height = io.display_size
		scale_x, scale_y = io.display_framebuffer_scale
		fb_width = int(display_width * scale_x)
		fb_height = int(display_height * scale_y)
		renderer._update_textures()
		if fb_width == 0 or fb_height == 0:
			return
		draw_data.scale_clip_rects(io.display_framebuffer_scale)

		gl.glEnable(gl.GL_BLEND)
		gl.glBlendEquation(gl.GL_FUNC_ADD)
		gl.glBlendFunc(gl.GL_SRC_ALPHA, gl.GL_ONE_MINUS_SRC_ALPHA)
		gl.glDisable(gl.GL_CULL_FACE)
		gl.glDisable(gl.GL_DEPTH_TEST)
		gl.glEnable(gl.GL_SCISSOR_TEST)
		gl.glActiveTexture(gl.GL_TEXTURE0)
		gl.glViewport(0, 0, fb_width, fb_height)

		if last_size[0] != (display_width, display_height):
			last_size[0] = (display_width, display_height)
			ortho[:] = (2.0 / display_width, 0.0, 0.0, 0.0, 0.0, 2.0 / -display_height, 0.0, 0.0, 0.0, 0.0, -1.0, 0.0, -1.0, 1.0, 0.0, 1.0)
		gl.glUseProgram(renderer._shader_handle)
		gl.glUniform1i(renderer._attrib_location_tex, 0)
		gl.glUniformMatrix4fv(renderer._attrib_proj_mtx, 1, gl.GL_FALSE, ortho)
		gl.glBindVertexArray(renderer._vao_handle)
		gl.glBindBuffer(gl.GL_ARRAY_BUFFER, renderer._vbo_handle)
		gl.glBindBuffer(gl.GL_ELEMENT_ARRAY_BUFFER, renderer._elements_handle)

		bound_texture = -1
		for commands in draw_data.cmd_lists:
			buffer_data(_GL_ARRAY_BUFFER, commands.vtx_buffer.size() * vertex_size, commands.vtx_buffer.data_address(), _GL_STREAM_DRAW)
			buffer_data(_GL_ELEMENT_ARRAY_BUFFER, commands.idx_buffer.size() * index_size, commands.idx_buffer.data_address(), _GL_STREAM_DRAW)
			for command in commands.cmd_buffer:
				texture = command.tex_ref.get_tex_id()
				if texture != bound_texture:
					bind_texture(_GL_TEXTURE_2D, texture)
					bound_texture = texture
				x, y, z, w = command.clip_rect
				scissor(int(x), int(fb_height - w), int(z - x), int(w - y))
				draw_elements(_GL_TRIANGLES, command.elem_count, index_type, command.idx_offset * index_size)

		# Known baseline for the next pass (chams / next frame)
		gl.glDisable(gl.GL_SCISSOR_TEST)
		gl.glBindVertexArray(0)
		gl.glUseProgram(0)

	renderer.render = render
	return True


__all__ = ["install"]
