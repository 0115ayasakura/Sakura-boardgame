"""Offline movement facts: actual status and Agent commit hint, not model prose.

No plugin activation, server, host configuration, or user saves are used.
"""
from __future__ import annotations

from pathlib import Path
import sys
import tempfile
import unittest

sys.dont_write_bytecode = True
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'sakura_boardgame'))
sys.path.insert(0, str(Path(__file__).resolve().parent))
import engine
import plugin
from test_action_ledger import Config, Dice, Logger, fixture
from test_agent_turns import Context, HostChat


class MovementNarrationTests(unittest.TestCase):
    def setUp(self):
        # Keep even temporary saves on E:, away from the live host's D: files.
        tmp = tempfile.TemporaryDirectory(prefix='movement-narration-',
                                          dir=Path(__file__).resolve().parent)
        self.addCleanup(tmp.cleanup)
        self.root = Path(tmp.name)
        self.chat = HostChat()
        self.service = plugin.BoardgameService(
            Config(decision_driver='agent'), lambda name: self.root / name,
            Logger(), context=Context(self.chat))
        self.chat.service = self.service
        self.service._opponent['id'] = 'character-1'
        self.work = []
        self.service._agent_turns.launch = self.work.append
        self.service._agent_turns.clock = lambda: 1000.0

    def commit(self, action='roll', arguments=None):
        response = self.service.handle_action('act_sakura', {})
        self.assertTrue(response['ok'], response)
        self.assertEqual(len(self.work), 1)
        self.work.pop()()
        turns = self.service._agent_turns
        self.assertEqual(turns.public()['status'], 'thinking')
        result = turns.commit({'token': turns.ticket['token'], 'action': action,
                               'arguments': arguments or {}})
        self.assertTrue(result['ok'])
        self.assertEqual(result['agentDecision']['status'], 'committed')
        self.assertNotIn('movement', result)
        return result

    def route_game(self):
        data = fixture(kind='rest', actor='sakura').to_dict()
        data.update(size=11, cols=11, rows=1, main_path=list(range(11)),
                    branches=[[9, 5]], positions={'sakura': 7, 'user': 0},
                    edges=[[i, (i + 1) % 11] for i in range(11)] + [[9, 5]])
        cell = data['cells'][2]
        data['cells'] = [{**cell, 'id': i, 'x': i, 'name': f'cell-{i}',
                          'type': 'start' if i == 0 else 'rest'} for i in range(11)]
        game = engine.restore_game(data)
        game._dice_rng = lambda: Dice(3)
        self.service._game = game
        return game

    def assert_movement_hint(self, result):
        # These are supplied constraints, not a claim about live model compliance.
        for text in ('dice是可用步数，不是已走距离', 'path是累计动画轨迹',
                     '不耗步的传送或退回', '不得按len(path)推算实际步数',
                     '不得自行计算实际步数', 'steps_left>0',
                     '只说已部分行走与剩余步数', 'pending.player=sakura',
                     '不得说轮到用户'):
            self.assertIn(text, result['hint'])
            self.assertIn(text, self.chat.requests[-1]['message'])
        self.assertNotIn('boardgame_decide', result['hint'])
        for text in ('不必逐项念状态或每轮重复余额', '感受和玩笑可以自由发挥',
                     '不把旧事件当成本次发生', '提交失败就自然说明没办成'):
            self.assertIn(text, result['hint'])
            self.assertIn(text, self.chat.requests[-1]['message'])

    def test_roll_three_stops_at_route_with_one_step_left(self):
        self.route_game()
        result = self.commit()
        self.assertEqual(result['dice'], 3)
        self.assertEqual(result['path'], [7, 8, 9])
        self.assertEqual(result['steps_left'], 1)
        self.assertEqual(result['pending']['type'], 'route')
        self.assertEqual(result['pending']['player'], 'sakura')
        self.assertIsNone(result['next'])
        self.assertIn('还剩 1 步没走完', result['status'])
        self.assertNotIn(f"轮到 {plugin.PLAYER_LABEL['user']}", result['status'])
        self.assert_movement_hint(result)

    def test_route_status_prefers_result_then_current_walk_remaining(self):
        game = self.route_game()
        game._walk = {'player': 'sakura', 'steps_left': 8, 'path': [7, 8, 9]}
        result = {'story': [], 'pending': {'type': 'route', 'player': 'sakura',
                                         'options': [5, 10]}, 'steps_left': 1}
        status = self.service._monopoly_status(result)
        self.assertIn('还剩 1 步没走完', status)
        self.assertNotIn('还剩 8 步', status)
        del result['steps_left']
        self.assertIn('还剩 8 步没走完', self.service._monopoly_status(result))
        result['steps_left'] = 0
        self.assertNotIn('还剩 8 步', self.service._monopoly_status(result))
        game._walk = None
        del result['steps_left']
        self.assertNotIn('还剩', self.service._monopoly_status(result))

    def test_route_completion_reports_real_next_actor_not_stale_remaining(self):
        game = self.route_game()
        self.commit()
        result = self.commit('decide', {'action': '10'})
        self.assertEqual(result['path'], [7, 8, 9, 10])
        self.assertEqual(result['steps_left'], 0)
        self.assertIsNone(result['pending'])
        self.assertIsNone(game._walk)
        self.assertEqual(result['next'], 'user')
        self.assertIn(f"轮到 {plugin.PLAYER_LABEL['user']}", result['status'])
        self.assertNotIn('没走完', result['status'])
        self.assertNotIn('没走完', self.service._monopoly_status(result))
        self.assert_movement_hint(result)

    def test_teleport_animation_does_not_authorize_actual_step_arithmetic(self):
        game = fixture(kind='rest', actor='sakura')
        game.cells[1]['type'] = 'end'
        self.service._game = game
        result = self.commit()
        self.assertEqual(result['dice'], 1)
        self.assertEqual(result['path'], [0, 1, 0])
        self.assertEqual(result['steps_left'], 0)
        self.assertEqual(game.positions['sakura'], 0)
        self.assertIn('传送门', result['status'])
        self.assertNotIn('走了 2 步', result['status'])
        self.assertNotIn('没走完', result['status'])
        self.assert_movement_hint(result)


if __name__ == '__main__':
    unittest.main()
