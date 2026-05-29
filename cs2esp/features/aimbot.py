"""
Smooth dwViewAngles writer with guarded WPM output.

The aimbot selects the closest in-cone enemy bone, eases each flick over
configured write steps, and optionally adds a small perpendicular arc. All
writes honor the live read-only safety gate.
"""

import math
from dataclasses import dataclass
from random import choice, uniform
from struct import pack

from cs2esp.entity import Player
from cs2esp.memory import MemoryReader
from cs2esp.offsets import Netvars, Offsets


@dataclass(frozen=True, slots=True)
class AimFrame:
    """Per-frame immutable adapter consumed by WPMAimbot."""
    local_pawn: int
    local_view_angles: tuple[float, float]
    local_eye: tuple[float, float, float]
    enemies: list[Player]


# Bone heights above origin (feet), measured live 2026-07-10. Fallback for when real bones haven't healed yet.
_FALLBACK_BONE_HEIGHTS: dict[int, float] = {
    Netvars.BONE_HEAD: 64.0,
    Netvars.BONE_CHEST: 54.0,
    Netvars.BONE_PELVIS: 38.0,
}


@dataclass(frozen=True, slots=True)
class _AimTarget:
    """Per-tick selected target."""

    pawn_addr: int
    bone_label: str
    angles: tuple[float, float]
    delta_deg: float


# Bone passes per priority option. New option = one entry here + one dropdown row.
_BONE_PRIORITY: dict[str, tuple[tuple[int, str], ...]] = {
    "head": (
        (Netvars.BONE_HEAD, "head"),
        (Netvars.BONE_CHEST, "chest"),
        (Netvars.BONE_PELVIS, "pelvis"),
    ),
    "body": (
        (Netvars.BONE_CHEST, "chest"),
        (Netvars.BONE_PELVIS, "pelvis"),
    ),
}


