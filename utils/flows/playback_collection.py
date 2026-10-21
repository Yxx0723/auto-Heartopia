"""Point/dynamic gathering and evidence checks on one playback session."""
from utils.core import thresholds as policy
from utils.core.special_approach import in_interaction_range, global_special_targets, special_search_targets
from utils.core import special_verdict
from utils.core.special_verdict import snapshot as special_snapshot
from utils.core.coordinate_route import distance, valid_position


SPECIAL_OUTCOME_TEXT = {
    special_verdict.GATHERED: '已确认采到',
    special_verdict.GONE: '资源已消失',
    special_verdict.COOLING: '冷却已开始',
    special_verdict.CHANGED: '状态有变化',
    special_verdict.UNCHANGED: '未见变化',
    special_verdict.UNKNOWN: '未能读到状态',
}

SPECIAL_REACHED_TEXT = {
    None: '动态采集已结束',
    special_verdict.GATHERED: '已确认采到',
    special_verdict.GONE: '资源已消失，判定采集完成',
    special_verdict.COOLING: '冷却已开始，判定采集成功',
    special_verdict.CHANGED: '状态有变化，请核对背包',
    special_verdict.UNCHANGED: '未见状态变化，请核对背包',
    special_verdict.UNKNOWN: '状态未能读取，请核对背包',
}


