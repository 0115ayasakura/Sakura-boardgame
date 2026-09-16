"""一局到底走了多少格、绕了几圈、掷了几次骰——用于判断"57 回合"的节奏。"""
import random
import sys
import types
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "sakura_boardgame"))
import engine as E  # noqa: E402

CELLS = []
LAPS = 0


def play(seed: int) -> dict:
    game = E.MonopolyGame(size=28, dice_sides=6, seed=seed, map_kind="city")
    stat = {"cells": {p: 0 for p in E.PLAYERS}, "rolls": {p: 0 for p in E.PLAYERS},
            "salary": {p: 0 for p in E.PLAYERS}, "decisions": 0}
    orig_roll = game.roll
    orig_step = game._step_to
    orig_decide = game.decide
    orig_salary = game._grant_salary

    def roll(player):
        stat["rolls"][player] += 1
        return orig_roll(player)

    def step_to(node, story):
        stat["cells"][game._walk["player"]] += 1
        return orig_step(node, story)

    def decide(player, action):
        stat["decisions"] += 1
        return orig_decide(player, action)

    def salary(player, story):
        stat["salary"][player] += 1
        return orig_salary(player, story)

    game.roll = roll
    game._step_to = step_to
    game.decide = decide
    game._grant_salary = salary

    rng = random.Random(seed)
    moves = 0
    while game.winner is None and moves < 500:
        pending = game.pending
        if pending:
            if pending["type"] == "route":
                game.decide(pending["player"], str(rng.choice(pending["options"])))
            else:
                options = pending["options"]
                cell = game.cells[pending["cell"]]
                price = game.current_price(cell)
                if "buy" in options and game.cash[pending["player"]] >= price:
                    game.decide(pending["player"], "buy")
                elif "upgrade" in options and game.cash[pending["player"]] >= price // 2:
                    game.decide(pending["player"], "upgrade")
                else:
                    game.decide(pending["player"], "skip")
        else:
            game.roll(game.turn)
        moves += 1

    stat["move_count"] = game.move_count
    stat["max_rounds"] = game.max_rounds
    stat["ring_nodes"] = len([n for n in game.main_path]) - 1
    stat["winner"] = game.winner
    stat["cash"] = dict(game.cash)
    stat["props"] = {p: sum(1 for c in game.cells if c["type"] == "property" and c["owner"] == p)
                     for p in E.PLAYERS}
    return stat


rows = [play(1000 + i) for i in range(40)]
print(f"平均：move_count {sum(r['move_count'] for r in rows) / len(rows):.1f}"
      f" / 上限 {rows[0]['max_rounds']}（主环 {rows[0]['ring_nodes']} 格）")
for p in E.PLAYERS:
    print(f"  {E.PLAYER_LABEL[p]}：掷骰 {sum(r['rolls'][p] for r in rows) / len(rows):.1f} 次"
          f"｜移动 {sum(r['cells'][p] for r in rows) / len(rows):.1f} 格"
          f"｜领工资 {sum(r['salary'][p] for r in rows) / len(rows):.2f} 次"
          f"（= 绕环 "
          f"{sum(r['salary'][p] for r in rows) / len(rows):.2f} 圈）"
          f"｜终局地产 {sum(r['props'][p] for r in rows) / len(rows):.1f} 处")
print(f"决策（买地/升级/选路）占 move_count 的比例："
      f"{sum(r['decisions'] for r in rows) / sum(r['move_count'] for r in rows):.0%}")
