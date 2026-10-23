from copy import deepcopy
from types import SimpleNamespace


def test_collision_snapshot_is_bounded_and_keeps_candidates_distinct_from_real_contact():
    from utils.core.playback_trace import collision_context
    boxes = [{'polygon': [[i*.01, 0], [i*.01+.2, 0], [i*.01+.2, .2]], 'height': [0, 2],
              'source': 'verified-native-world-collider'} for i in range(20)]
    scene = {'state': 'ready', 'colliders': boxes, 'terrain_revision': 'v1'}
    before = deepcopy(scene)
    result = collision_context(SimpleNamespace(scene=scene), [0, 0, 0], [10, 0, 0])
    assert result['contact_confirmed'] is False
    assert len(result['colliders']) == 8 and result['candidate_count'] == 20
    assert result['truncated'] is True
    assert scene == before


def test_missing_scene_and_invalid_geometry_are_explicitly_unknown():
    from utils.core.playback_trace import collision_context
    assert collision_context(None, [0, 0, 0], [1, 0, 0])['state'] == 'unavailable'
    nav = SimpleNamespace(scene={'state': 'ready', 'colliders': [{'polygon': [[float('nan'), 0]]}]})
    assert collision_context(nav, [0, 0, 0], [1, 0, 0])['colliders'] == []
