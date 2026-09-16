"""确定性岔路预览：把对局推进到"玩家正站在岔路口"那一刻就停住，供浏览器验收。

用法：python preview_fork.py [seed] [any|budget]
  any    = 第一个玩家岔路（默认）
  budget = 特意找一个"剩余步数走不完到下一个岔路"的岔路（考验高亮终点必须停在步数用尽处）

输出：棋盘 URL + 每个选项的落点，并断言"网页延伸逻辑"与"引擎实际行走"一致。
"""
import json
import pathlib
import random
import sys
import time
import urllib.request

from pathlib import Path as _Path

ROOT = _Path(__file__).resolve().parents[1]      # 仓库根目录
sys.path.insert(0, str(ROOT / "sakura_boardgame"))

import engine as engine_mod  # noqa: E402
import plugin as plugin_module  # noqa: E402

SEED = int(sys.argv[1]) if len(sys.argv) > 1 else 42
MODE = sys.argv[2] if len(sys.argv) > 2 else "any"
ROOT = ROOT / "boardgame_preview_data" / f"fork{SEED}{MODE}"


class FakeConfig:
    def __init__(self):
        self.values = {"dice_sides": 6, "track_length": 28, "gomoku_level": 2,
                       "map_kind": "city", "auto_open_board": False, "auto_narrate": False}

    def get(self):
        return dict(self.values)

    def update(self, values):
        self.values.update(values)
        return "applied"


class Logger:
    def __getattr__(self, name):
        return lambda message, fields=None: None


class Ctx:
    plugin_id = "local.sakura.boardgame"

    def __init__(self, root):
        self.config = FakeConfig()
        self.tools = {}
        self.root = root

    def get(self, key):
        if key == "sakura.host.logging":
            return Logger()
        if key == "sakura.host.tools":
            return type("T", (), {"register": staticmethod(
                lambda d, h: self.tools.__setitem__(d["name"], h))})()
        return self

    def data_path(self, relative):
        return self.root / relative

    def register(self, *args, **kwargs):
        pass

    def effect(self, fn):
        pass


ctx = Ctx(ROOT)
plugin_module.BoardgamePlugin().setup(ctx)
rng = random.Random(7)
ctx.tools["boardgame_start"]({"game": "monopoly"})


def read_state(url):
    return json.loads(urllib.request.urlopen(url + "state", timeout=5).read().decode("utf-8"))


def client_chain(raw, g, node, came_from, budget):
    """网页 forwardOf + 步数预算的复刻。

    `chain[0]` 就是"迈出的第一步"（网页的 cellsAhead 一开始是 [fromNode, 选项]），
    所以判断预算时要把这一步算进去：`len(chain) >= budget` 就停，而不是 len-1。
    """
    chain = [node]
    cur, back = node, came_from
    for _ in range(24):
        if len(chain) >= budget:
            break
        base = raw["moves"].get(str(cur)) or []
        fwd = [n for n in base if n != back and n not in chain]
        if len(fwd) != 1:
            break
        back, cur = cur, fwd[0]
        chain.append(cur)
    return chain


def chains_for(raw):
    """返回 [(选项, 引擎走到哪, 网页应画到哪, 链路)]"""
    g = raw["game"]
    pending = g["pending"]
    from_node = g["walk"]["path"][-1]
    came = g["walk"]["path"][-2] if len(g["walk"]["path"]) >= 2 else None
    out = []
    for opt in pending["options"]:
        clone = engine_mod.restore_game(json.loads(json.dumps(g, ensure_ascii=False)))
        clone.decide("user", str(opt))
        walk = clone._walk or clone._last_walk or {"path": []}
        chain = [from_node] + client_chain(raw, g, opt, from_node, pending["steps_left"])
        out.append((opt, walk["path"][-1], chain[-1], chain, walk.get("steps_left")))
    return out


def wanted(raw):
    """budget 模式：每个选项都是"步数先走完、还没碰到下一个岔路"。"""
    if MODE != "budget":
        return True
    budget = raw["game"]["pending"]["steps_left"]
    rows = chains_for(raw)
    # 链路正好把剩余步数用光（说明是"步数用尽"而不是"撞上岔路"截断的）
    return bool(rows) and all(len(chain) == budget for _, _, _, chain, _ in rows)


for _ in range(400):
    state = ctx.tools["boardgame_state"]({})
    if state.get("winner") or "对局结束" in state.get("status", ""):
        ctx.tools["boardgame_start"]({"game": "monopoly"})
        continue
    pending = state.get("pending")
    if pending and pending.get("type") == "route" and pending["player"] == "user":
        raw_now = read_state(state["board_url"])
        if wanted(raw_now):
            break
        ctx.tools["boardgame_decide"]({"action": str((pending.get("options") or ["skip"])[0])})
        continue
    if pending:
        options = pending.get("options") or []
        ctx.tools["boardgame_decide"]({"action": str(rng.choice(options)) if options else "skip"})
        continue
    player = "sakura" if "轮到 夜乃樱" in state["status"] else "user"
    ctx.tools["boardgame_roll"]({"player": player})
else:
    raise SystemExit("没能在 400 步内造出想要的岔路")

state = ctx.tools["boardgame_state"]({})
url = state["board_url"]
raw = read_state(url)
g = raw["game"]
from_node = g["walk"]["path"][-1]
came = g["walk"]["path"][-2] if len(g["walk"]["path"]) >= 2 else None
budget = g["pending"]["steps_left"]

print("URL:", url, flush=True)
print(f"岔路节点 #{from_node}「{g['cells'][from_node]['name']}」 来路 #{came} 剩余步数 {budget}",
      flush=True)
ok = True
for opt, engine_end, web_end, chain, left in chains_for(raw):
    agree = engine_end == web_end
    ok = ok and agree
    print(f"  选项 #{opt}「{g['cells'][opt]['name']}」"
          f" | 引擎这一程走到 #{engine_end}（剩 {left} 步）"
          f" | 网页应画到 #{web_end}「{g['cells'][web_end]['name']}」"
          f" 链路 {chain} {'✓' if agree else '✗ 不一致！'}",
          flush=True)
print("结论：网页延伸逻辑与引擎" + ("一致 ✓" if ok else "不一致 ✗"), flush=True)

while True:
    time.sleep(1)
