"""棋盘游戏引擎：五子棋与图结构大富翁。

纯标准库实现，不依赖 Sakura SDK；所有方法只收发有界 JSON，
方便通过 Plugin API v4 的工具回调暴露给模型。

大富翁地图是"节点 + 邻接边"的图结构：格子之间可以有岔路和回路，
掷骰后逐步移动，遇岔路由行动方选择路线。
"""
from __future__ import annotations

import math
import copy
import random
from typing import Any

try:
    from . import theme as _theme
    from .black_train import BlackTrainMixin
    from .economy import CashCategory, cash_amount
    from .effect_policy import adventure_active, acquire_duration, consume_effect, tick_completed_action
except ImportError:
    import theme as _theme  # type: ignore[no-redef]
    from black_train import BlackTrainMixin
    from economy import CashCategory, cash_amount
    from effect_policy import adventure_active, acquire_duration, consume_effect, tick_completed_action

GOMOKU_SIZE = 15
GOMOKU_WIN_RUN = 5
GOMOKU_DIRECTIONS = ((0, 1), (1, 0), (1, 1), (1, -1))

GAMES = ("gomoku", "monopoly")
PLAYERS = ("user", "sakura")

PLAYER_LABEL = {"user": "玩家", "sakura": "夜乃樱"}
PLAYER_MARK = {"user": "○", "sakura": "●"}
DEFAULT_OPPONENT_LABEL = PLAYER_LABEL["sakura"]   # 读不到当前角色名时的兜底


def set_opponent_label(name: str | None) -> str:
    """把对手（座位 `sakura`）的显示名换成当前角色的名字。

    座位键 `"sakura"` 是**数据键**——存档、工具参数（player="sakura"）、网页 API 都用它，不跟着改；
    这里只换显示名。对局记录、结算文案、棋局摘要都读 `PLAYER_LABEL`，所以一处生效。
    传空串/None 表示恢复默认。
    """
    label = (name or "").strip() or DEFAULT_OPPONENT_LABEL
    PLAYER_LABEL["sakura"] = label
    return label


# ---- 大富翁 ----
# 存档版本：**只要改过地图结构或经济数值就必须 +1**（旧存档会连带老数值一起被读回来，
# 表现得像"新版本没生效"）。当前 11：踩到终点立即传回起点（不再停在终点）。
MAP_VERSION = 13
ACTION_RECORD_LIMIT = 120

# 走进中央装置后，在里面探索几间（全是事件格；只有事件，不涉及买卖）
MONOPOLY_DEVICE_ROOMS = 3
# 起始资金与工资：跑批实测"一局里的钱 ÷ 棋盘总价"决定地产系统铺不铺得满。
# 城市图 29 处地产总价 ¥3420；2 人 × ¥1000 起始只有 0.77，买走不到一半、垄断 33%；
# 改成 2 × ¥1400（工资 ¥200→250）后比值 1.07，买走 16.8/29、垄断 56%、租金事件 5.15 笔/局。
MONOPOLY_START_CASH = 1400
MONOPOLY_PASS_START = 250
# 格子棋盘的工资**单独调**（用户 2026-10-02 拍板）：经济评估显示格子棋盘一圈只有 31 格、
# 每步摊到的工资远高于城市图（工资 ¥1719/局），是"85% 对局打满回合上限"的主要剩余症结。
# 250 → 150：钱/地价比值 1.33 → ~1.1，向城市图的健康线（~1.07）靠拢。城市图工资不变。
# 预算口径：150/次 ≈ **每局工资总额 ~1000**（双方合计；一局领 4~9 次、逐局浮动
# 600~1350，平均 ~950-1030，取决于对局长短与终点传送次数——无法逐局钉死在整数）。
MONOPOLY_PASS_START_GRID = 150


def pass_start(map_kind: str) -> int:
    """按地图种类取每圈工资。"""
    return MONOPOLY_PASS_START_GRID if map_kind == "grid" else MONOPOLY_PASS_START
MONOPOLY_TAX = 100
MONOPOLY_BONUS = 150
MONOPOLY_TRAIN_GAME_REWARD = 150  # 游戏格判定成功的归路谢礼；不是普通列车格的票价。
MONOPOLY_MAX_LEVEL = 1
# ---- 金币格：三档现金，等概率（用户 2026-09-21 定：三档先放着，以后再调）----
MONOPOLY_COIN_TIERS = (100, 200, 300)
# ---- 商店格：保留特殊骰子与现金增益，新增四类普通道具均匀盲抽 ----
# 特殊骰子：买一个，可择机使用自定义骰选择点数（持有上限 1）（用完就没）。价格按预期收益定：
# 自选 6 点比裸骰期望多走 2.5 格，¥180 略低于一次工资，买得起也用得上。
# 现金增益：接下来 N 回合收入 ×gain（用户 2026-09-30 定：增益是"翻倍"）。
# 买它要过**掷骰判定**：点数 ≥ 骰子最大点数一半才算买到手；没过就拿减益（支出 ×loss_up），
# 钱照扣——判定本身要有代价，否则"买增益"就稳赚不赔了。
MONOPOLY_SHOP_BUFF_ROUNDS = 3
MONOPOLY_SHOP_BUFF_GAIN = 2.0
MONOPOLY_SHOP_BUFF_LOSS = 0.5
MONOPOLY_SHOP_BUFF_LOSS_UP = 1.5
MONOPOLY_SHOP_CHECK_THRESHOLD = 0.5
MONOPOLY_SHOP_GAIN_PRICE = 200
# 游戏格赢下"异色骰子"事件的概率由牌堆与 WIN_ROLL 共同决定；拿到手 = 一次自选点数
MONOPOLY_SHOP_DICE_PRICE = 180
MONOPOLY_SHOP_DRAW_PRICE = 100
MONOPOLY_ADVENTURE_ROUNDS = 2
MONOPOLY_ADVENTURE_MULTIPLIER = 1.25
SHOP_ITEM_POOL = (
    {"key": "rent_shield", "label": "租金护盾", "note": "下一次实际支付地产租金减半后消耗；免租或抵押地产不消耗"},
    {"key": "upgrade_coupon", "label": "装修券", "note": "下一次升级费用减免50%，最多减免¥60；不跳过等级限制"},
    {"key": "adventure", "label": "冒险契约", "note": "未来2个本人完整普通行动收入及被动支出×1.25；与旧倍率取同方向最大值；主动标价扣款不加价；黑列车暂停"},
    {"key": "lucky_charm", "label": "判定幸运签", "note": "下次小游戏首次判定失败时自动重判一次并接受第二结果；成功不消耗"},
)
SHOP_ITEM_FLAG_DEFAULTS = {"rent_shield": False, "upgrade_coupon": False,
                           "adventure_left": 0, "lucky_charm": False}
# ---- 游戏格：抽一件特别事件，掷骰判输赢；赢了拿好处，输了记成"日常一幕"----
# 掷出 4/5/6 算赢（等概率最直观，也不必让玩家记规则）。
MONOPOLY_GAME_WIN_P = 0.5
MONOPOLY_GAME_WIN_ROLL = 4
# 地价单位：地产价 = 档位 × 这个数（城市图 ¥60/120/180，随机图同）。原来是 100——
# 跑批显示在"每圈一份工资"的节奏下买不起几块地（每局只买得下 5 块、垄断 6%），降到 60 后
# 买得动、铺得开（买到的地 6.5 → 8.9 处，垄断 6% → 22%）。
MONOPOLY_PRICE_UNIT = 60
MONOPOLY_RENT_DIVISOR = 1.5     # 租金 = 现价 ÷ 这个数 × 2^等级。2026-10-01 经济评估后
                                # 由 2 收紧到 1.5（格子棋盘钱太多：97.5% 打满回合上限、
                                # 强制抵押 0.09 次/局，租金是最健康的水泵）
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

# ---- 主题层（主题包见 theme.py）----
# 街区标签/颜色、地产名、事件格名字、事件卡文案都是**主题**的一部分：换个角色来玩时，
# 这些文案应当跟着换成他的世界观（例如碧蓝航线的「港区」主题）。
# 下面这几个名字保留下来，是因为引擎、工具、测试到处在用；它们是"每次访问都去问当前主题"的
# 代理对象，不是快照——切了主题立刻生效，也不会有两份数据打架。
class _ThemeMapping:
    """只读的 dict 代理：访问时才向当前主题取数。"""

    def __init__(self, getter) -> None:
        self._getter = getter

    def _data(self) -> dict:
        return self._getter()

    def __getitem__(self, key):
        return self._data()[key]

    def __contains__(self, key) -> bool:
        return key in self._data()

    def __iter__(self):
        return iter(self._data())

    def __len__(self) -> int:
        return len(self._data())

    def get(self, key, default=None):
        return self._data().get(key, default)

    def keys(self):
        return self._data().keys()

    def items(self):
        return self._data().items()

    def values(self):
        return self._data().values()


class _ThemeSequence:
    """只读的 list 代理：同样每次访问都问当前主题。"""

    def __init__(self, getter) -> None:
        self._getter = getter

    def __getitem__(self, index):
        return self._getter()[index]

    def __len__(self) -> int:
        return len(self._getter())

    def __iter__(self):
        return iter(self._getter())


MONOPOLY_GROUPS = _ThemeMapping(_theme.groups)
MONOPOLY_GROUP_NAMES = _ThemeMapping(_theme.group_names)
MONOPOLY_GROUP_TIERS = _ThemeMapping(_theme.group_tiers)

# 事件卡：title/flavor 供页面与解说使用，effect 是引擎效果。牌组由主题提供，
# 默认主题（第二横滨市）的那 27 张就是原来的牌组；换主题只换文案，效果分布一模一样。
EVENT_CARDS = _ThemeSequence(_theme.event_cards)

SPECIAL_NAMES = _ThemeMapping(_theme.special_names)


def poi_icon_for(name: str) -> str:
    """按地产名找地标插图 key（随机图的"XX别馆"也归到原图）。映射表在主题里。"""
    return _theme.poi_icon_for(name)


def cell_effect_text(cell: dict[str, Any]) -> str:
    """格子本身的效果（写进对局记录，让人一眼看出这一格干了什么）。"""
    kind = cell["type"]
    sub = cell.get("sub")
    if kind == "game":
        table = {"tax": f"（效果：赢了免缴、输了缴 ¥{MONOPOLY_TAX}）",
                 "bonus": f"（效果：赢了拿 ¥{MONOPOLY_BONUS}、输了记成日常一幕）",
                 "cat": f"（效果：赢了 +¥{MONOPOLY_CAT_GAIN}、输了付 ¥{MONOPOLY_CAT_COST} 喂猫）",
                 "monster": f"（效果：赢了躲过、输了损失 ¥{MONOPOLY_MONSTER_TOLL}）",
                 "train": f"（效果：判定成功获得 ¥{MONOPOLY_TRAIN_GAME_REWARD} 归路谢礼，失败无资金变动）"}
        return table.get(str(sub), "（效果：掷骰判输赢）")
    if kind == "coin":
        return "（效果：三档现金 " + "/".join(f"¥{v}" for v in coin_tiers()) + "，等概率）"
    if kind == "shop":
        return (f"（效果：买特殊骰子 ¥{MONOPOLY_SHOP_DICE_PRICE}；买现金增益 ¥{MONOPOLY_SHOP_GAIN_PRICE}"
                f"要掷骰判定，≥最大点数一半成功、失败得减益；道具盲盒 ¥{MONOPOLY_SHOP_DRAW_PRICE}，从未持有类别均匀抽取）")
    if kind == "event":
        return "（效果：NAVI 推送一张事件卡）"
    return ""


