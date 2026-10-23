"""board_server 的无宿主独立性：单独 import 不拉 plugin/engine/Sakura SDK。

另外用假 service 跑一遍真实 loopback HTTP，证明 Handler 的闭包与常量都随模块完整迁移，
不依赖 plugin 模块里的任何名字。
"""
import ast
import http.client
import json
from pathlib import Path
import subprocess
import sys
import unittest

PLUGIN_DIR = Path(__file__).resolve().parents[1] / 'sakura_boardgame'
sys.dont_write_bytecode = True
sys.path.insert(0, str(PLUGIN_DIR))
import board_server  # noqa: E402


class _FakeServer:
    def __init__(self):
        self.touches = 0

    def _touch(self):
        self.touches += 1


class _FakeService:
    def __init__(self):
        self._game = None
        self._server = _FakeServer()
        self.busy = None
        self.actions = []

    def note_page_busy(self, busy):
        self.busy = busy

    def state_payload(self):
        return {'ok': True, 'state': 'fake'}

    def portrait_file(self):
        return None

    def handle_action(self, action, arguments):
        self.actions.append((action, arguments))
        return {'ok': True, 'action': action}


class StandaloneImportTests(unittest.TestCase):
    def test_import_does_not_load_plugin_engine_or_host_sdk(self):
        script = (
            'import sys\n'
            'sys.path.insert(0, sys.argv[1])\n'
            'import board_server\n'
            "leaked = sorted(n for n in sys.modules if n in {'plugin', 'engine', 'sakura'}\n"
            "                or n.startswith('sakura.'))\n"
            'assert not leaked, leaked\n'
            'assert board_server.BoardServer.IDLE_SHUTDOWN_SECONDS == 30 * 60\n'
            'assert board_server.make_handler is board_server._make_handler\n'
            "print('standalone-ok')\n"
        )
        result = subprocess.run([sys.executable, '-B', '-c', script, str(PLUGIN_DIR)],
                                cwd=str(PLUGIN_DIR.parent), capture_output=True, text=True, timeout=30)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn('standalone-ok', result.stdout)

    def test_module_imports_only_stdlib(self):
        tree = ast.parse((PLUGIN_DIR / 'board_server.py').read_text(encoding='utf-8'))
        imported = set()
        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                imported.update(alias.name.split('.')[0] for alias in node.names)
            elif isinstance(node, ast.ImportFrom) and not node.level:
                imported.add((node.module or '').split('.')[0])
        self.assertFalse(imported & {'plugin', 'engine', 'sakura'}, imported)
        self.assertLessEqual(imported, {'__future__', 'http', 'json', 're', 'threading', 'time',
                                        'pathlib', 'typing', 'uuid', 'urllib', 'theme_resources'}, imported)


class StandaloneServerTests(unittest.TestCase):
    def setUp(self):
        self.service = _FakeService()
        self.server = board_server.BoardServer(self.service, PLUGIN_DIR)
        self.addCleanup(self.server.stop)

    def request(self, method, path, body=None, headers=None):
        conn = http.client.HTTPConnection('127.0.0.1', self.server._port, timeout=3)
        try:
            conn.request(method, path, body=body, headers=headers or {})
            response = conn.getresponse()
            return response.status, response.read(), dict(response.getheaders())
        finally:
            conn.close()

    def test_board_and_state_served_without_plugin(self):
        self.server.start()
        status, body, headers = self.request('GET', '/')
        self.assertEqual(status, 200)
        self.assertIn('text/html', headers['Content-Type'])
        self.assertTrue(body)
        status, body, _ = self.request('GET', '/state?busy=1')
        self.assertEqual(status, 200)
        self.assertEqual(json.loads(body), {'ok': True, 'state': 'fake'})
        self.assertTrue(self.service.busy)
        self.assertEqual(self.service._server.touches, 2)

    def test_instance_header_stable_on_restart_and_unique_on_replacement(self):
        self.service._server = self.server
        self.server.start()
        token = self.server.instance_id
        status, _, headers = self.request('GET', '/state')
        self.assertEqual(status, 200)
        self.assertEqual(headers['X-Board-Instance'], token)
        self.server.stop()
        self.server.start()
        _, _, headers = self.request('GET', '/state')
        self.assertEqual(headers['X-Board-Instance'], token)
        other = board_server.BoardServer(self.service, PLUGIN_DIR)
        self.assertNotEqual(other.instance_id, token)

    def test_action_roundtrip_and_traversal_guard(self):
        self.server.start()
        payload = json.dumps({'action': 'ping', 'arguments': {'x': 1}}).encode()
        status, body, _ = self.request('POST', '/api/action', payload,
                                       {'Content-Type': 'application/json'})
        self.assertEqual(status, 200)
        self.assertEqual(json.loads(body), {'ok': True, 'action': 'ping'})
        self.assertEqual(self.service.actions, [('ping', {'x': 1})])
        status, _, _ = self.request('GET', '/art/../plugin.yaml')
        self.assertEqual(status, 404)
        status, _, _ = self.request('GET', '/art/poi/Evil.png')
        self.assertEqual(status, 404)

    def test_host_and_origin_boundary_without_plugin(self):
        self.server.start()
        status, _, _ = self.request('GET', '/state', headers={'Host': 'attacker.test'})
        self.assertEqual(status, 403)
        status, _, _ = self.request('POST', '/api/action', b'{}',
                                    {'Content-Type': 'application/json',
                                     'Origin': 'https://evil.example'})
        self.assertEqual(status, 403)
        self.assertEqual(self.service.actions, [])


if __name__ == '__main__':
    unittest.main(verbosity=2)
