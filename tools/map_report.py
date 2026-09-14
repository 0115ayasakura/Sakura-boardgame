"""地图体检：打印主环顺序（= 行进方向）、支街分类、路口分布、地产归属。

改完 city_map.py 之后除了跑 `city_map.validate_layout()`，也跑一下这个看结构是否还合理。
用法：pyembed\\python.exe -B boardgame_art\\map_report.py
"""
import sys

from pathlib import Path as _Path

ROOT = _Path(__file__).resolve().parents[1]   # 仓库根目录
sys.path.insert(0, str(ROOT / "sakura_boardgame"))
# 本机 cmd 控制台是 GBK，不重设会把中文打成乱码
try:
    sys.stdout.reconfigure(encoding="utf-8")
except (AttributeError, OSError):
    pass

import city_map  # noqa: E402

cells, edges, main_path, branches = city_map.build_city()
by_id = {c["id"]: c for c in cells}
ring = list(main_path)
if len(ring) > 1 and ring[0] == ring[-1]:
    ring = ring[:-1]
ring_set = set(ring)

print(f"格子 {len(cells)}｜边 {len(edges)}｜主环 {len(ring)} 节点｜支街 {len(branches)} 条")
print()
print("=== 主环顺序（行进方向；起点出发第一格必须是学园正门）===")
for i, node in enumerate(ring):
    cell = by_id[node]
    mark = "  <-- 起点" if cell["type"] == "start" else ""
    print(f"{i:2d} #{node:2d} {cell['name']}({cell['type']}){mark}")
print()
print("起点后继：", by_id[ring[1]]["name"])
print()
print("=== 支街（两端是否在环上）===")
for chain in branches:
    a, b = by_id[chain[0]], by_id[chain[-1]]
    kind = ("主动脉(环-环) " if chain[0] in ring_set and chain[-1] in ring_set
            else "岔路(环-尽头)" if (chain[0] in ring_set) != (chain[-1] in ring_set)
            else "岔路接岔路  ")
    print(f"{kind} len={len(chain):2d} {a['name']} -> {b['name']}")
print()
degree = {c["id"]: 0 for c in cells}
for a, b in edges:
    degree[a] += 1
    degree[b] += 1
junctions = sorted((degree[n], by_id[n]["name"]) for n in degree if degree[n] >= 3)
print(f"=== 路口（度>=3）：{len(junctions)} 个 ===")
for d, name in junctions:
    print(f"  {name} 度={d}")
print()
print("=== 地产归属 ===")
for key, info in city_map.DISTRICTS.items():
    props = [c["name"] for c in cells if c.get("group") == key]
    print(f"{info['label']}（{len(props)}）：{'、'.join(props)}")
print()
issues = city_map.validate_layout()
print("校验：", "通过 ✓" if not issues else f"发现 {len(issues)} 个问题")
for issue in issues:
    print("  -", issue)
