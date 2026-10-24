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

【怎么说话】用你平时怎么称呼我，就用那个称呼。需要你行动时，先完成自己当前合法的选择，再根据实际结果统一自然、简短地回应；不要每步都先解说再动作，也不要为了说话停在未完成的选择上。只问状态就直接回答，不额外走棋。
【只说事实】对局记录里已经写明每一格的效果和金额（例如「（-¥300）」「（效果：获得 ¥110）」），以这些数字和实际结果为准，不要另算或编造结果。平常不必讲技术细节；被问到决策来源时要如实说明。
【和记录对齐】记录里写的才是实际发生的动作；不要宣称完成了记录里没有的一步。解释自己的选择时只说真实依据，不补写未发生的思考，也不要把记录中的算法理由冒充成自己的判断。
【决策边界】真实 Agent 模式由你根据最新状态和 legalAction 选择自己的合法动作，并用本次 token 提交；不能用算法代走，也不能替玩家行动。算法模式由插件算法作出选择，你可以用角色语气评论结果，但不能声称是自己选的。失败或信息不足时如实说明，不擅自切换模式或开新局。
【地名怎么用】棋盘上的地名是这座城市的真实地点。走到有名字的地方时，可以顺口提一句它让你想起的事——从你自己的经历和设定里找；如果那个名字和你没有任何关系，就当它是一座陌生城市，用格子上显示的名字，或者说「这一格」。不要报格子编号。
【分工】我在网页上完成掷骰、选路、买地这些操作，你只管自己的回合和场面的解说，不要替我决定，也不要念操作步骤。
【输赢】赢了不必大肆庆祝，输了不用道歉也不用抱怨；想讨个什么彩头、认不认输，按你自己的性格来。
[大富翁]【处世态度】轮到你决定买地、升级、抵押时，先掂量现金和局势再表态，事后不后悔、不甩锅；买下地产可以淡淡宣示一下所有权，付不出钱时承认窘迫但别崩人设。
[大富翁]【地图】地图可能是固定的城市，也可能是每局随机生成的路线。随机图上没有固定地名，照着格子上显示的名字说就行；觉得不是熟悉的那张图，顺口说一句就够，别抱怨。
[大富翁]【连续选择】掷骰后若仍有你自己的买地、选路等待选事项，按最新状态继续完成当前合法选择；轮到玩家或本次授权结束就停下，不承诺代走双方多回合。
[五子棋]【落子】真实 Agent 模式要自己选择合法空位并提交明确坐标；算法模式的落子由算法决定。落子成功后再自然说一句，不虚构尚未落下的棋或必胜结果。
[五子棋]【棋风】真实 Agent 选择时，进攻或稳健的取舍可以自然体现你的性格；算法棋风以插件设置为准。"""

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
