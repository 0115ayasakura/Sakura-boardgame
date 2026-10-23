"""Local listener concurrency, idle lifetime and HTTP trust-boundary regressions."""
import http.server
import io
from pathlib import Path
import sys
import tempfile
import threading
import time
import unittest
from concurrent.futures import ThreadPoolExecutor
from email.message import Message
from types import SimpleNamespace
from unittest.mock import patch

sys.dont_write_bytecode = True
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'sakura_boardgame'))
import board_server
import plugin
from test_action_ledger import Config, Logger


class ServerLifecycleTests(unittest.TestCase):
    def setUp(self):
        tmp = tempfile.TemporaryDirectory(prefix='sakura-server-')
        self.addCleanup(tmp.cleanup)
        self.service = plugin.BoardgameService(Config(), lambda n: Path(tmp.name) / n, Logger(),
                                              Path(plugin.__file__).parent)
        self.server = self.service._server
        self.addCleanup(self.server.stop)

    def test_legacy_plugin_exports_point_at_standalone_module(self):
        self.assertIs(plugin.BoardServer, board_server.BoardServer)
        self.assertIs(plugin._make_handler, board_server.make_handler)
        self.assertIsInstance(self.server, board_server.BoardServer)

    def test_parallel_demand_creates_one_listener(self):
        constructor = http.server.ThreadingHTTPServer
        created = []
        barrier = threading.Barrier(2)

        def slow_constructor(*args, **kwargs):
            time.sleep(.03)  # Enlarge the actual check -> bind -> publish window.
            server = constructor(*args, **kwargs)
            created.append(server)
            return server

        def demand():
            barrier.wait(timeout=3)
            return self.service.board_url

        try:
            with patch.object(board_server.http.server, 'ThreadingHTTPServer', slow_constructor):
                with ThreadPoolExecutor(max_workers=2) as pool:
                    urls = list(pool.map(lambda _: demand(), range(2)))
            self.assertEqual(len(created), 1, 'concurrent demand leaked a second listener')
            self.assertEqual(len(set(urls)), 1)
        finally:
            self.server.stop()
            # Also release orphan listeners when running this test against the old bug.
            for server in created:
                server.shutdown()
                server.server_close()

    def test_restart_reuses_previous_port(self):
        first = self.server.start()
        self.server.stop()
        self.assertEqual(self.server.start(), first)

    def test_active_game_survives_idle_check(self):
        self.service.start({'game': 'gomoku'}, skip_open=True)
        self.server.start()
        self.server._last_request = time.time() - self.server.IDLE_SHUTDOWN_SECONDS - 5
        self.server._idle_check()
        self.assertIsNotNone(self.server._httpd)

    def test_no_game_idle_listener_is_released(self):
        self.server.start()
        self.server._last_request = time.time() - self.server.IDLE_SHUTDOWN_SECONDS - 5
        self.server._idle_check()
        self.assertIsNone(self.server._httpd)
        self.assertEqual(self.server.url, '')

    def test_stale_watchdog_cannot_stop_restarted_listener(self):
        self.server.start()
        old_version = self.server._watchdog_version
        self.server.stop()
        self.server.start()
        self.server._last_request = time.time() - self.server.IDLE_SHUTDOWN_SECONDS - 5
        self.server._idle_check(old_version)
        self.assertIsNotNone(self.server._httpd)

    def test_busy_previous_port_falls_back_to_fresh_listener(self):
        self.server.start()
        port = self.server._port
        self.server.stop()
        constructor = http.server.ThreadingHTTPServer
        attempted = []

        def bind(address, handler):
            attempted.append(address)
            if address[1] == port:
                raise OSError('port occupied')
            return constructor(address, handler)

        with patch.object(board_server.http.server, 'ThreadingHTTPServer', bind):
            self.server.start()
        self.assertEqual(attempted, [('127.0.0.1', port), ('127.0.0.1', 0)])
        self.assertTrue(self.server._thread.is_alive())

    def test_stop_releases_real_socket_and_listener_thread(self):
        import socket
        import urllib.request
        url = self.server.start()
        thread = self.server._thread
        with urllib.request.urlopen(url + 'state', timeout=3) as response:
            self.assertEqual(response.status, 200)
        port = self.server._port
        self.server.stop()
        self.assertFalse(thread.is_alive())
        with self.assertRaises(OSError):
            socket.create_connection(('127.0.0.1', port), timeout=.5)

    def test_live_http_rejects_bad_requests_and_accepts_valid_action(self):
        import http.client
        self.server.start()
        port = self.server._port
        cases = [
            ('GET', '/state', None, {'Host': f'attacker.test:{port}'}, 403),
            ('POST', '/api/action', b'{}', {'Origin': 'https://evil.example'}, 403),
            ('POST', '/api/action', b'', {'Content-Length': '-1'}, 400),
            ('POST', '/api/action', b'[]', {}, 400),
            ('POST', '/api/action', b'{}', {'Content-Type': 'text/plain'}, 415),
            ('POST', '/api/action', b'{"action":"start","arguments":{"game":"gomoku"}}', {}, 200),
        ]
        for method, path, body, extra, expected in cases:
            conn = http.client.HTTPConnection('127.0.0.1', port, timeout=2)
            try:
                conn.request(method, path, body=body, headers={'Content-Type': 'application/json', **extra})
                response = conn.getresponse()
                response.read()
                self.assertEqual(response.status, expected, (method, extra))
            finally:
                conn.close()
        self.assertEqual(self.service._game.kind, 'gomoku')

    def test_requests_share_one_watchdog(self):
        with patch.object(board_server.threading, 'Timer') as timer:
            self.server.start()
            for _ in range(20):
                self.server._touch()
            self.assertEqual(timer.call_count, 1, 'every poll/asset request creates a timer thread')


