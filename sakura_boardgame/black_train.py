"""Approved black-train v3 preview rules; ongoing v2 saves retain their rules."""
from __future__ import annotations

import copy

try:
    from .economy import CashCategory
except ImportError:
    from economy import CashCategory

RETURN_BASE = 100
REWARD_CAP = 300
TICKET_CAP = 5
TICKET_VALUE = 20


def _choice(action, effect_text, **effects):
    return {"action": action, "effect_text": effect_text, **effects}


# Shared with board overlays: numeric node ID, never coordinates or cell type.
TILE_CATEGORIES = (
    ("gold", "金色车票"), ("blue", "蓝色契约"),
    ("white", "白色B.E.G."), ("black", "黑色异象"),
)

# Route/TIPS evidence supplies themes, not these encounters: tickets, contracts,
# lamp/ward support and their outcomes are fan-game abstractions, not canon facts.
LEGACY_EVENTS = (
    {"id": "gold_1", "category": "金色车票", "color": "gold", "title": "缺角的车票", "story": "缺角的金票仍指向黑列车。散落的乘客也在寻找归路，黑车掌请你留意沿途的线索。她留下谢意，供你选择眼前的回报，或为接下来的探索保留票根。", "choices": [_choice("领取金票收益", "个人收益 +40。", reward=40), _choice("收好票根", "个人收益 +10，票根 +1（最多5张）。", reward=10, tickets=1)]},
    {"id": "gold_2", "category": "金色车票", "color": "gold", "title": "空座的邀请", "story": "黑列车的空座让你想起那些未能归来的乘客。黑车掌说，她想带散落的乘客离开这里。你暂且接下这份邀请，在返程之前选择补偿，或留下继续探索的票根。", "choices": [_choice("领取补偿凭证", "个人收益 +30。", reward=30), _choice("收取纪念票根", "个人收益 +20，票根 +1（最多5张）。", reward=20, tickets=1)]},
    {"id": "gold_3", "category": "金色车票", "color": "gold", "title": "返程印章", "story": "返程印章落下，归路却还没有走完。黑车掌仍在寻找散落的乘客，她与你同样盼望旅途能够结束。你可以兑换这次探索的回报，也可以留下两张票根。", "choices": [_choice("兑换印章", "个人收益 +50。", reward=50), _choice("收藏双联票根", "票根 +2（最多5张），不增加个人收益。", tickets=2)]},
    {"id": "blue_1", "category": "蓝色契约", "color": "blue", "title": "密封信件", "story": "密封信件带来一项协助寻找归路的约定。黑车掌说明验封所需的票根与回报，她没有催促你接受。你可以付票继续，也可以让信件保持原样，拒绝这次委托。", "choices": [_choice("付票验封", "消耗1票根；个人收益 +40，无随机风险。", tickets=-1, reward=40, requires_tickets=1), _choice("拒绝契约", "不支付、不获利，不影响行动数。 撤回本次送信机会。") ]},
    {"id": "blue_2", "category": "蓝色契约", "color": "blue", "title": "无名乘客的契约", "story": "无名乘客留下的契约还未得到回应，归路上的约定也未必值得冒险。黑车掌先说明两种可能的得失，她将封签留给你选择；拒绝之后，你仍可继续探索。", "choices": [_choice("接受封签赌约", "各50%：个人收益 +80 或 -60；收益最低0。", risk=[80, -60]), _choice("拒绝契约", "零收益、零损失，保留票根。") ]},
    {"id": "blue_3", "category": "蓝色契约", "color": "blue", "title": "同步的步伐", "story": "脚步与回声渐渐错开，黑列车附近的异质法则让方向难辨。黑车掌提出押票辨认回声的约定，她先说明代价与风险；你也可以拒绝，另寻自己的归路。", "choices": [_choice("押票猜回声", "消耗1票根；各50%：个人收益 +90 或 -30。", tickets=-1, requires_tickets=1, risk=[90, -30]), _choice("拒绝契约", "不押票，不承受损失。") ]},
    {"id": "white_1", "category": "白色B.E.G.", "color": "white", "title": "B.E.G.的白灯", "story": "白灯照亮前路，却不能让探索毫无代价。你想起依心灵显现的B.E.G.，以及使用时的消耗。这次只能选择一份临时支援：借探照灯继续寻找，或带护符防备异象。", "choices": [_choice("领取探照灯", "下一个人事件自动 +30收益，然后消失；本次不触发。", item="lamp"), _choice("领取护符", "下一个人事件若为黑异象，免除所选惩罚；否则 +10收益，然后消失。", item="ward")]},
    {"id": "white_2", "category": "白色B.E.G.", "color": "white", "title": "B.E.G.救援箱", "story": "归路上的救援不意味着再获一件B.E.G.。黑车掌留下的救援箱只供这次探索周转，她请你在探照灯与护符之间选择；面对未知，援助终究有限。", "choices": [_choice("领取探照灯", "下一个人事件自动 +30收益，然后消失；本次不触发。", item="lamp"), _choice("领取护符", "下一个人事件若为黑异象，免除所选惩罚；否则 +10收益，然后消失。", item="ward")]},
    {"id": "white_3", "category": "白色B.E.G.", "color": "white", "title": "B.E.G.广播", "story": "广播提醒你留意前方的异象，B.E.G.的力量也并非没有消耗。黑车掌愿为探索提供一次临时支援，她让你选择探照灯或护符，随后仍要靠自己寻找归路。", "choices": [_choice("领取探照灯", "下一个人事件自动 +30收益，然后消失；本次不触发。", item="lamp"), _choice("领取护符", "下一个人事件若为黑异象，免除所选惩罚；否则 +10收益，然后消失。", item="ward")]},
    {"id": "black_1", "category": "黑色异象", "color": "black", "title": "倒行的影子", "story": "影子逆着脚步延伸，熟悉的方向不再可靠。列车附近混入的异质法则，让寻找归路也变得危险。黑车掌提醒你留意异象，她等你决定承受损失，还是用票根应对。", "choices": [_choice("承受影子侵蚀", "个人收益 -80，最低0；不扣现金。", reward=-80), _choice("用票根驱散", "消耗1票根，免除收益损失。", tickets=-1, requires_tickets=1)]},
    {"id": "black_2", "category": "黑色异象", "color": "black", "title": "被剪掉的一站", "story": "前方的一站仿佛从归途中缺失，熟悉的路已不能照常辨认。黑车掌提醒你，这里的法则并不安稳。她与你确认还能继续的方向，留下承担损失或交出票根的选择。", "choices": [_choice("承担失站损失", "个人收益 -60，并失去1票根（各自最低0）。", reward=-60, tickets=-1), _choice("交双票补全时刻表", "消耗2票根，免除收益损失。", tickets=-2, requires_tickets=2)]},
    {"id": "black_3", "category": "黑色异象", "color": "black", "title": "第十三声钟", "story": "第十三声钟响起，眼前的列车仍没有带来安稳。异质法则下的归路伴随着危险，黑车掌也不许诺毫无代价。她等你决定承受钟声，还是交出票根减轻这次损失。", "choices": [_choice("承受钟声", "个人收益 -100，最低0。", reward=-100), _choice("交票封钟", "消耗1票根，个人收益仅 -20。", tickets=-1, reward=-20, requires_tickets=1)]},
)


