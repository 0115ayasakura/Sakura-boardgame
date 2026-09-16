from __future__ import annotations

import http.server
import json
import os
import random
import re
import threading
import time
from collections import deque
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


STATE_FILE = "state.json"
_FRAGMENT_ID = "local.sakura.boardgame.state"
LOG_LIMIT = 60
_ART_NAME_RE = re.compile(r"^(poi/)?[a-z0-9_]+\.(png|jpg|svg)$")   # poi/ 子目录放 AI 出的地标插图
_MAX_BODY = 16 * 1024

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
                  "redeem": "赎回", "sell": "卖掉", "skip": "跳过"}


class BoardServer:
    """本地棋盘服务器：/ 页面、/state 状态、/art/ 素材、POST /api/action 网页操作。

    带**闲时自动关闭**：一段时间没有任何请求就停掉监听（省一个线程和一个端口）；
    下次访问 board_url 时会按需重启，对使用者透明。
    """

    IDLE_SHUTDOWN_SECONDS = 30 * 60       # 闲置 30 分钟自动关（页面下次打开会自动重启）

    def __init__(self, service: "BoardgameService", plugin_dir: Path) -> None:
        self._service = service
        self._plugin_dir = plugin_dir
        self._httpd: http.server.ThreadingHTTPServer | None = None
        self._thread: threading.Thread | None = None
        self._watchdog: threading.Timer | None = None
        self._last_request = time.time()
        self.url = ""

    def start(self) -> str:
        if self._httpd is not None:
            self._touch()
            return self.url
        handler = _make_handler(self._service, self._plugin_dir)
        self._httpd = http.server.ThreadingHTTPServer(("127.0.0.1", 0), handler)
        port = self._httpd.server_address[1]
        self.url = f"http://127.0.0.1:{port}/"
        self._thread = threading.Thread(target=self._httpd.serve_forever, name="boardgame-board", daemon=True)
        self._thread.start()
        self._touch()
        return self.url

    def _touch(self) -> None:
        """记录"刚有活动"，并重置闲时关闭的倒计时。"""
        self._last_request = time.time()
        if self._watchdog is not None:
            self._watchdog.cancel()
            self._watchdog = None
        if self._httpd is not None:
            self._watchdog = threading.Timer(self.IDLE_SHUTDOWN_SECONDS, self._idle_check)
            self._watchdog.daemon = True
            self._watchdog.start()

    def _idle_check(self) -> None:
        if self._httpd is None:
            return
        idle = time.time() - self._last_request
        if idle >= self.IDLE_SHUTDOWN_SECONDS - 1:
            self.stop()
        else:
            self._touch()

    def stop(self) -> None:
        if self._watchdog is not None:
            self._watchdog.cancel()
            self._watchdog = None
        if self._httpd is not None:
            self._httpd.shutdown()
            self._httpd.server_close()
            self._httpd = None
        if self._thread is not None:
            self._thread.join(timeout=5)
            self._thread = None
        # 清掉旧地址：board_url 发现 url 为空才会按需重启（否则会拿到失效的端口）
        self.url = ""


