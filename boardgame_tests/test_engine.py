"""引擎自测：全部通过时输出 ALL TESTS PASSED。"""
from __future__ import annotations

import math
import random
import time
from collections import Counter

import sys as _sys
from pathlib import Path as _Path

# 让脚本自己找到插件目录：即使在别处 clone 下来、从任意目录运行都能跑
_sys.path.insert(0, str(_Path(__file__).resolve().parents[1] / "sakura_boardgame"))
import engine
from engine import (
    EVENT_CARDS,
    MONOPOLY_GROUP_NAMES,
    GameError,
    GomokuGame,
    MonopolyGame,
    create_game,
    restore_game,
    rules_text,
)


def expect_error(fn, needle: str) -> None:
    try:
        fn()
    except GameError as error:
        assert needle in str(error), f"错误信息不符：{error}"
        return
    raise AssertionError("预期 GameError，但调用成功了")


def _fake_dice(value: int):
    return lambda: type("R", (), {"randint": staticmethod(lambda a, b: value)})()


def _fake_card(index: int):
    card = EVENT_CARDS[index]
    return lambda: card


def _prop(name, group, tier, owner=None, level=0, mortgaged=False, node_id=None):
    return {"id": node_id, "x": 0, "y": 0, "type": "property", "name": name, "group": group,
            "tier": tier, "price": tier * 100, "owner": owner, "level": level, "mortgaged": mortgaged}


# ================== 地图生成 ==================

def _reachable(game: MonopolyGame) -> set[int]:
    seen = {0}
    stack = [0]
    while stack:
        node = stack.pop()
        for nxt in game._neighbors.get(node, []):
            if nxt not in seen:
                seen.add(nxt)
                stack.append(nxt)
    return seen


def test_map_generation_connectivity_and_determinism() -> None:
    for seed in range(15):
        for size in (12, 24, 28, 48, 96):
            game = MonopolyGame(size=size, dice_sides=6, seed=seed)
            assert len(game.cells) == game.size
            # 随机图的格子是"网格上的随机子集"：占据一块 cols×rows 的网格里的若干格
            assert game.size <= game.cols * game.rows
            assert game.size <= 96 and game.size >= 12
            # 格子间距要够开（图钉/名字牌不会叠在一起）
            pts = [(c["x"], c["y"]) for c in game.cells]
            for i in range(game.size):
                for j in range(i + 1, game.size):
                    gap = math.hypot(pts[i][0] - pts[j][0], pts[i][1] - pts[j][1])
                    assert gap >= 1.2, f"种子{seed} 有两格挨太近：{gap:.2f}"
            # 支线：跨过主路至少两个节点，且不穿过任何主路段（不能出现"支线穿主线"）
            if game.main_path:
                trunk = game.main_path
                index_of = {node: i for i, node in enumerate(trunk)}
                segs = [(pts[trunk[i]], pts[trunk[i + 1]]) for i in range(len(trunk) - 1)]
                segs.append((pts[trunk[-1]], pts[trunk[0]]))

                def _cross(o, p, q):
                    return (p[0] - o[0]) * (q[1] - o[1]) - (p[1] - o[1]) * (q[0] - o[0])

                def _crossed(s, t):
                    if s[0] in t or s[1] in t:
                        return False
                    d1, d2 = _cross(t[0], t[1], s[0]), _cross(t[0], t[1], s[1])
                    d3, d4 = _cross(s[0], s[1], t[0]), _cross(s[0], s[1], t[1])
                    return ((d1 > 0) != (d2 > 0)) and ((d3 > 0) != (d4 > 0))

                # 主路自己不许交叉（用户直接指出过这个问题）
                for x in range(len(segs)):
                    for y in range(x + 1, len(segs)):
                        assert not _crossed(segs[x], segs[y]), f"种子{seed} 主路自交叉"

                for a, b in game.branches:
                    span = abs(index_of[a] - index_of[b])
                    assert span >= 3, f"支线只跨 {span} 步，会形成三角形"
                    for s in segs:
                        if s[0] in (pts[a], pts[b]) or s[1] in (pts[a], pts[b]):
                            continue
                        assert not _crossed((pts[a], pts[b]), s), f"种子{seed} 支线 {a}->{b} 穿过主路"
            # 连通
            seen = _reachable(game)
            assert len(seen) == game.size, f"seed={seed} size={size} 不连通"
            # 边合法且无重边
            edge_set = set()
            for a, b in game.edges:
                assert a != b and 0 <= a < game.size and 0 <= b < game.size
                key = (min(a, b), max(a, b))
                assert key not in edge_set, "重复边"
                edge_set.add(key)
            # 起点
            starts = [c for c in game.cells if c["type"] == "start"]
            assert len(starts) == 1
            # 特殊格齐全
            for required in ("navi", "cat", "monster", "train"):
                assert any(c["type"] == required for c in game.cells), required
            # 地产组内名不重复、组归属合法
            for group in MONOPOLY_GROUP_NAMES:
                names = [c["name"] for c in game.cells if c["type"] == "property" and c["group"] == group]
                assert len(names) == len(set(names)) or len(names) > 4, (group, names)
    # 确定性：同 seed 两次生成完全一致
    a = MonopolyGame(size=28, dice_sides=6, seed=777)
    b = MonopolyGame(size=28, dice_sides=6, seed=777)
    assert a.to_dict() == b.to_dict()
    # 大多数地图存在岔路（度 >= 3 的节点）
    branchy = 0
    for seed in range(20):
        game = MonopolyGame(size=28, dice_sides=6, seed=seed)
        if any(len(ns) >= 3 for ns in game._neighbors.values()):
            branchy += 1
    assert branchy >= 15, f"岔路地图出现率过低：{branchy}/20"


