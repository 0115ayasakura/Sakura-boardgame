from __future__ import annotations

import json
import copy
from functools import wraps
import os
import random
import re
import threading
import tempfile
import time
from collections import deque
from contextlib import contextmanager
from collections.abc import Mapping
from pathlib import Path
from typing import Any, Callable

try:                       # 个别嵌入式 Python 不带 webbrowser，不能让插件因此加载不了
    import webbrowser
except ImportError:        # pragma: no cover
    webbrowser = None      # type: ignore[assignment]

try:
    from . import engine
    from .engine import GAMES, GameError, PLAYER_LABEL, create_game, restore_game, rules_text
except ImportError:
    import engine  # type: ignore[no-redef]
    from engine import (  # type: ignore[no-redef]
        GAMES,
        GameError,
        PLAYER_LABEL,
        create_game,
        restore_game,
        rules_text,
    )

try:                       # 服务器只依赖标准库，可脱离宿主/engine 单独 import
    from .board_server import BoardServer, make_handler
except ImportError:
    from board_server import BoardServer, make_handler  # type: ignore[no-redef]
_make_handler = make_handler  # 旧私有入口，保持测试/外部引用兼容

try:
    from . import action_protocol, agent_turns
except ImportError:
    import action_protocol
    import agent_turns


STATE_FILE = "state.json"
_FRAGMENT_ID = "local.sakura.boardgame.state"
LOG_LIMIT = 60
# poi/ 地标插图、icons/ 格子图标、chibi/ 立牌棋子、board/ 棋盘底图、lib/ three.js 与 3D 场景
# （.js/.glb 是给 three.js 棋盘的 —— 库文件、board_scene.glb、board3d.js）
_ART_DIR = Path(__file__).resolve().parent / "art"

# 立牌棋子：设定集最后一页那四个 Q 版小人，用户在 PS 里抠好、装裱成白描边立牌
# （boardgame_art/dress_standees.py）。**每局开局时弹窗选两个**，默认玩家用索菲（左上金发）、
# 她（夜乃樱）用樱（右上银白发）—— 用户 2026-09-22 指定。
PIECES = ("sophie", "sakura", "suisen", "katan")
PIECE_LABELS = {"sophie": "索菲", "sakura": "樱", "suisen": "水仙", "katan": "华淡"}
PIECE_DEFAULT = {"user": "sophie", "sakura": "sakura"}
PIECE_SETTINGS = {"user": "piece_user", "sakura": "piece_sakura"}

# 等距格子棋盘的专属规则：两枚棋子不许停在同一格，后到的退一步停在前者身后。
SAME_CELL_RULE_TEXT = ("同格挡路：两枚棋子不能停在同一格 —— 后来走到已被对方占住的格子时，"
                       "会退后一步停在对方身后那一格，并结算实际停留格的事件。")


def _int_setting(values: Mapping[str, Any], key: str, default: int) -> int:
    """宿主配置可能留有空值；保留合法的 0，但不让坏配置击穿状态接口。"""
    try:
        return int(values.get(key, default))
    except (TypeError, ValueError, OverflowError):
        return default


def game_shop_items() -> dict[str, Any]:
    """商店道具（名字/说明/价格）。单独包一层是为了让 plugin 不必在模块顶层就 import engine。"""
    try:
        try:
            from . import engine as _engine
        except ImportError:
            import engine as _engine  # type: ignore[no-redef]
        return _engine.shop_items()
    except Exception:
        return {"dice": {"label": "特殊骰子", "price": 180},
                "gain": {"label": "现金增益", "price": 200},
                "loss": {"label": "节流道具", "price": 200}}

_EFFECT_DESC = {
    "cash": lambda e: f"现金 {'+' if e['amount'] >= 0 else ''}{e['amount']}",
    "dice_bonus": lambda e: f"下次掷骰 +{e['bonus']}",
    "rent_free": lambda e: "免租一次",
    "salary_next": lambda e: "下次经过起点工资翻倍",
    "goto_start": lambda e: "传回起点并领工资",
    "collect_each": lambda e: f"向对方收 ¥{e['amount']}",
    "pay_each": lambda e: f"给对方付 ¥{e['amount']}",
    "move": lambda e: f"沿路强制移动 {e['delta']} 格",
    "skip": lambda e: "停一回合",
}

# 待决策选项的中文名（对局记录与模型提示共用一份，避免两处写法不一致）
_OPTION_LABELS = {"buy": "买入", "upgrade": "升级", "mortgage": "抵押",
                  "redeem": "赎回", "sell": "卖掉", "skip": "跳过",
                  "buy_dice": "买特殊骰子", "buy_gain": "买现金增益（掷骰判定）"}


def serialized_action(method):
    @wraps(method)
    def run(self, *args, **kwargs):
        dispatch = False
        with self._action_lock:
            mutating = method.__name__ in action_protocol.MUTATING_METHODS or method.__name__ == "_commit_agent"
            if not mutating:
                return method(self, *args, **kwargs)
            outer = getattr(self, "_action_transaction", None) is None
            coordinator = getattr(self, "_agent_turns", None)
            agent_checkpoint = coordinator.checkpoint() if coordinator and outer else None
            metadata = kwargs.get("metadata") if method.__name__ == "handle_action" else None
            def invoke():
                # One gate covers HTTP, legacy tools and nested Agent/algorithm
                # actions under the same lock/rollback boundary. Explicit new
                # games and end remain available; settings are not game moves.
                name = method.__name__
                gameplay = name in {"roll", "decide", "place", "tower_action", "sakura_action",
                                    "auto", "undo", "_commit_agent"}
                action = args[0] if args else kwargs.get("action")
                if name == "handle_action":
                    gameplay = action in {"roll", "decide", "place", "tower_step", "tower_finish",
                                          "act_sakura", "auto", "undo", "agent_fallback", "black_train_start"}
                if gameplay:
                    self._guard_playing()
                return method(self, *args, **kwargs)

            try:
                response = action_protocol.run(self, invoke,
                                               metadata=metadata,
                                               action=args[0] if method.__name__ == "handle_action" and args else kwargs.get("action"),
                                               arguments=args[1] if method.__name__ == "handle_action" and len(args) > 1 else kwargs.get("arguments"))
                rejected = isinstance(response, dict) and response.get("ok") is False
                if rejected and agent_checkpoint is not None:
                    coordinator.restore(agent_checkpoint)
                control = method.__name__ == "handle_action" and (
                    (args[0] if args else kwargs.get("action")) in {"pause_play", "resume_play"})
                dispatch = bool(outer and coordinator and not rejected and not control)
            except Exception as error:
                if agent_checkpoint is not None:
                    coordinator.restore(agent_checkpoint)
                if method.__name__ != "handle_action":
                    raise
                self._logger.error("网页操作事务已回滚", fields={
                    "reason_code": "ACTION_ROLLED_BACK", "error_type": type(error).__name__})
                response = {"ok": False, "error": str(error) if isinstance(error, GameError)
                            else f"操作失败（{type(error).__name__}）",
                            "code": "ACTION_REJECTED" if isinstance(error, GameError) else "ACTION_FAILED",
                            "httpStatus": 400 if isinstance(error, GameError) else 500,
                            "version": self._version, "gameSeq": getattr(self, "_game_seq", 0), "duplicate": False}
                if isinstance(metadata, Mapping):
                    response["requestId"] = metadata.get("requestId")
        # Crucially outside the RLock AND after the outer atomic save. No host
        # inference/submit can block board polling, or survive a rejected save.
        if dispatch:
            coordinator.dispatch_pending()
        return response
    return run


