"""核对：每块地产的 icon 都有对应的 AI 插图（缺图会让图钉退回通用图标）。"""
import pathlib
import sys

from pathlib import Path as _Path

ROOT = _Path(__file__).resolve().parents[1]   # 仓库根目录
sys.path.insert(0, str(ROOT / "sakura_boardgame"))
try:
    sys.stdout.reconfigure(encoding="utf-8")
except (AttributeError, OSError):
    pass

import city_map  # noqa: E402

cells, _, _, _ = city_map.build_city()
poi_dir = ROOT / "sakura_boardgame" / "art" / "poi"
have = {p.stem for p in poi_dir.glob("*.png")}

need: dict[str, list[str]] = {}
for cell in cells:
    if cell["type"] == "property":
        need.setdefault(cell["icon"].replace("poi-", ""), []).append(cell["name"])

missing = sorted(k for k in need if k not in have)
extra = sorted(have - set(need))
print(f"地产种类 {len(need)}｜已有插图 {len(have)}")
print("缺图：", "、".join(missing) if missing else "无")
print("多余：", "、".join(extra) if extra else "无")
for key in sorted(need):
    flag = "OK " if key in have else "缺 "
    print(f"  {flag}{key:12s} {'、'.join(need[key])}")

if __name__ == "__main__" and missing:
    sys.exit(1)
