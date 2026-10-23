"""Isolated black-train v3 rules/legacy regression; no live services or saves."""
from pathlib import Path
import copy
import json
import sys
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'sakura_boardgame'))
from engine import MonopolyGame, GameError, restore_game
from black_train import EVENTS, LEGACY_EVENTS, RULES_VERSION, LAYOUT


def game(first='user', kind='grid'):
    return MonopolyGame(size=28, dice_sides=6, seed=45, first=first, map_kind=kind)


def rejects_unchanged(g, operation):
    before = copy.deepcopy(g.to_dict())
    try:
        operation()
    except GameError:
        assert g.to_dict() == before
    else:
        raise AssertionError('Expected GameError')


def encounter(g):
    actor = g.turn
    g.roll(actor)
    while g.pending and g.pending['type'] == 'route':
        left = g.black_train['actions_left']
        g.decide(actor, str(g.pending['options'][0]))
        assert g.black_train['actions_left'] == left
    assert g.pending['kind'] == 'black_train'
    assert all(isinstance(o, str) for o in g.pending['options'])
    assert_landing_event(g, g.positions[actor])
    return actor


def finish_action(g, option=0):
    actor = encounter(g)
    return g.decide(actor, g.pending['options'][min(option, len(g.pending['options']) - 1)])


def land_on(g, node):
    actor = g.turn
    previous = g.positions[actor]
    if g.positions[g._other(actor)] == node:
        g.positions[g._other(actor)] = next(c['id'] for c in g.cells if c['id'] != node)
    g.positions[actor] = node
    g._walk = {'player': actor, 'path': [previous, node], 'steps_left': 0}
    g._finish_walk([])
    assert g.pending['kind'] == 'black_train'
    assert_landing_event(g, g.positions[actor])
    return actor


def force_event(g, event_id):
    events = EVENTS if g.black_train['rules_version'] == RULES_VERSION else LEGACY_EVENTS
    index = next(i for i, e in enumerate(events) if e['id'] == event_id)
    color = events[index]['color']
    g.black_train['deck'] = [index]
    node = next(c['id'] for c in g.cells if c['type'] != 'end'
                and g.black_train['tiles'][str(c['id'])]['color'] == color)
    actor = land_on(g, node)
    assert g.pending['event']['id'] == event_id
    return actor


def choose_event(g, event_id, option=0):
    actor = force_event(g, event_id)
    action = option if isinstance(option, str) else g.pending['options'][option]
    return g.decide(actor, action)


def wallet(g, player=None):
    return g.black_train['players'][player or g.turn]


def fresh():
    g = game()
    g.start_black_train()
    return g


def assert_landing_event(g, node):
    tile = g.black_train['tiles'][str(node)]
    event = g.pending['event']
    assert event['cell_id'] == node
    assert (event['color'], event['category']) == (tile['color'], tile['category'])
    assert g.black_train['current_event'] == event


def test_ten_actions_both_first_players_and_maps():
    for first in ('user', 'sakura'):
        for kind in ('city', 'grid'):
            g = game(first, kind)
            cells = copy.deepcopy(g.cells)
            g.start_black_train()
            for index in range(10):
                actor = encounter(g)
                assert actor == (first if index % 2 == 0 else g._other(first))
                assert g.black_train['actions_left'] == 10 - index
                g.decide(actor, g.pending['options'][0])
            assert g.black_train['actions_taken'] == {'user': 5, 'sakura': 5}
            assert not g.black_train['active'] and not g.pending and not g._walk
            assert g.turn == first and g.winner is None and g.cells == cells
            assert g.black_train['outcome'] == 'safe_return'


