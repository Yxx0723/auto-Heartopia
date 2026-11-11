from copy import deepcopy

import pytest

from utils.core.cooking_catalog import load_catalog
from utils.flows import cooking_single_pot as flow


class BeforeStart(Exception):
    """Stop the simulation at the verified start target, before opening the pot."""


class Kitchen:
    def __init__(self, changes, blue_count):
        self.changes = iter(changes)
        self.time = 0
        self.buttons = set()
        self.keys = set()
        self.position = (0, 0)
        self.clicks = []
        self.ups = 0
        self.observations = []
        self.candidates = []
        self.second_candidate_reads = 0
        self.shift_second_candidate = False
        self.replaced = []
        self.picker = None
        self.pending = None
        self.slots = [
            dict(kind=kind, required_id=required, filled=True, item_id=item, net_id=item)
            for kind, required, item in [('item', 46102, 46102), ('item', 46103, 46103),
                                         ('group', 16, 46107), ('group', 16, 46107)]
        ]
        self.bag = [dict(item_id=item, net_id=item, count=count, locked=False, disabled=False, star=0)
                    for item, count in [(46102, 1), (46103, 1), (46107, 2), (46112, blue_count)]]

    def monotonic(self): return self.time
    def sleep(self, delay): self.time += delay
    def size(self): return (200, 100)
    def get_rect(self): return (100, 200, 300, 300)
    def pointer(self): return (*self.position, True)
    def foreground(self): return True
    def snapshot(self): return (True, False)
    def release_all(self): assert not self.buttons and not self.keys
    def bind(self, identity): assert identity == [1, 2, 3]

    def state(self):
        return dict(status=0, status_started=1, stove_identity=[1, 2, 3])

    def inventory(self):
        return dict(scope='bag', items=self.bag, counts={45160: 0})

    def materials(self):
        return dict(selected=dict(recipe_id=45153, name='原味浮云卷'), slots=deepcopy(self.slots),
                    stove_identity=[1, 2, 3], picker_open=self.picker is not None,
                    selected_slot=self.picker)

    def menu_target(self, size, kind, *, index=None, net_id=None, slot_observation=None,
                    material_observation=None, start_observation=None):
        self.pending = (kind, index)
        if kind == 'start':
            assert [s['item_id'] for s in self.slots] == [46102, 46103, 46112, 46112]
            raise BeforeStart
        if kind == 'slot':
            offset, widget, binding = 0, 100 + index, 'owner'
            if index == 3:
                change = next(self.changes, (0, widget, binding))
                if isinstance(change, Exception):
                    raise change
                offset, widget, binding = change
            rect = (10 + offset, 20, 30 + offset, 40)
            slot_observation.update(object_address=widget, local_rect=(0, 0, 20, 20),
                                    canvas_scale=(1, 1), selected_material_id=self.slots[index]['item_id'])
            return dict(kind=kind, scope='scene', stove_identity=[1, 2, 3], index=index,
                        widget=widget, panel_binding=binding, rect=rect,
                        recipe_id=45153, slot=deepcopy(self.slots[index]))
        offset = 0
        if material_observation is not None:
            material_observation.update(widget_address=200, row_address=300)
            if index == 3:
                self.second_candidate_reads += 1
                if self.shift_second_candidate and self.second_candidate_reads == 2:
                    offset = 3
        return dict(kind=kind, index=index, rect=(50 + offset, 20, 70 + offset, 40),
                    item_id=46112, visible=True)

    def execute(self, event):
        if event['type'] == 'cursor_move':
            self.position = (event['x'], event['y'])
        elif event['type'] == 'mouse_down':
            self.buttons.add('left')
            self.clicks.append(self.pending)
            kind, index = self.pending
            if kind == 'slot':
                self.picker = index
            elif kind == 'material':
                self.slots[index].update(item_id=46112, net_id=46112)
                self.picker = None
        elif event['type'] == 'mouse_up':
            self.buttons.remove('left')
            self.ups += 1

    def report(self, state, message, **data):
        if state == 'variant_slot_observation':
            self.observations.append(data['slot_observation'])
        if state == 'variant_slot_replaced':
            self.replaced.append(data['slot_index'])
        if state == 'variant_material_candidate':
            self.candidates.append(data['material_candidate'])


@pytest.fixture
def run(monkeypatch, tmp_path):
    monkeypatch.setattr(flow, 'begin_move', lambda *args: {})
    monkeypatch.setattr(flow, 'execute_move', lambda device, event, diagnostic: device.execute(event))
    monkeypatch.setattr(flow, 'finish_move', lambda *args: {})
    recipe = next(r for r in load_catalog()['recipes'] if r['recipe_id'] == 45160)

    def execute(kitchen, error):
        with pytest.raises(error):
            flow.run_single_pot(recipe=recipe, reader=kitchen, window=kitchen, controls=kitchen,
                                device=kitchen, inventory=kitchen.inventory, clock=kitchen,
                                report=kitchen.report, journal=tmp_path / 'pot.json')
        assert kitchen.ups == len(kitchen.clicks)
        assert not kitchen.buttons and not kitchen.keys
    return execute


