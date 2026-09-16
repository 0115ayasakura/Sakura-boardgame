"""棋盘游戏引擎：五子棋与图结构大富翁。

纯标准库实现，不依赖 Sakura SDK；所有方法只收发有界 JSON，
方便通过 Plugin API v4 的工具回调暴露给模型。

大富翁地图是"节点 + 邻接边"的图结构：格子之间可以有岔路和回路，
掷骰后逐步移动，遇岔路由行动方选择路线。
"""
from __future__ import annotations

import math
import random
from typing import Any

GOMOKU_SIZE = 15
GOMOKU_WIN_RUN = 5
GOMOKU_DIRECTIONS = ((0, 1), (1, 0), (1, 1), (1, -1))

GAMES = ("gomoku", "monopoly")
PLAYERS = ("user", "sakura")

PLAYER_LABEL = {"user": "玩家", "sakura": "夜乃樱"}
PLAYER_MARK = {"user": "○", "sakura": "●"}

# ---- 大富翁 ----
# 存档版本：**只要改过地图结构或经济数值就必须 +1**（旧存档会连带老数值一起被读回来，
# 表现得像"新版本没生效"）。当前 5：起始资金 1000→1400、工资 200→250、地价单位 100→60、
# 垄断门槛 全持→五成、判定 现金→总资产。
MAP_VERSION = 5
# 起始资金与工资：跑批实测"一局里的钱 ÷ 棋盘总价"决定地产系统铺不铺得满。
# 城市图 29 处地产总价 ¥3420；2 人 × ¥1000 起始只有 0.77，买走不到一半、垄断 33%；
# 改成 2 × ¥1400（工资 ¥200→250）后比值 1.07，买走 16.8/29、垄断 56%、租金事件 5.15 笔/局。
MONOPOLY_START_CASH = 1400
MONOPOLY_PASS_START = 250
MONOPOLY_TAX = 100
MONOPOLY_BONUS = 150
MONOPOLY_MAX_LEVEL = 1
# 地价单位：地产价 = 档位 × 这个数（城市图 ¥60/120/180，随机图同）。原来是 100——
# 跑批显示在"每圈一份工资"的节奏下买不起几块地（每局只买得下 5 块、垄断 6%），降到 60 后
# 买得动、铺得开（买到的地 6.5 → 8.9 处，垄断 6% → 22%）。
MONOPOLY_PRICE_UNIT = 60
MONOPOLY_RENT_DIVISOR = 2       # 租金 = 现价 ÷ 这个数 × 2^等级（÷2 = 五成租金，÷1 = 等额）
MONOPOLY_CAT_COST = 50
MONOPOLY_CAT_GAIN = 150
MONOPOLY_MONSTER_TOLL = 150
MONOPOLY_TRAIN_TOLL = 300
MONOPOLY_PRICE_STEP_MOVES = 8   # 每 8 回合未购地产涨价
MONOPOLY_PRICE_LAP_STEP = 20
MONOPOLY_PRICE_CAP = 1.5        # 涨价上限（相对基础价）
# ---- 平衡参数（跑批实验定的，见 boardgame_tests/econ_report.py）----
MONOPOLY_ROUNDS_PER_CELL = 1.5  # 回合上限 = 格数 × 系数（57 格 → 86 步，每人约 1.5 圈）
MONOPOLY_PROPERTY_SCORE = 0.6   # 终局判定时，地产按现价的几成计入总资产
MONOPOLY_HOLD_SHARE = 0.5       # 持有一个街区多少比例的地产算"垄断"（租金翻倍）
MONOPOLY_HOLD_MIN = 2           # 垄断至少要有几处（只有 1 处的街区不可能垄断）

MONOPOLY_GROUPS = {
    "shopping": {"label": "街市", "color": "#d95a72"},
    "gakuen": {"label": "学园区", "color": "#4a8bc9"},
    "daily": {"label": "住宅区", "color": "#3f9e83"},
    "taboo": {"label": "禁忌区", "color": "#4a5470"},
}
MONOPOLY_GROUP_NAMES = {
    "shopping": ["家庭餐厅", "披萨店", "KTV包厢", "情侣酒店"],
    "gakuen": ["学生会室", "图书馆委员会", "探索委员会", "大圣堂"],
    "daily": ["樱的公寓", "玩家的公寓", "公园", "流浪猫的据点"],
    "taboo": ["东京巨壁", "地下铁道", "下水道井盖", "黑列车车站"],
}
MONOPOLY_GROUP_TIERS = {
    "shopping": (1, 1, 2, 2),
    "gakuen": (1, 2, 2, 3),
    "daily": (1, 1, 1, 2),
    "taboo": (2, 3, 3, 3),
}

# NAVI 事件卡：title/flavor 供页面与解说使用，effect 是引擎效果。
EVENT_CARDS = (
    {"title": "三菜便当", "flavor": "冰咖喱、渍金枪鱼、炖牛肉——第三样和第一样重复了。但幸福不分冷热。", "effect": {"type": "cash", "amount": 150}},
    {"title": "芝士堆成山的披萨", "flavor": "吃下去才发现，原来自己是喜欢芝士的。", "effect": {"type": "cash", "amount": 120}},
    {"title": "命令：握手表演", "flavor": "被要求表演才艺。熟练地完成三周回转并汪了一声，赏金入账。", "effect": {"type": "cash", "amount": 100}},
    {"title": "好感度排行榜", "flavor": "第二。走在路上买了一罐宣言可乐冷静了一下。", "effect": {"type": "cash", "amount": -140}},
    {"title": "水仙的壁橱", "flavor": "从壁橱深处发现水仙，她默默递来一沓零花钱：「哥哥，拿去用吧」", "effect": {"type": "cash", "amount": 150}},
    {"title": "四颗心脏的梦", "flavor": "黑衣车掌在梦里摇响了巨铃。惊醒后去买护身符。", "effect": {"type": "cash", "amount": -160}},
    {"title": "高木秀明的遗言", "flavor": "脊椎仍插在古电脑里。敬畏之余，投一枚硬币当香火钱。", "effect": {"type": "cash", "amount": -130}},
    {"title": "井盖探险", "flavor": "井盖编号 142653190215。地下废弃铁道里捡到一件反现实遗物。", "effect": {"type": "cash", "amount": 200}},
    {"title": "舞步音符", "flavor": "用音符把自己从大楼间弹飞，赶上了末班电车，省下一笔打车费。", "effect": {"type": "cash", "amount": 100}},
    {"title": "NAVI的骚扰提醒", "flavor": "猫耳AI提醒：体检套餐截止日就是今天。", "effect": {"type": "cash", "amount": -130}},
    {"title": "KTV五人组", "flavor": "华淡起哄、水仙跑调、索菲优雅吃蜂蜜吐司。最后樱合唱了那首歌。", "effect": {"type": "cash", "amount": 180}},
    {"title": "蓝之心脏警报", "flavor": "反现实密度异常上升，全楼避难。便利店抢购花销不小。", "effect": {"type": "cash", "amount": -150}},
    {"title": "学园奖学金", "flavor": "学生会长亲自盖章批下来的款项，附言只有四个字：「好好努力」", "effect": {"type": "cash", "amount": 250}},
    {"title": "学生会室加薪", "flavor": "调停了探索委员会与图书馆委员会的纠纷。会长轻声说：「我总是不太会说话」。", "effect": {"type": "cash", "amount": 130}},
    {"title": "猫的祝福", "flavor": "灰色钩尾的流浪猫在第 215 次终于让人摸了头。下次掷骰 +1。", "effect": {"type": "dice_bonus", "bonus": 1}},
    {"title": "时间停止", "flavor": "「你的时间，先停一停。下次的账单，就一并免了吧」——B.E.G. 暂停了一次租金。", "effect": {"type": "rent_free"}},
    {"title": "沉睡的宜必斯", "flavor": "无调图书馆的白鸟借出一本禁书。下次经过起点，工资翻倍。", "effect": {"type": "salary_next"}},
    {"title": "黑列车的梦路", "flavor": "梦见列车进站，车掌直直盯着。醒来发现已站在起点。", "effect": {"type": "goto_start"}},
    {"title": "学生会活动费", "flavor": "以学生会的名义向在场所有人收取活动费。", "effect": {"type": "collect_each", "amount": 100}},
    {"title": "崩月家的请帖", "flavor": "家族聚会的份子钱，不交不行。", "effect": {"type": "pay_each", "amount": 100}},
    {"title": "究极女主角运", "flavor": "被卷入怪獣战却毫发无伤，事后领到观测协作抚恤金。", "effect": {"type": "cash", "amount": 160}},
    {"title": "恒常性擦除", "flavor": "街角的反现实被世界擦除了，顺手捡到一管观测数据。", "effect": {"type": "cash", "amount": 110}},
    {"title": "门禁六点", "flavor": "「玩到几点了。」——迟到罚站，零花钱被扣。", "effect": {"type": "cash", "amount": -120}},
    {"title": "周末的约会", "flavor": "「太疯跑的话，回去的路会很难走哦」——但花出去的每一分都值得。", "effect": {"type": "cash", "amount": -80}},
    {"title": "学生食堂的偏爱", "flavor": "「今天给你多盛一勺。」——食堂阿姨的偏爱换来了满格体力。", "effect": {"type": "cash", "amount": 90}},
    {"title": "蓝之心脏的共鸣", "flavor": "蓝色的心脏与街角的反现实同频了一瞬。回过神来，账户里多了一笔谁也说不清来源的钱。", "effect": {"type": "cash", "amount": 800}, "weight": 2, "rare": True},
    {"title": "黑列车的警笛", "flavor": "列车毫无预兆地碾过天际，巨铃震得耳膜发麻。回过神来，钱包和理智都空了一半。", "effect": {"type": "cash", "amount": -620}, "weight": 2, "rare": True},
)

