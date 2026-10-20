import struct

import pytest

from utils.core.stamina import stamina_status, usable_stamina
from utils.drivers.stamina import NativeGrowList, component_indices
from utils.ui.coordinate_monitor import CoordinateMonitor
from utils.ui.native_coordinate_monitor import CoordinateWindow, COLORS
from unittest.mock import Mock
from types import SimpleNamespace


@pytest.mark.parametrize('current,low', [(0, True), (16, True), (29, True), (30, False), (100, False)])
def test_threshold_uses_actual_stamina_units(current, low):
    result = stamina_status(current, 100, 100)
    assert result['low'] == low and usable_stamina(result)


@pytest.mark.parametrize('values', [(True, 100, 100), (-1, 100, 100), (10001, 100, 100),
                                   (16, 100, 0), (16., 100, 100), (0, 0, 0)])
def test_invalid_stamina_cannot_trigger_food(values):
    with pytest.raises(ValueError):
        stamina_status(*values)


def test_observed_overflow_after_restart_keeps_actual_value_and_does_not_trigger_food():
    # Current local-player data was repeatedly 109, base=100, boosted=100.
    status = stamina_status(109, 100, 100)
    assert usable_stamina(status)
    assert status['current'] == 109 and status['maximum'] == 100
    assert status['percent'] == pytest.approx(109.) and not status['low']


def test_component_mapping_uses_type_ids_and_each_own_data_index():
    raw = bytearray(96)
    struct.pack_into('<8H', raw, 0, 2, 1125, 1071, 1087, 1126, 1127, 1128, 1129)
    struct.pack_into('<8i', raw, 16, 944, 0, 934, 6, 0, 0, 0, 0)
    struct.pack_into('<iiQ', raw, 48, 8, 0, 0)
    indices = component_indices(raw, None)
    assert indices[2] == 944 and indices[1128] == 0 and indices[1125] == 0


def test_extended_component_mapping_keeps_extra_keys_separate_from_extra_indices():
    raw, extra = bytearray(96), bytearray(16*6)
    struct.pack_into('<8H', raw, 0, *range(1, 9))
    struct.pack_into('<8i', raw, 16, *range(10, 18))
    struct.pack_into('<iiQ', raw, 48, 10, 16, 0x40000)
    struct.pack_into('<2H', extra, 0, 1125, 1128)
    struct.pack_into('<2i', extra, 32, 0, 3)
    assert component_indices(raw, lambda at, size: bytes(extra))[1128] == 3
    struct.pack_into('<H', extra, 0, 1)
    with pytest.raises(ValueError):
        component_indices(raw, lambda at, size: bytes(extra))


def test_native_segments_use_sixteen_byte_descriptors_and_validate_growth():
    memory = bytearray(0x400)
    struct.pack_into('<4iQ2i', memory, 0, 4, 3, 2, 6, 0x100, 2, 2)
    struct.pack_into('<Qi', memory, 0x100, 0x200, 4)
    struct.pack_into('<Qi', memory, 0x110, 0x300, 2)
    read = lambda at, size: bytes(memory[at:at+size])
    grow = NativeGrowList(read, 0, 20)
    assert grow.at(3) == 0x23c and grow.at(4) == 0x300 and grow.at(5) == 0x314
    assert grow.stable()
    struct.pack_into('<i', memory, 12, 7)
    assert not grow.stable()
    with pytest.raises(ValueError):
        grow.at(6)


@pytest.mark.parametrize('header', [(0, 0, 0, 1, 0, 0, 0), (4, 3, 3, 2, 0, 1, 1),
                                    (4, 3, 2, 9, 0, 1, 1), (4, 3, 2, -1, 0, 0, 0)])
def test_invalid_native_container_is_rejected(header):
    with pytest.raises(ValueError):
        NativeGrowList(lambda at, size: struct.pack('<4iQ2i', *header), 0, 20)


def test_stamina_refresh_is_throttled_and_failure_does_not_disconnect_coordinates():
    now = [0.]
    reader = SimpleNamespace(snapshot=lambda: {'position': [0, 0, 0]}, close=Mock(),
                             stamina_snapshot=Mock(return_value=stamina_status(16, 100, 100)))
    monitor = CoordinateMonitor('profile', discover=lambda: [77], factory=lambda *a: reader, clock=lambda: now[0])
    assert monitor.status()['stamina']['current'] == 16
    now[0] = .2
    monitor.status()
    assert reader.stamina_snapshot.call_count == 1
    now[0] = 1.2
    reader.stamina_snapshot.side_effect = ValueError('reload')
    status = monitor.status()
    assert status['state'] == 'connected' and status['stamina']['state'] == 'unavailable'
    reader.close.assert_not_called()
    monitor.close()


def test_native_window_clears_stale_stamina_and_marks_below_thirty():
    ui = CoordinateWindow.__new__(CoordinateWindow)
    ui.view = SimpleNamespace(position=(0, 0, 0), paused=False,
                              status={'stamina': stamina_status(16, 100, 100)})
    ui.fields, ui.stamina_value = {'stamina': Mock()}, Mock()
    ui.render_stamina()
    assert ui.fields['stamina'].set.call_args.args == ('16 / 100',)
    assert ui.stamina_value.configure.call_args.kwargs['fg'] == COLORS['amber']
    ui.view.status = {'stamina': {'state': 'unavailable'}}
    ui.render_stamina()
    assert ui.fields['stamina'].set.call_args.args == ('未读取',)
