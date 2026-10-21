"""Injectable wall-clock adapter; safe to import without Windows or a display."""
import time


class PreciseClock:
    @staticmethod
    def monotonic():
        return time.perf_counter()

    @staticmethod
    def sleep(seconds):
        time.sleep(seconds)
