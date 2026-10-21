"""Local coordinate dashboard, independent of the recorder and its hotkeys."""
import argparse
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import json
from pathlib import Path
import secrets
import sys
import threading
from urllib.error import URLError
from urllib.request import urlopen
import webbrowser

from utils.core import thresholds as policy
from utils.drivers.coordinate_process import show_monitor_error
from utils.ui.coordinate_monitor import CoordinateMonitor


class DashboardServer(ThreadingHTTPServer):
    daemon_threads = True


def make_handler(monitor, resources):
    token = secrets.token_hex(24)
    resources = Path(resources)

    class Handler(BaseHTTPRequestHandler):
        def log_message(self, *args):
            pass

        def reply(self, code, value, kind='application/json; charset=utf-8'):
            data = value if isinstance(value, bytes) else json.dumps(value, ensure_ascii=False).encode('utf-8')
            self.send_response(code)
            self.send_header('Content-Type', kind)
            self.send_header('Content-Length', str(len(data)))
            self.send_header('Cache-Control', 'no-store')
            self.send_header('X-Content-Type-Options', 'nosniff')
            self.send_header('Content-Security-Policy', "default-src 'self'; script-src 'self' 'unsafe-inline'; style-src 'self' 'unsafe-inline'; frame-ancestors 'none'")
            self.end_headers()
            try:
                self.wfile.write(data)
            except (BrokenPipeError, ConnectionResetError, ConnectionAbortedError):
                pass

        def local(self):
            port = self.server.server_port
            return self.headers.get('Host') in (f'127.0.0.1:{port}', f'localhost:{port}')

        def do_GET(self):
            if not self.local():
                self.reply(403, {'error': '仅限本机访问'})
                return
            path = self.path.split('?', 1)[0]
            if path == '/':
                self.reply(200, (resources / 'coordinates.html').read_bytes(), 'text/html; charset=utf-8')
            elif path == '/api/ping':
                self.reply(200, {'app': 'myrail-coordinate-monitor', 'version': '1.0.0', 'token': token,
                                 'refresh_ms': int(policy.COORD_MONITOR_REFRESH_SECONDS * 1000),
                                 'stale_ms': int(policy.COORD_MONITOR_STALE_SECONDS * 1000),
                                 'trail_limit': policy.COORD_MONITOR_TRAIL_POINTS,
                                 'idle_radius': policy.COORD_IDLE_RADIUS,
                                 'max_speed': policy.COORD_MAX_SPEED})
            elif path == '/api/status':
                self.reply(200, monitor.status())
            elif path == '/coordinates.CT':
                self.reply(200, (resources / 'coordinates.CT').read_bytes(), 'application/octet-stream')
            elif path == '/favicon.ico':
                self.reply(204, b'')
            else:
                self.reply(404, {'error': '页面不存在'})

        def do_POST(self):
            origins = (f'http://127.0.0.1:{self.server.server_port}', f'http://localhost:{self.server.server_port}')
            if not self.local() or self.headers.get('Origin') not in origins \
                    or self.headers.get('X-Monitor-Token') != token:
                self.reply(403, {'error': '仅接受本机页面操作'})
                return
            if self.path == '/api/reconnect':
                monitor.reconnect()
                self.reply(200, {'ok': True})
            elif self.path == '/api/shutdown':
                self.reply(200, {'ok': True})
                threading.Thread(target=self.server.shutdown, daemon=True).start()
            else:
                self.reply(404, {'error': '操作不存在'})

    return Handler


def main():
    parser = argparse.ArgumentParser(description='心动小镇实时坐标监视器')
    parser.add_argument('--port', type=int, default=8668)
    parser.add_argument('--no-browser', action='store_true')
    parser.add_argument('--check-output', type=Path)
    args = parser.parse_args()
    resources = Path(sys._MEIPASS) / 'monitor' if getattr(sys, 'frozen', False) \
        else Path(__file__).resolve().parents[2] / 'webui' / 'coordinate-monitor'
    monitor = CoordinateMonitor(resources / 'profile.json')
    if args.check_output:
        try:
            args.check_output.write_text(json.dumps(monitor.status(), ensure_ascii=False, indent=2), encoding='utf-8')
        finally:
            monitor.close()
        return
    url = f'http://127.0.0.1:{args.port}/'
    try:
        server = DashboardServer(('127.0.0.1', args.port), make_handler(monitor, resources))
    except OSError as exc:
        try:
            with urlopen(url + 'api/ping', timeout=1) as response:
                existing = json.load(response)
            if existing.get('app') == 'myrail-coordinate-monitor':
                if not args.no_browser:
                    webbrowser.open(url)
                return
        except (OSError, URLError, ValueError):
            pass
        show_monitor_error(f'无法打开坐标页面：{exc}\n请检查端口 {args.port} 是否被其他程序占用。')
        return
    try:
        if not args.no_browser:
            webbrowser.open(url)
        server.serve_forever(poll_interval=.2)
    finally:
        server.server_close()
        monitor.close()
