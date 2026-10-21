"""Shared internal control signals; no dependency on the runner."""


class _Paused(Exception):
    pass

class _Stopped(Exception):
    pass

class _SkipPoint(Exception):
    pass

class _CalibrationRetry(Exception):
    pass

class _RecoveryLimit(Exception):
    def __init__(self, reason):
        super().__init__(reason)
        self.reason = reason
