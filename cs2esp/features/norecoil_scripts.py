"""
No-recoil script generator for supported mouse/keyboard vendors.

Logitech gets a G HUB Lua script; every other supported vendor uses the
shared AHK v2 generator. Public callers use VENDORS, build_script(vendor_id),
and suggested_filename(vendor_id).
"""

from typing import Callable, NamedTuple


# Per-bullet (dx, dy, delay_ms) compensation vectors calibrated for sensitivity 1.0/m_pitch 0.022; scripts play them while LMB is held and reset on release.

Pattern = list[tuple[int, int, int]]  # (dx, dy, delay_ms)


PATTERNS: dict[str, Pattern] = {}

# Rifles: AK-47, RPM 600 (100 ms/shot), 30-round mag. Vertical climb shots 2-7, left drift 5-9, right drift 10-16, zigzag tail.
PATTERNS["ak47"] = [
    ( 0, 0, 100),
    ( 0, 55, 100), ( 0, 68, 100), ( -2, 78, 100),
    ( -5, 85, 100), ( -8, 86, 100), (-12, 82, 100),
    (-15, 72, 100), (-15, 56, 100), (-12, 40, 100),
    ( -6, 28, 100), ( 2, 22, 100), ( 10, 18, 100),
    ( 18, 14, 100), ( 24, 10, 100), ( 26, 6, 100),
    ( 24, 2, 100), ( 18, -2, 100), ( 8, -2, 100),
    ( -4, 0, 100), (-14, 4, 100), (-22, 6, 100),
    (-26, 4, 100), (-24, 0, 100), (-16, -2, 100),
    ( -6, 0, 100), ( 6, 2, 100), ( 16, 2, 100),
    ( 22, 0, 100), ( 22, -2, 100),
]

# M4A4, RPM 666 (90 ms/shot), 30 mag. ~30% weaker vertical than AK; sideways drift comparable.
PATTERNS["m4a4"] = [
    ( 0, 0, 90),
    ( 0, 38, 90), ( 0, 48, 90), ( -1, 56, 90),
    ( -3, 60, 90), ( -6, 60, 90), ( -9, 56, 90),
    (-11, 48, 90), (-11, 36, 90), ( -8, 26, 90),
    ( -3, 20, 90), ( 4, 16, 90), ( 11, 12, 90),
    ( 16, 8, 90), ( 20, 4, 90), ( 20, 0, 90),
    ( 16, -2, 90), ( 8, -2, 90), ( -2, 0, 90),
    (-10, 2, 90), (-16, 4, 90), (-20, 4, 90),
    (-20, 2, 90), (-14, -1, 90), ( -6, 0, 90),
    ( 4, 2, 90), ( 12, 2, 90), ( 18, 0, 90),
    ( 18, -2, 90), ( 14, -2, 90),
]

# M4A1-S, RPM 600 (100 ms), 20-round mag, tighter pattern than M4A4.
PATTERNS["m4a1s"] = [
    ( 0, 0, 100),
    ( 0, 34, 100), ( 0, 44, 100), ( -1, 52, 100),
    ( -2, 56, 100), ( -4, 56, 100), ( -7, 52, 100),
    (-10, 44, 100), (-10, 32, 100), ( -6, 22, 100),
    ( -2, 16, 100), ( 4, 14, 100), ( 10, 10, 100),
    ( 14, 6, 100), ( 16, 2, 100), ( 14, 0, 100),
    ( 10, -2, 100), ( 4, -2, 100), ( -2, 0, 100),
    ( -8, 2, 100),
]

# FAMAS (RPM 666 \u2192 90 ms, 25 mag).
PATTERNS["famas"] = [
    ( 0, 0, 90),
    ( 0, 36, 90), ( -1, 50, 90), ( -3, 60, 90),
    ( -6, 62, 90), ( -9, 56, 90), (-11, 44, 90),
    ( -9, 30, 90), ( -4, 20, 90), ( 2, 16, 90),
    ( 8, 14, 90), ( 14, 10, 90), ( 18, 6, 90),
    ( 20, 2, 90), ( 16, -2, 90), ( 8, -2, 90),
    ( -2, 0, 90), (-10, 2, 90), (-16, 2, 90),
    (-18, 1, 90), (-14, -1, 90), ( -6, 0, 90),
    ( 4, 2, 90), ( 12, 1, 90), ( 16, -1, 90),
]

# Galil AR, RPM 666 (90 ms), 35 mag. More horizontal spread than AK, slightly lower peak pull.
PATTERNS["galil"] = [
    ( 0, 0, 90),
    ( 0, 44, 90), ( -1, 58, 90), ( -4, 66, 90),
    ( -8, 68, 90), (-12, 62, 90), (-15, 50, 90),
    (-13, 36, 90), ( -6, 24, 90), ( 2, 18, 90),
    ( 10, 14, 90), ( 18, 10, 90), ( 22, 6, 90),
    ( 24, 2, 90), ( 18, -2, 90), ( 8, -2, 90),
    ( -4, 0, 90), (-14, 2, 90), (-22, 2, 90),
    (-24, 0, 90), (-18, -2, 90), ( -8, -2, 90),
    ( 4, 0, 90), ( 14, 2, 90), ( 22, 0, 90),
    ( 22, -2, 90), ( 14, -2, 90), ( 4, 0, 90),
    ( -6, 2, 90), (-14, 2, 90),
]

