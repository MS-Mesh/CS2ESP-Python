"""Skeleton, bone-extension, and head-outline drawing."""

import math

from cs2esp.offsets import Netvars
from cs2esp.visuals.bone_extensions import iter_extensions
from cs2esp.visuals.math_utils import world_to_screen
from cs2esp.visuals.theme import color_u32


def compute_head_sphere(player, cfg, camera_pos=None):
    """Return the shared hit-test head sphere as ``(center, radius)``."""
    head = player.bones.get(Netvars.BONE_HEAD)
    if head is None:
        return None
    radius = float(getattr(cfg, "head_outline_radius_units", 9.0))
    if radius <= 0.0:
        return None
    center = _head_center_world(player, cfg, head, camera_pos)
    width_mul = float(getattr(cfg, "head_outline_width_mul", 1.0))
    height_mul = float(getattr(cfg, "head_outline_height_mul", 1.0))
    radius_world = radius * 0.5 * (width_mul + height_mul)
    return center, radius_world


def _head_center_world(player, cfg, head, camera_pos):
    """Return the shared world-space centre of the head outline and sphere."""
    off_up = float(getattr(cfg, "head_outline_offset_up", 0.0))
    off_right = float(getattr(cfg, "head_outline_offset_right", 0.0))
    anchor_screen = bool(getattr(cfg, "head_outline_anchor_to_screen", False))
    left_clavicle = player.bones.get(Netvars.BONE_L_CLAV)
    right_clavicle = player.bones.get(Netvars.BONE_R_CLAV)

    # Auto-angular: derives offset_right from camera<->shoulder angle so one magnitude keeps the ring on the cranium from every angle.
    auto_mode = bool(getattr(cfg, "head_outline_offset_right_auto", True))
    if auto_mode and camera_pos is not None:
        magnitude = float(getattr(cfg, "head_outline_offset_right_magnitude", 1.0))
        if left_clavicle is not None and right_clavicle is not None:
            sr_x_world = right_clavicle[0] - left_clavicle[0]
            sr_y_world = right_clavicle[1] - left_clavicle[1]
            sr_mag_world = math.sqrt(sr_x_world * sr_x_world + sr_y_world * sr_y_world)
            v_x_world = head[0] - camera_pos[0]
            v_y_world = head[1] - camera_pos[1]
            v_mag_world = math.sqrt(v_x_world * v_x_world + v_y_world * v_y_world)
            if sr_mag_world > 1e-3 and v_mag_world > 1e-3:
                inv_sr = 1.0 / sr_mag_world
                inv_v = 1.0 / v_mag_world
                cos_theta = (
                    sr_x_world * v_x_world * inv_sr * inv_v
                    + sr_y_world * v_y_world * inv_sr * inv_v
                )
                off_right = -cos_theta * magnitude
                anchor_screen = True

    # Body-anchored offset = enemy local backward (perpendicular to shoulder line). BONE_HEAD is at the throat; cranium centre sits a hand-span behind it, so a body-backward shift lands on the cranium from every angle. anchor_to_screen falls back to a screen-right shift.
    enemy_backward_axis = None
    if left_clavicle is not None and right_clavicle is not None:
        rx = right_clavicle[0] - left_clavicle[0]
        ry = right_clavicle[1] - left_clavicle[1]
        bx = ry
        by = -rx
        bmag = math.sqrt(bx * bx + by * by)
        if bmag > 1e-3:
            inv_b = 1.0 / bmag
            enemy_backward_axis = (bx * inv_b, by * inv_b, 0.0)

    if anchor_screen and camera_pos is not None:
        dx = head[0] - camera_pos[0]
        dy = head[1] - camera_pos[1]
        dz = head[2] - camera_pos[2]
        fmag = math.sqrt(dx * dx + dy * dy + dz * dz)
        if fmag > 1e-3:
            inv_f = 1.0 / fmag
            fx, fy = dx * inv_f, dy * inv_f
            sr_x = fy
            sr_y = -fx
            sr_mag = math.sqrt(sr_x * sr_x + sr_y * sr_y)
            if sr_mag > 1e-3:
                inv_sr = 1.0 / sr_mag
                offset_right_axis = (sr_x * inv_sr, sr_y * inv_sr, 0.0)
            else:
                offset_right_axis = (1.0, 0.0, 0.0)
        else:
            offset_right_axis = (1.0, 0.0, 0.0)
    elif enemy_backward_axis is not None:
        offset_right_axis = enemy_backward_axis
    else:
        offset_right_axis = (1.0, 0.0, 0.0)

    return (
        head[0] + offset_right_axis[0] * off_right,
        head[1] + offset_right_axis[1] * off_right,
        head[2] + offset_right_axis[2] * off_right + off_up,
    )


