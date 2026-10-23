"""Signed event displacement and legacy cat income audit regressions."""
from pathlib import Path
import copy
import sys
import unittest
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'sakura_boardgame'))
import engine


def game(kind='grid'):
    return engine.MonopolyGame(size=28, dice_sides=6, seed=45, first='user', map_kind=kind)


def move(g, node, delta, player='user', mock_landing=True):
    g.positions[player] = node
    card = {'title': '审计移动', 'effect': {'type': 'move', 'delta': delta}}
    story = []
    if mock_landing:
        with patch.object(g, '_resolve_landing', return_value={'kind': 'nothing'}) as landing:
            result = g._apply_card(player, card, '', story)
            landing.assert_called_once_with(player, g.positions[player], allow_navi=False, story=story)
    else:
        result = g._apply_card(player, card, '', story)
    return result, story


class EventMovementAuditTests(unittest.TestCase):
    def test_signed_moves_differ_on_both_maps_for_both_players(self):
        for kind in ('grid', 'city'):
            for player in ('user', 'sakura'):
                for delta, expected in ((-2, 1), (2, 5)):
                    with self.subTest(kind=kind, player=player, delta=delta):
                        g = game(kind)
                        g.last_step[player] = [2, 3]
                        before = g.cash[player]
                        result, _ = move(g, 3, delta, player)
                        self.assertEqual(g.positions[player], expected)
                        self.assertEqual(result['path'], [3, 2, 1] if delta < 0 else [3, 4, 5])
                        self.assertEqual(g.cash[player], before)
                        self.assertEqual(g.last_step[player], None if delta < 0 else [4, 5])

    def test_grid_backwards_clamps_at_start_without_reverse_portal_or_salary(self):
        for source, delta, path in ((1, -3, [1, 0]), (0, -2, [0])):
            g = game()
            g.flags['user']['salary_next'] = True
            before = g.cash['user']
            result, _ = move(g, source, delta)
            self.assertEqual(result['path'], path)
            self.assertEqual(g.positions['user'], g.start_node)
            self.assertEqual(g.cash['user'], before)
            self.assertTrue(g.flags['user']['salary_next'])
            self.assertIsNone(g.last_step['user'])

    def test_city_backwards_crosses_start_without_collecting_salary(self):
        g = game('city')
        g.flags['user']['salary_next'] = True
        before = g.cash['user']
        result, _ = move(g, 1, -3)
        self.assertEqual(result['path'], [1, 0, 27, 26])
        self.assertEqual(g.positions['user'], 26)
        self.assertEqual(g.cash['user'], before)
        self.assertTrue(g.flags['user']['salary_next'])

    def test_backward_retraces_actual_branch_at_merges(self):
        for kind, path in (('grid', [54, 55, 56, 17]),
                           ('city', [36, 37, 11])):
            with self.subTest(kind=kind):
                g = game(kind)
                g._last_walk = {'player': 'user', 'path': list(path), 'steps_left': 0}
                g.last_step['user'] = path[-2:]
                result, _ = move(g, path[-1], -2)
                self.assertEqual(result['path'], list(reversed(path[-3:])))
                self.assertEqual(g.positions['user'], path[-3])
                self.assertEqual(g._last_walk['path'], path + list(reversed(path[-3:]))[1:])

    def test_backward_uses_grid_branch_entrance_connector(self):
        g = game()
        result, _ = move(g, 32, -2)
        self.assertEqual(result['path'], [32, 31, 9])
        self.assertEqual(g.positions['user'], 9)
        self.assertIsNone(g.last_step['user'])

    def test_branch_32_to_31_keeps_next_roll_forward_and_path_actually_backward(self):
        g = game()
        g._last_walk = {'player': 'user', 'path': [31, 32], 'steps_left': 0}
        g.last_step['user'] = [31, 32]
        result, _ = move(g, 32, -1)
        self.assertEqual(result['path'], [32, 31])
        self.assertEqual(g._last_walk['path'], [31, 32, 31])
        self.assertEqual(g.last_step['user'], [9, 31])
        with patch.object(g, '_dice_rng') as rng, patch.object(g, '_resolve_landing', return_value={'kind': 'nothing'}):
            rng.return_value.randint.return_value = 1
            result = g.roll('user')
        self.assertIsNone(g.pending)
        self.assertEqual(result['path'], [31, 32])
        self.assertEqual(g.positions['user'], 32)

    def test_forward_keeps_normal_approach_at_grid_branch_entrance(self):
        g = game()
        g.last_step['user'] = [9, 31]
        result, _ = move(g, 31, 2)
        self.assertEqual(result['path'], [31, 32, 33])
        self.assertEqual(g.last_step['user'], [32, 33])

    def test_forward_portal_final_step_middle_step_and_end_origin(self):
        for source, delta, path, facing in ((29, 1, [29, 30, 0], None),
                                            (29, 2, [29, 30, 0, 1], [0, 1]),
                                            (30, 1, [30, 0, 1], [0, 1])):
            with self.subTest(source=source, delta=delta):
                g = game()
                before = g.cash['user']
                result, _ = move(g, source, delta)
                self.assertEqual(result['path'], path)
                self.assertEqual(g.positions['user'], path[-1])
                self.assertEqual(g.cash['user'] - before, engine.pass_start('grid'))
                self.assertEqual(g.last_step['user'], facing)

    def test_forward_crossing_start_city_pays_even_if_not_final(self):
        g = game('city')
        before = g.cash['user']
        result, _ = move(g, 27, 2)
        self.assertEqual(result['path'], [27, 0, 1])
        self.assertEqual(g.cash['user'] - before, engine.pass_start('city'))
        self.assertEqual(g.last_step['user'], [0, 1])

    def test_multiple_portals_pay_once_per_crossing_with_salary_next(self):
        g = game()
        g.flags['user']['salary_next'] = True
        before = g.cash['user']
        result, _ = move(g, 29, 32)
        self.assertEqual(result['path'].count(0), 2)
        self.assertEqual(g.positions['user'], 1)
        self.assertEqual(g.cash['user'] - before, engine.pass_start('grid') * 3)
        self.assertFalse(g.flags['user']['salary_next'])

    def test_zero_move_neither_salary_nor_facing_mutation(self):
        g = game()
        g.last_step['user'] = [27, 0]
        before = g.cash['user']
        result, _ = move(g, 0, 0)
        self.assertEqual(result['path'], [0])
        self.assertEqual(g.cash['user'], before)
        self.assertEqual(g.last_step['user'], [27, 0])

    def test_move_does_not_recursively_draw_event_or_change_action_counters(self):
        g = game()
        g.cells[1]['type'] = 'event'
        before = (g.move_count, g.dice_turn, g._rolls)
        with patch.object(g, '_draw_card', side_effect=AssertionError('recursive event')):
            result, _ = move(g, 3, -2, mock_landing=False)
        self.assertEqual(result['chain']['kind'], 'nothing')
        self.assertEqual((g.move_count, g.dice_turn, g._rolls), before)
        self.assertIsNone(g.winner)

    def test_real_roll_card_appends_displacement_not_synthetic_facing(self):
        g = game()
        g.positions['user'] = 32
        g.last_step['user'] = [31, 32]
        g.cells[33]['type'] = 'event'
        g.cells[31]['type'] = 'event'  # Landing must not open an unrelated property menu.
        card = {'title': '回退', 'flavor': '回退两步', 'effect': {'type': 'move', 'delta': -2}}
        with patch.object(g, '_draw_card', return_value=card), patch.object(g, '_dice_rng') as rng:
            rng.return_value.randint.return_value = 1
            result = g.roll('user')
        self.assertEqual(result['path'], [32, 33, 32, 31])
        self.assertEqual(g.positions['user'], 31)
        self.assertEqual(g.last_step['user'], [9, 31])
        self.assertEqual(g.move_count, 1)
        self.assertEqual(g.turn, 'sakura')

    def test_goto_start_clears_stale_approach_and_pays_once(self):
        g = game()
        g.last_step['user'] = [9, 31]
        before = g.cash['user']
        g._apply_card('user', {'title': '归途', 'effect': {'type': 'goto_start'}}, '', [])
        self.assertEqual(g.positions['user'], g.start_node)
        self.assertIsNone(g.last_step['user'])
        self.assertEqual(g.cash['user'] - before, engine.pass_start('grid'))

    def test_tower_still_defers_movement_without_mutating_position(self):
        g = game()
        g.positions['user'] = 3
        g._tower = {'active': True, 'player': 'user', 'deferred': []}
        card = {'title': '回退', 'effect': {'type': 'move', 'delta': -2}}
        with patch.object(g, '_resolve_landing', side_effect=AssertionError('not deferred')):
            result = g._apply_card('user', card, '', [])
        self.assertTrue(result['deferred'])
        self.assertEqual(g._tower['deferred'], [card])
        self.assertEqual(g.positions['user'], 3)


