

from types import SimpleNamespace
from unittest import mock

from evalsys.taskgen.evaluate import (
    REPORT_SCHEMA,
    _reported_item,
    _reporting_summary,
    _root_cause_metadata,
    render_report,
)
from evalsys.verdict import failed


def test_script_error_clusters_strict_failures_without_changing_items() -> None:
    error = "SCRIPT ERROR: Invalid access to property 'hit'"
    trace = failed("mechanic_trace", evidence={"errors": [error]})
    witness = failed("causal_witness", evidence={"witness": {"errors": [error]}})

    trace_cause = _root_cause_metadata(trace)
    witness_cause = _root_cause_metadata(witness)
    assert trace_cause == witness_cause
    assert trace_cause["cause_class"] == "submission_runtime"
    assert _reported_item(trace)["root_cause"]["cluster_id"].startswith("rc-")
    assert "root_cause" not in trace.evidence

    reporting = _reporting_summary(
        [trace, witness],
        strict_ids=frozenset({"mechanic_trace", "causal_witness"}),
        resolved=False,
    )
    assert reporting["resolved"] is False
    assert len(reporting["root_cause_clusters"]) == 1
    assert reporting["root_cause_clusters"][0]["item_ids"] == [
        "mechanic_trace",
        "causal_witness",
    ]
    assert {
        row["id"] for row in reporting["failing_strict_items"]
    } == {"mechanic_trace", "causal_witness"}


def test_report_heading_names_registry_and_prints_headline_ceiling() -> None:
    interval = SimpleNamespace(lo=1.0, hi=1.0, coverage=1.0, denominator=1.0)
    result = SimpleNamespace(
        interval=interval,
        comparable=True,
        resolved=True,
        eligibility_status="passed",
        package=SimpleNamespace(manifest={"mode": "gdd", "game_id": "fixture"}),
        eligibility_items=[],
        behavior_items=[],
        fidelity_items=[],
        limits=(),
    )
    card = {
        "registry_version": "2026-09-04.calib3",
        "weighted_total": {"score": 84.5, "headline_ceiling": 85.0},
        "measured_weight_share": 1.0,
        "ranking_eligible": True,
        "categories": [],
    }
    with mock.patch("evalsys.taskgen.evaluate.score_task_result", return_value=card):
        report = render_report(result)

    assert REPORT_SCHEMA == "gamebench.taskgen.report.v4"
    assert "Hierarchical scorecard (2026-09-04.calib3)" in report
    assert "headline_ceiling=85.000" in report
    assert "pilot weights" not in report


def test_display_terms_do_not_change_stored_scorecard_reasons() -> None:
    from copy import deepcopy

    result = SimpleNamespace(
        interval=SimpleNamespace(lo=1, hi=1, coverage=1, denominator=1),
        comparable=True, resolved=True, eligibility_status="passed",
        package=SimpleNamespace(manifest={"mode": "gdd", "game_id": "fixture"}),
        eligibility_items=[], behavior_items=[], fidelity_items=[], limits=(),
    )
    card = {
        "registry_version": "2026-09-10.mode34b",
        "weighted_total": {"score": 85, "headline_ceiling": 85},
        "measured_weight_share": 1, "ranking_eligible": True, "categories": [],
        "not_applicable_criteria": ["visual_experience/calibrated_surface"],
        "not_applicable_reasons": {
            "visual_experience/calibrated_surface": "S-card is uncalibrated; O-card retained",
        },
    }
    before = deepcopy(card)
    with mock.patch("evalsys.taskgen.evaluate.score_task_result", return_value=card):
        report = render_report(result)
    assert "Objective Behavioral Evaluation" in report
    assert "Perceptual Quality Assessment" in report
    assert "S-card" not in report and "O-card" not in report
    assert "weighted_total=85.000" in report
    assert card == before


def test_a_zero_weight_category_renders_without_inventing_a_coverage_share() -> None:


    result = SimpleNamespace(
        interval=SimpleNamespace(lo=1.0, hi=1.0, coverage=1.0, denominator=1.0),
        comparable=True,
        resolved=True,
        eligibility_status="passed",
        package=SimpleNamespace(manifest={"mode": "gdd", "game_id": "fixture"}),
        eligibility_items=[],
        behavior_items=[],
        fidelity_items=[],
        limits=(),
    )
    card = {
        "registry_version": "2026-09-15.mode2-redesign1",
        "weighted_total": {"score": 43.115, "headline_ceiling": 90.0},
        "measured_weight_share": 0.85,
        "ranking_eligible": True,
        "categories": [
            {
                "id": "mechanics_requirements",
                "name": "Mechanics and frozen-GDD fulfillment",
                "weight_in_total": 27.5,
                "reported_only": False,
                "score": {"score": 40.0},
                "measured_weight_share": 1.0,
            },
            {
                "id": "gdd_alignment",
                "name": "Non-duplicated frozen-GDD alignment",
                "weight_in_total": 0.0,
                "reported_only": True,
                "score": {"score": None},
                "measured_weight_share": None,
            },
        ],
    }
    with mock.patch("evalsys.taskgen.evaluate.score_task_result", return_value=card):
        report = render_report(result)

    row = next(line for line in report.splitlines()
               if line.startswith("| Non-duplicated frozen-GDD alignment"))
    assert row.endswith("| - |"), row
    assert "0.000" not in row, row

    other = next(line for line in report.splitlines()
                 if line.startswith("| Mechanics and frozen-GDD fulfillment"))
    assert other.endswith("| 1.000 |"), other
