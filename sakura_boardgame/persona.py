"""打法性格：让角色的**角色卡正文**决定她在棋盘上的打法。

Sakura 的 host 服务 `sakura.host.character.current()["systemPrompt"]` 就是角色卡正文
（`characters/<角色>/card.md`，夜乃桜那张约 2.2 万字）。这里把正文里描述性格的措辞
打分，再换算成四个具体策略参数——所以用户改角色卡就能改她的棋风，不用碰代码。

四个倾向（各 0~1）：
  caution   冷静/克制/理性/掂量 → 留底金，不乱花钱
  ambition  好胜/胜负欲/不服输   → 愿意升级、爱抢街区
  curiosity 好奇/闲逛/孩子气/猫  → 更愿意拐进支街逛
  kindness  温柔/体贴/照顾/宠    → 领先时偶尔让玩家一手（"手下留情"）

四个参数：
  reserve    买地前要留的现金底线
  upgrade    走到自己地上时愿意升级的概率
  branch     岔路上愿意拐支街的概率（否则尽量沿主环）
  mercy      明显领先时"这一块让给你"的概率
"""
from __future__ import annotations

import json
import re
from pathlib import Path
from typing import Any

# ---- 关键词表：改这里就能改她对性格措辞的敏感度 ----
# 只用**有区分度**的说法：像 冷静/安静/判断/计划/第一/玩 这种在长卡里到处都是的词会误判
# （实测夜乃桜那张卡里"第一"出现 12 次全是序数、"判断"是普通动词），所以都不收。
TRAITS: dict[str, dict[str, float]] = {
    "caution": {
        "掂量": 1.5, "衡量": 1.5, "精打细算": 2, "不乱花": 2, "节俭": 2, "省钱": 1.5,
        "爱钱": 1.5, "算账": 1.5, "留一手": 1.5, "有分寸": 1.5, "谨慎": 1, "稳重": 1,
        "克制": 1, "理性": 0.8, "占有欲": 1,
        "冲动": -1.5, "莽": -1.5, "挥霍": -2, "大手大脚": -2, "不在乎钱": -2,
    },
    "ambition": {
        "好胜": 2, "胜负欲": 2, "不服输": 2, "要强": 2, "争强": 1.5, "宣示主权": 2,
        "许输": 2, "心眼": 1, "不后悔": 1, "果断": 1, "较真": 1, "占有欲": 1.5, "执念": 1,
    },
    "curiosity": {
        "好奇": 2, "闲逛": 2, "逛逛": 2, "四处走走": 2, "散步": 1.5, "探索": 1.5,
        "孩子气": 1.5, "被可爱": 2, "可爱的东西": 1.5, "新鲜": 1, "冒险": 1, "猫": 0.8,
    },
    "kindness": {
        "温柔": 2, "体贴": 2, "照顾": 1.5, "宠": 1.5, "心疼": 1.5, "谦让": 2,
        "手下留情": 2, "不舍得": 1.5, "心软": 1.5, "让着你": 2, "不想赢你": 2,
        "怕你难过": 1.5, "希望你开心": 1.5,
    },
}
# 词长优先匹配（"不惜代价" 这类长词优先于短词）；同一个词最多认 3 次，免得一个词刷满整张卡
_ORDERED = {trait: sorted(words, key=len, reverse=True) for trait, words in TRAITS.items()}
_WORD_CAP = 3

# ---- 由倾向换算成参数（区间都夹紧，避免极端角色卡把 AI 变成木头）----
def policy_from(traits: dict[str, float]) -> dict[str, float]:
    caution = traits["caution"]
    ambition = traits["ambition"]
    curiosity = traits["curiosity"]
    kindness = traits["kindness"]
    return {
        "reserve": int(round(120 + 260 * caution)),
        "upgrade": round(min(0.95, max(0.25, 0.35 + 0.45 * ambition + 0.2 * caution)), 2),
        "branch": round(min(0.85, max(0.15, 0.2 + 0.55 * curiosity)), 2),
        "mercy": round(min(0.4, 0.35 * kindness), 2),
    }


