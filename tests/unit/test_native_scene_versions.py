import pytest

from utils.core.native_scene_versions import LEGACY_NATIVE_LAYOUT, native_scene_layout


CURRENT = {
    "sha256": "c528450f32bb7a344a4552d250455502060a0d472ff881011e5303d0665d7dcf",
    "file_size": 5199800,
    "unity_player_sha256": "76356f5ca4c1e61395396265e64ceae1f78ce98abb6dfd3cfca8dfc9a8d6db87",
    "unity_player_file_size": 28734392,
    "game_assembly_sha256": "e2f8e50441167dc83e31141a03a3da0ce4451de30dd316a0527574e51b5fe1e5",
    "game_assembly_file_size": 52976568,
}


def test_current_fingerprints_select_current_type_table():
    layout = native_scene_layout(CURRENT)
    assert layout != LEGACY_NATIVE_LAYOUT
    assert (layout.class_cache_rva, layout.type_count, layout.type_stride) == (0x2FAFA08, 14780, 88)
    assert (layout.xd_collider, layout.scene_loader_root, layout.object_translator) == (7706, 10198, 11022)


@pytest.mark.parametrize("field,value", [
    ("sha256", "0" * 64),
    ("unity_player_sha256", "8db4464dd493c99f42f267a2e509f40632f59aca3fb542d573beb7bd695ba302"),
    ("game_assembly_sha256", "8e44137ad5536b87fe46cc150980305bc866d12666cdbde9f0c59331cf0d8b0d"),
    ("game_assembly_file_size", 52892600),
])
def test_unknown_or_mixed_fingerprints_are_rejected(field, value):
    with pytest.raises(ValueError):
        native_scene_layout({**CURRENT, field: value})
