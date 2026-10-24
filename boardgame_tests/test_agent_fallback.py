"""Real-service fallback regression; fake host and workspace-local temporary saves only."""
from __future__ import annotations

import copy
from concurrent.futures import ThreadPoolExecutor
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


class AgentFallbackTests(unittest.TestCase):
    def setUp(self):
        tmp = tempfile.TemporaryDirectory(prefix='sakura-fallback-', dir=Path(__file__).parent)
        self.addCleanup(tmp.cleanup)
        self.root = Path(tmp.name)
        self.chat = HostChat()
        self.config = Config(decision_driver='agent', gomoku_level=1)
        self.s = plugin.BoardgameService(self.config, lambda name: self.root / name,
                                         Logger(), context=Context(self.chat))
        self.chat.service = self.s
        self.s.start({'game': 'monopoly', 'first': 'sakura'}, skip_open=True)
        self.s._game = fixture(actor='sakura')
        self.s._opponent['id'] = 'character-1'
        self.work = []
        self.s._agent_turns.launch = self.work.append
        self.now = 1000.0
        self.s._agent_turns.clock = lambda: self.now

    def meta(self, name='fallback'):
        return {'requestId': name, 'gameSeq': self.s._game_seq,
                'expectedVersion': self.s._version}

    def begin(self, status='expired'):
        response = self.s.handle_action('act_sakura', {})
        self.assertTrue(response['ok'], response)
        self.work.pop()()
        ticket = self.s._agent_turns.ticket
        self.assertEqual(ticket['status'], 'thinking')
        if status == 'expired':
            self.now += 121
            self.s._agent_turns.public()
        elif status == 'failed':
            self.s._agent_turns._fail(ticket['token'], 'injected host failure')
        return ticket['id'], ticket['token']

    def fallback(self, identifier, **kwargs):
        return self.s.handle_action('agent_fallback', {'id': identifier}, **kwargs)

    def assert_late_commit_rejected(self, token, action='roll', arguments=None):
        before = copy.deepcopy(self.s._game.__dict__)
        with self.assertRaises(engine.GameError):
            self.s._agent_turns.commit({'token': token, 'action': action,
                                         'arguments': arguments or {}})
        self.assertEqual(self.s._game.__dict__, before)

    def test_expired_roll_is_one_action_and_driver_and_notifications_unchanged(self):
        identifier, token = self.begin()
        before = self.s._game.action_seq
        with patch.object(self.s, '_notify_sakura') as notify:
            result = self.fallback(identifier)
        self.assertTrue(result['ok'], result)
        self.assertFalse(result['narrated'])
        self.assertEqual(result['agentDecision']['status'], 'cancelled')
        self.assertEqual(self.s._game.action_seq, before + 1)
        self.assertEqual(self.s._game.pending['type'], 'land')
        self.assertEqual(self.s.decision_driver(), 'agent')
        self.assertFalse(self.s._agent_applying)
        notify.assert_not_called()
        self.assertEqual(len(self.chat.requests), 1)
        self.assert_late_commit_rejected(token)
        with self.assertRaises(engine.GameError):
            self.s.decide({'player': 'sakura', 'action': 'buy'})
        # The next subdecision is a fresh Agent request, not permanent takeover.
        self.assertTrue(self.s.handle_action('act_sakura', {})['ok'])
        self.assertEqual(self.s._agent_turns.public()['status'], 'queued')

    def test_failed_monopoly_decision_calls_existing_policy(self):
        game = self.s._game
        game.pending = {'type': 'land', 'player': 'sakura', 'cell': 1,
                        'options': ['buy', 'skip']}
        identifier, token = self.begin('failed')
        with patch.object(self.s, '_pick_pending_action', wraps=self.s._pick_pending_action) as choose:
            expected = self.s._pick_pending_action(game)
            choose.reset_mock()
            result = self.fallback(identifier)
        self.assertTrue(result['ok'], result)
        choose.assert_called_once_with(game)
        self.assertEqual(game.action_records[-1]['context']['choice'], expected)
        self.assertEqual(game.action_records[-1]['source'], 'auto')
        self.assert_late_commit_rejected(token, 'decide', {'action': 'skip'})

    def test_gomoku_uses_real_configured_ai_and_rejects_late_place(self):
        self.s.start({'game': 'gomoku', 'first': 'sakura'}, skip_open=True)
        self.s._opponent['id'] = 'character-1'
        identifier, token = self.begin()
        game = self.s._game
        with patch.object(game, 'ai_move', wraps=game.ai_move) as ai:
            result = self.fallback(identifier)
        self.assertTrue(result['ok'], result)
        ai.assert_called_once()
        self.assertEqual(ai.call_args.args[:2], ('sakura', 1))
        self.assertEqual(game.move_count, 1)
        self.assertEqual(game.turn, 'user')
        self.assert_late_commit_rejected(token, 'place', {'row': 1, 'col': 1})
        self.assertFalse(self.fallback(identifier)['ok'])
        self.assertEqual(game.move_count, 1)

    def test_metadata_replay_and_distinct_duplicate_rejection(self):
        identifier, _ = self.begin()
        metadata = self.meta()
        first = self.fallback(identifier, metadata=metadata)
        frozen = copy.deepcopy(self.s._game.__dict__)
        second = self.fallback(identifier, metadata=metadata)
        self.assertEqual(second, {**first, 'duplicate': True})
        self.assertEqual(self.s._game.__dict__, frozen)
        self.assertFalse(self.fallback(identifier, metadata=self.meta('other'))['ok'])
        self.assertEqual(self.s._game.__dict__, frozen)

    def test_concurrent_double_click_executes_once(self):
        identifier, _ = self.begin()
        before = self.s._game.action_seq
        with ThreadPoolExecutor(max_workers=2) as pool:
            results = list(pool.map(lambda _: self.fallback(identifier), range(2)))
        self.assertEqual(sorted(result['ok'] for result in results), [False, True])
        self.assertEqual(self.s._game.action_seq, before + 1)

    def test_only_current_failed_or_expired_ticket_is_accepted(self):
        identifier, _ = self.begin('thinking')
        before = copy.deepcopy(self.s._game.__dict__)
        for bad in (None, '', 'old-ticket'):
            self.assertFalse(self.fallback(bad)['ok'])
        for status in ('thinking', 'queued', 'cancelled', 'committed', 'unknown'):
            self.s._agent_turns.ticket['status'] = status
            self.assertFalse(self.fallback(identifier)['ok'], status)
        self.assertEqual(self.s._game.__dict__, before)

    def test_stale_binding_user_turn_changed_legal_and_finished_game_rejected(self):
        identifier, _ = self.begin()
        initial_game = copy.deepcopy(self.s._game.__dict__)
        initial_ticket = copy.deepcopy(self.s._agent_turns.ticket)
        for mutate in (
            lambda: setattr(self.s, '_game_seq', self.s._game_seq + 1),
            lambda: setattr(self.s, '_version', self.s._version + 1),
            lambda: setattr(self.s._game, 'turn', 'user'),
            lambda: setattr(self.s._game, 'winner', 'sakura'),
            lambda: setattr(self.s._game, 'pending', {'type': 'land', 'player': 'user',
                                                    'cell': 1, 'options': ['skip']}),
            lambda: setattr(self.s._game, 'pending', {'type': 'land', 'player': 'sakura',
                                                    'cell': 1, 'options': ['skip']}),
            lambda: self.s._opponent.update(id='different-character'),
            lambda: self.config.update({'decision_driver': 'algorithm'}),
        ):
            with self.subTest(mutation=mutate):
                self.s._game.__dict__.clear()
                self.s._game.__dict__.update(copy.deepcopy(initial_game))
                self.s._agent_turns.ticket = copy.deepcopy(initial_ticket)
                self.s._game_seq = initial_ticket['gameSeq']
                self.s._version = initial_ticket['version']
                self.s._opponent['id'] = 'character-1'
                self.config.update({'decision_driver': 'agent'})
                mutate()
                before = copy.deepcopy(self.s._game.__dict__)
                self.assertFalse(self.fallback(identifier)['ok'])
                self.assertEqual(self.s._game.__dict__, before)

    def test_new_request_and_new_game_reject_old_identifier(self):
        identifier, _ = self.begin()
        self.s.handle_action('act_sakura', {})
        self.assertFalse(self.fallback(identifier)['ok'])
        self.s.start({'game': 'gomoku', 'first': 'sakura'}, skip_open=True)
        self.assertFalse(self.fallback(identifier)['ok'])
        self.assertEqual(self.s._game.move_count, 0)

    def test_save_failure_rolls_back_game_ticket_rng_log_file_and_retry(self):
        identifier, token = self.begin()
        before = copy.deepcopy(self.s._game.__dict__)
        ticket = copy.deepcopy(self.s._agent_turns.ticket)
        log = list(self.s._log)
        version = self.s._version
        rng = self.s._rng.getstate()
        saved = (self.root / plugin.STATE_FILE).read_bytes()
        metadata = self.meta()
        with patch.object(self.s, '_save_now', side_effect=OSError('injected save failure')):
            result = self.fallback(identifier, metadata=metadata)
        self.assertFalse(result['ok'])
        self.assertEqual(self.s._game.__dict__, before)
        self.assertEqual(self.s._agent_turns.ticket, ticket)
        self.assertEqual(list(self.s._log), log)
        self.assertEqual(self.s._version, version)
        self.assertEqual(self.s._rng.getstate(), rng)
        self.assertEqual((self.root / plugin.STATE_FILE).read_bytes(), saved)
        self.assertFalse(self.s._agent_applying)
        self.assert_late_commit_rejected(token)
        self.assertTrue(self.fallback(identifier, metadata=metadata)['ok'])

    def test_unknown_algorithm_result_is_not_reported_as_success(self):
        identifier, _ = self.begin('failed')
        ticket = copy.deepcopy(self.s._agent_turns.ticket)
        before = copy.deepcopy(self.s._game.__dict__)
        for unknown in (None, {}, {'ok': False, 'status': 'rejected'}):
            with self.subTest(result=unknown):
                with patch.object(self.s, '_execute_algorithm_action', return_value=unknown):
                    self.assertFalse(self.fallback(identifier)['ok'])
                self.assertEqual(self.s._agent_turns.ticket, ticket)
                self.assertEqual(self.s._game.__dict__, before)
                self.assertFalse(self.s._agent_applying)

    def test_queued_timeout_can_fallback_without_dispatching_host(self):
        response = self.s.handle_action('act_sakura', {})
        identifier = response['agentDecision']['id']
        self.assertEqual(self.s._agent_turns.public()['status'], 'queued')
        self.now += 121
        self.assertTrue(self.fallback(identifier)['ok'])
        self.work.pop()()  # A delayed queue worker must not submit the revoked ticket.
        self.assertEqual(self.chat.requests, [])

    def test_algorithm_exception_rolls_back_revocation_and_seat_permission(self):
        identifier, _ = self.begin('failed')
        ticket = copy.deepcopy(self.s._agent_turns.ticket)
        before = copy.deepcopy(self.s._game.__dict__)
        with patch.object(self.s, '_execute_algorithm_action', side_effect=RuntimeError('injected')):
            result = self.fallback(identifier)
        self.assertFalse(result['ok'])
        self.assertEqual(self.s._agent_turns.ticket, ticket)
        self.assertEqual(self.s._game.__dict__, before)
        self.assertFalse(self.s._agent_applying)
        self.assertTrue(self.fallback(identifier)['ok'])


if __name__ == '__main__':
    unittest.main()
