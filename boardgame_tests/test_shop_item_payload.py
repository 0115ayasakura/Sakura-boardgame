"""Shop draw and reroll protocol use authoritative engine records."""
from pathlib import Path
import sys
import unittest
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from boardgame_design.black_gold_final.serve_game_preview import create_service


class ShopPayloadTests(unittest.TestCase):
    def setUp(self):
        self.service = create_service()
        self.game = self.service._game

    def test_draw_empty_and_copy(self):
        self.assertIsNone(self.service.item_draw_payload())
        record = {'seq': 1, 'player': 'user', 'key': 'rent_shield',
                  'label': '租金护盾', 'price': 100, 'note': '下一次租金减半'}
        self.game._last_item_draw = record
        payload = self.service.state_payload()
        self.assertEqual(payload['itemDraw'], record)
        payload['itemDraw']['label'] = 'changed'
        self.assertEqual(record['label'], '租金护盾')

    def test_reroll_record(self):
        self.game._last_landing = {'seq': 1, 'player': 'user', 'roll': 6,
                                   'rolls': [1, 6], 'rerolled': True, 'won': True}
        payload = self.service.landing_payload()
        self.assertEqual(payload['rolls'], [1, 6])
        self.assertTrue(payload['rerolled'])
        self.assertEqual(payload['roll'], 6)

    def test_old_landing_record(self):
        self.game._last_landing = {'seq': 1, 'roll': 4}
        self.assertEqual(self.service.landing_payload()['rolls'], [4])
        self.assertFalse(self.service.landing_payload()['rerolled'])


if __name__ == '__main__':
    unittest.main()
