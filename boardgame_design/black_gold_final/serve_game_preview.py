"""Independent full-game black-train preview. Standard library only; no saves.

Run: python boardgame_design/black_gold_final/serve_game_preview.py
Test without opening any socket: append --self-test.
GET / serves the real board.html, /state and POST /api/action use the real
BoardgameService protocol. /art remains the original art; /theme serves only
allowlisted preview assets. Close with Ctrl+C; all game/config data is discarded.
"""
from __future__ import annotations

import argparse
import copy
import http.server
import json
import sys
import uuid
from pathlib import Path
from types import SimpleNamespace
from urllib.parse import unquote, urlsplit

# Imports must not create __pycache__ in the read-only production source tree.
sys.dont_write_bytecode = True
THEME_DIR = Path(__file__).resolve().parent
ROOT = THEME_DIR.parents[1]
PLUGIN_DIR = ROOT / "sakura_boardgame"
sys.path.insert(0, str(ROOT))

from sakura_boardgame.board_server import make_handler
from sakura_boardgame.engine import MonopolyGame
from sakura_boardgame.plugin import BoardgameService
from sakura_boardgame.theme_resources import (
    resolve_theme_file, runtime_config, validate_theme_package)


class MemoryConfig:
    def __init__(self):
        # This isolated fixture has no host Agent; algorithm is an explicit preview choice.
        self.values = {"decision_driver": "algorithm", "map_kind": "grid", "theme": "dimension_lovers",
                       "dice_sides": 6, "auto_mode": "off",
                       "auto_open_board": False, "proactive_enabled": False,
                       "gomoku_style_follow_card": False}

    def get(self):
        return dict(self.values)

    def update(self, changes):
        self.values.update(changes)


class PreviewLogger:
    def info(self, message, **kwargs):
        pass

    def warning(self, message, **kwargs):
        print(f"preview warning: {message}", file=sys.stderr)

    def error(self, message, **kwargs):
        print(f"preview error: {message} {kwargs}", file=sys.stderr)


def reject_data_path(*args, **kwargs):
    raise RuntimeError("Preview forbids all persistent data paths")


class PreviewService(BoardgameService):
    preview_black_train = True

    def _load(self):
        return None

    def _save(self):
        # Preview transactions and replay receipts are intentionally memory-only.
        return None

    def _save_now(self):
        # The outer transaction commits here even when nested _save is a no-op.
        # Never call data_path, mkdir or write_text, including modern metadata.
        return None

    def _delete_save_now(self):
        # Ending a preview discards memory only; there is no persistent save.
        return None

    def _load_guide(self):
        self._guide = self._guide_module().DEFAULT_TEXT
        self._guide_source = "built-in preview (memory only)"
        return self._guide

    @property
    def board_url(self):
        # Never call BoardServer.start(), including during actions or self-test.
        return self._server.url if self._server else ""

    def open_in_browser(self, url):
        return False


def create_service():
    service = PreviewService(MemoryConfig(), reject_data_path, PreviewLogger())
    service._server = SimpleNamespace(url="", instance_id=uuid.uuid4().hex,
                                      _touch=lambda: None)
    service.start({"game": "monopoly", "first": "user"}, skip_open=True)
    if not isinstance(service._game, MonopolyGame):
        raise RuntimeError("Expected an independent MonopolyGame")
    return service


def preview_html():
    # The shared page reads /board-config.js; never patch its implementation text.
    return (PLUGIN_DIR / "board.html").read_bytes()


def preview_config():
    return ("globalThis.BoardRuntimeConfig = " +
            json.dumps(runtime_config(PLUGIN_DIR), ensure_ascii=True) + ";\n").encode("utf-8")


def create_handler(service):
    base = make_handler(service, PLUGIN_DIR)

    class PreviewHandler(base):
        def do_GET(self):
            path = urlsplit(self.path).path
            if (path in ("/", "/index.html", "/board.html", "/board-config.js")
                    or path.startswith("/theme/")):
                if not self._trusted_request():
                    return
                service._server._touch()
                try:
                    if path in ("/", "/index.html", "/board.html"):
                        self._send(200, preview_html(), "text/html; charset=utf-8")
                    elif path == "/board-config.js":
                        self._send(200, preview_config(), "application/javascript; charset=utf-8")
                    else:
                        target = resolve_theme_file(PLUGIN_DIR, unquote(path[len("/theme/"):]))
                        if target is None:
                            self._send(404, b"not found", "text/plain")
                        else:
                            self._send_file(target)
                except (OSError, ValueError):
                    self._send(500, b"invalid theme package", "text/plain")
                return
            # Original trusted Host/Origin checks, JSON API and /art mapping.
            super().do_GET()

    return PreviewHandler


