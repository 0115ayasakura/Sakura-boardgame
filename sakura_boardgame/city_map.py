"""第二横滨市 · 城市地图（街道网络版，全图唯一事实来源）。

布局思路（对齐真实城市的规划方式，而不是"一圈环线"）
----------------------------------------------------
城市由**街道网络 + 街区**构成：

* 一条不规则的**外环干道**（有折角、疏密不均，像城市环线而不是椭圆）；
* 两条**主动脉**：中央横街（东西向）与中央纵街（南北向），在市中心交汇；
* 若干**支街**：学园小街、街市小街、住宅小街、河堤路、斜街；
  支街从外环接进市中心、或从一个支街接到另一个支街 —— 因此地图是有多个回路的**网络**，
  岔路能连岔路，而不是简单绕回；
* 四个街区（学园区 / 街市 / 住宅区 / 禁忌区）用多边形划定，格子由所在位置自动归属。

格子沿街道按弧长均匀分布（路口处两条街共用同一个节点），因此：
每个节点度数 ≥ 2（没有死胡同）、整张图连通、地标一定落在自己街区里且不会落进水里。
`validate_layout()` 会断言这一切；改地图后跑 `python city_map.py` 或跑测试即可。
"""
from __future__ import annotations

import math

# ---------- 画布（单位＝格，1 格 ≈ 132px） ----------
WORLD_W = 22.0
WORLD_H = 14.0
MIN_SPACING = 0.92      # 任意两格最小间距
JUNCTION_MERGE = 1.05   # 路口合并阈值，必须 >= MIN_SPACING，否则会留下过近的节点

def _curve(a, b, through=(), samples=18, wobble=0.0):
    """a→b 的平滑曲线（Catmull-Rom 采样，曲线严格经过 a、through、b）。

    四个街区**共用这几条边界曲线**：相邻两块把同一条曲线正反各用一次，
    因此边界严丝合缝（不重叠、不漏白），同时边界本身是有机的弧形而不是直线。
    """
    pts = [tuple(a)] + [tuple(p) for p in through] + [tuple(b)]
    n = len(pts)
    out: list[tuple[float, float]] = []
    for i in range(n - 1):
        p0, p1, p2, p3 = pts[max(0, i - 1)], pts[i], pts[i + 1], pts[min(n - 1, i + 2)]
        for k in range(samples):
            t = k / samples
            t2, t3 = t * t, t * t * t
            x = 0.5 * (2 * p1[0] + (-p0[0] + p2[0]) * t
                       + (2 * p0[0] - 5 * p1[0] + 4 * p2[0] - p3[0]) * t2
                       + (-p0[0] + 3 * p1[0] - 3 * p2[0] + p3[0]) * t3)
            y = 0.5 * (2 * p1[1] + (-p0[1] + p2[1]) * t
                       + (2 * p0[1] - 5 * p1[1] + 4 * p2[1] - p3[1]) * t2
                       + (-p0[1] + 3 * p1[1] - 3 * p2[1] + p3[1]) * t3)
            if wobble:
                dx, dy = p2[0] - p1[0], p2[1] - p1[1]
                span = math.hypot(dx, dy) or 1e-9
                phase = math.sin((i * samples + k) * 0.5)
                x += -dy / span * wobble * phase
                y += dx / span * wobble * phase
            out.append((round(x, 3), round(y, 3)))
    out.append((round(b[0], 3), round(b[1], 3)))
    return out


# ---------- 街区 ----------
# 四个街区在**唯一中心点** CUR 处交汇，四条边界曲线由相邻两块共用。
# 每条边界的另一头落在**地图边框**上，所以四块一直铺到边框、不留白边、互不重叠。
CUR = (10.4, 7.5)
FRAME = (0.4, 0.4, 21.6, 13.6)     # 地图边框 (x0, y0, x1, y1)
_NW, _NE = (0.4, 0.4), (21.6, 0.4)
_SW, _SE = (0.4, 13.6), (21.6, 13.6)

_B_WEST = _curve(CUR, (0.4, 5.7), wobble=0.16)                      # 学园区｜住宅区
_B_NORTH = _curve(CUR, (12.0, 0.4), through=[(12.1, 5.2)], wobble=0.16)   # 学园区｜街市
_B_EAST = _curve(CUR, (21.6, 8.8), through=[(13.2, 9.2)], wobble=0.16)    # 街市｜荒废街区
_B_SOUTH = _curve(CUR, (9.4, 13.6), through=[(9.6, 9.6)], wobble=0.16)    # 住宅区｜荒废街区

_FRAME_ORDER = {
    "gakuen": [_SW, _NW, (12.0, 0.4)],
    "shopping": [(21.6, 0.4), (21.6, 8.8)],
    "taboo": [(21.6, 13.6), (9.4, 13.6)],
    "daily": [(0.4, 13.6), (0.4, 5.7)],
}
_BOUNDARY_ORDER = {
    "gakuen": [_B_WEST, _B_NORTH],
    "shopping": [_B_NORTH, _B_EAST],
    "taboo": [_B_EAST, _B_SOUTH],
    "daily": [_B_SOUTH, _B_WEST],
}


def _district_poly(key: str) -> list[tuple[float, float]]:
    """按"中心点 → 第一条边界 → 边框 → 第二条边界（反向）"拼出街区多边形。"""
    first, second = _BOUNDARY_ORDER[key]
    poly = [CUR] + list(first)
    frame = _FRAME_ORDER[key]
    for point in frame:
        if poly[-1] != tuple(point):
            poly.append(tuple(point))
    back = list(reversed(second))
    if back and poly[-1] == back[0]:
        back = back[1:]
    poly.extend(back)
    return poly


