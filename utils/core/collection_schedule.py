"""One bounded supplementary pass; saved waypoint order is never mutated."""

# Engineering bounds; none is a measured character/terrain capability.
MEMORY_REJOIN_SECONDS = 1.
FAILED_ENTRY_SECONDS = 120.
SCENE_WAIT_SECONDS = 30.
LOOP_WINDOW_SECONDS = 12.
LOOP_RETURN_RADIUS = .75
LOOP_EXIT_RADIUS = 1.25  # Hysteresis: boundary jitter is not a departure.
LOOP_RETURN_HEIGHT = .35
LOOP_RETURN_COUNT = 4
LOOP_MIN_TRAVEL = 6.
MEMORY_ENTRY_HEIGHT = .35


class SupplementQueue:
    def __init__(self):
        self.pending = []
        self.retrying = False

    def failed(self, index):
        if not self.retrying and index not in self.pending:
            self.pending.append(index)

    def advance(self, following, total):
        if not self.retrying and following < total:
            return following
        self.retrying = True
        return self.pending.pop(0) if self.pending else None
