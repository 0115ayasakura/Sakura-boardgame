"""Gameplay and popup payload regressions for successful game-cell rolls."""
from pathlib import Path
import copy
import sys
import unittest
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'sakura_boardgame'))
import engine


class GameCellRewardTests(unittest.TestCase):
    def game(self):
        return engine.MonopolyGame(size=28, dice_sides=6, seed=45,
                                   first='user', map_kind='grid')

    def land(self, g, sub, drawn='bonus', roll=6, player='user'):
        cells = [c for c in g.cells if c['type'] == 'game']
        cell = next((c for c in cells if c.get('sub') == sub), cells[0])
        cell['sub'] = sub
        events = engine.game_events()
        draw = next(i for i, e in enumerate(events) if e['key'] == drawn)
        story = []
        with patch.object(g, '_effect_rng') as rng:
            rng.return_value.randrange.return_value = draw
            rng.return_value.randint.return_value = roll
            result = g._resolve_landing(player, cell['id'], True, story)
        return result, story, g._last_landing

    def test_decorative_sub_uses_drawn_cash_effect_not_zero_reward(self):
        # The shipped grid includes rest/navi game cells. Previously the chosen
        # bonus's text was shown but the decorative sub reached the reward switch.
        for sub in ('rest', 'navi', 'unknown_old_theme', None):
            for player in ('user', 'sakura'):
                with self.subTest(sub=sub, player=player):
                    g = self.game()
                    before = g.cash[player]
                    result, story, info = self.land(g, sub, player=player)
                    self.assertEqual(result['event'], 'bonus')
                    self.assertEqual(result['kind'], 'game_win')
                    self.assertEqual(result['amount'], engine.MONOPOLY_BONUS)
                    self.assertEqual(g.cash[player] - before, engine.MONOPOLY_BONUS)
                    self.assertEqual(info['event'], 'bonus')
                    self.assertTrue(info['won'])
                    self.assertEqual(info['amount'], result['amount'])
                    self.assertEqual(info['reward'], story[-1])
                    self.assertIn(str(engine.MONOPOLY_BONUS), info['reward'])
                    self.assertNotIn('+¥0', info['reward'])
                    self.assertEqual(info['player'], player)
                    self.assertEqual(info['roll'], 6)

    def test_fixed_known_sub_overrides_random_draw_consistently(self):
        for sub, amount in (('bonus', engine.MONOPOLY_BONUS),
                            ('cat', engine.MONOPOLY_CAT_GAIN),
                            ('tax', 0), ('monster', 0), ('train', engine.MONOPOLY_TRAIN_GAME_REWARD), ('dice_drop', 0)):
            with self.subTest(sub=sub):
                g = self.game()
                before = g.cash['user']
                result, story, info = self.land(g, sub, drawn='cat' if sub != 'cat' else 'bonus')
                self.assertEqual(result['event'], sub)
                self.assertEqual(info['event'], sub)
                self.assertEqual(result['amount'], amount)
                self.assertEqual(g.cash['user'] - before, amount)
                self.assertEqual(info['amount'], amount)
                self.assertEqual(info['reward'], story[-1])
                self.assertEqual(info['name'], next(e['name'] for e in engine.game_events() if e['key'] == sub))
                self.assertEqual(g.flags['user']['pick_dice'], sub == 'dice_drop')
                if sub in ('tax', 'monster'):
                    self.assertIn('免' if sub == 'tax' else '省下', info['reward'])

    def test_train_win_pays_real_cash_and_failure_remains_free(self):
        for player in ('user', 'sakura'):
            for buff, factor in ((None, 1), ('gain', 2)):
                g = self.game()
                g.flags[player]['buff'] = buff
                before = g.cash[player]
                result, story, info = self.land(g, 'train', player=player)
                amount = engine.MONOPOLY_TRAIN_GAME_REWARD * factor
                self.assertEqual(g.cash[player] - before, amount)
                self.assertEqual(result['amount'], amount)
                self.assertEqual(info['amount'], amount)
                self.assertIn(f'+¥{amount}', info['reward'])
                self.assertNotIn('省下', info['reward'])
                self.assertNotIn('免票', info['reward'])
                self.assertIn('她', info['reward'])
                before = g.cash[player]
                result, _, info = self.land(g, 'train', roll=1, player=player)
                self.assertEqual(g.cash[player], before)
                self.assertFalse(info['won'])
                self.assertEqual(result['amount'], 0)
        self.assertIn(str(engine.MONOPOLY_TRAIN_GAME_REWARD),
                      engine.cell_effect_text({'type': 'game', 'sub': 'train'}))

    def test_bonus_draw_respects_gain_buff_and_payload_actual_amount(self):
        g = self.game()
        g.flags['user']['buff'] = 'gain'
        before = g.cash['user']
        result, _, info = self.land(g, 'rest')
        amount = round(engine.MONOPOLY_BONUS * engine.MONOPOLY_SHOP_BUFF_GAIN)
        self.assertEqual(g.cash['user'] - before, amount)
        self.assertEqual(result['amount'], amount)
        self.assertEqual(info['amount'], amount)
        self.assertIn(str(amount), info['reward'])

    def test_failed_roll_never_pays_cash_or_grants_dice(self):
        for sub in ('rest', 'navi', 'bonus', 'cat', 'tax', 'monster', 'train', 'dice_drop'):
            with self.subTest(sub=sub):
                g = self.game()
                before = copy.deepcopy((g.cash, g.flags))
                result, _, info = self.land(g, sub, roll=3)
                self.assertEqual(result['kind'], 'game_lose')
                self.assertEqual((g.cash, g.flags), before)
                self.assertFalse(info['won'])
                self.assertEqual(info['amount'], 0)

    def test_success_threshold_and_held_die_cap(self):
        g = self.game()
        self.land(g, 'navi', drawn='dice_drop', roll=4)
        self.assertTrue(g.flags['user']['pick_dice'])
        before = g.cash['user']
        result, _, info = self.land(g, 'rest', drawn='dice_drop', roll=4)
        self.assertTrue(info['won'])
        self.assertTrue(g.flags['user']['pick_dice'])
        self.assertEqual(g.cash['user'], before)
        self.assertEqual(result['amount'], 0)
        self.assertIn('持有上限', info['reward'])
        self.assertEqual(info['seq'], 2)

    def test_real_roll_landing_awards_before_turn_handoff(self):
        g = self.game()
        node = g._moves[g.start_node][0]
        g.cells[node].update(type='game', sub='navi')
        events = engine.game_events()
        bonus = next(i for i, e in enumerate(events) if e['key'] == 'bonus')
        before = g.cash['user']
        with patch.object(g, '_dice_rng') as dice, patch.object(g, '_effect_rng') as effect:
            dice.return_value.randint.return_value = 1
            effect.return_value.randrange.return_value = bonus
            effect.return_value.randint.return_value = 6
            result = g.roll('user')
        self.assertIsNone(g.pending)
        self.assertEqual(g.turn, 'sakura')
        self.assertEqual(g.cash['user'] - before, engine.MONOPOLY_BONUS)
        self.assertEqual(g._last_landing['event'], 'bonus')
        self.assertIn(str(engine.MONOPOLY_BONUS), ''.join(result['story']))


if __name__ == '__main__':
    unittest.main(verbosity=2)
