"""单局逐笔流水：确认工资/租金/买地的实际金额与次数统计口径是对的。"""
import random
import sys
import types
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "sakura_boardgame"))
import engine as E  # noqa: E402

game = E.MonopolyGame(size=28, dice_sides=6, seed=4242, map_kind="city")
log = []
rng = random.Random(4242)


def wrap(name, fn):
    orig = getattr(game, name)
    setattr(game, name, types.MethodType(lambda _s, *a, **k: fn(orig, *a, **k), game))


def on_salary(orig, player, story):
    before = game.cash[player]
    orig(player, story)
    log.append(("工资", player, game.cash[player] - before, story[-1]))


def on_pay(orig, player, amount, beneficiary, story):
    n = len(story)
    res = orig(player, amount, beneficiary, story)
    log.append(("付款", player, -res["paid"], f"应付 {amount} → {beneficiary}｜" + " / ".join(story[n:])))
    return res


def on_decide(orig, player, action):
    cell = game.cells[game.pending["cell"]] if (game.pending and "cell" in game.pending) else None
    res = orig(player, action)
    if action in ("buy", "upgrade", "mortgage", "redeem", "sell"):
        log.append(("决策", player, 0, f"{action}「{cell['name'] if cell else '?'}」现价 "
                    f"{game.current_price(cell) if cell else '?'} 手头 ¥{game.cash[player]}"))
    return res


def on_landing(orig, player, cell_index, allow_navi, story):
    res = orig(player, cell_index, allow_navi, story)
    if res.get("kind") in ("rent", "tax", "train", "monster", "cannot_afford", "offer_buy"):
        log.append(("落点", player, res.get("amount", 0) if isinstance(res.get("amount"), int) else 0,
                    f"{res['kind']} @{game.cells[cell_index]['name']}"))
    return res


wrap("_grant_salary", on_salary)
wrap("_pay", on_pay)
wrap("decide", on_decide)
wrap("_resolve_landing", on_landing)

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

print(f"结局：{game.winner}  回合 {game.move_count}/{game.max_rounds}  现金 {game.cash}")
print("\n逐笔流水（前 60 条）：")
for kind, who, amount, note in log[:60]:
    print(f"  {kind} {E.PLAYER_LABEL[who]:<4}{amount:+6d}  {note}")

sal = [x for x in log if x[0] == "工资"]
print(f"\n工资事件 {len(sal)} 次，合计 ¥{sum(x[2] for x in sal)}，明细：{[x[2] for x in sal]}")
rent = [x for x in log if x[0] == "落点" and x[3].startswith("rent")]
print(f"租金事件 {len(rent)} 次，合计 ¥{-sum(x[2] for x in rent)}")
buy = [x for x in log if x[0] == "决策" and "buy" in x[3]]
print(f"买地 {len(buy)} 次")
print(f"付款总额 ¥{-sum(x[2] for x in log if x[0] == '付款')}")
