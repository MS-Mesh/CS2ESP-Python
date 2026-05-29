"""
Vanilla triggerbot for engine-raycast and overlay-assisted hit tests.

Mouse buttons still register through SendInput/mouse_event even when Raw Input
owns view movement. Timing uses reaction delay, jitter, a global click floor,
and per-target cooldowns; wall checks come from m_iIDEntIndex or overlay modes.
"""

import ctypes
import math
from pathlib import Path
from random import uniform
from time import perf_counter, sleep, strftime

from cs2esp.offsets import Offsets, Netvars
from cs2esp.visuals.bone_extensions import iter_targets, iter_segments
from cs2esp.visuals.math_utils import world_to_screen
from cs2esp.visuals.chams_pixel_probe import (
    get_alpha as get_chams_probe_alpha,
    get_last_probe_age_s as get_chams_probe_age_s,
)
from cs2esp.visuals.skeleton import compute_head_outline_polygon


# Per-segment world half-widths for the fat-skeleton silhouette test.
_SILHOUETTE_SEGMENTS: tuple[tuple[int, int, float], ...] = (
    (Netvars.BONE_CHEST, Netvars.BONE_SPINE, 11.0),
    (Netvars.BONE_SPINE, Netvars.BONE_PELVIS, 10.0),
    (Netvars.BONE_L_CLAV, Netvars.BONE_R_CLAV, 7.0),
    (Netvars.BONE_L_THIGH, Netvars.BONE_R_THIGH, 7.0),
    (Netvars.BONE_CHEST, Netvars.BONE_NECK, 5.0),
    (Netvars.BONE_NECK, Netvars.BONE_HEAD, 4.0),
    (Netvars.BONE_L_CLAV, Netvars.BONE_L_SHOULDER, 5.0),
    (Netvars.BONE_L_SHOULDER, Netvars.BONE_L_ELBOW, 4.0),
    (Netvars.BONE_L_ELBOW, Netvars.BONE_L_HAND, 3.5),
    (Netvars.BONE_R_CLAV, Netvars.BONE_R_SHOULDER, 5.0),
    (Netvars.BONE_R_SHOULDER, Netvars.BONE_R_ELBOW, 4.0),
    (Netvars.BONE_R_ELBOW, Netvars.BONE_R_HAND, 3.5),
    (Netvars.BONE_PELVIS, Netvars.BONE_L_THIGH, 5.0),
    (Netvars.BONE_L_THIGH, Netvars.BONE_L_KNEE, 5.5),
    (Netvars.BONE_L_KNEE, Netvars.BONE_L_FOOT, 4.0),
    (Netvars.BONE_PELVIS, Netvars.BONE_R_THIGH, 5.0),
    (Netvars.BONE_R_THIGH, Netvars.BONE_R_KNEE, 5.5),
    (Netvars.BONE_R_KNEE, Netvars.BONE_R_FOOT, 4.0),
)


def crosshair_on_fat_skeleton(
    bones: dict, view_matrix,
    screen_w: int, screen_h: int,
    eye: tuple[float, float, float],
    cx_px: float, cy_px: float,
    extra_pad_px: float = 0.0,
    tightness: float = 1.0,
) -> bool:
    """True iff (cx_px, cy_px) sits inside the painted body silhouette."""
    if tightness <= 0.0:
        tightness = 0.0
    focal = screen_w * 0.5
    for a_idx, b_idx, half_width_world in _SILHOUETTE_SEGMENTS:
        a = bones.get(a_idx)
        b = bones.get(b_idx)
        if a is None or b is None:
            continue
        sa = world_to_screen(a, view_matrix, screen_w, screen_h)
        sb = world_to_screen(b, view_matrix, screen_w, screen_h)
        if sa is None or sb is None:
            continue
        mx = (a[0] + b[0]) * 0.5
        my = (a[1] + b[1]) * 0.5
        mz = (a[2] + b[2]) * 0.5
        dx = mx - eye[0]
        dy = my - eye[1]
        dz = mz - eye[2]
        depth = math.sqrt(dx * dx + dy * dy + dz * dz)
        if depth < 1.0:
            depth = 1.0
        pad_px = (half_width_world * tightness) * focal / depth + extra_pad_px
        segment_x = sb[0] - sa[0]
        segment_y = sb[1] - sa[1]
        segment_length_sq = segment_x ** 2 + segment_y ** 2
        if segment_length_sq < 1e-6:
            closest_x, closest_y = sa
        else:
            along = (
                (cx_px - sa[0]) * segment_x
                + (cy_px - sa[1]) * segment_y
            ) / segment_length_sq
            along = max(0.0, min(1.0, along))
            closest_x = sa[0] + segment_x * along
            closest_y = sa[1] + segment_y * along
        distance_px = math.hypot(
            cx_px - closest_x,
            cy_px - closest_y,
        )
        if distance_px < pad_px:
            return True
    return False


