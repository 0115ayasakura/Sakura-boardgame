"""Offline proactive-chat priority regressions: real service, fake host, no server."""
from __future__ import annotations

import copy
from pathlib import Path
import sys
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import patch

sys.dont_write_bytecode = True
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'sakura_boardgame'))
sys.path.insert(0, str(Path(__file__).resolve().parent))
import plugin
from test_plugin_smoke import FakeContext


class CheckedContext(FakeContext):
    """Check even host service lookup, not just mobile.begin, is outside the lock."""
    service = None

    def get(self, key):
        if self.service is not None:
            assert not self.service._action_lock._is_owned(), 'host lookup under action lock'
        return super().get(key)


class ProactivePriorityTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory(prefix='sakura-proactive-priority-')
        self.addCleanup(temporary.cleanup)
        self.context = CheckedContext(Path(temporary.name))
        self.context.config.update({'decision_driver': 'agent', 'proactive_minutes': 2,
                                    'auto_narrate': True, 'auto_open_board': False})
        self.service = plugin.BoardgameService(
            self.context.config, self.context.data_path, self.context._logger,
            context=self.context)
        self.context.service = self.service
        self.clock = patch.object(plugin.time, 'time', return_value=1000.0)
        self.clock.start()
        self.addCleanup(self.clock.stop)
        self.service.start({'game': 'monopoly', 'first': 'user'}, skip_open=True)
        # Heartbeat only; no HTTP listener, timer, UI, worker or live host is started.
        self.service._server = SimpleNamespace(_last_visible_page=1000.0)
        self.service._busy_at = 0.0
        self.service._last_talk = 100.0
        self.context.mobile_calls.clear()

    def snapshot(self):
        s = self.service
        return copy.deepcopy((s._game.to_dict(), s._agent_turns.checkpoint(),
                              s._version, s._game_seq, s._last_talk))

    def assert_silent_and_unchanged(self):
        before = self.snapshot()
        self.assertFalse(self.service.proactive_tick())
        self.assertEqual(self.context.mobile_calls, [])
        self.assertEqual(self.snapshot(), before, 'tick changed board, ticket/token or last-talk')

    def stage_ticket(self, status):
        s = self.service
        with s._action_lock:
            s._game.turn = 'sakura'
            s._agent_turns.request()  # A real capability, without dispatch or background work.
            s._agent_turns.ticket['status'] = status
            # Isolate the ticket guard from the independent Sakura-turn guard.
            s._game.turn = 'user'
        return s._agent_turns.ticket['token']

    def test_active_tickets_block_even_on_player_turn(self):
        for status in ('queued', 'thinking'):
            with self.subTest(status=status):
                self.service._last_talk = 100.0
                self.context.mobile_calls.clear()
                token = self.stage_ticket(status)
                self.assert_silent_and_unchanged()
                self.assertEqual(self.service._agent_turns.ticket['token'], token)
                self.service._agent_turns.ticket = None

    def test_sakura_turn_blocks_without_ticket_in_both_drivers(self):
        for driver in ('agent', 'algorithm'):
            with self.subTest(driver=driver):
                self.service._last_talk = 100.0
                self.context.mobile_calls.clear()
                self.context.config.update({'decision_driver': driver})
                self.service._game.turn = 'sakura'
                self.assert_silent_and_unchanged()

    def test_pending_sakura_decision_blocks_without_ticket(self):
        for turn in ('sakura', 'user'):
            with self.subTest(turn=turn):
                self.service._last_talk = 100.0
                self.context.mobile_calls.clear()
                self.service._game.turn = turn
                self.service._game.pending = {
                    'type': 'land', 'player': 'sakura', 'cell': 1, 'options': ['buy', 'skip']}
                self.assert_silent_and_unchanged()

    def test_idle_player_chat_once_outside_lock_with_terminal_ticket(self):
        self.stage_ticket('committed')
        self.context.config.update({'auto_narrate': False})  # Proactive force still applies.
        before = self.snapshot()
        self.assertTrue(self.service.proactive_tick())
        self.assertEqual(len(self.context.mobile_calls), 1)
        self.assertIn('随口说说', self.context.mobile_calls[0]['text'])
        self.assertEqual(self.service._last_talk, 1000.0)
        self.assertEqual(self.snapshot()[:-1], before[:-1])
        self.assertFalse(self.service.proactive_tick())
        self.assertEqual(len(self.context.mobile_calls), 1)

    def test_player_without_ticket_reads_summary_under_lock_and_chats_once(self):
        s = self.service
        original_summary = s._game.summary

        def locked_summary():
            self.assertTrue(s._action_lock._is_owned(), 'board snapshot read outside action lock')
            return original_summary()

        before = self.snapshot()
        with patch.object(s._game, 'summary', side_effect=locked_summary) as summary:
            self.assertTrue(s.proactive_tick())
            self.assertEqual(summary.call_count, 1)
        self.assertEqual(len(self.context.mobile_calls), 1)
        self.assertEqual(self.snapshot()[:-1], before[:-1])
        self.assertFalse(s.proactive_tick())
        self.assertEqual(len(self.context.mobile_calls), 1)

    def test_completed_action_narration_is_not_blocked_by_priority(self):
        self.stage_ticket('thinking')
        self.service._game.turn = 'sakura'
        before = self.snapshot()
        self.service._notify_sakura('已执行的动作')
        self.assertEqual(len(self.context.mobile_calls), 1)
        self.assertIn('已执行的动作', self.context.mobile_calls[0]['text'])
        self.assertEqual(self.snapshot()[:-1], before[:-1])

    def test_existing_visibility_busy_interval_and_finished_gates(self):
        s = self.service
        for gate in ('hidden', 'page_busy', 'recent_user', 'interval', 'disabled', 'winner', 'draw'):
            with self.subTest(gate=gate):
                with patch.object(s._server, '_last_visible_page', 0.0 if gate == 'hidden' else 1000.0), \
                     patch.object(s, '_page_busy', gate == 'page_busy'), \
                     patch.object(s, '_busy_at', 999.0 if gate == 'recent_user' else 0.0), \
                     patch.object(s, '_last_talk', 999.0 if gate == 'interval' else 100.0), \
                     patch.object(s._game, 'winner', 'user' if gate == 'winner' else None), \
                     patch.object(s._game, 'draw', gate == 'draw', create=True):
                    self.context.config.update({'proactive_minutes': 0 if gate == 'disabled' else 2})
                    self.assert_silent_and_unchanged()


if __name__ == '__main__':
    unittest.main(verbosity=2)
