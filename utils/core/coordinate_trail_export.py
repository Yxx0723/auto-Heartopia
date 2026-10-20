"""Portable road-trail capture data from the live coordinate monitor."""
import math


TRAIL_EXPORT_SCHEMA = 'heartopia-road-walk-capture-v1'


def trail_capture_payload(trail, captured_at):
    """Group sampled XZ points without connecting across monitor segment breaks."""
    groups = []
    segment_id = None
    points = []

    def append_group():
        if len(points) >= 2:
            groups.append({
                'segment_id': len(groups) + 1,
                'points_xz': [[round(x, 6), round(z, 6)] for x, z in points],
                'point_count': len(points),
            })

    for sample in trail:
        current_segment = sample.get('segment')
        if segment_id is not None and current_segment != segment_id:
            append_group()
            points = []
        segment_id = current_segment
        x, z = float(sample['x']), float(sample['z'])
        if not math.isfinite(x) or not math.isfinite(z):
            raise ValueError('轨迹中包含无效坐标，无法导出。')
        points.append((x, z))
    append_group()
    if not groups:
        raise ValueError('当前没有完整的实走路段，请在游戏中移动后再导出。')
    return {
        'schema': TRAIL_EXPORT_SCHEMA,
        'coordinate_space': 'game-world-XZ',
        'captured_at': captured_at,
        'source': 'read-only live character coordinate trail',
        'segments': groups,
        'notes': [
            'XZ samples only; the line is a recorded walk, not an inferred road centerline.',
            'Monitor segment breaks are preserved so interrupted or discontinuous samples are never joined.',
            'Compare against the in-game map and verify each route before treating it as a traversable road.',
        ],
    }
