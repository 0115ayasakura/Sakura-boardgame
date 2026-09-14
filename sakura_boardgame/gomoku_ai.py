"""五子棋 AI：棋型评估 + 候选点生成 + alpha-beta 搜索。

纯标准库实现。难度由搜索深度与候选宽度控制；
aggression（0~1）控制进攻/防守倾向，用于让角色性格影响棋风。
"""
from __future__ import annotations

import random
import time
from typing import Any

SIZE = 15
DIRECTIONS = ((0, 1), (1, 0), (1, 1), (1, -1))

# 棋型分值：(连子数, 开放端数) → 分
_PATTERN_SCORE = {
    (5, 0): 1_000_000, (5, 1): 1_000_000, (5, 2): 1_000_000,
    (4, 2): 50_000, (4, 1): 6_000,
    (3, 2): 4_000, (3, 1): 500,
    (2, 2): 200, (2, 1): 30,
    (1, 2): 20, (1, 1): 5,
}
_TIME_LIMIT = 1.6

DIFFICULTY = {1: "入门", 2: "进阶", 3: "困难"}


def _pattern_value(count: int, open_ends: int) -> int:
    return _PATTERN_SCORE.get((min(count, 5), open_ends), 0)


def _in_board(r: int, c: int) -> bool:
    return 0 <= r < SIZE and 0 <= c < SIZE


def _point_pattern(board: list[list[int]], r: int, c: int, code: int) -> tuple[int, int]:
    """假设在 (r,c) 落 code 子，返回四方向中最强的 (连子数, 开放端数)。"""
    best = (0, 0)
    board[r][c] = code
    try:
        for dr, dc in DIRECTIONS:
            count = 1
            open_ends = 0
            for sign in (1, -1):
                nr, nc = r + sign * dr, c + sign * dc
                while _in_board(nr, nc) and board[nr][nc] == code:
                    count += 1
                    nr += sign * dr
                    nc += sign * dc
                if _in_board(nr, nc) and board[nr][nc] == 0:
                    open_ends += 1
            if (count, open_ends) > best or (count >= 5 and best[0] < 5):
                best = (count, open_ends)
    finally:
        board[r][c] = 0
    return best


def _point_score(board: list[list[int]], r: int, c: int, code: int, aggression: float) -> int:
    """在 (r,c) 落子对 code 的价值：进攻分 ×aggression + 阻挡分 ×(1-aggression)。"""
    my_count, my_open = _point_pattern(board, r, c, code)
    attack = _pattern_value(my_count, my_open)
    opp_code = 3 - code
    opp_count, opp_open = _point_pattern(board, r, c, opp_code)
    defense = _pattern_value(opp_count, opp_open)
    return int(attack * aggression + defense * (1.0 - aggression))


def _candidates(board: list[list[int]], limit: int, rng: random.Random, noise: float) -> list[tuple[int, int, int]]:
    """候选点：已有棋子切比雪夫距离 2 内的空位，按启发分排序。"""
    has_stone = any(board[r][c] for r in range(SIZE) for c in range(SIZE))
    if not has_stone:
        mid = SIZE // 2
        return [(mid, mid, 0)]
    scored = []
    for r in range(SIZE):
        for c in range(SIZE):
            if board[r][c]:
                continue
            near = False
            for dr in (-2, -1, 0, 1, 2):
                for dc in (-2, -1, 0, 1, 2):
                    nr, nc = r + dr, c + dc
                    if _in_board(nr, nc) and board[nr][nc]:
                        near = True
                        break
                if near:
                    break
            if near:
                scored.append((r, c, 0))
    # 预打分用中性权重
    prelim = sorted(scored, key=lambda item: -_point_score(board, item[0], item[1], 1, 0.5))
    trimmed = prelim[:limit]
    result = []
    for r, c, _ in trimmed:
        jitter = int(rng.random() * noise)
        result.append((r, c, jitter))
    return result


def _apply(board: list[list[int]], r: int, c: int, code: int) -> None:
    board[r][c] = code


def _revert(board: list[list[int]], r: int, c: int) -> None:
    board[r][c] = 0


