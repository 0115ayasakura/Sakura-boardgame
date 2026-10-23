"""桌游模式的通用引导（换角色不用改任何角色卡）。

以前这些内容写在 `角色卡补充.txt` 里，用户得手动粘进**每个**角色的角色卡；现在由插件
在有对局时逐轮注入，任何角色都自动适用，卡里一个字都不用加。

想定制：
- 全局改动 → 插件数据目录放 `guides/default.md`
- 只给某个角色改 → 放 `guides/<角色id>.md`（优先于 default）
文件里以 `[大富翁]` 或 `[五子棋]` 开头的行只在对应游戏里注入，其余行两个游戏都用。
注意这些文本是**给模型的行为要求**，不是给用户看的说明，所以要写成对她的指令。
"""
from __future__ import annotations

import re
from pathlib import Path
from typing import Any, Callable

# 通用文本：只写"任何角色都成立"的做法，具体桥段（走到某地会想起什么）交给她自己的设定去填。
# 地名一律自指，不点名第二横滨市的任何一格 —— 换了主题包、换成随机图都不用改这份文本。
DEFAULT_TEXT = """你在和我玩桌面游戏。下面是你这次要做的事，按它来，但不要把这条规则本身念出来。

【怎么说话】用你平时怎么称呼我，就用那个称呼；每走一步先解说再动作，像坐在对面的人，不要像在念操作说明。
【只念事实】对局记录里已经写明每一格的效果和金额（例如「（-¥300）」「（效果：获得 ¥110）」），直接引用那个数字，不要自己心算，也不要提期望值、概率、引擎、程序判定这类说法。
【和记录对齐】记录里写的才是你真正做了什么——连"她的判断：…"这种理由都在里面。解说、以及事后解释你为什么那么走，都要照着它说；不要另编一套，也不要宣称自己走了记录里没有的一步。
【地名怎么用】棋盘上的地名是这座城市的真实地点。走到有名字的地方时，可以顺口提一句它让你想起的事——从你自己的经历和设定里找；如果那个名字和你没有任何关系，就当它是一座陌生城市，用格子上显示的名字，或者说「这一格」。不要报格子编号。
【分工】我在网页上完成掷骰、选路、买地这些操作，你只管自己的回合和场面的解说，不要替我决定，也不要念操作步骤。
【输赢】赢了不必大肆庆祝，输了不用道歉也不用抱怨；想讨个什么彩头、认不认输，按你自己的性格来。
[大富翁]【处世态度】轮到你决定买地、升级、抵押时，先掂量现金和局势再表态，事后不后悔、不甩锅；买下地产可以淡淡宣示一下所有权，付不出钱时承认窘迫但别崩人设。
[大富翁]【地图】地图可能是固定的城市，也可能是每局随机生成的路线。随机图上没有固定地名，照着格子上显示的名字说就行；觉得不是熟悉的那张图，顺口说一句就够，别抱怨。
[大富翁]【自动推进】对方说「你们自己下一局」「自动走几回合」「你俩玩给我看」时，调用 `boardgame_auto(rounds=N)`
——**一次调用就能把双方 N 个回合真的走完**（不是模拟），返回的 story 就是经过。照它解说即可，
别再用 boardgame_roll 一回合一次地慢慢走（那样很容易调到一半就断了）。
[五子棋]【落子】棋力由插件代算，但落子理由要用你自己的口吻说出来——把那手棋说成你自己的判断，不要说成是引擎算的；下棋前安静想一想，别不吭声就落子。
[五子棋]【棋风】进攻或稳健的样子从你的性格里自然流露即可（插件设置和网页侧栏也能调）。"""

_MARKER = re.compile(r"^\[(大富翁|五子棋)\]\s*")
_KIND_BY_MARKER = {"大富翁": "monopoly", "五子棋": "gomoku"}
_SAFE_ID = re.compile(r"[^0-9A-Za-z_.\-]")


def for_game(text: str, kind: str) -> str:
    """按游戏类型筛掉另一边的行（顺便去掉行首标记）。"""
    kept: list[str] = []
    for line in text.splitlines():
        match = _MARKER.match(line)
        if match:
            if _KIND_BY_MARKER[match.group(1)] != kind:
                continue
            line = _MARKER.sub("", line)
        if line.strip() or (kept and kept[-1].strip()):
            kept.append(line)
    return "\n".join(kept).strip()


def _data_file(data_path: Callable[[str], Any], relative: str) -> Path | None:
    try:
        return Path(str(data_path(relative)))
    except Exception:
        return None


def load(data_path: Callable[[str], Any], character_id: str = "") -> tuple[str, str]:
    """返回 (引导文本, 来源说明)。读不到自定义文件就用内置通用版。"""
    candidates: list[tuple[str, str]] = []
    safe = _SAFE_ID.sub("", str(character_id or ""))[:64]
    if safe:
        candidates.append((f"guides/{safe}.md", f"角色专属文件 guides/{safe}.md"))
    candidates.append(("guides/default.md", "数据目录里的 guides/default.md"))
    for relative, source in candidates:
        path = _data_file(data_path, relative)
        if path is None or not path.is_file():
            continue
        try:
            text = path.read_text(encoding="utf-8", errors="replace").strip()
        except OSError:
            continue
        if text:
            return text, source
    return DEFAULT_TEXT, "内置通用版"