class WPMAimbot:
    def __init__(
        self,
        config,
        mem: MemoryReader,
    ) -> None:
        self.config = config
        self.cfg = config.aimbot
        self.mem = mem
        # Per-flick interpolation state.
        self._cursor: int = 0
        self._flick_start: tuple[float, float] | None = None
        self._d_pitch_full: float = 0.0
        self._d_yaw_full: float = 0.0
        self._delta_mag: float = 0.0
        self._flick_perp_sign: int = 1
        self._flick_curv_mag: float = 0.0
        self._last_target_id: tuple[int, str] = (0, "")
        self._was_active: bool = False

    def update(
        self,
        players: list[Player],
        local_pawn: int,
        local_team: int,
        local_pos: tuple[float, float, float],
        active: bool,
    ) -> None:
        """Run one frame of target selection and smooth interpolation."""
        # Falling edge: next press starts fresh from current view angles.
        if self._was_active and not active:
            self._cursor = 0
            self._last_target_id = (0, "")
        self._was_active = active

        if not active or local_pawn == 0:
            return

        try:
            view_angles = self.mem.read_vec3(self.mem.client(Offsets.dwViewAngles))
        except Exception:
            view_angles = (0.0, 0.0, 0.0)

        eye: tuple[float, float, float] = local_pos
        try:
            scene_node = self.mem.read_long(local_pawn + Netvars.m_pGameSceneNode)
            if scene_node:
                origin = self.mem.read_vec3(scene_node + Netvars.m_vecAbsOrigin)
                view_offset = self.mem.read_vec3(local_pawn + Netvars.m_vecViewOffset)
                eye = tuple(
                    origin[index] + view_offset[index]
                    for index in range(3)
                )
        except Exception:
            pass

        enemies: list[Player] = []
        for player in players:
            if not (player.pawn_addr
                    and player.team != local_team
                    and player.health > 0
                    and player.life_state == 0):
                continue
            if not player.bones:
                # Fall back to synthetic targets until real bone data is available.
                if not any(player.position):
                    continue
                player.bones = {
                    bone: (
                        player.position[0],
                        player.position[1],
                        player.position[2] + height,
                    )
                    for bone, height in _FALLBACK_BONE_HEIGHTS.items()
                }
            enemies.append(player)

        aim_frame = AimFrame(
            local_pawn,
            (view_angles[0], view_angles[1]),
            eye,
            enemies,
        )
        target = self._select_target(aim_frame)
        if target is None:
            return

        # Target switch re-rolls per-flick params next step.
        target_id = (target.pawn_addr, target.bone_label)
        if target_id != self._last_target_id:
            self._cursor = 0
            self._last_target_id = target_id

        self._smooth_write(aim_frame, target)

    def _select_target(self, frame: AimFrame) -> _AimTarget | None:
        """Select the closest in-cone target from the bone-priority table."""
        current_view = frame.local_view_angles
        max_fov = float(getattr(self.cfg, "max_fov_deg", 30.0))
        priority = _BONE_PRIORITY.get(
            str(getattr(self.cfg, "bone_priority", "head")),
            _BONE_PRIORITY["head"],
        )

        # Dedicated head pass first, so body bones can't win while a head is in cone.
        if priority and priority[0][1] == "head":
            head_bone = priority[0][0]
            best_head: _AimTarget | None = None
            for enemy in frame.enemies:
                bone_position = enemy.bones.get(head_bone)
                if bone_position is None:
                    continue
                pitch, yaw = _angles_to(frame.local_eye, bone_position)
                angular_distance = _angular_distance(current_view, (pitch, yaw))
                if angular_distance > max_fov:
                    continue
                if best_head is None or angular_distance < best_head.delta_deg:
                    best_head = _AimTarget(
                        pawn_addr=int(enemy.pawn_addr),
                        bone_label="head",
                        angles=(pitch, yaw),
                        delta_deg=angular_distance,
                    )
            if best_head is not None:
                return best_head
            scan = priority[1:]
        else:
            scan = priority

        # Body fallback or body priority: closest in-cone bone wins.
        best: _AimTarget | None = None
        for enemy in frame.enemies:
            for bone_index, label in scan:
                bone_position = enemy.bones.get(bone_index)
                if bone_position is None:
                    continue
                pitch, yaw = _angles_to(frame.local_eye, bone_position)
                angular_distance = _angular_distance(current_view, (pitch, yaw))
                if angular_distance > max_fov:
                    continue
                if best is None or angular_distance < best.delta_deg:
                    best = _AimTarget(
                        pawn_addr=int(enemy.pawn_addr),
                        bone_label=label,
                        angles=(pitch, yaw),
                        delta_deg=angular_distance,
                    )
        return best

    def _smooth_write(self, frame: AimFrame, target: _AimTarget) -> None:
        """Cosine ease-in-out interpolation toward target.angles."""
        steps = max(1, int(getattr(self.cfg, "smooth_steps", 6)))
        if self._cursor >= steps:
            return  # flick complete; hold on target

        current_pitch, current_yaw = frame.local_view_angles
        tgt_pitch, tgt_yaw = target.angles

        # One-time per-flick setup.
        if self._cursor == 0:
            self._flick_start = (current_pitch, current_yaw)
            self._d_pitch_full = tgt_pitch - current_pitch
            # Yaw seam wrap: shortest signed arc in [-180, +180].
            self._d_yaw_full = (tgt_yaw - current_yaw + 540.0) % 360.0 - 180.0
            self._delta_mag = math.hypot(self._d_pitch_full, self._d_yaw_full)
            humanize = bool(getattr(self.cfg, "humanize_path", True))
            curvature = float(getattr(self.cfg, "curvature", 0.0))
            if humanize and curvature > 0.0:
                self._flick_perp_sign = choice((-1, 1))
                self._flick_curv_mag = (
                    curvature * self._delta_mag * uniform(0.5, 1.0)
                )
            else:
                self._flick_curv_mag = 0.0

        assert self._flick_start is not None
        start_pitch, start_yaw = self._flick_start
        full_pitch_delta = self._d_pitch_full
        full_yaw_delta = self._d_yaw_full

        step_number = self._cursor + 1
        eased = 0.5 * (1.0 - math.cos(math.pi * step_number / steps))

        pos_pitch = start_pitch + full_pitch_delta * eased
        pos_yaw = start_yaw + full_yaw_delta * eased

        # Zero at both endpoints, peaks mid-flick.
        bump = self._flick_perp_sign * self._flick_curv_mag * math.sin(math.pi * eased)
        if self._delta_mag > 1e-6:
            perp_pitch = (-full_yaw_delta / self._delta_mag) * bump
            perp_yaw = (full_pitch_delta / self._delta_mag) * bump
        else:
            perp_pitch = 0.0
            perp_yaw = 0.0

        jitter_deg = float(getattr(self.cfg, "jitter_deg", 0.05))
        pitch_jitter = uniform(-jitter_deg, jitter_deg)
        yaw_jitter = uniform(-jitter_deg, jitter_deg)

        cand_pitch = pos_pitch + perp_pitch + pitch_jitter
        cand_yaw = pos_yaw + perp_yaw + yaw_jitter

        # Clamps against fixed flick-start geometry, not the drifting committed view.
        cand_pitch = _clamp_no_overshoot(start_pitch, tgt_pitch, cand_pitch)
        cand_pitch = max(cand_pitch, start_pitch) if full_pitch_delta >= 0 else min(cand_pitch, start_pitch)
        cand_yaw = _clamp_no_overshoot_yaw(start_yaw, tgt_yaw, cand_yaw)

        if bool(getattr(self.config.anti_detection, "read_only_mode", False)):
            return
        try:
            payload = pack("<3f", float(cand_pitch), float(cand_yaw), 0.0)
            self.mem.pm.write_bytes(
                self.mem.client(Offsets.dwViewAngles), payload, 12,
            )
        except Exception:
            return
        self._cursor += 1


