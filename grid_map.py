"""格子棋盘：**一条蜿蜒的线性路线**（照星铁那种等距方块棋盘的做法）。

数据是为渲染服务的，所以这里定死了三件事：

  1. **路线是线性的**：玩家沿主路**单向**前进，走到终点用**传送门回起点**
     （用户原话：不必是环线，可以线性走到头再传回起点）。
  2. **每格带一个"台阶高度" `h`**（渲染图里那根柱子的顶面高度）→ 网页按同一套正交投影
     把图钉/棋子钉到 3D 底图的格子上。
  3. **四个地产组按"路线分段"分**（前 1/4 = 学园区、1/4~1/2 = 街市 …），
     而不是按象限 —— 线性路线上一眼看得出"这一段是谁的地盘"。

路线正中间那格（第 31 格 = 棋盘几何中心）是**装置格**（汉加米塔），走进去进内部走廊探索。

格子形式（用户 2026-09-21 定的六种，见 `_cell_forms()`）：

    start/end  起点与终点：箭头图标，箭头方向就是路线方向
    property   地产：同一种图标（房子+金币）；踩上抽一张地契，买下后图标变成玩家色 + 对勾
    shop       商店：卖"可选点数的骰子"与"现金增减倍率"两种道具
    game       游戏：特别会计 / 学园补助 / 灰色的流浪猫 / 炭化之人 / 黑列车，
               踩上抽一件、掷骰判输赢，输了记成"日常一幕"
    coin       金币：三档现金，等概率
    event      事件：NAVI 播报（问号）

塔是**怎么进去**的（2026-09-23 用户纠正）：汉加米塔在美术上立在空场上、不挨着路线，
所以不铺新路 —— 走地图上那座**传送点**（(12,10)）进去，内部是 `build_submap()` 那张
三层回廊。引擎侧就是"落在那格 → 进塔"，与"终点→起点"那条传送门一样都是"传送"。

自检：python grid_map.py
"""
from __future__ import annotations

import json
import pathlib
import random
from typing import Any

try:
    from . import theme as _theme
except ImportError:
    import theme as _theme  # type: ignore[no-redef]

# 布局来自美术那份路线图（boardgame_art/board_route.json → make_web_assets.py 转过来）。
# 它是**唯一事实来源**：格子坐标、每格顶面高度、主路顺序、两条支路、传送门都在里面。
_LAYOUT_PATH = pathlib.Path(__file__).resolve().parent / "board_layout.json"
LAYOUT: dict[str, Any] = json.loads(_LAYOUT_PATH.read_text(encoding="utf-8"))

COLS, ROWS = int(LAYOUT["cols"]), int(LAYOUT["rows"])
CELL = 1.0                 # 一格 = 1 个世界单位（渲染图的相机就是这么定的）
GAP = 0.02                 # 地砖之间的缝（美术那边留了 0.016，这里只用于粗算）
WORLD_W, WORLD_H = float(COLS), float(ROWS)

# 每格的形式（渲染图里烤好的那个图标）：{"u,v": "property"|"shop"|"game"|"coin"|"event"|"arrow"}。
# 这份序列由美术侧 board_kinds.kind_of() 定，走 board_layout.json 过来 —— 两边共用一份。
_KINDS: dict[str, str] = {str(k): str(v) for k, v in (LAYOUT.get("forms") or {}).items()}

# 事件格上贴哪个图标：问号（NAVI 播报）与游戏柄各一种，靠 `special_cycle` 定。
# 这里只兜底"没排到具体名目"的情况。
SPECIAL_TAIL = {"event": "badge-navi", "game": "badge-game", "shop": "badge-shop",
                "coin": "badge-coin", "property": "badge-property"}