def compute_head_outline_polygon(player, view_matrix, screen_w: int, screen_h: int,
                                 cfg, camera_pos=None):
    """Build the camera-facing 32-point head outline shared with hit testing."""
    head = player.bones.get(Netvars.BONE_HEAD)
    if head is None:
        return None

    radius = float(getattr(cfg, "head_outline_radius_units", 9.0))
    if radius <= 0.0:
        return None

    # Ring basis: two perpendicular axes spanning the silhouette plane. Camera-facing case is normal to fwd = (head - camera) / |head - camera|; fallback path uses (player_right, world_up).
    if camera_pos is not None:
        dx = head[0] - camera_pos[0]
        dy = head[1] - camera_pos[1]
        dz = head[2] - camera_pos[2]
        dist = math.sqrt(dx * dx + dy * dy + dz * dz)
        if dist > 1e-3:
            inv = 1.0 / dist
            fwd = (dx * inv, dy * inv, dz * inv)
            # Use world-up × forward, falling back when the camera is directly above or below.
            rx, ry, rz = -fwd[1], fwd[0], 0.0
            rmag = math.sqrt(rx * rx + ry * ry + rz * rz)
            if rmag < 1e-3:
                right_axis = (1.0, 0.0, 0.0)
            else:
                inv_r = 1.0 / rmag
                right_axis = (rx * inv_r, ry * inv_r, rz * inv_r)
            # up = fwd x right (Gram-Schmidt completes the basis).
            up_axis = (
                fwd[1] * right_axis[2] - fwd[2] * right_axis[1],
                fwd[2] * right_axis[0] - fwd[0] * right_axis[2],
                fwd[0] * right_axis[1] - fwd[1] * right_axis[0],
            )
        else:
            # Camera and head co-located - degenerate, won't be visible anyway but render something.
            right_axis = (1.0, 0.0, 0.0)
            up_axis = (0.0, 0.0, 1.0)
    else:
        # Fall back to clavicle-derived player-right plus world-up.
        l_clav = player.bones.get(Netvars.BONE_L_CLAV)
        r_clav = player.bones.get(Netvars.BONE_R_CLAV)
        if l_clav is not None and r_clav is not None:
            rx = r_clav[0] - l_clav[0]
            ry = r_clav[1] - l_clav[1]
            rz = r_clav[2] - l_clav[2]
            rmag = math.sqrt(rx * rx + ry * ry + rz * rz)
            if rmag > 1e-3:
                inv_r = 1.0 / rmag
                right_axis = (rx * inv_r, ry * inv_r, rz * inv_r)
            else:
                right_axis = (1.0, 0.0, 0.0)
        else:
            right_axis = (1.0, 0.0, 0.0)
        up_axis = (0.0, 0.0, 1.0)

    # Centre uses the shared cranium correction (compute_head_sphere).
    width_mul = float(getattr(cfg, "head_outline_width_mul", 1.0))
    height_mul = float(getattr(cfg, "head_outline_height_mul", 1.0))
    rot_deg = float(getattr(cfg, "head_outline_rotation_deg", 0.0))

    cos_rotation = math.cos(math.radians(rot_deg))
    sin_rotation = math.sin(math.radians(rot_deg))
    radius_x = radius * width_mul
    radius_y = radius * height_mul

    cx_w, cy_w, cz_w = _head_center_world(player, cfg, head, camera_pos)

    # 32 samples = ~11.25 deg/segment, smooth enough for a continuous ellipse out to ~1500px screen radius without Moire patterns.
    samples = 32
    points = []
    for sample_index in range(samples):
        angle = (2.0 * math.pi) * (sample_index / samples)
        ellipse_x = math.cos(angle) * radius_x
        ellipse_y = math.sin(angle) * radius_y
        # In-plane rotation (CCW by rot_deg).
        rotated_x = ellipse_x * cos_rotation - ellipse_y * sin_rotation
        rotated_y = ellipse_x * sin_rotation + ellipse_y * cos_rotation
        world_x = cx_w + right_axis[0] * rotated_x + up_axis[0] * rotated_y
        world_y = cy_w + right_axis[1] * rotated_x + up_axis[1] * rotated_y
        world_z = cz_w + right_axis[2] * rotated_x + up_axis[2] * rotated_y
        points.append(world_to_screen((world_x, world_y, world_z), view_matrix, screen_w, screen_h))
    return points


def draw_skeleton(draw_list, player, view_matrix, screen_w: int, screen_h: int, cfg,
                  camera_pos=None) -> None:
    if not cfg.enabled or not player.bones:
        return
    skeleton_color = color_u32(cfg.color)
    for first_bone, second_bone in Netvars.SKELETON_PAIRS:
        first_world = player.bones.get(first_bone)
        second_world = player.bones.get(second_bone)
        if first_world is None or second_world is None:
            continue
        first_screen = world_to_screen(first_world, view_matrix, screen_w, screen_h)
        second_screen = world_to_screen(second_world, view_matrix, screen_w, screen_h)
        if first_screen is None or second_screen is None:
            continue
        draw_list.add_line(
            (first_screen[0], first_screen[1]),
            (second_screen[0], second_screen[1]),
            skeleton_color, cfg.thickness,
        )

    # Extension stubs: synthetic gun/toe/head-crown lines show where the triggerbot's silhouette starts and ends. Drawn thinner than main bones for clear hierarchy.
    ext_thickness = max(cfg.thickness * 0.7, 0.5)
    for tip_world, ext_world, _label in iter_extensions(player.bones):
        tip_screen = world_to_screen(tip_world, view_matrix, screen_w, screen_h)
        extension_screen = world_to_screen(ext_world, view_matrix, screen_w, screen_h)
        if tip_screen is None or extension_screen is None:
            continue
        draw_list.add_line(
            (tip_screen[0], tip_screen[1]),
            (extension_screen[0], extension_screen[1]),
            skeleton_color, ext_thickness,
        )

    if getattr(cfg, "head_outline_enabled", False):
        points = compute_head_outline_polygon(
            player, view_matrix, screen_w, screen_h, cfg, camera_pos,
        )
        if points is not None:
            outline_color = color_u32(getattr(cfg, "head_outline_color", cfg.color))
            thickness = max(float(getattr(cfg, "head_outline_thickness_px", 1.5)), 0.5)
            for point_index, start_point in enumerate(points):
                end_point = points[(point_index + 1) % len(points)]
                if start_point is None or end_point is None:
                    continue
                draw_list.add_line(
                    (start_point[0], start_point[1]),
                    (end_point[0], end_point[1]),
                    outline_color, thickness,
                )
