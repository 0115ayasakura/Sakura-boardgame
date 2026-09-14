"""重出不合格的几张 + 荒废街区那一批（上一批被"去饱和"提示词带成了素描风，和其余不搭）。

用法：pyembed\\python.exe -B boardgame_art\\regen_poi.py
"""
from __future__ import annotations

import pathlib
import sys

from pathlib import Path as _Path

ROOT = _Path(__file__).resolve().parents[1]   # 仓库根目录
sys.path.insert(0, str(pathlib.Path(__file__).parent))
from comfy_client import run  # noqa: E402

OUT = ROOT / "sakura_boardgame" / "art" / "poi"

STYLE = ("isometric flat vector illustration, single building landmark, "
         "clean minimal game map asset, soft pastel palette, "
         "white and pale blue grey tones, subtle soft shadow, "
         "plain white background, crisp outlines, high detail, centered composition")
NEGATIVE = ("EasyNegative, text, letters, watermark, signature, logo, people, person, "
            "blurry, lowres, jpeg artifacts, photo, photorealistic, 3d render, "
            "multiple buildings, busy background, dark, gloomy, messy, monochrome, "
            "sketch, lineart, empty room, blank")

# (key, seed, 主体描述, 色彩倾向)
REDO = [
    ("catspot", 20261599,
     "a small street cat sitting inside a cardboard box shelter with a food bowl and a cushion",
     "warm beige and soft grey accents"),
    ("tower2", 20263732,
     "a seaside observation tower with a wide glass viewing deck and a slim white support column",
     "warm coral and cream accents"),
    ("seaside", 20264691,
     "a seaside promenade with a wooden railing, a lamp post, a bench and the open sea horizon behind",
     "soft mint green and light blue accents"),
    ("garrison", 20266061,
     "a long abandoned military barracks building with rows of broken windows and a collapsed roof",
     "cool grey blue accents"),
    ("hospital", 20266198,
     "an abandoned hospital building exterior with boarded windows and a faded entrance sign",
     "cool grey blue accents"),
    ("shelter", 20266335,
     "a concrete air raid shelter with a heavy round steel door set into a grassy mound",
     "cool grey blue accents"),
    ("alley", 20266472,
     "a narrow back alley between two buildings with pipes, a puddle and a single wall lamp",
     "cool grey blue accents"),
    ("subway", 20265513,
     "an abandoned underground subway entrance with a tiled roof, dark stairs going down and a broken sign",
     "cool grey blue accents"),
    ("wall", 20265376,
     "a massive concrete defensive wall with warning stripes and a rusted iron gate",
     "cool grey blue accents"),
    ("manhole", 20265650,
     "a round cast iron manhole cover with a geometric pattern set in cracked asphalt",
     "cool grey blue accents"),
    ("station", 20265787,
     "a small desolate railway platform with a rusted shelter, a bench and a single track",
     "cool grey blue accents"),
]

for index, (key, seed, subject, tint) in enumerate(REDO):
    target = OUT / f"{key}.png"
    print(f"[{index + 1}/{len(REDO)}] {key} ...", flush=True)
    try:
        run(f"{subject}, {STYLE}, {tint}", NEGATIVE, seed, size=512, steps=28, cfg=7.0,
            ckpt="Counterfeit-V3.0_fp16.safetensors", sampler="dpmpp_2m", batch=1,
            out_paths=[str(target)])
    except Exception as error:  # noqa: BLE001
        print(f"    失败 {key}: {type(error).__name__} {error}", flush=True)

print("重出完成", len(REDO), "张")
