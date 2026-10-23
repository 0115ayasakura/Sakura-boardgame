"""Blocking notification uses train categories, without leaking normal place names."""
from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from boardgame_design.black_gold_final.serve_game_preview import create_service


def test_block_notification_labels_and_sequence():
    service = create_service()
    game = service._game
    game.start_black_train()
    for cell in game.cells:
        game._block_seq = cell['id'] + 1
        game._last_walk = {'blocked': True, 'blockedAt': cell['id'],
                           'player': 'user', 'blockedBy': 'sakura'}
        payload = service.same_cell_payload()
        assert payload['on'] and payload['seq'] == game._block_seq
        assert game.black_train['tiles'][str(cell['id'])]['category'] in payload['now']
        assert cell['name'] not in payload['now']
    game.black_train['active'] = False
    assert cell['name'] in service.same_cell_payload()['now']
    game._last_walk = {'blocked': False}
    assert service.same_cell_payload()['now'] == ''
    game.map_kind = 'city'
    assert not service.same_cell_payload()['on']


if __name__ == '__main__':
    test_block_notification_labels_and_sequence()
    print('PASS blocking notification categories, ordinary labels, sequence and city guard')
