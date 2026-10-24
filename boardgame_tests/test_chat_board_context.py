"""Offline chat-context regressions: real engine facts, no server/model/UI or user saves."""
from __future__ import annotations

import copy
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import patch

sys.dont_write_bytecode = True
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'sakura_boardgame'))
sys.path.insert(0, str(Path(__file__).resolve().parent))
import engine
import plugin
from test_gomoku_draw import FULL_ROWS
from test_plugin_smoke import FakeContext


def won_game(winner):
    """Produce either winner through legal alternating moves, with row != col."""
    game = engine.GomokuGame(first=winner, seed=1)
    other = 'user' if winner == 'sakura' else 'sakura'
    for index in range(5):
        game.place(winner, 3, 5 + index)
        if index < 4:
            game.place(other, 14, 1 + index * 2)
    assert game.winner == winner and game.last_move == (2, 8)
    return game


def drawn_game():
    """Replay the established no-five fixture legally, rather than setting draw."""
    game = engine.GomokuGame(first='user', seed=1)
    moves = {p: [(r + 1, c + 1) for r, row in enumerate(FULL_ROWS)
                 for c, code in enumerate(row) if int(code) == game.code[p]]
             for p in engine.PLAYERS}
    for _ in range(225):
        actor = game.turn
        game.place(actor, *moves[actor].pop(0))
    assert game.draw and game.winner is None
    return game


