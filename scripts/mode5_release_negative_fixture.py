"""Evaluate a disposable C# failure fixture, never a model-authored submission.

Runs on the licensed Linux coordinator. The original fixture is untouched.
"""
from __future__ import annotations

import argparse
from pathlib import Path
import json
import shutil
import sys


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--state-dir", required=True)
    parser.add_argument("--package", required=True)
    parser.add_argument("--positive-fixture", required=True)
    parser.add_argument("--out", required=True)
    parser.add_argument("--restore-positive-control", action="store_true",
                        help="Copy our controlled negative fixture without its injected syntax-error file")
    args = parser.parse_args()
    root = Path(__file__).resolve().parents[1]
    sys.path.insert(0, str(root / "eval/evalsys"))
    from evalsys.taskgen.matrix import collect_submission
    from evalsys.taskgen.mode5.community_cli import main as mode5_main
    out = Path(args.out).resolve()
    if out.exists():
        parser.error("choose a new output directory; no fixture is overwritten")
    out.mkdir(parents=True)
    submission = out / "submission"
    if args.restore_positive_control:
        source = Path(args.positive_fixture).resolve()
        injected = source / "Assets/Game/GBReleaseIntentionalSyntaxError.cs"
        expected = "public class GBReleaseIntentionalSyntaxError { this is invalid C#; }\n"
        if not injected.is_file() or injected.read_text(encoding="utf-8") != expected:
            parser.error("restore only accepts the exact controlled negative fixture made by this script")
        shutil.copytree(source, submission, ignore=shutil.ignore_patterns(
            "GBReleaseIntentionalSyntaxError.cs", "GBReleaseIntentionalSyntaxError.cs.meta"))
        (out / "control-fixture.json").write_text(json.dumps({
            "fixture_class": "controlled-human-fixture-not-model-output",
            "source": str(source),
            "omitted": ["Assets/Game/GBReleaseIntentionalSyntaxError.cs"],
            "original_unchanged": True,
        }, indent=2) + "\n", encoding="utf-8")
        print(json.dumps({"control_submission": str(submission)}))
        return 0
    collect_submission(Path(args.positive_fixture).resolve(), submission, mode="port")
    bad = submission / "Assets/Game/GBReleaseIntentionalSyntaxError.cs"
    bad.parent.mkdir(parents=True, exist_ok=True)
    bad.write_text("public class GBReleaseIntentionalSyntaxError { this is invalid C#; }\n", encoding="utf-8")
    return mode5_main([
        "evaluate", "--state-dir", args.state_dir, "--package", args.package,
        "--submission", str(submission), "--out", str(out / "evaluation"),
    ])


if __name__ == "__main__":
    raise SystemExit(main())
