"""棋盘页面预览服务器：跑一局后保持运行，供浏览器截图验收。

用法：python preview_server.py [monopoly|gomoku]
"""
import pathlib
import random
import sys
import time

from pathlib import Path as _Path

ROOT = _Path(__file__).resolve().parents[1]   # 仓库根目录
sys.path.insert(0, str(ROOT / "sakura_boardgame"))

import plugin as plugin_module
from engine import GameError

# 第二个参数可以指定地图来源：preview_server.py monopoly random
MAP_KIND = sys.argv[2] if len(sys.argv) > 2 else "city"


class FakeConfig:
    def __init__(self):
        self.values = {"dice_sides": 6, "track_length": 28, "gomoku_level": 2,
                       "map_kind": MAP_KIND,
                       "auto_open_board": True, "auto_narrate": True}

    def get(self):
        return dict(self.values)

    def update(self, values):
        self.values.update(values)
        return "applied"


class Logger:
    def __getattr__(self, name):
        return lambda message, fields=None: None


class Mobile:
    def __init__(self):
        self.calls = []

    def begin(self, plugin_id, character_id, text, artifact=None):
        self.calls.append(text)
        return {"jobId": "job"}


class Character:
    def current(self):
        return {"id": "sakura-1"}


class Ctx:
    def __init__(self, root):
        self.plugin_id = "local.sakura.boardgame"
        self.config = FakeConfig()
        self.tools = {}
        self._logger = Logger()
        self.mobile = Mobile()
        self._services = {
            "sakura.host.logging": self._logger,
            "sakura.host.context": self,
            "sakura.host.settings": self,
            "sakura.host.character": Character(),
            "sakura.host.mobile": self.mobile,
        }
        self.root = root

    def get(self, key):
        return self._services.get(key) or self

    def data_path(self, relative):
        return self.root / relative

    def register(self, *args, **kwargs):
        pass

    def effect(self, fn):
        pass


ctx = Ctx(ROOT / "boardgame_preview_data")
ctx._services["sakura.host.tools"] = type(
    "T", (), {"register": staticmethod(lambda d, h: ctx.tools.__setitem__(d["name"], h))}
)()
plugin_module.BoardgamePlugin().setup(ctx)

KIND = sys.argv[1] if len(sys.argv) > 1 else "monopoly"
rng = random.Random(42)

start = lambda kind: ctx.tools["boardgame_start"]({"game": kind})


def step():
    """让玩家与夜乃樱交替推进一局（用引擎工具，等价于网页操作）。"""
    if KIND == "gomoku":
        state = ctx.tools["boardgame_state"]({})
        if "轮到 玩家" in state["status"]:
            ctx.tools["boardgame_place"]({"row": rng.randint(3, 12), "col": rng.randint(3, 12)})
        else:
            ctx.tools["boardgame_place"]({})
        return
    state = ctx.tools["boardgame_state"]({})
    game = ctx.tools["boardgame_state"]({})
    if "待决策" in state["status"] or state.get("pending"):
        pending = state.get("pending") or {}
        options = pending.get("options") or []
        action = str(rng.choice(options)) if options else "skip"
        ctx.tools["boardgame_decide"]({"action": action})
        return
    player = "user" if "轮到 玩家" in state["status"] else "sakura"
    if "走格子中" in state["status"]:
        player = "sakura" if "夜乃樱走格子" in state["status"] else "user"
    ctx.tools["boardgame_roll"]({"player": player})


start(KIND)
for _ in range(26 if KIND == "monopoly" else 14):
    try:
        step()
    except GameError:
        start(KIND)

print("URL:", ctx.tools["boardgame_state"]({})["board_url"], flush=True)
while True:
    time.sleep(1)
