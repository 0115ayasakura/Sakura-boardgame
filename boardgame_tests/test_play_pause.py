"""Pause/resume against the real service, disposable saves and fake host only."""
from __future__ import annotations

import copy
import json
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import patch

sys.dont_write_bytecode = True
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'sakura_boardgame'))
sys.path.insert(0, str(Path(__file__).resolve().parent))
import engine
import plugin
from test_action_ledger import Config, Logger, fixture
from test_agent_turns import Context, HostChat


class PlayPauseTests(unittest.TestCase):
    def setUp(self):
        tmp = tempfile.TemporaryDirectory(prefix='sakura-pause-', dir=Path(__file__).parent)
        self.addCleanup(tmp.cleanup)
        self.root = Path(tmp.name)
        self.chat = HostChat()
        self.config = Config(decision_driver='agent', auto_mode='both', gomoku_level=1)
        self.s = self.reload()
        self.chat.service = self.s
        self.s.start({'game': 'monopoly', 'first': 'sakura'}, skip_open=True)
        self.s._game = fixture(actor='sakura')
        self.s._opponent['id'] = 'character-1'
        self.work = []
        self.s._agent_turns.launch = self.work.append

    def reload(self):
        return plugin.BoardgameService(self.config, lambda name: self.root / name,
                                       Logger(), context=Context(self.chat))

    def meta(self, name):
        return {'requestId': name, 'gameSeq': self.s._game_seq,
                'expectedVersion': self.s._version}

    def pause(self):
        result = self.s.handle_action('pause_play', {})
        self.assertTrue(result['ok'], result)
        self.assertTrue(result['playPaused'])
        return result

    def fingerprint(self):
        s = self.s
        return copy.deepcopy((s._game.__dict__, s._rng.getstate(), s._version,
                              s._game_seq, s._play_paused, s._agent_turns.checkpoint(),
                              s._last_event, list(s._log), list(s._snapshots), s._request_cache))

    def test_pause_revokes_queued_ticket_and_resume_is_inert_and_durable(self):
        self.assertTrue(self.s.handle_action('act_sakura', {})['ok'])
        old = self.s._agent_turns.ticket['token']
        game, seq, version = copy.deepcopy(self.s._game.__dict__), self.s._game_seq, self.s._version
        with patch.object(self.s, '_notify_sakura') as notify:
            self.pause()
            self.assertEqual(self.s._agent_turns.public()['status'], 'cancelled')
            self.work.pop()()  # Already scheduled worker must not submit.
            self.assertEqual(self.chat.requests, [])
            loaded = self.reload()
            self.assertTrue(loaded.state_payload()['playPaused'])
            self.assertEqual(loaded._game_seq, seq)
            result = self.s.handle_action('resume_play', {})
            self.assertTrue(result['ok'], result)
            self.assertFalse(result['playPaused'])
            self.assertEqual(result['autoMode'], 'both')
            notify.assert_not_called()
        self.assertEqual(self.s._game.__dict__, game)
        self.assertEqual(self.s._game_seq, seq)
        self.assertEqual(self.s._version, version + 2)
        self.assertFalse(self.reload().state_payload()['playPaused'])
        self.assertEqual(self.work, [])
        with self.assertRaises(engine.GameError):
            self.s._agent_turns.commit({'token': old, 'action': 'roll', 'arguments': {}})
        self.assertTrue(self.s.handle_action('act_sakura', {})['ok'])
        self.assertNotEqual(self.s._agent_turns.ticket['token'], old)

    def test_thinking_commit_and_all_gameplay_entrypoints_are_frozen(self):
        self.s.handle_action('act_sakura', {})
        self.work.pop()()
        token = self.s._agent_turns.ticket['token']
        self.pause()
        before = self.fingerprint()
        for action in ('roll', 'decide', 'place', 'act_sakura', 'auto', 'undo',
                       'tower_step', 'tower_finish', 'agent_fallback', 'black_train_start'):
            result = self.s.handle_action(action, {})
            self.assertFalse(result['ok'], action)
            self.assertIn('暂停', result['error'])
            self.assertEqual(self.fingerprint(), before)
        for method, args in ((self.s.roll, {}), (self.s.decide, {}), (self.s.place, {}),
                             (self.s.auto, {}), (self.s.undo, {}), (self.s.tower_action, {})):
            with self.assertRaisesRegex(engine.GameError, '暂停'):
                method(args)
            self.assertEqual(self.fingerprint(), before)
        with self.assertRaises(engine.GameError):
            self.s._agent_turns.commit({'token': token, 'action': 'roll', 'arguments': {}})
        self.assertEqual(self.fingerprint(), before)
        self.assertTrue(self.s.state({})['playPaused'])
        self.assertFalse(self.s.proactive_tick())

    def test_pending_fx_history_and_rng_survive_pause_resume(self):
        game = self.s._game
        game.pending = {'type': 'land', 'player': 'sakura', 'cell': 1, 'options': ['buy', 'skip']}
        self.s._last_event = {'kind': 'move', 'dice': 2, 'path': [0, 1]}
        game._last_landing = {'seq': 8, 'result': 'presentation still pending'}
        before = copy.deepcopy((game.__dict__, self.s._last_event, list(self.s._log),
                                list(self.s._snapshots), self.s._rng.getstate()))
        self.pause()
        self.assertTrue(self.s.handle_action('resume_play', {})['ok'])
        self.assertEqual((game.__dict__, self.s._last_event, list(self.s._log),
                          list(self.s._snapshots), self.s._rng.getstate()), before)

    def test_save_failure_rolls_back_pause_resume_and_ticket_and_rng(self):
        self.s.handle_action('act_sakura', {})
        for action in ('pause_play', 'resume_play'):
            if action == 'resume_play':
                self.pause()
            for operation in ('fsync', 'replace'):
                before = self.fingerprint()
                saved = (self.root / plugin.STATE_FILE).read_bytes()
                with patch.object(plugin.os, operation, side_effect=OSError('injected')):
                    result = self.s.handle_action(action, {}, metadata=self.meta(action + operation))
                self.assertFalse(result['ok'], result)
                self.assertEqual(self.fingerprint(), before)
                self.assertEqual((self.root / plugin.STATE_FILE).read_bytes(), saved)

    def test_metadata_late_requests_idempotence_and_old_save(self):
        late = self.meta('late-roll')
        meta = self.meta('pause')
        result = self.s.handle_action('pause_play', {}, metadata=meta)
        self.assertTrue(result['ok'], result)
        version = self.s._version
        self.assertEqual(self.s.handle_action('pause_play', {}, metadata=meta), {**result, 'duplicate': True})
        self.pause()
        self.assertEqual(self.s._version, version)
        self.assertEqual(self.s.handle_action('roll', {}, metadata=late)['code'], 'STALE_VERSION')
        self.s.handle_action('resume_play', {})
        saved = json.loads((self.root / plugin.STATE_FILE).read_text(encoding='utf8'))
        saved['_service'].pop('playPaused', None)
        (self.root / plugin.STATE_FILE).write_text(json.dumps(saved), encoding='utf8')
        self.assertFalse(self.reload().state_payload()['playPaused'])

    def test_algorithm_driver_gomoku_and_monopoly_cannot_advance_while_paused(self):
        self.config.update({'decision_driver': 'algorithm'})
        for kind in ('monopoly', 'gomoku'):
            self.s.start({'game': kind, 'first': 'sakura'}, skip_open=True)
            self.pause()
            before = self.fingerprint()
            for action in ('act_sakura', 'auto', 'roll', 'place'):
                self.assertFalse(self.s.handle_action(action, {})['ok'])
                self.assertEqual(self.fingerprint(), before)
            with self.assertRaisesRegex(engine.GameError, '暂停'):
                self.s.sakura_action()
            self.assertEqual(self.fingerprint(), before)
            self.s.handle_action('resume_play', {})
            self.assertEqual(self.config.get()['auto_mode'], 'both')
            self.assertEqual(self.s._game.__dict__, before[0])

    def test_failed_ticket_fallback_is_blocked_until_fresh_intention_after_resume(self):
        self.s.handle_action('act_sakura', {})
        self.work.pop()()
        ticket = self.s._agent_turns.ticket
        self.s._agent_turns._fail(ticket['token'], 'injected')
        identifier = ticket['id']
        self.pause()
        before = copy.deepcopy(self.s._game.__dict__)
        self.assertFalse(self.s.handle_action('agent_fallback', {'id': identifier})['ok'])
        self.s.handle_action('resume_play', {})
        self.assertFalse(self.s.handle_action('agent_fallback', {'id': identifier})['ok'])
        self.assertEqual(self.s._game.__dict__, before)
        self.assertTrue(self.s.handle_action('act_sakura', {})['ok'])
        self.assertNotEqual(self.s._agent_turns.ticket['id'], identifier)

    def test_explicit_new_game_and_end_are_allowed_and_failed_start_preserves_pause(self):
        self.pause()
        self.assertFalse(self.s.handle_action('start', {'game': 'invalid'})['ok'])
        self.assertTrue(self.s.state_payload()['playPaused'])
        self.assertTrue(self.s.handle_action('start', {'game': 'gomoku'})['ok'])
        self.assertFalse(self.s.state_payload()['playPaused'])
        self.pause()
        self.s.end({})
        self.assertIsNone(self.s._game)
        self.assertFalse((self.root / plugin.STATE_FILE).exists())


if __name__ == '__main__':
    unittest.main()