def test_economy_frozen_and_restored():
    g = game()
    g.move_count = g.max_rounds - 1
    for p in ('user', 'sakura'):
        g.flags[p].update(salary_next=True, rent_free=True, dice_bonus=3,
                          pick_dice=True, buff='loss_up', buff_left=2)
        g.skip_pending[p] = True
    original = copy.deepcopy({k: getattr(g, k) for k in ('cash', 'cells', 'positions', 'flags', 'skip_pending', 'last_step')})
    g.start_black_train()
    for index in range(10):
        choose_event(g, 'gold_1', 1)
        for k in ('cells', 'flags', 'skip_pending'):
            assert getattr(g, k) == original[k]
        if index < 9:
            assert g.cash == original['cash']
        assert g.move_count == g.max_rounds - 1 and g.winner is None
    for k, value in original.items():
        if k != 'cash':
            assert getattr(g, k) == value
    for p in ('user', 'sakura'):
        assert g.cash[p] == original['cash'][p] + g.black_train['settlement'][p]['total']
    assert g.roll(g.turn)['skipped'] and g.move_count == g.max_rounds


def test_safe_start_invalid_choices_and_repeatable_preview():
    for attribute, value in [('winner', 'user'), ('pending', {'player': 'user'}),
                             ('_walk', {'player': 'user', 'steps_left': 1}), ('submap', {'x': 1})]:
        g = game()
        setattr(g, attribute, value)
        rejects_unchanged(g, g.start_black_train)
    g = fresh()
    rejects_unchanged(g, g.start_black_train)
    rejects_unchanged(g, lambda: g.roll('sakura'))
    actor = encounter(g)
    rejects_unchanged(g, lambda: g.roll(actor))
    rejects_unchanged(g, lambda: g.decide(g._other(actor), g.pending['options'][0]))
    rejects_unchanged(g, lambda: g.decide(actor, 'buy'))
    g.decide(actor, g.pending['options'][0])
    for _ in range(9):
        finish_action(g)
    g.start_black_train()
    assert g.black_train['actions_left'] == 10 and g.black_train['preview']


def test_pending_save_reload_and_rng_reproducibility():
    g = fresh()
    encounter(g)
    restored = restore_game(json.loads(json.dumps(g.to_dict())))
    for _ in range(10):
        if not g.pending:
            encounter(g)
            encounter(restored)
        assert g.pending == restored.pending
        action = g.pending['options'][0]
        g.decide(g.turn, action)
        restored.decide(restored.turn, action)
        assert g.black_train == restored.black_train
        assert g.positions == restored.positions and g.turn == restored.turn
    assert g._effect_rng().random() == restored._effect_rng().random()


def test_mid_route_restore_and_undo_snapshot():
    g = game(kind='city')
    g.start_black_train()
    before = copy.deepcopy(g.to_dict())
    g.roll(g.turn)
    restored = restore_game(json.loads(json.dumps(g.to_dict())))
    while g.pending['type'] == 'route':
        action = str(g.pending['options'][0])
        g.decide(g.turn, action)
        restored.decide(restored.turn, action)
    assert g.pending == restored.pending
    undone = restore_game(before)
    assert undone.black_train['actions_left'] == 10 and undone.pending is None
    assert undone._effect_draws == 0 and undone._rolls == 0


def test_layout_ids_counts_portal_and_no_rng_or_cell_changes():
    g = game()
    cells = copy.deepcopy(g.cells)
    g.start_black_train()
    tiles = g.black_train['tiles']
    assert len(tiles) == 57
    for color, ids in LAYOUT.items():
        assert {int(n) for n, t in tiles.items() if t['color'] == color} == set(ids)
    assert {color: sum(t['color'] == color for t in tiles.values()) for color in LAYOUT} == dict(gold=24, blue=11, white=12, black=10)
    assert tiles['0'] == tiles['30'] and tiles['29']['color'] == 'white'
    assert cells == g.cells and g._effect_draws == 0
    actor = land_on(g, 30)
    assert g.positions[actor] == 0
    assert_landing_event(g, 0)


def test_all_twelve_event_schema_and_choices_execute():
    assert len(EVENTS) == 12 and len({e['id'] for e in EVENTS}) == 12
    for event in EVENTS:
        assert len(event['choices']) >= 2
        assert all(c['action'] and c['effect_text'] for c in event['choices'])
        for index in range(len(event['choices'])):
            g = fresh()
            wallet(g).update(tickets=5, reward=100)
            force_event(g, event['id'])
            available = g.pending['options']
            g.decide(g.turn, available[min(index, len(available) - 1)])
            assert g.black_train['actions_left'] == 9
            assert g.black_train['history'][-1]['event'] == event['id']


