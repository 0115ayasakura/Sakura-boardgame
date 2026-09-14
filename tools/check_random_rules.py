"""测随机地图的三条硬要求：锐角数量、支线穿主线、支线跨步。"""
import math
import sys

from pathlib import Path as _Path

ROOT = _Path(__file__).resolve().parents[1]   # 仓库根目录
sys.path.insert(0, str(ROOT / "sakura_boardgame"))
try:
    sys.stdout.reconfigure(encoding="utf-8")
except (AttributeError, OSError):
    pass

from engine import MonopolyGame  # noqa: E402


def cross(o, p, q):
    return (p[0] - o[0]) * (q[1] - o[1]) - (p[1] - o[1]) * (q[0] - o[0])


def seg_cross(a1, a2, b1, b2):
    d1, d2 = cross(b1, b2, a1), cross(b1, b2, a2)
    d3, d4 = cross(a1, a2, b1), cross(a1, a2, b2)
    return ((d1 > 0) != (d2 > 0)) and ((d3 > 0) != (d4 > 0))


worst_acute = 0
total_acute = 0
crossings = 0
trunk_crossings = 0
min_span = 99
min_gap_worst = 99
for seed in range(15):
    for size in (12, 24, 28, 48, 96):
        game = MonopolyGame(size=size, dice_sides=6, seed=seed)
        pts = [(c["x"], c["y"]) for c in game.cells]
        n = game.size
        trunk = game.main_path
        segs = [(pts[trunk[i]], pts[trunk[i + 1]]) for i in range(len(trunk) - 1)]
        segs.append((pts[trunk[-1]], pts[trunk[0]]))
        # 主路自己交叉
        for a in range(len(segs)):
            for b in range(a + 1, len(segs)):
                s, t = segs[a], segs[b]
                if s[0] in t or s[1] in t:
                    continue                      # 相邻边共享端点
                if seg_cross(s[0], s[1], t[0], t[1]):
                    trunk_crossings += 1
        # 锐角
        acute = 0
        for i in range(len(trunk)):
            a, b, c = pts[trunk[i - 1]], pts[trunk[i]], pts[trunk[(i + 1) % len(trunk)]]
            v1 = (a[0] - b[0], a[1] - b[1])
            v2 = (c[0] - b[0], c[1] - b[1])
            n1, n2 = math.hypot(*v1), math.hypot(*v2)
            if n1 < 1e-9 or n2 < 1e-9:
                continue
            cos = (v1[0] * v2[0] + v1[1] * v2[1]) / (n1 * n2)
            if cos > math.cos(math.radians(60)):
                acute += 1
        total_acute += acute
        worst_acute = max(worst_acute, acute)
        # 支线穿主线
        for a, b in game.branches:
            for s in segs:
                if s[0] in (pts[a], pts[b]) or s[1] in (pts[a], pts[b]):
                    continue
                if seg_cross(pts[a], pts[b], s[0], s[1]):
                    crossings += 1
        # 最小间距
        for i in range(n):
            for j in range(i + 1, n):
                d = math.hypot(pts[i][0] - pts[j][0], pts[i][1] - pts[j][1])
                min_gap_worst = min(min_gap_worst, d)
        # 支线跨步
        idx = {node: k for k, node in enumerate(trunk)}
        for a, b in game.branches:
            min_span = min(min_span, abs(idx[a] - idx[b]))

print(f"锐角总数={total_acute}（单图最多 {worst_acute}）｜主路自交叉={trunk_crossings}｜"
      f"支线穿主线={crossings}｜最小支线跨步={min_span}｜最小格子间距={min_gap_worst:.2f}")