def test_map_size_clamp() -> None:
    assert MonopolyGame(size=5, dice_sides=6, seed=1).size >= 12
    assert MonopolyGame(size=500, dice_sides=6, seed=1).size <= 96


def test_monopoly_players_start_on_start_node() -> None:
    for seed in range(12):
        game = MonopolyGame(size=28, dice_sides=6, seed=seed)
        start = next(c for c in game.cells if c["type"] == "start")
        assert game.positions["user"] == start["id"], (seed, game.positions)
        assert game.positions["sakura"] == start["id"], (seed, game.positions)
        assert game.start_node == start["id"]
        # 起点格不应是地产
        assert start["owner"] is None and start["price"] == 0
    # 序列化保留起点
    game = MonopolyGame(size=28, dice_sides=6, seed=3)
    restored = restore_game(game.to_dict())
    assert restored.start_node == game.start_node
    assert restored.positions == game.positions


def test_map_has_no_dead_ends_and_branches_rejoin() -> None:
    """轨道式地图：没有死胡同（每格连通度 ≥ 2），且岔路一定汇回主路。"""
    for seed in range(15):
        game = MonopolyGame(size=28, dice_sides=6, seed=seed)
        degree = {i: len(ns) for i, ns in game._neighbors.items()}
        dead_ends = [i for i, d in degree.items() if d < 2]
        assert not dead_ends, f"seed={seed} 存在死胡同 {dead_ends}"
        # 岔路口（度 ≥ 3）数量不应太多以至于像一张网（纯环、没有岔路也是合法地图）
        branches = sum(1 for d in degree.values() if d >= 3)
        assert 0 <= branches <= game.size // 2, (seed, branches)
        # 从任一点出发都能走遍全图（连通）
        assert len(_reachable(game)) == game.size


def test_map_cell_composition() -> None:
    """格子构成：地产 + 日常 + NAVI 应占绝大多数，特殊惩罚格只是点缀。"""
    for size in (24, 28, 48, 96):
        game = MonopolyGame(size=size, dice_sides=6, seed=11)
        counts = Counter(cell["type"] for cell in game.cells)
        total = sum(counts.values())
        main = counts["property"] + counts["rest"] + counts["navi"]
        assert main / total >= 0.75, (size, counts)
        assert counts["property"] / total >= 0.4, (size, counts)
        # 惩罚/奖励类格子总数不超过 1/4
        extra = counts["tax"] + counts["bonus"] + counts["cat"] + counts["monster"] + counts["train"]
        assert extra / total <= 0.25, (size, counts)
        # 四种特殊格至少各一处
        for required in ("navi", "cat", "monster", "train"):
            assert counts[required] >= 1, (size, required, counts)


