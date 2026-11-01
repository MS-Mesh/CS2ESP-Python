"""
Persistent HUD geometry, one-widget mouse gestures and deferred component selection; callers supply focus and occlusion authorization,
while edit mode and the edit lock remain the interaction gate.
"""

import ctypes as _ctypes
import ctypes.wintypes as _wintypes
from dataclasses import dataclass

from imgui_bundle import imgui

from cs2esp.ui.fonts import get_fonts_for_current_context, pop as pop_font, push_sized, text_size


_EDITOR_WIDGETS = ("fps_counter", "event_feed", "hotkeys", "status_pills", "spectators", "c4_capsule")


@dataclass
class WidgetInteractionState:
	is_dragging: bool = False
	is_resizing: bool = False
	offset_x: float = 0.0
	offset_y: float = 0.0
	start_x: float = 0.0
	start_y: float = 0.0
	resize_orig_w: float = 0.0
	resize_orig_h: float = 0.0
	resize_edge: str = ""
	layout_changed: bool = False
	position_changed: bool = False
	reference_orig_w: float = 0.0
	reference_orig_h: float = 0.0
	reference_start_x: float = 0.0
	reference_start_y: float = 0.0
	reference_edge: str = "bottom"


class _CURSORINFO(_ctypes.Structure):
	_fields_ = [("cbSize", _wintypes.DWORD), ("flags", _wintypes.DWORD), ("hCursor", _ctypes.c_void_p), ("ptScreenPos", _wintypes.POINT)]


