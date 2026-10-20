"""Synthetic metadata and monitor tests; no game input or process access."""
import struct
from types import SimpleNamespace
from unittest.mock import Mock

import pytest

from tests.unit.test_mono_position import Runtime
from utils.core.tool_durability import axe_pick_status, tool_status
from utils.drivers.tool_durability import ToolDurabilityReader
from utils.ui.coordinate_monitor import CoordinateMonitor
from utils.ui.native_coordinate_monitor import CoordinateWindow, COLORS


def runtime():
    r = Runtime()
    r.p64(r.image+0x30, r.string('XDTBaseService'))
    r.add_class('GameWorld', 'XDTGame.Core', [('_currentLevel', 0, 18, 17)], size=16)
    r.add_class('LevelNode', 'XDTGame.Core', [('_instance', 16, 18), ('_dataModules', 24, 18)], size=32)
    r.add_class('GameLevel_Main', 'XDTGameSystem.GameLevels', [], size=24)
    r.add_class('SubModules`1', '', [('gameLevel', 16, 18), ('group', 24, 18)], size=32)
    r.add_class('ModuleGroupSorted', 'XDTGame.Core', [('_modules', 16, 29)], size=24)
    r.add_class('DataModule', 'XDTGame.Core', [('<XDTGame.Core.IModule.Owner>k__BackingField', 16, 18)], size=24)
    r.add_class('TakenToolComponent', 'EcsClient.XDT.Scene.Shared.Modules.Tools',
                [('ToolId', 16, 8), ('SkinId', 20, 8)], size=24)
    r.add_class('ToolSystem', 'XDTGameSystem.GameplaySystem.Tool',
                [('_toolsData', 32, 21), ('_currentHandHold', 64, 17)], size=72, parent='DataModule')
    table = struct.unpack('<Q', r.read(r.classes['ToolSystem']+0xa0, 8))[0]
    typ = struct.unpack('<Q', r.read(table+32, 8))[0]
    r.p64(typ, r.classes['TakenToolComponent'])
    r.add_class('Tool', 'XDTGameSystem.GameplaySystem.Tool', [('Name', 16, 14), ('Id', 48, 8),
                ('unlock', 52, 2), ('durability', 56, 8), ('maxDurability', 60, 8)], size=80)
    r.add_class('String', 'System', [], size=24)
    r.add_class('ToolEntry', '', [('next', 20, 8), ('key', 24, 8), ('value', 32, 18)], size=40)
    r.add_class('Dictionary`2', 'System.Collections.Generic', [('_entries', 24, 29),
                ('_count', 64, 8), ('_version', 76, 8)], size=80)
    def array(name, element, capacity):
        c = r.add_class(name, '', [], size=32)
        r.write(c+0x1b, b'\x05')
        r.p64(c, r.classes[element])
        stride = 24 if element == 'ToolEntry' else 8
        at = r.make_object(name, 32+stride*capacity)
        r.p64(at+24, capacity)
        return at
    modules_array = array('IModule[]', 'ToolSystem', 1)
    entries = array('ToolEntry[]', 'ToolEntry', 3)
    node, owner, sub, group, system, tool, dictionary = (
        r.make_object(n, size) for n, size in [('LevelNode', 32), ('GameLevel_Main', 24),
        ('SubModules`1', 32), ('ModuleGroupSorted', 24), ('ToolSystem', 72), ('Tool', 80), ('Dictionary`2', 80)])
    text = '斧镐'.encode('utf-16le')
    name = r.make_object('String', 20+len(text))
    r.write(name+16, struct.pack('<i', 2))
    r.write(name+20, text)
    for at, value in ((node+16, owner), (node+24, sub), (sub+16, node), (sub+24, group),
                      (group+16, modules_array), (modules_array+32, system), (system+16, owner),
                      (system+32, dictionary), (dictionary+24, entries), (tool+16, name)):
        r.p64(at, value)
    r.write(dictionary+64, struct.pack('<i', 2))
    r.write(entries+32+4, struct.pack('<i', -1))
    r.write(entries+32+8, struct.pack('<i', 1))
    r.p64(entries+32+16, tool)
    # A freed old slot with the same key must not look like a second axe-pick.
    r.write(entries+56+4, struct.pack('<i', -2))
    r.write(entries+56+8, struct.pack('<i', 1))
    r.p64(entries+56+16, r.character)
    r.write(tool+48, struct.pack('<i', 1))
    r.write(tool+52, b'\x01')
    r.write(tool+56, struct.pack('<ii', 890, 1100))
    r.write(system+64, struct.pack('<ii', 1, 0))
    storage = r.alloc(8)
    r.p64(storage, node)
    r.p64(r.slots['GameWorld']+0x70, storage)
    classes = [c for c in r.classes.values() if r.read(c+0x1b, 1) == b'\x01']
    buckets = r.alloc(len(classes)*8)
    for i, c in enumerate(classes): r.p64(buckets+i*8, c)
    r.write(r.image+0x4e8, struct.pack('<IIQ', len(classes), len(classes), buckets))
    reader = ToolDurabilityReader(r.read, r.base, r.domain)
    r.parts = SimpleNamespace(node=node, owner=owner, sub=sub, group=group, system=system,
                              tool=tool, dictionary=dictionary, entries=entries, modules=modules_array)
    return r, reader


