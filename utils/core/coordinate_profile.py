"""The data contract for a version-pinned, read-only coordinate locator."""
import re


def validate_profile(data):
    if isinstance(data, dict) and data.get('schema') == 'myrail-position-mono-v1':
        return validate_mono_profile(data)
    if not isinstance(data, dict) or data.get('schema') != 'myrail-position-v1' \
            or data.get('process_name') != 'xdt.exe' or data.get('module') != 'UnityPlayer.dll' \
            or data.get('read_only') is not True or data.get('verified_after_restart') is not True:
        return ['坐标配置未经重启验证或格式不支持']
    if not isinstance(data.get('sha256'), str) or not re.fullmatch('[0-9a-f]{64}', data['sha256']) \
            or type(data.get('file_size')) is not int or data['file_size'] <= 0:
        return ['坐标配置缺少游戏版本指纹']
    if not isinstance(data.get('game_assembly_sha256'), str) \
            or not re.fullmatch('[0-9a-f]{64}', data['game_assembly_sha256']) \
            or type(data.get('game_assembly_file_size')) is not int or data['game_assembly_file_size'] <= 0:
        return ['坐标配置缺少游戏逻辑模块指纹']
    paths = data.get('paths')
    if not isinstance(paths, list) or not 2 <= len(paths) <= 64:
        return ['坐标配置至少需要两条经过验证的链']
    for path in paths:
        if not isinstance(path, dict) or type(path.get('rva')) is not int or path['rva'] < 0:
            return ['坐标入口偏移无效']
        offsets = path.get('offsets')
        if not isinstance(offsets, list) or not 1 <= len(offsets) <= 8 \
                or any(type(v) is not int or not 0 <= v <= 4096 for v in offsets):
            return ['坐标指针层数或偏移无效']
    return []


def validate_mono_profile(data):
    if data.get('process_name') != 'xdt.exe' or data.get('module') != 'mono-2.0-sgen.dll' \
            or any(data.get(flag) is not True for flag in (
                'read_only', 'verified_after_restart', 'verified_player_movement',
                'verified_camera_independence', 'require_movement_calibration')):
        return ['角色对象配置缺少移动、镜头或重启验证']
    for prefix in ('', 'unity_player_', 'game_assembly_'):
        digest, size = data.get(prefix + 'sha256'), data.get(prefix + 'file_size')
        if not isinstance(digest, str) or not re.fullmatch('[0-9a-f]{64}', digest) \
                or type(size) is not int or size <= 0:
            return ['角色对象配置缺少游戏版本指纹']
    return []
