"""完整大富翁对局跑批：看经济系统（现金/租金/工资/涨价/抵押/破产/回合上限）是否健康。

用法：python econ_report.py [局数] [玩家策略] [夜乃樱策略]
  策略：greedy（=网页上夜乃樱的买法：够钱就买、够钱就升级、其余跳过）
       hoard （从不买地，只攒现金——用来检验"回合上限按现金判胜"是否惩罚买地）
       tier3 （只买基础价 300 档的地产）
两者可以不同，用来做"买地 vs 攒钱"的对照实验。默认 200 局、双方都 greedy。
"""
from __future__ import annotations

import random
import statistics
import sys
import types
from collections import Counter, defaultdict
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "sakura_boardgame"))

import engine as E  # noqa: E402

SWEEP = sys.argv[1:2] == ["sweep"]
KNOWN = ("greedy", "hoard", "reserve", "tier3")
N = int(sys.argv[1]) if len(sys.argv) > 1 and not SWEEP else 200
POLICY = {"user": sys.argv[2] if len(sys.argv) > 2 else "greedy",
          "sakura": sys.argv[3] if len(sys.argv) > 3 else (sys.argv[2] if len(sys.argv) > 2 else "greedy")}
for who, pol in POLICY.items():
    if pol not in KNOWN:            # 参数写错就直接报错，别静默当成别的策略跑（踩过这个坑）
        raise SystemExit(f"策略只能从 {KNOWN} 里选，收到 sakura/user={pol!r}；"
                         f"用法：econ_report.py [局数] [玩家策略] [夜乃樱策略] [上限系数] [地产数]")
LABEL = f"玩家={POLICY['user']} / 夜乃樱={POLICY['sakura']}"
if len(sys.argv) > 4:                       # 第 4 个参数：回合上限系数（覆盖 MONOPOLY_ROUNDS_PER_CELL）
    E.MONOPOLY_ROUNDS_PER_CELL = float(sys.argv[4])
    LABEL += f"｜上限系数 {sys.argv[4]}"
if len(sys.argv) > 5:                       # 第 5 个参数：地产数量（覆盖 city_map.PROPERTY_TARGET）
    import city_map
    city_map.PROPERTY_TARGET = int(sys.argv[5])
    LABEL += f"｜地产 {sys.argv[5]} 处"
PRICE_SCALE = float(sys.argv[6]) if len(sys.argv) > 6 else 1.0
if PRICE_SCALE != 1.0:                      # 第 6 个参数：地价系数（生成后整体缩放，租金同步缩放）
    LABEL += f"｜地价 ×{PRICE_SCALE}"


def new_rec() -> dict:
    zero = {p: 0 for p in E.PLAYERS}
    return {
        "paid": dict(zero), "due": dict(zero), "got": dict(zero),
        "salary": dict(zero), "salary_count": dict(zero),
        "buy": dict(zero), "buy_count": dict(zero), "upgrade": dict(zero), "upgrade_count": dict(zero),
        "mortgage_gain": dict(zero), "mortgage_count": dict(zero),
        "sell_gain": dict(zero), "redeem": dict(zero),
        "forced_mortgage": dict(zero), "forced_mortgage_gain": dict(zero), "cant_afford": dict(zero),
        "rent_paid": dict(zero), "rent_got": dict(zero), "toll_paid": dict(zero),
        "rent_sizes": [], "rent_events_real": 0, "landings_by_cell": Counter(),
        "card_net": dict(zero), "card_count": dict(zero), "rare": dict(zero),
        "min_cash": dict(game_cash_initial()), "max_single_rent": 0, "rent_events": 0,
        "monopoly_rent_events": 0, "landing": Counter(), "cards": Counter(), "actions": Counter(),
        "peak_props": dict(zero), "monopoly_seen": {p: set() for p in E.PLAYERS},
        "bankrupt": None, "cash_path": defaultdict(list),
    }


def game_cash_initial() -> dict:
    return {p: E.MONOPOLY_START_CASH for p in E.PLAYERS}