def test_shared_tool_is_discovered_by_named_roots_and_tracks_actual_durability_and_hold_state():
    r, reader = runtime()
    first = reader.snapshot()
    assert first['current'] == 890 and first['maximum'] == 1100
    assert first['name'] == '斧镐' and first['equipped']
    r.write(r.parts.tool+56, struct.pack('<i', 880))
    r.write(r.parts.system+64, b'\0'*4)
    second = reader.snapshot()
    assert second['current'] == 880 and not second['equipped']


@pytest.mark.parametrize('tool_id,name,current,maximum', [
    (1, '斧镐', 890, 1100), (2, '浇水壶', 286, 800), (3, '鱼竿', 360, 400),
    (4, '扫描镜', 10, 300), (5, '虫网', 0, 400), (6, '平板电脑', 200, 200),
    (7, '海洋清洁仪', 1000, 1000)])
def test_held_tool_is_selected_from_current_hand_id(tool_id, name, current, maximum):
    r, reader = runtime()
    text = name.encode('utf-16le')
    at = r.make_object('String', 20+len(text))
    r.write(at+16, struct.pack('<i', len(text)//2))
    r.write(at+20, text)
    r.p64(r.parts.tool+16, at)
    r.write(r.parts.tool+48, struct.pack('<i', tool_id))
    r.write(r.parts.entries+40, struct.pack('<i', tool_id))
    r.write(r.parts.tool+56, struct.pack('<ii', current, maximum))
    r.write(r.parts.system+64, struct.pack('<i', tool_id))
    result = reader.snapshot(held=True)
    assert result == tool_status(name, current, maximum, True, True)


def test_empty_hand_clears_display_but_preserves_axe_data_for_playback():
    r, reader = runtime()
    r.write(r.parts.system+64, b'\0'*4)
    assert reader.snapshot(held=True) == {'state': 'empty', 'name': '', 'equipped': False}
    assert reader.snapshot()['current'] == 890
    assert not reader.snapshot()['equipped']


def test_switching_between_tools_reads_each_own_durability_and_keeps_replay_on_axe():
    r, reader = runtime()
    fish = r.make_object('Tool', 80)
    name = r.make_object('String', 24)
    r.write(name+16, struct.pack('<i', 2))
    r.write(name+20, '鱼竿'.encode('utf-16le'))
    r.p64(fish+16, name)
    r.write(fish+48, struct.pack('<i', 3))
    r.write(fish+52, b'\x01')
    r.write(fish+56, struct.pack('<ii', 360, 400))
    r.write(r.parts.entries+60, struct.pack('<i', -1))
    r.write(r.parts.entries+64, struct.pack('<i', 3))
    r.p64(r.parts.entries+72, fish)
    r.write(r.parts.system+64, struct.pack('<i', 3))
    assert reader.snapshot(held=True)['name'] == '鱼竿'
    assert reader.snapshot(held=True)['current'] == 360
    assert reader.snapshot()['name'] == '斧镐'
    assert not reader.snapshot()['equipped']
    r.write(r.parts.system+64, struct.pack('<i', 1))
    assert reader.snapshot(held=True)['current'] == 890
    assert reader.snapshot()['equipped']


@pytest.mark.parametrize('hand_id', [-1, 999, 100001])
def test_invalid_or_missing_held_tool_never_falls_back_to_axe(hand_id):
    r, reader = runtime()
    r.write(r.parts.system+64, struct.pack('<i', hand_id))
    with pytest.raises(ValueError):
        reader.snapshot(held=True)


def test_tool_switch_during_read_is_rejected():
    r, reader = runtime()
    original = r.read
    def change_hand(at, size):
        data = original(at, size)
        if at == r.parts.tool and size == 80:
            r.write(r.parts.system+64, struct.pack('<i', 3))
        return data
    reader.meta.read = change_hand
    with pytest.raises(ValueError):
        reader.snapshot(held=True)


@pytest.mark.parametrize('current,maximum,condition', [(0, 1100, 'broken'), (1, 1100, 'low'),
    (220, 1100, 'low'), (221, 1100, 'normal'), (1100, 1100, 'normal')])
def test_low_and_exhausted_durability_are_distinct(current, maximum, condition):
    status = axe_pick_status(current, maximum, True, False)
    assert status['condition'] == condition and status['percent'] == current/maximum*100


@pytest.mark.parametrize('current,maximum', [(-1, 1100), (1101, 1100), (0, 0), (1, -1),
    (True, 1100), (1., 1100), (float('nan'), 1100), (0, 10000001)])
def test_invalid_data_is_not_reported_as_full_or_empty_durability(current, maximum):
    with pytest.raises(ValueError): axe_pick_status(current, maximum, True, False)


def test_locked_tool_does_not_claim_a_live_durability_number():
    assert axe_pick_status(0, 0, False, False)['state'] == 'locked'


@pytest.mark.parametrize('fault', ['owner', 'sub_owner', 'count', 'missing', 'duplicate', 'namespace', 'identity', 'unlock', 'domain'])
def test_foreign_owners_invalid_lists_and_mismatched_tool_identity_are_rejected(fault):
    r, reader = runtime()
    p = r.parts
    if fault == 'owner': r.p64(p.system+16, p.node)  # Owner is _instance, not LevelNode itself.
    if fault == 'sub_owner': r.p64(p.sub+16, r.entity)
    if fault == 'count': r.write(p.dictionary+64, struct.pack('<i', 129))
    if fault == 'missing': r.write(p.entries+40, struct.pack('<i', 2))
    if fault == 'duplicate': r.write(p.entries+60, struct.pack('<i', -1))
    if fault == 'namespace':
        r.p64(r.classes['Tool']+0x50, r.string('Unknown'))
        reader.meta.cache.pop(r.classes['Tool'], None)
    if fault == 'identity': r.write(p.tool+48, struct.pack('<i', 3))
    if fault == 'unlock': r.write(p.tool+52, b'\x02')
    if fault == 'domain': r.p64(r.base+0x4d3878, r.entity)
    with pytest.raises(ValueError): reader.snapshot()


@pytest.mark.parametrize('fault', ['durability', 'dictionary_slot', 'version', 'owner', 'level'])
def test_mutation_during_a_sample_cannot_publish_mixed_tool_state(fault):
    r, reader = runtime()
    read, p, changed = r.read, r.parts, [False]
    def mutate(at, size):
        raw = read(at, size)
        if at == p.tool and size == 80 and not changed[0]:
            changed[0] = True
            if fault == 'durability': r.write(p.tool+56, struct.pack('<i', 880))
            if fault == 'dictionary_slot': r.p64(p.entries+48, r.character)
            if fault == 'version': r.write(p.dictionary+76, struct.pack('<i', 1))
            if fault == 'owner': r.p64(p.system+16, p.node)
            if fault == 'level': r.p64(p.node+16, r.entity)
        return raw
    reader.meta.read = mutate
    with pytest.raises(ValueError): reader.snapshot()


class FakePosition:
    def __init__(self): self.tool_calls, self.fail, self.closed = 0, False, False
    def snapshot(self): return {'position': [0, 0, 0]}
    def tools_snapshot(self):
        self.tool_calls += 1
        if self.fail: raise ValueError('tool reloading')
        return axe_pick_status(890, 1100, True, True)
    def close(self): self.closed = True


def test_monitor_throttles_tool_reads_and_tool_failure_does_not_disconnect_coordinates():
    now, reader = [0.], FakePosition()
    monitor = CoordinateMonitor('profile', discover=lambda: [77], factory=lambda *a: reader, clock=lambda: now[0])
    assert monitor.status()['tools']['current'] == 890
    now[0] = .2
    monitor.status()
    assert reader.tool_calls == 1
    reader.fail = True
    now[0] = 1.1
    status = monitor.status()
    assert status['state'] == 'connected' and status['tools']['state'] == 'unavailable' and not reader.closed
    reader.fail = False
    now[0] = 2.2
    assert monitor.status()['tools']['current'] == 890
    monitor.reconnect()
    assert monitor.status()['state'] == 'connected'
    monitor.close()


def test_monitor_uses_held_tool_display_instead_of_playback_axe_status():
    reader = FakePosition()
    reader.held_tool_snapshot = Mock(return_value=tool_status('鱼竿', 360, 400, True, True))
    monitor = CoordinateMonitor('profile', discover=lambda: [77], factory=lambda *a: reader)
    assert monitor.status()['tools']['name'] == '鱼竿'
    assert reader.tool_calls == 0
    monitor.close()


@pytest.mark.parametrize('condition,expected', [('normal', 'green'), ('low', 'amber'), ('broken', 'red')])
def test_window_shows_percent_and_warning_and_hides_stale_numbers(condition, expected):
    window = CoordinateWindow.__new__(CoordinateWindow)
    current = {'normal': 890, 'low': 100, 'broken': 0}[condition]
    window.view = SimpleNamespace(position=(0, 0, 0), paused=False,
        status={'tools': axe_pick_status(current, 1100, True, True)})
    window.fields = {'tool_durability': Mock()}
    window.tool_value, window.tool_hint, window.tool_progress, window.tool_fill = Mock(), Mock(), Mock(), 1
    window.tool_progress.winfo_width.return_value = 200
    window.render_tool_durability()
    assert str(current) in window.fields['tool_durability'].set.call_args.args[0]
    assert window.tool_value.configure.call_args.kwargs['fg'] == COLORS[expected]
    window.view.status = {'tools': {'state': 'unavailable'}}
    window.render_tool_durability()
    assert window.fields['tool_durability'].set.call_args.args == ('未读取',)
    assert window.tool_progress.coords.call_args.args == (1, 0, 0, 0., 4)


def test_window_updates_tool_name_and_removes_previous_numbers_on_empty_hand():
    window = CoordinateWindow.__new__(CoordinateWindow)
    window.view = SimpleNamespace(position=(0, 0, 0), paused=False,
        status={'tools': tool_status('鱼竿', 360, 400, True, True)})
    window.fields = {'tool_durability': Mock()}
    window.tool_title, window.tool_value, window.tool_hint = Mock(), Mock(), Mock()
    window.tool_progress, window.tool_fill = Mock(), 1
    window.tool_progress.winfo_width.return_value = 200
    window.render_tool_durability()
    assert window.tool_title.configure.call_args.kwargs['text'] == '鱼竿耐久'
    assert '360 / 400' in window.fields['tool_durability'].set.call_args.args[0]
    window.view.status = {'tools': {'state': 'empty'}}
    window.render_tool_durability()
    assert window.tool_title.configure.call_args.kwargs['text'] == '手持工具耐久'
    assert window.fields['tool_durability'].set.call_args.args == ('未持工具',)
    assert window.tool_progress.coords.call_args.args == (1, 0, 0, 0., 4)
