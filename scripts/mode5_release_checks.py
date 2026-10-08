"""Run POSIX entrypoint checks on WSL with the user's pure-Python pytest stack.

The Windows interpreter cannot execute bash scripts with Linux path semantics.
This runner reuses pytest's pure-Python packages from the requested Python
installation, but runs shell checks under the Linux interpreter. Plugin
autoload is disabled to avoid importing unrelated Windows binary extensions.
"""
from __future__ import annotations

import argparse
import os
from pathlib import Path
import sys


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--pytest-site", default="/mnt/d/daily/study/3.10py/Lib/site-packages")
    parser.add_argument("--junit-xml", help="Optional test evidence output (not a score report)")
    parser.add_argument("--basetemp", help="New temporary directory; preserve licensed test evidence outside pytest retention")
    parser.add_argument("tests", nargs="*")
    args = parser.parse_args()
    if args.basetemp and Path(args.basetemp).exists():
        parser.error("--basetemp must be a new directory; never erase existing evidence")
    root = Path(__file__).resolve().parents[1]
    os.chdir(root / "eval/evalsys")
    os.environ["PYTEST_DISABLE_PLUGIN_AUTOLOAD"] = "1"
    os.environ["PYTHONPATH"] = str(root / "eval/evalsys")
    # Select only pytest's pure-Python stack from Windows. Prioritizing the
    # entire site-packages directory accidentally imports Windows numpy DLLs.
    import importlib.abc
    import importlib.machinery

    class PurePytestStack(importlib.abc.MetaPathFinder):
        def find_spec(self, fullname, path=None, target=None):
            if path is None and fullname in {
                "pytest", "_pytest", "pluggy", "iniconfig", "pygments",
                "packaging", "exceptiongroup", "tomli",
            }:
                return importlib.machinery.PathFinder.find_spec(fullname, [args.pytest_site])
            return None

    sys.meta_path.insert(0, PurePytestStack())
    sys.path.insert(0, str(root / "eval/evalsys"))
    import pytest
    return pytest.main([
        *(args.tests or ["tests/test_run_benchmark_entrypoint.py", "tests/test_evaluate_entrypoint.py"]),
        "-q", "-p", "no:cacheprovider", "--tb=short",
        *(["--junit-xml", args.junit_xml] if args.junit_xml else []),
        *(["--basetemp", args.basetemp] if args.basetemp else []),
    ])


if __name__ == "__main__":
    raise SystemExit(main())
