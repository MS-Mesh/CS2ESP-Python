# MS Mesh &mdash; the CS2 ESP that's *pure Python*

> Everyone writes their CS2 cheat in C++. The handful of Python ones
> are usually 200-line single-file scripts. **MS Mesh isn't.** It's a
> fully-architected, menu-driven external overlay &mdash; native engine
> glow, bone/bounds-accurate ESP, a themed PySide6 control panel, and a
> research-grade anti-detection layer &mdash; written end-to-end in
> Python. No C++, no DLL injection, no compiler. `pip install`, run.

External overlay for **Counter-Strike 2** built for anti-cheat
research, reverse-engineering practice, and game-security education.
Reads `cs2.exe` memory through a user-mode handle and renders a
transparent click-through overlay on top of the game window via a
PySide6/Qt menu and a hardware-composited GLFW overlay.

<p align="center">
  <img src="media/menu_keybinds.png" alt="MS Mesh control center — Keybinds" width="800">
</p>

<p align="center">
  <img src="media/menu_esp.png" alt="MS Mesh control center — ESP" width="49%">
  <img src="media/menu_combat.png" alt="MS Mesh control center — Combat" width="49%">
</p>

> **Warning.** Use only on **community/private servers you control**,
> in **offline practice**, or against **bots**. Running this against
> VAC-secured matchmaking, FACEIT, or ESEA is at your own risk and
> may result in permanent account bans. There is no kernel-level
> anti-cheat bypass in this project &mdash; see the
> [Architecture &amp; Detection-Resistance Notes](#architecture--detection-resistance-notes)
> below for what would be required and why it is out of scope for
> Python.

---

## Table of contents

- [Feature overview](#feature-overview)
- [Setup](#setup)
- [Run from source](#run-from-source)
- [Default hotkeys](#default-hotkeys)
- [Build a standalone .exe](#build-a-standalone-exe)
- [Configuration](#configuration)
- [Anti-Detection toggles](#anti-detection-toggles)
- [Project layout](#project-layout)
- [Troubleshooting](#troubleshooting)
- [Architecture &amp; Detection-Resistance Notes](#architecture--detection-resistance-notes)
  - [1. Module map (post-flatten)](#1-module-map-post-flatten)
  - [2. Attach strategy (no admin, when possible)](#2-attach-strategy-no-admin-when-possible)
  - [3. Anti-Detection section &mdash; what's actually plumbed](#3-anti-detection-section--whats-actually-plumbed)
  - [4. Detection signals deliberately left intact](#4-the-detection-signals-this-script-deliberately-leaves-intact)
  - [5. What "VAC-resistant" or "FACEIT-resistant" means](#5-what-vac-resistant-or-faceit-resistant-actually-means-research-only)
  - [6. Why this stays Python-only](#6-why-this-stays-python-only)
  - [7. Configuration internals](#7-configuration)
  - [8. Hotkeys internals](#8-hotkeys)
  - [9. Roadmap](#9-roadmap-research-not-commitments)
  - [10. Recent fixes](#10-recent-fixes-this-revision)

---

## Feature overview

| Subsystem | Notes |
|-----------|-------|
| **ESP primitives** &mdash; box, skeleton, head outline, name, health, distance, snapline, weapon | Per-element TOML config, per-team colours, distance fade, scope-aware visibility. The box is sized from real memory: **bones** (padded AABB around the projected skeleton, default), **bounds** (the entity collision hull `m_vecMins`/`m_vecMaxs`), or a legacy **fixed** rectangle &mdash; pick in the ESP panel |
| **Native CS2 glow** | High-risk write surface. Writes `CGlowProperty` directly on the pawn so the engine renders the highlight (no overlay redraw). **Note:** CS2's glow render is gated by the engine's screen-highlight selector, so external writes only light enemies near the crosshair &mdash; a constant all-enemy glow is not reachable from user-mode on current builds |
| **Chams (3D mesh)** | Safe overlay-only draw. A procedural low-poly humanoid built from primitives and aligned to the bone matrix, drawn by our overlay (GL pass). This is an overlay redraw, **not** an engine model tint &mdash; external `m_clrRender` writes hold in memory but CS2's deferred renderer ignores them for player models, so engine-side chams is injection-only and out of scope here |
| **Mini-radar** | Auto-anchored corner widget. Size scales with screen height; 1080p / 1440p / 4K out of the box |
| **Awareness vignette** | Pulses the screen edge an enemy is approaching from when their crosshair is on you |
| **FPS / RTT HUD** | Optional on-overlay counter showing render fps and round-trip ping (Cloudflare 1.1.1.1, configurable) |
| **Triggerbot** | Medium-risk synthetic-input feature with Feedback / Vanilla selector. Feedback is the default and uses `m_iIDEntIndex` plus health-drop confirmation; Vanilla keeps the legacy glow / chams / fat-skeleton fallback with distance bands, reaction delay, scoped-weapon gating, and jitter |
| **Aimbot** | High-risk `dwViewAngles` writer. Smooth cosine velocity profile, configurable FOV, bone target dropdown (Head / Body), jitter, and optional humanized curved path; all writes pass the same guard as Glow |
| **No-recoil scripts** | Per-weapon recoil compensation packs generated as Logitech Lua or shared AutoHotkey v2 scripts |
| **Bunny-hop helper** | `SendInput` auto-jump helper with hold/toggle modes and humanisation knobs |
| **Auto-click / rapid-fire** | Configurable CPS, jitter, and input target |
| **Responsive PySide6/Qt control center** | Frameless sidebar navigation, bundled Space Grotesk typography, vector icons, live connection status, and `Midnight`, `Slate`, and `Daylight` palettes. UI scale is adjustable from 0.5&times; to 2.5&times;. **Keybinds** is the first section; every hotkey rebinds live, and edits autosave to TOML |
| **Anti-Detection (research-grade)** | `WDA_EXCLUDEFROMCAPTURE`, read-cadence jitter, read-only mode, taskbar/title concealment, window-class masquerade, module-name spoofing, and read-pattern obfuscation &mdash; see the [Anti-Detection toggles](#anti-detection-toggles) section |
| **Spectator-aware position anchor** | When the local pawn is dead, the position pivot follows the spectated pawn so radar / distance / awareness track the live first-person camera, not the corpse |
| **Live keybind sync** | Hotkey changes in any panel propagate to every other panel instantly without a relaunch |
| **Auto-CS2 focus** | On launch, brings `cs2.exe` to the foreground so you don't have to Alt-Tab. Closing the menu does the same |
| **Auto-offset fetch** | On startup, pulls the latest offsets from `a2x/cs2-dumper` |
| **No-admin attach** | Falls back to a limited-rights `OpenProcess` when full access fails |

---

## Setup

```powershell
# from the repo root
.\.venv\Scripts\Activate.ps1
cd Python\CS2
pip install -r requirements.txt
```

Python 3.11 or newer is required.

---

## Run from source

```powershell
python main.py
```

The Qt menu is shown on launch by default. Press your menu hotkey
(default `Ctrl+J`) to hide or restore it. Set
`general.start_menu_visible = false` in `config/default.toml` to keep
the menu hidden until the first hotkey press.

When the app starts it automatically brings `cs2.exe` to the
foreground so you don't have to Alt-Tab. This is controlled by
`general.focus_cs2_on_launch = true`. Closing the menu also re-focuses
CS2 so you can keep playing without an extra Alt-Tab.

### Default hotkeys

All defaults are rebindable from the **Keybinds** section inside the
menu. They are picked to (a) exist on 60% / TKL keyboards and (b) not
clash with any default CS2 bind.

| Default | Action |
|--------:|--------|
| `Ctrl+J` | Show or hide the standalone Qt control center |
| `Ctrl+K` | Master enable / disable (every visual + combat feature) |
| `Ctrl+L` | Quit cleanly (config flushed, overlay torn down) |
| *empty*  | Triggerbot, bhop, rapid-fire, auto-pistol, no-recoil, radar, wallhack, name / snapline / crosshair / fps-counter toggles &mdash; all unbound by default; assign in **Keybinds** |

To rebind: open the menu, click *Rebind* on the row you want, then
press the key combo. `Esc` cancels an active capture.

---

## Build a standalone .exe

The build script lives at [`build/build.py`](build/build.py) and
supports two modes. From `Python\CS2`, use the default APP build or
select a mode explicitly:

| Mode | Output | Use case |
|------|--------|----------|
| `DEV` | Flat `build/DEV/` folder (`cs2esp.exe`, sibling runtime files, and `config/`) with a console | Day-to-day development. Console output shows `[main]`, `[memory]`, and `[offsets]` traffic. Startup is faster because the bootloader does not self-extract. |
| `APP` (default) | `build/APP/cs2esp.exe` &mdash; **single self-contained file**, no console | Distributable artefact. The interpreter, defaults, required Qt plugins, vector-icon fonts, and UI assets are embedded; first launch seeds an editable `config/` beside the executable. |

```powershell
# APP (default)
python build\build.py

# Explicit modes
python build\build.py --app
python build\build.py --dev

# Equivalent environment-variable selection
$env:CS2_BUILD_MODE = "DEV"
python build\build.py
Remove-Item Env:CS2_BUILD_MODE
```

**Single-file distribution (APP).** The `config/` folder is bundled
inside the exe via PyInstaller's `--add-data`. On first run the exe
self-extracts `config/default.toml` next to itself, so distribution
requires only `cs2esp.exe`. APP has no console, so this first-run seed
is intentionally silent. Rebuilding replaces only the staged
executable and leaves an existing `build/APP/config/` untouched, so
live tweaks survive a `python build\build.py` re-run. Close a running
`cs2esp.exe` before rebuilding so Windows can replace the executable.

The script:

1. Auto-installs PyInstaller into the active venv if missing, and
   re-installs `requirements.txt` if any runtime dep is missing.
2. Cleans `build/_work/` and `build/_spec/`. DEV temporarily preserves
   its editable config before rebuilding `build/DEV/`; APP builds into
   `build/_app_stage/` and then replaces only `build/APP/cs2esp.exe`.
3. Generates a PyInstaller spec at `build/_spec/cs2esp_<mode>.spec`.
   Unwanted plugin DLLs are pruned by `BINARY_DROP_PATTERNS`, and
   `filter_submodules` drops demo, sandbox, and alternate-backend code
   from large packages. An override hook at `build/_hooks/hook-OpenGL.py`
   excludes the legacy VC9 GLUT/GLE DLLs and their `MSVCR90.dll`
   dependency chain.
4. Aggregates `collect_all` only where compiled extensions and metadata
   are required (`pymem`, `imgui_bundle`, `glfw`, `keyboard`, and
   `qtawesome`). PySide6 is collected through PyInstaller's bundled Qt
   hooks; NumPy is an explicit hidden import for ImGui's native texture
   upload; and PyOpenGL is limited to `OpenGL.GL`, `OpenGL.GL.shaders`,
   and `OpenGL.arrays`.
5. Bundles `config/` plus the Qt assets directory, including Space
   Grotesk and its SIL OFL licence. Demo packages, unused Qt modules,
   alternate render backends, optional codecs, and unused QtAwesome
   font families are excluded. Only the MDI5 family used by the menu
   remains. The final `DONE` banner reports the exact fresh executable
   size instead of relying on a stale hard-coded estimate.
6. DEV flattens PyInstaller's generated wrapper directory and places
   the preserved or source-seeded `config/` beside `cs2esp.exe`, so
   the in-app *Save config* action writes the file the executable reads.

### Current APP size

The fresh APP build measured on **2026-07-13** is **43,871,980 bytes
(41.8396 MiB)** for `build/APP/cs2esp.exe`, or **43,885,539 bytes
(41.8525 MiB)** for the complete `build/APP/` folder including its
editable config. Compared with the original approximate 60,000 KB
build, the executable is about **16,128 KB (26.9%) smaller**.

Retaining only the icon family the menu actually uses saves
**2,052,776 bytes (1.9577 MiB, 4.47%)** versus the first redesigned
APP build. Even after adding Space Grotesk and its licence, the final
executable is **1,965,591 bytes (1.8745 MiB, 4.29%) smaller** than the
immediate pre-redesign executable.

### Running the exe

Double-click `build/APP/cs2esp.exe` (or run from a shell). **No
admin prompt**: the embedded manifest is `asInvoker`, and
`cs2esp/memory.py` falls back to a limited-rights `OpenProcess` that
works without elevation when CS2 was launched by the same user
(the normal case).

If source mode or the DEV console reports
`OpenProcess(limited) failed (LastError=5)`, CS2 is running at higher
integrity than your shell (typically because Steam was launched as
admin). Restart Steam normally or run `cs2esp.exe` as administrator.
Use DEV when diagnosing APP startup because APP intentionally has no
console.

### DEV vs APP &mdash; when to use which

Use **DEV** while iterating: the console prints every offset patch,
attach attempt, and Qt-launcher transition, so when something
breaks you can see why. Use **APP** for the artefact you actually
ship or run day-to-day &mdash; one file, no DLLs visible next to it,
no taskbar console window stealing focus from CS2.

---

## Configuration

All settings live in `config/default.toml`. Open the menu &rarr; edit
anything &rarr; it autosaves ~1&nbsp;s after the last change
(configurable in `[general]`). The **Settings** section also has a *Save
config now* button for explicit saves. When running as `.exe`, the
config sits next to the executable and is fully user-editable.

Most numeric / colour fields show a tooltip with their accepted range
and a one-line description. Hover any setting label for ~600&nbsp;ms
to see the help popup.

### Self-test

Open the **Settings** section and click *Run feature self-test*. This
walks every enabled feature, confirms its config is sane, that the
runtime found the expected offsets, that the overlay window is
actually `WDA_EXCLUDEFROMCAPTURE`-protected, and prints a
pass/warn/skip/fail report. Use this before assuming a feature is
"silently broken".

---

## Anti-Detection toggles

Every option in `[anti_detection]` is documented in detail in
[&sect;3 of the Architecture notes](#3-anti-detection-section--whats-actually-plumbed)
below. Each row in the **Anti-Detection** section also tells you
whether the toggle takes effect **live** (immediate) or **on next
launch**, and how to verify it is actually doing something.

| Toggle | What it does |
|--------|--------------|
| `hide_overlay_from_capture` | `SetWindowDisplayAffinity(WDA_EXCLUDEFROMCAPTURE)` &mdash; asks standard DWM/GDI/DXGI capture paths such as OBS, Discord, and Snipping Tool to omit the overlay; it is not a kernel-capture bypass |
| `randomize_read_jitter_ms` | Adds 0..N ms random sleep per frame so the read loop isn't a clean fixed frequency |
| `read_only_mode` | **Hard kill-switch.** Blocks Glow/Aimbot writes and synthetic combat dispatch, leaving the read-only visual paths |
| `hide_from_taskbar` | `WS_EX_TOOLWINDOW` &mdash; overlay does not appear in Alt-Tab or default `EnumWindows` |
| `auto_capture_protect_on_focus_loss` | Re-applies `WDA_EXCLUDEFROMCAPTURE` every second so a single-shot strip is healed |
| `randomize_window_title` | Picks an innocuous title (`Settings`, `Audio Mixer`, ...) at launch |
| `window_class_masquerade_enabled` | Re-registers the Qt window class as the selected `window_class_masquerade_target` on the next launch |
| `module_spoof_enabled` | Loads the interpreter under `module_spoof_masquerade_name`; `module_spoof_unlink_from_peb` controls whether its PEB-list entry is also unlinked |
| `read_obfuscator_enabled` | Wraps `ReadProcessMemory` on the next launch and adds `read_obfuscator_bogus_per_real` decoy reads around each real read |

---

## Project layout

```
Python/CS2/
├── main.py                          # entry point, frame loop, attach + shutdown,
│                                    #   also re-invokes itself as `--qt-menu`
├── README.md
├── requirements.txt
├── config/
│   └── default.toml                 # authoritative defaults
├── build/
│   └── build.py                     # PyInstaller wrapper (DEV / APP)
└── cs2esp/
    ├── __init__.py                  # version + package marker
    ├── config.py                    # TOML loader (frozen-aware) + autosave-on-change
    ├── memory.py                    # Two-stage attach (admin or limited-rights fallback)
    ├── offsets.py                   # Offsets / Netvars / CGlowProperty + auto-fetch from a2x/cs2-dumper
    ├── sigscan.py                   # Pattern-scan healing when offsets drift
    ├── entity.py                    # Entity-list traversal + spectator pivot
    ├── ipc.py                       # status.json bridge between overlay and Qt menu
    ├── display_info.py              # Detect monitor refresh; clamp target_fps
    ├── win32_utils.py               # Layered window + capture-protect helpers
    ├── diagnostics.py               # Feature-by-feature pass/warn/skip/fail check
    ├── anti_detect/                 # Concealment layers
    │   ├── module_spoof.py          #   PEB rename of python3xx.dll
    │   ├── read_obfuscator.py       #   bogus-read shim around pymem.read_bytes
    │   └── window_class.py          #   GCLP_ATOM masquerade
    ├── visuals/                     # Per-frame draw + overlay context
    │   ├── overlay.py               #   GLFW + imgui-bundle window; randomised title/class
    │   ├── esp.py                   #   Box (bones/bounds/fixed) / name / health / distance / weapon / snapline / crosshair
    │   ├── bbox.py                  #   Box geometry: bone-AABB + collision-hull projection
    │   ├── skeleton.py              #   Bone draw + head-outline polygon
    │   ├── chams.py                 #   3D procedural-mesh chams (GL pass)
    │   ├── chams_pixel_probe.py     #   GL framebuffer probe (chams trigger sampler)
    │   ├── radar.py                 #   Anchored mini-map; size-fraction scales 1080p / 1440p / 4K
    │   ├── overlays.py              #   Awareness vignette + per-edge directional cues
    │   ├── fps_counter.py           #   Render FPS + RTT HUD
    │   ├── menu_window.py           #   Compositor window for the legacy ImGui menu
    │   ├── math_utils.py            #   world_to_screen + team constants
    │   ├── bone_extensions.py       #   Synthetic gun-tip / toe / crown extrapolation points
    │   └── theme.py                 #   RGBA → packed uint32 helpers + health gradient
    ├── features/                    # In-process behaviour modules
    │   ├── glow.py                  #   Native CGlowProperty writer (background thread)
    │   ├── triggerbot_feedback.py   #   m_iIDEntIndex + health-drop feedback fire path
    │   ├── triggerbot_vanilla.py    #   Legacy glow / chams / silhouette hit-test + click
    │   ├── aimbot.py                #   Smooth dwViewAngles writer + WriteGuard
    │   ├── autoclick.py             #   Rapid-fire / auto-pistol shared engine
    │   ├── bhop.py                  #   Space-spam on jump key
    │   └── norecoil_scripts.py      #   Lua / AHK script generator with embedded recoil patterns
    └── ui/                          # Controls
        ├── menu.py                  #   Legacy in-overlay ImGui menu (Qt-failure fallback)
        ├── hotkeys.py               #   Global keybind manager (edge-triggered)
        ├── fonts.py                 #   ImGui font loader
        ├── qt_launcher.py           #   Boots & tracks the Qt menu subprocess
        └── qt/                      #   PySide6 / Qt menu
            ├── menu_app.py          #     subprocess entry point
            ├── main_window.py       #     top-level window + autosave pump
            ├── theme.py             #     semantic palettes, metrics, and QSS
            ├── fonts.py             #     bundled-font registration
            ├── assets/              #     Space Grotesk font + SIL OFL licence
            ├── widgets/             #     KeybindRow, LabeledRow/Slider, ColorButton, HelpIcon, …
            └── panels/              #     one navigation section per file
                ├── keybinds.py
                ├── esp.py
                ├── combat.py
                ├── awareness.py
                ├── radar.py
                ├── norecoil.py
                ├── anti_detection.py
                └── settings.py
```

### UI typography

The Qt control center bundles **Space Grotesk** by Florian Karsten and
registers it at application startup. The font is distributed under the
SIL Open Font License 1.1; the complete licence is included at
[`cs2esp/ui/qt/assets/OFL.txt`](cs2esp/ui/qt/assets/OFL.txt).

**Where things moved in the round-cleanup pass:**

* `cs2esp/lua/` &mdash; deleted. Logitech Lua and AutoHotkey v2 scripts
  are generated at runtime by `features/norecoil_scripts.py`.
* `cs2esp/diagnostics/` &mdash; collapsed from a package with one
  module into a single `cs2esp/diagnostics.py`. The public API
  (`run_full_health_check`, `HealthReport`, `CheckResult`,
  `CheckStatus`) is unchanged.
* `cs2esp/bone_extensions.py` and `cs2esp/math_utils.py` &mdash;
  moved under `cs2esp/visuals/` because every consumer
  (`esp.py`, `skeleton.py`, `chams.py`, `overlays.py`, plus the
  triggerbot's own world-to-screen call) lives in or beside the
  visuals subtree.

---

## Troubleshooting

**Menu won't open.**
Check the *Keybinds* section inside the menu (or open
`config/default.toml` and look at `general.toggle_menu_key`). The
default is `ctrl+j`. If `J` alone is bound to something else in your
shell, the runtime will still capture the combo because it uses the
low-level keyboard hook, but a stuck modifier from a previous focus
loss can prevent it firing &mdash; tap `Ctrl` and `Shift` once each
to clear sticky-modifier state.

**Menu spawns but never appears.**
Run `python -m cs2esp.ui.qt` directly &mdash; this bypasses the
parent's `stderr=DEVNULL` and surfaces any traceback from the Qt
subprocess.

**`OpenProcess(limited) failed (LastError=5)`.**
CS2 is running at higher integrity than your shell. Either restart
Steam normally (so it doesn't elevate) or run `cs2esp.exe` as admin.

**Head outline drifts when spectating a teammate.**
Fixed: when `life_state != 0` the position pivot follows the
observer target pawn, not the dead local pawn. If you still see
drift, share a screenshot &mdash; the issue is reproducible only on
specific game-modes that route observer state through a different
controller.

**Hotkey changes in one panel don't show in another.**
Fixed: every `KeybindRow` instance subscribes to the cross-panel
keybind registry, so a write or *Clear* in one panel refreshes
every peer row bound to the same config field instantly.

**`pip install` fails on `pymem`.**
You need MSVC build tools (Visual Studio 2022 / Build Tools, "Desktop
development with C++" workload). pymem ships compiled wheels for
recent Python versions; if you are on a non-LTS Python, the wheel
may not exist yet. Pin `python==3.11` if so.

**Self-test reports `WDA capture-protect: WARN`.**
Some Windows builds revoke `WDA_EXCLUDEFROMCAPTURE` when the overlay
loses focus. The `auto_capture_protect_on_focus_loss` toggle re-arms
it every second; enable it if you see this warn.

**Build defaults differ from running config.**
The TOML at `config/default.toml` is the single source of truth.
There is no second build-time defaults file. If a freshly-built
exe seems to ship different defaults, delete the stale
`config/` folder next to the exe and re-run the build script &mdash;
PyInstaller's `--add-data` only re-bundles when the source TOML
changed.

---

# Architecture &amp; Detection-Resistance Notes

This document describes the internal design of the project and gives a deeper, defender-oriented look at what stands between an external CS2 cheat and **VAC** / **FACEIT AC** detection. **No bypass code is implemented** in this codebase, and Python is the wrong language for one &mdash; see "Why this stays Python-only" below.

The intent is the same as published research from PortSwigger, OWASP, BlackHat, DEF CON, and the academic anti-cheat literature: a defender who does not understand how an external cheat is structured cannot meaningfully detect it.

---

## 1. Module map (post-flatten)

```
cs2esp/
├── config.py             # TOML loader; nested attribute access; frozen-aware path
├── memory.py             # Two-stage attach (admin → limited-rights fallback); typed read/write
├── offsets.py            # Offsets, Netvars, CGlowProperty + auto-fetch from a2x/cs2-dumper
├── sigscan.py            # Pattern-scan healing when the offset table drifts
├── entity.py             # Entity-list traversal, Player dataclass, glow-pawn scan
├── ipc.py                # status.json bridge (overlay ↔ Qt menu)
├── display_info.py       # Monitor-refresh detection / target_fps clamp
├── win32_utils.py        # GWL_EXSTYLE, layered/topmost/transparent + capture-protect
├── diagnostics.py        # One-shot health check (was a package; now a single module)
├── anti_detect/          # Module spoof, read obfuscator, and window-class masquerade
├── visuals/
│   ├── overlay.py        # GLFW + imgui-bundle window; click-through toggle; capture-protect
│   ├── esp.py            # Per-player rendering + crosshair; box modes (bones/bounds/fixed)
│   ├── bbox.py           # ESP box geometry — bone AABB + collision-hull (mins/maxs) projection
│   ├── skeleton.py       # Bone-line drawing + head-outline polygon
│   ├── chams.py          # 3D procedural-mesh chams (GL pass)
│   ├── chams_pixel_probe.py   # GL framebuffer sampler (chams trigger)
│   ├── radar.py          # Anchored mini-map; size-fraction scales any monitor
│   ├── overlays.py       # Awareness vignette + directional cues (formerly awareness.py)
│   ├── fps_counter.py    # Render FPS + RTT HUD
│   ├── math_utils.py     # world_to_screen, head estimation, angle helpers, team constants
│   ├── bone_extensions.py# Synthetic gun-tip / toe / crown points (used by triggerbot + skeleton)
│   ├── menu_window.py    # Compositor window for the legacy ImGui menu
│   └── theme.py          # Color helpers (RGBA → packed uint32)
├── features/
│   ├── glow.py           # Native CGlowProperty writer; tracks active pawns and clears stale
│   ├── triggerbot_feedback.py # m_iIDEntIndex + health-drop feedback fire path
│   ├── triggerbot_vanilla.py  # Legacy glow / chams / fat-skeleton fallback hit-test
│   ├── aimbot.py         # Smooth dwViewAngles writer + WriteGuard
│   ├── autoclick.py      # Rapid-fire / auto-pistol shared engine
│   ├── bhop.py           # SendInput auto-jump helper (hold/toggle)
│   └── norecoil_scripts.py # Lua / AHK generator with embedded recoil patterns
└── ui/
    ├── menu.py           # Legacy ImGui fallback menu
    ├── hotkeys.py        # Edge-triggered keybind manager via the `keyboard` lib
    ├── fonts.py · qt_launcher.py
    └── qt/               # PySide6 / Qt menu (panels / widgets / theme / main_window)
```

`platform/` was renamed to `win32_utils.py` because `platform` shadows the
Python stdlib module of the same name. `core/` and `game/` were single-folder
wrappers that added nothing &mdash; flattened. `cs2esp/lua/` and the
`diagnostics/` package were each collapsed in the round-cleanup pass
(see [Project layout](#project-layout)).

### Per-frame data flow

```
  config (TOML) ──┐
                  ▼
  MemoryReader ─► EntitySystem ─► [Player[], pawns_for_glow]
                                        │
                  ┌─────────────────────┼──────────────────────────┐
                  ▼                     ▼                          ▼
              GlowSystem            visuals.esp                 Radar / Awareness
              (writes CGlow)        skeleton + box              (read positions)
                                        │
                                        ▼
                                  imgui draw_list
                                        │
                                        ▼
                                Overlay.end_frame() → swap → screen
```

---

## 2. Attach strategy (no admin, when possible)

Two-stage in `cs2esp/memory.py`:

1. `pymem.Pymem("cs2.exe")` &mdash; asks for `PROCESS_ALL_ACCESS`. Succeeds when the caller holds `SeDebugPrivilege` (admin) or when the game was launched at the same integrity level as the script.
2. **Fallback**: hand-rolled `OpenProcess` with `PROCESS_VM_READ | PROCESS_VM_WRITE | PROCESS_VM_OPERATION | PROCESS_QUERY_INFORMATION`. These rights are grantable from a normal-user process to another normal-user process at the same integrity level (the common case: Steam not launched as admin). The handle is then injected into a fresh `pymem.Pymem()` instance via `process_id` / `process_handle`.

If both fail, the user sees one of two clear messages:

* `cs2.exe is not running — please launch CS2 first.`
* `OpenProcess(limited) failed (LastError=5).` plus an instruction to either restart Steam normally or run as admin.

---

## 3. Anti-Detection section &mdash; what's actually plumbed

The **Anti-Detection** menu section and `[anti_detection]` TOML section expose the toggles below. Each is a *defensive*, low-noise tactic that works from user-mode Python alone. None of them defeat a kernel-driver anti-cheat (FACEIT AC, EAC, BattlEye, Vanguard).

| Toggle | Implementation | What it stops | What it does NOT stop |
|---|---|---|---|
| `hide_overlay_from_capture` | Calls `SetWindowDisplayAffinity(hwnd, WDA_EXCLUDEFROMCAPTURE)` through `Overlay.set_capture_protect`. | Standard DWM/GDI/DXGI capture paths used by common recording and screenshot tools. | Kernel-level surface inspection or explicit window enumeration. |
| `randomize_read_jitter_ms` | Adds `sleep(uniform(0, N/1000))` per frame in `main.py`. | Simple fixed-frequency analysis of the reader loop. | Per-call telemetry or structural analysis of the addresses being read. |
| `read_only_mode` | Global live guard in `main.py`; suppresses Glow/Aimbot writes and synthetic combat dispatch. | Cross-process writes and generated combat input while it is enabled. | `OpenProcess`, `ReadProcessMemory`, the overlay window, or global key hooks. |
| `hide_from_taskbar` | Applies `WS_EX_TOOLWINDOW` to the overlay. | Its taskbar tile and normal Alt-Tab entry. | Enumeration that explicitly includes tool windows. |
| `auto_capture_protect_on_focus_loss` | Re-applies `WDA_EXCLUDEFROMCAPTURE` once per second while capture protection is enabled. | A capture-affinity flag that is cleared and left unset. | A strip-and-capture operation that completes before the next refresh. |
| `randomize_window_title` | Selects an innocuous overlay title at launch. | Basic title-regex scans. | Detection that ignores window titles. |
| `module_spoof_enabled` | At startup, loads the interpreter under `module_spoof_masquerade_name`; optional `module_spoof_unlink_from_peb` removes its PEB-list entry. | User-mode module scans that trust the process PEB. | Kernel image-load telemetry or independent module maps. |
| `read_obfuscator_enabled` | At startup, wraps each real read with `read_obfuscator_bogus_per_real` decoy reads. | A stock, perfectly clustered read-call pattern. | Correlation against the real CS2 addresses that still must be read. |
| `window_class_masquerade_enabled` | After the Qt window is shown, re-registers its class using `window_class_masquerade_target`. | Simple class-name matching through `GetClassNameW`. | Process identity, the Qt window procedure, or behavioural inspection. |

---

## 4. The detection signals this script *deliberately* leaves intact

A defender reading this codebase should be able to enumerate every signal an EAC- or FACEIT-grade anti-cheat would catch &mdash; these are the things you would have to remove or hide to evade kernel-level detection, and they are all **explicitly out of scope**:

| Signal | Why it fires here | Defender lesson |
|---|---|---|
| `OpenProcess` from a non-whitelisted PID, with `PROCESS_VM_READ` on `cs2.exe` | `pymem.attach()` (or our fallback) does exactly this | The AC kernel driver hooks `NtOpenProcess` (or registers an `ObRegisterCallbacks` callback on `PsProcessType`) and inspects the requesting PID. Whitelist OS / driver callers, alert on others. |
| Repeated cross-process `ReadProcessMemory` on the same `client.dll` regions | The 144 Hz read loop hammers the same offsets | The driver can monitor `MmAccessFault` callbacks or guard specific pages with `PAGE_GUARD`. High-frequency external reads from the same PID light up. |
| `WriteProcessMemory` to `pawn + m_Glow + 0x30..0x51` from outside the game's own threads | `GlowSystem.update_pawns` | The glow struct is never modified by the game during normal play to highlight unauthorised players. Periodic integrity hashing of fields that should remain constant can therefore expose the write. |
| Topmost layered window with `WS_EX_TRANSPARENT \| WS_EX_TOOLWINDOW` overlapping the game viewport | `Overlay.create()` uses exactly this combo | `EnumWindows` plus `GetWindowLong(GWL_EXSTYLE)` flags any topmost layered click-through window co-located with the game. The AC does this trivially. The **`hide_overlay_from_capture`** toggle blinds *capture* but not *enumeration*. |
| Global low-level keyboard hook (`SetWindowsHookExW(WH_KEYBOARD_LL)` from `keyboard`) | Keybinds section | An AC can enumerate the user-mode hook chain via undocumented `gpsi`/`win32k` walking. An unsigned hook coming from `python.exe` is a high-confidence flag. |
| `python.exe` parent process with non-Steam command line, no Authenticode signature | The whole runtime | Code-signing attestation plus parent-process telemetry is sent to the AC backend. `cs2esp.exe` from PyInstaller is also unsigned by default. |

---

## 5. What "VAC-resistant" or "FACEIT-resistant" actually means (research only)

This is *research notation*, not an implementation roadmap. None of these are in this repo, and several are illegal in many jurisdictions to deploy against services you do not own:

1. **Kernel driver** that proxies all reads/writes via `MmCopyVirtualMemory` from a system-context thread, behind either:
   * A stolen-but-not-yet-revoked code-signing certificate, or
   * **BYOVD** (Bring Your Own Vulnerable Driver) &mdash; load a signed-but-buggy third-party driver and exploit it as a kernel-read primitive. This is exactly the technique mainstream paid cheats use, and is the basis of the `LoLDrivers` (Living Off the Land Drivers) defensive project.
2. **Hardware input** &mdash; Arduino Leonardo / Raspberry Pi Pico over USB-HID. No `SendInput`, no hooks. Mouse motion is indistinguishable from a physical device at the OS level.
3. **Two-PC rig** &mdash; the gaming PC sees no overlay window at all; an HDMI capture card feeds frames to a second PC, which renders the ESP on a separate monitor. Detection signals on the gaming PC drop to near zero. This is the architecture every "DMA-grade" public cheat uses.
4. **DMA hardware** (a PCIe FPGA card such as the Squirrel/PCILeech) &mdash; performs reads over the PCIe bus with no driver on the gaming PC at all. Requires the second-PC rig.
5. **Read-only design** &mdash; never write to the game process. ESP/radar/awareness are derivable from reads alone; the aimbot becomes "reads positions, USB-HID emits mouse delta" with zero process interaction.
6. **Code-signing + fake-IOCTL channel** &mdash; driver communication randomised per build to defeat signature scans.
7. **Anti-screenshot on the *game*, not the overlay** &mdash; game-side `WDA_MONITOR` blanks DXGI capture but not kernel capture. Full mitigation requires the two-PC rig in (3).

The detection chain is: **each layer of stealth costs ~10× the engineering of the layer below it; the first three (driver, hardware, two-PC) raise the bar past virtually every paid public cheat**. They are also all C/C++/Rust + driver work.

### FACEIT AC specifically

FACEIT ships a kernel driver (`faceitclient.sys`-equivalent) with periodic hash-checks of `client.dll` regions, screenshot uploads at random intervals, and parent-process attestation of `cs2.exe`. Effective mitigations against FACEIT, in increasing order of effort:

* `WDA_EXCLUDEFROMCAPTURE` &mdash; defeats the screenshot upload path (already implemented).
* Read-only design &mdash; defeats the hash-check on `client.dll` regions you would otherwise patch.
* Two-PC rig &mdash; defeats every host-side check by removing the script from the game machine entirely.
* DMA hardware &mdash; defeats parent-process attestation on the gaming PC because no script runs there.

### VAC specifically

VAC is comparatively **lighter** than FACEIT AC: it relies on signature scans of running modules, observed game behaviour (aim consistency, demo review), and Trust Factor (a server-side ML model). For a non-injecting external script with no module footprint inside `cs2.exe`:

* No injected DLL &rarr; VAC's signature scan finds nothing.
* No code patches &rarr; no `client.dll` modification to detect.
* `WriteProcessMemory` to `CGlowProperty` *is* observable client-side, but VAC historically ban-waves on signatures, not behavioural cross-process write detection.
* The biggest VAC risk is **Overwatch / Trust Factor** flagging the *play pattern* (perfect pre-aim through walls, perfect tracking). The shipped combat features expose timing, FOV, smoothness, and write/read-only gates so the risk is visible before enabling them.

---

## 6. Why this stays Python-only

* No way to install a kernel driver from Python without an external loader.
* No way to hide the Python interpreter (or PyInstaller-bundled `cs2esp.exe`) process from AC enumeration.
* `WriteProcessMemory` from `python.exe` / `cs2esp.exe` is a standing red flag.
* Any genuine VAC-resistance work belongs in **C / C++ / Rust + an unsigned-or-BYOVD driver + USB-HID hardware + a second PC**, and is **out of scope** for this educational project.

This project is therefore explicitly:

* **Read-mostly** (writes only to `CGlowProperty`, easily disabled in TOML).
* **Visible** (overlay window, hotkey hooks, parent process all detectable).
* **Useful for learning** the offset / entity-list / world-to-screen pipeline and for **building detection** against external cheats &mdash; not for evading detection in production.

---

## 7. Configuration

`config/default.toml` is the single source of truth. The `Config` class wraps the parsed dict in a recursive `Section` so `cfg.visuals.box.enabled = False` propagates through the underlying dict and survives `cfg.save()`. `tomli_w` writes back; `tomllib` (Python 3.11+) reads.

When running as a frozen `.exe`, `_resolve_config_dir()` returns `Path(sys.executable).parent / "config"` so the file is right next to the binary and remains user-editable.

The wallhack preset picker flips enabled flags and modes on demand from the menu (never at startup, and never overwriting custom colours), so the live `default.toml` (the single source of truth) survives every relaunch.

---

## 8. Hotkeys

`KeybindManager` wraps the `keyboard` library. `just_pressed(key)` is edge-triggered (per-key boolean debounce). When the menu becomes visible, `Overlay.set_click_through(False)` strips `WS_EX_TRANSPARENT` and clears GLFW's `MOUSE_PASSTHROUGH` so ImGui receives input. Toggling off restores both.

---

## 9. Roadmap (research, not commitments)

* Replace `pymem` with a thin `ctypes` wrapper to remove a noisy dependency.
* Spectator-list reader (skip rendering when the local player is being watched).
* Bomb / planted C4 indicator on radar (`dwPlantedC4` already in `Offsets`).
* Batched `ReadProcessMemory` for the radar dot loop (one syscall per frame instead of N).
* Optional second-PC mode: replace the local overlay with a network sender that ships entity data to a separate viewer process.

---

## 10. Recent fixes (this revision)

* **Qt control-center redesign:** rebuilt the frameless shell, sidebar, status card, feature cards, setting rows, sliders, selectors, keybind controls, help surfaces, and every panel around responsive semantic components. The UI now ships Space Grotesk, vector navigation icons, three coordinated palettes, geometry-aware scaling, and preserved live bindings for every existing setting and action.
* **Overlay compositing fix:** the master overlay now renders over CS2 again. The old per-pixel-alpha path that cycled `WS_EX_LAYERED` was breaking DWM transparency on some rigs and has been removed; the overlay uses GLFW-native `TRANSPARENT_FRAMEBUFFER` + `MOUSE_PASSTHROUGH`, with `set_colorkey_transparency()` kept as an opt-in fallback.
* **Bone/bounds-derived ESP box:** the box is now sized from live memory instead of a fixed `height × 0.42` rectangle. `visuals/bbox.py` projects either the padded skeleton AABB (`box_mode = "bones"`, default) or the entity collision hull `m_vecMins`/`m_vecMaxs` (`box_mode = "bounds"`); `"fixed"` keeps the legacy formula. Selectable in the ESP panel. Collision offsets (`m_pCollision`/`m_vecMins`/`m_vecMaxs`) are auto-fetched from the dumper.
* **Build-size cleanup:** PyInstaller now filters imgui demo/sandbox modules, unused alternate imgui backends, PySide6 plugin bloat, and PyOpenGL's extension sweep. The `MSVCR90.dll` VC9 warning chain is gone, and each fresh build reports its exact executable size in the final `DONE` banner. Remaining `warn-*.txt` entries are optional platform imports rather than runtime failures.
* **Dead-offset removal:** dropped the confirmed-non-functional `m_clrRender`/`m_nRenderMode`/`m_nRenderFX` (engine model-tint chams) and `m_flFlashOverlayAlpha` (flash-overlay glow). Live testing showed external writes to these hold in memory but never render on current CS2, so they were defined-but-unused dead weight.
* **Build modes:** `build/build.py` supports `--dev`, `--app`, `CS2_BUILD_MODE=DEV|APP`, and the `BUILD_MODE` constant. DEV is an onedir console build at `build/DEV/`; APP is a single-file no-console build at `build/APP/cs2esp.exe`. APP mode preserves an existing `build/APP/config/` between rebuilds so live tweaks survive a re-run.
* **Cleanup pass:** `cs2esp/lua/` deleted (Lua bridge is now generated at runtime by `features/norecoil_scripts.py`); `cs2esp/diagnostics/` package collapsed into a single `cs2esp/diagnostics.py`; `bone_extensions.py` and `math_utils.py` moved under `cs2esp/visuals/` next to their consumers. Unused `import subprocess` and `apply_preset` symbols pruned from `main.py`. Public API (`from cs2esp.diagnostics import run_full_health_check`) unchanged.
* **Pixel-probe removal:** the round-46-v11 glow path switched to `m_iIDEntIndex` resolve, retiring the GDI desktop pixel probe. `screen_pixel_probe.py` has been moved out of `cs2esp/visuals/` to `temp/` (dev-only harness), and its no-op `shutdown()` call on exit was dropped from `main.py`.
* **Combat suite:** added the Feedback / Vanilla triggerbot selector. Feedback is the default path and combines `m_iIDEntIndex` target lookup with health-drop confirmation; Vanilla preserves the previous distance-banded screen/glow/chams path under `triggerbot_vanilla.py`.
* **Aimbot:** added `features/aimbot.py`, a high-risk smooth `dwViewAngles` writer with cosine velocity profile, Head / Body bone dropdown, jitter, and optional humanized curve. All writes pass `WriteGuard`, which mirrors the Glow write gate.
* **Risk audit:** Glow is now high risk on both menu surfaces because it performs cross-process writes. Chams is safe in the Qt panel because it is pure GL overlay draw.
* **norecoil_active retired:** removed the legacy in-process recoil compensator module, config section, diagnostics hook, runtime wiring, and stale UI references. `norecoil_scripts.py` remains the live No-Recoil path.
* **Config schema 59:** current defaults merge missing keys into older user TOMLs, apply the declared retired/reset paths during upgrades, and preserve ordinary user preferences.
* **Source humanisation milestone:** UI modules use concise docstrings, short operational comments, organised imports, semantic widget names, and shared components instead of panel-local styling.
* **Import and name cleanup:** UI modules now use top-level imports except for intentional guarded/lazy imports (`keyboard`, `qtawesome`, Win32 helpers, anti-detect hooks, and the `ToggleSwitch` lazy import used to avoid a circular widget dependency). Dense local names and stale section labels were normalised across the Qt panels, widgets, legacy ImGui fallback, and `main.py`.
* **Default hotkeys:** menu/master/exit defaults are now `Ctrl+J`, `Ctrl+K`, and `Ctrl+L`, matching `config/default.toml` and the live keybind rows.
* **Radar:** anchored to a screen corner with size-as-fraction-of-screen-height &mdash; self-scales on 1080p / 1440p / 4K. Old `[radar].position` becomes the corner offset.
* **Menu:** Qt is the primary menu surface; the ImGui menu remains as a fallback. Window size, last panel, theme, and live hotkeys are persisted through config.
* **Aimbot black-screen bug:** root cause was the menu's window-X close path leaving the overlay non-click-through. Fixed by syncing `overlay.set_click_through(not menu.visible)` every frame in `Menu.render`.
* **Awareness:** added `[awareness].rear_only = true` and `rear_fov_deg`. The vignette is suppressed when the enemy who's aimed at us is also inside our forward FOV &mdash; eliminates the constant flicker against any teammate-of-line-of-sight enemy.
* **Glow:** added `[glow].miss_grace_frames` (default 6). Pawns are only un-glowed once they've been absent from the entity-list scan for that many consecutive frames, eliminating the per-frame off/on flicker caused by transient read misses.
* **Radar:** defaults realigned to overlay CS2's built-in minimap (top-left, `size_fraction = 0.16`, `position = [15, 50]`, `range = 2200`).
* **Config autosave:** any value mutated through the menu marks the root `Config` dirty; `Config.autosave_if_due()` is called every frame in `main.py`. Debounced by `general.autosave_interval_sec`. Manual *Save config now* button is still present.
* **Start-menu-visible:** `general.start_menu_visible` (default `true`) controls whether the Qt menu is shown on launch. The configured menu hotkey (default `Ctrl+J`) toggles it afterwards.
* **Crosshair:** thickness slider exposed; `draw_crosshair` already reads from `config.visuals.crosshair` every frame so all changes are live.
* **Anti-detection controls:** `read_only_mode`, `hide_from_taskbar`, `auto_capture_protect_on_focus_loss`, `randomize_window_title`, module spoofing, read obfuscation, and window-class masquerading are wired through TOML, the Qt panel, and their live or startup runtime paths.