def test_gold_ticket_branches_and_caps():
    for option, tickets, reward in [(0, 3, 0), (1, 2, 20)]:
        g = fresh()
        choose_event(g, 'gold_1', option)
        assert wallet(g, 'user')['tickets'] == tickets
        assert wallet(g, 'user')['reward'] == reward
    g = fresh()
    wallet(g).update(tickets=5, reward=295)
    choose_event(g, 'gold_1', 1)
    assert wallet(g, 'user')['reward'] == 300 and wallet(g, 'user')['tickets'] == 5
    assert g.black_train['history'][-1]['actual']['reward_delta'] == 5


def test_die_purchase_prices_affordability_and_cap_one():
    for event, field, price in [('gold_2', 'tickets', 3), ('white_1', 'reward', 60)]:
        for amount in (price - 1, price):
            g = fresh()
            wallet(g)[field] = amount
            force_event(g, event)
            if amount < price:
                assert '购买自定义骰' not in g.pending['options']
                rejects_unchanged(g, lambda: g.decide(g.turn, '购买自定义骰'))
            else:
                g.decide(g.turn, '购买自定义骰')
                assert wallet(g, 'user')['custom_die'] is True
                assert wallet(g, 'user')[field] == 0
                choose_event(g, 'gold_1')
                wallet(g)[field] = price
                force_event(g, event)
                assert '购买自定义骰' not in g.pending['options']


def test_gold_return_reward_no_cell_selection_and_ticket_alternative():
    for action, reward, tickets in [('领取归途酬谢', 30, 1), ('领取票根', 0, 2)]:
        g = fresh()
        force_event(g, 'gold_3')
        assert g.pending['options'] == ['领取归途酬谢', '领取票根']
        assert all(not c.get('target') and not c.get('recolor') for c in g.pending['event']['choices'])
        before_cells = copy.deepcopy(g.cells)
        before_tiles = copy.deepcopy(g.black_train['tiles'])
        actor = g.turn
        g.decide(actor, action)
        assert wallet(g, actor)['reward'] == reward
        assert wallet(g, actor)['tickets'] == tickets
        assert g.cells == before_cells and g.black_train['tiles'] == before_tiles
    g = fresh()
    wallet(g)['reward'] = 290
    choose_event(g, 'gold_3')
    assert wallet(g, 'user')['reward'] == 300


def test_contract_two_future_own_actions_lock_and_completion_after_payment():
    g = fresh()
    wallet(g)['custom_die'] = True
    choose_event(g, 'blue_1')
    assert wallet(g, 'user')['lock'] == 2 and not g._black_train_custom_die_available('user')
    choose_event(g, 'gold_1', 1)
    assert wallet(g, 'user')['lock'] == 2
    choose_event(g, 'gold_1', 1)
    assert wallet(g, 'user')['lock'] == 1 and wallet(g, 'user')['reward'] == 20
    choose_event(g, 'gold_1', 1)
    # 20 cannot pay a 25 conversion even though +30 will mature afterwards.
    force_event(g, 'white_3')
    assert '收益兑换双票' not in g.pending['options']
    g.decide(g.turn, '不兑换，继续')
    assert wallet(g, 'user')['lock'] == wallet(g, 'user')['dice_lock'] == 0
    assert wallet(g, 'user')['reward'] == 50 and wallet(g, 'user')['tickets'] == 4
    assert g.black_train['history'][-1]['actual']['contract_paid']
    assert g._black_train_custom_die_available('user')


def test_contract_not_drawn_with_less_than_two_future_actions():
    for taken in (3, 4):
        g = fresh()
        g.black_train['actions_taken']['user'] = taken
        g.black_train['deck'] = [3]
        node = next(int(n) for n, t in g.black_train['tiles'].items() if t['color'] == 'blue')
        land_on(g, node)
        assert g.pending['event']['id'] != 'blue_1' and 3 in g.black_train['deck']