class BoardgameService:
    def __init__(self, config: Any, data_path: Callable[[str], Any], logger: Any,
                 plugin_dir: Path | None = None, context: Any = None) -> None:
        self._action_lock = threading.RLock()
        self._config = config
        # 只归一配置，不改旧存档的地图类型与格子。
        if str(config.get().get("map_kind") or "city") == "random":
            config.update({"map_kind": "city"})
        self._data_path = data_path
        self._logger = logger
        self._context = context
        # 先备好日志/事件这些 _load 可能要用到的东西（作废旧存档时要在网页上说一句）
        self._version = 1
        self._game_seq = 0
        self._play_paused = False
        action_protocol.initialize(self)
        self._log: deque[str] = deque(maxlen=LOG_LIMIT)
        self._lines_written = 0          # _log 会滚动，按"累计写过多少行"来取新增部分（见 _story_since）
        self._last_event: dict[str, Any] | None = None
        self._game = self._load()
        self._server = BoardServer(self, plugin_dir) if plugin_dir else None
        self._rng = random.Random()
        # ---- 她主动搭话（定时器）----
        self._last_talk = 0.0
        self._proactive_thread: threading.Thread | None = None
        self._proactive_stop = threading.Event()
        self._page_busy = False          # 网页上正开着弹窗
        self._busy_at = 0.0              # 用户最近一次在网页上操作的时间
        # ---- 悔棋快照（只存在内存里，重启就没了）----
        self._snapshots: deque[dict[str, Any]] = deque(maxlen=12)
        self._opponent = {"id": "", "name": "", "portrait": ""}
        self._build_identity()          # 先定对手是谁（对局记录里的名字要用它）
        self._persona = self._build_persona()
        self._guide = ""
        self._guide_source = ""
        self._load_guide()              # 桌游引导（依赖角色 id，所以在身份之后）
        self._agent_applying = False
        self._agent_turns = agent_turns.AgentTurns(self)

    def decision_driver(self) -> str:
        return "algorithm" if self._config.get().get("decision_driver") == "algorithm" else "agent"

    def save_settings(self, values: Mapping[str, Any]) -> dict[str, Any]:
        # Host settings bypass HTTP actions. A driver change must revoke tickets
        # even when the user switches away and back before the next board poll.
        with self._action_lock:
            previous = self.decision_driver()
            saved = self._config.update(values)
            if self.decision_driver() != previous:
                self._version += 1
                self._agent_turns.public()
            return {"applicationState": saved}

    @staticmethod
    def agent_error(message: str) -> GameError:
        return GameError(message)

    @staticmethod
    def agent_shop_items() -> dict[str, Any]:
        return game_shop_items()

    def _guard_playing(self) -> None:
        if self._play_paused:
            raise GameError("对局已暂停，请先继续游戏；也可以显式结束或重开一局。")

    def _guard_agent_seat(self, player: str) -> None:
        if player == "sakura" and self.decision_driver() == "agent" and not self._agent_applying:
            raise GameError("真实Agent模式只能用本次token调用boardgame_agent_commit；不会由算法代走。")

    @serialized_action
    def _commit_agent(self, arguments: Mapping[str, Any], *, identity: Mapping[str, Any]) -> dict[str, Any]:
        return self._agent_turns.apply(arguments, identity)

    def _execute_agent_action(self, action: str, arguments: dict[str, Any]) -> dict[str, Any]:
        # The outer _commit_agent transaction owns persistence and rollback. Seat
        # is forced here, never accepted from model arguments.
        values = {**arguments, "player": "sakura"}
        if action == "roll":
            return self.roll(values)
        if action == "decide":
            return self.decide(values)
        if action == "place":
            return self.place(values)
        if action in ("tower_step", "tower_finish"):
            return self.tower_action(values, finish=action == "tower_finish")
        raise GameError("不支持的Agent动作。")

    def _execute_algorithm_action(self, legal: Mapping[str, Any]) -> dict[str, Any]:
        """Execute one authorized fallback using the ordinary algorithm policies."""
        action = legal['action']
        values: dict[str, Any] = {'player': 'sakura', '_automatic': True}
        if action == 'place':
            # No coordinates: place() selects the configured AI level/persona.
            return self.place(values)
        if action == 'roll':
            return self.roll(values)
        if action == 'decide':
            choice = self._pick_pending_action(self._game)
            note = self._decision_note(self._game, choice)
            result = self.decide({**values, 'action': choice})
            self._touch(None, f"她的判断：{note}")
            return result
        if action in ('tower_step', 'tower_finish'):
            return self.tower_action({**values, **legal['arguments']}, finish=action == 'tower_finish')
        raise GameError('当前动作没有可用的算法代操作。')

    # ---- 对手身份（名字 / 立绘跟随当前角色）--------------------------

    def _build_identity(self) -> dict[str, str]:
        """读当前角色的名字与立绘，并把名字灌进引擎的显示名。

        这样在 Sakura 里换角色，对局记录、侧栏、网页标题和棋盘棋子上的头像都跟着换，
        插件本身不用改。读不到就退回默认（夜乃樱 + 内置立绘）。
        """
        info = {"id": "", "name": "", "portrait": ""}
        try:
            info = self._persona_module().identity(self._context)
        except Exception as error:
            self._logger.warning("读取当前角色身份失败，用默认名字与立绘", fields={
                "reason_code": "IDENTITY_FAILED", "error_type": type(error).__name__})
        label = engine.set_opponent_label(info.get("name"))
        self._opponent = {
            "id": info.get("id") or "",
            "name": label,
            # 有角色立绘就指向它，否则用插件自带的（网页里也有兜底）
            "portrait": "/portrait" if info.get("portrait") else "/art/avatar_sakura.png",
        }
        self._logger.info("对手角色已确定", fields={
            "character_id": self._opponent["id"] or "(无)",
            "display_name": label,
            "custom_portrait": bool(info.get("portrait")),
        })
        return self._opponent

    def _refresh_identity(self) -> None:
        """角色换了就重读（每次 /state 都问一次很便宜：只在 id 变化时才读角色包）。"""
        if self._context is None:
            return
        try:
            service = self._context.get("sakura.host.character")
            current = service.current() if service is not None else {}
            if str((current or {}).get("id") or "") != self._opponent.get("id"):
                self._build_identity()
                self._load_guide()          # 换了角色就换一份引导（她可能有专属的那份）
        except Exception:
            return

    def opponent_payload(self) -> dict[str, str]:
        self._refresh_identity()
        portrait = self._opponent.get("portrait") or ""
        if portrait == "/portrait":
            # 带上角色 id 做缓存失效：换角色后 URL 变了，浏览器不会拿旧图
            portrait = f"/portrait?c={self._opponent.get('id', '')}"
        return {"name": self._opponent.get("name") or "", "portrait": portrait,
                "id": self._opponent.get("id") or ""}

    def portrait_file(self) -> Path | None:
        """当前角色立绘的绝对路径；没有就 None（网页会退回插件自带的头像）。"""
        self._refresh_identity()
        if self._opponent.get("portrait") != "/portrait" or self._context is None:
            return None
        try:
            service = self._context.get("sakura.host.character")
            character_id = self._opponent.get("id") or ""
            if service is None or not character_id:
                return None
            info = self._persona_module().identity(self._context)
            path = Path(info.get("portrait") or "")
            return path if path.is_file() else None
        except Exception:
            return None

    # ---- 地图主题（地名与事件卡属于哪个世界）------------------------

    @staticmethod
    def _theme_module() -> Any:
        try:
            import theme
        except ImportError:
            from . import theme
        return theme

    def active_theme(self) -> str:
        """该用哪个主题：有对局就用存档里的那份（地名已经烙在格子上），否则看设置。

        设置项「地图主题」默认是 `auto` = **跟着当前角色走**——主题文件里写了这个角色 id 就用它
        （例如碧蓝航线的港区主题认领 chuyue），没写就退回默认主题。想让某个角色固定用别的世界，
        在设置里显式选一个即可。
        """
        if self._game is not None and getattr(self._game, "theme", ""):
            return str(self._game.theme)
        module = self._theme_module()
        setting = str(self._config.get().get("theme") or "auto")
        if setting != "auto" and setting in {item["id"] for item in module.available()}:
            return setting
        return module.theme_for_character(self._opponent.get("id", ""))

    def theme_payload(self) -> dict[str, Any]:
        """网页侧栏要显示的东西：这一局的世界、设置指定要用的世界、以及"要重开一局才生效"。

        有对局时**这一局的主题不会中途变**（地名已经烙在格子上、存档里记的是它），
        所以设置里改了主题、或者换了角色，都要等下一局——这中间的落差必须让用户看得见，
        否则会以为"主题没生效"（实测踩过：换了初月、设置也选了港区，但那一局是旧的存档）。
        """
        module = self._theme_module()
        setting = str(self._config.get().get("theme") or "auto")
        target = self._theme_setting_target(setting)               # 设置/角色希望用的
        running = str(getattr(self._game, "theme", "") or "") if self._game is not None else ""
        current = running or target
        module.set_theme(current)                                  # 地名/事件卡按这一局渲染
        current = module.theme_id()
        names = {item["id"]: item["name"] for item in module.available()}
        return {"id": current, "name": names.get(current, current),
                "setting": setting, "available": module.available(),
                "nextId": target, "nextName": names.get(target, target),
                "pending": bool(running) and target != running,
                # 对局记录里"抽到事件卡"那一行的前缀（主题可改：默认 NAVI通知，港区是指挥部通报）
                "naviLog": engine._theme.navi_log_prefix()}

    def _theme_setting_target(self, setting: str) -> str:
        """按设置与当前角色算"新开一局会用哪个主题"。"""
        module = self._theme_module()
        # 格子棋盘永远用默认主题（次元错位恋人）——用户 2026-10-01 拍板，
        # 棋子全是次元错位恋人的角色，别的世界观太割裂；网页"下一局生效"的提示也要照实显示
        if str(self._config.get().get("map_kind") or "city") == "grid":
            return "dimension_lovers"
        if setting != "auto" and setting in {item["id"] for item in module.available()}:
            return setting
        return module.theme_for_character(self._opponent.get("id", ""))

    # ---- 打法性格 ---------------------------------------------------

    def _build_persona(self) -> dict[str, Any]:
        """从角色卡正文推出她的下棋性格（拿不到卡就用默认）。

        角色卡正文里怎么写她的性格，就直接决定她在棋盘上的四个参数（见 persona.py）：
        留多少底金、多爱升级、多爱拐进支街、领先时会不会让玩家一手。
        每次开局重读一次，改了角色卡不用重启插件。
        """
        try:
            settings = self._config.get()
            preset = str(settings.get("play_style") or "card")
            persona = self._persona_module()
            info = persona.build(self._context, preset)
            self._logger.info("对手的打法性格已确定", fields={
                "source": info["source"], "reserve": info["policy"]["reserve"],
                "upgrade": info["policy"]["upgrade"], "branch": info["policy"]["branch"],
                "mercy": info["policy"]["mercy"],
            })
            return info
        except Exception as error:
            self._logger.warning("打法性格推断失败，用默认性格", fields={
                "reason_code": "PERSONA_FAILED", "error_type": type(error).__name__})
            persona = self._persona_module()
            traits = dict(persona.DEFAULT_TRAITS)
            return {"traits": traits, "policy": persona.policy_from(traits),
                    "text": persona.describe(traits, persona.policy_from(traits)),
                    "source": "默认"}

    @staticmethod
    def _persona_module() -> Any:
        try:
            import persona
        except ImportError:
            from . import persona
        return persona

    def persona_text(self) -> str:
        return str(self._persona.get("text") or "")

    # ---- 桌游引导（换角色不用改角色卡）------------------------------

    def _load_guide(self) -> str:
        """读这次的「桌游引导」：角色专属文件 > 数据目录里的默认文件 > 内置通用版。

        这段文本以前得由用户手动粘进**每个**角色的角色卡（角色卡补充.txt）；现在插件自己在
        有对局时注入，换角色、换地图主题都不用动卡。角色一变就重读一次。
        """
        try:
            text, source = self._guide_module().load(self._data_path, self._opponent.get("id", ""))
        except Exception as error:
            self._logger.warning("桌游引导读取失败，改用内置通用版", fields={
                "reason_code": "GUIDE_FAILED", "error_type": type(error).__name__})
            text, source = self._guide_module().DEFAULT_TEXT, "内置通用版"
        self._guide = text
        self._guide_source = source
        self._logger.info("桌游引导已加载", fields={"source": source, "characters": len(text)})
        return text

    def guide_text(self, kind: str) -> str:
        """当前游戏该看到的引导（另一个游戏的 [大富翁]/[五子棋] 行会被滤掉）。"""
        if not self._guide:
            return ""
        return self._guide_module().for_game(self._guide, kind)

    @staticmethod
    def _guide_module() -> Any:
        try:
            import guide
        except ImportError:
            from . import guide
        return guide

    # ---- 棋盘服务器 -------------------------------------------------

    @property
    def board_url(self) -> str:
        if self._server is None:
            return ""
        return self._server.start()

    def open_in_browser(self, url: str) -> bool:
        """用系统默认浏览器打开 url。Windows 上依次尝试几种方式，全部失败返回 False。

        为什么要试多种：插件的 Python 是 Sakura 自带的嵌入式解释器，环境不保证和系统一致。
        `webbrowser.open` 失败时是**返回 False 而不是抛异常**（这个坑之前没接住，
        所以"打不开"的时候日志里什么都没有），`os.startfile` 走的是 ShellExecute，更直接。
        """
        if not url:
            return False
        attempts: list[tuple[str, Callable[[], Any]]] = []
        if webbrowser is not None:
            # 放在第一个：它失败时会明确返回 False（os.startfile 失败是静默的，无法判断）
            attempts.append(("webbrowser.open", lambda: webbrowser.open(url)))
        if hasattr(os, "startfile"):
            attempts.append(("os.startfile", lambda: os.startfile(url)))
        attempts.append(("cmd start", lambda: os.system(f'cmd /c start "" "{url}"')))

        errors = []
        for name, call in attempts:
            try:
                result = call()
            except Exception as error:  # noqa: BLE001 — 任何异常都算这次失败
                errors.append(f"{name}:{type(error).__name__}")
                continue
            if result is False:                            # webbrowser 明确说没打开
                errors.append(f"{name}:返回False")
                continue
            self._logger.info("已请系统浏览器打开棋盘", fields={"method": name, "url": url})
            return True
        self._logger.warning("自动打开棋盘失败（可手动复制地址）", fields={
            "reason_code": "BOARD_OPEN_FAILED", "url": url, "attempts": ";".join(errors)})
        return False

    def open_board(self) -> str:
        url = self.board_url
        if url:
            self.open_in_browser(url)
        return url

    def open_board_message(self) -> str:
        """设置页「打开棋盘页面」按钮回给用户的话——无论如何都把地址带上，方便手动复制。"""
        url = self.board_url
        if not url:
            return "棋盘服务器没起来，稍后再试一次。"
        if self.open_in_browser(url):
            return f"已请系统浏览器打开棋盘：{url}"
        return f"没能自动打开浏览器，请把这个地址复制到浏览器：{url}"

    def _city_module(self) -> Any:
        try:
            import city_map
        except ImportError:
            from . import city_map
        return city_map

    def _grid_module(self) -> Any:
        try:
            import grid_map
        except ImportError:
            from . import grid_map
        return grid_map

    def _moves_map(self) -> dict[str, list[int]] | None:
        """每格可以走的方向。城市图与格子棋盘都有单向规则，必须下发；随机图是无向图就不发。"""
        if not self._game or self._game.kind != "monopoly":
            return None
        if getattr(self._game, "map_kind", "random") not in ("city", "grid"):
            return None
        moves = getattr(self._game, "_moves", None)
        if not moves:
            return None
        return {str(node): list(dsts) for node, dsts in moves.items()}

    def _decor(self) -> dict[str, Any] | None:
        """手工城市图的制图数据（街区/水体）。随机地图没有装饰层。"""
        if not self._game or self._game.kind != "monopoly":
            return None
        if getattr(self._game, "map_kind", "random") != "city":
            return None
        return self._city_module().decor()

    def _map_template(self) -> dict[str, Any] | None:
        """静态模板：没开局时网页也能渲染演示棋盘，而不是退化成网格。"""
        kind = self._new_map_kind()
        if kind == "grid":
            grid_map = self._grid_module()
            cells, edges, main_path, branches, gates = grid_map.build_grid()
            return {"cells": cells, "edges": edges, "mainPath": main_path,
                    "branches": branches, "gates": gates, "decor": None, "mapKind": "grid"}
        if kind != "city":
            return None
        city_map = self._city_module()
        cells, edges, main_path, branches = city_map.build_city()
        return {"cells": cells, "edges": edges, "mainPath": main_path,
                "branches": branches, "gates": [], "decor": city_map.decor(), "mapKind": "city"}

    def piece_of(self, player: str) -> str:
        """这一局某方用哪个立牌棋子（配置里没设或设成非法值时回退到默认）。"""
        got = str(self._config.get().get(PIECE_SETTINGS[player]) or "")
        return got if got in PIECES else PIECE_DEFAULT[player]

    def pieces_payload(self) -> dict[str, Any]:
        """网页侧栏「棋子」那一块：四个可选项 + 双方当前选的。"""
        return {
            "options": [{"key": key, "label": PIECE_LABELS[key], "url": f"/art/chibi/{key}.png"}
                        for key in PIECES],
            "user": self.piece_of("user"),
            "sakura": self.piece_of("sakura"),
        }

    def board_view(self) -> dict[str, Any] | None:
        """棋盘底图的相机参数（art/board/board_view.json）。

        网页不能凭空猜渲染图里每格在哪：这张图是正交相机拍的，网页按同一套参数
        把 (格坐标, 顶面高度) 投到图像像素上，图钉/棋子/高亮才落在格子上。
        文件不在（还没出图）就返回 None，网页退回"没有底图"的画法。
        """
        cached = getattr(self, "_board_view_cache", "missing")
        if cached != "missing":
            return cached
        path = _ART_DIR / "board" / "board_view.json"
        try:
            view = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            view = None
        self._board_view_cache = view
        return view

    def same_cell_payload(self) -> dict[str, Any]:
        """同格挡路规则：规则原文 + **此刻的情况**（网页提示条直接显示）。

        「当前情况」只能由引擎说：棋子的落点在引擎里，网页只看得到坐标。

        用户 2026-09-23 说这条提示常驻会挡视野 —— 改成**触发时才弹**：`seq` 是"挡路发生过
        第几次"，网页只在它变大时弹一次（连挡两次也认得出，因为 blocked 一直是 True）。
        `now` 只在刚挡过路时有内容，其余时候是空串。
        """
        game = self._game
        if not game or game.kind != "monopoly" or getattr(game, "map_kind", "") != "grid":
            return {"on": False, "text": "", "now": "", "seq": 0}
        now = ""
        last = getattr(game, "_last_walk", None) or {}
        if last.get("blocked") and last.get("blockedAt") is not None:
            blocked_at = int(last["blockedAt"])
            who = PLAYER_LABEL.get(str(last.get("player")), "")
            by = PLAYER_LABEL.get(str(last.get("blockedBy")), "")
            label = game.cells[blocked_at]['name']
            if game._black_train_active():
                label = game._black_train_state()['tiles'].get(str(blocked_at), {}).get('category', '列车事件')
            now = (f"{who} 想停在「{label}」，"
                   f"那里已经有 {by}，所以退回了一格。")
        return {"on": True, "text": SAME_CELL_RULE_TEXT, "now": now,
                "seq": int(getattr(game, "_block_seq", 0))}

    def tower_payload(self) -> dict[str, Any] | None:
        """最近一次进塔的经过 —— 网页拿它放"塔内地图"那段上行动画。

        没有对局、或这一局还没进过塔就返回 None。`seq` 每进一次 +1，网页靠它认出
        "这是新的一次"（跟同格挡路那条提示条用 `blockSeq` 是一个套路），
        否则每秒轮询都会重放一遍动画。
        """
        game = self._game
        if not game or getattr(game, "kind", "") != "monopoly":
            return None
        tower = getattr(game, "_tower", None)
        if not tower:
            return None
        floors = copy.deepcopy(tower.get("floors") or [])
        cell = int(tower.get("cell") or 0)
        entry = ""
        if 0 <= cell < len(game.cells):
            entry = str(game.cells[cell].get("name") or "")
        return {
            "seq": int(tower.get("seq") or 0),
            "name": str(tower.get("name") or ""),
            "entry": entry,
            "player": str(tower.get("player") or ""),
            "playerLabel": PLAYER_LABEL.get(str(tower.get("player") or ""), ""),
            "floors": floors,
            "active": bool(tower.get("active")),
            "at": int(tower.get("at", -1)),
            "winner": game.winner,
        }

    def cash_fx_payload(self) -> dict[str, Any] | None:
        """最近一批"现金盈亏头顶小字"（金币进账/交租双方/她买特殊骰子）。

        与 landing 同一套 seq 认新机制；网页在棋子**落格后**才播放（跟弹窗一起放行）。
        """
        game = self._game
        if not game or getattr(game, "kind", "") != "monopoly":
            return None
        info = getattr(game, "_last_fx", None)
        if not info:
            return None
        return dict(info)

    def landing_payload(self) -> dict[str, Any] | None:
        """最近一次游戏格的结算经过 —— 网页拿它放"游戏判定"弹窗。

        抽到什么事件、掷出几点、赢没赢，整个过程要在画面上看得见（用户 2026-09-30 定：
        不能只藏在右侧对局记录里）。`seq` 每次结算 +1，网页靠它认出新的一次。
        """
        game = self._game
        if not game or getattr(game, "kind", "") != "monopoly":
            return None
        info = getattr(game, "_last_landing", None)
        if not info:
            return None
        return {
            "seq": int(info.get("seq") or 0),
            "player": str(info.get("player") or ""),
            "cellName": str(info.get("cellName") or ""),
            "towerSeq": info.get("towerSeq"),
            "towerFloor": info.get("towerFloor"),
            "event": str(info.get("event") or ""),
            "name": str(info.get("name") or ""),
            "roll": int(info.get("roll") or 0),
            "won": bool(info.get("won")),
            "rolls": list(info.get("rolls") or [int(info.get("roll") or 0)]),
            "rerolled": bool(info.get("rerolled")),
            "winRoll": int(engine.MONOPOLY_GAME_WIN_ROLL),
            "amount": int(info.get("amount") or 0),
            # 结果那行原话（引擎 story 的最后一段，金额/奖励都说清了）
            "reward": str(info.get("reward") or ""),
        }

    def item_draw_payload(self) -> dict[str, Any] | None:
        """权威商店抽卡结果，客户端按 seq 去重并排入普通事件队列。"""
        game = self._game
        if not game or getattr(game, "kind", "") != "monopoly":
            return None
        info = getattr(game, "_last_item_draw", None)
        return copy.deepcopy(info) if info else None

    def check_payload(self) -> dict[str, Any] | None:
        """最近一次商店掷骰判定 —— 网页拿它放"判定"动画弹窗。"""
        game = self._game
        if not game or getattr(game, "kind", "") != "monopoly":
            return None
        info = getattr(game, "_last_check", None)
        if not info:
            return None
        return {
            "seq": int(info.get("seq") or 0),
            "player": str(info.get("player") or ""),
            "roll": int(info.get("roll") or 0),
            "threshold": int(info.get("threshold") or 0),
            "success": bool(info.get("success")),
            "label": str(info.get("label") or ""),
            "price": int(info.get("price") or 0),
            "sides": int(getattr(game, "dice_sides", 6) or 6),
            "note": (f"接下来 {engine.MONOPOLY_SHOP_BUFF_ROUNDS} 回合收入 ×{engine.MONOPOLY_SHOP_BUFF_GAIN:g}"
                     if info.get("success") else
                     f"接下来 {engine.MONOPOLY_SHOP_BUFF_ROUNDS} 回合支出 ×{engine.MONOPOLY_SHOP_BUFF_LOSS_UP:g}"),
        }

    def legend(self) -> dict[str, Any]:
        """玩法说明里的"格子图例"与街区清单——金额取真实常量、名字取当前主题。

        以前这段文案写死在网页里，于是换主题之后图例还在讲"特别会计""学园补助""灰色的流浪猫"，
        连工资都还印着旧的 ¥200。这里由插件生成，网页照着渲染就不会再出现另一个世界的词。

        图例跟着**当前这局用哪套格子**走：等距立体的格子棋盘是六种新形式（含商店/金币），
        城市图与随机图照旧那套旧格（税/补助/日常/猫/怪獣/黑列车 + NAVI 播报）。
        """
        names = engine._theme.cell_names()
        cells: dict[str, str] = {
            "start": (f"起点：经过或落在这里领工资 ¥{engine.pass_start('city')}。"),
            "property": "地产：可以买地、收租、升级；持有同一街区一半的地产（且都没抵押）时租金翻倍。",
            "tax": f"{names.get('tax', '税')}：缴税 ¥{engine.MONOPOLY_TAX}。",
            "bonus": f"{names.get('bonus', '补助')}：直接 +¥{engine.MONOPOLY_BONUS}。",
            "rest": f"{names.get('rest', '休息')}：什么都不发生，歇一口气。",
            "cat": (f"{names.get('cat', '猫')}：花 ¥{engine.MONOPOLY_CAT_COST} 换回 "
                    f"¥{engine.MONOPOLY_CAT_GAIN} 的治愈。"),
            "monster": f"{names.get('monster', '怪獣')}：损失 ¥{engine.MONOPOLY_MONSTER_TOLL}。",
            "train": f"{names.get('train', '列车')}：被收走 ¥{engine.MONOPOLY_TRAIN_TOLL}。",
            "navi": f"{names.get('navi', '通知')}：抽取一张事件卡。",
            "device": (f"{names.get('device', '中央装置')}（棋盘中央）：走进去会进入建筑内部，"
                       "探索三间只有事件格的房间再出来，然后继续这一回合。"),
            "gate": "传送门：成对出现的青色圈，站在其中一格可以跳到对面那一格（双向）。",
        }
        # 图例跟着"这一局用的是哪种地图"走（没开局时按设置）
        game_kind = getattr(self._game, "map_kind", None) \
            or str(self._config.get().get("map_kind") or "city")
        if game_kind == "grid":
            shop = engine.shop_items()
            game_rows = engine.game_events()
            # 格子棋盘专属：把旧格换成六种新形式
            for legacy in ("tax", "bonus", "rest", "cat", "monster", "train", "navi"):
                cells.pop(legacy, None)
            cells["start"] = (f"起点：经过或落在这里领工资 ¥{engine.pass_start('grid')}；"
                              "格子上的箭头指着路线方向。")
            cells["end"] = "终点：走到这里这一圈就算走完，领一份工资后从传送门回起点。"
            cells["property"] = ("地产：踩上抽一张地契，买下后格子上会变成你的颜色 + 对勾；"
                                 "持有同一街区一半的地产（且都没抵押）时租金翻倍。")
            cells["shop"] = (f"商店：买「{shop['dice']['label']}」（¥{shop['dice']['price']}，"
                             f"可择机自选点数、持有上限1），或买「{shop['gain']['label']}」"
                             f"（¥{shop['gain']['price']}）——买它要掷骰判定，点数达到骰子最大点数"
                             f"一半才算买到；没过则接下来 {engine.MONOPOLY_SHOP_BUFF_ROUNDS} 回合支出 "
                             f"×{engine.MONOPOLY_SHOP_BUFF_LOSS_UP:g}（钱照扣）。"
                             "也可花 ¥100 抽一张道具卡：租金护盾、装修券、冒险契约或判定幸运签；"
                             "仅从尚未持有的种类中等概率抽取，四种全持有时不可抽。")
            cells["game"] = ("游戏：抽一件特别事件（"
                             + "、".join(str(e.get("name")) for e in game_rows)
                             + "），掷骰判输赢；输了记成日常一幕，什么都不发生。"
                             "赢下「异色的骰子」会得到特殊骰子。")
            cells["coin"] = "金币：" + "/".join(f"¥{v}" for v in engine.coin_tiers()) + " 三档，等概率。"
            cells["event"] = f"{names.get('event', '事件')}：抽取一张事件卡。"
            # 进塔走的是地图上那座**传送点**（美术上立着门、不贴图标的那格），不是另铺一条路
            # （用户 2026-09-23 纠正）。城市图/随机图没有塔，所以这两条只在格子棋盘上出现，
            # 旧的那条 `device`（"棋盘中央的装置格"）同时撤掉。
            cells.pop("device", None)
            cells["tower"] = (f"{names.get('device', '中央装置')}（入口）：支路上那座传送点踩上去"
                              "会被送进塔里，沿着回廊往上走三层（事件卡 / 金币 / 特别事件，"
                              "每层当场结算），走完回到原来那一格继续这一回合。")
            cells["gate"] = "传送门：走完一圈从终点回到起点（就是这条环）。"
        return {
            "cells": cells,
            "districts": [{"key": d.get("key", ""), "label": d["label"], "color": d["color"]}
                          for d in self._city_module().district_washes()],
            "naviName": names.get("navi", "通知"),
            "themeName": engine._theme.current().get("name", ""),
        }

    @serialized_action
    def state_payload(self) -> dict[str, Any]:
        values = self._config.get()
        level = int(values.get("gomoku_level") or 2)
        aggression, style_note = self.gomoku_aggression()
        style = int(round(aggression * 100))
        style_follow = bool(values.get("gomoku_style_follow_card", True))
        # 先把主题切到"这一局/这个角色"该用的那一份，再取棋盘与日志（地名、事件卡都跟着它）
        self._theme_module().set_theme(self.active_theme())
        game_payload = self._game.to_dict() if self._game else None
        # History has its own field; do not send the same large ledger twice every poll.
        if game_payload is not None:
            game_payload.pop("action_records", None)
        return {
            "version": self._version,
            "protocolVersion": 1,
            "eventCursor": {"gameSeq": getattr(self, "_game_seq", 0),
                            "actionSeq": int(getattr(self._game, "action_seq", 0))},
            "presentationEventId": (f"{getattr(self, '_game_seq', 0)}:{self._version}:"
                                    f"{self._last_event.get('kind', 'event')}") if self._last_event else None,
            "previewBlackTrain": bool(getattr(self, "preview_black_train", False)),
            "game": game_payload,
            "log": list(self._log),
            # 与旧文本日志并列，不把 story 再拼回 log，避免重复播报。
            "actionRecords": copy.deepcopy(self._game.action_records)
            if self._game and self._game.kind == "monopoly" else [],
            "lastEvent": self._last_event,
            "boardUrl": self._server.url if self._server else "",
            # 对手是谁：网页拿它显示名字、按钮文案与棋子头像（换角色自动跟随）
            "opponent": self.opponent_payload(),
            # 当前地图主题（地名/事件卡来自哪一份世界）：网页与工具都拿它显示
            "theme": self.theme_payload(),
            # 玩法说明的图例与街区清单（金额与名字都由当前主题生成，网页不要写死）
            "legend": self.legend(),
            "pieces": self.pieces_payload(),
            "gameSeq": getattr(self, "_game_seq", 0),
            # 棋盘底图的相机参数：网页按它把每格投影到渲染图的像素位置上
            # （图与参数都是 boardgame_art/make_web_assets.py 出来的，改地图要重跑那份）
            "boardView": self.board_view(),
            # 同格挡路：只有等距格子棋盘有这条规则，文案也一起下发，网页不用自己编
            "sameCellRule": self.same_cell_payload(),
            # 最近一次进塔的经过（塔内地图那段上行动画）：没进过塔就是 None
            "tower": self.tower_payload(),
            # 游戏格的结算经过 / 商店的掷骰判定：网页拿它们放过程弹窗（判定要看得见）
            "landing": self.landing_payload(),
            "cashFx": self.cash_fx_payload(),
            "diceCheck": self.check_payload(),
            "itemDraw": self.item_draw_payload(),
            "cards": [
                {"title": card["title"], "flavor": card["flavor"],
                 "effect": _EFFECT_DESC.get(card["effect"]["type"], lambda e: "特殊效果")(card["effect"])}
                for card in engine.EVENT_CARDS
            ],
            "edges": self._game.edges if self._game and self._game.kind == "monopoly" else None,
            "mainPath": getattr(self._game, "main_path", None) if self._game and self._game.kind == "monopoly" else None,
            "branches": getattr(self._game, "branches", None) if self._game and self._game.kind == "monopoly" else None,
            # 传送门：成对的两格互通（格子棋盘上有），网页画青色圈 + 虚线弧
            "gates": getattr(self._game, "gates", None) if self._game and self._game.kind == "monopoly" else None,
            "mapKind": getattr(self._game, "map_kind", "random") if self._game else None,
            "settingMapKind": self._new_map_kind(),
            # 悔棋：还有没有快照可退（网页据此决定要不要显示「悔棋」按钮）
            "canUndo": self.can_undo(),
            # 自动模式：off / hers（只自动她的回合）/ both（双方都自动，看两家打）
            "playPaused": self._play_paused,
            "autoMode": str(values.get("auto_mode") or "off"),
            "decisionDriver": self.decision_driver(),
            "agentDecision": self._agent_turns.public(),
            "decor": self._decor(),
            # 每格允许前往的方向（城市图的主环是单向的，网页按它画金色箭头）
            "moves": self._moves_map(),
            # 只在没有城市图对局时才下发模板：有对局时装饰层已经随 decor 下发，不必重复
            "mapTemplate": None if (self._game and self._game.kind == "monopoly")
            else self._map_template(),
            "stats": self._game.stats() if self._game and self._game.kind == "monopoly" else None,
            "difficulty": level,
            "style": style,
            # 棋风的来源（跟随角色卡 / 手动），以及那句人话说明，网页拿它显示
            "styleFollowCard": style_follow,
            "styleNote": style_note,
            "styleManual": _int_setting(values, "gomoku_style", 45),
        }

    def _new_map_kind(self) -> str:
        """新局配置只接受固定地图；宿主可能仍传来旧 random 设置。"""
        kind = str(self._config.get().get("map_kind") or "city")
        return kind if kind in ("city", "grid") else "city"

    def _ledger_snapshot(self) -> dict[str, Any]:
        """只从权威状态取数，不解析 story，也不持有可变状态引用。"""
        game = self._game
        players = {}
        for player in engine.PLAYERS:
            flags = copy.deepcopy(game.flags[player])
            inventory = {"pick_dice": bool(flags.pop("pick_dice", False))}
            effects = {**flags, "skip_pending": bool(game.skip_pending[player])}
            players[player] = {"cash": int(game.cash[player]),
                               "effects": effects, "inventory": inventory}
        properties = {
            cell["id"]: {"name": cell["name"], "owner": cell.get("owner"),
                         "level": int(cell.get("level") or 0),
                         "mortgaged": bool(cell.get("mortgaged"))}
            for cell in game.cells if cell["type"] == "property"
        }
        return {"players": players, "properties": properties}

    @staticmethod
    def _ledger_changes(before: dict, after: dict) -> dict[str, Any]:
        return {key: {"before": copy.deepcopy(before.get(key)),
                      "after": copy.deepcopy(after.get(key))}
                for key in sorted(before.keys() | after.keys())
                if before.get(key) != after.get(key)}

    def _record_action(self, before: dict[str, Any], player: str, action: str,
                       description: str, result: dict[str, Any],
                       context: dict[str, Any] | None = None, automatic: bool = False) -> None:
        game = self._game
        after = self._ledger_snapshot()
        players = {}
        for seat in engine.PLAYERS:
            old, new = before["players"][seat], after["players"][seat]
            players[seat] = {
                "cash": {"before": old["cash"], "after": new["cash"],
                         "delta": new["cash"] - old["cash"]},
                **{key: {"before": old[key], "after": new[key],
                         "changed": self._ledger_changes(old[key], new[key])}
                   for key in ("effects", "inventory")},
            }
        properties = []
        for cell, old in before["properties"].items():
            new = after["properties"][cell]
            old_state = {key: old[key] for key in ("owner", "level", "mortgaged")}
            new_state = {key: new[key] for key in ("owner", "level", "mortgaged")}
            if old_state != new_state:
                properties.append({"cell": cell, "name": new["name"],
                                   "before": old_state, "after": new_state,
                                   "changed": self._ledger_changes(old_state, new_state)})
        game.action_seq += 1
        game.action_records.append({
            "id": game.action_seq, "turn": game.dice_turn,
            "turn_complete": bool(game.winner or (not game.pending and not game._walk)),
            "actor": player, "action": action,
            "description": description, "source": "auto" if automatic else "direct",
            "context": copy.deepcopy(context or {}), "story": list(result.get("story") or []),
            "players": players, "properties": properties,
        })
        del game.action_records[:-engine.ACTION_RECORD_LIMIT]

    def _touch(self, story: list[str] | None = None, status: str | None = None) -> None:
        """把这一步的经过写进对局记录（网页按行渲染）。

        `status` 通常就是 `"".join(story)` 再拼一句尾部状态（「轮到 X。」「还剩 N 步没走完。」），
        所以两个都追加会让同一句话在记录里连着出现两次——只补 status 里多出来的那一段。
        """
        self._version += 1
        for entry in story or []:
            self._log.append(entry)
            self._lines_written += 1
        if not status:
            return
        prefix = "".join(story or [])
        if prefix and status.startswith(prefix):
            tail = status[len(prefix):].strip()
            if tail:
                self._log.append(tail)
                self._lines_written += 1
            return
        self._log.append(status)
        self._lines_written += 1

    def _story_since(self, seq: int) -> list[str]:
        """取第 `seq` 行之后新写进对局记录的内容（`_log` 是 deque，会用总数倒推）。"""
        added = self._lines_written - seq
        return list(self._log)[-added:] if added > 0 else []

    # ---- 存档 -------------------------------------------------------

    def _load(self) -> Any:
        path = self._data_path(STATE_FILE)
        if not path.exists():
            return None
        data: dict[str, Any] | None = None
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
            game = restore_game(data)
            action_protocol.restore_protocol(self, data.get("_service"))
            service_data = data.get("_service")
            self._play_paused = isinstance(service_data, dict) and service_data.get("playPaused") is True
            return game
        except GameError:
            # 旧版本的存档（地图结构或经济数值改过）不再兼容。**一定要在网页上说一句**：
            # 否则旧存档里的老数值（老地价、老起始资金）被读回来，看起来就像"新版本没生效"。
            self._logger.warning("旧版存档已作废（版本不兼容）", fields={
                "reason_code": "STATE_VERSION_OUTDATED",
                "saved_version": data.get("map_version") if isinstance(data, dict) else None,
            })
            self._touch(None, "检测到旧版本的对局存档，已经作废——这一版改过地图与经济数值，"
                              "请点「开一局 / 重开一局」开始新的对局。")
            return None
        except (OSError, ValueError, KeyError, TypeError) as error:
            self._logger.warning("棋局存档读取失败，已忽略旧存档", fields={
                "reason_code": "STATE_LOAD_FAILED",
                "error_type": type(error).__name__,
            })
            return None

    def _save(self) -> None:
        transaction = getattr(self, "_action_transaction", None)
        if transaction is not None:
            transaction["dirty"] = True
            return
        self._save_now()

    def _delete_save_now(self) -> None:
        path = self._data_path(STATE_FILE)
        if path.exists():
            path.unlink()

    def _save_now(self) -> None:
        if self._game is None:
            return
        path = self._data_path(STATE_FILE)
        path.parent.mkdir(parents=True, exist_ok=True)
        # Never truncate the last valid save before the replacement is ready.
        # Use the same directory so os.replace stays on one filesystem; close
        # the temporary file before replacing it (required on Windows).
        data = self._game.to_dict()
        data["_service"] = {**action_protocol.protocol_state(self), "playPaused": self._play_paused}
        payload = json.dumps(data, ensure_ascii=False)
        temporary = None
        try:
            with tempfile.NamedTemporaryFile(mode="w", encoding="utf-8", dir=path.parent,
                                             prefix=path.name + ".", suffix=".tmp",
                                             delete=False) as stream:
                temporary = Path(stream.name)
                stream.write(payload)
                stream.flush()
                os.fsync(stream.fileno())
            os.replace(temporary, path)
        finally:
            if temporary is not None:
                try:
                    temporary.unlink(missing_ok=True)
                except OSError:
                    # Replacement already committed: failed temp cleanup must not
                    # report failure and roll memory back behind the durable state.
                    self._logger.warning("临时存档清理失败", fields={"reason_code": "SAVE_TEMP_CLEANUP_FAILED"})

    # ---- 网页操作入口 ------------------------------------------------

    @serialized_action
    def handle_action(self, action: str, arguments: Mapping[str, Any], *,
                      metadata: Mapping[str, Any] | None = None) -> dict[str, Any]:
        # 用户在网页上动过手了：主动搭话要避开这种时刻（他正在玩，别插嘴）
        self._busy_at = time.time()
        try:
            if action in ("pause_play", "resume_play"):
                paused = action == "pause_play"
                if paused and self._game is None:
                    raise GameError("当前没有进行中的对局，不能暂停。")
                if self._play_paused != paused:
                    self._play_paused = paused
                    self._version += 1  # Fence late moves, without changing game identity.
                    self._agent_turns.public()  # Revoke old capabilities; never mint a new one.
                    self._save()
                return {"ok": True, "playPaused": self._play_paused,
                        "version": self._version, "gameSeq": self._game_seq,
                        "autoMode": str(self._config.get().get("auto_mode") or "off"),
                        "status": ("对局已暂停；存档、待确认结果与待决策均保留，可继续或显式结束/重开。"
                                   if paused else "对局已恢复；保留原自动模式，等待棋盘按原流程请求下一步。")}
            # 冷启动兜底：网页上直接操作时如果还没有对局，就先自己开一局。
            # 这样"模型没调用工具"也不影响玩——网页自己就能开局。
            if (self._game is None and action in ("roll", "decide", "act_sakura", "place", "auto")
                    and not (self.decision_driver() == "agent" and action in ("act_sakura", "auto"))):
                kind = "gomoku" if action == "place" else "monopoly"
                self.start({"game": kind}, skip_open=True)
            if action == "start":
                result = self.start({"game": str(arguments.get("game") or "")}, skip_open=True)
                return {"ok": True, "status": result["status"]}
            if action == "black_train_start":
                if not getattr(self, "preview_black_train", False):
                    raise GameError("黑列车触发入口仅限测试预览。")
                if not self._game or self._game.kind != "monopoly":
                    raise GameError("请先开启大富翁测试对局。")
                with self._snapshot("黑列车抵达前"):
                    result = self._game.start_black_train()
                self._save()
                status = " ".join(result.get("story") or ["黑列车抵达，合作探险开始。"])
                self._touch(result.get("story"), status)
                return {"ok": True, "status": status}
            if action == "roll":
                result = self.roll({"player": "user", "use_custom_die": arguments.get("use_custom_die") is True})
                return {"ok": True, "status": result["status"], "board_url": self.board_url}
            if action == "decide":
                result = self.decide({"action": str(arguments.get("action") or "")})
                return {"ok": True, "status": result["status"], "board_url": self.board_url}
            if action in ("tower_step", "tower_finish"):
                return self.tower_action(arguments, finish=action == "tower_finish")
            if action == "place":
                result = self.place({"row": arguments.get("row"), "col": arguments.get("col")})
                return {"ok": True, "status": result["status"], "board_url": self.board_url}
            if action == "act_sakura":
                result = self.sakura_action()
                return {"ok": True, "status": result["status"], "narrated": result.get("narrated", False),
                        **({"agentDecision": result["agentDecision"]} if "agentDecision" in result else {})}
            if action == "cancel_agent":
                return self._agent_turns.cancel(arguments.get("id"))
            if action == "agent_fallback":
                return self._agent_turns.fallback(arguments.get("id"))
            if action == "set_decision_driver":
                driver = str(arguments.get("driver") or "")
                if driver not in ("agent", "algorithm"):
                    raise GameError("决策驱动只能是agent或algorithm。")
                self._config.update({"decision_driver": driver})
                self._version += 1
                return {"ok": True, "status": "决策驱动已切换。", "decisionDriver": driver}
            if action == "undo":
                result = self.undo({})
                return {"ok": True, "status": result["status"], "board_url": self.board_url}
            if action == "auto":
                # 网页上的「自动演出」：让双方自己走几回合，完全不经过模型
                result = self.auto({"rounds": arguments.get("rounds") or 3})
                return {"ok": True, "status": result["status"], "story": result.get("story"),
                        "turns": result.get("turns"), "finished": result.get("finished")}
            if action == "set_auto_mode":
                mode = str(arguments.get("mode") or "off")
                if mode not in ("off", "hers", "both"):
                    raise GameError("自动模式只能是 off、hers 或 both。")
                self._config.update({"auto_mode": mode})
                self._version += 1
                labels = {"off": "已关闭自动模式（自己点按钮）",
                          "hers": f"自动模式：{PLAYER_LABEL['sakura']}的回合会自动走完",
                          "both": "自动模式：双方都由插件自动走（旁观整局）"}
                return {"ok": True, "status": labels[mode]}
            if action == "set_difficulty":
                level = int(arguments.get("level", 2))
                if level not in (1, 2, 3):
                    raise GameError("难度只能是 1（入门）、2（进阶）、3（困难）。")
                self._config.update({"gomoku_level": level})
                self._version += 1
                return {"ok": True, "status": f"五子棋难度已设为 {level}。"}
            if action == "set_style":
                style = int(arguments.get("style", 45))
                if not 0 <= style <= 100:
                    raise GameError("棋风数值要在 0–100 之间（0 最稳健，100 最进攻）。")
                # 在网页上手动点了棋风，就等于"不再跟随角色卡"（想跟回去去设置里打开）
                self._config.update({"gomoku_style": style, "gomoku_style_follow_card": False})
                self._version += 1
                return {"ok": True, "status": f"棋风已手动设为 {style}（不再跟随角色卡）。"}
            if action == "set_piece":
                who = str(arguments.get("player") or "")
                key = str(arguments.get("key") or "")
                if who not in PIECE_SETTINGS or key not in PIECES:
                    raise GameError("棋子只能从这四个立牌里选。")
                other = "sakura" if who == "user" else "user"
                if key == self.piece_of(other):
                    raise GameError("两位不能选同一个立牌。")
                self._config.update({PIECE_SETTINGS[who]: key})
                self._version += 1
                return {"ok": True, "status": f"棋子已换成「{PIECE_LABELS[key]}」。"}
            if action == "set_map_kind":
                kind = str(arguments.get("kind") or "")
                if kind not in ("grid", "city"):
                    raise GameError("地图来源只能是 grid（格子棋盘）或 city（城市图）；随机玩法已移除。")
                self._config.update({"map_kind": kind})
                self._version += 1
                label = {"grid": "格子棋盘", "city": "第二横滨市"}[kind]
                return {"ok": True, "status": f"地图来源已切到「{label}」，下一局生效。"}
            return {"ok": False, "error": f"未知操作 {action}"}
        except GameError as error:
            return {"ok": False, "error": str(error)}
        except Exception as error:  # 兜底：任何意外都不应让网页连接断开
            self._logger.error("网页操作处理失败", fields={
                "operation": action,
                "reason_code": "WEB_ACTION_FAILED",
                "error_type": type(error).__name__,
            })
            return {"ok": False, "error": f"操作失败（{type(error).__name__}）"}

    def _pick_pending_action(self, game: Any) -> str:
        """替夜乃樱决定待决策——**按她的性格来**（参数来自角色卡，见 persona.py）。

        岔路：好奇的愿意拐进支街，好胜的往自己已有地产的街区钻，其余保留随机。
        地产：买地前先留够底金（谨慎的角色留得多，所以更不容易被账单逼到抵押），
        升级按意愿概率决定；明显领先时偶尔会把一块地让给玩家（温柔的角色才会）。
        """
        pending = game.pending
        policy = self._persona.get("policy") or {}
        traits = self._persona.get("traits") or {}
        pending_type = pending.get("type")
        if pending_type == "black_train":
            return str(pending["options"][0])
        if pending_type == "route":
            return str(self._pick_route(game, pending["options"], policy, traits))
        if pending_type == "dice":
            # 特殊骰子：保守的角色定在中间，好胜/好奇的往大点数上顶
            sides = int(getattr(game, "dice_sides", 6) or 6)
            bold = float(traits.get("boldness") or 0.5)
            return str(max(1, min(sides, round(1 + bold * (sides - 1)))))
        reserve = int(policy.get("reserve") or 0)
        cash = game.cash["sakura"]
        if pending_type == "shop":
            # 商店：留够底金才买；先补骰子（还没用过），再考虑倍率道具
            items = game_shop_items()
            if "buy_dice" in pending["options"] and not game.flags["sakura"].get("pick_dice"):
                if cash >= int(items["dice"]["price"]) + reserve:
                    return "buy_dice"
            if "buy_draw" in pending["options"] and cash >= int(items["draw"]["price"]) + reserve:
                if self._rng.random() < 0.6:
                    return "buy_draw"
            for key in ("gain", "loss"):
                option = f"buy_{key}"
                if option in pending["options"] and cash >= int(items[key]["price"]) + reserve:
                    if self._rng.random() < 0.6:
                        return option
            return "skip"
        options = pending["options"]
        cell = game.cells[pending["cell"]]
        price = game.current_price(cell)
        if "buy" in options:
            if self._should_be_merciful(game, policy):
                return "skip"
            if cash >= price + reserve:
                return "buy"
        if "upgrade" in options and cash >= price // 2 + reserve // 2:
            if self._rng.random() < float(policy.get("upgrade") or 0.5):
                return "upgrade"
        return "skip"

    def _decision_note(self, game: Any, action: str) -> str:
        """她为什么这么决定——**写进对局记录**，这样解说有据可依，不必现编。

        自动模式下棋是插件算的，如果记录里只有"做了什么"、没有"为什么"，
        她被问到理由时就只能自己编，容易和实际决策对不上。
        """
        pending = getattr(game, "pending", None) or {}
        kind = str(pending.get("type") or "")
        policy = self._persona.get("policy") or {}
        cash = int(game.cash.get("sakura", 0))
        reserve = int(policy.get("reserve") or 0)
        if action == "buy":
            return f"现金 ¥{cash} 买得起，还留得住底金 ¥{reserve}"
        if action == "skip" and kind == "land":
            return f"现金 ¥{cash} 不够宽裕（想留 ¥{reserve} 备用），这次不买"
        if action == "upgrade":
            return f"现金够，升级后租金翻倍（升级意愿 {int(float(policy.get('upgrade') or 0) * 100)}%）"
        if kind == "route":
            ring = set(getattr(game, "main_path", None) or [])
            try:
                node = int(str(action))
            except ValueError:
                node = None
            if node is not None and ring and node not in ring:
                return "好奇心上来，拐进支街看看"
            return "留在主环继续往前走"
        if kind == "dice":
            return f"特殊骰子定在 {action} 点"
        if kind == "shop":
            if action == "skip":
                return f"现金 ¥{cash} 想留着（底金 ¥{reserve}），这次不进商店"
            return f"现金 ¥{cash} 买得起，顺手买一件（底金 ¥{reserve}）"
        return "按当前的局势和她自己的性格"

    def gomoku_aggression(self) -> tuple[float, str]:
        """五子棋该用多大的进攻倾向（0–1）＋它的来源说明。

        默认**跟随角色卡**：和大富翁的"打法性格"同源——好胜的更爱做杀、谨慎的先拆招、心软的不会太狠
        （见 persona.gomoku_aggression）。在设置里关掉跟随、或在网页上点了「稳健/均衡/进攻」，
        就用手动值（原来的那个 0–100 滑块）。
        """
        values = self._config.get()
        manual = _int_setting(values, "gomoku_style", 45)
        if not values.get("gomoku_style_follow_card", True):
            return max(0.0, min(1.0, manual / 100.0)), f"手动值 {manual}"
        style = self._persona.get("gomoku") or {}
        derived = style.get("aggression")
        if derived is None:
            return max(0.0, min(1.0, manual / 100.0)), f"手动值 {manual}"
        return max(0.0, min(1.0, float(derived) / 100.0)), f"角色卡推出 {int(derived)}"

    def _pick_route(self, game: Any, options: list[Any], policy: dict[str, Any],
                    traits: dict[str, Any]) -> Any:
        """岔路选向：走主环还是拐支街，看她的好奇心和好胜心。"""
        ring = set(getattr(game, "main_path", None) or [])
        branch = float(policy.get("branch") or 0.3)
        ambition = float(traits.get("ambition") or 0.5)

        def district_pull(node: int) -> float:
            """她在这片街区已经有多少地——越多越想往里走（好胜心的体现）。"""
            group = (game.cells[node] or {}).get("group")
            if not group:
                return 0.0
            mine = sum(1 for c in game.cells
                       if c.get("group") == group and c.get("owner") == "sakura")
            return min(1.0, mine / 3.0)

        best, best_score = options[0], -1.0
        for node in options:
            on_ring = (node in ring) if ring else True
            score = (1.0 - branch) if on_ring else branch
            score += 0.8 * ambition * district_pull(node)
            score += self._rng.random() * 0.4          # 留点随机，别像机器
            if score > best_score:
                best, best_score = node, score
        return best

    def _should_be_merciful(self, game: Any, policy: dict[str, Any]) -> bool:
        """温柔的角色在明显领先时，偶尔把一块无主地留给玩家。"""
        mercy = float(policy.get("mercy") or 0.0)
        if mercy <= 0:
            return False
        mine = self._net_worth(game, "sakura")
        theirs = self._net_worth(game, "user")
        if theirs <= 0 or mine < theirs * 1.25:       # 只在她领先 25% 以上时才心软
            return False
        return self._rng.random() < mercy

    def _net_worth(self, game: Any, player: str) -> float:
        props = [c for c in game.cells
                 if c["type"] == "property" and c["owner"] == player and not c["mortgaged"]]
        return game.cash[player] + sum(game.current_price(c) for c in props) * 0.6

    @serialized_action
    def sakura_action(self) -> dict[str, Any]:
        """让夜乃樱执行她的回合动作（引擎处理 + 触发聊天反应）。

        **一次点按走完整个回合**：掷骰后如果路上连续遇到岔路（或者落点又要买地），
        以前每遇到一个待决策就停下、要玩家再点一次；现在循环把所有属于她的决策都处理完，
        直到步数走完、回合交给玩家为止。
        以上只适用于显式algorithm；默认agent每次仅派发一个真实决策。
        """
        if self.decision_driver() == "agent":
            return self._agent_turns.request()
        if self._game is None:
            raise GameError("当前没有进行中的对局，先开局。")
        game = self._game
        if game.winner or getattr(game, "draw", False):
            raise GameError("对局已经结束，先开新一局吧。")

        if game.kind == "gomoku":
            if game.turn != "sakura":
                raise GameError(f"现在轮到玩家行动，不需要{PLAYER_LABEL['sakura']}动作。")
            result = self.place({"player": "sakura"})
            self._notify_sakura(result["status"])
            return {**result, "narrated": True}

        last: dict[str, Any] = {}
        roll_event: dict[str, Any] | None = None
        steps = 0
        black_train_choice_ready = bool(game.pending and game.pending.get('type') == 'black_train')
        while steps < 12:                      # 安全上限：正常一回合不会处理这么多次
            steps += 1
            if game.winner or getattr(game, "draw", False):
                break
            if game.pending:
                if game.pending.get("type") == "tower":
                    break
                if game.pending.get("type") == "black_train" and not black_train_choice_ready:
                    break
                if game.pending["player"] != "sakura":
                    break                      # 轮到她等玩家决定，交给玩家点
                choice = self._pick_pending_action(game)
                note = self._decision_note(game, choice)     # 决策前取：decide() 会把 pending 清掉
                last = self.decide({"action": choice, "player": "sakura", "_automatic": True})
                # 把"为什么这么定"写进记录：解说与事后解释都以它为准，免得她现编一套
                self._touch(None, f"她的判断：{note}")
                continue
            if game._walk is not None:         # 还有步数没走完（理论上引擎会自动推进）
                last = self.state()
                continue
            if game.turn != "sakura":
                break
            last = self.roll({"player": "sakura", "_automatic": True})
            # 记下她的掷骰事件：后续岔路选择会覆盖 lastEvent（且不含 dice），
            # 不合并的话网页上她的骰子点数就不显示了
            if self._last_event and self._last_event.get("dice"):
                roll_event = dict(self._last_event)
            if game.pending or game._walk is not None:
                continue                       # 还有她的决策/步数，继续处理
            break

        status = last.get("status") or f"{PLAYER_LABEL['sakura']}的回合已结束。"
        # 把骰子点数合并回最终的移动事件：一次 act_sakura 里"掷骰+选路"是原子的，
        # 网页只会看到最后一个 lastEvent——它必须同时带 dice（弹骰子动画）和 path（播移动）
        if roll_event and self._last_event and self._last_event.get("kind") == "move":
            self._last_event = {**self._last_event, "dice": roll_event["dice"]}
        elif roll_event:
            self._last_event = roll_event
        self._notify_sakura(status)
        return {**last, "status": status, "narrated": True}

    def _auto_decide(self, game: Any, player: str) -> str:
        """自动推进时替某一方做决定：她按性格（persona），玩家那一边用一套中性策略。

        玩家的策略只求"像个人在玩"：留一小笔现金就买地、钱够多才升级、抵押/赎回/卖掉一律不动手
        （那些是可选的，自动演出时不需要它替玩家做资产操作）。
        """
        pending = game.pending
        if player == "sakura":
            return self._pick_pending_action(game)
        options = list(pending.get("options") or [])
        pending_type = pending.get("type")
        if pending_type == "black_train":
            return str(pending["options"][0])
        if pending_type == "route":
            ring = set(getattr(game, "main_path", None) or [])
            forward = [node for node in options if node in ring]
            return str(forward[0] if forward else options[0])
        if pending_type == "dice":
            # 自动演出：玩家这边取中位数点数，稳一点
            sides = int(getattr(game, "dice_sides", 6) or 6)
            return str(max(1, (sides + 1) // 2))
        if pending_type == "shop":
            items = game_shop_items()
            cash = game.cash[player]
            if "buy_dice" in options and cash >= int(items["dice"]["price"]) + 400:
                return "buy_dice"
            return "skip"
        if not options:
            return "skip"
        cell = game.cells[pending["cell"]]
        price = game.current_price(cell)
        cash = game.cash[player]
        if "buy" in options and cash >= price + 120:
            return "buy"
        if "upgrade" in options and cash >= price // 2 + 250:
            return "upgrade"
        return "skip"

    @serialized_action
    def auto(self, arguments: Mapping[str, Any]) -> dict[str, Any]:
        """自动推进若干回合（双方各自按策略走），供"让她自己下一局"用。

        为什么要这个工具：模型调用工具并不可靠，而"陪玩"需要连续几十次掷骰与决策——
        让它连调几十次几乎必然中断。这里一次调用走完若干回合，把整段经过交回去让她解说，
        模型只要成功调**一次**就够了。网页上的「自动演出」走的也是这个方法（完全不经过模型）。

        `rounds` = 双方各走几次；有上限，并且按时间预算提前收工（工具回调有 15 秒上限）。
        """
        if self.decision_driver() == "agent":
            raise GameError("真实Agent模式不支持算法自动演出，请逐步请求Agent决策；算法模式需显式选择。")
        if self._game is None:
            raise GameError("当前没有进行中的对局，先开局。")
        game = self._game
        if game.winner or getattr(game, "draw", False):
            raise GameError("对局已经结束，先开新一局吧。")
        rounds = int(arguments.get("rounds") or 3)
        rounds = max(0, min(20, rounds))
        if rounds == 0:
            rounds = 1
        deadline = time.monotonic() + 8.0            # 留出余量给返回与解说
        before_seq = self._lines_written
        turns = {"user": 0, "sakura": 0}
        stop_reason = ""
        guard = 0
        while not (game.winner or getattr(game, "draw", False)):
            # 回合数够了就收工，但**要把当前这一个回合的决策处理完**再收
            # （否则会停在她的岔路/买地待决策上，网页看起来像"卡住了"）
            if max(turns.values()) >= rounds and (
                    game.kind == "gomoku" or (not game.pending and game._walk is None)):
                break
            guard += 1
            if guard > rounds * 12 + 40 or time.monotonic() > deadline:
                stop_reason = "时间用完了" if time.monotonic() > deadline else "步数异常，先停下"
                break
            if game.kind == "gomoku":
                actor = str(game.turn)
                if actor == "sakura":
                    self.place({"player": "sakura"})
                else:
                    # 玩家的落子要给出坐标：自动演出时用同一套 AI 代算（否则 place 会拒绝）
                    values = self._config.get()
                    level = int(values.get("gomoku_level") or 2)
                    aggression, _note = self.gomoku_aggression()
                    try:
                        from . import gomoku_ai
                    except ImportError:
                        import gomoku_ai
                    move = gomoku_ai.choose_move(game.board, game.code["user"], level, aggression)
                    self.place({"row": move["row"], "col": move["col"], "player": "user"})
                turns[actor] = turns.get(actor, 0) + 1
                continue
            if game.pending:
                if game.pending.get("type") == "tower":
                    stop_reason = "塔内流程暂停，等待逐层完成"
                    break
                actor = str(game.pending.get("player") or game.turn)
                try:
                    choice = self._auto_decide(game, actor)
                    note = self._decision_note(game, choice) if actor == "sakura" else ""
                    self.decide({"action": choice, "player": actor, "_automatic": True})
                    if note:
                        self._touch(None, f"她的判断：{note}")
                except GameError as error:          # 决策失败了就停在这一步，别让整次调用炸掉
                    stop_reason = f"有一处决策没走通（{error}）"
                    break
                continue
            if game._walk is not None:               # 还有步数没走完（下一次调用会接着走）
                stop_reason = "这一程还剩几步没走完"
                break
            actor = str(game.turn)
            turns[actor] = turns.get(actor, 0) + 1
            try:
                self.roll({"player": actor, "_automatic": True})
            except GameError as error:
                stop_reason = f"掷骰没能继续（{error}）"
                break

        picked = self._story_since(before_seq)
        summary = (f"自动推进了 {turns['user']} 个玩家回合 / {turns['sakura']} 个对手回合"
                   f"（共 {len(picked)} 条记录）")
        if game.winner:
            summary += f"，对局结束：{PLAYER_LABEL[game.winner]}获胜。"
        elif getattr(game, "draw", False):
            summary += "，棋盘已满，和棋，对局结束。"
        elif stop_reason:
            summary += f"，{stop_reason}。"
        self._notify_sakura(summary)
        return {
            "status": summary + " " + game.summary(),
            "story": picked,
            "turns": dict(turns),
            "finished": bool(game.winner or getattr(game, "draw", False)),
            "board": game.render(),
            "board_url": self.board_url,
            "narration_hint": "这些回合已经真的走完了（记录在 story 里）。用你的语气把过程讲给对方听，"
                              "重点讲你买下了什么、收了多少租金、踩到什么；不要再调用工具去重复这些回合。",
        }

    # ---- 她主动搭话（定时器）----------------------------------------

    PROACTIVE_TICK_SECONDS = 45
    PROACTIVE_PAGE_IDLE_SECONDS = 15        # 仅可见页面的短期心跳租约；服务启动/素材请求不算
    PROACTIVE_USER_IDLE_SECONDS = 45        # 用户刚在网页上操作过就先别说（他正在玩）

    def note_page_busy(self, busy: bool) -> None:
        """网页告诉服务端"现在正忙"（弹窗开着 / 用户刚点了东西）。"""
        self._page_busy = bool(busy)
        if busy:
            self._page_busy_at = time.time()

    def _user_is_busy(self) -> bool:
        """用户是不是正在这局里忙——忙的时候她不插嘴（照着 ST 那个模拟器扩展的礼貌规则做的）。"""
        if self._page_busy:
            return True
        now = time.time()
        if now - float(self._busy_at or 0.0) < self.PROACTIVE_USER_IDLE_SECONDS:
            return True        # 刚点过骰子/选项：让他看完这一步再说
        return False

    def start_proactive(self) -> None:
        """起一个后台定时器：对局进行中、且棋盘页面还开着时，隔一阵让她主动说一句。

        为什么要它：插件能把话递给她（mobile 通道），但**没有任何"到点唤醒"的能力**——
        没有它，她只会对棋盘上的操作做反应，绝不会自己开口。宿主的主动事件白名单
        （reminder_due / screen_awareness_check / update_available）只认壳递进来的事件，
        插件碰不到，所以"主动"只能由插件自己的定时器发起。宿主那侧打了气泡补丁后，
        这里说的话会真的变成气泡 + 语音。间隔为 0（设置里可改）时等于关闭。
        """
        if self._proactive_thread is not None:
            return
        # 把生效的配置记进日志：这一项出问题时最需要知道的就是"到底有没有开、间隔多少"
        values = self._config.get()
        self._logger.info("主动搭话定时器已启动", fields={
            "minutes": values.get("proactive_minutes", 2),
            "auto_narrate": bool(values.get("auto_narrate", True)),
            "tick_seconds": self.PROACTIVE_TICK_SECONDS,
        })

        def loop() -> None:
            while not self._proactive_stop.wait(self.PROACTIVE_TICK_SECONDS):
                try:
                    self.proactive_tick()
                except Exception:                      # 定时器绝不把宿主拖下水
                    pass

        self._proactive_thread = threading.Thread(target=loop, name="boardgame-proactive", daemon=True)
        self._proactive_thread.start()

    def stop_proactive(self) -> None:
        self._proactive_stop.set()
        thread, self._proactive_thread = self._proactive_thread, None
        if thread is not None:
            thread.join(timeout=2)

    def proactive_tick(self) -> bool:
        """到点就让她说一句。返回是否真的说了（测试直接调这个，不依赖线程时序）。"""
        # 默认 5 分钟（缺键时也要生效）；用户填 0 表示关掉
        minutes = float(self._config.get().get("proactive_minutes", 2) or 0)
        if minutes <= 0:
            return False
        with self._action_lock:
            game = self._game
            if self._play_paused or game is None or game.winner or getattr(game, "draw", False):
                return False
            # 闲聊不能抢角色待行动的通道；只读原始 ticket，不刷新/作废 token。
            ticket = self._agent_turns.ticket
            pending = getattr(game, "pending", None)
            if ((ticket and ticket.get("status") in agent_turns.ACTIVE)
                    or game.turn == "sakura"
                    or (pending and pending.get("player") == "sakura")):
                return False
            if self._server is None:
                return False
            visible_at = float(getattr(self._server, "_last_visible_page", 0.0) or 0.0)
            idle = time.time() - visible_at
            if not visible_at or idle > self.PROACTIVE_PAGE_IDLE_SECONDS:  # 未打开/隐藏/失联不搭话
                return False
            if self._user_is_busy():                       # 用户正在操作/弹窗开着，让他先玩
                return False
            if time.time() - self._last_talk < minutes * 60:
                return False
            summary, move_count = game.summary(), game.move_count
        # 宿主调用必须在锁外；这里仅发送上面一致性读取的棋盘快照。
        # force=True：主动搭话**不受「对手自动反应」开关管**——那是"对操作做反应"的开关，
        # 而这里是"她自己想说话"，两个功能各有各的开关（这里关掉靠把分钟数填 0）。
        self._notify_sakura(
            f"已经 {int(minutes)} 分钟没动静了。看一眼棋盘：{summary}",
            force=True,
            prompt="现在没人在操作，你只是自己看了一眼棋盘。用一句话随口说说局面或者你在想什么即可，"
                   "不需要调用任何工具，也不要复述播报内容。")
        self._logger.info("她主动开口了", fields={"minutes": int(minutes),
                                                  "move_count": move_count})
        return True

    # ---- 悔棋（回到上一回合开始前）-----------------------------------

    @contextmanager
    def _snapshot(self, label: str | None):
        """动作前取快照，成功后才入栈；拒绝动作不能挤掉有上限的真实悔棋点。

        调用方持有 _action_lock。快照仅存内存，不改变存档格式。
        """
        snap = None
        if label is not None and self._game is not None:
            try:
                snap = {
                    "label": label,
                    "game": copy.deepcopy(self._game.to_dict()),
                    "log": list(self._log),
                    "lines": self._lines_written,
                }
            except Exception as error:                 # 快照失败不该影响正常下棋
                self._logger.warning("悔棋快照失败", fields={
                    "reason_code": "SNAPSHOT_FAILED", "error_type": type(error).__name__})
        yield
        if snap is not None:
            self._snapshots.append(snap)

    def can_undo(self) -> bool:
        return bool(self._snapshots) and self._game is not None

    @serialized_action
    def undo(self, _arguments: Mapping[str, Any] | None = None) -> dict[str, Any]:
        """悔棋：回到最近一次快照（= 上一个回合开始前）。"""
        if self._game is None:
            raise GameError("当前没有进行中的对局。")
        if not self._snapshots:
            raise GameError("没有可回退的记录了（快照只保留本进程最近的若干步）。")
        snap = self._snapshots.pop()
        game = restore_game(snap["game"])
        self._game = game
        self._log.clear()
        for line in snap["log"]:
            self._log.append(line)
        self._lines_written = int(snap.get("lines") or len(snap["log"]))
        self._last_event = None                        # 免得网页播上一次的动画
        self._version += 1
        self._save()
        self._touch(None, f"悔棋：回到了「{snap['label']}」的状态。")
        return {"status": f"已退回「{snap['label']}」。{game.summary()}",
                "board": game.render(), "board_url": self.board_url}

    def _notify_sakura(self, summary: str, force: bool = False, prompt: str = "") -> None:
        """通过手机通道让当前角色对局势做出反应。

        措辞要写清"这一步已经执行完了"：否则她只会解说、还可能说成"我这边打不开/没法操作"。
        实测光写"做出简短反应"她确实只说话不动手——所以这里明确告诉她不需要再调用工具。

        注意：宿主把这条通道设成**静默**的（`run_host_message(_publish_events=False)`），
        所以她的话会写进聊天记录，但**不会弹气泡、也没有语音**。试着改宿主把它打开会让 Core
        崩溃（实测 `[Errno 22] TRANSPORT_WRITE_FAILED`），所以这条路放弃了；
        想要她出声只能用宿主自带的「屏幕感知」。
        """
        transaction = getattr(self, "_action_transaction", None)
        if transaction is not None:
            transaction["outbox"].append((summary, force, prompt))
            return
        self._last_talk = time.time()
        values = self._config.get()
        # 「对手自动反应」这个开关只管"对棋盘操作做出反应"；她要自己开口是另一件事
        # （靠「她主动搭话」的分钟数控制，那边会传 force=True）
        if not force and not values.get("auto_narrate", True):
            return
        if self._context is None:
            return
        instruct = prompt or ("这一步操作已经由程序执行完毕，你不需要再调用任何工具；"
                              "请只用你的语气对局势说一句简短反应，不要复述播报内容。")
        try:
            character = self._context.get("sakura.host.character")
            mobile = self._context.get("sakura.host.mobile")
            if character is None or mobile is None:
                return
            current = character.current()
            mobile.begin(self._context.plugin_id, current["id"], f"（桌游播报：{summary} {instruct}）")
        except Exception as error:
            self._logger.warning("触发对手反应失败", fields={
                "reason_code": "SAKURA_NOTIFY_FAILED",
                "error_type": type(error).__name__,
            })

    # ---- 工具回调 ---------------------------------------------------

    @serialized_action
    def start(self, arguments: Mapping[str, Any], skip_open: bool = False) -> dict[str, Any]:
        kind = str(arguments.get("game") or "").strip()
        first = str(arguments.get("first") or "dice").strip()
        if kind == "monopoly" and first not in PLAYER_LABEL:
            first = "user"  # 大富翁在同一条地图上，先后手只影响谁先掷骰
        if first not in PLAYER_LABEL and first != "dice":
            raise GameError("first 只能是 user、sakura 或 dice。")
        values = self._config.get()
        map_kind = self._new_map_kind()
        # 主题：新一局按**设置与当前角色**算，不能看正在跑的那一局——它马上要被替换掉了，
        # 而它的主题是上一局的（实测踩过：换了角色又重开，地图还是旧世界）。
        # ⚠️ 例外（用户 2026-10-01 拍板）：**格子棋盘永远用默认主题（次元错位恋人）**——
        # 棋子用的都是次元错位恋人作品里的角色，地图/事件换成别的作品的世界观太割裂，
        # 也不再为格子棋盘做碧蓝航线那类主题适配。
        theme_id = self._theme_setting_target(str(values.get("theme") or "auto"))
        if kind == "monopoly" and map_kind == "grid":
            theme_id = "dimension_lovers"
        game = create_game(
            kind,
            first,
            dice_sides=int(values.get("dice_sides") or 6),
            map_size=28,  # 旧函数签名占位；两种固定地图均忽略规模
            map_kind=map_kind,
            theme_id=theme_id,
        )
        self._game = game
        self._play_paused = False
        self._last_event = None  # 新局不能继承旧移动；相同骰点/路径否则会被网页误判为已播过。
        self._game_seq = getattr(self, "_game_seq", 0) + 1   # 网页靠它认出"新的一局"（开局弹选棋子）
        self._snapshots.clear()                    # 新对局：旧的悔棋快照作废
        self._build_identity()                     # 换角色/换卡：名字与立绘先跟上
        self._persona = self._build_persona()      # 每局重读一次角色卡，改了卡就换棋风
        self._save()
        opening = f"新对局开始：{game.summary()} 先手：{PLAYER_LABEL[first if first in PLAYER_LABEL else game.turn]}。"
        self._touch(
            [f"—— 新对局开始：{game.kind}，先手 {PLAYER_LABEL[game.turn]} ——"],
            opening,
        )
        if game.kind == "gomoku":
            rolls = game.first_rolls
            dice_note = (f"先后手骰：玩家 {rolls['user']} 点，{PLAYER_LABEL['sakura']} {rolls['sakura']} 点 —— "
                         f"{PLAYER_LABEL[game.turn]}先手。")
            self._touch([dice_note], None)
            self._last_event = {"kind": "first_roll", "user": rolls["user"], "sakura": rolls["sakura"],
                                "first": game.turn}
            opening += " " + dice_note
        self._logger.info("新对局已开始", fields={"game": game.kind, "first": game.turn,
                                                   "theme": getattr(game, "theme", ""),
                                                   "character_id": self._opponent.get("id", "")})
        board_url = self.board_url
        want_open = bool(not skip_open and values.get("auto_open_board", True))
        opened = self.open_in_browser(board_url) if (want_open and board_url) else False
        if board_url:
            # 把地址写进返回文本：即使系统没弹出浏览器，旦那さま也能直接点/复制这个链接
            if opened:
                hint = "已自动打开"
            elif want_open:
                hint = "没能自动打开，请把下面这个地址复制到浏览器"
            else:
                hint = "已关闭自动打开，请复制到浏览器"
            opening += f"\n棋盘页面（{hint}）：{board_url}"
        return {
            "active": True,
            "game": game.kind,
            "first": game.turn,
            "status": opening,
            "rules": rules_text(game),
            "board": game.render(),
            "board_url": board_url,
            "narration_hint": "用你的角色语气开场：宣布开局、说明先手（五子棋是先掷骰比大小决定的），并告诉对方网页棋盘已经打开。",
        }

    @serialized_action
    def state(self, _arguments: Mapping[str, Any]) -> dict[str, Any]:
        if self._game is None:
            return {
                "active": False,
                "playPaused": self._play_paused,
                "status": "当前没有进行中的对局。可以先问用户想玩什么，再调用 boardgame_start。",
                "board_url": self.board_url,
            }
        game = self._game
        result: dict[str, Any] = {
            "active": True,
            "playPaused": self._play_paused,
            "game": game.kind,
            "status": game.summary(),
            "winner": game.winner,
            "draw": bool(getattr(game, "draw", False)),
            "finished": bool(game.winner or getattr(game, "draw", False)),
            "board": game.render(),
            "board_url": self.board_url,
        }
        if game.kind == "gomoku" and not (game.winner or game.draw):
            threats = game.threat_text()
            if threats:
                result["threats"] = threats
        if game.kind == "monopoly":
            if game.pending:
                result["pending"] = game.pending
                result["status"] += f" 待决策：{PLAYER_LABEL[game.pending['player']]}——{game._pending_label()}，处理后才能继续。"
            if game._walk:
                result["status"] += f" {PLAYER_LABEL[game._walk['player']]}走格子中，还剩 {game._walk['steps_left']} 步。"
        return result

    @serialized_action
    def roll(self, arguments: Mapping[str, Any]) -> dict[str, Any]:
        player = str(arguments.get("player") or "").strip()
        if player not in PLAYER_LABEL:
            raise GameError("player 只能是 user 或 sakura。")
        self._guard_agent_seat(player)
        if self._game is None:
            raise GameError("当前没有进行中的对局，先调用 boardgame_start。")
        if self._game.kind != "monopoly":
            raise GameError("当前对局是五子棋，没有掷骰；落子请用 boardgame_place。")
        # 悔棋：在"这一回合开始掷骰"之前留一份快照（一次掷骰 = 一个回合的起点）
        label = (f"第 {self._game.dice_turn + 1} 回合·{PLAYER_LABEL[player]}掷骰前"
                 if player == self._game.turn and not self._game.pending else None)
        before = self._ledger_snapshot()
        with self._snapshot(label):
            result = self._game.roll(player, use_custom_die=arguments.get("use_custom_die") is True)
        description = "罚停一回合" if result.get("skipped") else (
            f"掷骰（{result['dice']} 点）" if result.get("dice") else "掷骰，等待特殊骰子选点")
        self._record_action(before, player, "roll", description, result,
                            automatic=bool(arguments.get("_automatic")))
        self._save()
        status = self._monopoly_status(result)
        if result.get("dice"):
            # path 供网页播放棋子逐格移动的动画
            self._last_event = {"kind": "move", "player": player, "dice": result["dice"],
                                "diceTurn": self._game.dice_turn,
                                "path": list(result.get("path") or [])}
        self._touch(result.get("story"), status)
        # 工具指令只放进工具结果的 hint 字段（给模型看），status/对局记录里保持纯播报
        hint = self._pending_hint(result)
        return {**result, "status": status, "board_url": self.board_url, **({"hint": hint} if hint else {})}

    @serialized_action
    def tower_action(self, arguments: Mapping[str, Any], finish: bool = False) -> dict[str, Any]:
        game = self._game
        if not game or game.kind != "monopoly" or not getattr(game, "_tower", None):
            raise GameError("当前没有塔内流程。")
        before = self._ledger_snapshot()
        player = game._tower["player"]
        self._guard_agent_seat(player)
        context = {"seq": game._tower["seq"], "cell": game._tower["cell"],
                   "floor": game._tower["at"] if finish else game._tower["at"] + 1}
        result = game.advance_tower(player, int(arguments.get("seq", -1)),
                                    int(arguments.get("floor", -2)), finish=finish)
        if not result.get("duplicate"):
            self._record_action(before, player, "tower_finish" if finish else "tower_step",
                                "确认离开塔" if finish else f"塔内第 {context['floor'] + 1} 层",
                                result, context, bool(arguments.get("_automatic")))
            self._save()
            self._last_event = {"kind": "tower", "player": result["player"],
                                "seq": game._tower["seq"], "floor": game._tower["at"]}
            self._touch(result["story"], " ".join(result["story"]))
        return {"ok": True, "status": " ".join(result["story"]), "tower": self.tower_payload(),
                "duplicate": bool(result.get("duplicate")), "version": self._version, "game": game.to_dict()}

    @serialized_action
    def decide(self, arguments: Mapping[str, Any]) -> dict[str, Any]:
        action = str(arguments.get("action") or "").strip()
        if not action:
            raise GameError("要给出决策内容。")
        if self._game is None:
            raise GameError("当前没有进行中的对局，先调用 boardgame_start。")
        if self._game.kind != "monopoly":
            raise GameError("当前对局没有地产决策或路线选择。")
        if self._game.pending is None:
            raise GameError("当前没有待决策，正常掷骰就行。")
        player = str(arguments.get("player") or self._game.pending["player"]).strip()
        self._guard_agent_seat(player)
        pending = copy.deepcopy(self._game.pending)
        before = self._ledger_snapshot()
        context = {"type": pending.get("type"), "choice": action}
        if "cell" in pending:
            context["cell"] = pending["cell"]
        record_action = "decide"
        description = _OPTION_LABELS.get(action, f"选择 {action}")
        if pending.get("type") == "route":
            description = f"选择路线 #{action}"
        elif pending.get("type") == "dice":
            description = f"特殊骰子选择 {action} 点"
        elif pending.get("type") == "tower":
            record_action = "tower_finish" if action == "tower_finish" else "tower_step"
            tower = self._game._tower
            context.update(seq=tower["seq"], cell=tower["cell"],
                           floor=tower["at"] if action == "tower_finish" else tower["at"] + 1)
            description = "确认离开塔" if action == "tower_finish" else f"塔内第 {context['floor'] + 1} 层"
        result = self._game.decide(player, action)
        if not result.get("duplicate"):
            self._record_action(before, player, record_action, description, result, context,
                                bool(arguments.get("_automatic")))
        self._save()
        status = "".join(result["story"])
        if result["winner"]:
            status += f"{PLAYER_LABEL[result['winner']]}获胜，对局结束！"
        elif result.get("next"):
            status += f"轮到 {PLAYER_LABEL[result['next']]}。"
        elif self._game._walk:
            status += f"还剩 {self._game._walk['steps_left']} 步没走完。"
        # 同一掷骰的连续选择可早于网页下一次poll；保留骰点及完整轨迹。
        if result.get("path") and len(result["path"]) >= 2:
            previous = self._last_event or {}
            dice = result.get("dice")
            if (dice is None and pending.get("type") == "route"
                    and previous.get("player") == player
                    and previous.get("diceTurn") == self._game.dice_turn):
                dice = previous.get("dice")
            self._last_event = {"kind": "move", "player": player,
                                "diceTurn": self._game.dice_turn, "path": list(result["path"])}
            if dice is not None:
                self._last_event["dice"] = dice
        self._touch(result["story"], status)
        hint = self._pending_hint(result)
        return {**result, "status": status, "board_url": self.board_url, **({"hint": hint} if hint else {})}

    @serialized_action
    def place(self, arguments: Mapping[str, Any]) -> dict[str, Any]:
        if self._game is None:
            raise GameError("当前没有进行中的五子棋对局，先调用 boardgame_start。")
        if self._game.kind != "gomoku":
            raise GameError("当前对局不是五子棋，落子工具用不上。")
        if self._game.winner or self._game.draw:
            raise GameError("对局已经结束，先开新一局吧。")
        player = str(arguments.get("player") or self._game.turn).strip()
        self._guard_agent_seat(player)
        row = arguments.get("row")
        col = arguments.get("col")
        # 悔棋：保存动作前状态，但只有引擎接受落子后才入栈。
        with self._snapshot(f"{PLAYER_LABEL.get(player, player)}落子前（第 {self._game.move_count + 1} 手）"):
            if row is None or col is None:
                if player != "sakura":
                    raise GameError(f"只有{PLAYER_LABEL['sakura']}的落子可以由 AI 代算；请给出 row/col。")
                values = self._config.get()
                level = int(values.get("gomoku_level") or 2)
                aggression, style_note = self.gomoku_aggression()
                result, reason = self._game.ai_move(player, level, aggression)
                status = (f"{PLAYER_LABEL['sakura']}落子 ({result['move'][0]},{result['move'][1]})"
                          f"——{reason}（棋风 {style_note}）。")
            else:
                result = self._game.place(player, int(row), int(col))
                status = f"{PLAYER_LABEL[result['player']]}落子 ({row},{col})。"
        if result["winner"]:
            status += f"{PLAYER_LABEL[result['winner']]}连成五子，对局结束！"
        elif result["draw"]:
            status += "棋盘已满，和棋，对局结束！"
        else:
            status += f"轮到 {PLAYER_LABEL[result['next']]}。"
        if result["threats"]:
            status += f"局势：{result['threats']}。"
        self._save()
        self._last_event = {"kind": "place", "player": result["player"], "move": result["move"]}
        self._touch([status], None)
        return {**result, "status": status, "board_url": self.board_url}

    @serialized_action
    def end(self, _arguments: Mapping[str, Any]) -> dict[str, Any]:
        if self._game is None:
            return {"status": "当前没有进行中的对局。"}
        summary = f"对局结束：{self._game.summary()} 共 {self._game.move_count} 手。"
        if self._game.winner:
            summary += f"赢家：{PLAYER_LABEL[self._game.winner]}。"
        elif getattr(self._game, "draw", False):
            summary += "结果：和棋。"
        self._game = None
        self._play_paused = False
        self._last_event = None
        transaction = getattr(self, "_action_transaction", None)
        if transaction is not None:
            transaction["delete"] = True
        else:
            self._delete_save_now()
        self._touch(None, summary + " 存档已清除。")
        self._logger.info("对局已手动结束")
        return {"status": summary + " 存档已清除，随时可以开始新一局。", "board_url": self.board_url}

    def _monopoly_status(self, result: dict[str, Any]) -> str:
        """写进**对局记录**（网页上那串播报）的文本，玩家会逐条读到。

        这里绝不能出现"调用 boardgame_decide(action=…)"这类**给模型的工具指令**：
        以前两种文本混在一句里，玩家就在对局记录里读到了工具调用要求。
        给模型的提示改由 `_pending_hint()` 单独返回，只进工具结果，不进对局记录。
        """
        parts = list(result.get("story") or [])
        if result.get("winner"):
            parts.append(f"{PLAYER_LABEL[result['winner']]}获胜，对局结束！")
        elif result.get("pending"):
            pending = result["pending"]
            actor = PLAYER_LABEL[pending["player"]]
            if pending.get("type") == "route":
                choices = "、".join(f"#{node}" for node in pending["options"])
                if pending["player"] == "user":
                    parts.append(f"岔路！请玩家在网页上选路线（可选：{choices}）。")
                else:
                    parts.append(f"岔路！{actor}要选路线（可选：{choices}）。")
                steps_left = result.get("steps_left")
                if steps_left is None and self._game and self._game._walk:
                    steps_left = self._game._walk["steps_left"]
                if steps_left is not None and steps_left > 0:
                    parts.append(f"还剩 {steps_left} 步没走完。")
            elif pending.get("type") == "dice":
                choices = "/".join(str(o) for o in pending["options"])
                parts.append(f"{actor}摸出特殊骰子，要点数（{choices}）。")
            elif pending.get("type") == "shop":
                choices = "、".join(_OPTION_LABELS.get(o, o) for o in pending["options"])
                parts.append(f"{actor}在商店里挑东西（{choices}）。")
            else:
                choices = "、".join(_OPTION_LABELS.get(o, o) for o in pending["options"])
                verb = ("等玩家拿主意" if pending["player"] == "user"
                        else f"轮到{PLAYER_LABEL['sakura']}拿主意")
                parts.append(f"{verb}：可选项（{choices}）。")
        elif not result.get("skipped") and result.get("next"):
            parts.append(f"轮到 {PLAYER_LABEL[result['next']]}。")
        elif self._game and self._game._walk:
            parts.append(f"还剩 {self._game._walk['steps_left']} 步没走完。")
        return "".join(parts)

    def _pending_hint(self, result: dict[str, Any]) -> str:
        """给模型的"下一步怎么做"提示：只在工具结果里，不进对局记录、不进播报。"""
        pending = result.get("pending")
        if not pending or pending.get("type") in ("route", "tower"):
            return ""
        choices = "、".join(_OPTION_LABELS.get(o, o) for o in pending["options"])
        if pending["player"] == "user":
            return (f"向玩家说明可选项（{choices}），让他在网页上点，或由他授权后调用 "
                    f"boardgame_decide(action=\"…\")。")
        return f"等她拿定主意后调用 boardgame_decide(action=\"…\", player=\"sakura\")。"

    # ---- Prompt 上下文 ----------------------------------------------

    def context_fragment(self) -> str:
        """完整只读快照；注册 builder 决定本轮是否需要注入。

        直接调用时即使没有对局也返回身份、规则和棋盘地址，
        保证用户明确问棋盘在哪时，不调用工具也能拿到真实地址。
        """
        url = self.board_url
        style = self.persona_text()
        lines = ["【权威身份】sakura=你（当前角色），user=用户；双方身份不可互换。"]
        chat_rule = ("普通聊天/复盘只读本上下文的现有权威快照，不为核实状态调用token工具"
                     "（boardgame_agent_state/boardgame_agent_commit）；信息不足就直说，不自动开新局。"
                     "仅当本次棋盘主动决策请求含有效token时才可使用这两个工具；"
                     "没有token不得调用，不填none/null，不猜测、不沿用旧token。")
        if self._game is None:
            return ("\n".join(lines + ["【权威状态】当前无对局，无可判定的胜负。", chat_rule]) + "\n"
                    f"【棋盘游戏】当前没有进行中的对局。棋盘页面地址：{url}\n"
                    "用户说想玩、来一局、开一局大富翁或五子棋时，调用 boardgame_start 开局；"
                    "如果他问棋盘在哪、或者你觉得没开起来，就把上面这个地址原样给他——"
                    "那个页面在浏览器里能直接打开，页面上也有「开一局」的按钮，不依赖工具也能玩。")
        game = self._game
        draw = bool(getattr(game, "draw", False))
        finished = bool(game.winner or draw)
        result = ("你赢了，用户输了。" if game.winner == "sakura" else
                  "用户赢了，你输了。" if game.winner == "user" else
                  "和棋，双方都没赢也没输。" if draw else "对局进行中，尚无胜负。")
        lines.append(f"【权威状态】winner={game.winner or 'null'}，draw={str(draw).lower()}；{result}")
        if game.kind == "gomoku":
            lines.append("五子棋坐标为1基：row=行（从上到下），col=列（从左到右），不可对调。")
            if game.last_move is not None:
                row, col = game.last_move
                lines.append(f"权威最后落子：第{row + 1}行、第{col + 1}列（row={row + 1}, col={col + 1}）。")
        lines += [chat_rule, f"【{'已结束的对局' if finished else '进行中的对局'}】{game.summary()}"]
        if self.decision_driver() == "agent":
            lines.append("当前为真实Agent驱动：sakura只可用主动请求的token调用boardgame_agent_state/commit，"
                         "每个token一次动作；不可调用旧工具或算法代走。没有token时等待棋盘派发，不自行生成。")
        if game.kind == "monopoly" and not game.winner:
            # 她的下棋性格（由角色卡推出）也要进上下文，否则解说会和她的落子对不上
            lines.append(f"你自己的打法性格：{style}。落子和场面解说都按这个性格来，不要忽冷忽热。")
        guide = self.guide_text(game.kind)
        if guide and not finished:
            # 桌游引导只在对局中注入：平时闲聊不需要"每步先解说再动作"这类要求
            lines.append(f"【桌游引导】{guide}")
        lines += [f"棋盘页面地址：{url}", game.render()]
        if game.kind == "gomoku" and not (game.winner or game.draw):
            threats = game.threat_text()
            if threats:
                lines.append(f"局势：{threats}。")
        recent = list(self._log)[-5:]
        if recent:
            lines.append("最近动态：" + " / ".join(recent))
        lines.append("上方的棋盘快照是权威状态，解说和落子决策以它为准，不要凭记忆复述棋盘。")
        return "\n".join(lines)


class BoardgamePlugin:
    def setup(self, context: Any) -> None:
        logger = context.get("sakura.host.logging")
        plugin_dir = Path(__file__).resolve().parent
        service = BoardgameService(context.config, context.data_path, logger, plugin_dir, context)

        def stop_server() -> None:
            try:
                service._server.stop()
            except Exception as error:
                logger.warning("棋盘服务器清理失败", fields={
                    "reason_code": "BOARD_SERVER_STOP_FAILED",
                    "error_type": type(error).__name__,
                })

        context.effect(stop_server)
        context.effect(service.stop_proactive)      # 主动搭话的定时器也要随插件 scope 停掉
        context.effect(service._agent_turns.close)   # 撤销token；不虚构宿主chat.cancel
        service.start_proactive()
        service.board_url  # 启动即监听，保证随时可打开
        # 主题包：先让数据目录里的主题文件有机会覆盖插件自带的那几份（用户可以自己改地名/事件卡）
        try:
            service._theme_module().install(context.data_path)
        except Exception as error:
            logger.warning("主题目录装载失败，改用插件自带的主题", fields={
                "reason_code": "THEME_INSTALL_FAILED", "error_type": type(error).__name__})
        logger.info("棋盘游戏插件已初始化", fields={"games": len(GAMES)})

        tools = context.get("sakura.host.tools")
        service_tool_names: list[str] = []

        def register(name: str, description: str, properties: dict[str, Any], required: list[str],
                     handler: Callable[[Mapping[str, Any]], dict[str, Any]]) -> None:
            tools.register(
                {
                    "name": name,
                    "description": description,
                    "parameters": {
                        "type": "object",
                        "properties": properties,
                        "required": required,
                    },
                    # Sakura exposes only default/plugin groups on the first Agent step.
                    # Token tools must be directly visible: this proactive turn cannot
                    # rely on the model discovering the boardgame group via search.
                    "group": "plugin" if name in {
                        "boardgame_agent_state", "boardgame_agent_commit"
                    } else "boardgame",
                    "risk": "low",
                },
                handler,
            )
            service_tool_names.append(name)

        register(
            "boardgame_agent_state",
            "仅当本次棋盘主动决策请求或commit返回的continuation含有效token时使用；普通聊天/复盘没有token不得调用，"
            "不填none/null，不猜测、不沿用旧token，不为核实状态调用。"
            "真实Agent取得本次token绑定的权威棋盘与唯一合法动作；必须在提交前读取。",
            {"token": {"type": "string", "description": "本次主动请求的一次性token"}},
            ["token"], service._agent_turns.state,
        )
        register(
            "boardgame_agent_commit",
            "仅当本次棋盘主动决策请求或commit返回的continuation含有效token时使用；普通聊天/复盘没有token不得调用，"
            "不填none/null，不猜测、不沿用旧token，不为核实状态调用。"
            "真实Agent执行本次token允许的一次棋步。只控制sakura，必须严格使用state的legalAction。"
            "place必须给row/col；decide的arguments.action必须是options之一，"
            '例如action="decide", arguments={"action":"buy_draw"}（仅当options含buy_draw），不是choice。'
            "roll不得指定骰点。成功结果有continuation时用新token继续state和commit，完成自己的待选分支后再短解说；"
            "没有continuation就停止，不自行开始下一次掷骰。失败/过期立即停止，不可改用旧工具或算法。相同token相同参数重复仅返回原结果。",
            {"token": {"type": "string"},
             "action": {"type": "string", "enum": ["roll", "decide", "place", "tower_step", "tower_finish"]},
             "arguments": {
                 "type": "object",
                 "description": "只填本次legalAction允许的字段：decide仅action；place仅row/col；"
                                "roll为空对象或仅use_custom_die；塔内动作仅seq/floor。不得附player或内部参数。",
                 "properties": {
                     "action": {"type": "string", "description": "decide必填：原样填写legalAction.options中的一个字符串，不是choice"},
                     "row": {"type": "integer", "minimum": 1, "maximum": 15, "description": "place必填：行号1–15"},
                     "col": {"type": "integer", "minimum": 1, "maximum": 15, "description": "place必填：列号1–15"},
                     "use_custom_die": {"type": "boolean", "description": "仅roll可选：是否使用已持有的自选骰道具，不指定骰点"},
                     "seq": {"type": "integer", "description": "塔内动作必填：原样复制legalAction.arguments.seq"},
                     "floor": {"type": "integer", "description": "塔内动作必填：原样复制legalAction.arguments.floor"},
                 },
                 "additionalProperties": False,
             }},
            ["token", "action", "arguments"], service._agent_turns.commit,
        )
        register(
            "boardgame_start",
            "开始一局双人游戏——**用户说想玩、来一局、开一局大富翁/五子棋时，必须调用本工具**；"
            "只在聊天里描述是开不了棋盘的（不调用就没有棋盘页面）。"
            "game=monopoly 是大富翁（掷骰走格、买地收租、事件卡，默认走第二横滨市城市地图），"
            "game=gomoku 是五子棋（开局双方各掷一次骰子决定先后手）。"
            "**返回里的 board_url 是棋盘地址，必须原样告诉用户**；返回文本里若写了「没能自动打开」，"
            "还要提醒用户把该地址复制到浏览器。开局后向用户介绍规则和先手。",
            {
                "game": {"type": "string", "enum": list(GAMES), "description": "游戏类型"},
                "first": {"type": "string", "enum": ["dice", "user", "sakura"],
                          "description": "先手：dice 表示由骰子决定（五子棋默认），也可显式指定 user 或 sakura"},
            },
            ["game"],
            service.start,
        )
        register(
            "boardgame_state",
            "查看当前对局的权威棋盘快照、轮次和棋盘地址（board_url）。忘记局面、怀疑记错、"
            "用户询问进度或想要棋盘地址时调用。",
            {},
            [],
            service.state,
        )
        register(
            "boardgame_roll",
            "大富翁掷骰：为指定玩家掷骰并自动走格子、结算事件格；遇岔路会暂停等待选路。"
            "引擎会拒绝不属于当前回合的玩家。",
            {"player": {"type": "string", "enum": ["user", "sakura"], "description": "本次掷骰的玩家"}},
            ["player"],
            service.roll,
        )
        register(
            "boardgame_place",
            "五子棋用户落子必须给坐标；真实Agent控制sakura时必须使用boardgame_agent_commit和本次token。"
            "仅显式algorithm驱动允许旧算法不填row/col代算。",
            {
                "row": {"type": "integer", "minimum": 1, "maximum": 15, "description": "行号 1–15，不填则由 AI 代算（仅限对手一方）"},
                "col": {"type": "integer", "minimum": 1, "maximum": 15, "description": "列号 1–15，不填则由 AI 代算（仅限对手一方）"},
                "player": {"type": "string", "enum": ["user", "sakura"], "description": "可省略，默认当前轮次玩家"},
            },
            [],
            service.place,
        )
        register(
            "boardgame_decide",
            "大富翁决策：岔路选路时 action 填目标格编号（如 \"14\"）；地产菜单时填 buy/upgrade/mortgage/redeem/sell/skip。"
            "引擎会在返回值里给出当前可选项。",
            {
                "action": {"type": "string", "description": "路线目标格编号，或地产决策 buy/upgrade/mortgage/redeem/sell/skip"},
                "player": {"type": "string", "enum": ["user", "sakura"], "description": "可省略，默认待决策的玩家"},
            },
            ["action"],
            service.decide,
        )
        register(
            "boardgame_auto",
            "自动推进若干回合：双方都按各自策略把这段对局真的走完（不是模拟），"
            "返回 story（这一段的经过）与建议的解说方式。用户说「你们自己下一局」「你来陪她玩」"
            "「自动走几回合」时用它——**一次调用顶几十次掷骰与决策**，比你反复调 boardgame_roll 可靠得多。"
            "回来之后照 story 解说即可，不要再重复调用工具走同样的回合。",
            {
                "rounds": {"type": "integer", "minimum": 1, "maximum": 15,
                           "description": "双方各走几回合（默认 3；想一次看完一局可以给 10–15）"},
            },
            [],
            service.auto,
        )
        register(
            "boardgame_undo",
            "悔棋：退回上一个回合开始前（大富翁）或撤回一手（五子棋）。"
            "用户说「刚才那步不算」「悔棋」「退回去」时用它。只能退本进程最近的若干步。",
            {},
            [],
            service.undo,
        )
        register(
            "boardgame_end",
            "结束并清除当前对局存档。用户喊停或对局结束后想重开时调用。",
            {},
            [],
            service.end,
        )

        context_host = context.get("sakura.host.context")

        def build_context(request: Mapping[str, Any]) -> list[dict[str, Any]]:
            # 只看本次输入，不由旧存档、页面可见性、历史消息或事件类型续开话题。
            # 这是事实注入门控，不限制角色在闲聊里自然提起过去的经历。
            current = request.get("current_input")
            if not isinstance(current, str) or not current.strip():
                return []
            current = current.strip()
            plugin_request = current.startswith((
                "（桌游播报：", "【棋盘插件主动决策请求，不是用户消息】",
            ))
            board_topic = any(word in current for word in (
                "桌游", "棋盘", "五子棋", "大富翁", "黑列车", "掷骰", "骰子", "票根",
                "下棋", "走棋", "棋局", "棋子", "棋谱", "落子", "悔棋", "对弈",
            )) or re.search(
                r"\b(?:board\s*games?|gomoku|five[ -]in[ -]a[ -]row|monopoly|"
                r"black\s+train|ticket\s+stubs?|dice)\b", current, re.IGNORECASE,
            ) is not None
            if not (plugin_request or board_topic):
                return []
            content = service.context_fragment()
            if not content:
                return []
            return [{
                "id": _FRAGMENT_ID,
                "content": content,
                "priority": 75,
                "budgetHint": 2400,          # 引导文本也在这段里，1000 会不够
                "sensitivity": "public",
            }]

        context_host.register(
            {
                "providerId": "local.sakura.boardgame.state",
                "description": "仅当前输入明确涉及桌游或本插件主动请求时，提供完整权威棋盘快照。",
                "order": 70,
                "enabled": True,
            },
            build_context,
        )

        settings = context.get("sakura.host.settings")
        settings.register(
            {
                "sectionId": "boardgame",
                "title": "棋盘游戏",
                "order": 100,
                "fields": [
                    {
                        "key": "decision_driver", "label": "对手决策驱动", "type": "select", "default": "agent",
                        "options": [{"label": "角色 Agent", "value": "agent"},
                                    {"label": "本地算法（备用）", "value": "algorithm"}],
                        "description": "Agent 自主选择行动；失败或超时不会切换为算法。",
                    },
                    {
                        "key": "dice_sides",
                        "label": "骰子面数",
                        "type": "integer",
                        "minimum": 2,
                        "maximum": 20,
                        "default": 6,
                        "description": "大富翁使用的骰子面数，新对局生效。",
                    },
                    {
                        "key": "theme",
                        "label": "地图主题",
                        "type": "select",
                        "default": "auto",
                        "options": [{"label": "跟随角色", "value": "auto"}] + [
                            {"label": item["name"], "value": item["id"]}
                            for item in service._theme_module().available()
                        ],
                        "description": "决定地名和事件文案，新对局生效。",
                    },
                    {
                        "key": "map_kind",
                        "label": "地图来源",
                        "type": "select",
                        "default": "city",
                        "options": [
                            {"label": "格子棋盘", "value": "grid"},
                            {"label": "城市图", "value": "city"},
                        ],
                        "description": "新对局生效。",
                    },
                    {
                        "key": "piece_user",
                        "label": "玩家的棋子",
                        "type": "select",
                        "default": PIECE_DEFAULT["user"],
                        "options": [{"label": PIECE_LABELS[k], "value": k} for k in PIECES],
                        "description": "也可在棋盘侧栏更换。",
                    },
                    {
                        "key": "piece_sakura",
                        "label": "对手的棋子",
                        "type": "select",
                        "default": PIECE_DEFAULT["sakura"],
                        "options": [{"label": PIECE_LABELS[k], "value": k} for k in PIECES],
                        "description": "不能与玩家使用同一立绘。",
                    },
                    {
                        "key": "auto_open_board",
                        "label": "自动打开棋盘页面",
                        "type": "boolean",
                        "default": True,
                        "description": "开局时自动在浏览器中打开可视化棋盘。",
                    },
                    {
                        "key": "auto_mode",
                        "label": "自动请求对手行动",
                        "type": "select",
                        "default": "off",
                        "options": [
                            {"label": "关闭", "value": "off"},
                            {"label": "开启", "value": "hers"},
                        ],
                        "description": "棋盘页面打开时，轮到对手便按所选驱动请求行动；失败后需手动重试。",
                    },
                    {
                        "key": "proactive_minutes",
                        "label": "闲时搭话间隔（分钟）",
                        "type": "integer",
                        "minimum": 0,
                        "maximum": 120,
                        "default": 2,
                        "description": "0 为关闭。仅在对局页面打开且空闲时请求聊天，不推进棋步。",
                    },
                    {
                        "key": "auto_narrate",
                        "label": "操作后追加聊天反应",
                        "type": "boolean",
                        "default": True,
                        "description": "对已完成的操作追加聊天反应；不影响 Agent 决策。",
                    },
                ],
                "actions": [
                    {
                        "actionId": "openBoard",
                        "label": "打开棋盘页面",
                        "description": "在浏览器中打开可视化棋盘，可随时查看和操作当前对局。",
                        "danger": False,
                    }
                ],
            },
            load=lambda: dict(context.config.get()),
            save=service.save_settings,
            actions={"openBoard": lambda _values: {"message": service.open_board_message()}},
        )

        # ---- 聊天输入框「+」菜单：注册「打开棋盘」入口 --------------------
        # 宿主服务 sakura.host.ui.composer-tools-v0：register(plugin_id, descriptor, handle)。
        # descriptor 只允许 toolId/label/description/icon/order 五个键，
        # icon 只能取 camera/folder/globe/link/note/settings/sparkles/terminal。
        # 回调必须以 shape="ui.composer_tool.invoke" 注册，收到一个参数 {"source": "composer"}，
        # 返回 {"status": "completed", "message": "<=200字"}。
        def on_composer_invoke(_payload: Mapping[str, Any]) -> dict[str, str]:
            """点「+」菜单里的「打开棋盘」：开一局（如果没有）并打开浏览器。"""
            url = service.board_url
            if service._game is None:
                result = service.start({"game": "monopoly"}, skip_open=True)
                url = service.board_url
                opening = (result.get("status") or "").splitlines()
                summary = opening[0] if opening else "已开一局大富翁"
            else:
                summary = "继续当前对局"
            opened = service.open_in_browser(url)
            note = f"已打开棋盘（{summary}）：{url}" if opened else \
                f"没能自动打开浏览器，请把这个地址复制到浏览器（{summary}）：{url}"
            return {"status": "completed", "message": note[:200]}

        def register_composer_tool() -> None:
            composer = context.get("sakura.host.ui.composer-tools-v0")
            if composer is None or composer is context:
                logger.info("宿主未提供「+」菜单扩展服务，跳过注册", fields={
                    "reason_code": "COMPOSER_TOOLS_UNAVAILABLE"})
                return
            try:
                handle, _ = context._register_callback("ui.composer_tool.invoke", on_composer_invoke)
                composer.register(context.plugin_id, {
                    "toolId": "open_board",
                    "label": "打开棋盘",
                    "description": "进入下棋模式：打开网页棋盘，没有对局就自动开一局",
                    "icon": "sparkles",
                    "order": 50.0,
                }, handle)
                logger.info("「打开棋盘」已注册进聊天输入框扩展菜单")
            except Exception as error:  # noqa: BLE001 — 扩展菜单注册失败不能拖垮插件
                logger.warning("注册「打开棋盘」扩展菜单失败", fields={
                    "reason_code": "COMPOSER_TOOL_REGISTER_FAILED",
                    "error_type": type(error).__name__,
                })

        register_composer_tool()

        # 注册完再写一行：这样日志里能分清"插件加载了"和"工具真的挂上了"
        logger.info("棋盘游戏工具已注册", fields={
            "tools": ", ".join(sorted(service_tool_names)),
            "board": service.board_url,
        })
