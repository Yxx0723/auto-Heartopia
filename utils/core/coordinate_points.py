"""Persistent manual point format, independent of the UI and filesystem."""
from copy import deepcopy
from datetime import datetime
import math

from utils.core.coordinate_route import valid_position
from utils.core import thresholds as policy


def empty_points():
    return {'schema': 'coordinate-points-v1', 'points': []}


def valid_stamp(value):
    if not isinstance(value, str):
        return False
    try:
        return datetime.fromisoformat(value).utcoffset() is not None
    except ValueError:
        return False


def validate_points(data):
    if not isinstance(data, dict) or data.get('schema') != 'coordinate-points-v1' \
            or not isinstance(data.get('points'), list):
        raise ValueError('点位文件格式不支持，请保留原文件并检查内容。')
    for number, point in enumerate(data['points'], 1):
        if not isinstance(point, dict) or type(point.get('id')) is not int \
                or point['id'] != number or not isinstance(point.get('name'), str) \
                or not point['name'].strip() or not valid_position(point.get('position')) \
                or not valid_stamp(point.get('sample_at')) or not valid_stamp(point.get('saved_at')):
            raise ValueError(f'第 {number} 条点位数据无效，原文件未修改。')
        if 'collect_wait' in point:
            validate_collect_wait(point['collect_wait'])
    return data


def validate_collect_wait(seconds):
    if type(seconds) not in (int, float) or not math.isfinite(seconds) \
            or not 0 <= seconds <= policy.COORD_COLLECTION_MAX_WAIT:
        raise ValueError(f'采集等待时间应为 0～{policy.COORD_COLLECTION_MAX_WAIT:g} 秒的有效数字。')
    return float(seconds)


def validate_loop_count(count):
    if type(count) is not int or not 1 <= count <= policy.COORD_PLAYBACK_MAX_LOOPS:
        raise ValueError(f'循环次数应为 1～{policy.COORD_PLAYBACK_MAX_LOOPS} 的整数。')
    return count


def append_point(data, snapshot, saved_at, collect_wait=None):
    validate_points(data)
    if not isinstance(snapshot, dict) or not valid_position(snapshot.get('position')) \
            or not valid_stamp(snapshot.get('sample_at')) or not valid_stamp(saved_at):
        raise ValueError('没有可保存的有效实时坐标或采样时间。')
    result = deepcopy(data)
    number = len(result['points']) + 1
    result['points'].append({'id': number, 'name': f'点位 {number}',
                             'position': list(snapshot['position']),
                             'sample_at': snapshot['sample_at'], 'saved_at': saved_at})
    if collect_wait is not None:
        result['points'][-1]['collect_wait'] = validate_collect_wait(collect_wait)
    return result


def set_collect_wait(data, point_id, seconds):
    validate_points(data)
    seconds = validate_collect_wait(seconds)
    if point_id is not None and (type(point_id) is not int or not 1 <= point_id <= len(data['points'])):
        raise ValueError('需要设置等待时间的点位不存在。')
    result = deepcopy(data)
    for point in result['points']:
        if point_id is None or point['id'] == point_id:
            point['collect_wait'] = seconds
    return result


def ordered_waypoints(data):
    validate_points(data)
    if not data['points']:
        raise ValueError('请先保存至少一个点位。')
    return [{'id': point['id'], 'name': point['name'],
             'position': list(point['position']), 'wait': 0.,
             **({'collect_wait': point['collect_wait']} if 'collect_wait' in point else {})}
            for point in data['points']]


def choose_start_index(points, position, mode):
    if mode not in ('first', 'nearest') or not valid_position(position) \
            or not isinstance(points, list) or not points \
            or any(not isinstance(point, dict) or not valid_position(point.get('position')) for point in points):
        raise ValueError('起点模式或当前坐标、点位数据无效。')
    if mode == 'first':
        return 0
    # Include height so an overlapping point on another floor is less likely
    # to win. Equal distances keep the earliest saved point deterministically.
    return min(range(len(points)), key=lambda index: math.dist(position, points[index]['position']))
