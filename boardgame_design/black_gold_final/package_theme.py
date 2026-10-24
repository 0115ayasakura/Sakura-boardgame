"""Copy six approved theme assets verbatim into the checkout plugin package.

No installed-plugin target and no arbitrary path CLI are provided. --check
validates the package and byte identity to all six design sources without writes.
Missing sources fail before copying anything. Ordinary board assets are untouched.
"""
from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path
import shutil
import sys

sys.dont_write_bytecode = True
SOURCE_DIR = Path(__file__).resolve().parent
ROOT = SOURCE_DIR.parents[1]
PLUGIN_DIR = ROOT / "sakura_boardgame"
sys.path.insert(0, str(ROOT))
from sakura_boardgame.theme_resources import (MANIFEST_NAME, MANIFEST_VERSION,
    THEME_FILES, resolve_theme_file, validate_theme_package)


def package_theme(source_dir=SOURCE_DIR, plugin_dir=PLUGIN_DIR, *, check=False):
    source_dir, plugin_dir = Path(source_dir).resolve(), Path(plugin_dir).resolve()
    sources = {}
    # Validate the complete source set before creating output directories.
    for name in sorted(THEME_FILES):
        source = (source_dir / name).resolve()
        if not source.is_relative_to(source_dir) or not source.is_file():
            raise ValueError(f"Missing or unsafe design source: {name}")
        sources[name] = source
    destination = (plugin_dir / "art" / "black_train").resolve()
    if not destination.is_relative_to(plugin_dir):
        raise ValueError("Theme destination escapes plugin directory")
    manifest = {"version": MANIFEST_VERSION, "files": {}}
    for name, source in sources.items():
        blob = source.read_bytes()
        manifest["files"][name] = {"size": len(blob), "sha256": hashlib.sha256(blob).hexdigest()}
    if check:
        actual = validate_theme_package(plugin_dir)
        if actual != manifest:
            raise ValueError("Packaged manifest differs from design source hashes")
    else:
        # Reject existing destination symlink escapes before writing any asset.
        for name in THEME_FILES | {MANIFEST_NAME}:
            if not (destination / name).resolve().is_relative_to(destination):
                raise ValueError(f"Unsafe theme destination: {name}")
        for name, source in sources.items():
            target = destination / name
            target.parent.mkdir(parents=True, exist_ok=True)
            shutil.copyfile(source, target)
        (destination / MANIFEST_NAME).write_text(
            json.dumps(manifest, indent=2, sort_keys=True) + "\n", encoding="utf-8")
        validate_theme_package(plugin_dir)
    for name, source in sources.items():
        if resolve_theme_file(plugin_dir, name).read_bytes() != source.read_bytes():
            raise ValueError(f"Packaged bytes differ from source: {name}")
    return manifest


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--check", action="store_true", help="verify only; do not copy")
    args = parser.parse_args()
    manifest = package_theme(check=args.check)
    print(json.dumps({"ok": True, "check_only": args.check, **manifest}, indent=2))


if __name__ == "__main__":
    main()
