"""Cash classification and duration refactor: preserve existing rule asymmetries."""
from pathlib import Path
import copy
import sys
import unittest
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'sakura_boardgame'))
import engine
from economy import CashCategory as C, cash_amount
from effect_policy import EFFECT_POLICY, acquire_duration, consume_effect, tick_completed_action


def game():
    return engine.MonopolyGame(size=28, dice_sides=6, seed=45, first='user', map_kind='grid')


class EconomyPolicyTests(unittest.TestCase):
    def test_policy_matrix_all_categories_legacy_and_adventure(self):
        for buff, adventure in ((None, False), ('gain', False), ('loss', False),
                                ('loss_up', False), (None, True), ('gain', True),
                                ('loss', True), ('loss_up', True)):
            income = 2 if buff == 'gain' else (1.25 if adventure else 1)
            passive = {'loss': .5, 'loss_up': 1.5}.get(buff, 1)
            if adventure:
                passive = max(passive, 1.25)
            expected = {C.INCOME: income, C.RENT_RECEIPT: income if adventure else 1,
                        C.ACTIVE_PRICE: 1, C.PASSIVE_EXPENSE: passive,
                        C.ASSET_VOLUNTARY: income, C.ASSET_FORCED: 1,
                        C.ADVENTURE_SETTLEMENT: 1}
            for category, multiplier in expected.items():
                for amount in (0, 50, 150, -50, -150):
                    with self.subTest(buff=buff, adventure=adventure, category=category, amount=amount):
                        self.assertEqual(cash_amount(amount, category, {'buff': buff},
                            adventure_active=adventure, gain_rate=2, loss_rate=.5,
                            loss_up_rate=1.5, adventure_rate=1.25), round(amount * multiplier))

    def test_unclassified_and_double_quote_bypass_rejected(self):
        g = game()
        with self.assertRaises(ValueError):
            g._post_cash('user', 100, 'unknown')
        with self.assertRaises(ValueError):
            g._post_cash('user', 100, C.INCOME, already_quoted=True)

    def test_posting_does_not_tick_or_consume_items(self):
        g = game()
        g.flags['user'].update(buff='gain', buff_left=3, adventure_left=2,
                               rent_shield=True, upgrade_coupon=True)
        flags = copy.deepcopy(g.flags)
        before = g.cash['user']
        self.assertEqual(g._post_cash('user', -50, C.ACTIVE_PRICE), -50)
        self.assertEqual(g._post_cash('user', 300, C.ADVENTURE_SETTLEMENT), 300)
        self.assertEqual(g.cash['user'], before + 250)
        self.assertEqual(g.flags, flags)

    def test_passive_due_quoted_once_then_shielded_and_paid(self):
        g = game()
        g.flags['user'].update(buff='loss_up', adventure_left=2)
        g.flags['sakura'].update(buff='gain', adventure_left=2)
        before = dict(g.cash)
        result = g._pay('user', 150, 'sakura', [], rent_shield=True)
        self.assertEqual(result, {'paid': 112, 'due': 112, 'bankrupt': False})
        self.assertEqual(g.cash['user'], before['user'] - 112)
        self.assertEqual(g.cash['sakura'], before['sakura'] + 224)

    def test_rent_legacy_gain_alone_does_not_amplify(self):
        g = game()
        g.flags['sakura'].update(buff='gain', buff_left=3)
        before = g.cash['sakura']
        g._pay('user', 75, 'sakura', [])
        self.assertEqual(g.cash['sakura'], before + 75)

    def test_forced_liquidation_not_multiplied_and_partial_debt_not_requoted(self):
        g = game()
        cell = next(c for c in g.cells if c['type'] == 'property')
        cell.update(owner='user', mortgaged=False)
        g.cash['user'] = 0
        g.flags['user'].update(buff='gain', adventure_left=2)
        value = g.current_price(cell) // 2
        result = g._pay('user', 10000, None, [])
        self.assertEqual(result['paid'], value)
        self.assertEqual(result['due'], 12500)
        self.assertTrue(result['bankrupt'])
        self.assertEqual(g.cash['user'], 0)

    def test_voluntary_liquidation_keeps_existing_income_multiplier(self):
        for action in ('mortgage', 'sell'):
            g = game()
            cell = next(c for c in g.cells if c['type'] == 'property')
            cell.update(owner='user', mortgaged=False)
            g.flags['user'].update(buff='gain', buff_left=3)
            g.pending = {'type': 'property', 'player': 'user', 'cell': cell['id'], 'options': [action]}
            value = g.current_price(cell) // (2 if action == 'mortgage' else 3)
            before = g.cash['user']
            g.decide('user', action)
            self.assertEqual(g.cash['user'], before + value * 2)

    def test_train_pauses_adventure_but_settlement_is_always_unmultiplied(self):
        g = game()
        g.flags['user'].update(buff='gain', buff_left=3, adventure_left=2)
        with patch.object(g, '_black_train_active', return_value=True):
            self.assertFalse(g._adventure_active('user'))
            self.assertEqual(g._post_cash('user', 300, C.ADVENTURE_SETTLEMENT), 300)
            before = copy.deepcopy(g.flags)
            g._tick_buff('user', [])
            self.assertEqual(g.flags, before)