def test_escrow_tickets_occupy_capacity_and_return_with_bonus():
    g = fresh()
    wallet(g)['tickets'] = 5
    choose_event(g, 'blue_2')
    assert wallet(g, 'user')['tickets'] == 4 and wallet(g, 'user')['escrow_tickets'] == 1
    choose_event(g, 'gold_1')
    choose_event(g, 'gold_1')
    assert wallet(g, 'user')['tickets'] == 4
    for _ in range(7):
        choose_event(g, 'blue_2', '拒绝契约')
    account = g.black_train['settlement']['user']
    assert account['tickets'] == 5 and account['reward'] == 30 and account['total'] == 230


def test_escrow_reward_is_unspendable_counts_cap_and_matures_once():
    g = fresh()
    wallet(g)['reward'] = 40
    choose_event(g, 'blue_3')
    account = wallet(g, 'user')
    assert account['reward'] == 0 and account['escrow_principal'] == 40 and account['escrow_reward'] == 60
    choose_event(g, 'blue_2', '拒绝契约')
    force_event(g, 'white_1')
    assert '购买自定义骰' not in g.pending['options']
    g.decide(g.turn, '领取票根')
    account['reward'] = 255
    choose_event(g, 'gold_1')
    choose_event(g, 'gold_1', 1)
    assert account['reward'] == 260
    for _ in range(5):
        choose_event(g, 'blue_2', '拒绝契约')
    assert g.black_train['settlement']['user']['reward'] == 300
    cash = copy.deepcopy(g.cash)
    restored = restore_game(json.loads(json.dumps(g.to_dict())))
    assert restored.cash == cash
    rejects_unchanged(restored, lambda: restored.decide(restored.turn, '封存40收益'))


def test_ward_persists_and_is_explicitly_used_not_automatic():
    g = fresh()
    choose_event(g, 'white_2')
    choose_event(g, 'gold_1')
    choose_event(g, 'gold_1', 1)
    assert wallet(g, 'user')['items'] == ['ward']
    choose_event(g, 'gold_1')
    choose_event(g, 'black_1', '承受影子侵蚀')
    assert wallet(g, 'user')['items'] == ['ward'] and wallet(g, 'user')['reward'] == 0
    choose_event(g, 'gold_1')
    wallet(g)['reward'] = 70
    force_event(g, 'black_2')
    old_position = g.positions[g.turn]
    assert '使用护符化解本次黑事件' in g.pending['options']
    g.decide(g.turn, '使用护符化解本次黑事件')
    assert wallet(g, 'user')['items'] == [] and wallet(g, 'user')['reward'] == 70
    assert g.positions['user'] == old_position
    assert g.black_train['history'][-1]['actual']['prevented']
    g = fresh()
    choose_event(g, 'white_2', 1)
    assert wallet(g, 'user')['reward'] == 10


def test_last_action_has_no_useless_future_options_but_die_can_return():
    for event, forbidden in [('white_2', '领取持久护符'), ('gold_3', '选择前方改色目标')]:
        g = fresh()
        g.black_train['actions_taken']['user'] = 4
        force_event(g, event)
        assert forbidden not in g.pending['options']
        assert not any(c.get('future') and c.get('available') for c in g.pending['event']['choices'])
    g = fresh()
    g.black_train['actions_taken']['user'] = 4
    wallet(g)['tickets'] = 3
    force_event(g, 'gold_2')
    assert '购买自定义骰' in g.pending['options']


def test_black_shadow_payment_no_die_and_floor():
    for reward in (0, 10, 50):
        g = fresh()
        wallet(g)['reward'] = reward
        choose_event(g, 'black_1', '承受影子侵蚀')
        assert wallet(g, 'user')['reward'] == max(0, reward - 25)
        assert not wallet(g, 'user')['custom_die']
    g = fresh()
    wallet(g)['reward'] = 50
    choose_event(g, 'black_1', '用票根驱散')
    assert wallet(g, 'user')['reward'] == 50 and wallet(g, 'user')['tickets'] == 0
    assert not wallet(g, 'user')['custom_die']
    choose_event(g, 'gold_1')
    force_event(g, 'black_1')
    assert g.pending['options'] == ['承受影子侵蚀']