def instrument(game, rec) -> None:
    def wrap(name, fn):
        orig = getattr(game, name)

        def wrapper(_self, *args, **kwargs):
            return fn(orig, *args, **kwargs)

        setattr(game, name, types.MethodType(wrapper, game))

    def on_pay(orig, player, amount, beneficiary, story):
        starved = game.cash[player] < amount          # 付不出 → 会被强制抵押
        mark = len(story)
        res = orig(player, amount, beneficiary, story)
        rec["paid"][player] += res["paid"]
        rec["due"][player] += res["due"]
        if beneficiary:
            rec["got"][beneficiary] += res["paid"]
        if starved:
            rec["forced_mortgage"][player] += 1
            for line in story[mark:]:                  # 从剧情里抠出强制抵押回收了多少钱
                if line.startswith("抵押 ") and "回收 ¥" in line:
                    rec["forced_mortgage_gain"][player] += int(line.rsplit("回收 ¥", 1)[1].rstrip("。"))
        if res["bankrupt"]:
            rec["bankrupt"] = player
        return res

    def on_salary(orig, player, story):
        before = game.cash[player]
        orig(player, story)
        rec["salary"][player] += game.cash[player] - before
        rec["salary_count"][player] += 1

    def on_card(orig, player, card, flavor, story):
        before = game.cash[player]
        res = orig(player, card, flavor, story)
        rec["card_net"][player] += game.cash[player] - before
        rec["card_count"][player] += 1
        rec["cards"][card["title"]] += 1
        if card.get("rare"):
            rec["rare"][player] += 1
        return res

    def on_rent(orig, cell):
        rent = orig(cell)
        rec["rent_events"] += 1
        rec["max_single_rent"] = max(rec["max_single_rent"], rent)
        if game._has_monopoly(cell["owner"], cell["group"]):
            rec["monopoly_rent_events"] += 1
        return rent

    def on_landing(orig, player, cell_index, allow_navi, story):
        res = orig(player, cell_index, allow_navi, story)
        kind = res.get("kind")
        rec["landing"][kind] += 1
        if kind == "cannot_afford":
            rec["cant_afford"][player] += 1
        if kind == "rent":
            amount = res.get("amount") or 0
            owner = game.cells[cell_index]["owner"]
            rec["rent_paid"][player] += amount
            if owner:
                rec["rent_got"][owner] += amount
            rec["rent_events_real"] += 1
            rec["rent_sizes"].append(amount)
        elif kind in ("tax", "train", "monster"):
            rec["toll_paid"][player] += res.get("amount") or 0
        rec["landings_by_cell"][cell_index] += 1
        return res

    def on_decide(orig, player, action):
        cell = game.cells[game.pending["cell"]] if (game.pending and "cell" in game.pending) else None
        price = game.current_price(cell) if cell else 0
        res = orig(player, action)
        if action == "buy":
            rec["buy"][player] += price
            rec["buy_count"][player] += 1
        elif action == "upgrade":
            rec["upgrade"][player] += price // 2
            rec["upgrade_count"][player] += 1
        elif action == "mortgage":
            rec["mortgage_gain"][player] += price // 2
            rec["mortgage_count"][player] += 1
        elif action == "redeem":
            rec["redeem"][player] += price * 3 // 5
        elif action == "sell":
            rec["sell_gain"][player] += cell["price"] // 6 if cell and cell["mortgaged"] else (
                cell["price"] // 3 if cell else 0)
        rec["actions"][action] += 1
        return res

    def on_finish(orig, story):
        orig(story)
        # 记录本局的"最高光时刻"：谁一度拥有最多地产 / 有没有凑成垄断
        for p in E.PLAYERS:
            own = sum(1 for c in game.cells if c["type"] == "property" and c["owner"] == p)
            rec["peak_props"][p] = max(rec["peak_props"][p], own)
            for g in game._player_monopolies(p):
                rec["monopoly_seen"][p].add(g)

    wrap("_pay", on_pay)
    wrap("_grant_salary", on_salary)
    wrap("_apply_card", on_card)
    wrap("_rent", on_rent)
    wrap("_resolve_landing", on_landing)
    wrap("decide", on_decide)
    wrap("_finish_walk", on_finish)


def choose(game, rng) -> str | None:
    """返回下一步决策动作；None 表示该掷骰。"""
    pending = game.pending
    if not pending:
        return None
    if pending["type"] == "route":
        return str(rng.choice(pending["options"]))
    options = pending["options"]
    policy = POLICY[pending["player"]]
    cell = game.cells[pending["cell"]]
    price = game.current_price(cell)
    if policy == "hoard":
        return "skip" if "skip" in options else options[0]
    if "buy" in options:
        if policy == "tier3" and cell["price"] < 300:      # 只看基础价档位
            return "skip"
        reserve = 250 if policy == "reserve" else 0        # 留点现金付账单再买
        if game.cash[pending["player"]] >= price + reserve:
            return "buy"
    if policy in ("greedy", "reserve") and "upgrade" in options:
        need = price // 2 + (250 if policy == "reserve" else 0)
        if game.cash[pending["player"]] >= need:
            return "upgrade"
    return "skip"


