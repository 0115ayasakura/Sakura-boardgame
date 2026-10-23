"""格子棋盘自测：**一条线性路线** + 台阶高度 + 传送门 + 装置格进内部走廊 + 存档往返。

只在全部通过时输出 ALL GRID TESTS PASSED。跑法：
    python test_grid_map.py
"""
from __future__ import annotations

import sys as _sys
from pathlib import Path as _Path

_sys.path.insert(0, str(_Path(__file__).resolve().parents[1] / "sakura_boardgame"))
import engine  # noqa: E402
import grid_map  # noqa: E402
from engine import MAP_VERSION, MonopolyGame, restore_game  # noqa: E402


def _game(seed: int = 7) -> MonopolyGame:
    return MonopolyGame(size=63, dice_sides=6, seed=seed, first="user", map_kind="grid")


def test_layout_is_a_linear_main_path_with_two_branches() -> None:
    """地图几何来自美术那份路线图：主路 31 格单向 + 两条支路双向 + 一对传送门。

    用户 2026-09-22 定的：把 board_iso 那张图当游戏地图，所以这里的数字就是美术那边的数字。
    """
    cells, edges, route, branches, gates = grid_map.build_grid()
    assert len(cells) == len(route) + sum(len(b) for b in branches), "格子 = 主路 + 支路"
    assert len(route) == 31, len(route)
    assert [len(b) for b in branches] == [20, 6], branches
    assert len(set(route)) == len(route), "主路里不该有重复"
    assert grid_map.validate(cells, edges, route, gates, branches) == []
    by_id = {c["id"]: c for c in cells}
    for i in range(len(route) - 1):
        a, b = by_id[route[i]], by_id[route[i + 1]]
        assert abs(a["col"] - b["col"]) + abs(a["row"] - b["row"]) == 1, (i, a["name"], b["name"])
    # 每格都要有渲染图里的顶面高度（网页靠它把棋子钉到格子上）
    assert all(c["h"] > 0 for c in cells), [c["h"] for c in cells]
    assert len({round(c["h"], 3) for c in cells}) >= 3, "台阶高度要有起伏"
    start = next(c for c in cells if c["type"] == "start")
    end = next(c for c in cells if c["type"] == "end")
    assert (start["col"], start["row"]) == tuple(grid_map.LAYOUT["start"])
    assert (end["col"], end["row"]) == tuple(grid_map.LAYOUT["end"])
    # 没有 `device` 这种"棋盘中央的装置格"了：汉加米塔不挨着路线，进塔靠**支路上那座传送点**
    # （(12,10)，用户 2026-09-23 纠正：走传送点进去，不另铺路）—— 那格引擎里判成 `tower`。
    assert not any(c["type"] == "device" for c in cells)
    gate = grid_map.tower_gate_uv()
    assert gate == (12, 10), gate
    tower = next(c for c in cells if (c["col"], c["row"]) == gate)
    assert tower["type"] == "tower", tower
    assert tower["name"].endswith("（入口）"), tower["name"]


def test_engine_moves_main_one_way_branches_two_way() -> None:
    """主路只能顺着走；支路两端挂在主路上、可以拐进去也能退出来。"""
    game = MonopolyGame(size=28, dice_sides=6, seed=3, map_kind="grid")
    route = list(game.main_path)
    assert game._moves[route[0]] == [route[1]], "起点只通向主路下一格"
    for i in range(1, len(route) - 1):
        assert route[i + 1] in game._moves[route[i]], f"{game.cells[route[i]]['name']} 走不到下一格"
        assert route[i - 1] not in game._moves[route[i]], f"{game.cells[route[i]]['name']} 能倒着走"
    # 支路：内部双向，且两端都连着主路
    for chain in game.branches:
        for a, b in zip(chain, chain[1:]):
            assert b in game._moves[a] and a in game._moves[b], "支路应该能来回走"
        for end in (chain[0], chain[-1]):
            node = game.cells[end]
            neighbours = [(game.cells[m]["col"], game.cells[m]["row"]) for m in game._moves[end]]
            assert any(abs(node["col"] - c) + abs(node["row"] - r) == 1 for c, r in neighbours),                 "支路的一端要挂在主路上"


