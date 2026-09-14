"""随机地图体检：最小间距、支线长度、主路是否有机（不是整齐网格）。

用法：pyembed\\python.exe -B boardgame_art/check_random_map.py
"""
import sys

from pathlib import Path as _Path

ROOT = _Path(__file__).resolve().parents[1]   # 仓库根目录
sys.path.insert(0, str(ROOT / "sakura_boardgame"))
try:
    sys.stdout.reconfigure(encoding="utf-8")
except (AttributeError, OSError):
    pass

from engine import MonopolyGame  # noqa: E402

for size in (28, 48, 72, 96):
    game = MonopolyGame(size=size, dice_sides=6, seed=11, map_kind="random")
    pts = [(c["x"], c["y"]) for c in game.cells]
    gaps = []
    for i in range(len(pts)):
        for j in range(i + 1, len(pts)):
            gaps.append(((pts[i][0] - pts[j][0]) ** 2 + (pts[i][1] - pts[j][1]) ** 2) ** 0.5)
    lengths = []
    for a, b in game.branches:
        pa, pb = pts[a], pts[b]
        lengths.append(round(((pa[0] - pb[0]) ** 2 + (pa[1] - pb[1]) ** 2) ** 0.5, 2))
    # "有机"程度：相邻主路节点的间距差异越大，越不像整齐网格
    trunk = game.main_path
    steps = []
    for i in range(len(trunk) - 1):
        pa, pb = pts[trunk[i]], pts[trunk[i + 1]]
        steps.append(((pa[0] - pb[0]) ** 2 + (pa[1] - pb[1]) ** 2) ** 0.5)
    spread = max(steps) - min(steps)
    print(f"size={size:3d} 格子={game.size:3d} 最小间距={min(gaps):5.2f} 最短主路边={min(steps):5.2f} "
          f"最长主路边={max(steps):5.2f}（差 {spread:4.2f}） 支线={len(game.branches):2d} "
          f"最长支线={max(lengths) if lengths else 0:5.2f}")
    assert min(gaps) >= 1.3, "格子挨太近，图钉会叠在一起"
    assert spread > 0.4, "主路边长几乎一致，看起来还是整齐网格"
print("随机地图体检通过")
