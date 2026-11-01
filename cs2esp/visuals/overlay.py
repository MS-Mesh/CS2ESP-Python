"""
Transparent click-through GLFW/imgui overlay that follows CS2's client rect; GLFW per-pixel alpha is the primary path and the colorkey window is the DWM fallback.
"""

import os
from ctypes import WinDLL, c_long
from random import choice
from time import perf_counter

import glfw
import OpenGL.GL as gl
from imgui_bundle import imgui
from imgui_bundle.python_backends.glfw_backend import GlfwRenderer

from cs2esp.app_identity import apply_window_icon, set_app_identity
from cs2esp.config import CONFIG_DIR
from cs2esp.display_info import monitor_for_hwnd, pick_overlay_monitor, rects_differ
from cs2esp.ui.fonts import forget_fonts_for_current_context, load_fonts_for_current_context
from cs2esp.win32_utils import (GWL_EXSTYLE, WS_EX_NOACTIVATE, WS_EX_TOOLWINDOW, WS_EX_TOPMOST, apply_taskbar_invisibility,
	extend_frame_into_client, get_client_rect, make_overlay_clickthrough, set_capture_protect, set_colorkey_transparency, set_noactivate, set_topmost, user32)


def _target_rect(target_hwnd: int) -> tuple[int, int, int, int]:
	'''Use CS2's projection-space client rect, falling back to its monitor.'''
	if target_hwnd:
		client = get_client_rect(target_hwnd)
		if client is not None:
			return client
	return pick_overlay_monitor(target_hwnd)


_DECOY_TITLES = ("Profile Manager", "Cache Cleaner", "Update Assistant", "Workspace Helper", "Sync Daemon", "Theme Studio", "Volume Mixer Plus")
_SWP_NOSIZE = 0x0001
_SWP_NOMOVE = 0x0002
_SWP_NOZORDER = 0x0004
_SWP_NOACTIVATE = 0x0010
_SWP_FRAMECHANGED = 0x0020
_dwm_flush = WinDLL("dwmapi").DwmFlush
_dwm_flush.argtypes, _dwm_flush.restype = [], c_long