def _make_handler(service: "BoardgameService", plugin_dir: Path):
    board_page = plugin_dir / "board.html"
    art_dir = plugin_dir / "art"
    content_types = {".png": "image/png", ".jpg": "image/jpeg", ".svg": "image/svg+xml",
                     ".html": "text/html; charset=utf-8"}

    class Handler(http.server.BaseHTTPRequestHandler):
        def do_GET(self) -> None:  # noqa: N802
            service._server._touch()          # 有请求 = 页面还开着，重置闲时关闭倒计时
            path = self.path.split("?", 1)[0]
            try:
                if path in ("/", "/index.html"):
                    self._send_file(board_page)
                elif path == "/state":
                    body = json.dumps(service.state_payload(), ensure_ascii=False).encode("utf-8")
                    self._send(200, body, "application/json; charset=utf-8")
                elif path == "/portrait":
                    # 当前角色的立绘（换角色后 URL 会带上新 id，不会吃到旧图）
                    target = service.portrait_file()
                    if target is None:
                        self._send(404, b"no portrait", "text/plain")
                    else:
                        self._send_file(target)
                elif path.startswith("/art/"):
                    name = path[len("/art/"):]
                    if not _ART_NAME_RE.match(name):
                        self._send(404, b"not found", "text/plain")
                        return
                    # /art/poi/xxx.png = AI 出的地标插图（放在 art/poi/ 子目录里）
                    self._send_file(art_dir / name)
                else:
                    self._send(404, b"not found", "text/plain")
            except OSError:
                self._send(500, b"internal error", "text/plain")

        def do_POST(self) -> None:  # noqa: N802
            service._server._touch()
            path = self.path.split("?", 1)[0]
            if path != "/api/action":
                self._send(404, b"not found", "text/plain")
                return
            try:
                length = int(self.headers.get("Content-Length") or 0)
                if length > _MAX_BODY:
                    self._send(413, b"too large", "text/plain")
                    return
                payload = json.loads(self.rfile.read(length).decode("utf-8")) if length else {}
                action = str(payload.get("action") or "")
                arguments = payload.get("arguments") or {}
                result = service.handle_action(action, arguments if isinstance(arguments, dict) else {})
                body = json.dumps(result, ensure_ascii=False).encode("utf-8")
                self._send(200, body, "application/json; charset=utf-8")
            except (ValueError, json.JSONDecodeError) as error:
                body = json.dumps({"ok": False, "error": f"请求无效：{type(error).__name__}"}, ensure_ascii=False).encode("utf-8")
                self._send(400, body, "application/json; charset=utf-8")

        def _send_file(self, target: Path) -> None:
            suffix = target.suffix.lower()
            with target.open("rb") as handle:
                self._send(200, handle.read(), content_types.get(suffix, "application/octet-stream"))

        def _send(self, code: int, body: bytes, content_type: str) -> None:
            self.send_response(code)
            self.send_header("Content-Type", content_type)
            self.send_header("Content-Length", str(len(body)))
            self.send_header("Cache-Control", "no-store")
            self.end_headers()
            self.wfile.write(body)

        def log_message(self, format: str, *args: Any) -> None:  # 静默默认访问日志
            return

    return Handler


