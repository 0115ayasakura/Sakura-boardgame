"""Deterministic fake-host tests; never invoke a real model/server or user save."""
from __future__ import annotations

import copy
import json
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
import sys
import tempfile
import threading
import unittest
from unittest.mock import patch

sys.dont_write_bytecode = True
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'sakura_boardgame'))
sys.path.insert(0, str(Path(__file__).resolve().parent))
import engine
import plugin
from agent_snapshot import decode_board
from test_action_ledger import Config, Logger, fixture


class HostChat:
    def __init__(self):
        self.service = None
        self.current_value = {'sessionId': 'session-1', 'characterId': 'character-1', 'idle': True}
        self.requests = []
        self.accepted = {'accepted': True, 'operationId': 'operation-1'}
        self.on_submit = None

    def unlocked(self):
        if self.service is not None:
            assert not self.service._action_lock._is_owned(), 'host RPC under game lock'

    def current(self):
        self.unlocked()
        return dict(self.current_value)

    def submit(self, request):
        self.unlocked()
        self.requests.append(copy.deepcopy(request))
        if self.on_submit:
            self.on_submit()
        return self.accepted


class Context:
    plugin_id = 'test.boardgame'

    def __init__(self, chat):
        self.chat = chat

    def get(self, name):
        return self.chat if name == 'sakura.host.chat' else None