DISTRICTS = {
    "gakuen": {"label": "学园区", "color": "#4a8bc9", "poly": _district_poly("gakuen")},
    "shopping": {"label": "街市", "color": "#d95a72", "poly": _district_poly("shopping")},
    "daily": {"label": "住宅区", "color": "#3f9e83", "poly": _district_poly("daily")},
    "taboo": {"label": "荒废街区", "color": "#4a5470", "poly": _district_poly("taboo")},
}

# ---------- 街道网络 ----------
# role: ring = 单向主环（行进方向就是 main_path 的顺序）；artery = 环与环之间的双向主动脉；
#       branch = 从主环拐进去的支街（双向，另一端是尽头或接别的支街）
STREETS = {
    # 外环干道（单向主环；首尾由最后一条边接上）—— 有折角、疏密不均
    "ring": {
        "pts": [(2.2, 3.0), (4.6, 1.6), (8.2, 1.3), (12.4, 1.9), (16.6, 2.5), (19.4, 4.4),
                (20.4, 6.6), (19.6, 9.0), (17.6, 11.0), (14.2, 12.4), (10.6, 12.9),
                (7.0, 12.4), (4.0, 11.0), (2.4, 8.6), (1.7, 5.8)],
        "closed": True, "step": 1.75, "role": "ring",
    },
    # 中央横街（东西向主动脉）
    "cross_ew": {"pts": [(1.9, 6.5), (6.0, 6.4), (9.5, 6.6), (13.2, 6.4), (17.4, 6.3), (20.0, 6.6)],
                 "closed": False, "step": 1.65, "role": "artery"},
    # 中央纵街（南北向主动脉）
    "cross_ns": {"pts": [(9.0, 1.4), (9.3, 4.3), (9.5, 6.6), (9.6, 9.5), (9.4, 12.9)],
                 "closed": False, "step": 1.65, "role": "artery"},
    # 学园小街：外环 → 中央横街
    "gakuen_st": {"pts": [(4.6, 1.6), (5.4, 3.4), (6.6, 5.0), (6.0, 6.4)],
                  "closed": False, "step": 1.65, "role": "branch"},
    # 斜街：从一个支街接到中央纵街（岔路连岔路）
    "diag_st": {"pts": [(5.4, 3.4), (7.3, 4.6), (9.3, 4.3)],
                "closed": False, "step": 1.65, "role": "branch"},
    # 街市小街：外环 → 中央横街
    "shopping_st": {"pts": [(16.6, 2.5), (16.2, 4.3), (16.0, 6.3)],
                    "closed": False, "step": 1.65, "role": "branch"},
    # 住宅小街：外环 → 中央横街
    "daily_st": {"pts": [(4.0, 11.0), (5.2, 9.2), (6.0, 6.4)],
                 "closed": False, "step": 1.65, "role": "branch"},
    # 河堤路：外环 → 外环（荒废街区内横穿，第二处回路）
    "river_st": {"pts": [(14.2, 12.4), (15.6, 11.0), (17.6, 11.0)],
                 "closed": False, "step": 1.65, "role": "artery"},
    # ---- 以下五条补内圈，让市中心也有街可走 ----
    # 西横街：外环 → 中央横街（住宅区西北）
    "west_link": {"pts": [(2.4, 8.6), (4.8, 7.9), (6.0, 6.4)],
                  "closed": False, "step": 1.65, "role": "branch"},
    # 南斜街：外环 → 中央纵街（住宅区东南）
    "south_link": {"pts": [(7.0, 12.4), (8.4, 10.6), (9.6, 9.5)],
                   "closed": False, "step": 1.65, "role": "branch"},
    # 荒废小街：中央纵街 → 外环（荒废街区中部）
    "taboo_link": {"pts": [(9.6, 9.5), (12.2, 10.4), (14.2, 12.4)],
                   "closed": False, "step": 1.65, "role": "branch"},
    # 街市斜街：外环 → 中央横街（街市中部）
    "shopping_ns": {"pts": [(12.4, 1.9), (13.8, 4.2), (13.2, 6.4)],
                    "closed": False, "step": 1.65, "role": "branch"},
    # 东横街：中央横街 → 外环（街市与荒废街区之间）
    "east_link": {"pts": [(17.4, 6.3), (16.8, 8.6), (17.6, 11.0)],
                  "closed": False, "step": 1.65, "role": "branch"},
}

# ---------- 水域：右下角是海（海岸线从右边框切到底边框），另有两处水塘 ----------
# 海岸线用和街区边界同一套曲线采样：折线海岸会有折角，看起来像地图缺了一块
SEA_COAST = _curve((21.6, 5.4), (15.6, 13.6),
                   through=[(20.7, 8.6), (19.8, 10.6), (17.8, 12.4)], samples=12)
SEA_POLY = SEA_COAST + [_SE]
PONDS = [(3.4, 10.6, 0.62, 0.38), (4.2, 4.0, 0.60, 0.36)]


# 具名特殊格的场景归属（用户要求：黑列车在学园区）
TOKEN_DISTRICT = {"train": "gakuen", "cat": "daily", "monster": "taboo"}

