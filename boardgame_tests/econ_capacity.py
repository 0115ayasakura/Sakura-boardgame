"""棋盘价格与经济容量：29 处地产总标价 vs 一局里流动的钱。"""
import sys
from collections import Counter
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "sakura_boardgame"))
import engine as E  # noqa: E402
import city_map  # noqa: E402

cells, _, main_path, _ = city_map.build_city()
props = [c for c in cells if c["type"] == "property"]
ring = set(main_path)
print(f"地产 {len(props)} 处（主环 {sum(1 for c in props if c['id'] in ring)}，支街 "
      f"{sum(1 for c in props if c['id'] not in ring)}）")
print("基础价分布：", dict(sorted(Counter(c["price"] for c in props).items())))
total = sum(c["price"] for c in props)
print(f"全买下来需要 ¥{total}")
print(f"涨价上限后总价 ¥{int(total * E.MONOPOLY_PRICE_CAP)}")

sides = len(E.PLAYERS)
start = E.MONOPOLY_START_CASH * sides
print(f"\n一局里玩家的钱：起始 ¥{start}"
      f" + 工资（每人约 1 圈 ¥{E.MONOPOLY_PASS_START}×2 = ¥{E.MONOPOLY_PASS_START * sides}）"
      f" + 事件卡净（实测约 +¥240 双方合计）")
print(f"  ≈ ¥{start + E.MONOPOLY_PASS_START * sides + 240} 可支配（还得扣掉税/车费约 ¥570）"
      f" → 实际能买下约 {int((start + E.MONOPOLY_PASS_START * sides + 240 - 570) / (total / len(props)))} 处地产")

print("\n租金曲线（等级 0 → 1，垄断 ×2；按涨价上限算最高值）：")
for price in (100, 200, 300):
    for level in (0, 1):
        base = (price // 2) * (2 ** level)
        cap = (int(price * E.MONOPOLY_PRICE_CAP) // 2) * (2 ** level)
        print(f"  标价 ¥{price} 等级 {level}：租金 ¥{base}（涨价后 ¥{cap}），垄断 ×2 → ¥{cap * 2}")
print(f"\n一局每人领到的工资 ¥{E.MONOPOLY_PASS_START}×约 1 圈 = ¥{E.MONOPOLY_PASS_START}，"
      f"对照：踩一次开发过的最高级地产最多付 ¥{(int(300 * E.MONOPOLY_PRICE_CAP) // 2) * 2 * 2}")
