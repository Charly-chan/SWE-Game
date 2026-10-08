
import json

import pytest

from evalsys.taskgen.evaluate import _ops_items, _rubric_interface_item
from evalsys.taskgen.scorecard import score_task_result
from evalsys.taskgen.submission import load_submission
from evalsys.verdict import failed, inconclusive, passed
from test_mode1_redesign_scorecard import _result as brief_result
from test_progressive_redesign_scorecard import _axis
from test_redesign_evidence_regressions import _complete_result
from test_taskgen_mode34b import _fixture


def result_for(mode):
    result = (brief_result(qwen_like=False) if mode == "brief" else
              _complete_result() if mode == "gdd" else
              _fixture("skeleton_noop_canopy_dash"))
    context = getattr(result, "mode1_context", None) or {}
    context.setdefault("rubric", {})["mechanic_checks"] = [{
        "id": "pickup", "observable": {
            "kind": "trace_checkpoint", "predicate": "count_delta(gb_collectible) < 0",
        },
    }]
    result.mode1_context = context
    return result


def replace_items(result, replacements):
    names = {item.id for item in replacements}
    result.items = [item for item in result.items if item.id not in names] + replacements


@pytest.mark.parametrize("mode", ["brief", "gdd", "skeleton"])
def test_rejected_feature_manifest_is_failed_demo_coverage_not_missing_probe(tmp_path, mode):
    (tmp_path / "project.godot").write_text("config_version=5\n")
    (tmp_path / "demos.json").write_text(json.dumps({"schema_version": 1, "demos": [{
        "id": "pickup", "description": "Collect the item",
        "ops": [{"op": "hold", "action": "undeclared_action", "frames": 10}],
    }]}))
    submission = load_submission(tmp_path)
    assert submission.demos_error and not submission.demos
    result = result_for(mode)
    replace_items(result, _ops_items(submission))
    result.mode1_context.update(feature_demos=True, demonstrations=None)
    card = score_task_result(result)
    for name in ("demo_validity", "demo_coverage"):
        axis = _axis(card, name)
        assert axis["credit"] == 0
        assert axis["status"] == "candidate_failure"
        assert any(row.get("axis") == name for row in card["candidate_failures"])
        assert not any(row.get("axis") == name for row in card["evaluator_failures"])
    assert _axis(card, "demo_coverage")["evidence"]["expected"] == ["pickup"]

    assert _axis(card, "task_visual")["credit"] is None
    assert not card["ranking_eligible"]


@pytest.mark.parametrize("mode", ["brief", "gdd", "skeleton"])
@pytest.mark.parametrize("input_measured", [True, False])
def test_legacy_demo_without_coverage_measurement_stays_unmeasured(mode, input_measured):
    result = result_for(mode)
    result.mode1_context.update(feature_demos=False, demonstrations=None)
    replace_items(result, [
        passed("ops_present"), passed("ops_not_idle"),
        passed("ops_valid") if input_measured else inconclusive("ops_valid", detail="input check unavailable"),
        inconclusive("mechanic_trace", detail="engine replay did not run"),
    ])
    card = score_task_result(result)
    assert _axis(card, "demo_coverage")["status"] == "not_instrumented_zero"
    assert "demo_coverage" in {row["source"] for row in card["weighted_total"]["unmeasured"]}
    assert any(row.get("axis") == "demo_coverage" for row in card["evaluator_failures"])
    assert not card["ranking_eligible"]


@pytest.mark.parametrize("credit", [0.0, 0.5, 1.0])
def test_valid_legacy_demo_retains_its_measured_coverage(credit):
    result = result_for("gdd")
    result.mode1_context.update(feature_demos=False, demonstrations=None)
    replace_items(result, [passed(name) for name in ("ops_present", "ops_valid", "ops_not_idle")]
                  + [passed("mechanic_trace", credit=credit)])
    axis = _axis(score_task_result(result), "demo_coverage")
    assert axis["credit"] == credit
    assert axis["status"] == "measured"


def test_rejected_demo_keeps_gdd_requirement_owner_and_failed_status():
    result = result_for("gdd")
    result.mode1_context.update(feature_demos=True, demonstrations=None)
    replace_items(result, [
        failed("ops_valid", detail="manifest refused"),
        passed("gdd_mechanics_observable", evidence={"observed": ["mechanic:pickup"]}),
    ])
    card = score_task_result(result)
    requirement = card["gdd_requirement_coverage"][0]
    assert requirement["primary_axis"] == "demo_coverage"
    assert requirement["status"] == "fail"
    alignment = _axis(card, "gdd_requirement_alignment")
    assert alignment["evidence"]["coverage"]["atoms_orphaned"] == 0
    assert alignment["earned_points"] == alignment["weight_in_total"] == 0


@pytest.mark.parametrize("mode", ["brief", "gdd"])
def test_unapproved_rubric_cannot_award_full_interface_credit(mode):
    result = result_for(mode)
    oracle = {"rubric": result.mode1_context["rubric"], "rubric_audit": {"ready": False}}
    item = _rubric_interface_item(None, oracle, mode=mode)
    replace_items(result, [item])
    card = score_task_result(result)
    axis = _axis(card, "rubric_interface")
    assert axis["earned_points"] == 0
    assert axis["status"] == "not_instrumented_zero"
    assert any(row.get("axis") == "rubric_interface" for row in card["evaluator_failures"])
    assert not card["ranking_eligible"]


def test_brief_without_numeric_requirements_is_not_a_candidate_numeric_failure():
    result = brief_result(qwen_like=True)
    result.mode1_context["rubric"]["required_numeric_slots"] = []
    card = score_task_result(result)
    assert _axis(card, "numeric_contract")["status"] == "empty_denominator"
    assert not any(row.get("axis") == "numeric_contract" for row in card["candidate_failures"])


def test_retry_plan_keeps_independent_objective_and_visual_gaps():
    result = brief_result(qwen_like=False)
    replace_items(result, [
        inconclusive("authored_gdd_quality", detail="GDD audit unavailable"),
        inconclusive("task_visual", detail="visual judge response unavailable"),
    ])
    card = score_task_result(result)
    assert {"objective", "visual"} <= {row["domain"] for row in card["evaluator_failures"]}
    assert {"objective", "visual"} <= {row["domain"] for row in card["retry_plan"]}
    assert card["weighted_total"]["score"] is None


def test_existing_visual_retry_is_not_duplicated_when_objective_evidence_is_missing():
    result = brief_result(qwen_like=False)
    replace_items(result, [inconclusive("task_visual", evidence={"evaluator_failures": [{
        "domain": "visual", "failure_code": "judge_response_missing", "retryable": True,
    }]})])
    card = score_task_result(result)
    visual_retries = [row for row in card["retry_plan"] if row["domain"] == "visual"]
    assert len(visual_retries) == 1
    assert visual_retries[0]["failure_code"] == "judge_response_missing"