# ---------- 地产名（每街区 7–9 块，够铺满内圈新加的街道） ----------
PROPERTY_NAMES = {
    "gakuen": [("gate", "学园正门", 1, "poi-gate"), ("council", "学生会室", 2, "poi-council"),
               ("libcom", "图书馆委员会", 2, "poi-libcom"), ("explore", "探索委员会", 2, "poi-explore"),
               ("mutelib", "无调图书馆", 3, "poi-mutelib"), ("cathedral", "地下大圣堂", 3, "poi-cathedral"),
               ("tower", "汉加米塔", 3, "poi-tower"),
               ("pool", "学园游泳池", 1, "poi-pool"), ("dojo", "弓道场", 1, "poi-dojo")],
    "shopping": [("family", "家庭餐厅", 1, "poi-family"), ("ktv", "KTV包厢", 2, "poi-ktv"),
                 ("hotel", "情侣酒店", 2, "poi-hotel"), ("british", "英国人街", 2, "poi-british"),
                 ("esports", "Esports街区", 3, "poi-esports"), ("tower2", "观景台", 3, "poi-tower2"),
                 ("conveni", "便利店", 1, "poi-conveni"), ("arcade", "游戏中心", 2, "poi-arcade")],
    "daily": [("sakura_flat", "樱的公寓", 1, "poi-sakura_flat"), ("user_flat", "玩家的公寓", 2, "poi-user_flat"),
              ("park", "公园", 1, "poi-park"), ("catspot", "流浪猫的据点", 1, "poi-catspot"),
              ("riverwalk", "海边步道", 2, "poi-seaside"), ("bookstore", "旧书店", 2, "poi-bookstore"),
              ("bakery", "面包店", 1, "poi-bakery"), ("sento", "旧澡堂", 2, "poi-sento"),
              ("bench", "海边长椅", 1, "poi-bench")],
    "taboo": [("wall", "东京巨壁", 2, "poi-wall"), ("subway", "地下铁道", 3, "poi-subway"),
              ("manhole", "下水道井盖", 2, "poi-manhole"), ("station", "黑列车车站", 3, "poi-station"),
              ("houzuki", "崩月家", 3, "poi-houzuki"), ("garrison", "废置兵舍", 2, "poi-garrison"),
              ("hospital", "废弃医院", 3, "poi-hospital"), ("shelter", "防空洞", 2, "poi-shelter"),
              ("alley", "崩月家后巷", 2, "poi-alley")],
}

# ---------- 事件格序列（沿路循环铺设；无效果/抽卡类占多数，与用户要求一致） ----------
SPECIAL_CYCLE = [
    ("rest", "日常一幕", "badge-rest"),
    ("navi", "NAVI播报", "badge-navi"),
    ("rest", "日常一幕", "badge-rest"),
    ("rest", "日常一幕", "badge-rest"),
    ("navi", "NAVI播报", "badge-navi"),
    ("rest", "中央广场", "badge-rest"),
    ("bonus", "学园补助", "badge-bonus"),
    ("rest", "日常一幕", "badge-rest"),
    ("navi", "NAVI播报", "badge-navi"),
    ("tax", "特别会计", "badge-tax"),
    ("rest", "日常一幕", "badge-rest"),
    ("navi", "NAVI播报", "badge-navi"),
    ("rest", "日常一幕", "badge-rest"),
    ("cat", "灰色的流浪猫", "badge-cat"),
    ("rest", "日常一幕", "badge-rest"),
    ("navi", "NAVI播报", "badge-navi"),
    ("bonus", "学园补助", "badge-bonus"),
    ("rest", "日常一幕", "badge-rest"),
    ("tax", "特别会计", "badge-tax"),
    ("navi", "NAVI播报", "badge-navi"),
    ("rest", "日常一幕", "badge-rest"),
    ("monster", "炭化之人", "badge-monster"),
    ("rest", "日常一幕", "badge-rest"),
    ("train", "黑列车", "badge-train"),
    ("rest", "日常一幕", "badge-rest"),
    ("navi", "NAVI播报", "badge-navi"),
    ("rest", "日常一幕", "badge-rest"),
    ("rest", "日常一幕", "badge-rest"),
]


# ---------- 几何工具 ----------
def _round_poly(poly, radius: float = 1.0, push: tuple[float, float] | None = None,
                push_at: int | None = None, samples: int = 8, wobble: float = 0.0):
    """把多边形顶点改成圆弧过渡，得到有机的边界。

    街区的边界原来是一段段直线，放大看全是棱角；这里在每个顶点处用二次曲线过渡。
    push/push_at 用来把某个顶点（街区交汇的中心点）往外推一点，让相邻街区的色块在中心
    轻微重叠而不是留缝。wobble 给边界加极小的起伏，避免圆弧看起来像机械倒角。
    """
    n = len(poly)
    if n < 3:
        return [tuple(p) for p in poly]
    pts = [tuple(p) for p in poly]
    if push is not None and push_at is not None:
        pts[push_at] = (pts[push_at][0] + push[0], pts[push_at][1] + push[1])

    out: list[tuple[float, float]] = []
    for i in range(n):
        prev, cur, nxt = pts[i - 1], pts[i], pts[(i + 1) % n]
        len_a = math.hypot(cur[0] - prev[0], cur[1] - prev[1]) or 1e-9
        len_b = math.hypot(nxt[0] - cur[0], nxt[1] - cur[1]) or 1e-9
        t = min(0.32, radius / len_a, radius / len_b)
        a = (cur[0] + (prev[0] - cur[0]) * t, cur[1] + (prev[1] - cur[1]) * t)
        b = (cur[0] + (nxt[0] - cur[0]) * t, cur[1] + (nxt[1] - cur[1]) * t)
        for k in range(samples):
            u = k / samples
            x = (1 - u) ** 2 * a[0] + 2 * (1 - u) * u * cur[0] + u * u * b[0]
            y = (1 - u) ** 2 * a[1] + 2 * (1 - u) * u * cur[1] + u * u * b[1]
            if wobble:
                # 沿外法线加一点起伏，让边界有手工感；按点序与顶点号决定相位，可复现
                dx, dy = nxt[0] - prev[0], nxt[1] - prev[1]
                span = math.hypot(dx, dy) or 1e-9
                phase = math.sin((i * 2.399 + k * 0.9) * 1.7)
                x += -dy / span * wobble * phase
                y += dx / span * wobble * phase
            out.append((round(x, 3), round(y, 3)))
    return out


