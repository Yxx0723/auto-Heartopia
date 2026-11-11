from types import SimpleNamespace

import pytest

from utils.drivers.cooking_single_pot import SinglePotReader
from utils.drivers.il2cpp_scene import NativeSceneMetadata


class NativeButtonMetadata(NativeSceneMetadata):
    def __init__(self, scenario):
        self.scenario = scenario
        self.reads = []
        self.resolutions = 0
        self.classes = {
            1: dict(address=1, name='Button', namespace='UnityEngine.UI', parent=2, size=48, fields={}),
            2: dict(parent=3, fields={'m_Interactable': {'code': 2, 'flags': 0, 'offset': 24}}),
            3: dict(parent=0, fields={}),
        }
        if scenario != 'missing':
            self.classes[3]['fields']['m_Enabled'] = {
                'code': 8 if scenario == 'wrong_type' else 2, 'flags': 0,
                'offset': 48 if scenario == 'outside_object' else 32,
            }

    def cls(self, at):
        return self.classes[at]

    def pointer(self, at):
        assert at == 100
        if self.scenario == 'invalid_object':
            raise ValueError('模拟对象已失效')
        return 1

    def resolve(self, wrapper, name, namespace):
        assert (wrapper, name, namespace) == (200, 'Button', 'UnityEngine.UI')
        self.resolutions += 1
        return 101 if self.scenario == 'binding_changed' and self.resolutions == 2 else 100

    def data(self, at, width):
        self.reads.append((at, width))
        if self.scenario == 'read_error':
            raise OSError('模拟原生读取异常')
        return {'disabled': b'\0', 'invalid_bool': b'\2'}.get(self.scenario, b'\1')


@pytest.mark.parametrize('scenario,state,reads,error', [
    ('enabled', 'enabled', [(132, 1)], None),
    ('disabled', 'disabled', [(132, 1)], None),
    ('missing', 'unknown', [], '缺少字段'),
    ('wrong_type', 'unknown', [], '字段布局不符'),
    ('outside_object', 'unknown', [], '偏移超出对象范围'),
    ('invalid_object', 'unknown', [], '对象已失效'),
    ('read_error', 'unknown', [(132, 1)], '原生读取异常'),
    ('invalid_bool', 'unknown', [(132, 1)], '不是有效布尔值'),
    ('binding_changed', 'unknown', [(132, 1)], '原生引用变化'),
])
def test_button_enable_state_from_verified_native_metadata(scenario, state, reads, error):
    native = NativeButtonMetadata(scenario)
    reader = SinglePotReader.__new__(SinglePotReader)
    reader.layout = SimpleNamespace(native=native, resolve=native.resolve)
    result = reader.start_button_enabled(200, 100)
    assert result['state'] == state
    assert result['source'] == 'm_Enabled'
    assert native.reads == reads
    if error is not None:
        assert error in result['error']['message']
        assert result['error']['type'] == ('OSError' if scenario == 'read_error' else 'ValueError')
    else:
        assert 'error' not in result
