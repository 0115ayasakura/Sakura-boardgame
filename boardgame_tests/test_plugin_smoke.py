"""插件集成冒烟测试：工具注册、上下文、HTTP 服务器、网页 API、mobile 通道与清理。"""
from __future__ import annotations

import json
import pathlib
import sys
import tempfile
import time
import urllib.error
import urllib.request

from pathlib import Path as _Path

ROOT = _Path(__file__).resolve().parents[1]      # 仓库根目录
sys.path.insert(0, str(ROOT / "sakura_boardgame"))

import plugin as plugin_module
import engine as engine_mod
import persona as persona_mod      # 棋风也是从角色卡推的，测试要能复算
from engine import GameError


class FakeConfig:
    def __init__(self) -> None:
        self.values = {"decision_driver": "algorithm", "dice_sides": 6, "track_length": 16, "gomoku_level": 2, "gomoku_style": 45,
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
        self.tool_descriptors: dict[str, dict] = {}
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
                self_ref.tool_descriptors[descriptor["name"]] = descriptor

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


# 回环地址不走代理：带 http_proxy 的终端里，urllib 会把 127.0.0.1 也交给代理，
# 于是"端口已关闭"会变成 502 Bad Gateway，测试就会误判"服务器没关"。
urllib.request.install_opener(urllib.request.build_opener(urllib.request.ProxyHandler({})))


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
    try:
        with urllib.request.urlopen(request, timeout=20) as response:
            return response.status, json.loads(response.read())
    except urllib.error.HTTPError as error:
        # Business rejections now use HTTP status semantics. Still validate the
        # JSON envelope; do not turn arbitrary server failures into success.
        if error.code not in (400, 409):
            raise
        result = json.loads(error.read())
        assert result.get("ok") is False and isinstance(result.get("error"), str), result
        return error.code, result


def main() -> None:
    with tempfile.TemporaryDirectory() as tmp:
        ctx = FakeContext(pathlib.Path(tmp) / "data")
        plugin_module.BoardgamePlugin().setup(ctx)

        # ---- 注册面 ----
        assert set(ctx.tools) == {
            "boardgame_start", "boardgame_state", "boardgame_roll", "boardgame_place",
            "boardgame_decide", "boardgame_auto", "boardgame_undo", "boardgame_end",
            "boardgame_agent_state", "boardgame_agent_commit",
        }, ctx.tools.keys()
        # Real Sakura starts each model turn with only default/plugin groups.
        # Registration alone is insufficient: token tools must survive that filter.
        initially_visible = {
            name for name, spec in ctx.tool_descriptors.items()
            if spec.get('group', 'default') in {'default', 'plugin'}
        }
        assert {'boardgame_agent_state', 'boardgame_agent_commit'} <= initially_visible
        assert 'boardgame_auto' not in initially_visible, 'do not expose an algorithm shortcut'
        assert "race" not in str(ctx.tools), "赛道骰子应已移除"
        assert len(ctx.context_contributions) == 1
        assert "openBoard" in ctx.settings_actions
        assert ctx.effects, "应登记服务器清理 effect"
        descriptor, builder = ctx.context_contributions[0]
        # 明确问棋盘时，即使没开局也要提供真实地址；无关闲聊不注入。
        # （放在「+」菜单测试之前——那条会真的开一局，冷启动文案就看不到了）
        cold = builder({'current_input': '请查看棋盘当前局面'})
        assert cold, "没对局时上下文不该是空的"
        cold_text = json.dumps(cold, ensure_ascii=False)
        assert "http://127.0.0.1:" in cold_text, cold_text[:200]
        assert "boardgame_start" in cold_text
        # 桌游引导只在有对局时注入：平时闲聊不该被塞"每步先解说再动作"这类要求
        assert "【桌游引导】" not in cold_text, cold_text[:300]
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
        assert status == 200 and "桌游室".encode("utf-8") in body
        # 页面上不该再有日文标题：主标题就是"<当前角色>的桌游室"（换角色后由 applyOpponent 改写）
        assert "桜のボードゲーム室".encode("utf-8") not in body, "旧的日文主标题应当已经删掉"
        assert b'/art/lib/board_connection.js' in body
        status, connection_js = http_get(base + 'art/lib/board_connection.js')
        assert status == 200 and b'/api/action' in connection_js
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
        # 也不许同一句话连着出现两次（status 是 story 的拼接 + 尾巴，两个都追加就会重复）
        for before, after in zip(payload["log"], payload["log"][1:]):
            assert before != after, f"对局记录出现重复行：{after}"

        # ---- 打法性格：从角色卡推出，并且真的改变她的决策 ----
        service = ctx.tools["boardgame_state"].__self__          # 绑定的处理方法 → 拿到服务对象
        assert service is not None
        traits = service._persona["traits"]
        assert traits["caution"] > 0.2, traits                    # 假卡里写了"冷静/克制/掂量"
        assert traits["ambition"] > 0.2, traits                   # "好胜/不服输"
        assert traits["kindness"] > 0.2, traits                   # "温柔/体贴"
        policy = service._persona["policy"]
        assert policy["reserve"] >= 120, policy
        assert "打法性格" in json.dumps(builder({'current_input': '请查看棋盘当前局面'}), ensure_ascii=False)

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

        # ---- 自动推进（boardgame_auto / 网页的「自动演出」）----
        # 目的：模型只调一次工具就能让对局真的走一段（它连调几十次是不可靠的）
        ctx.config.update({"auto_narrate": True})      # 上一节把它关掉了，这里要开着才能验播报
        before_log = len(json.loads(http_get(base + "state")[1])["log"])
        before_moves = json.loads(http_get(base + "state")[1])["game"]["move_count"]
        auto = ctx.tools["boardgame_auto"]({"rounds": 2})
        assert auto["turns"]["user"] >= 1 and auto["turns"]["sakura"] >= 1, auto["turns"]
        assert len(auto["story"]) >= 4, auto["story"]
        assert auto["board_url"].startswith("http://127.0.0.1:")
        assert "story" in auto and "narration_hint" in auto
        after = json.loads(http_get(base + "state")[1])
        assert after["game"]["move_count"] > before_moves, (before_moves, after["game"]["move_count"])
        assert len(after["log"]) > before_log
        # 自动推进也会走 mobile 通道让她解说
        assert ctx.mobile_calls and "自动推进" in ctx.mobile_calls[-1]["text"]
        # 网页上的「自动演出」走的是同一个方法（不经过模型）
        status, res = http_post(base + "api/action", {"action": "auto", "arguments": {"rounds": 1}})
        assert status == 200 and res["ok"] is True and res["story"], res
        # 对局结束后自动推进会拒绝（不能凭空续命）
        service._game.winner = "user"
        try:
            ctx.tools["boardgame_auto"]({"rounds": 1})
        except GameError as error:
            assert "已经结束" in str(error), str(error)
        else:
            raise AssertionError("对局结束后不该还能自动推进")
        service._game.winner = None

        # ---- 玩法说明的图例由插件按主题生成（网页不再写死另一个世界的词）----
        # 图例跟着"这局用哪套格子"走：这一局是默认的城市图 → 旧格图例
        legend = json.loads(http_get(base + "state")[1])["legend"]
        assert set(legend["cells"]) == {"start", "property", "tax", "bonus", "rest",
                                        "cat", "monster", "train", "navi",
                                        "device", "gate"}, legend["cells"]
        assert "¥1400" not in legend["cells"]["start"]        # 工资要用真值
        assert f"¥{engine_mod.MONOPOLY_PASS_START}" in legend["cells"]["start"], legend["cells"]["start"]
        assert f"¥{engine_mod.MONOPOLY_TAX}" in legend["cells"]["tax"], legend["cells"]["tax"]
        # 城市图/随机图不该出现六种新形式的图例词
        for new_form in ("shop", "coin", "game", "event", "end"):
            assert new_form not in legend["cells"], (new_form, legend["cells"])
        assert len(legend["districts"]) == 4 and all(d["color"].startswith("#") for d in legend["districts"])
        assert legend["naviName"] and legend["themeName"]
        state_payload = json.loads(http_get(base + "state")[1])
        assert state_payload["theme"]["naviLog"], state_payload["theme"]

        # ---- 立牌棋子：四个选项（图能取到）、默认金发 + 银白发、两位不能选同一个 ----
        pieces = json.loads(http_get(base + "state")[1])["pieces"]
        assert [o["key"] for o in pieces["options"]] == ["sophie", "sakura", "suisen", "katan"], pieces
        for option in pieces["options"]:
            assert option["label"], option
            assert option["url"] == f"/art/chibi/{option['key']}.png", option
            status, body = http_get(base + option["url"].lstrip("/"))
            assert status == 200 and body[:4] == b"\x89PNG", option["url"]
        assert (pieces["user"], pieces["sakura"]) == ("sophie", "sakura"), pieces
        # 她已经在用银白发，玩家再选银白发要被拒绝
        _, res = http_post(base + "api/action",
                           {"action": "set_piece", "arguments": {"player": "user", "key": "sakura"}})
        assert res["ok"] is False and "同一个" in res["error"], res
        _, res = http_post(base + "api/action",
                           {"action": "set_piece", "arguments": {"player": "user", "key": "suisen"}})
        assert res["ok"] is True, res
        pieces = json.loads(http_get(base + "state")[1])["pieces"]
        assert (pieces["user"], pieces["sakura"]) == ("suisen", "sakura"), pieces
        _, res = http_post(base + "api/action",
                           {"action": "set_piece", "arguments": {"player": "user", "key": "nope"}})
        assert res["ok"] is False, res
        # 换回默认，后面的用例不受影响
        http_post(base + "api/action",
                  {"action": "set_piece", "arguments": {"player": "user", "key": "sophie"}})

        # ---- 换成格子棋盘：图例要跟着变成六种新形式（商店/金币/游戏/事件）----
        ctx.config.update({"map_kind": "grid"})
        ctx.tools["boardgame_start"]({"game": "monopoly"})
        legend = json.loads(http_get(base + "state")[1])["legend"]
        # 格子棋盘上没有"棋盘中央的装置格"了：进塔走**支路上那座传送点**（(12,10)），
        # 所以图例里是 `tower` 而不是 `device`（用户 2026-09-23 纠正）。
        assert set(legend["cells"]) == {"start", "end", "property", "shop", "game",
                                        "coin", "event", "tower", "gate"}, legend["cells"]
        assert "箭头" in legend["cells"]["start"], legend["cells"]["start"]
        assert "对勾" in legend["cells"]["property"], legend["cells"]["property"]
        assert "特殊骰子" in legend["cells"]["shop"], legend["cells"]["shop"]
        assert f"¥{engine_mod.MONOPOLY_SHOP_DICE_PRICE}" in legend["cells"]["shop"], legend["cells"]["shop"]
        # 游戏格文案要把当前主题那几件特别事件列出来
        for event in engine_mod.game_events():
            assert event["name"] in legend["cells"]["game"], (event["name"], legend["cells"]["game"])
        for tier in engine_mod.coin_tiers():
            assert f"¥{tier}" in legend["cells"]["coin"], legend["cells"]["coin"]
        # 换回城市图，后面的用例还按默认地图走
        ctx.config.update({"map_kind": "city"})
        ctx.tools["boardgame_start"]({"game": "monopoly"})

        # ---- 她主动搭话：对局中 + 页面还开着 + 间隔到了，才说一句 ----
        # （宿主那侧的打补丁与否只影响"这句话会不会变成气泡/语音"，插件这边照发）
        ctx.config.update({"auto_narrate": True, "proactive_minutes": 5})
        service._game = None
        assert service.proactive_tick() is False, "没对局时不该自言自语"
        ctx.tools["boardgame_start"]({"game": "monopoly"})
        http_get(base + "state?visible=1&busy=0")                     # 模拟"网页刚刚还开着"
        service._last_talk = 0.0
        service._busy_at = 0.0                       # 用户已经闲下来了（不是刚点过）
        service.note_page_busy(False)
        before_talk = len(ctx.mobile_calls)
        assert service.proactive_tick() is True, "对局中 + 页面开着 + 间隔到了，应当主动说一句"
        assert len(ctx.mobile_calls) == before_talk + 1
        assert "看一眼棋盘" in ctx.mobile_calls[-1]["text"]
        # 刚说完就不再打扰
        assert service.proactive_tick() is False
        # 页面没人看时不说（闲置超过阈值）
        service._last_talk = 0.0
        service._server._last_visible_page = 0.0
        service._server._touch()
        assert service.proactive_tick() is False, "服务刚启动/有普通请求不能冒充页面在线"
        http_get(base + "state")
        assert service.proactive_tick() is False, "普通状态查询不能冒充可见棋盘"
        http_get(base + "state?visible=1&busy=0")
        http_get(base + "state?visible=0&busy=0")
        assert service.proactive_tick() is False, "隐藏棋盘不能触发主动搭话"
        service._server._last_visible_page = time.time() - 16
        assert service.proactive_tick() is False, "关闭/失联后心跳租约过期不得搭话"
        # 关掉（0 分钟）就彻底不说话
        http_get(base + "state?visible=1&busy=0")
        service._last_talk = 0.0
        ctx.config.update({"proactive_minutes": 0})
        assert service.proactive_tick() is False
        # **「对手自动反应」关着时她也要能自己开口**（实测踩过：把那个开关当成了总开关，
        # 于是用户关了"对操作做反应"，主动搭话也一起消失了）
        ctx.config.update({"proactive_minutes": 1, "auto_narrate": False})
        http_get(base + "state?visible=1&busy=0")
        service._last_talk = 0.0
        before_talk = len(ctx.mobile_calls)
        assert service.proactive_tick() is True, "主动搭话不该被「对手自动反应」挡住"
        assert len(ctx.mobile_calls) == before_talk + 1
        # 配置里没有这个键时按默认 5 分钟走（不能当成 0=关闭）
        ctx.config.values.pop("proactive_minutes", None)
        http_get(base + "state?visible=1&busy=0")
        service._last_talk = 0.0
        before_talk = len(ctx.mobile_calls)
        assert service.proactive_tick() is True, "缺键时应当按默认 5 分钟生效"
        assert len(ctx.mobile_calls) == before_talk + 1
        # 定时器随插件 scope 停掉（不留下后台线程）
        service.stop_proactive()
        assert service._proactive_thread is None
        ctx.config.update({"proactive_minutes": 5, "auto_narrate": True})

        # ---- 悔棋：回到上一回合开始前 ----
        # 先记下"掷骰之前"的样子，再真的掷一次（掷骰前会留一份快照）
        before_roll = json.loads(http_get(base + "state")[1])
        moves_before = before_roll["game"]["move_count"]
        cash_before = dict(before_roll["game"]["cash"])
        owner_before = [c["owner"] for c in before_roll["game"]["cells"]]
        turn_now = before_roll["game"]["turn"]
        ctx.tools["boardgame_roll"]({"player": turn_now})
        assert json.loads(http_get(base + "state")[1])["canUndo"] is True
        status, res = http_post(base + "api/action", {"action": "undo"})
        assert status == 200 and res["ok"] is True, res
        assert "已退回" in res["status"], res["status"]
        state_after = json.loads(http_get(base + "state")[1])
        assert state_after["game"]["cash"] == cash_before, (state_after["game"]["cash"], cash_before)
        assert [c["owner"] for c in state_after["game"]["cells"]] == owner_before
        assert state_after["game"]["move_count"] == moves_before, state_after["game"]["move_count"]
        # 回退也要写进对局记录（玩家看得到发生了什么）
        assert any("悔棋" in line for line in state_after["log"]), state_after["log"][-3:]
        # 工具形式同样可用（再走一步造出新快照，然后用工具退）
        turn_now = json.loads(http_get(base + "state")[1])["game"]["turn"]
        ctx.tools["boardgame_roll"]({"player": turn_now})
        tool_undo = ctx.tools["boardgame_undo"]({})
        assert "已退回" in tool_undo["status"], tool_undo
        service._snapshots.clear()
        try:
            ctx.tools["boardgame_undo"]({})
        except GameError as error:
            assert "没有可回退" in str(error), str(error)
        else:
            raise AssertionError("没有快照时应当报错")

        # ---- 礼貌规则：用户刚操作过 / 页面开着弹窗时，她不该插嘴 ----
        ctx.config.update({"proactive_minutes": 1, "auto_narrate": True})
        http_get(base + "state?visible=1&busy=0")
        service._last_talk = 0.0
        service._busy_at = time.time()                 # 刚点过棋盘
        assert service.proactive_tick() is False, "用户刚操作过就别说话"
        service._busy_at = 0.0
        service.note_page_busy(True)                   # 弹窗开着（骰子/事件卡）
        assert service.proactive_tick() is False, "页面上正开着弹窗就别说话"
        service.note_page_busy(False)
        service._last_talk = 0.0
        assert service.proactive_tick() is True, "都闲下来了就该能说话"
        ctx.config.update({"proactive_minutes": 0})

        # ---- 自动模式 + 判断理由写进记录（自动下棋时解说才有据可依）----
        mode_status, mode_res = http_post(base + "api/action",
                                          {"action": "set_auto_mode", "arguments": {"mode": "hers"}})
        assert mode_res["ok"] is True and "自动模式" in mode_res["status"], mode_res
        assert json.loads(http_get(base + "state")[1])["autoMode"] == "hers"
        status, res = http_post(base + "api/action", {"action": "set_auto_mode",
                                                     "arguments": {"mode": "nope"}})
        assert res["ok"] is False and "off" in res["error"], res
        http_post(base + "api/action", {"action": "set_auto_mode", "arguments": {"mode": "off"}})
        # 让她的回合自动走完，记录里必须出现"她的判断：…"
        ctx.tools["boardgame_start"]({"game": "monopoly"})
        game = service._game
        for _ in range(40):                      # 推到出现一次买地/升级/选路决策
            if game.pending and game.pending["player"] == "sakura":
                break
            if game.winner:
                break
            try:
                if game.pending:
                    ctx.tools["boardgame_decide"]({"action": "skip"})
                else:
                    ctx.tools["boardgame_roll"]({"player": game.turn})
            except GameError:
                break
        if game.pending and game.pending["player"] == "sakura":
            ctx.tools["boardgame_auto"]({"rounds": 1})
            notes = [line for line in service._log if line.startswith("她的判断：")]
            assert notes, list(service._log)[-5:]
            assert "现金" in notes[-1] or "支街" in notes[-1] or "主环" in notes[-1], notes[-1]

        # ---- 棋风：默认跟随角色卡（和大富翁打法性格同源），手动点了才用手动值 ----
        state_now = json.loads(http_get(base + "state")[1])
        assert state_now["styleFollowCard"] is True, state_now.get("styleFollowCard")
        derived = service._persona["gomoku"]["aggression"]     # 五子棋棋风也是从角色卡推的
        assert state_now["style"] == derived, (state_now["style"], derived)
        assert "角色卡" in state_now["styleNote"], state_now["styleNote"]
        # 手动改一下：切到手动，并且用回手动值
        ctx.config.update({"gomoku_style_follow_card": False, "gomoku_style": 88})
        state_manual = json.loads(http_get(base + "state")[1])
        assert state_manual["styleFollowCard"] is False and state_manual["style"] == 88, state_manual
        ctx.config.update({"gomoku_style_follow_card": True})

        # ---- 非法操作被拒绝且不影响状态 ----
        status, res = http_post(base + "api/action", {"action": "start", "arguments": {"game": "race"}})
        assert status == 400 and res["ok"] is False and "不支持" in res["error"]
        status, res = http_post(base + "api/action", {"action": "nope"})
        assert res["ok"] is False
        status, res = http_post(base + "api/action", {"action": "set_difficulty", "arguments": {"level": 9}})
        assert res["ok"] is False and "难度" in res["error"]

        # ---- 上下文片段 ----
        fragment = builder({'current_input': '请查看棋盘当前局面'})
        assert fragment and "【进行中的对局】" in fragment[0]["content"]
        assert "最近动态" in fragment[0]["content"]
        # 桌游引导：以前要用户手动粘进每个角色的角色卡，现在插件自己注入
        # （当前是大富翁，所以五子棋那几行必须被筛掉，标记本身也不该留在正文里）
        fragment_text = fragment[0]["content"]
        assert "【桌游引导】" in fragment_text, fragment_text[:200]
        assert "【处世态度】" in fragment_text, fragment_text[:200]
        assert "【落子】" not in fragment_text, "五子棋的行不该出现在大富翁的上下文里"
        assert "[大富翁]" not in fragment_text and "[五子棋]" not in fragment_text, fragment_text[:200]
        assert service._guide_source == "内置通用版", service._guide_source

        # ---- 五子棋：先后手骰 + 网页点击落子 + AI 代算 + 难度切换 ----
        status, res = http_post(base + "api/action", {"action": "start", "arguments": {"game": "gomoku"}})
        assert res["ok"] is True
        assert "先后手骰" in res["status"], res["status"]
        # 换成五子棋后，引导里只剩五子棋那一档
        gomoku_fragment = builder({'current_input': '请查看棋盘当前局面'})[0]["content"]
        assert "【落子】" in gomoku_fragment and "【处世态度】" not in gomoku_fragment, gomoku_fragment[:300]
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

        # 换角色后这些曾经写死在插件里的地方也要跟着换（对局记录、状态文案、报错文案）
        gomoku_start = ctx_char.tools["boardgame_start"]({"game": "gomoku"})
        assert FakeContext.TEST_DISPLAY_NAME in gomoku_start["status"], gomoku_start["status"]
        assert "夜乃樱" not in gomoku_start["status"], gomoku_start["status"]
        char_log = json.loads(http_get(char_base + "state")[1])["log"]
        assert char_log and all("夜乃樱" not in line for line in char_log), char_log
        status, res = http_post(char_base + "api/action", {"action": "set_style", "arguments": {"style": 70}})
        assert res["ok"] is True and "夜乃樱" not in res["status"], res
        assert "手动" in res["status"], res
        assert json.loads(http_get(char_base + "state")[1])["styleFollowCard"] is False
        try:
            ctx_char.tools["boardgame_place"]({"player": "user"})
        except GameError as error:
            assert "夜乃樱" not in str(error), str(error)
            assert FakeContext.TEST_DISPLAY_NAME in str(error), str(error)
        else:
            raise AssertionError("玩家不给坐标应当报错")

        # ---- 桌游引导可定制：角色专属文件 > 数据目录默认 > 内置通用版 ----
        guide_root = pathlib.Path(tmp) / "data_guide"
        (guide_root / "guides").mkdir(parents=True, exist_ok=True)
        (guide_root / "guides" / "default.md").write_text("数据目录里的默认引导。", encoding="utf-8")
        (guide_root / "guides" / f"{FakeContext.TEST_CHARACTER_ID}.md").write_text(
            "水仙专属引导。\n[大富翁]只在下大富翁时说的话。\n[五子棋]只在下五子棋时说的话。",
            encoding="utf-8")
        ctx_guide = FakeContext(guide_root, character_package=pkg)
        plugin_module.BoardgamePlugin().setup(ctx_guide)
        service_guide = ctx_guide.tools["boardgame_state"].__self__
        assert service_guide._guide_source.startswith("角色专属"), service_guide._guide_source
        assert service_guide.guide_text("monopoly") == "水仙专属引导。\n只在下大富翁时说的话。", \
            service_guide.guide_text("monopoly")
        assert service_guide.guide_text("gomoku") == "水仙专属引导。\n只在下五子棋时说的话。", \
            service_guide.guide_text("gomoku")
        ctx_guide.tools["boardgame_start"]({"game": "monopoly"})
        guide_fragment = ctx_guide.context_contributions[0][1]({'current_input': '请查看大富翁棋盘'})[0]["content"]
        assert "水仙专属引导" in guide_fragment and "只在下大富翁时说的话" in guide_fragment, guide_fragment[:300]
        assert "只在下五子棋时说的话" not in guide_fragment
        ctx_guide.teardown()

        # 没有角色专属文件时用数据目录里的 guides/default.md（角色 id 仍然解析得到，只是没那份文件）
        guide_root2 = pathlib.Path(tmp) / "data_guide_default"
        (guide_root2 / "guides").mkdir(parents=True, exist_ok=True)
        (guide_root2 / "guides" / "default.md").write_text("数据目录里的默认引导。", encoding="utf-8")
        ctx_guide2 = FakeContext(guide_root2, character_package=pkg)
        plugin_module.BoardgamePlugin().setup(ctx_guide2)
        service_guide2 = ctx_guide2.tools["boardgame_state"].__self__
        assert service_guide2._guide_source == "数据目录里的 guides/default.md", service_guide2._guide_source
        assert service_guide2.guide_text("monopoly") == "数据目录里的默认引导。"
        ctx_guide2.teardown()

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
        for index, url in enumerate(urls):
            status_after = http_get(url, expect_error=True)[0]
            assert status_after is None, f"服务器应在 teardown 后关闭：第 {index} 个实例 {url}"

    print("PLUGIN SMOKE TEST PASSED")


if __name__ == "__main__":
    main()
