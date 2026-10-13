"""Connection lifecycle for read-only coordinate displays; no server or GUI side effects."""
import copy
from datetime import datetime
import secrets
import threading
import time

from utils.core import thresholds as policy
from utils.drivers.coordinate_memory import ReadOnlyPosition
from utils.drivers.coordinate_process import find_game_processes


class CoordinateMonitor:
    def __init__(self, profile, discover=find_game_processes, factory=ReadOnlyPosition,
                 clock=time.monotonic, wall_clock=None):
        self.profile, self.discover, self.factory = profile, discover, factory
        self.clock = clock
        self.wall_clock = wall_clock or (lambda: datetime.now().astimezone().isoformat(timespec='milliseconds'))
        self.lock = threading.Lock()
        self.reader = None
        self.closed = False
        self.pid = None
        self.session = 0
        self.sequence = 0
        self.last_sample = float('-inf')
        self.next_attempt = float('-inf')
        self.state = self._empty('connecting', '正在连接心动小镇…')

    def _empty(self, state, message):
        return {'state': state, 'message': message, 'read_only': True, 'pid': self.pid,
                'session': self.session, 'sequence': self.sequence, 'position': None,
                'valid_paths': 0, 'configured_paths': 0, 'paths': [], 'sample_at': None,
                'sample_age': None}

    def _close_reader(self):
        if self.reader is not None:
            self.reader.close()
            self.reader = None
        self.pid = None

    def status(self):
        with self.lock:
            if self.closed:
                return copy.deepcopy(self.state)
            now = self.clock()
            if self.reader is None and now >= self.next_attempt:
                self.next_attempt = now + policy.COORD_MONITOR_RETRY_SECONDS
                try:
                    pids = self.discover()
                    if not pids:
                        self.state = self._empty('waiting', '等待游戏启动。进入角色后会自动连接。')
                    elif len(pids) > 1:
                        self.state = self._empty('error', '发现多个心动小镇进程，请只保留一个游戏实例。')
                    else:
                        self.reader = self.factory(pids[0], self.profile)
                        self.pid = pids[0]
                        self.session = secrets.token_hex(8)
                        self.last_sample = float('-inf')
                except (OSError, ValueError) as exc:
                    self._close_reader()
                    self.state = self._empty('error', str(exc))
            if self.reader is not None and now - self.last_sample >= policy.COORD_MONITOR_REFRESH_SECONDS:
                try:
                    sample = self.reader.snapshot()
                    self.sequence += 1
                    self.last_sample = self.clock()
                    self.state = {**self._empty('connected', '实时读取中'), **sample,
                                  'sequence': self.sequence, 'sample_at': self.wall_clock()}
                except (OSError, ValueError) as exc:
                    self._close_reader()
                    self.next_attempt = self.clock() + policy.COORD_MONITOR_RETRY_SECONDS
                    self.state = self._empty('disconnected', str(exc))
            result = copy.deepcopy(self.state)
            if result['state'] == 'connected':
                result['sample_age'] = max(0., self.clock() - self.last_sample)
            return result

    def reconnect(self):
        with self.lock:
            if self.closed:
                return
            self._close_reader()
            self.next_attempt = float('-inf')
            self.state = self._empty('connecting', '正在重新连接…')

    def close(self):
        with self.lock:
            self.closed = True
            self._close_reader()
            self.state = self._empty('stopped', '监视器已关闭')

