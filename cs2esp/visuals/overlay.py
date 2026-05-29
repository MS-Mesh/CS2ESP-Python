"""Transparent click-through GLFW/imgui overlay."""

from random import choice

import glfw
import OpenGL.GL as gl
from imgui_bundle import imgui
from imgui_bundle.python_backends.glfw_backend import GlfwRenderer

from cs2esp.config import CONFIG_DIR
from cs2esp.display_info import (
    pick_overlay_monitor,
    rects_differ,
)
from cs2esp.ui.fonts import (
    forget_fonts_for_current_context,
    load_fonts_for_current_context,
)
from cs2esp.win32_utils import (
    GWL_EXSTYLE,
    WS_EX_NOACTIVATE,
    WS_EX_TOOLWINDOW,
    WS_EX_TOPMOST,
    apply_taskbar_invisibility,
    extend_frame_into_client,
    get_client_rect,
    make_overlay_clickthrough,
    set_capture_protect,
    set_colorkey_transparency,
    set_noactivate,
    set_topmost,
    user32,
)


def _target_rect(target_hwnd: int) -> tuple[int, int, int, int]:
    """Use CS2's projection-space client rect, falling back to its monitor."""
    if target_hwnd:
        client = get_client_rect(target_hwnd)
        if client is not None:
            return client
    return pick_overlay_monitor(target_hwnd)


_DECOY_TITLES = (
    "Profile Manager",
    "Cache Cleaner",
    "Update Assistant",
    "Workspace Helper",
    "Sync Daemon",
    "Theme Studio",
    "Volume Mixer Plus",
)


