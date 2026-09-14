"""最后两张硬骨头：猫窝没画出猫、观景台画成了佛塔。

这两张不是"建筑"，所以风格模板里去掉 single building landmark，主体描述也写得更死。
用法：pyembed\\python.exe -B boardgame_art\\regen_poi2.py
"""
from __future__ import annotations

import pathlib
import sys

from pathlib import Path as _Path

ROOT = _Path(__file__).resolve().parents[1]   # 仓库根目录
sys.path.insert(0, str(pathlib.Path(__file__).parent))
from comfy_client import run  # noqa: E402

OUT = ROOT / "sakura_boardgame" / "art" / "poi"

STYLE = ("isometric flat vector illustration, clean minimal game map asset, "
         "soft pastel palette, plain white background, crisp outlines, centered composition")
NEGATIVE = ("EasyNegative, text, letters, watermark, signature, logo, people, person, human, "
            "blurry, lowres, jpeg artifacts, photo, photorealistic, 3d render, "
            "busy background, dark, gloomy, messy, monochrome, sketch, lineart, "
            "pagoda, temple, tower, multiple objects")

JOBS = [
    ("catspot", 20261599,
     "an open brown cardboard box on the ground with a fluffy grey tabby cat sitting inside it, "
     "a small red food bowl and a cushion beside the box, cat ears and tail clearly visible, "
     "warm beige and soft grey accents"),
    ("tower2", 20263732,
     "a modern seaside observation deck: a slim white concrete column with a wide round glass "
     "viewing platform near the top and a spiral staircase around it, "
     "warm coral and cream accents"),
]

for key, seed, subject in JOBS:
    print(f"--- {key} ---", flush=True)
    run(f"{subject}, {STYLE}", NEGATIVE, seed, size=512, steps=30, cfg=7.5,
        ckpt="Counterfeit-V3.0_fp16.safetensors", sampler="dpmpp_2m", batch=1,
        out_paths=[str(OUT / f"{key}.png")])
print("完成")