# 每个 (u,v) → 格子 id：主路 + 两条支路按顺序编号
_UV_ORDER: list[tuple[int, int]] = (
    [tuple(c) for c in LAYOUT["main_path"]]
    + [tuple(c) for chain in LAYOUT["branches"] for c in chain]
)
_UV_ID = {uv: i for i, uv in enumerate(_UV_ORDER)}
_MAIN_UV = [tuple(c) for c in LAYOUT["main_path"]]
_BRANCH_UV = [[tuple(c) for c in chain] for chain in LAYOUT["branches"]]
_HEIGHTS = LAYOUT.get("heights") or {}

# 塔内那张小图的格子 id 偏移（避免与主图撞号）。
SUBNODE_OFFSET = 10000

# 汉加米塔内部：**三层盘旋而上的回廊**。坐标是这张小图自己的格坐标，网页按等距把
# (col,row,h) 画成一段往上走的楼梯 —— h 逐层 +1，所以看着就是"在塔里往上爬"。
# 每层一间房，内容一律是**立即结算**的格子（没有地产、没有抉择）。
SUBMAP_LEVELS = (
    (0, 1, 0, "event"),      # 第 1 层 门厅：抽一张事件卡（NAVI 推送）
    (1, 1, 1, "coin"),       # 第 2 层 机枢层：三档现金
    (1, 0, 2, "game"),       # 第 3 层 观景层：抽一件特别事件，掷骰判输赢
)
SUBMAP_FLOOR_NAMES = ("门厅", "机枢层", "观景层")

GROUP_KEYS = ("gakuen", "shopping", "daily", "taboo")

# 金币格的三档（等概率）。值由主题给，改主题就改数值。
DEFAULT_COIN_TIERS = (100, 200, 300)


def _id_of(u: int, v: int) -> int:
    return _UV_ID[(int(u), int(v))]


def route_ids() -> list[int]:
    """主路（起点 → 终点）的格子 id 顺序。"""
    return [_id_of(u, v) for u, v in _MAIN_UV]


def branch_ids() -> list[list[int]]:
    return [[_id_of(u, v) for u, v in chain] for chain in _BRANCH_UV]


def route_all_ids() -> list[int]:
    """全部路线格（主路 + 支路），就是整张图的格子。"""
    return [_id_of(u, v) for u, v in _UV_ORDER]


def height_of(index: int) -> float:
    """第 index 格（按 _UV_ORDER）的**顶面高度**。网页按它把图钉/棋子钉到渲染图的格子上。"""
    u, v = _UV_ORDER[index]
    return float(_HEIGHTS.get(f"{u},{v}", 0.0))


def group_of(index: int, total: int) -> str:
    """按路线分段分地产组：前 1/4 学园区、1/4~1/2 街市、1/2~3/4 住宅区、末段荒废街区。"""
    ratio = index / max(1, total - 1)
    return GROUP_KEYS[min(len(GROUP_KEYS) - 1, int(ratio * len(GROUP_KEYS)))]


def coin_tiers() -> list[int]:
    """金币格的三档金额（主题可改；改不了就用默认）。"""
    try:
        tiers = _theme.coin_tiers()
    except Exception:
        tiers = ()
    values = [int(v) for v in (tiers or DEFAULT_COIN_TIERS) if int(v) > 0]
    return values or list(DEFAULT_COIN_TIERS)


def _cell_forms() -> dict[str, dict[str, Any]]:
    """六种格子形式的元数据（网页画地砖、图例、播报文案都读它）。

    `icon` 是**图标名**，网页按它找 `art/icons/<name>.png`；`label` 是图例上的中文名。
    """
    names = _theme.cell_names()
    return {
        "start": {"icon": "arrow", "label": names.get("start") or _theme.start_name(),
                  "note": "箭头指向路线方向；经过领工资"},
        "end": {"icon": "arrow", "label": names.get("end") or "终点",
                "note": "箭头指向路线方向；走到这里就回起点"},
        "property": {"icon": "property", "label": names.get("property") or "地产",
                     "note": "抽一张地契；买下后格子上是你的颜色 + 对勾"},
        "shop": {"icon": "shop", "label": names.get("shop") or "商店",
                 "note": "买特殊骰子（自选点数）或现金增减倍率"},
        "game": {"icon": "game", "label": names.get("game") or "游戏",
                 "note": "抽一件特别事件，掷骰判输赢；输了记成日常一幕"},
        "coin": {"icon": "coin", "label": names.get("coin") or "金币",
                 "note": "三档现金，等概率"},
        "event": {"icon": "event", "label": names.get("event") or "事件",
                  "note": "NAVI 推送一张事件卡"},
    }