class EffectLifecycleTests(unittest.TestCase):
    def test_acquisition_compensates_current_completion_then_exact_future_duration(self):
        flags = {'buff': 'gain'}
        acquire_duration(flags, 'adventure_left', 2)
        acquire_duration(flags, 'buff_left', 3)
        self.assertEqual((flags['adventure_left'], flags['buff_left']), (3, 4))
        self.assertEqual(tick_completed_action(flags, in_train=False), [])
        self.assertEqual((flags['adventure_left'], flags['buff_left']), (2, 3))
        tick_completed_action(flags, in_train=False)
        self.assertEqual(len(tick_completed_action(flags, in_train=False)), 1)
        self.assertEqual(flags['adventure_left'], 0)
        self.assertEqual(flags['buff'], 'gain')
        self.assertEqual(len(tick_completed_action(flags, in_train=False)), 1)
        self.assertEqual(flags['buff_left'], 0)
        self.assertIsNone(flags['buff'])
        self.assertEqual(tick_completed_action(flags, in_train=False), [])

    def test_pause_and_one_shot_items_never_expire_by_duration_tick(self):
        flags = {'buff': 'loss', 'buff_left': 2, 'adventure_left': 1,
                 'rent_shield': True, 'upgrade_coupon': True, 'pick_dice': True, 'lucky_charm': True}
        before = dict(flags)
        self.assertEqual(tick_completed_action(flags, in_train=True), [])
        self.assertEqual(flags, before)
        tick_completed_action(flags, in_train=False)
        for key in ('rent_shield', 'upgrade_coupon', 'pick_dice', 'lucky_charm'):
            self.assertTrue(flags[key])
            self.assertIn('consume', EFFECT_POLICY[key])
        with self.assertRaises(ValueError):
            acquire_duration(flags, 'pick_dice', 2)

    def test_one_shots_require_explicit_matching_operation_trigger(self):
        flags = {key: True for key in ('rent_shield', 'upgrade_coupon', 'lucky_charm', 'pick_dice')}
        for key in list(flags):
            with self.assertRaises(ValueError):
                consume_effect(flags, key, trigger='ordinary_turn_end')
            self.assertTrue(flags[key])
            self.assertTrue(consume_effect(flags, key, trigger=EFFECT_POLICY[key]['consume']))
            self.assertFalse(consume_effect(flags, key, trigger=EFFECT_POLICY[key]['consume']))
            self.assertFalse(flags[key])

    def test_skip_preserves_existing_no_tick_semantics(self):
        g = game()
        g.flags['user'].update(buff='gain', buff_left=3, adventure_left=2)
        before = copy.deepcopy(g.flags)
        g.skip_pending['user'] = True
        result = g.roll('user')
        self.assertTrue(result['skipped'])
        self.assertEqual(g.flags, before)

    def test_custom_picker_and_route_do_not_tick_before_completion(self):
        g = game()
        g.flags['user'].update(buff='gain', buff_left=3, adventure_left=2, pick_dice=True)
        g.positions['user'] = 8
        g.last_step['user'] = [7, 8]
        before = copy.deepcopy(g.flags)
        g.roll('user', use_custom_die=True)
        self.assertEqual(g.flags, before)
        g.decide('user', '2')
        self.assertEqual(g.pending['type'], 'route')
        self.assertEqual(g.flags['user']['adventure_left'], 2)
        self.assertEqual(g.flags['user']['buff_left'], 3)

    def test_save_restore_retains_duration_without_clock_progress(self):
        g = game()
        g.flags['user'].update(buff='loss_up', buff_left=2, adventure_left=1, lucky_charm=True)
        restored = engine.MonopolyGame.from_dict(g.to_dict())
        self.assertEqual(restored.flags, g.flags)
        story = []
        restored._tick_buff('user', story)
        self.assertEqual(restored.flags['user']['buff_left'], 1)
        self.assertEqual(restored.flags['user']['adventure_left'], 0)
        self.assertEqual(len(story), 1)


if __name__ == '__main__':
    unittest.main()
