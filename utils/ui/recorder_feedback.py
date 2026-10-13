"""Visible and machine-readable status for recording and playback."""
import json
import os
from pathlib import Path
import threading
import time

from utils.drivers.record_overlay import StatusOverlay


class Feedback:
    def __init__(self, base, overlay=True):
        self.path = Path(base) / 'logs/recorder-state.json'
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.lock = threading.RLock()
        self.state = 'starting'
        self.message = '正在启动输入监听…'
        self.last_write = 0
        self.overlay = StatusOverlay() if overlay else None

    def update(self, state, message, timeline=None, window=None, **extra):
        with self.lock:
            self.state, self.message = state, message
            print(message, flush=True)
            self._write(timeline, **extra)
            if self.overlay:
                rect = window.get_rect() if window and window.hwnd else None
                self.overlay.update(message, rect)

    def _write(self, timeline=None, **extra):
        data = {'state': self.state, 'message': self.message, 'pid': os.getpid(),
                'events': len(timeline.events) if timeline else 0,
                'duration': round(timeline.at(time.perf_counter()), 1) if timeline else 0, **extra}
        if timeline is not None and hasattr(timeline, 'samples'):
            data['engine'] = 'coordinate_route'
            data['point_count'] = len(timeline.samples)
            data['position'] = timeline.samples[-1]['position'] if timeline.samples else None
        tmp = self.path.with_suffix('.tmp')
        tmp.write_text(json.dumps(data, ensure_ascii=False), encoding='utf-8')
        os.replace(tmp, self.path)
        self.last_write = time.monotonic()

    def refresh(self, timeline, window=None):
        with self.lock:
            if time.monotonic() - self.last_write >= 0.5:
                self._write(timeline)

    def close(self):
        if self.overlay:
            self.overlay.close()