class ChatBoardContextTests(unittest.TestCase):
    def setUp(self):
        tmp = tempfile.TemporaryDirectory(prefix='sakura-chat-context-')
        self.addCleanup(tmp.cleanup)
        self.context = FakeContext(Path(tmp.name) / 'data')
        self.context.config.update({'decision_driver': 'agent'})
        # Exercise real setup/registration, but never bind a port or start a worker.
        server = self.enterContext(patch.object(plugin, 'BoardServer'))
        server.return_value.start.return_value = 'http://127.0.0.1:1/'
        proactive = self.enterContext(patch.object(plugin.BoardgameService, 'start_proactive'))
        plugin.BoardgamePlugin().setup(self.context)
        proactive.assert_called_once()
        self.addCleanup(self.context.teardown)
        self.service = self.context.tools['boardgame_state'].__self__
        descriptor, self.builder = self.context.context_contributions[0]
        self.assertEqual(len(self.context.context_contributions), 1)
        self.assertEqual(descriptor['providerId'], 'local.sakura.boardgame.state')

    def request_fragments(self, game, request):
        self.service._game = game
        before = copy.deepcopy(game.to_dict()) if game is not None else None
        version = self.service._version
        # Reading chat context must neither query token tools nor mutate the game.
        with patch.object(self.service._agent_turns, 'state', side_effect=AssertionError('token query')), \
             patch.object(self.service._agent_turns, 'commit', side_effect=AssertionError('token commit')), \
             patch.object(self.service, 'state', side_effect=AssertionError('state tool')), \
             patch.object(self.service, 'start', side_effect=AssertionError('new game')):
            fragments = self.builder(request)
        self.assertEqual(self.service._version, version)
        self.assertEqual(game.to_dict() if game is not None else None, before)
        self.assertIs(self.service._game, game)
        self.assertFalse(self.service._snapshots)
        self.assertFalse(self.context.mobile_calls)
        return fragments

    def fragment(self, game, current_input='请复盘当前棋盘，并给我棋盘地址。', **request):
        fragments = self.request_fragments(game, {'current_input': current_input, **request})
        self.assertEqual(len(fragments), 1)
        self.assertEqual(fragments[0]['id'], plugin._FRAGMENT_ID)
        text = fragments[0]['content']
        self.assertTrue(text.startswith('【权威身份】sakura=你（当前角色），user=用户'))
        for rule in ('普通聊天/复盘只读', '不为核实状态调用token工具',
                     '信息不足就直说', '不自动开新局',
                     '仅当本次棋盘主动决策请求含有效token', '没有token不得调用',
                     '不填none/null', '不猜测、不沿用旧token'):
            self.assertIn(rule, text)
        return text

    def test_unrelated_input_never_injects_even_with_saved_game_and_open_page(self):
        games = (None, engine.MonopolyGame(size=16, dice_sides=6, seed=1),
                 won_game('sakura'), drawn_game())
        requests = ({}, {'current_input': ''}, {'current_input': '   '},
                    {'current_input': None}, {'current_input': '今天晚饭吃什么？'},
                    {'current_input': '选择继续扮演这个角色，讨论理财收益。'},
                    {'current_input': 'Please continue discussing investment returns.'},
                    {'current_input': 'Discuss the bodice design and motherboard choice.'},
                    {'source': 'event', 'event_type': 'reminder_due',
                     'current_input': '该喝水了。'},
                    {'source': 'event', 'event_type': 'screen_awareness', 'current_input': ''})
        for game in games:
            # Round-trip an actual isolated save, not merely an in-memory active flag.
            if game is not None:
                self.service._game = game
                self.service._save()
                game = self.service._load()
            self.service._server._last_visible_page = 10**12
            for request in requests:
                with self.subTest(kind=getattr(game, 'kind', None),
                                  winner=getattr(game, 'winner', None), request=request):
                    with patch.object(self.service, 'context_fragment',
                                      wraps=self.service.context_fragment) as snapshot:
                        self.assertEqual(self.request_fragments(game, request), [])
                    snapshot.assert_not_called()

    def test_recent_messages_and_other_request_metadata_do_not_activate_context(self):
        game = engine.GomokuGame(first='user', seed=1)
        history = [{'role': 'user', 'content': '来一局五子棋，给我棋盘地址。'},
                   {'role': 'assistant', 'content': '（桌游播报：上次已落子）'},
                   {'role': 'user', 'content': '【棋盘插件主动决策请求，不是用户消息】'}]
        for step in (0, 2):
            for current in ('', '你还记得昨天我们一起度过的时光吗？'):
                with self.subTest(step=step, current=current):
                    request = {'current_input': current, 'source': 'chat', 'step_index': step,
                               'recent_messages': history, 'event_type': 'boardgame',
                               'available_tools': ['boardgame_agent_state', 'boardgame_start']}
                    self.assertEqual(self.request_fragments(game, request), [])

    def test_current_board_questions_keep_complete_snapshot_at_every_step(self):
        game = won_game('sakura')
        for current in ('桌游怎么玩？', '棋盘地址在哪？', '复盘这局五子棋', '开一局大富翁',
                        '黑列车怎么躲？', '帮我掷骰', '票根怎么用？',
                        'How do we play gomoku?', 'Show the board game rules.',
                        'Where is the Monopoly board?', 'Roll the dice, please.'):
            for step in (0, 2):
                with self.subTest(current=current, step=step):
                    text = self.fragment(game, current, source='chat', step_index=step)
                    self.assertEqual(text, self.service.context_fragment())
                    self.assertIn('你赢了，用户输了', text)

    def test_current_monopoly_question_keeps_guide_map_and_recent_dynamics(self):
        game = engine.MonopolyGame(size=16, dice_sides=6, seed=1)
        self.service._log.append('本局测试动态：刚才经过起点。')
        text = self.fragment(game, '帮我解释大富翁当前玩法和局势。')
        self.assertEqual(text, self.service.context_fragment())
        self.assertIn('【桌游引导】', text)
        self.assertIn(game.render(), text)
        self.assertIn('最近动态：本局测试动态：刚才经过起点。', text)

    def test_current_plugin_markers_keep_snapshot_for_notifications_and_decisions(self):
        game = engine.GomokuGame(first='sakura', seed=1)
        for source in ('chat', 'event'):
            for current in ('（桌游播报：这一步操作已经由程序执行完毕。）',
                            '【棋盘插件主动决策请求，不是用户消息】token=offline-test'):
                with self.subTest(source=source, current=current):
                    text = self.fragment(game, current, source=source, event_type='host_message')
                    self.assertEqual(text, self.service.context_fragment())
                    self.assertIn('【桌游引导】', text)

    def test_sakura_winner_is_you_not_user(self):
        text = self.fragment(won_game('sakura'))
        self.assertIn('winner=sakura，draw=false；你赢了，用户输了。', text)
        self.assertNotIn('用户赢了，你输了。', text)
        self.assertIn('已结束的对局', text)
        self.assertLess(text.index('你赢了，用户输了'), text.index('【已结束的对局】'))

    def test_user_winner_is_user_not_you(self):
        text = self.fragment(won_game('user'))
        self.assertIn('winner=user，draw=false；用户赢了，你输了。', text)
        self.assertNotIn('你赢了，用户输了。', text)

    def test_draw_has_no_winner_or_loser(self):
        text = self.fragment(drawn_game())
        self.assertIn('winner=null，draw=true；和棋，双方都没赢也没输。', text)
        self.assertIn('已结束的对局', text)
        self.assertNotIn('轮到', text)
        self.assertNotIn('【桌游引导】', text)

    def test_no_game_keeps_identity_chat_rules_and_board_address(self):
        text = self.fragment(None)
        self.assertIn('当前无对局，无可判定的胜负。', text)
        self.assertIn('http://127.0.0.1:1/', text)
        self.assertNotIn('你赢了', text)
        self.assertNotIn('你输了', text)
        self.assertNotIn('【桌游引导】', text)

    def test_in_progress_does_not_invent_result_or_last_move(self):
        game = engine.GomokuGame(first='sakura', seed=1)
        text = self.fragment(game)
        self.assertIn('winner=null，draw=false；对局进行中，尚无胜负。', text)
        self.assertIn('进行中的对局', text)
        self.assertNotIn('权威最后落子', text)
        self.assertNotIn('你赢了', text)
        self.assertNotIn('你输了', text)

    def test_last_move_is_one_based_row_then_column_for_both_drivers(self):
        for driver in ('agent', 'algorithm'):
            with self.subTest(driver=driver):
                self.context.config.update({'decision_driver': driver})
                game = engine.GomokuGame(first='user', seed=1)
                game.place('user', 2, 11)
                self.assertEqual(game.last_move, (1, 10))
                text = self.fragment(game)
                self.assertIn('row=行（从上到下），col=列（从左到右），不可对调', text)
                self.assertIn('权威最后落子：第2行、第11列（row=2, col=11）', text)
                self.assertNotIn('row=11, col=2', text)
                self.assertNotIn('row=1, col=10', text)
        text = self.fragment(won_game('sakura'))
        self.assertIn('权威最后落子：第3行、第9列（row=3, col=9）', text)

    def test_registered_token_descriptions_require_current_proactive_token(self):
        for name in ('boardgame_agent_state', 'boardgame_agent_commit'):
            with self.subTest(tool=name):
                spec = self.context.tool_descriptors[name]
                for rule in ('仅当本次棋盘主动决策请求或commit返回的continuation含有效token',
                             '普通聊天/复盘没有token不得调用', '不填none/null',
                             '不猜测、不沿用旧token', '不为核实状态调用'):
                    self.assertIn(rule, spec['description'])
                self.assertEqual(spec['group'], 'plugin')
                self.assertIn('token', spec['parameters']['required'])
        self.assertIn('必须在提交前读取', self.context.tool_descriptors['boardgame_agent_state']['description'])
        self.assertIn('失败/过期立即停止', self.context.tool_descriptors['boardgame_agent_commit']['description'])
        self.assertIn('相同token相同参数重复仅返回原结果',
                      self.context.tool_descriptors['boardgame_agent_commit']['description'])


if __name__ == '__main__':
    unittest.main(verbosity=2)