def _convex_hull_2d(points: list[tuple[float, float]]) -> list[tuple[float, float]]:
    """Andrew's monotone-chain CCW hull. <3 points returns input."""
    pts = sorted(set(points))
    if len(pts) < 3:
        return pts

    def cross(o, a, b):
        return (a[0] - o[0]) * (b[1] - o[1]) - (a[1] - o[1]) * (b[0] - o[0])

    lower = []
    for p in pts:
        while len(lower) >= 2 and cross(lower[-2], lower[-1], p) <= 0:
            lower.pop()
        lower.append(p)
    upper = []
    for p in reversed(pts):
        while len(upper) >= 2 and cross(upper[-2], upper[-1], p) <= 0:
            upper.pop()
        upper.append(p)
    return lower[:-1] + upper[:-1]


def _point_in_convex_polygon(px: float, py: float,
                             polygon: list[tuple[float, float]]) -> bool:
    """Cross-product point-in-polygon test for a CCW convex polygon.

    Edge cases (cross == 0) count as inside.
    """
    n = len(polygon)
    if n < 3:
        return False
    for i in range(n):
        ax, ay = polygon[i]
        bx, by = polygon[(i + 1) % n]
        cross = (bx - ax) * (py - ay) - (by - ay) * (px - ax)
        if cross < 0.0:
            return False
    return True


def _select_wh_mode(config) -> str:
    """Pick which hit-test the WH-compliant triggerbot uses."""
    if not bool(getattr(config.wallhack, "enabled", True)):
        return "default"
    glow_sec = getattr(config, "glow", None)
    if glow_sec is not None and bool(getattr(glow_sec, "enabled", False)):
        return "glow"
    visuals = getattr(config, "visuals", None)
    if visuals is None:
        return "default"
    if bool(getattr(getattr(visuals, "chams", None), "enabled", False)):
        return "chams"
    skeleton = getattr(visuals, "skeleton", None)
    if (skeleton is not None and bool(getattr(skeleton, "enabled", False))
            and bool(getattr(skeleton, "head_outline_enabled", False))):
        return "skel_head"
    return "default"


# Item-def indices for bolt-action snipers. allow_scoped=false blocks firing whenever one is equipped (zoomed or not). Item-def is stable across CS2 patches; m_bIsScoped drifted on the current build. AUG/SG553 excluded — they hipfire normally.
_SCOPED_WEAPON_IDS = frozenset((9, 40))  # AWP, SSG08


_user32 = ctypes.windll.user32

# SendInput is hardware-level (Raw Input safe). mouse_event is legacy fallback for builds that drop SendInput-injected buttons.
_INPUT_MOUSE = 0
_MOUSEEVENTF_LEFTDOWN = 0x0002
_MOUSEEVENTF_LEFTUP = 0x0004


class _MOUSEINPUT(ctypes.Structure):
    _fields_ = [
        ("dx", ctypes.c_long),
        ("dy", ctypes.c_long),
        ("mouseData", ctypes.c_uint32),
        ("dwFlags", ctypes.c_uint32),
        ("time", ctypes.c_uint32),
        ("dwExtraInfo", ctypes.c_ulonglong),
    ]


class _INPUT(ctypes.Structure):
    _fields_ = [("type", ctypes.c_uint32), ("mi", _MOUSEINPUT)]


