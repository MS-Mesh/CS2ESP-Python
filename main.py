"""
CS2 External ESP runtime entry point. Normal launch owns attach/re-attach, feature wiring, overlay/menu orchestration, status IPC and the frame loop;
--qt-menu starts only the PySide6 menu process before GLFW/imgui are imported. Feature logic stays inside cs2esp modules.
"""

import ctypes
import ctypes.wintypes as wt
import math
import os
import sys
from random import uniform
from time import perf_counter, sleep

from cs2esp.crash_logging import configure_crash_logging


configure_crash_logging("menu" if "--qt-menu" in sys.argv else "overlay")

try:
	ctypes.windll.winmm.timeBeginPeriod(1)
except Exception:
	pass

# Configure PyOpenGL before its first import
try:
	import OpenGL
	OpenGL.ERROR_CHECKING = bool(os.environ.get("CS2_GL_DEBUG"))
	OpenGL.ERROR_LOGGING = False
except Exception:
	pass

from cs2esp.ui.hotkeys import KeybindManager, ToggleHotkeyDetector, is_feature_active, is_key_pressed

# Keep Qt startup before glfw/imgui context creation
if "--qt-menu" in sys.argv:
	from cs2esp.ui.qt.menu_app import run
	raise SystemExit(run())

# Set DPI awareness before any window is painted
from cs2esp.display_info import (
	clamp_target_fps, dpi_scale_for_hwnd, enum_monitors, get_display_refresh_hz, is_process_dpi_aware, make_process_dpi_aware, monitor_for_hwnd)
if make_process_dpi_aware():
	print("[main] Process marked per-monitor DPI-aware.")
else:
	print("[main] WARNING: could not set DPI awareness — overlay may " "render blank/black on high-DPI or duplicate-monitor setups.")

# Select the OpenGL library before GLFW, PyOpenGL or imgui loads it
_startup_render_device_key = "auto"
_gpu_selection_warning = None
try:
	from cs2esp.config import DEFAULT_CONFIG as _CFG_PATH
	from cs2esp import gpu_devices as _gpu_devices
	_startup_render_device_key = _gpu_devices.read_configured_key(_CFG_PATH)
	_gpu_devices.apply_render_device(_startup_render_device_key)
	_gpu_selection_warning = getattr(_gpu_devices, "SELECTION_WARNING", None)
except Exception as _gpu_exc:
	_gpu_selection_warning = f"Render-device setup failed: {_gpu_exc}"
	print(f"[gpu] render-device setup skipped: {_gpu_exc!r}")

import glfw
from imgui_bundle import imgui


def _gpu_colorkey_needed() -> bool:
	'''Return True when the GPU device setup requires colorkey transparency.'''
	try:
		from cs2esp import gpu_devices
		return bool(gpu_devices.FORCE_COLORKEY)
	except Exception:
		return False


from cs2esp.config import Config
from cs2esp.memory import MemoryReader
from cs2esp.offsets import (CGlowProperty, Netvars, Offsets, OFFSETS_CACHE_PATH, apply_sigscan, load_offsets_cache,
	export_offsets_dict, import_offsets_dict, _live_offsets_readable, verified_offsets_cache_receipt,
	save_offsets_cache, update_offsets, verify_netvars, _verify_bone_array)
from cs2esp.entity import EntitySystem
from cs2esp.visuals.skeleton import draw_skeleton, project_players
from cs2esp.state_reader import GameStateReader
from cs2esp.recoil_runtime import HeldWeapon, RecoilRuntime
from cs2esp.input_guard import right_button_guard
from cs2esp.features.recoil_control import RecoilControl
from cs2esp.visuals.math_utils import world_to_screen
from cs2esp.visuals.overlay import Overlay
from cs2esp.visuals.menu_window import MenuWindow
from cs2esp.visuals.esp import draw_crosshair, draw_player_esp
from cs2esp.visuals.radar import Radar
from cs2esp.visuals.overlays import Awareness
from cs2esp.visuals.fps_counter import draw_fps_counter
from cs2esp.features.glow import GlowSystem
from cs2esp.visuals.chams import chams_begin_frame, chams_context_recreated, chams_end_frame, chams_shutdown, draw_chams
from cs2esp.visuals.vpk_chams import renderer as vpk_chams
from cs2esp.visuals.chams_pixel_probe import probe_center_pixel as chams_probe_center_pixel
from cs2esp.features.event_system import EventSystem, EventType
from cs2esp.features.combat_events import CombatEventTracker
from cs2esp.features.combat_telemetry import CombatTelemetry
from cs2esp.visuals.hud_manager import HudPositionManager
from cs2esp.visuals.event_feed import render_event_feed
from cs2esp.visuals.c4_capsule import render_c4_capsule
from cs2esp.visuals.spectator_list import render_spectator_list
from cs2esp.visuals.hotkeys_widget import render_hotkeys_widget
from cs2esp.visuals.status_pills import render_status_pills
from cs2esp.visuals.theme import color_u32
from cs2esp.ui.fonts import set_font_context


from cs2esp.features.triggerbot_vanilla import Triggerbot
from cs2esp.features.triggerbot_feedback import FeedbackTriggerbot
from cs2esp.features.aimbot import WPMAimbot
from cs2esp.features.autoclick import AutoPistol, RapidFire
from cs2esp.features.bhop import BHop
from cs2esp.ui.menu import Menu
from cs2esp.ui.qt_launcher import QtMenuController
from cs2esp.win32_utils import (create_single_instance_mutex, find_window_by_pid,
	focus_window, get_client_rect, is_window_minimized, is_window_valid, set_exclusion_region, set_noactivate, windows_covering, user32)

from cs2esp.ipc import StatusPublisher, read_offset_refresh_request, drain_control, send_control, write_instance, write_offset_ack, write_status


def _anti_detect_value(anti_detect, key: str, default):
	'''Read optional [anti_detection] keys safely.'''
	try:
		return getattr(anti_detect, key)
	except Exception:
		return default


_RENDER_PROFILE_READER_HZ = {"efficient": 90.0, "balanced": 75.0, "low_latency": 120.0, "energy_saving": 60.0}
_GLOW_ADVANCED_DEFAULTS = {"writer_burst": 1, "writer_sleep_ms": 10.0, "write_color": True, "write_type_block": True,
	"write_eligible": True, "write_glowing": True, "clear_on_drop": True, "debug_log": False}


def _active_render_profile(overlay_config, active_device_key: str) -> str:
	'''Resolve the running device's profile, independently of pending device changes.'''
	default = "energy_saving" if active_device_key == "cpu" else "efficient"
	if overlay_config is None:
		return default
	profiles = getattr(overlay_config, "render_profiles", None)
	data = profiles if isinstance(profiles, dict) else getattr(profiles, "_data", {})
	selected = data.get(active_device_key) if isinstance(data, dict) else None
	if selected is None and str(getattr(overlay_config, "render_device", "auto")) == active_device_key:
		selected = getattr(overlay_config, "render_profile", default)
	selected = str(selected or default)
	return selected if selected in _RENDER_PROFILE_READER_HZ else default


def _hud_editor_input_context(overlay, game_hwnd: int, qt_menu=None, menu_window=None, *, enabled: bool) -> tuple[bool, tuple]:
	'''Authorize HUD input for owned windows and exclude covered client-space rectangles.'''
	if not enabled or not game_hwnd or not getattr(overlay, 'hwnd', 0):
		return False, ()
	try:
		foreground = int(user32.GetForegroundWindow() or 0)
		menu_hwnd = int(qt_menu._locate_hwnd() or 0) if qt_menu is not None else 0
		external_hwnd = int(getattr(menu_window, 'hwnd', 0) or 0)
		trusted = {int(game_hwnd), int(overlay.hwnd)}
		trusted.update(handle for handle in (menu_hwnd, external_hwnd) if handle)
		ancestor = user32.GetAncestor
		ancestor.argtypes = [wt.HWND, wt.UINT]
		ancestor.restype = wt.HWND
		root_owner = int(ancestor(wt.HWND(foreground), 3) or foreground)
		trusted_roots = {int(ancestor(wt.HWND(handle), 3) or handle) for handle in trusted}
		if foreground not in trusted and root_owner not in trusted_roots:
			return False, ()
		covered = list(windows_covering(game_hwnd, skip_hwnds=(overlay.hwnd,)))
		for handle in (menu_hwnd, external_hwnd):
			if handle and user32.IsWindowVisible(wt.HWND(handle)):
				rectangle = get_client_rect(handle)
				if rectangle:
					covered.append(rectangle)
		origin_x, origin_y = overlay._monitor_rect[:2]
		return True, tuple((left - origin_x, top - origin_y, width, height) for left, top, width, height in covered if width > 0 and height > 0)
	except Exception:
		return False, ()


def _precise_sleep(duration: float) -> None:
	'''Sleep with a spin tail limited to the last 0.3 ms; the old 1.5 ms tail burned ~18% of a core at 120 fps.'''
	if duration <= 0:
		return
	deadline = perf_counter() + duration
	chunk = duration - 0.0003
	if chunk > 0:
		sleep(chunk)
	while perf_counter() < deadline:
		pass


def _filter_pawns(pawns: list, predicate, *predicate_args) -> list:
	'''Return the entries whose predicate is true, preserving order.'''
	filtered = []
	for entry in pawns:
		if predicate(entry, *predicate_args):
			filtered.append(entry)
	return filtered


def _within_fov(entry, local_pos, view_dir, min_dot) -> bool:
	'''Return True when the pawn centre is within the FOV cone; a near-zero distance is kept.'''
	pawn_position = entry[2]
	delta_x = pawn_position[0] - local_pos[0]
	delta_y = pawn_position[1] - local_pos[1]
	delta_z = pawn_position[2] - local_pos[2]
	distance = math.sqrt(delta_x*delta_x + delta_y*delta_y + delta_z*delta_z)
	if distance < 1e-3:
		return True
	inverse_distance = 1.0 / distance
	dot = (view_dir[0] * delta_x * inverse_distance + view_dir[1] * delta_y * inverse_distance + view_dir[2] * delta_z * inverse_distance)
	return dot >= min_dot


def _near_crosshair(entry, view_matrix, screen_w, screen_h, screen_center_x, screen_center_y, crosshair_radius_sq) -> bool:
	'''Return True when the projected pawn centre falls inside the crosshair radius.'''
	screen_pos = world_to_screen(entry[2], view_matrix, screen_w, screen_h)
	if screen_pos is None:
		return False
	screen_delta_x = screen_pos[0] - screen_center_x
	screen_delta_y = screen_pos[1] - screen_center_y
	return screen_delta_x * screen_delta_x + screen_delta_y * screen_delta_y <= crosshair_radius_sq