def test_black_adjacent_visible_legal_no_recursive_event_and_stay():
    g = fresh()
    force_event(g, 'black_2')
    options = [c for c in g.pending['event']['choices'] if c.get('adjacent')]
    assert [c['target'] for c in options] == g._black_train_targets(g.turn, adjacent=True)
    assert options
    target = options[0]
    actor = g.turn
    g.decide(actor, target['action'])
    assert g.positions[actor] == target['target'] and g.pending is None
    assert g.black_train['actions_left'] == 9 and len(g.black_train['history']) == 1
    g = fresh()
    force_event(g, 'black_2')
    old = g.positions[g.turn]
    g.decide(g.turn, '交票留在原地')
    assert g.positions['user'] == old and wallet(g, 'user')['tickets'] == 0
    # Even an isolated legal-road test double cannot deadlock a zero-ticket player.
    g = fresh()
    wallet(g)['tickets'] = 0
    g._black_train_targets = lambda player, adjacent=False: []
    force_event(g, 'black_2')
    assert g.pending['options'] == ['无法执行，继续']
    g.decide(g.turn, g.pending['options'][0])


def test_conversion_both_directions_affordability_and_continue():
    for event, price in [('white_3', 25), ('black_3', 30)]:
        g = fresh()
        wallet(g).update(reward=price, tickets=1)
        choose_event(g, event, '收益兑换双票')
        assert wallet(g, 'user')['tickets'] == 3 and wallet(g, 'user')['reward'] == 0
        g = fresh()
        wallet(g).update(reward=price - 1, tickets=0)
        force_event(g, event)
        assert '收益兑换双票' not in g.pending['options']
        if event == 'white_3':
            assert g.pending['options'] == ['不兑换，继续']
        else:
            g.decide(g.turn, '承担归途损失')
            assert wallet(g, 'user')['reward'] == price - 16
    g = fresh()
    choose_event(g, 'white_3', '一票兑换收益')
    assert wallet(g, 'user')['reward'] == 35 and wallet(g, 'user')['tickets'] == 0


def test_die_entry_no_duplication_consumption_and_return_merge():
    for existing, consume, purchase in [(True, False, False), (True, True, False), (False, False, True), (True, True, True)]:
        g = game()
        g.flags['user']['pick_dice'] = existing
        g.start_black_train()
        assert wallet(g)['custom_die'] is existing
        if consume:
            g._black_train_use_custom_die('user')
            assert not wallet(g)['custom_die']
            assert not g.black_train['saved']['flags']['user']['pick_dice']
            rejects_unchanged(g, lambda: g._black_train_use_custom_die('user'))
            g = restore_game(json.loads(json.dumps(g.to_dict())))
            assert not wallet(g)['custom_die']
            assert not g.black_train['saved']['flags']['user']['pick_dice']
        if purchase:
            wallet(g)['tickets'] = 3
            choose_event(g, 'gold_2', '购买自定义骰')
        for _ in range(g.black_train['actions_left']):
            choose_event(g, 'gold_1', 1)
        assert g.flags['user']['pick_dice'] is (purchase or (existing and not consume))
        assert g.black_train['settlement']['user']['custom_die'] == g.flags['user']['pick_dice']


