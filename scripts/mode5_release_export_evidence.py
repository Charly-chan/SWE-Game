"""Export selected public evaluation artifacts; never private licensing state.

Reports keep their original coordinator paths. The exported tree is a review
copy, not a relocated evaluation for rejudge; use the retained native originals.
"""
from __future__ import annotations

import argparse
import json
from pathlib import Path
import shutil
import re


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--runs", required=True)
    parser.add_argument("--out", required=True)
    parser.add_argument("--state-dir")
    parser.add_argument("--extra-evaluation", action="append", default=[], metavar="NAME=PATH")
    parser.add_argument("--only-extra", action="store_true")
    args = parser.parse_args()
    runs, out = Path(args.runs).resolve(), Path(args.out).resolve()
    if out.exists():
        parser.error("choose a new export directory")
    selected = {
        "controlled-positive": "controlled-positive",
        "controlled-syntax-negative": "controlled-syntax-negative/evaluation",
        "glm-20min-delivery": "cat-defense-glm-cc/evaluation-fixed-delivery",
        "glm-20min-rejudge": "cat-defense-glm-cc/rejudged-delivery",
    }
    selected = {} if args.only_extra else {name: runs / relative for name, relative in selected.items()}
    for value in args.extra_evaluation:
        name, separator, source = value.partition("=")
        if not separator or not re.fullmatch(r"[A-Za-z0-9_-]+", name) or name in selected:
            parser.error("extra evaluation needs a unique safe NAME=PATH")
        selected[name] = Path(source).resolve()
    if not selected:
        parser.error("select at least one evaluation")
    index = {"purpose": "review copies; native originals remain authoritative", "evaluations": {}}
    for name, source in selected.items():
        if not (source / "report.json").is_file():
            parser.error("a requested evaluation report is missing")
        if any(path.is_symlink() for path in source.rglob("*")):
            parser.error("refusing linked artifact paths")
        if any(path.suffix.lower() in {".ulf", ".alf"} for path in source.rglob("*")):
            parser.error("refusing licensing artifacts")
        shutil.copytree(source, out / name)
        index["evaluations"][name] = {"source": str(source), "report": name + "/report.json"}
    status = runs / "agent-helper-control-v3/status.json"
    if status.is_file():
        shutil.copy2(status, out / "agent-selfcheck.json")
    if args.state_dir:
        preflight = Path(args.state_dir) / "last-preflight.json"
        report = json.loads(preflight.read_text(encoding="utf-8"))
        if report.get("status") != "pass":
            parser.error("export only a passing preflight, not private failure logs")
        shutil.copy2(preflight, out / "doctor.json")
    (out / "index.json").write_text(json.dumps(index, indent=2) + "\n", encoding="utf-8")
    print(json.dumps({"exported_evaluations": len(selected), "out": str(out)}))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
