"""World-to-screen projection, angle math, and team constants."""


def world_to_screen(
    pos: tuple[float, float, float],
    view_matrix: list[list[float]],
    screen_width: int,
    screen_height: int,
) -> tuple[float, float] | None:
    """Project a 3D world position to 2D screen coords. Returns None if behind camera."""
    x, y, z = pos
    vw = (
        view_matrix[3][0] * x
        + view_matrix[3][1] * y
        + view_matrix[3][2] * z
        + view_matrix[3][3]
    )
    if vw < 0.001:
        return None
    vx = (
        view_matrix[0][0] * x
        + view_matrix[0][1] * y
        + view_matrix[0][2] * z
        + view_matrix[0][3]
    )
    vy = (
        view_matrix[1][0] * x
        + view_matrix[1][1] * y
        + view_matrix[1][2] * z
        + view_matrix[1][3]
    )
    inv_w = 1.0 / vw
    sx = (screen_width / 2.0) + (vx * inv_w) * (screen_width / 2.0)
    sy = (screen_height / 2.0) - (vy * inv_w) * (screen_height / 2.0)
    return (sx, sy)


def angle_diff_deg(a: float, b: float) -> float:
    """Return the smallest signed difference in degrees, in ``[-180, 180)``."""
    return ((a - b + 540.0) % 360.0) - 180.0


TEAM_CT = 3
