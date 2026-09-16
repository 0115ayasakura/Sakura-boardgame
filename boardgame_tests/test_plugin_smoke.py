"""插件集成冒烟测试：工具注册、上下文、HTTP 服务器、网页 API、mobile 通道与清理。"""
from __future__ import annotations

import json
import pathlib
import sys
import tempfile
import urllib.error
import urllib.request

from pathlib import Path as _Path

ROOT = _Path(__file__).resolve().parents[1]      # 仓库根目录
sys.path.insert(0, str(ROOT / "sakura_boardgame"))

import plugin as plugin_module
import engine as engine_mod
from engine import GameError


class FakeConfig:
    def __init__(self) -> None:
        self.values = {"dice_sides": 6, "track_length": 16, "gomoku_level": 2, "gomoku_style": 45,
                       "auto_open_board": False, "auto_narrate": True}

    def get(self) -> dict:
        return dict(self.values)

    def update(self, values: dict) -> str:
        self.values.update(values)
        return "applied"


class FakeContext:
    MINI_CARD = ("夜乃桜是个冷静、克制的少女，凡事会先掂量代价再开口；她好胜、不服输，"
                 "输掉的事会记很久；但对你很温柔体贴，看见猫会停下脚步。")
    # 角色包（宿主的 resolve_resource 就指向这个目录）：用来测"名字/立绘跟随当前角色"
    TEST_CHARACTER_ID = "suisen_test"
    TEST_DISPLAY_NAME = "水仙"

    def __init__(self, root: pathlib.Path, character_package: pathlib.Path | None = None,
                 display_name: str | None = None) -> None:
        self.plugin_id = "local.sakura.boardgame"
        self.config = FakeConfig()
        self.root = root
        self.character_package = character_package
        self.display_name = display_name if display_name is not None else self.TEST_DISPLAY_NAME
        self.tools: dict[str, object] = {}
        self.context_contributions: list = []
        self.effects: list = []
        self.logs: list[str] = []
        self.mobile_calls: list[dict] = []

        self_ref = self

        class Logger:
            def __getattr__(self, name):
                def record(message, fields=None):
                    self_ref.logs.append(f"{name}: {message}")
                return record

        self._logger = Logger()

        class Tools:
            def register(self, descriptor, handler):
                self_ref.tools[descriptor["name"]] = handler

        class ContextHost:
            def register(self, descriptor, builder):
                self_ref.context_contributions.append((descriptor, builder))

        class Settings:
            def register(self, descriptor, load=None, save=None, actions=None):
                self_ref.settings_actions = actions or {}

        class Character:
            """模拟宿主的角色服务：current() 给 id + 角色卡正文，resolve_resource() 给角色包里的文件。"""

            def current(self):
                return {"id": self_ref.TEST_CHARACTER_ID, "systemPrompt": self_ref.MINI_CARD}

            def resolve_resource(self, character_id, relative_path):
                if self_ref.character_package is None:
                    raise ValueError("no package")
                if str(character_id) != self_ref.TEST_CHARACTER_ID:
                    raise ValueError("unknown character")
                target = self_ref.character_package / relative_path
                if not target.is_file():
                    raise ValueError("missing resource")
                return str(target)

        class Mobile:
            def begin(self, plugin_id, character_id, text, artifact=None):
                self_ref.mobile_calls.append({"plugin_id": plugin_id, "character_id": character_id, "text": text})
                return {"jobId": "job-1"}

        class ComposerTools:
            """模拟宿主的「+」菜单扩展服务：捕获注册，支持手动触发回调。"""

            def __init__(self):
                self_ref.composer_tools = {}

            def register(self, plugin_id, descriptor, handle):
                self_ref.composer_tools[descriptor["toolId"]] = {
                    "plugin_id": plugin_id, "descriptor": dict(descriptor), "handle": handle}

        class Callbacks:
            """模拟宿主的回调注册：句柄是字符串，调用走 invoke_callback（与真实宿主一致）。"""

            def __init__(self):
                self.count = 0
                self.bindings: dict[str, tuple] = {}

            def _register_callback(self, shape, callback):
                self.count += 1
                handle = f"cb_{self.count:032x}"
                self.bindings[handle] = (shape, callback)
                return handle, (lambda: None)

        self._services = {
            "sakura.host.logging": self._logger,
            "sakura.host.tools": Tools(),
            "sakura.host.context": ContextHost(),
            "sakura.host.settings": Settings(),
            "sakura.host.character": Character(),
            "sakura.host.mobile": Mobile(),
            "sakura.host.ui.composer-tools-v0": ComposerTools(),
            "_callbacks": Callbacks(),
        }

    def get(self, key):
        if key == "_callbacks":
            return self._services["_callbacks"]
        return self._services.get(key)

    def _register_callback(self, shape, callback):
        """真实宿主（SDK Context）有这个方法，插件注册「+」菜单回调要用它。"""
        return self._services["_callbacks"]._register_callback(shape, callback)

    def invoke_callback(self, handle, shape, args):
        """模拟宿主按句柄回调插件函数。"""
        shape_expected, callback = self._services["_callbacks"].bindings[handle]
        assert shape_expected == shape, (shape_expected, shape)
        return callback(*args)

    def data_path(self, relative):
        return self.root / relative

    def effect(self, fn):
        self.effects.append(fn)

    def teardown(self):
        for fn in reversed(self.effects):
            fn()