def _poly_contains(poly, x, y) -> bool:
    inside = False
    n = len(poly)
    for i in range(n):
        x1, y1 = poly[i]
        x2, y2 = poly[(i + 1) % n]
        if (y1 > y) != (y2 > y):
            xin = (x2 - x1) * (y - y1) / (y2 - y1) + x1
            if x < xin:
                inside = not inside
    return inside


def district_of(x: float, y: float) -> str | None:
    for key, info in DISTRICTS.items():
        if _poly_contains(info["poly"], x, y):
            return key
    # 落在街区边界缝隙时，归到最近的街区质心
    best, best_d = None, 1e9
    for key, info in DISTRICTS.items():
        cx = sum(p[0] for p in info["poly"]) / len(info["poly"])
        cy = sum(p[1] for p in info["poly"]) / len(info["poly"])
        d = math.hypot(x - cx, y - cy)
        if d < best_d:
            best, best_d = key, d
    return best


def in_water(x: float, y: float) -> bool:
    if _poly_contains(SEA_POLY, x, y):
        return True
    for px, py, rx, ry in PONDS:
        if ((x - px) / rx) ** 2 + ((y - py) / ry) ** 2 <= 1.0:
            return True
    return False


def _sample_polyline(pts, step: float, closed: bool):
    """沿折线按弧长每 step 取一个点；closed 时首尾相接。"""
    line = list(pts) + ([pts[0]] if closed else [])
    out = [line[0]]
    for i in range(len(line) - 1):
        ax, ay = line[i]
        bx, by = line[i + 1]
        seg = math.hypot(bx - ax, by - ay)
        if seg <= 1e-9:
            continue
        n = max(1, round(seg / step))
        for k in range(1, n + 1):
            t = k / n
            out.append((ax + (bx - ax) * t, ay + (by - ay) * t))
    return out


# ---------- 构建 ----------
# 可购地产的目标数量（其余格子铺成事件格）。改这个数字就能调"地产密度"：
# 27 处以上偏向大富翁（地多、租金稀），16 处左右偏向快节奏（地少、每块更值钱）。
# 注意：减少它只是把多出来的格子变成事件格，**地图几何/街区/路网/插图都不动**，
# 但会有一批已画好的地标不再出现在城市图上（改成"日常一幕"这类徽章格）。
PROPERTY_TARGET = 29
ROAD_CORNER_RADIUS = 0.55                                # 街道拐角圆弧半径（画路与高亮共用）
STREET_GEOM: dict[str, list[tuple[float, float]]] = {}   # 画路用的街道几何（合并后的节点坐标）
EDGE_GEOM: dict[str, list[list[float]]] = {}             # 每条图边的路网几何（网页高亮用）


def price_unit() -> int:
    """地价单位（地产价 = 档位 × 它）。数值跟 engine 的 MONOPOLY_PRICE_UNIT 保持一致，
    免得两张地图两个价；单独跑本模块（校验脚本）时用同一个默认值。"""
    try:
        try:
            from .engine import MONOPOLY_PRICE_UNIT
        except ImportError:
            from engine import MONOPOLY_PRICE_UNIT  # type: ignore[no-redef]
        return MONOPOLY_PRICE_UNIT
    except ImportError:
        return 60


