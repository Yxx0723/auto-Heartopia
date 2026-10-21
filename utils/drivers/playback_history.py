"""Per-run local route snapshots and streaming traces, without game input."""
from datetime import datetime
import json
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
        self.closed = False
        self.origin = self.last_sample = self.last_flush = None
        self.previous = None
        self.summary_at = None
        self.context = {}
        now = self.wall_clock()
        self.path = Path(root)/(now.strftime('%Y%m%d-%H%M%S-%f')+'-'+uuid4().hex[:8])
        self.summary = {'schema': 'playback-history-v1', 'route_name': name,
                        'created_at': now.isoformat(timespec='milliseconds'),
                        'started_at': None, 'ended_at': None, 'state': 'waiting',
                        'options': options, 'sample_count': 0, 'distance_xz': 0.,
                        'reached_count': 0, 'cycle_finished_count': 0,
                        'skipped_count': 0, 'unresolved_points': []}
        self.events = self.trajectory = None
        try:
            self.path.mkdir(parents=True, exist_ok=False)
            self._json('route.json', route)
            self.events = (self.path/'events.jsonl').open('a', encoding='utf-8')
            self.streams.append(self.events)
            self.trajectory = (self.path/'trajectory.jsonl').open('a', encoding='utf-8')
            self.streams.append(self.trajectory)
            self._json('summary.json', self.summary)
        except (OSError, ValueError, TypeError) as exc:
            self.error = str(exc)

    def _json(self, filename, data):
        target = self.path/filename
        temporary = target.with_suffix('.tmp')
        temporary.write_text(json.dumps(data, ensure_ascii=False, allow_nan=False, indent=2), encoding='utf-8')
        temporary.replace(target)

    def event(self, status):
        with self.lock:
            if self.closed or self.error:
                return
            try:
                now = self.wall_clock()
                stamp = now.isoformat(timespec='milliseconds')
                previous_state = self.summary['state']
                # Planned navigation paths are already represented by real samples;
                # avoid duplicating large planned polygons in every status message.
                entry = {k: v for k, v in status.items() if k != 'navigation_path'}
                self.events.write(json.dumps({'at': stamp, **entry}, ensure_ascii=False, allow_nan=False)+'\n')
                self.events.flush()
                self.context.update({k: status[k] for k in ('state', 'cycle', 'waypoint') if k in status})
                state = status['state']
                if state == 'playing' and self.summary['started_at'] is None:
                    self.summary['started_at'] = stamp
                self.summary.update(state=state, last_message=status.get('message', ''), **{
                    k: status[k] for k in ('cycle', 'waypoint', 'waypoint_count', 'loop_count') if k in status})
                if state == 'reached':
                    self.summary['reached_count'] += 1
                if state == 'cycle_finished':
                    self.summary['cycle_finished_count'] += 1
                if state == 'skipped':
                    self.summary['skipped_count'] += 1
                if 'unresolved_points' in status:
                    self.summary['unresolved_points'] = status['unresolved_points']
                if state in ('finished', 'stopped', 'error'):
                    self.summary['ended_at'] = stamp
                if previous_state != state or self.summary_at is None or now.timestamp()-self.summary_at >= 1:
                    self._json('summary.json', self.summary)
                    self.summary_at = now.timestamp()
            except (OSError, ValueError, TypeError) as exc:
                self.error = str(exc)

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
                entry = {'at': self.wall_clock().isoformat(timespec='milliseconds'),
                         'elapsed_seconds': elapsed, 'position': list(position), **self.context}
                self.trajectory.write(json.dumps(entry, ensure_ascii=False, allow_nan=False)+'\n')
                if self.last_flush is None or monotonic-self.last_flush >= 1:
                    self.trajectory.flush()
                    self.last_flush = monotonic
                if self.previous is not None:
                    self.summary['distance_xz'] += math.hypot(position[0]-self.previous[0], position[2]-self.previous[2])
                self.previous = tuple(position)
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
                    if self.summary['state'] not in ('finished', 'stopped', 'error'):
                        self.summary['state'] = 'interrupted'
                    self.summary['ended_at'] = self.summary['ended_at'] or self.wall_clock().isoformat(timespec='milliseconds')
                    if self.summary['started_at']:
                        self.summary['duration_seconds'] = max(0., (
                            datetime.fromisoformat(self.summary['ended_at'])-
                            datetime.fromisoformat(self.summary['started_at'])).total_seconds())
                    self._json('summary.json', self.summary)
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

    def read_for_playback(self):
        position = getattr(self.reader, 'read_for_playback', self.reader.read)()
        self.history.sample(position, self.monotonic())
        return position

    def __getattr__(self, name):
        return getattr(self.reader, name)
