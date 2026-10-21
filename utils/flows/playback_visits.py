"""Finite deferred visits, sharing the follower's input and state owner."""
from utils.core import thresholds as policy
from utils.flows.playback_signals import _SkipPoint


class VisitActions:
    def finish_visit(self, points, index, position, reason=None):
        point = points[index]
        if reason is not None:
            self.release()
            self.deferred.append(point)
            self.failed_reasons[point['id']] = reason
            self.report('skipped', f'暂时跳过「{point["name"]}」：{reason} · 继续其他点，稍后补采',
                        waypoint=index+1, waypoint_count=len(points), point_id=point['id'],
                        retry_pass=self.retry_pass, skip_reason=reason, position=list(position))
        else:
            self.failed_reasons.pop(point['id'], None)
        self.visit_progress = None
        index += 1
        if index < len(points):
            return points, index
        if self.deferred and self.retry_pass < policy.COORD_VISIT_RETRY_PASSES:
            points, self.deferred = self.deferred, []
            self.retry_pass += 1
            self.report('retrying', f'开始第 {self.retry_pass}/{policy.COORD_VISIT_RETRY_PASSES} 轮补采 · '
                        f'{len(points)} 个暂未到达点 · F9停止', retry_pass=self.retry_pass,
                        waypoint_count=len(points))
            return points, 0
        unresolved = [{'cycle': self.cycle, 'point_id': p['id'], 'name': p['name'],
                       'reason': self.failed_reasons[p['id']]} for p in self.deferred]
        self.unresolved_points.extend(unresolved)
        self.report('cycle_finished', '本轮回放结束' + (f' · 尚有 {len(unresolved)} 个点未完成' if unresolved else ' · 全部点位完成'),
                    waypoint=index, waypoint_count=len(points), unresolved_points=list(self.unresolved_points),
                    retry_pass=self.retry_pass)
        if self.cycle < self.loop_count:
            self.cycle += 1
            self.start_index = 0
            self.retry_pass, self.deferred, self.failed_reasons = 0, [], {}
            self.active_time, self.last_tick = 0., self.clock.monotonic()
            self.last_report = self.last_collect_report = float('-inf')
            return self.waypoints, 0
        return points, index

    def check_visit(self, point):
        if not self.skip_unreachable or self.collection is not None:
            return
        from utils.core.visit_progress import VisitProgress
        position = self.read()
        now = self.clock.monotonic()
        if self.visit_progress is None:
            self.visit_progress = VisitProgress(position, point['position'], now)
        scene = self.navigator.scene_provider() or {} if self.navigator else {}
        reason = self.visit_progress.observe(position, now, ready=scene.get('state') == 'ready')
        if reason:
            raise _SkipPoint(reason)

    def pause_visit_clock(self):
        if self.visit_progress is not None:
            self.visit_progress.pause(self.clock.monotonic())