# 格子棋盘的地产单价倍率（2026-10-01 经济评估后定）。
# 评估结论：格子棋盘的钱明显偏多（供给 ¥2978/局 + 起始 ¥2800，而地价只有 ¥2880，
# 比值 2.0 → 97.5% 的对局打满回合、强制抵押几乎为零）。既然用户拍板"少放地产格"，
# 那就让**剩下的地产更贵**：单价 ×1.5（¥90/180/270），地价总额与租金一并上来，
# 钱/地价比值收到 1.3 附近。这是**格子棋盘专属**，城市图/随机图的价格不变。
GRID_PRICE_MULT = 1.5


def _price_unit() -> int:
    try:
        try:
            from .engine import MONOPOLY_PRICE_UNIT
        except ImportError:
            from engine import MONOPOLY_PRICE_UNIT  # type: ignore[no-redef]
        return round(MONOPOLY_PRICE_UNIT * GRID_PRICE_MULT)
    except ImportError:
        return round(60 * GRID_PRICE_MULT)


def build_grid():
    """返回 (cells, edges, main_path, branches, gates)，字段与 city_map.build_city 一致。

    几何全部来自 board_layout.json（美术那份路线图）：

      · **主路是一条线性路线**（31 格，起点 (0,2) → 终点 (16,16)），玩家沿它**单向**前进；
        走到终点领一份工资、再由传送门回起点（这就是环）。
      · **两条支路**（20 格 / 6 格）两端都挂在主路上，**双向**（可以拐进去、也能退出来）。
      · **传送门**：终点 → 起点（成环）。另外支路 B 上的 (12,10) 那座门是**汉加米塔的入口**
        （`towerGate`），踩上去进塔内那张小图，见 `build_submap()`。
      · 每格带 `h` = 它在渲染图里的**顶面高度**（网页按它把图钉钉到格子上）。
    """
    total = len(_UV_ORDER)
    nodes: list[dict[str, Any]] = []
    for index, (u, v) in enumerate(_UV_ORDER):
        nodes.append({
            "id": index, "col": u, "row": v,
            # x/y = 渲染图里的世界坐标（美术那边格心就在整数格点上，没有 +0.5）
            "x": float(u), "y": float(v),
            "type": None, "name": "", "group": None, "tier": 0, "price": 0,
            "owner": None, "level": 0, "mortgaged": False, "icon": "",
            "h": round(height_of(index), 4), "step": index,
        })

    main = route_ids()
    branches = branch_ids()
    # ---- 边：四邻接里"真的挨着"的那些（方向规则交给 _build_moves）----
    by_uv = {uv: i for i, uv in enumerate(_UV_ORDER)}
    edges: list[list[int]] = []
    for (u, v), a in by_uv.items():
        for du, dv in ((1, 0), (0, 1)):
            b = by_uv.get((u + du, v + dv))
            if b is not None:
                edges.append([a, b])

    # ---- 形式：**照美术图里烤好的图标来**（board_layout.json 的 forms）----
    # 渲染图每格顶面画的是哪个图标，是美术侧按 board_kinds.kind_of() 的序数序列烤进去的；
    # 引擎必须吃同一份序列，否则网页把渲染图当底图之后，"图上画的"和"踩上去结算的"对不上。
    # 地产名/价格、事件名目仍然来自主题（换角色主题包时地名跟着换）。
    props = {key: [list(row) for row in rows] for key, rows in _theme.properties().items()}
    pool = [list(row) for row in _theme.special_cycle()]
    names = _theme.cell_names()
    forms = _cell_forms()
    pool_index = 0
    # 游戏格专用名单：开头固定一件「异色骰子」，其余沿用主题循环
    game_pool = [["dice_drop", "异色的骰子"]] + [list(row) for row in pool]
    game_index = 0
    for index, node_id in enumerate(route_all_ids()):
        node = nodes[node_id]
        if node_id == main[0]:
            node.update({"type": "start", "name": forms["start"]["label"],
                         "icon": forms["start"]["icon"]})
            continue
        if node_id == main[-1]:
            # 终点那格美术上立的是传送门（没有箭头图标），但引擎这边它仍是"走到就回起点"的终点
            node.update({"type": "end", "name": forms["end"]["label"],
                         "icon": forms["end"]["icon"]})
            continue
        # 地产组按**在整条路线上的位置**分段 —— 不能取模回到主路长度：
        # 那样第一个街区会被轮流抽两遍、名额早早用光，后面的地产格全退化成事件格（踩过）
        group = group_of(index, len(_UV_ORDER))
        kind = _KINDS.get("%d,%d" % _UV_ORDER[node_id], "event")
        if kind == "property":
            if props.get(group):
                key, label, tier, icon = props[group].pop(0)
                node.update({"type": "property", "name": label, "group": group,
                             "tier": tier, "price": tier * _price_unit(),
                             "icon": forms["property"]["icon"], "poi": icon})
                continue
            kind = "event"                    # 名额用完就退回事件格
        if kind in ("event", "game"):
            # 事件格/游戏格的名目从主题的 special_cycle 里抽；**类型以美术图为准**。
            # 游戏格的名单前面**固定插一件「异色骰子」**（用户 2026-09-30：游戏格里要有
            # 概率拿到商店那种自选点数骰子）——这张图第一个游戏格就是它，掷骰赢了才拿。
            if kind == "game":
                entry = game_pool[game_index % len(game_pool)]
                game_index += 1
            else:
                entry = pool[pool_index % len(pool)] if pool else ["rest", names.get("event") or "事件"]
                pool_index += 1
            sub, label = str(entry[0]), str(entry[1])
            node.update({"type": kind, "sub": sub, "name": label,
                         "icon": "badge-game" if kind == "game" else "badge-navi"})
            continue
        node.update({"type": kind, "name": names.get(kind) or forms[kind]["label"],
                     "icon": forms[kind]["icon"]})

    # ---- 传送点那一格改判成「塔入口」----
    # 美术上这格立的是传送门（所以没有烤图标），引擎这边踩上去就是**进塔**。
    # 它原来按 forms 被判成了商店（(12,10) 在支路 B 上正好排到 shop）—— 与画面对不上，
    # 2026-09-23 改掉：传送点 = 汉加米塔的入口。
    gate_uv = tower_gate_uv()
    gate_id = _UV_ID.get(gate_uv) if gate_uv is not None else None
    if gate_id is not None:
        device_name = names.get("device") or "中央装置"
        nodes[gate_id].update({"type": "tower", "sub": None,
                               "name": "%s（入口）" % device_name, "icon": ""})

    # ---- 传送门：终点→起点（线性路线靠它成环）+ 美术上那两格的成对传送门 ----
    gates: list[list[int]] = [[main[-1], main[0]]]
    for pair in LAYOUT.get("gates") or []:
        a, b = _id_of(*pair[0]), _id_of(*pair[1])
        if [a, b] not in gates:
            gates.append([a, b])

    return nodes, edges, main, branches, gates