def _click() -> None:
    """Fire one left-click via both SendInput and legacy mouse_event.

    CS2 fires once per frame so the dual path is harmless; the
    redundancy guarantees the click lands on every tested build.
    """
    down = _INPUT()
    down.type = _INPUT_MOUSE
    down.mi = _MOUSEINPUT(0, 0, 0, _MOUSEEVENTF_LEFTDOWN, 0, 0)
    up = _INPUT()
    up.type = _INPUT_MOUSE
    up.mi = _MOUSEINPUT(0, 0, 0, _MOUSEEVENTF_LEFTUP, 0, 0)
    arr = (_INPUT * 2)(down, up)
    _user32.SendInput(2, arr, ctypes.sizeof(_INPUT))
    _user32.mouse_event(_MOUSEEVENTF_LEFTDOWN, 0, 0, 0, 0)
    sleep(0.005)
    _user32.mouse_event(_MOUSEEVENTF_LEFTUP, 0, 0, 0, 0)


def _angle_to(target, origin, view_yaw_deg: float, view_pitch_deg: float) -> float:
    """Combined yaw/pitch angle from view direction to target."""
    dx = target[0] - origin[0]
    dy = target[1] - origin[1]
    dz = target[2] - origin[2]
    hyp = math.sqrt(dx * dx + dy * dy)
    if hyp < 0.0001:
        return 999.0
    target_yaw = math.degrees(math.atan2(dy, dx))
    target_pitch = -math.degrees(math.atan2(dz, hyp))
    yaw_diff = (target_yaw - view_yaw_deg + 540.0) % 360.0 - 180.0
    pitch_diff = target_pitch - view_pitch_deg
    return math.sqrt(yaw_diff * yaw_diff + pitch_diff * pitch_diff)


def _ray_hits_sphere(
    origin: tuple[float, float, float],
    direction: tuple[float, float, float],
    center: tuple[float, float, float],
    radius: float,
) -> bool:
    """True iff the ray from ``origin`` along unit ``direction`` pierces the
    sphere at ``center`` with ``radius``. A sphere behind the view never hits.
    """
    if radius <= 0.0:
        return False
    ox = center[0] - origin[0]
    oy = center[1] - origin[1]
    oz = center[2] - origin[2]
    # Distance along the ray to the sphere-center's closest approach.
    t = ox * direction[0] + oy * direction[1] + oz * direction[2]
    if t < 0.0:
        return False
    # Closest point on the ray, and its squared distance to the center.
    cx = origin[0] + direction[0] * t
    cy = origin[1] + direction[1] * t
    cz = origin[2] + direction[2] * t
    dx = center[0] - cx
    dy = center[1] - cy
    dz = center[2] - cz
    return (dx * dx + dy * dy + dz * dz) <= radius * radius


def view_direction(
    view_yaw_deg: float,
    view_pitch_deg: float,
) -> tuple[float, float, float]:
    """Unit forward vector for CS2 view angles (dwViewAngles convention)."""
    pitch = math.radians(view_pitch_deg)
    yaw = math.radians(view_yaw_deg)
    cos_pitch = math.cos(pitch)
    return (
        cos_pitch * math.cos(yaw),
        cos_pitch * math.sin(yaw),
        -math.sin(pitch),
    )


# CS2 unit → metre. 1 unit ≈ 1 inch (engine-correct).
_UNITS_PER_METRE = 39.37

# Feet-to-eye rise for a standing pawn (m_vecAbsOrigin sits at the feet).
_EYE_Z = 60.0


# Fallback distance bands when cfg.distance_bands is missing/empty. (min_m, max_m, fov_deg, skeleton_fov_deg).
_FALLBACK_BANDS: tuple[tuple[float, float, float, float], ...] = (
    (4.0, 9999.0, 0.5, 0.5),  # long
    (2.0, 4.0,    2.0, 1.0),  # mid
    (0.5, 2.0,    4.0, 2.0),  # close
    (0.0, 0.5,    8.0, 4.0),  # point-blank
)