# AUG, RPM 666 (90 ms), scoped M4 variant.
PATTERNS["aug"] = [
    ( 0, 0, 90),
    ( 0, 36, 90), ( 0, 46, 90), ( -1, 54, 90),
    ( -3, 58, 90), ( -6, 56, 90), ( -9, 48, 90),
    ( -8, 36, 90), ( -4, 24, 90), ( 2, 18, 90),
    ( 8, 14, 90), ( 14, 10, 90), ( 18, 6, 90),
    ( 18, 2, 90), ( 14, 0, 90), ( 8, -2, 90),
    ( 0, -2, 90), ( -6, 0, 90), (-12, 2, 90),
    (-16, 2, 90), (-14, 0, 90), ( -8, -2, 90),
    ( 0, -2, 90), ( 6, 0, 90), ( 12, 2, 90),
]

# SG 553, AK's CT scoped equivalent. Same magnitudes as AK, slower (RPM 545 -> 110 ms/shot).
PATTERNS["sg553"] = [
    ( 0, 0, 110),
    ( 0, 52, 110), ( 0, 64, 110), ( -2, 74, 110),
    ( -5, 80, 110), ( -8, 80, 110), (-12, 74, 110),
    (-14, 64, 110), (-14, 50, 110), (-10, 36, 110),
    ( -4, 26, 110), ( 4, 20, 110), ( 12, 16, 110),
    ( 18, 12, 110), ( 22, 8, 110), ( 22, 4, 110),
    ( 18, 0, 110), ( 12, -2, 110), ( 4, -2, 110),
    ( -6, 0, 110), (-14, 2, 110), (-20, 4, 110),
]

# SMGs are flatter than rifles: less vertical punch, more horizontal wander, and faster cycle times.
PATTERNS["mac10"] = [
    ( 0, 0, 75),
    ( 0, 30, 75), ( -1, 38, 75), ( -3, 42, 75),
    ( -6, 40, 75), ( -8, 34, 75), ( -8, 24, 75),
    ( -4, 16, 75), ( 2, 12, 75), ( 8, 10, 75),
    ( 14, 6, 75), ( 16, 2, 75), ( 14, 0, 75),
    ( 8, -2, 75), ( 0, -2, 75), ( -8, 0, 75),
    (-14, 2, 75), (-16, 2, 75), (-12, 0, 75),
    ( -4, -2, 75), ( 4, 0, 75), ( 12, 2, 75),
    ( 16, 0, 75), ( 12, -2, 75), ( 4, -2, 75),
]
PATTERNS["mp9"] = [
    ( 0, 0, 70),
    ( 0, 24, 70), ( 0, 30, 70), ( -2, 34, 70),
    ( -4, 34, 70), ( -6, 28, 70), ( -6, 20, 70),
    ( -3, 12, 70), ( 2, 8, 70), ( 6, 6, 70),
    ( 10, 4, 70), ( 12, 2, 70), ( 10, 0, 70),
    ( 6, -2, 70), ( 0, -2, 70), ( -6, 0, 70),
    (-10, 2, 70), (-12, 1, 70), ( -8, 0, 70),
    ( -2, -2, 70), ( 4, 0, 70), ( 8, 2, 70),
    ( 10, 0, 70), ( 6, -2, 70),
]
PATTERNS["mp7"] = [
    ( 0, 0, 80),
    ( 0, 26, 80), ( 0, 34, 80), ( -2, 38, 80),
    ( -5, 38, 80), ( -8, 32, 80), ( -8, 22, 80),
    ( -4, 14, 80), ( 2, 10, 80), ( 8, 8, 80),
    ( 14, 6, 80), ( 16, 2, 80), ( 12, 0, 80),
    ( 6, -2, 80), ( -2, -2, 80), ( -8, 0, 80),
    (-14, 2, 80), (-14, 1, 80), (-10, 0, 80),
    ( -2, -2, 80), ( 6, 0, 80), ( 12, 2, 80),
    ( 14, 0, 80), ( 10, -2, 80),
]
PATTERNS["mp5sd"] = [
    ( 0, 0, 80),
    ( 0, 28, 80), ( 0, 36, 80), ( -2, 40, 80),
    ( -5, 38, 80), ( -8, 32, 80), ( -9, 22, 80),
    ( -5, 14, 80), ( 2, 10, 80), ( 9, 8, 80),
    ( 14, 6, 80), ( 16, 2, 80), ( 12, 0, 80),
    ( 6, -2, 80), ( -2, -2, 80), ( -9, 0, 80),
    (-14, 2, 80), (-14, 1, 80), (-10, 0, 80),
    ( -2, -2, 80), ( 6, 0, 80), ( 12, 2, 80),
    ( 14, 0, 80), ( 10, -2, 80),
]
PATTERNS["ump45"] = [
    ( 0, 0, 90),
    ( 0, 32, 90), ( 0, 42, 90), ( -2, 48, 90),
    ( -5, 46, 90), ( -8, 38, 90), ( -8, 26, 90),
    ( -4, 16, 90), ( 2, 12, 90), ( 8, 10, 90),
    ( 14, 6, 90), ( 14, 2, 90), ( 10, 0, 90),
    ( 4, -2, 90), ( -4, -2, 90), (-10, 0, 90),
    (-14, 2, 90), (-14, 1, 90), ( -8, -1, 90),
    ( 0, -2, 90), ( 8, 0, 90), ( 12, 2, 90),
    ( 14, 0, 90), ( 10, -2, 90), ( 2, -2, 90),
]
PATTERNS["p90"] = [
    ( 0, 0, 68),
    ( 0, 22, 68), ( 0, 28, 68), ( -1, 30, 68),
    ( -3, 30, 68), ( -5, 26, 68), ( -6, 18, 68),
    ( -4, 12, 68), ( 0, 8, 68), ( 4, 6, 68),
    ( 8, 4, 68), ( 10, 2, 68), ( 8, 0, 68),
    ( 4, -2, 68), ( -2, -2, 68), ( -6, 0, 68),
    ( -8, 2, 68), ( -8, 1, 68), ( -4, -1, 68),
    ( 2, -2, 68), ( 6, 0, 68), ( 8, 2, 68),
    ( 8, 0, 68), ( 4, -2, 68), ( 0, -2, 68),
    ( -4, 0, 68), ( -6, 2, 68), ( -8, 1, 68),
    ( -6, 0, 68), ( 0, -2, 68),
]
PATTERNS["bizon"] = [
    ( 0, 0, 80),
    ( 0, 24, 80), ( 0, 32, 80), ( -2, 36, 80),
    ( -5, 36, 80), ( -8, 30, 80), ( -8, 22, 80),
    ( -4, 14, 80), ( 2, 10, 80), ( 8, 8, 80),
    ( 12, 6, 80), ( 14, 2, 80), ( 10, 0, 80),
    ( 4, -2, 80), ( -2, -2, 80), ( -8, 0, 80),
    (-12, 2, 80), (-12, 1, 80), ( -8, -1, 80),
    ( -2, -2, 80), ( 4, 0, 80), ( 10, 2, 80),
    ( 12, 0, 80), ( 8, -2, 80), ( 2, -2, 80),
]

