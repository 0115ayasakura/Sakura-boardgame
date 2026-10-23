"""Zero is a valid style, not a missing value."""
import sys
import tempfile
import unittest
from pathlib import Path
sys.dont_write_bytecode = True
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'sakura_boardgame'))
import plugin
from test_action_ledger import Config, Logger

class SettingsTests(unittest.TestCase):
    def setUp(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.config = Config()
        self.service = plugin.BoardgameService(self.config, lambda n: Path(tmp.name) / n, Logger())

    def test_zero_style_survives_action_payload_and_ai(self):
        result = self.service.handle_action('set_style', {'style': 0})
        self.assertTrue(result['ok'])
        self.assertEqual(self.config.get()['gomoku_style'], 0)
        self.assertEqual(self.service.state_payload()['styleManual'], 0)
        self.assertEqual(self.service.gomoku_aggression()[0], 0.0)

    def test_empty_or_invalid_host_style_falls_back_without_state_failure(self):
        for value in (None, '', '  ', 'bad', float('inf')):
            with self.subTest(value=value):
                self.config.update({'gomoku_style': value, 'gomoku_style_follow_card': False})
                self.assertEqual(self.service.state_payload()['styleManual'], 45)
                self.assertEqual(self.service.gomoku_aggression()[0], 0.45)

    def test_zero_difficulty_rejected_without_update(self):
        before = self.config.get()
        result = self.service.handle_action('set_difficulty', {'level': 0})
        self.assertFalse(result['ok'])
        self.assertEqual(self.config.get(), before)

if __name__ == '__main__':
    unittest.main()