def build_city():
    """返回 (cells, edges, main_path, branches)。"""
    nodes: list[dict] = []
    id_of_street: dict[str, list[int]] = {}

    def add_node(x: float, y: float, street: str) -> int:
        # 路口合并：与已有节点足够近就复用
        for node in nodes:
            if math.hypot(node["x"] - x, node["y"] - y) < JUNCTION_MERGE:
                if street not in node["_streets"]:
                    node["_streets"].append(street)
                return node["id"]
        node_id = len(nodes)
        nodes.append({"id": node_id, "x": round(x, 3), "y": round(y, 3),
                      "type": None, "name": "", "group": None, "tier": 0, "price": 0,
                      "owner": None, "level": 0, "mortgaged": False, "icon": "",
                      "_streets": [street], "_street": street})
        return node_id

    street_nodes: dict[str, list[int]] = {}
    for name, spec in STREETS.items():
        chain = [add_node(x, y, name) for x, y in _sample_polyline(spec["pts"], spec["step"], spec["closed"])]
        # 去重（采样点可能落到同一路口）
        dedup = [chain[0]]
        for node_id in chain[1:]:
            if node_id != dedup[-1]:
                dedup.append(node_id)
        street_nodes[name] = dedup
        # 画路用的几何：节点**合并后**的坐标（这样每条街都精确连到共享路口）
        STREET_GEOM[name] = [(nodes[i]["x"], nodes[i]["y"]) for i in dedup]

    # ---- 每条图边的路网几何：**网页实际画出来的那条路**上的一小段 ----
    # 旧做法是拿节点去街道的原始折点上找最近折点、再截两点之间的折线段。问题是原始折点是
    # "控制点"，节点却是沿折线按弧长采样出来的中间点，两者根本不在同一个位置：截出来的段
    # 经常越过相邻格子（斜穿街区），闭合的主环末点绕回起点时更会把**整圈**截下来
    # —— 网页上的金色高亮因此斜穿地图，甚至把整个主环点亮。
    # 现在改为用**节点坐标**（与 roads() 同源）做圆角路径后再切片，端头正落在格子上，
    # 中间必然压在路面上。
    for name, chain in street_nodes.items():
        pts = [(nodes[i]["x"], nodes[i]["y"]) for i in chain]
        path, stops = _round_path_stops(pts, radius=ROAD_CORNER_RADIUS)
        for i in range(len(chain) - 1):
            a, b = chain[i], chain[i + 1]
            if a == b:
                continue
            seg = [pts[i]] + path[stops[i] + 1:stops[i + 1]] + [pts[i + 1]]
            key = f"{min(a, b)}_{max(a, b)}"
            if key not in EDGE_GEOM:      # 同一条边被两条街共用时，以先建的（主环）为准
                EDGE_GEOM[key] = [[round(x, 3), round(y, 3)] for x, y in seg]

    ring_ids = street_nodes["ring"]

    # ---- 分配格子类型：环上的地产按街区给，支街与环上余量铺事件格 ----
    prop_pool = {k: list(v) for k, v in PROPERTY_NAMES.items()}
    special_pool = list(SPECIAL_CYCLE)
    total_nodes = len(nodes)
    # 目标：地产约占一半，其余为事件格 + 起点（数量见模块级 PROPERTY_TARGET）
    property_target = PROPERTY_TARGET
    property_count = 0

    def take_special():
        return special_pool.pop(0) if special_pool else ("rest", "日常一幕", "badge-rest")

    # 起点定在环上最靠西北的节点
    start_id = min(ring_ids, key=lambda i: nodes[i]["x"] + nodes[i]["y"])
    nodes[start_id].update({"type": "start", "name": "学园前・起点", "icon": "badge-start"})

    # 先给环上节点按"隔一个放一个"的节奏铺地产，只放到六成，剩下的留给支街
    ring_cap = round(property_target * 0.55)
    order_ring = [start_id] + [i for i in ring_ids if i != start_id]
    for index, node_id in enumerate(order_ring):
        node = nodes[node_id]
        if node["type"]:
            continue
        district = district_of(node["x"], node["y"])
        want_property = (index % 2 == 1) and property_count < ring_cap and prop_pool.get(district)
        if want_property:
            key, label, tier, icon = prop_pool[district].pop(0)
            node.update({"type": "property", "name": label, "group": district,
                         "tier": tier, "price": tier * price_unit(), "icon": icon})
            property_count += 1
        else:
            kind, label, icon = take_special()
            node.update({"type": kind, "name": label, "icon": icon})

    # 支街：按"层"轮着铺（先给每条街第一个空位，再第二轮……），
    # 这样每条岔路都有自己的地产，拐进去才有理由，而不是空走两格
    chains = [(name, chain) for name, chain in street_nodes.items() if name != "ring"]
    depth_max = max((len(chain) for _, chain in chains), default=0)
    for depth in range(depth_max):
        for name, chain in chains:
            if depth >= len(chain):
                continue
            node = nodes[chain[depth]]
            if node["type"]:
                continue
            district = district_of(node["x"], node["y"])
            if property_count < property_target and prop_pool.get(district):
                key, label, tier, icon = prop_pool[district].pop(0)
                node.update({"type": "property", "name": label, "group": district,
                             "tier": tier, "price": tier * price_unit(), "icon": icon})
                property_count += 1
            else:
                kind, label, icon = take_special()
                node.update({"type": kind, "name": label, "icon": icon})

    # ---- 具名特殊格归位：黑列车在学园区、流浪猫在住宅区、炭化之人在荒废街区 ----
    # 这些格子有明确的场景归属（黑列车车站是学园内的地标），跟普通事件格一起排队铺会乱跑，
    # 所以铺完之后把它们和"目标街区里最近的一个空事件格"对调。
    for kind, want in TOKEN_DISTRICT.items():
        holders = [n for n in nodes if n["type"] == kind]
        if not holders or district_of(holders[0]["x"], holders[0]["y"]) == want:
            continue
        holder = holders[0]
        spare = None
        best = 1e9
        for node in nodes:
            if node["type"] != "rest" or district_of(node["x"], node["y"]) != want:
                continue
            gap = math.hypot(node["x"] - holder["x"], node["y"] - holder["y"])
            if gap < best:
                spare, best = node, gap
        if spare is None:
            continue
        name, icon = holder["name"], holder["icon"]
        holder.update({"type": "rest", "name": "日常一幕", "icon": "badge-rest"})
        spare.update({"type": kind, "name": name, "icon": icon})

    # ---- 边：每条街内部相邻节点相连；环首尾相接 ----
    edges: list[list[int]] = []
    for name, chain in street_nodes.items():
        pairs = list(zip(chain, chain[1:]))
        # 闭合街道的采样末点会落回起点，此时最后一对是 (起点, 起点)：
        # 它不是一条边而是一个自环，会让"当前格"出现在选路选项里，必须跳过
        if STREETS[name]["closed"] and chain[-1] != chain[0]:
            pairs.append((chain[-1], chain[0]))
        for a, b in pairs:
            if a != b:
                edges.append([a, b])

    # ---- 支线（用于渲染与说明）：非环街道去掉与环共用的端点后剩下的链 ----
    branches: list[list[int]] = []
    for name, chain in street_nodes.items():
        if name == "ring":
            continue
        inner = [i for i in chain]
        branches.append(inner)

    for node in nodes:
        node.pop("_streets", None)
        node.pop("_street", None)
    return nodes, edges, ring_ids, branches


