"""Real loopback boundary for action metadata and runtime assets (no host SDK)."""
import http.client
import json
from pathlib import Path
import sys
import unittest

sys.dont_write_bytecode = True
ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'sakura_boardgame'))
from board_server import BoardServer


class Service:
    _game = None

    def __init__(self):
        self.calls = []

    def handle_action(self, action, arguments, *, metadata=None):
        self.calls.append((action, arguments, metadata))
        if action == 'fail':
            return {'ok': False, 'code': 'ACTION_FAILED', 'httpStatus': 500}
        if metadata is not None:
            return {'ok': False, 'code': 'STALE_VERSION', 'httpStatus': 409,
                    'requestId': metadata.get('requestId'), 'version': 9, 'gameSeq': 2}
        return {'ok': True}


class HttpProtocolTests(unittest.TestCase):
    def setUp(self):
        self.service = Service()
        self.server = BoardServer(self.service, ROOT / 'sakura_boardgame')
        self.service._server = self.server
        self.server.start()
        self.addCleanup(self.server.stop)

    def request(self, method, path, payload=None):
        conn = http.client.HTTPConnection('127.0.0.1', self.server._port, timeout=4)
        self.addCleanup(conn.close)
        body = json.dumps(payload).encode() if payload is not None else None
        conn.request(method, path, body=body, headers={'Content-Type': 'application/json'})
        response = conn.getresponse()
        return response.status, response.read(), dict(response.getheaders())

    def test_metadata_stays_separate_and_conflict_is_json_409(self):
        meta = {'requestId': 'test-request', 'expectedVersion': 8, 'gameSeq': 2}
        status, body, headers = self.request('POST', '/api/action',
                                             {'action': 'roll', 'arguments': {'player': 'user'}, 'metadata': meta})
        self.assertEqual(status, 409)
        self.assertIn('application/json', headers['Content-Type'])
        self.assertEqual(json.loads(body)['code'], 'STALE_VERSION')
        self.assertEqual(self.service.calls, [('roll', {'player': 'user'}, meta)])

    def test_legacy_call_remains_supported(self):
        status, body, _ = self.request('POST', '/api/action', {'action': 'roll', 'arguments': {}})
        self.assertEqual((status, json.loads(body)), (200, {'ok': True}))
        self.assertEqual(self.service.calls, [('roll', {}, None)])

    def test_transaction_failure_preserves_http_500(self):
        status, body, _ = self.request('POST', '/api/action', {'action': 'fail', 'arguments': {}})
        self.assertEqual(status, 500)
        self.assertEqual(json.loads(body)['code'], 'ACTION_FAILED')

    def test_runtime_config_and_asset_whitelist(self):
        status, body, headers = self.request('GET', '/board-config.js')
        self.assertEqual(status, 200)
        self.assertIn('javascript', headers['Content-Type'])
        prefix = b'globalThis.BoardRuntimeConfig = '
        self.assertTrue(body.startswith(prefix))
        config = json.loads(body[len(prefix):].strip().rstrip(b';'))
        self.assertEqual(config.get('blackTrainTheme'), {'baseUrl': '/theme'})
        status, asset, _ = self.request('GET', '/theme/icons_unified/gold_ticket.png')
        self.assertEqual(status, 200)
        self.assertTrue(asset.startswith(b'\x89PNG'))
        for path in ['/theme/../plugin.py', '/theme/manifest.json', '/theme/%2e%2e/engine.py',
                     '/theme/icons_unified/../tile_black_gold.png']:
            self.assertEqual(self.request('GET', path)[0], 404, path)


if __name__ == '__main__':
    unittest.main(verbosity=2)
