"""对比"地产 29 处"与"16 处"两种设置：哪些地标会从城市图上消失、事件格多出多少。"""
import sys
from collections import Counter
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "sakura_boardgame"))
import city_map  # noqa: E402


def snapshot(target: int) -> dict:
    city_map.PROPERTY_TARGET = target
    cells, edges, main_path, branches = city_map.build_city()
    props = [c["name"] for c in cells if c["type"] == "property"]
    kinds = Counter(c["type"] for c in cells)
    dist = Counter(c["group"] for c in cells if c["type"] == "property")
    return {"props": props, "kinds": kinds, "dist": dist, "cells": len(cells),
            "edges": len(edges), "ring": len(main_path), "branches": len(branches),
            "geom": tuple(sorted(city_map.STREET_GEOM)), "target": target}


a = snapshot(29)
b = snapshot(16)
print(f"格子数 {a['cells']} vs {b['cells']}（应该一样）；边 {a['edges']} vs {b['edges']}；"
      f"主环 {a['ring']} vs {b['ring']}；支街 {a['branches']} vs {b['branches']}")
print(f"路网几何是否完全一致：{a['geom'] == b['geom']}")
print(f"\n地产数：{len(a['props'])} → {len(b['props'])}")
print(f"每街区：{dict(a['dist'])} → {dict(b['dist'])}")
print(f"\n格子类型变化：")
for k in sorted(set(a["kinds"]) | set(b["kinds"])):
    print(f"  {k:<10}{a['kinds'].get(k, 0):>4} → {b['kinds'].get(k, 0):>4}")
gone = [n for n in a["props"] if n not in b["props"]]
print(f"\n会从城市图上消失的地标（{len(gone)} 个）：{gone}")
