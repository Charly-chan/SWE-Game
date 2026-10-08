#!/usr/bin/env python3


from __future__ import annotations

import argparse
import difflib
import json
import re
from pathlib import Path

PREFIX = "PLAYTEST_TRACE "
STATE_ID = ("scene", "node", "script", "property")
STATE_VALUES = ("before_observed", "before", "first", "last", "min", "max", "changes", "observed_frames")


def read_json(path: Path, default):
    return json.loads(path.read_text(encoding="utf-8")) if path.is_file() else default


def prepare(project: Path, watch_path: Path | None) -> None:
    manifest = read_json(project / "gb_levels.json", {})
    numeric = manifest.get("numeric", {})
    fields = [v for v in numeric.values() if isinstance(v, str)] if isinstance(numeric, dict) else []
    watches = read_json(watch_path, []) if watch_path else []
    if not isinstance(watches, list) or any(
        not isinstance(w, dict) or not (w.get("script") or w.get("node"))
        or not isinstance(w.get("properties"), list)
        or any(not isinstance(v, str) for v in w["properties"])
        for w in watches
    ):
        raise ValueError("watch must be an array of {script or node, properties: [names]}")
    (project / "gb_playtest_trace.json").write_text(
        json.dumps({"fields": fields, "watches": watches}) + "\n", encoding="utf-8")


def source_context(project: Path, script: str, field: str) -> list[dict]:
    path = project / script.removeprefix("res://")
    if not script.startswith("res://") or not path.is_file():
        return []
    token = field.split(".")[-1]
    if not token.isidentifier():
        token = field.split(".")[0]
    pattern = re.compile(r"\b" + re.escape(token) + r"\b")
    function = "<declaration>"
    hits = []
    for number, line in enumerate(path.read_text(encoding="utf-8").splitlines(), 1):
        found = re.match(r"\s*(?:static\s+)?func\s+(\w+)", line)
        if found:
            function = found.group(1)
        if not line.lstrip().startswith("#") and pattern.search(line):
            hits.append({"line": number, "function": function, "code": line.strip()[:240]})
    return hits[:8]


def execution_error_groups(project: Path, lines: list[str]) -> list[dict]:

    groups = {}
    for index, line in enumerate(lines):
        if not line.startswith(("REPLAY_ERROR", "SCRIPT ERROR")):
            continue
        stack = []
        for following in lines[index + 1:]:
            stripped = following.strip()
            if not (stripped.startswith(("at:", "GDScript backtrace"))
                    or re.match(r"\[\d+\] ", stripped)):
                break
            stack.append(stripped)
        key = (line, tuple(stack))
        if key in groups:
            groups[key]["count"] += 1
            continue
        locations = []
        for entry in stack:
            match = re.search(r"\((res://.+?):(\d+)\)", entry)
            if not match:
                continue
            script, number = match.group(1), int(match.group(2))
            path = project / script.removeprefix("res://")
            if any(loc["script"] == script and loc["line"] == number for loc in locations):
                continue
            loc = {"script": script, "line": number}
            if path.is_file():
                source = path.read_text(encoding="utf-8").splitlines()
                if 1 <= number <= len(source):
                    loc["code"] = source[number - 1].strip()
            locations.append(loc)
        groups[key] = {"message": line, "count": 1, "stack": stack, "locations": locations}
    return list(groups.values())


def ops_array(raw) -> list:
    return raw.get("ops", []) if isinstance(raw, dict) else raw


