"""Complete-action train timetable regression; isolated engine, no service."""
from pathlib import Path
import copy
import json
import sys
import unittest
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'sakura_boardgame'))
from engine import MonopolyGame, restore_game


def game(count=0, kind='grid'):
    g = MonopolyGame(size=28, dice_sides=6, seed=45, first='user', map_kind=kind)
    g.move_count = count
    return g


def one_step(g, kind='property', custom=False):
    """Real roll/dice/finish hooks, with deterministic single forward step."""
    actor = g.turn
    node = g._moves[g.start_node][0]
    g.positions.update({actor: g.start_node, g._other(actor): g.start_node})
    g.last_step[actor] = None
    g.cells[node]['type'] = kind
    if kind == 'property':
        g.cells[node].update(owner=None, price=60, tier=1, group='daily')
    if custom:
        g.flags[actor]['pick_dice'] = True
        return g.roll(actor, use_custom_die=True)
    with patch.object(g, '_dice_rng') as rng:
        rng.return_value.randint.return_value = 1
        return g.roll(actor)


def end_pending(g):
    actor = g.turn
    while g.pending and g.pending['type'] == 'route':
        g.decide(actor, str(g.pending['options'][0]))
    return g.decide(actor, 'skip')


def complete_train(g):
    while g._black_train_active():
        actor = g.turn
        g.roll(actor)
        while g.pending['type'] == 'route':
            g.decide(actor, str(g.pending['options'][0]))
        g.decide(actor, g.pending['options'][-1])


