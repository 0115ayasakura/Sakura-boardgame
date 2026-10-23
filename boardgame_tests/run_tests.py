"""Run every runtime regression script in an isolated process (no installed plugin changes).

Usage: python -B boardgame_tests/run_tests.py [--node /path/to/node]
Real Edge/WebGL tests remain opt-in: verify_browser_lifecycle.mjs needs serve_plugin.py.
"""
from __future__ import annotations

import argparse
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
import time


def main() -> int:
    here = Path(__file__).resolve().parent
    parser = argparse.ArgumentParser(description=__doc__)
    bundled_node = Path(sys.executable).resolve().parent.parent / 'node' / 'bin' / 'node.exe'
    parser.add_argument('--node', default=shutil.which('node') or
                        (str(bundled_node) if bundled_node.is_file() else None))
    args = parser.parse_args()
    results = []
    # Windows redirected stdout otherwise uses GBK and fails on valid UI text (¥).
    child_env = dict(os.environ, PYTHONIOENCODING='utf-8')
    tests = [(test, [sys.executable, '-B', str(test)]) for test in sorted(here.glob('test_*.py'))]
    tests += [(test, [args.node, str(test)]) for test in sorted(here.glob('test_*.mjs'))]
    # Renderer geometry tests live alongside the assets, not in this directory.
    icons = here.parent / 'sakura_boardgame' / 'tests' / 'black_train_icons.test.mjs'
    tests.append((icons, [args.node, '--test', str(icons)]))
    for test, command in tests:
        print(f'\n=== {test.name} ===', flush=True)
        started = time.monotonic()
        if command[0] is None:
            print('Node.js is required for JS tests; supply --node.', flush=True)
            code = 127
        else:
            try:
                code = subprocess.run(command, cwd=here.parent, timeout=180, env=child_env).returncode
            except (OSError, subprocess.TimeoutExpired) as error:
                print(f'FAILED: {error}', flush=True)
                code = 1
        results.append({'test': test.name, 'exitCode': code,
                        'seconds': round(time.monotonic() - started, 3)})
    passed = sum(item['exitCode'] == 0 for item in results)
    report = {'passed': passed == len(results), 'scriptsPassed': passed,
              'scriptsTotal': len(results), 'results': results}
    (here / 'test_run_report.json').write_text(json.dumps(report, indent=2) + '\n', encoding='utf-8')
    print(f'\n{passed}/{len(results)} regression scripts passed.', flush=True)
    return 0 if report['passed'] else 1


if __name__ == '__main__':
    raise SystemExit(main())
