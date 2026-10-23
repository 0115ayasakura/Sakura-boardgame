"""主题包：把地名、事件卡、街区名、插图 key 从代码里抽出来，变成可替换的数据。

为什么要有这一层：地图与事件卡的文案原来写死在 `engine.py` / `city_map.py` 里，全是「第二横滨市」
那一套（学园、黑列车、NAVI、樱的公寓……）。换个角色来下棋时，名字、立绘、棋风都已经跟着角色走了，
只有地名与事件卡还是别人的世界——这一层就是解决这件事的。

一份主题长这样（`themes/<id>.json`）：

    id / name           主题标识与显示名
    characters          这个主题归属哪些角色 id（当前角色命中就自动用它）
    start_name          起点格的名字
    districts           四个街区的标签与颜色（**键不变**：gakuen/shopping/daily/taboo，几何共用）
    properties          每个街区的地产名池 [key, 名字, 档位, 插图 key]——
                        每个街区的条数要与默认主题一致，否则分到地图上的地产数量会变
    specials            日常/NAVI/税/补助这类格子的名字
    special_names       具名特殊格（猫 / 怪獣 / 黑列车 / NAVI）的名字
    special_cycle       事件格沿路循环铺设的序列
    token_district      具名特殊格各自落在哪个街区
    groups              街区的标签与颜色（引擎与随机地图用）
    group_names         随机地图的地产名池
    group_tiers         随机地图的档位
    poi_icons           地产名 → 地标插图 key（`art/poi/<key>.png`）
    event_cards         事件牌组（**只改文案**：效果分布要与默认主题一致，否则经济平衡会变）

查找顺序：插件数据目录 `themes/<id>.json` > 插件自带 `themes/<id>.json` > 内置默认主题。
所以用户可以自己往数据目录丢一份改过的主题，不用动代码；引擎与测试不装主题也能跑（自带那份就在旁边）。
"""
from __future__ import annotations

import json
import re
from pathlib import Path
from typing import Any, Callable

DEFAULT_THEME_ID = "dimension_lovers"
THEME_DIR = Path(__file__).resolve().parent / "themes"

# 四个街区的键是几何的一部分（city_map 用它们算边界），主题只能改标签与颜色。
DISTRICT_KEYS = ("gakuen", "shopping", "daily", "taboo")

_state: dict[str, Any] = {"id": DEFAULT_THEME_ID, "themes": None, "source": ""}

# 格子播报的默认措辞（对局记录里那一行）。主题可以整句换掉——这些句子里本来就有
# 「黑列车」「炭化之人」「特别会计」这类专有名词，不换的话换个世界观就会露馅。
# 占位符用 {name} {owner} {cell} {rent} {paid} {amount} {toll} {cost} {gain} {salary}
# {payer} {opponent} {free_card} {bank}，由 engine 在相应位置填。
DEFAULT_STORY = {
    "cat": "在「{name}」跟前蹲下来，花 ¥{cost} 买了猫粮——这次它没有躲开（+¥{gain}）。",
    "cat_broke": "路过{name}，想喂猫却连猫粮钱都掏不出来……只能远远看一眼。",
    "monster": "「{name}」出现在路口！{opponent}发动『{free_card}』把它定在原地——但被烧掉的钱停不住（-¥{toll}）。",
    "train": "{name}无声地停在面前，持巨铃的车掌伸出了手——这是车票钱（-¥{toll}）。",
    "tax": "踩到{name}，缴税 ¥{paid}。",
    "bonus": "踩到{name}，+¥{amount}。",
    "rent_free": "踩到 {owner}的「{cell}」，租金 ¥{rent}——但{payer}发动『{free_card}』把账单停住了，免租！",
    "salary_double": "经过起点，领工资 +¥{salary}（禁书知识让工资翻了倍）。",
    "mortgage_bank": "把「{cell}」抵押给了{bank}，回收 ¥{gain}（抵押期间不收租）。",
    "mortgage_forced": "抵押「{cell}」，回收 ¥{gain}。",
}
DEFAULT_FLAG_CARDS = {"rent_free": "时间停止", "salary_next": "沉睡的宜必斯"}
DEFAULT_BANK_NAME = "特别会计"

_REQUIRED_KEYS = ("id", "name", "districts", "properties", "specials", "special_names",
                  "special_cycle", "groups", "group_names", "group_tiers", "event_cards")


# ---------- 装载 ----------

def _read_theme_file(path: Path) -> dict[str, Any] | None:
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None
    return data if isinstance(data, dict) and data.get("id") else None


def _load_themes() -> dict[str, dict[str, Any]]:
    """自带主题 + 数据目录里的主题（同 id 时后者覆盖，方便用户自己改）。"""
    themes: dict[str, dict[str, Any]] = {}
    for path in sorted(THEME_DIR.glob("*.json")) if THEME_DIR.is_dir() else []:
        data = _read_theme_file(path)
        if data:
            themes[str(data["id"])] = data
    provider = _state.get("_data_path")
    if callable(provider):
        for path in sorted(_data_dir(provider).glob("*.json")):
            data = _read_theme_file(path)
            if data:
                data["_user"] = True
                themes[str(data["id"])] = data
    return themes