def test_engine_custom_die_default_random_opt_in_and_contract_rejection():
    g = fresh()
    wallet(g)['custom_die'] = True
    g.roll('user')
    assert wallet(g, 'user')['custom_die'] and g._rolls == 1
    while g.pending['type'] == 'route':
        g.decide('user', str(g.pending['options'][0]))
    g.decide('user', g.pending['options'][0])
    choose_event(g, 'gold_1')
    rolls = g._rolls
    g.roll('user', use_custom_die=True)
    assert g.pending['type'] == 'dice' and wallet(g)['custom_die'] and g._rolls == rolls
    rejects_unchanged(g, lambda: g.decide('user', '99'))
    g.decide('user', '1')
    assert not wallet(g, 'user')['custom_die']
    g = fresh()
    wallet(g)['custom_die'] = True
    choose_event(g, 'blue_1')
    choose_event(g, 'gold_1')
    rejects_unchanged(g, lambda: g.roll('user', use_custom_die=True))
    assert wallet(g)['lock'] == 2 and g.black_train['actions_left'] == 8


def test_decks_shared_color_scanning_exhaustion_and_off_color_retention():
    g = fresh()
    g.black_train['deck'] = [0, 3, 1, 11]
    actor = land_on(g, 0)
    assert g.pending['event']['id'] == 'gold_2' and g.black_train['deck'] == [0, 3, 11]
    assert g._effect_draws == 0
    g.decide(actor, '领取票根')
    land_on(g, 2)
    assert g.pending['event']['id'] == 'blue_1' and g.black_train['deck'] == [0, 11]
    for color in LAYOUT:
        g = fresh()
        node = next(c['id'] for c in g.cells if c['type'] != 'end' and g.black_train['tiles'][str(c['id'])]['color'] == color)
        ids = []
        for _ in range(3):
            actor = land_on(g, node)
            ids.append(g.pending['event']['id'])
            g.decide(actor, g.pending['options'][-1])
        assert set(ids) == {e['id'] for e in EVENTS if e['color'] == color}
        assert g.black_train['deck'] == []


def test_final_black_adjacent_history_keeps_event_origin_and_actual_position():
    g = fresh()
    for _ in range(9):
        choose_event(g, 'gold_1', 1)
    force_event(g, 'black_2')
    actor = g.turn
    origin = g.positions[actor]
    choice = next(c for c in g.pending['event']['choices'] if c.get('adjacent') and c['available'])
    g.decide(actor, choice['action'])
    row = g.black_train['history'][-1]
    assert row['cell_id'] == origin
    assert row['actual']['position'] == row['actual']['target'] == choice['target']
    assert g.positions[actor] == g.start_node  # ordinary position restored on return
    g = fresh()
    force_event(g, 'gold_3')
    origin = g.positions[g.turn]
    g.decide(g.turn, '领取归途酬谢')
    assert g.black_train['history'][-1]['actual']['position'] == origin
    assert g.black_train['history'][-1]['actual']['target'] is None


def test_contract_accepted_with_exactly_two_future_actions_matures_on_final_action():
    g = fresh()
    for _ in range(4):
        choose_event(g, 'gold_1', 1)
    choose_event(g, 'blue_1')
    choose_event(g, 'blue_1')
    assert wallet(g, 'user')['lock'] == wallet(g, 'sakura')['lock'] == 2
    choose_event(g, 'blue_2', '拒绝契约')
    choose_event(g, 'blue_2', '拒绝契约')
    assert wallet(g, 'user')['lock'] == wallet(g, 'sakura')['lock'] == 1
    choose_event(g, 'black_1', '承受影子侵蚀')
    assert g.black_train['history'][-1]['actual']['contract_paid']
    assert wallet(g, 'user')['reward'] == 45  # 40 -25 then +30
    choose_event(g, 'black_1', '承受影子侵蚀')
    assert all(s['reward'] == 45 for s in g.black_train['settlement'].values())