def compare_operations(report: dict, previous: dict) -> dict:

    current_ops = ops_array(report["ops"])
    prior_ops = ops_array(previous.get("ops", []))
    prefix = 0
    for current, prior in zip(current_ops, prior_ops):
        if current != prior:
            break
        prefix += 1
    same = current_ops == prior_ops
    result = {"available": bool(report.get("operations") and previous.get("operations")),
              "same_inputs": same, "shared_prefix_ops": prefix,
              "compared_windows": 0, "first_difference": None}
    if not result["available"]:
        return result
    old = {(w["phase"], w["op_index"]): w for w in previous["operations"]}

    def state_map(window):
        out = {}
        for row in window["states"]:

            out.setdefault(tuple(row[k] for k in STATE_ID), []).append(row)
        return out

    for window in report["operations"]:
        phase, index = window["phase"], window["op_index"]
        if (phase == "op" and index >= prefix) or (phase == "tail" and not same):
            continue
        prior = old.get((phase, index))
        if prior is None:
            continue

        if phase == "op" and not (window.get("complete") and prior.get("complete")):
            continue
        result["compared_windows"] += 1
        before, after = state_map(prior), state_map(window)
        differences = []
        for key in sorted(before.keys() | after.keys()):
            before_rows, after_rows = before.get(key, []), after.get(key, [])
            before_values = [{k: r[k] for k in STATE_VALUES} for r in before_rows]
            after_values = [{k: r[k] for k in STATE_VALUES} for r in after_rows]
            if before_values != after_values:
                differences.append({**dict(zip(STATE_ID, key)),
                                    "previous": before_rows, "current": after_rows})
        if differences:
            result["first_difference"] = {
                "phase": phase, "op_index": index,
                "first_frame": window["first_frame"], "last_frame": window["last_frame"],
                "first_tape_frame": window["first_tape_frame"],
                "last_tape_frame": window["last_tape_frame"],
                "differences": differences,
            }
            break
    return result


def window_label(window: dict, ops: list) -> str:
    index = window["op_index"]
    if window["phase"] != "op":
        return window["phase"]
    op = ops[index]
    if not isinstance(op, dict):
        return f"ops[{index}] invalid operation"
    actions = op.get("actions", [op["action"]] if op.get("action") else [])
    return f"ops[{index}] {op.get('op', 'unknown')} {' '.join(actions)}".rstrip()


def state_reading(row: dict) -> str:
    before = repr(row["before"]) if row["before_observed"] else "unobserved"
    span = f"; range {row['min']!r}–{row['max']!r}" if row["min"] is not None else ""
    return f"{before} → {row['last']!r}{span}; {row['changes']} changes"


def render_operations(report: dict, previous: dict) -> list[str]:
    if not report.get("operations"):
        return []
    ops = ops_array(report["ops"])
    lines = ["## Operations and observed effects", "",
             "Indices refer to the zero-based entries in ops.json. Each window includes every observed tick, even after change samples reach their cap.",
             "The before value is sampled on the preceding tick for that same object. New or unreadable objects have an unobserved before value.",
             "Windows show when changes were observed, not what caused them; a buffered input edge can take effect in the following operation.", ""]
    comparison = report["operation_comparison"]
    if previous and comparison["available"]:
        lines += [f"Compared {comparison['compared_windows']} window(s) in the unchanged input prefix."]
        if not comparison["same_inputs"]:
            lines += [f"Inputs first differ at ops[{comparison['shared_prefix_ops']}]; later windows are not compared."]
        first = comparison["first_difference"]
        if first:
            lines += [f"**First observed difference: {window_label(first, ops)}, "
                      f"tape frames {first['first_tape_frame']}–{first['last_tape_frame']}.**", ""]
            for diff in first["differences"][:8]:
                before = "; ".join(state_reading(r) for r in diff["previous"]) or "unobserved"
                after = "; ".join(state_reading(r) for r in diff["current"]) or "unobserved"
                lines += [f"- `{diff['node']}.{diff['property']}` (`{diff['script']}`): "
                          f"previous [{before}]; current [{after}]."]
            if len(first["differences"]) > 8:
                lines.append("Additional changed readings are in trace.json → operation_comparison.")
        else:
            lines += ["No difference found in the comparable completed windows."]
        lines.append("")
    lines += ["| Operation | Tape frames | Observed changes (excerpt) |",
              "| --- | --- | --- |"]
    for window in report["operations"]:
        changed = [r for r in window["states"] if r["changes"] or not r["before_observed"]]
        cells = [f"`{r['node']}.{r['property']}`: {state_reading(r)}" for r in changed[:4]]
        if len(changed) > 4:
            cells.append(f"… {len(changed) - 4} more readings")
        label = window_label(window, ops)
        if window.get("complete") is False:
            label += " (partial)"
        reading = "; ".join(cells) if cells else f"No observed change in {len(window['states'])} readable values"

        reading = reading.replace("|", "\\|").replace("\n", " ")
        lines.append(f"| `{label}` | {window['first_tape_frame']}–{window['last_tape_frame']} | {reading} |")
    lines += ["", "Full per-operation before/after, ranges, observation counts and frame bounds are in trace.json → operations.", ""]
    return lines


