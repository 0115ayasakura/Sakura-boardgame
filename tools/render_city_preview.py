"""渲染城市布局预览：python render_city_preview.py → boardgame_art/city_layout_preview.html

画的是"城市规划图"：街区色块、河与公园、街道网络、成片建筑、以及棋子行走的路线，
用来判断城市结构是否合理（不是最终美术）。
"""
from __future__ import annotations

import html
import math
import pathlib
import sys

from pathlib import Path as _Path

ROOT = _Path(__file__).resolve().parents[1]   # 仓库根目录
sys.path.insert(0, str(ROOT / "sakura_boardgame"))
import city_map as cm  # noqa: E402

S = 78          # 1 格 = 78px
OUT = pathlib.Path(__file__).parent / "city_layout_preview.html"

# 绿地与建筑都由 city_map 提供，网页和预览用同一份数据
GREEN_ZONES = cm.GREEN_ZONES


def px(x: float, y: float) -> tuple[float, float]:
    return (x * S, y * S)


def poly_d(pts, close=True) -> str:
    out = " ".join(f"{'M' if i == 0 else 'L'}{px(*p)[0]:.1f},{px(*p)[1]:.1f}" for i, p in enumerate(pts))
    return out + (" Z" if close else "")


def main() -> None:
    cells, edges, ring, branches = cm.build_city()
    by_id = {c["id"]: c for c in cells}
    W, H = cm.WORLD_W * S, cm.WORLD_H * S

    # 所有街道折线（含外环）——画路网用
    streets = []
    for name, spec in cm.STREETS.items():
        pts = list(spec["pts"]) + ([spec["pts"][0]] if spec["closed"] else [])
        streets.append(pts)

    svg = [f'<svg viewBox="0 0 {W:.0f} {H:.0f}" xmlns="http://www.w3.org/2000/svg">']
    svg.append(f'<rect width="{W:.0f}" height="{H:.0f}" fill="#eef1f5"/>')
    svg.append(f'<rect x="10" y="10" width="{W - 20:.0f}" height="{H - 20:.0f}" rx="34" fill="#f7f9fb" stroke="#dfe4ea" stroke-width="2"/>')

    # 绿地
    for pts in GREEN_ZONES.values():
        svg.append(f'<path d="{poly_d(pts)}" fill="#d5e8d4" stroke="#bcd9bb" stroke-width="1.6"/>')
    # 水域：右下角的海 + 两处水塘
    svg.append(f'<path d="{poly_d(cm.SEA_POLY)}" fill="#cfe4f2" stroke="#a9cbe4" stroke-width="1.8"/>')
    for px0, py0, rx, ry in cm.PONDS:
        cx, cy = px(px0, py0)
        svg.append(f'<ellipse cx="{cx:.0f}" cy="{cy:.0f}" rx="{rx * S:.0f}" ry="{ry * S:.0f}" fill="#cfe4f2" stroke="#a9cbe4" stroke-width="1.8"/>')

    # 街区色晕
    for key, info in cm.DISTRICTS.items():
        svg.append(f'<path d="{poly_d(info["poly"])}" fill="{info["color"]}" opacity=".12"/>')

    # 建筑：街道之间成片铺（顶面 + 接地投影），让城市读起来是"整体"
    blocks = [(b["x"], b["y"], b["w"], b["h"], b["k"]) for b in cm.buildings()]
    for bx, by, bw, bh, k in blocks:
        cx, cy = px(bx, by)
        shade = 226 + int(k * 18)
        svg.append(f'<rect x="{cx - bw * S / 2 + 2:.1f}" y="{cy - bh * S / 2 + 3:.1f}" width="{bw * S:.1f}" height="{bh * S:.1f}" '
                   f'rx="2" fill="#5c6b88" opacity=".13"/>')
        svg.append(f'<rect x="{cx - bw * S / 2:.1f}" y="{cy - bh * S / 2:.1f}" width="{bw * S:.1f}" height="{bh * S:.1f}" '
                   f'rx="2" fill="rgb({shade},{shade + 2},{shade + 6})" stroke="#c3cbd6" stroke-width=".7"/>')

    # 街道网络（浅灰城市街道）
    for pts in streets:
        svg.append(f'<path d="{poly_d(pts, close=False)}" fill="none" stroke="#c9d2dc" stroke-width="{0.26 * S:.0f}" '
                   f'stroke-linecap="round" stroke-linejoin="round"/>')
        svg.append(f'<path d="{poly_d(pts, close=False)}" fill="none" stroke="#eef1f5" stroke-width="{0.06 * S:.0f}" '
                   f'stroke-dasharray="6 8"/>')

    # 行走路线：主环（明亮主角层）+ 支街（金色）
    ring_set = set(ring)
    for chain in branches:
        pts = [(by_id[i]["x"], by_id[i]["y"]) for i in chain]
        svg.append(f'<path d="{poly_d(pts, close=False)}" fill="none" stroke="#b8913c" stroke-width="17" stroke-linecap="round"/>')
        svg.append(f'<path d="{poly_d(pts, close=False)}" fill="none" stroke="#f4c96a" stroke-width="12" stroke-linecap="round"/>')
        # 行进方向箭头：从靠外环的一端指向另一端（进入支街的方向）
        ordered = list(chain)
        if ordered[-1] in ring_set and ordered[0] not in ring_set:
            ordered.reverse()
        for frac in (0.3, 0.62, 0.88):
            idx = min(len(ordered) - 2, max(0, int(frac * (len(ordered) - 1))))
            a = by_id[ordered[idx]]
            b = by_id[ordered[idx + 1]]
            ax, ay = px(a["x"], a["y"])
            bx, by = px(b["x"], b["y"])
            ang = math.degrees(math.atan2(by - ay, bx - ax))
            svg.append(f'<g transform="translate({ax:.1f},{ay:.1f}) rotate({ang:.1f})">'
                       f'<path d="M-6 -5 L7 0 L-6 5z" fill="#ffffff" stroke="#8a6a1e" stroke-width="1.3"/></g>')
    ring_pts = [(by_id[i]["x"], by_id[i]["y"]) for i in ring] + [(by_id[ring[0]]["x"], by_id[ring[0]]["y"])]
    svg.append(f'<path d="{poly_d(ring_pts, close=False)}" fill="none" stroke="#3f4d63" stroke-width="30" stroke-linecap="round" stroke-linejoin="round" opacity=".92"/>')
    svg.append(f'<path d="{poly_d(ring_pts, close=False)}" fill="none" stroke="#8fb8dd" stroke-width="25" stroke-linecap="round" stroke-linejoin="round"/>')
    svg.append(f'<path d="{poly_d(ring_pts, close=False)}" fill="none" stroke="#ffffff" stroke-width="17" stroke-linecap="round" stroke-linejoin="round"/>')
    svg.append(f'<path d="{poly_d(ring_pts, close=False)}" fill="none" stroke="#7fa9d2" stroke-width="3" stroke-dasharray="11 13" stroke-linecap="round"/>')

    # 区域名标牌（放在街区质心）
    for key, info in cm.DISTRICTS.items():
        cx = sum(p[0] for p in info["poly"]) / len(info["poly"])
        cy = sum(p[1] for p in info["poly"]) / len(info["poly"])
        sx, sy = px(cx, cy)
        width = len(info["label"]) * 18 + 46
        svg.append(f'<g transform="translate({sx:.0f},{sy:.0f})">'
                   f'<rect x="{-width / 2:.0f}" y="-19" width="{width}" height="38" rx="19" fill="#fff" opacity=".95" '
                   f'stroke="{info["color"]}" stroke-width="2.5"/>'
                   f'<text x="0" y="7" font-size="20" font-weight="700" fill="{info["color"]}" '
                   f'text-anchor="middle" letter-spacing="3">{info["label"]}</text></g>')
    svg.append("</svg>")

    # 地标标记
    marks = []
    for cell in cells:
        x, y = px(cell["x"], cell["y"])
        color = cm.DISTRICTS[cell["group"]]["color"] if cell.get("group") else "#8a90ab"
        if cell["type"] == "start":
            color = "#e8a91f"
        price = f'<span class="p">¥{cell["price"]}</span>' if cell["price"] else ""
        marks.append(f'<div class="cell" style="left:{x:.0f}px;top:{y:.0f}px;--c:{color}">'
                     f'<div class="dot"></div><div class="lab">{html.escape(cell["name"])}{price}</div></div>')

    counts: dict[str, int] = {}
    for cell in cells:
        counts[cell["type"]] = counts.get(cell["type"], 0) + 1
    summary = "、".join(f"{k} {v}" for k, v in sorted(counts.items(), key=lambda kv: -kv[1]))

    OUT.write_text(f"""<!DOCTYPE html><html lang="zh-CN"><head><meta charset="utf-8">
<title>第二横滨市 · 城市规划图（{len(cells)} 格）</title><style>
  body {{ background:#eef3fa; font-family:"Segoe UI","Microsoft YaHei",sans-serif; color:#2f3a52; padding:14px 18px 20px; }}
  h1 {{ font-size:18px; letter-spacing:3px; margin-bottom:4px;
    background:linear-gradient(90deg,#4a86c8,#d95a72); -webkit-background-clip:text; background-clip:text; color:transparent; }}
  .lead {{ font-size:12px; color:#6b7590; margin-bottom:10px; line-height:1.75; max-width:1500px; }}
  .lead b {{ color:#2f3a52; }}
  #stage {{ position:relative; width:{W:.0f}px; height:{H:.0f}px; border-radius:18px; overflow:hidden;
    box-shadow:0 10px 34px rgba(70,100,150,.22); background:#eef1f5; }}
  #stage svg {{ position:absolute; inset:0; width:100%; height:100%; }}
  .cell {{ position:absolute; transform:translate(-50%,-50%); }}
  .cell .dot {{ width:14px; height:14px; border-radius:50%; background:#fff; border:3.5px solid var(--c);
    box-shadow:0 2px 6px rgba(45,75,125,.4); margin:0 auto; }}
  .cell .lab {{ position:absolute; left:50%; top:12px; transform:translateX(-50%); white-space:nowrap;
    background:rgba(255,255,255,.95); border-left:3px solid var(--c); border-radius:7px;
    padding:2px 7px 3px; font-size:11px; font-weight:700; box-shadow:0 2px 6px rgba(45,75,125,.22); }}
  .cell .lab .p {{ color:#c98a10; margin-left:4px; font-weight:800; }}
</style></head><body>
  <h1>第二横滨市 · 城市规划图（{len(cells)} 格）</h1>
  <div class="lead">
    <b>街道网络</b>：外环干道（有折角、疏密不均）+ 两条中央主动脉（横街/纵街）+ 7 条支街；
    支街既接外环也接其他支街 → 地图是有多个回路的<b>网络</b>，岔路能连岔路，不是一圈环线。
    <b>蓝色</b>是主行走路线，<b>金色</b>是支街。<b>灰白方块</b>是街区之间的成片建筑（街道给它留了空隙）。
    构成：{summary}（地产 {counts.get('property', 0)} 块、事件/抽卡格 {counts.get('rest', 0) + counts.get('navi', 0)} 个）。
    校验器 <b>city_map.py</b> 自动断言：地标都在自己街区内、无一落水、间距达标、无死胡同、全图连通。
  </div>
  <div id="stage">{''.join(svg)}{''.join(marks)}</div>
</body></html>""", encoding="utf-8")
    print("saved:", OUT, "| buildings:", len(blocks), "| cells:", len(cells))


if __name__ == "__main__":
    main()
