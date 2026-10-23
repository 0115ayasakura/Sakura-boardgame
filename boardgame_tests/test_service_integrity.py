"""Persistence, rejected-action undo integrity and direct tool concurrency regressions."""
import copy
import json
from pathlib import Path
import sys
import tempfile
import threading
import time
import unittest
from concurrent.futures import ThreadPoolExecutor
from unittest.mock import patch

sys.dont_write_bytecode = True
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'sakura_boardgame'))
import engine
import plugin
from test_action_ledger import Config, Logger


class ServiceIntegrityTests(unittest.TestCase):
    def setUp(self):
        tmp = tempfile.TemporaryDirectory(prefix='sakura-integrity-')
        self.addCleanup(tmp.cleanup)
        self.root = Path(tmp.name)
        self.config = Config(gomoku_level=1)
        self.service = self.reload()

    def reload(self):
        return plugin.BoardgameService(self.config, lambda name: self.root / name, Logger())

    def start(self, kind='gomoku', first='user'):
        self.service.start({'game': kind, 'first': first}, skip_open=True)
        return self.service._game

    def test_gomoku_moves_survive_service_reload(self):
        self.start()
        self.service.place({'player': 'user', 'row': 8, 'col': 8})
        self.service.place({'player': 'sakura', 'row': 8, 'col': 9})
        saved = json.loads((self.root / plugin.STATE_FILE).read_text(encoding='utf8'))
        self.assertEqual(saved['move_count'], 2)
        self.assertEqual(self.reload()._game.to_dict(), self.service._game.to_dict())

    def test_failed_save_keeps_last_valid_state(self):
        game = self.start()
        path = self.root / plugin.STATE_FILE
        previous = path.read_bytes()
        game.move_count = 7
        for operation in ('fsync', 'replace'):
            with self.subTest(operation=operation):
                with patch.object(plugin.os, operation, side_effect=OSError('simulated disk failure')):
                    with self.assertRaises(OSError):
                        self.service._save()
                self.assertEqual(path.read_bytes(), previous)
                self.assertEqual(list(self.root.glob('state.json.*.tmp')), [])
                self.assertEqual(self.reload()._game.move_count, 0)
        self.service._save()
        self.assertEqual(self.reload()._game.move_count, 7)
        self.assertEqual(list(self.root.glob('state.json.*.tmp')), [])

    def test_serialization_failure_does_not_touch_save(self):
        game = self.start()
        path = self.root / plugin.STATE_FILE
        previous = path.read_bytes()
        with patch.object(game, 'to_dict', return_value={'unsupported': object()}):
            with self.assertRaises(TypeError):
                self.service._save()
        self.assertEqual(path.read_bytes(), previous)
        self.assertEqual(list(self.root.glob('state.json.*.tmp')), [])

    def test_ai_move_is_saved_and_undoable(self):
        self.start(first='sakura')
        self.service.place({'player': 'sakura'})
        self.assertEqual(self.reload()._game.to_dict(), self.service._game.to_dict())
        self.service.undo({})
        self.assertEqual(self.service._game.move_count, 0)
        self.assertEqual(self.reload()._game.move_count, 0)

    def test_rejected_places_cannot_evict_real_undo(self):
        self.start()
        self.service.place({'player': 'user', 'row': 8, 'col': 8})
        self.service.place({'player': 'sakura', 'row': 8, 'col': 9})
        before = copy.deepcopy(list(self.service._snapshots))
        invalid = [
            {'row': 8, 'col': 8}, {'row': 'invalid', 'col': 1}, {'row': 99, 'col': 1},
            {}, {'player': 'sakura', 'row': 3, 'col': 3},
        ]
        for args in invalid * 3:
            with self.assertRaises((engine.GameError, ValueError)):
                self.service.place(args)
        self.assertEqual(list(self.service._snapshots), before)
        self.service.undo({})
        self.assertEqual(self.service._game.move_count, 1)
        self.assertEqual(sum(bool(c) for row in self.service._game.board for c in row), 1)

    def test_rejected_roll_cannot_create_undo(self):
        game = self.start('monopoly')
        game.winner = 'user'
        before = copy.deepcopy(list(self.service._snapshots))
        with self.assertRaises(engine.GameError):
            self.service.roll({'player': 'user'})
        self.assertEqual(list(self.service._snapshots), before)

    def test_direct_tool_places_are_serialized(self):
        game = self.start()
        barrier = threading.Barrier(2)
        has_win = game._has_win

        def slow_win(*args, **kwargs):
            # Enlarge the real check-turn -> update-turn window deterministically.
            time.sleep(.05)
            return has_win(*args, **kwargs)

        def place(col):
            barrier.wait(timeout=3)
            try:
                self.service.place({'player': 'user', 'row': 8, 'col': col})
                return True
            except engine.GameError:
                return False

        with patch.object(game, '_has_win', slow_win), ThreadPoolExecutor(max_workers=2) as pool:
            jobs = [pool.submit(place, col) for col in (8, 9)]
            result = [job.result(timeout=5) for job in jobs]
        self.assertEqual(sorted(result), [False, True])
        self.assertEqual(game.move_count, 1)
        self.assertEqual(game.turn, 'sakura')
        self.assertEqual(len(self.service._snapshots), 1)
        self.assertEqual(self.reload()._game.to_dict(), game.to_dict())


if __name__ == '__main__':
    unittest.main(verbosity=2)
