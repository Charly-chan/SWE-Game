#!/usr/bin/env python3


from __future__ import annotations

import argparse
import csv
import json
from collections import defaultdict
from pathlib import Path
from statistics import mean
from typing import Any

FIELDS = [
    "game", "mode", "case_id", "harness", "model", "kit", "budget_s", "wall_s", "turns",
    "cost_usd", "tokens_in", "tokens_out", "weighted_total",
    "repair_correctness", "mode4_tier", "difficulty_evidence_points",
    "difficulty_evidence_max", "difficulty_calibrated",
    "resolution_status", "f2p_rate", "p2p_rate",
    "headline_ceiling", "resolved", "failed_required_items",
    "ranking_eligible", "ranking_note", *[f"O{i}" for i in range(1, 10)],
    "registry_version",
]


def load(path: Path) -> dict[str, Any]:
    if not path.is_file():
        return {}
    value = json.loads(path.read_text(encoding="utf-8"))
    return value if isinstance(value, dict) else {}


def cost_usd(value: Any) -> float | None:
    if isinstance(value, (int, float)):
        return float(value)
    if isinstance(value, dict):
        for key in ("usd", "cost_usd", "total_usd", "total"):
            if isinstance(value.get(key), (int, float)):
                return float(value[key])
    return None


def identity(
    cell: Path, report: dict[str, Any], request: dict[str, Any], state: dict[str, Any]
) -> tuple[str, str, str, str, str]:

    parts = cell.name.split("__", 4)
    named = parts + [""] * (5 - len(parts)) if len(parts) >= 4 else [""] * 5
    provider = request.get("provider") or {}
    return (
        str(report.get("game_id") or named[0]),
        str(report.get("mode") or named[1]),
        str(state.get("case_id") or named[4]),
        str(request.get("backend") or named[2]),
        str(provider.get("model") or named[3]),
    )


def ocard_points(card: dict[str, Any]) -> dict[str, Any]:
    points = {f"O{i}": None for i in range(1, 10)}
    for category in card.get("categories") or []:
        for criterion in category.get("criteria") or []:
            for source in criterion.get("sources") or []:
                key = str(source.get("id") or "")
                if source.get("kind") == "ocard" and key in points:
                    points[key] = source.get("point")
    return points


def blocked_row(cell: Path, state: dict[str, Any]) -> dict[str, Any]:

    request = load(cell / "agent" / "request.json")
    game, mode, case_id, harness, model = identity(cell, {}, request, state)
    row = {
        "game": game,
        "mode": mode,
        "case_id": case_id,
        "harness": harness,
        "model": model,
        "ranking_eligible": False,
        "ranking_note": f"package_blocked: {state.get('detail') or 'package blocked'}",
    }
    return {key: row.get(key) for key in FIELDS}


def row_for(cell: Path) -> dict[str, Any] | None:
    report = load(cell / "evaluation" / "report.json")
    state = load(cell / "state.json")
    if not report:
        if state.get("status") == "package_blocked":
            return blocked_row(cell, state)
        if state.get("status") != "submitted":
            return None
    request = load(cell / "agent" / "request.json")
    usage = load(cell / "agent" / "usage.json")
    manifest = load(cell / "package" / "manifest.json")
    game, mode, case_id, harness, model = identity(cell, report, request, state)
    card = report.get("scorecard") or {}
    weighted = card.get("weighted_total") or {}
    repair = card.get("repair_correctness") or {}
    difficulty = card.get("difficulty_evidence") or {}
    strict = card.get("strict") or {}
    totals = usage.get("totals") or {}
    durations = state.get("durations_s") or {}
    per_turn = usage.get("per_turn")
    row = {
        "game": game,
        "mode": mode,
        "case_id": case_id,
        "harness": harness,
        "model": model,
        "kit": manifest.get("playtest_kit", ""),

        "budget_s": request.get("timeout_s"),
        "wall_s": durations.get("agent"),
        "turns": len(per_turn) if isinstance(per_turn, list) else None,
        "cost_usd": cost_usd(usage.get("cost")),
        "tokens_in": totals.get("input_tokens"),
        "tokens_out": totals.get("output_tokens"),
        "weighted_total": weighted.get("score"),
        "repair_correctness": repair.get("score"),
        "mode4_tier": difficulty.get("tier"),
        "difficulty_evidence_points": difficulty.get("earned_evidence_points"),
        "difficulty_evidence_max": difficulty.get("max_evidence_points"),
        "difficulty_calibrated": difficulty.get("calibration_status") == "empirically_calibrated",
        "resolution_status": card.get("resolution_status"),
        "f2p_rate": card.get("f2p_rate"),
        "p2p_rate": card.get("p2p_rate"),
        "headline_ceiling": weighted.get("headline_ceiling"),
        "resolved": card.get("resolved", report.get("resolved")),
        "failed_required_items": strict.get("failed_required_items") or [],
        "ranking_eligible": card.get("ranking_eligible"),
        "ranking_note": card.get("ranking_note"),
        "registry_version": card.get("registry_version"),
    }
    row.update(ocard_points(card))
    if not report:
        row.update(ranking_eligible=False, ranking_note=state.get("detail") or "evaluation deferred (--eval off)")
    return {key: row.get(key) for key in FIELDS}