def test_monopoly_stats_reporting() -> None:
    game = MonopolyGame(size=24, dice_sides=6, seed=5)
    stats = game.stats()
    assert set(stats) == {"user", "sakura"}
    assert stats["user"]["properties"] == 0 and stats["user"]["income"] == 0
    props = [c for c in game.cells if c["type"] == "property"][:3]
    for cell in props:
        cell["owner"] = "user"
    stats = game.stats()
    assert stats["user"]["properties"] == 3
    assert stats["user"]["income"] == sum(game._rent(c) for c in props)
    # 抵押后不再计入收益
    props[0]["mortgaged"] = True
    stats = game.stats()
    assert stats["user"]["mortgaged"] == 1
    assert stats["user"]["income"] == sum(game._rent(c) for c in props[1:])


def _cell(i, x, y, kind="rest", name="日常一幕", group=None, tier=0, owner=None, level=0, mortgaged=False):
    return {"id": i, "x": x, "y": y, "type": kind, "name": name, "group": group,
            "tier": tier, "price": tier * 100, "owner": owner, "level": level, "mortgaged": mortgaged}


def _build_game(cells, edges, positions=None, turn="user", dice=1, cash=None):
    """用显式格子/边构造大富翁局，绕开随机地图。"""
    game = MonopolyGame.__new__(MonopolyGame)
    game.kind = "monopoly"
    game.cols, game.rows = 4, 1
    game.size = game.requested_size = len(cells)
    game.dice_sides = 6
    game.seed = 1
    game._rolls = 0
    game._card_draws = 0
    game.cash = dict(cash or {"user": 1000, "sakura": 1000})
    game.positions = dict(positions or {"user": 0, "sakura": 0})
    game.skip_pending = {"user": False, "sakura": False}
    game.flags = {p: {"salary_next": False, "rent_free": False, "dice_bonus": 0} for p in ("user", "sakura")}
    game.turn = turn
    game.winner = None
    game.move_count = 0
    game.max_rounds = 100
    game.pending = None
    game._walk = None
    game.cells = cells
    game.edges = edges
    game._neighbors = game._build_neighbors()
    # 手搭的局没有主环，按无向图直通（map_kind 缺失时 _build_moves 走随机图分支）
    game.map_kind = "random"
    game.main_path = []
    game.branches = []
    game._moves = game._build_moves()
    game._dice_rng = _fake_dice(dice)
    return game


# ================== 走格子与岔路 ==================

def test_monopoly_roll_walks_and_finishes() -> None:
    game = MonopolyGame(size=12, dice_sides=6, seed=3)
    game._dice_rng = _fake_dice(2)
    result = game.roll("user")
    if not result["pending"]:
        assert result["steps_left"] == 0 and game._walk is None
        assert len(result["path"]) == 3  # 起点 + 2 步
        assert game.positions["user"] == result["path"][-1]
        assert game.turn == "sakura" or game.pending or game.winner
    else:
        assert result["pending"]["type"] == "route"
        assert game._walk is not None and game._walk["steps_left"] >= 1


def test_monopoly_route_decision_flow() -> None:
    # 十字岔路：0 为中心，1-4 为四邻
    cells = [
        _cell(0, 2, 2, name="中心"),
        _cell(1, 1, 2, name="左"),
        _cell(2, 3, 2, name="右"),
        _cell(3, 2, 1, name="上"),
        _cell(4, 2, 3, name="下"),
    ]
    game = _build_game(cells, [[0, 1], [0, 2], [0, 3], [0, 4]], dice=1)

    result = game.roll("user")  # 掷 1，中心有 4 个方向 → 岔路
    assert result["pending"] and result["pending"]["type"] == "route"
    assert set(result["pending"]["options"]) == {1, 2, 3, 4}
    expect_error(lambda: game.roll("user"), "待决策")
    expect_error(lambda: game.decide("user", "9"), "不能走")
    expect_error(lambda: game.decide("sakura", "1"), "轮不到")
    result = game.decide("user", "2")
    assert game.positions["user"] == 2 and game._walk is None
    assert game.turn == "sakura"
    assert any("选择走向" in entry for entry in result["story"])