class CollectionActions:
    def collection_position(self, point):
        position = self.read(force=True)
        if distance(position, point['position']) > policy.COORD_REACH_RADIUS \
                or abs(position[1] - point['position'][1]) > policy.COORD_MAX_HEIGHT_ERROR:
            raise ValueError('采集等待期间离开了当前点位或高度改变，已停止，请重新开始回放')
        return position

    def collect_point(self, point, index, total):
        if 'special_resource' in point:
            return self.collect_special(point, index, total)
        if self.collection is None:
            self.collection = {'sent': False, 'remaining': point.get('collect_wait', self.collect_wait_seconds)}
        self.release()
        self.collection_position(point)
        if not self.collection['sent']:
            if self.food is not None:
                self.read_stamina()
                if self.maintain_stamina(index, total):
                    return False
            if self.tool_selector is not None:
                self.read_tool()
                if self.maintain_tool(index, total):
                    return False
            self.report('collecting', f'点位 {index + 1}/{total} · 按 F 采集 · F6暂停 · F9停止',
                        waypoint=index + 1, waypoint_count=total)
            try:
                self.set_keys({'f'})
                self.collection['sent'] = True
                self.wait(policy.COORD_COLLECTION_PRESS_SECONDS)
            finally:
                self.release()
        remaining = self.collection['remaining']
        now = self.clock.monotonic()
        if now - self.last_collect_report >= policy.COORD_MONITOR_REFRESH_SECONDS:
            self.report('collection_wait', f'点位 {index + 1}/{total} · 采集等待 {remaining:.1f} 秒'
                        ' · F6暂停 · F9停止', waypoint=index + 1, waypoint_count=total,
                        remaining_wait=remaining)
            self.last_collect_report = now
        if remaining > 0:
            step = min(policy.COORD_SAMPLE_SECONDS, remaining)
            before = self.clock.monotonic()
            completed = False
            try:
                self.wait(step)
                completed = True
            finally:
                # Only elapsed active time counts. A pause releases F and
                # resumes this dwell without sending F again or moving to calibrate.
                elapsed = step if completed else min(step, max(0., self.clock.monotonic() - before))
                self.collection['remaining'] = max(0., remaining - elapsed)
        self.collection_position(point)
        return self.collection['remaining'] <= 0

    def special_objects(self, point, scene=None):
        """Every live object matching this dynamic identity, or None when unreadable."""
        if self.navigator is None:
            raise ValueError('动态资源采集需要连接实时场景与自动寻路')
        scene = self.navigator.scene_provider() or {} if scene is None else scene
        if scene.get('state') != 'ready':
            return None
        identity = point['special_resource']
        return [obj for obj in scene.get('objects', [])
                if obj.get('category') == identity['category']
                and obj.get('resource_static_id') == identity['static_id']
                and obj.get('map_dynamic') is True and not obj.get('map_cached')
                and valid_position(obj.get('position'))]

    def special_target(self, point, allow_global=False, scene=None):
        if scene is None:
            scene = self.navigator.scene_provider() or {} if self.navigator else {}
        targets = self.special_objects(point, scene=scene)
        if targets is None:
            from utils.core.special_approach import SpecialTargetUnavailable
            raise SpecialTargetUnavailable('动态资源场景尚未就绪，未按 F')
        if not targets and allow_global:
            identity = point['special_resource']
            targets = (global_special_targets(scene, identity['category'], identity['static_id'])
                       or special_search_targets(scene, identity['category'], identity['static_id']))
            if len(targets) == 1:
                return targets[0]
        if len(targets) != 1 or type(targets[0].get('resource_available')) is not int:
            from utils.core.special_approach import SpecialTargetUnavailable
            raise SpecialTargetUnavailable('当前动态资源未加载、已离开视野或存在歧义，未按 F；请靠近后重新启动采集')
        return targets[0]

    def sample_special(self, point):
        """One refreshed read-only sample: target, 'unknown', or None when it is gone."""
        if self.scene_refresh is not None:
            try:
                self.scene_refresh()
            except (OSError, ValueError):
                pass
        targets = self.special_objects(point)
        if targets is None:
            return 'unknown'
        if not targets:
            return None
        if len(targets) != 1 or type(targets[0].get('resource_available')) is not int:
            return 'unknown'
        return targets[0]

    def settle_special(self, point):
        """Judge the press that just happened from a fresh sample, and only report what moved."""
        progress = self.special_progress
        before = progress['before']
        if before is None:
            return
        progress['before'] = None
        deadline = self.clock.monotonic() + policy.COORD_SPECIAL_STATE_SETTLE_SECONDS
        observed, samples = [], {}
        while True:
            self.wait(policy.COORD_SPECIAL_STATE_POLL_SECONDS)
            sample = self.sample_special(point)
            if sample != 'unknown':
                after = special_snapshot(sample) if sample is not None else None
                outcome = special_verdict.verdict(before, after)
                observed.append(outcome)
                samples[outcome] = after
                if special_verdict.conclusive(outcome):
                    break
            if self.clock.monotonic() >= deadline:
                break
        outcome = special_verdict.strongest(observed)
        after = samples.get(outcome)
        progress['outcome'] = outcome
        progress['after'] = after
        if outcome == special_verdict.UNCHANGED:
            progress['unchanged'] += 1
        else:
            progress['unchanged'] = 0
        self.report('collecting', f'{point["name"]} · 第 {progress["sent"]} 次按 F · {SPECIAL_OUTCOME_TEXT[outcome]}',
                    waypoint=progress.get('index', 1), waypoint_count=progress.get('total', 1),
                    resource_press_count=progress['sent'], resource_outcome=outcome,
                    resource_available_before=before.get('available'),
                    resource_available_after=(after or {}).get('available'),
                    resource_cold_end_before=before.get('cold_end'),
                    resource_cold_end_after=(after or {}).get('cold_end'),
                    resource_state_digest=(after or {}).get('digest'))

    def finish_special(self, point, index, total):
        """Report what is actually known about this dynamic resource and end the point."""
        progress = self.special_progress
        sent, outcome = progress['sent'], progress['outcome']
        before, after = progress.get('pressed_from'), progress.get('after')
        name = point['name']
        if outcome == special_verdict.GATHERED:
            message = f'{name} · 已确认采到（可用次数 {before}→{after.get("available")}）· 共按 F {sent} 次'
        elif outcome == special_verdict.GONE:
            message = f'{name} · 资源已从场景消失，判定采集完成 · 共按 F {sent} 次'
        elif outcome == special_verdict.COOLING:
            message = f'{name} · 冷却已开始，判定本次采集成功 · 共按 F {sent} 次'
        elif outcome == special_verdict.UNCHANGED:
            message = (f'{name} · 连按 F {sent} 次资源状态无变化，已停止 · 请核对背包'
                       '（可能已在冷却或未命中）')
        elif outcome == special_verdict.CHANGED:
            message = f'{name} · 已按 F {sent} 次，资源状态有变化但未见掉落标志 · 请核对背包'
        else:
            message = f'{name} · 已按 F {sent} 次，未能读到资源状态 · 请核对背包'
        self.report('collecting', message, waypoint=index+1, waypoint_count=total,
                    resource_press_count=sent, resource_outcome=outcome,
                    resource_confirmed=special_verdict.confirmed(outcome))
        return True

    def collect_special(self, point, index, total):
        self.release()
        limit = point['special_resource'].get('press_count', 10)
        progress = self.special_progress
        if progress is None:
            progress = self.special_progress = {'sent': 0, 'unchanged': 0, 'outcome': None,
                                                'before': None, 'after': None, 'pressed_from': None}
        progress['index'], progress['total'] = index, total
        if progress['before'] is not None:
            self.settle_special(point)
        if special_verdict.conclusive(progress['outcome']) \
                or progress['unchanged'] >= policy.COORD_SPECIAL_UNCHANGED_LIMIT \
                or progress['sent'] >= limit:
            return self.finish_special(point, index, total)
        try:
            target = self.special_target(point)
        except ValueError:
            # A target that vanished after a press is a completed collection,
            # not a broken route. A second read guards against one short scan.
            if progress['sent'] > 0 and self.sample_special(point) is None:
                progress['outcome'] = special_verdict.GONE
                return self.finish_special(point, index, total)
            raise
        if target['resource_available'] <= 0:
            from utils.core.special_approach import SpecialTargetUnavailable
            raise SpecialTargetUnavailable('资源当前没有可用采集标志，已释放按键，请核对是否已采完或处于冷却')
        current = self.read(force=True)
        if not in_interaction_range(current, target['position']):
            return False
        progress['before'] = special_snapshot(target)
        progress['pressed_from'] = progress['before']['available']
        self.report('collecting', f'{point["name"]} · 连按 F {progress["sent"]+1}/{limit} · F6暂停 · F9停止',
                    waypoint=index+1, waypoint_count=total, resource_press_count=progress['sent'],
                    position=list(self.position))
        try:
            self.set_keys({'f'})
            progress['sent'] += 1
            self.wait(policy.COORD_COLLECTION_PRESS_SECONDS)
        finally:
            self.release()
        self.settle_special(point)
        if special_verdict.conclusive(progress['outcome']) \
                or progress['unchanged'] >= policy.COORD_SPECIAL_UNCHANGED_LIMIT:
            return self.finish_special(point, index, total)
        return False