def _data_dir(provider: Callable[[str], Any]) -> Path:
    try:
        return Path(str(provider("themes")))
    except Exception:
        return Path()


def install(data_path: Callable[[str], Any] | str | Path | None = None) -> None:
    """允许**数据目录**里的主题文件覆盖自带主题（给用户自己改地名/事件卡用）。

    `data_path` 可以是宿主的 `context.data_path` 回调，也可以直接给插件数据目录本身；
    传 None 表示卸掉覆盖、只用自带主题（测试里会这么用）。
    """
    if data_path is None:
        _state["_data_path"] = None
    elif callable(data_path):
        _state["_data_path"] = data_path
    else:
        base = Path(str(data_path))
        _state["_data_path"] = lambda rel, _base=base: _base / rel
    _state["themes"] = None
    _reload()


def _reload() -> None:
    _state["themes"] = None
    current()          # 立即装载一次，免得第一次取数时才发现主题文件有问题


def themes() -> dict[str, dict[str, Any]]:
    if _state["themes"] is None:
        _state["themes"] = _load_themes()
    return _state["themes"]


def available() -> list[dict[str, str]]:
    """可供选择的主题（给插件设置项与网页用）。"""
    out = []
    for theme in themes().values():
        out.append({"id": str(theme["id"]), "name": str(theme.get("name") or theme["id"])})
    return sorted(out, key=lambda item: item["id"])


# ---------- 选择 ----------

def theme_id() -> str:
    return str(_state["id"] or DEFAULT_THEME_ID)


def set_theme(theme_id_: str | None) -> str:
    """切主题。找不到就退回默认主题（不抛异常：存档里的主题可能来自被删掉的主题包）。"""
    wanted = str(theme_id_ or "").strip() or DEFAULT_THEME_ID
    if wanted not in themes():
        wanted = DEFAULT_THEME_ID
    _state["id"] = wanted
    return wanted


def theme_for_character(character_id: str | None) -> str:
    """按角色自动选主题：主题里写了这个角色 id 就用它，否则用默认主题。"""
    wanted = str(character_id or "").strip()
    if wanted:
        for theme in themes().values():
            owners = theme.get("characters") or []
            if wanted in [str(item) for item in owners]:
                return str(theme["id"])
    return DEFAULT_THEME_ID


def current() -> dict[str, Any]:
    """当前主题（缺的键用默认主题补齐，所以只写一半的用户主题也能用）。"""
    table = themes()
    theme = table.get(theme_id()) or table.get(DEFAULT_THEME_ID)
    if theme is None:                       # 连自带主题都读不到：给一份最小可用数据，别让游戏崩
        return {"id": DEFAULT_THEME_ID, "name": "默认主题", "_missing": True}
    base = table.get(DEFAULT_THEME_ID) or {}
    if theme is not base:
        merged = dict(base)
        merged.update(theme)
        return merged
    return theme


def raw(theme_id_: str | None = None) -> dict[str, Any]:
    """主题文件里原样的数据（**不做默认值补齐**）——体检用户自己写的主题时要用它。"""
    wanted = str(theme_id_ or theme_id())
    table = themes()
    return dict(table.get(wanted) or table.get(DEFAULT_THEME_ID) or {})


def validate(theme: dict[str, Any]) -> list[str]:
    """结构体检：主题文件写错了要能一眼看出来（测试与插件日志都用它）。"""
    problems: list[str] = []
    for key in _REQUIRED_KEYS:
        if key not in theme:
            problems.append(f"缺少 {key}")
    for key in DISTRICT_KEYS:
        if key not in (theme.get("districts") or {}):
            problems.append(f"districts 缺少 {key}")
        if key not in (theme.get("properties") or {}):
            problems.append(f"properties 缺少 {key}")
        if not (theme.get("group_names") or {}).get(key):
            problems.append(f"group_names 缺少 {key}")
        if len((theme.get("group_tiers") or {}).get(key) or []) != len(
                (theme.get("group_names") or {}).get(key) or []):
            problems.append(f"group_tiers 与 group_names 长度不一致：{key}")
    cards = theme.get("event_cards") or []
    if len(cards) < 20:
        problems.append(f"事件卡只有 {len(cards)} 张（至少 20）")
    for index, card in enumerate(cards):
        if not card.get("title") or not isinstance(card.get("effect"), dict):
            problems.append(f"第 {index + 1} 张事件卡缺 title/effect")
    for key, item in (theme.get("properties") or {}).items():
        for row in item:
            if len(row) != 4:
                problems.append(f"properties[{key}] 的条目要 4 项：[key, 名字, 档位, 插图]")
                break
    return problems


# ---------- 取数（引擎与地图都走这里）----------

def start_name() -> str:
    return str(current().get("start_name") or "起点")


