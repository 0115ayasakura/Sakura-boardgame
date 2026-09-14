"""把 poi 插图的白边裁掉：AI 出的图主体只占中间一小块，缩到 62px 图钉里就看不清了。

用阈值找内容包围盒（白底 → alpha/亮度阈值），裁完四周留一点点边距再存回原文件。
用法：pyembed\\python.exe -B boardgame_art/trim_poi.py
"""
from __future__ import annotations

import pathlib

from PIL import Image, ImageChops

POI = ROOT / "sakura_boardgame" / "art" / "poi"
PAD = 6            # 裁完保留的边距（像素）
THRESHOLD = 16     # 与纯白的容差，用来生成"内容掩码"


def content_box(image: Image.Image) -> tuple[int, int, int, int] | None:
    """内容包围盒：把接近纯白的部分当背景挖掉。"""
    gray = image.convert("L")
    mask = gray.point(lambda v: 255 if v < 255 - THRESHOLD else 0)
    return mask.getbbox()


def main() -> None:
    total = 0
    for path in sorted(POI.glob("*.png")):
        with Image.open(path) as raw:
            image = raw.convert("RGB")
            box = content_box(image)
            if not box:
                print("跳过（整张都是白）:", path.name)
                continue
            x0, y0, x1, y1 = box
            x0, y0 = max(0, x0 - PAD), max(0, y0 - PAD)
            x1, y1 = min(image.width, x1 + PAD), min(image.height, y1 + PAD)
            before = image.size
            cropped = image.crop((x0, y0, x1, y1))
            # 统一输出成正方形，网页里 object-fit:cover 才不会把主体裁掉
            side = max(cropped.size)
            canvas = Image.new("RGB", (side, side), (255, 255, 255))
            canvas.paste(cropped, ((side - cropped.width) // 2, (side - cropped.height) // 2))
            canvas.save(path)
            total += 1
            print(f"{path.name}: {before} -> {canvas.size}")
    print("裁剪完成", total, "张")


if __name__ == "__main__":
    main()
