"""地图规模 × 人数的适配性：2 人局在不同格数的地图上，经济能不能"铺满"。

用随机地图（格数可控）扫一遍，每组跑两批：
  greedy vs greedy → 看活跃度（租金事件、垄断、被买走的地、圈数）
  greedy vs hoard  → 看平衡（买地的一方是不是还吃亏）
指标口径：圈数 = 每人领工资次数；地产总数/被买走 = 终局归属。
"""
from __future__ import annotations

import random
import statistics
import sys
import types
from collections import Counter
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "sakura_boardgame"))
import engine as E  # noqa: E402

SIZES = [int(x) for x in (sys.argv[1:] or ["16", "22", "28", "36", "48"])]
GAMES = 120


def play(seed: int, size: int, policy: dict[str, str]) -> dict:
    game = E.MonopolyGame(size=size, dice_sides=6, seed=seed + size * 1000, map_kind="random")
    cash_start = dict(game.cash)
    rng = random.Random(seed)
    stats = {"rent": 0, "mono": set(), "salary": Counter(), "buy": Counter(),
             "bankrupt": False, "cells_moved": 0}
    orig_salary = game._grant_salary
    orig_landing = game._resolve_landing
    orig_buy = game.decide
    orig_pay = game._pay

    def pay(player, amount, beneficiary, story):
        res = orig_pay(player, amount, beneficiary, story)
        if res["bankrupt"]:
            stats["bankrupt"] = True
        return res

    def salary(player, story):
        stats["salary"][player] += 1
        return orig_salary(player, story)

    def landing(player, cell_index, allow_navi, story):
        res = orig_landing(player, cell_index, allow_navi, story)
        if res.get("kind") == "rent":                  # 只数"真的付了租金"的次数
            stats["rent"] += 1
            owner = game.cells[cell_index]["owner"]
            for g in game._player_monopolies(owner):
                stats["mono"].add((owner, g))
        return res

    def decide(player, action):
        if action == "buy":
            stats["buy"][player] += 1
        return orig_buy(player, action)

    game._pay = pay
    game._grant_salary = salary
    game._resolve_landing = landing
    game.decide = decide

    moves = 0
    while game.winner is None and moves < 4000:
        pending = game.pending
        if pending:
            pol = policy[pending["player"]]
            if pending["type"] == "route":
                game.decide(pending["player"], str(rng.choice(pending["options"])))
            else:
                options = pending["options"]
                cell = game.cells[pending["cell"]]
                price = game.current_price(cell)
                if pol == "greedy" and "buy" in options and game.cash[pending["player"]] >= price:
                    game.decide(pending["player"], "buy")
                elif pol == "greedy" and "upgrade" in options and game.cash[pending["player"]] >= price // 2:
                    game.decide(pending["player"], "upgrade")
                else:
                    game.decide(pending["player"], "skip")
        else:
            game.roll(game.turn)
        moves += 1
    props = [c for c in game.cells if c["type"] == "property"]
    return {
        "props": len(props), "owned": sum(1 for c in props if c["owner"]),
        "rent": stats["rent"], "mono": len(stats["mono"]),
        "laps": sum(stats["salary"].values()) / 2,
        "moves": game.move_count, "cap": game.max_rounds,
        "winner": game.winner, "cash": dict(game.cash), "start": cash_start,
        "bankrupt": stats["bankrupt"],
        "props_value": sum(c["price"] for c in props),
        "money": sum(cash_start.values()) + sum(stats["salary"].values()) * E.MONOPOLY_PASS_START,
    }


print(f"{'格数':>4}{'地产':>5}{'买走':>8}{'钱/地价':>11}{'圈数/人':>8}{'租金件':>7}"
      f"{'垄断':>6}{'破产':>7}{'步数':>6}{'买地胜率':>9}")
for size in SIZES:
    act = [play(1000 + i, size, {"user": "greedy", "sakura": "greedy"}) for i in range(GAMES)]
    bal = [play(2000 + i, size, {"user": "greedy", "sakura": "hoard"}) for i in range(GAMES)]
    n = len(act)
    props = statistics.mean(g["props"] for g in act)
    owned = statistics.mean(g["owned"] for g in act)
    value = statistics.mean(g["props_value"] for g in act)
    money = statistics.mean(g["money"] for g in act)
    laps = statistics.mean(g["laps"] for g in act)
    rent = statistics.mean(g["rent"] for g in act)
    mono = 100.0 * sum(1 for g in act if g["mono"]) / n
    bk = 100.0 * sum(1 for g in act if g["bankrupt"]) / n
    moves = statistics.median(g["moves"] for g in act)
    buywin = 100.0 * sum(1 for g in bal if g["winner"] == "user") / n
    print(f"{size:>4}{props:>5.0f}{owned:>8.1f}{money / max(1, value):>11.2f}{laps:>8.2f}"
          f"{rent:>7.2f}{mono:>5.0f}%{bk:>6.0f}%{moves:>6.0f}{buywin:>8.0f}%")
print("\n钱/地价 <1 表示「一局里的钱买不下整张棋盘」；圈数/人 = 每人平均领工资次数（约等于绕环圈数）")