def http_get(url, expect_error=False):
    try:
        with urllib.request.urlopen(url, timeout=5) as response:
            return response.status, response.read()
    except urllib.error.HTTPError as error:
        return error.code, error.read() if expect_error else (_ for _ in ()).throw(error)
    except (urllib.error.URLError, OSError):
        return (None, b"") if expect_error else (_ for _ in ()).throw(AssertionError(f"请求失败 {url}"))


def http_post(url, payload):
    data = json.dumps(payload).encode("utf-8")
    request = urllib.request.Request(url, data=data, headers={"Content-Type": "application/json"}, method="POST")
    with urllib.request.urlopen(request, timeout=20) as response:
        return response.status, json.loads(response.read())


def main() -> None:
    with tempfile.TemporaryDirectory() as tmp:
        ctx = FakeContext(pathlib.Path(tmp) / "data")
        plugin_module.BoardgamePlugin().setup(ctx)

        # ---- 注册面 ----
        assert set(ctx.tools) == {
            "boardgame_start", "boardgame_state", "boardgame_roll",
            "boardgame_place", "boardgame_decide", "boardgame_end",
        }, ctx.tools.keys()
        assert "race" not in str(ctx.tools), "赛道骰子应已移除"
        assert len(ctx.context_contributions) == 1
        assert "openBoard" in ctx.settings_actions
        assert ctx.effects, "应登记服务器清理 effect"
        descriptor, builder = ctx.context_contributions[0]
        # 没开局时也要注入内容：必须把棋盘地址放进上下文，
        # 否则"模型不调用工具"时她拿不到地址，只能瞎说"棋盘应该已经打开了"
        # （放在「+」菜单测试之前——那条会真的开一局，冷启动文案就看不到了）
        cold = builder({})
        assert cold, "没对局时上下文不该是空的"
        cold_text = json.dumps(cold, ensure_ascii=False)
        assert "http://127.0.0.1:" in cold_text, cold_text[:200]
        assert "boardgame_start" in cold_text
        # 「+」菜单：注册了「打开棋盘」（触发行为放到最后的独立实例里测，
        # 因为点击会真的开一局，会污染本实例"未开局"的前置状态）
        assert "open_board" in ctx.composer_tools, ctx.composer_tools
        reg = ctx.composer_tools["open_board"]
        assert reg["plugin_id"] == "local.sakura.boardgame"
        assert reg["descriptor"]["label"] == "打开棋盘"
        assert reg["descriptor"]["icon"] in {"camera", "folder", "globe", "link",
                                            "note", "settings", "sparkles", "terminal"}

        # ---- 未开局 ----
        state0 = ctx.tools["boardgame_state"]({})
        assert state0["active"] is False and state0["board_url"].startswith("http://127.0.0.1:")
        base = state0["board_url"]
        try:
            ctx.tools["boardgame_roll"]({"player": "user"})
        except GameError as error:
            assert "没有进行中" in str(error)
        else:
            raise AssertionError("未开局掷骰应当报错")

        # ---- 静态资源 ----
        status, body = http_get(base)
        assert status == 200 and "桜のボードゲーム室".encode("utf-8") in body
        assert b"/api/action" in body
        status, body = http_get(base + "state")
        payload = json.loads(body)
        assert payload["game"] is None
        # 没开局也要能画出城市底图（模板 + 装饰层），网页才不会退化成方格演示棋盘
        assert payload["decor"] is None
        template = payload["mapTemplate"]
        assert template and len(template["cells"]) >= 50, template
        assert template["mainPath"] and len(template["branches"]) >= 10
        assert len(template["decor"]["buildings"]) > 100
        assert len(payload["cards"]) >= 24, len(payload["cards"])
        assert all("title" in card and "effect" in card for card in payload["cards"])
        status, body = http_get(base + "art/avatar_sakura.png")
        assert status == 200 and body[:4] == b"\x89PNG"
        status, body = http_get(base + "art/avatar_user.svg")
        assert status == 200 and b"<svg" in body
        assert http_get(base + "art/..%5Cstate.json", expect_error=True)[0] == 404
        assert http_get(base + "nope", expect_error=True)[0] == 404

        # ---- 通过网页 API 开一局大富翁 ----
        status, res = http_post(base + "api/action", {"action": "start", "arguments": {"game": "monopoly"}})
        assert status == 200 and res["ok"] is True, res
        payload = json.loads(http_get(base + "state")[1])
        assert payload["game"]["kind"] == "monopoly" and payload["edges"]
        assert payload["game"]["map_version"] == engine_mod.MAP_VERSION
        assert payload["game"]["map_kind"] == "city"          # 插件默认用城市地图
        assert payload["mainPath"] and payload["branches"]     # 主路与支线随载荷下发
        assert len(payload["game"]["cells"]) == payload["game"]["size"]
        # 单向主环：每格的可走方向随载荷下发，起点只能走学园正门（用户要求的行进方向规则）
        moves = payload["moves"]
        assert moves and len(moves) == payload["game"]["size"], len(moves or {})
        start = str(payload["game"]["start_node"])
        assert moves[start] == [payload["mainPath"][1]], (moves[start], payload["mainPath"][:2])
        assert payload["game"]["cells"][payload["mainPath"][1]]["name"] == "学园正门"
        assert all(moves[str(i)] for i in range(payload["game"]["size"])), "有格子走不出去"
        # 制图装饰层：街区多边形、水体、绿地、路网、建筑体块（网页底图全靠它）
        decor = payload["decor"]
        assert decor and len(decor["districts"]) == 4, decor
        assert len(decor["green"]) == 3 and decor["sea"] and decor["sea"]["poly"]
        assert decor["ponds"] and len(decor["roads"]) >= 10
        assert {r["role"] for r in decor["roads"]} == {"ring", "artery", "branch"}
        assert len(decor["buildings"]) > 100, len(decor.get("buildings") or [])
        # 街区色块必须贴住地图边框（至少有一条边落在边框上），不能缩进来在边上留白
        frame = decor["frame"]
        eps = 0.02
        for dist in decor["districts"]:
            xs = [p[0] for p in dist["poly"]]
            ys = [p[1] for p in dist["poly"]]
            on_frame = (min(xs) <= frame["x0"] + eps or max(xs) >= frame["x1"] - eps
                        or min(ys) <= frame["y0"] + eps or max(ys) >= frame["y1"] - eps)
            assert on_frame, f"{dist['key']} 没贴住地图边框"
        assert payload["mapTemplate"] is None                  # 有对局时不再重复下发模板

        # 玩家掷骰 → 自动走格；遇岔路或落点决策则处理
        steps = 0
        while True:
            payload = json.loads(http_get(base + "state")[1])
            game = payload["game"]
            if game["winner"]:
                break
            pending = game.get("pending")
            if pending:
                if pending["player"] != "user":
                    break
                action = str(pending["options"][0])
                status, res = http_post(base + "api/action", {"action": "decide", "arguments": {"action": action}})
            elif game.get("walk"):
                break
            elif game["turn"] == "user":
                status, res = http_post(base + "api/action", {"action": "roll"})
            else:
                break
            assert status == 200 and res["ok"] is True, res
            # 待决策时：工具指令只进 hint，status（会写进对局记录）里不许有
            if res.get("pending") and res["pending"].get("type") != "route":
                assert "boardgame" not in res["status"], res["status"]
                assert "boardgame_decide" in res.get("hint", ""), res
            steps += 1
            assert steps < 200, "网页操作无法推进对局"
        assert steps > 0, "至少应能通过网页推进一次"

        # 最后一次移动事件带路径（供页面播棋子移动动画）；
        # dice 仅在"掷骰产生移动"时存在——岔路选向后的继续行走没有骰子但有路径
        payload = json.loads(http_get(base + "state")[1])
        assert payload["lastEvent"], payload["lastEvent"]
        event = payload["lastEvent"]
        assert event["kind"] == "move", event
        assert event["path"] and len(event["path"]) >= 1, event
        if "dice" in event:
            assert 1 <= event["dice"] <= 12, event
        # 地产数量与租金收益统计
        stats = payload["stats"]
        assert set(stats) == {"user", "sakura"}, stats
        for side in ("user", "sakura"):
            assert "properties" in stats[side] and "income" in stats[side], stats
            assert stats[side]["properties"] >= 0 and stats[side]["income"] >= 0

        # ---- 对局记录里不许出现"给模型的工具指令" ----
        # 玩家会逐条读对局记录，工具调用说明是给模型看的，混进来就是脏数据
        for line in payload["log"]:
            assert "boardgame_" not in line, line
            assert "调用" not in line, line
            assert "action=" not in line, line

        # ---- 打法性格：从角色卡推出，并且真的改变她的决策 ----
        service = ctx.tools["boardgame_state"].__self__          # 绑定的处理方法 → 拿到服务对象
        assert service is not None
        traits = service._persona["traits"]
        assert traits["caution"] > 0.2, traits                    # 假卡里写了"冷静/克制/掂量"
        assert traits["ambition"] > 0.2, traits                   # "好胜/不服输"
        assert traits["kindness"] > 0.2, traits                   # "温柔/体贴"
        policy = service._persona["policy"]
        assert policy["reserve"] >= 120, policy
        assert "打法性格" in json.dumps(builder({}), ensure_ascii=False)

        # 留底金：现金刚好够买、但不够"价格 + 底金"时她会忍住；够了才买
        game = engine_mod.MonopolyGame(size=12, dice_sides=6, seed=3)
        prop = next(c for c in game.cells if c["type"] == "property")
        price = game.current_price(prop)
        reserve = int(policy["reserve"])
        game.pending = {"player": "sakura", "type": "land", "cell": prop["id"], "options": ["buy", "skip"]}
        game.cash["sakura"] = price + reserve - 10
        assert service._pick_pending_action(game) == "skip", (price, reserve)
        game.cash["sakura"] = price + reserve + 50
        assert service._pick_pending_action(game) == "buy"

        # 设置里换性格：进取的底金应当明显低于稳健
        ctx.config.update({"play_style": "bold"})
        bold = service._build_persona()["policy"]["reserve"]
        ctx.config.update({"play_style": "steady"})
        steady = service._build_persona()["policy"]["reserve"]
        assert bold < steady, (bold, steady)
        ctx.config.update({"play_style": "card"})
        assert service._build_persona()["policy"]["reserve"] == reserve

        # ---- 请夜乃樱行动（含 mobile 通道）----
        status, res = http_post(base + "api/action", {"action": "act_sakura"})
        assert status == 200 and res["ok"] is True, res
        assert ctx.mobile_calls, "应通过 mobile 通道触发夜乃樱反应"
        call = ctx.mobile_calls[-1]
        assert call["plugin_id"] == ctx.plugin_id and call["character_id"] == FakeContext.TEST_CHARACTER_ID
        assert "桌游播报" in call["text"]

        # 关掉自动反应后不再调用 mobile
        before = len(ctx.mobile_calls)
        ctx.config.update({"auto_narrate": False})
        http_post(base + "api/action", {"action": "act_sakura"})
        assert len(ctx.mobile_calls) == before

        # ---- 非法操作被拒绝且不影响状态 ----
        status, res = http_post(base + "api/action", {"action": "start", "arguments": {"game": "race"}})
        assert status == 200 and res["ok"] is False and "不支持" in res["error"]
        status, res = http_post(base + "api/action", {"action": "nope"})
        assert res["ok"] is False
        status, res = http_post(base + "api/action", {"action": "set_difficulty", "arguments": {"level": 9}})
        assert res["ok"] is False and "难度" in res["error"]

        # ---- 上下文片段 ----
        fragment = builder({})
        assert fragment and "【进行中的对局】" in fragment[0]["content"]
        assert "最近动态" in fragment[0]["content"]

        # ---- 五子棋：先后手骰 + 网页点击落子 + AI 代算 + 难度切换 ----
        status, res = http_post(base + "api/action", {"action": "start", "arguments": {"game": "gomoku"}})
        assert res["ok"] is True
        assert "先后手骰" in res["status"], res["status"]
        payload = json.loads(http_get(base + "state")[1])
        game = payload["game"]
        rolls = game["first_rolls"]
        assert 1 <= rolls["user"] <= 6 and 1 <= rolls["sakura"] <= 6
        assert game["first"] == ("user" if rolls["user"] > rolls["sakura"] else "sakura")
        assert game["turn"] == game["first"]
        assert payload["lastEvent"]["kind"] == "first_roll"
        first = game["turn"]
        if first == "user":
            status, res = http_post(base + "api/action", {"action": "place", "arguments": {"row": 8, "col": 8}})
            assert res["ok"] is True, res
            assert "玩家落子" in res["status"]
        status, res = http_post(base + "api/action", {"action": "act_sakura"})
        assert res["ok"] is True and "夜乃樱落子" in res["status"], res
        # 若接下来轮到玩家，再落一子，确保双方都有落子记录
        payload = json.loads(http_get(base + "state")[1])
        if payload["game"]["turn"] == "user":
            status, res = http_post(base + "api/action", {"action": "place", "arguments": {"row": 9, "col": 9}})
            assert res["ok"] is True, res
        payload = json.loads(http_get(base + "state")[1])
        assert payload["game"]["move_count"] >= 2, payload["game"]["move_count"]
        assert any("玩家落子" in line for line in payload["log"])
        assert any("夜乃樱落子" in line for line in payload["log"])
        # 难度切换
        status, res = http_post(base + "api/action", {"action": "set_difficulty", "arguments": {"level": 3}})
        assert res["ok"] is True and ctx.config.get()["gomoku_level"] == 3
        assert json.loads(http_get(base + "state")[1])["difficulty"] == 3
        # 棋风切换（影响 AI 进攻/防守倾向）
        status, res = http_post(base + "api/action", {"action": "set_style", "arguments": {"style": 80}})
        assert res["ok"] is True and ctx.config.get()["gomoku_style"] == 80
        assert json.loads(http_get(base + "state")[1])["style"] == 80
        status, res = http_post(base + "api/action", {"action": "set_style", "arguments": {"style": 200}})
        assert res["ok"] is False and "0–100" in res["error"]
        # 非法落子
        status, res = http_post(base + "api/action", {"action": "place", "arguments": {"row": 8, "col": 8}})
        assert res["ok"] is False
        ctx.tools["boardgame_end"]({})

        # ---- 存档恢复 ----
        ctx2 = FakeContext(pathlib.Path(tmp) / "data_restore")
        plugin_module.BoardgamePlugin().setup(ctx2)
        ctx2.tools["boardgame_start"]({"game": "monopoly"})
        base2 = ctx2.tools["boardgame_state"]({})["board_url"]
        http_post(base2 + "api/action", {"action": "roll"})
        snapshot = json.loads(http_get(base2 + "state")[1])["game"]
        ctx3 = FakeContext(pathlib.Path(tmp) / "data_restore")
        plugin_module.BoardgamePlugin().setup(ctx3)
        restored = json.loads(http_get(ctx3.tools["boardgame_state"]({})["board_url"] + "state")[1])["game"]
        assert restored["cash"] == snapshot["cash"]
        assert restored["edges"] == snapshot["edges"]
        assert restored["cells"] == snapshot["cells"]
        assert restored["map_version"] == engine_mod.MAP_VERSION

        # ---- 对手角色自动跟随：名字与立绘从角色包里读 ----
        pkg = pathlib.Path(tmp) / "char_pkg"
        (pkg / "portraits").mkdir(parents=True, exist_ok=True)
        (pkg / "character.json").write_text(json.dumps({
            "id": FakeContext.TEST_CHARACTER_ID,
            "display_name": FakeContext.TEST_DISPLAY_NAME,
            "card": "card.md",
            "portrait": {"default": "portraits/p7.png"},
        }, ensure_ascii=False), encoding="utf-8")
        (pkg / "card.md").write_text("冷静、克制的少女。", encoding="utf-8")
        png = bytes.fromhex("89504e470d0a1a0a0000000d49484452000000010000000108060000001f15c489"
                            "0000000a49444154789c6360000002000100ffff03000006000557bfabd40000000049454e44ae426082")
        (pkg / "portraits" / "p7.png").write_bytes(png)

        ctx_char = FakeContext(pathlib.Path(tmp) / "data_char", character_package=pkg)
        plugin_module.BoardgamePlugin().setup(ctx_char)
        char_base = ctx_char.tools["boardgame_state"]({})["board_url"]
        char_state = json.loads(http_get(char_base + "state")[1])
        assert char_state["opponent"]["name"] == FakeContext.TEST_DISPLAY_NAME, char_state["opponent"]
        assert char_state["opponent"]["portrait"].startswith("/portrait"), char_state["opponent"]
        # 立绘由 /portrait 端点吐出来（就是角色包里那张）
        status, body = http_get(char_base + char_state["opponent"]["portrait"])
        assert status == 200 and body == png, (status, body[:8])
        # 引擎的显示名也跟着换：对局记录里应当出现新名字而不是"夜乃樱"
        started = ctx_char.tools["boardgame_start"]({"game": "monopoly"})
        assert FakeContext.TEST_DISPLAY_NAME in started["status"], started["status"]
        assert engine_mod.PLAYER_LABEL["sakura"] == FakeContext.TEST_DISPLAY_NAME
        rolled = ctx_char.tools["boardgame_roll"]({"player": "user"})
        assert "夜乃樱" not in rolled["status"], rolled["status"]

        # 没有角色包时退回默认（夜乃樱 + 插件自带立绘）
        ctx_plain = FakeContext(pathlib.Path(tmp) / "data_char_plain")
        plugin_module.BoardgamePlugin().setup(ctx_plain)
        plain_state = json.loads(http_get(ctx_plain.tools["boardgame_state"]({})["board_url"] + "state")[1])
        assert plain_state["opponent"]["name"] == "夜乃樱", plain_state["opponent"]
        assert plain_state["opponent"]["portrait"] == "/art/avatar_sakura.png", plain_state["opponent"]
        assert engine_mod.PLAYER_LABEL["sakura"] == "夜乃樱"
        ctx_char.teardown()
        ctx_plain.teardown()

        # ---- 旧版存档被安全忽略，并且**网页上要留一句话**（否则看着像"新版本没生效"）----
        data_dir = pathlib.Path(tmp) / "data_old"
        data_dir.mkdir(parents=True, exist_ok=True)
        (data_dir / "state.json").write_text(
            json.dumps({"kind": "monopoly", "cells": [], "map_version": engine_mod.MAP_VERSION - 1}),
            encoding="utf-8")
        ctx4 = FakeContext(data_dir)
        plugin_module.BoardgamePlugin().setup(ctx4)
        assert ctx4.tools["boardgame_state"]({})["active"] is False
        old_state = json.loads(http_get(ctx4.tools["boardgame_state"]({})["board_url"] + "state")[1])
        assert old_state["game"] is None
        assert any("旧版本的对局存档" in line for line in old_state["log"]), old_state["log"]
        assert any("作废" in entry for entry in ctx4.logs), ctx4.logs

        # ---- 损坏存档被安全忽略 ----
        (data_dir / "state.json").write_text("{broken", encoding="utf-8")
        ctx5 = FakeContext(data_dir)
        plugin_module.BoardgamePlugin().setup(ctx5)
        assert ctx5.tools["boardgame_state"]({})["active"] is False

        # ---- 「+」菜单触发：点击「打开棋盘」应自动开一局并返回地址 ----
        ctx6 = FakeContext(pathlib.Path(tmp) / "data6")
        plugin_module.BoardgamePlugin().setup(ctx6)
        reg6 = ctx6.composer_tools["open_board"]
        result6 = ctx6.invoke_callback(reg6["handle"], "ui.composer_tool.invoke", {"source": "composer"})
        assert result6["status"] == "completed" and "http://127.0.0.1:" in result6["message"], result6
        assert ctx6.tools["boardgame_state"]({})["active"] is True, "点击后应已自动开一局"

        # ---- 服务器随插件 scope 停止 ----
        # 先把各实例的地址记下来（teardown 之后再调工具会把服务器按需重启——这是特性，
        # 闲时自动关闭后重新访问 board_url 会自动拉起来）
        urls = [c.tools["boardgame_state"]({})["board_url"] for c in
                [ctx, ctx2, ctx3, ctx4, ctx5, ctx6]]
        all_ctx = [ctx, ctx2, ctx3, ctx4, ctx5, ctx6]
        for c in all_ctx:
            c.teardown()
        for url in urls:
            assert http_get(url, expect_error=True)[0] is None, "服务器应在 teardown 后关闭"

    print("PLUGIN SMOKE TEST PASSED")


if __name__ == "__main__":
    main()
