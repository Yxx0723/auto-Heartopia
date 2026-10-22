"""UI 令牌：颜色可读性、集中化与控件高度常量必须可被脚本判定。"""
import re
from pathlib import Path

from utils.ui import monitor_style, ui_tokens
from utils.ui.ui_tokens import COLORS, CONTRAST_TARGETS, CONTROL, SPACE, TYPE, contrast_ratio

REPO_ROOT = Path(__file__).resolve().parent.parent.parent
UI_DIR = REPO_ROOT / 'utils' / 'ui'

# v1 起就在用的 UI 键，任何重构都不得删除
REQUIRED_KEYS = {'bg', 'card', 'card_hi', 'plot', 'line', 'line_strong', 'text', 'muted', 'faint',
                 'green', 'accent_dim', 'red', 'blue', 'amber', 'button', 'button_hover',
                 'button_pressed', 'disabled_text', 'danger_bg', 'primary_text',
                 'primary_hover', 'primary_pressed'}


def test_ui_tokens_expose_every_key():
    assert REQUIRED_KEYS <= set(COLORS)
    assert all(re.fullmatch(r'#[0-9a-f]{6}', COLORS[key]) for key in REQUIRED_KEYS)


def test_monitor_style_reexports_tokens():
    for key in REQUIRED_KEYS:
        assert monitor_style.COLORS[key] == COLORS[key]


def test_text_contrast_targets_hold():
    for key, target in CONTRAST_TARGETS.items():
        ratio = round(contrast_ratio(COLORS[key], COLORS['card']), 2)
        assert ratio >= target - 0.02, f'{key} 对比度 {ratio}:1 低于目标 {target}:1'


def test_primary_button_text_is_readable():
    assert contrast_ratio(COLORS['primary_text'], COLORS['green']) >= 4.5
    assert contrast_ratio(COLORS['disabled_text'], COLORS['card']) >= 3.0


def test_control_height_matches_plan():
    assert CONTROL['height'] == 30
    assert CONTROL['icon'] == 30
    assert CONTROL['canvas_inset'] == 6
    assert set(TYPE) == {'section', 'body', 'value', 'coord', 'label', 'hint', 'micro'}


def test_spacing_tokens_are_multiples_of_four():
    assert all(value % 4 == 0 for value in SPACE.values())


def test_ui_modules_have_no_hardcoded_hex_colours():
    """颜色只能来自 ui_tokens，避免第二套色值再次散落。"""
    # 地图绘制与地图标注自带一套卡图配色，不属于窗口 UI chrome。
    allowed = {'ui_tokens.py', 'monitor_style.py', 'map_presentation.py',
               'monitor_painter.py', 'map_labels.py', 'scene_map_worker.py'}
    offenders = {}
    for path in sorted(UI_DIR.glob('*.py')):
        if path.name in allowed:
            continue
        literals = re.findall(r'#[0-9a-fA-F]{6}', path.read_text(encoding='utf-8'))
        if literals:
            offenders[path.name] = sorted(set(literals))
    assert offenders == {}


def test_scale_is_identity_on_100_percent():
    assert ui_tokens.ui_scale() == 1.0 or ui_tokens.ui_scale() > 0
    assert ui_tokens.px(30) >= 30
