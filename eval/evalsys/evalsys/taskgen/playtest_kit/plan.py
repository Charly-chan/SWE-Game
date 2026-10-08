


from __future__ import annotations

import argparse
import json
import os
import re
import shutil
import subprocess
import time
from pathlib import Path


def compact(ops: list[dict]) -> list[dict]:
    result = []
    for op in ops:
        if (result and op["op"] == result[-1]["op"] == "state"
                and op.get("actions", []) == result[-1].get("actions", [])
                and result[-1]["frames"] + op["frames"] <= 600):
            result[-1]["frames"] += op["frames"]
        else:
            result.append(dict(op))
    return result


def run(project: Path, plan: Path, out: Path, *, godot: str, driver: Path) -> dict:
    out.mkdir(parents=True, exist_ok=False)
    scratch = out / "scratch"
    shutil.copytree(project, scratch, ignore=shutil.ignore_patterns(".godot", ".git", "playtest_out"))
    shutil.copy2(Path(__file__).with_name("gb_playtest_plan.gd"), scratch / "gb_playtest_plan.gd")
    shutil.copy2(plan, scratch / "gb_playtest_plan.json")
    project_file = scratch / "project.godot"
    text = project_file.read_text(encoding="utf-8")
    entry = 'GBPlaytestPlan="*res://gb_playtest_plan.gd"\n'
    if "[autoload]" in text:
        text = text.replace("[autoload]", "[autoload]\n" + entry, 1)
    else:
        text += "\n[autoload]\n" + entry
    project_file.write_text(text, encoding="utf-8")
    empty = out / "empty.json"
    empty.write_text('{"ops": []}\n', encoding="utf-8")
    measurements = {}
    for name, args in [
        ("import", ["--editor", "--import", "--quit"]),
        ("execution", ["--fixed-fps", "60", "-s", str(driver), "--", str(empty)]),
    ]:
        start = time.monotonic()
        with (out / f"{name}.log").open("w", encoding="utf-8") as log:
            proc = subprocess.run([godot, "--headless", "--path", str(scratch), *args],
                                  stdout=log, stderr=subprocess.STDOUT, timeout=110)
        measurements[name] = {"exit_code": proc.returncode, "wall_s": time.monotonic() - start}
    result_path = scratch / "gb_playtest_plan_result.json"
    result = json.loads(result_path.read_text()) if result_path.exists() else {
        "status": "incomplete", "reason": "controller produced no result", "ops": []}
    ops = compact(result.pop("ops"))
    result["measurements"] = measurements
    result["exported_ops"] = len(ops)
    result["exported_frames"] = sum(op["frames"] for op in ops)
    log = (out / "execution.log").read_text(encoding="utf-8")
    result["replay_final"] = re.findall(r"^REPLAY_FINAL .*$", log, re.M)
    result["script_errors"] = len(re.findall(r"^SCRIPT ERROR", log, re.M))
    (out / "generated-ops.json").write_text(json.dumps({"ops": ops}, indent=2) + "\n", encoding="utf-8")
    (out / "result.json").write_text(json.dumps(result, indent=2) + "\n", encoding="utf-8")
    return result


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("project", type=Path)
    parser.add_argument("plan", type=Path)
    parser.add_argument("out", type=Path)
    parser.add_argument("--godot", default=os.environ.get("GODOT_BIN") or shutil.which("godot"))
    parser.add_argument("--driver", type=Path,
                        default=Path(__file__).resolve().parent.parent / "interface/replay_ops.gd")
    args = parser.parse_args()
    if not args.godot:
        parser.error("Godot not found: set GODOT_BIN or pass --godot")
    project = args.project.resolve()
    if not (project / "project.godot").is_file() and (project / "game/project.godot").is_file():
        project = project / "game"
    result = run(project, args.plan.resolve(), args.out.resolve(),
                 godot=args.godot, driver=args.driver.resolve())
    print(json.dumps({k: v for k, v in result.items() if k not in {"history", "target_observations"}},
                     ensure_ascii=False, indent=2))
    print(f"Full observations: {args.out / 'result.json'}")
    print(f"Replay this input with playtest.sh: {args.out / 'generated-ops.json'}")
    raise SystemExit(0 if result["status"] == "complete" and not result["script_errors"] else 1)


if __name__ == "__main__":
    main()