class CatIncomeAuditTests(unittest.TestCase):
    def test_city_cat_income_uses_highest_buff_without_discounting_food(self):
        for buff, adventure, expected_gain in ((None, 0, 150), (None, 3, 188),
                                               ('gain', 0, 300), ('gain', 3, 300),
                                               ('loss', 3, 188), ('loss_up', 0, 150)):
            for player in ('user', 'sakura'):
                with self.subTest(buff=buff, adventure=adventure, player=player):
                    g = game('city')
                    g.flags[player].update(buff=buff, adventure_left=adventure)
                    g.cash[player] = 50
                    cat = next(c for c in g.cells if c['type'] == 'cat')
                    story = []
                    result = g._resolve_landing(player, cat['id'], True, story)
                    self.assertEqual(result['kind'], 'cat')
                    self.assertEqual(g.cash[player], expected_gain)  # exactly 50 food, not boosted
                    self.assertIn(str(expected_gain), ''.join(story))
                    self.assertIn('50', ''.join(story))
                    self.assertEqual(g.flags[player]['adventure_left'], adventure)

    def test_unaffordable_cat_does_not_charge_or_grant_income(self):
        g = game('city')
        g.cash['user'] = 49
        g.flags['user'].update(buff='gain', adventure_left=3)
        before = copy.deepcopy((g.cash, g.flags))
        cat = next(c for c in g.cells if c['type'] == 'cat')
        result = g._resolve_landing('user', cat['id'], True, [])
        self.assertEqual(result['kind'], 'nothing')
        self.assertEqual((g.cash, g.flags), before)


if __name__ == '__main__':
    unittest.main(verbosity=2)
