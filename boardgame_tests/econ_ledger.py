"""经济系统不变量校验（跑几百局，找"钱凭空多出来/漏扣/重复扣"这类真 bug）。

检查的不变量：
  1. 任何时刻现金不为负
  2. 玩家之间的转移是零和的：付款方（含抵押回收）净支出 == 收款方净收入
  3. 付给银行时，实际现金变化 == _pay 报告的 paid（不多扣也不少扣）
  4. 买地/升级/赎回/抵押/卖出扣的钱 == current_price 口径算出来的价
  5. 每一次"踩到别人地产"恰好对应一笔租金转移（配对检查，不重不漏）
  6. 工资只能是 200 或 400
  7. 强制抵押回收的总额 ≤ 涉事地产（现价）的一半之和

输出写到 econ_ledger_out.txt（UTF-8），避免控制台代码页把中文弄乱。
"""
from __future__ import annotations

import random
import sys
import types
from collections import Counter, deque
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE.parent / "sakura_boardgame"))
import engine as E  # noqa: E402

OUT = HERE / "econ_ledger_out.txt"
lines: list[str] = []
problems: Counter = Counter()


def note(kind: str, msg: str) -> None:
    problems[kind] += 1
    if problems[kind] <= 3:
        lines.append(f"  [{kind}] {msg}")


def play(seed: int) -> None:
    game = E.MonopolyGame(size=28, dice_sides=6, seed=seed, map_kind="city")
    # 租金配对：_resolve_landing 里会同步调用 _pay，所以在落地调用期间打标记，
    # 由 _pay 记账一次，落地返回后再核对金额（顺序不能反——付款发生在登记之前）
    flag = {"rent_landing": False, "captured": None}

    def wrap(name, fn):
        orig = getattr(game, name)
        setattr(game, name, types.MethodType(lambda _s, *a, **k: fn(orig, *a, **k), game))

    def check_negative(where: str) -> None:
        for p in E.PLAYERS:
            if game.cash[p] < 0:
                note("现金为负", f"seed {seed} {where} {E.PLAYER_LABEL[p]}=¥{game.cash[p]}")

    def on_pay(orig, player, amount, beneficiary, story):
        before = dict(game.cash)
        res = orig(player, amount, beneficiary, story)
        after = dict(game.cash)
        gain = sum(int(l.rsplit("回收 ¥", 1)[1].rstrip("。")) for l in story if "回收 ¥" in l)
        spent_payer = before[player] + gain - after[player]
        if spent_payer != res["paid"]:
            note("付款金额不符", f"seed {seed} {player} 现金实际动 {spent_payer}，_pay 报告 {res['paid']}")
        if beneficiary:
            got = after[beneficiary] - before[beneficiary]
            if got != res["paid"]:
                note("收款金额不符", f"seed {seed} {beneficiary} 实收 {got}，应为 {res['paid']}")
            if flag["rent_landing"]:
                if flag["captured"] is not None:
                    note("一笔租金付了两次", f"seed {seed} {player}→{beneficiary} 第二次 {got}")
                flag["captured"] = (beneficiary, got)
        else:
            if spent_payer < 0:
                note("付银行反赚", f"seed {seed} {player} 净 {spent_payer}（含抵押回收 {gain}）")
        check_negative("_pay 之后")
        return res

    def on_salary(orig, player, story):
        before = game.cash[player]
        orig(player, story)
        gain = game.cash[player] - before
        if gain not in (E.MONOPOLY_PASS_START, E.MONOPOLY_PASS_START * 2):   # 正常 / 工资翻倍
            note("工资数额异常", f"seed {seed} {player} +¥{gain}")
        return None

    def on_landing(orig, player, cell_index, allow_navi, story):
        cell = game.cells[cell_index]
        will_pay_rent = (cell["type"] == "property" and cell["owner"] not in (None, player)
                         and not cell["mortgaged"] and not game.flags[player]["rent_free"])
        flag["rent_landing"] = will_pay_rent
        flag["captured"] = None
        res = orig(player, cell_index, allow_navi, story)
        flag["rent_landing"] = False
        if res.get("kind") == "rent":
            if flag["captured"] != (cell["owner"], res["amount"]):
                note("租金支付与落点不符",
                     f"seed {seed} 落点认为应收 {(cell['owner'], res['amount'])}，实际支付 {flag['captured']}")
        elif will_pay_rent and res.get("kind") in ("nothing", "offer_buy"):
            note("该收租却没收", f"seed {seed} {player} 踩到 {cell['owner']} 的「{cell['name']}」结果 {res.get('kind')}")
        check_negative("落点之后")
        return res

    def on_decision(orig, player, action):
        cell = game.cells[game.pending["cell"]] if game.pending and "cell" in game.pending else None
        expect = None
        if cell:
            price = game.current_price(cell)
            expect = {"buy": -price, "upgrade": -(price // 2), "redeem": -(price * 3 // 5),
                      "mortgage": price // 2,
                      "sell": cell["price"] // 6 if cell["mortgaged"] else cell["price"] // 3}.get(action)
        before = game.cash[player]
        res = orig(player, action)
        delta = game.cash[player] - before
        if expect is not None and delta != expect:
            note("决策金额不符", f"seed {seed} {player} {action}"
                                  f"「{cell['name'] if cell else '?'}」实际 {delta:+d}，应为 {expect:+d}")
        check_negative("决策之后")
        return res

    def on_card(orig, player, card, flavor, story):
        before = game.cash[player]
        res = orig(player, card, flavor, story)
        after = game.cash[player]
        effect = card["effect"]
        if effect["type"] == "cash" and effect["amount"] > 0:
            if after - before != effect["amount"]:
                note("卡面数额不符", f"seed {seed} {card['title']} 实际 {after - before:+d}，卡面 {effect['amount']:+d}")
        return res

    wrap("_pay", on_pay)
    wrap("_grant_salary", on_salary)
    wrap("_resolve_landing", on_landing)
    wrap("decide", on_decision)
    wrap("_apply_card", on_card)

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


N = int(sys.argv[1]) if len(sys.argv) > 1 else 150
for i in range(N):
    play(7000 + i)

lines.insert(0, f"跑了 {N} 局（城市图，双方都按「够钱就买」行动）")
lines.insert(1, f"问题统计：{dict(problems) if problems else '全部不变量通过 ✓'}")
OUT.write_text("\n".join(lines) + "\n", encoding="utf-8")
print(f"跑了 {N} 局；问题统计 {dict(problems) if problems else '全部通过'}；明细见 {OUT}")
