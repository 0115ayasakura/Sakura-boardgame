"""事件卡收益平衡速查：python card_balance.py

改完 engine.py 里的 EVENT_CARDS 后跑一下，确认期望值仍在你想要的区间。
"""
import sys
from collections import Counter

from pathlib import Path as _Path

ROOT = _Path(__file__).resolve().parents[1]   # 仓库根目录
sys.path.insert(0, str(ROOT / "sakura_boardgame"))
from engine import EVENT_CARDS

print(f"共 {len(EVENT_CARDS)} 张事件卡\n")
kinds = Counter()
total = 0
for card in EVENT_CARDS:
    eff = card["effect"]
    kinds[eff["type"]] += 1
    if eff["type"] == "cash":
        total += eff["amount"]

cash_cards = [c for c in EVENT_CARDS if c["effect"]["type"] == "cash"]
pos = [c["effect"]["amount"] for c in cash_cards if c["effect"]["amount"] > 0]
neg = [c["effect"]["amount"] for c in cash_cards if c["effect"]["amount"] < 0]
print("效果类型分布：", dict(kinds))
print(f"现金卡 {len(cash_cards)} 张，合计 {total:+d}，均值 {total / len(cash_cards):+.1f}")
print(f"  正收益 {len(pos)} 张，均值 {sum(pos) / len(pos):+.1f}")
print(f"  负收益 {len(neg)} 张，均值 {sum(neg) / len(neg):+.1f}")
print("\n其他效果（非现金）：")
for card in EVENT_CARDS:
    if card["effect"]["type"] != "cash":
        print(f"  {card['title']}: {card['effect']}")