def _scale_fov_for_distance(cfg, base_pt: float, base_seg: float,
                            dist_units: float,
                            is_scoped: bool = False) -> tuple[float, float]:
    """Distance-banded auto-scaling of trigger FOV gates."""
    m = dist_units / _UNITS_PER_METRE

    pt_band: float | None = None
    seg_band: float | None = None
    bands = getattr(cfg, "distance_bands", None)
    if isinstance(bands, list):
        for band in bands:
            try:
                lo = float(band.get("min_m", 0.0))
                hi = float(band.get("max_m", 9999.0))
                if lo <= m < hi:
                    pt_band = float(band.get("fov_deg", base_pt))
                    seg_band = float(band.get("skeleton_fov_deg", base_seg))
                    break
            except (TypeError, ValueError, AttributeError):
                continue
    if pt_band is None or seg_band is None:
        for lo, hi, pt, seg in _FALLBACK_BANDS:
            if lo <= m < hi:
                pt_band = pt
                seg_band = seg
                break
    if pt_band is None or seg_band is None:
        pt_band, seg_band = base_pt, base_seg

    if not is_scoped:
        return pt_band, seg_band

    mode = str(getattr(cfg, "scoped_band_mode", "off")).lower()
    if mode == "off":
        return pt_band, seg_band

    mult = float(getattr(cfg, "scoped_band_multiplier", 0.25))
    if mode == "segment_only":
        return pt_band, base_seg
    if mode == "point_only":
        return base_pt, seg_band
    if mode == "both_off":
        return base_pt, base_seg
    if mode == "both_scaled":
        return pt_band * mult, seg_band * mult
    # Unknown mode -> behave as "off".
    return pt_band, seg_band


