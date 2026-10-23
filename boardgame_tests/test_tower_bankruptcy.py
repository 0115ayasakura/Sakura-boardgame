"""Terminal tower regressions: deterministic cards, temporary saves, no host/server."""
from __future__ import annotations

import copy
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import patch

sys.dont_write_bytecode = True
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "sakura_boardgame"))
sys.path.insert(0, str(Path(__file__).resolve().parent))
import engine
import plugin
from test_action_ledger import Config, Logger


LOSS = {"title": "bankruptcy regression", "flavor": "现金损失",
        "effect": {"type": "cash", "amount": -100}}
MOVE = {"title": "deferred move", "effect": {"type": "goto_start"}}


def tower_game(actor="sakura", floor=0):
    game = engine.MonopolyGame(size=28, dice_sides=6, seed=5, map_kind="grid")
    gate = next(c["id"] for c in game.cells if c["type"] == "tower")
    game.turn = actor
    game.positions[actor] = gate
    game.cash[actor] = 1
    game._enter_device(actor, gate, [])
    # Retain the real three-floor shape; preceding rooms have no random effects.
    for index, room in enumerate(game.submap["cells"]):
        room["type"] = "event" if index == floor else "rest"
    game._draw_card = lambda: copy.deepcopy(LOSS)
    for expected in range(-1, floor - 1):
        game.advance_tower(actor, game._tower["seq"], expected)
    game._apply_card(actor, MOVE, "", [])
    return game, gate


