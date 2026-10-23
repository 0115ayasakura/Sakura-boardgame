"""主题包测试：地名/事件卡的世界可以整个换掉，而地图几何与经济一动不动。

用法：python test_theme.py

这里盯住四件事：
1. 每份自带主题的结构是完整的（`theme.validate`），引用的插图文件真的存在；
2. **换主题不改几何与价格**——同一张城市图在两个主题下，格子类型/档位/坐标逐格相同，只有名字变；
3. 随机地图的地名也来自当前主题（以前不管什么主题都印「第二横滨市」的地名）；
4. 主题跟着角色走、跟着存档走（读到旧存档要切回它那一份主题）。
"""
from __future__ import annotations

import json
import pathlib
import sys
import tempfile

ROOT = pathlib.Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "sakura_boardgame"))

import city_map  # noqa: E402
import engine  # noqa: E402
import theme  # noqa: E402

ART = ROOT / "sakura_boardgame" / "art" / "poi"
SHIPPED = ("dimension_lovers", "azur_lane")


def digest_cells(game) -> list[tuple]:
    """格子的"骨架"：类型、街区、档位、坐标——名字不算（换主题只该改名字）。"""
    return [(c["id"], c["type"], c["group"], c["tier"], c["price"], c["x"], c["y"])
            for c in game.cells]


def test_shipped_themes_are_complete() -> None:
    ids = {item["id"] for item in theme.available()}
    for wanted in SHIPPED:
        assert wanted in ids, f"自带主题 {wanted} 没被装载：{ids}"
    for wanted in SHIPPED:
        theme.set_theme(wanted)
        problems = theme.validate(theme.current())
        assert not problems, f"{wanted} 结构有问题：{problems}"
        assert theme.event_cards(), wanted
        assert len(theme.special_cycle()) >= 20, wanted
        for key in theme.DISTRICT_KEYS:
            assert theme.district_label(key) != key, f"{wanted} 缺少街区标签：{key}"
            assert theme.district_color(key).startswith("#"), key
        # 六种格子形式的四张新表（格子名 / 金币档位 / 商店道具 / 游戏事件）都要齐，
        # 少一张就会退回内置默认值，等于把另一个世界的词又印回页面上
        forms = theme.cell_forms()
        for key in ("start", "end", "property", "shop", "game", "coin", "event"):
            assert forms.get(key), f"{wanted} 缺少格子名：{key}"
        tiers = theme.coin_tiers()
        assert len(tiers) >= 2 and all(isinstance(v, int) and v > 0 for v in tiers), (wanted, tiers)
        # 主题里写的是扁平文案键（dice_label / dice_note…），价格由引擎常量定；
        # 这里查主题那一侧，引擎侧的组装由 test_engine 的商店用例覆盖
        items = theme.shop_items()
        for key in ("dice", "gain", "loss"):
            assert items.get(f"{key}_label"), (wanted, key, items)
            assert items.get(f"{key}_note"), (wanted, key, items)
        normalized = engine.shop_items()
        for key in ("dice", "gain", "loss"):
            assert normalized[key]["label"] and int(normalized[key]["price"]) > 0, (wanted, key)
        rows = theme.game_events()
        assert len(rows) >= 3, (wanted, rows)
        assert len({row.get("key") for row in rows}) == len(rows), f"{wanted} 游戏事件 key 重复"
        for row in rows:
            assert row.get("name") and row.get("story"), (wanted, row)
    theme.set_theme(theme.DEFAULT_THEME_ID)


def test_icons_referenced_by_themes_exist() -> None:
    """主题里写的插图 key 必须真的有图，否则地图钉子上会空一块。"""
    missing: list[str] = []
    for wanted in SHIPPED:
        theme.set_theme(wanted)
        for rows in theme.properties().values():
            for row in rows:
                icon = str(row[3] or "")
                if not icon.startswith("poi-"):
                    continue
                if not (ART / f"{icon[4:]}.png").is_file():
                    missing.append(f"{wanted}: {row[1]} → {icon}")
        for name in list(theme.poi_icons()):
            assert theme.poi_icon_for(name), f"{wanted} 里 {name} 找不到插图"
    theme.set_theme(theme.DEFAULT_THEME_ID)
    assert not missing, "主题引用了不存在的插图：" + "、".join(missing)


def test_theme_switch_keeps_geometry_and_prices() -> None:
    """换主题只换文案：同一张地图的骨架（类型/档位/价格/坐标）必须逐格相同。"""
    theme.set_theme("dimension_lovers")
    base = engine.MonopolyGame(size=48, dice_sides=6, seed=7, map_kind="city")
    base_names = [c["name"] for c in base.cells]
    other = engine.MonopolyGame(size=48, dice_sides=6, seed=7, map_kind="city", theme_id="azur_lane")
    assert digest_cells(base) == digest_cells(other), "换主题动了地图结构或价格"
    assert [c["name"] for c in other.cells] != base_names, "换主题之后名字没变"
    # 总地价也必须一致（平衡靠它）
    assert (sum(c["price"] for c in base.cells if c["type"] == "property")
            == sum(c["price"] for c in other.cells if c["type"] == "property"))
    # 起点名字跟着主题走
    assert base.cells[base.start_node]["name"] != other.cells[other.start_node]["name"]
    theme.set_theme(theme.DEFAULT_THEME_ID)


