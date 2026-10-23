"""Settings descriptor regression; no host, sockets or persistent configuration."""
from pathlib import Path
import ast
import types
import unittest

SOURCE = Path(__file__).resolve().parents[1] / 'sakura_boardgame' / 'plugin.py'


class SettingsPageTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.tree = ast.parse(SOURCE.read_text(encoding='utf-8'))
        cls.call = next(node for node in ast.walk(cls.tree)
                        if isinstance(node, ast.Call)
                        and isinstance(node.func, ast.Attribute)
                        and isinstance(node.func.value, ast.Name)
                        and node.func.value.id == 'settings'
                        and node.func.attr == 'register')
        theme = types.SimpleNamespace(available=lambda: [{'name': 'Test theme', 'id': 'test'}])
        scope = {'service': types.SimpleNamespace(_theme_module=lambda: theme),
                 'PIECE_DEFAULT': {'user': 'a', 'sakura': 'b'},
                 'PIECE_LABELS': {'a': 'A', 'b': 'B'}, 'PIECES': ['a', 'b']}
        cls.page = eval(compile(ast.Expression(cls.call.args[0]), str(SOURCE), 'eval'), scope)
        cls.fields = {field['key']: field for field in cls.page['fields']}

    def test_only_current_general_controls_are_exposed(self):
        self.assertEqual(set(self.fields), {
            'decision_driver', 'dice_sides', 'theme', 'map_kind', 'piece_user',
            'piece_sakura', 'auto_open_board', 'auto_mode', 'proactive_minutes', 'auto_narrate'})
        self.assertEqual(len(self.page['fields']), len(self.fields))

    def test_defaults_and_stored_value_contract_are_unchanged(self):
        self.assertEqual(self.fields['decision_driver']['default'], 'agent')
        self.assertEqual([x['value'] for x in self.fields['decision_driver']['options']],
                         ['agent', 'algorithm'])
        self.assertEqual([x['value'] for x in self.fields['auto_mode']['options']], ['off', 'hers'])
        self.assertEqual(self.fields['proactive_minutes']['minimum'], 0)
        self.assertEqual(self.fields['dice_sides']['default'], 6)
        self.assertEqual(self.fields['map_kind']['default'], 'city')

    def test_copy_is_short_and_does_not_claim_algorithm_only_control(self):
        for field in self.fields.values():
            self.assertLessEqual(len(field.get('description', '')), 65, field['key'])
        copy = repr(self.page)
        for obsolete in ('不经过模型', '9×7', '手动值', '宿主不允许', '屏幕感知', '设定集最后一页'):
            self.assertNotIn(obsolete, copy)
        self.assertIn('不影响 Agent 决策', self.fields['auto_narrate']['description'])

    def test_save_keeps_existing_compatibility_keys_and_revocation_callback(self):
        save = next(keyword.value for keyword in self.call.keywords if keyword.arg == 'save')
        self.assertEqual(ast.unparse(save), 'service.save_settings')
        load = next(keyword.value for keyword in self.call.keywords if keyword.arg == 'load')
        values = {'play_style': 'steady', 'gomoku_level': 3, 'gomoku_style': 45,
                  'gomoku_style_follow_card': False, 'decision_driver': 'agent'}
        context = types.SimpleNamespace(config=types.SimpleNamespace(get=lambda: dict(values)))
        loaded = eval(compile(ast.Expression(load), str(SOURCE), 'eval'), {'context': context})()
        self.assertEqual(loaded, values)
        self.assertIsNot(loaded, values)


if __name__ == '__main__':
    unittest.main()