# 主题的 special_cycle 里哪些名目算"游戏格"（其余算事件格的问号）
_GAME_SUB = ("tax", "bonus", "cat", "monster", "train")


def tower_gate_uv() -> tuple[int, int] | None:
    """通往塔内的那个**传送点**所在格（现在是 (12,10)）。

    用户 2026-09-23："（12,10）这个传送点传送进塔" —— 塔立在空场上、不挨着路线，
    所以进塔靠地图上已有的那座传送门，不走新铺的路。这个格子由美术数据给
    （board_layout.json 的 `towerGate`），网页也读同一份来画入口标记。
    """
    gate = LAYOUT.get("towerGate")
    return (int(gate[0]), int(gate[1])) if gate else None


def build_submap(offset: int = SUBNODE_OFFSET) -> dict[str, Any]:
    """汉加米塔**内部**那张图：三层盘旋而上的回廊，每层一间房。

    ⚠️ 每层都必须是**立即结算**的格子（没有地产、没有抉择）：有抉择的话
    `pending["cell"]` 就会指到子图 id 上，`decide()` 那边找不到格子。类型只在这三种里选：
    `event`（抽事件卡）/ `coin`（三档现金）/ `game`（抽特别事件 + 掷骰判输赢），
    引擎对它们全是即时结算。

    ⚠️ 房间名只写**类型**，不写具体名目（"第 2 层 · 机枢层｜金币"），因为抽到什么要等
    踩上去那一刻才由引擎掷出来 —— 名字里写死"灰色の流浪猫"就会出现"标着猫、开出补助"。

    `h` 就是层号 —— 网页按 (col,row,h) 做等距投影，画出来是一段往上走的楼梯。
    """
    names = _theme.cell_names()
    forms = _cell_forms()
    device = names.get("device") or "中央装置"
    cells: list[dict[str, Any]] = []
    for index, (col, row, h, kind) in enumerate(SUBMAP_LEVELS):
        label = names.get(kind) or forms[kind]["label"]
        cells.append({
            "id": offset + index, "col": col, "row": row,
            "x": float(col), "y": float(row),
            "type": kind, "sub": None,
            # 名字只写**类型**（"金币"），楼层前缀由显示方拼（`floor_name(index)`）——
            # 写进 name 里的话，"踩到「第 2 层 · 机枢层｜金币」"这种句子会读得很别扭。
            "name": label,
            "floor": index + 1, "floorName": SUBMAP_FLOOR_NAMES[index % len(SUBMAP_FLOOR_NAMES)],
            "group": None, "tier": 0, "price": 0, "owner": None, "level": 0,
            "mortgaged": False, "icon": forms[kind]["icon"],
            "h": float(h), "step": index, "inside": device,
        })
    edges = [[cells[i]["id"], cells[i + 1]["id"]] for i in range(len(cells) - 1)]
    return {"name": device, "cells": cells, "edges": edges, "offset": offset}