# LMGs
PATTERNS["m249"] = [
    ( 0, 0, 80),
    ( 0, 36, 80), ( -1, 50, 80), ( -4, 60, 80),
    ( -8, 62, 80), (-12, 56, 80), (-14, 42, 80),
    (-10, 28, 80), ( -2, 18, 80), ( 6, 14, 80),
    ( 14, 10, 80), ( 20, 6, 80), ( 22, 2, 80),
    ( 18, -2, 80), ( 8, -2, 80), ( -4, 0, 80),
    (-14, 2, 80), (-22, 4, 80), (-22, 2, 80),
    (-16, 0, 80), ( -4, -2, 80), ( 6, 0, 80),
    ( 16, 2, 80), ( 22, 0, 80), ( 18, -2, 80),
    ( 6, -2, 80), ( -6, 0, 80), (-16, 2, 80),
    (-20, 0, 80), (-12, -2, 80),
]
PATTERNS["negev"] = [
    ( 0, 0, 75),
    ( 0, 44, 75), ( -2, 58, 75), ( -6, 68, 75),
    (-12, 68, 75), (-16, 58, 75), (-14, 42, 75),
    ( -6, 26, 75), ( 4, 18, 75), ( 12, 14, 75),
    ( 20, 10, 75), ( 24, 4, 75), ( 22, 0, 75),
    ( 14, -2, 75), ( 2, -2, 75), (-10, 0, 75),
    (-20, 2, 75), (-24, 2, 75), (-20, 0, 75),
    ( -8, -2, 75), ( 4, 0, 75), ( 16, 2, 75),
    ( 22, 0, 75), ( 18, -2, 75), ( 6, -2, 75),
    ( -8, 0, 75), (-18, 2, 75), (-20, 0, 75),
    (-12, -2, 75),
]

# Pistols use only light per-shot pull-down for fast semi-auto taps.
PATTERNS["pistol"] = [
    ( 0, 0, 110),
    ( 0, 10, 110), ( 0, 10, 110), ( 0, 8, 120),
    ( 0, 8, 120), ( 0, 6, 130), ( 0, 6, 130),
    ( 0, 4, 140), ( 0, 4, 140),
]

# Single-shot weapons: explicit no-op so weapon switches don't leave the last rifle pattern playing.
for noop in ("awp", "ssg08", "scar20", "g3sg1",
             "deagle", "usp", "p2000", "revolver",
             "nova", "mag7", "sawedoff", "xm1014",
             "knife", "grenade"):
    PATTERNS[noop] = [(0, 0, 100)]

# Normalize recorded gun drift into compensation vectors and fill shot-one recoil.
for weapon_name, pattern in PATTERNS.items():
    shifted = pattern
    if len(pattern) >= 2 and pattern[0][:2] == (0, 0):
        shifted = [
            (
                int(round(pattern[1][0] * 0.5)),
                int(round(pattern[1][1] * 0.5)),
                pattern[0][2],
            ),
            *pattern[1:],
        ]
    PATTERNS[weapon_name] = [
        (int(round(-dx * 0.70)), dy, delay)
        for dx, dy, delay in shifted
    ]
del weapon_name, pattern, shifted, noop

# Stable generation order keeps output diffs clean.
ALL_WEAPONS: list[str] = [
    "ak47", "m4a4", "m4a1s", "famas", "galil", "aug", "sg553",
    "mac10", "mp9", "mp7", "mp5sd", "ump45", "p90", "bizon",
    "m249", "negev", "pistol", "awp", "ssg08", "scar20", "g3sg1",
    "deagle", "usp", "p2000", "revolver", "nova", "mag7", "sawedoff",
    "xm1014", "knife", "grenade",
]