def test_removed_random_setting_uses_themed_city() -> None:
    """Legacy random settings normalize to the fixed city without losing character/theme names."""
    theme.set_theme("dimension_lovers")
    default_names = {c["name"] for c in engine.MonopolyGame(size=24, dice_sides=6, seed=11).cells}
    legacy = engine.MonopolyGame(size=24, dice_sides=6, seed=11, map_kind="random", theme_id="azur_lane")
    explicit = engine.MonopolyGame(size=24, dice_sides=6, seed=11, map_kind="city", theme_id="azur_lane")
    assert legacy.map_kind == "city"
    assert legacy.cells == explicit.cells and legacy.edges == explicit.edges
    assert {c["name"] for c in legacy.cells} != default_names
    theme.set_theme(theme.DEFAULT_THEME_ID)


def test_event_deck_keeps_effects_across_themes() -> None:
    """牌组只换文案：效果、权重、稀有标记的位置必须一模一样（否则经济平衡会变）。"""
    theme.set_theme("dimension_lovers")
    base = [(c["effect"], c.get("weight"), c.get("rare")) for c in engine.EVENT_CARDS]
    base_titles = [c["title"] for c in engine.EVENT_CARDS]
    theme.set_theme("azur_lane")
    other = [(c["effect"], c.get("weight"), c.get("rare")) for c in engine.EVENT_CARDS]
    other_titles = [c["title"] for c in engine.EVENT_CARDS]
    assert base == other, "换主题改了事件卡的效果分布"
    assert base_titles != other_titles, "换主题之后事件卡文案没变"
    for card in theme.event_cards():
        # 标题会原样写进对局记录（不经过人称替换），所以不能出现"你"
        assert "你" not in card["title"], card
    theme.set_theme(theme.DEFAULT_THEME_ID)


def test_theme_follows_character() -> None:
    """主题认领角色：初月（chuyue）自动用港区主题，别的角色用默认主题。"""
    assert theme.theme_for_character("chuyue") == "azur_lane"
    assert theme.theme_for_character("suisen_test") == theme.DEFAULT_THEME_ID
    assert theme.theme_for_character("") == theme.DEFAULT_THEME_ID
    # 角色 id 里的路径字符不许穿透到文件系统
    assert theme.theme_for_character("../../etc/passwd") == theme.DEFAULT_THEME_ID


def test_save_carries_theme() -> None:
    """存档记住主题：读回来时切回那一份，存档里的地名才有对应的街区标签。"""
    game = engine.MonopolyGame(size=24, dice_sides=6, seed=5, map_kind="random", theme_id="azur_lane")
    data = game.to_dict()
    assert data["theme"] == "azur_lane", data.get("theme")
    theme.set_theme(theme.DEFAULT_THEME_ID)
    restored = engine.restore_game(data)
    assert restored.theme == "azur_lane"
    assert theme.theme_id() == "azur_lane", "读档之后没切回存档里的主题"
    assert restored.cells[0]["name"] == game.cells[0]["name"]
    theme.set_theme(theme.DEFAULT_THEME_ID)
    # 存档里的主题被删掉时安全退回默认主题
    data["theme"] = "ghost_theme"
    assert engine.restore_game(data).theme == theme.DEFAULT_THEME_ID


def test_user_theme_in_data_dir_overrides_shipped() -> None:
    """用户可以往数据目录丢一份自己的主题，覆盖自带的那份。"""
    with tempfile.TemporaryDirectory() as tmp:
        root = pathlib.Path(tmp)
        (root / "themes").mkdir(parents=True, exist_ok=True)
        custom = json.loads((ROOT / "sakura_boardgame" / "themes" / "dimension_lovers.json")
                            .read_text(encoding="utf-8"))
        custom["name"] = "我的第二横滨市"
        custom["start_name"] = "自定义起点"
        custom["districts"]["gakuen"]["label"] = "自定义学园区"
        (root / "themes" / "dimension_lovers.json").write_text(
            json.dumps(custom, ensure_ascii=False), encoding="utf-8")
        theme.install(root)
        try:
            theme.set_theme("dimension_lovers")
            assert theme.current()["name"] == "我的第二横滨市", theme.current()["name"]
            assert theme.start_name() == "自定义起点"
            assert theme.district_label("gakuen") == "自定义学园区"
        finally:
            theme.install(None)                     # 卸掉覆盖，恢复自带主题
        theme.set_theme("dimension_lovers")
        assert theme.current()["name"] == "第二横滨市", theme.current()["name"]


