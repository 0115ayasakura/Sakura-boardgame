"""Packaged black-train theme boundary (stdlib only; no workspace fallbacks).

The manifest detects incomplete/changed packages, not hostile replacement of an
entire plugin. Only the six named assets are HTTP-visible, never the manifest.
"""
from __future__ import annotations

import hashlib
import json
from pathlib import Path

THEME_FILES = frozenset({"station_black_gold.glb", "tile_black_gold.png"} | {
    f"icons_unified/{key}.png" for key in
    ("gold_ticket", "blue_contract", "white_beg", "black_anomaly")})
MANIFEST_NAME = "manifest.json"
MANIFEST_VERSION = 1


def _package_root(plugin_dir: Path) -> Path | None:
    plugin = Path(plugin_dir).resolve()
    root = (plugin / "art" / "black_train").resolve()
    return root if root.is_relative_to(plugin) else None


def resolve_theme_file(plugin_dir: Path, name: str) -> Path | None:
    """Resolve an already URL-decoded relative whitelist name, or deny it.

    This is not an arbitrary /art resolver. Backslashes, extra encoding, query
    strings, absolute names and traversal all fail the exact whitelist check.
    """
    if not isinstance(name, str) or name not in THEME_FILES:
        return None
    root = _package_root(plugin_dir)
    if root is None:
        return None
    target = (root / name).resolve()
    return target if target.is_relative_to(root) and target.is_file() else None


def validate_theme_package(plugin_dir: Path) -> dict:
    """Fail closed on missing files, invalid schema, symlink escape or hashes."""
    root = _package_root(plugin_dir)
    if root is None:
        raise ValueError("Theme package escapes plugin directory")
    manifest = (root / MANIFEST_NAME).resolve()
    if not manifest.is_relative_to(root) or not manifest.is_file():
        raise ValueError("Missing or unsafe black-train manifest")
    try:
        data = json.loads(manifest.read_text(encoding="utf-8"))
    except (OSError, ValueError) as error:
        raise ValueError("Invalid black-train manifest") from error
    if (not isinstance(data, dict) or set(data) != {"version", "files"}
            or type(data["version"]) is not int or data["version"] != MANIFEST_VERSION
            or not isinstance(data["files"], dict)
            or set(data["files"]) != THEME_FILES):
        raise ValueError("Unexpected black-train manifest schema or file set")
    for name in sorted(THEME_FILES):
        entry = data["files"][name]
        if (not isinstance(entry, dict) or set(entry) != {"size", "sha256"}
                or type(entry["size"]) is not int or entry["size"] < 0
                or not isinstance(entry["sha256"], str)
                or len(entry["sha256"]) != 64
                or any(c not in "0123456789abcdef" for c in entry["sha256"])):
            raise ValueError(f"Invalid theme manifest entry: {name}")
        target = resolve_theme_file(plugin_dir, name)
        if target is None:
            raise ValueError(f"Missing or unsafe theme asset: {name}")
        if (target.stat().st_size != entry["size"]
                or hashlib.sha256(target.read_bytes()).hexdigest() != entry["sha256"]):
            raise ValueError(f"Theme asset hash/size mismatch: {name}")
    return data


def runtime_config(plugin_dir: Path) -> dict:
    """No package => ordinary board; installed invalid package => explicit error."""
    package = Path(plugin_dir) / "art" / "black_train"
    if not package.exists() and not package.is_symlink():
        return {}
    validate_theme_package(plugin_dir)
    return {"blackTrainTheme": {"baseUrl": "/theme"}}
