"""五子棋 AI 回归：_point_pattern 还原、搜索超时的"无结果"传播。

直接跑：python -B boardgame_tests/test_gomoku_search.py
全部通过时输出 ALL TESTS PASSED。
"""
from __future__ import annotations

import random
import sys as _sys
import time
from pathlib import Path as _Path

_sys.path.insert(0, str(_Path(__file__).resolve().parents[1] / "sakura_boardgame"))
import gomoku_ai

SIZE = gomoku_ai.SIZE


def _empty() -> list[list[int]]:
    return [[0] * SIZE for _ in range(SIZE)]


def _midgame() -> list[list[int]]:
    """六颗子的小局面：谁都没有四连，也没有哪一点能一手成五。"""
    board = _empty()
    for r, c, code in ((7, 7, 1), (7, 8, 2), (6, 6, 1), (8, 9, 2), (5, 6, 1), (9, 8, 2)):
        board[r][c] = code
    return board


def _copy(board: list[list[int]]) -> list[list[int]]:
    return [row[:] for row in board]


def _stones(board: list[list[int]]) -> int:
    return sum(1 for row in board for cell in row if cell)


def test_point_pattern_restores_original_cell() -> None:
    """_point_pattern 是"假设在此落子"的探测函数：算完必须还原 (r,c) 调用前的值。

    旧实现 finally 无条件写 0：探测一个已有子的格子会把那颗子抹掉；而
    _search 是先 _apply 再调它，于是父着被清掉，递归根本看不到这层刚下的子。
    """
    board = _empty()
    before = _copy(board)
    assert gomoku_ai._point_pattern(board, 7, 7, 1) == (1, 2)
    assert board == before, "空位探测后 (7,7) 必须还是空位"

    board[7][8] = board[7][9] = 1
    before = _copy(board)
    assert gomoku_ai._point_pattern(board, 7, 7, 1) == (3, 2)
    assert board == before, "连子数按假想落子算，但既有棋子不能被改动"

    occupied = _empty()
    occupied[7][7] = 2
    occupied[7][8] = occupied[7][9] = 2
    before = _copy(occupied)
    assert gomoku_ai._point_pattern(occupied, 7, 7, 2) == (3, 2)
    assert occupied[7][7] == 2, "已占位探测把原来的子清成了 0"
    assert occupied == before

    foreign = _empty()
    foreign[7][7] = 1
    before = _copy(foreign)
    gomoku_ai._point_pattern(foreign, 7, 7, 2)
    assert foreign[7][7] == 1, "换一方探测时同样不能抹掉原来的子"
    assert foreign == before


def test_search_recursion_sees_stone_just_played() -> None:
    """_search 先 _apply 再递归：递归入口的石子数必须比上一层多 1。

    choose_move(level=3) 的调用链：根局面 N 子 → _search(depth=2) 入口 N+1
    → _search(depth=1) 入口 N+2。旧代码里 _point_pattern 把刚下的子清成 0，
    depth=1 入口只剩 N+1，等于递归看不到父着。
    """
    board = _midgame()
    base = _stones(board)
    original_search = gomoku_ai._search
    seen: list[tuple[int, int]] = []

    def recording(board_, me, depth, aggression, deadline, rng, alpha=-10**9, beta=10**9):
        seen.append((depth, _stones(board_)))
        return original_search(board_, me, depth, aggression, deadline, rng, alpha, beta)

    gomoku_ai._search = recording
    try:
        decision = gomoku_ai.choose_move(board, 2, 3, 0.45)
    finally:
        gomoku_ai._search = original_search

    assert 1 <= decision["row"] <= SIZE and 1 <= decision["col"] <= SIZE, decision
    assert board[decision["row"] - 1][decision["col"] - 1] == 0, decision
    depth2 = {count for depth, count in seen if depth == 2}
    depth1 = {count for depth, count in seen if depth == 1}
    assert depth2 == {base + 1}, f"depth=2 入口应比根多 1 子：{sorted(seen)}"
    assert depth1 == {base + 2}, f"depth=1 入口应再比 depth=2 多 1 子：{sorted(seen)}"


def test_search_deadline_before_candidates_is_no_result() -> None:
    """deadline 在第一个候选点之前就过期：_search 必须报"没有结果"。

    旧实现返回 best_score 初值 -10**9；父层再取负就是 +10**9，凭空变成必胜分。
    """
    board = _midgame()
    before = _copy(board)
    score, row, col, nodes = gomoku_ai._search(
        board, 2, 2, 0.45, time.monotonic() - 1.0, random.Random(0)
    )
    assert nodes == 0
    assert score is None, f"一手都没算，不能拿 -10**9 冒充分数：{score}"
    assert board == before