def coin_tiers() -> list[int]:
    """金币格的三档金额（主题可覆盖；等概率抽一档）。"""
    try:
        tiers = _theme.coin_tiers()
    except Exception:
        tiers = []
    values = [int(v) for v in tiers if int(v) > 0]
    return values or list(MONOPOLY_COIN_TIERS)


def game_events() -> list[dict[str, Any]]:
    """游戏格能抽到的特别事件（主题给；没给就用内置那五件）。

    末尾**永远**追加一件「异色骰子」：赢了送一次自选点数（用户 2026-09-30 定：
    游戏格里要有概率拿到商店那种特殊骰子）。主题包只写世界观事件，不用复述这条。
    """
    try:
        rows = _theme.game_events()
    except Exception:
        rows = []
    if rows:
        return rows + [_DICE_DROP_EVENT]
    names = _CELL_NAMES
    return [
        {"key": "tax", "name": names.get("tax", "特别会计"),
         "story": "「{name}」翻开了账本——这次要和你算一算。",
         "win": "你抢在结账前找到了漏洞，这次不用缴。",
         "lose": "账目对得严丝合缝，缴税 ¥{amount}。"},
        {"key": "bonus", "name": names.get("bonus", "学园补助"),
         "story": "「{name}」的申请批下来了。",
         "win": "补助到手，+¥{amount}。",
         "lose": "手续差了最后一枚印章，这次没批下来。"},
        {"key": "cat", "name": names.get("cat", "灰色的流浪猫"),
         "story": "「{name}」从纸箱后面探出头。",
         "win": "它蹭了蹭你的手，领你去了它藏东西的地方（+¥{amount}）。",
         "lose": "它叼走猫粮就跑，连头都没回（-¥{amount}）。"},
        {"key": "monster", "name": names.get("monster", "炭化之人"),
         "story": "「{name}」在路口站住了。",
         "win": "你贴着墙根绕了过去，它没有追。",
         "lose": "它抬手一挥，烧掉的钱停不住（-¥{amount}）。"},
        {"key": "train", "name": names.get("train", "黑列车"),
         "story": "「{name}」无声地停在面前，车掌伸出了手。",
         "win": "你帮黑车掌确认了归路，她交给你一份谢礼（+¥{amount}）。",
         "lose": "没有月票，只能补票（-¥{amount}）。"},
        _DICE_DROP_EVENT,
    ]


# 「异色骰子」：游戏格赢下它 = 拿到商店那种特殊骰子（下一次掷骰自选点数）。
# 单独一个常量：主题包替换整份 game_events() 时也要把它接回去。
_DICE_DROP_EVENT = {
    "key": "dice_drop", "name": "异色的骰子",
    "story": "「{name}」的奖品盒里滚出一颗漆色眼熟的六面骰——店主说赢了他就归你。",
    "win": "你赢下了这局小游戏，骰子归你了：可择机使用自定义骰选择点数（持有上限 1）。",
    "lose": "骰子咕噜噜滚进了桌底，怎么摸也摸不着。",
}


def shop_items() -> dict[str, Any]:
    """商店卖的货与"判定失败"那件减益的显示名（价格是引擎的常量，主题只改文案）。

    `draw` 付费均匀抽取尚未持有的普通道具，四种各上限1；活跃契约视为持有。
    `gain` 要过掷骰判定，没过就拿 `loss` 这条减益（保留旧行为与特殊骰子价格）。
    """
    try:
        rows = _theme.shop_items()
    except Exception:
        rows = {}
    return {
        "draw": {"label": "道具盲盒", "price": MONOPOLY_SHOP_DRAW_PRICE,
                 "note": "从未持有的四种道具中均匀抽取一件，每种上限1；全满不能购买",
                 "pool": [dict(item) for item in SHOP_ITEM_POOL]},
        "dice": {"label": rows.get("dice_label") or "特殊骰子",
                 "note": "持有上限 1；可择机点击自定义骰选点，普通掷骰不消耗",
                 "price": MONOPOLY_SHOP_DICE_PRICE},
        "gain": {"label": rows.get("gain_label") or "现金增益",
                 "note": rows.get("gain_note") or f"掷骰判定（≥最大点数一半）通过后：接下来 {MONOPOLY_SHOP_BUFF_ROUNDS} 回合收入 ×{MONOPOLY_SHOP_BUFF_GAIN:g}",
                 "price": MONOPOLY_SHOP_GAIN_PRICE},
        "loss": {"label": rows.get("loss_label") or "现金减益",
                 "note": rows.get("loss_note") or f"判定失败才拿到：接下来 {MONOPOLY_SHOP_BUFF_ROUNDS} 回合支出 ×{MONOPOLY_SHOP_BUFF_LOSS_UP:g}",
                 "price": MONOPOLY_SHOP_GAIN_PRICE},
    }


def shop_check_threshold(dice_sides: int) -> int:
    """买增益的掷骰判定线：点数 ≥ 骰子最大点数的一半（向上取整）算成功。"""
    return max(1, math.ceil(dice_sides * MONOPOLY_SHOP_CHECK_THRESHOLD))


