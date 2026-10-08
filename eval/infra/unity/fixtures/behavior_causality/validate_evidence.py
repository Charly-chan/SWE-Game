#!/usr/bin/env python3


from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path


REPOSITORY_ROOT = Path(__file__).resolve().parents[5]
sys.path.insert(0, str(REPOSITORY_ROOT / "eval" / "evalsys"))

from evalsys.routes.schema import Route
from evalsys.taskgen.unity.unity_semantic import (
    reading_from_unity_report,
    validate_unity_semantic_report,
)


SESSIONS = (
    "positive_01", "positive_02", "positive_03", "positive_04", "positive_05",
    "no_fire_control", "auto_win", "input_ignored", "enemy_auto_disappears",
    "telemetry_fake", "witness_candidate", "witness_hidden", "visual_only",
)


def _load(path: Path) -> dict[str, object]:
    value = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(value, dict):
        raise RuntimeError(f"expected a JSON object: {path}")
    return value


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("evidence", type=Path)
    args = parser.parse_args()

    route = Route.from_dict(
        {
            "route_id": "infra/behavior_causality/first_causal_event",
            "tier": 2,
            "goal": {
                "predicate": "whole_game_clear()",
                "milestones": [
                    {
                        "name": "enemy_removed",
                        "predicate": "count_delta(gb_enemy) < 0",
                    },
                ],
                "observations": [
                    {"name": "projectile_contact", "predicate": "overlap(gb_projectile, gb_enemy)"},
                    {
                        "name": "score_increased",
                        "predicate": "numeric_delta(score) > 0",
                        "baseline": "previous_row",
                    }
                ],
            },
            "budget": {"steps": 1, "frames": 120},


            "required_devices": ["goal#0"],
        }
    )
    findings = route.validate()
    if findings:
        raise RuntimeError(f"calibration route is invalid: {findings}")

    readings: dict[str, dict[str, object]] = {}
    for session in SESSIONS:
        report = _load(args.evidence / f"cat-{session}.semantic-report.json")
        validate_unity_semantic_report(report)
        reading = reading_from_unity_report(route, report).to_dict()
        readings[session] = {
            "reached": reading["reached"],
            "used_required": reading["used_required"],
            "milestones_reached": reading["milestones_reached"],
            "observations_reached": reading["observations_reached"],
        }

    expected_vector = {
        "reached": True,
        "used_required": True,
        "milestones_reached": ["enemy_removed"],
        "observations_reached": ["projectile_contact", "score_increased"],
    }
    positives = [readings[f"positive_{index:02d}"] for index in range(1, 6)]
    if any(reading != expected_vector for reading in positives):
        raise RuntimeError(f"positive shared-reader vector mismatch: {positives}")
    if readings["no_fire_control"]["reached"] or readings["no_fire_control"]["used_required"]:
        raise RuntimeError("no-fire matched control incorrectly reached the goal or used fire")

    summary = _load(args.evidence / "cat-summary.json")
    expected = _load(Path(__file__).with_name("expected_outcomes.json"))["fixtures"]
    checks = summary.get("expected_outcomes")
    if not isinstance(checks, dict) or set(checks) != set(expected):
        raise RuntimeError("cat summary does not cover the frozen expected-outcome manifest")
    if checks["positive"].get("status") != "passed":
        raise RuntimeError("positive calibration did not pass")
    for fixture in set(expected) - {"positive"}:
        if checks[fixture].get("status") != "expected_failure_observed":
            raise RuntimeError(f"negative calibration did not fail as expected: {fixture}")
        if checks[fixture].get("observed_primary_item") != expected[fixture].get("primary_item"):
            raise RuntimeError(f"negative primary-item mismatch: {fixture}")
        if checks[fixture].get("resolved") is not False:
            raise RuntimeError(f"negative fixture was incorrectly resolved: {fixture}")

    print(
        json.dumps(
            {
                "schema": "gamebench.unity-behavior-causality-validation.v1",
                "status": "passed",
                "semantic_report_count": len(SESSIONS),
                "positive_repeat_count": len(positives),
                "shared_reader_strict_vector": expected_vector,
                "negative_fixture_count": len(expected) - 1,
            },
            sort_keys=True,
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
