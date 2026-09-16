"""双人局：把"一局里的钱"对齐棋盘总价，看地产系统能不能铺满（城市图 57 格 / 29 处地产）。

钱从哪来：起始资金（每人数值相同）+ 工资（每绕一圈 ¥200）。
棋盘总价（地价单位 60）= ¥3420。所以 2 人局的钱 ≈ 2×(起始 + 圈数×200)。
"""
from __future__ import annotations

import random
import statistics
import sys
from collections import Counter
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "sakura_boardgame"))
import engine as E  # noqa: E402

STARTS = [int(x) for x in (sys.argv[1:] or ["1000", "1400", "1800"])]
GAMES = 150


def play(seed: int) -> dict:
    game = E.MonopolyGame(size=28, dice_sides=6, seed=seed, map_kind="city")
    rng = random.Random(seed)
    stat = {"rent": 0, "salary": Counter(), "mono": set(), "bankrupt": False, "bought": Counter()}
    orig_landing, orig_salary, orig_pay, orig_buy = (
        game._resolve_landing, game._grant_salary, game._pay, game.decide)

    def landing(player, cell_index, allow_navi, story):
        res = orig_landing(player, cell_index, allow_navi, story)
        if res.get("kind") == "rent":
            stat["rent"] += 1
            owner = game.cells[cell_index]["owner"]
            for g in game._player_monopolies(owner):
                stat["mono"].add((owner, g))
        return res

    def salary(player, story):
        stat["salary"][player] += 1
        return orig_salary(player, story)

    def pay(player, amount, beneficiary, story):
        res = orig_pay(player, amount, beneficiary, story)
        stat["bankrupt"] = stat["bankrupt"] or bool(res["bankrupt"])
        return res

    def decide(player, action):
        if action == "buy":
            stat["bought"][player] += 1
        return orig_buy(player, action)

    game._resolve_landing, game._grant_salary, game._pay, game.decide = landing, salary, pay, decide

    moves = 0
    while game.winner is None and moves < 4000:
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
    props = [c for c in game.cells if c["type"] == "property"]
    laps = sum(stat["salary"].values()) / 2
    return {
        "owned": sum(1 for c in props if c["owner"]), "props": len(props),
        "rent": stat["rent"], "mono": len(stat["mono"]), "laps": laps,
        "bankrupt": stat["bankrupt"],
        "moves": game.move_count,
        "money": 2 * E.MONOPOLY_START_CASH + sum(stat["salary"].values()) * E.MONOPOLY_PASS_START,
        "value": sum(c["price"] for c in props),
    }


print(f"城市图 29 处地产 · 总价 ¥3420\n")
print(f"{'起始资金':>8}{'一局的钱':>9}{'钱/地价':>8}{'买走':>9}{'圈数/人':>8}{'租金件':>7}{'垄断':>6}{'破产':>7}{'步数':>6}")
for start in STARTS:
    E.MONOPOLY_START_CASH = start
    rows = [play(3000 + i) for i in range(GAMES)]
    n = len(rows)
    print(f"{start:>8}{statistics.mean(r['money'] for r in rows):>9.0f}"
          f"{statistics.mean(r['money'] / r['value'] for r in rows):>8.2f}"
          f"{statistics.mean(r['owned'] for r in rows):>5.1f}/{rows[0]['props']}"
          f"{statistics.mean(r['laps'] for r in rows):>8.2f}"
          f"{statistics.mean(r['rent'] for r in rows):>7.2f}"
          f"{100.0 * sum(1 for r in rows if r['mono']) / n:>5.0f}%"
          f"{100.0 * sum(1 for r in rows if r['bankrupt']) / n:>6.0f}%"
          f"{statistics.median(r['moves'] for r in rows):>6.0f}")
