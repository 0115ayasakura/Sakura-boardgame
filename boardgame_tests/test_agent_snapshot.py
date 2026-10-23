"""Production Agent history codec regressions; no real host, UI or deployment."""
from __future__ import annotations

import copy
import json
from pathlib import Path
import sys
import unittest

sys.dont_write_bytecode = True
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'sakura_boardgame'))
sys.path.insert(0, str(Path(__file__).resolve().parent))
import agent_snapshot as codec
import test_agent_turns as fixtures


def wire(value):
    return json.loads(json.dumps(value, ensure_ascii=False, allow_nan=False))


def size(value):
    return len(json.dumps(value, ensure_ascii=False).encode('utf-8'))


class AgentSnapshotCodecTests(unittest.TestCase):
    def board(self):
        facts = {
            'cash': {'before': 100, 'after': 80, 'delta': -20},
            'effects': {'before': {'shield': True}, 'after': {'shield': False},
                        'changed': {'shield': {'before': True, 'after': False}}},
            'inventory': {'before': [], 'after': ['ticket']},
            'future_field': {'nullable': None, 'list': [1, '1', False], '$ref': 9},
        }
        return {'kind': 'monopoly', 'future_board': {'nested': [None, False, '事实']},
                'action_records': [
                    {'id': 7, 'players': {'sakura': facts, 'user': copy.deepcopy(facts)},
                     'properties': [{'cell': 4, 'before': {'owner': None},
                                     'after': {'owner': 'sakura'}}],
                     'context': {'black_train': {'position': 56}}, 'story': ['领取票根'],
                     'turn_complete': False, 'future_row': {'list': [None, True, 1]}},
                    {'id': 8, 'players': {'sakura': dict(reversed(list(facts.items())))},
                     'turn_complete': True, 'context': {'returned_to': 17}},
                    {'id': 9, 'players': {}}, {'id': 10, 'future_row': 'no players'},
                ]}

    def test_json_roundtrip_preserves_all_records_fields_and_explicit_protocol(self):
        original = self.board()
        frozen = copy.deepcopy(original)
        packed = codec.encode_board(original)
        metadata = packed[codec.ENCODING_KEY]
        self.assertEqual(metadata['format'], 'sakura-agent-action-records-v1')
        self.assertIn('从0开始', metadata['readme'])
        self.assertIn('完整历史事实', metadata['readme'])
        self.assertEqual(len(metadata['player_facts']), 1)
        self.assertIsInstance(packed['action_records'], list)
        self.assertEqual([r['id'] for r in packed['action_records']], [7, 8, 9, 10])
        self.assertEqual(packed['action_records'][0]['players']['sakura'], {'$ref': 0})
        self.assertEqual(codec.decode_board(wire(packed)), frozen)
        self.assertEqual(original, frozen)
        for before, after in zip(original['action_records'], packed['action_records']):
            self.assertEqual({k: v for k, v in before.items() if k != 'players'},
                             {k: v for k, v in after.items() if k != 'players'})
        self.assertEqual(packed['future_board'], frozen['future_board'])

    def test_json_scalar_types_are_not_conflated(self):
        values = [True, 1, 1.0, False, 0, 0.0, None, '1', [1], {'x': 1}]
        original = {'action_records': [{'id': i, 'players': {'sakura': {'future': value}}}
                                       for i, value in enumerate(values)]}
        packed = codec.encode_board(original)
        self.assertEqual(len(packed[codec.ENCODING_KEY]['player_facts']), len(values))
        restored = codec.decode_board(wire(packed))
        self.assertEqual(json.dumps(restored, sort_keys=True), json.dumps(original, sort_keys=True))

    def test_no_aliases_in_either_direction_or_between_reconstructed_facts(self):
        original = self.board()
        frozen = copy.deepcopy(original)
        packed = codec.encode_board(original)
        decoded = codec.decode_board(packed)
        decoded['action_records'][0]['players']['sakura']['cash']['after'] = -999
        self.assertEqual(decoded['action_records'][0]['players']['user']['cash']['after'], 80)
        self.assertEqual(decoded['action_records'][1]['players']['sakura']['cash']['after'], 80)
        self.assertEqual(codec.decode_board(packed), frozen)
        packed[codec.ENCODING_KEY]['player_facts'][0]['effects']['after']['shield'] = 'changed'
        packed['future_board']['nested'].append('changed')
        packed['action_records'][0]['story'].append('changed')
        self.assertEqual(original, frozen)
        original['action_records'][0]['players']['sakura']['cash']['before'] = -1
        self.assertEqual(packed[codec.ENCODING_KEY]['player_facts'][0]['cash']['before'], 100)

    def test_empty_and_non_ledger_boards_are_detached_and_roundtrip(self):
        for original in ({'kind': 'monopoly', 'action_records': []},
                         {'kind': 'gomoku', 'board': [[None, 'user']]}):
            with self.subTest(original=original):
                self.assertEqual(codec.decode_board(wire(codec.encode_board(original))), original)
                self.assertIsNot(codec.encode_board(original), original)
                self.assertIsNot(codec.decode_board(original), original)
        self.assertNotIn(codec.ENCODING_KEY, codec.encode_board({'kind': 'gomoku'}))

    def test_unknown_versions_double_encoding_and_invalid_refs_fail_closed(self):
        packed = codec.encode_board(self.board())
        with self.assertRaises(ValueError):
            codec.encode_board(packed)
        bad = copy.deepcopy(packed)
        bad[codec.ENCODING_KEY]['format'] = 'sakura-agent-action-records-v2'
        with self.assertRaises(ValueError):
            codec.decode_board(bad)
        for ref in ({'$ref': -1}, {'$ref': True}, {'$ref': 0.0}, {'$ref': '0'},
                    {'$ref': 1}, {'$ref': 0, 'cash': {}}, {}, None):
            with self.subTest(ref=ref):
                bad = copy.deepcopy(packed)
                bad['action_records'][0]['players']['sakura'] = ref
                with self.assertRaises(ValueError):
                    codec.decode_board(bad)
        self.assertEqual(codec.decode_board(packed), self.board())