def test_gates_end_loop_and_tower_entrance() -> None:
    """传送门只有「终点→起点」那一条（线性路线靠它成环）。

    美术上立在 (12,10) 的那座门**不是普通传送门**：它是汉加米塔的入口（引擎里 `type="tower"`），
    踩上去进塔（用户 2026-09-23 纠正："（12,10）这个传送点传送进塔"），所以它不在 gates 里。
    """
    game = MonopolyGame(size=28, dice_sides=6, seed=3, map_kind="grid")
    end, start = game.main_path[-1], game.main_path[0]
    assert [list(g) for g in game.gates] == [[end, start]], game.gates
    assert start in game._moves[end], "终点要能传送回起点"
    assert end not in game._moves[start], "起点不该能反向传回终点"
    gate_uv = grid_map.tower_gate_uv()
    tower = next(c for c in game.cells if (c["col"], c["row"]) == gate_uv)
    assert tower["type"] == "tower", tower
    assert [tower["id"], end] not in [list(g) for g in game.gates], "塔入口不该被当成成对传送门"


def test_districts_follow_route_segments() -> None:
    """地产按"路线位置"分四个街区 —— 数的是**全部格子**（主路 + 两条支路）。"""
    cells, _edges, route, _b, _g = grid_map.build_grid()
    groups = [c["group"] for c in cells if c["type"] == "property"]
    # 2026-10-01 经济评估后手排序列：地产 26→23（用户拍板"适当减少地产格、增加其他格"），
    # 省出的格子给了游戏格（3→6）与事件格（9→11）
    assert len(groups) == 23, f"地产应有 23 处，实际 {len(groups)} 处"
    # 分段：同一段应该是同一个组（允许边界处交叠，但主要区间必须成段）
    assert groups[0] == grid_map.GROUP_KEYS[0]
    assert groups[-1] == grid_map.GROUP_KEYS[-1]
    for key in grid_map.GROUP_KEYS:
        assert groups.count(key) >= 4, (key, groups.count(key))


def test_submap_rooms_still_build() -> None:
    """塔内那张小图（三层回廊）能建，且每层都是**立即结算**的格（不能挂抉择）。"""
    sub = grid_map.build_submap()
    assert sub["cells"] and all(c["type"] != "property" for c in sub["cells"]), "内部应该只有立即结算的格"
    assert all(c["id"] >= grid_map.SUBNODE_OFFSET for c in sub["cells"])
    assert len(sub["edges"]) == len(sub["cells"]) - 1, "内部是一间间串起来的回廊"
    # 三层，层号从 1 起、逐层往上（网页按 h 画那段上行的楼梯）
    assert [c["floor"] for c in sub["cells"]] == [1, 2, 3], sub["cells"]
    assert [c["h"] for c in sub["cells"]] == [0.0, 1.0, 2.0], [c["h"] for c in sub["cells"]]
    assert all(c["floorName"] for c in sub["cells"]), "每层要有楼层名"
    assert all(c["type"] in ("event", "coin", "game") for c in sub["cells"]), sub["cells"]