# Logitech G HUB Lua script
def _build_logitech_lua() -> str:
    """Return a complete G HUB Lua script with spray patterns."""
    weapon_blocks: list[str] = []
    for name in ALL_WEAPONS:
        rows: list[str] = []
        chunk: list[str] = []
        for dx, dy, delay in PATTERNS[name]:
            chunk.append(f"{{{dx:>4},{dy:>4},{delay:>4}}}")
            if len(chunk) == 4:
                rows.append(" " + ", ".join(chunk) + ",")
                chunk = []
        if chunk:
            rows.append(" " + ", ".join(chunk) + ",")
        body = "\n".join(rows)
        weapon_blocks.append(
            f'PATTERNS["{name}"] = {{\n{body}\n}}\n'
        )
    patterns_section = "\n".join(weapon_blocks)

    return f"""-- ========================================================================
-- CS2 No-Recoil script \u2014 Logitech G HUB (Lua)
-- Generated by \u00b7 CS2 ESP \u00b7 build
--
-- HOW TO INSTALL
-- 1. Open G HUB.
-- 2. Click your mouse \u2192 \"GAMES & APPLICATIONS\" \u2192 \"Add\"
-- \u2192 pick cs2.exe.
-- 3. With the cs2.exe profile selected, click \"ASSIGNMENTS\"
-- \u2192 \"SYSTEM\" \u2192 \"Scripting\" \u2192 New Lua Script.
-- 4. Paste THIS WHOLE FILE in. Save (Ctrl+S). Close the editor.
-- 5. Press Caps Lock to enable compensation in-game; press
-- Caps Lock again to disable. (G HUB can only poll the
-- Caps/Num/Scroll lock keys \u2014 the rest of the keyboard
-- is opaque to it.)
--
-- WEAPON SELECTION
-- G HUB can't see your keyboard 1/2/3 weapon-select presses.
-- Instead, the script defaults to whatever you set
-- ACTIVE_PRIMARY to below (default “ak47”). To switch between
-- common rifles WITHOUT alt-tabbing to G HUB, use your mouse
-- side-buttons:
-- Mouse button 4 (back) → primary slot (sends “1” to CS2)
-- Mouse button 5 (front) → pistol slot (sends “2” to CS2)
-- the side buttons now use PressKey(“1”) /
-- PressKey(“2”) so CS2 actually swaps the equipped weapon.
-- Old behaviour (rounds 13-37) only flipped the internal
-- pattern variable, leaving the player still holding the
-- knife / nade they had out.
-- For SMGs / Galil / SG 553 / etc., edit ACTIVE_PRIMARY before
-- the round or remap the buttons in the OnEvent handler at the
-- bottom of this file.
--
-- TUNING TO YOUR SENS (fix — IMPORTANT)
-- Patterns are calibrated for CS2 sens 1.0, m_pitch 0.022.
-- The scaling formula is:
--
-- MULT = 1.0 / your_in_game_sens
--
-- default: MULT = 0.50 — calibrated baseline
-- on sens 1.0 showed full magnitudes pulled the
-- crosshair below the target on long sprays (over-compensation
-- of the late tail). Half-strength brings the late tail back to
-- neutral while still cancelling ∼ 80 % of shots 2–7.
--
-- Sens 1.0 → MULT = 0.50 (baseline setup, leave default)
-- Sens 2.0 → MULT = 0.25
-- Sens 0.5 → MULT = 1.00
-- Sens 1.5 → MULT = 0.33
--
-- HARDWARE DPI IS IRRELEVANT. The script writes raw HID
-- counts directly to CS2's Raw Input layer; the conversion
-- to degrees is sens × m_pitch only. A 400 DPI user and
-- a 3200 DPI user on the SAME in-game sens use the SAME MULT.
--
-- If compensation feels weak or strong overall after matching
-- your sens, nudge MULT empirically by \u00b110\u201320 %.
--
-- WHAT THIS IS / IS NOT
-- This is *static spray-pattern compensation* \u2014 it plays
-- the known recoil pattern of each weapon while LMB is held.
-- It runs inside G HUB's sandboxed Lua VM, NOT in cs2.exe's
-- address space. No DLL injection, no memory reads, no IPC.
-- The motion is delivered through the Logitech HID driver
-- (signed by Microsoft) as raw mouse movement. It stays outside
-- the game process; use only in environments where such scripts
-- are allowed.
-- ========================================================================

EnablePrimaryMouseButtonEvents(true)

-- ── User-tunable constants ──────────────────────────────────────────
local MULT = 0.50 -- scale: see TUNING header
local USE_SIDE_BUTTONS = true -- false = ignore mouse 4/5 entirely
local PRIMARY = \"ak47\" -- compensation pattern for the primary rifle
local SECONDARY = \"m4a1s\" -- compensation pattern for the secondary rifle
local CAPS_KEY = \"capslock\" -- universal on/off for the LAST USED rifle
-- --------------------------------------------------------------------

-- Pattern tables. Each entry = {{dx, dy, delay_ms}}.
-- dx, dy = per-bullet mouse-count delta (positive dy = pull DOWN).
-- delay_ms = milliseconds to wait BEFORE the next entry. The
-- first entry fires immediately on LMB-press.
local PATTERNS = {{}}

{patterns_section}

-- ── state machine ──────────────────────────────────────────
-- The Lua script tracks four bools that together describe what the
-- user is doing:
--
-- current_weapon \"primary\" | \"secondary\"
-- Which of the two configured rifles the script
-- BELIEVES the player has equipped right now.
-- Updated by mouse 4 (sets to \"primary\"),
-- mouse 5 (sets to \"secondary\"). Lua can't see
-- keyboard 1/2 presses so it relies on the side
-- buttons + the user actually swapping in CS2 to
-- the matching rifle.
-- last_used \"primary\" | \"secondary\"
-- Which rifle was MOST RECENTLY toggled. Drives
-- CapsLock targeting - CapsLock flips norecoil
-- on/off for whichever rifle is in last_used.
-- Defaults to \"primary\" so a fresh CapsLock
-- press without any prior side-button activity
-- enables the AK pattern.
-- primary_on norecoil active for the PRIMARY rifle
-- secondary_on norecoil active for the SECONDARY rifle
-- Mutually exclusive: turning one on always turns
-- the other off (CS2 only equips one rifle at a
-- time, so playing two patterns at once would
-- fight itself).
local current_weapon = \"primary\"
local last_used = \"primary\"
local primary_on = false
local secondary_on = false
local last_caps = nil

local function active_pattern_key()
    if current_weapon == \"primary\" and primary_on then return PRIMARY end
    if current_weapon == \"secondary\" and secondary_on then return SECONDARY end
    return nil
end

local function announce()
    -- Print exactly the rifle the user just toggled, so the log
    -- mirrors what they see in their head: \"<rifle> norecoil on\"
    -- or \"<rifle> norecoil off\". broke this by mixing
    -- panic-pause messages into the same stream; keeps
    -- panic out of this announce path entirely.
    local target, on
    if last_used == \"primary\" then
        target = PRIMARY
        on = primary_on
    else
        target = SECONDARY
        on = secondary_on
    end
    OutputLogMessage(target .. \" norecoil \" ..
                     (on and \"on\" or \"off\") .. \"\\n\")
end

local function press_slot1()
    -- Both PRIMARY and SECONDARY live in CS2 slot 1 (the rifle
    -- slot). Sending \"1\" makes CS2 actually equip whichever rifle
    -- is in the active loadout - matching what the user expects
    -- when they hit the side button.
    PressKey(\"1\")
    Sleep(20)
    ReleaseKey(\"1\")
end

local function on_g4()
    -- Mouse 4 = primary rifle. Two cases:
    -- We were on secondary -> SWITCH to primary + turn norecoil
    -- ON for primary. Sends \"1\" so CS2 equips the rifle.
    -- We were already on primary -> just toggle primary_on.
    if current_weapon == \"secondary\" then
        current_weapon = \"primary\"
        primary_on = true
        secondary_on = false
        press_slot1()
    else
        primary_on = not primary_on
        secondary_on = false
    end
    last_used = \"primary\"
    announce()
end

local function on_g5()
    -- Mouse 5 = secondary rifle. Mirror image of on_g4.
    if current_weapon == \"primary\" then
        current_weapon = \"secondary\"
        secondary_on = true
        primary_on = false
        press_slot1()
    else
        secondary_on = not secondary_on
        primary_on = false
    end
    last_used = \"secondary\"
    announce()
end

local function on_caps_change()
    -- CapsLock = universal on/off for the LAST USED rifle.
    -- Defaults to primary if the user hasn't touched the side
    -- buttons yet.
    if last_used == \"primary\" then
        primary_on = not primary_on
        secondary_on = false
    else
        secondary_on = not secondary_on
        primary_on = false
    end
    announce()
end

function OnEvent(event, arg)
    -- ── Side-button handling (toggleable) ──────────────────────────
    -- We listen for BOTH event types so the script works whether
    -- the side button is bound as a system mouse button (Mouse 4 /
    -- Mouse 5 -> MOUSE_BUTTON_PRESSED with arg 4 / 5) or as a
    -- Logitech G-key (G4 / G5 -> G_PRESSED with arg 4 / 5).
    -- many Logitech mice route them through G_PRESSED unless the
    -- profile remaps them. Handling both means "Mouse 5" can never
    -- silently route to a different button.
    if USE_SIDE_BUTTONS then
        if (event == \"MOUSE_BUTTON_PRESSED\" and arg == 4)
                or (event == \"G_PRESSED\" and arg == 4) then
            on_g4()
        elseif (event == \"MOUSE_BUTTON_PRESSED\" and arg == 5)
                or (event == \"G_PRESSED\" and arg == 5) then
            on_g5()
        end
    end

    -- ── CapsLock universal toggle (state-change polled) ────────────
    -- G HUB's Lua VM cannot subscribe to keyboard events outside
    -- Logitech keyboards. We instead poll the lock state on every
    -- OnEvent call (any mouse button / wheel / move triggers
    -- OnEvent, so the polling rate is plenty for catching a
    -- CapsLock press).
    local caps = IsKeyLockOn(CAPS_KEY)
    if last_caps == nil then
        last_caps = caps
    elseif caps ~= last_caps then
        last_caps = caps
        on_caps_change()
    end

    -- ── LMB held: walk the active pattern ──────────────────────────
    if event == \"MOUSE_BUTTON_PRESSED\" and arg == 1 then
        local key = active_pattern_key()
        if not key then return end
        local pattern = PATTERNS[key]
        if not pattern then return end
        for i = 1, #pattern do
            if not IsMouseButtonPressed(1) then break end
            -- Re-check active state every bullet so flipping the
            -- toggle MID-spray (e.g. CapsLock disengage) breaks
            -- out cleanly instead of finishing the magazine on
            -- stale state.
            if active_pattern_key() ~= key then break end
            local dx = math.floor(pattern[i][1] * MULT + 0.5)
            local dy = math.floor(pattern[i][2] * MULT + 0.5)
            local ms = pattern[i][3]
            if dx ~= 0 or dy ~= 0 then
                MoveMouseRelative(dx, dy)
            end
            Sleep(ms)
        end
    end
end
"""


