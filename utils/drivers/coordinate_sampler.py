"""Poll the position provider on the recorder thread, independently of input."""
import time

from utils.core.thresholds import COORD_SAMPLE_SECONDS
from utils.drivers.coordinate_memory import ReadOnlyPosition


class PositionSampler:
    def __init__(self, track, window, profile_file, clock=time.perf_counter,
                 factory=ReadOnlyPosition.from_window):
        self.track, self.window, self.profile_file = track, window, profile_file
        self.clock, self.factory = clock, factory
        self.reader = None
        self.last = float('-inf')

    def poll(self):
        now = self.clock()
        if not self.track.active or not self.window.foreground():
            return
        if now - self.last < COORD_SAMPLE_SECONDS and not self.track.resuming:
            return
        if self.reader is None:
            self.reader = self.factory(self.window, self.profile_file)
        self.track.sample(self.reader.read(), now)
        self.last = now

    def close(self):
        if self.reader:
            self.reader.close()