@pytest.mark.parametrize('changes,offset,replaced_widget', [
    ([], 0, False),
    ([(x, 103, 'owner') for x in (0, 5, 5, 5)], 5, False),
    ([(x, 103, 'owner') for x in (0, 5, 9, 9, 9)], 9, False),
    ([(0, widget, 'owner') for widget in (103, 503, 503, 503)], 0, True),
])
def test_both_slots_confirm_once(run, changes, offset, replaced_widget):
    kitchen = Kitchen(changes, 2)
    run(kitchen, BeforeStart)
    assert kitchen.clicks == [('slot', 2), ('material', 2), ('slot', 3), ('material', 3)]
    assert kitchen.replaced == [2, 3]
    last = kitchen.observations[-1]
    assert last['stage'] == 'before_dispatch' and last['binding_stable'] and last['rect_stable']
    assert last['screen_rect'][0] == 110 + offset
    if replaced_widget:
        assert any(s.get('binding_changed_fields') == ['widget'] for s in kitchen.observations)


@pytest.mark.parametrize('changes,reason', [
    ([(x, 103, 'owner') for x in (0, 5, 9, 14)], 'rect_changed'),
    ([(0, 103, 'owner'), (0, 103, 'other')], 'binding_changed'),
    ([(0, 103, 'owner'), ValueError('对象失效')], 'read_or_validation_failed'),
    ([ValueError('坐标转换失败')], 'read_or_validation_failed'),
    ([(0, 103, 'owner'), (0, 103, 'owner'), (3, 103, 'owner')], 'rect_changed'),
])
def test_second_slot_rejects_uncertain_targets(run, changes, reason):
    kitchen = Kitchen(changes, 2)
    run(kitchen, ValueError)
    assert kitchen.clicks == [('slot', 2), ('material', 2)]
    assert kitchen.replaced == [2]
    assert reason in kitchen.observations[-1]['stability_failure_reason']


def test_missing_blue_sugar_never_clicks(run):
    kitchen = Kitchen([], 1)
    run(kitchen, ValueError)
    assert not kitchen.clicks and not kitchen.observations


def test_second_material_candidate_shift_logs_both_reads_without_clicking(run):
    kitchen = Kitchen([], 2)
    kitchen.shift_second_candidate = True
    run(kitchen, ValueError)
    assert kitchen.clicks == [('slot', 2), ('material', 2), ('slot', 3)]
    assert kitchen.replaced == [2]
    first, second = [entry for entry in kitchen.candidates if entry['slot_index'] == 3]
    assert (first['sequence'], second['sequence']) == (1, 2)
    assert first['candidate']['click_target']['item_id'] == 46112
    assert second['candidate']['click_target']['item_id'] == 46112
    assert first['candidate']['screen_rect'] == (150, 220, 170, 240)
    assert second['candidate']['screen_rect'] == (153, 220, 173, 240)
    assert second['changed_fields'] == ['screen_rect']
    assert second['changed_target_fields'] == []
    assert second['rect_edge_delta'] == (3, 0, 3, 0)
    assert second['rect_stable'] is False


class StartKitchen(Kitchen):
    def __init__(self, scenario):
        super().__init__([], 2)
        self.scenario = scenario
        self.start_reads = 0
        self.comparisons = []
        self.reserved = []
        self.started = False

    def get_rect(self):
        shift = 8 if self.scenario == 'window' and self.start_reads >= 1 else 0
        return (100 + shift, 200, 300 + shift, 300)

    def state(self):
        if self.started:
            raise BeforeStart
        return super().state()

    def start_diagnostics(self, size):
        return {}

    def materials(self):
        view = super().materials()
        if self.scenario == 'slots' and self.start_reads >= 3:
            view['slots'][0]['item_id'] = 0
        return view

    def snapshot(self):
        return (True, self.scenario == 'f9' and self.start_reads == 1)

    def foreground(self):
        return not (self.scenario == 'focus' and self.start_reads == 1)

    def pause(self):
        pass

    def menu_target(self, size, kind, *, start_observation=None, **kwargs):
        if kind != 'start':
            return super().menu_target(size, kind, **kwargs)
        self.pending = ('start', None)
        self.start_reads += 1
        second = self.start_reads == 2
        shift = 2 if second and self.scenario == 'rect' else 0
        target = dict(kind='start', scope='scene', stove_identity=[1, 2, 3],
                      rect=(10 + shift, 20, 30 + shift, 40))
        if start_observation is not None:
            start_observation.update(
                button_address=8 if second and self.scenario == 'button' else 7,
                panel_address=6 if second and self.scenario == 'panel' else 5,
                button_active=not (second and self.scenario == 'activation'),
                canvas_scale=(2, 2) if second and self.scenario == 'canvas' else (1, 1),
                picker_open=second and self.scenario == 'picker',
                m_Interactable=not (second and self.scenario == 'interaction'))
            if second and self.scenario == 'stove':
                target['stove_identity'] = [1, 2, 4]
            if second and self.scenario in ('picker', 'interaction', 'read_error'):
                raise ValueError(self.scenario)
        return target

    def execute(self, event):
        super().execute(event)
        if self.pending == ('start', None) and event['type'] == 'mouse_up':
            self.started = True

    def report(self, state, message, **data):
        super().report(state, message, **data)
        if state == 'variant_start_comparison':
            self.comparisons.append(deepcopy(data['start_comparison']))
        if state == 'start_reserved':
            self.reserved.append(data['material_slots'])