def floor_label(cell: dict[str, Any]) -> str:
    """塔内一间房的显示名：`第 2 层 · 机枢层｜金币`。"""
    return "第 %d 层 · %s｜%s" % (int(cell.get("floor") or 0),
                                  str(cell.get("floorName") or ""), str(cell.get("name") or ""))


def validate(cells: list[dict], edges: list[list[int]], main_path: list[int],
             gates: list[list[int]] | None = None,
             branches: list[list[int]] | None = None) -> list[str]:
    """结构体检：主路相邻、支路两端都挂回主路、格子数对得上、传送门合法、起点终点在位。"""
    problems: list[str] = []
    if len(cells) != len(_UV_ORDER):
        problems.append(f"格子数应为 {len(_UV_ORDER)}，实际 {len(cells)}")
    by_id = {c["id"]: c for c in cells}
    for index in range(len(main_path) - 1):
        a, b = by_id[main_path[index]], by_id[main_path[index + 1]]
        if abs(a["col"] - b["col"]) + abs(a["row"] - b["row"]) != 1:
            problems.append(f"主路第 {index}→{index + 1} 步不相邻（{a['name']} → {b['name']}）")
    if len(main_path) != len(set(main_path)):
        problems.append("主路里有重复节点")
    # 支路：内部要连着，两端都要挨着主路（否则拐进去出不来）
    for bi, chain in enumerate(branches or []):
        for index in range(len(chain) - 1):
            a, b = by_id[chain[index]], by_id[chain[index + 1]]
            if abs(a["col"] - b["col"]) + abs(a["row"] - b["row"]) != 1:
                problems.append(f"支路 {bi} 第 {index}→{index + 1} 步不相邻")
    # 真正的连通性约束：**除起点外不许有死胡同**（悬空的支路会把人困在里面）。
    # ⚠️ 不能要求"支路两端都挂在主路上" —— 这张图上第二条支路是挂在第一条支路上的
    # （(12,6) 属于支路 A），那样写会误报。
    degree = {c["id"]: 0 for c in cells}
    for a, b in edges:
        degree[a] = degree.get(a, 0) + 1
        degree[b] = degree.get(b, 0) + 1
    dead = [by_id[i]["name"] for i, d in degree.items() if d < 2 and i != main_path[0]]
    if dead:
        problems.append(f"除了起点不该有死胡同：{dead}")
    edge_pairs = {frozenset((a, b)) for a, b in edges}
    for index in range(len(main_path) - 1):
        if frozenset((main_path[index], main_path[index + 1])) not in edge_pairs:
            problems.append(f"主路第 {index}→{index + 1} 步没进 edges")
            break
    start = next((c for c in cells if c["type"] == "start"), None)
    if start is None or start["id"] != main_path[0]:
        problems.append("起点应在主路第一格")
    end_node = next((c for c in cells if c["type"] == "end"), None)
    if end_node is None or end_node["id"] != main_path[-1]:
        problems.append("终点应在主路最后一格")
    if not gates:
        problems.append("至少要有一条传送门（线性路线靠它成环）")
    for a, b in gates:
        if a not in by_id or b not in by_id:
            problems.append(f"传送门指向了不存在的格子：{a} → {b}")
    groups: dict[str, int] = {}
    for cell in cells:
        if cell["type"] == "property":
            groups[cell["group"]] = groups.get(cell["group"], 0) + 1
    for key in GROUP_KEYS:
        if groups.get(key, 0) < 4:
            problems.append(f"街区「{key}」的地产只有 {groups.get(key, 0)} 处（要 ≥4 才有垄断玩法）")
    return problems