def test_unaffordable_escrow_and_multiple_seals_survive_reload():
    g = fresh()
    wallet(g).update(tickets=0, reward=39)
    force_event(g, 'blue_3')
    assert g.pending['options'] == ['拒绝契约']
    rejects_unchanged(g, lambda: g.decide(g.turn, '封存40收益'))
    g.decide(g.turn, '拒绝契约')
    force_event(g, 'blue_2')
    g.decide(g.turn, '封存一张票根')
    force_event(g, 'blue_2')
    assert g.pending['options'] == ['拒绝契约']
    g.decide(g.turn, '拒绝契约')
    choose_event(g, 'blue_2', '拒绝契约')
    wallet(g)['reward'] = 80
    choose_event(g, 'blue_3')
    choose_event(g, 'blue_2', '拒绝契约')
    choose_event(g, 'blue_3')
    assert wallet(g, 'user')['escrow_principal'] == 80
    assert wallet(g, 'user')['escrow_reward'] == 120
    restored = restore_game(json.loads(json.dumps(g.to_dict())))
    for _ in range(g.black_train['actions_left']):
        choose_event(g, 'blue_2', '拒绝契约')
        choose_event(restored, 'blue_2', '拒绝契约')
    assert g.black_train == restored.black_train and g.cash == restored.cash
    assert g.black_train['settlement']['user']['reward'] == 120


def test_ward_does_not_bypass_affordability_or_duplicate_and_expires_at_return():
    g = fresh()
    choose_event(g, 'white_2')
    choose_event(g, 'gold_1')
    force_event(g, 'white_2')
    assert '领取持久护符' not in g.pending['options']
    g.decide(g.turn, '领取即时收益')
    choose_event(g, 'gold_1')
    wallet(g).update(tickets=0, reward=0)
    force_event(g, 'black_3')
    assert '收益兑换双票' not in g.pending['options']
    assert '使用护符化解本次黑事件' in g.pending['options']
    g.decide(g.turn, '承担归途损失')
    for _ in range(5):
        choose_event(g, 'gold_1', 1)
    assert g.black_train['settlement']['user']['expired_items'] == ['ward']
    assert wallet(g, 'user')['items'] == []


def test_current_event_choices_have_matching_stories_and_female_conductor():
    assert all(e['story'] != old['story'] for e, old in zip(EVENTS, LEGACY_EVENTS))
    assert all('黑车掌' in e['story'] and '她' in e['story'] and '他' not in e['story'] for e in EVENTS)
    assert '两程' in EVENTS[3]['story'] and '本金' in EVENTS[5]['story']
    assert '后续' in EVENTS[7]['story'] and '相邻' in EVENTS[10]['story']


def test_effect_rng_separate_from_movement_rng():
    g, h = game(), game()
    for _ in range(20):
        g._effect_rng().randint(1, 20)
    assert g._rolls == h._rolls == 0
    for _ in range(12):
        g._rolls += 1
        h._rolls += 1
        assert g._dice_rng().randint(1, 6) == h._dice_rng().randint(1, 6)


def test_legacy_v2_pending_and_layout_preserved():
    g = fresh()
    g.black_train['rules_version'] = 'auditable-playtest-v2'
    g.black_train['tiles'] = g._black_train_tiles()
    for p in ('user', 'sakura'):
        g.black_train['players'][p] = {'reward': 0, 'tickets': 1, 'items': []}
    force_event(g, 'gold_1')
    assert g.pending['options'] == ['领取金票收益', '收好票根']
    restored = restore_game(json.loads(json.dumps(g.to_dict())))
    assert restored.black_train == g.black_train and restored.pending == g.pending
    restored.decide('user', '领取金票收益')
    assert wallet(restored, 'user')['reward'] == 40
    assert restored.black_train['tiles']['30']['color'] == 'white'
    for _ in range(9):
        choose_event(restored, 'gold_1', 0)
    assert restored.black_train['settlement']['user']['reward'] == 200
    restored.start_black_train()
    assert restored.black_train['rules_version'] == RULES_VERSION
    assert restored.black_train['tiles']['30']['color'] == 'gold'


