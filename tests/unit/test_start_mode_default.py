"""起点方式默认应为「最近点接入」，下拉起始值不得再硬编码为完整回放。"""
import ast
from pathlib import Path

from utils.ui.monitor_layout import START_MODE_DEFAULT, START_MODE_OPTIONS

REPO_ROOT = Path(__file__).resolve().parent.parent.parent
LAYOUT = REPO_ROOT / 'utils' / 'ui' / 'monitor_layout.py'


def test_default_start_mode_is_nearest_entry():
    assert START_MODE_DEFAULT == '最近点接入'
    assert START_MODE_OPTIONS == ('完整回放', '最近点接入')


def test_start_mode_variable_uses_default_constant():
    tree = ast.parse(LAYOUT.read_text(encoding='utf-8'))
    calls = [node for node in ast.walk(tree)
             if isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute)
             and node.func.attr == 'StringVar']
    assigned = [node for node in calls
                if any(keyword.arg == 'value'
                       and isinstance(keyword.value, ast.Name)
                       and keyword.value.id == 'START_MODE_DEFAULT'
                       for keyword in node.keywords)]
    assert len(assigned) == 1
    literals = [keyword.value.value for node in calls for keyword in node.keywords
                if keyword.arg == 'value' and isinstance(keyword.value, ast.Constant)]
    assert '完整回放' not in literals