def test_monopoly_multi_step_walk_stops_at_branch() -> None:
    # 直线 0-1-2-3，起点在 0：掷 3 应一步不停走完
    cells = [_cell(i, i, 0) for i in range(4)]
    game = _build_game(cells, [[0, 1], [1, 2], [2, 3]], dice=3)
    result = game.roll("user")
    assert result["path"] == [0, 1, 2, 3]
    assert game.positions["user"] == 3
    assert game._walk is None and game.turn == "sakura"

    # 中途遇岔路：0-1-2，2 之后分叉到 3 和 4；从 0 掷 3 → 走到 2 时剩 1 步，需选路
    cells2 = [_cell(i, i % 3, i // 3) for i in range(5)]
    game2 = _build_game(cells2, [[0, 1], [1, 2], [2, 3], [2, 4]], dice=3)
    result2 = game2.roll("user")
    assert result2["path"] == [0, 1, 2], result2["path"]
    assert result2["pending"] and result2["pending"]["type"] == "route"
    assert set(result2["pending"]["options"]) == {3, 4}  # 不能回头
    game2.decide("user", "3")
    assert game2.positions["user"] == 3 and game2._walk is None


def test_monopoly_salary_on_start_landing() -> None:
    # 直线 0-1-2，起点 0；玩家在 1，掷 1 选左边 → 落在起点领工资
    cells = [_cell(0, 0, 0, kind="start", name="起点"), _cell(1, 1, 0), _cell(2, 2, 0)]
    game = _build_game(cells, [[0, 1], [1, 2]], positions={"user": 1, "sakura": 0}, dice=1)
    result = game.roll("user")
    assert result["pending"]["type"] == "route" and set(result["pending"]["options"]) == {0, 2}
    result = game.decide("user", "0")
    assert game.cash["user"] == 1200, game.cash
    assert any("领工资" in entry for entry in result["story"])

    # 工资翻倍旗标
    game2 = _build_game(cells, [[0, 1], [1, 2]], positions={"user": 1, "sakura": 0}, dice=1)
    game2.flags["user"]["salary_next"] = True
    game2.roll("user")
    game2.decide("user", "0")
    assert game2.cash["user"] == 1400, game2.cash
    assert game2.flags["user"]["salary_next"] is False


# ================== 地产与经济 ==================

def test_monopoly_property_management() -> None:
    game = MonopolyGame(size=12, dice_sides=6, seed=1)
    prop = next(c for c in game.cells if c["type"] == "property" and c["price"] == 100)
    prop_id = prop["id"]
    # 直接落在空地 → 买
    game.positions["user"] = prop_id
    game.pending = None
    game._walk = None
    game.turn = "user"
    story: list[str] = []
    game._resolve_landing("user", prop_id, True, story)
    assert game.pending and game.pending["options"] == ["buy", "skip"]
    game.decide("user", "buy")
    assert prop["owner"] == "user" and game.cash["user"] == 900
    # 回到自己地 → 菜单
    game.turn = "user"
    game.pending = None
    game._resolve_landing("user", prop_id, True, story)
    assert game.pending and "upgrade" in game.pending["options"]
    game.decide("user", "upgrade")
    assert prop["level"] == 1 and game.cash["user"] == 850
    # 满级后：抵押 / 卖 / 跳过
    game.turn = "user"
    game.pending = None
    game._resolve_landing("user", prop_id, True, story)
    assert game.pending["options"] == ["mortgage", "sell", "skip"]
    game.decide("user", "mortgage")
    assert prop["mortgaged"] is True and game.cash["user"] == 900
    # 抵押中免租
    game.turn = "sakura"
    game.pending = None
    outcome = game._resolve_landing("sakura", prop_id, True, story)
    assert outcome["kind"] == "mortgaged" and game.cash["sakura"] == 1000
    # 赎回
    game.turn = "user"
    game.pending = None
    game._resolve_landing("user", prop_id, True, story)
    assert game.pending["options"] == ["redeem", "sell", "skip"]
    game.decide("user", "redeem")
    assert prop["mortgaged"] is False and game.cash["user"] == 840
    # 卖掉
    game.turn = "user"
    game.pending = None
    game._resolve_landing("user", prop_id, True, story)
    game.decide("user", "sell")
    assert prop["owner"] is None and game.cash["user"] == 840 + 33


def test_monopoly_monopoly_multiplier_and_rent() -> None:
    game = MonopolyGame(size=12, dice_sides=6, seed=1)
    # 显式构造两块同组地产：价格 200 → 基础租金 100
    props = [
        {"id": 1, "x": 0, "y": 0, "type": "property", "name": "家庭餐厅", "group": "shopping",
         "tier": 2, "price": 200, "owner": None, "level": 0, "mortgaged": False},
        {"id": 2, "x": 1, "y": 0, "type": "property", "name": "披萨店", "group": "shopping",
         "tier": 2, "price": 200, "owner": None, "level": 0, "mortgaged": False},
        {"id": 3, "x": 2, "y": 0, "type": "property", "name": "学生会室", "group": "gakuen",
         "tier": 2, "price": 200, "owner": None, "level": 0, "mortgaged": False},
    ]
    game.cells = props
    game.move_count = 0

    assert game._has_monopoly("user", "shopping") is False
    assert game._rent(props[0]) == 100  # 无主 → 基础租金

    props[0]["owner"] = "user"
    assert game._has_monopoly("user", "shopping") is False  # 只持一块不算垄断
    props[1]["owner"] = "user"
    assert game._has_monopoly("user", "shopping") is True
    assert game._rent(props[0]) == 200  # 垄断翻倍
    assert game._player_monopolies("user") == ["shopping"]

    props[0]["level"] = 1
    assert game._rent(props[0]) == 400  # 升级再翻倍

    props[1]["mortgaged"] = True
    assert game._has_monopoly("user", "shopping") is False  # 任一抵押即失去垄断
    props[1]["mortgaged"] = False

    # 只持一块同组地（组内成员 >= 2）不构成垄断
    props[2]["owner"] = "sakura"
    assert game._has_monopoly("sakura", "gakuen") is False


def test_monopoly_forced_mortgage_and_bankruptcy() -> None:
    game = MonopolyGame(size=12, dice_sides=6, seed=1)
    cells = game.cells
    user_props = [c for c in cells if c["type"] == "property"][:2]
    for cell in user_props:
        cell["owner"] = "user"
        cell["price"] = 300  # 每块抵押可回收 150
    sakura_prop = next(c for c in cells if c["type"] == "property" and c not in user_props)
    sakura_prop["owner"] = "sakura"

    story: list[str] = []
    # user 现金不足 → 逐个抵押自救（10 + 150 + 150 = 310 ≥ 300）
    game.cash["user"] = 10
    payment = game._pay("user", 300, "sakura", story)
    assert payment["bankrupt"] is False
    assert all(c["mortgaged"] for c in user_props)
    assert game.cash["user"] == 10
    assert game.cash["sakura"] == 1000 + 300
    assert any("抵押" in entry for entry in story)
    # sakura 已无产可抵 → 破产，资产过户给 user
    game.cash["sakura"] = 10
    sakura_prop["mortgaged"] = True
    payment = game._pay("sakura", 300, "user", story)
    assert payment["bankrupt"] is True and game.winner == "user"
    assert sakura_prop["owner"] == "user" and sakura_prop["mortgaged"] is True
    assert game.cash["sakura"] == 0


def test_monopoly_round_limit_and_price_inflation() -> None:
    game = MonopolyGame(size=12, dice_sides=6, seed=1)
    prop = next(c for c in game.cells if c["type"] == "property")
    base = prop["price"]
    game.move_count = 8
    assert game.current_price(prop) == base + 20
    game.move_count = 800
    assert game.current_price(prop) == int(base * 1.5)
    # 回合上限结算
    game2 = MonopolyGame(size=12, dice_sides=6, seed=1)
    game2.max_rounds = 1
    game2.cash["sakura"] = 2000
    game2._dice_rng = _fake_dice(1)
    game2.roll("user")
    while game2._walk or game2.pending:
        if game2.pending and game2.pending.get("type") == "route":
            game2.decide(game2.pending["player"], str(game2.pending["options"][0]))
        elif game2.pending:
            game2.decide(game2.pending["player"], "skip")
        else:
            game2._finish_walk([])
    assert game2.winner == "sakura"
    expect_error(lambda: game2.roll("sakura"), "已经结束")


# ================== 特殊格与事件卡 ==================

def test_monopoly_special_cells() -> None:
    game = MonopolyGame(size=12, dice_sides=6, seed=9)
    # 找格子直接结算
    for required, expect_cash in (("cat", 1000 - 50 + 150), ("monster", 1000 - 150), ("train", 1000 - 300)):
        cell = next(c for c in game.cells if c["type"] == required)
        game.cash["user"] = 1000
        game.pending = None
        game._walk = None
        story = []
        outcome = game._resolve_landing("user", cell["id"], True, story)
        assert game.cash["user"] == expect_cash, (required, game.cash["user"])
        assert outcome["kind"] == required
    # NAVI 卡：指定抽“猫的祝福”
    navi = next(c for c in game.cells if c["type"] == "navi")
    game.pending = None
    game._walk = None
    game.flags["user"]["dice_bonus"] = 0
    game._draw_card = _fake_card(14)
    story = []
    outcome = game._resolve_landing("user", navi["id"], True, story)
    assert outcome["kind"] == "navi_card" and game.flags["user"]["dice_bonus"] == 1

    # 全部事件卡文案都不含第二人称“你”（避免对夜乃樱用错人称）
    for index in range(len(EVENT_CARDS)):
        game.cash.update({"user": 1000, "sakura": 1000})
        game.pending = None
        game._walk = None
        game._last_walk = None
        game.winner = None
        game.flags = {p: {"salary_next": False, "rent_free": False, "dice_bonus": 0} for p in ("user", "sakura")}
        game._draw_card = _fake_card(index)
        story = []
        game._resolve_landing("user", navi["id"], True, story)
        navi_line = next(entry for entry in story if entry.startswith("NAVI通知"))
        assert "你" not in navi_line, navi_line


def test_monopoly_full_random_games() -> None:
    rng = random.Random(2024)
    for trial in range(12):
        game = MonopolyGame(size=rng.choice([12, 24, 28]), dice_sides=6,
                            seed=rng.randint(1, 10**9), first=rng.choice(["user", "sakura"]))
        for _step in range(400):
            if game.winner:
                break
            if game.pending:
                pending = game.pending
                if pending.get("type") == "route":
                    game.decide(pending["player"], str(rng.choice(pending["options"])))
                else:
                    options = pending["options"]
                    game.decide(pending["player"], rng.choice(options))
            elif game._walk:
                # 理论上 roll 后不会残留 walk（遇岔路必有 pending）
                raise AssertionError("walk 残留且无 pending")
            else:
                game.roll(game.turn)
            # 序列化一致性抽查
            restored = restore_game(game.to_dict())
            assert restored.cash == game.cash and restored.positions == game.positions
            game = restored
        assert game.render() and game.summary()


# ================== 序列化 ==================

def test_event_card_weights_and_rare_cards() -> None:
    """彩蛋卡（大赚/大亏）存在且权重更低，抽取分布明显偏向普通卡。"""
    rare = [c for c in EVENT_CARDS if c.get("rare")]
    assert len(rare) == 2, [c["title"] for c in rare]
    amounts = sorted(c["effect"]["amount"] for c in rare)
    assert amounts[0] <= -600 and amounts[1] >= 800, amounts      # 一张大亏、一张大赚
    for card in rare:
        assert int(card.get("weight", 10)) < 10, card["title"]

    game = MonopolyGame(size=24, dice_sides=6, seed=17)
    seen = Counter()
    total_weight = sum(int(c.get("weight", 10)) for c in EVENT_CARDS)
    for _ in range(4000):
        seen[game._draw_card()["title"]] += 1
    rare_hits = sum(seen[c["title"]] for c in rare)
    expected = 4000 * 4 / total_weight      # 两张彩蛋卡权重各 2
    assert 0 < rare_hits < expected * 2.5, (rare_hits, expected)
    assert len(seen) >= len(EVENT_CARDS) * 0.8, len(seen)


def test_draw_card_is_deterministic() -> None:
    a = MonopolyGame(size=24, dice_sides=6, seed=99)
    b = MonopolyGame(size=24, dice_sides=6, seed=99)
    seq_a = [a._draw_card()["title"] for _ in range(30)]
    seq_b = [b._draw_card()["title"] for _ in range(30)]
    assert seq_a == seq_b
    c = MonopolyGame(size=24, dice_sides=6, seed=99)
    for _ in range(10):
        c._draw_card()
    restored = restore_game(c.to_dict())
    assert [restored._draw_card()["title"] for _ in range(5)] == seq_a[10:15]


def test_monopoly_serialization_pending_and_walk() -> None:
    game = MonopolyGame(size=24, dice_sides=6, seed=42)
    game._dice_rng = _fake_dice(3)
    result = game.roll("user")
    data = game.to_dict()
    assert data["map_version"] == 4
    restored = restore_game(data)
    assert restored.cash == game.cash
    assert restored.edges == game.edges and restored.cells == game.cells
    assert restored.pending == game.pending and (restored._walk == game._walk)
    # 旧存档拒绝
    expect_error(lambda: restore_game({"kind": "monopoly", "cells": []}), "旧版存档")
    expect_error(lambda: restore_game({"kind": "chess"}), "存档损坏")


# ================== 五子棋 ==================

def test_gomoku_basic_rules() -> None:
    game = GomokuGame(first="user")
    coords = [(8, 4), (8, 5), (8, 6), (8, 7)]
    for r, c in coords:
        game.place("user", r, c)
        game.place("sakura", 10, c)
    result = game.place("user", 8, 8)
    assert result["winner"] == "user"
    expect_error(lambda: game.place("sakura", 1, 1), "已经结束")
    fresh = GomokuGame(first="user")
    fresh.place("user", 8, 8)
    expect_error(lambda: fresh.place("sakura", 99, 1), "超出棋盘")
    expect_error(lambda: fresh.place("sakura", 8, 8), "已经有子")
    expect_error(lambda: GomokuGame(first="cat"), "先手")
    # 序列化
    restored = restore_game(game.to_dict())
    assert restored.board == game.board and restored.winner == "user"


def test_gomoku_ai_must_win_and_block() -> None:
    game = GomokuGame(first="sakura")
    # 我方四连，AI 必须补成五
    for r, c in [(7, 3), (7, 4), (7, 5), (7, 6)]:
        game.board[r][c - 1] = 2  # sakura 在 (7,2)..(7,5)? 用更直接布法：
    game.board = [[0] * 15 for _ in range(15)]
    for col in (3, 4, 5, 6):
        game.board[7][col] = 2  # 夜乃樱横向四连，两头开放
    game.board[9][9] = 1
    game.board[9][10] = 1
    game.turn = "sakura"
    result, reason = game.ai_move("sakura", 3)
    row, col = result["move"]
    # 1-based 坐标：横向四连 (7,3)-(7,6) 的成五点是 (8,3)/(8,8)
    assert (row == 8 and col in (3, 8)) or game.winner == "sakura", (row, col, reason)
    # 必防：对方四连
    game2 = GomokuGame(first="sakura")
    for col in (3, 4, 5, 6):
        game2.board[7][col] = 1  # 玩家横向四连
    game2.board[5][5] = 2
    game2.turn = "sakura"
    result, reason = game2.ai_move("sakura", 2)
    row, col = result["move"]
    assert row == 8 and col in (3, 8), (row, col, reason)


def test_gomoku_ai_speed_and_levels() -> None:
    game = GomokuGame(first="sakura")
    rng = random.Random(7)
    for _ in range(12):
        r, c = rng.randint(4, 10), rng.randint(4, 10)
        if game.board[r][c] == 0:
            game.board[r][c] = 1 if _ % 2 == 0 else 2
    game.turn = "sakura"
    for level in (1, 2, 3):
        game.turn = "sakura"
        game.winner = None
        started = time.monotonic()
        result, reason = game.ai_move("sakura", level)
        elapsed = time.monotonic() - started
        assert elapsed < 3.0, f"level {level} 耗时 {elapsed:.2f}s"
        row, col = result["move"]
        assert 1 <= row <= 15 and 1 <= col <= 15
        assert reason
        game.board[row - 1][col - 1] = 0  # 还原，保证三次同盘比较
    # 空盘首手在中央附近
    fresh = GomokuGame(first="sakura")
    result, _ = fresh.ai_move("sakura", 2)
    row, col = result["move"]
    assert abs(row - 8) <= 1 and abs(col - 8) <= 1


def test_gomoku_first_dice_decides_turn() -> None:
    # 骰子决定先后手：点数大者执先，且两次掷骰被记录
    for seed in range(20):
        game = GomokuGame(first="dice", seed=seed)
        rolls = game.first_rolls
        assert 1 <= rolls["user"] <= 6 and 1 <= rolls["sakura"] <= 6
        assert rolls["user"] != rolls["sakura"], (seed, rolls)
        expected = "user" if rolls["user"] > rolls["sakura"] else "sakura"
        assert game.turn == expected, (seed, rolls, game.turn)
        assert game.first == expected
    # 显式指定先手时，骰子只作展示
    game = GomokuGame(first="sakura", seed=1)
    assert game.turn == "sakura" and game.first == "sakura"
    assert game.first_rolls["user"] and game.first_rolls["sakura"]
    # 序列化保留
    restored = restore_game(game.to_dict())
    assert restored.first_rolls == game.first_rolls and restored.first == game.first
    # 同 seed 确定
    assert GomokuGame(first="dice", seed=99).first_rolls == GomokuGame(first="dice", seed=99).first_rolls
    # 非法先手
    expect_error(lambda: GomokuGame(first="cat"), "先手")


def test_rules_text_and_create() -> None:
    expect_error(lambda: create_game("race", "user", 6, 28), "不支持的游戏类型")
    expect_error(lambda: create_game("monopoly", "cat", 6, 28), "先手")
    monopoly = create_game("monopoly", "user", 6, 28)
    assert "单向" in rules_text(monopoly) or "岔路" in rules_text(monopoly)
    gomoku = create_game("gomoku", "dice", 6, 28)
    assert "五子" in rules_text(gomoku)
    assert gomoku.turn in ("user", "sakura")
    # 大富翁传 dice 时按用户先手处理，不报错
    assert create_game("monopoly", "dice", 6, 28).turn == "user"


def test_city_map_in_engine() -> None:
    """城市地图接入引擎：布置、起点、主路/支线、单向主环、可玩、可存档。"""
    from engine import MonopolyGame  # noqa: F401
    import engine as engine_mod

    try:
        from . import city_map
    except ImportError:
        import city_map  # type: ignore[no-redef]

    # 期望值从地图数据本身推导，地图调整时这里不用跟着改数字
    cells, edges, main_path, branches = city_map.build_city()

    game = engine_mod.MonopolyGame(size=48, dice_sides=6, seed=7, map_kind="city")
    assert game.map_kind == "city"
    assert len(game.cells) == len(cells)
    assert len(game.main_path) == len(main_path) and game.main_path[0] == game.start_node
    assert len(game.branches) == len(branches)
    assert game.positions["user"] == game.start_node
    assert game.positions["sakura"] == game.start_node, "夜乃樱也要从起点出发"
    # 起点格本身就是"起点"，且两张地图都成立
    assert game.cells[game.start_node]["type"] == "start"
    # 城市图自带校验
    assert city_map.validate_layout() == []
    # 起点唯一的环上前进方向必须是学园正门（用户要求：出发第一格就是学园正门）
    ring = list(game.main_path)
    if ring[0] == ring[-1]:
        ring = ring[:-1]
    forward = ring[1]
    assert game.cells[forward]["name"] == "学园正门", game.cells[forward]
    moves = game.next_nodes(game.start_node)
    assert forward in moves
    assert ring[-1] not in moves, "主环是单向的，不能从起点倒着走"
    # 每个格子都至少有一条出边（否则走不动），且都从起点可达
    for cell in game.cells:
        assert game.next_nodes(cell["id"]), f"#{cell['id']} 无路可走"
    seen, stack = {game.start_node}, [game.start_node]
    while stack:
        for nxt in game.next_nodes(stack.pop()):
            if nxt not in seen:
                seen.add(nxt)
                stack.append(nxt)
    assert len(seen) == len(game.cells), f"单向规则下有 {len(game.cells) - len(seen)} 格走不到"
    # 能正常走一段路（遇路口就选第一个方向）
    for _ in range(40):
        if game.pending:
            pending = game.pending
            if pending.get("type") == "route":
                game.decide(pending["player"], str(pending["options"][0]))
            else:
                game.decide(pending["player"], "skip")
        else:
            game.roll(game.turn)
        if game.winner:
            break
    assert game.move_count > 0
    # 存档带 map_kind / main_path / branches
    data = game.to_dict()
    assert data["map_kind"] == "city" and data["map_version"] == 4
    restored = engine_mod.restore_game(data)
    assert restored.map_kind == "city"
    assert restored.main_path == game.main_path
    assert restored.branches == game.branches
    assert restored.cells == game.cells
    assert restored._moves == game._moves        # 单向规则也要能从存档恢复


if __name__ == "__main__":
    for name, fn in sorted(globals().items()):
        if name.startswith("test_"):
            fn()
            print(f"ok  {name}")
    print("ALL TESTS PASSED")
