from __future__ import annotations

import importlib.util
from pathlib import Path

import pytest


_spec = importlib.util.spec_from_file_location("boardgame_guide", Path(__file__).resolve().parents[1] / "guide.py")
assert _spec is not None and _spec.loader is not None
guide = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(guide)


@pytest.mark.parametrize("kind", ["monopoly", "gomoku"])
def test_builtin_guide_keeps_action_and_decision_boundaries_after_filtering(tmp_path: Path, kind: str) -> None:
    text, source = guide.load(lambda relative: tmp_path / relative)
    assert source == "内置通用版"
    prompt = guide.for_game(text, kind)
    # These are action/provenance constraints, not snapshots of all dialogue copy.
    assert "先完成自己当前合法的选择" in prompt
    assert "只问状态就直接回答，不额外走棋" in prompt
    assert "legalAction" in prompt and "token" in prompt
    assert "不能用算法代走" in prompt
    assert "不能声称是自己选的" in prompt
    assert "boardgame_auto" not in prompt
    assert "每走一步先解说再动作" not in prompt
    assert "把那手棋说成你自己的判断" not in prompt
    assert ("【连续选择】" in prompt) is (kind == "monopoly")
    assert ("【落子】" in prompt) is (kind == "gomoku")
