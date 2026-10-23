"""真实角色包验收：换角色零改动——名字、立绘、棋风、**桌游引导**都跟着走，角色卡里不用加任何东西。

用法：python test_generic_character.py [角色包目录] [角色 id]
默认角色包：D:\\Sakura\\characters\\suisen_test（崩月水仙的测试角色卡）

为什么单独一个脚本：冒烟测试用的是假卡（三行字），这里用的是**真实角色包**——真的 card.md、
真的 character.json、真的立绘，才能验证"换角色不用改卡"这件事在真实数据上成立。
"""
from __future__ import annotations

import json
import pathlib
import sys
import tempfile

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1] / "sakura_boardgame"))

import plugin as plugin_module  # noqa: E402
import engine as engine_mod  # noqa: E402
from engine import GameError  # noqa: E402

DEFAULT_PKG = pathlib.Path(r"D:\Sakura\characters\suisen_test")


class FakeConfig:
    def __init__(self) -> None:
        self.values = {"dice_sides": 6, "track_length": 16, "gomoku_level": 2, "gomoku_style": 45,
                       "map_kind": "city", "auto_open_board": False, "auto_narrate": False,
                       "play_style": "card"}

    def get(self) -> dict:
        return dict(self.values)

    def update(self, values: dict) -> str:
        self.values.update(values)
        return "applied"


class Logger:
    def __init__(self) -> None:
        self.lines: list[str] = []

    def __getattr__(self, name):
        def record(message, fields=None):
            self.lines.append(f"{name}: {message} {fields or ''}")
        return record


class Character:
    """和宿主一致：current() 给 id + 角色卡正文，resolve_resource(id, rel) 给包内文件。"""

    def __init__(self, package: pathlib.Path) -> None:
        self.package = package
        manifest = json.loads((package / "character.json").read_text(encoding="utf-8"))
        self.manifest = manifest
        self.character_id = str(manifest["id"])
        self.card = (package / manifest.get("card", "card.md")).read_text(encoding="utf-8")

    def current(self) -> dict:
        return {"id": self.character_id, "systemPrompt": self.card}

    def resolve_resource(self, character_id, relative_path):
        if str(character_id) != self.character_id:
            raise ValueError("unknown character")
        target = self.package / relative_path
        if not target.is_file():
            raise ValueError(f"missing {relative_path}")
        return str(target)


class Ctx:
    plugin_id = "local.sakura.boardgame"

    def __init__(self, root: pathlib.Path, package: pathlib.Path, has_character: bool = True) -> None:
        self.root = root
        self.config = FakeConfig()
        self.tools: dict = {}
        self.logger = Logger()
        self.mobile_calls: list = []
        self.contributions: list = []
        self.character = Character(package) if has_character else None
        self_ref = self

        class Tools:
            def register(self, descriptor, handler):
                self_ref.tools[descriptor["name"]] = handler

        class Mobile:
            def begin(self, plugin_id, character_id, text, artifact=None):
                self_ref.mobile_calls.append({"character_id": character_id, "text": text})
                return {"jobId": "job"}

        class Settings:
            """设置项在这里只要求"注册不报错"。"""

            def register(self, descriptor, load=None, save=None, actions=None):
                self_ref.settings_descriptor = descriptor

        self._services = {
            "sakura.host.logging": self.logger,
            "sakura.host.tools": Tools(),
            "sakura.host.character": self.character,
            "sakura.host.mobile": Mobile(),
            "sakura.host.settings": Settings(),
        }

    def get(self, key):
        if key == "sakura.host.context":
            return self
        return self._services.get(key)

    def data_path(self, relative):
        return self.root / relative

    def register(self, descriptor, builder):
        self.contributions.append((descriptor, builder))

    def effect(self, fn):
        self._effect = fn

    def teardown(self):
        if getattr(self, "_effect", None):
            self._effect()

    # 便捷方法
    def fragment(self) -> str:
        """当前上下文片段（插件注入给模型的那段文本）。"""
        assert self.contributions, "没有注册上下文贡献者"
        fragments = self.contributions[0][1]({})
        assert fragments, "上下文片段为空"
        return fragments[0]["content"]

    def service(self):
        return self.tools["boardgame_state"].__self__