def test_partial_theme_falls_back_to_default() -> None:
    """只写了一半的主题也能用：缺的键从默认主题补。"""
    with tempfile.TemporaryDirectory() as tmp:
        root = pathlib.Path(tmp)
        (root / "themes").mkdir(parents=True, exist_ok=True)
        (root / "themes" / "tiny.json").write_text(json.dumps({
            "id": "tiny", "name": "半个主题", "start_name": "小小起点",
        }, ensure_ascii=False), encoding="utf-8")
        theme.install(root)
        try:
            assert theme.set_theme("tiny") == "tiny"
            assert theme.start_name() == "小小起点"
            assert theme.district_label("gakuen") == "学园区"          # 从默认主题补齐
            assert len(theme.event_cards()) == len(json.loads(
                (ROOT / "sakura_boardgame" / "themes" / "dimension_lovers.json")
                .read_text(encoding="utf-8"))["event_cards"])
            problems = theme.validate(theme.raw("tiny"))
            assert problems, "半个主题应当被结构体检挑出来（体检看的是原文件，不是补过默认值的）"
        finally:
            theme.install(None)
        theme.set_theme(theme.DEFAULT_THEME_ID)


def test_no_default_world_words_leak_into_other_theme() -> None:
    """玩港区主题时，对局记录里不该冒出默认主题的专有名词（那是最容易露馅的地方）。

    做法：把每个格子都踩一遍，把播报收集起来逐条查。
    """
    banned = ("黑列车", "炭化", "流浪猫", "学园", "樱", "特别会计", "禁书", "宜必斯",
              "崩月", "汉加米")
    theme.set_theme("azur_lane")
    engine.set_opponent_label("初月")            # 真实玩法里插件会先把显示名换成当前角色
    try:
        game = engine.MonopolyGame(size=48, dice_sides=6, seed=4, map_kind="city", theme_id="azur_lane")
        kinds: dict[str, int] = {}
        for cell in game.cells:
            kinds.setdefault(str(cell["type"]), int(cell["id"]))
        checked = 0
        for kind, node in kinds.items():
            if kind == "start":
                continue
            for player in ("user", "sakura"):
                game.pending = None
                game._walk = None
                game.winner = None
                game.flags = {p: {"salary_next": False, "rent_free": False, "dice_bonus": 0}
                              for p in ("user", "sakura")}
                game.cash = {p: engine.MONOPOLY_START_CASH for p in ("user", "sakura")}
                story: list[str] = []
                game._resolve_landing(player, node, True, story)
                for line in story:
                    for word in banned:
                        assert word not in line, f"{kind} 格的播报里出现了默认主题的词「{word}」：{line}"
                checked += 1
        assert checked >= 8, checked
        # 起点工资那条也要走一遍（翻倍那句原本写着"禁书"）
        game.flags["user"]["salary_next"] = True
        story = []
        game._grant_salary("user", story)
        assert "禁书" not in "".join(story) and "心智魔方" in "".join(story), story
        # 抵押与赎回：收下地产的机构名也要换掉
        node = kinds["property"]
        game.cells[node]["owner"] = "user"
        game.pending = {"player": "user", "type": "manage", "cell": node,
                        "options": ["mortgage", "skip"]}
        result = game.decide("user", "mortgage")
        assert "特别会计" not in "".join(result["story"]), result["story"]
        assert "指挥部的会计科" in "".join(result["story"]), result["story"]
    finally:
        theme.set_theme(theme.DEFAULT_THEME_ID)
        engine.set_opponent_label(None)


def test_city_map_decor_and_rules_follow_theme() -> None:
    """网页底图的街区标签、规则说明里的地名都要跟着主题走。"""
    theme.set_theme("azur_lane")
    labels = [d["label"] for d in city_map.district_washes()]
    assert "重樱泊地" in labels and "学园区" not in labels, labels
    game = engine.MonopolyGame(size=24, dice_sides=6, seed=3, map_kind="city", theme_id="azur_lane")
    rules = engine.rules_text(game)
    assert "重樱泊地" in rules and "学园正门" not in rules, rules[:120]
    assert "学园区" not in rules, rules[:160]
    theme.set_theme(theme.DEFAULT_THEME_ID)
    rules_default = engine.rules_text(engine.MonopolyGame(size=24, dice_sides=6, seed=3, map_kind="city"))
    assert "学园正门" in rules_default and "学园区" in rules_default


def main() -> None:
    tests = [(name, fn) for name, fn in sorted(globals().items())
             if name.startswith("test_") and callable(fn)]
    for name, fn in tests:
        fn()
        print(f"ok  {name}")
    print(f"ALL {len(tests)} THEME TESTS PASSED")


if __name__ == "__main__":
    main()