def test_landing_on_tower_entrance_walks_the_floors() -> None:
    """踩到塔入口那格 → 引擎进塔逐层结算，并留下一次"塔内经过"给网页放动画。"""
    game = _game(seed=5)
    gate_uv = grid_map.tower_gate_uv()
    tower_id = next(c["id"] for c in game.cells if (c["col"], c["row"]) == gate_uv)
    assert game.cells[tower_id]["type"] == "tower"
    before = game.cash["user"]
    result = game._resolve_landing("user", tower_id, True, [])
    assert result["kind"] == "device", result
    assert len(result["rooms"]) == 3, result["rooms"]
    tower = game._tower
    assert tower and tower["seq"] == 1 and tower["cell"] == tower_id, tower
    assert [f["floor"] for f in tower["floors"]] == [1, 2, 3], tower["floors"]
    assert all(f["floorName"] and f["label"] for f in tower["floors"]), tower["floors"]
    # 每层都是即时结算：不该留下指向子图 id 的抉择（decide 那边找不到子图格子）
    assert game.pending['type'] == 'tower' and game.cash['user'] == before
    for expected in (-1, 0, 1):
        game.advance_tower('user', tower['seq'], expected)
    assert game.pending['type'] == 'tower'  # 完成展示确认前仍暂停
    game.advance_tower('user', tower['seq'], 2, True)
    # 三层实际进入之后才结算现金
    assert any(f["amount"] for f in tower["floors"]) or game.cash["user"] != before, tower["floors"]


def test_grid_save_roundtrip() -> None:
    game = _game(seed=11)
    game.positions["user"] = game.main_path[3]
    data = game.to_dict()
    assert data["map_version"] == MAP_VERSION
    assert data["map_kind"] == "grid"
    assert data["gates"] == [list(g) for g in game.gates]
    restored = restore_game(data)
    assert restored.map_kind == "grid"
    assert restored.gates == game.gates
    assert restored.main_path == game.main_path
    assert restored._moves == game._moves
    assert restored.positions == game.positions


def test_grid_rules_text_mentions_portal_and_tower() -> None:
    game = _game()
    text = engine.rules_text(game)
    assert "格子棋盘" in text
    assert "传送门" in text
    assert "传送点" in text, "规则里要讲清「进塔走的是支路上那座传送点」"
    assert "汉加米塔" in text, "塔名要跟着主题走（默认主题是汉加米塔）"


def test_roll_keeps_advancing_along_the_route() -> None:
    """掷骰后应当沿路线一格一格往前走（不会回头、不会跳格）。"""
    game = _game(seed=3)
    start = game.positions["user"]
    result = game.roll("user")
    path = result["path"]
    assert path[0] == start
    route = game.main_path
    index = {node: i for i, node in enumerate(route)}
    for a, b in zip(path, path[1:]):
        if [a, b] in game.gates:          # 传送门那一跳不算在路线里
            continue
        assert index[b] == index[a] + 1, f"{game.cells[a]['name']} → {game.cells[b]['name']} 不是路线上的下一步"


class _FixedDice:
    """把骰子钉死，好让"走一步正好踩到对方"这种局面可复现。"""

    def __init__(self, value: int) -> None:
        self.value = value

    def randint(self, a: int, b: int) -> int:
        return max(a, min(b, self.value))

    def randrange(self, n: int) -> int:
        return 0


def test_same_cell_rule_steps_back_and_resolves_landing() -> None:
    """同格挡路（用户 2026-09-22 定，只在格子棋盘）：后到的那枚棋子退后一步停在对方身后，
    **而且不结算落点**（那格是她的地产也不交租——被挡住不是正常落脚）。"""
    game = MonopolyGame(size=28, dice_sides=6, seed=5, map_kind="grid")
    route = list(game.main_path)
    idx = next(i for i, node in enumerate(route) if game.cells[node]["type"] == "property")
    ahead, here = route[idx], route[idx - 1]
    game.cells[ahead]["owner"] = "sakura"          # 玩家正前方是她的地产
    game.positions["user"] = here
    game.positions["sakura"] = ahead
    game.turn = "user"
    game.cells[here]['type'] = 'coin'  # 让位后实际停留格应结算
    cash_before = game.cash["user"]
    game._dice_rng = lambda: _FixedDice(1)         # 只走一步 → 正好落在她站的那格

    result = game.roll("user")

    assert game.positions["user"] == here, "应当退回到出发的那一格"
    assert game.positions["sakura"] == ahead, "她的位置不该被挤走"
    assert result["path"][-1] == here, result["path"]
    assert game.cash["user"] > cash_before, "让位回退后应触发实际停留金币格"
    assert not game.pending, "被挡住也不该弹「要不要买地」的决策"
    last = game._last_walk or {}
    assert last.get("blocked") is True, last
    assert last.get("blockedAt") == ahead and last.get("blockedBy") == "sakura", last
    assert any("退后一步" in line for line in result["story"]), result["story"]
    # 退回那一格要能继续走，而且**面朝前方**（2026-09-30 修的倒走 bug）：
    # 被推回去不算走——last_step 不记成"从前方来"，下一回合岔路只给顺行方向。
    # 旧版在这里记 [ahead, here]，下一回合从 here 出发兜底会放行 here 的来路，
    # 棋子就一路倒着走回支路（实测 (12,5)→(9,4)）。
    assert game.last_step["user"] is None, game.last_step
    preds = game._road_preds.get(here, set())
    options = [n for n in game._moves[here] if n not in preds]
    assert ahead in options, "下一步应当还能朝前方（被挡住的那格）走"