# AHK v2 script (Razer / SteelSeries / Corsair / others)
def _build_ahk_universal() -> str:
    """Return one AHK v2 script for all non-Logitech vendors."""
    weapon_blocks: list[str] = []
    for name in ALL_WEAPONS:
        rows: list[str] = []
        chunk: list[str] = []
        for dx, dy, delay in PATTERNS[name]:
            chunk.append(f"[{dx:>4},{dy:>4},{delay:>4}]")
            if len(chunk) == 4:
                rows.append(" " + ", ".join(chunk) + ",")
                chunk = []
        if chunk:
            rows.append(" " + ", ".join(chunk) + ",")
        body = "\n".join(rows)
        weapon_blocks.append(
            f'patterns["{name}"] := [\n{body}\n]\n'
        )
    patterns_section = "\n".join(weapon_blocks)

    return f"""; ========================================================================
; CS2 No-Recoil script \u2014 AutoHotkey v2 (universal)
; Generated by \u00b7 CS2 ESP \u00b7 build
;
; WORKS FOR: Razer, SteelSeries, Corsair, Roccat, Glorious, every
; non-Logitech vendor that doesn't ship a custom Lua-script runtime
; on the mouse firmware. All of them route through the same OS HID
; layer, so one AHK file covers every brand.
;
; HOW TO INSTALL
; 1. Install AutoHotkey v2 from https://www.autohotkey.com/v2/
; 2. Save this file as cs2_norecoil.ahk.
; 3. Double-click it. The green H icon appears in the system tray.
; 4. In CS2: Raw Input ON (it's on by default).
; 5. Press Caps Lock in-game to enable compensation; press it
; again to pause. The Caps Lock LED reflects the state.
;
; WEAPON SELECTION
; Number-row 1 \u2192 primary rifle (default AK-47)
; Number-row 2 \u2192 pistol
; Number-row 3 \u2192 knife (no compensation)
; Mouse button 4 (XButton1, back) \u2192 same as pressing 1
; Mouse button 5 (XButton2, front) \u2192 same as pressing 2
; Edit ACTIVE_PRIMARY below to swap between rifles (M4A4,
; M4A1-S, FAMAS, Galil, AUG, SG 553, MAC-10, MP9, etc).
;
; TUNING TO YOUR SENS (fix \u2014 IMPORTANT)
; Patterns are calibrated for CS2 sens 1.0, m_pitch 0.022.
; The scaling formula is:
;
; MULT := 1.0 / your_in_game_sens
;
; default: MULT := 0.50 \u2014 see Lua header for
; why we halved the baseline. Half-strength tracks the calibrated
; sens 1.0 baseline better than full
; magnitudes (which pulled crosshair BELOW target on the
; late tail).
;
; Sens 1.0 \u2192 MULT := 0.50 (baseline setup, leave default)
; Sens 2.0 \u2192 MULT := 0.25
; Sens 0.5 \u2192 MULT := 1.00
; Sens 1.5 \u2192 MULT := 0.33
;
; HARDWARE DPI IS IRRELEVANT. The script writes raw HID
; counts via DllCall(mouse_event), CS2 reads them via Raw
; Input and converts to degrees with sens * m_pitch only.
; A 400 DPI user and a 3200 DPI user on the SAME in-game
; sens use the SAME MULT.
;
; Nudge MULT \u00b110\u201320 % if compensation feels weak or
; strong overall after matching your sens.
;
; SAFETY
; No DLL injection, no memory reads of cs2.exe, no IPC into the
; game. AHK delivers raw mouse counts to the OS HID stack; CS2
; receives them as mouse input from outside the game process. Use
; only in environments where external scripts are allowed.
; ========================================================================

#Requires AutoHotkey v2.0
#SingleInstance Force

; ── User-tunable constants ────────────────────────────────────────
global MULT := 0.50 ; sens-scale; see TUNING header
global USE_SIDE_BUTTONS := true ; false = mouse 4/5 do nothing
global PRIMARY := \"ak47\" ; pattern for the primary rifle
global SECONDARY := \"m4a1s\" ; pattern for the secondary rifle
; ──────────────────────────────────────────────────────────────────

; state machine - matches the Logitech Lua version 1:1.
; current_weapon \"primary\" / \"secondary\"
; Tracks which rifle the user has equipped. Updated by:
; - mouse 4 -> sets to \"primary\"
; - mouse 5 -> sets to \"secondary\"
; - keyboard 1 -> sets to \"primary\" (the user usually has the AK
; in slot 1 so number-row 1 maps that way)
; AHK CAN poll the keyboard, so unlike Lua we DO update on
; the number-row presses.
; last_used \"primary\" / \"secondary\"
; CapsLock target. Defaults to \"primary\".
; primary_on / secondary_on
; Mutually-exclusive on/off flags for each rifle's pattern.
global current_weapon := \"primary\"
global last_used := \"primary\"
global primary_on := false
global secondary_on := false

; Pattern tables. Each entry = [dx, dy, delay_ms]. dy > 0 pulls down.
global patterns := Map()

{patterns_section}

ActiveKey() {{
    global current_weapon, primary_on, secondary_on, PRIMARY, SECONDARY
    if (current_weapon = \"primary\" && primary_on)
        return PRIMARY
    if (current_weapon = \"secondary\" && secondary_on)
        return SECONDARY
    return \"\"
}}

Announce() {{
    ; Print exactly the rifle the user just toggled - matches the
    ; Lua \"<rifle> norecoil on/off\" format the user asked for.
    global last_used, primary_on, secondary_on, PRIMARY, SECONDARY
    if (last_used = \"primary\") {{
        target := PRIMARY
        on := primary_on
    }} else {{
        target := SECONDARY
        on := secondary_on
    }}
    OutputDebug(target . \" norecoil \" . (on ? \"on\" : \"off\") . \"`n\")
}}

OnG4() {{
    ; Mouse 4 = primary rifle.
    ; if on secondary: SWITCH to primary + norecoil ON for primary
    ; if on primary : toggle primary_on
    global current_weapon, primary_on, secondary_on, last_used
    if (current_weapon = \"secondary\") {{
        current_weapon := \"primary\"
        primary_on := true
        secondary_on := false
        Send \"1\" ; equip the rifle in CS2 slot 1
    }} else {{
        primary_on := !primary_on
        secondary_on := false
    }}
    last_used := \"primary\"
    Announce()
}}

OnG5() {{
    ; Mouse 5 = secondary rifle. Mirror of OnG4.
    global current_weapon, primary_on, secondary_on, last_used
    if (current_weapon = \"primary\") {{
        current_weapon := \"secondary\"
        secondary_on := true
        primary_on := false
        Send \"1\"
    }} else {{
        secondary_on := !secondary_on
        primary_on := false
    }}
    last_used := \"secondary\"
    Announce()
}}

OnCapsChange() {{
    ; CapsLock = universal on/off for the LAST USED rifle.
    global last_used, primary_on, secondary_on
    if (last_used = \"primary\") {{
        primary_on := !primary_on
        secondary_on := false
    }} else {{
        secondary_on := !secondary_on
        primary_on := false
    }}
    Announce()
}}

; ── Hotkeys (only when CS2 is the foreground window) ──────────────
#HotIf WinActive(\"Counter-Strike 2\")

; Number-row 1 sends \"1\" AND tells the script we're now on the
; primary rifle. Number-row 2 / 3 send through unmodified - they
; don't update current_weapon because the user might be switching
; to pistol / knife and we don't want to clobber the rifle state.
~1:: {{
    global current_weapon
    current_weapon := \"primary\"
}}
2:: Send \"2\"
3:: Send \"3\"

~XButton1:: {{
    global USE_SIDE_BUTTONS
    if (!USE_SIDE_BUTTONS)
        return
    OnG4()
}}
~XButton2:: {{
    global USE_SIDE_BUTTONS
    if (!USE_SIDE_BUTTONS)
        return
    OnG5()
}}

~CapsLock:: {{
    ; CapsLock runs the universal on/off. AHK fires the hotkey
    ; on key DOWN; the lock state itself flips slightly later, so
    ; we just call OnCapsChange directly - it doesn't read the
    ; lock LED, it walks last_used / primary_on / secondary_on.
    OnCapsChange()
}}

~LButton:: {{
    global MULT, patterns
    key := ActiveKey()
    if (key = \"\")
        return
    if (!patterns.Has(key))
        return
    pat := patterns[key]
    n := pat.Length
    if (n = 0)
        return
    i := 1
    while (i <= n) {{
        if (!GetKeyState(\"LButton\", \"P\"))
            break
        ; Re-check active state every bullet so a CapsLock or
        ; side-button flip mid-spray drops out cleanly.
        if (ActiveKey() != key)
            break
        entry := pat[i]
        dx := Round(entry[1] * MULT)
        dy := Round(entry[2] * MULT)
        ms := entry[3]
        if (dx != 0 || dy != 0)
            DllCall(\"mouse_event\", \"UInt\", 0x01,
                                    \"Int\", dx,
                                    \"Int\", dy,
                                    \"UInt\", 0, \"UPtr\", 0)
        Sleep ms
        i += 1
    }}
}}

#HotIf
"""


