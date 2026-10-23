"""Real setup schema + real commit regression; fake host, temporary saves, no server/UI."""
from __future__ import annotations

import copy
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import Mock, PropertyMock, patch

sys.dont_write_bytecode = True
ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'sakura_boardgame'))
sys.path.insert(0, str(ROOT / 'Sakura'))
import engine
import plugin
from app.agent.tools.registry import _normalize_parameters_schema, _validate_tool_arguments


class Config:
    def __init__(self):
        self.values = {'decision_driver': 'agent', 'map_kind': 'grid',
                       'auto_open_board': False, 'auto_narrate': False}

    def get(self):
        return dict(self.values)

    def update(self, values):
        self.values.update(values)


class Host:
    plugin_id = 'test.boardgame'

    def __init__(self, root):
        self.root = root
        self.config = Config()
        self.effects = []
        self.descriptors = {}
        self.handlers = {}
        self.chat = Mock()
        self.chat.current.return_value = {
            'sessionId': 'session-1', 'characterId': 'character-1', 'idle': True}
        self.chat.submit.return_value = {'accepted': True, 'operationId': 'operation-1'}
        tools = Mock()
        tools.register.side_effect = self.register
        self.services = {'sakura.host.tools': tools, 'sakura.host.chat': self.chat,
                         'sakura.host.logging': Mock(), 'sakura.host.context': Mock(),
                         'sakura.host.settings': Mock()}

    def register(self, descriptor, handler):
        self.descriptors[descriptor['name']] = copy.deepcopy(descriptor)
        self.handlers[descriptor['name']] = handler

    def get(self, key):
        return self.services.get(key)

    def data_path(self, relative):
        return self.root / relative

    def effect(self, callback):
        self.effects.append(callback)


class AgentCommitSchemaTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory(prefix='boardgame-commit-schema-')
        self.addCleanup(temporary.cleanup)
        self.host = Host(Path(temporary.name))
        # Execute actual setup/registration, blocking only external startup edges.
        board_url = patch.object(plugin.BoardgameService, 'board_url', new_callable=PropertyMock,
                                 return_value='http://offline.invalid/board')
        board_url.start()
        self.addCleanup(board_url.stop)
        with patch.object(plugin, 'BoardServer') as server, \
                patch.object(plugin.BoardgameService, 'start_proactive'):
            plugin.BoardgamePlugin().setup(self.host)
            server.return_value.start.assert_not_called()
        self.addCleanup(self.cleanup_host)
        self.commit = self.host.handlers['boardgame_agent_commit']
        self.service = self.commit.__self__.service
        self.schema = self.host.descriptors['boardgame_agent_commit']['parameters']

    def cleanup_host(self):
        for callback in reversed(self.host.effects):
            callback()

    def test_registered_nested_schema_survives_host_normalization(self):
        normalized = _normalize_parameters_schema(self.schema)
        arguments = normalized['properties']['arguments']
        self.assertEqual(arguments, self.schema['properties']['arguments'])
        self.assertEqual(set(arguments['properties']),
                         {'action', 'row', 'col', 'use_custom_die', 'seq', 'floor'})
        self.assertIs(arguments['additionalProperties'], False)
        self.assertEqual(normalized['required'], ['token', 'action', 'arguments'])
        self.assertEqual(self.host.descriptors['boardgame_agent_commit']['group'], 'plugin')
        descriptor_text = self.host.descriptors['boardgame_agent_commit']['description']
        self.assertIn('action="decide", arguments={"action":"buy_draw"}', descriptor_text)
        for action, params in [
            ('decide', {'action': 'buy_draw'}), ('roll', {}),
            ('roll', {'use_custom_die': True}), ('place', {'row': 1, 'col': 15}),
            ('tower_step', {'seq': 2, 'floor': 1}),
            ('tower_finish', {'seq': 2, 'floor': 1}),
        ]:
            with self.subTest(action=action, params=params):
                self.assertEqual(_validate_tool_arguments(
                    {'token': 'test-token', 'action': action, 'arguments': params}, normalized), '')
        for params in ({'choice': 'buy_draw'}, {'player': 'user'},
                       {'row': 0, 'col': 1}, {'use_custom_die': 1}, {'seq': '2'}):
            with self.subTest(params=params):
                self.assertTrue(_validate_tool_arguments(
                    {'token': 'test-token', 'action': 'decide', 'arguments': params}, normalized))

    def test_choice_rejected_without_mutation_then_action_commits_real_purchase(self):
        service = self.service
        service.start({'game': 'monopoly', 'first': 'sakura'}, skip_open=True)
        service._opponent['id'] = 'character-1'
        game = service._game
        shop = next(cell['id'] for cell in game.cells if cell['type'] == 'shop')
        game._enter_shop('sakura', shop, [])
        work = []
        service._agent_turns.launch = work.append
        queued = service.handle_action('act_sakura', {}, metadata={
            'requestId': 'schema-shop', 'gameSeq': service._game_seq,
            'expectedVersion': service._version})
        self.assertTrue(queued['ok'], queued)
        self.assertEqual(len(work), 1)
        work.pop()()
        token = service._agent_turns.ticket['token']
        state = self.host.handlers['boardgame_agent_state']({'token': token})
        self.assertEqual(state['legalAction']['action'], 'decide')
        self.assertIn('buy_draw', state['legalAction']['options'])
        before_game = copy.deepcopy(game.__dict__)
        before_ticket = copy.deepcopy(service._agent_turns.ticket)
        before_receipts = copy.deepcopy(service._agent_turns.receipts)
        before_version = service._version
        saved = self.host.data_path(plugin.STATE_FILE).read_bytes()
        wrong = {'token': token, 'action': 'decide', 'arguments': {'choice': 'buy_draw'}}
        self.assertIn('choice', _validate_tool_arguments(wrong, self.schema))
        # Bypass host validation deliberately: the real apply boundary must still reject.
        with self.assertRaisesRegex(engine.GameError, '决策必须是当前合法选项'):
            self.commit(wrong)
        self.assertEqual(service._game.__dict__, before_game)
        self.assertEqual(service._agent_turns.ticket, before_ticket)
        self.assertEqual(service._agent_turns.receipts, before_receipts)
        self.assertEqual(service._version, before_version)
        self.assertEqual(self.host.data_path(plugin.STATE_FILE).read_bytes(), saved)
        correct = {'token': token, 'action': 'decide', 'arguments': {'action': 'buy_draw'}}
        self.assertEqual(_validate_tool_arguments(correct, self.schema), '')
        result = self.commit(correct)
        self.assertTrue(result['ok'], result)
        self.assertEqual(result['agentDecision']['status'], 'committed')
        self.assertEqual(service._game.cash['sakura'],
                         before_game['cash']['sakura'] - engine.MONOPOLY_SHOP_DRAW_PRICE)
        self.assertEqual(result['itemDraw']['player'], 'sakura')
        self.assertEqual(service._game.action_seq, before_game['action_seq'] + 1)
        self.assertNotEqual(self.host.data_path(plugin.STATE_FILE).read_bytes(), saved)


if __name__ == '__main__':
    unittest.main()