def run(seed: int) -> dict:
    game = E.MonopolyGame(size=28, dice_sides=6, seed=seed, map_kind="city")
    if PRICE_SCALE != 1.0:
        for c in game.cells:
            if c["type"] == "property":
                c["price"] = int(round(c["price"] * PRICE_SCALE / 10.0)) * 10
    rec = new_rec()
    instrument(game, rec)
    rng = random.Random(seed)
    moves = 0
    stuck = 0
    while game.winner is None and moves < 4000:
        action = choose(game, rng)
        try:
            if action is None:
                if game.pending or game._walk is not None:
                    stuck += 1
                    if stuck > 5:
                        break
                    continue
                stuck = 0
                game.roll(game.turn)
            else:
                game.decide(game.pending["player"], action)
        except E.GameError:
            break
        moves += 1
        for p in E.PLAYERS:
            rec["min_cash"][p] = min(rec["min_cash"][p], game.cash[p])

    rec["winner"] = game.winner
    rec["moves"] = game.move_count
    rec["max_rounds"] = game.max_rounds
    rec["cash"] = dict(game.cash)
    props = [c for c in game.cells if c["type"] == "property"]
    owned = [c for c in props if c["owner"]]
    rec["props_total"] = len(props)
    rec["props_owned"] = len(owned)
    rec["props_unbought"] = [c["name"] for c in props if not c["owner"]]
    rec["by_player"] = {
        p: {
            "count": sum(1 for c in props if c["owner"] == p),
            "value": sum(game.current_price(c) for c in props if c["owner"] == p),
            "mortgaged": sum(1 for c in props if c["owner"] == p and c["mortgaged"]),
            "monopolies": game._player_monopolies(p),
        } for p in E.PLAYERS
    }
    rec["ended_by"] = "bankrupt" if rec["bankrupt"] else ("cap" if game.winner else "hang")
    return rec


def pct(n, d) -> str:
    return f"{100.0 * n / max(1, d):.1f}%"


