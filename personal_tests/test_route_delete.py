import importlib.util
import json
from pathlib import Path
import tempfile
import threading
import unittest
from unittest.mock import Mock
import urllib.request


class DeleteRouteTests(unittest.TestCase):
    def setUp(self):
        root = Path(__file__).resolve().parents[1]
        spec = importlib.util.spec_from_file_location('delete_route_server_test', root / 'webui/server.py')
        self.server = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(self.server)
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.base = Path(self.temp.name)
        self.server.BASE_DIR = str(self.base)
        self.folder = self.base / 'map/default'
        self.folder.mkdir(parents=True)
        self.route = self.folder / 'map_1-1_1.json'
        self.route.write_text('{"name":"我的路线"}', encoding='utf-8')

    def test_delete_removes_only_selected_route_and_keeps_recoverable_copy(self):
        other = self.folder / 'map_1-1_2.json'
        other.write_bytes(b'other')
        original = self.route.read_bytes()
        result = self.server.route_delete('default', self.route.name)
        self.assertFalse(self.route.exists())
        self.assertEqual(other.read_bytes(), b'other')
        trash = self.base / 'route_trash' / result['trash_id']
        self.assertEqual((trash / self.route.name).read_bytes(), original)
        self.assertEqual(len(self.server.build_map_tree()['default']['1']), 1)

    def test_undo_restores_route_and_reference_screenshot(self):
        images = self.base / 'my_starts'
        images.mkdir()
        image = images / 'map_1-1_1.png'
        image.write_bytes(b'original screenshot')
        result = self.server.route_delete('default', self.route.name)
        image.unlink()
        restored = self.server.route_restore(result['trash_id'])
        self.assertEqual(restored['file'], self.route.name)
        self.assertEqual(json.loads(self.route.read_text(encoding='utf-8'))['name'], '我的路线')
        self.assertEqual(image.read_bytes(), b'original screenshot')

    def test_undo_never_overwrites_a_new_recording_with_the_same_id(self):
        result = self.server.route_delete('default', self.route.name)
        self.route.write_bytes(b'new recording')
        restored = self.server.route_restore(result['trash_id'])
        self.assertNotEqual(restored['file'], self.route.name)
        self.assertEqual(self.route.read_bytes(), b'new recording')
        self.assertEqual(json.loads((self.folder / restored['file']).read_text(encoding='utf-8'))['name'], '我的路线')

    def test_running_task_blocks_delete_and_undo(self):
        deleted = self.server.route_delete('default', self.route.name)
        self.route.write_bytes(b'new recording')
        proc = Mock()
        proc.poll.return_value = None
        self.server._RUN['proc'] = proc
        with self.assertRaisesRegex(ValueError, '运行'):
            self.server.route_delete('default', self.route.name)
        with self.assertRaisesRegex(ValueError, '运行'):
            self.server.route_restore(deleted['trash_id'])
        self.assertEqual(self.route.read_bytes(), b'new recording')

    def test_invalid_paths_and_repeat_undo_are_rejected(self):
        for version, file in [('..', self.route.name), ('default', '../config.json'), ('default', 'map_1-1_1.json/extra')]:
            with self.assertRaises(ValueError):
                self.server.route_delete(version, file)
        for token in ('../config.json', '', 'a' * 31):
            with self.assertRaises(ValueError):
                self.server.route_restore(token)
        result = self.server.route_delete('default', self.route.name)
        self.server.route_restore(result['trash_id'])
        with self.assertRaisesRegex(ValueError, '已恢复'):
            self.server.route_restore(result['trash_id'])

    def test_http_delete_and_restore(self):
        httpd = self.server.Server(('127.0.0.1', 0), self.server.Handler)
        worker = threading.Thread(target=httpd.serve_forever, daemon=True)
        worker.start()
        try:
            def post(endpoint, body):
                request = urllib.request.Request(f'http://127.0.0.1:{httpd.server_port}' + endpoint,
                                                 data=json.dumps(body).encode(), headers={'Content-Type': 'application/json'})
                with urllib.request.urlopen(request, timeout=5) as response:
                    return json.load(response)
            deleted = post('/api/map/delete', {'version': 'default', 'file': self.route.name})
            self.assertTrue(deleted['ok'])
            self.assertFalse(self.route.exists())
            restored = post('/api/map/restore', {'trash_id': deleted['trash_id']})
            self.assertTrue(restored['ok'])
            self.assertTrue(self.route.exists())
        finally:
            httpd.shutdown()
            httpd.server_close()
            worker.join(timeout=5)


if __name__ == '__main__':
    unittest.main()
