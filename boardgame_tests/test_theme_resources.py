"""Theme packaging/config boundary tests; no sockets or installed-plugin writes."""
from __future__ import annotations

import importlib.util
import json
from pathlib import Path
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import patch

import sys
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from sakura_boardgame.theme_resources import (
    THEME_FILES, resolve_theme_file, runtime_config, validate_theme_package)

ROOT = Path(__file__).resolve().parents[1]


def load_design_module(name):
    spec = importlib.util.spec_from_file_location(name, ROOT / "boardgame_design" /
                                                "black_gold_final" / (name + ".py"))
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


packager = load_design_module("package_theme")
preview = load_design_module("serve_game_preview")


class ThemeResourceTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.source, self.plugin = self.root / "design", self.root / "plugin"
        self.source.mkdir()
        self.plugin.mkdir()
        for name in THEME_FILES:
            path = self.source / name
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_bytes(("fixture:" + name).encode())

    def package(self):
        return packager.package_theme(self.source, self.plugin)

    def test_exact_package_and_nonwriting_check(self):
        original = self.plugin / "art" / "board" / "station_remake.glb"
        original.parent.mkdir(parents=True)
        original.write_bytes(b"ordinary board must remain unchanged")
        manifest = self.package()
        self.assertEqual(set(manifest["files"]), THEME_FILES)
        self.assertEqual(validate_theme_package(self.plugin), manifest)
        self.assertEqual(runtime_config(self.plugin), {"blackTrainTheme": {"baseUrl": "/theme"}})
        files = list((self.plugin / "art" / "black_train").rglob("*"))
        stamps = {p: p.stat().st_mtime_ns for p in files if p.is_file()}
        self.assertEqual(packager.package_theme(self.source, self.plugin, check=True), manifest)
        self.assertEqual(stamps, {p: p.stat().st_mtime_ns for p in stamps})
        self.assertEqual(original.read_bytes(), b"ordinary board must remain unchanged")
        for name in THEME_FILES:
            self.assertEqual(resolve_theme_file(self.plugin, name).read_bytes(),
                             (self.source / name).read_bytes())

    def test_absent_package_falls_back_and_missing_source_fails_before_copy(self):
        self.assertEqual(runtime_config(self.plugin), {})
        (self.source / "station_black_gold.glb").unlink()
        with self.assertRaisesRegex(ValueError, "Missing or unsafe design source"):
            self.package()
        self.assertFalse((self.plugin / "art").exists())
        with self.assertRaises(ValueError):
            validate_theme_package(self.plugin)

    def test_changed_and_missing_asset_fail_closed(self):
        self.package()
        asset = resolve_theme_file(self.plugin, "tile_black_gold.png")
        blob = asset.read_bytes()
        asset.write_bytes(bytes([blob[0] ^ 1]) + blob[1:])
        with self.assertRaisesRegex(ValueError, "hash/size mismatch"):
            runtime_config(self.plugin)
        asset.unlink()
        self.assertIsNone(resolve_theme_file(self.plugin, "tile_black_gold.png"))
        with self.assertRaises(ValueError):
            validate_theme_package(self.plugin)

    def test_manifest_schema_and_whitelist(self):
        self.package()
        for name in ("../station_black_gold.glb", "/station_black_gold.glb",
                     "icons_unified/../tile_black_gold.png", "icons_unified\\gold_ticket.png",
                     "%2e%2e/station_black_gold.glb", "manifest.json", "tile_black_gold.png?x=1",
                     "station_black_gold.blend", "", None):
            self.assertIsNone(resolve_theme_file(self.plugin, name), name)
        manifest_path = self.plugin / "art" / "black_train" / "manifest.json"
        manifest = json.loads(manifest_path.read_text())
        manifest["files"]["../outside.png"] = {"size": 0, "sha256": "0" * 64}
        manifest_path.write_text(json.dumps(manifest))
        with self.assertRaisesRegex(ValueError, "schema or file set"):
            validate_theme_package(self.plugin)
        manifest_path.write_text("not JSON")
        with self.assertRaisesRegex(ValueError, "Invalid black-train manifest"):
            runtime_config(self.plugin)

    def test_source_change_detected_by_check(self):
        self.package()
        (self.source / "tile_black_gold.png").write_bytes(b"new source")
        with self.assertRaisesRegex(ValueError, "differs from design source"):
            packager.package_theme(self.source, self.plugin, check=True)

    def test_symlink_escape_is_denied(self):
        self.package()
        target = self.plugin / "art" / "black_train" / "tile_black_gold.png"
        target.unlink()
        outside = self.root / "outside.png"
        outside.write_bytes(b"outside")
        try:
            target.symlink_to(outside)
        except OSError as error:
            self.skipTest(f"Symlink creation unavailable: {error}")
        self.assertIsNone(resolve_theme_file(self.plugin, "tile_black_gold.png"))
        with self.assertRaises(ValueError):
            validate_theme_package(self.plugin)
        with self.assertRaisesRegex(ValueError, "Unsafe theme destination"):
            self.package()
        self.assertEqual(outside.read_bytes(), b"outside")

    def test_package_root_symlink_escape_is_denied(self):
        outside = self.root / "external-package"
        outside.mkdir()
        art = self.plugin / "art"
        art.mkdir()
        try:
            (art / "black_train").symlink_to(outside, target_is_directory=True)
        except OSError as error:
            self.skipTest(f"Symlink creation unavailable: {error}")
        self.assertIsNone(resolve_theme_file(self.plugin, "tile_black_gold.png"))
        with self.assertRaisesRegex(ValueError, "escapes plugin"):
            runtime_config(self.plugin)
        with self.assertRaisesRegex(ValueError, "escapes plugin"):
            self.package()
        self.assertEqual(list(outside.iterdir()), [])

    def test_source_symlink_escape_fails_before_copy(self):
        target = self.source / "tile_black_gold.png"
        target.unlink()
        outside = self.root / "outside-source.png"
        outside.write_bytes(b"outside")
        try:
            target.symlink_to(outside)
        except OSError as error:
            self.skipTest(f"Symlink creation unavailable: {error}")
        with self.assertRaisesRegex(ValueError, "Missing or unsafe design source"):
            self.package()
        self.assertFalse((self.plugin / "art").exists())

    def test_preview_raw_html_fixed_config_and_get_whitelist_without_socket(self):
        self.package()
        html = b'<html>arbitrary bootstrap; no string replacement</html>'
        (self.plugin / "board.html").write_bytes(html)
        service = SimpleNamespace(_server=SimpleNamespace(_touch=lambda: None))
        handler_class = preview.create_handler(service)
        handler = handler_class.__new__(handler_class)
        responses = []
        handler._trusted_request = lambda: True
        handler._send = lambda status, data, mime: responses.append((status, data, mime))
        handler._send_file = lambda path: responses.append((200, path.read_bytes(), path.suffix))
        with patch.object(preview, "PLUGIN_DIR", self.plugin):
            self.assertEqual(preview.preview_html(), html)
            config = preview.preview_config().decode()
            self.assertTrue(config.startswith("globalThis.BoardRuntimeConfig = "))
            self.assertEqual(json.loads(config.split(" = ", 1)[1].removesuffix(";\n")),
                             {"blackTrainTheme": {"baseUrl": "/theme"}})
            for path, expected in (("/board.html", 200), ("/board-config.js", 200),
                                   ("/theme/tile_black_gold.png", 200),
                                   ("/theme/manifest.json", 404),
                                   ("/theme/%2e%2e/board.html", 404),
                                   ("/theme/%252e%252e/board.html", 404)):
                handler.path = path
                handler.do_GET()
                self.assertEqual(responses[-1][0], expected, path)
            handler._trusted_request = lambda: False
            n = len(responses)
            handler.do_GET()
            self.assertEqual(len(responses), n)

    def test_checkout_distribution_manifest_matches_packaged_assets(self):
        # The release intentionally ships size-optimized assets, not private/raw
        # design sources. Validate every distributed byte against its manifest;
        # temporary fixtures above still test exact source copying/check mode.
        manifest = validate_theme_package(ROOT / "sakura_boardgame")
        self.assertEqual(len(manifest["files"]), 6)
        for entry in manifest["files"].values():
            self.assertGreater(entry["size"], 0)
            self.assertEqual(len(entry["sha256"]), 64)


if __name__ == "__main__":
    unittest.main()
