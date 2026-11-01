"""
Out-of-process Qt menu launcher.
PySide6 runs in a separate process because the overlay loop owns GLFW/imgui; this controller spawns that process, finds its HWND once Qt is ready,
and treats focus failures as menu-only problems rather than overlay-loop failures.
"""

import ctypes
import os
import subprocess
import sys
from pathlib import Path
from time import monotonic


CREATE_NO_WINDOW = 0x08000000
SW_HIDE = 0
SW_SHOWNORMAL = 1
SW_SHOWNOACTIVATE = 4
SW_RESTORE = 9


class QtMenuController:
	"""Manage the Qt menu subprocess without bringing down the overlay loop."""

	def __init__(self) -> None:
		self._proc: subprocess.Popen | None = None
		self._hwnd: int = 0
		self._last_hwnd_lookup: float = 0.0
		self._focus_deadline: float = 0.0
		self._editor_revision: int = 0

	def show_editor(self, selection: dict | None, *, cs2_hwnd: int = 0) -> bool:
		'''Show a hidden menu once for a new released HUD selection; never toggle, hide, or interrupt dragging.'''
		if not isinstance(selection, dict) or not selection.get("allowed") or selection.get("interacting"):
			return False
		if selection.get("widget_id") not in ("fps_counter", "event_feed", "hotkeys", "status_pills", "spectators", "c4_capsule"):
			return False
		try:
			revision = int(selection.get("revision", 0))
		except (TypeError, ValueError):
			return False
		if revision <= self._editor_revision:
			return False
		if not self.is_alive():
			self.spawn()
			self._focus_deadline = 0.0
			return False
		hwnd = self._locate_hwnd()
		if not hwnd:
			return False
		try:
			user32 = ctypes.windll.user32
			visible = not user32.IsIconic(hwnd) and bool(user32.IsWindowVisible(hwnd))
			if not visible:
				user32.ShowWindow(hwnd, SW_RESTORE if user32.IsIconic(hwnd) else SW_SHOWNOACTIVATE)
				self._activate(hwnd)
				if cs2_hwnd:
					user32.ShowWindow(int(cs2_hwnd), SW_SHOWNOACTIVATE)
			self._focus_deadline = 0.0
			self._editor_revision = revision
			return True
		except Exception as error:
			print(f"[qt-launcher] Show editor failed: {error!r}")
			return False

	def spawn(self) -> bool:
		'''Launch the Qt menu subprocess if it is not already running; True only on a fresh spawn.'''
		if self.is_alive():
			return False
		try:
			command = [sys.executable, "--qt-menu"] if getattr(sys, "frozen", False) else [sys.executable, "-m", "cs2esp.ui.qt"]
			creationflags = CREATE_NO_WINDOW if sys.platform == "win32" else 0
			env = dict(os.environ)
			# The menu exits on its own once this process is gone, so a crashed overlay leaves no orphan menu
			env["CS2ESP_OWNER_PID"] = str(os.getpid())
			self._proc = subprocess.Popen(
				command,
				env=env,
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
		'''True iff the subprocess handle exists and the process has not exited; poll only, no waitpid.'''
		if self._proc is None:
			return False
		return self._proc.poll() is None

	@property
	def is_visible(self) -> bool:
		'''True if the Qt window exists and is not minimized or hidden.'''
		if not self.is_alive():
			return False
		hwnd = self._locate_hwnd()
		if not hwnd:
			return False
		try:
			user32 = ctypes.windll.user32
			return not bool(user32.IsIconic(hwnd)) and bool(user32.IsWindowVisible(hwnd))
		except Exception:
			return False

	def _locate_hwnd(self) -> int:
		'''Look up the menu's top-level HWND by PID; return 0 until Qt creates its window.'''
		if self._proc is None or not self.is_alive():
			return 0
		now = monotonic()
		# Once found the HWND is stable for the process lifetime; re-verify cheaply instead of re-enumerating every frame
		if self._hwnd and now - self._last_hwnd_lookup < 0.5:
			return self._hwnd
		if self._hwnd:
			try:
				if ctypes.windll.user32.IsWindow(self._hwnd):
					self._last_hwnd_lookup = now
					return self._hwnd
			except Exception:
				pass
			self._hwnd = 0
		# A hidden menu has no visible top-level window, so a miss would re-enumerate every window each frame without this cooldown
		if not self._hwnd and now - self._last_hwnd_lookup < 0.5:
			return 0
		self._last_hwnd_lookup = now
		try:
			from cs2esp.win32_utils import find_window_by_pid

			hwnd = find_window_by_pid(int(self._proc.pid))
			if not hwnd:
				# Windows venv redirectors can own the process handle while a child interpreter owns the Qt window
				from cs2esp.metrics_win32 import process_tree
				for child_pid in process_tree((int(self._proc.pid),)):
					if child_pid == self._proc.pid:
						continue
					hwnd = find_window_by_pid(child_pid)
					if hwnd:
						break
			if hwnd:
				self._hwnd = int(hwnd)
		except Exception:
			pass
		return self._hwnd

	def toggle(self, *, cs2_hwnd: int = 0) -> None:
		'''Show the menu over the game, or hide it if already visible; opening must never minimise a fullscreen-optimised CS2.'''
		if not self.is_alive():
			self.spawn()
			return

		hwnd = self._locate_hwnd()
		if not hwnd:
			return

		try:
			user32 = ctypes.windll.user32
			menu_visible = not user32.IsIconic(hwnd) and bool(user32.IsWindowVisible(hwnd))
			foreground_hwnd = int(user32.GetForegroundWindow() or 0)
			menu_is_foreground = foreground_hwnd == hwnd

			# Visible and focused -> a second press hides it
			if menu_visible and menu_is_foreground:
				user32.ShowWindow(hwnd, SW_HIDE)
				self._focus_deadline = 0.0
				if cs2_hwnd:
					self._activate(int(cs2_hwnd))
				return

			# Otherwise bring the menu up over the game
			if user32.IsIconic(hwnd):
				user32.ShowWindow(hwnd, SW_RESTORE)
			self._activate(hwnd)
			# Keep CS2 alive behind the menu without stealing focus back, so a fullscreen-optimised game does not minimise
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
		'''Best-effort foreground grab that avoids taskbar flashing.'''
		try:
			from cs2esp.win32_utils import focus_window

			focus_window(int(hwnd))
		except Exception as e:
			print(f"[qt-launcher] activate failed: {e!r}")

	def tick_focus(self) -> None:
		'''Retry foreground focus until the spawn deadline expires.'''
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
			foreground_hwnd = user32.GetForegroundWindow()
			if int(foreground_hwnd) == hwnd:
				self._focus_deadline = 0.0
				return
			user32.ShowWindow(hwnd, SW_SHOWNORMAL)
			user32.SetForegroundWindow(hwnd)
		except Exception:
			pass

	def terminate(self) -> None:
		'''Ask Qt to save and close before falling back to process termination.'''
		if self._proc is None:
			return
		try:
			hwnd = self._locate_hwnd() if self._proc.poll() is None else 0
			if hwnd and ctypes.windll.user32.PostMessageW(ctypes.c_void_p(hwnd), 0x0010, 0, 0):
				try:
					self._proc.wait(timeout=2.0)
				except subprocess.TimeoutExpired:
					pass
			if self._proc.poll() is None:
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
