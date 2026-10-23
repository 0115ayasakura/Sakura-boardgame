"""Service transaction/replay failure injection. All writes use disposable directories."""
from __future__ import annotations

import copy
import json
from pathlib import Path
import sys
import tempfile
import unittest
from concurrent.futures import ThreadPoolExecutor
from unittest.mock import patch

sys.dont_write_bytecode = True
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'sakura_boardgame'))
sys.path.insert(0, str(Path(__file__).resolve().parent))
import action_protocol
import engine
import plugin
from test_action_ledger import Config, Logger


class ActionTransactionTests(unittest.TestCase):
    def setUp(self):
        tmp = tempfile.TemporaryDirectory(prefix='sakura-transaction-')
        self.addCleanup(tmp.cleanup)
        self.root = Path(tmp.name)
        self.config = Config(gomoku_level=1)
        self.service = self.reload()
        self.service.start({'game': 'monopoly', 'first': 'user'}, skip_open=True)

    def reload(self):
        return plugin.BoardgameService(self.config, lambda name: self.root / name, Logger())

    def metadata(self, request='request-1'):
        return {'requestId': request, 'gameSeq': self.service._game_seq,
                'expectedVersion': self.service._version}

    def fingerprint(self):
        s = self.service
        return copy.deepcopy({'game': s._game.__dict__, 'rng': s._rng.getstate(),
                              'version': s._version, 'game_seq': s._game_seq,
                              'log': list(s._log), 'lines': s._lines_written,
                              'event': s._last_event, 'snapshots': list(s._snapshots),
                              'cache': s._request_cache})

    def save_bytes(self):
        return (self.root / plugin.STATE_FILE).read_bytes()

    def test_save_failures_restore_rng_events_logs_undo_and_file(self):
        s = self.service
        original_game = s._game
        for operation in ('fsync', 'replace'):
            before, saved = self.fingerprint(), self.save_bytes()
            with patch.object(plugin.os, operation, side_effect=OSError('injected')):
                with self.assertRaises(OSError):
                    s.roll({'player': 'user'})
            self.assertIs(s._game, original_game)
            self.assertEqual(self.fingerprint(), before)
            self.assertEqual(self.save_bytes(), saved)
            self.assertEqual(list(self.root.glob('*.tmp')), [])
        # Retry consumes the same dice RNG stream as an untouched engine.
        reference = copy.deepcopy(s._game)
        expected = reference.roll('user')
        actual = s.roll({'player': 'user'})
        self.assertEqual(actual['dice'], expected['dice'])
        self.assertEqual(s._game.to_dict(), {**reference.to_dict(),
                                           'action_records': s._game.action_records,
                                           'action_seq': s._game.action_seq})

    def test_engine_error_after_rng_and_mutations_rolls_everything_back(self):
        s = self.service
        before, saved = self.fingerprint(), self.save_bytes()
        original = engine.MonopolyGame.roll

        def failed(game, *args, **kwargs):
            result = original(game, *args, **kwargs)
            s._rng.random()
            game._last_item_draw = {'seq': 998}
            s._touch(['must disappear'], 'failure')
            raise RuntimeError('after mutation')

        with patch.object(engine.MonopolyGame, 'roll', failed):
            with self.assertRaises(RuntimeError):
                s.roll({'player': 'user'})
        self.assertEqual(self.fingerprint(), before)
        self.assertEqual(self.save_bytes(), saved)

    def test_ledger_failure_rolls_engine_back(self):
        before, saved = self.fingerprint(), self.save_bytes()
        with patch.object(self.service, '_record_action', side_effect=RuntimeError('ledger')):
            with self.assertRaises(RuntimeError):
                self.service.roll({'player': 'user'})
        self.assertEqual(self.fingerprint(), before)
        self.assertEqual(self.save_bytes(), saved)

    def test_serialization_failure_after_move_restores_memory_and_disk(self):
        s = self.service
        before, saved = self.fingerprint(), self.save_bytes()
        with patch.object(plugin.json, 'dumps', side_effect=TypeError('serialization')):
            with self.assertRaises(TypeError):
                s.roll({'player': 'user'})
        self.assertEqual(self.fingerprint(), before)
        self.assertEqual(self.save_bytes(), saved)

    def test_caught_nested_game_error_marks_entire_auto_rollback_only(self):
        s = self.service
        before, saved = self.fingerprint(), self.save_bytes()
        original = engine.MonopolyGame.roll

        def failed(game, *args, **kwargs):
            original(game, *args, **kwargs)
            s._rng.random()
            raise engine.GameError('injected after RNG')

        with patch.object(engine.MonopolyGame, 'roll', failed):
            # auto historically catches GameError; the outer transaction must
            # still reject its partial execution, rather than commit a half move.
            with self.assertRaises(engine.GameError):
                s.auto({'rounds': 1})
        self.assertEqual(self.fingerprint(), before)
        self.assertEqual(self.save_bytes(), saved)

    def test_handle_failure_rolls_back_and_retry_is_not_cached(self):
        s = self.service
        meta = self.metadata()
        before, saved = self.fingerprint(), self.save_bytes()
        with patch.object(plugin.os, 'replace', side_effect=OSError('save')):
            failed = s.handle_action('roll', {}, metadata=meta)
        self.assertFalse(failed['ok'])
        self.assertEqual(failed['httpStatus'], 500)
        self.assertEqual(failed['requestId'], meta['requestId'])
        self.assertEqual(self.fingerprint(), before)
        self.assertEqual(self.save_bytes(), saved)
        accepted = s.handle_action('roll', {}, metadata=meta)
        self.assertTrue(accepted['ok'])
        self.assertFalse(accepted['duplicate'])

    def test_repeat_original_receipt_is_owned_and_persistent(self):
        s = self.service
        meta = self.metadata()
        first = s.handle_action('roll', {}, metadata=meta)
        before = self.fingerprint()
        duplicate = s.handle_action('roll', {}, metadata=meta)
        self.assertEqual(duplicate, {**first, 'duplicate': True})
        self.assertEqual(self.fingerprint(), before)
        duplicate['status'] = 'caller mutation'
        self.assertNotEqual(s.handle_action('roll', {}, metadata=meta)['status'], 'caller mutation')
        reloaded = self.reload()
        self.assertEqual(reloaded._version, s._version)
        self.assertEqual(reloaded._game_seq, s._game_seq)
        self.assertEqual(reloaded.handle_action('roll', {}, metadata=meta), {**first, 'duplicate': True})

    def test_duplicate_requests_racing_execute_exactly_once(self):
        meta = self.metadata()
        s = self.service
        with ThreadPoolExecutor(max_workers=2) as pool:
            futures = [pool.submit(s.handle_action, 'roll', {}, metadata=meta) for _ in range(2)]
            results = [future.result(timeout=5) for future in futures]
        self.assertTrue(all(result['ok'] for result in results))
        self.assertEqual(sorted(result['duplicate'] for result in results), [False, True])
        self.assertEqual(s._game._rolls, 1)
        self.assertEqual(s._game.action_seq, 1)

    def test_metadata_rejection_is_pure_and_stale_new_game_is_rejected(self):
        s = self.service
        good = self.metadata()
        invalid = [({}, 'INVALID_REQUEST_METADATA'),
                   ({**good, 'gameSeq': True}, 'INVALID_REQUEST_METADATA'),
                   ({**good, 'expectedVersion': -1}, 'INVALID_REQUEST_METADATA'),
                   ({**good, 'requestId': ' '}, 'INVALID_REQUEST_METADATA'),
                   ({**good, 'expectedVersion': good['expectedVersion'] - 1}, 'STALE_VERSION'),
                   ({**good, 'gameSeq': good['gameSeq'] - 1}, 'STALE_GAME')]
        for meta, code in invalid:
            before, saved = self.fingerprint(), self.save_bytes()
            result = s.handle_action('roll', {}, metadata=meta)
            self.assertFalse(result['ok'])
            self.assertEqual(result['code'], code)
            self.assertEqual(self.fingerprint(), before)
            self.assertEqual(self.save_bytes(), saved)
        accepted = s.handle_action('roll', {}, metadata=good)
        self.assertTrue(accepted['ok'])
        before = self.fingerprint()
        reused = s.handle_action('decide', {'action': 'skip'}, metadata=good)
        self.assertEqual(reused['code'], 'REQUEST_ID_REUSED')
        self.assertEqual(self.fingerprint(), before)
        stale = self.metadata('old-board')
        s.start({'game': 'gomoku', 'first': 'user'}, skip_open=True)
        self.assertEqual(s.handle_action('place', {'row': 8, 'col': 8}, metadata=stale)['code'], 'STALE_GAME')

    def test_nested_auto_commits_once_and_notifies_only_after_save(self):
        s = self.service
        saved = []
        notify_calls = []
        real_save = s._save_now

        def save():
            real_save()
            saved.append(True)

        # Observe the actual host delivery helper rather than the enqueue wrapper.
        class Mobile:
            def begin(self, *args):
                self_outer.assertTrue(saved)
                notify_calls.append(args)
        class Character:
            def current(self):
                return {'id': 'test'}
        class Context:
            plugin_id = 'test'
            def get(self, key):
                return Mobile() if key == 'sakura.host.mobile' else Character()
        self_outer = self
        s._context = Context()
        self.config.update({'auto_narrate': True})
        with patch.object(s, '_save_now', side_effect=save):
            s.auto({'rounds': 1})
        self.assertEqual(len(saved), 1)
        self.assertEqual(len(notify_calls), 1)
        self.assertEqual(json.loads(self.save_bytes())['_service']['version'], s._version)

    def test_nested_save_failure_never_publishes_or_partially_commits(self):
        s = self.service
        before, saved = self.fingerprint(), self.save_bytes()
        with patch.object(s, '_save_now', side_effect=OSError('late save')):
            with patch.object(s, '_notify_sakura', wraps=s._notify_sakura) as notify:
                with self.assertRaises(OSError):
                    s.auto({'rounds': 1})
        self.assertEqual(self.fingerprint(), before)
        self.assertEqual(self.save_bytes(), saved)
        # It may enqueue once; it may not call a host after failure.
        self.assertEqual(s._last_talk, 0)

    def test_post_commit_notification_failure_keeps_saved_move_and_receipt(self):
        s = self.service
        # Failed notification is after commit, not an action failure.
        def operation():
            s._game.cash['user'] += 1
            s._touch(['committed'], None)
            s._save()
            s._action_transaction['outbox'].append(('notify', False, ''))
            return {'ok': True}
        with patch.object(s, '_notify_sakura', side_effect=RuntimeError('host')):
            result = action_protocol.run(s, operation)
        self.assertTrue(result['ok'])
        self.assertEqual(self.reload()._game.cash['user'], s._game.cash['user'])

    def test_end_delete_failure_and_undo_save_failure_roll_back(self):
        s = self.service
        s.roll({'player': 'user'})
        before, saved = self.fingerprint(), self.save_bytes()
        with patch.object(s, '_save_now', side_effect=OSError('undo')):
            with self.assertRaises(OSError):
                s.undo({})
        self.assertEqual(self.fingerprint(), before)
        self.assertEqual(self.save_bytes(), saved)
        with patch.object(s, '_delete_save_now', side_effect=OSError('delete')):
            with self.assertRaises(OSError):
                s.end({})
        self.assertEqual(self.fingerprint(), before)
        self.assertEqual(self.save_bytes(), saved)

    def test_settings_commit_does_not_depend_on_game_save(self):
        s = self.service
        meta = self.metadata('setting')
        before_version, saved = s._version, self.save_bytes()
        with patch.object(s, '_save_now', side_effect=OSError('unrelated game storage')) as save:
            result = s.handle_action('set_difficulty', {'level': 3}, metadata=meta)
            duplicate = s.handle_action('set_difficulty', {'level': 3}, metadata=meta)
        save.assert_not_called()
        self.assertTrue(result['ok'])
        self.assertTrue(duplicate['duplicate'])
        self.assertEqual(self.config.get()['gomoku_level'], 3)
        self.assertEqual(s._version, before_version + 1)
        self.assertEqual(self.save_bytes(), saved)
        # Settings receipts are process-local until the next ordinary game save.
        self.assertNotIn('setting', self.reload()._request_cache)
        s.roll({'player': 'user'})
        self.assertIn('setting', self.reload()._request_cache)

    def test_cache_bounded_and_old_save_defaults(self):
        s = self.service
        with patch.object(action_protocol, 'CACHE_LIMIT', 3):
            for n in range(6):
                result = s.handle_action('set_difficulty', {'level': 1}, metadata=self.metadata(str(n)))
                self.assertTrue(result['ok'])
            self.assertEqual(len(s._request_cache), 3)
        data = json.loads(self.save_bytes())
        data.pop('_service')
        (self.root / plugin.STATE_FILE).write_text(json.dumps(data), encoding='utf8')
        old = self.reload()
        self.assertEqual(old._version, 1)
        self.assertEqual(old._game_seq, 0)
        self.assertEqual(len(old._request_cache), 0)
        self.assertEqual(old.state_payload()['protocolVersion'], 1)


if __name__ == '__main__':
    unittest.main(verbosity=2)