def test_missing_tiles_migrate_by_saved_version_and_early_clue_save():
    for version in (RULES_VERSION, 'auditable-playtest-v2'):
        g = fresh()
        g.black_train['rules_version'] = version
        g.black_train['tiles'] = g._black_train_tiles()
        expected = g.black_train.pop('tiles')
        restored = restore_game(json.loads(json.dumps(g.to_dict())))
        restored.render()
        assert restored.black_train['tiles'] == expected
    g = fresh()
    g.black_train['rules_version'] = 'auditable-playtest-v2'
    g.black_train['tiles'] = g._black_train_tiles()
    force_event(g, 'white_1')
    del g.black_train['players']
    g.black_train.update(clues=3, goal=4, energy=5)
    del g.black_train['rules_version']
    g.pending['options'] = ['共同译码', '向巡检员求援']
    restored = restore_game(json.loads(json.dumps(g.to_dict())))
    cash = copy.deepcopy(restored.cash)
    restored.decide('user', '共同译码')
    assert restored.cash == cash and restored.black_train['legacy']['clues'] == 3
    assert wallet(restored, 'user')['items'] == ['lamp']


def finish_path(g, actor, path):
    g.positions[actor] = path[-1]
    g._walk = {'player': actor, 'path': list(path), 'steps_left': 0}
    story = []
    g._finish_walk(story)
    return story


def test_grid_collision_retreat_all_categories_both_players():
    for actor in ('user', 'sakura'):
        for color in LAYOUT:
            g = game(first=actor)
            g.start_black_train()
            back = next(c['id'] for c in g.cells if c['type'] not in ('start', 'end') and g.black_train['tiles'][str(c['id'])]['color'] == color)
            occupied = next(n for n in g._moves[back] if n != back)
            g.positions.update({actor: back, g._other(actor): occupied})
            g._block_seq = 7
            story = finish_path(g, actor, [back, occupied])
            assert g.positions[actor] == back and g.last_step[actor] is None
            assert g._last_walk['path'] == [back, occupied, back]
            assert g._last_walk['blockedAt'] == occupied and g._last_walk['blockSeq'] == 8
            assert any('同格挡路' in s for s in story)
            assert_landing_event(g, back)
            assert g.black_train['actions_left'] == 10
            g.decide(actor, g.pending['options'][0])
            assert g.black_train['actions_left'] == 9 and g.turn == g._other(actor)


def test_portal_occupancy_and_city_overlap_match_normal_navigation():
    for actor in ('user', 'sakura'):
        for occupied_end in (False, True):
            normal, preview = game(first=actor), game(first=actor)
            end = next(c['id'] for c in preview.cells if c['type'] == 'end')
            back = next(c['id'] for c in preview.cells if c['type'] not in ('start', 'end'))
            for g in (normal, preview):
                g.positions.update({actor: back, g._other(actor): end if occupied_end else g.start_node})
            preview.start_black_train()
            finish_path(normal, actor, [back, end])
            finish_path(preview, actor, [back, end])
            assert preview.positions == normal.positions and preview._last_walk == normal._last_walk
            assert preview.positions[actor] == (back if occupied_end else preview.start_node)
            assert_landing_event(preview, preview.positions[actor])
    g = game(kind='city')
    g.start_black_train()
    back, node = [c['id'] for c in g.cells if c['type'] not in ('start', 'end')][:2]
    g.positions['sakura'] = node
    finish_path(g, 'user', [back, node])
    assert not g._last_walk['blocked'] and g.last_step['user'] == [back, node]


def test_return_payout_personal_caps_and_no_double_credit():
    g = fresh()
    cash = copy.deepcopy(g.cash)
    for index in range(10):
        choose_event(g, 'gold_1', index % 2)
        if index < 9:
            assert g.cash == cash
    settlements = g.black_train['settlement']
    assert settlements['user']['total'] == 200
    assert settlements['sakura']['total'] == 300
    assert all(100 <= s['total'] <= 500 for s in settlements.values())
    assert all(g.cash[p] == cash[p] + s['total'] for p, s in settlements.items())
    assert '已安全返回' in g.summary() and '经营已恢复' in g.render()
    restored = restore_game(json.loads(json.dumps(g.to_dict())))
    assert restored.cash == g.cash


if __name__ == '__main__':
    tests = [(n, f) for n, f in list(globals().items()) if n.startswith('test_') and callable(f)]
    for name, test in tests:
        test()
        print('PASS', name)
    print('Black-train regression passed', len(tests))