class Overlay:
	def __init__(self, width: int = 0, height: int = 0, title: str | None = None, randomize_title: bool = False, hide_from_taskbar: bool = True):
		self.width = width
		self.height = height
		self.title = title or (choice(_DECOY_TITLES) if randomize_title else "CS2 Master Overlay")
		self.hide_from_taskbar = hide_from_taskbar
		self.window = None
		self.impl = None
		self.hwnd = None
		self.ctx = None
		self._running = False
		self._click_through = True
		self._colorkey_fallback = False
		self._capture_protected = False
		self._capture_protect_applied: bool | None = None
		self._taskbar_hidden_applied: bool | None = None
		self._vsync = False
		self._frame_fence = None
		self._dwm_pacing = os.environ.get("CS2ESP_DWM_PACING", "1") != "0"
		self.dwm_wait_s = 0.0
		self.fence_wait_counts = {"signaled": 0, "timeout": 0, "failed": 0, "unknown": 0, "error": 0}
		self.fence_wait_s = 0.0
		self.swap_start_at = None
		self.swap_return_at = None
		self.swap_s = 0.0
		self._trace_frame_times = bool(os.environ.get("CS2ESP_FRAME_STATS") or os.environ.get("CS2ESP_FRAME_TRACE") or os.environ.get("CS2ESP_PIPELINE_TRACE"))
		self.imgui_commands_submitted_at = None
		self.flush_returned_at = None
		self._target_hwnd: int = 0
		self._monitor_rect: tuple[int, int, int, int] = (0, 0, 0, 0)
		self._monitor_check_frame = 0
		self._monitor_checked_at = 0.0
		self._display_handle = 0
		self._target_sync_pending = False
		self._occlusion_sig = None
		self._dump_enabled = bool(os.environ.get("CS2_DUMP_FRAME"))

	def create(self, target_hwnd: int = 0, colorkey_fallback: bool = False, vsync: bool = False) -> bool:
		'''Create the transparent overlay over CS2's client area / monitor.'''
		if not glfw.init():
			print("[overlay] Failed to init GLFW")
			return False

		self._colorkey_fallback = bool(colorkey_fallback)
		self._vsync = bool(vsync)
		self._target_hwnd = int(target_hwnd or 0)
		monitor = monitor_for_hwnd(self._target_hwnd)
		self._display_handle = monitor.handle if monitor is not None else 0
		rect_x, rect_y, rect_w, rect_h = _target_rect(target_hwnd)
		self._monitor_rect = (rect_x, rect_y, rect_w, rect_h)
		if self.width <= 0:
			self.width = rect_w
		if self.height <= 0:
			self.height = rect_h

		if not self._build_window((rect_x, rect_y, self.width, self.height)):
			glfw.terminate()
			return False

		if colorkey_fallback:
			ok = set_colorkey_transparency(self.hwnd, 0, 0, 0)
			print(f"[overlay] colorkey fallback: {'OK' if ok else 'FAILED'}")
		else:
			print("[overlay] GLFW-native per-pixel-alpha compositing")

		self._running = True
		print(f"[overlay] Created {self.width}x{self.height} overlay (title={self.title!r})")
		return True

	def _build_window(self, rect: tuple[int, int, int, int]) -> bool:
		'''Build the GLFW window and its bound GL/imgui context at ``rect``.'''
		# Window styles and clipping must be reset for each replacement HWND
		self._ct_applied = False
		self._occlusion_sig = None
		self.hwnd = None
		self._capture_protect_applied = None
		self._taskbar_hidden_applied = None
		left, top, width, height = rect
		glfw.window_hint(glfw.FLOATING, glfw.TRUE)
		glfw.window_hint(glfw.TRANSPARENT_FRAMEBUFFER, glfw.TRUE)
		glfw.window_hint(glfw.DECORATED, glfw.FALSE)
		if not self._colorkey_fallback:
			glfw.window_hint(glfw.MOUSE_PASSTHROUGH, glfw.TRUE)
		glfw.window_hint(glfw.FOCUS_ON_SHOW, glfw.FALSE)
		glfw.window_hint(glfw.VISIBLE, glfw.FALSE)
		position_hints = hasattr(glfw, "POSITION_X") and hasattr(glfw, "POSITION_Y") and glfw.get_version() >= (3, 4, 0)
		if position_hints:
			glfw.window_hint(glfw.POSITION_X, left)
			glfw.window_hint(glfw.POSITION_Y, top)
		try:
			from cs2esp import gpu_devices
			software_gl = (gpu_devices.ACTIVE_DEVICE is not None and gpu_devices.ACTIVE_DEVICE.kind == "cpu")
		except Exception:
			software_gl = False
		if software_gl:
			glfw.window_hint(glfw.CONTEXT_VERSION_MAJOR, 3)
			glfw.window_hint(glfw.CONTEXT_VERSION_MINOR, 3)
			glfw.window_hint(glfw.OPENGL_PROFILE, glfw.OPENGL_CORE_PROFILE)
			glfw.window_hint(glfw.OPENGL_FORWARD_COMPAT, glfw.TRUE)

		set_app_identity()
		try:
			self.window = glfw.create_window(width, height, self.title, None, None)
		finally:
			if position_hints:
				glfw.window_hint(glfw.POSITION_X, glfw.ANY_POSITION)
				glfw.window_hint(glfw.POSITION_Y, glfw.ANY_POSITION)
		if not self.window:
			print("[overlay] Failed to create window")
			return False

		glfw.set_window_pos(self.window, left, top)
		glfw.make_context_current(self.window)
		glfw.swap_interval(1 if self._vsync else 0)

		self.hwnd = glfw.get_win32_window(self.window)
		apply_window_icon(self.hwnd)
		style = user32.GetWindowLongW(self.hwnd, GWL_EXSTYLE)
		# Per-pixel alpha must not use WS_EX_LAYERED; only the colorkey path adds it
		style |= WS_EX_TOPMOST | WS_EX_NOACTIVATE
		if self.hide_from_taskbar:
			style |= WS_EX_TOOLWINDOW
		user32.SetWindowLongW(self.hwnd, GWL_EXSTYLE, style)
		self._taskbar_hidden_applied = bool(user32.GetWindowLongW(self.hwnd, GWL_EXSTYLE) & WS_EX_TOOLWINDOW)
		set_topmost(self.hwnd)
		extend_frame_into_client(self.hwnd)
		apply_taskbar_invisibility(self.hwnd)
		self.set_click_through(self._click_through)
		if self._capture_protected:
			self.set_capture_protect(True)

		self.ctx = imgui.create_context()
		try:
			imgui.get_io().set_ini_filename(str(CONFIG_DIR / "imgui.ini"))
		except Exception as exc:
			print(f"[overlay] imgui.ini redirect failed: {exc!r}")

		try:
			load_fonts_for_current_context(ctx=self.ctx)
		except Exception as exc:
			print(f"[overlay] font load failed: {exc!r}")

		native_mode = os.environ.get("CS2ESP_NATIVE_IMGUI_GL", "0" if os.environ.get("CS2ESP_STOCK_IMGUI_GL") else "2").strip().lower()
		native_renderer = False
		if native_mode in ("1", "2", "compat"):
			from cs2esp.visuals.native_imgui_gl import NativeCompatGlfwRenderer
			try:
				self.impl = NativeCompatGlfwRenderer(self.window)
				native_renderer = True
				print("[overlay] native C++ ImGui compatibility renderer")
			except Exception as exc:
				try:
					if getattr(imgui.get_io(), "backend_renderer_user_data", None) is not None:
						imgui.backends.opengl2_shutdown()
				except Exception:
					pass
				print(f"[overlay] native renderer unavailable: {exc}; keeping Python renderer")
		if not native_renderer:
			self.impl = GlfwRenderer(self.window)
		if (not native_renderer and not os.environ.get("CS2ESP_STOCK_IMGUI_GL")):
			from cs2esp.visuals.fast_imgui_gl import install as _fast_gl
			_fast_gl(self.impl)

		style_obj = imgui.get_style()
		style_obj.window_rounding = 6.0
		style_obj.frame_rounding = 4.0
		style_obj.anti_aliased_lines = True

		return True

	def show(self) -> None:
		'''Show overlay window without taking focus.'''
		if self.window is not None:
			try:
				glfw.show_window(self.window)
			except Exception:
				pass
		if self.hwnd is not None:
			try:
				user32.ShowWindow(self.hwnd, 4)  # SW_SHOWNOACTIVATE
				set_topmost(self.hwnd)
			except Exception:
				pass

	def hide(self) -> None:
		'''Completely hide overlay window.'''
		if self.window is not None:
			try:
				glfw.hide_window(self.window)
			except Exception:
				pass
		if self.hwnd is not None:
			try:
				user32.ShowWindow(self.hwnd, 0)  # SW_HIDE
			except Exception:
				pass

	def set_vsync(self, enabled: bool) -> None:
		'''Toggle V-Sync on the active window.'''
		self._vsync = bool(enabled)
		if self.window:
			try:
				glfw.swap_interval(1 if self._vsync else 0)
			except Exception:
				pass

	def set_click_through(self, enabled: bool) -> None:
		if self.hwnd is None:
			return
		# Avoid changing styles and notifying DWM when passthrough state is unchanged
		if getattr(self, "_ct_applied", False) and bool(enabled) == self._click_through:
			return
		self._ct_applied = True
		self._click_through = enabled
		make_overlay_clickthrough(self.hwnd, enabled)

		try:
			user32.SetWindowPos(self.hwnd, 0, 0, 0, 0, 0, _SWP_NOMOVE | _SWP_NOSIZE | _SWP_NOZORDER | _SWP_NOACTIVATE | _SWP_FRAMECHANGED)
		except Exception:
			pass

		if not getattr(self, "_colorkey_fallback", False):
			try:
				glfw.set_window_attrib(self.window, glfw.MOUSE_PASSTHROUGH, glfw.TRUE if enabled else glfw.FALSE)
			except Exception:
				pass

	def set_target_hwnd(self, hwnd: int) -> None:
		'''Follow a resolved game window immediately, then resume periodic geometry checks.'''
		target = int(hwnd or 0)
		if target == self._target_hwnd:
			return
		self._target_hwnd = target
		self._target_sync_pending = True
		self._occlusion_sig = None

	def sync_to_monitor(self) -> bool:
		'''Match CS2's client rect, recreating only when its size changes.'''
		if not self._running or self.window is None:
			return False
		self._monitor_check_frame += 1
		now = perf_counter()
		if self._monitor_check_frame < 60 and not self._target_sync_pending and now - getattr(self, "_monitor_checked_at", 0.0) < 0.05:
			return False
		self._monitor_check_frame = 0
		self._monitor_checked_at = now
		self._target_sync_pending = False

		target = self._target_hwnd
		monitor = monitor_for_hwnd(target) if target else None
		display_handle = monitor.handle if monitor is not None else getattr(self, "_display_handle", 0)
		display_changed = bool(display_handle and getattr(self, "_display_handle", 0) and display_handle != self._display_handle)
		if target:
			new_rect = _target_rect(target)
			if new_rect[2] <= 0 or new_rect[3] <= 0:
				return False
		else:
			new_rect = pick_overlay_monitor(0)
		if not rects_differ(new_rect, self._monitor_rect, tolerance=1) and not display_changed:
			self._display_handle = display_handle
			return False

		old_rect = self._monitor_rect
		self._occlusion_sig = None
		new_x, new_y, new_w, new_h = new_rect
		size_changed = (new_w != self.width or new_h != self.height)
		print(f"[overlay] client-rect change {old_rect} -> {new_rect} (size_changed={size_changed})")
		if size_changed or display_changed:
			try:
				if self.window is not None:
					self._release_frame_fence()
					try:
						forget_fonts_for_current_context(ctx=self.ctx)
					except Exception:
						pass
					try:
						self.impl.shutdown()
					except Exception:
						pass
					try:
						if self.ctx is not None:
							imgui.destroy_context(self.ctx)
					except Exception:
						pass
					try:
						glfw.destroy_window(self.window)
					except Exception:
						pass
					self.window = None
					self.impl = None
					self.ctx = None

				left, top, self.width, self.height = new_rect
				if not self._build_window((left, top, self.width, self.height)):
					raise RuntimeError("glfw.create_window failed during re-sync")
				if self._colorkey_fallback:
					set_colorkey_transparency(self.hwnd, 0, 0, 0)
				print(f"[overlay] Re-created {self.width}x{self.height} overlay at ({left},{top})")
			except Exception as exc:
				print(f"[overlay] monitor re-sync recreate failed: {exc!r}")
				return False
		else:
			try:
				glfw.set_window_pos(self.window, new_x, new_y)
				self.width = new_w
				self.height = new_h
			except Exception as exc:
				print(f"[overlay] monitor re-sync move failed: {exc!r}")
				return False
		self._monitor_rect = new_rect
		self._display_handle = display_handle
		return True

	def set_capture_protect(self, enabled: bool) -> bool:
		'''Retain the desired affinity and record only successful API requests for this HWND.'''
		self._capture_protected = bool(enabled)
		if not self.hwnd:
			self._capture_protect_applied = None
			return False
		applied = set_capture_protect(self.hwnd, self._capture_protected)
		if applied:
			self._capture_protect_applied = self._capture_protected
		return applied

	def set_hide_from_taskbar(self, enabled: bool, *, visible: bool) -> bool:
		'''Persist the live choice for replacement HWNDs without showing a deliberately hidden overlay.'''
		self.hide_from_taskbar = bool(enabled)
		if not self.hwnd:
			self._taskbar_hidden_applied = None
			return False
		self._taskbar_hidden_applied = None
		try:
			style = user32.GetWindowLongW(self.hwnd, GWL_EXSTYLE)
			style = style | WS_EX_TOOLWINDOW if self.hide_from_taskbar else style & ~WS_EX_TOOLWINDOW
			user32.ShowWindow(self.hwnd, 0)
			user32.SetWindowLongW(self.hwnd, GWL_EXSTYLE, style)
			if self.hide_from_taskbar:
				apply_taskbar_invisibility(self.hwnd)
			user32.SetWindowPos(self.hwnd, 0, 0, 0, 0, 0, _SWP_NOSIZE | _SWP_NOMOVE | _SWP_NOZORDER | _SWP_FRAMECHANGED)
			self._taskbar_hidden_applied = bool(user32.GetWindowLongW(self.hwnd, GWL_EXSTYLE) & WS_EX_TOOLWINDOW)
			return self._taskbar_hidden_applied == self.hide_from_taskbar
		except Exception:
			return False
		finally:
			if visible:
				try:
					user32.ShowWindow(self.hwnd, 4)
				except Exception:
					pass

	def set_noactivate(self, enabled: bool) -> None:
		'''Toggle foreground activation for menu interaction.'''
		if self.hwnd is None:
			return
		set_noactivate(self.hwnd, enabled)

	def bring_to_top(self) -> None:
		'''Ensure overlay window is topmost above CS2 and other windows.'''
		if self.hwnd is not None:
			set_topmost(self.hwnd)

	@property
	def running(self) -> bool:
		return self._running and not glfw.window_should_close(self.window)

	def begin_frame(self):
		'''Wait before sampling a fresh scene instead of adding another frame behind a busy GPU.'''
		glfw.poll_events()
		if not self._throttle_queue():
			return False
		self.impl.process_inputs()
		imgui.new_frame()
		gl.glClearColor(0.0, 0.0, 0.0, 0.0)
		gl.glClear(gl.GL_COLOR_BUFFER_BIT)
		return True

	def end_frame(self):
		'''Submit one frame; the next begin_frame must observe its completion before drawing again.'''
		imgui.render()
		self.impl.render(imgui.get_draw_data())
		if self._trace_frame_times:
			self.imgui_commands_submitted_at = perf_counter()
		if self._dump_enabled:
			self._dump_frame()
		self.swap_start_at = perf_counter()
		glfw.swap_buffers(self.window)
		self.swap_return_at = perf_counter()
		self.swap_s = self.swap_return_at - self.swap_start_at
		try:
			self._frame_fence = gl.glFenceSync(gl.GL_SYNC_GPU_COMMANDS_COMPLETE, 0)
		except Exception:
			self.fence_wait_counts["error"] += 1
			gl.glFinish()
		gl.glFlush()
		self.dwm_wait_s = 0.0
		if self._dwm_pacing:
			started = perf_counter()
			status = _dwm_flush()
			self.dwm_wait_s = perf_counter() - started
			if status != 0:
				self._dwm_pacing = False
				print(f'[overlay] Compositor pacing unavailable: HRESULT {status:#x}')
		if self._trace_frame_times:
			self.flush_returned_at = perf_counter()

	def _throttle_queue(self) -> bool:
		'''Keep a timed-out fence and skip submission; deleting it would allow the queue to grow without a bound.'''
		started = perf_counter()
		previous_fence = self._frame_fence
		if previous_fence is None:
			self.fence_wait_s = 0.0
			return True
		try:
			result = gl.glClientWaitSync(previous_fence, gl.GL_SYNC_FLUSH_COMMANDS_BIT, 8_000_000)
			if result == gl.GL_TIMEOUT_EXPIRED:
				self.fence_wait_counts["timeout"] += 1
				return False
			if result in (gl.GL_ALREADY_SIGNALED, gl.GL_CONDITION_SATISFIED):
				self.fence_wait_counts["signaled"] += 1
			else:
				self.fence_wait_counts["failed" if result == gl.GL_WAIT_FAILED else "unknown"] += 1
				gl.glFinish()
			gl.glDeleteSync(previous_fence)
			self._frame_fence = None
		except Exception:
			self.fence_wait_counts["error"] += 1
			gl.glFinish()
			self._frame_fence = None
			if previous_fence is not None:
				try:
					gl.glDeleteSync(previous_fence)
				except Exception:
					pass
		finally:
			self.fence_wait_s = perf_counter() - started
		return True

	def _release_frame_fence(self) -> None:
		'''Delete a context-owned fence before the owning window is destroyed.'''
		fence, self._frame_fence = self._frame_fence, None
		if fence is None or self.window is None:
			return
		try:
			glfw.make_context_current(self.window)
			gl.glDeleteSync(fence)
		except Exception:
			# A lost GL context may already have released the object
			pass

	def _dump_frame(self):
		try:
			import struct
			from os import environ
			from pathlib import Path
			count = getattr(self, "_dump_count", 0) + 1
			self._dump_count = count
			if count == 30:
				gl.glPixelStorei(gl.GL_PACK_ALIGNMENT, 1)
				data = gl.glReadPixels(0, 0, self.width, self.height, gl.GL_BGRA, gl.GL_UNSIGNED_BYTE)
				file_size = 54 + len(data)
				header = struct.pack(
					'<2sIHHI', b'BM', file_size, 0, 0, 54) + struct.pack('<IIIHHIIIIII', 40, self.width, self.height, 1, 32, 0, len(data), 0, 0, 0, 0)
				out_path = Path("temp/live_overlay_dump.bmp")
				out_path.parent.mkdir(parents=True, exist_ok=True)
				with open(out_path, "wb") as output:
					output.write(header)
					output.write(data)
				print(f"[overlay] Live overlay frame dump saved to {out_path} ({self.width}x{self.height})")

				try:
					from PIL import Image
					import ctypes
					from ctypes import wintypes
					gdi32 = ctypes.windll.gdi32
					overlay_image = Image.frombytes("RGBA", (self.width, self.height), data, "raw", "BGRA").transpose(Image.FLIP_TOP_BOTTOM)

					background_image = None
					if self._target_hwnd:
						hdc_wnd = user32.GetDC(self._target_hwnd)
						hdc_mem = gdi32.CreateCompatibleDC(hdc_wnd)
						bitmap_handle = gdi32.CreateCompatibleBitmap(hdc_wnd, self.width, self.height)
						gdi32.SelectObject(hdc_mem, bitmap_handle)
						user32.PrintWindow(self._target_hwnd, hdc_mem, 2)

						class BITMAPINFOHEADER(ctypes.Structure):
							_fields_ = [("biSize", wintypes.DWORD), ("biWidth", wintypes.LONG),
								("biHeight", wintypes.LONG), ("biPlanes", wintypes.WORD), ("biBitCount", wintypes.WORD), ("biCompression", wintypes.DWORD),
								("biSizeImage", wintypes.DWORD), ("biXPelsPerMeter", wintypes.LONG),
								("biYPelsPerMeter", wintypes.LONG), ("biClrUsed", wintypes.DWORD), ("biClrImportant", wintypes.DWORD)]
						bitmap_info = BITMAPINFOHEADER()
						bitmap_info.biSize = ctypes.sizeof(BITMAPINFOHEADER)
						bitmap_info.biWidth = self.width
						bitmap_info.biHeight = -self.height
						bitmap_info.biPlanes = 1
						bitmap_info.biBitCount = 32
						bitmap_info.biCompression = 0
						pixel_buffer = ctypes.create_string_buffer(self.width * self.height * 4)
						gdi32.GetDIBits(hdc_mem, bitmap_handle, 0, self.height, pixel_buffer, ctypes.byref(bitmap_info), 0)
						gdi32.DeleteObject(bitmap_handle)
						gdi32.DeleteDC(hdc_mem)
						user32.ReleaseDC(self._target_hwnd, hdc_wnd)
						background_image = Image.frombuffer("RGBA", (self.width, self.height), pixel_buffer, "raw", "BGRA", 0, 1).convert("RGBA")

					if background_image:
						composite = Image.alpha_composite(background_image, overlay_image)
					else:
						composite = overlay_image

					art_dir = Path("temp/frame_dumps")
					art_dir.mkdir(parents=True, exist_ok=True)
					comp_path = art_dir / "live_composite.png"
					composite.save(str(comp_path))
					print(f"[overlay] Live composite saved to {comp_path}")
				except Exception as composite_error:
					print(f"[overlay] composite save failed: {composite_error}")

				if environ.get("CS2_EXIT_ON_DUMP"):
					self._running = False
		except Exception as error:
			print(f"[overlay] dump error: {error}")

	def get_draw_list(self):
		return imgui.get_background_draw_list()

	def destroy(self):
		self._running = False
		self._release_frame_fence()
		if self.impl:
			self.impl.shutdown()
		if self.ctx is not None:
			try:
				imgui.destroy_context(self.ctx)
			except Exception:
				pass
		if self.window:
			glfw.destroy_window(self.window)
		self.impl = None
		self.ctx = None
		self.window = None
		self.hwnd = None
		self._capture_protect_applied = None
		self._taskbar_hidden_applied = None
		glfw.terminate()
		print("[overlay] Destroyed")
