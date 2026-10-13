"""Display names that are also safe, portable Windows route filenames."""
import re

from utils.core import thresholds as policy


def validate_route_name(value):
    if not isinstance(value, str):
        raise ValueError('请输入路线名称。')
    name = value.strip()
    reserved = {'CON', 'PRN', 'AUX', 'NUL', 'CLOCK$'} | {
        prefix + str(number) for prefix in ('COM', 'LPT') for number in range(1, 10)}
    if not name or len(name) > policy.COORD_POINT_ROUTE_NAME_LENGTH \
            or name.startswith('.') or name.endswith('.') \
            or re.search(r'[<>:"/\\|?*\x00-\x1f]', name) \
            or name.split('.')[0].upper() in reserved:
        raise ValueError(f'路线名称需为 1～{policy.COORD_POINT_ROUTE_NAME_LENGTH} 个字符，'
                         '不能使用路径、特殊文件字符或系统保留名称。')
    return name
