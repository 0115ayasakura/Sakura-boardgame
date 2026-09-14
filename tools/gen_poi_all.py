"""批量出全部地标插图 → sakura_boardgame/art/poi/<key>.png

风格模板与已获确认的 6 张样图完全一致（同 ckpt / sampler / steps / cfg / 同风格提示词），
前 6 张沿用它们原来的 seed，保证已经看过的观感不变。需要 ComfyUI 在 127.0.0.1:8188 运行。

用法：pyembed\\python.exe -B boardgame_art\\gen_poi_all.py [只跑前 N 张]
"""
from __future__ import annotations

import pathlib
import sys
import time

from pathlib import Path as _Path

ROOT = _Path(__file__).resolve().parents[1]   # 仓库根目录
sys.path.insert(0, str(pathlib.Path(__file__).parent))
from comfy_client import run  # noqa: E402

OUT = ROOT / "sakura_boardgame" / "art" / "poi"
OUT.mkdir(parents=True, exist_ok=True)

# 与已确认样图相同的风格模板
STYLE = ("isometric flat vector illustration, single building landmark, "
         "clean minimal game map asset, soft pastel palette, "
         "white and pale blue grey tones, subtle soft shadow, "
         "plain white background, crisp outlines, high detail, centered composition")
NEGATIVE = ("EasyNegative, text, letters, watermark, signature, logo, people, person, "
            "blurry, lowres, jpeg artifacts, photo, photorealistic, 3d render, "
            "multiple buildings, busy background, dark, gloomy, messy")

# 四个街区各给一点色彩倾向，让同一区的插图看得出来是一伙的
TINT = {
    "gakuen": "cool blue accents",
    "shopping": "warm coral and amber accents",
    "daily": "soft mint green accents",
    "taboo": "muted desaturated blue grey palette, quiet abandoned mood",
}

# (key, 街区, seed 偏移, 主体描述)
# 前 6 个 key 的 seed 偏移与 gen_poi_samples.py 相同 → 复现已确认的样图
SUBJECTS = [
    # ---- 学园区 ----
    ("gate", "gakuen", 0 * 137, "a school front gate with two white pillars, a blue arch banner and a round golden emblem"),
    ("council", "gakuen", 1 * 137, "a student council room desk with papers, a chair and a wall clock"),
    ("libcom", "gakuen", 2 * 137, "a tall bookshelf with colorful book spines, a book cart and a stack of books"),
    ("explore", "gakuen", None, "an exploration club room with a large wall map, a brass compass and a rolled scroll"),
    ("mutelib", "gakuen", 3 * 137, "a tall library tower with shelves full of books and a white crane bird perched on top"),
    ("cathedral", "gakuen", None, "a gothic underground cathedral entrance with stained glass windows and stone steps going down"),
    ("tower", "gakuen", None, "a slender white clock tower with a golden bell and a small annex building at its base"),
    ("pool", "gakuen", None, "an outdoor school swimming pool with lane ropes and a small changing hut"),
    ("dojo", "gakuen", None, "a japanese archery dojo with wooden pillars, a tiled roof and a target stand"),
    # ---- 街市 ----
    ("family", "shopping", 4 * 137, "a family restaurant facade with striped awning, neon sign and a table with a plate"),
    ("ktv", "shopping", None, "a karaoke box building with glass doors, a microphone sign and pink neon lighting"),
    ("hotel", "shopping", None, "a small love hotel with a round bed sign, curtains and a heart shaped neon"),
    ("british", "shopping", None, "a british style shopping street corner with a red phone booth and a brick storefront"),
    ("esports", "shopping", None, "an esports arcade hall with gaming chairs, monitors and a trophy display"),
    ("tower2", "shopping", None, "an observation deck tower with a glass viewing platform and an elevator shaft"),
    ("conveni", "shopping", None, "a small convenience store with glass windows, shelves of goods and a striped sign"),
    ("arcade", "shopping", None, "a game center with claw machines, arcade cabinets and colourful lights"),
    # ---- 住宅区 ----
    ("sakura_flat", "daily", None, "a small japanese apartment room with a low bed, a desk with a tea cup and a window with curtains"),
    ("user_flat", "daily", None, "a small cozy apartment room with a sofa, a bookshelf and a warm desk lamp"),
    ("park", "daily", None, "a small city park with a tree, a bench, a swing and a paved path"),
    ("catspot", "daily", 5 * 137, "a cardboard box shelter with a grey cat, a food bowl and a paw print"),
    ("seaside", "daily", None, "a seaside promenade with a railing, a lamp post and calm water behind"),
    ("bookstore", "daily", None, "an old second hand bookstore with stacked books outside and a worn wooden sign"),
    ("bakery", "daily", None, "a small bakery with a bread display, a rolling pin and a warm awning"),
    ("sento", "daily", None, "an old japanese public bath house with a tiled roof and a red curtain at the entrance"),
    ("bench", "daily", None, "a wooden bench on a seaside path with a small vending machine beside it"),
    # ---- 荒废街区 ----
    ("wall", "taboo", None, "a massive concrete barrier wall with barbed wire and a warning stripe"),
    ("subway", "taboo", None, "an abandoned underground subway entrance with a dark staircase and a broken sign"),
    ("manhole", "taboo", None, "a rusted manhole cover on cracked pavement with weeds growing through"),
    ("station", "taboo", None, "a desolate train station platform with a rusted bench and a dark tunnel mouth"),
    ("houzuki", "taboo", None, "a traditional japanese mansion gate with a tiled roof, dark wooden doors and overgrown garden"),
    ("garrison", "taboo", None, "an abandoned military barracks with broken windows and collapsed roof beams"),
    ("hospital", "taboo", None, "an abandoned hospital wing with boarded windows and a faded cross sign"),
    ("shelter", "taboo", None, "a concrete air raid shelter entrance with a heavy steel door and sandbags"),
    ("alley", "taboo", None, "a narrow gloomy back alley with pipes on the wall, puddles and a flickering lamp"),
]

BASE_SEED = 20260914
limit = int(sys.argv[1]) if len(sys.argv) > 1 else len(SUBJECTS)

done, failed = [], []
started = time.time()
for index, (key, district, offset, subject) in enumerate(SUBJECTS[:limit]):
    seed = BASE_SEED + (offset if offset is not None else 900 + index * 137)
    target = OUT / f"{key}.png"
    prompt = f"{subject}, {STYLE}, {TINT[district]}"
    stamp = time.strftime("%H:%M:%S")
    print(f"[{index + 1}/{limit}] {stamp} {key} (seed {seed}) ...", flush=True)
    try:
        run(prompt, NEGATIVE, seed, size=512, steps=28, cfg=7.0,
            ckpt="Counterfeit-V3.0_fp16.safetensors", sampler="dpmpp_2m", batch=1,
            out_paths=[str(target)])
        done.append(key)
    except Exception as error:  # noqa: BLE001
        failed.append(key)
        print(f"    失败 {key}: {type(error).__name__} {error}", flush=True)

minutes = (time.time() - started) / 60
print(f"\n完成 {len(done)}/{limit} 张，用时 {minutes:.1f} 分钟")
if failed:
    print("失败：", "、".join(failed))
