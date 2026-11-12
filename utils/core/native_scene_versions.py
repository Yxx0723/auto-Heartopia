"""Verified native scene tables, selected by the complete game fingerprint."""
from dataclasses import dataclass


@dataclass(frozen=True)
class NativeSceneLayout:
    class_cache_rva: int
    type_stride: int
    type_count: int
    xd_collider: int
    scene_loader_root: int
    object_translator: int


LEGACY_NATIVE_LAYOUT = NativeSceneLayout(0x2f850c8, 88, 14794, 7737, 10404, 11618)

_LAYOUTS = {
    (
        ("236fe6fc21a95a2b2b51aaeac53f8f8b0ebd60dc1014a59a8d952bf0ad6fc89b", 5199800),
        ("8db4464dd493c99f42f267a2e509f40632f59aca3fb542d573beb7bd695ba302", 28724152),
        ("8e44137ad5536b87fe46cc150980305bc866d12666cdbde9f0c59331cf0d8b0d", 52892600),
    ): LEGACY_NATIVE_LAYOUT,
    (
        ("c528450f32bb7a344a4552d250455502060a0d472ff881011e5303d0665d7dcf", 5199800),
        ("76356f5ca4c1e61395396265e64ceae1f78ce98abb6dfd3cfca8dfc9a8d6db87", 28734392),
        ("e2f8e50441167dc83e31141a03a3da0ce4451de30dd316a0527574e51b5fe1e5", 52976568),
    ): NativeSceneLayout(0x2fafa08, 88, 14780, 7706, 10198, 11022),
}


def native_scene_layout(profile):
    fingerprint = tuple((profile.get(prefix + "sha256"), profile.get(prefix + "file_size"))
                        for prefix in ("", "unity_player_", "game_assembly_"))
    layout = _LAYOUTS.get(fingerprint)
    if layout is None:
        raise ValueError("当前游戏模块指纹组合没有经过验证的原生场景布局")
    return layout
