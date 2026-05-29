"""
Out-of-process Qt menu launcher.

The overlay loop owns GLFW/imgui, so PySide6 runs in a separate process. This
controller spawns that process, finds its HWND once Qt is ready, and treats focus
failures as menu-only problems rather than overlay-loop failures.
"""

import ctypes
import subprocess
import sys
from pathlib import Path
from time import monotonic


class QtMenuController:
    """Manage the Qt menu subprocess without bringing down the overlay loop."""

    def __init__(self) -> None:
        self._proc: subprocess.Popen | None = None
        self._hwnd: int = 0
        self._last_hwnd_lookup: float = 0.0
        self._focus_deadline: float = 0.0

    def spawn(self) -> bool:
        """Launch the Qt menu subprocess if it isn't already running.

        Returns True on a fresh spawn, False if a process was already
        alive (or if the spawn itself failed).
        """
        if self.is_alive():
            return False
        try:
            command = (
                [sys.executable, "--qt-menu"]
                if getattr(sys, "frozen", False)
                else [sys.executable, "-m", "cs2esp.ui.qt"]
            )
            creationflags = 0
            if sys.platform == "win32":
                creationflags = 0x08000000
            self._proc = subprocess.Popen(
                command,
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
                stdin=subprocess.DEVNULL,
                creationflags=creationflags,
                cwd=str(Path(__file__).resolve().parents[2]),
            )
            self._hwnd = 0
            self._last_hwnd_lookup = 0.0
            self._focus_deadline = monotonic() + 2.5
            print(f"[qt-launcher] Spawned Qt menu PID {self._proc.pid}")
            return True
        except Exception as e:
            print(f"[qt-launcher] Spawn failed: {e!r}")
            self._proc = None
            return False

    def is_alive(self) -> bool:
        """True iff the subprocess handle exists AND the process has
        not yet exited. Cheap: no waitpid, just a poll."""
        if self._proc is None:
            return False
        return self._proc.poll() is None

    def _locate_hwnd(self) -> int:
        """Look up the Qt menu's top-level window HWND via its PID.

        Returns 0 until the Qt window has actually been created (there
        is a noticeable delay between Popen returning and Qt finishing
        its app.exec() startup, so the first few calls may return 0).

        We cache the result for 0.5 s so the per-frame call cost stays
        bounded at < 50 µs even when Qt is slow to start up.
        """
        if self._proc is None or not self.is_alive():
            return 0
        now = monotonic()
        if self._hwnd and now - self._last_hwnd_lookup < 0.5:
            return self._hwnd
        try:
            from cs2esp.win32_utils import find_window_by_pid
            hwnd = find_window_by_pid(int(self._proc.pid))
            if hwnd:
                self._hwnd = int(hwnd)
                self._last_hwnd_lookup = now
        except Exception:
            pass
        return self._hwnd

    def toggle(self, *, cs2_hwnd: int = 0) -> None:
        """Show the menu over the game, or hide it if already visible.

        Opening the menu must NOT minimise CS2. A borderless / fullscreen-
        optimised game auto-minimises when another window takes foreground
        on its monitor, which looked like "the menu appears outside the
        game and CS2 disappears". We avoid that by keeping CS2 restored
        (SW_SHOWNOACTIVATE) behind the menu and pinning the menu topmost,
        so the game keeps rendering underneath instead of minimising.
        """
        if not self.is_alive():
            self.spawn()
            return

        hwnd = self._locate_hwnd()
        if not hwnd:
            return

        try:
            user32 = ctypes.windll.user32
            SW_RESTORE = 9
            SW_SHOWNOACTIVATE = 4

            menu_visible = (not user32.IsIconic(hwnd)
                            and bool(user32.IsWindowVisible(hwnd)))
            fg = int(user32.GetForegroundWindow() or 0)
            menu_is_foreground = (fg == hwnd)

            # Visible AND focused -> a second press hides it.
            if menu_visible and menu_is_foreground:
                user32.ShowWindow(hwnd, 0)
                self._focus_deadline = 0.0
                if cs2_hwnd:
                    self._activate(int(cs2_hwnd))
                return

            # Otherwise bring the menu up over the game.
            if user32.IsIconic(hwnd):
                user32.ShowWindow(hwnd, SW_RESTORE)
            self._activate(hwnd)
            # Keep CS2 alive behind the menu without stealing focus back, so a fullscreen-optimised game does not minimise.
            if cs2_hwnd:
                try:
                    user32.ShowWindow(int(cs2_hwnd), SW_SHOWNOACTIVATE)
                except Exception:
                    pass
            self._focus_deadline = monotonic() + 1.5
        except Exception as e:
            print(f"[qt-launcher] Toggle failed: {e!r}")

    @staticmethod
    def _activate(hwnd: int) -> None:
        """Best-effort foreground grab that avoids taskbar flashing."""
        try:
            from cs2esp.win32_utils import focus_window
            focus_window(int(hwnd))
        except Exception as e:
            print(f"[qt-launcher] activate failed: {e!r}")

    def tick_focus(self) -> None:
        """Retry foreground focus until the spawn deadline expires."""
        if self._focus_deadline <= 0.0:
            return
        if monotonic() >= self._focus_deadline:
            self._focus_deadline = 0.0
            return
        if not self.is_alive():
            self._focus_deadline = 0.0
            return
        hwnd = self._locate_hwnd()
        if not hwnd:
            return
        try:
            user32 = ctypes.windll.user32
            fg = user32.GetForegroundWindow()
            if int(fg) == hwnd:
                self._focus_deadline = 0.0
                return
            SW_SHOWNORMAL = 1
            user32.ShowWindow(hwnd, SW_SHOWNORMAL)
            user32.SetForegroundWindow(hwnd)
        except Exception:
            pass

    def terminate(self) -> None:
        """Best-effort shutdown of the Qt subprocess."""
        if self._proc is None:
            return
        try:
            self._proc.terminate()
            try:
                self._proc.wait(timeout=2.0)
            except subprocess.TimeoutExpired:
                self._proc.kill()
        except Exception:
            pass
        self._proc = None
        self._hwnd = 0


__all__ = ["QtMenuController"]
