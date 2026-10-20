"""One repair-box use followed by a real-time effect window and live verification."""
import math


REPAIR_THRESHOLD_PERCENT = 20.
REPAIR_EFFECT_SECONDS = 17.


class ToolRepairCycle:
    def __init__(self):
        self.started_at = None
        self.before = None
        self.maximum = None

    @property
    def active(self):
        return self.started_at is not None

    @staticmethod
    def usable(tool):
        return (isinstance(tool, dict) and tool.get('state') == 'ready'
                and type(tool.get('current')) is int and type(tool.get('maximum')) is int
                and 0 <= tool['current'] <= tool['maximum'] <= 10000000 and tool['maximum'] > 0
                and type(tool.get('equipped')) is bool)

    def remaining(self, now):
        return max(0., self.started_at+REPAIR_EFFECT_SECONDS-now) if self.active else 0.

    def observe(self, tool, now):
        if not isinstance(now, (int, float)) or not math.isfinite(now):
            raise ValueError('维修计时无效')
        if self.active and self.remaining(now) > 0:
            # Effects continue while playback is paused or its window is in the
            # background. A subsequent observation must never send a second use.
            return 'waiting'
        if not self.usable(tool):
            return 'unavailable'
        if not self.active and tool['current']/tool['maximum']*100 >= REPAIR_THRESHOLD_PERCENT:
            return 'idle'
        if not tool['equipped']:
            return 'not_held'
        if self.active:
            if tool['maximum'] != self.maximum:
                return 'changed'
            if tool['current'] <= self.before or tool['current']/tool['maximum']*100 < REPAIR_THRESHOLD_PERCENT:
                return 'failed'
            self.started_at = self.before = self.maximum = None
            return 'completed'
        return 'needed' if tool['current']/tool['maximum']*100 < REPAIR_THRESHOLD_PERCENT else 'idle'

    def mark_used(self, tool, now):
        if self.observe(tool, now) != 'needed':
            raise ValueError('当前无需使用维修盒')
        # Record BEFORE invoking input: an interrupted/ambiguous key press must
        # not consume another box on F6 resume.
        self.started_at, self.before, self.maximum = now, tool['current'], tool['maximum']