# Per-vendor HTML guide copy rendered by the Qt panel's RichText label.

_GUIDE_LOGITECH = """\
<h3>Logitech G HUB \u2014 how to install</h3>
<ol>
<li>Make sure you have the latest <b>Logitech G HUB</b> (not the
older <i>Logitech Gaming Software</i> \u2014 it uses a slightly
different Lua dialect).</li>
<li>Plug in your Logitech mouse. In G HUB, click it, then click
<b>\u201cScripting\u201d</b> (the scroll icon in the left rail).
If you don't see it, enable it under G HUB Settings \u2192
Advanced \u2192 Scripting.</li>
<li>Create a new script, name it <code>CS2 No-Recoil</code>,
and paste the whole file from the <b>Copy</b> button above.</li>
<li>Save. Toggle <b>Caps Lock ON</b> in-game to enable the
script; <b>off</b> to pause it.</li>
</ol>
<p><b>Important:</b> G HUB's Lua runtime is sandboxed \u2014 it
can only read your mouse's state and move the mouse. It cannot
talk to CS2 or to this app; use it only where external scripts
are allowed.
</p>
<p><b>Tuning (corrected):</b> patterns are calibrated
for CS2 sens 1.0 with <code>m_pitch 0.022</code>. Edit the line
near the top of the script:</p>
<pre>local MULT = 0.50 -- default; nudge \u00b110-20 % to taste</pre>
<p>Sens 1.0 \u2192 <code>MULT = 0.5</code> (calibrated baseline).
Sens 2.0 \u2192 <code>MULT = 0.25</code>. Sens 0.5 \u2192
<code>MULT = 1.0</code>.</p>
<p><b>Hardware DPI is irrelevant</b> \u2014 the script writes
raw HID counts directly to CS2's Raw Input layer, and CS2's
counts\u2192degrees conversion uses only sens \u00d7 m_pitch.
Whether your mouse is 400 or 3200 DPI, the same in-game sens
needs the same MULT.</p>
"""