def test_same_cell_rule_is_grid_only() -> None:
    """随机图/城市图没有这条规则：棋子可以停在对方那一格（只有格子棋盘挡路）。"""
    game = MonopolyGame(size=28, dice_sides=6, seed=5, map_kind="random")
    start = game.start_node
    target = game._moves[start][0]
    game.positions["user"] = start
    game.positions["sakura"] = target
    game.turn = "user"
    game._dice_rng = lambda: _FixedDice(1)

    game.roll("user")
    # 随机图里几乎每格都是岔路（出度 ≥2），第一步会停下来等选方向 —— 就选她站的那格
    if game.pending and game.pending.get("type") == "route":
        game.decide("user", str(target))

    assert game.positions["user"] == target, "非格子棋盘不挡路"
    assert (game._last_walk or {}).get("blocked") in (None, False)


def test_fork_never_offers_backward_road() -> None:
    """岔路上不许出现"逆着路线走"的假选项（用户 2026-09-30）。

    站在支路汇入点 (12,6)、刚从另一条支路 (12,7) 出来：再往 (12,5) 走是
    逆着这条支路的来路掉头 —— 这不是岔路，不该出现在选项里。
    进塔的方向不受影响：沿支路 0 正着走到 (12,6) 时，(12,7)（进塔方向）仍是可选。
    """
    byuv = {(c["col"], c["row"]): c["id"] for c in _game().cells}
    game = _game()
    # 从 (12,7) 走向 (12,6)，还剩 2 步 → 在 (12,6) 决策（(12,5) 是这条支路的来路）
    game._walk = {"player": "user", "steps_left": 2,
                  "path": [byuv[(12, 7)], byuv[(12, 6)]]}
    game.positions["user"] = byuv[(12, 6)]
    story: list[str] = []
    game._continue_walk(story)
    if game.pending:                       # 若还有岔路，选项里不许有 (12,5)
        assert byuv[(12, 5)] not in game.pending["options"], game.pending["options"]
    while game.pending:                    # 一步步把这一回合走完
        game.decide("user", str(game.pending["options"][0]))
    assert byuv[(12, 5)] not in (game._last_walk or {}).get("path", [])

    # 塔入口不受影响：正着走到 (12,6) 时，(12,7)（进塔方向）仍是可选
    game2 = _game()
    game2._walk = {"player": "user", "steps_left": 2,
                   "path": [byuv[(12, 5)], byuv[(12, 6)]]}
    game2.positions["user"] = byuv[(12, 6)]
    story2: list[str] = []
    game2._continue_walk(story2)
    if game2.pending:
        assert byuv[(12, 7)] in game2.pending["options"], game2.pending["options"]
        assert byuv[(12, 5)] not in game2.pending["options"]


if __name__ == "__main__":
    for name, fn in sorted(globals().items()):
        if name.startswith("test_"):
            fn()
            print(f"ok  {name}")
    print("ALL GRID TESTS PASSED")