# ---------- 校验 ----------
def validate_layout() -> list[str]:
    problems: list[str] = []
    cells, edges, main_path, branches = build_city()
    total = len(cells)

    if not (50 <= total <= 72):
        problems.append(f"格子数 {total} 超出预期区间 50–72")
    props = [c for c in cells if c["type"] == "property"]
    if len({c["name"] for c in props}) != len(props):
        problems.append("存在重名的地产")
    if len([c for c in cells if c["type"] == "start"]) != 1:
        problems.append("起点不唯一")

    for cell in cells:
        x, y = cell["x"], cell["y"]
        if cell["type"] == "start":
            continue
        if in_water(x, y):
            problems.append(f"{cell['name']} 落在水域里")
        if cell["type"] == "property":
            got = district_of(x, y)
            if got != cell["group"]:
                problems.append(f"{cell['name']} 应在 {cell['group']}，实际落在 {got}")

    # 绿地不该泡在水里（海边绿地最容易踩到），也要在地图边框内
    x0, y0, x1, y1 = FRAME
    for key, poly in GREEN_ZONES.items():
        wet = sum(1 for px, py in poly if in_water(px, py))
        if wet:
            problems.append(f"绿地 {key} 有 {wet} 个顶点落在水里")
        for px, py in poly:
            if not (x0 <= px <= x1 and y0 <= py <= y1):
                problems.append(f"绿地 {key} 顶点 ({px},{py}) 超出地图边框")
    # 街区必须严丝合缝地铺满地图：每个采样点都该命中且只命中一个街区
    probe = 0
    for gx in range(1, 22):
        for gy in range(1, 14):
            hits = [k for k, info in DISTRICTS.items()
                    if _poly_contains(info["poly"], gx + 0.5, gy + 0.5)]
            if len(hits) != 1:
                probe += 1
    if probe:
        problems.append(f"街区覆盖有 {probe} 处重叠或漏空（应处处恰好命中一个街区）")
    # 画路用的几何必须**精确穿过节点**：否则街会停在离主路一格远的地方，看着"没接上"
    node_xy = {(c["x"], c["y"]) for c in cells}
    for name, geom in STREET_GEOM.items():
        for px, py in geom:
            if (round(px, 3), round(py, 3)) not in node_xy:
                problems.append(f"街道 {name} 的绘制路径有点 ({px},{py}) 不在任何格子上")
                break

    # 建筑不许泡水：四角都要在陆地上（只测中心点的话，半边身子会伸进海里/水塘里）
    for block in buildings():
        for cx, cy in ((block["x"] - block["w"] / 2, block["y"] - block["h"] / 2),
                       (block["x"] + block["w"] / 2, block["y"] - block["h"] / 2),
                       (block["x"] - block["w"] / 2, block["y"] + block["h"] / 2),
                       (block["x"] + block["w"] / 2, block["y"] + block["h"] / 2)):
            if in_water(cx, cy):
                problems.append(f"建筑 ({block['x']},{block['y']}) 有角泡在水里")
                break
    # 具名特殊格应该在自己该在的街区
    for kind, want in TOKEN_DISTRICT.items():
        for cell in cells:
            if cell["type"] == kind and district_of(cell["x"], cell["y"]) != want:
                problems.append(f"{cell['name']} 应在 {want}，实际在 {district_of(cell['x'], cell['y'])}")

    for i in range(total):
        for j in range(i + 1, total):
            gap = math.hypot(cells[i]["x"] - cells[j]["x"], cells[i]["y"] - cells[j]["y"])
            if gap < MIN_SPACING:
                problems.append(f"{cells[i]['name']} 与 {cells[j]['name']} 太近（{gap:.2f} 格）")

    neighbors: dict[int, list[int]] = {i: [] for i in range(total)}
    for a, b in edges:
        if b not in neighbors[a]:
            neighbors[a].append(b)
        if a not in neighbors[b]:
            neighbors[b].append(a)
    for node, links in neighbors.items():
        if len(links) < 2:
            problems.append(f"{cells[node]['name']} 是死胡同（度 {len(links)}）")
    seen, stack = {main_path[0]}, [main_path[0]]
    while stack:
        node = stack.pop()
        for nxt in neighbors[node]:
            if nxt not in seen:
                seen.add(nxt)
                stack.append(nxt)
    if len(seen) != total:
        problems.append(f"地图不连通：可达 {len(seen)}/{total}")

    count: dict[str, int] = {}
    for cell in cells:
        count[cell["type"]] = count.get(cell["type"], 0) + 1
    if (count.get("property", 0) + count.get("rest", 0) + count.get("navi", 0)) / total < 0.75:
        problems.append("地产+日常+NAVI 占比过低")
    if count.get("property", 0) / total < 0.40:
        problems.append("地产占比过低")
    for required in ("navi", "cat", "monster", "train"):
        if count.get(required, 0) < 1:
            problems.append(f"缺少 {required} 格")
    for district in DISTRICTS:
        size = sum(1 for c in props if c["group"] == district)
        if size < 2:
            problems.append(f"{district} 地产不足 2 块")
    # 网络性：至少两处"支街接支街"的路口（度数 ≥ 3 的节点数）
    junctions = sum(1 for links in neighbors.values() if len(links) >= 3)
    if junctions < 3:
        problems.append(f"路口（度≥3）只有 {junctions} 个，网络感不足")
    return problems