class HudPositionManager:
	"""Persistent HUD widget geometry, gesture state and selection hand-off."""

	def __init__(self, config) -> None:
		self.config = config
		self._states: dict[str, WidgetInteractionState] = {}
		self._overlay_hwnd: int = 0
		self._hovered_this_frame: set[str] = set()
		self._runtime_pos: dict[str, tuple[float, float]] = {}
		self._runtime_size: dict[str, tuple[float, float]] = {}
		self._last_bounds: dict[str, tuple[float, float, float, float]] = {}
		self._selected_widget: str = ""
		self._selection_revision: int = 0
		self._selection_ready: bool = False
		self._selection_cancelled: bool = False
		self._input_enabled: bool = True
		self._excluded_rects: tuple[tuple[float, float, float, float], ...] = ()
		self._pending_frames: list[tuple] = []
		self._max_w: float = 0.0
		self._max_h: float = 0.0
		self.snap_enabled: bool = True
		self._toolbar_hover: bool = False
		self._toolbar_pressed: str = ""
		self._toolbar_buttons: list[tuple] = []
		self._toolbar_label = "HUD EDIT MODE · drag body · grips resize · Ctrl bypasses snap"
		self._toolbar_rect: tuple[float, float, float, float] = (0.0, 0.0, 0.0, 0.0)
		self._mouse_cache: dict[bool, tuple[float, float, bool]] = {}
		self._mouse_snapshot: tuple[float, float, bool] | None = None
		self._cursor_visible = False
		self._free_cursor = False
		self._last_edit_mode = self.edit_mode_active()
		self._reference_edges: dict[str, str] = {}
		self._reference_screen_h = 0
		# A press captures one widget until release; entering another widget during a drag cannot capture it
		self._frame_down: bool = False
		self._prev_down: bool = False
		self._claimed: str = ""
		self._press_blocked = False

	def set_overlay_hwnd(self, hwnd: int) -> None:
		'''Set the overlay client window used for pointer coordinates.'''
		self._overlay_hwnd = int(hwnd or 0)

	def _cancel_interactions(self) -> None:
		'''Invalidate gestures and selection when their editing context ends.'''
		self._press_blocked = True
		self._selection_cancelled = bool(self._selected_widget)
		self._selection_ready = False
		for widget_id, state in self._states.items():
			if state.is_dragging or state.is_resizing:
				self._runtime_pos.pop(widget_id, None)
				self._runtime_size.pop(widget_id, None)
			state.is_dragging = False
			state.is_resizing = False
			state.layout_changed = False
			state.position_changed = False
		self._hovered_this_frame.clear()
		self._last_bounds.clear()
		self._pending_frames.clear()
		self._toolbar_hover = False
		self._toolbar_pressed = ""

	def set_input_context(self, *, enabled: bool, excluded_rects=(), free_cursor: bool = False) -> None:
		'''Gate HUD input and an owned menu's free pointer; exclusions use overlay-client (x, y, width, height).'''
		edit_mode_active = self.edit_mode_active()
		context_closed = (self._free_cursor and not free_cursor) or (self._last_edit_mode and not edit_mode_active)
		self._last_edit_mode = edit_mode_active
		self._input_enabled = bool(enabled)
		self._free_cursor = self._input_enabled and bool(free_cursor)
		self._mouse_cache.clear()
		self._excluded_rects = tuple(tuple(float(value) for value in rect) for rect in excluded_rects if len(rect) == 4)
		if context_closed or not self._input_enabled or not self.editing_allowed(True):
			self._cancel_interactions()

	def editor_selection(self) -> dict | None:
		'''Return a transient selection notice; consumers defer opening its existing form until release.'''
		if self._selected_widget not in _EDITOR_WIDGETS:
			return None
		return {
			"widget_id": self._selected_widget,
			"revision": self._selection_revision,
			"allowed": self.editing_allowed(True) and not self._selection_cancelled,
			"interacting": self.is_interacting() or not self._selection_ready,
		}

	def edit_mode_active(self) -> bool:
		'''HUD edit mode (photoshop mode), toggled from the menu button.'''
		event_visuals = getattr(self.config, "event_visuals", None)
		return bool(getattr(event_visuals, "edit_mode", False)) if event_visuals is not None else False

	def editing_allowed(self, interactive: bool) -> bool:
		'''Allow geometry changes in Edit Mode or while the lock is off; pointer visibility is gated separately.'''
		if not self._input_enabled:
			return False
		if self.edit_mode_active():
			return True
		event_visuals = getattr(self.config, "event_visuals", None)
		lock = bool(getattr(event_visuals, "edit_lock", True)) if event_visuals is not None else True
		return not lock

	def begin_frame(self) -> None:
		'''Sample the physical button even when no HUD component is visible.'''
		self._hovered_this_frame.clear()
		self._mouse_cache.clear()
		self._pending_frames.clear()
		self._toolbar_hover = False
		self._prev_down = self._frame_down
		self._mouse_snapshot = self._read_mouse_state(True)
		self._frame_down = self._mouse_snapshot[2]
		if not self._frame_down:
			self._press_blocked = False
		if not self._prev_down and not self.is_interacting():
			self._claimed = ""

	def flush_selection(self) -> None:
		'''Draw queued selection frames on top of every widget, then clear.'''
		pending, self._pending_frames = self._pending_frames, []
		for item in pending:
			(draw_list, x, y, width, height, accent, edges, grip, hovered_grip, active, label, max_w, max_h, preview_only) = item
			self._max_w, self._max_h = max_w, max_h
			try:
				self._draw_selection_frame(draw_list, x, y, width, height, accent, edges, grip, hovered_grip, active, label, preview_only)
			except Exception as e:
				print(f"[hud_manager] selection frame draw failed: {e!r}")

	def is_hovering_any(self) -> bool:
		'''Return True if the pointer is over any HUD widget, grip or the toolbar.'''
		return bool(self._hovered_this_frame) or self.is_interacting() or self._toolbar_hover

	def is_interacting(self) -> bool:
		'''Return True if any widget is currently being dragged or resized.'''
		return any(state.is_dragging or state.is_resizing for state in self._states.values())

	def wants_input(self) -> bool:
		'''Locked widgets never request pointer capture.'''
		return self.editing_allowed(True) and (self.edit_mode_active() or self.is_hovering_any())

	@staticmethod
	def content_scale(width: float, default_w: float, lo: float = 0.6, hi: float = 2.5) -> float:
		'''Scale factor from a widget's resized width vs its design width, clamped to [lo, hi].'''
		if default_w <= 0:
			return 1.0
		return max(lo, min(hi, width / default_w))

	@staticmethod
	def content_scale_2d(width: float, height: float, default_w: float, default_h: float, lo: float = 0.6, hi: float = 2.5) -> float:
		'''Scale from whichever frame edge the user dragged furthest, so stacked widgets keep their aspect ratio.'''
		ratios = []
		if default_w > 0:
			ratios.append(width / default_w)
		if default_h > 0:
			ratios.append(height / default_h)
		if not ratios:
			return 1.0
		ratio = max(ratios, key=lambda value: abs(value - 1.0))
		return max(lo, min(hi, ratio))

	_desktop_attached = False

	@classmethod
	def _ensure_desktop(cls) -> None:
		'''Attach the thread to the input desktop once so pointer reads work over the game.'''
		if cls._desktop_attached:
			return
		try:
			user32 = _ctypes.windll.user32
			desktop_handle = user32.OpenInputDesktop(0, False, 0x01FF)
			if desktop_handle:
				user32.SetThreadDesktop(desktop_handle)
				user32.CloseDesktop(desktop_handle)
				cls._desktop_attached = True
		except Exception:
			pass

	def _get_mouse_state(self, interactive: bool = False) -> tuple[float, float, bool]:
		'''Read pointer position and button once per frame; a hidden aiming cursor is never authorized.'''
		cached = self._mouse_cache.get(bool(interactive))
		if cached is not None:
			return cached
		if self._mouse_snapshot is None:
			self._mouse_snapshot = self._read_mouse_state(True)
		result = self._mouse_snapshot
		if not self.edit_mode_active() and not self._cursor_visible and not self._free_cursor:
			result = (-1.0, -1.0, result[2])
		self._mouse_cache[bool(interactive)] = result
		return result

	def _read_mouse_state(self, interactive: bool = False) -> tuple[float, float, bool]:
		'''Read the Win32 pointer state, falling back to ImGui io when unavailable.'''
		try:
			user32 = _ctypes.windll.user32
			cursor_info = _CURSORINFO()
			cursor_info.cbSize = _ctypes.sizeof(_CURSORINFO)
			self._cursor_visible = bool(user32.GetCursorInfo(_ctypes.byref(cursor_info)) and cursor_info.flags & 1)
			lbutton_down = bool(user32.GetAsyncKeyState(0x01) & 0x8000)
			cursor_point = _wintypes.POINT()
			if user32.GetCursorPos(_ctypes.byref(cursor_point)):
				if self._overlay_hwnd and user32.IsWindow(self._overlay_hwnd):
					user32.ScreenToClient(self._overlay_hwnd, _ctypes.byref(cursor_point))
				return float(cursor_point.x), float(cursor_point.y), lbutton_down
			return -1.0, -1.0, lbutton_down
		except Exception:
			pass

		try:
			input_state = imgui.get_io()
			self._cursor_visible = bool(input_state.want_capture_mouse or self.edit_mode_active())
			if input_state.want_capture_mouse or input_state.mouse_down[0] or interactive:
				return float(input_state.mouse_pos.x), float(input_state.mouse_pos.y), bool(input_state.mouse_down[0])
		except Exception:
			pass

		return -1.0, -1.0, False

	def _screen_size(self) -> tuple[int, int]:
		'''Overlay client size for keeping widgets on-screen.'''
		if self._overlay_hwnd:
			try:
				rect = _wintypes.RECT()
				if _ctypes.windll.user32.GetClientRect(self._overlay_hwnd, _ctypes.byref(rect)):
					return max(1, rect.right), max(1, rect.bottom)
			except Exception:
				pass
		return 1920, 1080

	def _clamp_to_screen(self, x: float, y: float, w: float, h: float) -> tuple[float, float, float, float]:
		'''Clamp a widget rect so it stays fully inside the overlay client.'''
		screen_w, screen_h = self._screen_size()
		width = min(max(1.0, w), float(screen_w))
		height = min(max(1.0, h), float(screen_h))
		clamped_x = min(max(0.0, x), max(0.0, screen_w - width))
		clamped_y = min(max(0.0, y), max(0.0, screen_h - height))
		return clamped_x, clamped_y, width, height

	def get_pos(self, widget_id: str, default_x: float, default_y: float, screen_w: int, screen_h: int) -> tuple[float, float]:
		"""Get current (x, y) coordinates for a widget, clamping to viewport."""
		if widget_id in self._runtime_pos:
			runtime_x, runtime_y = self._runtime_pos[widget_id]
			est_w, est_h = self.get_size(widget_id, 260.0, 120.0)
			return (max(0.0, min(float(screen_w - est_w), runtime_x)), max(0.0, min(float(screen_h - est_h), runtime_y)))

		hud_positions = getattr(self.config, "hud_positions", None)
		if hud_positions is not None:
			saved = getattr(hud_positions, widget_id, None)
			if saved is not None and isinstance(saved, (list, tuple)) and len(saved) >= 2:
				saved_x = float(saved[0])
				saved_y = float(saved[1])
				if widget_id == "fps_counter" and saved_x == -2.0:
					# Right anchoring follows the current width, so do not cache an absolute default X
					est_h = self.get_size(widget_id, 260.0, 120.0)[1]
					actual_y = screen_h + saved_y if saved_y < 0 else saved_y
					return (max(0.0, min(float(screen_w), default_x)), max(0.0, min(float(screen_h - est_h), actual_y)))
				if saved_x == -1.0:
					actual_x = max(0.0, (screen_w - 270.0) * 0.5)
				elif saved_x < 0:
					actual_x = screen_w + saved_x
				else:
					actual_x = saved_x

				if saved_y < 0:
					actual_y = screen_h + saved_y
				else:
					actual_y = saved_y
				if saved_x < 0 and saved_x != -1.0:
					pos = (max(0.0, min(float(screen_w - 40), actual_x)), max(0.0, min(float(screen_h - 20), actual_y)))
				else:
					est_w, est_h = self.get_size(widget_id, 260.0, 120.0)
					pos = (max(0.0, min(float(screen_w - est_w), actual_x)), max(0.0, min(float(screen_h - est_h), actual_y)))
				if widget_id != "event_feed" or saved_y >= 0:
					self._runtime_pos[widget_id] = pos
				return pos

		return default_x, default_y

	def has_saved_position(self, widget_id: str) -> bool:
		"""True when the user has dragged this widget to a custom position."""
		hud_positions = getattr(self.config, "hud_positions", None)
		if hud_positions is None:
			return False
		saved = getattr(hud_positions, widget_id, None)
		return isinstance(saved, (list, tuple)) and len(saved) >= 2

	def get_size(self, widget_id: str, default_w: float, default_h: float) -> tuple[float, float]:
		"""Get persisted (w, h) dimensions for a widget, or defaults."""
		if widget_id in self._runtime_size:
			return self._runtime_size[widget_id]

		hud_positions = getattr(self.config, "hud_positions", None)
		if hud_positions is not None:
			saved = getattr(hud_positions, f"{widget_id}_size", None)
			if saved is not None and isinstance(saved, (list, tuple)) and len(saved) >= 2:
				try:
					width = max(1.0, float(saved[0]))
					height = max(1.0, float(saved[1]))
					self._runtime_size[widget_id] = (width, height)
					return width, height
				except (ValueError, TypeError):
					pass
		return default_w, default_h

	def set_max_bounds(self, max_w: float, max_h: float) -> None:
		'''Ceiling the caller allows for the widget it is about to handle.'''
		self._max_w = float(max_w or 0)
		self._max_h = float(max_h or 0)

	def event_reference_edge(self, reference_y: float, reference_h: float, screen_h: int) -> str:
		'''Keep automatic alignment independent of notification count and freeze it during a gesture.'''
		state = self._states.get("event_feed")
		if state is not None and (state.is_dragging or state.is_resizing):
			return state.reference_edge
		if "event_feed" not in self._reference_edges:
			saved = getattr(getattr(self.config, "hud_positions", None), "event_feed_anchor", "")
			self._reference_edges["event_feed"] = saved if saved in ("top", "bottom") else ("top" if reference_y + reference_h * 0.5 < screen_h * 0.5 else "bottom")
		elif self._reference_screen_h and self._reference_screen_h != screen_h:
			self._reference_edges["event_feed"] = "top" if reference_y + reference_h * 0.5 < screen_h * 0.5 else "bottom"
		self._reference_screen_h = screen_h
		return self._reference_edges["event_feed"]

	def handle_interaction(
		self,
		widget_id: str,
		x: float,
		y: float,
		width: float,
		height: float,
		interactive: bool = True,
		draw_list=None,
		label: str = "",
		min_w: float = 120.0,
		min_h: float = 24.0,
		*,
		reference_size: tuple[float, float] | None = None,
		reference_edge: str = "bottom",
		preserve_aspect: bool = False,
	) -> tuple[float, float, float, float, bool]:
		"""Return live bounds; reference_size decouples changing row counts, while preserve_aspect scales content-driven geometry."""
		state = self._states.setdefault(widget_id, WidgetInteractionState())
		x, y, width, height = self._clamp_to_screen(x, y, width, height)
		if not self._input_enabled:
			self._cancel_interactions()
			return x, y, width, height, False
		editable = self.editing_allowed(interactive)
		if not editable:
			self._cancel_interactions()
			return x, y, width, height, False
		mouse_x, mouse_y, mouse_down = self._get_mouse_state(interactive=interactive)
		self._frame_down = self._frame_down or mouse_down
		if not mouse_down:
			self._press_blocked = False
		if mouse_x < 0 or mouse_y < 0:
			self._cancel_interactions()
			return x, y, width, height, False
		if any(left <= mouse_x <= left + span_w and top <= mouse_y <= top + span_h for left, top, span_w, span_h in self._excluded_rects):
			self._cancel_interactions()
			return x, y, width, height, False
		reference_w, reference_h = reference_size if reference_size is not None else (width, height)
		reference_w, reference_h = float(reference_w), float(reference_h)
		if state.is_dragging or state.is_resizing:
			reference_edge = state.reference_edge
		press_edge = mouse_down and not self._prev_down
		# A mouse-bound feature may add a row on the same press that selects its old grip
		hit_x, hit_y, hit_w, hit_h = self._last_bounds.get(widget_id, (x, y, width, height)) if preserve_aspect and press_edge else (x, y, width, height)

		grip = 9.0
		grip_hit = 13.0
		edges = {
			"nw": (hit_x, hit_y),
			"n": (hit_x + hit_w * 0.5, hit_y),
			"ne": (hit_x + hit_w, hit_y),
			"w": (hit_x, hit_y + hit_h * 0.5),
			"e": (hit_x + hit_w, hit_y + hit_h * 0.5),
			"sw": (hit_x, hit_y + hit_h),
			"s": (hit_x + hit_w * 0.5, hit_y + hit_h),
			"se": (hit_x + hit_w, hit_y + hit_h),
		}
		hovered_grip = ""
		hovered_distance = float("inf")
		for name, (grip_x, grip_y) in edges.items():
			distance = (mouse_x - grip_x) ** 2 + (mouse_y - grip_y) ** 2
			if editable and abs(mouse_x - grip_x) <= grip_hit and abs(mouse_y - grip_y) <= grip_hit and distance < hovered_distance:
				hovered_grip = name
				hovered_distance = distance
		in_widget_rect = (hit_x <= mouse_x <= hit_x + hit_w) and (hit_y <= mouse_y <= hit_y + hit_h)

		changed = False

		if editable and (in_widget_rect or hovered_grip or state.is_dragging or state.is_resizing):
			self._hovered_this_frame.add(widget_id)

		tx0, ty0, tx1, ty1 = self._toolbar_rect
		on_toolbar = tx0 <= mouse_x <= tx1 and ty0 <= mouse_y <= ty1
		can_claim = editable and press_edge and not self._press_blocked and not self._claimed and not on_toolbar and not self.is_interacting()
		if can_claim and not state.is_dragging and not state.is_resizing:
			self._start_gesture(
				state=state, widget_id=widget_id, mouse_x=mouse_x, mouse_y=mouse_y, x=x, y=y, height=height,
				reference_w=reference_w, reference_h=reference_h, reference_edge=reference_edge, hovered_grip=hovered_grip,
				in_widget_rect=in_widget_rect, hit_x=hit_x, hit_y=hit_y, hit_w=hit_w, hit_h=hit_h)

		if state.is_resizing:
			x, y, width, height, reference_w, reference_h, changed = self._update_resize(
				state=state, widget_id=widget_id, x=x, y=y, width=width, height=height, reference_w=reference_w,
				reference_h=reference_h, reference_edge=reference_edge, reference_size=reference_size, min_w=min_w, min_h=min_h,
				preserve_aspect=preserve_aspect, mouse_x=mouse_x, mouse_y=mouse_y, mouse_down=mouse_down)
		elif state.is_dragging:
			x, y, width, height, changed = self._update_drag(
				state=state, widget_id=widget_id, x=x, current_y=y, width=width, height=height, reference_w=reference_w,
				reference_h=reference_h, reference_edge=reference_edge, reference_size=reference_size, mouse_x=mouse_x,
				mouse_y=mouse_y, mouse_down=mouse_down)

		if draw_list is not None:
			selected = editable and not self._selection_cancelled and self._selected_widget == widget_id
			active = state.is_dragging or state.is_resizing
			if selected or active or hovered_grip or in_widget_rect:
				accent = 0xFFB57BFF if (selected or active) else 0x88FFFFFF
				# Paint selection after opaque widget contents so the frame remains visible
				self._pending_frames.append(
					(
						draw_list,
						x,
						y,
						width,
						height,
						accent,
						edges,
						grip,
						hovered_grip,
						active,
						label or widget_id,
						float(getattr(self, "_max_w", 0) or 0),
						float(getattr(self, "_max_h", 0) or 0),
						not (selected or active),
					)
				)

		self._last_bounds[widget_id] = (x, y, width, height)
		return x, y, width, height, changed

	def _start_gesture(self, *, state, widget_id, mouse_x, mouse_y, x, y, height, reference_w, reference_h, reference_edge,
		hovered_grip, in_widget_rect, hit_x, hit_y, hit_w, hit_h) -> None:
		'''Select this widget on a fresh press and begin a grip resize or body drag.'''
		if hovered_grip or in_widget_rect:
			state.layout_changed = False
			state.position_changed = False
			state.reference_orig_w = reference_w
			state.reference_orig_h = reference_h
			state.reference_start_x = x
			state.reference_start_y = y if reference_edge == "top" else y + height - reference_h
			state.reference_edge = reference_edge
			state.start_x = mouse_x
			state.start_y = mouse_y
			self._selection_revision += 1
			self._selection_ready = False
			self._selection_cancelled = False
		if hovered_grip:
			self._claimed = widget_id
			state.is_resizing = True
			state.resize_edge = hovered_grip
			state.offset_x = hit_x
			state.offset_y = hit_y
			state.start_x = mouse_x
			state.start_y = mouse_y
			state.resize_orig_w = hit_w
			state.resize_orig_h = hit_h
			self._selected_widget = widget_id
		elif in_widget_rect:
			self._claimed = widget_id
			state.is_dragging = True
			state.offset_x = mouse_x - x
			state.offset_y = mouse_y - y
			self._selected_widget = widget_id

	def _update_resize(self, *, state, widget_id, x, y, width, height, reference_w, reference_h, reference_edge, reference_size,
		min_w, min_h, preserve_aspect, mouse_x, mouse_y, mouse_down):
		'''Apply an active resize gesture and persist the reference box on release.'''
		changed = False
		if mouse_down:
			delta_x = mouse_x - state.start_x
			delta_y = mouse_y - state.start_y
			edge = state.resize_edge or "se"
			new_w, new_h = state.resize_orig_w, state.resize_orig_h
			new_x, new_y = state.offset_x, state.offset_y
			if abs(delta_x) > 0.5 or abs(delta_y) > 0.5:
				if "e" in edge:
					new_w = max(min_w, state.resize_orig_w + delta_x)
				if "w" in edge:
					new_w = max(min_w, state.resize_orig_w - delta_x)
					new_x = state.offset_x + (state.resize_orig_w - new_w)
				if "s" in edge:
					new_h = max(min_h, state.resize_orig_h + delta_y)
				if "n" in edge:
					new_h = max(min_h, state.resize_orig_h - delta_y)
					new_y = state.offset_y + (state.resize_orig_h - new_h)
				if preserve_aspect:
					ratios = []
					if "e" in edge or "w" in edge:
						ratios.append(new_w / max(1.0, state.resize_orig_w))
					if "n" in edge or "s" in edge:
						ratios.append(new_h / max(1.0, state.resize_orig_h))
					ratio = max(ratios, key=lambda value: abs(value - 1.0))
					ratio = max(min_w / max(1.0, state.resize_orig_w), min_h / max(1.0, state.resize_orig_h), ratio)
					if self._max_w > 0:
						ratio = min(ratio, self._max_w / max(1.0, state.resize_orig_w))
					if self._max_h > 0:
						ratio = min(ratio, self._max_h / max(1.0, state.resize_orig_h))
					grid = 8.0 if (self.snap_enabled and not imgui.get_io().key_ctrl) else 1.0
					target_reference_w = round(state.reference_orig_w * ratio / grid) * grid
					target_reference_h = state.reference_orig_h * target_reference_w / max(1.0, state.reference_orig_w)
					live_ratio = target_reference_w / max(1.0, reference_w)
					new_w = width * live_ratio
					new_h = height * live_ratio
					new_x = state.offset_x + (state.resize_orig_w - new_w) if "w" in edge else state.offset_x
					new_y = state.offset_y + (state.resize_orig_h - new_h) if "n" in edge else state.offset_y
			physical_motion = abs(delta_x) > 0.5 or abs(delta_y) > 0.5
			bounds_changed = abs(new_w - width) > 0.5 or abs(new_h - height) > 0.5 or abs(new_x - x) > 0.5 or abs(new_y - y) > 0.5
			if physical_motion and bounds_changed:
				grid = 8.0 if (self.snap_enabled and not imgui.get_io().key_ctrl) else 1.0
				new_x = round(new_x / grid) * grid
				new_y = round(new_y / grid) * grid
				if not preserve_aspect:
					new_w = round(new_w / grid) * grid
					new_h = round(new_h / grid) * grid
				previous_x, previous_y = x, y
				x, y, width, height = self._clamp_to_screen(new_x, new_y, new_w, new_h)
				if reference_size is not None:
					if preserve_aspect:
						reference_w, reference_h = target_reference_w, target_reference_h
					else:
						reference_w = state.reference_orig_w * width / max(1.0, state.resize_orig_w)
						reference_h = state.reference_orig_h * height / max(1.0, state.resize_orig_h)
				else:
					reference_w, reference_h = width, height
				reference_y = y if reference_edge == "top" else y + height - reference_h
				if reference_size is not None:
					position_changed = abs(x - state.reference_start_x) > 0.5 or abs(reference_y - state.reference_start_y) > 0.5
				else:
					position_changed = abs(x - previous_x) > 0.5 or abs(y - previous_y) > 0.5
				state.position_changed = state.position_changed or position_changed
				saved = getattr(getattr(self.config, "hud_positions", None), widget_id, None)
				keep_anchor = widget_id == "event_feed" and isinstance(saved, (tuple, list)) and len(saved) >= 2 and float(saved[1]) < 0
				if not keep_anchor or state.position_changed:
					self._runtime_pos[widget_id] = (x, reference_y)
				else:
					self._runtime_pos.pop(widget_id, None)
				self._runtime_size[widget_id] = (reference_w, reference_h)
				state.layout_changed = True
				changed = True
		else:
			state.is_resizing = False
			state.resize_edge = ""
			self._selection_ready = True
			if state.layout_changed:
				reference_w, reference_h = self._runtime_size.get(widget_id, (reference_w, reference_h))
				reference_y = y if reference_edge == "top" else y + height - reference_h
				saved = getattr(getattr(self.config, "hud_positions", None), widget_id, None)
				if widget_id == "event_feed" and not state.position_changed and isinstance(saved, (tuple, list)) and len(saved) >= 2 and float(saved[1]) < 0:
					reference_y = float(saved[1])
					self._runtime_pos.pop(widget_id, None)
				else:
					self._runtime_pos[widget_id] = (x, reference_y)
					if widget_id == "event_feed":
						new_edge = "top" if reference_y + reference_h * 0.5 < self._screen_size()[1] * 0.5 else "bottom"
						if new_edge != reference_edge:
							reference_y = y if new_edge == "top" else y + height - reference_h
						self._reference_edges[widget_id] = new_edge
						self._runtime_pos[widget_id] = (x, reference_y)
				self._save_layout(widget_id, x, reference_y, reference_w, reference_h)
		return x, y, width, height, reference_w, reference_h, changed

	def _update_drag(self, *, state, widget_id, x, current_y, width, height, reference_w, reference_h, reference_edge, reference_size,
		mouse_x, mouse_y, mouse_down):
		'''Apply an active drag gesture and persist the reference position on release.'''
		changed = False
		if mouse_down:
			if reference_size is not None:
				new_x = state.reference_start_x + mouse_x - state.start_x
				new_reference_y = state.reference_start_y + mouse_y - state.start_y
				new_y = new_reference_y if reference_edge == "top" else new_reference_y + reference_h - height
			else:
				new_x = mouse_x - state.offset_x
				new_y = mouse_y - state.offset_y
			physical_motion = abs(mouse_x - state.start_x) > 0.5 or abs(mouse_y - state.start_y) > 0.5
			if physical_motion and (abs(new_x - x) > 0.5 or abs(new_y - current_y) > 0.5):
				grid = 8.0 if (self.snap_enabled and not imgui.get_io().key_ctrl) else 1.0
				new_x = round(new_x / grid) * grid
				if reference_size is not None:
					new_reference_y = round(new_reference_y / grid) * grid
					x, reference_y, _, _ = self._clamp_to_screen(new_x, new_reference_y, reference_w, reference_h)
					current_y = reference_y if reference_edge == "top" else reference_y + reference_h - height
				else:
					new_y = round(new_y / grid) * grid
					x, current_y, _, _ = self._clamp_to_screen(new_x, new_y, width, height)
				self._runtime_pos[widget_id] = (x, current_y if reference_edge == "top" else current_y + height - reference_h)
				state.layout_changed = True
				state.position_changed = True
				changed = True
		else:
			state.is_dragging = False
			self._selection_ready = True
			if state.layout_changed:
				reference_y = current_y if reference_edge == "top" else current_y + height - reference_h
				if widget_id == "event_feed":
					new_edge = "top" if reference_y + reference_h * 0.5 < self._screen_size()[1] * 0.5 else "bottom"
					if new_edge != reference_edge:
						reference_y = current_y if new_edge == "top" else current_y + height - reference_h
					self._reference_edges[widget_id] = new_edge
				self._runtime_pos[widget_id] = (x, reference_y)
				self._save_layout(widget_id, x, reference_y, reference_w, reference_h)
		return x, current_y, width, height, changed

	def _draw_selection_frame(
		self, draw_list, x: float, y: float, width: float, height: float, accent: int, edges: dict, grip: float, hovered_grip: str,
		active: bool, label: str, preview_only: bool = False
	) -> None:
		'''Use a translucent hover hint before selection; selected components keep their resize controls.'''
		if not self.editing_allowed(True):
			return
		if preview_only:
			draw_list.add_rect_filled((x - 4.0, y - 4.0), (x + width + 4.0, y + height + 4.0), 0x127CA8C4, 7.0)
			draw_list.add_rect((x - 4.0, y - 4.0), (x + width + 4.0, y + height + 4.0), 0x887CA8C4, rounding=7.0, thickness=1.2, flags=0)
			if self.editing_allowed(True):
				for grip_x, grip_y in edges.values():
					draw_list.add_rect_filled((grip_x - 2.5, grip_y - 2.5), (grip_x + 2.5, grip_y + 2.5), 0x887CA8C4, 1.5)
			return
		dash = 6.0
		corners = [
			((x - 4.0, y - 4.0), (x + width + 4.0, y - 4.0)),
			((x + width + 4.0, y - 4.0), (x + width + 4.0, y + height + 4.0)),
			((x + width + 4.0, y + height + 4.0), (x - 4.0, y + height + 4.0)),
			((x - 4.0, y + height + 4.0), (x - 4.0, y - 4.0)),
		]
		for corner_start, corner_end in corners:
			span = max(1.0, abs(corner_end[0] - corner_start[0]) + abs(corner_end[1] - corner_start[1]))
			steps = max(1, int(span / (dash * 2.0)))
			for dash_index in range(steps):
				fraction_start = (dash_index * 2.0 * dash) / span
				fraction_end = min(1.0, (dash_index * 2.0 * dash + dash) / span)
				dash_start = (corner_start[0] + (corner_end[0] - corner_start[0]) * fraction_start, corner_start[1] + (corner_end[1] - corner_start[1]) * fraction_start)
				dash_end = (corner_start[0] + (corner_end[0] - corner_start[0]) * fraction_end, corner_start[1] + (corner_end[1] - corner_start[1]) * fraction_end)
				draw_list.add_line(dash_start, dash_end, accent, 1.6)

		max_w = float(getattr(self, "_max_w", 0) or 0)
		max_h = float(getattr(self, "_max_h", 0) or 0)
		if max_w > width + 1.0 or max_h > height + 1.0:
			draw_list.add_rect((x, y), (x + max_w, y + max_h), 0x55B57BFF, rounding=6.0, thickness=1.0, flags=0)

		for name, (grip_x, grip_y) in edges.items():
			highlighted = (hovered_grip == name) or active
			draw_list.add_rect_filled((grip_x - grip * 0.5, grip_y - grip * 0.5), (grip_x + grip * 0.5, grip_y + grip * 0.5),
				0xFFFFFFFF if highlighted else 0xE0FFFFFF, 2.0)
			draw_list.add_rect((grip_x - grip * 0.5, grip_y - grip * 0.5), (grip_x + grip * 0.5, grip_y + grip * 0.5), 0xFFB57BFF, rounding=2.0, thickness=1.4, flags=0)

		tag = f"{label}  {int(width)}\u00d7{int(height)}"
		pushed = push_sized(getattr(get_fonts_for_current_context(), "body", None), 11.5)
		try:
			text_bounds = text_size(tag)
			chip_w = text_bounds.x + 16.0
			chip_h = 20.0
			chip_x = x - 4.0
			chip_y = max(4.0, y - chip_h - 12.0)
			draw_list.add_rect_filled((chip_x, chip_y), (chip_x + chip_w, chip_y + chip_h), 0xF20E0E12, 7.0)
			draw_list.add_rect((chip_x, chip_y), (chip_x + chip_w, chip_y + chip_h), accent, rounding=7.0, thickness=1.0, flags=0)
			draw_list.add_text((chip_x + 8.0, chip_y + (chip_h - text_bounds.y) * 0.5), 0xFFEDEDEF, tag)
		finally:
			pop_font(pushed)

	def handle_drag(self, widget_id: str, x: float, y: float, width: float, height: float, interactive: bool = True) -> tuple[float, float, bool]:
		'''Backward-compatible drag handle.'''
		new_x, new_y, _, _, changed = self.handle_interaction(widget_id=widget_id, x=x, y=y, width=width, height=height, interactive=interactive)
		return new_x, new_y, changed

	def reset_layout(self) -> None:
		'''Restore every widget to its built-in default position and size.'''
		self._cancel_interactions()
		self._runtime_pos.clear()
		self._runtime_size.clear()
		self._last_bounds.clear()
		self._states.clear()
		self._selected_widget = ""
		self._reference_edges.clear()
		self._reference_screen_h = 0
		hud_positions = getattr(self.config, "hud_positions", None)
		if hud_positions is not None:
			try:
				data = getattr(hud_positions, "_data", None)
				for widget_id in _EDITOR_WIDGETS:
					for key in (widget_id, f"{widget_id}_size"):
						if isinstance(data, dict):
							data[key] = []
						else:
							setattr(hud_positions, key, [])
				if isinstance(data, dict):
					data["event_feed_anchor"] = ""
				else:
					setattr(hud_positions, "event_feed_anchor", "")
				if hasattr(self.config, "save"):
					self.config.save()
			except Exception as e:
				print(f"[hud_manager] reset_layout failed: {e!r}")

	def finish_editing(self) -> None:
		'''Exit without changing the edit lock or discarding previously committed layout changes.'''
		self._cancel_interactions()
		self._selected_widget = ""
		self._claimed = "@toolbar"
		self._toolbar_rect = (0.0, 0.0, 0.0, 0.0)
		self._toolbar_buttons.clear()
		event_visuals = getattr(self.config, "event_visuals", None)
		if event_visuals is not None:
			event_visuals.edit_mode = False

	def prepare_edit_toolbar(self, screen_w: int) -> None:
		'''Resolve toolbar ownership and actions before any HUD component can claim the same press.'''
		self._toolbar_buttons.clear()
		if not self.edit_mode_active() or not self._input_enabled:
			self._toolbar_rect = (0.0, 0.0, 0.0, 0.0)
			self._toolbar_pressed = ""
			return
		snap_txt = "Snap 8px grid" if self.snap_enabled else "Snap off"
		pushed = push_sized(getattr(get_fonts_for_current_context(), "body", None), 13.0)
		try:
			label_width = text_size(self._toolbar_label).x
			buttons = [(text, text_size(text).x + 18.0, action) for text, action in ((snap_txt, "snap"), ("Reset", "reset"), ("Done", "done"))]
			total_w = 54.0 + label_width + sum(width for _text, width, _action in buttons) + 12.0
			toolbar_x = max(0.0, (screen_w - total_w) * 0.5)
			self._toolbar_rect = (toolbar_x, 18.0, toolbar_x + total_w, 52.0)
			button_x = toolbar_x + 36.0 + label_width
			for text, width, action in buttons:
				self._toolbar_buttons.append((text, button_x, 23.0, width, 24.0, action))
				button_x += width + 6.0
		finally:
			pop_font(pushed)
		mouse_x, mouse_y, mouse_down = self._get_mouse_state(interactive=True)
		self._frame_down = self._frame_down or mouse_down
		if any(left <= mouse_x <= left + width and top <= mouse_y <= top + height for left, top, width, height in self._excluded_rects):
			self._cancel_interactions()
			return
		left, top, right, bottom = self._toolbar_rect
		self._toolbar_hover = left <= mouse_x <= right and top <= mouse_y <= bottom
		hovered = next((action for _text, left, top, width, height, action in self._toolbar_buttons
			if left <= mouse_x <= left + width and top <= mouse_y <= top + height), "")
		if mouse_down and not self._prev_down and self._toolbar_hover and not self._press_blocked and not self._claimed:
			self._claimed = "@toolbar"
			self._toolbar_pressed = hovered
		if not mouse_down and self._toolbar_pressed:
			action, self._toolbar_pressed = self._toolbar_pressed, ""
			if self._claimed != "@toolbar" or hovered != action:
				return
			if action == "snap":
				self.snap_enabled = not self.snap_enabled
			elif action == "reset":
				self.reset_layout()
			elif action == "done":
				self.finish_editing()

	def draw_edit_toolbar(self, draw_list, screen_w: int) -> None:
		'''Draw the toolbar after component chrome; input was already resolved before rendering.'''
		if not self.edit_mode_active() or not self._input_enabled or not self._toolbar_buttons:
			return
		left, top, right, bottom = self._toolbar_rect
		mouse_x, mouse_y, _down = self._get_mouse_state(interactive=True)
		pushed = push_sized(getattr(get_fonts_for_current_context(), "body", None), 13.0)
		try:
			draw_list.add_rect_filled((left, top), (right, bottom), 0xE60C0F14, 9.0)
			draw_list.add_rect((left, top), (right, bottom), 0x887CA8C4, rounding=9.0, thickness=1.2, flags=0)
			draw_list.add_circle_filled((left + 14.0, 35.0), 3.0, 0xFF7CA8C4, 10)
			label_height = text_size(self._toolbar_label).y
			draw_list.add_text((left + 22.0, 35.0 - label_height * 0.5), 0xFFF0F4F8, self._toolbar_label)
			for text, button_x, button_y, btn_w, btn_h, action in self._toolbar_buttons:
				active = action == "snap" and self.snap_enabled
				hover = button_x <= mouse_x <= button_x + btn_w and button_y <= mouse_y <= button_y + btn_h
				if active or hover:
					fill = 0x407CA8C4 if active else 0x2A7CA8C4
					border = 0xFF7CA8C4
				else:
					fill = 0x22FFFFFF
					border = 0x55FFFFFF
				draw_list.add_rect_filled((button_x, button_y), (button_x + btn_w, button_y + btn_h), fill, 6.0)
				draw_list.add_rect((button_x, button_y), (button_x + btn_w, button_y + btn_h), border, rounding=6.0, thickness=1.0, flags=0)
				text_bounds = text_size(text)
				draw_list.add_text((button_x + 9.0, button_y + (btn_h - text_bounds.y) * 0.5), 0xFFF0F4F8 if active else 0xFFE6EDF3, text)
		finally:
			pop_font(pushed)

	def _save_layout(self, widget_id: str, x: float, y: float, w: float, h: float) -> None:
		'''Persist coordinates and dimensions in config.'''
		hud_positions = getattr(self.config, "hud_positions", None)
		if hud_positions is not None:
			try:
				setattr(hud_positions, widget_id, [int(round(x)), int(round(y))])
				setattr(hud_positions, f"{widget_id}_size", [int(round(w)), int(round(h))])
				if widget_id == "event_feed" and widget_id in self._reference_edges:
					data = getattr(hud_positions, "_data", None)
					if isinstance(data, dict):
						data["event_feed_anchor"] = self._reference_edges[widget_id]
					else:
						setattr(hud_positions, "event_feed_anchor", self._reference_edges[widget_id])
				if hasattr(self.config, "save"):
					self.config.save()
			except Exception as e:
				print(f"[hud_manager] save failed: {e}")
