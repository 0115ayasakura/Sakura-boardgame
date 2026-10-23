# -*- coding: utf-8 -*-
"""传送门自测：终点→起点（线性路线靠它成环）+ (12,10) 那座传送点其实是**塔入口**。

只在全部通过时输出 ALL PORTAL TESTS PASSED。跑法：
    python test_portal.py
"""
from __future__ import annotations

import sys as _sys
from pathlib import Path as _Path

_sys.path.insert(0, str(_Path(__file__).resolve().parents[1] / "sakura_boardgame"))

import engine  # noqa: E402
import grid_map  # noqa: E402
from engine import MonopolyGame  # noqa: E402


class _FixedDice:
    """把骰子钉死，好让"站在传送门上"这种局面可复现。"""

    def __init__(self, value: int) -> None:
        self.value = value

    def randint(self, a: int, b: int) -> int:
        return max(a, min(b, self.value))

    def randrange(self, n: int) -> int:
        return 0


def _game(dice: int = 1) -> MonopolyGame:
    game = MonopolyGame(size=63, dice_sides=6, seed=7, first="user", map_kind="grid")
    game._dice_rng = lambda: _FixedDice(dice)
    return game


def _uv_id(uv: tuple[int, int]) -> int:
    cells, *_ = grid_map.build_grid()
    for c in cells:
        if (int(c["col"]), int(c["row"])) == uv:
            return c["id"]
    raise AssertionError(f"没有 ({uv[0]},{uv[1]}) 这格")


def _park_opponent(game: MonopolyGame) -> None:
    """把对手挪到主路中段：测传送门时不想被"同格挡路"把玩家顶回来。

    挪到起点也不行 —— 经传送门回起点时会撞上"同格挡路"、被顶回终点（这正是那条规则
    该有的行为，但会把传送门本身的断言搅浑）。挪到 -1 之类的假 id 更不行：
    render() 要查 cells[对手位置]，直接 KeyError。主路中段离这几格都远。
    """
    game.positions["sakura"] = game.main_path[15]


def test_gates_declared() -> None:
    """传送门必须有定义，两端都是真格子，且只有「终点→起点」那一条。

    用户 2026-09-23 纠正："（12,10）这个传送点传送进塔" —— 美术上立在那格的门是**塔入口**，
    引擎里判成 `type="tower"`（踩上去进塔），不再是一对普通传送门。
    """
    cells, _, main, _, gates = grid_map.build_grid()
    ids = {c["id"] for c in cells}
    assert gates, "传送门一个都没有"
    assert [list(g) for g in gates] == [[main[-1], main[0]]], gates
    for a, b in gates:
        assert a in ids and b in ids, f"传送门指向了不存在的格子 {a}->{b}"
        assert a != b, f"传送门两端是同一格 {a}"
    gate_uv = grid_map.tower_gate_uv()
    tower_id = _uv_id(gate_uv)
    assert next(c for c in cells if c["id"] == tower_id)["type"] == "tower", gate_uv
    assert all(tower_id not in (a, b) for a, b in gates), "塔入口不该出现在 gates 里"


def test_gates_are_walkable() -> None:
    """每条传送门都要真的能走，且「终点→起点」是**单向**的。

    它只是把线性主路接成一个环；反向放行等于给了一条从起点直飞终点的捷径，那一圈就白走了。
    """
    game = _game()
    assert game.gates, "引擎没接住传送门"
    for a, b in game.gates:
        assert b in game._moves[a], f"传送门 #{a}→#{b} 走不通"
    ring_gate = [game.main_path[-1], game.main_path[0]]
    for a, b in game.gates:
        if [a, b] == ring_gate:
            assert a not in game._moves[b], "「终点→起点」不该反向放行（那是绕环的捷径）"
        else:
            assert a in game._moves[b], f"传送门 #{b}→#{a} 走不通"


def test_landing_on_end_teleports_to_start() -> None:
    """踩到终点 = 这一圈走完：领一份工资，**立即传回起点**（用户 2026-10-01 定）。

    旧版停在终点、下一步再经传送门走回起点 —— 用户实测她停在终点后朝反方向走了，
    现在落地即传送。工资在传送这一刻发，且只发一次（传送不经过 _step_to，
    不会在起点再发一遍）。路径里带 [终点, 起点]，网页靠它播传送门动画。
    """
    game = _game(dice=1)
    cells, _, main, _, _ = grid_map.build_grid()
    end = next(c["id"] for c in cells if c["type"] == "end")
    start = next(c["id"] for c in cells if c["type"] == "start")
    game.positions["user"] = main[main.index(end) - 1]
    _park_opponent(game)
    game.turn = "user"
    cash0 = game.cash["user"]
    result = game.roll("user")
    assert game.positions["user"] == start, f"应当被传回起点，停在 {game.positions['user']}"
    assert game.cash["user"] == cash0 + engine.pass_start('grid'),         f"终点这一圈要发一次工资：{game.cash['user']} vs {cash0}"
    assert not game.pending, "终点格不该弹任何决策"
    assert result["path"][-2:] == [end, start], result["path"]
    assert game.last_step["user"] is None, "传送落地不该记来路"


