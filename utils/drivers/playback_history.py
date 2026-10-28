"""Per-run local route snapshots and streaming traces, without game input."""
from datetime import datetime
import json
import hashlib
import re
import math
from pathlib import Path
import threading
from uuid import uuid4

from utils.core.coordinate_route import valid_position


class PlaybackHistory:
    def __init__(self, root, route, name, options, wall_clock=None):
        self.wall_clock = wall_clock or (lambda: datetime.now().astimezone())
        self.lock = threading.RLock()
        self.streams = []
        self.error = None
        self.archive_error = None
        self.route_digest = None
        self.closed = False
        self.origin = self.last_sample = self.last_flush = None
        self.previous = None
        self.summary_at = None
        self.context = {}
        self.route_points = route.get('points', [])
        self.plan_signature = self.incident_signature = None
        self.previous_stamp = None
        now = self.wall_clock()
        self.run_id = now.strftime('%Y%m%d-%H%M%S-%f')+'-'+uuid4().hex[:8]
        safe_name = re.sub(r'[^\w .-]', '_', str(name))[:40].strip(' .') or '未命名路线'
        self.path = Path(root)/(safe_name+'-'+self.run_id)
        self.summary = {'schema': 'playback-history-v1', 'route_name': name,
                        'created_at': now.isoformat(timespec='milliseconds'),
                        'started_at': None, 'ended_at': None, 'state': 'waiting',
                        'options': options, 'sample_count': 0, 'distance_xz': 0.,
                        'reached_count': 0, 'cycle_finished_count': 0,
                        'detail_schema': 'playback-evidence-v1', 'planned_path_count': 0,
                        'incident_count': 0, 'movement_stall_count': 0}
        self.events = self.trajectory = None
        self.plans = self.incidents = None
        try:
            self.path.mkdir(parents=True, exist_ok=False)
            self._json('route.json', route)
            self.route_digest = hashlib.sha256((self.path/'route.json').read_bytes()).hexdigest()
            self.events = (self.path/'events.jsonl').open('a', encoding='utf-8')
            self.streams.append(self.events)
            self.trajectory = (self.path/'trajectory.jsonl').open('a', encoding='utf-8')
            self.streams.append(self.trajectory)
            for name in ('plans', 'incidents'):
                stream = (self.path/(name+'.jsonl')).open('a', encoding='utf-8')
                setattr(self, name, stream)
                self.streams.append(stream)
            self._json('summary.json', self.summary)
            self._learning_index()
        except (OSError, ValueError, TypeError) as exc:
            self.error = str(exc)

    def _json(self, filename, data):
        target = self.path/filename
        temporary = target.with_suffix('.tmp')
        temporary.write_text(json.dumps(data, ensure_ascii=False, allow_nan=False, indent=2), encoding='utf-8')
        temporary.replace(target)

    def _learning_index(self, finalized=False):
        """Small discoverable index; raw observations never become learned walls."""
        data = {'schema': 'playback-learning-index-v1', 'run_id': self.run_id,
                'route_name': self.summary['route_name'], 'route_sha256': self.route_digest,
                'state': self.summary['state'], 'finalized': finalized,
                'run_complete': finalized and self.summary['state'] == 'finished',
                'automatic_promotion': False, 'collision_contact_confirmed': False,
                'requires_scene_and_traversal_revalidation': True,
                'completion_is_not_loot_confirmation': True,
                'files': {'route_snapshot': 'route.json', 'actual_trajectory': 'trajectory.jsonl',
                          'planned_paths': 'plans.jsonl', 'collision_candidates': 'incidents.jsonl',
                          'events': 'events.jsonl', 'summary': 'summary.json'},
                'statistics': {key: self.summary.get(key, 0) for key in
                               ('sample_count', 'distance_xz', 'reached_count', 'skipped_count',
                                'planned_path_count', 'incident_count', 'movement_stall_count')}}
        try:
            self._json('learning.json', data)
            self.archive_error = None
        except (OSError, ValueError, TypeError) as exc:
            # An optional index must not disable the original streaming trace.
            self.archive_error = str(exc)

    def event(self, status):
        with self.lock:
            if self.closed or self.error:
                return
            try:
                now = self.wall_clock()
                stamp = now.isoformat(timespec='milliseconds')
                previous_state = self.summary['state']
                # Actual travel cannot reconstruct an intended detour. Store
                # changed plans separately instead of repeating them per frame.
                entry = {k: v for k, v in status.items() if k != 'navigation_path'}
                self.events.write(json.dumps({'at': stamp, **entry}, ensure_ascii=False, allow_nan=False)+'\n')
                self.events.flush()
                self.context.update({k: status[k] for k in ('state', 'cycle', 'waypoint') if k in status})
                self._details(status, now, stamp)
                state = status['state']
                if state == 'playing' and self.summary['started_at'] is None:
                    self.summary['started_at'] = stamp
                self.summary.update(state=state, last_message=status.get('message', ''), **{
                    k: status[k] for k in ('cycle', 'waypoint', 'waypoint_count', 'loop_count', 'skipped_count') if k in status})
                self.summary.update({k: status[k] for k in (
                    'patrol_checked', 'patrol_total', 'patrol_missing', 'patrol_failed',
                    'patrol_result', 'patrol_completed_resources', 'resource_outcome', 'resource_confirmed') if k in status})
                if status.get('resource_evidence_id'):
                    # One evidence id is updated, never summed per polling/report event.
                    harvests = self.summary.setdefault('harvest_evidence', {})
                    harvests[status['resource_evidence_id']] = {
                        k: v for k, v in status.items() if k.startswith('resource_')}
                if state == 'reached':
                    self.summary['reached_count'] += 1
                if state == 'cycle_finished':
                    self.summary['cycle_finished_count'] += 1
                if state in ('finished', 'stopped', 'error', 'partial'):
                    self.summary['ended_at'] = stamp
                if previous_state != state or self.summary_at is None or now.timestamp()-self.summary_at >= 1:
                    self._json('summary.json', self.summary)
                    self.summary_at = now.timestamp()
            except (OSError, ValueError, TypeError) as exc:
                self.error = str(exc)

    def _details(self, status, now, stamp):
        context = {k: self.context[k] for k in ('cycle', 'waypoint') if k in self.context}
        path = status.get('navigation_path')
        if isinstance(path, (list, tuple)) and 0 < len(path) <= 10000 and all(valid_position(p) for p in path):
            signature = (context.get('cycle'), context.get('waypoint'), tuple(map(tuple, path)))
            if signature != self.plan_signature:
                self.plans.write(json.dumps({'at': stamp, **context, 'path': path,
                                             'kind': 'planned_path_snapshot'}, ensure_ascii=False, allow_nan=False)+'\n')
                self.plans.flush()
                self.summary['planned_path_count'] += 1
                self.plan_signature = signature
        elif path == []:
            self.plan_signature = None
        kind = status.get('incident_kind')
        if not kind and status.get('navigation_wait_reason'):
            kind = 'navigation_wait'
        if not kind and status.get('state') in ('blocked', 'error'):
            kind = status['state']
        signature = (kind, context.get('cycle'), context.get('waypoint'), status.get('navigation_wait_reason'))
        if not kind:
            self.incident_signature = None
            return
        if kind == 'navigation_wait' and signature == self.incident_signature:
            return
        self.incident_signature = signature
        position = status.get('position')
        source = status.get('position_source', 'reported')
        age = status.get('position_age_seconds')
        if not valid_position(position):
            position = list(self.previous) if self.previous is not None else None
            source = 'last_recorded_sample' if position is not None else 'unavailable'
            age = max(0., (now-self.previous_stamp).total_seconds()) if self.previous_stamp else None
        target = status.get('target')
        index = context.get('waypoint')
        if not valid_position(target) and type(index) is int and 1 <= index <= len(self.route_points):
            target = self.route_points[index-1].get('position')
        row = {**{k: v for k, v in status.items() if k != 'navigation_path'}, **context,
               'at': stamp, 'kind': kind, 'position': position, 'position_source': source,
               'position_age_seconds': age, 'target': target if valid_position(target) else None,
               'contact_confirmed': False}
        self.incidents.write(json.dumps(row, ensure_ascii=False, allow_nan=False)+'\n')
        self.incidents.flush()
        self.summary['incident_count'] += 1
        if kind == 'movement_stall':
            self.summary['movement_stall_count'] += 1

    def sample(self, position, monotonic):
        with self.lock:
            if self.closed or self.error or not valid_position(position):
                return
            if self.last_sample is not None and monotonic-self.last_sample < .1:
                return
            try:
                if self.origin is None:
                    self.origin = monotonic
                elapsed = round(monotonic-self.origin, 4)
                stamp = self.wall_clock()
                entry = {'at': stamp.isoformat(timespec='milliseconds'),
                         'elapsed_seconds': elapsed, 'position': list(position), **self.context}
                self.trajectory.write(json.dumps(entry, ensure_ascii=False, allow_nan=False)+'\n')
                if self.last_flush is None or monotonic-self.last_flush >= 1:
                    self.trajectory.flush()
                    self.last_flush = monotonic
                if self.previous is not None:
                    self.summary['distance_xz'] += math.hypot(position[0]-self.previous[0], position[2]-self.previous[2])
                self.previous = tuple(position)
                self.previous_stamp = stamp
                self.last_sample = monotonic
                self.summary['sample_count'] += 1
                self.summary['sampled_duration_seconds'] = elapsed
            except (OSError, ValueError, TypeError) as exc:
                self.error = str(exc)

    def close(self):
        with self.lock:
            if self.closed:
                return
            try:
                if not self.error:
                    if self.summary['state'] not in ('finished', 'stopped', 'error', 'partial'):
                        self.summary['state'] = 'interrupted'
                    self.summary['ended_at'] = self.summary['ended_at'] or self.wall_clock().isoformat(timespec='milliseconds')
                    if self.summary['started_at']:
                        self.summary['duration_seconds'] = max(0., (
                            datetime.fromisoformat(self.summary['ended_at'])-
                            datetime.fromisoformat(self.summary['started_at'])).total_seconds())
                    self._json('summary.json', self.summary)
                    self._learning_index(finalized=True)
            except (OSError, ValueError, TypeError) as exc:
                self.error = str(exc)
            finally:
                for stream in self.streams:
                    try:
                        stream.close()
                    except OSError as exc:
                        self.error = str(exc)
                self.closed = True


class RecordingPosition:
    """Delegate all reader capabilities; sample only successful coordinate reads."""
    def __init__(self, reader, history, monotonic):
        self.reader, self.history, self.monotonic = reader, history, monotonic

    def read(self):
        position = self.reader.read()
        self.history.sample(position, self.monotonic())
        return position

    def __getattr__(self, name):
        return getattr(self.reader, name)

class GuidanceRecordingPosition(RecordingPosition):
    """Record the semantic playback read without bypassing attached checks."""
    def read_for_playback(self):
        method = getattr(self.reader, 'read_for_playback', self.reader.read)
        position = method()
        self.history.sample(position, self.monotonic())
        return position
