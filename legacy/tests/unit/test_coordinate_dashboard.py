"""Monitor tests use injected readers; never attach to the game."""
import json
import threading
from urllib.error import HTTPError
from urllib.request import Request, urlopen

import pytest

from utils.ui.coordinate_dashboard import CoordinateMonitor, DashboardServer, make_handler


class Reader:
    def __init__(self):
        self.closed = False
        self.failed = False
        self.calls = 0

    def snapshot(self):
        self.calls += 1
        if self.failed:
            raise ValueError('坐标不可读')
        return {'position': (1., 2., 3.), 'valid_paths': 3, 'configured_paths': 3,
                'address': 0x30000, 'module_base': 0x10000, 'paths': []}

    def close(self):
        self.closed = True


def fixture_monitor(pids=None):
    now = [10.]
    reader = Reader()
    processes = pids if pids is not None else [123]
    monitor = CoordinateMonitor('unused.json', discover=lambda: list(processes),
                                factory=lambda pid, profile: reader, clock=lambda: now[0],
                                wall_clock=lambda: '2026-10-11T21:00:00+08:00')
    return monitor, reader, now, processes


def test_background_monitor_connects_and_caches_one_sample_for_concurrent_clients():
    monitor, reader, now, _ = fixture_monitor()
    status = monitor.status()
    assert status['state'] == 'connected'
    assert status['pid'] == 123
    assert status['position'] == (1., 2., 3.)
    assert status['valid_paths'] == 3
    assert status['read_only'] is True
    status['position'] = (999, 999, 999)
    assert monitor.status()['position'] == (1., 2., 3.)
    assert reader.calls == 1
    now[0] += .2
    assert monitor.status()['sequence'] == 2


def test_disconnect_clears_numbers_and_reconnect_starts_a_new_session():
    monitor, reader, now, _ = fixture_monitor()
    first = monitor.status()
    reader.failed = True
    now[0] += .2
    broken = monitor.status()
    assert broken['state'] == 'disconnected'
    assert broken['position'] is None
    assert broken['valid_paths'] == 0
    assert broken['paths'] == []
    assert reader.closed
    reader.failed = False
    monitor.reconnect()
    assert monitor.status()['session'] != first['session']


@pytest.mark.parametrize('pids,state', [([], 'waiting'), ([1, 2], 'error')])
def test_no_game_or_multiple_games_never_picks_an_arbitrary_process(pids, state):
    monitor, reader, now, processes = fixture_monitor(pids)
    assert monitor.status()['state'] == state
    assert monitor.status()['position'] is None
    assert reader.calls == 0
    processes[:] = [123]
    now[0] += 3
    assert monitor.status()['state'] == 'connected'


def test_failed_open_and_shutdown_close_handles_without_leaking_stale_values():
    monitor, reader, _, _ = fixture_monitor()
    def failure(*args):
        raise OSError('需要管理员权限')
    monitor.factory = failure
    assert monitor.status()['state'] == 'error'
    assert monitor.status()['position'] is None
    monitor.factory = lambda *args: reader
    monitor.reconnect()
    monitor.status()
    monitor.close()
    assert reader.closed
    calls = reader.calls
    assert monitor.status()['state'] == 'stopped'
    assert reader.calls == calls


def test_http_dashboard_serves_real_state_and_blocks_cross_origin_commands(tmp_path):
    (tmp_path / 'coordinates.html').write_text('<h1>坐标监视</h1>', encoding='utf-8')
    monitor, reader, _, _ = fixture_monitor()
    server = DashboardServer(('127.0.0.1', 0), make_handler(monitor, tmp_path))
    worker = threading.Thread(target=server.serve_forever, daemon=True)
    worker.start()
    base = f'http://127.0.0.1:{server.server_port}'
    try:
        with urlopen(base + '/api/status', timeout=2) as reply:
            state = json.load(reply)
        assert state['position'] == [1, 2, 3]
        with urlopen(base, timeout=2) as reply:
            assert '坐标监视' in reply.read().decode('utf-8')
        request = Request(base + '/api/reconnect', data=b'{}',
                          headers={'Origin': 'https://unrelated.example'})
        with pytest.raises(HTTPError) as result:
            urlopen(request, timeout=2)
        assert result.value.code == 403
        assert not reader.closed
        with urlopen(base + '/api/ping', timeout=2) as reply:
            token = json.load(reply)['token']
        request = Request(base + '/api/reconnect', data=b'{}',
                          headers={'Origin': base, 'X-Monitor-Token': token})
        with urlopen(request, timeout=2) as reply:
            assert reply.status == 200
        assert reader.closed
        with pytest.raises(HTTPError) as result:
            urlopen(Request(base + '/api/status', headers={'Host': 'attacker.example'}), timeout=2)
        assert result.value.code == 403
    finally:
        server.shutdown()
        server.server_close()
        worker.join(2)
        monitor.close()
