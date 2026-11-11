
import pytest

from evalsys.taskgen.scorecard import MODE2_REDESIGN_REGISTRY_VERSION, score_task_result
from test_progressive_redesign_scorecard import _axis, _gdd_with_atoms


def result_with_demos(*, measured=True, coverage=0.0, observed=()):
    result = _gdd_with_atoms(observed=["mechanic:pickup"], missing=[])
    result.mode1_context.update(feature_demos=True, demonstrations={
        "expected": ["pickup"], "observed": list(observed),
        "measured": measured, "coverage": coverage,
        "segments": [{"segment_clean": False, "observed": ["pickup"]}],
    })
    return result


@pytest.mark.parametrize("coverage,observed,status", [
    (0.0, (), "fail"), (1.0, ("pickup",), "pass"),
])
def test_mechanic_owned_by_demo_coverage_is_not_charged_twice(coverage, observed, status):
    card = score_task_result(result_with_demos(coverage=coverage, observed=observed),
                             MODE2_REDESIGN_REGISTRY_VERSION)
    alignment = _axis(card, "gdd_requirement_alignment")
    assert alignment["evidence"]["coverage"]["atoms_orphaned"] == 0
    assert alignment["earned_points"] == alignment["weight_in_total"] == 0
    assert _axis(card, "demo_coverage")["credit"] == coverage
    row = next(r for r in card["gdd_requirement_coverage"] if r["requirement_id"] == "mechanic:pickup")
    assert row["primary_axis"] == "demo_coverage"
    assert row["declared_axis"] == "task_checkpoints"

    assert row["status"] == status


def test_unmeasured_demo_stays_unmeasured_even_when_coverage_field_is_zero():
    card = score_task_result(result_with_demos(measured=False), MODE2_REDESIGN_REGISTRY_VERSION)


    assert _axis(card, "demo_coverage")["status"] == "not_instrumented_zero"
    assert "demo_coverage" in {row["source"] for row in card["weighted_total"]["unmeasured"]}
    assert card["evaluation_incomplete"]
    assert not card["ranking_eligible"]
    assert card["gdd_requirement_coverage"][0]["status"] == "unverified"


def test_unrelated_demo_denominator_does_not_hide_an_orphan():
    result = result_with_demos()
    result.mode1_context["demonstrations"]["expected"] = ["another_feature"]
    card = score_task_result(result, MODE2_REDESIGN_REGISTRY_VERSION)
    assert _axis(card, "gdd_requirement_alignment")["evidence"]["coverage"]["atoms_orphaned"] == 1
    assert not card["ranking_eligible"]
