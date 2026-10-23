"""Gomoku draws and single-commit auto regressions; only temporary save data is used."""
import copy
import json
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import patch

sys.dont_write_bytecode = True
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'sakura_boardgame'))
import engine
import gomoku_ai
import plugin
from test_action_ledger import Config, Logger

FULL_ROWS = '''112112122112212
122122111212211
221212212221211
221211211112221
212221112111122
121112121121221
121212121212111
112221211211122
221121221222212
122112212221221
111121212112121
122121112212111
221211222112222
111122211112211
221122221221222'''.splitlines()


def nearly_full(actor='user'):
    game = engine.GomokuGame(first=actor, seed=1)
    game.board = [[int(c) for c in row] for row in FULL_ROWS]
    if actor == 'sakura':
        game.board = [[3 - c for c in row] for row in game.board]
    game.board[0][0] = 0
    game.move_count = 224
    return game


def full_game():
    game = nearly_full()
    game.place('user', 1, 1)
    return game


def choose_first(board, code, level, aggression):
    for r, row in enumerate(board):
        for c, value in enumerate(row):
            if not value:
                return {'row': r + 1, 'col': c + 1, 'reason': 'test'}
    raise AssertionError('AI called on a full board')


class GomokuDrawTests(unittest.TestCase):
    def test_legal_225_move_draw(self):
        game = engine.GomokuGame(first='user', seed=1)
        moves = {p: [(r + 1, c + 1) for r, row in enumerate(FULL_ROWS)
                     for c, code in enumerate(row) if int(code) == game.code[p]]
                 for p in engine.PLAYERS}
        self.assertEqual([len(moves[p]) for p in engine.PLAYERS], [113, 112])
        for index in range(225):
            actor = game.turn
            row, col = moves[actor].pop(0)
            result = game.place(actor, row, col)
            self.assertIsNone(result['winner'])
            self.assertEqual(result['draw'], index == 224)
        self.assertTrue(game.draw)
        self.assertIsNone(result['next'])
        self.assertEqual(result['threats'], '')
        self.assertIn('和棋', game.render())
        self.assertIn('和棋', game.summary())
        self.assertNotIn('轮到', game.summary())

    def test_ai_last_empty_cell_draw(self):
        game = nearly_full('sakura')
        result, reason = game.ai_move('sakura', 1)
        self.assertEqual(result['move'], [1, 1])
        self.assertTrue(result['draw'])
        self.assertIsNone(result['winner'])
        self.assertIsNone(result['next'])
        self.assertTrue(reason)

    def test_new_game_and_nonterminal_result(self):
        game = engine.GomokuGame(first='user', seed=1)
        self.assertIs(game.draw, False)
        result = game.place('user', 8, 8)
        self.assertIs(result['draw'], False)
        self.assertEqual(result['next'], 'sakura')

    def test_win_on_last_empty_cell_has_priority(self):
        game = nearly_full()
        game.board[0][:5] = [0, 1, 1, 1, 1]
        result = game.place('user', 1, 1)
        self.assertEqual(result['winner'], 'user')
        self.assertFalse(result['draw'])
        self.assertIsNone(result['next'])
        self.assertIn('获胜', game.summary())

    def test_ended_actions_rejected_before_ai_search(self):
        for winner in (None, 'user', 'sakura'):
            game = full_game()
            if winner:
                game.winner, game.draw = winner, False
            before = copy.deepcopy(game.to_dict())
            with patch.object(gomoku_ai, 'choose_move', side_effect=AssertionError('must not search')):
                with self.assertRaises(engine.GameError):
                    game.ai_move(game.turn, 1)
                with self.assertRaises(engine.GameError):
                    game.place(game.turn, 1, 1)
            self.assertEqual(game.to_dict(), before)

    def test_draw_json_roundtrip_and_legacy_full_board(self):
        game = full_game()
        saved = json.loads(json.dumps(game.to_dict()))
        self.assertIs(saved['draw'], True)
        self.assertEqual(engine.restore_game(saved).to_dict(), game.to_dict())
        for flag in ('missing', False):
            legacy = copy.deepcopy(saved)
            if flag == 'missing':
                legacy.pop('draw')
            else:
                legacy['draw'] = flag
            restored = engine.restore_game(legacy)
            self.assertTrue(restored.draw)
            self.assertIsNone(restored.winner)

    def test_legacy_nonfull_defaults_false(self):
        data = nearly_full().to_dict()
        data.pop('draw')
        self.assertFalse(engine.restore_game(data).draw)

    def test_legacy_full_winner_beats_draw_flag(self):
        data = full_game().to_dict()
        data['board'][0][:5] = [1] * 5
        for winner in (None, 'user'):
            data['winner'] = winner
            restored = engine.restore_game(copy.deepcopy(data))
            self.assertEqual(restored.winner, 'user')
            self.assertFalse(restored.draw)


