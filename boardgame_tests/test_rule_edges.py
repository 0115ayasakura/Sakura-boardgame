"""规则边界回归：只测引擎规则，不启动服务、网页或浏览器。

覆盖 2026-10-18 审查里的“自有已抵押地产、低现金”候选：
- 走回自己的抵押地产、现金不够赎回时，仍然必须能选“卖掉”；
- 现金够赎回时，菜单仍是“赎回 / 卖掉 / 跳过”；
- 未抵押地产的菜单仍保留“抵押”，不能被自动歇脚吞掉；
- 抵押期间 render() 不能再声称这块地还在收租（与 stats().income = 0 对齐）。

不使用正式存档，不依赖随机地图的具体种子：只取一张现成地产做定向结算。
"""
from __future__ import annotations

import sys as _sys
from pathlib import Path as _Path

_sys.path.insert(0, str(_Path(__file__).resolve().parents[1] / "sakura_boardgame"))
import engine
from engine import MonopolyGame


def _game_with_property(*, cash: int, mortgaged: bool, level: int = 0):
    """构造一张“玩家自己的地产”并把现金设成指定值，返回 (game, cell, price)。"""
    game = MonopolyGame(size=12, dice_sides=6, seed=1)
    cell = next(c for c in game.cells if c["type"] == "property" and c["tier"] == 1)
    cell.update({"owner": "user", "mortgaged": mortgaged, "level": level})
    game.cash["user"] = cash
    game.turn = "user"
    game.pending = None
    game._walk = None
    return game, cell, game.current_price(cell)


def test_own_mortgaged_property_low_cash_still_offers_sell() -> None:
    """现金不够赎回时，抵押地产也必须弹出售卖入口（旧实现直接“安心歇脚”）。"""
    game, cell, price = _game_with_property(
        cash=0, mortgaged=True, level=engine.MONOPOLY_MAX_LEVEL)
    redeem_cost = price * 3 // 5
    assert game.cash["user"] < redeem_cost, (game.cash, redeem_cost)

    story: list[str] = []
    outcome = game._resolve_landing("user", cell["id"], True, story)

    assert outcome["kind"] == "offer_menu", outcome
    assert game.pending is not None, "低现金抵押地产不应自动跳过菜单"
    assert game.pending["options"] == ["sell", "skip"], game.pending
    assert any("卖掉" in line for line in story), story
    # 现金不够赎，菜单说明不能承诺一个点不到的选项
    assert not any("可以赎回" in line for line in story), story

    before = game.cash["user"]
    result = game.decide("user", "sell")
    assert result["action"] == "sell"
    assert cell["owner"] is None and cell["level"] == 0 and cell["mortgaged"] is False
    assert game.cash["user"] == before + price // 6, (game.cash["user"], before, price)
    assert game.move_count == 0, "菜单只是本回合内的决策，不能额外增加回合数"
    assert game.turn == "sakura", "卖掉后应正常换手，不能卡在待决策"


def test_own_mortgaged_property_affordable_redeem_keeps_menu() -> None:
    """现金够赎回时，菜单仍是赎回 / 卖掉 / 跳过。"""
    game, cell, _price = _game_with_property(cash=10**6, mortgaged=True)
    story: list[str] = []
    outcome = game._resolve_landing("user", cell["id"], True, story)

    assert outcome["kind"] == "offer_menu", outcome
    assert game.pending is not None and game.pending["options"] == ["redeem", "sell", "skip"]
    assert any("可以赎回" in line for line in story), story


def test_own_unmortgaged_property_still_offers_mortgage() -> None:
    """未抵押地产即使满级、现金为 0，也仍应显示抵押（不能自动歇脚）。"""
    game, cell, _price = _game_with_property(
        cash=0, mortgaged=False, level=engine.MONOPOLY_MAX_LEVEL)
    story: list[str] = []
    outcome = game._resolve_landing("user", cell["id"], True, story)

    assert outcome["kind"] == "offer_menu", outcome
    assert game.pending is not None
    assert game.pending["options"] == ["mortgage", "sell", "skip"], game.pending
    assert any("抵押" in line for line in story), story


def test_render_mortgaged_property_does_not_claim_rent() -> None:
    """抵押地产在 render() 里不能继续写“租¥X”（stats().income 已经归零）。"""
    game, cell, _price = _game_with_property(cash=0, mortgaged=True)
    assert game.stats()["user"]["income"] == 0
    line = next(item for item in game.render().splitlines() if item.startswith("地产一览："))
    assert "抵押中" in line, line
    assert "租¥" not in line, line

    # 未抵押时仍要能看见租金，别把正常展示一起删掉
    cell["mortgaged"] = False
    line = next(item for item in game.render().splitlines() if item.startswith("地产一览："))
    assert "租¥" in line, line


if __name__ == "__main__":
    for name, fn in sorted(globals().items()):
        if name.startswith("test_"):
            fn()
            print(f"ok  {name}")
    print("ALL TESTS PASSED")
