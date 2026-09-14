"""插件集成冒烟测试：工具注册、上下文、HTTP 服务器、网页 API、mobile 通道与清理。"""
from __future__ import annotations

import json
import pathlib
import sys
import tempfile
import urllib.error
import urllib.request

from pathlib import Path as _Path

ROOT = _Path(__file__).resolve().parents[1]   # 仓库根目录
sys.path.insert(0, str(ROOT / "sakura_boardgame"))

import plugin as plugin_module
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
    def __init__(self, root: pathlib.Path) -> None:
        self.plugin_id = "local.sakura.boardgame"
        self.config = FakeConfig()
        self.root = root
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
            def current(self):
                return {"id": "sakura-1", "systemPrompt": "..."}

        class Mobile:
            def begin(self, plugin_id, character_id, text, artifact=None):
                self_ref.mobile_calls.append({"plugin_id": plugin_id, "character_id": character_id, "text": text})
                return {"jobId": "job-1"}

        self._services = {
            "sakura.host.logging": self._logger,
            "sakura.host.tools": Tools(),
            "sakura.host.context": ContextHost(),
            "sakura.host.settings": Settings(),
            "sakura.host.character": Character(),
            "sakura.host.mobile": Mobile(),
        }

    def get(self, key):
        return self._services.get(key)

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
        cold = builder({})
        assert cold, "没对局时上下文不该是空的"
        cold_text = json.dumps(cold, ensure_ascii=False)
        assert "http://127.0.0.1:" in cold_text, cold_text[:200]
        assert "boardgame_start" in cold_text

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
        assert payload["game"]["map_version"] == 4
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
            steps += 1
            assert steps < 200, "网页操作无法推进对局"
        assert steps > 0, "至少应能通过网页推进一次"

        # 掷骰产生了带路径的移动事件（供页面播棋子移动动画）
        payload = json.loads(http_get(base + "state")[1])
        assert payload["lastEvent"], payload["lastEvent"]
        event = payload["lastEvent"]
        assert event["kind"] == "move" and 1 <= event["dice"] <= 12, event
        assert event["path"] and event["path"][0] == event["path"][0], event
        assert len(event["path"]) >= 1
        # 地产数量与租金收益统计
        stats = payload["stats"]
        assert set(stats) == {"user", "sakura"}, stats
        for side in ("user", "sakura"):
            assert "properties" in stats[side] and "income" in stats[side], stats
            assert stats[side]["properties"] >= 0 and stats[side]["income"] >= 0

        # ---- 请夜乃樱行动（含 mobile 通道）----
        status, res = http_post(base + "api/action", {"action": "act_sakura"})
        assert status == 200 and res["ok"] is True, res
        assert ctx.mobile_calls, "应通过 mobile 通道触发夜乃樱反应"
        call = ctx.mobile_calls[-1]
        assert call["plugin_id"] == ctx.plugin_id and call["character_id"] == "sakura-1"
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
        assert restored["map_version"] == 4

        # ---- 旧版存档被安全忽略 ----
        data_dir = pathlib.Path(tmp) / "data_old"
        data_dir.mkdir(parents=True, exist_ok=True)
        (data_dir / "state.json").write_text(
            json.dumps({"kind": "monopoly", "cells": [], "map_version": None}), encoding="utf-8")
        ctx4 = FakeContext(data_dir)
        plugin_module.BoardgamePlugin().setup(ctx4)
        assert ctx4.tools["boardgame_state"]({})["active"] is False

        # ---- 损坏存档被安全忽略 ----
        (data_dir / "state.json").write_text("{broken", encoding="utf-8")
        ctx5 = FakeContext(data_dir)
        plugin_module.BoardgamePlugin().setup(ctx5)
        assert ctx5.tools["boardgame_state"]({})["active"] is False

        # ---- 服务器随插件 scope 停止 ----
        all_ctx = [ctx, ctx2, ctx3, ctx4, ctx5]
        for c in all_ctx:
            c.teardown()
        for c in all_ctx:
            url = c.tools["boardgame_state"]({})["board_url"]
            assert http_get(url, expect_error=True)[0] is None, "服务器应在 teardown 后关闭"

    print("PLUGIN SMOKE TEST PASSED")


if __name__ == "__main__":
    main()
