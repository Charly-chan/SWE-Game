from __future__ import annotations

from math import sqrt
from types import SimpleNamespace

import pytest

from evalsys.taskgen.scorecard import (
    MODE1_REDESIGN_REGISTRY_VERSION,
    MODE1_REDESIGN_WEIGHTS,
    score_task_result,
)
from evalsys.verdict import failed, inconclusive, passed, skipped, unobservable


REQUIRED_GROUPS = ["gb_player", "gb_collectible", "gb_hazard", "gb_goal"]


def _truth(player: int, collectible: int, hazard: int, goal: int) -> dict:
    return {
        "levels": [{
            "groups": {
                "gb_player": {"alive": player},
                "gb_collectible": {"alive": collectible},
                "gb_hazard": {"alive": hazard},
                "gb_goal": {"alive": goal},
            }
        }]
    }


def _result(*, qwen_like: bool) -> SimpleNamespace:
    mechanics_credit = 0.87891 if qwen_like else 0.77855
    o3_point = sqrt(mechanics_credit)
    channels = [
        {"channel": "O1", "name": "runnable", "lo": 1.0, "hi": 1.0,
         "coverage": 1.0, "denominator": 1.0, "by_verdict": {"passed": 1.0}},
        {"channel": "O3", "name": "mechanics", "lo": o3_point, "hi": o3_point,
         "coverage": 1.0, "denominator": 1.0, "by_verdict": {"passed": 1.0}},
        {"channel": "O6", "name": "assets", "lo": 0.0, "hi": 0.0,
         "coverage": 1.0, "denominator": 48.0, "by_verdict": {"failed": 48.0}},
    ]
    items = [
        passed("layout"), passed("interface"), passed("anti_grant_static"),
        passed("auto_win_ready"), passed("null_no_win"), passed("anti_grant_diff"),
        passed("verifier_profile_complete"),
        unobservable("extended_mash_no_win", detail="no extended inputs"),
        passed("gdd"), passed("authored_gdd_interface"), passed("authored_gdd_quality"),
        passed("brief_gdd_grounding"), passed("ops_present"),
        passed("reproduction", evidence={"card": {"channels": channels}}),
    ]
    if qwen_like:
        items.extend([
            failed("rubric_interface", evidence={
                "findings": [
                    "required group gb_hazard is absent",
                    "required group gb_goal is absent",
                ]
            }),
            passed("mechanic_trace", credit=1.0),
            passed("causal_witness", credit=1.0),
            failed("ops_valid"), skipped("ops_not_idle"),
            inconclusive("demonstrations_complete"),
        ])
        candidate = _truth(1, 6, 0, 0)
        demonstrations = None
    else:
        items.extend([
            passed("rubric_interface"),
            passed("mechanic_trace", credit=2 / 3),
            passed("causal_witness", credit=0.5),
            passed("ops_valid"), passed("ops_not_idle"),
            failed("demonstrations_complete"),
        ])
        candidate = _truth(1, 35, 15, 1)
        demonstrations = {"coverage": 2 / 3}
    return SimpleNamespace(
        package=SimpleNamespace(manifest={"mode": "brief", "game_id": "canopy_dash"}),
        items=items,
        resolved=False,
        mode1_context={
            "rubric": {
                "required_groups": REQUIRED_GROUPS,
                "required_numeric_slots": ["progress", "score"],
            },
            "candidate_truth": candidate,
            "reference_truth": _truth(1, 21, 5, 0),
            "demonstrations": demonstrations,
            "feature_demos": True,
        },
    )


def test_mode1_redesign_weights_are_fixed_at_100() -> None:
    assert sum(MODE1_REDESIGN_WEIGHTS.values()) == 100
    assert MODE1_REDESIGN_WEIGHTS["visual_placeholder"] == 15


@pytest.mark.parametrize(
    ("qwen_like", "expected"),
    [(True, 22.867), (False, 33.738)],
)
def test_canopy_pilot_anchor_scores(qwen_like: bool, expected: float) -> None:
    card = score_task_result(
        _result(qwen_like=qwen_like), MODE1_REDESIGN_REGISTRY_VERSION
    )
    assert card["weighted_total"]["score"] == pytest.approx(expected, abs=0.001)
    assert card["weighted_total"]["headline_ceiling"] == 90
    assert card["evaluation_status"] == "complete"
    assert card["strict"]["resolved"] is False
    assert card["ranking_eligible"] is False
    assert card["evaluation_incomplete"] is True
    assert card["weighted_total"]["status"] == "evaluation_incomplete"
    expected_missing = {"universal_mechanics", "task_checkpoints", "hidden_scenarios"}
    if not qwen_like:
        expected_missing.add("numeric_contract")
    assert {row["source"] for row in card["weighted_total"]["unmeasured"]} == expected_missing
    numeric_axis = next(axis for axis in card["axes"] if axis["id"] == "numeric_contract")
    if qwen_like:
        assert numeric_axis["status"] == "candidate_failure"
        assert numeric_axis["credit"] == 0.0
        assert "numeric_contract" not in expected_missing
        demo_axis = next(axis for axis in card["axes"] if axis["id"] == "demo_coverage")
        assert demo_axis["status"] == "candidate_failure"
        assert demo_axis["credit"] == 0
    visual = next(axis for axis in card["axes"] if axis["id"] == "visual_placeholder")
    assert visual["earned_points"] == 5
    assert card["visual_protocol"] == "uncalibrated fixed placeholder: 5 earned points of 15"
    evidence_axes = {
        axis["id"]: axis for axis in card["axes"]
        if axis["id"] in {"universal_mechanics", "task_checkpoints", "hidden_scenarios"}
    }
    assert len(evidence_axes) == 3
    assert all(axis["earned_points"] == 0 for axis in evidence_axes.values())

    assert card["weighted_total"]["currently_reachable_ceiling"] < 90


def test_mode1_mash_is_not_a_gate_without_extended_inputs() -> None:
    card = score_task_result(_result(qwen_like=True), MODE1_REDESIGN_REGISTRY_VERSION)
    mash = next(row for row in card["gates"] if row["id"] == "extended_mash_no_win")
    assert mash["status"] == "not_applicable"


def test_mode1_domain_local_failures_do_not_gate_unrelated_axes() -> None:
    result = _result(qwen_like=False)
    local = {"interface", "null_no_win", "anti_grant_diff", "verifier_profile_complete"}
    result.items = [failed(item.id, detail="local failure") if item.id in local else item
                    for item in result.items]
    result.items.append(failed("extended_mash_no_win", detail="local failure"))
    card = score_task_result(result, MODE1_REDESIGN_REGISTRY_VERSION)
    assert card["evaluation_status"] == "complete"
    assert all(row["scope"] == "domain_local" for row in card["gates"] if row["id"] in local)


def test_mode1_confirmed_integrity_failure_still_gates() -> None:
    result = _result(qwen_like=False)
    result.items.append(failed("no_eval_smuggling", detail="certificate planted"))
    card = score_task_result(result, MODE1_REDESIGN_REGISTRY_VERSION)
    assert card["evaluation_status"] == "gated"


def test_visual_failure_reason_does_not_leak_into_objective_axes() -> None:
    result = _result(qwen_like=True)
    result.items.append(inconclusive("task_visual", detail="VLM response semantic invalid"))
    card = score_task_result(result, MODE1_REDESIGN_REGISTRY_VERSION)
    missing = {row["source"]: row for row in card["weighted_total"]["unmeasured"]}
    assert missing["task_checkpoints"]["step"] != "VLM response semantic invalid"
    assert missing["hidden_scenarios"]["step"] != "VLM response semantic invalid"