RULES_VERSION = "black-train-v3"
LAYOUT = {
    "gold": (0, 4, 5, 8, 10, 13, 15, 17, 20, 22, 24, 27, 30, 32, 34, 36, 39, 41, 43, 46, 48, 51, 52, 56),
    "blue": (2, 7, 12, 18, 23, 28, 33, 38, 44, 49, 54),
    "white": (1, 6, 11, 16, 21, 26, 29, 35, 40, 45, 50, 55),
    "black": (3, 9, 14, 19, 25, 31, 37, 42, 47, 53),
}


STORIES = (
    "缺角的车票仍能补签，黑列车的归路还没有结束。黑车掌把双联票与旅途酬谢放在窗口，她请你自行选择，继续寻找散落的乘客。",
    "改签窗口亮起微光，黑车掌替寻找归路的人留下了行程支援。她说明兑换的代价，也备好一张票根；不改签的人仍可继续前行。",
    "断续的路标终于接上，散落乘客的归途又清晰了一些。黑车掌替这次协助备好酬谢，她让你选择领取眼前的收益，或留下票根供接下来的旅途周转。",
    "一封尚未拆开的信等待送达。黑车掌请你暂且依循列车的步调，不借改签改变接下来的两程；她把送信的谢礼封好，也接受你的婉拒。",
    "封缄条款没有催促旅人签字。黑车掌提出暂存一张票根，待归来时原样奉还并另付谢礼；她留出空白，让你决定是否接下这份约定。",
    "归路上的回报可以暂存，也可以留作周转。黑车掌拿出封存凭证，她承诺返程时一并交还本金与谢礼；你仍有权把眼前的所得留在手中。",
    "白灯照向错乱的站点，B.E.G.的支援也需要消耗。黑车掌备好校准行程所需的器具，她请你衡量代价，或者领取票根继续寻找归路。",
    "白灯与脚步渐渐共鸣，B.E.G.的微光凝成一枚护符。黑车掌提醒你，它可以留待后续异象再使用；她也为不需护符的旅人准备了眼前的谢意。",
    "灯下的支援箱还留有转换旅途物资的余地。黑车掌说明票根与酬谢之间的交换，她不会收走不足的份额；你也可以合上箱子，保留手中的资源。",
    "影子逆着脚步延伸，列车附近的异质法则让方向不再可靠。黑车掌提醒你保护沿途所得，她等你承受侵蚀，或交出票根驱散这一次阴影。",
    "前方的一站仿佛从归途中被剪去，原本熟悉的站牌已无法辨认。黑车掌与你确认相邻的去路，她也接受一张票根，让你暂且留在原地。",
    "一位散落的乘客仍在寻找回家的方向。黑车掌替她摆出票根与旅途所需的补给，她请你选择交换，或承受这次绕行带来的小小损失。",
)


