"""Structured action ledger regression. All saves live in independent temporary dirs."""
from __future__ import annotations

import copy
import json
from pathlib import Path
import sys
import tempfile
import unittest

sys.dont_write_bytecode = True
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "sakura_boardgame"))
import engine
import plugin


class Config:
    def __init__(self, **values):
        self.values = {"decision_driver": "algorithm", "map_kind": "city", "auto_open_board": False,
                       "auto_narrate": False, **values}

    def get(self):
        return dict(self.values)

    def update(self, values):
        self.values.update(values)


class Logger:
    def __getattr__(self, name):
        return lambda *args, **kwargs: None


class Dice:
    def __init__(self, value=1):
        self.value = value

    def randint(self, low, high):
        return self.value

    def randrange(self, count):
        return 0


def fixture(kind="property", actor="user", owner=None, map_kind="city"):
    """Explicit four-cell fixture; legacy random fixtures are serialized, not generated."""
    game = engine.MonopolyGame(size=28, dice_sides=6, seed=3)
    data = game.to_dict()
    data.update(size=4, cols=4, rows=1, map_kind=map_kind, max_rounds=10000,
                main_path=[0, 1, 2, 3], branches=[], gates=[], start_node=0,
                positions={"user": 0, "sakura": 0}, turn=actor,
                edges=[[0, 1], [1, 2], [2, 3], [3, 0]])
    data["cells"] = [
        {"id": i, "x": i, "y": 0, "name": f"cell-{i}",
         "type": kind if i == 1 else ("start" if i == 0 else "rest"),
         "price": 60 if i == 1 and kind == "property" else 0,
         "tier": 1 if i == 1 and kind == "property" else 0,
         "group": "daily" if i == 1 and kind == "property" else None,
         "owner": owner if i == 1 else None, "level": 0, "mortgaged": False}
        for i in range(4)
    ]
    game = engine.restore_game(data)
    game._dice_rng = lambda: Dice()
    return game


class ActionLedgerTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory(prefix="boardgame-ledger-")
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)
        self.config = Config()
        self.service = self.make_service()

    def make_service(self, config=None):
        return plugin.BoardgameService(config or self.config,
                                       lambda name: self.root / name, Logger())

    def use(self, **kwargs):
        self.service._game = fixture(**kwargs)
        return self.service._game

    def last(self):
        return self.service.state_payload()["actionRecords"][-1]

    def test_rent_tracks_both_cash_and_original_story_once(self):
        game = self.use(owner="sakura")
        before = dict(game.cash)
        result = self.service.roll({"player": "user"})
        row = self.last()
        rent = game._rent(game.cells[1])
        self.assertEqual(row["actor"], "user")
        self.assertEqual(row["action"], "roll")
        self.assertEqual(row["players"]["user"]["cash"],
                         {"before": before["user"], "after": before["user"] - rent, "delta": -rent})
        self.assertEqual(row["players"]["sakura"]["cash"]["delta"], rent)
        self.assertEqual(row["story"], result["story"])
        self.assertEqual(row["properties"], [])
        for story in result["story"]:
            self.assertEqual(list(self.service._log).count(story), 1)

    def test_buy_upgrade_mortgage_redeem_sell(self):
        game = self.use()
        self.service.roll({"player": "user"})
        for choice, cash_delta, field, old, new in [
            ("buy", -60, "owner", None, "user"),
            ("upgrade", -30, "level", 0, 1),
            ("mortgage", 30, "mortgaged", False, True),
            ("redeem", -36, "mortgaged", True, False),
            ("mortgage", 30, "mortgaged", False, True),
            ("sell", 10, "owner", "user", None),
        ]:
            game.turn = "user"
            game.pending = {"type": "land", "player": "user", "cell": 1, "options": [choice]}
            self.service.decide({"action": choice})
            row = self.last()
            self.assertEqual(row["players"]["user"]["cash"]["delta"], cash_delta)
            self.assertEqual(row["players"]["sakura"]["cash"]["delta"], 0)
            prop = row["properties"][0]
            self.assertEqual(prop["cell"], 1)
            self.assertEqual(prop["changed"][field], {"before": old, "after": new})
        self.assertEqual(self.last()["properties"][0]["after"],
                         {"owner": None, "level": 0, "mortgaged": False})

    def test_effect_acquisition_consumption_and_inventory(self):
        game = self.use(kind="event")
        game._draw_card = lambda: {"title": "free", "flavor": "免租", "effect": {"type": "rent_free"}}
        self.service.roll({"player": "user"})
        self.assertEqual(self.last()["players"]["user"]["effects"]["changed"]["rent_free"],
                         {"before": False, "after": True})
        acquired = copy.deepcopy(self.last())
        game.positions["user"] = 0
        game.turn = "user"
        game.cells[1].update(type="property", owner="sakura", price=60, tier=1, group="daily")
        self.service.roll({"player": "user"})
        self.assertEqual(self.last()["players"]["user"]["effects"]["changed"]["rent_free"],
                         {"before": True, "after": False})
        self.assertEqual(self.last()["players"]["user"]["cash"]["delta"], 0)
        self.assertEqual(game.action_records[0], acquired, "ledger must not alias mutable flags")
        game.turn = "user"
        game.pending = {"type": "shop", "player": "user", "cell": 1, "options": ["buy_dice"]}
        self.service.decide({"action": "buy_dice"})
        self.assertEqual(self.last()["players"]["user"]["inventory"]["changed"]["pick_dice"],
                         {"before": False, "after": True})
        game.positions["user"] = 1
        game.turn = "user"
        self.service.roll({"player": "user", "use_custom_die": True})
        self.service.decide({"action": "1"})
        self.assertEqual(self.last()["players"]["user"]["inventory"]["changed"]["pick_dice"],
                         {"before": True, "after": False})

    def test_buff_and_skip_tick_are_effect_changes(self):
        game = self.use(kind="shop")
        game._dice_rng = lambda: Dice(6)
        game.pending = {"type": "shop", "player": "user", "cell": 1, "options": ["buy_gain"]}
        self.service.decide({"action": "buy_gain"})
        effects = self.last()["players"]["user"]["effects"]
        self.assertEqual(effects["after"]["buff"], "gain")
        self.assertEqual(effects["after"]["buff_left"], 3)
        game.turn = "user"
        game.skip_pending["user"] = True
        self.service.roll({"player": "user"})
        self.assertEqual(self.last()["players"]["user"]["effects"]["changed"]["skip_pending"],
                         {"before": True, "after": False})

    def test_auto_opponent_roll_and_decision_are_separate_records(self):
        game = self.use(actor="sakura")
        self.service._pick_pending_action = lambda game: "buy"
        self.service.sakura_action()
        rows = self.service.state_payload()["actionRecords"]
        self.assertEqual([r["action"] for r in rows], ["roll", "decide"])
        self.assertEqual([r["actor"] for r in rows], ["sakura", "sakura"])
        self.assertEqual([r["turn"] for r in rows], [1, 1])
        self.assertEqual(game.move_count, 1)
        self.assertTrue(all(r["source"] == "auto" for r in rows))
        self.assertEqual(rows[-1]["properties"][0]["after"]["owner"], "sakura")
        self.assertEqual(rows[-1]["players"]["sakura"]["cash"]["delta"], -60)
        self.service.auto({"rounds": 2})
        rows = self.service.state_payload()["actionRecords"]
        self.assertTrue(any(r["actor"] == "user" and r["source"] == "auto" for r in rows))
        self.assertEqual([r["id"] for r in rows], list(range(1, len(rows) + 1)))

    def test_each_tower_floor_persisted_duplicates_and_pause(self):
        game = self.use(kind="tower")
        self.service.roll({"player": "user"})
        self.assertEqual(game.pending["type"], "tower")
        self.assertEqual(self.last()["players"]["user"]["cash"]["delta"], 0)
        game._dice_rng = lambda: Dice(6)
        game._draw_card = lambda: {"title": "rent", "flavor": "收款", "effect": {"type": "collect_each", "amount": 80}}
        seq = game._tower["seq"]
        for at in (-1, 0, 1):
            before = dict(game.cash)
            self.service.tower_action({"seq": seq, "floor": at})
            row = self.last()
            self.assertEqual(row["action"], "tower_step")
            self.assertEqual(row["context"]["floor"], at + 1)
            for seat in engine.PLAYERS:
                self.assertEqual(row["players"][seat]["cash"]["delta"], game.cash[seat] - before[seat])
            saved = copy.deepcopy(game.action_records)
            duplicate = self.service.tower_action({"seq": seq, "floor": at})
            self.assertTrue(duplicate["duplicate"])
            self.assertEqual(game.action_records, saved)
            self.assertEqual(game.pending["type"], "tower")
        self.assertEqual(game.action_records[1]["players"]["user"]["cash"]["delta"], 80)
        self.assertEqual(game.action_records[1]["players"]["sakura"]["cash"]["delta"], -80)
        restored = self.make_service()
        self.assertEqual(restored._game.action_records, game.action_records)
        self.assertEqual(restored._game.pending["type"], "tower")
        self.service.tower_action({"seq": seq, "floor": 2}, finish=True)
        self.assertEqual(self.last()["action"], "tower_finish")
        self.assertEqual([row["turn"] for row in game.action_records], [1] * 5)
        self.assertTrue(self.last()["turn_complete"])
        self.assertEqual(game.move_count, 1)
        self.assertEqual(game.turn, "sakura")
        count = len(game.action_records)
        self.service.tower_action({"seq": seq, "floor": 2}, finish=True)
        self.assertEqual(len(game.action_records), count)
        self.service.undo()
        self.assertEqual(self.service._game.action_records, [])
        self.assertIsNone(self.service._game._tower)

    def test_tower_tool_decisions_have_same_ledger(self):
        game = self.use(kind="tower", actor="sakura")
        self.service.sakura_action()
        self.assertEqual(len(game.action_records), 1, "automatic opponent must pause on entry")
        for index in range(3):
            self.service.decide({"player": "sakura", "action": "tower_next"})
            self.assertEqual(self.last()["context"]["floor"], index)
        self.service.decide({"player": "sakura", "action": "tower_finish"})
        self.assertEqual([r["action"] for r in game.action_records],
                         ["roll", "tower_step", "tower_step", "tower_step", "tower_finish"])

    def test_undo_and_save_restore_ownership_records_and_numbering(self):
        game = self.use()
        self.service.roll({"player": "user"})
        self.service.decide({"action": "buy"})
        self.assertEqual(game.cells[1]["owner"], "user")
        restored = self.make_service()
        self.assertEqual(restored._game.action_records, game.action_records)
        self.service.undo()
        self.assertIsNone(self.service._game.cells[1]["owner"], "snapshot must deep-copy cells")
        self.assertEqual(self.service.state_payload()["actionRecords"], [])
        self.assertEqual(self.make_service()._game.action_seq, 0)
        self.service._game._dice_rng = lambda: Dice()
        self.service.roll({"player": "user"})
        self.assertEqual(self.last()["id"], 1)

    def test_bounded_records_and_payload_are_detached(self):
        game = self.use(kind="rest")
        # Isolate record retention from the separately tested scheduled journey.
        game.black_train_schedule['entered'] = True
        for _ in range(engine.ACTION_RECORD_LIMIT + 3):
            game.skip_pending[game.turn] = True
            self.service.roll({"player": game.turn})
        self.assertEqual(len(game.action_records), engine.ACTION_RECORD_LIMIT)
        self.assertEqual(game.action_records[0]["id"], 4)
        self.assertEqual(game.action_seq, engine.ACTION_RECORD_LIMIT + 3)
        restored = self.make_service()
        self.assertEqual(restored._game.action_records, game.action_records)
        payload = self.service.state_payload()
        payload["actionRecords"][0]["players"]["user"]["cash"]["after"] = -999
        self.assertNotEqual(game.action_records[0]["players"]["user"]["cash"]["after"], -999)

    def test_new_game_clears_previous_movement_and_ledger(self):
        self.use(owner="sakura")
        self.service.roll({"player": "user"})
        self.assertEqual(self.service._last_event["path"], [0, 1])
        self.service.start({"game": "monopoly", "first": "user"}, skip_open=True)
        payload = self.service.state_payload()
        self.assertIsNone(payload["lastEvent"])
        self.assertEqual(payload["actionRecords"], [])
        self.assertEqual(self.service._game.action_seq, 0)

    def test_invalid_actions_do_not_record(self):
        self.use()
        with self.assertRaises(engine.GameError):
            self.service.roll({"player": "sakura"})
        self.assertEqual(self.service.state_payload()["actionRecords"], [])
        self.service.roll({"player": "user"})
        with self.assertRaises(engine.GameError):
            self.service.decide({"action": "bogus"})
        self.assertEqual(len(self.service._game.action_records), 1)

    def test_legacy_random_save_read_without_rebuilding_or_rewriting(self):
        data = fixture(kind="rest", map_kind="random").to_dict()
        data.pop("action_records")
        data.pop("action_seq")
        path = self.root / plugin.STATE_FILE
        path.write_text(json.dumps(data, ensure_ascii=False), encoding="utf-8")
        original = path.read_bytes()
        old_generate = engine.MonopolyGame._generate_map
        engine.MonopolyGame._generate_map = lambda _: (_ for _ in ()).throw(AssertionError("must not generate on restore"))
        try:
            service = self.make_service(Config(map_kind="random", track_length=96))
        finally:
            engine.MonopolyGame._generate_map = old_generate
        self.assertEqual(path.read_bytes(), original, "reading must never rewrite an old save")
        game = service._game
        self.assertEqual(game.map_kind, "random")
        self.assertEqual(game.cells, data["cells"])
        self.assertEqual(game.edges, data["edges"])
        self.assertEqual(set(game.next_nodes(0)), {1, 3})
        self.assertEqual(service.state_payload()["settingMapKind"], "city")
        self.assertEqual(service.state_payload()["actionRecords"], [])
        game._dice_rng = lambda: Dice()
        service.roll({"player": "user"})
        service.decide({"action": "1"})
        self.assertEqual(len(game.action_records), 2)
        self.assertFalse(service.handle_action("set_map_kind", {"kind": "random"})["ok"])
        service.start({"game": "monopoly"}, skip_open=True)
        self.assertEqual(service._game.map_kind, "city")

    def test_engine_new_maps_default_city_and_ignore_obsolete_size(self):
        self.assertEqual(engine.MAP_VERSION, 13)
        for kind in ("city", "random", "unknown"):
            game = engine.MonopolyGame(size=96, dice_sides=6, seed=1, map_kind=kind)
            self.assertEqual(game.map_kind, "city")
            self.assertEqual(game.cells, engine.MonopolyGame(size=12, dice_sides=6, seed=9).cells)
        self.assertEqual(engine.create_game("monopoly", "user", 6, 96).map_kind, "city")
        self.assertEqual(engine.MonopolyGame(size=96, dice_sides=6, seed=1, map_kind="grid").map_kind, "grid")


if __name__ == "__main__":
    unittest.main(verbosity=2)
