"""Scalar-only payload baseline; no production protocol or real host changes."""
import copy
import json
import unittest
import test_agent_turns as fixtures
from test_agent_turns import engine, fixture
from ledger_reference_experiment import pack, unpack
from agent_snapshot import decode_board


class SnapshotProfileTests(unittest.TestCase):
    def test_reference_roundtrip_preserves_changed_and_unknown_facts(self):
        records = [
            {'id': 1, 'players': {'sakura': {'cash': {'before': 100, 'after': 80, 'delta': -20},
              'effects': {'before': {'shield': True}, 'after': {'shield': False}},
              'future_field': {'nullable': None, 'list': [1, '1', False]}}},
             'properties': [{'cell': 4, 'before': {'owner': None}, 'after': {'owner': 'sakura'}}],
             'context': {'black_train': {'position': 56}}, 'story': ['领取票根'],
             'turn_complete': False},
            {'id': 2, 'players': {'sakura': {'future_field': {'nullable': None, 'list': [True, '1', False]},
              'cash': {'before': 80, 'after': 80, 'delta': 0}}},
             'context': {'returned_to': 17}, 'turn_complete': True},
        ]
        before = copy.deepcopy(records)
        packed = pack(records)
        self.assertEqual(unpack(json.loads(json.dumps(packed))), before)
        self.assertEqual(records, before)
        self.assertEqual(len(packed['player_facts']), 2)
        self.assertEqual(unpack(pack([])), [])

    def test_full_ledger_profile_is_read_only_and_keeps_authority(self):
        harness = fixtures.AgentTurnTests('test_long_real_history_stays_in_state_tool_not_host_message')
        harness.setUp()
        self.addCleanup(harness.doCleanups)
        service = harness.service
        service._game = fixture(kind='rest', actor='sakura')
        service._game.black_train_schedule.update(announce_at=10000, enter_at=10001)
        harness.config.update({'decision_driver': 'algorithm'})
        for _ in range(engine.ACTION_RECORD_LIMIT):
            service.roll({'player': service._game.turn})
        harness.config.update({'decision_driver': 'agent'})
        token, snapshot = harness.begin()
        before_game = copy.deepcopy(service._game.to_dict())
        before_ticket = service._agent_turns.checkpoint()
        before_version = service._version
        again = service._agent_turns.state({'token': token})
        self.assertEqual(snapshot, again)
        self.assertEqual(service._game.to_dict(), before_game)
        self.assertEqual(service._agent_turns.checkpoint(), before_ticket)
        self.assertEqual(service._version, before_version)
        self.assertEqual(snapshot['state']['game'], snapshot['game'])
        self.assertEqual(snapshot['board']['kind'], snapshot['game'])
        self.assertIsInstance(snapshot['state']['board'], str)
        self.assertIsInstance(snapshot['board'], dict)
        self.assertEqual(snapshot['legalAction'], service._agent_turns.legal())
        self.assertEqual(len(snapshot['board']['action_records']), engine.ACTION_RECORD_LIMIT)
        def chars(value):
            return len(json.dumps(value, ensure_ascii=False))
        profile = {'chars': chars(snapshot),
                   'utf8_bytes': len(json.dumps(snapshot, ensure_ascii=False).encode('utf-8')),
                   'ledger_chars': chars(snapshot['board']['action_records']),
                   'state_chars': chars(snapshot['state']),
                   'ledger_count': len(snapshot['board']['action_records'])}
        self.assertGreater(profile['chars'], 32768)
        self.assertGreater(profile['ledger_chars'], profile['state_chars'])
        print('SNAPSHOT_PROFILE ' + json.dumps(profile, sort_keys=True))
        # Production v1 exposes references; preserve the original experiment and
        # full-history assertions by decoding, not by treating refs as facts.
        decoded_board = decode_board(json.loads(json.dumps(snapshot['board'], ensure_ascii=False)))
        self.assertEqual(decoded_board, before_game)
        records = decoded_board['action_records']
        self.assertEqual(records, service._game.action_records)
        original = copy.deepcopy(records)
        packed = pack(records)
        wire = json.loads(json.dumps(packed, ensure_ascii=False))
        restored = unpack(wire)
        self.assertEqual(restored, original)
        self.assertEqual(records, original)
        experiment = {'original_chars': chars(records), 'packed_chars': chars(wire),
                      'records': len(records), 'unique_player_facts': len(wire['player_facts'])}
        self.assertLess(experiment['packed_chars'], experiment['original_chars'])
        print('LEDGER_REFERENCE_EXPERIMENT ' + json.dumps(experiment, sort_keys=True))
        restored[0]['players']['sakura']['cash']['after'] = -999
        self.assertEqual(unpack(wire), original)
        self.assertEqual(service._game.to_dict(), before_game)
        # Editing a model-facing copy must never alter engine or receipt/ticket data.
        again['board']['action_records'].clear()
        self.assertEqual(service._game.to_dict(), before_game)
        self.assertEqual(service._agent_turns.checkpoint(), before_ticket)


if __name__ == '__main__':
    unittest.main()