def _angles_to(
    eye: tuple[float, float, float],
    point: tuple[float, float, float],
) -> tuple[float, float]:
    """Pitch/yaw in degrees from eye to point under the dwViewAngles convention."""
    delta_x = point[0] - eye[0]
    delta_y = point[1] - eye[1]
    delta_z = point[2] - eye[2]
    flat_distance = math.hypot(delta_x, delta_y)
    if flat_distance < 1e-6:
        return (-90.0 if delta_z > 0 else 90.0, 0.0)
    pitch = -math.degrees(math.atan2(delta_z, flat_distance))
    yaw = math.degrees(math.atan2(delta_y, delta_x))
    return (pitch, yaw)


def _angular_distance(
    cur: tuple[float, float],
    target: tuple[float, float],
) -> float:
    """Combined yaw/pitch angular distance (degrees) with yaw seam wrap."""
    pitch_delta = target[0] - cur[0]
    yaw_delta = (target[1] - cur[1] + 540.0) % 360.0 - 180.0
    return math.hypot(pitch_delta, yaw_delta)


def _clamp_no_overshoot(cur: float, tgt: float, candidate: float) -> float:
    """Project candidate onto [cur, tgt] so pitch never crosses past tgt."""
    if tgt >= cur:
        return min(candidate, tgt)
    return max(candidate, tgt)


def _clamp_no_overshoot_yaw(cur: float, tgt: float, candidate: float) -> float:
    """Yaw-aware no-overshoot clamp using the shortest-arc direction."""
    arc_signed = (tgt - cur + 540.0) % 360.0 - 180.0  # in [-180, +180]
    if arc_signed == 0.0:
        return tgt
    # Anchored on target so diametric 180-degree flicks clamp correctly.
    past_signed = (candidate - tgt + 540.0) % 360.0 - 180.0
    if (
        (arc_signed > 0.0 and past_signed >= 0.0)
        or (arc_signed < 0.0 and past_signed <= 0.0)
    ):
        return tgt
    candidate_signed = (candidate - cur + 540.0) % 360.0 - 180.0
    if arc_signed > 0.0:
        clamped = max(0.0, candidate_signed)
    else:
        clamped = min(0.0, candidate_signed)
    return cur + clamped
