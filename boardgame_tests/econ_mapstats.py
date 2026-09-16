"""看看城市图现在的地产/街区/格子构成，为调整参数做依据。"""
import sys
from collections import Counter
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "sakura_boardgame"))
import engine as E  # noqa: E402

g = E.MonopolyGame(size=28, dice_sides=6, seed=7, map_kind="city")
print("每街区地产数：", dict(Counter(c["group"] for c in g.cells if c["type"] == "property")))
print("格子类型：", dict(Counter(c["type"] for c in g.cells)))
print(f"size {g.size}｜max_rounds {g.max_rounds}｜主环 {len(g.main_path) - 1} 格｜支街 {len(g.branches)} 条")
print("地产价分布：", dict(Counter(c["price"] for c in g.cells if c["type"] == "property")))
ring = set(g.main_path)
print("主环上的地产：", sum(1 for c in g.cells if c["type"] == "property" and c["id"] in ring),
      "｜支街上的：", sum(1 for c in g.cells if c["type"] == "property" and c["id"] not in ring))
print("总地价：", sum(c["price"] for c in g.cells if c["type"] == "property"))