class BlackTrainScheduleTests(unittest.TestCase):
    def test_train_rolls_do_not_shorten_86_operating_round_limit(self):
        g = game()
        def skip_action():
            actor = g.turn
            g.skip_pending[actor] = True
            return g.roll(actor)
        for _ in range(26):
            skip_action()
        self.assertTrue(g._black_train_active())
        complete_train(g)
        self.assertEqual((g.move_count, g.dice_turn), (26, 36))
        for _ in range(54):
            skip_action()
        self.assertEqual((g.move_count, g.dice_turn), (80, 90))
        self.assertIsNone(g.winner)
        for _ in range(6):
            result = skip_action()
        self.assertEqual((g.move_count, g.dice_turn), (86, 96))
        self.assertIsNotNone(g.winner)
        self.assertIn('回合数达到上限', ''.join(result['story']))

    def test_schedule_is_public_and_copy_isolated(self):
        g = game()
        s = g.to_dict()['black_train_schedule']
        self.assertEqual(s, {'announce_at': 22, 'enter_at': 26, 'announced': False,
                             'entered': False, 'last_completed': 0})
        s['entered'] = True
        self.assertFalse(g.black_train_schedule['entered'])
        self.assertEqual(g.max_rounds, 86)

    def test_22_property_pending_waits_for_complete_and_announces_once(self):
        g = game(21)
        result = one_step(g)
        self.assertEqual(g.move_count, 22)
        self.assertEqual(g.pending['type'], 'land')
        self.assertFalse(g.black_train_schedule['announced'])
        self.assertNotIn('黑列车预告', ''.join(result['story']))
        result = end_pending(g)
        self.assertTrue(g.black_train_schedule['announced'])
        self.assertEqual(g.black_train_schedule['last_completed'], 22)
        self.assertIsNone(g.black_train)
        self.assertIn('第22回合已结束；再过4回合，第26回合结束后，双方将进入黑列车探险', ''.join(result['story']))
        self.assertNotIn('完整行动', ''.join(result['story']))
        one_step(g)
        result = end_pending(g)
        self.assertNotIn('黑列车预告', ''.join(result['story']))

    def test_26_property_pending_and_turn_handoff_before_entry(self):
        for kind in ('grid', 'city'):
            g = game(25, kind)
            one_step(g)
            self.assertEqual(g.move_count, 26)
            self.assertIsNone(g.black_train)
            self.assertFalse(g.black_train_schedule['entered'])
            end_pending(g)
            self.assertEqual(g.turn, 'sakura')
            self.assertTrue(g._black_train_active())
            self.assertTrue(g.black_train_schedule['entered'])
            self.assertEqual(g.black_train['saved']['positions'], g.positions)
            self.assertEqual(g.black_train['actions_left'], 10)

    def test_26_shop_completion_enters_after_its_payment_and_buff_tick(self):
        g = game(25)
        one_step(g, 'shop')
        self.assertEqual(g.pending['type'], 'shop')
        self.assertIsNone(g.black_train)
        end_pending(g)
        self.assertTrue(g._black_train_active())
        self.assertEqual(g.turn, 'sakura')

    def test_dice_picker_and_route_are_not_complete_actions(self):
        g = game(25)
        result = one_step(g, custom=True)
        self.assertEqual(g.move_count, 26)
        self.assertEqual(g.pending['type'], 'dice')
        self.assertIsNone(g.black_train)
        g.decide('user', '1')
        self.assertEqual(g.pending['type'], 'land')
        self.assertIsNone(g.black_train)
        end_pending(g)
        self.assertTrue(g._black_train_active())
        g = game(25)
        # Force a real route pause on the first step without changing move count.
        options = g._moves[g.start_node]
        alternate = next(c['id'] for c in g.cells if c['id'] not in (g.start_node, options[0]))
        g._moves[g.start_node] = [options[0], alternate]
        with patch.object(g, '_dice_rng') as rng:
            rng.return_value.randint.return_value = 1
            g.roll('user')
        self.assertEqual(g.pending['type'], 'route')
        self.assertIsNone(g.black_train)
        g.cells[options[0]]['type'] = 'property'
        g.cells[options[0]].update(owner=None, price=60, tier=1, group='daily')
        g.decide('user', str(options[0]))
        self.assertEqual(g.pending['type'], 'land')
        self.assertIsNone(g.black_train)
        end_pending(g)
        self.assertTrue(g._black_train_active())

    def test_immediate_landing_completes_and_skips_count_as_actions(self):
        for threshold in (22, 26):
            for skipped in (False, True):
                g = game(threshold - 1)
                if skipped:
                    g.skip_pending[g.turn] = True
                    result = g.roll(g.turn)
                    self.assertTrue(result['skipped'])
                else:
                    result = one_step(g, 'start')
                self.assertEqual(g.move_count, threshold)
                self.assertTrue(g.black_train_schedule['announced'])
                self.assertEqual(g._black_train_active(), threshold == 26)
                self.assertEqual(g.turn, 'sakura')

    def test_tower_all_floors_pause_then_finish_is_safe_point(self):
        for threshold in (22, 26):
            g = game(threshold - 1)
            one_step(g, 'tower')
            self.assertEqual(g.pending['type'], 'tower')
            actor, seq = g.turn, g._tower['seq']
            for floor in (-1, 0, 1):
                g.advance_tower(actor, seq, floor)
                self.assertIsNone(g.black_train)
                self.assertFalse(g.black_train_schedule['announced'])
                self.assertEqual(g.move_count, threshold)
            result = g.advance_tower(actor, seq, 2, True)
            self.assertTrue(g.black_train_schedule['announced'])
            self.assertEqual(g._black_train_active(), threshold == 26)
            self.assertIsNone(g.submap)
            self.assertFalse(g._tower['active'])
            before = copy.deepcopy(g.to_dict())
            duplicate = g.advance_tower(actor, seq, 2, True)
            self.assertTrue(duplicate['duplicate'])
            self.assertEqual(g.to_dict(), before)

    def test_mid_tower_restore_does_not_trigger_until_exit(self):
        g = game(25)
        one_step(g, 'tower')
        g.advance_tower('user', g._tower['seq'], -1)
        h = restore_game(json.loads(json.dumps(g.to_dict())))
        self.assertIsNone(h.black_train)
        seq = h._tower['seq']
        h.advance_tower('user', seq, 0)
        h.advance_tower('user', seq, 1)
        self.assertIsNone(h.black_train)
        h.advance_tower('user', seq, 2, True)
        self.assertTrue(h._black_train_active())

    def test_warning_and_pending_save_restore_and_no_load_side_effect(self):
        g = game(21)
        one_step(g)
        h = restore_game(json.loads(json.dumps(g.to_dict())))
        self.assertFalse(h.black_train_schedule['announced'])
        end_pending(h)
        self.assertTrue(h.black_train_schedule['announced'])
        h = restore_game(json.loads(json.dumps(h.to_dict())))
        self.assertTrue(h.black_train_schedule['announced'])
        h.move_count = 25
        one_step(h)
        h = restore_game(json.loads(json.dumps(h.to_dict())))
        self.assertIsNone(h.black_train)
        self.assertFalse(h.black_train_schedule['entered'])
        end_pending(h)
        self.assertTrue(h._black_train_active())
        h = restore_game(json.loads(json.dumps(h.to_dict())))
        self.assertTrue(h.black_train_schedule['entered'])
        self.assertEqual(h.black_train['actions_left'], 10)

    def test_legacy_overdue_save_enters_only_next_safe_completion(self):
        for pending in (False, True):
            g = game(50)
            if pending:
                one_step(g)
            saved = json.loads(json.dumps(g.to_dict()))
            del saved['black_train_schedule']
            h = restore_game(saved)
            self.assertIsNone(h.black_train)
            if pending:
                result = end_pending(h)
            else:
                one_step(h)
                self.assertIsNone(h.black_train)
                result = end_pending(h)
            text = ''.join(result['story'])
            self.assertIn('第51回合已结束', text)
            self.assertNotIn('第22回合已结束', text)
            self.assertNotIn('第26回合已结束', text)
            self.assertTrue(h._black_train_active())
            self.assertTrue(h.black_train_schedule['entered'])

    def test_old_default_timetable_migrates_without_load_progress(self):
        for count, announced, pending in ((21, False, False), (23, False, False),
                                           (25, True, True), (40, True, False)):
            with self.subTest(count=count, announced=announced, pending=pending):
                g = game(count)
                if pending:
                    one_step(g)
                saved = json.loads(json.dumps(g.to_dict()))
                saved['black_train_schedule'].update(announce_at=42, enter_at=46,
                                                     announced=announced, last_completed=count)
                h = restore_game(saved)
                self.assertEqual(h.black_train_schedule, {
                    'announce_at': 22, 'enter_at': 26, 'announced': announced,
                    'entered': False, 'last_completed': count})
                self.assertIsNone(h.black_train)
                if not pending:
                    one_step(h)
                result = end_pending(h)
                self.assertTrue(h.black_train_schedule['announced'])
                self.assertEqual(h._black_train_active(), h.move_count >= 26)
                if not announced:
                    self.assertIn(f'第{h.move_count}回合已结束', ''.join(result['story']))

    def test_old_entered_timetable_migration_keeps_once_flag(self):
        for returned in (False, True):
            g = game(26)
            g.start_black_train()
            if returned:
                complete_train(g)
            saved = json.loads(json.dumps(g.to_dict()))
            saved['black_train_schedule'].update(announce_at=42, enter_at=46,
                                                 announced=True, entered=True, last_completed=26)
            h = restore_game(saved)
            self.assertEqual(h.black_train_schedule['enter_at'], 26)
            self.assertTrue(h.black_train_schedule['entered'])
            if not returned:
                complete_train(h)
            one_step(h)
            result = end_pending(h)
            self.assertFalse(h._black_train_active())
            self.assertNotIn('黑列车预告', ''.join(result['story']))

    def test_nondefault_timetable_is_not_rewritten(self):
        g = game()
        g.black_train_schedule.update(announce_at=30, enter_at=34)
        h = restore_game(json.loads(json.dumps(g.to_dict())))
        self.assertEqual(h.black_train_schedule, g.black_train_schedule)

    def test_return_freezes_count_and_does_not_reenter(self):
        g = game(25)
        one_step(g)
        end_pending(g)
        self.assertEqual(g.move_count, 26)
        complete_train(g)
        self.assertEqual(g.move_count, 26)
        self.assertTrue(g.black_train_schedule['entered'])
        self.assertFalse(g._black_train_active())
        for _ in range(4):
            one_step(g)
            result = end_pending(g)
            self.assertFalse(g._black_train_active())
            self.assertNotIn('黑列车预告', ''.join(result['story']))
        h = restore_game(json.loads(json.dumps(g.to_dict())))
        one_step(h)
        end_pending(h)
        self.assertFalse(h._black_train_active())

    def test_legacy_prior_train_counts_as_used(self):
        g = game(25)
        g.start_black_train()
        complete_train(g)
        saved = json.loads(json.dumps(g.to_dict()))
        del saved['black_train_schedule']
        h = restore_game(saved)
        self.assertTrue(h.black_train_schedule['entered'])
        one_step(h)
        end_pending(h)
        self.assertFalse(h._black_train_active())

    def test_final_round_and_bankruptcy_take_priority(self):
        for pending in (False, True):
            g = game(85)
            one_step(g, 'property' if pending else 'start')
            if pending:
                self.assertIsNone(g.winner)
                self.assertIsNone(g.black_train)
                end_pending(g)
            self.assertIsNotNone(g.winner)
            self.assertIsNone(g.black_train)
            self.assertFalse(g.black_train_schedule['entered'])
        g = game(25)
        g.cash['user'] = 0
        actor = g.turn
        node = g._moves[g.start_node][0]
        g.positions[actor] = g.start_node
        g.cells[node]['type'] = 'tax'
        with patch.object(g, '_dice_rng') as rng:
            rng.return_value.randint.return_value = 1
            g.roll(actor)
        self.assertIsNotNone(g.winner)
        self.assertIsNone(g.black_train)


if __name__ == '__main__':
    unittest.main(verbosity=2)