def main() -> None:
    package = pathlib.Path(sys.argv[1]) if len(sys.argv) > 1 else DEFAULT_PKG
    if not package.is_dir():
        print(f"跳过：找不到角色包 {package}（本机才有这个测试卡）")
        return
    manifest = json.loads((package / "character.json").read_text(encoding="utf-8"))
    name = str(manifest["display_name"])

    with tempfile.TemporaryDirectory() as tmp:
        root = pathlib.Path(tmp)
        ctx = Ctx(root / "data", package)
        plugin_module.BoardgamePlugin().setup(ctx)
        service = ctx.service()

        # ---- 身份跟随（真实角色包）----
        assert service._opponent["name"] == name, service._opponent
        assert service._opponent["id"] == str(manifest["id"]), service._opponent
        assert service._opponent["portrait"].startswith("/portrait"), service._opponent

        # ---- 桌游引导：内置通用版就能用，角色卡里没加任何东西 ----
        assert service._guide_source == "内置通用版", service._guide_source
        assert service.guide_text("monopoly") and service.guide_text("gomoku")
        # 引导是"自指"写法：不该点名任何一座具体地标
        for landmark in ("无调图书馆", "黑列车车站", "汉加米塔", "樱的公寓"):
            assert landmark not in service._guide, f"通用引导里不该出现具体地名：{landmark}"

        # ---- 有对局才注入：引导跟着游戏类型筛行 ----
        ctx.tools["boardgame_start"]({"game": "monopoly"})
        monopoly_fragment = ctx.fragment()
        assert "【桌游引导】" in monopoly_fragment, monopoly_fragment[:200]
        assert "【处世态度】" in monopoly_fragment and "【落子】" not in monopoly_fragment
        assert "【大富翁】" not in monopoly_fragment and "[大富翁]" not in monopoly_fragment
        # 换角色后不该再出现旧名字（对局记录、状态文案、报错文案都要跟随）
        state = ctx.tools["boardgame_state"]({})
        assert name in state["status"], state["status"]
        assert "夜乃樱" not in state["status"], state["status"]
        ctx.tools["boardgame_roll"]({"player": "user"})
        record = list(ctx.service()._log)
        assert record, "掷骰后对局记录不该是空的"
        assert all("夜乃樱" not in line for line in record), record

        ctx.tools["boardgame_start"]({"game": "gomoku"})
        gomoku_fragment = ctx.fragment()
        assert "【落子】" in gomoku_fragment and "【处世态度】" not in gomoku_fragment
        assert "夜乃樱" not in ctx.tools["boardgame_state"]({})["status"]
        try:
            ctx.tools["boardgame_place"]({"player": "user"})
        except GameError as error:
            assert "夜乃樱" not in str(error) and name in str(error), str(error)
        else:
            raise AssertionError("玩家不给坐标应当报错")

        # ---- 棋风来自真实角色卡正文（不是默认值）----
        persona = service._persona
        assert persona["source"] not in ("默认",), persona["source"]
        print(f"  角色卡推出来的棋风：底金 ¥{persona['policy']['reserve']}、"
              f"升级 {persona['policy']['upgrade']:.0%}、拐支街 {persona['policy']['branch']:.0%}、"
              f"让地 {persona['policy']['mercy']:.0%}（来源：{persona['source']}）")

        # ---- 角色专属引导文件：加一份就生效，不用碰角色卡 ----
        guide_dir = root / "data" / "guides"
        guide_dir.mkdir(parents=True, exist_ok=True)
        (guide_dir / f"{manifest['id']}.md").write_text(
            "这是专属引导。\n[五子棋]只有下五子棋才说的话。", encoding="utf-8")
        ctx.tools["boardgame_end"]({})
        ctx.teardown()

        ctx2 = Ctx(root / "data", package)
        plugin_module.BoardgamePlugin().setup(ctx2)
        service2 = ctx2.service()
        assert service2._guide_source.startswith("角色专属"), service2._guide_source
        ctx2.tools["boardgame_start"]({"game": "gomoku"})
        custom = ctx2.fragment()
        assert "这是专属引导。" in custom and "只有下五子棋才说的话。" in custom, custom[:300]
        assert "[五子棋]" not in custom
        ctx2.tools["boardgame_start"]({"game": "monopoly"})
        assert "只有下五子棋才说的话。" not in ctx2.fragment(), "五子棋专属行不该出现在大富翁里"
        ctx2.teardown()

        # ---- 没有角色包时退回默认（夜乃樱 + 内置立绘 + 内置引导）----
        ctx3 = Ctx(root / "data_plain", package, has_character=False)
        plugin_module.BoardgamePlugin().setup(ctx3)
        service3 = ctx3.service()
        assert service3._opponent["name"] == "夜乃樱", service3._opponent
        assert service3._guide_source == "内置通用版", service3._guide_source
        ctx3.teardown()

    check_second_character()
    print(f"GENERIC CHARACTER TEST PASSED（角色：{name}）")


