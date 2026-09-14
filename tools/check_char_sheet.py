"""校验角色卡补充文件：必须讲到"调用工具才会开局"和"把棋盘地址转达给用户"。

中文文件名经 cmd 传参会被 codepage 弄坏，所以这个脚本自己去找文件，不接受命令行参数。
用法：pyembed\\python.exe -B boardgame_art\\check_char_sheet.py
"""
from __future__ import annotations

import pathlib
import sys

try:
    sys.stdout.reconfigure(encoding="utf-8")
except (AttributeError, OSError):
    pass

from pathlib import Path as _Path

ROOT = _Path(__file__).resolve().parents[1]
candidates = sorted(p for p in ROOT.glob("sakura_boardgame*") if p.suffix == ".txt")
if not candidates:
    print("找不到角色卡补充文件（sakura_boardgame*.txt）")
    raise SystemExit(1)

path = candidates[0]
text = path.read_text(encoding="utf-8")
print(f"文件：{path.name}｜{len(text)} 字｜{len(text.splitlines())} 行\n")

MUST = {
    "必须调用 boardgame_start": "boardgame_start" in text,
    "不能只靠口头描述开局": "不调用工具" in text or "只在聊天里说" in text,
    "要转达棋盘地址": "board_url" in text or "棋盘地址" in text,
    "提到地图来源切换": "地图来源" in text,
    "提到随机地图": "随机地图" in text,
    "提到四个街区": all(k in text for k in ("学园区", "街市", "住宅区", "荒废街区")),
    "提到单向主环": "单向主环" in text,
    "不再有“自动打开”的误导说法": "会自动在浏览器打开" not in text,
}
bad = 0
for label, ok in MUST.items():
    print(f"  {'OK ' if ok else '缺 '}{label}")
    bad += 0 if ok else 1

print("\n结论：" + ("通过 ✓" if not bad else f"{bad} 项需要补"))
raise SystemExit(1 if bad else 0)