def main() -> None:
    games = [run(1000 + i) for i in range(N)]
    n = len(games)
    print(f"策略：{LABEL}   共 {n} 局   城市图")
    caps = [g for g in games if g["ended_by"] == "cap"]
    bks = [g for g in games if g["ended_by"] == "bankrupt"]
    hang = [g for g in games if g["ended_by"] == "hang"]
    print(f"\n结束方式：破产 {len(bks)}（{pct(len(bks), n)}）｜回合上限 {len(caps)}（{pct(len(caps), n)}）"
          f"｜未结束 {len(hang)}")
    print(f"回合数：中位 {statistics.median(g['moves'] for g in games):.0f}"
          f"  平均 {statistics.mean(g['moves'] for g in games):.1f}"
          f"  上限 {games[0]['max_rounds']}")

    wins = Counter(g["winner"] for g in games)
    print(f"胜者：玩家 {wins['user']}（{pct(wins['user'], n)}）｜夜乃樱 {wins['sakura']}（{pct(wins['sakura'], n)}）")
    print(f"终局现金：中位 {statistics.median(g['cash'][g['winner']] for g in games):.0f}"
          f"  最小 {min(g['cash'][g['winner']] for g in games)}"
          f"  最大 {max(g['cash'][g['winner']] for g in games)}")
    # 上限局里，赢家是靠现金还是靠地产赢的？
    if caps:
        richer = sum(1 for g in caps if g["cash"][g["winner"]] > g["cash"][
            "sakura" if g["winner"] == "user" else "user"])
        less_land = sum(1 for g in caps if g["by_player"][g["winner"]]["count"]
                        < g["by_player"]["sakura" if g["winner"] == "user" else "user"]["count"])
        print(f"  上限局里：赢家现金更多 {pct(richer, len(caps))}；赢家地产更少 {pct(less_land, len(caps))}"
              f"（改按总资产判定后，两条都该接近 50%——否则说明判定还是偏向某一头）")

    print("\n—— 钱从哪来、到哪去（每局平均，双方各一行）——")
    for p in E.PLAYERS:
        print(f"  {E.PLAYER_LABEL[p]}：买地 {statistics.mean(g['buy_count'][p] for g in games):.1f} 处"
              f" 花 ¥{statistics.mean(g['buy'][p] for g in games):.0f}"
              f"｜升级 {statistics.mean(g['upgrade_count'][p] for g in games):.1f} 次"
              f" ¥{statistics.mean(g['upgrade'][p] for g in games):.0f}"
              f"｜收租 ¥{statistics.mean(g['rent_got'][p] for g in games):.0f}"
              f"｜付租 ¥{statistics.mean(g['rent_paid'][p] for g in games):.0f}"
              f"｜缴税/车费 ¥{statistics.mean(g['toll_paid'][p] for g in games):.0f}"
              f"｜工资 ¥{statistics.mean(g['salary'][p] for g in games):.0f}"
              f"（{statistics.mean(g['salary_count'][p] for g in games):.1f} 次，"
              f"均值 ¥{statistics.mean(g['salary'][p] for g in games) / max(1e-9, statistics.mean(g['salary_count'][p] for g in games)):.0f}）"
              f"｜事件卡净 {statistics.mean(g['card_net'][p] for g in games):+.0f}"
              f"｜抵押回收 ¥{statistics.mean(g['mortgage_gain'][p] for g in games):.0f}")
    sizes = [s for g in games for s in g["rent_sizes"]]
    print(f"  地产投资回报：每局买地+升级共花 ¥{statistics.mean(g['buy']['user'] + g['buy']['sakura'] + g['upgrade']['user'] + g['upgrade']['sakura'] for g in games):.0f}，"
          f"同期收到的租金合计 ¥{statistics.mean(g['rent_got']['user'] + g['rent_got']['sakura'] for g in games):.0f}")
    print(f"  实际发生的租金支付：每局 {statistics.mean(g['rent_events_real'] for g in games):.2f} 笔，"
          f"单笔中位 ¥{statistics.median(sizes) if sizes else 0:.0f}，最高 ¥{max(sizes) if sizes else 0}")

    print("\n—— 压力指标 ——")
    print(f"  单笔最高租金（含预览计算）：中位 {statistics.median(g['max_single_rent'] for g in games):.0f}"
          f"  最高 {max(g['max_single_rent'] for g in games)}")
    print(f"  触发过垄断加成的租子：{pct(sum(1 for g in games if g['monopoly_rent_events']), n)} 的局")
    print(f"  凑成过垄断（某街区全持未抵押）：{pct(sum(1 for g in games if any(g['monopoly_seen'][p] for p in E.PLAYERS)), n)} 的局")
    print(f"  单方一度拥有地产峰值：平均 "
          f"{statistics.mean(max(g['peak_props'].values()) for g in games):.1f} 处"
          f"  最高 {max(max(g['peak_props'].values()) for g in games)} 处")
    print(f"  被迫抵押（付不出钱）发生的局：{pct(sum(1 for g in games if sum(g['forced_mortgage'].values())), n)}"
          f"  平均每局 {statistics.mean(sum(g['forced_mortgage'].values()) for g in games):.2f} 次"
          f"，其中回收现金 ¥{statistics.mean(sum(g['forced_mortgage_gain'].values()) for g in games):.0f}/局")
    print(f"  破产方终局：现金 {statistics.mean(g['cash'][g['bankrupt']] for g in bks) if bks else 0:.0f}"
          f"  破产前最低现金 {statistics.mean(g['min_cash'][g['bankrupt']] for g in bks) if bks else 0:.0f}")
    print(f"  双方都穷到买不起地：{pct(sum(1 for g in games if sum(g['cant_afford'].values())), n)} 的局"
          f"  平均 {statistics.mean(sum(g['cant_afford'].values()) for g in games):.1f} 次")
    print(f"  稀有卡（+800 / -620）出现：{pct(sum(1 for g in games if sum(g['rare'].values())), n)} 的局，"
          f"平均每局 {statistics.mean(sum(g['rare'].values()) for g in games):.2f} 张")
    print(f"  终局地产归属：已买 {statistics.mean(g['props_owned'] for g in games):.1f}"
          f"/{games[0]['props_total']} 处；"
          f"完全没人买的格子 {statistics.mean(len(g['props_unbought']) for g in games):.1f} 处")

    print("\n—— 落点分布（每局平均次数）——")
    kinds = Counter()
    for g in games:
        kinds.update(g["landing"])
    total_land = sum(kinds.values())
    for k, v in kinds.most_common():
        print(f"  {k:<14}{statistics.mean(g['landing'][k] for g in games):6.2f}  （占比 {pct(v, total_land)}）")

    print("\n—— 最常被抽到的事件卡（前 6）——")
    cards = Counter()
    for g in games:
        cards.update(g["cards"])
    for title, cnt in cards.most_common(6):
        print(f"  {title:<16}{cnt}")

    never = Counter()
    for g in games:
        for name in g["props_unbought"]:
            never[name] += 1
    print("\n—— 最常没人买的格子（前 8）——")
    for name, cnt in never.most_common(8):
        print(f"  {name:<14}{pct(cnt, n)} 的局里到最后都没人买")

    # 主环 vs 支街：被踩到的频率（决定地产值不值钱）
    sample = E.MonopolyGame(size=28, dice_sides=6, seed=1, map_kind="city")
    ring = set(sample.main_path)
    in_branch = {i for i, c in enumerate(sample.cells)
                 if c["type"] == "property" and i not in ring}
    land = Counter()
    for g in games:
        land.update(g["landings_by_cell"])
    total_land = sum(land.values())
    ring_hits = sum(v for k, v in land.items() if k in ring)
    ring_props = [i for i, c in enumerate(sample.cells) if c["type"] == "property" and i in ring]
    branch_props = [i for i, c in enumerate(sample.cells) if c["type"] == "property" and i not in ring]
    ring_prop_hits = sum(land.get(i, 0) for i in ring_props)
    branch_prop_hits = sum(land.get(i, 0) for i in branch_props)
    print(f"\n—— 主环 vs 支街（每局平均被踩到的次数，地产格）——")
    print(f"  主环上地产 {len(ring_props)} 处：{ring_prop_hits / n:.2f} 次/局"
          f"（每格 {ring_prop_hits / n / max(1, len(ring_props)):.2f}）")
    print(f"  支街上地产 {len(branch_props)} 处：{branch_prop_hits / n:.2f} 次/局"
          f"（每格 {branch_prop_hits / n / max(1, len(branch_props)):.2f}）")
    print(f"  主环格被踩的频率是支街的 "
          f"{(ring_hits / max(1, len(ring))) / max(1e-9, (total_land - ring_hits) / max(1, len(sample.cells) - len(ring))):.1f} 倍")