_CELL_NAMES = _ThemeMapping(_theme.cell_names)      # 格子类型 → 页面上的名字（主题提供）

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
        self.draw = False
        self.move_count = 0
        self.last_move: tuple[int, int] | None = None

    def place(self, player: str, row: int, col: int) -> dict[str, Any]:
        if self.winner or self.draw:
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
        elif all(cell for line in self.board for cell in line):
            self.draw = True
        else:
            self.turn = "sakura" if self.turn == "user" else "user"
        result: dict[str, Any] = {
            "player": player,
            "move": [row, col],
            "winner": self.winner,
            "draw": self.draw,
            "next": None if self.winner or self.draw else self.turn,
            "threats": "" if self.winner or self.draw else self.threat_text(),
            "board": self.render(),
        }
        return result

    def ai_move(self, player: str, level: int, aggression: float = 0.45) -> tuple[dict[str, Any], str]:
        """让 AI 为 player 落子，返回 (place结果, 落子理由)。

        aggression 0~1：越大越偏进攻，越小越偏稳健防守——用来让角色性格影响棋风。
        """
        if self.winner or self.draw:
            raise GameError("对局已经结束，请先开始新一局。")
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
        ending = f"\n{self.summary()}" if self.winner or self.draw else ""
        return "\n".join(lines) + f"\n○={PLAYER_LABEL['user']} ●={PLAYER_LABEL['sakura']} {last}" + ending

    def summary(self) -> str:
        if self.winner:
            return f"五子棋：第 {self.move_count} 手，{PLAYER_LABEL[self.winner]}获胜，对局结束。"
        if self.draw:
            return f"五子棋：第 {self.move_count} 手，棋盘已满，和棋，对局结束。"
        return f"五子棋：第 {self.move_count} 手，轮到 {PLAYER_LABEL[self.turn]} 落子。"

    def to_dict(self) -> dict[str, Any]:
        return {
            "kind": self.kind,
            "board": self.board,
            "turn": self.turn,
            "winner": self.winner,
            "draw": self.draw,
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
        game.winner = data.get("winner") if data.get("winner") in PLAYERS else None
        game.draw = False
        # Legacy saves may leave a full board playable. Stones are authoritative;
        # a five-in-a-row on the final move still wins instead of drawing.
        if all(cell for row in game.board for cell in row):
            if game.winner is None:
                for r, row in enumerate(game.board):
                    for c, code in enumerate(row):
                        if game._has_win(r, c, code):
                            game.winner = "user" if code == 1 else "sakura"
                            break
                    if game.winner:
                        break
            game.draw = game.winner is None
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


class MonopolyGame(BlackTrainMixin):
    """图结构大富翁：岔路地图 + 地产经营。

    地图是固定城市/格子布局的"节点 + 邻接边"图；旧随机图仅从存档恢复。
    掷骰后逐步移动，遇岔路（多个前进方向）暂停等待行动方选路；
    经过或落在起点领工资；落在地产格产生决策菜单（买/升/抵/赎/卖/跳）。
    付不起账单强制逐个抵押；破产时资产移交债主；回合上限按总资产（现金 + 地产现价六成）判胜负。
    """

    kind = "monopoly"
    _black_train_error = GameError
    # Also support minimal legacy/test instances constructed with __new__.
    black_train = None
    _effect_draws = 0

    def __init__(self, size: int, dice_sides: int, seed: int, first: str = "user",
                 map_kind: str = "city", theme_id: str | None = None) -> None:
        # 主题决定地名与事件卡文案（街区键/几何不变）。存档里带着主题 id，
        # 读档时先切回那一份，免得用别的角色的地名去解释旧棋盘。
        self.theme = _theme.set_theme(theme_id)
        # size 仅为旧调用方保留签名；固定地图不再接受规模设置。
        self.cols = self.rows = self.size = 0
        self.action_records: list[dict[str, Any]] = []
        self.action_seq = 0
        self.dice_sides = max(2, min(20, int(dice_sides)))
        self.seed = int(seed)
        self._rolls = 0
        self._card_draws = 0
        self._effect_draws = 0
        self.black_train: dict[str, Any] | None = None
        self.black_train_schedule = self._new_black_train_schedule()
        self.cash = {"user": MONOPOLY_START_CASH, "sakura": MONOPOLY_START_CASH}
        self.skip_pending = {"user": False, "sakura": False}
        self.last_step: dict[str, list[int] | None] = {"user": None, "sakura": None}
        self.flags = {p: {"salary_next": False, "rent_free": False, "dice_bonus": 0,
                          "pick_dice": False, "buff": None, "buff_left": 0,
                          **SHOP_ITEM_FLAG_DEFAULTS} for p in PLAYERS}
        self._item_draw_seq = 0
        self._last_item_draw: dict[str, Any] | None = None
        self.turn = first if first in PLAYERS else "user"
        self.winner: str | None = None
        self.move_count = 0
        self.dice_turn = 0  # 骰子回合编号；独立兼容旧存档的重复动作计数。
        self.pending: dict[str, Any] | None = None
        self._walk: dict[str, Any] | None = None
        self._last_walk: dict[str, Any] | None = None
        # 「同格挡路」发生过几次：网页的提示条只在**新的一次**挡路时弹（常驻会挡视野）
        self._block_seq = 0
        # 最近一次"看得见的落点结算"（游戏格事件）与商店掷骰判定：
        # 网页拿它们放过程弹窗，seq 用来认出"新的一次"
        self._landing_seq = 0
        self._last_landing: dict[str, Any] | None = None
        # 现金盈亏的"头顶小字"（金币进账/交租/她买特殊骰子）：一个动作里的多条记进同一批
        self._fx_seq = 0
        self._fx_items: list[dict[str, Any]] | None = None
        self._last_fx: dict[str, Any] | None = None
        self._check_seq = 0
        self._last_check: dict[str, Any] | None = None
        # 新局仅 city/grid；旧配置/调用方的 random 归一为 city。
        self.map_kind = map_kind if map_kind in ("city", "grid") else "city"
        self.main_path: list[int] = []
        self.branches: list[list[int]] = []
        # 传送门：[[a, b], …] 两格互通（双向瞬移）
        self.gates: list[list[int]] = []
        # 装置内部的子图（只有立即结算的格子）：按需生成，格子 id 带偏移，避免与主图撞号
        self.submap: dict[str, Any] | None = None
        self._device_visits = 0
        # 最近一次进塔的经过（网页拿它放"塔内地图"那段上行动画）；`_tower_seq` 每进一次 +1，
        # 网页靠它认出"这是新的一次"，跟同格挡路那条提示条用的是同一个套路。
        self._tower: dict[str, Any] | None = None
        self._tower_seq = 0
        self.cells, self.edges = self._generate_map()
        self.requested_size = self.size
        # 回合上限 = 格数 × 1.5（城市图 57 格 → 86 步）。原来是"格数"（57 步 = 双方各约 28 步
        # ≈ 绕环 0.9 圈），一圈都绕不完：工资只能领一次、租金几乎收不到、垄断凑不齐，
        # 跑批实测"买地的一方胜率只有 6.5%"就是这么来的。1.5 倍后每人约 1.5 圈，经济才转得动。
        self.max_rounds = max(36, round(self.size * MONOPOLY_ROUNDS_PER_CELL))
        self._neighbors = self._build_neighbors()
        self._moves = self._build_moves()
        self._road_preds = self._build_road_preds()
        # 双方都从起点格出发
        self.positions = {"user": self.start_node, "sakura": self.start_node}

    # ---- 地图生成 ---------------------------------------------------

    def _generate_map(self) -> tuple[list[dict[str, Any]], list[list[int]]]:
        """生成固定地图：city=手工城市图（街道网络），grid=格子棋盘。

        城市图来自 city_map.py：外环干道 + 两条主动脉 + 支街，支街之间也相连，
        因此是有多个回路的网络，且每格度数 ≥ 2（无死胡同）。
        """
        if self.map_kind == "grid":
            try:
                from . import grid_map
            except ImportError:
                import grid_map  # type: ignore[no-redef]
            cells, edges, main_path, branches, gates = grid_map.build_grid()
            self.size = len(cells)
            self.cols = grid_map.COLS
            self.rows = grid_map.ROWS
            self.start_node = next(c["id"] for c in cells if c["type"] == "start")
            self.main_path = list(main_path)
            self.branches = [list(chain) for chain in branches]
            self.gates = [list(pair) for pair in gates]
            return cells, edges

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

        raise GameError("新局只支持 city（城市图）或 grid（格子棋盘）。")

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
        if self.map_kind == "grid":
            # 等距格子棋盘：主路是**一条线性路线**（只能沿 main_path 往前走一步，不闭环 ——
            # 终点回起点是**传送门**，不是一格路）；**支路双向**（两端都挂在主路上，
            # 拐进去也能退出来），与主路相连的那条连接边同样双向；
            # 传送门的方向由布局数据给（成对的门写了两条，终点→起点只有一条）。
            main_index = {node: i for i, node in enumerate(ring)}
            for a, b in self.edges:
                if a in main_index and b in main_index:
                    if main_index[b] == main_index[a] + 1:      # 主路上只放行顺行
                        allow(a, b)
                    continue
                allow(a, b)                                     # 沾到支路的一律双向
                allow(b, a)
            for a, b in getattr(self, "gates", []):
                allow(a, b)
            return moves

        if self.map_kind != "city" or len(ring) < 3:
            for a, b in self.edges:
                allow(a, b)
                allow(b, a)
            for a, b in getattr(self, "gates", []):   # 随机图若有传送门也一并放行
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

    def _build_road_preds(self) -> dict[int, set[int]]:
        """每格在"官方路线"上的**来向格**：主路的上一个节点 + 每条支路链的上一个节点。

        走格子时用它把"逆着路线走"的假岔路剔掉（用户 2026-09-30）：站在支路汇入点
        （比如格子棋盘 (12,6)），刚从另一条支路出来，不许再逆着这条支路往回走——
        那不是岔路，是掉头。主路本来就是单向的（_build_moves 只放顺行），所以
        这里主要拦的是支路汇入点上的"退回支路来路"。
        """
        preds: dict[int, set[int]] = {}
        ring = list(self.main_path)
        if len(ring) > 1 and ring[0] == ring[-1]:
            ring = ring[:-1]
        for i in range(1, len(ring)):
            preds.setdefault(ring[i], set()).add(ring[i - 1])
        for chain in self.branches:
            for i in range(1, len(chain)):
                preds.setdefault(chain[i], set()).add(chain[i - 1])
        return preds

    # ---- 随机流 -----------------------------------------------------

    def _dice_rng(self) -> random.Random:
        rng = random.Random(self.seed)
        for _ in range(self._rolls):
            rng.randint(1, self.dice_sides)
        return rng

    def _effect_rng(self) -> random.Random:
        """One independently seeded effect stream per call; count survives save/undo.

        Tests wanting fixed effects should patch this method, not _dice_rng.
        Movement stream and its historic off-by-one sequence remain unchanged.
        """
        self._effect_draws += 1
        return random.Random(f"{self.seed}:effect:{self._effect_draws}")

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
        rent = round(self.current_price(cell) / MONOPOLY_RENT_DIVISOR) * (2 ** cell["level"])
        if cell["group"] and self._has_monopoly(cell["owner"], cell["group"]):
            rent *= 2
        return rent

    def _has_monopoly(self, player: str | None, group: str | None) -> bool:
        """街区垄断（租金翻倍）：持有该街区至少一半地产、且至少 2 处，并且都没抵押。

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

    def _pay(self, player: str, amount: int, beneficiary: str | None, story: list[str],
             *, rent_shield: bool = False) -> dict[str, Any]:
        # Quote passive expenses once; actual debits below are already adjusted.
        if amount > 0:
            amount = self._quote_cash(player, amount, CashCategory.PASSIVE_EXPENSE)
            if rent_shield:
                amount = max(1, round(amount * 0.5))
        if self.cash[player] >= amount:
            self._post_cash(player, -amount, CashCategory.PASSIVE_EXPENSE, already_quoted=True)
            if beneficiary:
                self._receive_payment(beneficiary, amount)
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
            self._post_cash(player, gain, CashCategory.ASSET_FORCED)
            story.append(_theme.story("mortgage_forced", cell=cell["name"], gain=gain))
        paid = min(self.cash[player], amount)
        self._post_cash(player, -paid, CashCategory.PASSIVE_EXPENSE, already_quoted=True)
        if beneficiary:
            self._receive_payment(beneficiary, paid)
        bankrupt = paid < amount
        if bankrupt:
            self._transfer_assets(player, beneficiary)
            self.winner = beneficiary if beneficiary else self._other(player)
            story.append(f"{PLAYER_LABEL[player]}倾尽所有也付不起了，破产！剩余资产移交给{PLAYER_LABEL[beneficiary] if beneficiary else '银行'}。")
        return {"paid": paid, "due": amount, "bankrupt": bankrupt}

    # ---- 行动 -------------------------------------------------------

    def _grant_salary(self, player: str, story: list[str]) -> None:
        if self._black_train_active():
            return
        salary = pass_start(getattr(self, "map_kind", ""))
        doubled = self.flags[player]["salary_next"]
        if doubled:
            salary *= 2
            self.flags[player]["salary_next"] = False
        salary = self._gain(player, salary)
        if doubled:
            story.append(_theme.story("salary_double", salary=salary))
        else:
            story.append(f"经过起点，领工资 +¥{salary}。")

    def _tick_buff(self, player: str, story: list[str]) -> None:
        """Existing ordinary-action completion sites own the clock (skip unchanged)."""
        story.extend(tick_completed_action(self.flags[player], in_train=self._black_train_active()))

    def roll(self, player: str, use_custom_die: bool = False) -> dict[str, Any]:
        if self.winner:
            raise GameError("对局已经结束，请先开始新一局。")
        if self.pending:
            raise GameError(f"{PLAYER_LABEL[self.pending['player']]}还有待决策，先用 boardgame_decide 处理。")
        if self._walk:
            raise GameError(f"{PLAYER_LABEL[self._walk['player']]}还在走格子（剩 {self._walk['steps_left']} 步），先完成移动。")
        if player != self.turn:
            raise GameError(f"还没轮到 {PLAYER_LABEL[player]}。现在是 {PLAYER_LABEL[self.turn]} 行动。")
        # Explicit opt-in only. Reject before touching counters, RNG, FX or actions.
        if use_custom_die:
            if self._black_train_active():
                if not self._black_train_custom_die_available(player):
                    raise GameError("未持有自定义骰，或契约锁定期间不能使用。")
            elif not self.flags[player].get("pick_dice"):
                raise GameError("未持有自定义骰。")
        self._flush_fx()               # 每个玩家可见动作开一批新的头顶小字
        # 一次掷骰（包括罚停）只计一个回合，所有后续决策/塔层共享此编号。
        if not self._black_train_active():
            self.move_count += 1
        self.dice_turn += 1

        if self.skip_pending[player] and not self._black_train_active():
            self.skip_pending[player] = False
            self._pass_turn()
            story = [f"{PLAYER_LABEL[player]}被罚停一回合。"]
            self._maybe_settle(story)
            self._complete_black_train_schedule(story)
            return {
                "player": player,
                "skipped": True,
                "story": story,
                "winner": self.winner,
                "next": None if self.winner else self.turn,
                "board": self.render(),
            }

        # Merely opening the picker reserves no item and draws no random die.
        # The item is consumed only by a validated dice decision below.
        if use_custom_die:
            self.pending = {"player": player, "type": "dice",
                            "options": [str(n) for n in range(1, self.dice_sides + 1)]}
            story = [f"{PLAYER_LABEL[player]}选择使用自定义骰——请选择点数（持有上限 1）。"]
            return {
                "player": player, "dice": None, "path": [self.positions[player]],
                "steps_left": 0, "story": story, "pending": self.pending,
                "winner": self.winner, "next": None, "board": self.render(),
            }
        self._rolls += 1
        dice = self._dice_rng().randint(1, self.dice_sides)
        bonus = 0 if self._black_train_active() else self.flags[player]["dice_bonus"]
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
            # ⚠️ 中途踩到终点（骰子点数越过终点、还要继续走）：和"走完落在终点"同一条规则
            # —— 立刻传回起点、发一次工资，剩余步数从起点接着走。不拦的话下一轮循环会把
            # 终点当岔路：它的邻居是 (16,15) 和门对面的起点，网页就摆出"回起点还是去
            # (16,15)"的假岔路（用户 2026-10-05 实测 (15,16)→(16,16) 就是这样）。
            # 传送这一跳写进 path（网页认出 [终点,起点] 播传送门动画），不消耗步数。
            if walk["steps_left"] > 0 and self.cells[node]["type"] == "end":
                story.append(f"路过「{self.cells[node]['name']}」——这一圈走完了，"
                             f"{PLAYER_LABEL[walk['player']]}走进传送门回到了起点。")
                self._grant_salary(walk["player"], story)
                walk["path"].append(self.start_node)
                self.positions[walk["player"]] = self.start_node
                continue
            neighbors = self._moves[node]
            came_from = walk["path"][-2] if len(walk["path"]) >= 2 else None
            if came_from is None:
                # 行走的第一步也不能原路折返：排除上一回合"最后一步的来路"
                # （例如上回合沿 #39→#38 走完，这回合从 #38 迈第一步时不给 #39）
                last = self.last_step.get(walk["player"])
                if last and last[1] == node:
                    came_from = last[0]
            options = [n for n in neighbors if n != came_from]
            # 岔路不许"逆着路线走"（用户 2026-09-30）：站在 (12,6) 这种支路汇入点，
            # 刚从别的路出来，(12,5) 那个方向是这条支路的来路 —— 选它等于掉头，
            # 不是岔路。主路的来路本来就因为单向规则不在可走列表里，这里把支路的也拦掉。
            # ⚠️ 只作用于 city/grid（路线方向是设计出来的）；随机图本来就四面都能走，不拦。
            preds = self._road_preds.get(node) if self.map_kind in ("city", "grid") else None
            if preds:
                options = [n for n in options if n not in preds]
            if not options:
                # 兜底（极小概率：被同格挡路推回去又面朝来路）：先放"原路折返"，
                # 再放所有邻居 —— 顺序不能反，否则面朝后方的棋子会一路倒着走完整条支路
                options = ([n for n in neighbors if n != came_from]
                           or list(neighbors))
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
        if self._black_train_active():
            self._black_train_landing(walk, story)
            return
        player = walk["player"]
        final = walk["path"][-1]
        # 同格挡路（**只有等距格子棋盘**有这条规则，城市图/随机图照旧允许同格）：
        # 落点已经站着对方的棋子 → 本棋子退后一步，停到它身后那一格。
        # 退后这一步也写进 path（网页的移动动画要看得见这个回退），
        # 实际停留格照常结算，不能让让位回合空过。
        stepped_back = False
        blocked_by: str | None = None
        teleported = False
        if self.map_kind == "grid" and len(walk["path"]) >= 2:
            other = "sakura" if player == "user" else "user"
            if self.positions[other] == final:
                back = walk["path"][-2]
                story.append(
                    f"「{self.cells[final]['name']}」上已经站着 {PLAYER_LABEL[other]} —— "
                    f"本模式同格挡路，{PLAYER_LABEL[player]} 退后一步停在它身后。")
                walk["path"].append(back)
                blocked_by = final
                final = back
                self.positions[player] = back
                stepped_back = True
        # 走到终点：这一圈走完了 —— 领一份工资，**立即从传送门回到起点**（用户 2026-10-01：
        # 踩到终点就该被送回去，不该停在终点、更不该下一步朝终点反方向走）。
        # 路径里补上 [终点, 起点] 这一段：网页认出这是传送门那一跳，会播"缩进门→在起点
        # 长出来"的动画。工资在传送这一刻发（传送不经过 _step_to，不会在起点再发一次）。
        if self.cells[final]["type"] == "end":
            story.append(f"走到「{self.cells[final]['name']}」——这一圈走完了，"
                         f"{PLAYER_LABEL[player]}走进传送门回到了起点。")
            self._grant_salary(player, story)
            walk["path"].append(self.start_node)
            self.positions[player] = self.start_node
            final = self.start_node
            teleported = True                   # 传送落地，没有"来路"可言（见下面的 last_step）
        # `blockedAt` / `blockedBy` 是给网页那条"同格挡路"提示用的：上面那句话只出现在
        # 对局记录里，提示条要能单独讲清"刚才谁被谁挡住了"。
        # `blockSeq` 是"挡路发生了第几次"——提示条改成"触发才弹"之后，网页靠它认出新的一次
        # 挡路（连挡两次时 blocked 一直是 True，只比对 blocked 会漏掉第二次）。
        if stepped_back:
            self._block_seq = int(getattr(self, "_block_seq", 0)) + 1
        self._last_walk = {"player": player, "path": list(walk["path"]), "steps_left": 0,
                           "blocked": stepped_back, "blockedAt": blocked_by,
                           "blockedBy": other if blocked_by is not None else None,
                           "blockSeq": int(getattr(self, "_block_seq", 0))}
        # 记录最后一步（来路 → 落点）：下一回合从落点迈第一步时不许原路折返。
        # ⚠️ 同格挡路的"退后一步"不算走——它是被推回去的，面仍然朝前；这时候照常记
        # last_step 会把"来路"记成身前那格（她的正前方），下一回合岔路兜底就把她
        # 放去身后、一路倒着走完整条支路（2026-09-30 实测 (12,5)→(9,4) 就是这么来的）。
        # 推回之后清空 last_step：下一回合按岔路规则正常只给"顺行方向"。
        if stepped_back or teleported:
            self.last_step[player] = None
        elif len(walk["path"]) >= 2:
            self.last_step[player] = [walk["path"][-2], walk["path"][-1]]
        self._walk = None
        if self.cells[final]["type"] not in ("start", "end"):
            self._resolve_landing(player, final, allow_navi=True, story=story)
        self._maybe_settle(story)
        # 落点决策（买地/升级/逛商店等）期间不换手，等 decide 完成后再移交轮次
        if not self.winner and not self.pending:
            self._tick_buff(player, story)
            self._pass_turn()
            self._complete_black_train_schedule(story)

    def decide(self, player: str, action: str) -> dict[str, Any]:
        if self._black_train_active():
            if self.pending and self.pending.get("kind") == "black_train":
                return self._black_train_decide(player, action)
            # Validate before _flush_fx: illegal preview actions are pure failures.
            if not self.pending or player != self.pending["player"]:
                raise GameError("当前没有属于你的黑列车决策。")
            if self.pending.get("type") not in ("route", "dice") or str(action) not in [str(o) for o in self.pending["options"]]:
                raise GameError("请选择当前提供的黑列车路线或骰子点数。")
        if self.winner:
            raise GameError("对局已经结束，请先开始新一局。")
        if self.pending and self.pending.get("type") == "dice":
            if player != self.pending["player"] or str(action) not in self.pending["options"]:
                raise GameError(f"请由当前行动方选择 1~{self.dice_sides} 的整数点数。")
            if self._black_train_active():
                if not self._black_train_custom_die_available(player):
                    raise GameError("未持有自定义骰，或契约锁定期间不能使用。")
            elif not self.flags[player].get("pick_dice"):
                raise GameError("未持有自定义骰。")
        if (self.pending and self.pending.get("type") == "shop" and action == "buy_dice"
                and self.flags[player].get("pick_dice")):
            raise GameError("自定义骰持有上限为 1，请先使用已有骰子。")
        if self.pending and self.pending.get("type") == "shop" and action == "buy_draw":
            if not self._available_shop_items(player):
                raise GameError("四种道具均已持有，不能重复抽取。")
            if self.cash[player] < MONOPOLY_SHOP_DRAW_PRICE:
                raise GameError("现金不足，无法购买道具盲盒。")
        self._flush_fx()               # 每个玩家可见动作开一批新的头顶小字
        if not self.pending:
            raise GameError("当前没有待决策，正常掷骰就行。")
        if player != self.pending["player"]:
            raise GameError(f"这个决策轮不到 {PLAYER_LABEL[player]}，是 {PLAYER_LABEL[self.pending['player']]} 的。")
        pending_type = self.pending.get("type")

        if pending_type == "tower":
            return self.advance_tower(player, self._tower["seq"], self._tower["at"], action == "tower_finish") if action in self.pending["options"] else self._invalid_tower_action()

        if pending_type == "dice":
            # 特殊骰子：自选点数，选完立刻按这个点数走
            try:
                value = int(action)
            except ValueError as error:
                raise GameError(f"特殊骰子要给出点数（1~{self.dice_sides}）。") from error
            if not 1 <= value <= self.dice_sides:
                raise GameError(f"点数要在 1~{self.dice_sides} 之间，不能选 {value}。")
            if self._black_train_active():
                self._black_train_use_custom_die(player)
            else:
                consume_effect(self.flags[player], "pick_dice", trigger="validated_custom_die_choice")
            self.pending = None
            self._walk = {"player": player, "steps_left": value, "path": [self.positions[player]]}
            story = [f"{PLAYER_LABEL[player]}把特殊骰子定在 {value} 点，从 "
                     f"#{self.positions[player]}「{self.cells[self.positions[player]]['name']}」出发。"]
            self._continue_walk(story)
            return self._walk_result(story, dice=value, player=player)

        if pending_type == "shop":
            items = shop_items()
            if action not in self.pending["options"]:
                raise GameError(f"商店里只能选 {'、'.join(self.pending['options'])}。")
            cell_index = self.pending["cell"]
            self.pending = None
            flags = self.flags[player]
            story: list[str] = []
            if action == "skip":
                story.append(f"{PLAYER_LABEL[player]}在「{self.cells[cell_index]['name']}」里转了一圈，什么都没买。")
            else:
                key = action[len("buy_"):]
                item = items[key]
                self._post_cash(player, -int(item["price"]), CashCategory.ACTIVE_PRICE)
                if key == "draw":
                    pool = self._available_shop_items(player)
                    drawn = pool[self._effect_rng().randrange(len(pool))]
                    if drawn["key"] == "adventure":
                        # 此次完整行动结束会tick；只有未来两次本人行动计入持续时间。
                        acquire_duration(flags, "adventure_left", MONOPOLY_ADVENTURE_ROUNDS)
                    else:
                        flags[drawn["key"]] = True
                    self._item_draw_seq = int(getattr(self, "_item_draw_seq", 0)) + 1
                    self._last_item_draw = {"seq": self._item_draw_seq, "player": player,
                                            **drawn, "price": int(item["price"])}
                    story.append(f"{PLAYER_LABEL[player]}花 ¥{item['price']} 抽到「{drawn['label']}」：{drawn['note']}。")
                    if player == "sakura":
                        self._record_fx(player, f"{drawn['label']} +1")
                elif key == "dice":
                    flags["pick_dice"] = True
                    story.append(f"{PLAYER_LABEL[player]}花 ¥{item['price']} 买下「{item['label']}」——"
                                 f"可择机使用自定义骰选择点数（持有上限 1）。")
                    # 头顶小字只给她（用户回合的购买有商店窗交代，不用再滚字）
                    if player == "sakura":
                        self._record_fx(player, "特殊骰子 +1")
                else:
                    # 增益要过掷骰判定（用户 2026-09-30 定）：点数 ≥ 骰子最大点数的一半
                    # 判定成功拿到增益；没过则钱照扣、拿减益（接下来几回合支出变重）。
                    threshold = shop_check_threshold(self.dice_sides)
                    roll = self._effect_rng().randint(1, self.dice_sides)
                    won = roll >= threshold
                    flags["buff"] = "gain" if won else "loss_up"
                    # 买下的这一回合结束时会立刻 tick 掉一次（_tick_buff），
                    # 所以多给一回合，保证"接下来 3 个我方回合"足数
                    acquire_duration(flags, "buff_left", MONOPOLY_SHOP_BUFF_ROUNDS)
                    self._check_seq = int(getattr(self, "_check_seq", 0)) + 1
                    # 判定的经过单独记一份：网页拿它放"掷骰判定"弹窗（过程要看得见，
                    # 不能只躺在对局记录里）。
                    self._last_check = {
                        "seq": self._check_seq,
                        "player": player,
                        "cell": cell_index,
                        "roll": roll,
                        "threshold": threshold,
                        "success": won,
                        "label": str(item["label"]),
                        "price": int(item["price"]),
                    }
                    if won:
                        story.append(f"{PLAYER_LABEL[player]}花 ¥{item['price']} 买「{item['label']}」并掷骰判定："
                                     f"{roll} 点 ≥ {threshold} 点，判定通过——接下来 {MONOPOLY_SHOP_BUFF_ROUNDS} "
                                     f"回合收入 ×{MONOPOLY_SHOP_BUFF_GAIN:g}。")
                    else:
                        story.append(f"{PLAYER_LABEL[player]}花 ¥{item['price']} 买「{item['label']}」并掷骰判定："
                                     f"{roll} 点 < {threshold} 点，没过——钱照扣，接下来 {MONOPOLY_SHOP_BUFF_ROUNDS} "
                                     f"回合支出 ×{MONOPOLY_SHOP_BUFF_LOSS_UP:g}。")
            self._maybe_settle(story)
            if not self.winner:
                self._tick_buff(player, story)
                self._pass_turn()
                self._complete_black_train_schedule(story)
            return {
                "player": player, "action": action, "cell": cell_index,
                "cash": dict(self.cash), "story": story, "winner": self.winner,
                "diceCheck": dict(getattr(self, "_last_check", None) or {}) or None,
                "itemDraw": copy.deepcopy(self._last_item_draw) if action == "buy_draw" else None,
                "next": None if self.winner else self.turn, "board": self.render(),
            }

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
            self._post_cash(player, -price, CashCategory.ACTIVE_PRICE)
            cell["owner"] = player
            story.append(f"{PLAYER_LABEL[player]}抽到「{cell['name']}」的地契，花 ¥{price} 买下了，"
                         f"基础租金 ¥{price // 2}。")
        elif action == "upgrade":
            cost = self.upgrade_cost(player, cell)
            if self.flags[player].get("upgrade_coupon"):
                consume_effect(self.flags[player], "upgrade_coupon", trigger="successful_upgrade")
                story.append("装修券已抵扣本次升级费用。")
            self._post_cash(player, -cost, CashCategory.ACTIVE_PRICE)
            cell["level"] += 1
            story.append(f"{PLAYER_LABEL[player]}花 ¥{cost} 升级了「{cell['name']}」，租金涨到 ¥{self._rent(cell)}。")
        elif action == "mortgage":
            gain = self._post_cash(player, price // 2, CashCategory.ASSET_VOLUNTARY)
            cell["mortgaged"] = True
            story.append(f"{PLAYER_LABEL[player]}把「{cell['name']}」抵押给了{_theme.bank_name()}，"
                         f"回收 ¥{gain}（抵押期间不收租）。")
        elif action == "redeem":
            cost = price * 3 // 5
            self._post_cash(player, -cost, CashCategory.ACTIVE_PRICE)
            cell["mortgaged"] = False
            story.append(f"{PLAYER_LABEL[player]}花 ¥{cost} 赎回了「{cell['name']}」。")
        elif action == "sell":
            # 卖出也按现价（以前用基础价，涨价后卖掉比赎回还亏）
            value = price // 6 if cell["mortgaged"] else price // 3
            self._post_cash(player, value, CashCategory.ASSET_VOLUNTARY)
            cell["owner"] = None
            cell["level"] = 0
            cell["mortgaged"] = False
            story.append(f"{PLAYER_LABEL[player]}卖掉了「{cell['name']}」，回收 ¥{value}。")

        self.pending = None
        self._maybe_settle(story)
        if not self.winner:
            self._tick_buff(player, story)
            self._pass_turn()
            self._complete_black_train_schedule(story)
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
        pending_type = self.pending.get("type")
        options = self.pending.get("options") or []
        if pending_type == "black_train":
            return f"黑列车·{self.pending['event']['title']}（{'、'.join(options)}）"
        if pending_type == "tower":
            return "塔内流程暂停中，逐层完成后确认离开"
        if pending_type == "route":
            return f"选路线（{self._route_label()}）"
        if pending_type == "dice":
            return f"特殊骰子定点数（{'/'.join(str(o) for o in options)}）"
        if pending_type == "shop":
            return f"逛商店（{'、'.join(options)}）"
        return f"处理 #{self.pending['cell']} 格地产（{'、'.join(options)}）"

    def upgrade_cost(self, player: str, cell: dict[str, Any]) -> int:
        """升级菜单与结算共用的实际标价；装修券减半，最多减60。"""
        cost = self.current_price(cell) // 2
        if self.flags[player].get("upgrade_coupon"):
            cost -= min(60, cost // 2)
        return cost

    def _own_property_options(self, player: str, cell: dict[str, Any]) -> list[str]:
        options: list[str] = []
        price = self.current_price(cell)
        if cell["level"] < MONOPOLY_MAX_LEVEL and not cell["mortgaged"] and self.cash[player] >= self.upgrade_cost(player, cell):
            options.append("upgrade")
        if not cell["mortgaged"]:
            options.append("mortgage")
        elif self.cash[player] >= price * 3 // 5:
            options.append("redeem")
        options.append("sell")
        options.append("skip")
        return options

    def _cell(self, index: int) -> dict[str, Any]:
        """按 id 取格子。装置内部的子图格子 id 带偏移，存在 self.submap 里。"""
        if 0 <= index < len(self.cells):
            return self.cells[index]
        for room in (self.submap or {}).get("cells", []):
            if room["id"] == index:
                return room
        raise GameError(f"格子 {index} 不存在。")

    def _invalid_tower_action(self):
        raise GameError("请使用塔内逐层操作或完成确认。")

    def _close_tower(self) -> None:
        """Close the session without replaying movement, ticking buffs or passing turn."""
        self._tower["active"] = False
        self._tower["deferred"] = []
        self.pending = None
        self.submap = None

    def _tower_pending(self) -> None:
        # A terminal floor must finish inside the action that resolved it: neither
        # seat can be required to obtain permission for another post-game action.
        if self.winner:
            self._close_tower()
            return
        tower = self._tower
        done = tower["at"] >= len(tower["floors"]) - 1
        self.pending = {"type": "tower", "player": tower["player"], "cell": tower["cell"],
                        "seq": tower["seq"], "floor": tower["at"],
                        "options": ["tower_finish" if done else "tower_next"]}

    def _enter_device(self, player: str, cell_index: int, story: list[str]) -> dict[str, Any]:
        """Enter a paused, persisted tower session; no unvisited room is resolved."""
        try:
            from . import grid_map
        except ImportError:
            import grid_map
        if getattr(self, "submap", None) is None:
            self.submap = grid_map.build_submap()
        self._tower_seq = int(getattr(self, "_tower_seq", 0)) + 1
        floors = [{"floor": int(room.get("floor") or i + 1),
                   "floorName": str(room.get("floorName") or ""), "label": room["name"],
                   "type": room["type"], "resolved": False, "result": "pending",
                   "amount": 0, "note": ""} for i, room in enumerate(self.submap["cells"])]
        self._tower = {"seq": self._tower_seq, "name": str(self.submap.get("name") or "中央装置"),
                       "cell": cell_index, "player": player, "floors": floors,
                       "at": -1, "active": True, "deferred": []}
        self._tower_pending()
        story.append(f"「{self._cell(cell_index)['name']}」的传送门把{PLAYER_LABEL[player]}送进{self._tower['name']}，棋盘暂停，等待逐层探索。")
        return {"kind": "device", "rooms": floors}

    def advance_tower(self, player: str, seq: int, expected_floor: int, finish: bool = False) -> dict[str, Any]:
        tower = getattr(self, "_tower", None)
        if not tower or int(seq) != tower["seq"] or player != tower["player"]:
            raise GameError("这次塔内流程已失效，请刷新棋盘。")
        if not tower.get("active"):
            if finish:
                return {"player": player, "story": [], "pending": self.pending, "winner": self.winner,
                        "next": None if self.pending or self.winner else self.turn, "duplicate": True}
            raise GameError("已经离开塔内。")
        if not self.pending or self.pending.get("type") != "tower":
            raise GameError("塔内流程不在等待状态。")
        if not -1 <= expected_floor < len(tower['floors']):
            raise GameError('楼层参数无效。')
        if expected_floor < tower["at"] and not finish:
            return {"player": player, "story": [], "pending": self.pending, "winner": self.winner,
                    "next": None, "duplicate": True}
        if expected_floor != tower["at"]:
            raise GameError("楼层状态已变化，请刷新后再继续。")
        story: list[str] = []
        if finish:
            if tower["at"] < len(tower["floors"]) - 1 and not self.winner:
                raise GameError("请逐层完成塔内事件后再离开。")
            deferred = list(tower.get("deferred") or [])
            self._close_tower()
            story.append(f"从{tower['name']}里出来，回到棋盘。")
            for card in deferred:
                if self.winner:
                    break
                self._apply_card(player, card, "", story)
            self._maybe_settle(story)
            if not self.winner and not self.pending:
                self._tick_buff(player, story)
                self._pass_turn()
                self._complete_black_train_schedule(story)
        else:
            if self.winner or tower["at"] >= len(tower["floors"]) - 1:
                raise GameError("塔内事件已结束，请确认离开。")
            self._flush_fx()
            index = tower["at"] + 1
            tower["at"] = index
            room = self.submap["cells"][index]
            before = self.cash[player]
            result = self._resolve_landing(player, room["id"], True, story)
            row = tower["floors"][index]
            row.update({"resolved": True, "result": result.get("kind") or "nothing",
                        "amount": self.cash[player] - before, "note": " ".join(story)})
            if room["type"] == "game":
                row["landing"] = copy.deepcopy(self._last_landing)
            story.insert(0, "· " + str(row["floor"]) + " 层 · " + row["floorName"])
            self._device_visits += 1
            self._maybe_settle(story)
            self._tower_pending()
        return {"player": player, "story": story, "pending": self.pending, "winner": self.winner,
                "next": None if self.pending or self.winner else self.turn, "board": self.render()}

    def _resolve_landing(self, player: str, cell_index: int, allow_navi: bool, story: list[str]) -> dict[str, Any]:
        cell = self._cell(cell_index)
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
                # 抵押地产即使赎回不起也要弹菜单：这时“卖掉”是唯一能换现金的自救项。
                # 未抵押地产一定有“抵押”选项，本来就不会走到自动歇脚；保留旧兜底条件。
                if cell["mortgaged"] or options != ["sell", "skip"]:
                    self.pending = {"player": player, "type": "land", "cell": cell_index, "options": options,
                                    "upgradeCost": self.upgrade_cost(player, cell)}
                    if cell["mortgaged"]:
                        if "redeem" in options:
                            story.append(f"回到被抵押的「{cell['name']}」。可以赎回（¥{price * 3 // 5}）、卖掉或跳过。")
                        else:
                            story.append(f"回到被抵押的「{cell['name']}」。现金不够赎回，只能卖掉或跳过。")
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
                story.append(_theme.story(
                    "rent_free", owner=PLAYER_LABEL[cell["owner"]], cell=cell["name"],
                    rent=self._rent(cell), payer=PLAYER_LABEL[player],
                    free_card=_theme.flag_card("rent_free")))
                return {"kind": "rent_free"}
            rent = self._rent(cell)
            shield = bool(self.flags[player].get("rent_shield")) and rent > 0
            monopoly_note = "（垄断加成）" if self._has_monopoly(cell["owner"], cell["group"]) else ""
            owner_before = self.cash[cell["owner"]]
            payment = self._pay(player, rent, cell["owner"], story, rent_shield=shield)
            received = self.cash[cell["owner"]] - owner_before
            if shield and payment["paid"] > 0:
                consume_effect(self.flags[player], "rent_shield", trigger="actual_nonexempt_rent_payment")
                story.append("租金护盾生效：本次地产租金减半，护盾已消耗。")
            story.append(f"踩到 {PLAYER_LABEL[cell['owner']]}的「{cell['name']}」{monopoly_note}，交租金 ¥{payment['paid']}（应收 ¥{payment['due']}）。")
            # 头顶小字：交租时**双方**各一条（付款 −、收款 +）
            self._record_fx(player, f"-¥{payment['paid']}")
            self._record_fx(cell["owner"], f"+¥{received}")
            if received != payment["paid"]:
                story.append(f"{PLAYER_LABEL[cell['owner']]}的冒险契约收入加成生效，实际收到 ¥{received}。")
            return {"kind": "rent", "amount": payment["paid"], "bankrupt": payment["bankrupt"]}

        # ---- 旧形式的格子：只有城市图 / 随机图还在用（六种新形式是格子棋盘专属）----
        if kind == "cat":
            if self.cash[player] >= MONOPOLY_CAT_COST:
                # Feeding is an optional, fixed-price purchase, not a boosted
                # penalty; the discovered money is income and uses normal buffs.
                self._post_cash(player, -MONOPOLY_CAT_COST, CashCategory.ACTIVE_PRICE)
                gain = self._gain(player, MONOPOLY_CAT_GAIN)
                story.append(_theme.story("cat", name=_CELL_NAMES.get("cat", "猫"),
                                          cost=MONOPOLY_CAT_COST, gain=gain))
                return {"kind": "cat"}
            story.append(_theme.story("cat_broke", name=_CELL_NAMES.get("cat", "猫")))
            return {"kind": "nothing"}

        if kind == "monster":
            story.append(_theme.story("monster", name=_CELL_NAMES.get("monster", "怪獣"),
                                      opponent=PLAYER_LABEL["sakura"],
                                      free_card=_theme.flag_card("rent_free"),
                                      toll=MONOPOLY_MONSTER_TOLL))
            payment = self._pay(player, MONOPOLY_MONSTER_TOLL, None, story)
            return {"kind": "monster", "amount": payment["paid"], "bankrupt": payment["bankrupt"]}

        if kind == "train":
            story.append(_theme.story("train", name=_CELL_NAMES.get("train", "黑列车"),
                                      toll=MONOPOLY_TRAIN_TOLL))
            payment = self._pay(player, MONOPOLY_TRAIN_TOLL, None, story)
            return {"kind": "train", "amount": payment["paid"], "bankrupt": payment["bankrupt"]}

        if kind == "tax":
            payment = self._pay(player, MONOPOLY_TAX, None, story)
            story.append(_theme.story("tax", name=_CELL_NAMES.get("tax", "税"),
                                      paid=payment["paid"]))
            return {"kind": "tax", "amount": payment["paid"], "bankrupt": payment["bankrupt"]}

        if kind == "bonus":
            amount = self._gain(player, MONOPOLY_BONUS)
            story.append(_theme.story("bonus", name=_CELL_NAMES.get("bonus", "补助"),
                                      amount=amount))
            return {"kind": "bonus", "amount": amount}

        if kind == "coin":
            tiers = coin_tiers()
            amount = tiers[self._effect_rng().randrange(len(tiers))]
            amount = self._gain(player, amount)
            story.append(f"踩到「{cell['name']}」，三档里抽中 ¥{amount}"
                         f"（{'/'.join(f'¥{v}' for v in tiers)} 等概率）。")
            # 头顶小字报进账（用户 2026-10-05：金币格不再弹窗，改棋子头顶滚金额）
            self._record_fx(player, f"+¥{amount}")
            return {"kind": "coin", "amount": amount}

        if kind == "shop":
            return self._enter_shop(player, cell_index, story)

        if kind == "game":
            return self._play_game_cell(player, cell_index, story)

        if kind == "end":
            # 走到终点：领一份工资并送回起点（事件卡"移动"落在这里时走这条；
            # 正常掷骰的落点在 _finish_walk 里处理并补上 [终点,起点] 的动画路径）
            story.append(f"走到「{cell['name']}」——这一圈走完了，"
                         f"{PLAYER_LABEL[player]}走进传送门回到了起点。")
            self._grant_salary(player, story)
            self.positions[player] = self.start_node
            return {"kind": "end"}

        if kind in ("device", "tower"):
            # "tower" = 那座传送点格子（美术上是传送门、没有烤图标）；两者都是"进塔"
            return self._enter_device(player, cell_index, story)

        if kind in ("event", "navi"):
            if not allow_navi:
                story.append(f"{_theme.navi_log_prefix()}：这次没有推送。")
                return {"kind": "nothing"}
            card = self._draw_card()
            flavor = card["flavor"].replace("你", PLAYER_LABEL[player])
            # 把实际效果写进同一行：对局记录和网页弹出的卡面都要看得出"这张卡到底做了什么"
            story.append(f"{_theme.navi_log_prefix()}【{card['title']}】{flavor}（{card_effect_text(card)}）")
            return self._apply_card(player, card, flavor, story)

        return {"kind": "nothing"}

    # ---- 六种格子形式的结算 -----------------------------------------

    def _adventure_active(self, player: str) -> bool:
        return adventure_active(self.flags[player], in_train=self._black_train_active())

    def _quote_cash(self, player: str, amount: int, category: CashCategory) -> int:
        return cash_amount(amount, category, self.flags[player],
                           adventure_active=self._adventure_active(player),
                           gain_rate=MONOPOLY_SHOP_BUFF_GAIN, loss_rate=MONOPOLY_SHOP_BUFF_LOSS,
                           loss_up_rate=MONOPOLY_SHOP_BUFF_LOSS_UP,
                           adventure_rate=MONOPOLY_ADVENTURE_MULTIPLIER)

    def _post_cash(self, player: str, amount: int, category: CashCategory, *,
                   already_quoted: bool = False) -> int:
        """Post one classified signed change; solvency/options remain caller-owned.

        Only passive payments use already_quoted: their due amount was quoted
        before mortgage liquidation, shielding and partial-payment handling.
        Black-train settlement may use ADVENTURE_SETTLEMENT without income buffs.
        """
        category = CashCategory(category)
        if already_quoted and category != CashCategory.PASSIVE_EXPENSE:
            raise ValueError("only settled passive payments may bypass quoting")
        actual = int(amount) if already_quoted else self._quote_cash(player, amount, category)
        self.cash[player] += actual
        return actual

    def _receive_payment(self, player: str, amount: int) -> int:
        return self._post_cash(player, amount, CashCategory.RENT_RECEIPT)

    def _gain(self, player: str, amount: int) -> int:
        """Ordinary earned income; asset liquidation is explicitly classified separately."""
        return self._post_cash(player, amount, CashCategory.INCOME)

    def _available_shop_items(self, player: str) -> list[dict[str, Any]]:
        flags = self.flags[player]
        return [dict(item) for item in SHOP_ITEM_POOL
                if not (int(flags.get("adventure_left") or 0) > 0 if item["key"] == "adventure"
                        else flags.get(item["key"]))]

    def _enter_shop(self, player: str, cell_index: int, story: list[str]) -> dict[str, Any]:
        """普通商店：特殊骰子、旧现金增益，以及均匀抽取未持有道具的盲盒。

        增益不是"买了就有"：付完钱要过**掷骰判定**（点数 ≥ 骰子最大点数的一半），
        没过就拿减益（接下来几回合支出 ×1.5）。判定过程由网页弹窗演示。
        """
        items = shop_items()
        flags = self.flags[player]
        options: list[str] = []
        for key in ("dice", "gain", "draw"):
            if self.cash[player] < int(items[key]["price"]):
                continue
            if key == "dice" and flags.get("pick_dice"):
                continue
            if key == "draw" and not self._available_shop_items(player):
                continue
            options.append(f"buy_{key}")
        options.append("skip")
        self.pending = {"player": player, "type": "shop", "cell": cell_index,
                        "options": options,
                        # 弹窗要用：每件货的显示名/说明/价格随待决策一起下发
                        "items": {k: copy.deepcopy(items[k]) for k in ("dice", "gain", "draw")},
                         "drawPool": self._available_shop_items(player)}
        menu = "、".join(f"{items[k]['label']} ¥{items[k]['price']}" for k in ("dice", "gain", "draw"))
        story.append(f"走进「{self._cell(cell_index)['name']}」：{menu}。"
                     f"当前现金 ¥{self.cash[player]}。")
        if flags.get("pick_dice"):
            story.append("（自定义骰持有上限 1，已有一颗，不能重复购买。）")
        return {"kind": "offer_shop", "cell": cell_index, "options": options, "items": items}

    def _play_game_cell(self, player: str, cell_index: int, story: list[str]) -> dict[str, Any]:
        """游戏格：抽一件特别事件，掷骰判输赢。输了就是"日常一幕"（什么都不发生）。

        「异色骰子」这件赢了送自定义骰子（持有上限1，可择机使用）。整个经过（抽到什么、
        掷出几点、结果）会记进 `_last_landing`，网页拿它放游戏弹窗——判定过程要看得见，
        不能只躺在对局记录里（用户 2026-09-30 定）。
        """
        cell = self._cell(cell_index)
        events = game_events()
        event = events[self._effect_rng().randrange(len(events))] if events else {"key": "rest"}
        # A board's decorative sub-key may be rest/navi, not a playable event.
        # In that case keep the drawn event's effect AND its presentation together;
        # otherwise a displayed bonus win used to settle as unknown/rest with ¥0.
        fixed_sub = str(cell.get("sub") or "")
        event = next((e for e in events if str(e.get("key")) == fixed_sub), event)
        sub = str(event.get("key") or "rest")
        name = str(event.get("name") or cell["name"])
        story.append(f"「{cell['name']}」——抽到的是「{name}」。"
                     + str(event.get("story") or "").format(name=name).replace("你", PLAYER_LABEL[player]))
        roll = self._effect_rng().randint(1, 6)
        rolls = [roll]
        if roll < MONOPOLY_GAME_WIN_ROLL and self.flags[player].get("lucky_charm"):
            consume_effect(self.flags[player], "lucky_charm", trigger="first_failed_game_check")
            roll = self._effect_rng().randint(1, 6)
            rolls.append(roll)
            story.append(f"判定幸运签消耗：首次 {rolls[0]} 点失败，自动重判 {roll} 点，接受第二结果。")
        won = roll >= MONOPOLY_GAME_WIN_ROLL
        if not won:
            story.append(f"掷出 {roll} 点，没能过关——记成日常一幕，什么也没发生。")
            result = {"kind": "game_lose", "event": sub, "name": name, "roll": roll, "amount": 0}
        else:
            # 主题文案是"你"视角写的；弹窗/记录里要叫角色名（用户 2026-10-05：
            # "事件格弹出的人称仍是你，应改为 Sakura 角色的名称"）
            text = str(event.get("win") or "这次你赢了。").replace("你", PLAYER_LABEL[player])
            story.append(f"掷出 {roll} 点，赢了！")
            result = self._apply_game_reward(player, sub, name, text, roll, story)
        result.update(rolls=rolls, rerolled=len(rolls) > 1)
        # 记给网页弹窗：此时 story 的最后一段就是"结果那行"（金额/奖励都说清了）
        self._record_landing(player, cell_index, {
            "event": sub,
            "name": name,
            "roll": roll,
            "won": won,
            "rolls": rolls,
            "rerolled": len(rolls) > 1,
            "amount": int(result.get("amount") or 0),
            "reward": story[-1] if story else "",
        })
        return result

    def _record_landing(self, player: str, cell_index: int, info: dict[str, Any]) -> None:
        """记下最近一次"看得见的落点结算"（游戏格事件等），网页靠 seq 认出新的一次。"""
        self._landing_seq = int(getattr(self, "_landing_seq", 0)) + 1
        payload = dict(info)
        if getattr(self, "_tower", None) and self._tower.get("active"):
            payload["towerSeq"] = self._tower["seq"]
            payload["towerFloor"] = self._tower["at"]
        payload["seq"] = self._landing_seq
        payload["player"] = player
        payload["cell"] = cell_index
        payload["cellName"] = str(self._cell(cell_index).get("name") or "")
        self._last_landing = payload

    def _record_fx(self, player: str, text: str) -> None:
        """记一条现金盈亏的"头顶小字"（棋子头上滚一下淡出的金色数字）。

        同一个动作里的多条（交租时双方各一条）记进同一批、共用一个 seq；
        每个玩家可见动作（roll/decide）开头 _flush_fx() 开新的一批。
        """
        if self._fx_items is None:
            self._fx_seq += 1
            self._fx_items = []
        self._fx_items.append({"player": player, "text": text})
        self._last_fx = {"seq": self._fx_seq, "items": list(self._fx_items)}

    def _flush_fx(self) -> None:
        self._fx_items = None

    def _apply_game_reward(self, player: str, sub: str, name: str,
                           text: str, roll: int, story: list[str]) -> dict[str, Any]:
        """游戏格赢了之后发奖：按事件各自的赢法结算（金额与旧版特殊格一致）。"""
        if sub == "dice_drop":
            # Boolean inventory enforces a single die; no stacked replacement reward.
            if self.flags[player].get("pick_dice"):
                story.append("自定义骰持有上限 1；已有一颗，本次不再获得。")
            else:
                self.flags[player]["pick_dice"] = True
                story.append(text.format(name=name, amount=0))
            return {"kind": "game_win", "event": sub, "name": name, "roll": roll, "amount": 0}
        if sub == "tax":
            story.append(f"（赢了免缴 ¥{MONOPOLY_TAX}。）")
            return {"kind": "game_win", "event": sub, "name": name, "roll": roll, "amount": 0}
        if sub == "bonus":
            amount = self._gain(player, MONOPOLY_BONUS)
            story.append(text.format(name=name, amount=amount))
            return {"kind": "game_win", "event": sub, "name": name, "roll": roll, "amount": amount}
        if sub == "cat":
            amount = self._gain(player, MONOPOLY_CAT_GAIN)
            story.append(text.format(name=name, amount=amount))
            return {"kind": "game_win", "event": sub, "name": name, "roll": roll, "amount": amount}
        if sub == "monster":
            story.append(f"{text.format(name=name, amount=MONOPOLY_MONSTER_TOLL)}"
                         f"（省下 ¥{MONOPOLY_MONSTER_TOLL}）")
            return {"kind": "game_win", "event": sub, "name": name, "roll": roll, "amount": 0}
        if sub == "train":
            amount = self._gain(player, MONOPOLY_TRAIN_GAME_REWARD)
            # Always describe the actual payment, including for older theme packs
            # whose win text still claims a waived fare rather than a cash prize.
            story.append(f"{PLAYER_LABEL[player]}帮黑车掌确认了归路，她交给一份谢礼（+¥{amount}）。")
            return {"kind": "game_win", "event": sub, "name": name, "roll": roll, "amount": amount}
        story.append(text.format(name=name, amount=0))
        return {"kind": "game_win", "event": sub, "name": name, "roll": roll, "amount": 0}

    def _apply_card(self, player: str, card: dict[str, Any], flavor: str, story: list[str]) -> dict[str, Any]:
        effect = card["effect"]
        kind = effect["type"]
        tower = getattr(self, "_tower", None)
        if tower and tower.get("active") and tower["player"] == player and kind in ("move", "goto_start"):
            tower["deferred"].append(copy.deepcopy(card))
            story.append("这张卡的棋盘移动效果将在出塔后执行。")
            return {"kind": "navi_card", "card": card["title"], "deferred": True}
        if kind == "cash":
            amount = effect["amount"]
            if amount >= 0:
                self._gain(player, amount)
            else:
                payment = self._pay(player, -amount, None, story)
                return {"kind": "navi_card", "card": card["title"], "bankrupt": payment["bankrupt"]}
            return {"kind": "navi_card", "card": card["title"]}
        if kind == "move":
            delta = int(effect["delta"])
            steps = abs(delta)
            forward = delta >= 0
            node = self.positions[player]
            movement_path = [node]  # Actual displacement, never inferred from facing.
            last = self.last_step.get(player)
            came_from = last[0] if last and last[1] == node else None
            preds = self._road_preds if self.map_kind in ("city", "grid") else {}
            previous_walk = getattr(self, "_last_walk", None) or {}
            history = (list(previous_walk.get("path") or [])
                       if previous_walk.get("player") == player else [])
            if not history or history[-1] != node:
                history = []
            # Include the final boundary check: end -> start consumes no step,
            # just as in ordinary walking, even when the last step reaches end.
            for step in range(steps + 1):
                if forward and steps and self.cells[node]["type"] == "end":
                    story.append(f"走进「{self.cells[node]['name']}」的传送门，回到起点。")
                    self._grant_salary(player, story)
                    node, came_from = self.start_node, None
                    movement_path.append(node)
                if step == steps:
                    break
                if forward:
                    options = [n for n in self._moves[node]
                               if n != came_from and n not in preds.get(node, ())]
                    if not options:
                        options = ([n for n in self._moves[node] if n != came_from]
                                   or list(self._moves[node]))
                else:
                    # Reverse the official road, not the outgoing move list.
                    # Grid start is a hard boundary: never reverse its portal
                    # into end and accidentally collect another salary.
                    if self.map_kind == "grid" and node == self.start_node:
                        break
                    options = sorted(preds.get(node, ()))
                    # Grid branch chains omit their main-road connectors. Infer
                    # missing entrance links from incoming legal forward edges.
                    incoming = [src for src, destinations in self._moves.items()
                                if node in destinations and node not in preds.get(src, ())]
                    if not options:
                        options = incoming
                    if self.map_kind == "city" and node == self.start_node and self.main_path:
                        ring = self.main_path[:-1] if self.main_path[-1] == self.start_node else self.main_path
                        options = [ring[-1]] + [n for n in options if n != ring[-1]]
                    if self.map_kind not in ("city", "grid"):
                        options = [n for n in self._moves[node] if n != came_from]
                    # At merges retrace the actual walk when it is known; do not
                    # invent a reverse traversal through a teleport or shortcut.
                    prior = history[-2] if len(history) >= 2 else came_from
                    if prior in incoming or prior in options:
                        options = [prior] + [n for n in options if n != prior]
                    if len(history) >= 2 and options and options[0] == history[-2]:
                        history.pop()
                    else:
                        history = []
                if not options:
                    break
                came_from, node = node, options[0]
                movement_path.append(node)
                if forward and self.cells[node]["type"] == "start":
                    self._grant_salary(player, story)
            self.positions[player] = node
            if steps:
                # Never write the actual backwards arrival as facing direction:
                # it would forbid moving forward on the following normal roll.
                self.last_step[player] = [came_from, node] if forward and came_from is not None else None
                if not forward and self.map_kind == "grid" and node != self.start_node and not preds.get(node):
                    entrances = [src for src, destinations in self._moves.items()
                                 if node in destinations and node not in preds.get(src, ())]
                    if len(entrances) == 1:
                        # A branch entrance is absent from _road_preds; retain
                        # its forward-facing approach instead of exposing a U-turn.
                        self.last_step[player] = [entrances[0], node]
            if (previous_walk.get("player") == player and previous_walk.get("path")
                    and previous_walk["path"][-1] == movement_path[0]):
                previous_walk["path"].extend(movement_path[1:])
            story.append(f"移动到 #{node}「{self.cells[node]['name']}」。")
            chain = self._resolve_landing(player, node, allow_navi=False, story=story)
            return {"kind": "navi_card", "card": card["title"], "chain": chain,
                    "path": movement_path}
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
            self.last_step[player] = None  # Teleportation has no arrival direction.
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
                f"{PLAYER_LABEL['user']} {describe('user', user_score)} 对 "
                f"{PLAYER_LABEL['sakura']} {describe('sakura', sakura_score)}，"
                f"{PLAYER_LABEL[winner]}获胜！")

    def _maybe_settle(self, story: list[str]) -> None:
        if not self._black_train_active() and self.winner is None and self.pending is None and not self._walk and self.move_count >= self.max_rounds:
            story.append(self._settle())

    @staticmethod
    def _new_black_train_schedule() -> dict[str, Any]:
        return {"announce_at": 22, "enter_at": 26, "announced": False,
                "entered": False, "last_completed": 0}

    def _complete_black_train_schedule(self, story: list[str]) -> None:
        """Only ordinary action-completion sites call this, after handing off turn.

        move_count counts rolls, not sub-decisions; a pending choice must finish
        before its number can be considered a completed ordinary action.
        """
        if (self.winner is not None or self._black_train_active() or self.pending is not None
                or self._walk is not None or getattr(self, "submap", None) is not None
                or (getattr(self, "_tower", None) and self._tower.get("active"))):
            return
        schedule = getattr(self, "black_train_schedule", None)
        if schedule is None:
            schedule = self._new_black_train_schedule()
            self.black_train_schedule = schedule
        # Existing preview/legacy journeys also constitute the once-per-game visit.
        if self.black_train:
            schedule["entered"] = True
        if self.move_count <= schedule["last_completed"]:
            return
        schedule["last_completed"] = self.move_count
        if schedule["entered"]:
            return
        if self.move_count >= schedule["announce_at"] and not schedule["announced"]:
            schedule["announced"] = True
            remaining = max(0, schedule["enter_at"] - self.move_count)
            if remaining:
                story.append(f"黑列车预告：第{self.move_count}回合已结束；再过{remaining}回合，"
                             f"第{schedule['enter_at']}回合结束后，双方将进入黑列车探险。")
            else:
                story.append(f"黑列车预告：第{self.move_count}回合已结束，双方即将进入黑列车探险。")
        if self.move_count >= schedule["enter_at"]:
            # Do not mark used until the safe-point start succeeds.
            result = self.start_black_train()
            schedule["entered"] = True
            story.append(f"第{self.move_count}回合已结束，黑列车自动到站，双方开始本局唯一一次探险。")
            story.extend(result["story"])

    def _pass_turn(self) -> None:
        self.turn = self._other(self.turn)

    def render(self) -> str:
        lines = [self._black_train_label()] if self.black_train else []
        for player in PLAYERS:
            node = self.positions[player]
            moves = "、".join(f"#{n}「{self.cells[n]['name']}」" for n in self._moves[node])
            lines.append(f"{PLAYER_LABEL[player]}在 #{node}「{self.cells[node]['name']}」（现金 ¥{self.cash[player]}，可行方向：{moves}）")
        owned = []
        for cell in self.cells:
            if cell["type"] != "property" or cell["owner"] is None:
                continue
            if cell["mortgaged"]:
                state, rent = "抵押中（不收租）", ""
            else:
                state = "已升级 " if cell["level"] else ""
                rent = f"租¥{self._rent(cell)}"
            owned.append(f"#{cell['id']} {cell['name']}[{MONOPOLY_GROUPS[cell['group']]['label']}]（{PLAYER_LABEL[cell['owner']]}·{state}{rent}）")
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
            (self._black_train_label() + "\n" if self.black_train else "") +
            f"大富翁：第 {self.move_count}/{self.max_rounds} 回合，轮到 {PLAYER_LABEL[self.turn]}{walk_note}。"
            f"{PLAYER_LABEL['user']}现金 ¥{self.cash['user']}，{PLAYER_LABEL['sakura']}现金 ¥{self.cash['sakura']}。"
        )

    def to_dict(self) -> dict[str, Any]:
        return {
            "kind": self.kind,
            # 存档版本：**改过地图结构或经济数值就要 +1**，否则旧存档会把老数值（起始资金、
            # 地价、玩家现金）整个带进新版本，看起来像"新版本没生效"。
            # 4 → 5：起始资金 1000→1400、工资 200→250、地价单位 100→60、垄断门槛与判定规则都变了。
            "map_version": MAP_VERSION,
            "action_records": copy.deepcopy(getattr(self, "action_records", [])[-ACTION_RECORD_LIMIT:]),
            "action_seq": getattr(self, "action_seq", 0),
            "dice_turn": self.dice_turn,
            "tower_session": copy.deepcopy(getattr(self, "_tower", None)),
            "tower_seq": getattr(self, "_tower_seq", 0),
            "device_visits": getattr(self, "_device_visits", 0),
            "cols": self.cols,
            "rows": self.rows,
            "size": self.size,
            "dice_sides": self.dice_sides,
            "seed": self.seed,
            "rolls": self._rolls,
            "card_draws": self._card_draws,
            "effect_draws": self._effect_draws,
            "item_draw_seq": int(getattr(self, "_item_draw_seq", 0)),
            "lastItemDraw": copy.deepcopy(getattr(self, "_last_item_draw", None)),
            "black_train": copy.deepcopy(self.black_train),
            "black_train_schedule": copy.deepcopy(getattr(self, "black_train_schedule", self._new_black_train_schedule())),
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
            "pending": copy.deepcopy(self.pending),
            "walk": copy.deepcopy(self._walk),
            # 上一步走过的路（含"被同格挡路退回"的标记）：网页那条同格提示条读它
            "last_walk": dict(self._last_walk) if self._last_walk else None,
            "cells": self.cells,
            "edges": self.edges,
            "start_node": self.start_node,
            "map_kind": getattr(self, "map_kind", "random"),
            # 地名与事件卡来自哪个主题包（换主题不改几何与经济，只换文案；读档要切回那一份）
            "theme": getattr(self, "theme", _theme.DEFAULT_THEME_ID),
            "main_path": list(getattr(self, "main_path", [])),
            "branches": [list(c) for c in getattr(self, "branches", [])],
            "gates": [list(g) for g in getattr(self, "gates", [])],
        }

    def _migrate_action_turns(self) -> None:
        """旧日志按 roll 边界分组，仅修改恢复后的内存，不写存档。"""
        rows = self.action_records
        if not rows or all("turn" in row for row in rows):
            return
        groups: list[list[dict[str, Any]]] = []
        for row in rows:
            if not groups or row.get("action") == "roll" or row.get("actor") != groups[-1][-1].get("actor"):
                groups.append([])
            groups[-1].append(row)
        # 无完整历史时只能以引擎当前编号为锚点；绝不用动作 id 充当回合号。
        last_turn = max(self.dice_turn, len(groups))
        if rows[0].get("id") == 1 and rows[0].get("action") == "roll":
            last_turn = len(groups)
        self.dice_turn = last_turn
        for index, group in enumerate(groups):
            number = last_turn - len(groups) + index + 1
            for row in group:
                row.setdefault("turn", number)
                row.setdefault("turn_complete", False)
            group[-1]["turn_complete"] = index < len(groups) - 1 or bool(
                self.winner or (not self.pending and not self._walk))

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "MonopolyGame":
        if data.get("map_version") != MAP_VERSION:
            raise GameError("旧版存档不兼容。")
        game = cls.__new__(cls)
        game.kind = "monopoly"
        game.action_records = copy.deepcopy((data.get("action_records") or [])[-ACTION_RECORD_LIMIT:])
        game.action_seq = max(int(data.get("action_seq") or 0),
                              max((int(r.get("id") or 0) for r in game.action_records), default=0))
        # 先把主题切回存档那一份：格子上的地名已经烙在存档里，渲染时用到的街区标签、
        # 事件卡文案、NAVI 行前缀都要与它一致（找不到那份主题就退回默认主题）
        game.theme = _theme.set_theme(data.get("theme") or _theme.DEFAULT_THEME_ID)
        game.cols = int(data["cols"])
        game.rows = int(data["rows"])
        game.size = int(data["size"])
        game.requested_size = game.size
        game.dice_sides = int(data["dice_sides"])
        game.seed = int(data["seed"])
        game._rolls = int(data["rolls"])
        game._card_draws = int(data["card_draws"])
        game._effect_draws = int(data.get("effect_draws", 0))
        game.black_train = copy.deepcopy(data.get("black_train"))
        game.black_train_schedule = game._new_black_train_schedule()
        game.black_train_schedule.update(copy.deepcopy(data.get("black_train_schedule") or {}))
        if (game.black_train_schedule["announce_at"], game.black_train_schedule["enter_at"]) == (42, 46):
            # Migrate only the former default timetable, not custom schedules.
            # Keep all progress/once flags; loading never emits a late announcement
            # or enters the train until the next safely completed ordinary action.
            game.black_train_schedule.update(announce_at=22, enter_at=26)
        if game.black_train and not data.get("black_train_schedule"):
            game.black_train_schedule["entered"] = True
        # Loading is read-only with respect to progress: overdue legacy games
        # enter at their next completed ordinary action, never inside from_dict.
        game.cash = {p: int(data["cash"][p]) for p in PLAYERS}
        game.positions = {p: int(data["positions"][p]) for p in PLAYERS}
        game.skip_pending = {p: bool(data["skip_pending"][p]) for p in PLAYERS}
        # 旧存档没有 last_step 字段，容忍缺失（None = 首步不限制来路）
        raw_last = data.get("last_step") or {}
        game.last_step = {p: (list(raw_last[p]) if raw_last.get(p) else None) for p in PLAYERS}
        game.flags = {p: {**SHOP_ITEM_FLAG_DEFAULTS, **dict(data["flags"][p])} for p in PLAYERS}
        # 黑列车保存的是完整普通flags；旧快照也补默认值，返程恢复不会丢字段。
        if game.black_train:
            for saved_flags in game.black_train.get("saved", {}).get("flags", {}).values():
                for key, value in SHOP_ITEM_FLAG_DEFAULTS.items():
                    saved_flags.setdefault(key, value)
        game._last_item_draw = copy.deepcopy(data.get("lastItemDraw"))
        game._item_draw_seq = max(int(data.get("item_draw_seq") or 0),
                                  int((game._last_item_draw or {}).get("seq") or 0))
        game.turn = data["turn"]
        game.winner = data["winner"]
        game.move_count = int(data["move_count"])
        game.dice_turn = int(data.get("dice_turn", max(game.move_count, game._rolls)))
        game.max_rounds = int(data["max_rounds"])
        game.pending = copy.deepcopy(data["pending"])
        game._walk = copy.deepcopy(data["walk"])
        game.cells = data["cells"]
        game.edges = data["edges"]
        game.map_kind = data.get("map_kind") or "random"
        game.main_path = [int(i) for i in (data.get("main_path") or [])]
        game.branches = [[int(i) for i in chain] for chain in (data.get("branches") or [])]
        game.gates = [[int(i) for i in pair] for pair in (data.get("gates") or [])]
        game.start_node = int(data.get("start_node")
                             if data.get("start_node") is not None
                             else next(c["id"] for c in game.cells if c["type"] == "start"))
        game._last_walk = None
        game._block_seq = 0
        # 读档不进塔的动画：塔内经过是"这一次发生的事"，重启/读档后不该再放一遍
        saved_tower = data.get("tower_session")
        game._tower = copy.deepcopy(saved_tower) if saved_tower and saved_tower.get("active") else None
        game._tower_seq = int(data.get("tower_seq") or 0)
        game._device_visits = int(data.get("device_visits") or 0)
        game.submap = None
        if game._tower and game.winner:
            # Old versions persisted a finish prompt after tower bankruptcy.
            # Normalize only the restored session; never replay its deferred cards.
            game._close_tower()
        elif game._tower:
            try:
                from . import grid_map
            except ImportError:
                import grid_map
            game.submap = grid_map.build_submap()
        # 同理：弹窗用的"最近一次结算/判定"也不跨读档重放
        game._landing_seq = 0
        game._last_landing = None
        game._check_seq = 0
        game._last_check = None
        game._fx_seq = 0
        game._fx_items = None
        game._last_fx = None
        game._neighbors = game._build_neighbors()
        game._moves = game._build_moves()
        game._road_preds = game._build_road_preds()
        game._migrate_action_turns()
        return game


def create_game(kind: str, first: str, dice_sides: int, map_size: int, event_ratio: float = 0.0,
                map_kind: str = "random", theme_id: str | None = None) -> GomokuGame | MonopolyGame:
    if kind == "gomoku":
        return GomokuGame(first=first or "dice")
    if first not in PLAYERS:
        if first == "dice":
            first = "user"
        else:
            raise GameError("先手只能是 user 或 sakura。")
    if kind == "monopoly":
        return MonopolyGame(size=map_size, dice_sides=dice_sides, seed=random.randint(1, 10**9),
                            first=first, map_kind=map_kind, theme_id=theme_id)
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
        names = _theme.cell_names()
        specials = "、".join(filter(None, (
            f"税格扣钱（{names.get('tax', '')}）" if names.get("tax") else "税格扣钱",
            f"赏格加钱（{names.get('bonus', '')}）" if names.get("bonus") else "赏格加钱",
            f"{names.get('cat', '猫')}格喂猫" if names.get("cat") else "猫格喂猫",
            f"{names.get('monster', '怪獣')}格遇袭" if names.get("monster") else "怪獣格遇袭",
            f"{names.get('train', '黑列车')}格交车票钱" if names.get("train") else "黑列车格交车票钱",
            f"{names.get('navi', '问号')}格推送事件卡" if names.get("navi") else "问号格由 NAVI 推送事件卡",
        )))
        if getattr(game, "map_kind", "") == "grid":
            tower_name = _theme.cell_names().get("device") or "中央装置"
            move_line = (
                "规则：在这张**格子棋盘**上轮流掷骰，掷出几点就走几格。"
                "**主路是一条单向路线**（起点 → 终点），两条支路可以拐进去；"
                "在岔路上只能往前选，不许逆着路线掉头往回走。"
                "走到终点就算走完一圈、领一份工资，再由传送门回到起点。"
                f"支路上那座**传送点**是{tower_name}的入口：踩上去会被送进塔里，"
                "沿着回廊一层层往上走，每层一间房（事件卡 / 金币 / 特别事件，都是当场结算），"
                f"走完从{tower_name}里出来、回到原来那一格继续这一回合。"
                "走到有多个可行方向的格子会暂停，由当前行动方选择走哪一格，"
                "选完继续走完剩余步数。经过或落在起点领工资。"
            )
        else:
            move_line = (
                f"规则：在「{_theme.current().get('name') or '城市地图'}」的街道上轮流掷骰，掷出几点就走几格。"
                f"**主环是单向的**——行进方向由地图规定，从起点出发第一格就是{_first_property_name()}；"
                "支街是双向的，可以拐进去再退出来。走到路口（有多个可行方向）会暂停，"
                "由当前行动方选择走哪一格，选完继续走完剩余步数。经过或落在起点领工资。"
            )
        return (
            move_line +
            "地产分四组（" + "/".join(_theme.district_label(key) for key in _theme.DISTRICT_KEYS) + "），"
            "持有同组一半且无抵押时租金翻倍。"
            "走到无主地产且现金够时会暂停询问是否购买；走回自己的地产可以升级（租金翻倍）、抵押（回收半价，期间不收租）、"
            "赎回（付六成价）或卖掉（回收三分之一）——此时先向用户说明选项或说出你的决定，再调用 boardgame_decide(action=…)。"
            f"踩到他人地产自动付租金；{specials}。"
            "付不起账单会被强制抵押地产，破产时资产移交债主；回合数到上限后按总资产（现金 + 地产现价六成）判胜负。掷骰用 boardgame_roll。"
        )
    return (
        "规则：十五路棋盘，横竖斜先连成五子者胜。工具调用方式：用户落子后你先点评上一手；"
        "轮到你时调用 boardgame_place（不填坐标即由 AI 替你计算落子和理由），再用你的语气解说这手棋。"
    )


def _first_property_name() -> str:
    """起点沿主环往外走的第一格叫什么（规则说明里用来讲"行进方向"）。"""
    rows = _theme.properties().get("gakuen") or []
    for row in rows:
        if len(row) >= 2:
            return str(row[1])
    return "第一格地产"