class GomokuServiceDrawTests(unittest.TestCase):
    def setUp(self):
        tmp = tempfile.TemporaryDirectory(prefix='sakura-gomoku-draw-')
        self.addCleanup(tmp.cleanup)
        self.root = Path(tmp.name)
        self.config = Config(gomoku_level=1)
        self.service = self.reload()

    def reload(self):
        return plugin.BoardgameService(self.config, lambda name: self.root / name, Logger())

    def test_place_final_draw_saved_and_undoable(self):
        self.service._game = nearly_full()
        result = self.service.place({'player': 'user', 'row': 1, 'col': 1})
        self.assertTrue(result['draw'])
        self.assertIn('和棋', result['status'])
        self.assertNotIn('轮到', result['status'])
        self.assertTrue(self.reload()._game.draw)
        self.service.undo({})
        self.assertFalse(self.service._game.draw)
        self.assertEqual(self.service._game.board[0][0], 0)
        self.assertFalse(self.reload()._game.draw)

    def test_draw_state_context_and_rejected_actions(self):
        self.service._game = full_game()
        self.service._guide = '继续落子测试指令'
        state = self.service.state({})
        self.assertTrue(state['finished'])
        self.assertTrue(state['draw'])
        self.assertIsNone(state['winner'])
        self.assertNotIn('threats', state)
        fragment = self.service.context_fragment()
        self.assertIn('已结束的对局', fragment)
        self.assertNotIn('轮到', fragment)
        self.assertNotIn('继续落子测试指令', fragment)
        before = copy.deepcopy(self.service._game.to_dict())
        for action in (lambda: self.service.auto({'rounds': 1}),
                       self.service.sakura_action,
                       lambda: self.service.place({'player': 'sakura'}),
                       lambda: self.service.place({'player': 'user', 'row': 1, 'col': 1})):
            with self.assertRaises(engine.GameError):
                action()
        self.assertEqual(self.service._game.to_dict(), before)
        self.assertFalse(self.service.proactive_tick())
        self.assertEqual(len(self.service._snapshots), 0)

    def test_auto_final_draw_for_either_actor_commits_once(self):
        for actor in engine.PLAYERS:
            with self.subTest(actor=actor):
                self.service._game = nearly_full(actor)
                self.service._snapshots.clear()
                with patch.object(gomoku_ai, 'choose_move', side_effect=choose_first) as choose:
                    result = self.service.auto({'rounds': 2})
                self.assertEqual(choose.call_count, 1)
                self.assertTrue(result['finished'])
                self.assertIn('和棋', result['status'])
                self.assertEqual(result['turns'][actor], 1)
                self.assertEqual(self.service._game.move_count, 225)
                self.assertEqual(len(self.service._snapshots), 1)
                self.assertTrue(self.reload()._game.draw)

    def test_auto_round_limit_without_monopoly_fields_or_double_place(self):
        for first in engine.PLAYERS:
            with self.subTest(first=first):
                self.service._game = engine.GomokuGame(first=first, seed=1)
                self.service._snapshots.clear()
                with patch.object(gomoku_ai, 'choose_move', side_effect=choose_first):
                    result = self.service.auto({'rounds': 2})
                count = sum(result['turns'].values())
                self.assertEqual(count, 3)
                self.assertEqual(self.service._game.move_count, count)
                self.assertEqual(sum(bool(c) for row in self.service._game.board for c in row), count)
                self.assertEqual(len(self.service._snapshots), count)
                self.assertEqual(self.reload()._game.to_dict(), self.service._game.to_dict())

    def test_act_sakura_can_complete_draw(self):
        self.service._game = nearly_full('sakura')
        with patch.object(gomoku_ai, 'choose_move', side_effect=choose_first):
            result = self.service.sakura_action()
        self.assertTrue(result['draw'])
        self.assertTrue(result['narrated'])
        self.assertIn('和棋', result['status'])

    def test_end_draw_reports_no_winner(self):
        self.service._game = full_game()
        self.service._save()
        result = self.service.end({})
        self.assertIn('和棋', result['status'])
        self.assertNotIn('赢家', result['status'])
        self.assertNotIn('轮到', result['status'])
        self.assertIsNone(self.service._game)
        self.assertFalse((self.root / plugin.STATE_FILE).exists())

    def test_reload_legacy_full_board_is_terminal(self):
        data = full_game().to_dict()
        data.pop('draw')
        (self.root / plugin.STATE_FILE).write_text(json.dumps(data), encoding='utf8')
        service = self.reload()
        self.assertTrue(service._game.draw)
        self.assertIn('和棋', service.state({})['status'])
        with self.assertRaises(engine.GameError):
            service.auto({'rounds': 1})


if __name__ == '__main__':
    unittest.main(verbosity=2)