def sweep() -> None:
    """把"提高租金事件/垄断率"的几组旋钮扫一遍，输出一行一组的对比表。"""
    import city_map
    combos = [
        ("基线：地价60/垄断60%/上限1.5", dict(unit=60, share=0.6, factor=1.5, props=29, div=2)),
        ("垄断降到 50%", dict(unit=60, share=0.5, factor=1.5, props=29, div=2)),
        ("上限提到 2.0", dict(unit=60, share=0.6, factor=2.0, props=29, div=2)),
        ("垄断50% + 上限2.0", dict(unit=60, share=0.5, factor=2.0, props=29, div=2)),
        ("再降地价到 40 + 上限2.0 + 垄断50%", dict(unit=40, share=0.5, factor=2.0, props=29, div=2)),
        ("租金按半价→按全价（div=1）", dict(unit=60, share=0.5, factor=2.0, props=29, div=1)),
    ]
    print(f"{'方案':<34}{'步数':>5}{'破产':>7}{'租金件':>7}{'垄断':>7}{'买地':>7}{'租金总额':>9}{'均步价':>8}")
    for name, cfg in combos:
        E.MONOPOLY_PRICE_UNIT = cfg["unit"]
        E.MONOPOLY_HOLD_SHARE = cfg["share"]
        E.MONOPOLY_ROUNDS_PER_CELL = cfg["factor"]
        E.MONOPOLY_RENT_DIVISOR = cfg["div"]
        city_map.PROPERTY_TARGET = cfg["props"]
        n = 150
        games = [run(1000 + i) for i in range(n)]
        rent_events = statistics.mean(g["rent_events_real"] for g in games)
        mono = pct(sum(1 for g in games if any(g["monopoly_seen"][p] for p in E.PLAYERS)), n)
        bk = pct(sum(1 for g in games if g["ended_by"] == "bankrupt"), n)
        moves = statistics.median(g["moves"] for g in games)
        owned = statistics.mean(g["props_owned"] for g in games)
        total_rent = statistics.mean(g["rent_got"]["user"] + g["rent_got"]["sakura"] for g in games)
        sizes = [s for g in games for s in g["rent_sizes"]]
        print(f"{name:<34}{moves:>5.0f}{bk:>7}{rent_events:>7.2f}{mono:>7}{owned:>7.1f}"
              f"{total_rent:>9.0f}{statistics.median(sizes) if sizes else 0:>8.0f}")


if __name__ == "__main__":
    if SWEEP:
        sweep()
    else:
        main()