class HandlerBoundaryTests(unittest.TestCase):
    def request(self, method='POST', body=b'{}', **headers):
        calls = []
        service = SimpleNamespace(_server=SimpleNamespace(_touch=lambda: None),
                                  handle_action=lambda *args: calls.append(args) or {'ok': True})
        handler_type = board_server.make_handler(service, Path(plugin.__file__).parent)
        handler = object.__new__(handler_type)
        handler.path = '/api/action'
        handler.server = SimpleNamespace(server_address=('127.0.0.1', 12345))
        handler.headers = Message()
        for key, value in {'Host': '127.0.0.1:12345', 'Content-Type': 'application/json',
                           'Content-Length': str(len(body)), **headers}.items():
            handler.headers[key] = value
        reads = []
        raw = io.BytesIO(body)
        handler.rfile = SimpleNamespace(read=lambda n: reads.append(n) or raw.read(n))
        response = []
        handler._send = lambda *args: response.append(args)
        getattr(handler, 'do_' + method)()
        return response[0][0], calls, reads

    def test_negative_length_rejected_before_read(self):
        status, calls, reads = self.request(**{'Content-Length': '-1'})
        self.assertEqual(status, 400)
        self.assertEqual(reads, [])
        self.assertEqual(calls, [])

    def test_non_object_json_rejected(self):
        self.assertEqual(self.request(body=b'[]')[0], 400)

    def test_cross_origin_and_rebound_host_rejected(self):
        for headers in ({'Origin': 'https://evil.example'}, {'Host': 'attacker-rebind.test:12345'},
                        {'Origin': 'null'}, {'Origin': 'http://127.0.0.1:12346'}):
            status, calls, _ = self.request(**headers)
            self.assertEqual(status, 403, headers)
            self.assertEqual(calls, [])
        self.assertEqual(self.request(method='GET', Host='attacker-rebind.test:12345')[0], 403)

    def test_json_content_type_required(self):
        self.assertEqual(self.request(**{'Content-Type': 'text/plain'})[0], 415)

    def test_same_origin_and_local_tools_remain_supported(self):
        self.assertEqual(self.request()[0], 200)
        self.assertEqual(self.request(Origin='http://127.0.0.1:12345')[0], 200)
        self.assertEqual(self.request(Host='localhost:12345', Origin='http://localhost:12345')[0], 200)


if __name__ == '__main__':
    unittest.main(verbosity=2)