def _event(index, title, *choices):
    old = LEGACY_EVENTS[index]
    return {**old, "title": title, "story": STORIES[index], "choices": list(choices)}


EVENTS = (
    _event(0, "补签车票", _choice("领取双票", "票根+2，总持有上限5。", tickets=2), _choice("领取票根与收益", "票根+1，收益+20。", tickets=1, reward=20)),
    _event(1, "改签窗口", _choice("购买自定义骰", "支付3票购买一颗自定义骰，持有上限1，可带回。", tickets=-3, requires_tickets=3, custom_die=True), _choice("领取票根", "票根+1。", tickets=1)),
    _event(2, "补全归路", _choice("领取归途酬谢", "个人收益+30，无需支付或选择地格。", reward=30), _choice("领取票根", "票根+1。", tickets=1)),
    _event(3, "密封信件", _choice("接受送信契约", "接下来两次本人行动禁自定义骰；第二次完成后+2票、+30收益。", contract=True, future=2), _choice("拒绝契约", "不支付、不获利。")),
    _event(4, "封缄条款", _choice("封存一张票根", "封存1票，仍占票上限；返程归还并+30收益。", tickets=-1, requires_tickets=1, escrow_ticket=True), _choice("拒绝契约", "不支付、不获利。")),
    _event(5, "封存回报", _choice("封存40收益", "封存40收益，返程归还60；本金仍计收益上限。", reward=-40, requires_reward=40, escrow_reward=60), _choice("拒绝契约", "不支付、不获利。")),
    _event(6, "行程校准", _choice("购买自定义骰", "支付60收益购买一颗自定义骰，持有上限1，可带回。", reward=-60, requires_reward=60, custom_die=True), _choice("领取票根", "票根+1。", tickets=1)),
    _event(7, "稳定共鸣", _choice("领取持久护符", "保留一次护符，后续黑事件可主动使用；不自动消耗。", item="ward", future=True), _choice("领取即时收益", "收益+10。", reward=10)),
    _event(8, "能量转换", _choice("一票兑换收益", "支付1票，收益+35。", tickets=-1, requires_tickets=1, reward=35), _choice("收益兑换双票", "支付25收益，票根+2。", reward=-25, requires_reward=25, tickets=2), _choice("不兑换，继续", "保留资源，继续。")),
    _event(9, "倒行的影子", _choice("承受影子侵蚀", "损失25收益，最低0。", reward=-25), _choice("用票根驱散", "支付1票免除此项损失；不发骰。", tickets=-1, requires_tickets=1)),
    _event(10, "被剪掉的一站", _choice("选择相邻格移动", "移动到合法相邻格，不再触发事件。", adjacent=True), _choice("交票留在原地", "支付1票，留在原地。", tickets=-1, requires_tickets=1)),
    _event(11, "寻找归途的乘客", _choice("收益兑换双票", "支付30收益，票根+2。", reward=-30, requires_reward=30, tickets=2), _choice("承担归途损失", "损失15收益，最低0。", reward=-15)),
)