def gomoku_aggression(traits: dict[str, Any]) -> int:
    """（旧接口，保留兼容）同一张角色卡推出五子棋的进攻倾向（0–100）。

    只用大富翁那四个倾向推出来的值区分度很低（长卡会把倾向全刷满，谁都贴着中间值），
    所以实际用的是下面 `gomoku_style()` 的专用词表。
    """
    ambition = float(traits.get("ambition") or 0.5)
    caution = float(traits.get("caution") or 0.5)
    kindness = float(traits.get("kindness") or 0.5)
    value = 0.45 + 0.50 * ambition - 0.40 * caution - 0.15 * kindness
    return int(round(max(0.05, min(0.95, value)) * 100))


# 五子棋的"进攻 / 稳健"用**单独一套词**打分：挑的是"下棋风格"的词。
# 用大富翁那四个倾向（冷静/好胜/好奇/温柔）推的话，两万字的卡四项全在 0.75 以上，
# 谁推出来都是 41–42，等于没有区分（实测过三张卡：夜乃樱/水仙/初月全是 41–42）。
GOMOKU_TRAITS: dict[str, dict[str, float]] = {
    "attack": {
        "好胜": 1.0, "不服输": 1.0, "胜负欲": 1.0, "要强": 0.9, "进攻": 1.1, "主动出击": 1.0,
        "抢": 0.8, "宣示主权": 0.8, "胜利": 0.8, "赢了": 0.6, "MVP": 0.9, "第一名": 0.9,
        "不会让": 1.0, "杀气": 1.1, "凶狠": 1.1, "做杀": 1.0, "寸步不让": 1.1, "志在必得": 1.0,
    },
    "guard": {
        "冷静": 0.8, "克制": 0.8, "谨慎": 1.0, "掂量": 0.8, "稳健": 1.0, "守": 0.9,
        "拆招": 1.1, "让着": 0.9, "手下留情": 1.1, "温柔": 0.5, "体贴": 0.5, "谦让": 1.0,
        "不冒进": 1.1, "保守": 1.0, "低调": 0.7, "耐心": 0.8, "防守": 1.0, "退一步": 0.9,
    },
}


def score_gomoku(text: str) -> dict[str, float]:
    """按五子棋专用词表打分（同一套饱和公式，见 score()）。"""
    saturate = max(3.0, len(text) / 4000.0)
    out: dict[str, float] = {}
    for trait, words in GOMOKU_TRAITS.items():
        total = 0.0
        for word, weight in words.items():
            hits = len(re.findall(re.escape(word), text))
            if hits:
                total += weight * min(hits, _WORD_CAP)
        total = max(0.0, total)
        out[trait] = round(total / (total + saturate), 3) if total else 0.0
    return out


def gomoku_style(card_text: str) -> dict[str, Any]:
    """从角色卡推五子棋棋风：进攻倾向 0–100 ＋ 依据（给设置页与日志看）。"""
    text = trim(card_text or "")
    values = score_gomoku(text) if text.strip() else {"attack": 0.0, "guard": 0.0}
    attack, guard = values["attack"], values["guard"]
    aggression = int(round(max(0.05, min(0.95, 0.5 + 0.9 * (attack - guard))) * 100))
    basis = (f"进攻词 {attack:.2f} / 稳健词 {guard:.2f}"
             if (attack or guard) else "卡里没找到明显的棋风词")
    return {"aggression": aggression, "attack": attack, "guard": guard, "basis": basis}


DEFAULT_TRAITS = {"caution": 0.55, "ambition": 0.5, "curiosity": 0.35, "kindness": 0.5}
# 设置项里手动指定性格时的预设
PRESETS: dict[str, dict[str, float]] = {
    "steady": {"caution": 0.85, "ambition": 0.55, "curiosity": 0.3, "kindness": 0.4},
    "bold": {"caution": 0.2, "ambition": 0.9, "curiosity": 0.5, "kindness": 0.05},   # 全力打，不让棋
    "casual": {"caution": 0.35, "ambition": 0.3, "curiosity": 0.9, "kindness": 0.5},
    "gentle": {"caution": 0.6, "ambition": 0.35, "curiosity": 0.5, "kindness": 0.95},
}


def score(text: str) -> dict[str, float]:
    """关键词命中 → 0~1 的倾向分。

    用"命中分 /（命中分 + 饱和常数）"，饱和常数随卡的长短走（每 4000 字 +1）——
    否则两万字的卡随便什么词都刷到 0.95，四个倾向全满，等于没判。
    """
    saturate = max(3.0, len(text) / 4000.0)
    out: dict[str, float] = {}
    for trait, words in _ORDERED.items():
        total = 0.0
        for word in words:
            hits = len(re.findall(re.escape(word), text))
            if hits:
                total += TRAITS[trait][word] * min(hits, _WORD_CAP)
        total = max(0.0, total)
        out[trait] = round(total / (total + saturate), 3) if total else 0.0
    return out


