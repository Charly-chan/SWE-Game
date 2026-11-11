#!/usr/bin/env python3


from __future__ import annotations

import json
import re
import sys
from pathlib import Path

DEFAULT_FRAMES = {"noop": 1, "wait": 30, "tap": 6, "hold": 30, "state": 1, "release": 1}
SUCCESS_ENDINGS = ("victory", "success", "win", "complete", "run_complete")
FAILURE_ENDINGS = ("defeat", "failure", "failed", "loss", "lose")
TAIL_FRAMES = 30

_FINAL_RE = re.compile(r"^REPLAY_FINAL frame=(\d+) tape_frame=(\d+) scene=(\S*)")
_SCENE_RE = re.compile(r"^REPLAY_SCENE .*scene=(\S*)(?: ending=(\S+))?")
_HEALTH_RE = re.compile(r"^PLAYTEST_HEALTH frame=(\d+) value=(-?[0-9.]+)")


def _manifest(project_root: Path | None) -> dict:
    if project_root is None:
        return {}
    manifest = project_root / "gb_levels.json"
    if not manifest.is_file():
        return {}
    try:
        parsed = json.loads(manifest.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return {}
    return parsed if isinstance(parsed, dict) else {}


def load_ops(path: Path) -> list[dict]:
    parsed = json.loads(path.read_text(encoding="utf-8"))
    if isinstance(parsed, dict):
        parsed = parsed.get("ops", [])
    if not isinstance(parsed, list):
        raise SystemExit(f"pt_tape: {path} is neither an ops array nor an object with ops")
    return [op for op in parsed if isinstance(op, dict)]


def op_frames(op: dict) -> int:
    kind = str(op.get("op", ""))
    default = DEFAULT_FRAMES.get(kind, 1)
    try:
        frames = int(op.get("frames", default))
    except (TypeError, ValueError):
        frames = default
    return max(1, min(600, frames))


def tape_frames(ops: list[dict]) -> int:
    return sum(op_frames(op) for op in ops)


def null_control(ops: list[dict]) -> dict:
    return {"ops": [{"op": "wait", "frames": op_frames(op)} for op in ops]}


def endings(project_root: Path | None) -> dict[str, str]:

    table = _manifest(project_root).get("endings")
    if not isinstance(table, dict):
        return {}
    return {
        str(scene): str(key)
        for key, scene in table.items()
        if isinstance(scene, str) and (key in SUCCESS_ENDINGS or key in FAILURE_ENDINGS)
    }


def declared_levels(project_root: Path | None) -> list[str]:

    levels = _manifest(project_root).get("levels")
    if not isinstance(levels, list):
        return []
    return [str(item) for item in levels if isinstance(item, str) and item.startswith("res://")]


def health_property(project_root: Path | None) -> str:

    numeric = _manifest(project_root).get("numeric")
    if not isinstance(numeric, dict):
        return ""
    value = numeric.get("health", "")
    return value.strip() if isinstance(value, str) else ""


def visited_levels(scenes: list[str], levels: list[str]) -> list[str]:

    wanted = set(levels)
    seen: list[str] = []
    for scene in scenes:
        if scene in wanted and scene not in seen:
            seen.append(scene)
    return seen


def summarise(log_text: str, project_root: Path | None) -> dict:
    final = None
    scenes: list[tuple[str, str]] = []
    health: list[tuple[int, float]] = []
    warnings = 0
    replay_errors = 0
    script_errors = 0
    for line in log_text.splitlines():
        stripped = line.strip()
        match = _FINAL_RE.match(stripped)
        if match:
            final = (int(match.group(1)), int(match.group(2)), match.group(3))
            continue
        match = _SCENE_RE.match(stripped)
        if match:
            scenes.append((match.group(1), match.group(2) or ""))
            continue
        match = _HEALTH_RE.match(stripped)
        if match:
            try:
                health.append((int(match.group(1)), float(match.group(2))))
            except ValueError:
                pass
            continue
        if stripped.startswith("REPLAY_WARNING"):
            warnings += 1
        elif stripped.startswith("REPLAY_ERROR"):
            replay_errors += 1
        elif stripped.startswith("SCRIPT ERROR"):
            script_errors += 1
    table = endings(project_root)
    final_scene = final[2] if final else ""
    ending = table.get(final_scene, "")
    if not ending:
        for scene, key in scenes:
            if scene == final_scene and key:
                ending = key
    reached_success = ending in SUCCESS_ENDINGS
    levels = declared_levels(project_root)
    visited = visited_levels([scene for scene, _ in scenes], levels)
    prop = health_property(project_root)
    # "n/a": no numeric.health declared; "unresolved": declared but the probe


    if not prop:
        health_decreased = "n/a"
    elif not health:
        health_decreased = "unresolved"
    else:
        health_decreased = "yes" if any(b < a for (_, a), (_, b) in zip(health, health[1:])) else "no"
    return {
        "finished": final is not None,
        "frames": final[0] if final else 0,
        "tape_frames": final[1] if final else 0,
        "final_scene": final_scene,
        "ending": ending,
        "reached_success_ending": reached_success,
        "scenes_visited": [scene for scene, _ in scenes],
        "levels_declared": levels,
        "levels_visited": visited,
        "health_property": prop,
        "health_series": health,
        "health_decreased": health_decreased,
        "replay_warnings": warnings,
        "replay_errors": replay_errors,
        "script_errors": script_errors,
    }


def _verdict_line(prefix: str, summary: dict) -> str:
    levels = summary["levels_declared"]
    levels_visited = f"{len(summary['levels_visited'])}/{len(levels)}" if levels else "-"
    return (
        f"{prefix} finished={'yes' if summary['finished'] else 'no'}"
        f" reached_success_ending={'yes' if summary['reached_success_ending'] else 'no'}"
        f" ending={summary['ending'] or '-'}"
        f" final_scene={summary['final_scene'] or '-'}"
        f" frames={summary['frames']} tape_frames={summary['tape_frames']}"
        f" script_errors={summary['script_errors']}"
        f" replay_errors={summary['replay_errors']}"
        f" replay_warnings={summary['replay_warnings']}"
        f" levels_visited={levels_visited}"
        f" health_decreased={summary['health_decreased']}"
    )


def _levels_line(summary: dict) -> str:
    levels = summary["levels_declared"]
    visited = summary["levels_visited"]
    missed = [level for level in levels if level not in visited]
    return (
        f"PLAYTEST_LEVELS declared={len(levels)} visited={len(visited)}"
        f" order={','.join(visited) or '-'} missed={','.join(missed) or '-'}"
    )


def _health_line(summary: dict) -> str:
    prop = summary["health_property"]
    series = summary["health_series"]
    if not prop:
        return "PLAYTEST_HEALTH_SUMMARY declared=no"
    if not series:
        return f"PLAYTEST_HEALTH_SUMMARY declared={prop} readable=no"
    values = [value for _, value in series]
    return (
        f"PLAYTEST_HEALTH_SUMMARY declared={prop} readable=yes first={values[0]:g}"
        f" min={min(values):g} last={values[-1]:g} changes={len(series) - 1}"
        f" decreased={summary['health_decreased']}"
    )


def main(argv: list[str]) -> int:
    if len(argv) < 2:
        print(__doc__, file=sys.stderr)
        return 2
    command = argv[1]
    if command == "frames" and len(argv) == 3:
        print(tape_frames(load_ops(Path(argv[2]))))
        return 0
    if command == "null" and len(argv) == 4:
        control = null_control(load_ops(Path(argv[2])))
        Path(argv[3]).write_text(json.dumps(control) + "\n", encoding="utf-8")
        print(tape_frames(control["ops"]))
        return 0
    if command in ("summary", "details") and len(argv) in (3, 4, 5):
        log = Path(argv[2]).read_text(encoding="utf-8", errors="replace")
        root = Path(argv[3]) if len(argv) >= 4 and argv[3] else None
        prefix = argv[4] if len(argv) == 5 else "PLAYTEST_VERDICT"
        summary = summarise(log, root)
        if command == "summary":
            print(_verdict_line(prefix, summary))
        else:
            print(_levels_line(summary))
            print(_health_line(summary))
        return 0
    print(__doc__, file=sys.stderr)
    return 2


if __name__ == "__main__":
    raise SystemExit(main(sys.argv))