class AgentTurnTests(unittest.TestCase):
    def setUp(self):
        tmp = tempfile.TemporaryDirectory(prefix='sakura-agent-')
        self.addCleanup(tmp.cleanup)
        self.root = Path(tmp.name)
        self.chat = HostChat()
        self.config = Config(decision_driver='agent', gomoku_level=1)
        self.service = plugin.BoardgameService(self.config, lambda n: self.root / n, Logger(),
                                               context=Context(self.chat))
        self.chat.service = self.service
        self.service.start({'game': 'monopoly', 'first': 'sakura'}, skip_open=True)
        self.service._opponent['id'] = 'character-1'
        self.work = []
        self.service._agent_turns.launch = self.work.append
        self.now = 1000.0
        self.request_seq = 0
        self.service._agent_turns.clock = lambda: self.now

    def meta(self, request='request-1'):
        return {'requestId': request, 'gameSeq': self.service._game_seq,
                'expectedVersion': self.service._version}

    def begin(self):
        self.request_seq += 1
        response = self.service.handle_action('act_sakura', {}, metadata=self.meta(f'begin-{self.request_seq}'))
        self.assertTrue(response['ok'], response)
        self.assertEqual(response['agentDecision']['status'], 'queued')
        self.assertNotIn('token', response['agentDecision'])
        self.assertEqual(len(self.work), 1)
        self.work.pop()()
        self.assertEqual(self.service._agent_turns.public()['status'], 'thinking')
        token = self.service._agent_turns.ticket['token']
        return token, self.service._agent_turns.state({'token': token})

    def commit(self, token, action='roll', arguments=None):
        return self.service._agent_turns.commit({'token': token, 'action': action,
                                               'arguments': arguments or {}})

    def test_default_is_real_agent_and_legacy_paths_are_explicit(self):
        self.config.values.pop('decision_driver', None)
        self.assertEqual(self.service.decision_driver(), 'agent')
        with self.assertRaises(engine.GameError):
            self.service.roll({'player': 'sakura'})
        with self.assertRaises(engine.GameError):
            self.service.auto({'rounds': 1})
        self.config.update({'decision_driver': 'algorithm'})
        result = self.service.sakura_action()
        self.assertIn('status', result)
        self.assertEqual(self.chat.requests, [])

    def test_one_action_real_tools_receipt_and_parameter_scope(self):
        s = self.service
        token, state = self.begin()
        self.assertEqual(state['legalAction']['action'], 'roll')
        request = self.chat.requests[0]
        self.assertEqual(set(request), {'sessionId', 'message', 'resources'})
        self.assertEqual(request['sessionId'], 'session-1')
        self.assertIn('boardgame_agent_commit', request['message'])
        self.assertIn(token, request['message'])
        with self.assertRaises(engine.GameError):
            self.commit(token, arguments={'player': 'user'})
        before = s._game.action_seq
        first = self.commit(token)
        self.assertEqual(s._game.action_seq, before + 1)
        self.assertEqual(first['agentDecision']['status'], 'committed')
        frozen = copy.deepcopy(s._game.__dict__)
        second = self.commit(token)
        self.assertEqual(second, {**first, 'duplicate': True})
        self.assertEqual(s._game.__dict__, frozen)
        second['status'] = 'caller mutation'
        self.assertNotEqual(self.commit(token)['status'], 'caller mutation')
        with self.assertRaises(engine.GameError):
            self.commit(token, arguments={'use_custom_die': True})
        self.assertEqual(s._game.__dict__, frozen)

    def test_roll_receipt_exposes_post_action_cash_and_freezes_it_for_retry(self):
        s = self.service
        token, _ = self.begin()
        result = self.commit(token)
        self.assertEqual(result['cash'], s._game.cash)
        self.assertIsNot(result['cash'], s._game.cash)
        frozen = copy.deepcopy(result['cash'])
        s._game.cash['sakura'] += 123
        self.assertEqual(self.commit(token)['cash'], frozen)
        self.assertNotEqual(self.commit(token)['cash'], s._game.cash)
        result['cash']['user'] = -1
        self.assertEqual(self.commit(token)['cash'], frozen)

    def test_route_then_buy_results_constrain_facts_money_and_legacy_hints(self):
        s = self.service
        s._game = fixture(actor='sakura')
        game = s._game
        game.cash['sakura'] = 1370
        game.cells[1]['price'] = 180
        game.pending = {'type': 'route', 'player': 'sakura', 'options': [1, 3]}
        game._walk = {'player': 'sakura', 'steps_left': 1, 'path': [0]}
        token, _ = self.begin()
        raw_results = []
        execute = s._execute_agent_action

        def capture(action, arguments):
            result = execute(action, arguments)
            raw_results.append(copy.deepcopy(result))
            return result

        with patch.object(s, '_execute_agent_action', side_effect=capture):
            route = self.commit(token, 'decide', {'action': '1'})
            self.assertEqual(route['pending']['type'], 'land')
            self.assertIsNone(game.cells[1]['owner'])
            self.assertEqual(game.cash['sakura'], 1370)
            self.assertIn('boardgame_decide', raw_results[0]['hint'])
            self.assertNotIn('boardgame_decide', route['hint'])
            self.assertIn('路线选择成功不等于购买地产', route['hint'])
            self.assertIn('不得声称产权归自己', route['hint'])
            for key in ('pending', 'story', 'status'):
                self.assertEqual(route[key], raw_results[0][key])
            token, _ = self.begin()
            bought = self.commit(token, 'decide', {'action': 'buy'})

        self.assertEqual(bought['cash']['sakura'], 1190)
        self.assertEqual(game.cells[1]['owner'], 'sakura')
        self.assertEqual(bought['cash'], raw_results[1]['cash'])
        self.assertEqual(bought['story'], raw_results[1]['story'])
        self.assertNotIn('boardgame_decide', bought['hint'])
        for text in ('原始整数', '¥单位', '禁止自行计算余额或换算成万、亿',
                     '完全相同的阿拉伯数字', '缺少权威金额就不报金额',
                     '以行动后的commit结果为准', '停止调用走棋工具'):
            self.assertIn(text, bought['hint'])
            self.assertIn(text, self.chat.requests[-1]['message'])
        self.assertEqual(self.commit(token, 'decide', {'action': 'buy'})['hint'], bought['hint'])
        # These checks verify supplied constraints and actual engine facts, not
        # an impossible guarantee that a live model never hallucinates prose.

    def test_long_real_history_stays_in_state_tool_not_host_message(self):
        s = self.service
        s._game = fixture(kind='rest', actor='sakura')
        s._game.black_train_schedule.update(announce_at=10000, enter_at=10001)
        self.config.update({'decision_driver': 'algorithm'})
        # Fill the actual engine ledger with ordinary completed actions, not a
        # synthetic oversized string that bypasses normal serialization.
        for _ in range(engine.ACTION_RECORD_LIMIT):
            s.roll({'player': s._game.turn})
        self.config.update({'decision_driver': 'agent'})
        self.assertEqual(s._game.turn, 'sakura')
        token, snapshot = self.begin()
        self.assertEqual(len(snapshot['board']['action_records']), engine.ACTION_RECORD_LIMIT)
        decoded_board = decode_board(json.loads(json.dumps(snapshot['board'], ensure_ascii=False)))
        self.assertEqual(decoded_board, s._game.to_dict())
        self.assertEqual(decoded_board['action_records'], s._game.action_records)
        self.assertGreater(len(json.dumps(snapshot, ensure_ascii=False)), 32768)
        message = self.chat.requests[-1]['message']
        self.assertLess(len(message), 32768)
        self.assertLess(len(message.encode('utf-8')), 32768)
        self.assertIn('boardgame_agent_state', message)
        dispatch = json.loads(message.split('\n', 1)[1])
        self.assertEqual(set(dispatch), {'token', 'gameSeq', 'version', 'player', 'legalAction'})
        self.assertEqual(dispatch['token'], token)
        self.assertEqual(dispatch['legalAction'], snapshot['legalAction'])
        before = s._game.action_seq
        self.assertTrue(self.commit(token)['ok'])
        self.assertEqual(s._game.action_seq, before + 1)

    def test_outer_save_failure_does_not_schedule_ghost_task(self):
        s = self.service
        with patch.object(s, '_save_now', side_effect=OSError('injected')):
            response = s.handle_action('act_sakura', {}, metadata=self.meta())
        self.assertFalse(response['ok'])
        self.assertIsNone(s._agent_turns.ticket)
        self.assertEqual(self.work, [])
        self.assertEqual(self.chat.requests, [])
        self.begin()

    def test_commit_save_failure_restores_ticket_rng_and_allows_same_token(self):
        s = self.service
        token, _ = self.begin()
        before = copy.deepcopy(s._game.__dict__)
        saved = (self.root / plugin.STATE_FILE).read_bytes()
        with patch.object(s, '_save_now', side_effect=OSError('injected')):
            with self.assertRaises(OSError):
                self.commit(token)
        self.assertEqual(s._game.__dict__, before)
        self.assertEqual(s._agent_turns.public()['status'], 'thinking')
        self.assertNotIn(token, s._agent_turns.receipts)
        self.assertEqual((self.root / plugin.STATE_FILE).read_bytes(), saved)
        self.assertTrue(self.commit(token)['ok'])

    def test_duplicate_commit_race_executes_only_once(self):
        token, _ = self.begin()
        before = self.service._game.action_seq
        with ThreadPoolExecutor(max_workers=2) as pool:
            results = list(pool.map(lambda _: self.commit(token), range(2)))
        self.assertEqual(sorted(r['duplicate'] for r in results), [False, True])
        self.assertEqual(self.service._game.action_seq, before + 1)

    def test_duplicate_request_reuses_one_dispatch_and_no_action_expires(self):
        s = self.service
        meta = self.meta()
        first = s.handle_action('act_sakura', {}, metadata=meta)
        repeat = s.handle_action('act_sakura', {}, metadata=meta)
        self.assertTrue(repeat['duplicate'])
        self.assertEqual(first['agentDecision']['id'], repeat['agentDecision']['id'])
        self.assertEqual(len(self.work), 1)
        self.work.pop()()
        self.assertEqual(s._game.action_seq, 0)
        self.now += 121
        self.assertEqual(s.state_payload()['agentDecision']['status'], 'expired')
        with self.assertRaises(engine.GameError):
            self.commit(s._agent_turns.ticket['token'])

    def test_queued_expiry_before_worker_is_not_reported_as_uncommitted(self):
        s = self.service
        response = s.handle_action('act_sakura', {})
        decision = response['agentDecision']
        self.assertNotIn('waitReason', decision)  # No host observation yet.
        self.now = decision['expiresAt'] / 1000 - 0.001
        self.assertEqual(s._agent_turns.public()['status'], 'queued')
        self.now = decision['expiresAt'] / 1000
        expired = s._agent_turns.public()
        self.assertEqual(expired['status'], 'expired')
        self.assertIn('等待宿主空闲', expired['error'])
        self.assertIn('尚未派发', expired['error'])
        self.assertNotIn('未在时限内提交', expired['error'])
        self.assertNotIn('waitReason', expired)
        self.work.pop()()
        self.assertEqual(self.chat.requests, [])
        self.assertEqual(s._game.action_seq, 0)

    def test_wait_reason_requires_verified_identity_and_explicit_busy(self):
        s = self.service
        for idle in (False, None, 0, 'false', 'missing'):
            with self.subTest(idle=idle):
                self.chat.current_value['idle'] = idle
                if idle == 'missing':
                    self.chat.current_value.pop('idle')
                response = s.handle_action('act_sakura', {})
                self.assertNotIn('waitReason', response['agentDecision'])
                deadline = response['agentDecision']['expiresAt']
                waits = []

                def wait(seconds):
                    waits.append(seconds)
                    decision = s._agent_turns.public()
                    self.assertEqual(decision['status'], 'queued')
                    if idle is False:
                        self.assertEqual(decision['waitReason'], 'host_busy')
                    else:
                        self.assertNotIn('waitReason', decision)
                    self.assertEqual(set(decision) - {'waitReason'},
                                     {'id', 'status', 'gameSeq', 'version', 'action', 'expiresAt'})
                    # The reason is an observation, not a new TTL or dispatch.
                    self.assertEqual(decision['expiresAt'], deadline)
                    self.assertEqual(self.chat.requests, [])
                    self.now = deadline / 1000

                s._agent_turns.wait = wait
                self.work.pop()()
                self.assertEqual(waits, [0.25])
                expired = s._agent_turns.public()
                self.assertEqual(expired['status'], 'expired')
                self.assertNotIn('waitReason', expired)
        self.chat.current_value = {'sessionId': 'session-1', 'idle': False}
        s.handle_action('act_sakura', {})
        self.work.pop()()
        failed = s._agent_turns.public()
        self.assertEqual(failed['status'], 'failed')
        self.assertNotIn('waitReason', failed)
        self.assertEqual(self.chat.requests, [])

    def test_unknown_idle_observation_clears_previous_busy_reason(self):
        s = self.service
        self.chat.current_value['idle'] = False
        s.handle_action('act_sakura', {})
        waits = []

        def wait(seconds):
            waits.append(seconds)
            decision = s._agent_turns.public()
            if len(waits) == 1:
                self.assertEqual(decision['waitReason'], 'host_busy')
                self.chat.current_value.pop('idle')
            else:
                self.assertEqual(len(waits), 2)
                self.assertNotIn('waitReason', decision)
                self.chat.current_value['idle'] = True

        s._agent_turns.wait = wait
        self.work.pop()()
        self.assertEqual(waits, [0.25, 0.25])
        self.assertEqual(len(self.chat.requests), 1)
        self.assertEqual(s._agent_turns.public()['status'], 'thinking')
        self.assertNotIn('waitReason', s._agent_turns.public())
        self.commit(s._agent_turns.ticket['token'])
        self.assertNotIn('waitReason', s._agent_turns.public())

    def test_thinking_expiry_during_submit_does_not_claim_acceptance_or_retry(self):
        s = self.service
        response = s.handle_action('act_sakura', {})
        deadline = response['agentDecision']['expiresAt']
        self.chat.accepted = None  # Unknown outcome must never cause resubmission.

        def during_submit():
            thinking = s._agent_turns.public()
            self.assertEqual(thinking['status'], 'thinking')
            self.assertNotIn('waitReason', thinking)
            self.now = deadline / 1000
            expired = s._agent_turns.public()
            self.assertEqual(expired['status'], 'expired')
            self.assertIn('派发后未在时限内提交', expired['error'])
            self.assertNotIn('受理', expired['error'])
            self.assertNotIn('waitReason', expired)

        self.chat.on_submit = during_submit
        worker = self.work.pop()
        worker()
        worker()
        s._agent_turns.dispatch_pending()
        self.assertEqual(len(self.chat.requests), 1)
        self.assertEqual(self.work, [])
        self.assertEqual(s._agent_turns.public()['status'], 'expired')
        with self.assertRaises(engine.GameError):
            self.commit(s._agent_turns.ticket['token'])
        self.assertEqual(s._game.action_seq, 0)

    def test_unknown_submit_outcome_is_failed_without_automatic_retry(self):
        s = self.service
        self.chat.accepted = None
        s.handle_action('act_sakura', {})
        worker = self.work.pop()
        worker()
        self.assertEqual(s._agent_turns.public()['status'], 'failed')
        self.assertNotIn('waitReason', s._agent_turns.public())
        worker()
        s._agent_turns.dispatch_pending()
        self.assertEqual(len(self.chat.requests), 1)
        self.assertEqual(self.work, [])
        with self.assertRaises(engine.GameError):
            self.commit(s._agent_turns.ticket['token'])
        self.assertEqual(s._game.action_seq, 0)

    def test_cancel_expire_new_game_undo_driver_and_unload_revoke(self):
        token, _ = self.begin()
        s = self.service
        identifier = s._agent_turns.public()['id']
        wrong = s.handle_action('cancel_agent', {'id': 'old'})
        self.assertFalse(wrong['ok'])
        accepted = s.handle_action('cancel_agent', {'id': identifier})
        self.assertTrue(accepted['ok'])
        with self.assertRaises(engine.GameError):
            self.commit(token)
        # A successful new game increments the epoch-bound board revision.
        s.start({'game': 'monopoly', 'first': 'sakura'}, skip_open=True)
        s._opponent['id'] = 'character-1'
        token, _ = self.begin()
        s.start({'game': 'monopoly', 'first': 'sakura'}, skip_open=True)
        with self.assertRaises(engine.GameError):
            self.commit(token)
        s._opponent['id'] = 'character-1'
        token, _ = self.begin()
        s.handle_action('set_decision_driver', {'driver': 'algorithm'})
        with self.assertRaises(engine.GameError):
            self.commit(token)
        s._agent_turns.close()
        with self.assertRaises(engine.GameError):
            self.commit(token)

    def test_chat_session_and_character_change_permanently_revoke(self):
        for key in ('sessionId', 'characterId'):
            with self.subTest(key=key):
                token, _ = self.begin()
                old = self.chat.current_value[key]
                self.chat.current_value[key] = 'changed'
                with self.assertRaises(engine.GameError):
                    self.commit(token)
                self.assertEqual(self.service._agent_turns.public()['status'], 'cancelled')
                self.chat.current_value[key] = old
                with self.assertRaises(engine.GameError):
                    self.commit(token)

    def test_host_settings_driver_toggle_revokes_even_without_polling(self):
        token, _ = self.begin()
        s = self.service
        s.save_settings({'decision_driver': 'algorithm'})
        s.save_settings({'decision_driver': 'agent'})
        with self.assertRaises(engine.GameError):
            self.commit(token)
        self.assertEqual(s._agent_turns.public()['status'], 'cancelled')

    def test_busy_rejection_and_submit_exception_never_fallback(self):
        s = self.service
        self.chat.current_value['idle'] = False
        response = s.handle_action('act_sakura', {})
        waits = []
        def wait(seconds):
            self.assertFalse(s._action_lock._is_owned())
            self.assertEqual(s._agent_turns.public()['status'], 'queued')
            self.assertEqual(self.chat.requests, [])
            waits.append(seconds)
            self.now += 121
        s._agent_turns.wait = wait
        self.work.pop()()
        self.assertEqual(waits, [0.25])
        self.assertEqual(s._agent_turns.public()['status'], 'expired')
        self.assertEqual(self.chat.requests, [])
        self.assertEqual(s._game.action_seq, 0)
        self.chat.current_value['idle'] = True
        self.chat.accepted = {'accepted': False, 'reasonCode': 'BUSY'}
        s.handle_action('act_sakura', {})
        self.work.pop()()
        self.assertEqual(s._agent_turns.public()['status'], 'failed')
        self.assertIn('BUSY', s._agent_turns.public()['error'])
        self.chat.accepted = {'accepted': True}
        self.chat.on_submit = lambda: (_ for _ in ()).throw(RuntimeError('host'))
        s.handle_action('act_sakura', {})
        self.work.pop()()
        self.assertEqual(s._agent_turns.public()['status'], 'failed')
        self.assertEqual(s._game.action_seq, 0)

    def queue_busy_continuation(self):
        s = self.service
        s._game = fixture(actor='sakura')
        s._version += 1
        self.chat.current_value.update(sessionId='session-1', characterId='character-1', idle=True)
        token, _ = self.begin()
        self.commit(token)
        self.assertEqual(s._game.pending['type'], 'land')
        self.chat.current_value['idle'] = False
        response = s.handle_action('act_sakura', {})
        self.assertTrue(response['ok'])
        self.assertEqual(s._agent_turns.public()['status'], 'queued')
        return self.work.pop()

    def test_committed_continuation_waits_unlocked_then_submits_once(self):
        callback = self.queue_busy_continuation()
        s = self.service
        before = s._game.action_seq
        count = len(self.chat.requests)
        waits = []

        def wait(seconds):
            self.assertFalse(s._action_lock._is_owned())
            self.assertEqual(s._agent_turns.public()['status'], 'queued')
            self.assertEqual(s._agent_turns.public()['waitReason'], 'host_busy')
            self.assertEqual(len(self.chat.requests), count)
            duplicate = s.handle_action('act_sakura', {})
            self.assertTrue(duplicate['ok'])
            self.assertEqual(self.work, [])  # Same ticket already has its one worker.
            waits.append(seconds)
            self.chat.current_value['idle'] = True

        s._agent_turns.wait = wait
        callback()
        self.assertEqual(waits, [0.25])
        self.assertEqual(len(self.chat.requests), count + 1)
        self.assertEqual(s._game.action_seq, before)
        self.assertEqual(s._agent_turns.public()['status'], 'thinking')
        self.assertNotIn('waitReason', s._agent_turns.public())
        self.assertIn('不要让用户代选', self.chat.requests[-1]['message'])
        token = s._agent_turns.ticket['token']
        snapshot = s._agent_turns.state({'token': token})
        self.commit(token, 'decide', {'action': snapshot['legalAction']['options'][0]})
        self.assertEqual(s._agent_turns.public()['status'], 'committed')

    def test_waiting_continuation_cancel_switch_version_timeout_unload_stop(self):
        s = self.service
        for boundary in ('cancel', 'session', 'character', 'version', 'newgame', 'timeout', 'unload'):
            with self.subTest(boundary=boundary):
                callback = self.queue_busy_continuation()
                count = len(self.chat.requests)
                waits = []

                def wait(seconds):
                    self.assertFalse(s._action_lock._is_owned())
                    waits.append(seconds)
                    self.assertEqual(len(waits), 1)
                    self.assertEqual(s._agent_turns.public()['waitReason'], 'host_busy')
                    if boundary == 'cancel':
                        s.handle_action('cancel_agent', {'id': s._agent_turns.ticket['id']})
                    elif boundary in ('session', 'character'):
                        self.chat.current_value[boundary + 'Id'] = 'changed'
                    elif boundary == 'version':
                        s._version += 1
                    elif boundary == 'newgame':
                        s.start({'game': 'monopoly', 'first': 'sakura'}, skip_open=True)
                    elif boundary == 'timeout':
                        self.now += 121
                    else:
                        s._agent_turns.close()
                    self.chat.current_value['idle'] = True

                s._agent_turns.wait = wait
                callback()
                self.assertEqual(len(waits), 1)
                self.assertEqual(len(self.chat.requests), count)
                expected = 'expired' if boundary == 'timeout' else 'cancelled'
                self.assertEqual(s._agent_turns.public()['status'], expected)
                self.assertNotIn('waitReason', s._agent_turns.public())

    def test_wait_poll_rechecks_cancel_after_identity_rpc(self):
        callback = self.queue_busy_continuation()
        s = self.service
        count = len(self.chat.requests)
        original = self.chat.current

        def current():
            identity = original()
            s.handle_action('cancel_agent', {'id': s._agent_turns.ticket['id']})
            return {**identity, 'idle': True}

        with patch.object(self.chat, 'current', side_effect=current):
            callback()
        self.assertEqual(len(self.chat.requests), count)
        self.assertEqual(s._agent_turns.public()['status'], 'cancelled')

    def test_new_version_waits_but_old_worker_and_token_stay_revoked(self):
        old_worker = self.queue_busy_continuation()
        s = self.service
        old_token = s._agent_turns.ticket['token']
        s._version += 1
        response = s.handle_action('act_sakura', {})
        self.assertTrue(response['ok'])
        new_token = s._agent_turns.ticket['token']
        self.assertNotEqual(new_token, old_token)
        count = len(self.chat.requests)
        old_worker()
        self.assertEqual(len(self.chat.requests), count)
        waits = []
        def wait(seconds):
            self.assertFalse(s._action_lock._is_owned())
            self.assertEqual(s._agent_turns.public()['status'], 'queued')
            duplicate = s.handle_action('act_sakura', {})
            self.assertEqual(duplicate['agentDecision']['id'], response['agentDecision']['id'])
            self.assertEqual(self.work, [])
            waits.append(seconds)
            self.chat.current_value['idle'] = True
        s._agent_turns.wait = wait
        self.work.pop()()
        self.assertEqual(waits, [0.25])
        self.assertEqual(len(self.chat.requests), count + 1)
        with self.assertRaises(engine.GameError):
            s._agent_turns.state({'token': old_token})
        self.assertEqual(s._agent_turns.state({'token': new_token})['version'], s._version)

    def test_slow_host_submit_does_not_hold_board_lock(self):
        entered, release = threading.Event(), threading.Event()
        self.chat.on_submit = lambda: (entered.set(), release.wait(3))
        self.service.handle_action('act_sakura', {})
        callback = self.work.pop()
        with ThreadPoolExecutor(max_workers=2) as pool:
            worker = pool.submit(callback)
            self.assertTrue(entered.wait(2))
            try:
                state = pool.submit(self.service.state_payload).result(timeout=1)
                self.assertEqual(state['agentDecision']['status'], 'thinking')
                cancelled = pool.submit(self.service.handle_action, 'cancel_agent',
                                       {'id': state['agentDecision']['id']}).result(timeout=1)
                self.assertTrue(cancelled['ok'])
            finally:
                release.set()
            worker.result(timeout=2)
        self.assertEqual(self.service._agent_turns.public()['status'], 'cancelled')

    def test_cancel_and_new_game_before_worker_prevent_host_submission(self):
        s = self.service
        accepted = s.handle_action('act_sakura', {})
        cancelled = s.handle_action('cancel_agent', {'id': accepted['agentDecision']['id']})
        self.assertTrue(cancelled['ok'])
        self.work.pop()()
        self.assertEqual(self.chat.requests, [])
        s.handle_action('act_sakura', {})
        s.start({'game': 'monopoly', 'first': 'sakura'}, skip_open=True)
        self.work.pop()()
        self.assertEqual(self.chat.requests, [])
        self.assertEqual(s._agent_turns.public()['status'], 'cancelled')

    def test_undo_and_absent_host_never_allow_late_or_algorithm_moves(self):
        s = self.service
        s.start({'game': 'gomoku', 'first': 'user'}, skip_open=True)
        s._opponent['id'] = 'character-1'
        s.place({'player': 'user', 'row': 1, 'col': 1})
        token, _ = self.begin()
        s.undo({})
        with self.assertRaises(engine.GameError):
            self.commit(token, 'place', {'row': 2, 'col': 2})
        self.assertEqual(s._game.move_count, 0)
        s.start({'game': 'monopoly', 'first': 'sakura'}, skip_open=True)
        s._context = None
        s.handle_action('act_sakura', {})
        self.work.pop()()
        self.assertEqual(s._agent_turns.public()['status'], 'failed')
        self.assertEqual(s._game.action_seq, 0)
        self.assertEqual(len(self.chat.requests), 1)  # Only the earlier valid request.

    def test_gomoku_requires_explicit_coordinates_and_never_calls_old_ai(self):
        s = self.service
        s.start({'game': 'gomoku', 'first': 'sakura'}, skip_open=True)
        s._opponent['id'] = 'character-1'
        token, state = self.begin()
        self.assertEqual(state['legalAction']['action'], 'place')
        with patch.object(s._game, 'ai_move', side_effect=AssertionError('no legacy AI')):
            with self.assertRaises(engine.GameError):
                s.place({'player': 'sakura'})
            for args in ({}, {'row': True, 'col': 1}, {'row': 16, 'col': 1}):
                with self.assertRaises(engine.GameError):
                    self.commit(token, 'place', args)
            self.assertTrue(self.commit(token, 'place', {'row': 8, 'col': 8})['ok'])
        self.assertEqual(s._game.move_count, 1)
        # User's explicit coordinate path remains available in Agent mode.
        self.assertIn('status', s.place({'player': 'user', 'row': 8, 'col': 9}))

    def test_legal_pending_choice_and_tower_seq_floor_handshake(self):
        s = self.service
        g = s._game
        # Real paused tower, not a fabricated public payload.
        g.map_kind = 'grid'
        g._enter_device('sakura', 0, [])
        while g.pending and g.pending.get('type') == 'tower':
            token, state = self.begin()
            legal = state['legalAction']
            bad = {**legal['arguments'], 'floor': 999}
            with self.assertRaises(engine.GameError):
                self.commit(token, legal['action'], bad)
            result = self.commit(token, legal['action'], legal['arguments'])
            self.assertTrue(result['ok'])
        self.assertFalse(g._tower['active'])

    def test_real_route_and_black_train_choices_stay_single_step(self):
        s, g = self.service, self.service._game
        # Real custom-die pending allows deterministic movement into a route.
        g.flags['sakura']['pick_dice'] = True
        token, _ = self.begin()
        self.commit(token, 'roll', {'use_custom_die': True})
        self.assertEqual(g.pending['type'], 'dice')
        token, state = self.begin()
        self.assertIn('1', state['legalAction']['options'])
        with self.assertRaises(engine.GameError):
            self.commit(token, 'decide', {'action': 'not-a-choice'})
        self.commit(token, 'decide', {'action': '1'})
        # Black train uses the engine's real setup and current pending owner.
        g.pending = None
        g._walk = None
        g.turn = 'sakura'
        g.flags['sakura']['pick_dice'] = True
        g.start_black_train()
        token, state = self.begin()
        self.assertEqual(state['legalAction']['action'], 'roll')
        self.commit(token, 'roll', {'use_custom_die': True})
        for _ in range(12):
            if g.pending and g.pending['type'] == 'black_train':
                break
            token, state = self.begin()
            self.assertEqual(state['legalAction']['action'], 'decide')
            self.commit(token, 'decide', {'action': state['legalAction']['options'][0]})
        self.assertEqual(g.pending['type'], 'black_train')
        token, state = self.begin()
        self.commit(token, 'decide', {'action': state['legalAction']['options'][0]})
        self.assertEqual(s._agent_turns.public()['status'], 'committed')
        self.assertEqual(g.black_train['actions_taken']['sakura'], 1)


if __name__ == '__main__':
    unittest.main()