def test_walking_past_start_pays_once_per_lap() -> None:
    """踩上起点（无论从主路走进来、还是经传送门跳回来）都发一次工资，且只发一次。"""
    game = _game(dice=1)
    start = _uv_id((0, 2))
    game.positions["user"] = start
    _park_opponent(game)
    game.turn = "user"
    cash0 = game.cash["user"]
    # 起点只通向主路下一格（单向），所以这一掷不会绕回来
    game.roll("user")
    assert game.cash["user"] == cash0, "从起点出发的那一步不该发工资（还没绕回来）"
    # 从终点经传送门跳回起点：这一跳落在 start 上，发工资（见 test_passing_end_midwalk）
    game2 = _game(dice=2)
    game2.positions["user"] = _uv_id((15, 16))
    _park_opponent(game2)
    game2.turn = "user"
    cash1 = game2.cash["user"]
    end, start2 = _uv_id((16, 16)), start
    result = game2.roll("user")
    assert game2.cash["user"] == cash1 + engine.pass_start('grid'), (
        f"经传送门回起点应当发 ¥{engine.pass_start('grid')}，"
        f"实际 {game2.cash['user'] - cash1}")
    assert result["path"][:3] == [_uv_id((15, 16)), end, start2], result["path"]


def test_passing_end_midwalk_teleports() -> None:
    """**中途**踩到终点（骰子越过终点还要继续走）：同样立即传回起点，绝不出岔路。

    用户 2026-10-05 实测 (15,16)→(16,16)：终点被当成岔路、摆出「回起点 (0,2)」和
    「去 (16,15)」两个选项 —— 终点的邻居就这俩，但它根本不该是岔路。
    规则与"走完落在终点"一致：发一次工资 + 传送回起点，剩余步数从起点接着走；
    传送不消耗步数、path 里带 [终点, 起点]（网页播传送门动画）。
    """
    game = _game(dice=2)
    cells, _, main, _, _ = grid_map.build_grid()
    end = next(c["id"] for c in cells if c["type"] == "end")
    start = next(c["id"] for c in cells if c["type"] == "start")
    game.positions["user"] = main[main.index(end) - 1]
    _park_opponent(game)
    game.turn = "user"
    cash0 = game.cash["user"]
    result = game.roll("user")
    # 终点不弹任何岔路（落点决策是买地/跳过之类，跟终点无关）
    if game.pending:
        assert game.pending.get("cell") != end, f"终点不该出岔路：{game.pending}"
    assert game.cash["user"] == cash0 + engine.pass_start('grid'), (
        f"过终点要发一次工资：{game.cash['user']} vs {cash0}")
    assert result["path"][1:3] == [end, start], result["path"]
    # 剩余步数从起点接着走：落在 main[1]（起点的下一格）
    assert game.positions["user"] == main[1], game.positions["user"]


def test_gate_pair_is_bidirectional_and_one_step() -> None:
    """传送门是**一步**跨到对面（不是沿着路走）。

    2026-10-05 起"踩到终点"（无论走完落在终点、还是中途路过）一律立即传回起点，
    不再存在"站在终点选传送门"的岔路 —— 走为上的行为由 test_landing_on_end_teleports
    与 test_passing_end_midwalk_teleports 覆盖，这里只留结构断言。
    """
    end, start = _uv_id((16, 16)), _uv_id((0, 2))
    game = _game()
    assert start in game._moves[end], f"传送门 #{end}→#{start} 没接进可走表"
    assert end not in game._moves[start], "终点→起点不该反向放行（那是绕环的捷径）"


def test_tower_entrance_is_not_a_teleport_pair() -> None:
    """走进塔入口 (12,10)：这是**进塔**，不是把人传到别处（用户 2026-09-23 纠正）。

    从支路上前一格 (12,9) 迈一步进去，应当结算塔内那三层，且**位置仍停在这格上**
    （进塔不是传送，出来还在原地）。
    """
    tower_id = _uv_id((12, 10))
    prev_id = _uv_id((12, 9))
    game = _game(dice=1)
    game.positions["user"] = prev_id
    _park_opponent(game)
    game.turn = "user"
    result = game.roll("user")
    # 支路是双向的：从 (12,9) 走一步会停下来问方向，选塔那一格
    if game.pending and game.pending.get("type") == "route":
        assert tower_id in game.pending["options"], game.pending
        result = game.decide("user", str(tower_id))
    assert game.positions["user"] == tower_id, game.positions
    assert any("传送门" in line and "送进" in line for line in result["story"]), result["story"]
    tower = game._tower
    assert tower and len(tower["floors"]) == 3, tower
    assert tower["cell"] == tower_id, tower


def test_gate_not_available_on_other_maps() -> None:
    """传送门只属于格子棋盘：城市图/随机图不该冒出传送门。"""
    for kind in ("city", "random"):
        game = MonopolyGame(size=40, dice_sides=6, seed=7, first="user", map_kind=kind)
        assert not game.gates, f"{kind} 图不该有传送门"


if __name__ == "__main__":
    for name, fn in sorted(globals().items()):
        if name.startswith("test_"):
            fn()
            print(f"ok  {name}")
    print("ALL PORTAL TESTS PASSED")
