"""Bounded local playback status history; logging can never stop movement cleanup."""
from datetime import datetime
import json
import logging
from logging.handlers import RotatingFileHandler
from pathlib import Path
import threading


class PlaybackDiagnostics:
    def __init__(self, path):
        self.handler = None
        self.lock = threading.Lock()
        try:
            path = Path(path)
            path.parent.mkdir(parents=True, exist_ok=True)
            self.handler = RotatingFileHandler(path, maxBytes=256 * 1024, backupCount=2, encoding='utf-8')
            self.handler.setFormatter(logging.Formatter('%(message)s'))
        except OSError:
            pass

    def write(self, status):
        if self.handler is None:
            return
        entry = {'at': datetime.now().astimezone().isoformat(timespec='milliseconds')}
        for key in ('state', 'message', 'completed_at', 'waypoint', 'waypoint_count', 'cycle', 'loop_count',
                    'position', 'target_height', 'recovery_phase', 'recovery_result',
                    'recovery_attempt', 'recovery_failures'):
            if key in status:
                entry[key] = status[key]
        try:
            message = json.dumps(entry, ensure_ascii=False, allow_nan=False)
            record = logging.LogRecord('point-playback', logging.INFO, '', 0, message, (), None)
            with self.lock:
                if self.handler is not None:
                    self.handler.handle(record)
        except (OSError, TypeError, ValueError):
            pass

    def close(self):
        with self.lock:
            if self.handler is not None:
                self.handler.close()
                self.handler = None
