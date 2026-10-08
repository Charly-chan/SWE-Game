

from __future__ import annotations

import csv
import importlib.util
import json
from pathlib import Path


TOOL = Path(__file__).resolve().parents[2] / "tools" / "summarize_results.py"
SPEC = importlib.util.spec_from_file_location("summarize_results", TOOL)
assert SPEC and SPEC.loader
summarize_results = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(summarize_results)


def write_json(path: Path, value: object) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value), encoding="utf-8")


def test_fixture_report_becomes_contracted_rows(tmp_path: Path) -> None:
    write_json(tmp_path / "run.json", {"schema": "gamebench.turnkey.run.v1"})
    cell = tmp_path / "cells" / "fixture__gdd__codex__model-x"
    write_json(
        cell / "evaluation" / "report.json",
        {
            "game_id": "fixture",
            "mode": "gdd",
            "resolved": True,
            "scorecard": {
                "registry_version": "registry-fixture",
                "ranking_eligible": True,
                "ranking_note": "complete",
                "weighted_total": {"score": 72.5, "headline_ceiling": 85.0},
                "repair_correctness": {"score": 72.5},
                "difficulty_evidence": {
                    "tier": "medium", "earned_evidence_points": 1.45,
                    "max_evidence_points": 2.0,
                    "calibration_status": "author_provisional_not_ranking_eligible",
                },
                "resolution_status": "FULL",
                "f2p_rate": 100.0,
                "p2p_rate": 100.0,
                "resolved": True,
                "strict": {"failed_required_items": []},
                "categories": [
                    {
                        "criteria": [
                            {
                                "sources": [
                                    {"kind": "ocard", "id": "O1", "point": 80.0},
                                    {"kind": "item", "id": "layout", "point": 1.0},
                                    {"kind": "ocard", "id": "O9", "status": "unobservable"},
                                ]
                            }
                        ]
                    }
                ],
            },
        },
    )
    write_json(
        cell / "agent" / "request.json",
        {"backend": "codex", "provider": {"model": "model-x"}, "timeout_s": None},
    )
    write_json(
        cell / "agent" / "usage.json",
        {
            "cost": {"usd": 1.25},
            "per_turn": [{}, {}],
            "totals": {"input_tokens": 123, "output_tokens": 45},
        },
    )
    write_json(cell / "state.json", {"durations_s": {"agent": 9.5}})
    write_json(cell / "package" / "manifest.json", {"playtest_kit": "on"})

    rows = summarize_results.summarize(tmp_path)

    assert len(rows) == 1
    row = rows[0]
    assert list(row) == summarize_results.FIELDS
    assert row == {
        "game": "fixture",
        "mode": "gdd",
        "case_id": "",
        "harness": "codex",
        "model": "model-x",
        "kit": "on",
        "budget_s": None,
        "wall_s": 9.5,
        "turns": 2,
        "cost_usd": 1.25,
        "tokens_in": 123,
        "tokens_out": 45,
        "weighted_total": 72.5,
        "repair_correctness": 72.5,
        "mode4_tier": "medium",
        "difficulty_evidence_points": 1.45,
        "difficulty_evidence_max": 2.0,
        "difficulty_calibrated": False,
        "resolution_status": "FULL",
        "f2p_rate": 100.0,
        "p2p_rate": 100.0,
        "headline_ceiling": 85.0,
        "resolved": True,
        "failed_required_items": [],
        "ranking_eligible": True,
        "ranking_note": "complete",
        "O1": 80.0,
        "O2": None,
        "O3": None,
        "O4": None,
        "O5": None,
        "O6": None,
        "O7": None,
        "O8": None,
        "O9": None,
        "registry_version": "registry-fixture",
    }
    payload = json.loads((tmp_path / "summary.json").read_text(encoding="utf-8"))
    assert payload["rows"] == [row]
    with (tmp_path / "summary.csv").open(encoding="utf-8", newline="") as handle:
        csv_rows = list(csv.DictReader(handle))
    assert list(csv_rows[0]) == summarize_results.FIELDS
    assert csv_rows[0]["failed_required_items"] == "[]"
    assert csv_rows[0]["budget_s"] == ""
    assert csv_rows[0]["wall_s"] == "9.5"
    assert csv_rows[0]["O9"] == ""
    leaderboard = (tmp_path / "leaderboard.md").read_text(encoding="utf-8")
    assert "## gdd" in leaderboard
    assert "| codex | model-x | 1 | 1 | 72.500 | 100.0% |" in leaderboard


def test_explicit_budget_is_recorded(tmp_path: Path) -> None:
    write_json(tmp_path / "run.json", {"schema": "gamebench.turnkey.run.v1"})
    cell = tmp_path / "cells" / "fixture__gdd__codex__model-x"
    write_json(cell / "evaluation" / "report.json", {"game_id": "fixture", "mode": "gdd"})
    write_json(cell / "agent" / "request.json", {"backend": "codex", "timeout_s": 600})
    write_json(cell / "state.json", {"durations_s": {"agent": 512.0}})

    rows = summarize_results.summarize(tmp_path)

    assert rows[0]["budget_s"] == 600
    assert rows[0]["wall_s"] == 512.0


def test_bugfix_cell_name_carries_case_id(tmp_path: Path) -> None:
    write_json(tmp_path / "run.json", {"schema": "gamebench.turnkey.run.v1"})
    cell = tmp_path / "cells" / "fixture__bugfix__codex__model-x__fixture-case-v1"
    write_json(cell / "evaluation" / "report.json", {"game_id": "fixture", "mode": "bugfix"})

    rows = summarize_results.summarize(tmp_path)

    assert [(r["game"], r["mode"], r["case_id"], r["harness"], r["model"]) for r in rows] == [
        ("fixture", "bugfix", "fixture-case-v1", "codex", "model-x")
    ]


def test_package_blocked_cell_is_a_row_with_the_reason(tmp_path: Path) -> None:
    write_json(tmp_path / "run.json", {"schema": "gamebench.turnkey.run.v1"})
    blocked = tmp_path / "cells" / "fixture__brief__codex__model-x"
    write_json(
        blocked / "state.json",
        {"status": "package_blocked", "detail": "video larger than 536870912 bytes; attach out of band"},
    )
    write_json(blocked / "package" / "manifest.json", {"blockers": ["video larger than 536870912 bytes"]})

    write_json(tmp_path / "cells" / "other__brief__codex__model-x" / "state.json", {"status": "running"})

    rows = summarize_results.summarize(tmp_path)

    assert len(rows) == 1
    row = rows[0]
    assert list(row) == summarize_results.FIELDS
    assert (row["game"], row["mode"], row["case_id"], row["harness"], row["model"]) == (
        "fixture", "brief", "", "codex", "model-x"
    )
    assert row["ranking_eligible"] is False
    assert row["ranking_note"] == "package_blocked: video larger than 536870912 bytes; attach out of band"
    assert row["resolved"] is None and row["weighted_total"] is None
    with (tmp_path / "summary.csv").open(encoding="utf-8", newline="") as handle:
        csv_rows = list(csv.DictReader(handle))
    assert csv_rows[0]["ranking_note"].startswith("package_blocked: ")
    assert csv_rows[0]["ranking_eligible"] == "false"
    leaderboard = (tmp_path / "leaderboard.md").read_text(encoding="utf-8")
    assert "| codex | model-x | 1 | 0 | — | 0.0% |" in leaderboard
