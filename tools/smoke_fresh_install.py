"""模拟"刚装好插件"的状态跑一遍：加载 → 开局 → 切地图 → 再开局 → 走几步。

用的是 test_plugin_smoke 那套假宿主（Ctx），但走的是**全新数据目录**，
等价于用户第一次安装后的首次运行。
用法：pyembed\\python.exe -B boardgame_art\\smoke_fresh_install.py
"""
from __future__ import annotations

import json
import pathlib
import shutil
import sys
import tempfile
import urllib.request

from pathlib import Path as _Path

ROOT = _Path(__file__).resolve().parents[1]   # 仓库根目录
sys.path.insert(0, str(ROOT / "boardgame_tests"))
sys.path.insert(0, str(ROOT / "sakura_boardgame"))
try:
    sys.stdout.reconfigure(encoding="utf-8")
except (AttributeError, OSError):
    pass

import plugin as plugin_module  # noqa: E402


def http_json(url: str, payload=None):
    if payload is None:
        with urllib.request.urlopen(url, timeout=10) as resp:
            return json.loads(resp.read())
    data = json.dumps(payload).encode()
    req = urllib.request.Request(url, data=data, headers={"Content-Type": "application/json"})
    with urllib.request.urlopen(req, timeout=10) as resp:
        return json.loads(resp.read())


class FakeConfig:
    def __init__(self, values):
        self.values = dict(values)

    def get(self):
        return dict(self.values)

    def update(self, values):
        self.values.update(values)
        return "applied"


class Ctx:
    def __init__(self, root):
        self.plugin_id = "local.sakura.boardgame"
        self.config = FakeConfig({"dice_sides": 6, "track_length": 28, "gomoku_level": 2,
                                  "map_kind": "city", "auto_open_board": False,
                                  "auto_narrate": True})
        self.tools = {}
        self.root = root
        self.mobile_calls = []
        self._services = {
            "sakura.host.logging": type("L", (), {"__getattr__": lambda s, n: lambda *a, **k: None})(),
            "sakura.host.context": self,
            "sakura.host.settings": self,
            "sakura.host.character": type("C", (), {"current": staticmethod(lambda: {"id": "sakura-1"})})(),
            "sakura.host.mobile": type("M", (), {
                "begin": lambda s, *a, **k: self.mobile_calls.append(a) or {"jobId": "job"} })(),
        }

    def get(self, key):
        return self._services.get(key) or self

    def data_path(self, relative):
        return self.root / relative

    def register(self, *args, **kwargs):
        pass

    def effect(self, fn):
        pass


workdir = pathlib.Path(tempfile.mkdtemp(prefix="boardgame-fresh-"))
try:
    ctx = Ctx(workdir)
    ctx._services["sakura.host.tools"] = type(
        "T", (), {"register": staticmethod(lambda d, h: ctx.tools.__setitem__(d["name"], h))})()
    plugin_module.BoardgamePlugin().setup(ctx)

    print("工具：", "、".join(sorted(ctx.tools)))

    base = ctx.tools["boardgame_state"]({})["board_url"].rstrip("/") + "/"
    print("棋盘地址：", base)

    # 0) 冷启动兜底：还没开局时，直接在网页上掷骰子应当自动开一局
    #    （这样"模型没调用工具"也不影响玩）
    res = http_json(base + "api/action", {"action": "roll", "arguments": {}})
    print("冷启动掷骰：", str(res.get("status") or res)[:70])
    assert res["ok"] is True, res
    assert http_json(base + "state")["game"] is not None, "冷启动没自动开局"

    # 1) 默认应该是城市图
    start = ctx.tools["boardgame_start"]({"game": "monopoly"})
    status = start.get("status") or ""
    print("开局返回里的地址提示：", [l for l in status.splitlines() if "棋盘页面" in l] or "（没有！）")
    assert "棋盘页面" in status and base.rstrip("/") in status, "开局返回必须带上棋盘地址"
    state = http_json(base + "state")
    print(f"默认地图：{state['mapKind']}｜格子 {state['game']['size']}｜存档版本 {state['game']['map_version']}")
    assert state["mapKind"] == "city"
    assert state["decor"] and state["moves"], "城市图应该下发装饰层与可走方向"
    assert len(state["mainPath"]) > 5 and state["branches"]

    # 2) 网页上切到随机图，然后重开一局
    res = http_json(base + "api/action", {"action": "set_map_kind", "arguments": {"kind": "random"}})
    print("切换地图：", res.get("status") or res)
    assert res["ok"] is True
    ctx.tools["boardgame_start"]({"game": "monopoly"})
    state = http_json(base + "state")
    print(f"切换后地图：{state['mapKind']}｜格子 {state['game']['size']}")
    assert state["mapKind"] == "random" and state["decor"] is None
    assert len(state["mainPath"]) == state["game"]["size"]

    # 3) 掷几步骰子（网页接口），确认能正常跑
    steps = 0
    while steps < 8:
        state = http_json(base + "state")
        game = state["game"]
        if game["winner"]:
            break
        if game.get("pending"):
            action = "decide"
            args = {"action": str((game["pending"].get("options") or ["skip"])[0])}
        elif game["turn"] == "user":
            action, args = "roll", {"player": "user"}
        else:
            action, args = "act_sakura", {}      # 轮到她：走网页上的「请夜乃樱行动」
        res = http_json(base + "api/action", {"action": action, "arguments": args})
        assert res["ok"] is True, res
        steps += 1
    state = http_json(base + "state")
    print(f"走了 {steps} 步｜回合数 {state['game']['move_count']}｜记录 {len(state['log'])} 条")
    assert steps >= 4 and state["log"]

    # 4) 五子棋也能开
    ctx.tools["boardgame_start"]({"game": "gomoku"})
    print("五子棋：", http_json(base + "state")["game"]["kind"])

    # 5) 关键：只点一次「请夜乃樱行动」，就要走完她的整个回合（岔路连发也不该停下等第二次）
    ctx.tools["boardgame_start"]({"game": "monopoly"})
    clicked = 0
    for _ in range(60):
        state = http_json(base + "state")
        game = state["game"]
        if game["winner"]:
            break
        if game.get("pending"):
            option = str((game["pending"].get("options") or ["skip"])[0])
            http_json(base + "api/action", {"action": "decide", "arguments": {"action": option}})
            continue
        if game["turn"] == "user":
            http_json(base + "api/action", {"action": "roll", "arguments": {}})
            continue
        clicked += 1
        http_json(base + "api/action", {"action": "act_sakura", "arguments": {}})
        after = http_json(base + "state")["game"]
        pending = after.get("pending")
        assert not after.get("walk"), f"一次点按后还在走格子：{after.get('walk')}"
        assert pending is None or pending.get("player") != "sakura", f"她的决策没走完：{pending}"
        break
    print(f"一次「请夜乃樱行动」后，她的待决策已清空、回合已交出（点击 {clicked} 次）")
    assert clicked == 1
    print("\n全新安装模拟通过 ✓（数据目录：%s）" % workdir)
finally:
    shutil.rmtree(workdir, ignore_errors=True)