_GUIDE_AHK = """\
<h3>AHK (Razer / SteelSeries / Corsair / other vendors)</h3>
<p>Razer Synapse, SteelSeries GG, Corsair iCUE and the smaller
vendors (Roccat, Glorious, Pulsar\u2026) don't ship a Lua runtime
on the mouse firmware the way Logitech G HUB does. The clean
path on all of them is the same: a sidecar
<b>AutoHotkey v2</b> script at the OS HID layer.</p>
<ol>
<li>Install AutoHotkey v2 from
<a href=\"https://www.autohotkey.com/v2/\">autohotkey.com/v2</a>.
Pick the v2 installer \u2014 v1 syntax is incompatible.</li>
<li>Save the generated <code>cs2_norecoil.ahk</code> file
anywhere on disk (Downloads is fine).</li>
<li>Double-click it. A green H icon appears in the system tray
confirming the script is loaded.</li>
<li>In CS2 keep <b>Raw Input ON</b> (it's the default). Press
<b>Caps Lock</b> in-game to enable compensation; press it again
to pause. The Caps Lock LED on your keyboard reflects the state.
</li>
<li>Continue using your vendor's app for lighting, DPI shift
buttons and macros \u2014 they don't conflict with AHK at all.
</li>
</ol>
<p><b>Why not the vendor's native macro editor?</b> Razer
Synapse 3, SteelSeries GG and Corsair iCUE all use fixed-sequence
macro playback. None of them can loop on live LMB state, break on
release, or read keyboard state to switch weapons \u2014 all
three are mandatory for per-bullet recoil compensation. AHK does
all of them with one script.</p>
<p><b>Tuning (corrected):</b> calibrated for CS2
sens 1.0 with <code>m_pitch 0.022</code>. Edit the line near
the top of the script:</p>
<pre>global MULT := 0.50 ; default; nudge \u00b110-20 % to taste</pre>
<p>Sens 1.0 \u2192 <code>MULT := 0.5</code> (calibrated baseline).
Sens 2.0 \u2192 <code>MULT := 0.25</code>. Sens 0.5 \u2192
<code>MULT := 1.0</code>.</p>
<p><b>Hardware DPI is irrelevant</b> \u2014 AHK sends raw HID
counts through <code>DllCall("mouse_event")</code> and CS2's
Raw Input converts them to degrees using sens \u00d7 m_pitch
only. A 400 DPI user and a 3200 DPI user on the same in-game
sens use the same MULT.</p>
"""


