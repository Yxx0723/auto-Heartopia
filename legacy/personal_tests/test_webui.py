import importlib.util
import io
import json
from pathlib import Path
import tempfile
import threading
import unittest
from unittest.mock import Mock, patch
import urllib.request

from utils.core.personal_route import save_route

ROOT = Path(__file__).resolve().parents[1]


def load_server():
    spec = importlib.util.spec_from_file_location('personal_webui_test', ROOT / 'webui/server.py')
    server = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(server)
    return server


class WebTests(unittest.TestCase):
    def setUp(self):
        self.server = load_server()
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.server.BASE_DIR = self.temp.name
        self.server.CONFIG_PATH = str(Path(self.temp.name) / 'config.json')
        Path(self.server.CONFIG_PATH).write_text(json.dumps({'map_version': 'default'}), encoding='utf-8')
        (Path(self.temp.name) / 'map/default').mkdir(parents=True)

    def test_empty_library_does_not_spawn_or_require_game(self):
        with patch.object(self.server.subprocess, 'Popen') as spawn:
            result = self.server.run_start('normal')
        self.assertFalse(result['ok'])
        self.assertIn('路线库为空', result['error'])
        spawn.assert_not_called()

    def test_bad_recording_metadata_does_not_spawn(self):
        with patch.object(self.server.subprocess, 'Popen') as spawn:
            result = self.server.run_start('record', route_name='  ')
        self.assertFalse(result['ok'])
        spawn.assert_not_called()

    def test_selected_route_checks_path_and_existence(self):
        save_route(Path(self.temp.name) / 'map/default', '测试', '我', '1', [{'w': 1}])
        for route in ('../anything', '1-1_999'):
            with patch.object(self.server.subprocess, 'Popen') as spawn:
                result = self.server.run_start('map', route)
            self.assertFalse(result['ok'])
            spawn.assert_not_called()

    def test_recording_metadata_passes_as_separate_arguments(self):
        fake = Mock(pid=123)
        fake.poll.return_value = None
        with patch.object(self.server.subprocess, 'Popen', return_value=fake) as spawn, patch.object(self.server.threading, 'Thread'):
            result = self.server.run_start('record', route_name='我的 空格路线', route_author='本人', route_planet='3')
        self.assertTrue(result['ok'])
        args = spawn.call_args.args[0]
        self.assertEqual(args[-6:], ['--route-name', '我的 空格路线', '--route-author', '本人', '--route-planet', '3'])
        self.assertNotIn('shell', spawn.call_args.kwargs)

    def test_portable_engine_is_selected(self):
        portable = Path(self.temp.name) / 'MyRail.exe'
        portable.touch()
        self.server.FHOE_EXE = str(portable)
        fake = Mock(pid=124)
        fake.poll.return_value = None
        with patch.object(self.server.subprocess, 'Popen', return_value=fake) as spawn, patch.object(self.server.threading, 'Thread'):
            self.server.run_start('record')
        self.assertEqual(spawn.call_args.args[0][:2], [str(portable), '--record'])

    def test_live_http_empty_maps_and_recording_page(self):
        httpd = self.server.Server(('127.0.0.1', 0), self.server.Handler)
        worker = threading.Thread(target=httpd.serve_forever, daemon=True)
        worker.start()
        try:
            base = f'http://127.0.0.1:{httpd.server_port}'
            with urllib.request.urlopen(base + '/api/maps', timeout=5) as response:
                data = json.load(response)
            self.assertEqual(data['tree'], {'default': {}})
            self.assertEqual(data['version_names']['default'], '我的路线')
            with urllib.request.urlopen(base + '/', timeout=5) as response:
                page = response.read().decode('utf-8')
            self.assertIn('MyRail · 我的小镇路线', page)
            self.assertIn('record-name', page)
            req = urllib.request.Request(base + '/api/run', data=b'{"mode":"normal"}', headers={'Content-Type': 'application/json'})
            with urllib.request.urlopen(req, timeout=5) as response:
                result = json.load(response)
            self.assertFalse(result['ok'])
            self.assertIn('路线库为空', result['error'])
        finally:
            httpd.shutdown()
            httpd.server_close()
            worker.join(timeout=5)

    def test_new_recorded_route_refreshes_nested_library(self):
        with patch.object(self.server.os.path, 'getmtime', return_value=123):
            self.assertEqual(self.server.build_map_tree(), {'default': {}})
            save_route(Path(self.temp.name) / 'map/default', '新路线', '我', '1', [{'w': 1}])
            tree = self.server.build_map_tree()
        self.assertEqual(len(tree['default']['1']), 1)

    def test_playback_http_route_id_reaches_portable_command(self):
        save_route(Path(self.temp.name) / 'map/default', '测试', '我', '1', [{'w': 1}])
        portable = Path(self.temp.name) / 'MyRail.exe'
        portable.touch()
        self.server.FHOE_EXE = str(portable)
        httpd = self.server.Server(('127.0.0.1', 0), self.server.Handler)
        worker = threading.Thread(target=httpd.serve_forever, daemon=True)
        worker.start()
        try:
            for field in ('map_id', 'map'):
                with self.subTest(field=field):
                    self.server._RUN['proc'] = None
                    fake = Mock(pid=126, stdout=io.BytesIO(b''))
                    fake.poll.return_value = None
                    body = {'mode': 'map', field: '1-1_1', 'map_version': 'default'}
                    request = urllib.request.Request(
                        f'http://127.0.0.1:{httpd.server_port}/api/run',
                        data=json.dumps(body).encode('utf-8'), headers={'Content-Type': 'application/json'})
                    with patch.object(self.server.subprocess, 'Popen', return_value=fake) as spawn:
                        with urllib.request.urlopen(request, timeout=5) as response:
                            result = json.load(response)
                    self.assertTrue(result['ok'], result)
                    self.assertEqual(spawn.call_args.args[0],
                                     [str(portable), '--map', '1-1_1', '--map-version', 'default'])
        finally:
            httpd.shutdown()
            httpd.server_close()
            worker.join(timeout=5)

    def test_recording_status_is_bound_to_current_process(self):
        folder = Path(self.temp.name) / 'logs'
        folder.mkdir()
        (folder / 'recorder-state.json').write_text(json.dumps({'state': 'ready', 'pid': 125}), encoding='utf-8')
        self.assertIsNone(self.server.recorder_status(126))
        self.assertEqual(self.server.recorder_status(125)['state'], 'ready')

    def test_coordinate_route_can_be_edited_through_http_and_invalid_edit_is_rejected(self):
        from utils.core.coordinate_route import PositionTrack, save_coordinates
        import urllib.error
        track = PositionTrack()
        track.resume(0)
        track.sample([0, 0, 0], 0)
        track.sample([0, 0, 2], 1)
        track.pause(1)
        file = save_coordinates(Path(self.temp.name) / 'map/default', '坐标', '我', '心动小镇', track, [1280, 720])
        data = json.loads(file.read_text(encoding='utf-8'))
        httpd = self.server.Server(('127.0.0.1', 0), self.server.Handler)
        worker = threading.Thread(target=httpd.serve_forever, daemon=True)
        worker.start()
        try:
            def post(route):
                body = {'version': 'default', 'file': file.name, 'data': route}
                request = urllib.request.Request(f'http://127.0.0.1:{httpd.server_port}/api/map',
                                                 data=json.dumps(body).encode(), headers={'Content-Type': 'application/json'})
                return urllib.request.urlopen(request, timeout=5)
            data['name'] = '改名的坐标路线'
            with post(data) as response:
                self.assertTrue(json.load(response)['saved'])
            saved = file.read_bytes()
            data['samples'][1]['position'] = [1000, 0, 0]
            with self.assertRaises(urllib.error.HTTPError) as error:
                post(data)
            self.assertEqual(error.exception.code, 400)
            self.assertEqual(file.read_bytes(), saved)
        finally:
            httpd.shutdown()
            httpd.server_close()
            worker.join(timeout=5)


if __name__ == '__main__':
    unittest.main()