class AgentSnapshotIntegrationTests(unittest.TestCase):
    def harness(self):
        harness = fixtures.AgentTurnTests('test_long_real_history_stays_in_state_tool_not_host_message')
        harness.setUp()
        self.addCleanup(harness.doCleanups)
        return harness

    def test_real_max_history_snapshot_is_lossless_read_only_and_smaller(self):
        harness = self.harness()
        service = harness.service
        service._game = fixtures.fixture(kind='rest', actor='sakura')
        service._game.black_train_schedule.update(announce_at=10000, enter_at=10001)
        harness.config.update({'decision_driver': 'algorithm'})
        for _ in range(fixtures.engine.ACTION_RECORD_LIMIT):
            service.roll({'player': service._game.turn})
        harness.config.update({'decision_driver': 'agent'})
        token, snapshot = harness.begin()
        raw_board = copy.deepcopy(service._game.to_dict())
        checkpoint = service._agent_turns.checkpoint()
        version = service._version
        ui_payload = copy.deepcopy(service.state_payload())
        saved = (harness.root / fixtures.plugin.STATE_FILE).read_bytes()
        transported = wire(snapshot)
        decoded_board = codec.decode_board(transported['board'])
        self.assertEqual(decoded_board, raw_board)
        self.assertEqual(decoded_board['action_records'], service._game.action_records)
        self.assertEqual(len(decoded_board['action_records']), fixtures.engine.ACTION_RECORD_LIMIT)
        self.assertEqual([r['id'] for r in decoded_board['action_records']],
                         [r['id'] for r in service._game.action_records])
        self.assertEqual(snapshot['state'], service.state({}))
        self.assertEqual(snapshot['shopItems'], service.agent_shop_items())
        self.assertEqual(snapshot['legalAction'], service._agent_turns.legal())
        baseline = {**copy.deepcopy(snapshot), 'board': raw_board}
        metrics = {'records': len(raw_board['action_records']),
                   'original_utf8_bytes': size(baseline), 'encoded_utf8_bytes': size(snapshot),
                   'unique_player_facts': len(snapshot['board'][codec.ENCODING_KEY]['player_facts'])}
        self.assertLess(metrics['encoded_utf8_bytes'], metrics['original_utf8_bytes'] * 0.75)
        print('AGENT_SNAPSHOT_PRODUCTION ' + json.dumps(metrics, sort_keys=True))
        self.assertEqual(service._agent_turns.state({'token': token}), snapshot)
        # Shared table, ordinary rows and non-history fields are all detached.
        snapshot['board'][codec.ENCODING_KEY]['player_facts'][0]['cash']['after'] = -999
        snapshot['board']['action_records'][0]['story'].append('model edit')
        snapshot['state'].clear()
        snapshot['shopItems'].clear()
        self.assertEqual(service._game.to_dict(), raw_board)
        self.assertEqual(service._agent_turns.checkpoint(), checkpoint)
        self.assertEqual(service._version, version)
        self.assertEqual(service.state_payload(), ui_payload)
        self.assertEqual((harness.root / fixtures.plugin.STATE_FILE).read_bytes(), saved)
        self.assertEqual(codec.decode_board(service._agent_turns.state({'token': token})['board']), raw_board)
        self.assertNotIn(codec.ENCODING_KEY, raw_board)
        self.assertEqual(ui_payload['actionRecords'], raw_board['action_records'])

    def test_real_property_and_inventory_changes_survive_production_state_tool(self):
        harness = self.harness()
        service = harness.service
        service._game = fixtures.fixture(actor='sakura')
        token, _ = harness.begin()
        harness.commit(token)
        token, _ = harness.begin()
        harness.commit(token, 'decide', {'action': 'buy'})
        game = service._game
        game.turn = 'sakura'
        game.pending = {'type': 'shop', 'player': 'sakura', 'cell': 1, 'options': ['buy_dice']}
        token, _ = harness.begin()
        harness.commit(token, 'decide', {'action': 'buy_dice'})
        game.turn = 'sakura'
        _, snapshot = harness.begin()
        decoded = codec.decode_board(wire(snapshot['board']))
        self.assertEqual(decoded, game.to_dict())
        self.assertEqual(decoded['action_records'][1]['properties'][0]['after']['owner'], 'sakura')
        self.assertEqual(decoded['action_records'][2]['players']['sakura']['inventory']['changed']['pick_dice'],
                         {'before': False, 'after': True})


if __name__ == '__main__':
    unittest.main()