def check_assets():
    preview_html()
    validate_theme_package(PLUGIN_DIR)  # Fail before binding on missing/changed package.
    preview_config()
    for name in ("art/board/station_remake.glb", "art/board/board_view.json",
                 "art/lib/black_train_transition.js", "art/lib/black_train_view.js"):
        if not (PLUGIN_DIR / name).is_file():
            raise RuntimeError(f"Missing original asset: {name}")


def self_test():
    """Drive real service actions through both success and safe-return outcomes.

    No HTTPServer is constructed and no listening socket or persistent file is
    created. This is protocol/engine validation, not a browser/WebGL test.
    """
    check_assets()
    outcomes = []
    for choice_index in (0, -1):
        service = create_service()
        game = service._game
        original = copy.deepcopy(game.to_dict())
        # Deterministically cover both outcomes; keep production route policy.
        pick_pending = service._pick_pending_action
        service._pick_pending_action = lambda current: (
            str(current.pending["options"][choice_index])
            if current.pending.get("type") == "black_train" else pick_pending(current))
        state = service.state_payload()
        assert state["previewBlackTrain"] is True
        metadata = {"requestId": f"preview-train-{choice_index}", "gameSeq": state["gameSeq"],
                    "expectedVersion": state["version"]}
        accepted = service.handle_action("black_train_start", {}, metadata=metadata)
        assert accepted["ok"] and accepted["duplicate"] is False, accepted
        replay = service.handle_action("black_train_start", {}, metadata=metadata)
        assert replay == {**accepted, "duplicate": True}, replay
        for _ in range(1000):
            if not game.black_train["active"]:
                break
            if game.pending:
                # Drive both players with the same production decide method;
                # additionally exercise act_sakura for partner turns below.
                if game.pending["player"] == "sakura":
                    result = service.handle_action("act_sakura", {})
                else:
                    result = service.handle_action("decide", {
                        "action": str(game.pending["options"][choice_index])})
            elif game.turn == "sakura":
                result = service.handle_action("act_sakura", {})
            else:
                result = service.handle_action("roll", {})
            assert result.get("ok"), result
        else:
            raise AssertionError("Black-train flow did not terminate")
        assert game.black_train["actions_taken"] == {"user": 5, "sakura": 5}
        assert game.black_train["actions_left"] == 0
        current = game.to_dict()
        for key in ("cells", "positions", "last_step", "skip_pending", "move_count"):
            assert current[key] == original[key], f"Frozen state changed: {key}"
        for player in ("user", "sakura"):
            expected_flags = dict(original["flags"][player])
            expected_flags["pick_dice"] = bool(game.black_train["players"][player]["custom_die"])
            assert current["flags"][player] == expected_flags, "Only final custom-die inventory may change"
        # dice_turn is an animation/ledger ID, not the frozen economic move_count.
        assert current["dice_turn"] == original["dice_turn"] + 10
        assert game.black_train["outcome"] == "safe_return"
        # Polling response remains JSON serializable after all ten actions.
        json.dumps(service.state_payload(), ensure_ascii=False)
        outcomes.append({"actions_taken": game.black_train["actions_taken"],
                         "outcome": game.black_train["outcome"]})
    print(json.dumps({"ok": True, "socket_opened": False,
                      "persistent_data_access": False, "runs": outcomes}, ensure_ascii=False))


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--self-test", action="store_true", help="test real actions without starting HTTP")
    args = parser.parse_args()
    if args.self_test:
        self_test()
        return
    check_assets()
    service = create_service()
    # No host/port switches: loopback only and OS-selected ephemeral port only.
    with http.server.ThreadingHTTPServer(("127.0.0.1", 0), create_handler(service)) as server:
        service._server.url = f"http://127.0.0.1:{server.server_address[1]}/"
        print(f"Independent in-memory black-train preview: {service._server.url}", flush=True)
        print("Global operating round 22 completion announces the train; round 26 completion starts the journey, after that round's animations and event windows finish. Ctrl+C discards this game.", flush=True)
        try:
            server.serve_forever()
        except KeyboardInterrupt:
            pass


if __name__ == "__main__":
    main()
