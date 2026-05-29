"""Separate GLFW/imgui window for the settings menu."""

import ctypes

import glfw
import OpenGL.GL as gl
from imgui_bundle import imgui
from imgui_bundle.python_backends.glfw_backend import GlfwRenderer

from cs2esp.ui.fonts import load_fonts_for_current_context
from cs2esp.win32_utils import focus_window


class _FocusInsensitiveGlfwRenderer(GlfwRenderer):
    """Forward cursor coordinates even when GLFW briefly reports no focus."""

    def mouse_callback(self, *_args, **_kwargs):
        try:
            mx, my = glfw.get_cursor_pos(self.window)
            w, h = glfw.get_window_size(self.window)
            if 0 <= mx < w and 0 <= my < h:
                self.io.add_mouse_pos_event(mx, my)
            else:
                self.io.add_mouse_pos_event(-1, -1)
        except Exception:
            pass
    def mouse_button_callback(self, _window, button, action, _mods):
        # Re-query cursor so clicks land where the pointer is, even right after a focus change.
        try:
            mx, my = glfw.get_cursor_pos(self.window)
            self.io.add_mouse_pos_event(mx, my)
        except Exception:
            pass
        self.io.add_mouse_button_event(button, action == glfw.PRESS)


class MenuWindow:
    """Standalone OS window hosting the settings menu."""

    def __init__(self, width: int = 1100, height: int = 720,
                 title: str = "MS Mesh \u00b7 Settings",
                 parent_glfw_window=None):
        # Clamp to a sane minimum so bad configs can't produce an unseeable window.
        self.width = max(640, int(width))
        self.height = max(420, int(height))
        self.title = title
        self.parent_glfw_window = parent_glfw_window
        self.window = None
        self.impl: GlfwRenderer | None = None
        self.ctx = None
        self.hwnd: int = 0

    def create(self) -> bool:
        """Spawn the window + GL context + imgui context. No-op if already created."""
        if self.window is not None:
            return True

        # Preserve the caller's GL and imgui contexts.
        prev_glfw = glfw.get_current_context()
        prev_imgui_ctx = imgui.get_current_context()

        # Keep normal click-to-raise z-order; GLFW FLOATING would make the menu topmost.
        glfw.window_hint(glfw.DECORATED, glfw.TRUE)
        glfw.window_hint(glfw.RESIZABLE, glfw.TRUE)
        glfw.window_hint(glfw.FLOATING, glfw.FALSE)
        glfw.window_hint(glfw.TRANSPARENT_FRAMEBUFFER, glfw.FALSE)
        glfw.window_hint(glfw.VISIBLE, glfw.TRUE)
        glfw.window_hint(glfw.FOCUS_ON_SHOW, glfw.TRUE)

        # Share GL resources with the overlay.
        self.window = glfw.create_window(
            self.width, self.height, self.title,
            None, self.parent_glfw_window,
        )
        if not self.window:
            print("[menu_window] glfw.create_window failed")
            return False

        glfw.make_context_current(self.window)
        glfw.swap_interval(1)

        try:
            self.hwnd = int(glfw.get_win32_window(self.window))
        except Exception:
            self.hwnd = 0

        # Remove click-through/topmost styles and expose the menu in the taskbar and Alt-Tab.
        if self.hwnd:
            try:
                GWL_EXSTYLE = -20
                WS_EX_TRANSPARENT = 0x00000020
                WS_EX_LAYERED = 0x00080000
                WS_EX_NOACTIVATE = 0x08000000
                WS_EX_TOPMOST = 0x00000008
                WS_EX_APPWINDOW = 0x00040000
                SWP_NOMOVE = 0x0002
                SWP_NOSIZE = 0x0001
                SWP_NOACTIVATE = 0x0010
                SWP_FRAMECHANGED = 0x0020
                HWND_NOTOPMOST = -2
                user32 = ctypes.windll.user32
                style = user32.GetWindowLongW(self.hwnd, GWL_EXSTYLE)
                style &= ~(WS_EX_TRANSPARENT | WS_EX_LAYERED
                           | WS_EX_NOACTIVATE | WS_EX_TOPMOST)
                style |= WS_EX_APPWINDOW
                user32.SetWindowLongW(self.hwnd, GWL_EXSTYLE, style)
                user32.SetWindowPos(
                    self.hwnd, HWND_NOTOPMOST, 0, 0, 0, 0,
                    SWP_NOMOVE | SWP_NOSIZE | SWP_NOACTIVATE | SWP_FRAMECHANGED,
                )
            except Exception as exc:
                print(f"[menu_window] style scrub failed: {exc}")

        # The renderer must be created after this window's imgui context.
        self.ctx = imgui.create_context()
        imgui.set_current_context(self.ctx)
        self.impl = _FocusInsensitiveGlfwRenderer(self.window)

        # Each imgui context owns its font atlas, so mirror the overlay font load.
        try:
            load_fonts_for_current_context(self.impl)
        except Exception as exc:
            print(f"[menu_window] font load failed: {exc!r}")

        io = imgui.get_io()
        io.mouse_draw_cursor = False

        style = imgui.get_style()
        style.window_rounding = 8.0
        style.frame_rounding = 6.0
        style.anti_aliased_lines = True

        if prev_imgui_ctx is not None:
            imgui.set_current_context(prev_imgui_ctx)
        if prev_glfw is not None:
            glfw.make_context_current(prev_glfw)
        return True

    def destroy(self) -> None:
        if self.window is None:
            return
        try:
            glfw.make_context_current(self.window)
        except Exception:
            pass
        try:
            if self.ctx is not None:
                imgui.set_current_context(self.ctx)
        except Exception:
            pass
        try:
            if self.impl is not None:
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
        self.hwnd = 0

    def should_close(self) -> bool:
        return (self.window is not None
                and bool(glfw.window_should_close(self.window)))

    def get_size(self) -> tuple[int, int]:
        """Return the current window size, or ``(0, 0)`` after destruction."""
        if self.window is None:
            return (0, 0)
        try:
            width, height = glfw.get_window_size(self.window)
            return (int(width), int(height))
        except Exception:
            return (0, 0)

    def begin_frame(self) -> None:
        """Make this window's contexts current and start an imgui frame."""
        glfw.make_context_current(self.window)
        imgui.set_current_context(self.ctx)
        self.impl.process_inputs()
        imgui.new_frame()
        gl.glClearColor(0.06, 0.06, 0.08, 1.0)
        gl.glClear(gl.GL_COLOR_BUFFER_BIT)

    def end_frame(self) -> None:
        imgui.render()
        self.impl.render(imgui.get_draw_data())
        glfw.swap_buffers(self.window)

    def focus(self) -> None:
        """Grant foreground and keyboard focus using the Win32 focus helper."""
        if not self.hwnd:
            return
        try:
            focus_window(self.hwnd)
        except Exception:
            pass

