"""Export read-only observations as session-specific CE float entries."""
import xml.etree.ElementTree as ET

from utils.core.coordinate_route import valid_position
from utils.core.thresholds import COORD_MONITOR_STALE_SECONDS


def current_position_table(status):
    address = status.get('address')
    if status.get('state') != 'connected' or not valid_position(status.get('position')) \
            or type(address) is not int or not 0x10000 <= address < 0x7ffffffffff7 \
            or status.get('sample_age', 0.) > COORD_MONITOR_STALE_SECONDS:
        raise ValueError('没有可导出的实时坐标，请等待游戏连接')
    root = ET.Element('CheatTable', CheatEngineTableVersion='45')
    entries = ET.SubElement(root, 'CheatEntries')
    for i, axis in enumerate('XYZ'):
        entry = ET.SubElement(entries, 'CheatEntry')
        ET.SubElement(entry, 'ID').text = str(i + 1)
        ET.SubElement(entry, 'Description').text = f'"角色 {axis}（当前会话）"'
        ET.SubElement(entry, 'VariableType').text = 'Float'
        ET.SubElement(entry, 'Address').text = f'{address + 4 * i:X}'
    ET.SubElement(root, 'Comments').text = (
        f'只读观测地址，游戏进程 PID {status.get("pid", "未知")}。'
        '仅供当前会话使用；游戏重启或切换场景后请在监视器中重新导出。'
        '本表不包含注入脚本或修改游戏的功能。')
    return '<?xml version="1.0" encoding="utf-8"?>\n' + ET.tostring(root, encoding='unicode')