def _on_screen(entry, view_matrix, screen_w, screen_h) -> bool:
	'''Return True when the projected pawn centre falls inside the screen bounds.'''
	screen_pos = world_to_screen(entry[2], view_matrix, screen_w, screen_h)
	if screen_pos is None:
		return False
	return 0.0 <= screen_pos[0] <= screen_w and 0.0 <= screen_pos[1] <= screen_h


def main() -> int:
	'''Run the overlay until exit and return the process exit code.'''
	# Defer cyclic collection while rendering; idle frames still collect and reference counting stays active
	import gc
	gc.collect()
	gc.freeze()
	gc.set_threshold(100_000, 1000, 1000)
	mutex_name = os.environ.get("CS2ESP_MUTEX")
	mutex_handle, already_running = (create_single_instance_mutex(mutex_name) if mutex_name else create_single_instance_mutex())
	if already_running:
		# Second launch = "open the menu", not a second overlay
		send_control("show_menu")
		print("[main] CS2 ESP is already running — asked it to show the menu. Exiting duplicate.")
		return 0
	write_instance(os.getpid())
	drain_control()

	print("[main] CS2 ESP \u2014 research build")
	config = Config.load()
	launch_render_device = _startup_render_device_key
	try:
		_bind_list = []
		for _name, _section in (
			("Triggerbot", getattr(config, "triggerbot", None)), ("Aimbot", getattr(config, "aimbot", None)), ("Rapid-fire", getattr(config, "rapidfire", None)),
			("Auto-pistol", getattr(config, "autopistol", None)), ("Recoil control", getattr(config, "recoil_control", None)),
			("Bunny Hop", getattr(config, "bhop", None)), ("Wallhack", getattr(config, "wallhack", None)), ("Radar", getattr(config, "radar", None)),
			("Awareness", getattr(config, "awareness", None)),
			("FPS counter", getattr(config, "fps_counter", None)), ("Snapline", getattr(getattr(config, "visuals", None), "snapline", None)),
			("Crosshair", getattr(getattr(config, "visuals", None), "crosshair", None)), ("Names", getattr(getattr(config, "visuals", None), "name", None))):
			if _section is None:
				continue
			for _attr, _mode in (("hotkey", "hold"), ("hotkey_toggle", "toggle")):
				_key = str(getattr(_section, _attr, "") or "").strip().lower()
				if _key:
					_bind_list.append((_key, f"{_name} {_mode}"))
		_by_key = {}
		for _key, _label in _bind_list:
			_by_key.setdefault(_key, []).append(_label)
		for _key, _labels in sorted(_by_key.items()):
			if len(_labels) > 1:
				print(f"[binds] duplicate key {_key!r}: {', '.join(_labels)} " f"(hold and toggle on the same key interact)")
	except Exception:
		pass
	if (sys.platform == "win32" and bool(getattr(config.general, "hide_terminal", True))):
		try:
			kernel32 = ctypes.windll.kernel32
			console_processes = (ctypes.c_ulong * 4)()
			attached = int(kernel32.GetConsoleProcessList(console_processes, 4))
			if attached > 1:
				print(f"[main] hide_terminal=true but console shared with " f"{attached - 1} other process(es); leaving it visible.")
			elif attached == 1:
				console_hwnd = kernel32.GetConsoleWindow()
				if console_hwnd:
					ctypes.windll.user32.ShowWindow(console_hwnd, 0)
		except Exception as error:
			print(f"[main] hide_terminal failed: {error!r}")
	anti_detect = config.anti_detection

	try:
		if bool(getattr(anti_detect, "module_spoof_enabled", False)):
			from cs2esp.anti_detect import spoof_python_module_name, module_spoof_supported
			if module_spoof_supported():
				masquerade_name = getattr(anti_detect, "module_spoof_masquerade_name", None)
				unlink_original = bool(getattr(anti_detect, "module_spoof_unlink_from_peb", True))
				spoofed_module_name = spoof_python_module_name(masquerade_name=masquerade_name, unlink_original=unlink_original)
				if spoofed_module_name:
					print(f"[anti-det] interpreter loader display path aliased as {spoofed_module_name!r}; loader basename preserved.")
				else:
					print("[anti-det] interpreter display-path alias was not applied; runtime identity remains unchanged.")
	except Exception as error:
		print(f"[anti-det] module-spoof raised: {error!r}")

	if not load_offsets_cache():
		update_offsets()
	else:
		try:
			update_offsets()
		except Exception:
			pass
	# Save refreshed offsets before runtime validation can reload an older cache
	try:
		save_offsets_cache()
	except Exception as exc:
		print(f"[offsets] startup cache save failed: {exc!r}")

	qt_menu: QtMenuController | None = None
	if bool(getattr(config.general, "qt_menu_enabled", True)):
		qt_menu = QtMenuController()
		if bool(getattr(config.general, "start_menu_visible", True)):
			if not qt_menu.spawn():
				print("[main] Qt menu subprocess could not start \u2014 " "falling back to imgui in-overlay menu.")
				qt_menu = None
		else:
			print("[main] start_menu_visible=false \u2014 Qt menu will " "spawn on first toggle-hotkey press.")
	else:
		print("[main] Qt menu disabled in config; using imgui menu.")

	mem = MemoryReader()
	attached = mem.attach(quiet=True)
	if not attached:
		print("[main] Waiting for cs2.exe \u2014 launch the game any time. " "The menu stays open during the wait.")
		last_status_at = 0.0
		log_attach_failure = True
		exit_key = str(getattr(config.general, "exit_key", "ctrl+l"))
		menu_toggle_key = str(getattr(config.general, "toggle_menu_key", "ctrl+j"))
		master_toggle_key = str(getattr(config.general, "master_toggle_key", "ctrl+k"))
		last_menu_toggle_at = 0.0
		last_master_toggle_at = 0.0
		while not attached:
			attached = mem.attach(quiet=not log_attach_failure)
			if attached:
				break
			log_attach_failure = False

			try:
				if is_key_pressed(exit_key):
					print(f"[main] Exit hotkey ({exit_key}) pressed during " "the CS2 wait. Quitting.")
					break
			except Exception:
				pass

			now = perf_counter()
			try:
				if qt_menu is not None and is_key_pressed(menu_toggle_key) and now - last_menu_toggle_at > 0.4:
					qt_menu.toggle()
					last_menu_toggle_at = now
			except Exception:
				pass
			try:
				if (is_key_pressed(master_toggle_key) and now - last_master_toggle_at > 0.4):
					was_enabled = bool(getattr(config.general, "master_enabled", True))
					config.general.master_enabled = not was_enabled
					last_master_toggle_at = now
			except Exception:
				pass

			quit_requested = False
			for command in drain_control():
				action = command.get("action")
				if action == "quit":
					quit_requested = True
				elif action == "master":
					config.general.master_enabled = bool(command.get("value", True))
				elif action == "show_menu" and qt_menu is not None:
					if not qt_menu.is_alive():
						qt_menu.spawn()
			if quit_requested:
				print("[main] Quit requested from the menu during the CS2 wait.")
				break

			if qt_menu is not None:
				try:
					qt_menu.tick_focus()
				except Exception:
					pass

			if now - last_status_at >= 0.25:
				try:
					write_status(fps=None, cs2_attached=False, master_enabled=bool(getattr(config.general, "master_enabled", True)),
								 render_device_requested=launch_render_device, render_device_warning=_gpu_selection_warning)
				except Exception:
					pass
				last_status_at = now
			sleep(0.2)

	if not attached:
		if qt_menu is not None:
			try:
				qt_menu.terminate()
			except Exception:
				pass
		return 0

	apply_sigscan(mem.pm)
	fixed_netvars = verify_netvars(mem)
	if fixed_netvars:
		print(f"[main] Auto-corrected {fixed_netvars} netvars from live memory.")

	try:
		entity_list_addr = mem.read_long(mem.client(Offsets.dwEntityList))
		controller_addr = mem.read_long(mem.client(Offsets.dwLocalPlayerController))
		if not entity_list_addr and not controller_addr:
			print("[main] WARNING: dwEntityList and dwLocalPlayerController "
				  "both read NULL after dumper + sigscan. Offsets are stale " "for this CS2 build and the sigscan patterns did not match. "
				  "ESP/aim/trigger will be idle until offsets update. " "(If you're at the main menu this is expected — join a map.)")
	except Exception:
		pass

	try:
		if bool(getattr(anti_detect, "read_obfuscator_enabled", False)):
			from cs2esp.anti_detect import install_read_obfuscator
			bogus_reads_per_real = int(getattr(anti_detect, "read_obfuscator_bogus_per_real", 4))
			install_read_obfuscator(mem, bogus_per_real=bogus_reads_per_real)
			print(f"[anti-det] read-pattern obfuscator installed " f"(bogus_per_real={bogus_reads_per_real})")
	except Exception as error:
		print(f"[anti-det] read-obfuscator raised: {error!r}")

	cs2_pid = getattr(mem.pm, "process_id", 0) if mem.pm else 0
	cs2_hwnd = find_window_by_pid(cs2_pid) if cs2_pid else 0

	# Log displays, DPI scale and the client rect to tell clipping apart from projection or adapter faults
	try:
		monitors = enum_monitors()
		print(f"[display] DPI-aware={is_process_dpi_aware()} " f"scale={dpi_scale_for_hwnd(cs2_hwnd):.2f} " f"monitors={len(monitors)}")
		for index, monitor in enumerate(monitors):
			tag = " [primary]" if monitor.is_primary else ""
			print(f"[display]   #{index}: {monitor.rect}{tag}")
		cs2_monitor = monitor_for_hwnd(cs2_hwnd)
		if cs2_monitor is not None:
			print(f"[display] CS2 (HWND 0x{cs2_hwnd:X}) is on " f"monitor {cs2_monitor.rect}")
		else:
			print("[display] CS2 monitor unresolved — overlay will cover " "the largest / primary display.")
		client = get_client_rect(cs2_hwnd)
		if client is not None:
			print(f"[display] CS2 client rect (overlay target) = {client}")
		else:
			print("[display] CS2 client rect unresolved — falling back to " "monitor rect for overlay sizing.")
	except Exception as exc:
		print(f"[display] monitor dump failed: {exc!r}")

	overlay = Overlay(title=None, randomize_title=_anti_detect_value(anti_detect, "randomize_window_title", False),
		hide_from_taskbar=_anti_detect_value(anti_detect, "hide_from_taskbar", True))
	overlay_vsync = bool(getattr(getattr(config, "overlay", None), "vsync", False))
	if not overlay.create(target_hwnd=cs2_hwnd, colorkey_fallback=bool(getattr(getattr(config, "overlay", None), "colorkey_fallback", False)
												 or _gpu_colorkey_needed()), vsync=overlay_vsync):
		return 1
	overlay.bring_to_top()

	capture_protect_requested = bool(_anti_detect_value(anti_detect, "hide_overlay_from_capture", False))
	capture_applied = overlay.set_capture_protect(capture_protect_requested)
	last_capture_reapply_t = perf_counter()
	if capture_protect_requested or not capture_applied:
		print(f'[anti-det] Overlay capture-affinity request ({"enable" if capture_protect_requested else "disable"}): '
			f'{"applied" if capture_applied else "failed; retry pending"}.')

	entities = EntitySystem(mem)
	state_reader = GameStateReader(entities)
	glow = GlowSystem(mem)
	trigger_vanilla = Triggerbot(mem, entities, config)
	trigger_feedback = FeedbackTriggerbot(mem, entities, config)
	aimbot = WPMAimbot(config, mem)
	rapidfire = RapidFire(config)
	autopistol = AutoPistol(config, mem)
	bhop = BHop(mem, entities, config)
	recoil_control = RecoilControl(mem, config)
	recoil_runtime = RecoilRuntime(mem, entities)
	radar = Radar(config)
	awareness = Awareness(config)
	hud_manager = HudPositionManager(config)
	if overlay and overlay.hwnd:
		hud_manager.set_overlay_hwnd(overlay.hwnd)
	from cs2esp.visuals.edit_backdrop import EditBackdrop
	edit_backdrop = EditBackdrop()

	from cs2esp.metrics import RealMetrics
	real_metrics = RealMetrics()

	from cs2esp.features.event_sounds import EventSounds
	event_sounds = EventSounds(config)
	event_sounds.attach()
	menu = Menu(config, overlay, cs2_hwnd=0)
	keys = KeybindManager()

	def _emit_toggle_event(label: str, key: str, on: bool) -> None:
		'''Publish a toggle flip to the shared event feed.'''
		EventSystem.get().emit(EventType.HOTKEY_TOGGLED,
			title=label.upper(), detail=f"{'ON' if on else 'OFF'} · {key.upper()}", duration=1.8, data={"slot": f"bind:{label}", "active": on, "mode": "toggle"})

	toggle_keys = ToggleHotkeyDetector(on_flip=_emit_toggle_event)
	menu_window: MenuWindow | None = None
	menu_focus_budget: int = 0


	if qt_menu is not None:
		menu.visible = False
		try:
			overlay.set_click_through(True)
		except Exception:
			pass
	else:
		menu.visible = bool(config.general.start_menu_visible)
		try:
			overlay.set_click_through(not menu.visible or bool(getattr(config.general, "menu_external_window", False)))
		except Exception:
			pass

	menu.cs2_hwnd = cs2_hwnd
	try:
		overlay.set_target_hwnd(int(cs2_hwnd or 0))
	except Exception:
		pass
	if getattr(config.general, "focus_cs2_on_launch", True) and cs2_hwnd:
		if focus_window(cs2_hwnd):
			print(f"[main] Focused cs2.exe (HWND 0x{cs2_hwnd:X}).")
		else:
			print("[main] Could not auto-focus CS2 \u2014 click the game window once.")
		overlay.bring_to_top()

	if cs2_hwnd:
		try:
			window_rect = wt.RECT()
			user32.GetWindowRect(ctypes.c_void_p(cs2_hwnd), ctypes.byref(window_rect))
			screen_width = user32.GetSystemMetrics(0)
			screen_height = user32.GetSystemMetrics(1)
			covers_screen = (window_rect.left <= 0
				and window_rect.top <= 0 and (window_rect.right - window_rect.left) >= screen_width and (window_rect.bottom - window_rect.top) >= screen_height)
			GWL_STYLE = -16
			WS_BORDER = 0x00800000
			WS_THICKFRAME = 0x00040000
			style = user32.GetWindowLongW(ctypes.c_void_p(cs2_hwnd), GWL_STYLE)
			chromeless = not (style & (WS_BORDER | WS_THICKFRAME))
			if covers_screen and chromeless:
				print("[main] WARNING: CS2 looks like EXCLUSIVE FULLSCREEN.")
				print("[main] Switch CS2 to 'Fullscreen Windowed' (Borderless),")
				print("[main] otherwise CS2 will go BLACK whenever you click the menu.")
		except Exception:
			pass

	def _release_synthetic_inputs():
		'''Release owned input and discard partial tap bursts and aim paths on master/focus loss.'''
		aimbot.reset()
		for clicker in (rapidfire, autopistol):
			try:
				clicker.release()
			except Exception:
				pass
		try:
			bhop.release()
		except Exception:
			pass

	status_publisher: StatusPublisher | None = None

	def _apply_master(enabled: bool, source: str):
		'''Set master, release synthetic input on OFF and publish the status.'''
		enabled = bool(enabled)
		was_enabled = bool(getattr(config.general, "master_enabled", True))
		if enabled != was_enabled:
			config.general.master_enabled = enabled
		if not enabled:
			_release_synthetic_inputs()
			recoil_control.reset()
			try:
				glow.clear_all()
			except Exception:
				pass
			state_reader.request(enabled=False, bones=False, slab=False, health=False)
			state_reader.clear()
		try:
			publish_status = (status_publisher.publish if status_publisher is not None else write_status)
			publish_status(fps=None, cs2_attached=mem.is_process_alive(), master_enabled=enabled, cs2_monitor=overlay._monitor_rect,
						 renderer=gl_renderer_name, render_device_requested=launch_render_device, render_device_warning=_gpu_selection_warning)
		except Exception:
			pass
		print(f"[main] Master = {'ON' if enabled else 'OFF'} ({source})")

	def _toggle_master():
		'''Flip master from the hotkey.'''
		try:
			_apply_master(not bool(getattr(config.general, "master_enabled", True)), "hotkey")
		except Exception as error:
			print(f"[main] master toggle failed: {error!r}")


	try:
		menu.set_master_status_provider(lambda: bool(getattr(config.general, "master_enabled", True)))
	except Exception:
		pass

	last_status_write_at = 0.0
	last_control_poll_at = 0.0
	last_gc_at = perf_counter()
	frame_stats = [] if os.environ.get("CS2ESP_FRAME_STATS") else None
	frame_stats_at = perf_counter()
	pipeline_trace = None
	trace_path = os.environ.get("CS2ESP_PIPELINE_TRACE")
	if trace_path:
		from cs2esp.pipeline_trace import PipelineTrace
		pipeline_trace = PipelineTrace(trace_path)
	diagnostics_enabled = frame_stats is not None or pipeline_trace is not None
	late_visuals_enabled = os.environ.get("CS2ESP_LATE_VISUALS", "1") == "1"
	print(f"[perf] early HUD schedule={late_visuals_enabled}; interactive modes retain original order")
	frame_id = 0
	previous_paced_dt = None
	gl_renderer_name: str | None = None
	last_master_seen = bool(getattr(config.general, "master_enabled", True))
	status_write_interval = 0.25
	frame_times: list[float] = []
	displayed_fps: float | None = None
	last_reattach_attempt_at: float = 0.0
	reattach_interval = 1.5

	display_hz = get_display_refresh_hz(int(overlay.hwnd or 0))
	raw_target = int(getattr(config.general, "target_fps", 144) or 144)
	target_fps = clamp_target_fps(raw_target, int(overlay.hwnd or 0))
	if target_fps != raw_target:
		print(f"[main] Clamping target_fps {raw_target} -> {target_fps} " f"(display reports {display_hz:.2f} Hz)")
		try:
			config.general.target_fps = target_fps
		except Exception:
			pass
	else:
		print(f"[main] target_fps = {target_fps} " f"(display {display_hz:.2f} Hz)")
	frame_dt = 1.0 / max(30, target_fps)
	active_render_profile = ""
	last_frame_started_at = None

	print(f"[main] Running. {config.general.toggle_menu_key.upper()} = menu, "
		  f"{config.general.master_toggle_key.upper()} = master toggle, " f"{config.general.exit_key.upper()} = exit.")

	if config.triggerbot.enabled:
		print(f"[main] Triggerbot ON (hotkey={config.triggerbot.hotkey!r}, " f"delay={config.triggerbot.delay_ms}\u00b1{config.triggerbot.delay_jitter_ms} ms).")
	if config.rapidfire.enabled:
		print(f"[main] Rapid-fire ON (hotkey={config.rapidfire.hotkey!r}, " f"{config.rapidfire.cps:.1f} cps \u00b1{int(config.rapidfire.jitter_pct*100)}%).")
	if config.autopistol.enabled:
		print(f"[main] Auto-pistol ON (hotkey={config.autopistol.hotkey!r}, " f"{config.autopistol.cps:.1f} cps).")
	if config.bhop.enabled:
		print(f"[main] BHop ON (hotkey={config.bhop.hotkey!r}, " f"min_gap={config.bhop.min_gap_ms} ms, humanize={config.bhop.humanize}).")

	taskbar_requested = bool(_anti_detect_value(anti_detect, "hide_from_taskbar", True))
	last_taskbar_attempt_t = 0.0
	read_only_active = bool(_anti_detect_value(anti_detect, "read_only_mode", False))

	bones_diag_logged = False
	glow_diag_at = 0.0
	spectator_diag_logged = False
	last_count_diag_at = 0.0
	netvar_reheal_done = False
	last_reheal_at = 0.0
	last_bone_heal_at = 0.0
	last_offset_cmd_ts = 0.0
	last_offset_cmd_poll = 0.0
	qt_menu_was_alive = False
	overlay_sync_warning_logged = False
	fps_counter_warning_logged = False
	last_topmost_at = 0.0
	last_occlusion_at = 0.0
	last_metrics_at = 0.0
	combat_events = CombatEventTracker(EventSystem.get())
	combat_telemetry = CombatTelemetry(mem, entities, EventSystem.get())
	spectator_names: list = []
	last_spec_poll_at = 0.0
	right_column_y = 140.0

	def _sync_hud_edit_mode() -> bool:
		'''Apply entry and exit immediately, including a toolbar action in the current frame.'''
		editing = hud_manager.edit_mode_active()
		if editing != getattr(overlay, "_edit_focus_on", False):
			overlay._edit_focus_on = editing
			try:
				set_noactivate(int(overlay.hwnd or 0), not editing)
				if editing:
					ctypes.windll.user32.ClipCursor(None)
					focus_window(int(overlay.hwnd or 0))
				elif cs2_hwnd:
					focus_window(int(cs2_hwnd))
			except Exception as error:
				print(f'[edit-mode] focus switch failed: {error!r}')
		if editing != getattr(overlay, "_edit_backdrop_on", False):
			if not editing:
				edit_backdrop.invalidate()
			overlay._edit_backdrop_on = editing
		return editing

	try:
		status_publisher = StatusPublisher()
		keys.bind(config.general.master_toggle_key, _toggle_master)
		while overlay.running:
			frame_started_at = perf_counter()
			if diagnostics_enabled:
				frame_id += 1
			frame_interval = (frame_started_at - last_frame_started_at if last_frame_started_at is not None else None)
			last_frame_started_at = frame_started_at
			snapshot_age_at_draw = None
			snapshot_selected_at = None
			sync_read_started_at = None
			sync_read_completed_at = None
			visual_refresh_started_at = None
			visual_refresh_at = None
			visual_refresh_to_return = None
			frame_player_count = 0
			frame_visible_player_count = 0
			mark_combat = None
			mark_esp = None

			if keys.just_pressed(config.general.toggle_menu_key):
				if qt_menu is not None:
					qt_menu.toggle(cs2_hwnd=int(cs2_hwnd or 0))
				else:
					menu.toggle()
			if keys.is_pressed(config.general.exit_key):
				break

			control_due = frame_started_at - last_control_poll_at >= 0.05
			if control_due:
				last_control_poll_at = frame_started_at
				quit_requested = False
				for command in drain_control():
					action = command.get("action")
					if action == "quit":
						quit_requested = True
					elif action == "master":
						_apply_master(bool(command.get("value", True)), "menu")
					elif action == "show_menu":
						if qt_menu is not None:
							if qt_menu.is_alive():
								qt_menu.toggle(cs2_hwnd=int(cs2_hwnd or 0))
							else:
								qt_menu.spawn()
						else:
							menu.toggle()
				if quit_requested:
					print("[main] Quit requested from the menu.")
					break

			if qt_menu is not None:
				if qt_menu.is_alive():
					qt_menu_was_alive = True
				elif qt_menu_was_alive:
					master_on = bool(getattr(config.general, "master_enabled", True))
					if not master_on:
						print("[main] Qt menu closed and master is OFF " "— exiting.")
						break
					print("[main] Qt menu closed; master overlay still "
						f"active ({config.general.toggle_menu_key.upper()} " "to respawn menu, " f"{config.general.master_toggle_key.upper()} " "to toggle master).")
					qt_menu_was_alive = False
				try:
					qt_menu.tick_focus()
				except Exception:
					pass

			try:
				# Poll configuration at 50 ms intervals; master changes also arrive over IPC
				if control_due and config.reload_if_changed():
					target_fps = int(getattr(config.general, "target_fps", target_fps) or target_fps)
					frame_dt = 1.0 / max(30, target_fps)
					new_vsync = bool(getattr(getattr(config, "overlay", None), "vsync", False))
					overlay.set_vsync(new_vsync)
			except Exception:
				pass

			if control_due:
				profile = _active_render_profile(getattr(config, "overlay", None), launch_render_device)
				if profile != active_render_profile:
					active_render_profile = profile
					state_reader.set_target_hz(_RENDER_PROFILE_READER_HZ[profile])
					print(f"[perf] render profile={profile}, reader={_RENDER_PROFILE_READER_HZ[profile]:.0f} Hz")

			try:
				if frame_started_at - last_offset_cmd_poll >= 0.5:
					last_offset_cmd_poll = frame_started_at
					offset_message = read_offset_refresh_request(last_offset_cmd_ts)
					offset_req = offset_message is not None
					if offset_message is not None:
						last_offset_cmd_ts = offset_message["ts"]
				else:
					offset_req = False
				if offset_req:
					print("[main] Live offset update requested via IPC — reloading...")
					from hashlib import sha256
					applied_cnt = 0
					fixed = 0
					loaded = saved = succeeded = False
					loaded_digest = final_digest = None
					refresh_error = None
					attached_now = bool(mem and mem.pm and mem.client_base)
					previous_offsets = export_offsets_dict()
					state_reader.clear()
					try:
						expected_digest = offset_message.get("cache_sha256")
						loaded = load_offsets_cache(expected_sha256=expected_digest)
						if not loaded:
							raise RuntimeError("cache missing, invalid, or changed since the update request")
						loaded_digest = expected_digest or sha256(OFFSETS_CACHE_PATH.read_bytes()).hexdigest()
						if attached_now:
							applied = apply_sigscan(mem.pm)
							applied_cnt = len(applied)
							fixed = verify_netvars(mem)
							if not _live_offsets_readable(mem):
								raise RuntimeError("runtime live global validation failed")
							print(f"[main] Live healed in-process: sigscan={len(applied)}, netvars={fixed}")
							saved = bool(save_offsets_cache())
							if not saved:
								raise RuntimeError("runtime could not persist healed offsets")
						else:
							saved = True
						if attached_now:
							receipt = verified_offsets_cache_receipt()
							if receipt is None:
								saved = False
								raise RuntimeError("runtime offset cache changed before saved-byte confirmation")
							final_digest = receipt["sha256"]
						else:
							final_digest = loaded_digest
							if sha256(OFFSETS_CACHE_PATH.read_bytes()).hexdigest() != final_digest:
								saved = False
								raise RuntimeError("offset cache changed before application confirmation")
						succeeded = True
					except Exception as exc:
						refresh_error = str(exc)
						import_offsets_dict(previous_offsets)
						print(f"[main] Offset refresh not applied: {exc!r}")
					finally:
						state_reader.clear()
					try:
						write_offset_ack(request_ts=last_offset_cmd_ts, applied=applied_cnt, netvars=fixed, attached=attached_now,
							success=succeeded, cache_loaded=loaded, cache_saved=saved, live_verified=True if succeeded and attached_now else None,
							applied_cache_sha256=loaded_digest, cache_sha256=final_digest, error=refresh_error)
					except Exception as exc:
						print(f"[main] offset ack failed: {exc!r}")
					if succeeded:
						EventSystem.get().emit(
							EventType.OFFSETS_UPDATED, title="OFFSETS UPDATED", detail=f"offsets refreshed via GUI ({applied_cnt} sigscans applied)")
					netvar_reheal_done = False
					bones_diag_logged = False

			except Exception:
				pass

			labels_toggle_key = getattr(config.visuals.name, "hotkey_toggle", "")
			toggle_keys.poll([
				(getattr(config.triggerbot, "hotkey_toggle", ""), config.triggerbot, "enabled", "Triggerbot"), (getattr(config.rapidfire, "hotkey_toggle", ""),
				 config.rapidfire, "enabled", "Rapid-fire"), (getattr(config.autopistol, "hotkey_toggle", ""), config.autopistol, "enabled", "Auto-pistol"),
				(getattr(config.bhop, "hotkey_toggle", ""), config.bhop, "enabled", "Bunny Hop"), (getattr(config.aimbot, "hotkey_toggle", ""),
				 config.aimbot, "enabled", "Aimbot"), (getattr(config.recoil_control, "hotkey_toggle", ""), config.recoil_control, "enabled", "Recoil control"),
				(getattr(config.radar, "hotkey_toggle", ""), config.radar, "enabled", "Radar"),
				(getattr(config.awareness, "hotkey_toggle", ""), config.awareness, "enabled", "Awareness"),
				(getattr(config.wallhack, "hotkey_toggle", ""), config.wallhack, "enabled", "Wallhack"), (getattr(config.visuals.snapline, "hotkey_toggle", ""),
				 config.visuals.snapline, "enabled", "Snapline"), (getattr(config.visuals.crosshair, "hotkey_toggle", ""),
				 config.visuals.crosshair, "enabled", "Crosshair"), (getattr(config.fps_counter, "hotkey_toggle", ""), config.fps_counter, "enabled", "FPS counter"),
				(labels_toggle_key, config.visuals.name, "enabled", "Names"), (labels_toggle_key, config.visuals.distance, "enabled", "Names")])

			cs2_alive = mem.is_process_alive()

			if not cs2_alive:
				recoil_runtime.reset()
				recoil_control.reset()
				reconnect_now = perf_counter()
				if reconnect_now - last_reattach_attempt_at >= reattach_interval:
					last_reattach_attempt_at = reconnect_now
					# Invalidate any in-flight walk before changing the process handle; a result begun for the old process must not be published
					state_reader.request(enabled=False, bones=False, slab=False, health=False)
					state_reader.clear()
					if mem.pm is not None:
						try:
							mem.detach()
						except Exception:
							pass
					try:
						if mem.attach(quiet=True):
							cs2_alive = True
							last_status_write_at = 0.0
					except Exception:
						pass

			master_enabled = bool(getattr(config.general, "master_enabled", True))
			if master_enabled != last_master_seen:
				# Apply the same release/reset path to hotkeys, menu commands and configuration changes
				if not master_enabled:
					_apply_master(False, "config")
				last_master_seen = master_enabled

			if cs2_alive and (not cs2_hwnd or not is_window_valid(cs2_hwnd)):
				cs2_pid = getattr(mem.pm, "process_id", 0) if mem.pm else 0
				new_hwnd = find_window_by_pid(cs2_pid) if cs2_pid else 0
				if new_hwnd and new_hwnd != cs2_hwnd:
					cs2_hwnd = new_hwnd
					try:
						overlay.set_target_hwnd(cs2_hwnd)
					except Exception:
						pass
				if cs2_hwnd and not getattr(overlay, "_cs2_focused_once", False):
					try:
						focus_window(cs2_hwnd)
						overlay._cs2_focused_once = True
					except Exception:
						pass

			cs2_window_ready = bool(cs2_hwnd and is_window_valid(cs2_hwnd))
			cs2_minimized = is_window_minimized(cs2_hwnd) if cs2_window_ready else False
			is_menu_open = bool((qt_menu is not None and qt_menu.is_visible) or (menu is not None and getattr(menu, "visible", False)))
			is_imgui_menu_open = bool(qt_menu is None and menu is not None and getattr(menu, "visible", False))

			# Overlay only over the active game; the Qt/imgui menu and HUD edit mode keep it alive so widgets stay reachable
			fg_hwnd = 0
			try:
				fg_hwnd = int(user32.GetForegroundWindow() or 0)
			except Exception:
				pass
			cs2_foreground = bool(cs2_hwnd and fg_hwnd == cs2_hwnd)
			hud_edit_mode = bool(getattr(getattr(config, "event_visuals", None), "edit_mode", False))

			if hud_edit_mode:
				try:
					if is_key_pressed("esc"):
						hud_manager.finish_editing()
						hud_edit_mode = False
				except Exception:
					pass

			hud_edit_mode = _sync_hud_edit_mode()
			# Clip unrelated windows without hiding the active menu or HUD editor
			should_show_overlay = bool(cs2_alive and not cs2_minimized and ((master_enabled and cs2_window_ready) or is_imgui_menu_open))

			# Synchronise window-local clipping after geometry changes
			try:
				context_before_sync = overlay.ctx
				overlay.sync_to_monitor()
				if overlay.ctx is not context_before_sync:
					chams_context_recreated()
					vpk_chams().invalidate_context()
					menu.invalidate_current_context_state()
					edit_backdrop.invalidate()
			except Exception as error:
				if not overlay_sync_warning_logged:
					print(f"[overlay] monitor sync failed: {error!r}")
					overlay_sync_warning_logged = True

			occlusion_signature = getattr(overlay, "_occlusion_sig", None)
			if should_show_overlay and (occlusion_signature is None or frame_started_at - last_occlusion_at >= 0.25):
				last_occlusion_at = frame_started_at
				try:
					holes = [] if hud_edit_mode else windows_covering(cs2_hwnd, skip_hwnds=(overlay.hwnd,))
					signature = (int(overlay.hwnd or 0), cs2_hwnd, overlay._monitor_rect, tuple(holes))
					if signature != occlusion_signature:
						if holes:
							applied = set_exclusion_region(overlay.hwnd, list(holes))
						else:
							applied = set_exclusion_region(overlay.hwnd, [])
						# A transient Win32 failure must not cache an unapplied region until a manual toggle
						if applied:
							overlay._occlusion_sig = signature
				except Exception:
					pass

			try:
				overlay_is_visible = bool(glfw.get_window_attrib(overlay.window, glfw.VISIBLE))
				if should_show_overlay and not overlay_is_visible:
					overlay.show()
				elif not should_show_overlay and overlay_is_visible:
					overlay.hide()
					overlay._occlusion_sig = None
			except Exception as error:
				print(f"[overlay] visibility sync failed: {error!r}")
			if should_show_overlay and frame_started_at - last_topmost_at >= 1.0:
				overlay.bring_to_top()
				last_topmost_at = frame_started_at

			screen_w, screen_h = overlay.width, overlay.height
			if overlay and overlay.hwnd:
				hud_manager.set_overlay_hwnd(overlay.hwnd)
			# Sample process and game metrics outside the per-frame hot path
			if frame_started_at - last_metrics_at >= 0.25:
				last_metrics_at = frame_started_at
				try:
					_pids = [os.getpid()]
					if qt_menu is not None:
						child = getattr(qt_menu, "_proc", None)
						if child is not None and child.pid:
							_pids.append(child.pid)
					real_metrics.set_pids(tuple(_pids))
					real_metrics.update(mem if cs2_alive else None)
				except Exception:
					pass

			if not cs2_foreground or not master_enabled or hud_edit_mode or bool(config.anti_detection.read_only_mode):
				right_button_guard.configure(config.aimbot, None, int(cs2_hwnd or 0), enabled=False)
			if overlay.begin_frame() is False:
				continue
			hud_manager.begin_frame()
			editor_enabled, editor_exclusions = _hud_editor_input_context(
				overlay, cs2_hwnd, qt_menu, menu_window, enabled=bool(should_show_overlay and master_enabled and cs2_window_ready))
			hud_manager.set_input_context(enabled=editor_enabled, excluded_rects=editor_exclusions, free_cursor=bool(is_menu_open and not cs2_foreground))
			hud_manager.prepare_edit_toolbar(screen_w)
			hud_edit_mode = _sync_hud_edit_mode()
			try:
				_ev_sys = EventSystem.get()
				_ev_cfg = getattr(config, "event_visuals", None)
				if _ev_cfg is not None:
					_ev_sys.default_duration = float(getattr(_ev_cfg, "event_duration_sec", 4.5) or 4.5)
			except Exception as error:
				print(f"[event_system] event duration setup failed: {error}")
			draw_list = overlay.get_draw_list()
			hud_prepared_early = False
			world_hud_channels = False
			hud_prepare_s = 0.0
			def render_hud_now():
				'''Draw the HUD for this frame and return the preparation time.'''
				nonlocal fps_counter_warning_logged, spectator_names, last_spec_poll_at, right_column_y
				hud_started_at = perf_counter()
				if should_show_overlay and master_enabled:
					if hud_edit_mode:
						blurred = False
						try:
							blurred = edit_backdrop.draw(draw_list, screen_w, screen_h, cs2_hwnd)
						except Exception:
							blurred = False
						draw_list.add_rect_filled((0, 0), (screen_w, screen_h), color_u32((0.02, 0.02, 0.03, 0.28 if blurred else 0.42)))
					try:
						if len(frame_times) >= 4:
							average_frame_time = sum(frame_times) / len(frame_times)
							frame_variance = sum((frame_time - average_frame_time) ** 2 for frame_time in frame_times) / len(frame_times)
							jitter_ms = int(round((frame_variance ** 0.5) * 1000.0))
						else:
							jitter_ms = None
					except Exception:
						jitter_ms = None
					ping_ms = real_metrics.game_ping_ms

					if not local_pawn:
						ping_ms = None
						jitter_ms = None

					menu_interactive = bool(is_menu_open)
					hud_interactive = bool(menu_interactive or hud_edit_mode)

					try:
						theme_active = getattr(config.general, "ui_theme", "slate")
						# PresentMon measures present-to-display delay, not input-to-photon latency or overlay processing time
						draw_fps_counter(draw_list, screen_w, screen_h, config.fps_counter, fps=real_metrics.fps,
							ping_ms=ping_ms, jitter_ms=jitter_ms, latency_ms=real_metrics.present_to_display_ms, cpu_percent=real_metrics.overlay_cpu_percent,
							sys_cpu_percent=real_metrics.cpu_percent, ram_mb=real_metrics.overlay_ram_mb, ram_pair=real_metrics.system_ram, hud_manager=hud_manager,
							interactive=hud_interactive, theme_name=theme_active, root_config=config, fps_source=real_metrics.fps_source, overlay_fps=displayed_fps)
					except Exception as error:
						if not fps_counter_warning_logged:
							print(f"[fps_counter] draw failed: {error!r}")
							fps_counter_warning_logged = True

					try:
						render_event_feed(
							draw_list=draw_list, screen_w=screen_w, screen_h=screen_h, config=config, hud_manager=hud_manager, interactive=hud_interactive)
					except Exception as error:
						print(f"[event_feed] render failed: {error!r}")

					try:
						render_c4_capsule(draw_list=draw_list, screen_w=screen_w, screen_h=screen_h, config=config, mem=mem if cs2_alive else None,
							local_pos=local_pos if local_pawn else None, local_armor=local_armor, hud_manager=hud_manager, interactive=hud_interactive)
					except Exception:
						pass

					try:
						# Keep Hotkeys above Spectators and clamp the right-anchored column to the screen
						if cs2_alive and local_pawn and (frame_started_at - last_spec_poll_at >= 0.5):
							last_spec_poll_at = frame_started_at
							spectator_names = entities.get_spectators_of_local()
						elif not (cs2_alive and local_pawn):
							spectator_names = []
						right_x = screen_w - 24.0
						hk_w, hk_h = render_hotkeys_widget(draw_list=draw_list, screen_w=screen_w,
							screen_h=screen_h, config=config, hud_manager=hud_manager, interactive=hud_interactive, anchor_right=right_x, anchor_y=right_column_y)
						spec_y = right_column_y + hk_h + (8.0 if hk_h else 0.0)
						spec_w, spec_h = render_spectator_list(draw_list=draw_list, screen_w=screen_w, screen_h=screen_h, config=config,
							current_spectators=spectator_names, hud_manager=hud_manager, interactive=hud_interactive, anchor_right=right_x, anchor_y=spec_y)
						if not hud_interactive:
							bottom = spec_y + spec_h if spec_h else right_column_y + hk_h
							available = screen_h - 24.0
							if bottom > available:
								right_column_y = max(24.0, right_column_y - (bottom - available))
					except Exception as error:
						print(f"[hud-column] render failed: {error!r}")

					try:
						render_status_pills(
							draw_list=draw_list, screen_w=screen_w, screen_h=screen_h, config=config, hud_manager=hud_manager, interactive=hud_interactive)
					except Exception as error:
						print(f"[status_pills] render failed: {error!r}")

					try:
						hud_manager.flush_selection()
					except Exception as error:
						print(f"[hud] selection flush failed: {error!r}")

					if hud_edit_mode:
						try:
							hud_manager.draw_edit_toolbar(draw_list, screen_w)
						except Exception as error:
							print(f"[edit-toolbar] render failed: {error!r}")

				# Resolve hit testing after every widget has registered its hover and capture state
				overlay.set_click_through(not hud_manager.wants_input())

				return perf_counter() - hud_started_at


			read_only = bool(_anti_detect_value(anti_detect, "read_only_mode", False))
			if read_only and not read_only_active:
				_release_synthetic_inputs()
			read_only_active = read_only
			wallhack_on = bool(getattr(config.wallhack, "enabled", True))
			vpk_chams_on = False

			view_matrix = None
			in_match = False
			if cs2_alive:
				try:
					view_matrix = mem.read_matrix(mem.client(Offsets.dwViewMatrix))
					in_match = bool(view_matrix)
				except Exception:
					view_matrix = None

			local_team = None
			local_pawn = None
			local_yaw = 0.0
			local_pos = None
			local_armor = 0

			held_weapon = HeldWeapon()
			if cs2_alive and in_match:
				held_weapon = recoil_runtime.sample(entities.get_local_pawn() or 0)
			else:
				recoil_runtime.reset()
				recoil_control.reset()
			if not master_enabled or not cs2_foreground:
				recoil_control.reset()
			right_button_guard.configure(config.aimbot, held_weapon, int(cs2_hwnd or 0),
				enabled=bool(master_enabled and cs2_foreground and not read_only and not hud_edit_mode))

			game_snapshot = None
			if not (in_match and master_enabled):
				state_reader.request(enabled=False, bones=False, slab=False, health=False)
				state_reader.clear()
				combat_events.reset()
				combat_telemetry.reset()
			if in_match and master_enabled:
				needs_weapons = bool(config.visuals.name.enabled and getattr(config.visuals.name, "weapon_enabled", False))
				needs_bones = (config.visuals.skeleton.enabled
							   or config.visuals.chams.enabled or is_feature_active(config.triggerbot) or is_feature_active(config.aimbot))
				vpk_chams_on = bool(config.visuals.chams.enabled and wallhack_on and str(getattr(config.visuals.chams, "mode", "vpk")) == "vpk")
				# Only the first snapshot requires a synchronous entity scan
				state_reader.request(enabled=True, bones=needs_bones, slab=vpk_chams_on, health=True, weapons=needs_weapons)
				game_snapshot = state_reader.latest()
				if game_snapshot is not None and (game_snapshot.players and needs_bones and vpk_chams_on and game_snapshot.players[0].bone_slab is None):
					game_snapshot = None
				if diagnostics_enabled and game_snapshot is not None:
					snapshot_selected_at = perf_counter()
				if game_snapshot is not None:
					local_team = game_snapshot.local_team
					local_pawn = game_snapshot.local_pawn
					local_yaw = game_snapshot.local_yaw
				else:
					local_team = entities.get_local_team()
					local_pawn = entities.get_local_pawn()
					local_yaw = entities.get_local_eye_yaw()

				if not netvar_reheal_done and local_pawn and perf_counter() - last_reheal_at > 1.0:
					state_reader.clear()
					try:
						fixed_netvars = verify_netvars(mem)
						if fixed_netvars:
							print(f"[main] match-join reheal corrected " f"{fixed_netvars} netvars from live memory.")
					except Exception as exc:
						print(f"[main] match-join reheal raised: {exc!r}")
					finally:
						state_reader.clear()
					game_snapshot = None
					netvar_reheal_done = True
					last_reheal_at = perf_counter()
					local_team = entities.get_local_team()
					local_pawn = entities.get_local_pawn()
					local_yaw = entities.get_local_eye_yaw()

				if game_snapshot is not None:
					players = game_snapshot.render_players()
				else:
					if diagnostics_enabled:
						sync_read_started_at = perf_counter()
						sync_read_completed_at = None
					players = entities.get_players(read_bones=needs_bones, read_bone_slab=vpk_chams_on, read_weapons=needs_weapons)
					if diagnostics_enabled:
						sync_read_completed_at = perf_counter()

				if (needs_bones and players and not any(candidate_player.bones for candidate_player in players) and perf_counter() - last_bone_heal_at > 2.0):
					last_bone_heal_at = perf_counter()
					state_reader.clear()
					try:
						if _verify_bone_array(mem):
							print("[main] bone-array healed from live memory — re-reading players.")
							if diagnostics_enabled:
								sync_read_started_at = perf_counter()
								sync_read_completed_at = None
							players = entities.get_players(read_bones=needs_bones, read_bone_slab=vpk_chams_on, read_weapons=needs_weapons)
							if diagnostics_enabled:
								sync_read_completed_at = perf_counter()
							game_snapshot = None
							local_team = entities.get_local_team()
							local_pawn = entities.get_local_pawn()
							local_yaw = entities.get_local_eye_yaw()
							if any(candidate_player.bones for candidate_player in players):
								save_offsets_cache()
					except Exception as exc:
						print(f"[main] bone-array heal raised: {exc!r}")
						game_snapshot = None
						try:
							if diagnostics_enabled:
								sync_read_started_at = perf_counter()
								sync_read_completed_at = None
							players = entities.get_players(read_bones=needs_bones, read_bone_slab=vpk_chams_on, read_weapons=needs_weapons)
							if diagnostics_enabled:
								sync_read_completed_at = perf_counter()
							local_team = entities.get_local_team()
							local_pawn = entities.get_local_pawn()
							local_yaw = entities.get_local_eye_yaw()
						except Exception as read_exc:
							players = []
							local_team, local_pawn, local_yaw = 0, 0, 0.0
							print(f"[main] bone-array reread failed: {read_exc!r}")
					finally:
						state_reader.clear()

				try:
					controller_addr = mem.read_long(mem.client(Offsets.dwLocalPlayerController))
					entity_list_addr = mem.read_long(mem.client(Offsets.dwEntityList))
					in_match = bool(controller_addr and entity_list_addr)
				except Exception:
					in_match = False

				if in_match and (not local_pawn or not players) and perf_counter() - last_reheal_at > 2.5:
					state_reader.clear()
					try:
						fixed_netvars = verify_netvars(mem)
						if fixed_netvars:
							print(f"[main] on-the-fly reheal corrected {fixed_netvars} netvars from live memory.")
							save_offsets_cache()
							if diagnostics_enabled:
								sync_read_started_at = perf_counter()
								sync_read_completed_at = None
							players = entities.get_players(read_bones=needs_bones, read_bone_slab=vpk_chams_on, read_weapons=needs_weapons)
							if diagnostics_enabled:
								sync_read_completed_at = perf_counter()
							game_snapshot = None
							local_team = entities.get_local_team()
							local_pawn = entities.get_local_pawn()
							local_yaw = entities.get_local_eye_yaw()
					except Exception as exc:
						print(f"[main] on-the-fly reheal raised: {exc!r}")
						game_snapshot = None
						try:
							if diagnostics_enabled:
								sync_read_started_at = perf_counter()
								sync_read_completed_at = None
							players = entities.get_players(read_bones=needs_bones, read_bone_slab=vpk_chams_on, read_weapons=needs_weapons)
							if diagnostics_enabled:
								sync_read_completed_at = perf_counter()
							local_team = entities.get_local_team()
							local_pawn = entities.get_local_pawn()
							local_yaw = entities.get_local_eye_yaw()
						except Exception as read_exc:
							players = []
							local_team, local_pawn, local_yaw = 0, 0, 0.0
							print(f"[main] on-the-fly reread failed: {read_exc!r}")
					finally:
						state_reader.clear()
					last_reheal_at = perf_counter()

				local_pos = (game_snapshot.local_pos if game_snapshot is not None else entities.get_scan_local_position())
				try:
					_ctrl = mem.read_long(mem.client(Offsets.dwLocalPlayerController))
					local_armor = mem.read_int(_ctrl + Netvars.m_iPawnArmor) if _ctrl else 0
				except Exception:
					local_armor = 0

				if not bones_diag_logged and players:
					sample = players[0]
					bone_count = len(sample.bones) if sample.bones else 0
					sample_bone = next(iter(sample.bones.values())) if sample.bones else None
					print(f"[diag] First player bones={bone_count} sample={sample_bone} " f"(skeleton.enabled={config.visuals.skeleton.enabled})")
					bones_diag_logged = True

				distance_cap = config.visuals.distance_cap
				visible = [player for player in players if player.distance <= distance_cap]
				frame_player_count = len(players)
				frame_visible_player_count = len(visible)
				writes_allowed = not read_only

				lbutton_down = bool(user32.GetAsyncKeyState(0x01) & 0x8000)
				if cs2_foreground and not hud_manager.is_interacting() and local_pawn and mem.read_byte(local_pawn + Netvars.m_lifeState) == 0:
					try:
						shots_fired = mem.read_int(local_pawn + Netvars.m_iShotsFired)
						fired_at = mem.read_float(local_pawn + Netvars.m_flLastFiredWeaponTime)
						bullet_services = mem.read_long(local_pawn + Netvars.m_pBulletServices)
						server_hits = mem.read_int(bullet_services + Netvars.m_totalHitsOnServer) if 0x10000 <= bullet_services <= 0x7FFFFFFFFFFF else None
						ent_index = mem.read_int(local_pawn + Netvars.m_iIDEntIndex)
						target_pawn = entities.entity_index_to_pawn(ent_index) if 0 < ent_index < 2048 else 0
						health_hint = game_snapshot.health if game_snapshot is not None else entities.get_health_snapshot(local_team)
						health, damage_bones, shot_context = combat_telemetry.capture(
							local_pawn, health_hint, players, local_pos, held_weapon, fired_at, shots_fired=shots_fired, target_pawn=target_pawn)
						combat_events.update(local_pawn, shots_fired, server_hits, health, target_pawn=target_pawn, snapshot_at=perf_counter(),
							ping_ms=real_metrics.game_ping_ms, fired_at=fired_at, damage_bones=damage_bones, shot_context=shot_context)
					except Exception as error:
						combat_events.reset()
						combat_telemetry.reset()
						print(f"[events] combat sample failed: {error!r}")
				else:
					combat_events.reset()
					combat_telemetry.reset()

				mark_combat = perf_counter()
				# Run combat updates against the latest snapshot
				# Only the foreground game may receive synthetic input
				if not read_only and cs2_foreground:
					if is_feature_active(config.triggerbot):
						if str(getattr(config.triggerbot, "version", "feedback")) == "vanilla":
							trigger_vanilla.update(players=players, local_pos=local_pos, view_matrix=view_matrix, screen_w=screen_w, screen_h=screen_h)
						else:
							trigger_feedback.update(players=players, local_pawn=local_pawn, local_team=local_team)
					rapid_active = is_feature_active(config.rapidfire)
					pistol_active = is_feature_active(config.autopistol)
					if rapid_active or pistol_active or rapidfire.has_pending_release() or autopistol.has_pending_release():
						if pistol_active and autopistol.accepts_weapon(held_weapon):
							rapidfire.release()
							autopistol.update(held_weapon=held_weapon)
						else:
							autopistol.release()
							rapidfire.update(held_weapon=held_weapon)
					if is_feature_active(config.bhop) or bhop.has_pending_resume():
						bhop.update()
				rcs_section = getattr(config, "recoil_control", None)
				recoil_control.update(local_pawn or 0, held_weapon=held_weapon,
					active=bool(not read_only and cs2_foreground and lbutton_down and rcs_section is not None and is_feature_active(rcs_section)))

				aimbot.update(players=players, local_pawn=local_pawn, local_team=local_team, local_pos=local_pos,
					active=bool(writes_allowed and cs2_foreground and is_feature_active(config.aimbot)))

				# Refresh the camera matrix immediately before projection
				try:
					fresh_vm = mem.read_matrix(mem.client(Offsets.dwViewMatrix))
					if fresh_vm:
						view_matrix = fresh_vm
				except Exception:
					pass

				now = perf_counter()
				if now - last_count_diag_at > 5.0:
					controller_addr = mem.read_long(mem.client(Offsets.dwLocalPlayerController))
					entity_list_addr = mem.read_long(mem.client(Offsets.dwEntityList))
					if controller_addr == 0 and entity_list_addr == 0:
						print("[diag] CS2 NOT IN MATCH " "(lobby / main menu / loading) \u2014 ESP idle.")
					else:
						enemies_count = sum(1 for player in players if player.team != local_team)
						teams_count = sum(1 for player in players if player.team == local_team)
						print(f"[diag] players: total={len(players)} " f"enemies={enemies_count} teammates={teams_count} "
							  f"local_team={local_team} visible<=cap={len(visible)} " f"controller=0x{controller_addr:X} " f"entity_list=0x{entity_list_addr:X}")
					last_count_diag_at = now

				writes_allowed = not read_only

				if config.glow.enabled and writes_allowed and wallhack_on:
					pawns = [(player.pawn_addr, player.team, player.position) for player in players if player.pawn_addr and player.is_alive]
					if not config.visuals.team_esp:
						pawns = [pawn_entry for pawn_entry in pawns if pawn_entry[1] != local_team]

					pawns_after_team = len(pawns)

					range_units = float(getattr(config.glow, "radius", 0.0) or 0.0)

					fov_deg = float(getattr(config.glow, "fov_deg", 0.0) or 0.0)
					xhair_px = float(getattr(config.glow, "crosshair_radius_px", 0.0) or 0.0)

					if fov_deg > 0.0 and pawns and local_pos:
						try:
							view_angles = mem.read_vec3(mem.client(Offsets.dwViewAngles))
							pitch_rad = math.radians(float(view_angles[0]))
							yaw_rad = math.radians(float(view_angles[1]))
							cos_pitch = math.cos(pitch_rad)
							sin_pitch = math.sin(pitch_rad)
							cos_yaw = math.cos(yaw_rad)
							sin_yaw = math.sin(yaw_rad)
							view_dir = (cos_pitch * cos_yaw, cos_pitch * sin_yaw, -sin_pitch)
							min_dot = math.cos(math.radians(fov_deg))
							pawns = _filter_pawns(pawns, _within_fov, local_pos, view_dir, min_dot)
						except Exception:
							pass

					if xhair_px > 0.0 and pawns and view_matrix is not None:
						screen_center_x = screen_w * 0.5
						screen_center_y = screen_h * 0.5
						crosshair_radius_sq = xhair_px * xhair_px
						pawns = _filter_pawns(pawns, _near_crosshair, view_matrix, screen_w, screen_h, screen_center_x, screen_center_y, crosshair_radius_sq)

					if int(config.glow.type) == CGlowProperty.VISIBLE_ONLY and pawns and view_matrix is not None:
						pawns = _filter_pawns(pawns, _on_screen, view_matrix, screen_w, screen_h)

					glow_debug = bool(getattr(getattr(config.glow, "advanced", None), "debug_log", False))
					if glow_debug and (now - glow_diag_at) >= 1.0:
						print(f"[diag] glow funnel: players={len(players)} "
							  f"alive_enemy={pawns_after_team} " f"after_filters={len(pawns)} type={config.glow.type} " f"fov={fov_deg:.0f} xhair={xhair_px:.0f}")
						glow_diag_at = now

					glow_advanced = getattr(config.glow, "advanced", None)
					advanced_values = {name: (getattr(glow_advanced, name, default) if glow_advanced else default)
						for name, default in _GLOW_ADVANCED_DEFAULTS.items()}
					glow.configure(writer_burst=int(advanced_values["writer_burst"]), writer_sleep_ms=float(advanced_values["writer_sleep_ms"]),
						write_color=bool(advanced_values["write_color"]), write_type_block=bool(advanced_values["write_type_block"]),
						write_eligible=bool(advanced_values["write_eligible"]), write_glowing=bool(advanced_values["write_glowing"]),
						clear_on_drop=bool(advanced_values["clear_on_drop"]), debug_log=bool(advanced_values["debug_log"]))
					glow_opacity = float(config.glow.opacity)
					if glow_advanced is not None:
						glow_opacity *= float(getattr(glow_advanced, "alpha_mult", 1.0))
					glow_opacity = max(0.0, min(1.0, glow_opacity))
					if glow_advanced is not None and bool(getattr(glow_advanced, "uniform_color_enabled", False)):
						uniform_colour = tuple(getattr(glow_advanced, "uniform_color", [1.0, 0.4, 1.0, 1.0]))
						ct_glow_colour = uniform_colour
						t_glow_colour = uniform_colour
					else:
						ct_glow_colour = tuple(config.glow.ct_color)
						t_glow_colour = tuple(config.glow.t_color)
					glow.update_pawns(pawns, local_team=local_team, glow_type=int(config.glow.type), opacity=glow_opacity, team_esp=config.visuals.team_esp,
						ct_color=ct_glow_colour, t_color=t_glow_colour, miss_grace_frames=int(getattr(config.glow, "miss_grace_frames", 1)), range_units=range_units)
				else:
					glow.clear_all()

				if config.visuals.chams.enabled and wallhack_on:
					if vpk_chams_on:
						vpk_chams().begin_frame()
					else:
						chams_begin_frame()

				spectated_pawn = entities.get_observer_target_pawn(local_pawn)
				local_life = mem.read_byte(local_pawn + Netvars.m_lifeState) if local_pawn else 0
				local_is_dead = (local_life != 0)
				if local_is_dead and not spectated_pawn:
					spectator_candidates = [player for player in visible if player.team != local_team]
					if spectator_candidates:
						spectator_candidates.sort(key=lambda candidate: candidate.distance)
						spectated_pawn = spectator_candidates[0].pawn_addr
				if not spectator_diag_logged and (spectated_pawn or local_is_dead):
					print(f"[diag] spectator: dead={local_is_dead} life={local_life} " f"spec_pawn=0x{spectated_pawn:X}")
					spectator_diag_logged = True
				if (late_visuals_enabled and should_show_overlay and master_enabled and not hud_edit_mode
						and not is_menu_open and not hud_manager.is_interacting() and not hud_manager.is_hovering_any()):
					draw_list.channels_split(2)
					world_hud_channels = True
					draw_list.channels_set_current(1)
					hud_prepare_s = render_hud_now()
					draw_list.channels_set_current(0)
					hud_prepared_early = True
				mark_esp = perf_counter()
				if diagnostics_enabled and game_snapshot is not None:
					snapshot_age_at_draw = max(0.0, mark_esp - game_snapshot.taken_at)
				# Refresh poses and the camera after other frame work has completed
				if diagnostics_enabled:
					visual_refresh_started_at = perf_counter()
				if needs_bones:
					entities.refresh_bone_slabs(players, full=bool(vpk_chams_on and config.visuals.chams.enabled and wallhack_on), update_visuals=True)
				else:
					for player in players:
						scene_node = getattr(player, "scene_node", 0)
						if scene_node:
							try:
								origin = mem.read_vec3(scene_node + Netvars.m_vecAbsOrigin)
								if any(origin):
									player.position = origin
							except Exception:
								pass
				try:
					fresh_vm = mem.read_matrix(mem.client(Offsets.dwViewMatrix))
					if fresh_vm:
						view_matrix = fresh_vm
				except Exception:
					pass

				if diagnostics_enabled:
					visual_refresh_at = perf_counter()

				bone_screen = None
				if config.visuals.skeleton.enabled and visible:
					try:
						bone_screen = project_players(visible, view_matrix, screen_w, screen_h)
					except Exception as exc:
						print(f"[perf] batched skeleton projection failed: {exc!r}")
				if wallhack_on:
					camera_pos = None
					if local_pos:
						camera_pos = (local_pos[0], local_pos[1], local_pos[2] + 64.0)
					for player in visible:
						if player.team == local_team and not config.visuals.team_esp:
							continue
						if spectated_pawn and player.pawn_addr == spectated_pawn:
							continue
						draw_player_esp(draw_list, player, view_matrix, screen_w, screen_h, config.visuals)
						if vpk_chams_on:
							if config.visuals.chams.enabled:
								vpk_chams().queue(player, view_matrix, config.visuals.chams, camera_pos)
						else:
							draw_chams(draw_list, player, view_matrix, screen_w, screen_h, config.visuals.chams)
						draw_skeleton(draw_list, player, view_matrix, screen_w, screen_h,
							config.visuals.skeleton, camera_pos=camera_pos, screen=(bone_screen or {}).get(player.pawn_addr))

				if config.visuals.chams.enabled and wallhack_on:
					if vpk_chams_on:
						vpk_chams().end_frame()
					else:
						chams_end_frame()
					# Only silhouette-trigger mode needs the pipeline-stalling pixel read
					if (str(getattr(config.triggerbot, "version", "feedback")) == "vanilla" and is_feature_active(config.triggerbot)):
						try:
							chams_probe_center_pixel(screen_w, screen_h)
						except Exception:
							pass

				enemies_radar = [(player.position, player.team) for player in visible if player.team != local_team]
				teammates_radar = [(player.position, player.team) for player in visible if player.team == local_team]
				radar.render(draw_list, local_pos, local_yaw, enemies_radar, teammates_radar, screen_w=screen_w, screen_h=screen_h)

				if not local_is_dead:
					enemy_players = [player for player in visible if player.team != local_team]
					awareness.update(local_pos, local_yaw, enemy_players)
					awareness.render(draw_list, screen_w, screen_h)

				else:
					awareness.update(local_pos, local_yaw, [])


				draw_crosshair(draw_list, screen_w, screen_h, config.visuals.crosshair)

			elif cs2_alive:
				recoil_control.reset()
				try:
					glow.clear_all()
				except Exception:
					pass

			world_finished_at = perf_counter()
			if not hud_prepared_early:
				hud_prepare_s = render_hud_now()
			if world_hud_channels:
				draw_list.channels_merge()
			selection = hud_manager.editor_selection()
			if qt_menu is not None:
				qt_menu.show_editor(selection, cs2_hwnd=int(cs2_hwnd or 0))
			else:
				menu.sync_hud_editor(selection)

			if qt_menu is None:
				menu.render()
			mark_tail = perf_counter()
			overlay.end_frame()
			if diagnostics_enabled and visual_refresh_at is not None:
				visual_refresh_to_return = perf_counter() - visual_refresh_at

			if qt_menu is None and bool(getattr(config.general, "menu_external_window", False)) and menu.visible:
				if menu_window is None:
					saved_menu_width = int(getattr(config.general, "menu_window_width", 1100) or 1100)
					saved_menu_height = int(getattr(config.general, "menu_window_height", 720) or 720)
					menu_window = MenuWindow(width=saved_menu_width, height=saved_menu_height, parent_glfw_window=overlay.window)
					if not menu_window.create():
						menu_window = None
					else:
						menu_focus_budget = 30
				if menu_window is not None:
					if menu_window.should_close():
						menu_window.destroy()
						menu_window = None
						menu.visible = False
						glfw.make_context_current(overlay.window)
						imgui.set_current_context(overlay.ctx)
						set_font_context(overlay.ctx)
					else:
						try:
							user32.ClipCursor(None)
							for _ in range(8):
								if user32.ShowCursor(True) >= 0:
									break
						except Exception:
							pass
						if menu_focus_budget > 0:
							try:
								if user32.GetForegroundWindow() != menu_window.hwnd:
									menu_window.focus()
								else:
									menu_focus_budget = 0
							except Exception:
								pass
							menu_focus_budget = max(0, menu_focus_budget - 1)
						menu_window.begin_frame()
						menu.render(in_external_window=True)
						menu_window.end_frame()
						try:
							current_menu_width, current_menu_height = menu_window.get_size()
							if current_menu_width > 0 and current_menu_height > 0:
								saved_menu_width = int(getattr(config.general, "menu_window_width", 0) or 0)
								saved_menu_height = int(getattr(config.general, "menu_window_height", 0) or 0)
								if current_menu_width != saved_menu_width:
									config.general.menu_window_width = current_menu_width
								if current_menu_height != saved_menu_height:
									config.general.menu_window_height = current_menu_height
						except Exception:
							pass
						glfw.make_context_current(overlay.window)
						imgui.set_current_context(overlay.ctx)
						set_font_context(overlay.ctx)
			elif menu_window is not None:
				menu_window.destroy()
				menu_window = None
				glfw.make_context_current(overlay.window)
				imgui.set_current_context(overlay.ctx)
				set_font_context(overlay.ctx)

			try:
				want_capture_protect = bool(_anti_detect_value(anti_detect, "hide_overlay_from_capture", False))
				capture_now = perf_counter()
				capture_changed = want_capture_protect != capture_protect_requested
				capture_pending = overlay._capture_protect_applied != want_capture_protect
				capture_reapply = want_capture_protect and bool(_anti_detect_value(anti_detect, "auto_capture_protect_on_focus_loss", True))
				if capture_changed or ((capture_pending or capture_reapply) and capture_now - last_capture_reapply_t >= 1.0):
					capture_applied = overlay.set_capture_protect(want_capture_protect)
					capture_protect_requested = want_capture_protect
					last_capture_reapply_t = capture_now
					if capture_changed or not capture_applied:
						print(f'[anti-det] Overlay capture-affinity request ({"enable" if want_capture_protect else "disable"}): '
							f'{"applied" if capture_applied else "failed; retry pending"}.')
			except Exception as error:
				print(f"[anti-det] capture-protect live toggle failed: {error!r}")

			try:
				want_no_taskbar = bool(_anti_detect_value(anti_detect, "hide_from_taskbar", True))
				overlay.hide_from_taskbar = want_no_taskbar
				taskbar_now = perf_counter()
				taskbar_changed = want_no_taskbar != taskbar_requested
				if overlay.hwnd and (taskbar_changed or (overlay._taskbar_hidden_applied != want_no_taskbar and taskbar_now - last_taskbar_attempt_t >= 1.0)):
					taskbar_applied = overlay.set_hide_from_taskbar(want_no_taskbar, visible=should_show_overlay)
					taskbar_requested = want_no_taskbar
					last_taskbar_attempt_t = taskbar_now
					if not taskbar_applied:
						print('[anti-det] Overlay taskbar-style request failed; retry pending.')
			except Exception as error:
				print(f"[anti-det] taskbar-hide live toggle failed: {error!r}")

			try:
				config.autosave_if_due()
			except Exception:
				pass

			elapsed = perf_counter() - frame_started_at
			real_metrics.note_frame_latency(elapsed * 1000.0)
			if not should_show_overlay:
				idle_sleep = max(0.02, 0.05 - elapsed)
				paced_dt = 0.05
				if now - last_gc_at > 5.0:
					last_gc_at = now
					gc.collect(1)
				sleep(idle_sleep)
			else:
				# A missed budget must not halve the configured frame rate for seconds
				paced_dt = frame_dt
				sleep_for = paced_dt - elapsed
				if sleep_for > 0:
					_precise_sleep(sleep_for)

			jitter = anti_detect.randomize_read_jitter_ms
			if jitter > 0:
				sleep(uniform(0.0, jitter / 1000.0))

			now = perf_counter()
			frame_total = max(1e-6, frame_interval if frame_interval is not None else now - frame_started_at)
			frame_times.append(frame_total)
			if pipeline_trace is not None:
				pipeline_trace.emit({"event": "frame", "frame_id": frame_id, "frame_started_at": frame_started_at, "previous_start_interval_s": frame_interval,
					"previous_target_interval_s": previous_paced_dt, "snapshot_id": game_snapshot.sequence if game_snapshot is not None else None,
					"read_source": "snapshot" if game_snapshot is not None else ("sync_player_scan" if sync_read_started_at is not None else None),
					"read_started_at": game_snapshot.taken_at if game_snapshot is not None else sync_read_started_at,
					"read_completed_at": game_snapshot.read_completed_at if game_snapshot is not None else sync_read_completed_at,
					"publication_attempted_at": game_snapshot.published_at if game_snapshot is not None else None,
					"snapshot_selected_at": snapshot_selected_at if game_snapshot is not None else None,
					"visual_refresh_started_at": visual_refresh_started_at, "visual_refresh_completed_at": visual_refresh_at, "player_count": frame_player_count,
					"visible_player_count": frame_visible_player_count, "vpk_chams_enabled": vpk_chams_on, "overlay_end_called_at": mark_tail,
					"imgui_submitted_at": getattr(overlay, "imgui_commands_submitted_at", None), "swap_started_at": getattr(overlay, "swap_start_at", None),
					"swap_returned_at": getattr(overlay, "swap_return_at", None), "flush_returned_at": getattr(overlay, "flush_returned_at", None),
					"fence_wait_counts": dict(getattr(overlay, "fence_wait_counts", {})), "frame_work_s": elapsed,
					"pre_status_at": now, "target_interval_s": paced_dt, "visual_schedule": "early_hud" if late_visuals_enabled else "original",
					"hud_prepared_early": hud_prepared_early, "hud_prepare_s": hud_prepare_s})
				previous_paced_dt = paced_dt
			if frame_stats is not None:
				try:
					if mark_combat is None or mark_esp is None:
						raise ValueError("no active visual pass")
					frame_stats.append((frame_total, elapsed, mark_combat - frame_started_at,
										mark_esp - mark_combat - (hud_prepare_s if hud_prepared_early else 0.0), world_finished_at - mark_esp, hud_prepare_s,
										elapsed - (mark_tail - frame_started_at), snapshot_age_at_draw, visual_refresh_to_return))
				except Exception:
					pass
				if now - frame_stats_at >= 2.0 and len(frame_stats) > 10:
					totals = sorted(total_time for total_time, *_ in frame_stats)
					works = sorted(work_time for _, work_time, *_ in frame_stats)
					def avg(idx):
						return sum(row[idx] for row in frame_stats) / len(frame_stats) * 1e3
					sec = (avg(2), avg(3), avg(4), avg(5), avg(6))
					pick = lambda arr, q: arr[min(len(arr) - 1, int(len(arr) * q))] * 1e3
					ages = sorted(row[7] for row in frame_stats if row[7] is not None)
					refreshes = sorted(row[8] for row in frame_stats if row[8] is not None)
					age_p95 = f"{pick(ages, .95):.2f}" if ages else "n/a"
					refresh_p95 = f"{pick(refreshes, .95):.2f}" if refreshes else "n/a"
					print(f"[perf] fps={len(totals) / (now - frame_stats_at):.1f} "
						  f"work p50={pick(works, .5):.2f} p95={pick(works, .95):.2f} " f"p99={pick(works, .99):.2f} ms  frame p99={pick(totals, .99):.2f} ms "
						  f"fence={getattr(overlay, 'fence_wait_s', 0.0) * 1e3:.2f} " f"swap={getattr(overlay, 'swap_s', 0.0) * 1e3:.2f} ms "
						  f"paced={1.0 / max(1e-6, paced_dt):.0f} " f"| read={sec[0]:.1f} combat={sec[1]:.1f} esp={sec[2]:.1f} "
						  f"hud={sec[3]:.1f} tail={sec[4]:.1f} " f"| snapshot-age-at-draw p95={age_p95} ms " f"refresh-to-render-return p95={refresh_p95} ms")
					frame_stats.clear()
					frame_stats_at = now
			if len(frame_times) > 24:
				frame_times.pop(0)

			average_frame_time = sum(frame_times) / max(1, len(frame_times))
			real_fps = 1.0 / max(1e-6, average_frame_time)
			displayed_fps = real_fps if displayed_fps is None else (displayed_fps * 0.85 + real_fps * 0.15)

			if now - last_status_write_at >= status_write_interval:
				try:
					attached_now = mem.is_process_alive()
					fps_for_status = real_metrics.fps if master_enabled and attached_now else None
					if gl_renderer_name is None:
						try:
							import OpenGL.GL as _gl
							gl_renderer_name = (_gl.glGetString(_gl.GL_RENDERER) or b"").decode("utf-8", "replace")
							print(f"[gpu] overlay OpenGL renderer: {gl_renderer_name}")
						except Exception:
							gl_renderer_name = ""
					status_publisher.publish(fps=fps_for_status, fps_source=real_metrics.fps_source if fps_for_status is not None else None,
						cs2_attached=attached_now, master_enabled=master_enabled, cs2_monitor=overlay._monitor_rect,
						renderer=gl_renderer_name or None, render_device_requested=launch_render_device, render_device_warning=_gpu_selection_warning,
						hud_editor=hud_manager.editor_selection(), recoil_runtime=recoil_runtime.status_payload())
				except Exception:
					pass
				last_status_write_at = now
	except KeyboardInterrupt:
		print("[main] Interrupted")
	finally:
		try:
			if pipeline_trace is not None:
				pipeline_trace.close()
			try:
				_release_synthetic_inputs()
			except Exception:
				pass
			try:
				right_button_guard.close()
			except Exception as error:
				print(f'[input] Mouse2 guard shutdown failed: {error!r}')
			try:
				recoil_control.reset()
				recoil_runtime.reset()
				state_reader.stop()
			except Exception:
				pass
			try:
				glow.clear_all()
			except Exception:
				pass
			try:
				glow.stop()
			except Exception:
				pass
			try:
				chams_shutdown()
				vpk_chams().shutdown()
			except Exception:
				pass
			keys.unbind_all()
			try:
				if menu_window is not None:
					menu_window.destroy()
			except Exception:
				pass
			try:
				if qt_menu is not None:
					qt_menu.terminate()
			except Exception:
				pass
			try:
				if not config.flush_pending():
					print('[main] Pending settings could not be saved on shutdown')
			except Exception as error:
				print(f'[main] Final config save failed: {error!r}')
			try:
				real_metrics.close()
			except Exception:
				pass
			try:
				event_sounds.stop()
			except Exception:
				pass
			overlay.destroy()
			mem.detach()
			try:
				ctypes.windll.winmm.timeEndPeriod(1)
			except Exception:
				pass
		finally:
			try:
				recoil_runtime.reset()
				final_status = dict(fps=None, cs2_attached=False, master_enabled=bool(getattr(config.general, "master_enabled", True)),
					hud_editor=None, recoil_runtime=recoil_runtime.status_payload())
				if status_publisher is not None:
					if not status_publisher.close(final_payload=final_status, timeout=1.0):
						print("[ipc] final status flush did not complete successfully")
				else:
					write_status(**final_status)
			except Exception as exc:
				print(f"[ipc] status publisher shutdown failed: {exc!r}")
			finally:
				try:
					write_instance(0)
				except Exception:
					pass
		print("[main] Shutdown complete")

	return 0


if __name__ == "__main__":
	raise SystemExit(main())
