"""Dice-turn grouping regression; never touches a real save or test runner."""
from __future__ import annotations

import copy
import json
from pathlib import Path
import shutil
import subprocess
import tempfile
import unittest

from test_action_ledger import Config, Dice, Logger, fixture, engine, plugin


class TurnLedgerTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory(prefix="boardgame-turn-ledger-")
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)
        self.service = plugin.BoardgameService(Config(), lambda name: self.root / name, Logger())

    def use(self, **kwargs):
        self.service._game = fixture(**kwargs)
        return self.service._game

    def test_roll_buy_is_one_turn_and_next_roll_is_two(self):
        game = self.use()
        self.service.roll({"player": "user"})
        self.assertEqual(game.move_count, 1)
        self.service.decide({"action": "buy"})
        self.assertEqual(game.move_count, 1)
        self.assertEqual([r["turn"] for r in game.action_records], [1, 1])
        self.assertEqual([r["turn_complete"] for r in game.action_records], [False, True])
        game.skip_pending["sakura"] = True
        self.service.roll({"player": "sakura"})
        self.assertEqual(game.action_records[-1]["turn"], 2)
        self.assertEqual(game.move_count, 2)

    def test_shop_upgrade_and_skip_do_not_add_turns(self):
        for kind, owner, choice in [("shop", None, "buy_dice"),
                                    ("property", "user", "upgrade"),
                                    ("property", None, "skip")]:
            with self.subTest(choice=choice):
                game = self.use(kind=kind, owner=owner)
                self.service.roll({"player": "user"})
                self.service.decide({"action": choice})
                self.assertEqual(game.move_count, 1)
                self.assertEqual([r["turn"] for r in game.action_records], [1, 1])

    def test_special_dice_multiple_routes_and_purchase_share_turn(self):
        game = self.use(map_kind="random")
        game.flags["user"]["pick_dice"] = True
        game._moves = {0: [1, 2], 1: [2, 3], 2: [1, 3], 3: [0]}
        game.cells[1]["type"] = "rest"
        game.cells[3].update(type="property", price=60, tier=1, group="daily")
        self.service.roll({"player": "user", "use_custom_die": True})
        self.assertEqual(game.move_count, 1, "roll counts even before selecting special dice")
        for choice in ("2", "1", "3", "buy"):
            self.service.decide({"action": choice})
            self.assertEqual(game.move_count, 1)
        self.assertEqual([r["turn"] for r in game.action_records], [1] * 5)
        self.assertEqual(game.cells[3]["owner"], "user")
        self.assertTrue(game.action_records[-1]["turn_complete"])

    def test_tower_all_floors_and_exit_share_turn_and_restore(self):
        game = self.use(kind="tower")
        self.service.roll({"player": "user"})
        game._dice_rng = lambda: Dice(6)
        game._draw_card = lambda: {"title": "收款", "flavor": "收款", "effect": {"type": "collect_each", "amount": 80}}
        for choice in ("tower_next", "tower_next", "tower_next", "tower_finish"):
            self.service.decide({"action": choice})
            self.assertEqual(game.move_count, 1)
        self.assertEqual([r["turn"] for r in game.action_records], [1] * 5)
        self.assertTrue(game.action_records[-1]["turn_complete"])
        restored = engine.restore_game(game.to_dict())
        self.assertEqual(restored.dice_turn, 1)
        self.assertEqual(restored.action_records, game.action_records)
        self.service.undo()
        self.assertEqual(self.service._game.dice_turn, 0)
        self.assertEqual(self.service._game.move_count, 0)
        self.service._game._dice_rng = lambda: Dice()
        self.service.roll({"player": "user"})
        self.assertEqual(self.service._game.action_records[-1]["turn"], 1)

    def test_mid_special_dice_save_and_resume_keeps_number(self):
        game = self.use()
        game.flags["user"]["pick_dice"] = True
        self.service.roll({"player": "user", "use_custom_die": True})
        restored = plugin.BoardgameService(Config(), lambda name: self.root / name, Logger())
        self.assertEqual(restored._game.pending["type"], "dice")
        self.assertEqual(restored._game.dice_turn, 1)
        restored.decide({"action": "1"})
        restored.decide({"action": "buy"})
        self.assertEqual([r["turn"] for r in restored._game.action_records], [1, 1, 1])
        self.assertEqual(restored._game.move_count, 1)

    def test_skipped_turn_is_complete_and_new_game_resets(self):
        game = self.use(kind="rest")
        game.skip_pending["user"] = True
        self.service.roll({"player": "user"})
        self.assertEqual(game.action_records[-1]["turn"], 1)
        self.assertTrue(game.action_records[-1]["turn_complete"])
        self.service.start({"game": "monopoly", "first": "user"}, skip_open=True)
        self.assertEqual(self.service._game.dice_turn, 0)
        self.assertEqual(self.service._game.action_seq, 0)

    def test_old_records_migrate_in_memory_by_roll_not_action_id(self):
        game = self.use()
        self.service.roll({"player": "user"})
        self.service.decide({"action": "buy"})
        game.skip_pending["sakura"] = True
        self.service.roll({"player": "sakura"})
        data = copy.deepcopy(game.to_dict())
        data.pop("dice_turn")
        data["move_count"] = 3  # historical double counting must not alter old rule state
        for row in data["action_records"]:
            row.pop("turn")
            row.pop("turn_complete")
        path = self.root / plugin.STATE_FILE
        path.write_text(json.dumps(data, ensure_ascii=False), encoding="utf-8")
        original = path.read_bytes()
        restored = plugin.BoardgameService(Config(), lambda name: self.root / name, Logger())
        self.assertEqual(path.read_bytes(), original)
        self.assertEqual(restored._game.move_count, 3)
        self.assertEqual(restored._game.dice_turn, 2)
        self.assertEqual([r["turn"] for r in restored._game.action_records], [1, 1, 2])
        self.assertEqual(engine.MAP_VERSION, 13)
        restored._game._dice_rng = lambda: Dice()
        restored.roll({"player": "user"})
        self.assertEqual(restored._game.action_records[-1]["turn"], 3)

    def test_frontend_totals_keep_all_steps_and_net_property_effects(self):
        node = shutil.which("node")
        if not node:
            self.skipTest("Node unavailable for frontend pure-function test")
        game = self.use(kind="tower")
        self.service.roll({"player": "user"})
        game._dice_rng = lambda: Dice(6)
        game._draw_card = lambda: {"title": "收款", "flavor": "收款", "effect": {"type": "collect_each", "amount": 80}}
        for choice in ("tower_next", "tower_next", "tower_next", "tower_finish"):
            self.service.decide({"action": choice})
        rows = copy.deepcopy(game.action_records)
        # Repeated receive/pay within a turn must sum every step, not take the last delta.
        for row, delta in zip(rows, [0, 80, -30, 45, 0]):
            row["players"]["user"]["cash"]["delta"] = delta
            row["players"]["sakura"]["cash"]["delta"] = -delta
        rows[1]["properties"] = [{"cell": 1, "name": "地产", "before": {"owner": None, "level": 0, "mortgaged": False}, "after": {"owner": "user", "level": 0, "mortgaged": False}}]
        rows[3]["properties"] = [{"cell": 1, "name": "地产", "before": {"owner": "user", "level": 0, "mortgaged": False}, "after": {"owner": "user", "level": 1, "mortgaged": False}}]
        board = (Path(__file__).resolve().parents[1] / "sakura_boardgame" / "board.html").read_text(encoding="utf-8")
        functions = board[board.index("function actionRecordHTML("):board.index("function renderActionLog()")]
        script = "const assert=require('assert'); const NAMES={user:'玩家',sakura:'对手'}; const esc=v=>String(v);\n" + functions
        script += "\nconst rows=" + json.dumps(rows, ensure_ascii=False) + "; const groups=actionTurnGroups(rows); assert.equal(groups.length,1); assert.equal(groups[0].rows.length,5); const total=actionTurnSummary(groups[0]); assert.equal(total.players.user.cash.delta,95); assert.equal(total.players.sakura.cash.delta,-95); assert.equal(total.properties[0].changed.owner.before,null); assert.equal(total.properties[0].changed.level.after,1); assert.equal(total.description,'回合总净变化 · 已结束'); for(const row of rows)assert(actionRecordHTML(row).includes(row.story[0]||'')); const old=rows.map(r=>{const x={...r};delete x.turn;return x});assert.equal(actionTurnGroups(old).length,1); const partial=actionTurnGroups(rows.slice(1))[0];assert(partial.partial);assert.equal(actionTurnSummary(partial).description,'保留步骤净变化');"
        result = subprocess.run([node, "-e", script], capture_output=True, text=True, encoding="utf-8")
        self.assertEqual(result.returncode, 0, result.stderr)


if __name__ == "__main__":
    unittest.main(verbosity=2)