SPECIAL_NAMES = {"navi": "NAVI播报", "cat": "灰色的流浪猫", "monster": "炭化之人", "train": "黑列车"}

# 地产名 → 地标插图 key：随机地图的格子名来自 MONOPOLY_GROUP_NAMES，
# 用这张表映射到 city_map 的 poi 图片名，两张地图就共用同一套插图了。
POI_ICON_BY_NAME = {
    "家庭餐厅": "poi-family", "披萨店": "poi-family", "KTV包厢": "poi-ktv", "情侣酒店": "poi-hotel",
    "学生会室": "poi-council", "图书馆委员会": "poi-libcom", "探索委员会": "poi-explore",
    "大圣堂": "poi-cathedral",
    "樱的公寓": "poi-sakura_flat", "玩家的公寓": "poi-user_flat", "公园": "poi-park",
    "流浪猫的据点": "poi-catspot",
    "东京巨壁": "poi-wall", "地下铁道": "poi-subway", "下水道井盖": "poi-manhole",
    "黑列车车站": "poi-station",
}


def poi_icon_for(name: str) -> str:
    """按地产名找地标插图 key（随机图的"XX别馆"也归到原图）。"""
    if name in POI_ICON_BY_NAME:
        return POI_ICON_BY_NAME[name]
    for base, icon in POI_ICON_BY_NAME.items():
        if name.startswith(base):
            return icon
    return ""


def cell_effect_text(cell: dict[str, Any]) -> str:
    """格子本身的效果（写进对局记录，让人一眼看出这一格干了什么）。"""
    kind = cell["type"]
    if kind == "tax":
        return f"（效果：缴纳 ¥{MONOPOLY_TAX}）"
    if kind == "bonus":
        return f"（效果：获得 ¥{MONOPOLY_BONUS}）"
    if kind == "cat":
        return f"（效果：付 ¥{MONOPOLY_CAT_COST} 喂猫，换回 ¥{MONOPOLY_CAT_GAIN}）"
    if kind == "monster":
        return f"（效果：损失 ¥{MONOPOLY_MONSTER_TOLL}）"
    if kind == "train":
        return f"（效果：支付 ¥{MONOPOLY_TRAIN_TOLL} 车票钱）"
    return ""
_CELL_NAMES = {**SPECIAL_NAMES, "tax": "特别会计", "bonus": "学园补助", "rest": "日常一幕"}

# 沿主路循环铺设的格子序列：地产 ≈50%、日常 ≈17%、NAVI ≈14%，
# 税/补助/猫/怪獣/黑列车各占约 3%（整张图总数少而零星），保证"大部分格子是地产和无效果格"。
_CELL_PATTERN = (
    "property", "property", "rest", "property", "navi",
    "property", "property", "rest", "property", "property",
    "navi", "tax", "property", "rest", "property",
    "property", "navi", "property", "rest", "cat",
    "property", "property", "navi", "bonus", "property",
    "rest", "property", "property", "monster", "property",
    "navi", "property", "rest", "property", "property",
    "train",
)


class GameError(ValueError):
    """非法操作（未开局、轮次错误、坐标越界等）。"""