class Overlay:
    def __init__(
        self,
        width: int = 0,
        height: int = 0,
        title: str | None = None,
        randomize_title: bool = False,
        hide_from_taskbar: bool = True,
    ):
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
        # Track the target client rect so moving or resizing CS2 keeps projection aligned.
        self._target_hwnd: int = 0
        self._monitor_rect: tuple[int, int, int, int] = (0, 0, 0, 0)
        self._monitor_check_frame = 0

    def create(self, target_hwnd: int = 0, colorkey_fallback: bool = False) -> bool:
        """Create the transparent overlay over CS2's client area / monitor."""
        if not glfw.init():
            print("[overlay] Failed to init GLFW")
            return False

        self._colorkey_fallback = bool(colorkey_fallback)
        self._target_hwnd = int(target_hwnd or 0)
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
        print(f"[overlay] Created {self.width}x{self.height} overlay "
              f"(title={self.title!r})")
        return True

    def _build_window(self, rect: tuple[int, int, int, int]) -> bool:
        """Build the GLFW window and its bound GL/imgui context at ``rect``."""
        x, y, w, h = rect
        glfw.window_hint(glfw.FLOATING, glfw.TRUE)
        glfw.window_hint(glfw.TRANSPARENT_FRAMEBUFFER, glfw.TRUE)
        glfw.window_hint(glfw.DECORATED, glfw.FALSE)
        # Colorkey is the fallback when DWM refuses GLFW per-pixel alpha.
        if not self._colorkey_fallback:
            glfw.window_hint(glfw.MOUSE_PASSTHROUGH, glfw.TRUE)
        glfw.window_hint(glfw.FOCUS_ON_SHOW, glfw.FALSE)
        glfw.window_hint(glfw.VISIBLE, glfw.FALSE)

        self.window = glfw.create_window(w, h, self.title, None, None)
        if not self.window:
            print("[overlay] Failed to create window")
            return False

        glfw.make_context_current(self.window)
        glfw.set_window_pos(self.window, x, y)
        glfw.swap_interval(1)

        self.hwnd = glfw.get_win32_window(self.window)
        style = user32.GetWindowLongW(self.hwnd, GWL_EXSTYLE)
        style |= WS_EX_TOPMOST | WS_EX_NOACTIVATE
        if self.hide_from_taskbar:
            style |= WS_EX_TOOLWINDOW
        user32.SetWindowLongW(self.hwnd, GWL_EXSTYLE, style)
        set_topmost(self.hwnd)
        extend_frame_into_client(self.hwnd)
        apply_taskbar_invisibility(self.hwnd)
        self.set_click_through(self._click_through)
        if self._capture_protected:
            set_capture_protect(self.hwnd, True)

        try:
            glfw.show_window(self.window)
        except Exception:
            try:
                user32.ShowWindow(self.hwnd, 4)  # SW_SHOWNOACTIVATE
            except Exception:
                pass

        self.ctx = imgui.create_context()
        try:
            imgui.get_io().set_ini_filename(str(CONFIG_DIR / "imgui.ini"))
        except Exception as exc:
            print(f"[overlay] imgui.ini redirect failed: {exc!r}")
        self.impl = GlfwRenderer(self.window)
        try:
            load_fonts_for_current_context(self.impl)
        except Exception as exc:
            print(f"[overlay] font load failed: {exc!r}")

        style_obj = imgui.get_style()
        style_obj.window_rounding = 6.0
        style_obj.frame_rounding = 4.0
        style_obj.anti_aliased_lines = True
        return True

    def set_click_through(self, enabled: bool) -> None:
        if self.hwnd is None:
            return

        self._click_through = enabled
        make_overlay_clickthrough(self.hwnd, enabled)

        if not getattr(self, "_colorkey_fallback", False):
            try:
                glfw.set_window_attrib(
                    self.window,
                    glfw.MOUSE_PASSTHROUGH,
                    glfw.TRUE if enabled else glfw.FALSE,
                )
            except Exception:
                pass

    def set_target_hwnd(self, hwnd: int) -> None:
        """Update the CS2 window the overlay follows for monitor re-sync."""
        self._target_hwnd = int(hwnd or 0)

    def sync_to_monitor(self) -> bool:
        """Match CS2's client rect, recreating only when its size changes."""
        if not self._running or self.window is None:
            return False
        self._monitor_check_frame += 1
        if self._monitor_check_frame < 60:
            return False
        self._monitor_check_frame = 0

        target = self._target_hwnd
        if target:
            new_rect = _target_rect(target)
            if new_rect[2] <= 0 or new_rect[3] <= 0:
                return False
        else:
            new_rect = pick_overlay_monitor(0)
        if not rects_differ(new_rect, self._monitor_rect, tolerance=1):
            return False

        old_rect = self._monitor_rect
        self._monitor_rect = new_rect
        new_x, new_y, new_w, new_h = new_rect
        size_changed = (new_w != self.width or new_h != self.height)
        print(f"[overlay] client-rect change {old_rect} -> {new_rect} "
              f"(size_changed={size_changed})")
        if size_changed:
            try:
                if self.window is not None:
                    try:
                        forget_fonts_for_current_context()
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

                x, y, self.width, self.height = new_rect
                if not self._build_window((x, y, self.width, self.height)):
                    raise RuntimeError("glfw.create_window failed during re-sync")
                if self._colorkey_fallback:
                    set_colorkey_transparency(self.hwnd, 0, 0, 0)
                print(f"[overlay] Re-created {self.width}x{self.height} overlay "
                      f"at ({x},{y})")
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
        return True

    def set_capture_protect(self, enabled: bool) -> bool:
        """Hide the overlay from screen capture."""
        self._capture_protected = bool(enabled)
        if self.hwnd is None:
            return False
        return set_capture_protect(self.hwnd, self._capture_protected)

    def set_noactivate(self, enabled: bool) -> None:
        """Toggle foreground activation for menu interaction."""
        if self.hwnd is None:
            return
        set_noactivate(self.hwnd, enabled)

    @property
    def running(self) -> bool:
        return self._running and not glfw.window_should_close(self.window)

    def begin_frame(self):
        glfw.poll_events()
        self.impl.process_inputs()
        imgui.new_frame()
        gl.glClearColor(0.0, 0.0, 0.0, 0.0)
        gl.glClear(gl.GL_COLOR_BUFFER_BIT)

    def end_frame(self):
        imgui.render()
        self.impl.render(imgui.get_draw_data())
        glfw.swap_buffers(self.window)

    def get_draw_list(self):
        return imgui.get_background_draw_list()

    def destroy(self):
        self._running = False
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
        glfw.terminate()
        print("[overlay] Destroyed")
