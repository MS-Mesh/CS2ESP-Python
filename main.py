"""
CS2 External ESP runtime entry point.

Normal launch owns attach/re-attach, feature wiring, overlay/menu orchestration,
status IPC, and the frame loop. --qt-menu starts only the PySide6 menu process
before GLFW/imgui are imported. Feature logic stays inside cs2esp modules.
"""

import ctypes
import ctypes.wintypes as wt
import math
import sys
from random import uniform
from time import perf_counter, sleep

from keyboard import is_pressed as is_key_pressed

# Keep Qt startup before glfw/imgui context creation.
if "--qt-menu" in sys.argv:
    from cs2esp.ui.qt.menu_app import run
    raise SystemExit(run())

# Pins window's DPI awareness at first paint, so a late call leaves the overlay DPI-virtualised and black on high-DPI or duplicate-monitor
from cs2esp.display_info import (
    clamp_target_fps, dpi_scale_for_hwnd, enum_monitors, get_display_refresh_hz,
    is_process_dpi_aware, make_process_dpi_aware, monitor_for_hwnd,
)
if make_process_dpi_aware():
    print("[main] Process marked per-monitor DPI-aware.")
else:
    print("[main] WARNING: could not set DPI awareness — overlay may "
          "render blank/black on high-DPI or duplicate-monitor setups.")

import glfw
from imgui_bundle import imgui

from cs2esp.config import Config
from cs2esp.memory import MemoryReader
from cs2esp.offsets import CGlowProperty, Netvars, Offsets, apply_sigscan, update_offsets, verify_netvars, _verify_bone_array
from cs2esp.entity import EntitySystem
from cs2esp.visuals.math_utils import world_to_screen
from cs2esp.visuals.overlay import Overlay
from cs2esp.visuals.menu_window import MenuWindow
from cs2esp.visuals.esp import draw_crosshair, draw_player_esp
from cs2esp.visuals.skeleton import draw_skeleton
from cs2esp.visuals.radar import Radar
from cs2esp.visuals.overlays import Awareness
from cs2esp.visuals.fps_counter import draw_fps_counter
from cs2esp.features.glow import GlowSystem
from cs2esp.visuals.chams import chams_begin_frame, chams_context_recreated, chams_end_frame, chams_shutdown, draw_chams
from cs2esp.visuals.chams_pixel_probe import probe_center_pixel as chams_probe_center_pixel

from cs2esp.features.triggerbot_vanilla import Triggerbot
from cs2esp.features.triggerbot_feedback import FeedbackTriggerbot
from cs2esp.features.aimbot import WPMAimbot
from cs2esp.features.autoclick import AutoPistol, RapidFire
from cs2esp.features.bhop import BHop
from cs2esp.ui.menu import Menu
from cs2esp.ui.hotkeys import KeybindManager, ToggleHotkeyDetector, is_feature_active
from cs2esp.ui.qt_launcher import QtMenuController
from cs2esp.win32_utils import (
    GWL_EXSTYLE, WS_EX_TOOLWINDOW, PingPoller, apply_taskbar_invisibility,
    find_window_by_pid, focus_window, get_client_rect, user32,
)

from cs2esp.ipc import write_status


def _anti_detect_value(anti_detect, key: str, default):
    """Read optional [anti_detection] keys safely."""
    try:
        return getattr(anti_detect, key)
    except Exception:
        return default


