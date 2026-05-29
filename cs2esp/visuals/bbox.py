"""Player ESP box geometry derived from live CS2 data."""

from cs2esp.visuals.math_utils import world_to_screen

Box2D = tuple[float, float, float, float]


def compute_bounds_box(player, view_matrix, w: int, h: int) -> Box2D | None:
    """Screen AABB from the entity's collision hull."""
    mins = getattr(player, "bbox_mins", None)
    maxs = getattr(player, "bbox_maxs", None)
    if mins is None or maxs is None:
        return None
    ox, oy, oz = player.position
    xs: list[float] = []
    ys: list[float] = []
    for cx in (mins[0], maxs[0]):
        for cy in (mins[1], maxs[1]):
            for cz in (mins[2], maxs[2]):
                screen_position = world_to_screen((ox + cx, oy + cy, oz + cz),
                                                  view_matrix, w, h)
                if screen_position is not None:
                    xs.append(screen_position[0])
                    ys.append(screen_position[1])
    if len(xs) < 2:
        return None
    return (min(xs), min(ys), max(xs), max(ys))


def compute_bone_box(player, view_matrix, w: int, h: int,
                     side_pad_frac: float = 0.09,
                     crown_frac: float = 0.14,
                     foot_frac: float = 0.16) -> Box2D | None:
    """Padded screen AABB around all projected bones."""
    xs: list[float] = []
    ys: list[float] = []
    for world_position in (getattr(player, "bones", None) or {}).values():
        screen_position = world_to_screen(world_position, view_matrix, w, h)
        if screen_position is not None:
            xs.append(screen_position[0])
            ys.append(screen_position[1])
    if len(xs) < 4:
        return None
    min_x, max_x = min(xs), max(xs)
    min_y, max_y = min(ys), max(ys)
    height = max(2.0, max_y - min_y)
    side = height * side_pad_frac
    return (min_x - side,
            min_y - height * crown_frac,
            max_x + side,
            max_y + height * foot_frac)