class Triggerbot:
    def __init__(self, mem, entity_system, config):
        self.mem = mem
        self.entities = entity_system
        self.config = config
        # Per-target state so we don't spam clicks at the same head.
        self._sighted_at: float | None = None
        self._sighted_idx: int = 0
        self._fired_at: float = 0.0
        self._last_fired_idx: int = 0
        # Cached scope state so transient read failures don't flip the band damper mid-fire.
        self._last_scoped: bool = False
        self._scope_drift_warned = False
        self._last_item_def = -1
        self._weapon_walk_warned = False
        # Deduped state-change diagnostic.
        self._glow_last_msg: str = ""
        # File sink: temp/trigger.log (stdout is hidden by default).
        self._log_path: Path | None = None
        try:
            log_dir = Path(__file__).resolve().parents[2] / "temp"
            log_dir.mkdir(parents=True, exist_ok=True)
            self._log_path = log_dir / "trigger.log"
            self._log_path.write_text(
                f"# trigger log started {strftime('%Y-%m-%d %H:%M:%S')}\n",
                encoding="utf-8",
            )
        except Exception:
            self._log_path = None

    def _glow_diag(self, msg: str) -> None:
        """Append a deduped state-transition line to temp/trigger.log."""
        if msg == self._glow_last_msg:
            return
        self._glow_last_msg = msg
        if self._log_path is not None:
            try:
                with self._log_path.open("a", encoding="utf-8") as fh:
                    fh.write(f"{strftime('%H:%M:%S')} {msg}\n")
            except Exception:
                pass

    def _reset_sight_state(self) -> None:
        self._sighted_at = None
        self._sighted_idx = 0

    def update(
        self,
        players=None,
        local_pos=None,
        view_matrix=None,
        screen_w: int = 0,
        screen_h: int = 0,
    ) -> None:
        """Select a hit-test path and deliver at most one click."""
        cfg = self.config.triggerbot
        if not cfg.enabled:
            self._reset_sight_state()
            self._glow_diag("disabled (cfg.enabled=False)")
            return

        local_pawn = self.entities.get_local_pawn()
        if not local_pawn:
            self._glow_diag("no local_pawn (not in match)")
            return

        # Gate on stable weapon identity and retain the last scope flag across transient read failures.
        holding_scoped = False
        try:
            weapon_services = self.mem.read_long(
                local_pawn + Netvars.m_pWeaponServices
            )
            active_weapon = (
                self.mem.read_uint(
                    weapon_services + Netvars.m_hActiveWeapon
                )
                if weapon_services
                else 0
            )
            entity_list = (
                self.mem.read_long(self.mem.client(Offsets.dwEntityList))
                if active_weapon
                else 0
            )
            weapon_list_entry = (
                self.mem.read_long(
                    entity_list
                    + 0x8 * ((active_weapon & 0x7FFF) >> 9)
                    + 0x10
                )
                if entity_list
                else 0
            )
            weapon = (
                self.mem.read_long(
                    weapon_list_entry + 0x70 * (active_weapon & 0x1FF)
                )
                if weapon_list_entry
                else 0
            )
            if weapon:
                item_definition = self.mem.read_int(
                    weapon
                    + Netvars.m_AttributeManager
                    + Netvars.m_Item
                    + Netvars.m_iItemDefinitionIndex
                ) & 0xFFFF
                if item_definition != self._last_item_def:
                    self._last_item_def = item_definition
                    print(
                        f"[trigger] weapon=0x{weapon:X} "
                        f"item_def={item_definition} "
                        f"scoped={item_definition in _SCOPED_WEAPON_IDS} "
                        f"(handle=0x{active_weapon:X})"
                    )
                holding_scoped = item_definition in _SCOPED_WEAPON_IDS
        except Exception as error:
            if not self._weapon_walk_warned:
                print(f"[trigger] scoped-weapon walk failed: {error!r}")
                self._weapon_walk_warned = True
        if (holding_scoped
                and not bool(getattr(cfg, "allow_scoped", True))):
            self._reset_sight_state()
            self._glow_diag("scoped weapon held + allow_scoped=off (blocked)")
            return
        # Glow/chams modes fire on what's painted on screen (engine ID-index / chams pixel probe), skipping the raycast wall check. Legacy modes (skel_head/default) read m_iIDEntIndex.
        if bool(getattr(cfg, "wh_compliant", True)):
            wh_mode = _select_wh_mode(self.config)
        else:
            wh_mode = "default"
        self._glow_diag(f"mode={wh_mode}")
        glow_mode = wh_mode == "glow"
        silhouette_mode = wh_mode == "chams"

        if not (silhouette_mode or glow_mode):
            # idx == 0 means "nothing solid under the crosshair".
            try:
                target_idx = self.mem.read_int(
                    local_pawn + Netvars.m_iIDEntIndex
                )
            except Exception:
                return
            if target_idx <= 0:
                self._reset_sight_state()
                return

        if local_pos is None:
            local_pos = self.entities.get_local_position()
        if not local_pos or all(v == 0.0 for v in local_pos):
            return

        local_team = self.entities.get_local_team()
        if players is None:
            try:
                players = self.entities.get_players(read_bones=False)
            except Exception:
                return

        # Standing pawn: feet at m_vecAbsOrigin, head_top at +60.
        eye = (local_pos[0], local_pos[1], local_pos[2] + _EYE_Z)

        # Glow short-circuit: m_iIDEntIndex is CS2's own crosshair raycast result (non-zero = entity hitbox).
        if glow_mode:
            try:
                target_idx = self.mem.read_int(
                    local_pawn + Netvars.m_iIDEntIndex)
            except Exception:
                self._glow_diag("read_idx_failed")
                return
            if target_idx <= 0:
                self._reset_sight_state()
                self._glow_diag(f"idx={target_idx} (no entity)")
                return
            tgt_pawn = self.entities.entity_index_to_pawn(target_idx)
            if not tgt_pawn:
                self._reset_sight_state()
                self._glow_diag(f"idx={target_idx} resolve_failed")
                return
            matched = next(
                (player for player in players if player.pawn_addr == tgt_pawn),
                None,
            )
            if matched is None:
                self._reset_sight_state()
                self._glow_diag(
                    f"idx={target_idx} pawn=0x{tgt_pawn:X} not_in_players "
                    f"(n={len(players)})")
                return
            if matched.team == local_team:
                self._reset_sight_state()
                self._glow_diag(f"idx={target_idx} teammate")
                return
            if matched.health <= 0 or matched.life_state != 0:
                self._reset_sight_state()
                self._glow_diag(
                    f"idx={target_idx} dead (hp={matched.health} "
                    f"ls={matched.life_state})")
                return
            self._glow_diag(
                f"idx={target_idx} FIRE (hp={matched.health})")
            if self._head_only_blocks(cfg, tgt_pawn, players,
                                      view_matrix, screen_w, screen_h, eye):
                self._reset_sight_state()
                self._glow_diag(f"idx={target_idx} head_only: off head")
                return
            self._dispatch_fire(cfg, tgt_pawn)
            return

        # Chams short-circuit: centre-pixel alpha of our GL overlay's framebuffer, 1:1 with the painted body silhouette.
        if (
            silhouette_mode
            and view_matrix is not None
            and screen_w > 0
            and screen_h > 0
        ):
            center_x = screen_w * 0.5
            center_y = screen_h * 0.5
            probe_age = get_chams_probe_age_s()
            probe_fresh = probe_age < 0.250
            pixel_hit = (
                probe_fresh
                and get_chams_probe_alpha()
                >= int(getattr(cfg, "pixel_alpha_threshold", 24))
            )
            best_idx = 0
            best_dist = 0.0
            if pixel_hit:
                best_dist = float("inf")
                for player in players:
                    if (player.team == local_team
                            or player.health <= 0
                            or player.life_state != 0):
                        continue
                    distance = math.dist(player.position, eye)
                    if distance < best_dist:
                        best_dist = distance
                        best_idx = player.pawn_addr
            elif not probe_fresh:
                # Use geometry only before the first fresh framebuffer probe.
                inflate_pixels = float(getattr(
                    cfg, "silhouette_inflate_px", 0.0
                ))
                try:
                    skeleton_config = self.config.visuals.skeleton
                except AttributeError:
                    skeleton_config = None
                head_ring_enabled = bool(
                    skeleton_config
                    and getattr(
                        skeleton_config,
                        "head_outline_enabled",
                        False,
                    )
                )
                for player in players:
                    if (player.team == local_team
                            or player.health <= 0
                            or player.life_state != 0
                            or not player.bones):
                        continue
                    hit = crosshair_on_fat_skeleton(
                        player.bones,
                        view_matrix,
                        screen_w,
                        screen_h,
                        eye,
                        center_x,
                        center_y,
                        inflate_pixels,
                    )
                    if not hit and head_ring_enabled:
                        try:
                            polygon = compute_head_outline_polygon(
                                player,
                                view_matrix,
                                screen_w,
                                screen_h,
                                skeleton_config,
                                eye,
                            )
                        except Exception:
                            polygon = None
                        clean_points = (
                            [point for point in polygon if point is not None]
                            if polygon is not None
                            else []
                        )
                        if len(clean_points) >= 3:
                            hull = _convex_hull_2d(clean_points)
                            hit = (
                                len(hull) >= 3
                                and _point_in_convex_polygon(
                                    center_x,
                                    center_y,
                                    hull,
                                )
                            )
                    if hit:
                        best_idx = player.pawn_addr
                        best_dist = math.dist(player.position, eye)
                        break
            if best_idx == 0:
                self._reset_sight_state()
                self._glow_diag("chams: crosshair off body")
                return
            self._glow_diag(
                f"chams: pawn=0x{best_idx:X} dist={best_dist:.0f}"
            )
            if self._head_only_blocks(cfg, best_idx, players,
                                      view_matrix, screen_w, screen_h, eye):
                self._reset_sight_state()
                self._glow_diag("chams: head_only off head")
                return
            self._dispatch_fire(cfg, best_idx)
            return

        # Bone-aware path: closest point or segment inside its distance band fires.
        try:
            view = self.mem.read_vec3(self.mem.client(Offsets.dwViewAngles))
        except Exception:
            return
        view_pitch, view_yaw = float(view[0]), float(view[1])
        try:
            scope_byte = self.mem.read_int(
                local_pawn + Netvars.m_bIsScoped
            ) & 0xFF
            if scope_byte not in (0, 1):
                if not self._scope_drift_warned:
                    print(
                        f"[trigger] m_bIsScoped @ +0x{Netvars.m_bIsScoped:X} "
                        f"reads 0x{scope_byte:02X}; treating it as unscoped"
                    )
                    self._scope_drift_warned = True
                self._last_scoped = False
            else:
                self._last_scoped = bool(scope_byte)
        except Exception:
            pass
        is_scoped = self._last_scoped

        best_pt_diff = 9999.0
        best_pt_idx = 0
        best_pt_dist = 0.0
        best_seg_diff = 9999.0
        best_seg_idx = 0
        best_seg_dist = 0.0
        for p in players:
            if p.team == local_team or p.health <= 0 or p.life_state != 0:
                continue

            # Eye-to-player distance (units). One band lookup per player; band membership is constant across the skeleton.
            px, py, pz = p.position
            dxp, dyp, dzp = px - eye[0], py - eye[1], pz - eye[2]
            p_dist = math.sqrt(dxp * dxp + dyp * dyp + dzp * dzp)

            if p.bones:
                # Point-gate samples joints + synthetic extension tips (muzzle/toe/crown). polygon_density > 1 makes iter_targets interpolate extra mid-segment points for denser silhouette sampling. Slider still lives under [visuals.chams] for back-compat but is now triggerbot-only.
                density = 1
                try:
                    chams_cfg = self.config.visuals.chams
                    density = max(1, min(6, int(getattr(
                        chams_cfg, "polygon_density", 1
                    ))))
                except AttributeError:
                    pass
                for tgt in iter_targets(p.bones, density):
                    d = _angle_to(tgt, eye, view_yaw, view_pitch)
                    if d < best_pt_diff:
                        best_pt_diff = d
                        best_pt_idx = p.pawn_addr
                        best_pt_dist = p_dist
                # Segment-gate: every drawn skeleton line (incl. stubs).
                for sa, sb in iter_segments(p.bones):
                    segment_delta = tuple(
                        sb[index] - sa[index]
                        for index in range(3)
                    )
                    d = min(
                        _angle_to(
                            tuple(
                                sa[index]
                                + sample / 11.0 * segment_delta[index]
                                for index in range(3)
                            ),
                            eye,
                            view_yaw,
                            view_pitch,
                        )
                        for sample in range(12)
                    )
                    if d < best_seg_diff:
                        best_seg_diff = d
                        best_seg_idx = p.pawn_addr
                        best_seg_dist = p_dist
            else:
                # No bones → only the legacy 3-point gate runs.
                tx, ty, tz = p.position
                for off_z in (60.0, 50.0, 35.0):  # head / chest / pelvis
                    d = _angle_to((tx, ty, tz + off_z), eye,
                                  view_yaw, view_pitch)
                    if d < best_pt_diff:
                        best_pt_diff = d
                        best_pt_idx = p.pawn_addr
                        best_pt_dist = p_dist

        # Slider values are mid/long-range thresholds (>=4m); band table widens gates at closer ranges. Each gate gets its own band lookup since the closest joint and closest skeleton line can belong to different players in a chaotic scene.
        base_pt_fov = float(getattr(cfg, "fov_deg", 0.5))
        base_seg_fov = float(getattr(cfg, "skeleton_fov_deg", 0.5))
        pt_fov, _ = _scale_fov_for_distance(
            cfg, base_pt_fov, base_seg_fov, best_pt_dist, is_scoped
        )
        _, seg_fov = _scale_fov_for_distance(
            cfg, base_pt_fov, base_seg_fov, best_seg_dist, is_scoped
        )
        pt_hit = best_pt_idx != 0 and best_pt_diff <= pt_fov
        seg_hit = best_seg_idx != 0 and best_seg_diff <= seg_fov

        # skel_head bonus gate: crosshair inside the painted head ring (1:1 ellipse test). Wins on tie — if the ring is visible the shot is a head shot.
        head_idx = 0
        head_pawn_distance = 0.0
        if (wh_mode == "skel_head"
                and view_matrix
                and screen_w > 0
                and screen_h > 0):
            try:
                skeleton_config = self.config.visuals.skeleton
            except AttributeError:
                skeleton_config = None
            if (skeleton_config is not None
                    and bool(getattr(
                        skeleton_config, "head_outline_enabled", False
                    ))):
                center_x = screen_w * 0.5
                center_y = screen_h * 0.5
                closest_head_distance_sq = float("inf")
                for player in players:
                    if (player.team == local_team
                            or player.health <= 0
                            or player.life_state != 0
                            or not player.bones):
                        continue
                    polygon = compute_head_outline_polygon(
                        player,
                        view_matrix,
                        screen_w,
                        screen_h,
                        skeleton_config,
                        eye,
                    )
                    clean_points = (
                        [point for point in polygon if point is not None]
                        if polygon is not None
                        else []
                    )
                    if len(clean_points) < 3:
                        continue
                    hull = _convex_hull_2d(clean_points)
                    if (len(hull) < 3
                            or not _point_in_convex_polygon(
                                center_x,
                                center_y,
                                hull,
                            )):
                        continue
                    head = player.bones.get(Netvars.BONE_HEAD)
                    if head is None:
                        continue
                    head_distance_sq = sum(
                        (head[index] - eye[index]) ** 2
                        for index in range(3)
                    )
                    if head_distance_sq < closest_head_distance_sq:
                        closest_head_distance_sq = head_distance_sq
                        head_idx = player.pawn_addr
                        head_pawn_distance = math.dist(player.position, eye)
        if head_idx:
            best_pt_idx = head_idx
            best_pt_diff = 0.0
            best_pt_dist = head_pawn_distance
            pt_hit = True

        if not (pt_hit or seg_hit):
            self._reset_sight_state()
            self._glow_diag(
                f"legacy: miss (pt={best_pt_diff:.2f}/{pt_fov:.2f} "
                f"seg={best_seg_diff:.2f}/{seg_fov:.2f})"
            )
            return

        # Target priority: head > segment > point.
        if head_idx:
            best_idx = head_idx
            kind = "head"
        elif seg_hit:
            best_idx = best_seg_idx
            kind = "seg"
        else:
            best_idx = best_pt_idx
            kind = "pt"
        self._glow_diag(f"legacy {kind}: pawn=0x{best_idx:X}")
        if self._head_only_blocks(cfg, best_idx, players,
                                  view_matrix, screen_w, screen_h, eye):
            self._reset_sight_state()
            self._glow_diag(f"legacy {kind}: head_only off head")
            return
        self._dispatch_fire(cfg, best_idx)

    def _head_only_blocks(self, cfg, pawn_addr: int, players,
                          view_matrix, screen_w: int, screen_h: int,
                          eye) -> bool:
        """True when head-only is on and screen-centre is outside the target's
        green head ring.

        Builds the same polygon the outline draws (compute_head_outline_polygon)
        and tests the crosshair against it, so the gate matches what's on screen.
        Fails safe on an unknown pawn, missing bones, or an unbuildable polygon.
        """
        if not bool(getattr(cfg, "head_only", False)):
            return False
        if view_matrix is None or screen_w <= 0 or screen_h <= 0:
            return True
        try:
            skel_cfg = self.config.visuals.skeleton
        except AttributeError:
            return True
        target = next(
            (player for player in players if player.pawn_addr == pawn_addr),
            None,
        )
        if target is None or not target.bones:
            return True
        poly = compute_head_outline_polygon(
            target, view_matrix, screen_w, screen_h, skel_cfg, eye)
        if poly is None:
            return True
        clean = [pt for pt in poly if pt is not None]
        if len(clean) < 3:
            return True
        hull = _convex_hull_2d(clean)
        if len(hull) < 3:
            return True
        cx = screen_w * 0.5
        cy = screen_h * 0.5
        return not _point_in_convex_polygon(cx, cy, hull)

    def _dispatch_fire(self, cfg, best_idx: int) -> None:
        """Shared reaction-delay + cooldown + click flow."""
        now = perf_counter()

        # Reset reaction timer if we've changed targets.
        if best_idx != self._sighted_idx:
            self._sighted_idx = best_idx
            self._sighted_at = now
            self._glow_diag(f"sighted new target 0x{best_idx:X} (waiting)")
            return
        if self._sighted_at is None:
            self._sighted_at = now
            return

        # Wait for reaction delay before firing on a fresh target.
        delay_ms = float(getattr(cfg, "delay_ms", 25))
        jitter_ms = float(getattr(cfg, "delay_jitter_ms", 15))
        min_ms = float(getattr(cfg, "min_delay_ms", 40))
        wait = max(
            min_ms,
            delay_ms + uniform(-jitter_ms, jitter_ms),
        ) / 1000.0
        if (now - self._sighted_at) < wait:
            return

        # Per-target burst cooldown.
        cooldown_ms = float(getattr(cfg, "refire_cooldown_ms", 350))
        if (best_idx == self._last_fired_idx
                and (now - self._fired_at) * 1000.0 < cooldown_ms):
            self._glow_diag(
                f"per-target cooldown ({cooldown_ms:.0f} ms) on 0x{best_idx:X}"
            )
            return

        # Global inter-click floor: never more than ~8 shots/sec, even across different targets. Kills any auto-spam pathology.
        if (now - self._fired_at) * 1000.0 < 120.0:
            self._glow_diag("global 120ms inter-click floor")
            return

        _click()
        self._fired_at = now
        self._last_fired_idx = best_idx
        self._glow_diag(f"CLICK on 0x{best_idx:X}")