def main() -> int:
    print("[main] CS2 ESP \u2014 research build")
    config = Config.load()
    if (sys.platform == "win32" and bool(getattr(config.general, "hide_terminal", True))):
        try:
            kernel32 = ctypes.windll.kernel32
            console_processes = (ctypes.c_ulong * 4)()
            attached = int(
                kernel32.GetConsoleProcessList(console_processes, 4))
            if attached > 1:
                print(f"[main] hide_terminal=true but console shared with "
                      f"{attached - 1} other process(es); leaving it visible.")
            elif attached == 1:
                console_hwnd = kernel32.GetConsoleWindow()
                if console_hwnd:
                    ctypes.windll.user32.ShowWindow(console_hwnd, 0)
        except Exception as e:
            print(f"[main] hide_terminal failed: {e!r}")
    anti_detect = config.anti_detection

    try:
        if bool(getattr(anti_detect, "module_spoof_enabled", False)):
            from cs2esp.anti_detect import spoof_python_module_name, module_spoof_supported
            if module_spoof_supported():
                masquerade_name = getattr(anti_detect, "module_spoof_masquerade_name", None)
                unlink_original = bool(getattr(anti_detect, "module_spoof_unlink_from_peb", True))
                spoofed_module_name = spoof_python_module_name(masquerade_name=masquerade_name, unlink_original=unlink_original)
                if spoofed_module_name:
                    print(f"[anti-det] python interpreter DLL "
                          f"masqueraded as {spoofed_module_name!r}")
                else:
                    print("[anti-det] module spoof returned None - "
                          "PEB walk failed silently; check "
                          "Process Explorer DLLs view to confirm")
    except Exception as e:
        print(f"[anti-det] module-spoof raised: {e!r}")

    update_offsets()

    qt_menu: QtMenuController | None = None
    if bool(getattr(config.general, "qt_menu_enabled", True)):
        qt_menu = QtMenuController()
        if bool(getattr(config.general, "start_menu_visible", True)):
            if not qt_menu.spawn():
                print("[main] Qt menu subprocess could not start \u2014 "
                      "falling back to imgui in-overlay menu.")
                qt_menu = None
        else:
            print("[main] start_menu_visible=false \u2014 Qt menu will "
                  "spawn on first toggle-hotkey press.")
    else:
        print("[main] Qt menu disabled in config; using imgui menu.")

    mem = MemoryReader()
    attached = mem.attach(quiet=True)
    if not attached:
        print("[main] Waiting for cs2.exe \u2014 launch the game any time. "
              "The menu stays open during the wait.")
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
                    print(f"[main] Exit hotkey ({exit_key}) pressed during "
                          "the CS2 wait. Quitting.")
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

            if qt_menu is not None:
                try:
                    qt_menu.tick_focus()
                except Exception:
                    pass

            if now - last_status_at >= 0.25:
                try:
                    write_status(fps=None, cs2_attached=False, master_enabled=bool(getattr(config.general, "master_enabled", True)))
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

    # If both the dumper and sigscan miss the critical globals, every pointer reads null and the app silently does nothing — warn loudly.
    try:
        entity_list_addr = mem.read_long(mem.client(Offsets.dwEntityList))
        controller_addr = mem.read_long(mem.client(Offsets.dwLocalPlayerController))
        if not entity_list_addr and not controller_addr:
            print("[main] WARNING: dwEntityList and dwLocalPlayerController "
                  "both read NULL after dumper + sigscan. Offsets are stale "
                  "for this CS2 build and the sigscan patterns did not match. "
                  "ESP/aim/trigger will be idle until offsets update. "
                  "(If you're at the main menu this is expected — join a map.)")
    except Exception:
        pass

    try:
        if bool(getattr(anti_detect, "read_obfuscator_enabled", False)):
            from cs2esp.anti_detect import install_read_obfuscator
            bogus_reads_per_real = int(getattr(
                anti_detect, "read_obfuscator_bogus_per_real", 4))
            install_read_obfuscator(
                mem.pm, bogus_per_real=bogus_reads_per_real)
            print(f"[anti-det] read-pattern obfuscator installed "
                  f"(bogus_per_real={bogus_reads_per_real})")
    except Exception as e:
        print(f"[anti-det] read-obfuscator raised: {e!r}")

    cs2_pid = getattr(mem.pm, "process_id", 0) if mem.pm else 0
    cs2_hwnd = find_window_by_pid(cs2_pid) if cs2_pid else 0

    # Multi-monitor diagnostic dump — makes a blank/misaligned-overlay report self-explanatory from the log: displays, DPI scale, CS2's monitor, and the client rect the overlay actually sizes to.
    try:
        monitors = enum_monitors()
        print(f"[display] DPI-aware={is_process_dpi_aware()} "
              f"scale={dpi_scale_for_hwnd(cs2_hwnd):.2f} "
              f"monitors={len(monitors)}")
        for index, monitor in enumerate(monitors):
            tag = " [primary]" if monitor.is_primary else ""
            print(f"[display]   #{index}: {monitor.rect}{tag}")
        cs2_monitor = monitor_for_hwnd(cs2_hwnd)
        if cs2_monitor is not None:
            print(f"[display] CS2 (HWND 0x{cs2_hwnd:X}) is on "
                  f"monitor {cs2_monitor.rect}")
        else:
            print("[display] CS2 monitor unresolved — overlay will cover "
                  "the largest / primary display.")
        client = get_client_rect(cs2_hwnd)
        if client is not None:
            print(f"[display] CS2 client rect (overlay target) = {client}")
        else:
            print("[display] CS2 client rect unresolved — falling back to "
                  "monitor rect for overlay sizing.")
    except Exception as exc:
        print(f"[display] monitor dump failed: {exc!r}")

    overlay = Overlay(
        title=None,
        randomize_title=_anti_detect_value(anti_detect, "randomize_window_title", False),
        hide_from_taskbar=_anti_detect_value(anti_detect, "hide_from_taskbar", True),
    )
    if not overlay.create(target_hwnd=cs2_hwnd,
                          colorkey_fallback=bool(getattr(getattr(config, "overlay", None), "colorkey_fallback", False))):
        return 1

    if anti_detect.hide_overlay_from_capture:
        overlay.set_capture_protect(True)
        print("[anti-det] Overlay hidden from screen capture (FACEIT/OBS/DXGI).")

    entities = EntitySystem(mem)
    glow = GlowSystem(mem)
    trigger_vanilla = Triggerbot(mem, entities, config)
    trigger_feedback = FeedbackTriggerbot(mem, entities, config)
    aimbot = WPMAimbot(config, mem)
    rapidfire = RapidFire(config)
    autopistol = AutoPistol(config)
    bhop = BHop(mem, entities, config)
    radar = Radar(config)
    awareness = Awareness(config)
    menu = Menu(config, overlay, cs2_hwnd=0)
    keys = KeybindManager()
    toggle_keys = ToggleHotkeyDetector()
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
            overlay.set_click_through(
                not menu.visible
                or bool(getattr(
                    config.general, "menu_external_window", False,
                ))
            )
        except Exception:
            pass

    menu.cs2_hwnd = cs2_hwnd
    # Point the overlay's monitor tracker at CS2 so sync_to_monitor can follow it when dragging the game to another display
    try:
        overlay.set_target_hwnd(int(cs2_hwnd or 0))
    except Exception:
        pass
    if getattr(config.general, "focus_cs2_on_launch", True) and cs2_hwnd:
        if focus_window(cs2_hwnd):
            print(f"[main] Focused cs2.exe (HWND 0x{cs2_hwnd:X}).")
        else:
            print("[main] Could not auto-focus CS2 \u2014 click the game window once.")

    if cs2_hwnd:
        try:
            window_rect = wt.RECT()
            user32.GetWindowRect(ctypes.c_void_p(cs2_hwnd), ctypes.byref(window_rect))
            screen_width = user32.GetSystemMetrics(0)
            screen_height = user32.GetSystemMetrics(1)
            covers_screen = (
                window_rect.left <= 0
                and window_rect.top <= 0
                and (window_rect.right - window_rect.left) >= screen_width
                and (window_rect.bottom - window_rect.top) >= screen_height
            )
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

    def _toggle_master():
        try:
            was_master_enabled = bool(getattr(
                config.general, "master_enabled", True))
            config.general.master_enabled = not was_master_enabled
            if was_master_enabled:
                try:
                    glow.clear_all()
                except Exception:
                    pass
                menu_dead = qt_menu is None or not qt_menu.is_alive()
                if menu_dead:
                    print("[main] Master OFF and menu is closed — exiting.")
                    overlay._running = False
            print(f"[main] Master toggle = {not was_master_enabled}")
        except Exception as e:
            print(f"[main] master toggle failed: {e!r}")

    keys.bind(config.general.master_toggle_key, _toggle_master)

    try:
        menu.set_master_status_provider(
            lambda: bool(getattr(config.general, "master_enabled", True))
        )
    except Exception:
        pass

    last_status_write_at = 0.0
    status_write_interval = 0.25
    frame_times: list[float] = []
    displayed_fps: float | None = None
    last_reattach_attempt_at: float = 0.0
    reattach_interval = 1.5

    ping_poller = PingPoller(host="1.1.1.1", interval_s=2.0)
    ping_poller.start()

    display_hz = get_display_refresh_hz(int(overlay.hwnd or 0))
    raw_target = int(getattr(config.general, "target_fps", 144) or 144)
    target_fps = clamp_target_fps(raw_target, int(overlay.hwnd or 0))
    if target_fps != raw_target:
        print(f"[main] Clamping target_fps {raw_target} -> {target_fps} "
              f"(display reports {display_hz:.2f} Hz)")
        try:
            config.general.target_fps = target_fps
        except Exception:
            pass
    else:
        print(f"[main] target_fps = {target_fps} "
              f"(display {display_hz:.2f} Hz)")
    frame_dt = 1.0 / max(30, target_fps)

    print(f"[main] Running. {config.general.toggle_menu_key.upper()} = menu, "
          f"{config.general.master_toggle_key.upper()} = master toggle, "
          f"{config.general.exit_key.upper()} = exit.")

    if config.triggerbot.enabled:
        print(f"[main] Triggerbot ON (hotkey={config.triggerbot.hotkey!r}, "
              f"delay={config.triggerbot.delay_ms}\u00b1{config.triggerbot.delay_jitter_ms} ms).")
    if config.rapidfire.enabled:
        print(f"[main] Rapid-fire ON (hotkey={config.rapidfire.hotkey!r}, "
              f"{config.rapidfire.cps:.1f} cps \u00b1{int(config.rapidfire.jitter_pct*100)}%).")
    if config.autopistol.enabled:
        print(f"[main] Auto-pistol ON (hotkey={config.autopistol.hotkey!r}, "
              f"{config.autopistol.cps:.1f} cps).")
    if config.bhop.enabled:
        print(f"[main] BHop ON (hotkey={config.bhop.hotkey!r}, "
              f"min_gap={config.bhop.min_gap_ms} ms, humanize={config.bhop.humanize}).")

    last_capture_reapply_t = 0.0
    capture_protect_enabled = bool(_anti_detect_value(anti_detect, "hide_overlay_from_capture", False))
    hidden_from_taskbar = bool(_anti_detect_value(anti_detect, "hide_from_taskbar", True))

    bones_diag_logged = False
    glow_diag_at = 0.0
    spectator_diag_logged = False
    last_count_diag_at = 0.0
    netvar_reheal_done = False
    last_reheal_at = 0.0
    # Tracked separately from netvar reheal — retried until bones resolve.
    bone_heal_done = False
    last_bone_heal_at = 0.0
    qt_menu_was_alive = False
    labels_toggle_was_down = False
    overlay_sync_warning_logged = False
    fps_counter_warning_logged = False

    try:
        while overlay.running:
            frame_started_at = perf_counter()

            if keys.just_pressed(config.general.toggle_menu_key):
                if qt_menu is not None:
                    qt_menu.toggle(cs2_hwnd=int(cs2_hwnd or 0))
                else:
                    menu.toggle()
            if keys.is_pressed(config.general.exit_key):
                break

            if qt_menu is not None:
                if qt_menu.is_alive():
                    qt_menu_was_alive = True
                elif qt_menu_was_alive:
                    master_on = bool(getattr(
                        config.general, "master_enabled", True
                    ))
                    if not master_on:
                        print("[main] Qt menu closed and master is OFF "
                              "— exiting.")
                        break
                    print(
                        "[main] Qt menu closed; master overlay still "
                        f"active ({config.general.toggle_menu_key.upper()} "
                        "to respawn menu, "
                        f"{config.general.master_toggle_key.upper()} "
                        "to toggle master)."
                    )
                    qt_menu_was_alive = False
                try:
                    qt_menu.tick_focus()
                except Exception:
                    pass

            try:
                if config.reload_if_changed():
                    target_fps = int(getattr(
                        config.general, "target_fps", target_fps
                    ) or target_fps)
                    frame_dt = 1.0 / max(30, target_fps)
            except Exception:
                pass

            toggle_keys.poll([
                (getattr(config.triggerbot, "hotkey_toggle", ""),
                 config.triggerbot, "enabled"),
                (getattr(config.rapidfire, "hotkey_toggle", ""),
                 config.rapidfire, "enabled"),
                (getattr(config.autopistol, "hotkey_toggle", ""),
                 config.autopistol, "enabled"),
                (getattr(config.bhop, "hotkey_toggle", ""),
                 config.bhop, "enabled"),
                (getattr(config.aimbot, "hotkey_toggle", ""),
                 config.aimbot, "enabled"),
                (getattr(config.radar, "hotkey_toggle", ""),
                 config.radar, "enabled"),
                (getattr(config.wallhack, "hotkey_toggle", ""),
                 config.wallhack, "enabled"),
                (getattr(config.visuals.snapline, "hotkey_toggle", ""),
                 config.visuals.snapline, "enabled"),
                (getattr(config.visuals.crosshair, "hotkey_toggle", ""),
                 config.visuals.crosshair, "enabled"),
                (getattr(config.fps_counter, "hotkey_toggle", ""),
                 config.fps_counter, "enabled"),
            ])
            labels_toggle_key = getattr(config.visuals.name, "hotkey_toggle", "")
            if labels_toggle_key:
                try:
                    labels_key_down = is_key_pressed(labels_toggle_key)
                except Exception:
                    labels_key_down = False
                if labels_key_down and not labels_toggle_was_down:
                    labels_enabled = not bool(getattr(
                        config.visuals.name, "enabled", True
                    ))
                    try:
                        config.visuals.name.enabled = labels_enabled
                        config.visuals.distance.enabled = labels_enabled
                        print(f"[main] Labels toggle \u2192 {labels_enabled}")
                    except Exception:
                        pass
                labels_toggle_was_down = labels_key_down

            cs2_alive = mem.is_process_alive()

            if not cs2_alive:
                reconnect_now = perf_counter()
                if reconnect_now - last_reattach_attempt_at >= reattach_interval:
                    last_reattach_attempt_at = reconnect_now
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

            master_enabled = bool(getattr(
                config.general, "master_enabled", True
            ))
            overlay_active = cs2_alive and master_enabled

            try:
                should_show_overlay = bool(cs2_alive)
                overlay_is_visible = bool(glfw.get_window_attrib(overlay.window, glfw.VISIBLE))
                if should_show_overlay and not overlay_is_visible:
                    glfw.show_window(overlay.window)
                elif not should_show_overlay and overlay_is_visible:
                    glfw.hide_window(overlay.window)
            except Exception as e:
                print(f"[overlay] visibility sync failed: {e!r}")

            # Follow CS2 across monitors: if the game was dragged to a different display (or the monitor topology changed) the overlay repositions itself so ESP keeps lining up. Throttled inside the overlay so this is effectively free per frame.
            try:
                context_before_sync = overlay.ctx
                overlay.sync_to_monitor()
                if overlay.ctx is not context_before_sync:
                    chams_context_recreated()
                    menu.invalidate_current_context_state()
            except Exception as e:
                if not overlay_sync_warning_logged:
                    print(f"[overlay] monitor sync failed: {e!r}")
                    overlay_sync_warning_logged = True

            screen_w, screen_h = overlay.width, overlay.height
            overlay.begin_frame()
            draw_list = overlay.get_draw_list()

            read_only = bool(_anti_detect_value(anti_detect, "read_only_mode", False))
            wallhack_on = bool(getattr(config.wallhack, "enabled", True))

            view_matrix = None
            if overlay_active:
                try:
                    view_matrix = mem.read_matrix(mem.client(Offsets.dwViewMatrix))
                except Exception:
                    overlay_active = False
                    cs2_alive = False

            if overlay_active:
                local_team = entities.get_local_team()
                local_pawn = entities.get_local_pawn()
                local_yaw = entities.get_local_eye_yaw()

                if not netvar_reheal_done and local_pawn and perf_counter() - last_reheal_at > 1.0:
                    try:
                        fixed_netvars = verify_netvars(mem)
                        if fixed_netvars:
                            print(f"[main] match-join reheal corrected "
                                  f"{fixed_netvars} netvars from live memory.")
                    except Exception as exc:
                        print(f"[main] match-join reheal raised: {exc!r}")
                    netvar_reheal_done = True
                    last_reheal_at = perf_counter()
                    local_team = entities.get_local_team()
                    local_pawn = entities.get_local_pawn()

                needs_bones = (config.visuals.skeleton.enabled
                               or config.visuals.chams.enabled
                               or config.triggerbot.enabled
                               or getattr(config.aimbot, "enabled", False))
                players = entities.get_players(read_bones=needs_bones)

                # Retry the bone-array heal when players came back empty — pawns may not have spawned at match-join.
                if (
                    needs_bones
                    and not bone_heal_done
                    and players
                    and not any(p.bones for p in players)
                    and perf_counter() - last_bone_heal_at > 1.0
                ):
                    last_bone_heal_at = perf_counter()
                    try:
                        if _verify_bone_array(mem):
                            print("[main] bone-array healed from live memory "
                                  "— re-reading players.")
                            players = entities.get_players(read_bones=needs_bones)
                            if any(p.bones for p in players):
                                bone_heal_done = True
                    except Exception as exc:
                        print(f"[main] bone-array heal raised: {exc!r}")
                elif players and any(p.bones for p in players):
                    bone_heal_done = True

                if local_pawn and not players and perf_counter() - last_reheal_at > 3.0:
                    try:
                        controller_addr = mem.read_long(
                            mem.client(Offsets.dwLocalPlayerController))
                        entity_list_addr = mem.read_long(
                            mem.client(Offsets.dwEntityList))
                        in_match = bool(controller_addr and entity_list_addr)
                    except Exception:
                        in_match = False
                    if in_match:
                        try:
                            fixed_netvars = verify_netvars(mem)
                            if fixed_netvars:
                                print(f"[main] empty-walk reheal "
                                      f"corrected {fixed_netvars} netvars from "
                                      f"live memory.")
                                players = entities.get_players(
                                    read_bones=needs_bones)
                                local_team = entities.get_local_team()
                        except Exception as exc:
                            print(f"[main] empty-walk reheal raised: {exc!r}")
                        last_reheal_at = perf_counter()

                local_pos = entities.get_local_position()

                if not bones_diag_logged and players:
                    sample = players[0]
                    bone_count = len(sample.bones) if sample.bones else 0
                    sample_bone = next(iter(sample.bones.values())) if sample.bones else None
                    print(f"[diag] First player bones={bone_count} sample={sample_bone} "
                          f"(skeleton.enabled={config.visuals.skeleton.enabled})")
                    bones_diag_logged = True

                distance_cap = config.visuals.distance_cap
                visible = [player for player in players if player.distance <= distance_cap]

                now = perf_counter()
                if now - last_count_diag_at > 5.0:
                    controller_addr = mem.read_long(mem.client(Offsets.dwLocalPlayerController))
                    entity_list_addr = mem.read_long(mem.client(Offsets.dwEntityList))
                    if controller_addr == 0 and entity_list_addr == 0:
                        print("[diag] CS2 NOT IN MATCH "
                              "(lobby / main menu / loading) \u2014 ESP idle.")
                    else:
                        enemies_count = sum(1 for player in players if player.team != local_team)
                        teams_count = sum(1 for player in players if player.team == local_team)
                        print(f"[diag] players: total={len(players)} "
                              f"enemies={enemies_count} teammates={teams_count} "
                              f"local_team={local_team} visible<=cap={len(visible)} "
                            f"controller=0x{controller_addr:X} "
                            f"entity_list=0x{entity_list_addr:X}")
                    last_count_diag_at = now

                writes_allowed = not read_only

                if config.glow.enabled and writes_allowed and wallhack_on:
                    pawns = [(player.pawn_addr, player.team, player.position)
                             for player in players
                             if player.pawn_addr and player.is_alive]
                    if not config.visuals.team_esp:
                        pawns = [pawn_entry for pawn_entry in pawns if pawn_entry[1] != local_team]

                    pawns_after_team = len(pawns)

                    range_units = float(getattr(config.glow, "radius", 0.0) or 0.0)

                    fov_deg = float(getattr(config.glow, "fov_deg", 0.0) or 0.0)
                    xhair_px = float(
                        getattr(config.glow, "crosshair_radius_px", 0.0) or 0.0)

                    if fov_deg > 0.0 and pawns and local_pos:
                        try:
                            view_angles = mem.read_vec3(
                                mem.client(Offsets.dwViewAngles))
                            pitch_rad = math.radians(float(view_angles[0]))
                            yaw_rad = math.radians(float(view_angles[1]))
                            cos_pitch = math.cos(pitch_rad)
                            sin_pitch = math.sin(pitch_rad)
                            cos_yaw = math.cos(yaw_rad)
                            sin_yaw = math.sin(yaw_rad)
                            view_dir = (
                                cos_pitch * cos_yaw,
                                cos_pitch * sin_yaw,
                                -sin_pitch,
                            )
                            min_dot = math.cos(math.radians(fov_deg))
                            filtered: list = []
                            for entry in pawns:
                                _pawn_addr, _team_id, pawn_position = entry
                                dx = pawn_position[0] - local_pos[0]
                                dy = pawn_position[1] - local_pos[1]
                                dz = pawn_position[2] - local_pos[2]
                                distance = math.sqrt(dx*dx + dy*dy + dz*dz)
                                if distance < 1e-3:
                                    filtered.append(entry)
                                    continue
                                inverse_distance = 1.0 / distance
                                dot = (view_dir[0] * dx * inverse_distance
                                       + view_dir[1] * dy * inverse_distance
                                       + view_dir[2] * dz * inverse_distance)
                                if dot >= min_dot:
                                    filtered.append(entry)
                            pawns = filtered
                        except Exception:
                            pass

                    if xhair_px > 0.0 and pawns and view_matrix is not None:
                        screen_center_x = screen_w * 0.5
                        screen_center_y = screen_h * 0.5
                        crosshair_radius_sq = xhair_px * xhair_px
                        filtered = []
                        for entry in pawns:
                            _pawn_addr, _team_id, pawn_position = entry
                            screen_pos = world_to_screen(
                                pawn_position, view_matrix, screen_w, screen_h)
                            if screen_pos is None:
                                continue
                            screen_delta_x = screen_pos[0] - screen_center_x
                            screen_delta_y = screen_pos[1] - screen_center_y
                            if screen_delta_x * screen_delta_x + screen_delta_y * screen_delta_y <= crosshair_radius_sq:
                                filtered.append(entry)
                        pawns = filtered

                    if int(config.glow.type) == CGlowProperty.VISIBLE_ONLY and pawns and view_matrix is not None:
                        filtered = []
                        for entry in pawns:
                            _pawn_addr, _team_id, pawn_position = entry
                            screen_pos = world_to_screen(
                                pawn_position, view_matrix, screen_w, screen_h)
                            if screen_pos is None:
                                continue
                            if not (0.0 <= screen_pos[0] <= screen_w
                                    and 0.0 <= screen_pos[1] <= screen_h):
                                continue
                            filtered.append(entry)
                        pawns = filtered

                    glow_debug = bool(getattr(
                        getattr(config.glow, "advanced", None),
                        "debug_log", False))
                    if glow_debug and (now - glow_diag_at) >= 1.0:
                        print(f"[diag] glow funnel: players={len(players)} "
                              f"alive_enemy={pawns_after_team} "
                              f"after_filters={len(pawns)} type={config.glow.type} "
                              f"fov={fov_deg:.0f} xhair={xhair_px:.0f}")
                        glow_diag_at = now

                    glow_advanced = getattr(config.glow, "advanced", None)
                    glow.configure(
                        writer_burst=int(getattr(glow_advanced, "writer_burst", 1)) if glow_advanced else 1,
                        writer_sleep_ms=(
                            float(getattr(glow_advanced, "writer_sleep_ms", 10.0))
                            if glow_advanced else 10.0
                        ),
                        write_color=bool(getattr(glow_advanced, "write_color", True)) if glow_advanced else True,
                        write_type_block=(
                            bool(getattr(glow_advanced, "write_type_block", True))
                            if glow_advanced else True
                        ),
                        write_eligible=bool(getattr(glow_advanced, "write_eligible", True)) if glow_advanced else True,
                        write_glowing=bool(getattr(glow_advanced, "write_glowing", True)) if glow_advanced else True,
                        clear_on_drop=bool(getattr(glow_advanced, "clear_on_drop", True)) if glow_advanced else True,
                        debug_log=bool(getattr(glow_advanced, "debug_log", False)) if glow_advanced else False,
                    )
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
                    glow.update_pawns(
                        pawns,
                        local_team=local_team,
                        glow_type=int(config.glow.type),
                        opacity=glow_opacity,
                        team_esp=config.visuals.team_esp,
                        ct_color=ct_glow_colour,
                        t_color=t_glow_colour,
                        miss_grace_frames=int(getattr(config.glow, "miss_grace_frames", 1)),
                        range_units=range_units,
                    )
                else:
                    glow.clear_all()

                if config.visuals.chams.enabled and wallhack_on:
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
                    print(f"[diag] spectator: dead={local_is_dead} life={local_life} "
                          f"spec_pawn=0x{spectated_pawn:X}")
                    spectator_diag_logged = True
                if wallhack_on:
                    camera_pos = None
                    if local_pos:
                        camera_pos = (
                            local_pos[0], local_pos[1], local_pos[2] + 64.0)
                    for player in visible:
                        if player.team == local_team and not config.visuals.team_esp:
                            continue
                        if spectated_pawn and player.pawn_addr == spectated_pawn:
                            continue
                        draw_player_esp(draw_list, player, view_matrix, screen_w, screen_h, config.visuals)
                        draw_chams(draw_list, player, view_matrix, screen_w, screen_h, config.visuals.chams)
                        draw_skeleton(draw_list, player, view_matrix, screen_w, screen_h,
                                      config.visuals.skeleton, camera_pos=camera_pos)

                if config.visuals.chams.enabled and wallhack_on:
                    chams_end_frame()
                    try:
                        chams_probe_center_pixel(screen_w, screen_h)
                    except Exception:
                        pass

                enemies_radar = [(player.position, player.team) for player in visible if player.team != local_team]
                teammates_radar = [(player.position, player.team) for player in visible if player.team == local_team]
                radar.render(draw_list, local_pos, local_yaw,
                             enemies_radar, teammates_radar,
                             screen_w=screen_w, screen_h=screen_h)

                if not local_is_dead:
                    enemy_players = [player for player in visible if player.team != local_team]
                    awareness.update(local_pos, local_yaw, enemy_players)
                    awareness.render(draw_list, screen_w, screen_h)
                else:
                    awareness.update(local_pos, local_yaw, [])

                draw_crosshair(draw_list, screen_w, screen_h, config.visuals.crosshair)

                if not read_only:
                    if is_feature_active(config.triggerbot):
                        if str(getattr(config.triggerbot, "version", "feedback")) == "vanilla":
                            trigger_vanilla.update(
                                players=players,
                                local_pos=local_pos,
                                view_matrix=view_matrix,
                                screen_w=screen_w,
                                screen_h=screen_h,
                            )
                        else:
                            trigger_feedback.update(
                                players=players,
                                local_pawn=local_pawn,
                                local_team=local_team,
                            )
                    if is_feature_active(config.rapidfire) or rapidfire.has_pending_release():
                        rapidfire.update()
                    if is_feature_active(config.autopistol) or autopistol.has_pending_release():
                        autopistol.update()
                    if is_feature_active(config.bhop) or bhop.has_pending_resume():
                        bhop.update()

                if writes_allowed and is_feature_active(config.aimbot):
                    aimbot.update(
                        players=players,
                        local_pawn=local_pawn,
                        local_team=local_team,
                        local_pos=local_pos,
                        active=True,
                    )

            elif cs2_alive:
                try:
                    glow.clear_all()
                except Exception:
                    pass

            if overlay_active:
                try:
                    if len(frame_times) >= 4:
                        average_frame_time = sum(frame_times) / len(frame_times)
                        frame_variance = sum((x - average_frame_time) ** 2 for x in frame_times) / len(frame_times)
                        jitter_ms = int(round((frame_variance ** 0.5) * 1000.0))
                    else:
                        jitter_ms = None
                except Exception:
                    jitter_ms = None
                try:
                    ping_ms = ping_poller.get_ms()
                except Exception:
                    ping_ms = None

                if not local_pawn:
                    ping_ms = None
                    jitter_ms = None

                try:
                    draw_fps_counter(
                        draw_list, screen_w, screen_h,
                        config.fps_counter,
                        fps=displayed_fps,
                        ping_ms=ping_ms,
                        jitter_ms=jitter_ms,
                    )
                except Exception as e:
                    if not fps_counter_warning_logged:
                        print(f"[fps_counter] draw failed: {e!r}")
                        fps_counter_warning_logged = True

            if qt_menu is None:
                menu.render()
            overlay.end_frame()

            if qt_menu is None and bool(getattr(config.general, "menu_external_window", False)) and menu.visible:
                if menu_window is None:
                    saved_menu_width = int(getattr(config.general, "menu_window_width", 1100) or 1100)
                    saved_menu_height = int(getattr(config.general, "menu_window_height", 720) or 720)
                    menu_window = MenuWindow(
                        width=saved_menu_width,
                        height=saved_menu_height,
                        parent_glfw_window=overlay.window,
                    )
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
            elif menu_window is not None:
                menu_window.destroy()
                menu_window = None
                glfw.make_context_current(overlay.window)
                imgui.set_current_context(overlay.ctx)

            try:
                want_capture_protect = bool(_anti_detect_value(anti_detect, "hide_overlay_from_capture", False))
                if want_capture_protect != capture_protect_enabled:
                    overlay.set_capture_protect(want_capture_protect)
                    capture_protect_enabled = want_capture_protect
                    last_capture_reapply_t = perf_counter()
                if (
                    want_capture_protect
                    and _anti_detect_value(anti_detect, "auto_capture_protect_on_focus_loss", True)
                    and perf_counter() - last_capture_reapply_t > 1.0
                ):
                    overlay.set_capture_protect(True)
                    last_capture_reapply_t = perf_counter()
            except Exception as e:
                print(f"[anti-det] capture-protect live toggle failed: {e!r}")

            try:
                want_no_taskbar = bool(_anti_detect_value(anti_detect, "hide_from_taskbar", True))
                if want_no_taskbar != hidden_from_taskbar and overlay.hwnd:
                    SW_HIDE = 0
                    SW_SHOWNOACTIVATE = 4
                    style = user32.GetWindowLongW(overlay.hwnd, GWL_EXSTYLE)
                    if want_no_taskbar:
                        style |= WS_EX_TOOLWINDOW
                    else:
                        style &= ~WS_EX_TOOLWINDOW

                    user32.ShowWindow(overlay.hwnd, SW_HIDE)
                    user32.SetWindowLongW(overlay.hwnd, GWL_EXSTYLE, style)
                    if want_no_taskbar:
                        apply_taskbar_invisibility(overlay.hwnd)
                    user32.ShowWindow(overlay.hwnd, SW_SHOWNOACTIVATE)
                    set_window_pos_flags = 0x0027
                    user32.SetWindowPos(overlay.hwnd, 0, 0, 0, 0, 0, set_window_pos_flags)
                    hidden_from_taskbar = want_no_taskbar
            except Exception as e:
                print(f"[anti-det] taskbar-hide live toggle failed: {e!r}")

            try:
                config.autosave_if_due()
            except Exception:
                pass

            elapsed = perf_counter() - frame_started_at
            sleep_for = frame_dt - elapsed
            if sleep_for > 0:
                sleep(sleep_for)

            now = perf_counter()
            frame_total = max(1e-6, now - frame_started_at)
            frame_times.append(frame_total)
            if len(frame_times) > 16:
                frame_times.pop(0)
            if now - last_status_write_at >= status_write_interval:
                try:
                    average_frame_time = sum(frame_times) / max(1, len(frame_times))
                    fps_now = 1.0 / max(1e-6, average_frame_time)
                    displayed_fps = fps_now
                    attached_now = mem.is_process_alive()
                    fps_for_status = fps_now if master_enabled and attached_now else None
                    write_status(
                        fps=fps_for_status,
                        cs2_attached=attached_now,
                        master_enabled=master_enabled,
                        cs2_monitor=overlay._monitor_rect,
                    )
                except Exception:
                    pass
                last_status_write_at = now
            jitter = anti_detect.randomize_read_jitter_ms
            if jitter > 0:
                sleep(uniform(0.0, jitter / 1000.0))
    except KeyboardInterrupt:
        print("[main] Interrupted")
    finally:
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
            ping_poller.stop()
        except Exception:
            pass
        overlay.destroy()
        mem.detach()
        print("[main] Shutdown complete")

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