class GomokuGame:
    """十五路五子棋：引擎管落子、轮次和胜负；夜乃樱的棋由 AI 代算。

    开局时双方各掷一次骰子决定先后手（点数大者先手，平手重掷）；
    也可以显式指定先手。
    """

    kind = "gomoku"

    def __init__(self, first: str = "dice", seed: int | None = None) -> None:
        if first not in PLAYERS and first != "dice":
            raise GameError("先手只能是 user、sakura 或 dice（由骰子决定）。")
        rng = random.Random(seed)
        rolls = {"user": rng.randint(1, 6), "sakura": rng.randint(1, 6)}
        rerolls = 0
        while rolls["user"] == rolls["sakura"] and rerolls < 10:
            rolls = {"user": rng.randint(1, 6), "sakura": rng.randint(1, 6)}
            rerolls += 1
        if rolls["user"] == rolls["sakura"]:  # 极端情况下强制分先后
            rolls["sakura"] = rolls["user"] % 6 + 1
        self.first_rolls = rolls
        if first == "dice":
            first = "user" if rolls["user"] > rolls["sakura"] else "sakura"
        self.first = first
        self.size = GOMOKU_SIZE
        self.board: list[list[int]] = [[0] * self.size for _ in range(self.size)]
        self.code = {"user": 1, "sakura": 2}
        self.turn = first
        self.winner: str | None = None
        self.move_count = 0
        self.last_move: tuple[int, int] | None = None

    def place(self, player: str, row: int, col: int) -> dict[str, Any]:
        if self.winner:
            raise GameError("对局已经结束，请先开始新一局。")
        if player != self.turn:
            raise GameError(f"还没轮到 {PLAYER_LABEL[player]}。现在是 {PLAYER_LABEL[self.turn]} 行动。")
        if not (1 <= row <= self.size and 1 <= col <= self.size):
            raise GameError(f"坐标超出棋盘：行和列都要在 1–{self.size} 之间。")
        r, c = row - 1, col - 1
        if self.board[r][c]:
            raise GameError(f"({row},{col}) 上已经有子了，换一处落子。")

        self.board[r][c] = self.code[player]
        self.last_move = (r, c)
        self.move_count += 1
        won = self._has_win(r, c, self.code[player])
        if won:
            self.winner = player
        else:
            self.turn = "sakura" if self.turn == "user" else "user"
        result: dict[str, Any] = {
            "player": player,
            "move": [row, col],
            "winner": self.winner,
            "next": None if self.winner else self.turn,
            "threats": self.threat_text(),
            "board": self.render(),
        }
        return result

    def ai_move(self, player: str, level: int, aggression: float = 0.45) -> tuple[dict[str, Any], str]:
        """让 AI 为 player 落子，返回 (place结果, 落子理由)。

        aggression 0~1：越大越偏进攻，越小越偏稳健防守——用来让角色性格影响棋风。
        """
        try:
            from . import gomoku_ai
        except ImportError:
            import gomoku_ai  # type: ignore[no-redef]
        decision = gomoku_ai.choose_move(self.board, self.code[player], level, aggression)
        result = self.place(player, decision["row"], decision["col"])
        return result, decision["reason"]

    def _has_win(self, r: int, c: int, code: int) -> bool:
        for dr, dc in GOMOKU_DIRECTIONS:
            count = 1
            for sign in (1, -1):
                nr, nc = r + sign * dr, c + sign * dc
                while 0 <= nr < self.size and 0 <= nc < self.size and self.board[nr][nc] == code:
                    count += 1
                    nr += sign * dr
                    nc += sign * dc
            if count >= GOMOKU_WIN_RUN:
                return True
        return False

    def _best_run(self, player: str) -> dict[str, int] | None:
        code = self.code[player]
        best: dict[str, int] | None = None
        for r in range(self.size):
            for c in range(self.size):
                if self.board[r][c] != code:
                    continue
                for dr, dc in GOMOKU_DIRECTIONS:
                    pr, pc = r - dr, c - dc
                    if 0 <= pr < self.size and 0 <= pc < self.size and self.board[pr][pc] == code:
                        continue
                    count = 0
                    nr, nc = r, c
                    while 0 <= nr < self.size and 0 <= nc < self.size and self.board[nr][nc] == code:
                        count += 1
                        nr += dr
                        nc += dc
                    open_ends = 0
                    back_r, back_c = r - dr, c - dc
                    if 0 <= back_r < self.size and 0 <= back_c < self.size and self.board[back_r][back_c] == 0:
                        open_ends += 1
                    if 0 <= nr < self.size and 0 <= nc < self.size and self.board[nr][nc] == 0:
                        open_ends += 1
                    if count >= 2 and (best is None or count > best["run"] or (count == best["run"] and open_ends > best["open"])):
                        best = {"run": count, "open": open_ends}
        return best

    def threat_text(self) -> str:
        parts: list[str] = []
        for player in PLAYERS:
            best = self._best_run(player)
            if not best:
                continue
            label = PLAYER_LABEL[player]
            if best["run"] >= 4:
                parts.append(f"{label}已有{'活' if best['open'] >= 1 else ''}四连，{'直接危险！' if best['open'] >= 1 else '可以忽略'}")
            elif best["run"] == 3 and best["open"] == 2:
                parts.append(f"{label}有一手活三，再放一回合会变成活四")
            elif best["run"] == 3:
                parts.append(f"{label}有个三连但一头被堵住了")
        return "；".join(parts)

    def render(self) -> str:
        header = "   " + "".join(f"{col + 1:>2}" for col in range(self.size))
        lines = [header]
        for r in range(self.size):
            row = [f"{r + 1:>2} "]
            for c in range(self.size):
                if self.board[r][c] == 0:
                    row.append(" ·")
                else:
                    mark = PLAYER_MARK["user"] if self.board[r][c] == 1 else PLAYER_MARK["sakura"]
                    row.append(f" {mark}")
            lines.append("".join(row))
        last = f"（最后一手：{self.last_move[0] + 1},{self.last_move[1] + 1}）" if self.last_move else ""
        return "\n".join(lines) + f"\n○=玩家 ●=夜乃樱 {last}"

    def summary(self) -> str:
        return f"五子棋：第 {self.move_count} 手，轮到 {PLAYER_LABEL[self.turn]} 落子。"

    def to_dict(self) -> dict[str, Any]:
        return {
            "kind": self.kind,
            "board": self.board,
            "turn": self.turn,
            "winner": self.winner,
            "move_count": self.move_count,
            "last_move": list(self.last_move) if self.last_move else None,
            "first": self.first,
            "first_rolls": dict(self.first_rolls),
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "GomokuGame":
        game = cls.__new__(cls)
        game.size = GOMOKU_SIZE
        game.board = data["board"]
        game.turn = data["turn"]
        game.winner = data["winner"]
        game.move_count = int(data["move_count"])
        last = data["last_move"]
        game.last_move = (last[0], last[1]) if last else None
        game.code = {"user": 1, "sakura": 2}
        game.first = data.get("first") or "user"
        game.first_rolls = data.get("first_rolls") or {"user": 0, "sakura": 0}
        return game


def card_effect_text(card: dict[str, Any]) -> str:
    """事件卡的实际效果，写成一句人话（进对局记录，也显示在网页弹出的卡面上）。"""
    effect = card["effect"]
    kind = effect["type"]
    if kind == "cash":
        amount = effect["amount"]
        return f"效果：{'获得' if amount >= 0 else '损失'} ¥{abs(amount)}"
    if kind == "dice_bonus":
        return f"效果：下次掷骰 +{effect['bonus']}"
    if kind == "rent_free":
        return "效果：免付一次租金"
    if kind == "salary_next":
        return "效果：下次经过起点工资翻倍"
    if kind == "goto_start":
        return "效果：立刻回到起点"
    if kind == "collect_each":
        return f"效果：向对手收取 ¥{effect['amount']}"
    if kind == "pay_each":
        return f"效果：付给对手 ¥{effect['amount']}"
    if kind == "move":
        delta = effect["delta"]
        return f"效果：{'前进' if delta > 0 else '后退'} {abs(delta)} 格"
    if kind == "skip":
        return "效果：下回合停掷"
    return "效果：无"


class MonopolyGame:
    """图结构大富翁：岔路地图 + 地产经营。

    地图是"节点 + 邻接边"的图：从网格点阵生成生成树，再随机补环形成岔路与回路。
    掷骰后逐步移动，遇岔路（多个前进方向）暂停等待行动方选路；
    经过或落在起点领工资；落在地产格产生决策菜单（买/升/抵/赎/卖/跳）。
    付不起账单强制逐个抵押；破产时资产移交债主；回合上限按总资产（现金 + 地产现价六成）判胜负。
    """

    kind = "monopoly"

    def __init__(self, size: int, dice_sides: int, seed: int, first: str = "user",
                 map_kind: str = "random") -> None:
        self.requested_size = max(12, min(96, int(size)))
        self.cols, self.rows = self._grid_shape(self.requested_size)
        self.size = self.cols * self.rows
        self.dice_sides = max(2, min(20, int(dice_sides)))
        self.seed = int(seed)
        self._rolls = 0
        self._card_draws = 0
        self.cash = {"user": MONOPOLY_START_CASH, "sakura": MONOPOLY_START_CASH}
        self.skip_pending = {"user": False, "sakura": False}
        self.last_step: dict[str, list[int] | None] = {"user": None, "sakura": None}
        self.flags = {p: {"salary_next": False, "rent_free": False, "dice_bonus": 0} for p in PLAYERS}
        self.turn = first if first in PLAYERS else "user"
        self.winner: str | None = None
        self.move_count = 0
        self.pending: dict[str, Any] | None = None
        self._walk: dict[str, Any] | None = None
        self._last_walk: dict[str, Any] | None = None
        self.map_kind = map_kind if map_kind in ("city", "random") else "random"
        self.main_path: list[int] = []
        self.branches: list[list[int]] = []
        self.cells, self.edges = self._generate_map()
        # 回合上限 = 格数 × 1.5（城市图 57 格 → 86 步）。原来是"格数"（57 步 = 双方各约 28 步
        # ≈ 绕环 0.9 圈），一圈都绕不完：工资只能领一次、租金几乎收不到、垄断凑不齐，
        # 跑批实测"买地的一方胜率只有 6.5%"就是这么来的。1.5 倍后每人约 1.5 圈，经济才转得动。
        self.max_rounds = max(36, round(self.size * MONOPOLY_ROUNDS_PER_CELL))
        self._neighbors = self._build_neighbors()
        self._moves = self._build_moves()
        # 双方都从起点格出发
        self.positions = {"user": self.start_node, "sakura": self.start_node}

    # ---- 地图生成 ---------------------------------------------------

    @staticmethod
    def _grid_shape(requested: int) -> tuple[int, int]:
        """把请求的格子数取整到 m×n 网格（宽 > 高，越接近请求值越好）。"""
        best: tuple[int, int, int] | None = None
        for rows in range(3, 11):
            cols = max(rows, -(-requested // rows))
            total = cols * rows
            if total > 96:
                continue
            score = (abs(total - requested), cols)  # 差异小优先，其次偏宽
            if best is None or score < best[0]:
                best = (score, cols, rows)
        assert best is not None
        return best[1], best[2]

    def _generate_map(self) -> tuple[list[dict[str, Any]], list[list[int]]]:
        """生成地图：city=手工城市图（街道网络），random=程序生成的岔路地图。

        城市图来自 city_map.py：外环干道 + 两条主动脉 + 支街，支街之间也相连，
        因此是有多个回路的网络，且每格度数 ≥ 2（无死胡同）。
        """
        if self.map_kind == "city":
            try:
                from . import city_map
            except ImportError:
                import city_map  # type: ignore[no-redef]
            cells, edges, main_path, branches = city_map.build_city()
            self.size = len(cells)
            self.cols = round(city_map.WORLD_W)
            self.rows = round(city_map.WORLD_H)
            self.start_node = next(c["id"] for c in cells if c["type"] == "start")
            self.main_path = list(main_path)
            self.branches = [list(chain) for chain in branches]
            return cells, edges

        """（随机图）蛇形主环 + 短支线。

        主路按蛇形顺序覆盖全部格子，格子沿路"一步步往前走"，首尾相接成环。
        支线是主路上**相距很近（2–4 步）**的两个点之间的短连接 —— 贴着主干鼓出一个小包，
        不是横跨全图的拉长弦（那样整张图会乱）。支线两端都钉在主路上，所以没有死胡同，
        每个格子的连通度都 ≥ 2。
        """
        rng = random.Random(self.seed + 2)
        cols, rows, count = self.cols, self.rows, self.size

        # 1) 坐标在网格基础上抖动：保留上一版那种手画路线图的感觉，
        #    但格子间距放到 1.95 格（约 257px），图钉和名字牌不会挤在一起。
        STEP, JITTER = 1.95, 0.26
        points: list[list[float]] = []
        for i in range(count):
            col, row = i % cols, i // cols
            points.append([
                round(col * STEP + rng.uniform(-JITTER, JITTER), 3),
                round(row * STEP + rng.uniform(-JITTER, JITTER), 3),
            ])

        def dist(a: int, b: int) -> float:
            return math.hypot(points[a][0] - points[b][0], points[a][1] - points[b][1])

        def seg_cross(a1, a2, b1, b2) -> bool:
            """两段是否真正相交（共享端点/共线不算）。"""
            def cross(o, p, q):
                return (p[0] - o[0]) * (q[1] - o[1]) - (p[1] - o[1]) * (q[0] - o[0])
            d1, d2 = cross(b1, b2, a1), cross(b1, b2, a2)
            d3, d4 = cross(a1, a2, b1), cross(a1, a2, b2)
            return ((d1 > 0) != (d2 > 0)) and ((d3 > 0) != (d4 > 0))

        def crosses_trunk(new_a: int, new_b: int, seq) -> bool:
            """把 seq 里这一对当作新边，检查它是否和主路其它边交叉。"""
            for t in range(len(seq)):
                p, q = seq[t], seq[(t + 1) % len(seq)]
                if p in (new_a, new_b) or q in (new_a, new_b):
                    continue
                if seg_cross(points[new_a], points[new_b], points[p], points[q]):
                    return True
            return False

        # 2) 贪心最近邻成环 + 完整 2-opt 松解（把横跨全图的长边消掉）
        order = [0]
        remaining = set(range(1, count))
        while remaining:
            last = order[-1]
            nxt = min(remaining, key=lambda node: dist(last, node))
            order.append(nxt)
            remaining.discard(nxt)

        # 2-opt 松解：每次只做**一处**改进，然后从头重新扫描（first improvement）。
        # 之前是"改完一处后继续用旧的 b 比较"，比较基准已经失效 → 收敛不了，
        # 表现为**主路自己交叉**。改成重新扫描后，收敛时主路不会再有交叉。
        improvements = 0
        while improvements < 900:
            moved = False
            for i in range(count - 1):
                a, b = order[i], order[i + 1]
                for j in range(i + 1, count):
                    c = order[j]
                    d = order[(j + 1) % count]
                    if dist(a, c) + dist(b, d) < dist(a, b) + dist(c, d) - 1e-9:
                        order[i + 1:j + 1] = reversed(order[i + 1:j + 1])
                        improvements += 1
                        moved = True
                        break
                if moved:
                    break
            if not moved:
                break

        # 3) 消除锐角转弯：转角太尖（内角 < 60°）的地方尝试换一段路来救，
        #    换完不能变得更长太多、也不能引入新的锐角。
        def interior_cos(i: int) -> float:
            a, b, c = points[order[i - 1]], points[order[i]], points[order[(i + 1) % count]]
            v1 = (a[0] - b[0], a[1] - b[1])
            v2 = (c[0] - b[0], c[1] - b[1])
            n1, n2 = math.hypot(v1[0], v1[1]), math.hypot(v2[0], v2[1])
            if n1 < 1e-9 or n2 < 1e-9:
                return 1.0
            return (v1[0] * v2[0] + v1[1] * v2[1]) / (n1 * n2)

        COS_LIMIT = math.cos(math.radians(60))     # 内角 ≥60° 才算不尖

        def is_acute(i: int) -> bool:
            return interior_cos(i) > COS_LIMIT

        # 只检查受影响的两个拐角（换了 i+1..j 这一段，只有 i 和 j+1 的角度会变），
        # 这样每次尝试是 O(1)，整个修复过程很快
        for _ in range(90):
            bad = [i for i in range(count) if is_acute(i)]
            if not bad:
                break
            fixed = False
            for i in bad:
                if not is_acute(i):
                    continue
                for j in range(i + 2, min(count, i + 16)):
                    k = (j + 1) % count
                    before = is_acute(i) + is_acute(k)
                    # 换过之后的两条新边是 (order[i], order[j]) 和 (order[i+1], order[j+1])，
                    # 两个端点都不受反转影响，所以可以直接先判定：会交叉就放弃这次换路
                    if crosses_trunk(order[i], order[j], order) or \
                       crosses_trunk(order[i + 1], order[k], order):
                        continue
                    order[i + 1:j + 1] = reversed(order[i + 1:j + 1])
                    after = is_acute(i) + is_acute(k)
                    if after < before:
                        fixed = True
                        break
                    order[i + 1:j + 1] = reversed(order[i + 1:j + 1])
                if fixed:
                    break
            if not fixed:
                break        # 换路也救不了了，剩下的少数尖角接受（挪点的效果实测更差）

        # 4) 收尾再跑一次 2-opt：保证最终主路**收敛过、绝不自己交叉**。
        #    实测这比"多消几个尖角"重要得多——交叉一眼就看出来，尖角不太显眼。
        guard = 0
        while guard < 1200:
            moved = False
            for i in range(count - 1):
                a, b = order[i], order[i + 1]
                for j in range(i + 1, count):
                    c = order[j]
                    d = order[(j + 1) % count]
                    if dist(a, c) + dist(b, d) < dist(a, b) + dist(c, d) - 1e-9:
                        order[i + 1:j + 1] = reversed(order[i + 1:j + 1])
                        guard += 1
                        moved = True
                        break
                if moved:
                    break
            if not moved:
                break

        path_edges = [(order[i], order[i + 1]) for i in range(count - 1)]
        # 首尾相接，形成环路（回到起点＝跑完一圈）
        closing = (order[-1], order[0])

        # 4) 支线：跨步 ≥3（至少跨过主路上两个节点），且必须
        #    ① 不擦到别的格子 ② 不穿过主路段（否则就是"支线穿主线"）
        def seg_hits_cell(a: tuple[float, float], b: tuple[float, float],
                          pad: float = 0.32) -> bool:
            for k, (px, py) in enumerate(points):
                if k == a_index or k == b_index:      # 端点自己不算
                    continue
                vx, vy = b[0] - a[0], b[1] - a[1]
                span = vx * vx + vy * vy
                t = 0.0 if span == 0 else max(0.0, min(1.0, ((px - a[0]) * vx + (py - a[1]) * vy) / span))
                if math.hypot(px - (a[0] + t * vx), py - (a[1] + t * vy)) < pad:
                    return True
            return False

        trunk_segments = [(points[order[i]], points[order[i + 1]]) for i in range(count - 1)]
        trunk_segments.append((points[order[-1]], points[order[0]]))
        trunk_edges = {frozenset((order[i], order[i + 1])) for i in range(count - 1)}
        trunk_edges.add(frozenset((order[-1], order[0])))

        chords: list[tuple[int, int]] = []
        used: set[frozenset[int]] = set()
        target = max(2, count // 7)
        candidates: list[tuple[int, int]] = []
        for i in range(count):
            for step in (3, 4, 5, 6):
                j = i + step
                if j >= count:
                    break
                a, b = order[i], order[j]
                if frozenset((a, b)) in trunk_edges:
                    continue
                a_index, b_index = a, b
                pa, pb = points[a], points[b]
                if math.hypot(pa[0] - pb[0], pa[1] - pb[1]) > 3.8:
                    continue                                   # 太长就不算"贴着主干"
                if seg_hits_cell(pa, pb):
                    continue                                   # 压到别的格子
                if any(seg_cross(pa, pb, s[0], s[1]) for s in trunk_segments):
                    continue                                   # 穿过主路
                candidates.append((a, b))
        rng.shuffle(candidates)
        for a, b in candidates:
            if len(chords) >= target:
                break
            if frozenset((a, b)) in used:
                continue
            if (sum(1 for x, y in chords if a in (x, y)) >= 2
                    or sum(1 for x, y in chords if b in (x, y)) >= 2):
                continue
            used.add(frozenset((a, b)))
            chords.append((a, b))

        edge_list = [list(e) for e in path_edges] + [list(closing)] + [list(e) for e in chords]
        self.main_path = list(order)
        self.branches = [[a, b] for a, b in chords]

        # 4) 格子：坐标用上面抖动过的点，起点在主路首格
        self.start_node = order[0]
        nodes: list[dict[str, Any]] = []
        for i in range(count):
            nodes.append({
                "id": i, "x": points[i][0], "y": points[i][1], "type": "rest", "name": "日常一幕",
                "group": None, "tier": 0, "price": 0, "owner": None, "level": 0, "mortgaged": False,
            })
        nodes[self.start_node].update({"type": "start", "name": "起点"})

        groups = list(MONOPOLY_GROUPS)
        rng.shuffle(groups)
        names = {g: list(MONOPOLY_GROUP_NAMES[g]) for g in MONOPOLY_GROUPS}
        for g in names:
            rng.shuffle(names[g])
        tiers = {g: list(MONOPOLY_GROUP_TIERS[g]) for g in MONOPOLY_GROUPS}
        for g in tiers:
            rng.shuffle(tiers[g])
        group_cycle = 0
        duplicate_counters: dict[str, int] = {}

        # 按路程顺序循环铺设：地产 ≈50%，日常 ≈17%，NAVI ≈14%，其余特殊格零星分布
        for position, index in enumerate(order):
            if index == self.start_node:
                continue
            kind = _CELL_PATTERN[position % len(_CELL_PATTERN)]
            if kind == "property":
                group = groups[group_cycle % len(groups)]
                group_cycle += 1
                name = names[group].pop() if names[group] else None
                if name is None:
                    duplicate_counters[group] = duplicate_counters.get(group, 0) + 1
                    name = f"{MONOPOLY_GROUP_NAMES[group][0]}别馆{duplicate_counters[group]}"
                tier = tiers[group].pop() if tiers[group] else 2
                nodes[index].update({"type": "property", "name": name, "group": group, "tier": tier,
                                     "price": tier * MONOPOLY_PRICE_UNIT, "icon": poi_icon_for(name)})
            else:
                nodes[index].update({"type": kind, "name": _CELL_NAMES[kind]})

        # 小地图上按比例可能铺不满特殊格，兜底补足（占用普通格）
        for required in ("navi", "cat", "monster", "train"):
            if any(node["type"] == required for node in nodes):
                continue
            for wanted in ("rest", "bonus", "tax", "property"):
                target_index = next((i for i in order if nodes[i]["type"] == wanted), None)
                if target_index is None:
                    continue
                nodes[target_index].update({
                    "type": required, "name": SPECIAL_NAMES[required],
                    "group": None, "tier": 0, "price": 0, "owner": None, "level": 0, "mortgaged": False,
                })
                break
        return nodes, edge_list

    def _build_neighbors(self) -> dict[int, list[int]]:
        neighbors: dict[int, list[int]] = {i: [] for i in range(self.size)}
        for a, b in self.edges:
            neighbors[a].append(b)
            neighbors[b].append(a)
        return neighbors

    def _build_moves(self) -> dict[int, list[int]]:
        """可走方向。

        城市图的行进方向由地图本身规定：**主环是单向的**（方向 = main_path 的顺序，
        也就是起点出去先走学园正门那一侧），支街与主动脉是双向的（可以拐进去，也可以退出来）。
        随机图没有主环，仍然是任意方向都能走。
        """
        moves: dict[int, list[int]] = {i: [] for i in range(self.size)}

        def allow(src: int, dst: int) -> None:
            if 0 <= src < self.size and 0 <= dst < self.size and src != dst and dst not in moves[src]:
                moves[src].append(dst)

        ring = list(self.main_path)
        if len(ring) > 1 and ring[0] == ring[-1]:
            ring = ring[:-1]          # 采样末点会落回起点，去掉重复
        if self.map_kind != "city" or len(ring) < 3:
            for a, b in self.edges:
                allow(a, b)
                allow(b, a)
            return moves
        for i, node in enumerate(ring):
            allow(node, ring[(i + 1) % len(ring)])
        # 主环节点的"环上位置"，用于识别与主环重叠的支路
        ring_pos = {node: i for i, node in enumerate(ring)}
        for chain in self.branches:
            for a, b in zip(chain, chain[1:]):
                # 与主环重叠的"支路"（两端都是环上相邻节点）只保留环的顺行方向：
                # 双向放开会把主环的单行规则撕开（实测：河堤路沿线与主环重合，
                # 从岔路返回主路后可以逆时针走——违反行进方向原则）。
                if a in ring_pos and b in ring_pos:
                    pa, pb = ring_pos[a], ring_pos[b]
                    span = (pb - pa) % len(ring)
                    if span == 1:
                        allow(a, b)            # 与主环顺行同向：只留正向
                        continue
                    if span == len(ring) - 1:
                        allow(b, a)            # 与主环逆行相对：只留反向中的顺行
                        continue
                allow(a, b)
                allow(b, a)
        return moves

    def next_nodes(self, node: int) -> list[int]:
        """从某格出发允许前往的格子（网页用它画金色箭头）。"""
        return list(self._moves.get(node) or [])

    # ---- 随机流 -----------------------------------------------------

    def _dice_rng(self) -> random.Random:
        rng = random.Random(self.seed)
        for _ in range(self._rolls):
            rng.randint(1, self.dice_sides)
        return rng

    def _draw_card(self) -> dict[str, Any]:
        """按权重抽事件卡。

        普通卡权重 10，彩蛋卡（大赚/大亏）权重 2 —— 极端收益事件稀有但存在，
        对应"抽到彩蛋"的惊喜感。抽取过程由 seed 决定，同一存档可复现。
        """
        self._card_draws += 1
        rng = random.Random(self.seed + 5)
        weights = [int(card.get("weight", 10)) for card in EVENT_CARDS]
        total = sum(weights)
        for _ in range(self._card_draws - 1):   # 推进随机流到本次抽卡
            rng.random()
        pick = rng.random() * total
        acc = 0
        for card, weight in zip(EVENT_CARDS, weights):
            acc += weight
            if pick <= acc:
                return card
        return EVENT_CARDS[-1]

    def _other(self, player: str) -> str:
        return "sakura" if player == "user" else "user"

    # ---- 经济系统 ---------------------------------------------------

    def current_price(self, cell: dict[str, Any]) -> int:
        if cell["type"] != "property" or cell["price"] <= 0:
            return 0
        inflated = cell["price"] + (self.move_count // MONOPOLY_PRICE_STEP_MOVES) * MONOPOLY_PRICE_LAP_STEP
        return min(int(cell["price"] * MONOPOLY_PRICE_CAP), inflated)

    def _rent(self, cell: dict[str, Any]) -> int:
        rent = (self.current_price(cell) // MONOPOLY_RENT_DIVISOR) * (2 ** cell["level"])
        if cell["group"] and self._has_monopoly(cell["owner"], cell["group"]):
            rent *= 2
        return rent

    def _has_monopoly(self, player: str | None, group: str | None) -> bool:
        """街区垄断（租金翻倍）：持有该街区至少六成地产、且至少 2 处，并且都没抵押。

        以前要求"整个街区全部持有"——城市图每区 5–9 处，一局只有一圈多，
        跑批实测 0% 的对局能凑成，翻倍租金这条规则等于不存在。
        """
        if not player or not group:
            return False
        members = [c for c in self.cells if c["type"] == "property" and c["group"] == group]
        if len(members) < MONOPOLY_HOLD_MIN:
            return False
        need = max(MONOPOLY_HOLD_MIN, math.ceil(len(members) * MONOPOLY_HOLD_SHARE))
        owned = [c for c in members if c["owner"] == player and not c["mortgaged"]]
        return len(owned) >= need

    def _player_monopolies(self, player: str) -> list[str]:
        return [g for g in MONOPOLY_GROUPS if self._has_monopoly(player, g)]

    def stats(self) -> dict[str, Any]:
        """双方当前的地产数量与租金收益（供网页展示）。

        income = 名下未抵押地产的当前租金合计，也就是对手踩上去时要付的钱。
        """
        out: dict[str, Any] = {}
        for player in PLAYERS:
            props = [c for c in self.cells if c["type"] == "property" and c["owner"] == player]
            out[player] = {
                "properties": len(props),
                "mortgaged": sum(1 for c in props if c["mortgaged"]),
                "properties_value": sum(self.current_price(c) for c in props),
                "income": sum(self._rent(c) for c in props if not c["mortgaged"]),
                "monopolies": [MONOPOLY_GROUPS[g]["label"] for g in self._player_monopolies(player)],
            }
        return out

    def _transfer_assets(self, loser: str, beneficiary: str | None) -> None:
        for cell in self.cells:
            if cell["type"] == "property" and cell["owner"] == loser:
                if beneficiary:
                    cell["owner"] = beneficiary
                else:
                    cell["owner"] = None
                    cell["level"] = 0
                    cell["mortgaged"] = False

    def _pay(self, player: str, amount: int, beneficiary: str | None, story: list[str]) -> dict[str, Any]:
        if self.cash[player] >= amount:
            self.cash[player] -= amount
            if beneficiary:
                self.cash[beneficiary] += amount
            return {"paid": amount, "due": amount, "bankrupt": False}
        story.append(f"{PLAYER_LABEL[player]}现金不足（¥{self.cash[player]}，应付 ¥{amount}），开始抵押地产！")
        while self.cash[player] < amount:
            candidates = [(index, cell) for index, cell in enumerate(self.cells)
                          if cell["type"] == "property" and cell["owner"] == player and not cell["mortgaged"]]
            if not candidates:
                break
            index, cell = min(candidates, key=lambda pair: pair[1]["price"])
            cell["mortgaged"] = True
            gain = self.current_price(cell) // 2      # 与主动抵押同口径（都是现价的一半）
            self.cash[player] += gain
            story.append(f"抵押 {cell['name']}，回收 ¥{gain}。")
        paid = min(self.cash[player], amount)
        self.cash[player] -= paid
        if beneficiary:
            self.cash[beneficiary] += paid
        bankrupt = paid < amount
        if bankrupt:
            self._transfer_assets(player, beneficiary)
            self.winner = beneficiary if beneficiary else self._other(player)
            story.append(f"{PLAYER_LABEL[player]}倾尽所有也付不起了，破产！剩余资产移交给{PLAYER_LABEL[beneficiary] if beneficiary else '银行'}。")
        return {"paid": paid, "due": amount, "bankrupt": bankrupt}

    # ---- 行动 -------------------------------------------------------

    def _grant_salary(self, player: str, story: list[str]) -> None:
        salary = MONOPOLY_PASS_START
        if self.flags[player]["salary_next"]:
            salary *= 2
            self.flags[player]["salary_next"] = False
            story.append(f"经过起点，领工资 +¥{salary}（禁书知识让工资翻了倍）。")
        else:
            story.append(f"经过起点，领工资 +¥{salary}。")
        self.cash[player] += salary

    def roll(self, player: str) -> dict[str, Any]:
        if self.winner:
            raise GameError("对局已经结束，请先开始新一局。")
        if self.pending:
            raise GameError(f"{PLAYER_LABEL[self.pending['player']]}还有待决策，先用 boardgame_decide 处理。")
        if self._walk:
            raise GameError(f"{PLAYER_LABEL[self._walk['player']]}还在走格子（剩 {self._walk['steps_left']} 步），先完成移动。")
        if player != self.turn:
            raise GameError(f"还没轮到 {PLAYER_LABEL[player]}。现在是 {PLAYER_LABEL[self.turn]} 行动。")

        if self.skip_pending[player]:
            self.skip_pending[player] = False
            self._pass_turn()
            self.move_count += 1
            story = [f"{PLAYER_LABEL[player]}被罚停一回合。"]
            self._maybe_settle(story)
            return {
                "player": player,
                "skipped": True,
                "story": story,
                "winner": self.winner,
                "next": None if self.winner else self.turn,
                "board": self.render(),
            }

        self._rolls += 1
        dice = self._dice_rng().randint(1, self.dice_sides)
        bonus = self.flags[player]["dice_bonus"]
        if bonus:
            self.flags[player]["dice_bonus"] = 0
            dice += bonus
        dice = min(dice, self.dice_sides * 2)
        self._walk = {"player": player, "steps_left": dice, "path": [self.positions[player]]}
        story = [f"{PLAYER_LABEL[player]}掷出 {dice} 点（{'含猫的祝福 +' + str(bonus) if bonus else '裸骰'}），从 #{self.positions[player]}「{self.cells[self.positions[player]]['name']}」出发。"]
        self._continue_walk(story)
        return self._walk_result(story, dice=dice, player=player)

    def _walk_result(self, story: list[str], dice: int | None, player: str) -> dict[str, Any]:
        # 走完后 _walk 已被清空，此时用最后一次的路径，保证前端能做移动动画
        walk = self._walk or getattr(self, "_last_walk", None) or {"path": [], "steps_left": 0}
        return {
            "player": player,
            "dice": dice,
            "path": list(walk["path"]),
            "steps_left": walk["steps_left"],
            "story": story,
            "pending": self.pending,
            "winner": self.winner,
            "next": None if (self.winner or self.pending or self._walk) else self.turn,
            "board": self.render(),
        }

    def _continue_walk(self, story: list[str]) -> bool:
        """继续当前行走；走完时结算落点并返回 True（已换手）。"""
        walk = self._walk
        while True:
            if walk["steps_left"] <= 0:
                self._finish_walk(story)
                return True
            node = walk["path"][-1]
            neighbors = self._moves[node]
            came_from = walk["path"][-2] if len(walk["path"]) >= 2 else None
            if came_from is None:
                # 行走的第一步也不能原路折返：排除上一回合"最后一步的来路"
                # （例如上回合沿 #39→#38 走完，这回合从 #38 迈第一步时不给 #39）
                last = self.last_step.get(walk["player"])
                if last and last[1] == node:
                    came_from = last[0]
            options = [n for n in neighbors if n != came_from] or list(neighbors)
            if len(options) == 1:
                self._step_to(options[0], story)
                continue
            # 岔路：等待行动方选路
            self.pending = {"player": walk["player"], "type": "route",
                            "options": options, "steps_left": walk["steps_left"]}
            return False

    def _step_to(self, node: int, story: list[str]) -> None:
        walk = self._walk
        walk["steps_left"] -= 1
        walk["path"].append(node)
        self.positions[walk["player"]] = node
        if self.cells[node]["type"] == "start":
            self._grant_salary(walk["player"], story)

    def _finish_walk(self, story: list[str]) -> None:
        walk = self._walk
        player = walk["player"]
        final = walk["path"][-1]
        self._last_walk = {"player": player, "path": list(walk["path"]), "steps_left": 0}
        # 记录最后一步（来路 → 落点）：下一回合从落点迈第一步时不许原路折返
        if len(walk["path"]) >= 2:
            self.last_step[player] = [walk["path"][-2], walk["path"][-1]]
        self._walk = None
        self.move_count += 1
        if self.cells[final]["type"] != "start":
            self._resolve_landing(player, final, allow_navi=True, story=story)
        self._maybe_settle(story)
        # 落点决策（买地/升级等）期间不换手，等 decide 完成后再移交轮次
        if not self.winner and not self.pending:
            self._pass_turn()

    def decide(self, player: str, action: str) -> dict[str, Any]:
        if self.winner:
            raise GameError("对局已经结束，请先开始新一局。")
        if not self.pending:
            raise GameError("当前没有待决策，正常掷骰就行。")
        if player != self.pending["player"]:
            raise GameError(f"这个决策轮不到 {PLAYER_LABEL[player]}，是 {PLAYER_LABEL[self.pending['player']]} 的。")
        pending_type = self.pending.get("type")

        if pending_type == "route":
            try:
                target = int(action)
            except ValueError as error:
                raise GameError(f"路线决策要给出目标格编号，可选：{self._route_label()}。") from error
            if target not in self.pending["options"]:
                raise GameError(f"不能走 #{target}，可选路线：{self._route_label()}。")
            self.pending = None
            story = [f"{PLAYER_LABEL[player]}选择走向 #{target}「{self.cells[target]['name']}」。"]
            self._step_to(target, story)
            self._continue_walk(story)
            walk = self._walk
            # 路径必须覆盖**走完之后**的完整轨迹：若这一步把剩余步数走完，
            # _walk 已被清空，此时从 _last_walk 取完整路径——否则网页动画
            # 会把棋子停在倒数第二格（实测就是"棋子停在 #2"的根因）
            if walk:
                path = list(walk["path"])
                steps_left = walk["steps_left"]
            elif self._last_walk and self._last_walk["player"] == player:
                path = list(self._last_walk["path"])
                steps_left = 0
            else:
                path = []
                steps_left = 0
            return {
                "player": player,
                "action": f"route:{target}",
                "path": path,
                "steps_left": steps_left,
                "story": story,
                "pending": self.pending,
                "winner": self.winner,
                "next": None if (self.winner or self.pending or self._walk) else self.turn,
                "board": self.render(),
            }

        # 落点决策菜单
        cell_index = self.pending["cell"]
        cell = self.cells[cell_index]
        options = self.pending["options"]
        if action not in options:
            raise GameError(f"当前可选项是 {'、'.join(options)}，不能选 {action}。")

        price = self.current_price(cell)
        story: list[str] = []
        if action == "skip":
            story.append(f"{PLAYER_LABEL[player]}看着「{cell['name']}」，什么都没做。")
        elif action == "buy":
            self.cash[player] -= price
            cell["owner"] = player
            story.append(f"{PLAYER_LABEL[player]}花 ¥{price} 买下了「{cell['name']}」，基础租金 ¥{price // 2}。")
        elif action == "upgrade":
            cost = price // 2
            self.cash[player] -= cost
            cell["level"] += 1
            story.append(f"{PLAYER_LABEL[player]}花 ¥{cost} 升级了「{cell['name']}」，租金涨到 ¥{self._rent(cell)}。")
        elif action == "mortgage":
            self.cash[player] += price // 2
            cell["mortgaged"] = True
            story.append(f"{PLAYER_LABEL[player]}把「{cell['name']}」抵押给了特别会计，回收 ¥{price // 2}（抵押期间不收租）。")
        elif action == "redeem":
            cost = price * 3 // 5
            self.cash[player] -= cost
            cell["mortgaged"] = False
            story.append(f"{PLAYER_LABEL[player]}花 ¥{cost} 赎回了「{cell['name']}」。")
        elif action == "sell":
            # 卖出也按现价（以前用基础价，涨价后卖掉比赎回还亏）
            value = price // 6 if cell["mortgaged"] else price // 3
            self.cash[player] += value
            cell["owner"] = None
            cell["level"] = 0
            cell["mortgaged"] = False
            story.append(f"{PLAYER_LABEL[player]}卖掉了「{cell['name']}」，回收 ¥{value}。")

        self.pending = None
        self.move_count += 1
        self._maybe_settle(story)
        if not self.winner:
            self._pass_turn()
        return {
            "player": player,
            "action": action,
            "cell": cell_index,
            "cash": dict(self.cash),
            "story": story,
            "winner": self.winner,
            "next": None if self.winner else self.turn,
            "board": self.render(),
        }

    def _route_label(self) -> str:
        return "、".join(f"#{node}「{self.cells[node]['name']}」" for node in self.pending["options"])

    def _pending_label(self) -> str:
        if not self.pending:
            return ""
        if self.pending.get("type") == "route":
            return f"选路线（{self._route_label()}）"
        options = self.pending.get("options") or []
        return f"处理 #{self.pending['cell']} 格地产（{'、'.join(options)}）"

    def _own_property_options(self, player: str, cell: dict[str, Any]) -> list[str]:
        options: list[str] = []
        price = self.current_price(cell)
        if cell["level"] < MONOPOLY_MAX_LEVEL and not cell["mortgaged"] and self.cash[player] >= price // 2:
            options.append("upgrade")
        if not cell["mortgaged"]:
            options.append("mortgage")
        elif self.cash[player] >= price * 3 // 5:
            options.append("redeem")
        options.append("sell")
        options.append("skip")
        return options

    def _resolve_landing(self, player: str, cell_index: int, allow_navi: bool, story: list[str]) -> dict[str, Any]:
        cell = self.cells[cell_index]
        kind = cell["type"]

        if kind == "property":
            price = self.current_price(cell)
            if cell["owner"] is None:
                if self.cash[player] >= price:
                    self.pending = {"player": player, "type": "land", "cell": cell_index, "options": ["buy", "skip"]}
                    story.append(f"走到无主地产「{cell['name']}」（¥{price}），现金够买。")
                    return {"kind": "offer_buy", "cell": cell_index, "price": price}
                story.append(f"走到无主地产「{cell['name']}」（¥{price}），但现金只剩 ¥{self.cash[player]}，买不起。")
                return {"kind": "cannot_afford", "price": price}
            if cell["owner"] == player:
                options = self._own_property_options(player, cell)
                if options != ["sell", "skip"] or (cell["mortgaged"] and self.cash[player] >= price * 3 // 5):
                    self.pending = {"player": player, "type": "land", "cell": cell_index, "options": options}
                    if cell["mortgaged"]:
                        story.append(f"回到被抵押的「{cell['name']}」。可以赎回（¥{price * 3 // 5}）、卖掉或跳过。")
                    else:
                        story.append(f"回到自己的「{cell['name']}」，可以升级（租金翻倍）、抵押、卖掉或跳过。")
                    return {"kind": "offer_menu", "cell": cell_index, "options": options}
                story.append(f"回到自己的「{cell['name']}」，安心歇脚。")
                return {"kind": "nothing"}
            if cell["mortgaged"]:
                story.append(f"踩到 {PLAYER_LABEL[cell['owner']]}的「{cell['name']}」，但已被抵押，不用交租。")
                return {"kind": "mortgaged"}
            if self.flags[player]["rent_free"]:
                self.flags[player]["rent_free"] = False
                story.append(f"踩到 {PLAYER_LABEL[cell['owner']]}的「{cell['name']}」，租金 ¥{self._rent(cell)}——但樱发动『时间停止』把账单停住了，免租！")
                return {"kind": "rent_free"}
            rent = self._rent(cell)
            monopoly_note = "（垄断加成）" if self._has_monopoly(cell["owner"], cell["group"]) else ""
            payment = self._pay(player, rent, cell["owner"], story)
            story.append(f"踩到 {PLAYER_LABEL[cell['owner']]}的「{cell['name']}」{monopoly_note}，交租金 ¥{payment['paid']}（应收 ¥{payment['due']}）。")
            return {"kind": "rent", "amount": payment["paid"], "bankrupt": payment["bankrupt"]}

        if kind == "cat":
            if self.cash[player] >= MONOPOLY_CAT_COST:
                self.cash[player] -= MONOPOLY_CAT_COST
                self.cash[player] += MONOPOLY_CAT_GAIN
                story.append(f"在「灰色的流浪猫」跟前蹲下来，花 ¥{MONOPOLY_CAT_COST} 买了猫粮——这次它没有躲开（+¥{MONOPOLY_CAT_GAIN}）。")
                return {"kind": "cat"}
            story.append("路过灰色的流浪猫，想喂猫却连猫粮钱都掏不出来……只能远远看一眼。")
            return {"kind": "nothing"}

        if kind == "monster":
            story.append(f"「炭化之人」出现在路口！樱发动『时间停止』把它定在原地——但被烧掉的钱停不住（-¥{MONOPOLY_MONSTER_TOLL}）。")
            payment = self._pay(player, MONOPOLY_MONSTER_TOLL, None, story)
            return {"kind": "monster", "amount": payment["paid"], "bankrupt": payment["bankrupt"]}

        if kind == "train":
            story.append(f"黑列车无声地停在面前，持巨铃的车掌伸出了手——这是车票钱（-¥{MONOPOLY_TRAIN_TOLL}）。")
            payment = self._pay(player, MONOPOLY_TRAIN_TOLL, None, story)
            return {"kind": "train", "amount": payment["paid"], "bankrupt": payment["bankrupt"]}

        if kind == "tax":
            payment = self._pay(player, MONOPOLY_TAX, None, story)
            story.append(f"踩到特别会计，缴税 ¥{payment['paid']}。")
            return {"kind": "tax", "amount": payment["paid"], "bankrupt": payment["bankrupt"]}

        if kind == "bonus":
            self.cash[player] += MONOPOLY_BONUS
            story.append(f"踩到学园补助，+¥{MONOPOLY_BONUS}。")
            return {"kind": "bonus", "amount": MONOPOLY_BONUS}

        if kind == "navi":
            if not allow_navi:
                story.append("NAVI 路过，但这次没有推送。")
                return {"kind": "nothing"}
            card = self._draw_card()
            flavor = card["flavor"].replace("你", PLAYER_LABEL[player])
            # 把实际效果写进同一行：对局记录和网页弹出的卡面都要看得出"这张卡到底做了什么"
            story.append(f"NAVI通知【{card['title']}】{flavor}（{card_effect_text(card)}）")
            return self._apply_card(player, card, flavor, story)

        return {"kind": "nothing"}

    def _apply_card(self, player: str, card: dict[str, Any], flavor: str, story: list[str]) -> dict[str, Any]:
        effect = card["effect"]
        kind = effect["type"]
        if kind == "cash":
            amount = effect["amount"]
            if amount >= 0:
                self.cash[player] += amount
            else:
                payment = self._pay(player, -amount, None, story)
                return {"kind": "navi_card", "card": card["title"], "bankrupt": payment["bankrupt"]}
            return {"kind": "navi_card", "card": card["title"]}
        if kind == "move":
            # 图结构地图上的"移动"事件：沿当前路径回退/前进按邻接走，岔路自动直行
            delta = effect["delta"]
            steps = abs(delta)
            node = self.positions[player]
            came_from: int | None = None
            for _ in range(steps):
                options = [n for n in self._moves[node] if n != came_from] or list(self._moves[node])
                came_from = node
                node = options[0]
            self.positions[player] = node
            story.append(f"移动到 #{node}「{self.cells[node]['name']}」。")
            if self.cells[node]["type"] == "start":
                self._grant_salary(player, story)
            chain = self._resolve_landing(player, node, allow_navi=False, story=story)
            return {"kind": "navi_card", "card": card["title"], "chain": chain}
        if kind == "skip":
            self.skip_pending[player] = True
            story.append("下回合停掷。")
            return {"kind": "navi_card", "card": card["title"]}
        if kind == "salary_next":
            self.flags[player]["salary_next"] = True
            return {"kind": "navi_card", "card": card["title"]}
        if kind == "rent_free":
            self.flags[player]["rent_free"] = True
            return {"kind": "navi_card", "card": card["title"]}
        if kind == "dice_bonus":
            self.flags[player]["dice_bonus"] += effect["bonus"]
            return {"kind": "navi_card", "card": card["title"]}
        if kind == "goto_start":
            self.positions[player] = self.start_node
            self._grant_salary(player, story)
            return {"kind": "navi_card", "card": card["title"]}
        if kind == "collect_each":
            other = self._other(player)
            payment = self._pay(other, effect["amount"], player, story)
            if payment["bankrupt"]:
                story.append(f"{PLAYER_LABEL[other]}被会费压垮了！")
            return {"kind": "navi_card", "card": card["title"]}
        if kind == "pay_each":
            other = self._other(player)
            payment = self._pay(player, effect["amount"], other, story)
            return {"kind": "navi_card", "card": card["title"], "bankrupt": payment["bankrupt"]}
        return {"kind": "navi_card", "card": card["title"]}

    # ---- 结算与展示 -------------------------------------------------

    def _settle(self) -> str:
        """回合上限结算：按**总资产**判定（现金 + 地产现价 × 六成）。

        以前只看现金——买地等于把现金换成不计分的资产，于是"全程不买地"严格占优
        （跑批配对实验：买地那方胜率 6.5%，攒钱那方 93.5%）。改成总资产后，
        买地方回到 51% 左右。抵押出去的地产已经拿过一半现钱，不再重复计入。
        """
        def net_worth(player: str) -> tuple[float, int, int]:
            props = [c for c in self.cells if c["type"] == "property" and c["owner"] == player]
            held = sum(self.current_price(c) for c in props if not c["mortgaged"]) * MONOPOLY_PROPERTY_SCORE
            score = self.cash[player] + held
            return (score, self.cash[player], len(props))

        user_score = net_worth("user")
        sakura_score = net_worth("sakura")

        def describe(player: str, s: tuple[float, int, int]) -> str:
            return f"总资产 ¥{s[0]:.0f}（现金 ¥{s[1]} + 地产 {s[2]} 处）"

        winner = "user" if user_score >= sakura_score else "sakura"
        self.winner = winner
        return (f"回合数达到上限（{self.max_rounds} 回合），按总资产判定："
                f"玩家 {describe('user', user_score)} 对 夜乃樱 {describe('sakura', sakura_score)}，"
                f"{PLAYER_LABEL[winner]}获胜！")

    def _maybe_settle(self, story: list[str]) -> None:
        if self.winner is None and self.pending is None and not self._walk and self.move_count >= self.max_rounds:
            story.append(self._settle())

    def _pass_turn(self) -> None:
        self.turn = self._other(self.turn)

    def render(self) -> str:
        lines = []
        for player in PLAYERS:
            node = self.positions[player]
            moves = "、".join(f"#{n}「{self.cells[n]['name']}」" for n in self._moves[node])
            lines.append(f"{PLAYER_LABEL[player]}在 #{node}「{self.cells[node]['name']}」（现金 ¥{self.cash[player]}，可行方向：{moves}）")
        owned = []
        for cell in self.cells:
            if cell["type"] != "property" or cell["owner"] is None:
                continue
            state = "抵押中" if cell["mortgaged"] else ("已升级 " if cell["level"] else "")
            owned.append(f"#{cell['id']} {cell['name']}[{MONOPOLY_GROUPS[cell['group']]['label']}]（{PLAYER_LABEL[cell['owner']]}·{state}租¥{self._rent(cell)}）")
        lines.append("地产一览：" + ("；".join(owned) if owned else "尚无地产主"))
        monopolies = []
        for player in PLAYERS:
            groups = self._player_monopolies(player)
            if groups:
                monopolies.append(f"{PLAYER_LABEL[player]}垄断{'、'.join(MONOPOLY_GROUPS[g]['label'] for g in groups)}（租金×2）")
        if monopolies:
            lines.append("；".join(monopolies))
        if self._walk:
            path = " → ".join(f"#{p}" for p in self._walk["path"])
            lines.append(f"移动中：{path}，还剩 {self._walk['steps_left']} 步。")
        if self.pending:
            lines.append(f"待决策：{PLAYER_LABEL[self.pending['player']]}——{self._pending_label()}。")
        return "\n".join(lines)

    def summary(self) -> str:
        walk_note = f"（{PLAYER_LABEL[self._walk['player']]}走格子中，剩 {self._walk['steps_left']} 步）" if self._walk else ""
        return (
            f"大富翁：第 {self.move_count}/{self.max_rounds} 回合，轮到 {PLAYER_LABEL[self.turn]}{walk_note}。"
            f"玩家现金 ¥{self.cash['user']}，夜乃樱现金 ¥{self.cash['sakura']}。"
        )

    def to_dict(self) -> dict[str, Any]:
        return {
            "kind": self.kind,
            # 存档版本：**改过地图结构或经济数值就要 +1**，否则旧存档会把老数值（起始资金、
            # 地价、玩家现金）整个带进新版本，看起来像"新版本没生效"。
            # 4 → 5：起始资金 1000→1400、工资 200→250、地价单位 100→60、垄断门槛与判定规则都变了。
            "map_version": MAP_VERSION,
            "cols": self.cols,
            "rows": self.rows,
            "size": self.size,
            "dice_sides": self.dice_sides,
            "seed": self.seed,
            "rolls": self._rolls,
            "card_draws": self._card_draws,
            "cash": dict(self.cash),
            "positions": dict(self.positions),
            "skip_pending": dict(self.skip_pending),
            "last_step": {p: (list(self.last_step[p]) if self.last_step.get(p) else None)
                          for p in PLAYERS},
            "flags": {p: dict(self.flags[p]) for p in PLAYERS},
            "turn": self.turn,
            "winner": self.winner,
            "move_count": self.move_count,
            "max_rounds": self.max_rounds,
            "pending": dict(self.pending) if self.pending else None,
            "walk": dict(self._walk) if self._walk else None,
            "cells": self.cells,
            "edges": self.edges,
            "start_node": self.start_node,
            "map_kind": getattr(self, "map_kind", "random"),
            "main_path": list(getattr(self, "main_path", [])),
            "branches": [list(c) for c in getattr(self, "branches", [])],
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "MonopolyGame":
        if data.get("map_version") != MAP_VERSION:
            raise GameError("旧版存档不兼容。")
        game = cls.__new__(cls)
        game.kind = "monopoly"
        game.cols = int(data["cols"])
        game.rows = int(data["rows"])
        game.size = int(data["size"])
        game.requested_size = game.size
        game.dice_sides = int(data["dice_sides"])
        game.seed = int(data["seed"])
        game._rolls = int(data["rolls"])
        game._card_draws = int(data["card_draws"])
        game.cash = {p: int(data["cash"][p]) for p in PLAYERS}
        game.positions = {p: int(data["positions"][p]) for p in PLAYERS}
        game.skip_pending = {p: bool(data["skip_pending"][p]) for p in PLAYERS}
        # 旧存档没有 last_step 字段，容忍缺失（None = 首步不限制来路）
        raw_last = data.get("last_step") or {}
        game.last_step = {p: (list(raw_last[p]) if raw_last.get(p) else None) for p in PLAYERS}
        game.flags = {p: dict(data["flags"][p]) for p in PLAYERS}
        game.turn = data["turn"]
        game.winner = data["winner"]
        game.move_count = int(data["move_count"])
        game.max_rounds = int(data["max_rounds"])
        game.pending = dict(data["pending"]) if data["pending"] else None
        game._walk = dict(data["walk"]) if data["walk"] else None
        game.cells = data["cells"]
        game.edges = data["edges"]
        game.map_kind = data.get("map_kind") or "random"
        game.main_path = [int(i) for i in (data.get("main_path") or [])]
        game.branches = [[int(i) for i in chain] for chain in (data.get("branches") or [])]
        game.start_node = int(data.get("start_node")
                             if data.get("start_node") is not None
                             else next(c["id"] for c in game.cells if c["type"] == "start"))
        game._last_walk = None
        game._neighbors = game._build_neighbors()
        game._moves = game._build_moves()
        return game


def create_game(kind: str, first: str, dice_sides: int, map_size: int, event_ratio: float = 0.0,
                map_kind: str = "random") -> GomokuGame | MonopolyGame:
    if kind == "gomoku":
        return GomokuGame(first=first or "dice")
    if first not in PLAYERS:
        if first == "dice":
            first = "user"
        else:
            raise GameError("先手只能是 user 或 sakura。")
    if kind == "monopoly":
        return MonopolyGame(size=map_size, dice_sides=dice_sides, seed=random.randint(1, 10**9),
                            first=first, map_kind=map_kind)
    raise GameError(f"不支持的游戏类型 {kind}，可选：{ '、'.join(GAMES) }。")


def restore_game(data: dict[str, Any]) -> GomokuGame | MonopolyGame:
    kind = data.get("kind")
    if kind == "gomoku":
        return GomokuGame.from_dict(data)
    if kind == "monopoly":
        return MonopolyGame.from_dict(data)
    raise GameError("存档损坏：未知的游戏类型。")


def rules_text(game: GomokuGame | MonopolyGame) -> str:
    if game.kind == "monopoly":
        return (
            "规则：在城市地图的街道上轮流掷骰，掷出几点就走几格。"
            "**主环是单向的**——行进方向由地图规定，从起点出发第一格就是学园正门；"
            "支街是双向的，可以拐进去再退出来。走到路口（有多个可行方向）会暂停，"
            "由当前行动方选择走哪一格，选完继续走完剩余步数。经过或落在起点领工资。"
            "地产分四组（学园区/街市/住宅区/荒废街区），持有同组六成且无抵押时租金翻倍。"
            "走到无主地产且现金够时会暂停询问是否购买；走回自己的地产可以升级（租金翻倍）、抵押（回收半价，期间不收租）、"
            "赎回（付六成价）或卖掉（回收三分之一）——此时先向用户说明选项或说出你的决定，再调用 boardgame_decide(action=…)。"
            "踩到他人地产自动付租金；税格扣钱、赏格加钱、猫格喂猫、怪獣格遇袭、黑列车格交车票钱、问号格由 NAVI 推送事件卡。"
            "付不起账单会被强制抵押地产，破产时资产移交债主；回合数到上限后按总资产（现金 + 地产现价六成）判胜负。掷骰用 boardgame_roll。"
        )
    return (
        "规则：十五路棋盘，横竖斜先连成五子者胜。工具调用方式：用户落子后你先点评上一手；"
        "轮到你时调用 boardgame_place（不填坐标即由 AI 替你计算落子和理由），再用你的语气解说这手棋。"
    )