class _Vendor(NamedTuple):
    id: str
    display: str
    language: str  # "Lua" or "AHK"
    extension: str  # ".lua" or ".ahk"
    builder: Callable[[], str]
    guide_html: str


VENDORS: tuple[_Vendor, ...] = (
    _Vendor(
        id="logitech",
        display="Logitech G HUB",
        language="Lua",
        extension=".lua",
        builder=_build_logitech_lua,
        guide_html=_GUIDE_LOGITECH,
    ),
    # Razer/SteelSeries/Corsair collapse into one universal AHK card; script is identical for every non-Logitech mouse since the OS HID layer doesn't care about brand.
    _Vendor(
        id="ahk",
        display="Razer, SteelSeries, Corsair & others",
        language="AHK v2",
        extension=".ahk",
        builder=_build_ahk_universal,
        guide_html=_GUIDE_AHK,
    ),
)


_BY_ID: dict[str, _Vendor] = {v.id: v for v in VENDORS}


def get_vendor(vendor_id: str) -> _Vendor:
    """Look up a vendor registration. Raises KeyError on miss."""
    return _BY_ID[vendor_id]


def build_script(vendor_id: str) -> str:
    """Render the finished script text for vendor_id."""
    return get_vendor(vendor_id).builder()


def suggested_filename(vendor_id: str) -> str:
    """Default file name for QFileDialog save prompts."""
    v = get_vendor(vendor_id)
    suffix = "" if v.id == "ahk" else f"_{v.id}"
    return f"cs2_norecoil{suffix}{v.extension}"


__all__ = [
    "VENDORS", "get_vendor", "build_script",
    "suggested_filename", "ALL_WEAPONS", "PATTERNS",
]