@pytest.mark.parametrize('scenario,changed', [
    ('stable', None), ('button', 'button_address'), ('panel', 'panel_address'),
    ('activation', 'button_active'), ('canvas', 'canvas_scale'), ('window', 'window_rect'),
])
def test_start_observation_preserves_existing_comparison(run, monkeypatch, scenario, changed):
    # Isolate the existing two-snapshot comparison from the new wait.
    monkeypatch.setattr(flow, 'wait_for_variant_start', lambda read_target, **kwargs: read_target)
    kitchen = StartKitchen(scenario)
    run(kitchen, BeforeStart)
    trace, = kitchen.comparisons
    assert trace['comparison_passed'] is True
    assert trace['changed_target_fields'] == []
    assert trace['rect_edge_delta'] == (0, 0, 0, 0)
    if changed is not None:
        assert changed in trace['changed_fields']
    slots, = kitchen.reserved
    assert [slot['item_id'] for slot in slots] == [46102, 46103, 46112, 46112]
    assert kitchen.clicks.count(('start', None)) == 1


@pytest.mark.parametrize('scenario', ['rect', 'stove', 'picker', 'interaction',
                                     'read_error', 'slots', 'f9', 'focus'])
def test_start_rejection_preserves_evidence_and_never_clicks(run, monkeypatch, scenario):
    monkeypatch.setattr(flow, 'wait_for_variant_start', lambda read_target, **kwargs: read_target)
    kitchen = StartKitchen(scenario)
    run(kitchen, ValueError)
    trace, = kitchen.comparisons
    assert trace['first_snapshot']['observation']['button_address'] == 7
    assert trace['first_snapshot']['recipe']['target_recipe_id'] == 45160
    assert trace['first_snapshot']['recipe']['base_recipe_id'] == 45153
    assert ('start', None) not in kitchen.clicks
    assert not kitchen.reserved
    if scenario == 'rect':
        assert trace['comparison_passed'] is False
        assert trace['rect_edge_delta'] == (2, 0, 2, 0)
    elif scenario == 'stove':
        assert trace['changed_target_fields'] == ['stove_identity']
    elif scenario in ('picker', 'interaction', 'read_error'):
        assert trace['second_snapshot']['read_errors'][0]['message'] == scenario
        assert trace['error']['stage'] == 'second_snapshot'
    elif scenario in ('f9', 'focus'):
        assert trace['second_snapshot'] is None
        assert trace['error']['stage'] == 'between_reads'


class LayoutKitchen(StartKitchen):
    def __init__(self, scenario):
        super().__init__(scenario)
        self.wait_events = []
        self.wait_started = None

    def menu_target(self, size, kind, *, start_observation=None, **kwargs):
        if kind != 'start':
            return super().menu_target(size, kind, **kwargs)
        scenario=self.scenario
        if self.wait_started is None:self.wait_started=self.time
        # Use the ordinary simulated start target, then supply the new observations.
        self.scenario='stable'
        try:
            target=super().menu_target(size,kind,start_observation=start_observation)
        finally:
            self.scenario=scenario
        count=self.start_reads
        if scenario=='read_error' and count==2:raise OSError('模拟读取失败')
        offset=0
        if scenario in ('animation','over_threshold') and count>=2:offset=3.833
        if scenario=='threshold' and count>=2:offset=1.5
        if scenario=='moving':offset=count*2
        if scenario=='final_button' and count>=5:offset=3.833
        target['rect']=(10+offset,20,30+offset,40)
        if scenario=='stove' and count>=2:target['stove_identity']=[1,2,4]
        panel=6 if scenario=='panel' and count>=2 else 5
        active=scenario=='active' or (scenario=='animation' and count==1)
        start_observation.update(
            scene_scope='other' if scenario=='scene' and count>=2 else 'scene',
            ui_manager=2 if scenario=='manager' and count>=2 else 1,
            panel_address=panel, stove_identity=target['stove_identity'],
            base_recipe_id=999 if scenario=='recipe' and count>=2 else 45153,
            picker_state_verified=scenario!='unknown', picker_closed=not active,
            picker_root_active=active, button_binding_valid=True, button_bar_active=True,
            button_enabled=(
                {'state': 'unknown', 'source': 'm_Enabled',
                 'error': {'type': 'ValueError', 'message': '原生场景对象缺少字段：m_Enabled'}}
                if scenario.startswith('enabled_unknown') else
                {'state': 'disabled' if scenario == 'disabled' else 'enabled'}),
            m_Interactable=scenario not in ('interaction', 'enabled_unknown_not_interactable'),
            m_GroupsAllowInteraction=True, button_active=scenario!='activation',
            button_panel=99 if scenario=='wrong_owner' and count>=2 else panel,
            button_address=8 if scenario=='rebuild' and count>=2 else 7,
            button_wrapper=11, button_native=12, target_graphic=13, rect_transform=14)
        return target

    def report(self,state,message,**data):
        super().report(state,message,**data)
        if state=='variant_start_stability_wait':
            self.wait_events.append(deepcopy(data))