def csv_value(key: str, value: Any) -> Any:
    if key == "failed_required_items":
        return json.dumps(value, separators=(",", ":"))
    if isinstance(value, bool):
        return str(value).lower()
    return "" if value is None else value


def leaderboard(rows: list[dict[str, Any]]) -> str:
    grouped: dict[tuple[str, str, str], list[dict[str, Any]]] = defaultdict(list)
    for row in rows:
        grouped[(row["mode"], row["harness"], row["model"])].append(row)
    lines = [
        "# Leaderboard",
        "",
        "Scores are compared within mode only. Ranked means include cells that "
        "are both resolved and ranking-eligible.",
        "",
    ]
    for mode in sorted({key[0] for key in grouped}):
        lines += [
            f"## {mode}",
            "",
            "| Harness | Model | Cells | Ranked | Mean weighted total | Resolved rate |",
            "|---|---|---:|---:|---:|---:|",
        ]
        mode_rows = []
        for (group_mode, harness, model), group in grouped.items():
            if group_mode != mode:
                continue
            ranked = [
                row for row in group
                if row["resolved"] is True
                and row["ranking_eligible"] is True
                and isinstance(row["weighted_total"], (int, float))
            ]
            score = f"{mean(row['weighted_total'] for row in ranked):.3f}" if ranked else "—"
            resolved_rate = sum(row["resolved"] is True for row in group) / len(group)
            mode_rows.append((score == "—", -(float(score) if score != "—" else 0), harness, model, group, ranked, score, resolved_rate))
        for _, _, harness, model, group, ranked, score, resolved_rate in sorted(mode_rows):
            lines.append(
                f"| {harness} | {model} | {len(group)} | {len(ranked)} | "
                f"{score} | {resolved_rate:.1%} |"
            )
        lines.append("")
    return "\n".join(lines).rstrip() + "\n"


def summarize(root: Path) -> list[dict[str, Any]]:
    cells = root / "cells"
    rows = [
        row for cell in sorted(cells.iterdir() if cells.is_dir() else [])
        if cell.is_dir() and (row := row_for(cell)) is not None
    ]
    (root / "summary.json").write_text(
        json.dumps(
            {"schema": "gamebench.turnkey.summary.v1", "rows": rows},
            indent=2,
            ensure_ascii=False,
        ) + "\n",
        encoding="utf-8",
    )
    with (root / "summary.csv").open("w", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=FIELDS)
        writer.writeheader()
        for row in rows:
            writer.writerow({key: csv_value(key, row.get(key)) for key in FIELDS})
    (root / "leaderboard.md").write_text(leaderboard(rows), encoding="utf-8")
    return rows


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("run", type=Path, help="results/<run_id> directory")
    args = parser.parse_args()
    root = args.run.resolve()
    if not (root / "run.json").is_file():
        parser.error(f"run.json is missing: {root}")
    rows = summarize(root)
    print(f"summarized {len(rows)} cell(s) -> {root}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
