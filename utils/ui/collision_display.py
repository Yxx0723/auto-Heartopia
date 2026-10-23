"""Display-only faces. Orientation is not a support identity or walkability."""
import math
from utils.core.coordinate_route import valid_position


# Outward winding for box_geometry's dx/dy/dz corner order.
BOX_FACES = ((4, 6, 7, 5), (0, 1, 3, 2), (2, 3, 7, 6),
             (0, 4, 5, 1), (1, 5, 7, 3), (0, 2, 6, 4))


def display_corners(box):
    data = box.get('display_box')
    if box.get('map_cached') or not isinstance(data, dict) or data.get('schema') != 'oriented-box-v1':
        return ()
    corners = data.get('corners')
    if not isinstance(corners, (list, tuple)) or len(corners) != 8 or not all(valid_position(p) for p in corners):
        return ()
    return tuple(tuple(p) for p in corners)


def collision_faces(box):
    """Candidate upward faces and side projections, or None for unknown data.

    Dominant world-Y normal is a drawing convention, NOT a measured maximum
    walking slope. No grounding, clearance or passage is inferred here.
    """
    corners = display_corners(box)
    if not corners:
        return None
    result = []
    for indexes in BOX_FACES:
        a, b, c, _ = [corners[i] for i in indexes]
        u, v = [b[i]-a[i] for i in range(3)], [c[i]-a[i] for i in range(3)]
        normal = (u[1]*v[2]-u[2]*v[1], u[2]*v[0]-u[0]*v[2], u[0]*v[1]-u[1]*v[0])
        if not all(math.isfinite(n) for n in normal) or max(map(abs, normal)) == 0:
            return None
        kind = 'top' if normal[1] > max(abs(normal[0]), abs(normal[2])) else 'side'
        if normal[1] < -max(abs(normal[0]), abs(normal[2])):
            continue  # Underside is not a candidate top.
        result.append((kind, tuple((corners[i][0], corners[i][2]) for i in indexes)))
    return result or None