GREEN_ZONES = {
    "campus_lawn": [(2.6, 2.4), (7.6, 2.0), (10.4, 3.6), (10.2, 5.8), (7.0, 6.2), (3.0, 5.4)],
    "park":        [(1.8, 8.0), (5.4, 8.2), (6.6, 10.6), (5.4, 12.8), (2.0, 12.6), (1.4, 10.0)],
    # 海边绿地：整块都在海岸线以内，不会被海淹掉
    "riverside":   [(14.8, 10.0), (17.2, 9.6), (18.6, 10.4), (18.0, 11.6), (15.8, 12.0), (14.4, 11.2)],
}


def green_washes() -> list[dict]:
    """绿地画成有机形状（圆弧边界 + 轻微起伏）。绿地是独立形状，可以放心圆角。"""
    return [
        {"key": key, "poly": [[round(x, 3), round(y, 3)]
                              for x, y in _round_poly(poly, radius=1.3, wobble=0.2)]}
        for key, poly in GREEN_ZONES.items()
    ]


def district_washes() -> list[dict]:
    """街区色块直接下发。

    边界已经是相邻两块**共用**的平滑曲线（见 `_curve`），所以这里不需要再做圆角或外推：
    四块严丝合缝——一直铺到地图边框（不留白），彼此之间也不重叠（不混色）。
    """
    return [
        {"key": key, "label": info["label"], "color": info["color"],
         "poly": [[round(x, 3), round(y, 3)] for x, y in info["poly"]]}
        for key, info in DISTRICTS.items()
    ]


def _round_path(pts, radius: float = 0.5, samples: int = 7) -> list[tuple[float, float]]:
    """给折线的拐角做圆弧过渡（开放折线版）。

    街道折线只有几个折点，直接用线段画会有硬角；用 Catmull-Rom 平滑又会"甩"出原路线
    （以前岔路压在主路上的元凶）。这里只在拐角处切一小段圆弧，路线本身不会偏离。
    """
    line = [tuple(p) for p in pts]
    if len(line) < 3:
        return line
    out = [line[0]]
    for i in range(1, len(line) - 1):
        prev, cur, nxt = line[i - 1], line[i], line[i + 1]
        len_a = math.hypot(cur[0] - prev[0], cur[1] - prev[1]) or 1e-9
        len_b = math.hypot(nxt[0] - cur[0], nxt[1] - cur[1]) or 1e-9
        t = min(0.42, radius / len_a, radius / len_b)
        a = (cur[0] + (prev[0] - cur[0]) * t, cur[1] + (prev[1] - cur[1]) * t)
        b = (cur[0] + (nxt[0] - cur[0]) * t, cur[1] + (nxt[1] - cur[1]) * t)
        for k in range(samples):
            u = k / samples
            x = (1 - u) ** 2 * a[0] + 2 * (1 - u) * u * cur[0] + u * u * b[0]
            y = (1 - u) ** 2 * a[1] + 2 * (1 - u) * u * cur[1] + u * u * b[1]
            out.append((round(x, 3), round(y, 3)))
    out.append(line[-1])
    return out


