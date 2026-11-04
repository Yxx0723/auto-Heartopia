"""Scene-scoped evidence identity; no persistent world identity is guessed."""
import json
import hashlib


def scene_scope(scene):
    if not isinstance(scene, dict) or scene.get('state') != 'ready' or scene.get('cache_context') is None:
        return None
    try:
        value=json.dumps([scene['cache_context'], scene.get('terrain_revision')], sort_keys=True, allow_nan=False)
    except (TypeError,ValueError,OverflowError):
        return None
    return hashlib.sha256(value.encode()).hexdigest()