def navi_log_prefix() -> str:
    """对局记录里"抽到事件卡"那一行的前缀（默认主题是 NAVI通知；换主题可以叫别的）。"""
    return str(current().get("navi_log_prefix") or "NAVI通知")


def districts() -> dict[str, dict[str, Any]]:
    return dict(current().get("districts") or {})


def district_label(key: str) -> str:
    return str((districts().get(key) or {}).get("label") or key)


def district_color(key: str, fallback: str = "#8a93a6") -> str:
    return str((districts().get(key) or {}).get("color") or fallback)


def properties() -> dict[str, list[list[Any]]]:
    return {key: [list(row) for row in rows]
            for key, rows in (current().get("properties") or {}).items()}


def specials() -> dict[str, str]:
    return dict(current().get("specials") or {})


def special_names() -> dict[str, str]:
    return dict(current().get("special_names") or {})


def special_cycle() -> list[list[str]]:
    return [list(row) for row in (current().get("special_cycle") or [])]


def token_district() -> dict[str, str]:
    return dict(current().get("token_district") or {})


def groups() -> dict[str, dict[str, str]]:
    return {key: dict(value) for key, value in (current().get("groups") or {}).items()}


def group_names() -> dict[str, list[str]]:
    return {key: list(value) for key, value in (current().get("group_names") or {}).items()}


def group_tiers() -> dict[str, list[int]]:
    return {key: list(value) for key, value in (current().get("group_tiers") or {}).items()}


def event_cards() -> list[dict[str, Any]]:
    return [dict(card) for card in (current().get("event_cards") or [])]


def poi_icons() -> dict[str, str]:
    return dict(current().get("poi_icons") or {})


def cell_names() -> dict[str, str]:
    """格子类型 → 页面上的名字（含税/补助/日常/具名特殊格，以及六种格子形式的名字）。"""
    merged = {"rest": "日常一幕", "tax": "税务", "bonus": "补助", "navi": "通知",
              "start": "起点", "end": "终点", "property": "地产",
              "shop": "商店", "game": "游戏", "coin": "金币", "event": "事件"}
    merged.update(specials())
    merged.update(special_names())
    merged.update(cell_forms())
    return merged


def cell_forms() -> dict[str, str]:
    """六种格子形式在页面上的中文名（主题可覆盖：`cell_forms` 字段）。"""
    return {key: str(value) for key, value in (current().get("cell_forms") or {}).items()}


def coin_tiers() -> list[int]:
    """金币格的三档金额（等概率抽一档）。主题没写就用引擎默认。"""
    out: list[int] = []
    for value in (current().get("coin_tiers") or []):
        try:
            number = int(value)
        except (TypeError, ValueError):
            continue
        if number > 0:
            out.append(number)
    return out


def shop_items() -> dict[str, Any]:
    """商店卖的东西：特殊骰子与现金倍率道具的**名字/说明**（数值由引擎定）。"""
    return dict(current().get("shop_items") or {})


def game_events() -> list[dict[str, Any]]:
    """游戏格上那几件特别事件（特别会计 / 学园补助 / 猫 / 炭化之人 / 黑列车）。

    每条：`key`（引擎效果键）、`name`（页面名）、`story`（播报措辞）、
    `win`/`lose`（掷骰赢/输的措辞，占位符由引擎填）。
    """
    out: list[dict[str, Any]] = []
    for row in (current().get("game_events") or []):
        if isinstance(row, dict) and row.get("key"):
            out.append(dict(row))
    return out


def poi_icon_for(name: str) -> str:
    """按地产名找地标插图 key（随机图的"XX别馆"也归到原图）。"""
    table = poi_icons()
    if name in table:
        return table[name]
    for base, icon in table.items():
        if name.startswith(base):
            return icon
    return ""


def bank_name() -> str:
    """"强制抵押/赎回"里那个收下地产的机构叫什么（默认主题是特别会计）。"""
    return str(current().get("bank_name") or DEFAULT_BANK_NAME)


def flag_card(flag: str) -> str:
    """某个状态（rent_free / salary_next）是"哪张卡给的"——用于播报里那句台词。"""
    table = dict(DEFAULT_FLAG_CARDS)
    table.update(current().get("flag_cards") or {})
    return str(table.get(flag) or "")


def story(key: str, **fields: Any) -> str:
    """按主题渲染一格播报。主题没写这一条就用默认措辞。

    模板里只允许 {名字} 形式的占位符；填不满的字段会原样保留花括号（不至于抛异常把对局打断）。
    """
    table = dict(DEFAULT_STORY)
    table.update(current().get("story") or {})
    template = str(table.get(key) or "")
    if not template:
        return ""
    try:
        return template.format(**fields)
    except (KeyError, IndexError, ValueError):
        return template


def slug(text: str) -> str:
    """把显示名压成可用作文件名/id 的片段（用户自定义主题时用得上）。"""
    return re.sub(r"[^0-9A-Za-z_.\-]+", "-", str(text)).strip("-") or "theme"