class TowerBankruptcyTests(unittest.TestCase):
    def assert_closed(self, game, actor):
        self.assertEqual(game.winner, game._other(actor))
        self.assertIsNone(game.pending)
        self.assertFalse(game._tower["active"])
        self.assertEqual(game._tower["deferred"], [])
        self.assertIsNone(game.submap)
        self.assertEqual(game.turn, actor)  # Terminal cleanup is not a new turn.

    def test_bankruptcy_closes_immediately_for_both_seats_at_any_floor(self):
        for actor in ("user", "sakura"):
            for floor in range(3):
                with self.subTest(actor=actor, floor=floor):
                    game, gate = tower_game(actor, floor)
                    visits = game._device_visits
                    with patch.object(game, "_tick_buff", side_effect=AssertionError("terminal tick")), \
                         patch.object(game, "_pass_turn", side_effect=AssertionError("terminal turn")), \
                         patch.object(game, "_effect_rng", side_effect=AssertionError("later RNG")):
                        result = game.advance_tower(actor, game._tower["seq"], floor - 1)
                    self.assert_closed(game, actor)
                    self.assertIsNone(result["pending"])
                    self.assertIsNone(result["next"])
                    self.assertEqual(result["winner"], game._other(actor))
                    self.assertTrue(any("破产" in line for line in result["story"]))
                    self.assertEqual(game.positions[actor], gate)  # No deferred movement.
                    self.assertEqual(game._device_visits, visits + 1)
                    self.assertEqual(game._tower["at"], floor)
                    rows = game._tower["floors"]
                    self.assertEqual([r["resolved"] for r in rows], [i <= floor for i in range(3)])
                    self.assertEqual(rows[floor]["result"], "navi_card")
                    self.assertEqual(rows[floor]["amount"], -1)
                    self.assertIn("破产", rows[floor]["note"])
                    state = copy.deepcopy(game.to_dict())
                    duplicate = game.advance_tower(actor, game._tower["seq"], floor, finish=True)
                    self.assertTrue(duplicate["duplicate"])
                    self.assertEqual(game.to_dict(), state)
                    for args in ((game._other(actor), game._tower["seq"]),
                                 (actor, game._tower["seq"] + 1)):
                        with self.assertRaises(engine.GameError):
                            game.advance_tower(*args, floor, finish=True)
                    with self.assertRaises(engine.GameError):
                        game.advance_tower(actor, game._tower["seq"], floor)
                    restored = engine.restore_game(copy.deepcopy(state))
                    self.assertEqual(restored.winner, game.winner)
                    self.assertIsNone(restored.pending)
                    self.assertIsNone(restored.submap)
                    self.assertFalse(restored._tower and restored._tower.get("active"))

    def test_restore_repairs_old_terminal_tower_without_replaying_effects(self):
        for actor in ("user", "sakura"):
            with self.subTest(actor=actor):
                game, _ = tower_game(actor)
                state = copy.deepcopy(game.to_dict())
                state["winner"] = game._other(actor)
                state["cash"][actor] = 0
                state["tower_session"]["at"] = 0
                state["tower_session"]["floors"][0].update(resolved=True, result="navi_card", amount=-1)
                state["pending"].update(floor=0, options=["tower_finish"])
                original = copy.deepcopy(state)
                restored = engine.restore_game(state)
                self.assert_closed(restored, actor)
                self.assertEqual(restored.cash, state["cash"])
                self.assertEqual(restored.positions, state["positions"])
                self.assertEqual(restored._tower["floors"], state["tower_session"]["floors"])
                self.assertEqual(state, original)  # Read-only migration of the supplied snapshot.

    def test_service_user_and_agent_bankruptcy_need_no_finish_permission(self):
        class Chat:
            def current(self):
                return {"sessionId": "tower-session", "characterId": "tower-character", "idle": True}

            def submit(self, request):
                return {"accepted": True, "operationId": "tower-operation"}

        class Context:
            def get(self, name):
                return Chat() if name == "sakura.host.chat" else None

        for actor in ("user", "sakura"):
            with self.subTest(actor=actor), tempfile.TemporaryDirectory(prefix="tower-bankruptcy-") as root:
                service = plugin.BoardgameService(Config(decision_driver="agent"),
                                                  lambda name: Path(root) / name, Logger(), context=Context())
                game, _ = tower_game(actor)
                service._game = game
                service._opponent["id"] = "tower-character"
                args = {"seq": game._tower["seq"], "floor": -1}
                if actor == "user":
                    result = service.tower_action(args)
                else:
                    # Ordinary calls still cannot impersonate the Agent-controlled seat.
                    before = copy.deepcopy(game.to_dict())
                    with self.assertRaises(engine.GameError):
                        service.tower_action(args)
                    self.assertEqual(game.to_dict(), before)
                    work = []
                    service._agent_turns.launch = work.append
                    response = service.handle_action("act_sakura", {}, metadata={
                        "requestId": "tower-start", "gameSeq": service._game_seq,
                        "expectedVersion": service._version})
                    self.assertTrue(response["ok"], response)
                    self.assertEqual(len(work), 1)
                    work.pop()()
                    ticket = service._agent_turns.ticket
                    self.assertEqual(ticket["status"], "thinking")
                    self.assertEqual(ticket["legal"], {"action": "tower_step", "arguments": args})
                    commit = {"token": ticket["token"], "action": "tower_step", "arguments": args}
                    result = service._agent_turns.commit(commit)
                    self.assertEqual(result["agentDecision"]["status"], "committed")
                    self.assertTrue(service._agent_turns.commit(commit)["duplicate"])
                    with self.assertRaises(engine.GameError):
                        service._agent_turns.commit({**commit, "action": "tower_finish"})
                    with self.assertRaises(engine.GameError):
                        service._agent_turns.legal()
                    with self.assertRaises(engine.GameError):
                        service.tower_action({"seq": args["seq"], "floor": 0}, finish=True)
                game = service._game  # Rejected transactions may restore the engine instance.
                self.assert_closed(game, actor)
                self.assertTrue(result["ok"])
                self.assertFalse(result["tower"]["active"])
                self.assertEqual(game.action_records[-1]["action"], "tower_step")
                self.assertTrue(game.action_records[-1]["turn_complete"])


if __name__ == "__main__":
    unittest.main()
