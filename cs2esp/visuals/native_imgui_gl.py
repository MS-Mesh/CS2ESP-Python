"""
C++ OpenGL renderer with the existing Python GLFW input callbacks.
"""

from imgui_bundle import imgui
from imgui_bundle.python_backends.glfw_backend import GlfwRenderer
import OpenGL.GL as gl


class NativeCompatGlfwRenderer(GlfwRenderer):
	"""Native path that preserves the current HUD's RGB and alpha blending."""

	def _create_device_objects(self):
		if int(gl.glGetIntegerv(gl.GL_CONTEXT_PROFILE_MASK)) & gl.GL_CONTEXT_CORE_PROFILE_BIT:
			raise RuntimeError("Compatibility renderer requires a compatibility GL context")
		self._native_started = bool(imgui.backends.opengl2_init())
		if not self._native_started:
			raise RuntimeError("Native ImGui compatibility renderer initialization failed")

	def process_inputs(self):
		GlfwRenderer.process_inputs(self)
		imgui.backends.opengl2_new_frame()

	def render(self, draw_data):
		imgui.backends.opengl2_render_draw_data(draw_data)

	def shutdown(self):
		if getattr(self, "_native_started", False):
			imgui.backends.opengl2_shutdown()
			self._native_started = False
