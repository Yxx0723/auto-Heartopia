"""Cancellable offline planning worker. No Tk or route writes on this thread."""
from copy import deepcopy
import queue
import threading
from utils.core.route_optimization import optimize_route


class RouteOptimizationJob:
    def __init__(self):
        self.thread = None
        self.stop = threading.Event()
        self.results = queue.Queue()
        self.pending = False

    @property
    def active(self):
        return self.pending

    def start(self, data, position, scene, loops, context):
        if self.active:
            raise ValueError('路线正在优化，请稍候。')
        self.stop.clear()
        self.pending = True
        def check():
            if self.stop.is_set():
                raise ValueError('路线优化已取消。')
        def run():
            try:
                result = optimize_route(deepcopy(data), position, deepcopy(scene), loops, check=check)
                self.results.put({'data': result, 'context': context})
            except (ValueError, OSError) as exc:
                self.results.put({'error': str(exc), 'context': context})
        self.thread = threading.Thread(target=run, name='route-optimization', daemon=True)
        self.thread.start()

    def take_latest(self):
        try:
            result = self.results.get_nowait()
            self.pending = False
            return result
        except queue.Empty:
            return None

    def close(self):
        self.stop.set()