def _round_path_stops(pts, radius: float = 0.5, samples: int = 6):
    """与 `_round_path` 同一条圆角路线，另外给出**每个原始点在输出里的下标**。

    高亮线要精确贴在路面上，就得知道每个节点对应路线上的哪一点。`_round_path` 是
    "把拐角切掉"，节点坐标本身并不在输出里，所以这里把圆弧中点（离原拐角最近的一点）
    记为这个节点的位置。返回 (path, stops)，stops 是"序号 → path 下标"。
    """
    line = [tuple(p) for p in pts]
    if len(line) < 3:
        return line, list(range(len(line)))
    out = [line[0]]
    stops = [0]
    for i in range(1, len(line) - 1):
        prev, cur, nxt = line[i - 1], line[i], line[i + 1]
        len_a = math.hypot(cur[0] - prev[0], cur[1] - prev[1]) or 1e-9
        len_b = math.hypot(nxt[0] - cur[0], nxt[1] - cur[1]) or 1e-9
        t = min(0.42, radius / len_a, radius / len_b)
        a = (cur[0] + (prev[0] - cur[0]) * t, cur[1] + (prev[1] - cur[1]) * t)
        b = (cur[0] + (nxt[0] - cur[0]) * t, cur[1] + (nxt[1] - cur[1]) * t)
        stops.append(len(out) + samples // 2)
        for k in range(samples + 1):
            u = k / samples
            x = (1 - u) ** 2 * a[0] + 2 * (1 - u) * u * cur[0] + u * u * b[0]
            y = (1 - u) ** 2 * a[1] + 2 * (1 - u) * u * cur[1] + u * u * b[1]
            out.append((round(x, 3), round(y, 3)))
    out.append(line[-1])
    stops.append(len(out) - 1)
    return out, stops


def roads() -> list[dict]:
    """街道折线**按合并后的节点坐标**输出，带圆角过渡与 role。

    关键：画路要用 `STREET_GEOM`（节点实际坐标），不能用 STREETS 里的原始折点。
    路口合并阈值是 1.05 格，也就是某条街的端点可能被合并到最远 1 格外的环上节点 ——
    若按原始折点画，那条街就会停在离主路最多 1 格远的地方，看起来"没接上"。
    """
    build_city()                      # 填充 STREET_GEOM（0.3ms；网页每秒轮询也无所谓）
    ring_pts = STREET_GEOM.get("ring") or STREETS["ring"]["pts"]
    out = []
    for name, spec in STREETS.items():
        raw = STREET_GEOM.get(name) or [(float(x), float(y)) for x, y in spec["pts"]]
        pts = [(float(x), float(y)) for x, y in raw]
        role = spec["role"]
        if role != "ring":
            # 两端都在主环上 = 主动脉；否则是从主环拐出去的支街
            def near(point, ring=ring_pts):
                return min(math.hypot(point[0] - q[0], point[1] - q[1]) for q in ring) < 1.2
            if near(pts[0]) and near(pts[-1]):
                role = "artery"
        out.append({"key": name, "role": role, "closed": bool(spec["closed"]),
                    "pts": [[x, y] for x, y in _round_path(pts, radius=ROAD_CORNER_RADIUS)]})
    return out


def sea() -> dict:
    """右下角的海：海岸线 + 需要整块填充的顶点。"""
    return {"coast": [[round(x, 3), round(y, 3)] for x, y in SEA_COAST],
            "poly": [[round(x, 3), round(y, 3)] for x, y in SEA_POLY]}


def frame() -> dict:
    """地图边框（网页按它裁掉超出边界的水体与色块）。"""
    x0, y0, x1, y1 = FRAME
    return {"x0": x0, "y0": y0, "x1": x1, "y1": y1}


def _seg_dist(ax, ay, bx, by, x, y) -> float:
    dx, dy = bx - ax, by - ay
    span = dx * dx + dy * dy
    t = 0.0 if span == 0 else max(0.0, min(1.0, ((x - ax) * dx + (y - ay) * dy) / span))
    return ((x - (ax + t * dx)) ** 2 + (y - (ay + t * dy)) ** 2) ** 0.5


_BUILDING_CACHE: dict[int, list[dict]] = {}


def buildings(seed: int = 20260914) -> list[dict]:
    """街区里的成片建筑体块（格点采样，避让街道/路口/绿地/水域）。

    预览页与网页共用这一份，建筑才会长得一样。结果按 seed 缓存——网页每秒轮询一次状态。
    """
    import random as _random

    if seed in _BUILDING_CACHE:
        return [dict(block) for block in _BUILDING_CACHE[seed]]

    street_lines = []
    for spec in STREETS.values():
        pts = list(spec["pts"]) + ([spec["pts"][0]] if spec["closed"] else [])
        street_lines.append(pts)
    grid = _sample_nodes()

    def near_street(x, y, margin) -> bool:
        for pts in street_lines:
            for i in range(len(pts) - 1):
                if _seg_dist(pts[i][0], pts[i][1], pts[i + 1][0], pts[i + 1][1], x, y) < margin:
                    return True
        return False

    def near_node(x, y, margin) -> bool:
        return any((nx - x) ** 2 + (ny - y) ** 2 < margin * margin for nx, ny in grid)

    def brush_clear(x, y, w, h, margin=0.1) -> bool:
        """建筑是"面"不是"点"：四角（再放一点余量）都得在陆地上。

        只测中心点的话，贴着海岸/水塘生成的建筑会有一半泡进水里 —— 之前就是这个毛病。
        额外的 margin 留给立体的侧面（挤出厚度）和网页侧的缩放。
        """
        half_w, half_h = w / 2 + margin, h / 2 + margin
        for cx, cy in ((x - half_w, y - half_h), (x + half_w, y - half_h),
                       (x - half_w, y + half_h), (x + half_w, y + half_h)):
            if in_water(cx, cy):
                return False
        return True

    rng = _random.Random(seed)
    blocks: list[dict] = []
    step = 0.62
    y = 0.8
    while y < WORLD_H - 0.8:
        x = 0.8
        while x < WORLD_W - 0.8:
            if not near_street(x, y, 0.72) and not near_node(x, y, 0.62) and not in_water(x, y):
                if rng.random() < 0.62 and not any(_poly_contains(p, x, y) for p in GREEN_ZONES.values()):
                    w = 0.22 + rng.random() * 0.24
                    h = 0.18 + rng.random() * 0.22
                    k = rng.random()
                    if brush_clear(x, y, w, h):
                        blocks.append({
                            "x": round(x, 3), "y": round(y, 3),
                            "w": round(w, 3), "h": round(h, 3), "k": round(k, 3),
                        })
            x += step
        y += step
    _BUILDING_CACHE[seed] = blocks
    return [dict(block) for block in blocks]


def _sample_nodes() -> list[tuple[float, float]]:
    """只做一次布点，供建筑避让路口使用（不重复走完整 build_city）。"""
    points: list[tuple[float, float]] = []
    for spec in STREETS.values():
        for node in _sample_polyline(spec["pts"], spec["step"], bool(spec["closed"])):
            points.append((node[0], node[1]))
    return points


def decor() -> dict:
    """制图装饰数据：街区/绿地/水体/路网/建筑/边框。网页按这些画底图。"""
    return {
        "districts": district_washes(),
        "green": green_washes(),
        "roads": roads(),
        "sea": sea(),
        "ponds": [{"x": px, "y": py, "rx": rx, "ry": ry} for px, py, rx, ry in PONDS],
        "buildings": buildings(),
        "edgeGeom": dict(EDGE_GEOM),
        "frame": frame(),
        "world": [WORLD_W, WORLD_H],
    }


if __name__ == "__main__":
    cells, edges, main_path, branches = build_city()
    print(f"格子 {len(cells)}｜边 {len(edges)}｜主环 {len(main_path)} 节点｜支街 {len(branches)} 条")
    count: dict[str, int] = {}
    for cell in cells:
        count[cell["type"]] = count.get(cell["type"], 0) + 1
    print("类型分布：", count)
    print("地产分布：", {k: sum(1 for c in cells if c.get("group") == k) for k in DISTRICTS})
    issues = validate_layout()
    print("校验：", "通过 ✓" if not issues else f"发现 {len(issues)} 个问题")
    for issue in issues[:20]:
        print("  -", issue)