def check_second_character() -> None:
    """第二个角色（本机是碧蓝航线的初月）：名字/立绘/**地图主题**一起自动跟随。

    主题包认领角色：初月的角色包 id 是 `chuyue`，港区主题里写着认领它，所以开局地图就是
    「重樱泊地 / 军港前街 / 宿舍区 / 废弃船坞」，事件卡也换成港区那一套——角色卡与插件都不用改。
    """
    package = pathlib.Path(sys.argv[2]) if len(sys.argv) > 2 else pathlib.Path(r"D:\Sakura\characters\chuyue")
    if not package.is_dir():
        print(f"（跳过第二角色：找不到 {package}）")
        return
    manifest = json.loads((package / "character.json").read_text(encoding="utf-8"))
    name = str(manifest["display_name"])
    with tempfile.TemporaryDirectory() as tmp:
        ctx = Ctx(pathlib.Path(tmp) / "data", package)
        plugin_module.BoardgamePlugin().setup(ctx)
        service = ctx.service()
        assert service._opponent["name"] == name, service._opponent
        assert service.active_theme() == "azur_lane", service.active_theme()
        ctx.tools["boardgame_start"]({"game": "monopoly"})
        game = ctx.service()._game
        cell_names = [c["name"] for c in game.cells]
        assert game.theme == "azur_lane", game.theme
        assert "指挥室前・起点" in cell_names, cell_names[:6]
        assert not any("学园" in n or "樱的公寓" in n for n in cell_names), "城市图还是旧世界的地名"
        payload_theme = service.theme_payload()
        assert payload_theme["id"] == "azur_lane" and payload_theme["name"].startswith("港区"), payload_theme
        # 事件卡也跟着主题走（抽出来的卡来自港区牌组）
        state = ctx.tools["boardgame_state"]({})
        assert state["active"] is True
        # ---- 已经是别的一局时：主题落差要在 /state 里看得见（实测踩过）----
        # 换角色之后，**已经开始的那一局**不会中途换世界（地名烙在格子上、存档记着它），
        # 但设置/角色希望用港区——这个落差必须让网页能显示出来，否则看着像"主题没生效"。
        old_game = engine_mod.MonopolyGame(size=24, dice_sides=6, seed=9, map_kind="random",
                                           theme_id="dimension_lovers")
        service._game = old_game
        payload = service.theme_payload()
        assert payload["id"] == "dimension_lovers", payload
        assert payload["pending"] is True and payload["nextId"] == "azur_lane", payload
        # **重开一局必须真的换成港区**（曾经的 bug：开局时读的是"正在被替换的那一局"的主题，
        # 于是重开也还是旧世界）
        ctx.tools["boardgame_start"]({"game": "monopoly"})
        fresh = ctx.service()._game
        assert fresh.theme == "azur_lane", fresh.theme
        assert fresh.cells[fresh.start_node]["name"] == "指挥室前・起点"
        payload = service.theme_payload()
        assert payload["pending"] is False and payload["id"] == payload["nextId"], payload
        ctx.tools["boardgame_end"]({})
        ctx.teardown()
        print(f"  第二角色验收：{name} → 主题「{payload_theme['name']}」，"
              f"起点「{game.cells[game.start_node]['name']}」")


if __name__ == "__main__":
    main()
