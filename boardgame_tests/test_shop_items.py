"""Deterministic ordinary-shop inventory/economy regressions (engine only)."""
from pathlib import Path
import copy
import sys
import unittest
from unittest.mock import Mock, patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'sakura_boardgame'))
from engine import (MonopolyGame, GameError, SHOP_ITEM_POOL, SHOP_ITEM_FLAG_DEFAULTS,
                    MONOPOLY_SHOP_DRAW_PRICE, shop_items, pass_start)


def game():
    return MonopolyGame(28, 6, 45, map_kind='grid')


def shop(g, player='user'):
    g.turn = player
    index = next(c['id'] for c in g.cells if c['type'] == 'shop')
    g._enter_shop(player, index, [])
    return index


def property_cell(g, owner='sakura'):
    c = next(c for c in g.cells if c['type'] == 'property')
    c.update(owner=owner, mortgaged=False, level=0)
    return c


class ShopItemTests(unittest.TestCase):
    def test_catalog_prices_and_no_dice_changes(self):
        items = shop_items()
        self.assertEqual(items['draw']['price'], 100)
        self.assertEqual(items['dice']['price'], 180)
        self.assertEqual(items['gain']['price'], 200)
        self.assertEqual(len(items['draw']['pool']), 4)
        self.assertTrue(all(i['note'] for i in items['draw']['pool']))

    def test_every_uniform_pool_index_and_sakura_purchase(self):
        for player in ('user', 'sakura'):
            for index, item in enumerate(SHOP_ITEM_POOL):
                g = game()
                shop(g, player)
                rng = Mock()
                rng.randrange.return_value = index
                before = g.cash[player]
                with patch.object(g, '_effect_rng', return_value=rng):
                    result = g.decide(player, 'buy_draw')
                rng.randrange.assert_called_once_with(4)
                self.assertEqual(g.cash[player], before - MONOPOLY_SHOP_DRAW_PRICE)
                self.assertEqual(result['itemDraw']['key'], item['key'])
                self.assertEqual(result['itemDraw']['player'], player)
                self.assertEqual(result['itemDraw']['seq'], 1)
                self.assertEqual(result['itemDraw']['note'], item['note'])
                if item['key'] == 'adventure':
                    self.assertEqual(g.flags[player]['adventure_left'], 2)
                else:
                    self.assertIs(g.flags[player][item['key']], True)
                self.assertFalse(g.flags[player]['pick_dice'])
                self.assertEqual(g._rolls, 0)

    def test_pool_excludes_held_including_active_contract_and_full_cap(self):
        g = game()
        for item in SHOP_ITEM_POOL:
            if item['key'] == 'adventure':
                g.flags['user']['adventure_left'] = 2
            else:
                g.flags['user'][item['key']] = True
        shop(g)
        self.assertNotIn('buy_draw', g.pending['options'])
        # A stale client cannot charge or mutate the RNG when all slots are full.
        g.pending['options'].append('buy_draw')
        before = copy.deepcopy(g.to_dict())
        with self.assertRaises(GameError):
            g.decide('user', 'buy_draw')
        self.assertEqual(g.to_dict(), before)
        g.flags['user']['lucky_charm'] = False
        shop(g)
        rng = Mock()
        rng.randrange.return_value = 0
        with patch.object(g, '_effect_rng', return_value=rng):
            g.decide('user', 'buy_draw')
        rng.randrange.assert_called_once_with(1)
        self.assertTrue(g.flags['user']['lucky_charm'])

    def test_affordability_and_stale_balance(self):
        g = game()
        g.cash['user'] = 99
        shop(g)
        self.assertNotIn('buy_draw', g.pending['options'])
        g.pending['options'].append('buy_draw')
        before = copy.deepcopy(g.to_dict())
        with self.assertRaises(GameError):
            g.decide('user', 'buy_draw')
        self.assertEqual(g.to_dict(), before)

    def test_shield_only_actual_rent_after_multiplier(self):
        g = game()
        c = property_cell(g)
        g.flags['user'].update(rent_shield=True, rent_free=True, adventure_left=2)
        g._resolve_landing('user', c['id'], True, [])
        self.assertTrue(g.flags['user']['rent_shield'])
        c['mortgaged'] = True
        g._resolve_landing('user', c['id'], True, [])
        self.assertTrue(g.flags['user']['rent_shield'])
        g._pay('user', 80, None, [])
        self.assertTrue(g.flags['user']['rent_shield'])
        c['mortgaged'] = False
        rent = g._rent(c)
        before = dict(g.cash)
        result = g._resolve_landing('user', c['id'], True, [])
        expected = max(1, round(round(rent * 1.25) * .5))
        self.assertEqual(result['amount'], expected)
        self.assertEqual(g.cash['sakura'] - before['sakura'], expected)
        self.assertFalse(g.flags['user']['rent_shield'])
        # No actual cash can be paid; do not consume a shield on a zero payment.
        h = game()
        c = property_cell(h)
        h.cash['user'] = 0
        h.flags['user']['rent_shield'] = True
        h._resolve_landing('user', c['id'], True, [])
        self.assertTrue(h.flags['user']['rent_shield'])

    def test_coupon_menu_affordability_cap_and_single_use(self):
        for price in (120, 400):
            g = game()
            c = property_cell(g, 'user')
            c['price'] = price
            g.flags['user'].update(upgrade_coupon=True, adventure_left=2)
            base = g.current_price(c) // 2
            expected = base - min(60, base // 2)
            g.cash['user'] = expected
            g._resolve_landing('user', c['id'], True, [])
            self.assertIn('upgrade', g.pending['options'])
            self.assertEqual(g.pending['upgradeCost'], expected)
            g.decide('user', 'upgrade')
            self.assertEqual(g.cash['user'], 0)
            self.assertFalse(g.flags['user']['upgrade_coupon'])
            self.assertEqual(c['level'], 1)
            self.assertNotIn('upgrade', g._own_property_options('user', c))

    def test_contract_income_expense_max_and_plain_rent_compatibility(self):
        g = game()
        f = g.flags['user']
        f['adventure_left'] = 2
        self.assertEqual(g._gain('user', 80), 100)
        self.assertEqual(g._pay('user', 80, None, [])['paid'], 100)
        f['buff'] = 'gain'
        self.assertEqual(g._gain('user', 80), 160)
        f['buff'] = 'loss_up'
        self.assertEqual(g._pay('user', 80, None, [])['paid'], 120)
        f['buff'] = 'loss'
        self.assertEqual(g._pay('user', 80, None, [])['paid'], 100)
        # Incoming rent has its own recipient multiplier, never payer*recipient stacked twice.
        f['buff'] = None
        before = g.cash['user']
        g._pay('sakura', 80, 'user', [])
        self.assertEqual(g.cash['user'] - before, 100)
        f['buff'] = 'gain'
        before = g.cash['user']
        g._pay('sakura', 80, 'user', [])
        self.assertEqual(g.cash['user'] - before, 160)
        f['adventure_left'] = 0
        before = g.cash['user']
        g._pay('sakura', 80, 'user', [])
        self.assertEqual(g.cash['user'] - before, 80)  # unchanged old gain-alone rent

    def test_salary_bonus_and_active_price_payments(self):
        g = game()
        g.flags['user']['adventure_left'] = 2
        before = g.cash['user']
        g._grant_salary('user', [])
        self.assertEqual(g.cash['user'] - before, round(pass_start('grid') * 1.25))
        bonus = next(c for c in g.cells if c['type'] not in ('start', 'end'))
        original = copy.deepcopy(bonus)
        bonus['type'] = 'bonus'
        self.assertEqual(g._resolve_landing('user', bonus['id'], True, [])['amount'], round(150 * 1.25))
        bonus.update(original)
        shop(g)
        before = g.cash['user']
        g.decide('user', 'buy_dice')
        self.assertEqual(before - g.cash['user'], 180)
        c = property_cell(g, None)
        price = g.current_price(c)
        g.turn = 'user'
        g.flags['user']['adventure_left'] = 2
        g._resolve_landing('user', c['id'], True, [])
        before = g.cash['user']
        g.decide('user', 'buy')
        self.assertEqual(before - g.cash['user'], price)

    def test_lucky_retry_exactly_once_accepts_second_or_preserves_on_win(self):
        for rolls in ((1, 2), (1, 6), (6,)):
            g = game()
            c = next(c for c in g.cells if c['type'] == 'game')
            c['sub'] = 'bonus'
            g.flags['user']['lucky_charm'] = True
            rng = Mock()
            rng.randrange.return_value = 0
            rng.randint.side_effect = rolls
            with patch.object(g, '_effect_rng', return_value=rng):
                result = g._play_game_cell('user', c['id'], [])
            self.assertEqual(result['roll'], rolls[-1])
            self.assertEqual(result['rolls'], list(rolls))
            self.assertEqual(result['rerolled'], len(rolls) == 2)
            self.assertEqual(rng.randint.call_count, len(rolls))
            self.assertEqual(g.flags['user']['lucky_charm'], len(rolls) == 1)
            self.assertEqual(g._last_landing['rolls'], list(rolls))
            self.assertEqual(g._last_landing['won'], rolls[-1] >= 4)

    def test_contract_ticks_only_complete_ordinary_action_and_black_snapshot(self):
        g = game()
        g.flags['user']['adventure_left'] = 2
        g.flags['sakura']['adventure_left'] = 2
        shop(g)
        self.assertEqual(g.flags['user']['adventure_left'], 2)
        g.decide('user', 'skip')
        self.assertEqual(g.flags['user']['adventure_left'], 1)
        self.assertEqual(g.flags['sakura']['adventure_left'], 2)
        g.turn = 'user'
        g.start_black_train()
        frozen = copy.deepcopy(g.flags)
        g._tick_buff('user', [])
        self.assertEqual(g.flags, frozen)
        restored = MonopolyGame.from_dict(copy.deepcopy(g.to_dict()))
        self.assertEqual(restored.black_train['saved']['flags'], frozen)
        # Drive the actual return path through all ten train decisions.
        for _ in range(10):
            p = restored.turn
            restored._black_train_landing({'player': p, 'path': [restored.positions[p]]}, [])
            restored.decide(p, restored.pending['options'][-1])
        self.assertFalse(restored._black_train_active())
        self.assertEqual(restored.flags['user']['adventure_left'], 1)
        restored._tick_buff('user', [])
        self.assertEqual(restored.flags['user']['adventure_left'], 0)

    def test_picker_decisions_do_not_tick_until_action_complete(self):
        g = game()
        g.flags['user'].update(adventure_left=2, pick_dice=True, buff='gain', buff_left=2)
        g.roll('user', use_custom_die=True)
        self.assertEqual(g.flags['user']['adventure_left'], 2)
        self.assertEqual(g.flags['user']['buff_left'], 2)
        # Opening/choosing a die is not a completed movement or decision chain.
        with patch.object(g, '_continue_walk'):
            g.decide('user', '2')
        self.assertEqual(g.flags['user']['adventure_left'], 2)
        self.assertEqual(g.flags['user']['buff_left'], 2)
        g._walk = None
        shop(g)
        g.decide('user', 'skip')
        self.assertEqual(g.flags['user']['adventure_left'], 1)
        self.assertEqual(g.flags['user']['buff_left'], 1)
        g.turn = 'user'
        shop(g)
        g.decide('user', 'skip')
        self.assertEqual(g.flags['user']['adventure_left'], 0)
        self.assertEqual(g.flags['user']['buff_left'], 0)
        self.assertIsNone(g.flags['user']['buff'])

    def test_old_save_defaults_and_draw_seq_persistence_rng_resume(self):
        g = game()
        old = copy.deepcopy(g.to_dict())
        old.pop('item_draw_seq')
        old.pop('lastItemDraw')
        for flags in old['flags'].values():
            for key in SHOP_ITEM_FLAG_DEFAULTS:
                flags.pop(key)
        restored = MonopolyGame.from_dict(old)
        for p in ('user', 'sakura'):
            for key, default in SHOP_ITEM_FLAG_DEFAULTS.items():
                self.assertEqual(restored.flags[p][key], default)
        shop(restored)
        restored.decide('user', 'buy_draw')
        saved = copy.deepcopy(restored.to_dict())
        again = MonopolyGame.from_dict(saved)
        self.assertEqual(again.to_dict()['lastItemDraw'], restored._last_item_draw)
        for target in (restored, again):
            shop(target)
            target.decide('user', 'buy_draw')
        self.assertEqual(again._last_item_draw, restored._last_item_draw)
        self.assertEqual(again._last_item_draw['seq'], 2)
        self.assertNotEqual(again._last_item_draw['key'], saved['lastItemDraw']['key'])


if __name__ == '__main__':
    unittest.main()
