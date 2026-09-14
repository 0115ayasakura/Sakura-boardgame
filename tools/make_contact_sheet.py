"""把 poi 目录里的插图拼成一张 contact sheet，一次看完 35 张。

用法：pyembed\\python.exe -B boardgame_art\\make_contact_sheet.py
"""
from __future__ import annotations

import pathlib

from PIL import Image, ImageDraw

SRC = ROOT / "sakura_boardgame" / "art" / "poi"
OUT = ROOT / "poi_sheet.png"
COLS, CELL, PAD, LABEL = 7, 176, 6, 16

files = sorted(SRC.glob("*.png"))
rows = (len(files) + COLS - 1) // COLS
sheet = Image.new("RGB", (COLS * (CELL + PAD) + PAD, rows * (CELL + PAD + LABEL) + PAD), (255, 255, 255))
draw = ImageDraw.Draw(sheet)

for index, path in enumerate(files):
    row, col = divmod(index, COLS)
    x = PAD + col * (CELL + PAD)
    y = PAD + row * (CELL + PAD + LABEL)
    with Image.open(path) as raw:
        thumb = raw.convert("RGB")
        thumb.thumbnail((CELL, CELL), Image.LANCZOS)
    sheet.paste(thumb, (x, y))
    draw.rectangle([x - 1, y - 1, x + CELL, y + CELL], outline=(210, 216, 228))
    draw.text((x + 2, y + CELL + 3), path.stem, fill=(40, 48, 66))

sheet.save(OUT)
print("saved:", OUT, f"| {len(files)} 张 | {sheet.size}")
print("顺序（左→右、上→下）：", "、".join(p.stem for p in files))