class BlackTrainMixin:
    """Navigation is unchanged; all effects settle in the mode ledger."""

    def _black_train_active(self):
        return bool(self.black_train and self.black_train.get("active"))

    def _black_train_tiles(self, new=False):
        """Main-board walkable nodes, including start/end and branch entrances.

        IDs are numeric nodes (not col,row coordinates); no RNG or cell mutation.
        New games use the approved fixed layout; v2 saves retain id % 4.
        Frontend/GLB must consume the saved tiles map, with JSON string keys.
        """
        modern = new or not self.black_train or self.black_train.get("rules_version") == RULES_VERSION
        colors = {node: color for color, nodes in LAYOUT.items() for node in nodes}
        categories = dict(TILE_CATEGORIES)
        return {str(cell["id"]): {"color": colors.get(int(cell["id"]), "gold"),
                                 "category": categories[colors.get(int(cell["id"]), "gold")]}
                if modern else dict(zip(("color", "category"), TILE_CATEGORIES[int(cell["id"]) % 4]))
                for cell in self.cells}

    def _black_train_state(self):
        """Lazily migrate old saves without changing their ledgers or RNG stream."""
        state = self.black_train
        if "tiles" not in state:
            state["tiles"] = self._black_train_tiles()
        if "players" not in state:
            state["players"] = {p: {"tickets": 1, "reward": 0, "items": []} for p in ("user", "sakura")}
            state["migration_note"] = "旧共享线索/能量存档迁移：保留行动进度；不将旧线索折算现金，双方个人账本从1票根、0收益开始。"
            state["legacy"] = {k: state.pop(k) for k in ("clues", "energy", "goal") if k in state}
            for row in state.get("history", []):
                event = next((e for e in LEGACY_EVENTS if e["id"] == row.get("event")), {})
                row.setdefault("title", event.get("title", "旧版事件"))
                row.setdefault("story", "旧版共享线索事件；不追溯折现。")
                row.setdefault("effect_text", "旧版记录保留，不计入个人收益。")
                row.setdefault("actual", {"legacy": True, "reward_delta": 0, "tickets_delta": 0})
            old_event = state.get("current_event")
            if old_event and self.pending and self.pending.get("kind") == "black_train":
                event = copy.deepcopy(next(e for e in LEGACY_EVENTS if e["id"] == old_event["id"]))
                # Preserve the saved action strings so an already-rendered choice still works.
                for choice, old_action in zip(event["choices"], self.pending["options"]):
                    choice["action"] = old_action
                state["current_event"] = event
                self.pending["event"] = copy.deepcopy(event)
        state.setdefault("rules_version", "auditable-playtest-v2")
        return state

    def start_black_train(self):
        if (self.winner is not None or self.pending is not None or self._walk is not None or self.submap is not None
                or (self._tower and self._tower.get("active")) or self._black_train_active()):
            raise self._black_train_error("黑列车只能在无胜负、待决策、移动或塔内流程的安全点启动。")
        self.black_train = {
            "active": True, "preview": True, "rules_version": RULES_VERSION, "actions_left": 10,
            "actions_taken": {"user": 0, "sakura": 0},
            "players": {p: {"tickets": 1, "reward": 0, "items": [],
                             "custom_die": bool(self.flags[p].get("pick_dice")), "lock": 0, "dice_lock": 0,
                             "escrow_tickets": 0, "escrow_reward": 0, "escrow_principal": 0}
                        for p in ("user", "sakura")},
            "current_event": None, "history": [], "outcome": None, "tiles": self._black_train_tiles(new=True),
            "settlement_rules": {"base": RETURN_BASE, "reward_cap": REWARD_CAP, "ticket_cap": TICKET_CAP, "ticket_value": TICKET_VALUE, "payout_cap": 500},
            "saved": copy.deepcopy({"positions": self.positions, "last_step": self.last_step,
                                    "flags": self.flags, "skip_pending": self.skip_pending}), "deck": [],
        }
        return self._black_train_result(self.turn, ["黑列车·本轮可审计测试方案（非历史定稿复原）：双方各5个完整行动；各自1票根，个人收益不含固定返程金与票根折现，收益不均分。经营冻结，现金仅返程增加：每人固定100 + 个人收益（0至300）+ 剩余票根×20（最多5张），单人上限500。"])

    def _black_train_result(self, player, story, **extra):
        return {"player": player, "story": story, "pending": copy.deepcopy(self.pending),
                "black_train": copy.deepcopy(self.black_train), "winner": self.winner,
                "next": None if self.pending or self._walk else self.turn,
                "board": self.render(), **extra}

    def _black_train_landing(self, walk, story):
        player = walk["player"]
        final = walk["path"][-1]
        other = self._other(player)
        blocked_at = None
        teleported = False
        # Mirror ordinary grid landing BEFORE the end -> start portal. Only the
        # walked-to cell blocks: a portal arrival may share start with the other
        # player, just as in _finish_walk; do not run a second collision check.
        if (self.map_kind == "grid" and len(walk["path"]) >= 2
                and self.positions[other] == final):
            try:
                from .engine import PLAYER_LABEL
            except ImportError:
                from engine import PLAYER_LABEL
            blocked_at = final
            final = walk["path"][-2]
            walk["path"].append(final)
            self.positions[player] = final
            story.append(
                f"「{self._black_train_state()['tiles'][str(blocked_at)]['category']}」上已经站着 {PLAYER_LABEL[other]} —— "
                f"本模式同格挡路，{PLAYER_LABEL[player]} 退后一步停在它身后。")
        if self.cells[final]["type"] == "end":
            walk["path"].append(self.start_node)
            self.positions[player] = self.start_node
            teleported = True
        stepped_back = blocked_at is not None
        if stepped_back:
            self._block_seq = int(getattr(self, "_block_seq", 0)) + 1
        # Being pushed back or teleported supplies no forward-facing last step.
        if stepped_back or teleported:
            self.last_step[player] = None
        else:
            self.last_step[player] = list(walk["path"][-2:]) if len(walk["path"]) > 1 else None
        self._last_walk = copy.deepcopy(walk)
        self._last_walk.update(steps_left=0, blocked=stepped_back, blockedAt=blocked_at,
                               blockedBy=other if stepped_back else None,
                               blockSeq=int(getattr(self, "_block_seq", 0)))
        self._walk = None
        state = self._black_train_state()
        cell_id = self.positions[player]
        tile = state["tiles"][str(cell_id)]
        # Keep legacy global EVENTS indices (including one-card test overrides),
        # but never let an off-color card escape onto this tile. Other colors
        # remain queued for their own landings. Refill only this color's trio.
        deck = state.setdefault("deck", [])
        modern = state.get("rules_version") == RULES_VERSION
        events = EVENTS if modern else LEGACY_EVENTS
        eligible = [i for i, event in enumerate(events) if event["color"] == tile["color"]
                    and not (modern and event["id"] == "blue_1" and 4 - state["actions_taken"][player] < 2)]
        slot = next((i for i in range(len(deck) - 1, -1, -1)
                     if type(deck[i]) is int and deck[i] in eligible), None)
        if slot is None:
            self._effect_rng().shuffle(eligible)
            deck.extend(eligible)
            slot = len(deck) - 1
        event = copy.deepcopy(events[deck.pop(slot)])
        event["cell_id"] = cell_id
        wallet = state["players"][player]
        if modern:
            self._black_train_prepare(event, player)
            state["current_event"] = event
            self.pending = {"player": player, "type": "black_train", "kind": "black_train",
                            "event": copy.deepcopy(event),
                            "options": [c["action"] for c in event["choices"] if c["available"]]}
            story.append(f"【{event['category']}】{event['title']}：{event['story']}")
            return
        protected = "ward" in wallet["items"] and event["color"] == "black"
        for choice in event["choices"]:
            choice["available"] = protected or wallet["tickets"] >= choice.get("requires_tickets", 0)
            if not choice["available"]:
                choice["reason"] = "票根不足"
        state["current_event"] = event
        self.pending = {"player": player, "type": "black_train", "kind": "black_train",
                        "event": copy.deepcopy(event), "options": [c["action"] for c in event["choices"] if c.get("available", True)]}
        story.append(f"【{event['category']}】{event['title']}：{event['story']}")

    def _black_train_decide_legacy(self, player, action):
        pending = self.pending
        if not pending or pending.get("kind") != "black_train":
            raise self._black_train_error("当前没有黑列车事件选择。")
        if player != pending["player"] or action not in pending["options"]:
            raise self._black_train_error("请由当前行动方选择黑列车事件提供的选项。")
        state = self._black_train_state()
        event = state["current_event"]
        choice = next(c for c in event["choices"] if c["action"] == action)
        wallet = state["players"][player]
        protected = "ward" in wallet["items"] and event["color"] == "black"
        if not protected and wallet["tickets"] < choice.get("requires_tickets", 0):
            raise self._black_train_error("票根不足，不能选择此项。")
        before = copy.deepcopy(wallet)
        triggered = list(wallet["items"])
        wallet["items"] = []
        bonus = sum(30 if item == "lamp" else (0 if protected else 10) for item in triggered)
        rolled_reward = choice.get("reward", 0)
        if "risk" in choice:
            rolled_reward = self._effect_rng().choice(choice["risk"])
        # Apply the item first, then the event; cap each real ledger update.
        wallet["reward"] = min(REWARD_CAP, wallet["reward"] + bonus)
        if not protected:
            wallet["reward"] = max(0, min(REWARD_CAP, wallet["reward"] + rolled_reward))
            wallet["tickets"] = max(0, min(TICKET_CAP, wallet["tickets"] + choice.get("tickets", 0)))
        if choice.get("item"):
            wallet["items"].append(choice["item"])
        actual = {"reward_delta": wallet["reward"] - before["reward"], "tickets_delta": wallet["tickets"] - before["tickets"],
                  "reward": wallet["reward"], "tickets": wallet["tickets"], "items_triggered": triggered,
                  "items_gained": list(wallet["items"]), "prevented": protected,
                  "event_reward_roll": rolled_reward, "item_reward_requested": bonus}
        state["actions_taken"][player] += 1
        state["actions_left"] -= 1
        state["history"].append({"player": player, "cell_id": event.get("cell_id", self.positions[player]),
                                 "event": event["id"], "title": event["title"], "story": event["story"],
                                 "action": action, "effect_text": choice["effect_text"], "choices": copy.deepcopy(event["choices"]), "category": event["category"], "color": event["color"], "actual": actual})
        state["current_event"] = None
        self.pending = None
        self._pass_turn()
        story = [f"选择「{action}」：个人收益实际 {actual['reward_delta']:+}，票根实际 {actual['tickets_delta']:+}；剩余 {state['actions_left']} 个行动。" + ("护符已自动化解黑异象。" if protected else "")]
        if state["actions_left"] == 0:
            state["active"] = False
            state["outcome"] = "safe_return"
            state["settlement"] = {}
            for p, account in state["players"].items():
                payout = RETURN_BASE + account["reward"] + account["tickets"] * TICKET_VALUE
                state["settlement"][p] = {"base": RETURN_BASE, "reward": account["reward"], "tickets": account["tickets"], "ticket_cash": account["tickets"] * TICKET_VALUE, "total": payout, "expired_items": list(account["items"])}
                account["items"] = []
                self._post_cash(p, payout, CashCategory.ADVENTURE_SETTLEMENT)
            for key, value in state.pop("saved").items():
                setattr(self, key, copy.deepcopy(value))
            self._last_walk = None
            story.append("黑列车探险结束，已安全返回，恢复原位置与全部经营状态；经营回合未增加。各人独立返程结算：" + "；".join(f"{p} +{v['total']}" for p, v in state["settlement"].items()))
        return self._black_train_result(player, story, action=action)

    def _black_train_custom_die_available(self, player):
        if not self._black_train_active():
            return False
        wallet = self._black_train_state()["players"][player]
        return bool(wallet.get("custom_die") and not wallet.get("lock", wallet.get("dice_lock", 0)))

    def _black_train_use_custom_die(self, player):
        if not self._black_train_custom_die_available(player):
            raise self._black_train_error("未持有自定义骰，或契约锁定期间不能使用。")
        self.black_train["players"][player]["custom_die"] = False
        # The original flag is not a second item, and must never resurrect it.
        self.black_train.get("saved", {}).get("flags", {}).get(player, {})["pick_dice"] = False

    def _black_train_neighbors(self, node, previous=None):
        if self.cells[node]["type"] == "end":
            return [self.start_node]
        neighbors = list(self._moves[node])
        options = [n for n in neighbors if n != previous]
        preds = self._road_preds.get(node) if self.map_kind in ("city", "grid") else None
        if preds:
            options = [n for n in options if n not in preds]
        return options or [n for n in neighbors if n != previous] or neighbors

    def _black_train_targets(self, player, adjacent=False):
        start = self.positions[player]
        last = self.last_step.get(player)
        previous = last[0] if last and last[1] == start else None
        if adjacent:
            return sorted({self.start_node if self.cells[n]["type"] == "end" else n
                           for n in self._black_train_neighbors(start, previous)
                           if self.map_kind != "grid" or n != self.positions[self._other(player)]})
        seen, todo, targets = set(), [(start, previous)], set()
        while todo:
            node, prev = todo.pop()
            if (node, prev) in seen:
                continue
            seen.add((node, prev))
            for nxt in self._black_train_neighbors(node, prev):
                if nxt != start:
                    targets.add(nxt)
                todo.append((nxt, node))
        return sorted(n for n in targets if self.black_train["tiles"][str(n)]["color"] != "gold")

    def _black_train_prepare(self, event, player):
        state = self.black_train
        wallet = state["players"][player]
        remaining = 4 - state["actions_taken"][player]
        expanded = []
        for original in event["choices"]:
            choices = [copy.deepcopy(original)]
            if original.get("recolor") or original.get("adjacent"):
                choices = []
                for node in self._black_train_targets(player, adjacent=bool(original.get("adjacent"))):
                    c = copy.deepcopy(original)
                    c["target"] = node
                    c["action"] = ("改为金色" if c.get("recolor") else "移动到") + f" #{node}"
                    c["effect_text"] += f" 目标#{node}。"
                    choices.append(c)
            for choice in choices:
                reason = None
                if wallet["tickets"] < choice.get("requires_tickets", 0):
                    reason = "票根不足（封存票不可支付）"
                elif wallet["reward"] < choice.get("requires_reward", 0):
                    reason = "可用收益不足（封存收益不可支付）"
                elif choice.get("custom_die") and wallet.get("custom_die"):
                    reason = "自定义骰持有上限1"
                elif choice.get("future") and remaining < int(choice["future"]):
                    reason = "剩余本人行动不足，未来用途不可兑现"
                elif choice.get("contract") and wallet.get("lock", 0):
                    reason = "已有送信契约"
                elif choice.get("item") == "ward" and "ward" in wallet["items"]:
                    reason = "已有护符"
                choice["available"] = reason is None
                if reason:
                    choice["reason"] = reason
                expanded.append(choice)
        if event["color"] == "black" and "ward" in wallet["items"]:
            expanded.append(_choice("使用护符化解本次黑事件", "消耗护符，免除本事件支付、损失及移动。", ward=True, available=True))
        if not any(c["available"] for c in expanded):
            expanded.append(_choice("无法执行，继续", "没有可执行选项，保留资源继续。", available=True))
        event["choices"] = expanded

    def _black_train_decide(self, player, action):
        state = self._black_train_state()
        if state.get("rules_version") != RULES_VERSION:
            return self._black_train_decide_legacy(player, action)
        pending = self.pending
        if not pending or pending.get("kind") != "black_train" or pending["player"] != player or action not in pending["options"]:
            raise self._black_train_error("请选择当前行动方可用的黑列车选项。")
        event = state["current_event"]
        choice = next(c for c in event["choices"] if c["action"] == action)
        wallet = state["players"][player]
        if (wallet["tickets"] < choice.get("requires_tickets", 0)
                or wallet["reward"] < choice.get("requires_reward", 0)
                or (choice.get("custom_die") and wallet.get("custom_die"))):
            raise self._black_train_error("资源不足或持有已达上限。")
        before = copy.deepcopy(wallet)
        wallet["reward"] = max(0, min(REWARD_CAP - wallet["escrow_principal"], wallet["reward"] + choice.get("reward", 0)))
        wallet["tickets"] = max(0, min(TICKET_CAP - wallet["escrow_tickets"], wallet["tickets"] + choice.get("tickets", 0)))
        triggered = []
        if choice.get("ward"):
            wallet["items"].remove("ward")
            triggered.append("ward")
        if choice.get("item"):
            wallet["items"].append(choice["item"])
        if choice.get("custom_die"):
            wallet["custom_die"] = True
        if choice.get("escrow_ticket"):
            wallet["escrow_tickets"] += 1
        if choice.get("escrow_reward"):
            wallet["escrow_principal"] += 40
            wallet["escrow_reward"] += choice["escrow_reward"]
        if choice.get("recolor"):
            targets = [choice["target"]]
            if self.cells[choice["target"]]["type"] in ("start", "end"):
                targets = [c["id"] for c in self.cells if c["type"] in ("start", "end")]
            for target in targets:
                state["tiles"][str(target)] = {"color": "gold", "category": "金色车票"}
        if choice.get("adjacent"):
            old = self.positions[player]
            self.positions[player] = choice["target"]
            through_portal = any(self.cells[n]["type"] == "end" for n in self._black_train_neighbors(
                old, self.last_step[player][0] if self.last_step.get(player) else None)) and choice["target"] == self.start_node
            self.last_step[player] = None if through_portal else [old, choice["target"]]
            self._last_walk = {"player": player, "path": [old, choice["target"]], "steps_left": 0}
        contract_paid = False
        if choice.get("contract"):
            wallet["lock"] = wallet["dice_lock"] = 2
        elif wallet.get("lock", 0):
            wallet["lock"] -= 1
            wallet["dice_lock"] = wallet["lock"]
            if wallet["lock"] == 0:
                wallet["tickets"] = min(TICKET_CAP - wallet["escrow_tickets"], wallet["tickets"] + 2)
                wallet["reward"] = min(REWARD_CAP - wallet["escrow_principal"], wallet["reward"] + 30)
                contract_paid = True
        actual = {"reward_delta": wallet["reward"] - before["reward"],
                  "tickets_delta": wallet["tickets"] - before["tickets"], "reward": wallet["reward"],
                  "tickets": wallet["tickets"], "prevented": bool(choice.get("ward")),
                  "items_triggered": triggered, "items_gained": [choice["item"]] if choice.get("item") else [],
                  "contract_paid": contract_paid, "custom_die": wallet["custom_die"],
                  "target": choice.get("target"), "position": self.positions[player]}
        state["actions_taken"][player] += 1
        state["actions_left"] -= 1
        state["history"].append({"player": player, "cell_id": event["cell_id"], "event": event["id"],
                                 "title": event["title"], "story": event["story"], "color": event["color"],
                                 "category": event["category"], "action": action, "effect_text": choice["effect_text"],
                                 "choices": copy.deepcopy(event["choices"]), "actual": actual})
        state["current_event"] = None
        self.pending = None
        self._pass_turn()
        story = [f"选择「{action}」：收益实际{actual['reward_delta']:+}，票根实际{actual['tickets_delta']:+}；剩余{state['actions_left']}行动。"]
        if not state["actions_left"]:
            state.update(active=False, outcome="safe_return", settlement={})
            saved = state.pop("saved")
            for p, account in state["players"].items():
                account["tickets"] += account["escrow_tickets"]
                account["reward"] = min(REWARD_CAP, account["reward"] + account["escrow_reward"] + 30 * account["escrow_tickets"])
                payout = RETURN_BASE + account["reward"] + account["tickets"] * TICKET_VALUE
                state["settlement"][p] = {"base": RETURN_BASE, "reward": account["reward"], "tickets": account["tickets"],
                                           "ticket_cash": account["tickets"] * TICKET_VALUE, "total": payout,
                                           "custom_die": bool(account["custom_die"]), "expired_items": list(account["items"])}
                account.update(items=[], escrow_tickets=0, escrow_principal=0, escrow_reward=0)
                saved["flags"][p]["pick_dice"] = bool(account["custom_die"])
                self._post_cash(p, payout, CashCategory.ADVENTURE_SETTLEMENT)
            for key, value in saved.items():
                setattr(self, key, copy.deepcopy(value))
            self._last_walk = None
            story.append("黑列车探险结束，已安全返回；经营已恢复，道具账本合并，返程现金独立结算。")
        return self._black_train_result(player, story, action=action)

    def _black_train_label(self):
        if not self.black_train:
            return ""
        state = self._black_train_state()
        accounts = "，".join(
            f"{p} 收益{v['reward']}/300·票根{v['tickets']}/5"
            + (f"（封存本金{v.get('escrow_principal', 0)}、票{v.get('escrow_tickets', 0)}；"
               f"骰{'有' if v.get('custom_die') else '无'}·锁{v.get('lock', 0)}；护符{v['items'].count('ward')}）"
               if state.get("rules_version") == RULES_VERSION else "")
            for p, v in state["players"].items())
        if state["active"]:
            return f"黑列车·可审计测试探险中：{accounts}；剩余 {state['actions_left']}/10 行动（双方各5）；经营暂停且旧状态冻结。"
        return f"黑列车·已安全返回：{accounts}；经营已恢复。"