def _search(board: list[list[int]], me: int, depth: int, aggression: float,
            deadline: float, rng: random.Random, alpha: int = -10**9, beta: int = 10**9) -> tuple[int, int, int, int]:
    """返回 (分数, r, c, 结点数)。分数从我方视角。"""
    opp = 3 - me
    candidates = _candidates(board, 8, rng, noise=0)
    best_score = -10**9
    best_move = (candidates[0][0], candidates[0][1]) if candidates else (SIZE // 2, SIZE // 2)
    nodes = 0
    for r, c, _ in candidates:
        if time.monotonic() > deadline:
            break
        _apply(board, r, c, me)
        count, open_ends = _point_pattern(board, r, c, me)
        if count >= 5:
            score = 1_000_000
        else:
            if depth <= 1:
                score = _point_score(board, r, c, me, aggression)
            else:
                reply = _search(board, opp, depth - 1, 1.0 - aggression, deadline, rng, -beta, -alpha)
                score = -reply[0]
        _revert(board, r, c)
        nodes += 1
        if score > best_score:
            best_score = score
            best_move = (r, c)
        if best_score > alpha:
            alpha = best_score
        if alpha >= beta:
            break
    return best_score, best_move[0], best_move[1], nodes


def _describe(board: list[list[int]], r: int, c: int, code: int) -> str:
    count, open_ends = _point_pattern(board, r, c, code)
    opp_code = 3 - code
    opp_count, _ = _point_pattern(board, r, c, opp_code)
    if count >= 5:
        return "这一手连成五子"
    names = {(4, 2): "活四", (4, 1): "冲四", (3, 2): "活三", (3, 1): "眠三", (2, 2): "活二"}
    mine = names.get((min(count, 4), open_ends))
    theirs = names.get((min(opp_count, 4), 2)) or names.get((min(opp_count, 4), 1))
    if mine and opp_count >= 3:
        return f"既挡住了对方的{theirs}，又顺势做出{mine}"
    if mine:
        return f"在此做出{mine}"
    if theirs:
        return f"先拆掉对方的{theirs}"
    return "占住连接要点，慢慢铺棋"


def choose_move(board: list[list[int]], my_code: int, level: int,
                aggression: float = 0.45) -> dict[str, Any]:
    """为 my_code 选择落点。level: 1 入门 / 2 进阶 / 3 困难。返回 {row, col, reason}（1-based）。"""
    level = max(1, min(3, int(level)))
    rng = random.Random()
    deadline = time.monotonic() + _TIME_LIMIT
    work = [row[:] for row in board]
    candidates = _candidates(work, 10, rng, noise=0)
    if not candidates:
        raise ValueError("棋盘已满")

    # 必胜：直接连五
    for r, c, _ in candidates:
        _apply(work, r, c, my_code)
        count, _ = _point_pattern(work, r, c, my_code)
        _revert(work, r, c)
        if count >= 5:
            return {"row": r + 1, "col": c + 1, "reason": "这一手连成五子"}

    # 必防：对方下一手能连五的点
    opp_code = 3 - my_code
    for r, c, _ in candidates:
        _apply(work, r, c, opp_code)
        count, _ = _point_pattern(work, r, c, opp_code)
        _revert(work, r, c)
        if count >= 5:
            return {"row": r + 1, "col": c + 1,
                    "reason": "对方已经摆出四连，这一步不能不放"}

    if level == 1:
        scored = [( _point_score(work, r, c, my_code, aggression) + rng.randint(0, 120), r, c)
                  for r, c, _ in candidates]
        scored.sort(reverse=True)
        pool = scored[:4]
        _, r, c = pool[rng.randrange(len(pool))]
    else:
        depth = 2 if level == 2 else 3
        width = 10 if level == 2 else 8
        trimmed = candidates[:width]
        best_score = -10**9
        r, c = trimmed[0][0], trimmed[0][1]
        for cr, cc, _ in trimmed:
            if time.monotonic() > deadline:
                break
            _apply(work, cr, cc, my_code)
            reply = _search(work, opp_code, depth - 1, 1.0 - aggression, deadline, rng)
            _revert(work, cr, cc)
            score = -reply[0]
            if score > best_score:
                best_score = score
                r, c = cr, cc
    return {"row": r + 1, "col": c + 1, "reason": _describe(board, r, c, my_code)}
