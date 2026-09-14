"""安装前体检：插件目录里有什么、有没有绝对路径/开发目录依赖、资产是否齐全。"""
import pathlib
import re
import sys

try:
    sys.stdout.reconfigure(encoding="utf-8")
except (AttributeError, OSError):
    pass

ROOT = ROOT / "sakura_boardgame"
DEV_NAMES = ("boardgame_art", "boardgame_tests", "pyembed", "Zcode workspace", "E:\\")

print("=== 插件目录 ===")
total = 0
for path in sorted(ROOT.rglob("*")):
    if path.is_dir():
        continue
    size = path.stat().st_size
    total += size
    rel = path.relative_to(ROOT)
    print(f"{size:>9} {rel}")

print(f"\n合计 {total / 1048576:.2f} MB")

print("\n=== 绝对路径 / 开发目录依赖检查 ===")
bad = 0
for path in sorted(ROOT.rglob("*.py")):
    text = path.read_text(encoding="utf-8", errors="replace")
    for name in DEV_NAMES:
        if name in text:
            for lineno, line in enumerate(text.splitlines(), 1):
                if name in line and not line.strip().startswith("#"):
                    print(f"  !! {path.name}:{lineno} 提到 {name}: {line.strip()[:90]}")
                    bad += 1
print("  未发现依赖" if not bad else f"  发现 {bad} 处，需要处理")

print("\n=== 关键资产 ===")
checks = {
    "plugin.yaml": ROOT / "plugin.yaml",
    "plugin.py": ROOT / "plugin.py",
    "engine.py": ROOT / "engine.py",
    "gomoku_ai.py": ROOT / "gomoku_ai.py",
    "city_map.py": ROOT / "city_map.py",
    "board.html": ROOT / "board.html",
    "README.md": ROOT / "README.md",
    "art/avatar_sakura.png": ROOT / "art" / "avatar_sakura.png",
    "art/avatar_user.svg": ROOT / "art" / "avatar_user.svg",
}
for label, path in checks.items():
    print(f"  {'OK ' if path.exists() else '缺 '}{label}")
poi = sorted((ROOT / "art" / "poi").glob("*.png"))
print(f"  OK  art/poi/*.png：{len(poi)} 张（{(ROOT / 'art' / 'poi').exists()}）")

print("\n=== 设置项 ===")
text = (ROOT / "plugin.py").read_text(encoding="utf-8")
for key in re.findall(r'"key":\s*"([a-z_]+)"', text):
    print("  -", key)