def trim(text: str, limit: int = 200_000) -> str:
    return text[:limit]


def analyze(card_text: str) -> dict[str, float]:
    text = trim(card_text or "")
    if not text.strip():
        return dict(DEFAULT_TRAITS)
    return score(text)


STYLE_LABEL = {
    "steady": "稳健（留底金、不乱花钱）",
    "bold": "进取（抢地、升级、爱垄断）",
    "casual": "随性（爱拐支街逛）",
    "gentle": "温柔（领先时手下留情）",
}


def describe(traits: dict[str, float], policy: dict[str, float]) -> str:
    """给模型看的一句话：她会怎么打，这样解说才和落子一致。"""
    bits = []
    if traits["caution"] >= 0.6:
        bits.append("谨慎、先看现金再出手")
    elif traits["caution"] <= 0.3:
        bits.append("出手爽快、不太留现金")
    if traits["ambition"] >= 0.55:
        bits.append("好胜，爱抢街区、有机会就升级")
    if traits["curiosity"] >= 0.55:
        bits.append("好奇心重，岔路上常拐进支街逛")
    if traits["kindness"] >= 0.6:
        bits.append("对你心软，领先很多时会让一两块地")
    tail = (f"（买地前留 ¥{policy['reserve']} 现金；升级倾向 {policy['upgrade']:.0%}；"
            f"拐支街倾向 {policy['branch']:.0%}）")
    return ("、".join(bits) if bits else "按局势随机应变") + tail


# ---- 从宿主读角色卡 ----
def read_card(context: Any) -> str:
    """读当前角色的角色卡正文；拿不到就返回空串（调用方回退到默认性格）。"""
    if context is None:
        return ""
    try:
        service = context.get("sakura.host.character")
        if service is None:
            return ""
        return str(service.current().get("systemPrompt") or "")
    except Exception:                      # 宿主没给服务/角色卡读不出来都不该让插件挂掉
        return ""


def build(context: Any, preset: str = "card") -> dict[str, Any]:
    """返回 {traits, policy, text(给模型看的描述), source, gomoku(棋风)}。"""
    if preset in PRESETS:
        traits = dict(PRESETS[preset])
        return {"traits": traits, "policy": policy_from(traits),
                "text": describe(traits, policy_from(traits)),
                "source": f"预设：{STYLE_LABEL.get(preset, preset)}",
                "gomoku": gomoku_style("")}
    card = read_card(context)
    traits = analyze(card)
    policy = policy_from(traits)
    source = "角色卡" if card.strip() else "默认（没读到角色卡）"
    return {"traits": traits, "policy": policy, "text": describe(traits, policy), "source": source,
            "gomoku": gomoku_style(card)}


# ---- 当前角色的"身份"：显示名 + 立绘 ----
def identity(context: Any) -> dict[str, str]:
    """读当前角色的显示名与立绘文件路径，供网页跟随（换角色不用改插件）。

    宿主的 `character.current()` 只给 `{id, systemPrompt}`，名字和立绘要靠角色包里的
    `character.json`：先 `resolve_resource(id, "character.json")` 拿到清单，再按清单里的
    `portrait.default`（相对包路径）解析出立绘文件。任何一步失败都返回空值，调用方用默认。
    """
    out = {"id": "", "name": "", "portrait": ""}
    if context is None:
        return out
    try:
        service = context.get("sakura.host.character")
        if service is None:
            return out
        current = service.current() or {}
        character_id = str(current.get("id") or "")
        out["id"] = character_id
        if not character_id:
            return out
        manifest = json.loads(Path(service.resolve_resource(character_id, "character.json"))
                              .read_text(encoding="utf-8"))
        out["name"] = str(manifest.get("display_name") or "").strip()
        portrait = str((manifest.get("portrait") or {}).get("default") or "").strip()
        if portrait:
            path = Path(service.resolve_resource(character_id, portrait))
            if path.is_file():
                out["portrait"] = str(path)
    except Exception:      # 宿主没给服务 / 角色包不完整 / 路径被拒 —— 一律退回默认，不让插件挂
        return out
    return out