class BoardgameService:
    def __init__(self, config: Any, data_path: Callable[[str], Any], logger: Any,
                 plugin_dir: Path | None = None, context: Any = None) -> None:
        self._config = config
        self._data_path = data_path
        self._logger = logger
        self._context = context
        # 先备好日志/事件这些 _load 可能要用到的东西（作废旧存档时要在网页上说一句）
        self._version = 1
        self._log: deque[str] = deque(maxlen=LOG_LIMIT)
        self._last_event: dict[str, Any] | None = None
        self._game = self._load()
        self._server = BoardServer(self, plugin_dir) if plugin_dir else None
        self._rng = random.Random()
        self._opponent = {"id": "", "name": "", "portrait": ""}
        self._build_identity()          # 先定对手是谁（对局记录里的名字要用它）
        self._persona = self._build_persona()

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
            self._logger.info("夜乃樱的打法性格已确定", fields={
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

    # ---- 棋盘服务器 -------------------------------------------------

    @property
    def board_url(self) -> str:
        if self._server is None:
            return ""
        if not self._server.url:
            self._server.start()
        return self._server.url

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

    def _moves_map(self) -> dict[str, list[int]] | None:
        """每格可以走的方向。随机地图是无向图，方向没意义，就不下发。"""
        if not self._game or self._game.kind != "monopoly":
            return None
        if getattr(self._game, "map_kind", "random") != "city":
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
        """城市图的静态模板：没开局时网页也能渲染演示棋盘，而不是退化成网格。"""
        if str(self._config.get().get("map_kind") or "city") != "city":
            return None
        city_map = self._city_module()
        cells, edges, main_path, branches = city_map.build_city()
        return {"cells": cells, "edges": edges, "mainPath": main_path,
                "branches": branches, "decor": city_map.decor()}

    def state_payload(self) -> dict[str, Any]:
        values = self._config.get()
        level = int(values.get("gomoku_level") or 2)
        style = int(values.get("gomoku_style") or 45)
        return {
            "version": self._version,
            "game": self._game.to_dict() if self._game else None,
            "log": list(self._log),
            "lastEvent": self._last_event,
            "boardUrl": self._server.url if self._server else "",
            # 对手是谁：网页拿它显示名字、按钮文案与棋子头像（换角色自动跟随）
            "opponent": self.opponent_payload(),
            "cards": [
                {"title": card["title"], "flavor": card["flavor"],
                 "effect": _EFFECT_DESC.get(card["effect"]["type"], lambda e: "特殊效果")(card["effect"])}
                for card in engine.EVENT_CARDS
            ],
            "edges": self._game.edges if self._game and self._game.kind == "monopoly" else None,
            "mainPath": getattr(self._game, "main_path", None) if self._game and self._game.kind == "monopoly" else None,
            "branches": getattr(self._game, "branches", None) if self._game and self._game.kind == "monopoly" else None,
            "mapKind": getattr(self._game, "map_kind", "random") if self._game else None,
            "settingMapKind": str(self._config.get().get("map_kind") or "city"),
            "decor": self._decor(),
            # 每格允许前往的方向（城市图的主环是单向的，网页按它画金色箭头）
            "moves": self._moves_map(),
            # 只在没有城市图对局时才下发模板：有对局时装饰层已经随 decor 下发，不必重复
            "mapTemplate": None if (self._game and self._game.kind == "monopoly")
            else self._map_template(),
            "stats": self._game.stats() if self._game and self._game.kind == "monopoly" else None,
            "difficulty": level,
            "style": style,
        }

    def _touch(self, story: list[str] | None = None, status: str | None = None) -> None:
        self._version += 1
        if story:
            for entry in story:
                self._log.append(entry)
        if status:
            self._log.append(status)

    # ---- 存档 -------------------------------------------------------

    def _load(self) -> Any:
        path = self._data_path(STATE_FILE)
        if not path.exists():
            return None
        data: dict[str, Any] | None = None
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
            return restore_game(data)
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
        if self._game is None:
            return
        path = self._data_path(STATE_FILE)
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(self._game.to_dict(), ensure_ascii=False), encoding="utf-8")

    # ---- 网页操作入口 ------------------------------------------------

    def handle_action(self, action: str, arguments: Mapping[str, Any]) -> dict[str, Any]:
        try:
            # 冷启动兜底：网页上直接操作时如果还没有对局，就先自己开一局。
            # 这样"模型没调用工具"也不影响玩——网页自己就能开局。
            if self._game is None and action in ("roll", "decide", "act_sakura", "place"):
                kind = "gomoku" if action == "place" else "monopoly"
                self.start({"game": kind}, skip_open=True)
            if action == "start":
                result = self.start({"game": str(arguments.get("game") or "")}, skip_open=True)
                return {"ok": True, "status": result["status"]}
            if action == "roll":
                result = self.roll({"player": "user"})
                return {"ok": True, "status": result["status"], "board_url": self.board_url}
            if action == "decide":
                result = self.decide({"action": str(arguments.get("action") or "")})
                return {"ok": True, "status": result["status"], "board_url": self.board_url}
            if action == "place":
                result = self.place({"row": arguments.get("row"), "col": arguments.get("col")})
                return {"ok": True, "status": result["status"], "board_url": self.board_url}
            if action == "act_sakura":
                result = self.sakura_action()
                return {"ok": True, "status": result["status"], "narrated": result.get("narrated", False)}
            if action == "set_difficulty":
                level = int(arguments.get("level") or 2)
                if level not in (1, 2, 3):
                    raise GameError("难度只能是 1（入门）、2（进阶）、3（困难）。")
                self._config.update({"gomoku_level": level})
                self._version += 1
                return {"ok": True, "status": f"五子棋难度已设为 {level}。"}
            if action == "set_style":
                style = int(arguments.get("style") or 45)
                if not 0 <= style <= 100:
                    raise GameError("棋风数值要在 0–100 之间（0 最稳健，100 最进攻）。")
                self._config.update({"gomoku_style": style})
                self._version += 1
                return {"ok": True, "status": f"夜乃樱的棋风已调整为 {style}。"}
            if action == "set_map_kind":
                kind = str(arguments.get("kind") or "")
                if kind not in ("city", "random"):
                    raise GameError("地图来源只能是 city（第二横滨市）或 random（随机地图）。")
                self._config.update({"map_kind": kind})
                self._version += 1
                label = "第二横滨市" if kind == "city" else "随机地图"
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
        if pending.get("type") == "route":
            return str(self._pick_route(game, pending["options"], policy, traits))
        options = pending["options"]
        cell = game.cells[pending["cell"]]
        price = game.current_price(cell)
        reserve = int(policy.get("reserve") or 0)
        cash = game.cash["sakura"]
        if "buy" in options:
            if self._should_be_merciful(game, policy):
                return "skip"
            if cash >= price + reserve:
                return "buy"
        if "upgrade" in options and cash >= price // 2 + reserve // 2:
            if self._rng.random() < float(policy.get("upgrade") or 0.5):
                return "upgrade"
        return "skip"

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

    def sakura_action(self) -> dict[str, Any]:
        """让夜乃樱执行她的回合动作（引擎处理 + 触发聊天反应）。

        **一次点按走完整个回合**：掷骰后如果路上连续遇到岔路（或者落点又要买地），
        以前每遇到一个待决策就停下、要玩家再点一次；现在循环把所有属于她的决策都处理完，
        直到步数走完、回合交给玩家为止。
        """
        if self._game is None:
            raise GameError("当前没有进行中的对局，先开局。")
        game = self._game
        if game.winner:
            raise GameError("对局已经结束，先开新一局吧。")

        if game.kind == "gomoku":
            if game.turn != "sakura":
                raise GameError("现在轮到玩家行动，不需要夜乃樱动作。")
            result = self.place({"player": "sakura"})
            self._notify_sakura(result["status"])
            return {**result, "narrated": True}

        last: dict[str, Any] = {}
        roll_event: dict[str, Any] | None = None
        steps = 0
        while steps < 12:                      # 安全上限：正常一回合不会处理这么多次
            steps += 1
            if game.winner:
                break
            if game.pending:
                if game.pending["player"] != "sakura":
                    break                      # 轮到她等玩家决定，交给玩家点
                last = self.decide({"action": self._pick_pending_action(game), "player": "sakura"})
                continue
            if game._walk is not None:         # 还有步数没走完（理论上引擎会自动推进）
                last = self.state()
                continue
            if game.turn != "sakura":
                break
            last = self.roll({"player": "sakura"})
            # 记下她的掷骰事件：后续岔路选择会覆盖 lastEvent（且不含 dice），
            # 不合并的话网页上她的骰子点数就不显示了
            if self._last_event and self._last_event.get("dice"):
                roll_event = dict(self._last_event)
            if game.pending or game._walk is not None:
                continue                       # 还有她的决策/步数，继续处理
            break

        status = last.get("status") or "夜乃樱的回合已结束。"
        # 把骰子点数合并回最终的移动事件：一次 act_sakura 里"掷骰+选路"是原子的，
        # 网页只会看到最后一个 lastEvent——它必须同时带 dice（弹骰子动画）和 path（播移动）
        if roll_event and self._last_event and self._last_event.get("kind") == "move":
            self._last_event = {**self._last_event, "dice": roll_event["dice"]}
        elif roll_event:
            self._last_event = roll_event
        self._notify_sakura(status)
        return {**last, "status": status, "narrated": True}

    def _notify_sakura(self, summary: str) -> None:
        """通过手机通道让夜乃樱对局势做出反应（聊天记录可见；桌面气泡可能不实时弹出）。

        措辞要写清"这一步已经执行完了"：否则她只会解说、还可能说成"我这边打不开/没法操作"。
        实测光写"做出简短反应"她确实只说话不动手——所以这里明确告诉她不需要再调用工具。
        """
        values = self._config.get()
        if not values.get("auto_narrate", True) or self._context is None:
            return
        try:
            character = self._context.get("sakura.host.character")
            mobile = self._context.get("sakura.host.mobile")
            if character is None or mobile is None:
                return
            current = character.current()
            mobile.begin(self._context.plugin_id, current["id"],
                         f"（桌游播报：{summary} 这一步操作已经由程序执行完毕，你不需要再调用任何工具；"
                         f"请只用你的语气对局势说一句简短反应，不要复述播报内容。）")
        except Exception as error:
            self._logger.warning("触发夜乃樱反应失败", fields={
                "reason_code": "SAKURA_NOTIFY_FAILED",
                "error_type": type(error).__name__,
            })

    # ---- 工具回调 ---------------------------------------------------

    def start(self, arguments: Mapping[str, Any], skip_open: bool = False) -> dict[str, Any]:
        kind = str(arguments.get("game") or "").strip()
        first = str(arguments.get("first") or "dice").strip()
        if kind == "monopoly" and first not in PLAYER_LABEL:
            first = "user"  # 大富翁在同一条地图上，先后手只影响谁先掷骰
        if first not in PLAYER_LABEL and first != "dice":
            raise GameError("first 只能是 user、sakura 或 dice。")
        values = self._config.get()
        game = create_game(
            kind,
            first,
            dice_sides=int(values.get("dice_sides") or 6),
            map_size=int(values.get("track_length") or 28),
            map_kind=str(values.get("map_kind") or "city"),
        )
        self._game = game
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
            dice_note = (f"先后手骰：玩家 {rolls['user']} 点，夜乃樱 {rolls['sakura']} 点 —— "
                         f"{PLAYER_LABEL[game.turn]}先手。")
            self._touch([dice_note], None)
            self._last_event = {"kind": "first_roll", "user": rolls["user"], "sakura": rolls["sakura"],
                                "first": game.turn}
            opening += " " + dice_note
        self._logger.info("新对局已开始", fields={"game": game.kind, "first": game.turn})
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

    def state(self, _arguments: Mapping[str, Any]) -> dict[str, Any]:
        if self._game is None:
            return {
                "active": False,
                "status": "当前没有进行中的对局。可以先问用户想玩什么，再调用 boardgame_start。",
                "board_url": self.board_url,
            }
        game = self._game
        result: dict[str, Any] = {
            "active": True,
            "game": game.kind,
            "status": game.summary(),
            "board": game.render(),
            "board_url": self.board_url,
        }
        if game.kind == "gomoku" and not game.winner:
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

    def roll(self, arguments: Mapping[str, Any]) -> dict[str, Any]:
        player = str(arguments.get("player") or "").strip()
        if player not in PLAYER_LABEL:
            raise GameError("player 只能是 user 或 sakura。")
        if self._game is None:
            raise GameError("当前没有进行中的对局，先调用 boardgame_start。")
        if self._game.kind != "monopoly":
            raise GameError("当前对局是五子棋，没有掷骰；落子请用 boardgame_place。")
        result = self._game.roll(player)
        self._save()
        status = self._monopoly_status(result)
        if result.get("dice"):
            # path 供网页播放棋子逐格移动的动画
            self._last_event = {"kind": "move", "player": player, "dice": result["dice"],
                                "path": list(result.get("path") or [])}
        self._touch(result.get("story"), status)
        # 工具指令只放进工具结果的 hint 字段（给模型看），status/对局记录里保持纯播报
        hint = self._pending_hint(result)
        return {**result, "status": status, "board_url": self.board_url, **({"hint": hint} if hint else {})}

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
        result = self._game.decide(player, action)
        self._save()
        status = "".join(result["story"])
        if result["winner"]:
            status += f"{PLAYER_LABEL[result['winner']]}获胜，对局结束！"
        elif result.get("next"):
            status += f"轮到 {PLAYER_LABEL[result['next']]}。"
        elif self._game._walk:
            status += f"还剩 {self._game._walk['steps_left']} 步没走完。"
        # 岔路选向后的继续行走也要播动画：把走过的路径交给网页
        if result.get("path") and len(result["path"]) >= 2:
            self._last_event = {"kind": "move", "player": player, "path": list(result["path"])}
        self._touch(result["story"], status)
        hint = self._pending_hint(result)
        return {**result, "status": status, "board_url": self.board_url, **({"hint": hint} if hint else {})}

    def place(self, arguments: Mapping[str, Any]) -> dict[str, Any]:
        if self._game is None:
            raise GameError("当前没有进行中的五子棋对局，先调用 boardgame_start。")
        if self._game.kind != "gomoku":
            raise GameError("当前对局不是五子棋，落子工具用不上。")
        player = str(arguments.get("player") or self._game.turn).strip()
        row = arguments.get("row")
        col = arguments.get("col")
        if row is None or col is None:
            if player != "sakura":
                raise GameError("只有夜乃樱的落子可以由 AI 代算；请给出 row/col。")
            values = self._config.get()
            level = int(values.get("gomoku_level") or 2)
            style = int(values.get("gomoku_style") or 45)
            aggression = max(0.0, min(1.0, style / 100.0))
            result, reason = self._game.ai_move(player, level, aggression)
            status = f"夜乃樱落子 ({result['move'][0]},{result['move'][1]})——{reason}。"
        else:
            result = self._game.place(player, int(row), int(col))
            status = f"{PLAYER_LABEL[result['player']]}落子 ({row},{col})。"
        if result["winner"]:
            status += f"{PLAYER_LABEL[result['winner']]}连成五子，对局结束！"
        else:
            status += f"轮到 {PLAYER_LABEL[result['next']]}。"
        if result["threats"]:
            status += f"局势：{result['threats']}。"
        self._last_event = {"kind": "place", "player": result["player"], "move": result["move"]}
        self._touch([status], None)
        return {**result, "status": status, "board_url": self.board_url}

    def end(self, _arguments: Mapping[str, Any]) -> dict[str, Any]:
        if self._game is None:
            return {"status": "当前没有进行中的对局。"}
        summary = f"对局结束：{self._game.summary()} 共 {self._game.move_count} 手。"
        if self._game.winner:
            summary += f"赢家：{PLAYER_LABEL[self._game.winner]}。"
        self._game = None
        self._last_event = None
        path = self._data_path(STATE_FILE)
        if path.exists():
            path.unlink()
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
            else:
                choices = "、".join(_OPTION_LABELS.get(o, o) for o in pending["options"])
                verb = "等玩家拿主意" if pending["player"] == "user" else "轮到夜乃樱拿主意"
                parts.append(f"{verb}：可选项（{choices}）。")
        elif not result.get("skipped") and result.get("next"):
            parts.append(f"轮到 {PLAYER_LABEL[result['next']]}。")
        elif self._game and self._game._walk:
            parts.append(f"还剩 {self._game._walk['steps_left']} 步没走完。")
        return "".join(parts)

    def _pending_hint(self, result: dict[str, Any]) -> str:
        """给模型的"下一步怎么做"提示：只在工具结果里，不进对局记录、不进播报。"""
        pending = result.get("pending")
        if not pending or pending.get("type") == "route":
            return ""
        choices = "、".join(_OPTION_LABELS.get(o, o) for o in pending["options"])
        if pending["player"] == "user":
            return (f"向玩家说明可选项（{choices}），让他在网页上点，或由他授权后调用 "
                    f"boardgame_decide(action=\"…\")。")
        return f"等她拿定主意后调用 boardgame_decide(action=\"…\", player=\"sakura\")。"

    # ---- Prompt 上下文 ----------------------------------------------

    def context_fragment(self) -> str:
        """每轮注入的上下文。

        注意：**没有对局时也要返回内容**——必须把棋盘地址塞进她的上下文里。
        之前没对局就返回空字符串，结果"模型不调用工具"时她连地址都拿不到，
        只能凭想象说"棋盘应该已经打开了"。
        """
        url = self.board_url
        style = self.persona_text()
        if self._game is None:
            return (f"【棋盘游戏】当前没有进行中的对局。棋盘页面地址：{url}\n"
                    "用户说想玩、来一局、开一局大富翁或五子棋时，调用 boardgame_start 开局；"
                    "如果他问棋盘在哪、或者你觉得没开起来，就把上面这个地址原样给他——"
                    "那个页面在浏览器里能直接打开，页面上也有「开一局」的按钮，不依赖工具也能玩。")
        game = self._game
        lines = [f"【进行中的对局】{game.summary()}"]
        if game.kind == "monopoly" and not game.winner:
            # 她的下棋性格（由角色卡推出）也要进上下文，否则解说会和她的落子对不上
            lines.append(f"你自己的打法性格：{style}。落子和场面解说都按这个性格来，不要忽冷忽热。")
        lines += [f"棋盘页面地址：{url}", game.render()]
        if game.kind == "gomoku" and not game.winner:
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
        service.board_url  # 启动即监听，保证随时可打开
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
                    "group": "boardgame",
                    "risk": "low",
                },
                handler,
            )
            service_tool_names.append(name)

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
            "五子棋落子。轮到你时可以不填 row/col——AI 会按当前难度替你计算落子并给出理由；"
            "也可以自己给出坐标。用户落子时必须给坐标。",
            {
                "row": {"type": "integer", "minimum": 1, "maximum": 15, "description": "行号 1–15，不填则由 AI 代算（仅限夜乃樱）"},
                "col": {"type": "integer", "minimum": 1, "maximum": 15, "description": "列号 1–15，不填则由 AI 代算（仅限夜乃樱）"},
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
            "boardgame_end",
            "结束并清除当前对局存档。用户喊停或对局结束后想重开时调用。",
            {},
            [],
            service.end,
        )

        context_host = context.get("sakura.host.context")

        def build_context(_request: Mapping[str, Any]) -> list[dict[str, Any]]:
            content = service.context_fragment()
            if not content:
                return []
            return [{
                "id": _FRAGMENT_ID,
                "content": content,
                "priority": 75,
                "budgetHint": 1000,
                "sensitivity": "public",
            }]

        context_host.register(
            {
                "providerId": "local.sakura.boardgame.state",
                "description": "对局进行中时，把权威棋盘快照、轮次和最近动态注入 Prompt。",
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
                        "key": "dice_sides",
                        "label": "骰子面数",
                        "type": "integer",
                        "minimum": 2,
                        "maximum": 20,
                        "default": 6,
                        "description": "大富翁使用的骰子面数，新对局生效。",
                    },
                    {
                        "key": "play_style",
                        "label": "打法性格",
                        "type": "select",
                        "default": "card",
                        "options": [
                            {"label": "跟随角色卡", "value": "card"},
                            {"label": "稳健（留底金、不乱花钱）", "value": "steady"},
                            {"label": "进取（抢地、升级、爱垄断）", "value": "bold"},
                            {"label": "随性（爱拐支街逛）", "value": "casual"},
                            {"label": "温柔（领先时手下留情）", "value": "gentle"},
                        ],
                        "description": "默认读角色卡正文里的性格措辞来决定她的棋风（谨慎/好胜/好奇/温柔 → 留底金、升级意愿、拐支街倾向、领先时让不让地）。改了角色卡，下一局生效。",
                    },
                    {
                        "key": "map_kind",
                        "label": "地图来源",
                        "type": "select",
                        "default": "city",
                        "options": [
                            {"label": "第二横滨市（手工城市图）", "value": "city"},
                            {"label": "随机地图", "value": "random"},
                        ],
                        "description": "城市图是固定布局的 48 格街道网络；随机图每局不同，规模由下面的随机地图规模决定。",
                    },
                    {
                        "key": "track_length",
                        "label": "地图规模",
                        "type": "integer",
                        "minimum": 12,
                        "maximum": 96,
                        "default": 28,
                        "description": "大富翁岔路地图的格数（会取整到最接近的网格），新对局生效。",
                    },
                    {
                        "key": "gomoku_level",
                        "label": "五子棋难度",
                        "type": "integer",
                        "minimum": 1,
                        "maximum": 3,
                        "default": 2,
                        "description": "1=入门 2=进阶 3=困难；也可在网页上直接切换。",
                    },
                    {
                        "key": "gomoku_style",
                        "label": "五子棋棋风",
                        "type": "integer",
                        "minimum": 0,
                        "maximum": 100,
                        "default": 45,
                        "description": "0 最稳健（优先拆招），100 最进攻（优先做杀）。按角色性格调整，网页上也能改。",
                    },
                    {
                        "key": "auto_open_board",
                        "label": "自动打开棋盘页面",
                        "type": "boolean",
                        "default": True,
                        "description": "开局时自动在浏览器中打开可视化棋盘。",
                    },
                    {
                        "key": "auto_narrate",
                        "label": "夜乃樱自动反应",
                        "type": "boolean",
                        "default": True,
                        "description": "网页上点\"请夜乃樱行动\"时，通过手机聊天通道让她生成反应。",
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
            save=lambda values: {"applicationState": context.config.update(values)},
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
