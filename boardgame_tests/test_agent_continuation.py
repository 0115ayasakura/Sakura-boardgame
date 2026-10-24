"""Offline real-service/engine tests: host is fake, all saves are temporary."""
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
from agent_turns import MAX_CHAIN_ACTIONS, TTL_SECONDS
from test_action_ledger import Config, Logger, fixture
from test_agent_turns import HostChat, Context
import test_agent_turns as harness


class AgentContinuationTests(unittest.TestCase):
    meta = harness.AgentTurnTests.meta
    begin = harness.AgentTurnTests.begin
    commit = harness.AgentTurnTests.commit

    def setUp(self):
        tmp = tempfile.TemporaryDirectory(prefix='continuation-', dir=Path(__file__).parent)
        self.addCleanup(tmp.cleanup)
        self.root = Path(tmp.name)
        self.chat = HostChat()
        self.config = Config(decision_driver='agent')
        self.service = plugin.BoardgameService(self.config, lambda n: self.root / n, Logger(),
                                               context=Context(self.chat))
        self.chat.service = self.service
        self.service.start({'game': 'monopoly', 'first': 'sakura'}, skip_open=True)
        self.service._opponent['id'] = 'character-1'
        self.service._game = fixture(actor='sakura')
        self.work = []
        self.service._agent_turns.launch = self.work.append
        self.now = 1000.0
        self.request_seq = 0
        self.service._agent_turns.clock = lambda: self.now

    def route(self, steps=1):
        g = self.service._game
        g.pending = {'type': 'route', 'player': 'sakura', 'options': [1, 3]}
        g._walk = {'player': 'sakura', 'steps_left': steps, 'path': [0]}
        return g

    def follow(self, result):
        c = result['continuation']
        self.assertIsInstance(c, dict)
        snapshot = self.service._agent_turns.state({'token': c['token']})
        for key in ('token', 'gameSeq', 'version', 'expiresAt', 'player', 'legalAction'):
            self.assertEqual(c[key], snapshot[key])
        return c['token'], snapshot

    def test_route_land_purchase_one_host_conversation_and_frozen_receipts(self):
        g = self.route()
        old, _ = self.begin()
        original_deadline = self.service._agent_turns.ticket['expiresAt']
        self.now += TTL_SECONDS - 1
        first = self.commit(old, 'decide', {'action': '1'})
        new, state = self.follow(first)
        self.assertGreater(state['expiresAt'], original_deadline)
        self.assertEqual(state['expiresAt'], int((self.now + TTL_SECONDS) * 1000))
        message = self.chat.requests[0]['message']
        self.assertIn('result.continuation', message)
        self.assertIn('先完成连续选择，再最终简短解说', message)
        self.assertNotIn('只决定并提交一次动作', message)
        self.assertNotIn('插件将在宿主空闲后再次唤醒你', message)
        self.assertNotEqual(new, old)
        self.assertEqual(state['legalAction']['pendingType'], 'land')
        self.assertEqual(first['agentDecision']['status'], 'thinking')
        self.assertTrue(self.service._agent_turns.ticket['scheduled'])
        self.assertEqual(self.service._agent_turns.ticket['sessionId'], 'session-1')
        self.assertEqual(self.service._agent_turns.ticket['characterId'], 'character-1')
        with self.assertRaises(engine.GameError):
            self.commit(old, 'decide', {'action': 'buy'})
        with self.assertRaises(engine.GameError):
            self.service._agent_turns.state({'token': old})
        bought = self.commit(new, 'decide', {'action': 'buy'})
        self.assertIsNone(bought['continuation'])
        self.assertEqual(g.cells[1]['owner'], 'sakura')
        self.assertEqual(g.turn, 'user')
        frozen = copy.deepcopy(first)
        first['continuation']['legalAction']['options'].clear()
        self.assertEqual(self.commit(old, 'decide', {'action': '1'}), {**frozen, 'duplicate': True})
        self.assertEqual(len(self.chat.requests), 1)
        self.assertEqual(self.work, [])

    def test_custom_die_route_land_chain(self):
        g = self.service._game
        g.flags['sakura']['pick_dice'] = True
        old, _ = self.begin()
        rolled = self.commit(old, arguments={'use_custom_die': True})
        token, state = self.follow(rolled)
        self.assertEqual(state['legalAction']['pendingType'], 'dice')
        moved = self.commit(token, 'decide', {'action': '1'})
        token, state = self.follow(moved)
        self.assertEqual(state['legalAction']['pendingType'], 'land')
        self.assertIsNone(self.commit(token, 'decide', {'action': 'skip'})['continuation'])
        self.assertEqual(len(self.chat.requests), 1)

    def test_real_shop_and_black_train_choices_continue_without_host_resubmit(self):
        self.service._game = fixture(kind='shop', actor='sakura')
        token, _ = self.begin()
        result = self.commit(token)
        token, state = self.follow(result)
        self.assertEqual(state['legalAction']['pendingType'], 'shop')
        self.assertIsNone(self.commit(token, 'decide', {'action': 'skip'})['continuation'])
        g = self.service._game
        g.turn = 'sakura'
        g.flags['sakura']['pick_dice'] = True
        g.start_black_train()
        token, _ = self.begin()
        result = self.commit(token, arguments={'use_custom_die': True})
        saw_train = False
        for _ in range(MAX_CHAIN_ACTIONS - 1):
            token, state = self.follow(result)
            legal = state['legalAction']
            saw_train = saw_train or legal['pendingType'] == 'black_train'
            result = self.commit(token, 'decide', {'action': legal['options'][0]})
            if result['continuation'] is None:
                break
        self.assertTrue(saw_train)
        self.assertEqual(g.black_train['actions_taken']['sakura'], 1)
        self.assertEqual(len(self.chat.requests), 2)  # Shop turn and explicit train turn only.

    def test_chain_stops_at_eight_actions_even_when_route_remains(self):
        g = self.route(20)
        g._moves = {i: [j for j in range(4) if j != i] for i in range(4)}
        g._road_preds = {}
        token, state = self.begin()
        for count in range(1, MAX_CHAIN_ACTIONS + 1):
            result = self.commit(token, 'decide', {'action': state['legalAction']['options'][0]})
            if count < MAX_CHAIN_ACTIONS:
                token, state = self.follow(result)
                self.assertEqual(result['continuation']['chainActions'], count + 1)
            else:
                self.assertIsNone(result['continuation'])
        self.assertEqual(g.action_seq, 8)
        self.assertEqual(g.pending['type'], 'route')
        self.assertEqual(self.service._agent_turns.public()['status'], 'committed')
        self.assertEqual(len(self.chat.requests), 1)

    def test_duplicate_ui_requests_and_commit_race_never_dispatch_again(self):
        self.route()
        token, _ = self.begin()
        with ThreadPoolExecutor(max_workers=2) as pool:
            results = list(pool.map(lambda _: self.commit(token, 'decide', {'action': '1'}), range(2)))
        self.assertEqual(sorted(r['duplicate'] for r in results), [False, True])
        self.assertEqual(results[0]['continuation'], results[1]['continuation'])
        active = self.service._agent_turns.ticket['token']
        meta = self.meta('ui-repeat')
        first = self.service.handle_action('act_sakura', {}, metadata=meta)
        repeat = self.service.handle_action('act_sakura', {}, metadata=meta)
        self.assertTrue(repeat['duplicate'])
        self.assertEqual(first['agentDecision']['status'], 'thinking')
        self.assertEqual(active, self.service._agent_turns.ticket['token'])
        self.service._agent_turns.dispatch_pending()
        self.assertEqual(self.work, [])
        self.assertEqual(len(self.chat.requests), 1)
        self.assertEqual(self.service._game.action_seq, 1)

    def test_fresh_ticket_identity_epoch_version_game_and_ttl_boundaries(self):
        for boundary in ('sessionId', 'characterId', 'epoch', 'version', 'gameSeq', 'ttl', 'cancel', 'close'):
            with self.subTest(boundary=boundary):
                self.setUp()
                self.route()
                token, _ = self.begin()
                result = self.commit(token, 'decide', {'action': '1'})
                new = result['continuation']['token']
                turns = self.service._agent_turns
                if boundary in ('sessionId', 'characterId'):
                    self.chat.current_value[boundary] = 'other'
                elif boundary == 'epoch':
                    turns.epoch = 'new-epoch'
                elif boundary == 'version':
                    self.service._version += 1
                elif boundary == 'gameSeq':
                    self.service._game_seq += 1
                elif boundary == 'ttl':
                    self.now += TTL_SECONDS
                elif boundary == 'cancel':
                    self.service.handle_action('cancel_agent', {'id': turns.ticket['id']})
                else:
                    turns.close()
                with self.assertRaises(engine.GameError):
                    turns.state({'token': new})
                with self.assertRaises(engine.GameError):
                    self.commit(new, 'decide', {'action': 'buy'})
                self.assertIsNone(self.service._game.cells[1]['owner'])
                self.assertEqual(len(self.chat.requests), 1)

    def test_save_failure_restores_parent_and_receipt_then_retry_mints_fresh_ticket(self):
        self.route()
        token, _ = self.begin()
        turns = self.service._agent_turns
        checkpoint = turns.checkpoint()
        board = copy.deepcopy(self.service._game.__dict__)
        saved = (self.root / plugin.STATE_FILE).read_bytes()
        with patch.object(self.service, '_save_now', side_effect=OSError('injected')):
            with self.assertRaises(OSError):
                self.commit(token, 'decide', {'action': '1'})
        self.assertEqual(turns.checkpoint(), checkpoint)
        self.assertEqual(self.service._game.__dict__, board)
        self.assertEqual((self.root / plugin.STATE_FILE).read_bytes(), saved)
        first = self.commit(token, 'decide', {'action': '1'})
        next_token, _ = self.follow(first)
        checkpoint = turns.checkpoint()
        with patch.object(self.service, '_save_now', side_effect=OSError('injected')):
            with self.assertRaises(OSError):
                self.commit(next_token, 'decide', {'action': 'buy'})
        self.assertEqual(turns.checkpoint(), checkpoint)
        self.assertIsNone(self.service._game.cells[1]['owner'])
        self.assertTrue(self.commit(next_token, 'decide', {'action': 'buy'})['ok'])
        self.assertEqual(len(self.chat.requests), 1)

    def test_no_automatic_new_roll_after_rest_and_no_gomoku_continuation(self):
        self.service._game = fixture(kind='rest', actor='sakura')
        token, _ = self.begin()
        result = self.commit(token)
        self.assertIsNone(result['continuation'])
        self.assertEqual(self.service._game.turn, 'user')
        self.service.start({'game': 'gomoku', 'first': 'sakura'}, skip_open=True)
        self.service._opponent['id'] = 'character-1'
        token, _ = self.begin()
        self.assertIsNone(self.commit(token, 'place', {'row': 8, 'col': 8})['continuation'])

    def test_post_action_boundary_allowlist_is_explicit(self):
        # Real roll executes first; inject only the post-action boundary condition.
        for pending_type, player, winner, allowed in (
                ('route', 'sakura', None, True), ('dice', 'sakura', None, True),
                ('land', 'sakura', None, True), ('shop', 'sakura', None, True),
                ('black_train', 'sakura', None, True), ('tower', 'sakura', None, False),
                ('future_type', 'sakura', None, False), ('land', 'user', None, False),
                ('land', 'sakura', 'sakura', False), (None, 'sakura', None, False)):
            with self.subTest(pending=pending_type, player=player, winner=winner):
                self.setUp()
                token, _ = self.begin()
                execute = self.service._execute_agent_action
                def wrapped(action, arguments):
                    result = execute(action, arguments)
                    game = self.service._game
                    game.turn = 'sakura'
                    game.winner = winner
                    game.pending = ({'type': pending_type, 'player': player, 'options': ['skip'], 'cell': 1}
                                    if pending_type else None)
                    return result
                with patch.object(self.service, '_execute_agent_action', side_effect=wrapped):
                    result = self.commit(token)
                self.assertEqual(result['continuation'] is not None, allowed)
                self.assertEqual(len(self.chat.requests), 1)


if __name__ == '__main__':
    unittest.main()