def build_report(project: Path, ops: Path, log: Path, out: Path) -> dict:
    events = []
    log_lines = log.read_text(encoding="utf-8", errors="replace").splitlines()
    for line in log_lines:
        if line.startswith(PREFIX):
            events.append(json.loads(line[len(PREFIX):]))
    summaries = [e for e in events if e.get("kind") == "summary"]
    operations = [e for e in events if e.get("kind") == "operation"]
    sources = {}
    for row in summaries:
        script = row["script"]
        path = project / script.removeprefix("res://")
        if script.startswith("res://") and path.is_file():
            sources[script] = path.read_text(encoding="utf-8")
        row["source_references"] = source_context(project, script, row["property"])
    report = {
        "complete": (any(e.get("kind") == "end" for e in events)
                     and any(line.startswith("REPLAY_FINAL ") for line in log_lines)),
        "errors": [line for line in log_lines if line.startswith(("REPLAY_ERROR", "SCRIPT ERROR"))],
        "error_groups": execution_error_groups(project, log_lines),
        "ops": read_json(ops, []),
        "watches": read_json(project / "gb_playtest_trace.json", {}),
        "events": [e for e in events if e.get("kind") == "change"],
        "operations": operations,
        "series": summaries,
        "sources": sources,
    }
    current = out / "trace.json"
    previous = read_json(current, {})
    report["operation_comparison"] = compare_operations(report, previous)
    if previous:
        (out / "trace.previous.json").write_text(json.dumps(previous, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    current.write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    text = render_report(report, previous)
    (out / "trace.md").write_text(text, encoding="utf-8")
    print(f"PLAYTEST_TRACE_REPORT series={len(summaries)} complete={report['complete']} path={out / 'trace.md'}")
    first = report["operation_comparison"]["first_difference"]
    if first:
        print(f"PLAYTEST_FIRST_DIFFERENCE phase={first['phase']} op_index={first['op_index']} "
              f"tape_frames={first['first_tape_frame']}..{first['last_tape_frame']}")
    for row in sorted(summaries, key=lambda r: bool(r["changes"]), reverse=True)[:8]:
        print(f"  {row['script']} {row['node']} {row['property']}: {row['first']!r} -> {row['last']!r}; changes={row['changes']}")
    return report


def render_report(report: dict, previous: dict) -> str:
    lines = ["# Runtime feedback", "",
             "Read the actual state changes below, then open the referenced source and the recorded images.",
             "Source references show uses of the observed property; they do not prove which statement ran.",
             "Frames are observer physics ticks. Held actions and just-pressed/released edges are sampled at physics priority 1000.",
             "A one-frame tap may register its press edge after the action is no longer held.",
             "A missing change alone does not establish a defect; inspect the triggering interaction.", ""]
    if not report["complete"]:
        lines += ["**Replay trace did not finish. Use the partial events in trace.json and inspect replay.log.**", ""]
    if report.get("errors"):
        lines += ["## Execution errors", ""]
        for error in report["error_groups"]:
            lines += [f"### {error['message']} ({error['count']} occurrence(s))", ""]
            if error["stack"]:
                lines += ["```text", *error["stack"], "```", ""]
            for loc in error["locations"]:
                code = f": `{loc['code']}`" if "code" in loc else ""
                lines.append(f"- `{loc['script']}:{loc['line']}`{code}")
            lines.append("")
    if previous:
        same_inputs = ops_array(report["ops"]) == ops_array(previous.get("ops", []))
        lines += ["## Since the preceding playtest", "", f"Same replay inputs: **{same_inputs}**.",
                  "These are observed differences; changing the inputs or encounter state can also change the outcome.", ""]
        old = {(r["scene"], r["node"], r["property"]): r for r in previous.get("series", [])}
        differences = 0
        for row in report["series"]:
            prior = old.get((row["scene"], row["node"], row["property"]))
            if prior and any(row[k] != prior[k] for k in ("first", "last", "min", "max", "changes")):
                differences += 1
                ranges = (f"range [{prior['min']!r}, {prior['max']!r}] → [{row['min']!r}, {row['max']!r}]; "
                          if row["min"] is not None and prior["min"] is not None else "")
                lines.append(f"- `{row['node']}.{row['property']}`: last {prior['last']!r} → {row['last']!r}; "
                             f"{ranges}"
                             f"changes {prior['changes']} → {row['changes']}")
        if not differences:
            lines.append("No differences in the comparable state summaries.")
        lines += ["", "### Changes to observed scripts", ""]
        diffs = []
        for script, source in report["sources"].items():
            before = previous.get("sources", {}).get(script)
            if before is not None and before != source:
                diffs.extend(difflib.unified_diff(before.splitlines(), source.splitlines(), fromfile=script + " (previous)", tofile=script, n=2, lineterm=""))
        lines += ["```diff", *diffs[:220], "```"] if diffs else ["No changes in the scripts observed in both runs."]
        if len(diffs) > 220:
            lines.append("Diff excerpt truncated; full source snapshots are in trace.json and trace.previous.json.")
        lines.append("")
    lines += render_operations(report, previous)
    lines += ["## Observed state and source locations", ""]
    for row in sorted(report["series"], key=lambda r: bool(r["changes"]), reverse=True):
        numeric_range = f"min {row['min']!r}; max {row['max']!r}; " if row["min"] is not None else ""
        lines += [f"### {row['node']} · {row['property']}", "",
                  f"Scene `{row['scene']}`; script `{row['script']}`.",
                  f"First {row['first']!r}; last {row['last']!r}; {numeric_range}"
                  f"{row['changes']} changes, frames {row['first_frame']}–{row['last_frame']}."]
        changes = [e for e in report["events"] if all(e.get(k) == row.get(k) for k in ("scene", "node", "script", "property"))]
        for event in changes[:6]:
            inputs = f"held {event['actions']}"
            if "just_pressed" in event:
                inputs += f"; just pressed {event['just_pressed']}; just released {event['just_released']}"
            lines.append(f"- Frame {event['frame']}, {inputs}: {event['before']!r} → {event['after']!r}")
        if row["truncated"]:
            lines.append("Change samples were capped; the first/last/range/change count cover all observed ticks.")
        for ref in row["source_references"]:
            lines.append(f"- `{row['script']}:{ref['line']}` ({ref['function']}): `{ref['code']}`")
        lines.append("")
    lines += ["## Next edit", "",
              "Choose a visible or functional mismatch with the public task and reference video. Use the state and source references to locate its implementation.",
              "Start at the first changed operation when comparing a patch with the same input tape. Inspect its before state, the observed effects, the source and the game images; adjust the code or the route according to the public task.",
              "Make the smallest useful patch, replay, and inspect the changed state plus the game images. Keep changes that improve the game without breaking its working behavior.",
              "For a missing internal value, add a watch entry outside submission/ and run with PLAYTEST_WATCH=/absolute/path/watch.json.", ""]
    return "\n".join(lines)


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    sub = parser.add_subparsers(dest="command", required=True)
    prep = sub.add_parser("prepare")
    prep.add_argument("project", type=Path)
    prep.add_argument("--watch", type=Path)
    report = sub.add_parser("report")
    for name in ("project", "ops", "log", "out"):
        report.add_argument(name, type=Path)
    args = parser.parse_args()
    if args.command == "prepare":
        prepare(args.project, args.watch)
    else:
        build_report(args.project, args.ops, args.log, args.out)


if __name__ == "__main__":
    main()
