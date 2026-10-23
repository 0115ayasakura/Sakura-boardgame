"""Optional single-slot dice regressions; isolated engine only, no service/deploy."""
from pathlib import Path
import copy
import sys
import unittest
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'sakura_boardgame'))
from engine import MonopolyGame, GameError, MONOPOLY_SHOP_DICE_PRICE, restore_game


def game():
    return MonopolyGame(size=28, dice_sides=6, seed=45, first='user', map_kind='grid')


class OptionalDiceTests(unittest.TestCase):
    def rejects_unchanged(self, g, operation):
        before = copy.deepcopy(g.to_dict())
        transient = copy.deepcopy(g._fx_items)
        with self.assertRaises(GameError):
            operation()
        self.assertEqual(g.to_dict(), before)
        self.assertEqual(g._fx_items, transient)

    def test_legacy_roll_random_preserves_die_and_rng(self):
        g, reference = game(), game()
        g.flags['user']['pick_dice'] = True
        result = g.roll('user')
        expected = reference.roll('user')
        self.assertIsInstance(result['dice'], int)
        self.assertEqual(result['dice'], expected['dice'])
        self.assertEqual(g._rolls, reference._rolls)
        self.assertTrue(g.flags['user']['pick_dice'])
        self.assertNotEqual((g.pending or {}).get('type'), 'dice')

    def test_explicit_picker_only_consumes_at_valid_selection(self):
        g = game()
        g.flags['user']['pick_dice'] = True
        result = g.roll('user', use_custom_die=True)
        self.assertIsNone(result['dice'])
        self.assertEqual(result['path'], [g.start_node])
        self.assertEqual(result['pending']['type'], 'dice')
        self.assertEqual(result['pending']['options'], ['1', '2', '3', '4', '5', '6'])
        self.assertTrue(g.flags['user']['pick_dice'])
        self.assertEqual(g._rolls, 0)
        counts = (g.move_count, g.dice_turn)
        for value in ('0', '7', 'abc', '1.5', None, True, 1.5):
            self.rejects_unchanged(g, lambda value=value: g.decide('user', value))
        self.rejects_unchanged(g, lambda: g.decide('sakura', '1'))
        with patch.object(g, '_continue_walk'):
            picked = g.decide('user', '3')
        self.assertEqual(picked['dice'], 3)
        self.assertEqual(g._walk['steps_left'], 3)
        self.assertFalse(g.flags['user']['pick_dice'])
        self.assertEqual((g.move_count, g.dice_turn), counts)
        self.assertEqual(g._rolls, 0)

    def test_no_die_rejected_before_any_action(self):
        g = game()
        g._record_fx('user', 'untouched')
        self.rejects_unchanged(g, lambda: g.roll('user', use_custom_die=True))
        self.assertEqual(g.move_count, 0)
        self.assertEqual(g.dice_turn, 0)

    def test_shop_single_slot_stale_repeat_never_charges(self):
        g = game()
        shop = next(c['id'] for c in g.cells if c['type'] == 'shop')
        g._enter_shop('user', shop, [])
        before = g.cash['user']
        g.decide('user', 'buy_dice')
        self.assertIs(g.flags['user']['pick_dice'], True)
        self.assertEqual(g.cash['user'], before - MONOPOLY_SHOP_DICE_PRICE)
        g.turn = 'user'
        story = []
        g._enter_shop('user', shop, story)
        self.assertNotIn('buy_dice', g.pending['options'])
        self.assertTrue(any('上限 1' in line for line in story))
        self.rejects_unchanged(g, lambda: g.decide('user', 'buy_dice'))
        # Even a legacy/stale pending menu cannot overwrite and recharge the die.
        g.pending['options'].append('buy_dice')
        self.rejects_unchanged(g, lambda: g.decide('user', 'buy_dice'))

    def test_duplicate_game_reward_has_no_stack_or_compensation(self):
        g = game()
        before = g.cash['user']
        for _ in range(2):
            story = []
            g._apply_game_reward('user', 'dice_drop', 'dice', '获得骰子', 6, story)
            self.assertIs(g.flags['user']['pick_dice'], True)
        self.assertTrue(any('上限 1' in line for line in story))
        self.assertEqual(g.cash['user'], before)

    def black_game(self, held=True):
        g = game()
        g.flags['user']['pick_dice'] = held
        g.start_black_train()
        return g

    def test_black_ordinary_roll_uses_random_without_spending_wallet(self):
        g = self.black_game()
        result = g.roll('user')
        self.assertIsInstance(result['dice'], int)
        self.assertTrue(g.black_train['players']['user']['custom_die'])
        self.assertNotEqual((g.pending or {}).get('type'), 'dice')
        self.assertEqual(g.black_train['actions_left'], 10)
        self.assertEqual(g.black_train['actions_taken']['user'], 0)
        self.assertEqual(g.move_count, 0)

    def test_black_wallet_not_normal_flags_is_authority(self):
        g = self.black_game()
        wallet = g.black_train['players']['user']
        wallet['custom_die'] = False
        self.assertTrue(g.flags['user']['pick_dice'])
        self.rejects_unchanged(g, lambda: g.roll('user', use_custom_die=True))
        wallet['custom_die'] = True
        g.flags['user']['pick_dice'] = False
        result = g.roll('user', use_custom_die=True)
        self.assertEqual(result['pending']['type'], 'dice')
        self.assertTrue(wallet['custom_die'])

    def test_black_contract_lock_rejects_without_action_or_consumption(self):
        g = self.black_game()
        wallet = g.black_train['players']['user']
        wallet['lock'] = wallet['dice_lock'] = 2
        self.rejects_unchanged(g, lambda: g.roll('user', use_custom_die=True))
        self.assertTrue(wallet['custom_die'])
        g.roll('user')
        self.assertEqual(wallet['lock'], 2)  # Event completion, not roll, ticks contracts.

    def test_black_picker_selection_and_action_timing(self):
        g = self.black_game()
        wallet = g.black_train['players']['user']
        result = g.roll('user', use_custom_die=True)
        self.assertEqual(result['pending']['type'], 'dice')
        self.assertTrue(wallet['custom_die'])
        self.assertEqual(g.black_train['actions_left'], 10)
        self.assertEqual(g._rolls, 0)
        for value in ('0', '7', 'nonsense'):
            self.rejects_unchanged(g, lambda value=value: g.decide('user', value))
        result = g.decide('user', '1')
        self.assertEqual(result['dice'], 1)
        self.assertFalse(wallet['custom_die'])
        self.assertFalse(g.black_train['saved']['flags']['user']['pick_dice'])
        while g.pending and g.pending['type'] == 'route':
            g.decide('user', str(g.pending['options'][0]))
        self.assertEqual(g.pending['kind'], 'black_train')
        self.assertEqual(g.black_train['actions_left'], 10)
        g.decide('user', g.pending['options'][0])
        self.assertEqual(g.black_train['actions_left'], 9)
        self.assertEqual(g.black_train['actions_taken']['user'], 1)
        self.assertEqual(g.dice_turn, 1)
        self.assertEqual(g.move_count, 0)

    def test_save_restore_pending_picker_keeps_optional_consumption(self):
        for black in (False, True):
            g = self.black_game() if black else game()
            g.flags['user']['pick_dice'] = True
            g.roll('user', use_custom_die=True)
            restored = restore_game(g.to_dict())
            self.assertEqual(restored.pending['type'], 'dice')
            self.rejects_unchanged(restored, lambda: restored.decide('user', '0'))
            with patch.object(restored, '_continue_walk'):
                restored.decide('user', '6')
            held = restored.black_train['players']['user']['custom_die'] if black else restored.flags['user']['pick_dice']
            self.assertFalse(held)


if __name__ == '__main__':
    unittest.main(verbosity=2)