@pytest.mark.parametrize('scenario,wait_samples', [
    ('animation',3), ('over_threshold',3), ('threshold',2), ('rebuild',3), ('stable',2),
])
def test_variant_start_wait_then_dispatches_once(run, scenario, wait_samples):
    kitchen=LayoutKitchen(scenario)
    run(kitchen, BeforeStart)
    record,=kitchen.wait_events
    assert record['failure'] is None and len(record['samples'])==wait_samples
    assert kitchen.start_reads==wait_samples+3  # two original reads and the final reread
    assert kitchen.clicks.count(('start',None))==1
    assert len(kitchen.reserved)==1


@pytest.mark.parametrize('scenario,message', [
    ('active','500ms'), ('unknown','关闭状态未知'), ('moving','500ms'),
    ('wrong_owner','归属'), ('panel','身份变化'), ('scene','身份变化'),
    ('manager','身份变化'), ('stove','身份变化'), ('recipe','身份变化'),
    ('interaction','交互状态'), ('activation','显示'), ('f9','F9'), ('focus','失焦'),
    ('slots',None), ('final_button',None), ('read_error','模拟读取失败'),
    ('disabled','已禁用'), ('enabled_unknown_not_interactable','交互状态'),
])
def test_variant_start_wait_stops_without_starting(run, scenario, message):
    kitchen=LayoutKitchen(scenario)
    run(kitchen, OSError if scenario=='read_error' else ValueError)
    record,=kitchen.wait_events
    if message is not None:
        assert message in record['failure']['message']
    if scenario in ('active','moving'):
        assert kitchen.time-kitchen.wait_started==pytest.approx(.5)
    assert ('start',None) not in kitchen.clicks
    assert not kitchen.reserved
    if scenario == 'enabled_unknown_not_interactable':
        assert record['samples'][0]['button_enabled']['state'] == 'unknown'


class StartReadbackKitchen(LayoutKitchen):
    def __init__(self, preparing):
        super().__init__('enabled_unknown')
        self.preparing = preparing
        self.phases = []
        self.started_at = None

    def state(self):
        if not self.started:
            return super().state()
        if self.phases and self.phases[-1] == 1:
            raise BeforeStart
        if self.preparing and self.time > self.started_at:
            return dict(status=1, status_started=2, previous_status=0, stove_identity=[1, 2, 3],
                        owner_net_id=5, local_net_id=5)
        return dict(status=0, status_started=1, previous_status=0, stove_identity=[1, 2, 3])

    def execute(self, event):
        super().execute(event)
        if self.started and self.started_at is None:
            self.started_at = self.time

    def report(self, state, message, **data):
        super().report(state, message, **data)
        if state == 'native_observation':
            self.phases.append(data['native_state']['status'])


@pytest.mark.parametrize('preparing', [True, False])
def test_unknown_enabled_still_requires_native_start_confirmation(run, preparing):
    kitchen = StartReadbackKitchen(preparing)
    run(kitchen, BeforeStart if preparing else ValueError)
    record, = kitchen.wait_events
    assert record['failure'] is None
    for sample in record['samples']:
        assert sample['button_enabled'] == {
            'state': 'unknown', 'source': 'm_Enabled',
            'error': {'type': 'ValueError', 'message': '原生场景对象缺少字段：m_Enabled'}}
    assert len(kitchen.reserved) == 1
    assert kitchen.clicks.count(('start', None)) == 1
    assert kitchen.phases == ([0, 1] if preparing else [0])
    if not preparing:
        assert 8 <= kitchen.time - kitchen.started_at < 8.1