if __name__ == "__main__":
    cells, edges, main_path, branches, gates = build_grid()
    issues = validate(cells, edges, main_path, gates)
    counts: dict[str, int] = {}
    for cell in cells:
        counts[cell["type"]] = counts.get(cell["type"], 0) + 1
    print(f"格子 {len(cells)}｜路线 {len(main_path)} 步（线性）｜边 {len(edges)}｜传送门 {len(gates)}")
    print("构成：" + "、".join(f"{k}×{v}" for k, v in sorted(counts.items())))
    print("路线开头：", " → ".join(cells[i]["name"] for i in main_path[:4]), "…")
    print("路线结尾：", cells[main_path[-1]]["name"], "→（传送门）", cells[main_path[0]]["name"])
    print("台阶（前 24 步）：", "".join(str(cells[i]["h"]) for i in main_path[:24]))
    print("传送门：" + "、".join(f"{cells[a]['name']} → {cells[b]['name']}" for a, b in gates))
    gate = next((c for c in cells if c["type"] == "tower"), None)
    if gate is not None:
        print(f"塔入口：{gate['name']}（第 {gate['step']} 格，传送点 {tower_gate_uv()}）")
    sub = build_submap()
    print("塔内：" + "　".join(f"{c['name']}（h={c['h']:.0f}）" for c in sub["cells"]))
    print("金币三档：" + "、".join(f"¥{v}" for v in coin_tiers()))
    print("校验：" + ("通过 ✓" if not issues else "发现问题 → " + "；".join(issues)))