def test_child_timeout_not_negated_into_win() -> None:
    """子层超时（无结果）传到父层时，不能被取负成 +10**9 的伪胜分。"""
    board = _midgame()
    before = _copy(board)
    original_search = gomoku_ai._search
    past = time.monotonic() - 1.0

    def child_always_times_out(board_, me, depth, aggression, deadline, rng,
                              alpha=-10**9, beta=10**9):
        if depth <= 1:
            return original_search(board_, me, depth, aggression, past, rng, alpha, beta)
        return original_search(board_, me, depth, aggression, deadline, rng, alpha, beta)

    gomoku_ai._search = child_always_times_out
    try:
        score, row, col, nodes = original_search(
            board, 2, 2, 0.45, time.monotonic() + 1.0, random.Random(0)
        )
    finally:
        gomoku_ai._search = original_search

    assert nodes > 0, "父层连候选点都没试，测试没覆盖到传播路径"
    assert score is None, f"子层无结果被当成分数取负，出现伪胜分：{score}"
    assert board == before


def test_choose_move_skips_resultless_child() -> None:
    """choose_move 里某一手搜索超时无结果时，跳过它，不能让它压过正常分数。"""
    board = _midgame()
    before = _copy(board)
    fake_moves = [(0, 0, 0), (14, 14, 0)]
    real_candidates = gomoku_ai._candidates
    original_search = gomoku_ai._search
    calls = {"n": 0}

    def fake_candidates(board_, limit, rng, noise):
        return list(fake_moves)

    def fake_search(board_, me, depth, aggression, deadline, rng,
                    alpha=-10**9, beta=10**9):
        calls["n"] += 1
        if calls["n"] == 1:
            # 第一手：真实地"超时"，旧代码会返回 -10**9
            return original_search(board_, me, depth, aggression,
                                   time.monotonic() - 1.0, rng, alpha, beta)
        return (-100, 0, 0, 1)  # 真实有限分数；父层取负 = 100

    gomoku_ai._candidates = fake_candidates
    gomoku_ai._search = fake_search
    try:
        decision = gomoku_ai.choose_move(board, 2, 2, 0.45)
    finally:
        gomoku_ai._candidates = real_candidates
        gomoku_ai._search = original_search

    assert (decision["row"], decision["col"]) == (15, 15), (
        "第一手搜索无结果，不能被当成 +10**9 的必胜着；应改选第二手："
        f"{decision}"
    )
    assert board == before


def test_choose_move_api_tactics_and_time_bound() -> None:
    """保留原 API / 战术立即胜与挡 / 输入 board 不被改写 / 耗时上界。"""
    for level in (1, 2, 3):
        board = _midgame()
        before = _copy(board)
        started = time.monotonic()
        decision = gomoku_ai.choose_move(board, 2, level, 0.4)
        elapsed = time.monotonic() - started
        assert set(decision) == {"row", "col", "reason"}, decision
        assert isinstance(decision["reason"], str) and decision["reason"], decision
        assert 1 <= decision["row"] <= SIZE and 1 <= decision["col"] <= SIZE, decision
        assert board[decision["row"] - 1][decision["col"] - 1] == 0, decision
        assert board == before, f"level {level} 改写了调用方的棋盘"
        assert elapsed < 3.0, f"level {level} 耗时 {elapsed:.2f}s 超过上界"

    # 能连五就必须连五
    win = _empty()
    for col in (3, 4, 5, 6):
        win[7][col] = 2
    win[10][10] = 1
    before = _copy(win)
    decision = gomoku_ai.choose_move(win, 2, 2)
    assert (decision["row"], decision["col"]) in ((8, 3), (8, 8)), decision
    assert win == before

    # 对方四连必须挡
    block = _empty()
    for col in (3, 4, 5, 6):
        block[7][col] = 1
    block[10][10] = 2
    before = _copy(block)
    decision = gomoku_ai.choose_move(block, 2, 2)
    assert (decision["row"], decision["col"]) in ((8, 3), (8, 8)), decision
    assert block == before

    # 空盘落在中央
    fresh = _empty()
    before = _copy(fresh)
    decision = gomoku_ai.choose_move(fresh, 2, 2)
    assert (decision["row"], decision["col"]) == (8, 8), decision
    assert fresh == before

    # 时间预算压到 0：也必须马上返回合法着，而不是卡死或报错
    real_limit = gomoku_ai._TIME_LIMIT
    gomoku_ai._TIME_LIMIT = 0.0
    try:
        board = _midgame()
        before = _copy(board)
        started = time.monotonic()
        decision = gomoku_ai.choose_move(board, 2, 3, 0.4)
        elapsed = time.monotonic() - started
    finally:
        gomoku_ai._TIME_LIMIT = real_limit
    assert elapsed < 0.5, f"预算为 0 仍耗时 {elapsed:.2f}s"
    assert board == before
    assert board[decision["row"] - 1][decision["col"] - 1] == 0, decision


if __name__ == "__main__":
    for name, fn in sorted(globals().items()):
        if name.startswith("test_"):
            fn()
            print(f"ok  {name}")
    print("ALL TESTS PASSED")
