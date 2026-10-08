
from types import SimpleNamespace

import pytest

from evalsys.taskgen.evaluate import (
    _aggregate_behavior_item,
    _attribute_mode5_items,
    _unity_fidelity_score_items,
)
from evalsys.taskgen.scorecard import (
    _MODE5_INFRASTRUCTURE_GATES, _MODE5_SUBMISSION_GATES, _MODE5_VALIDITY_GATES,
    _mode5_environment_rows,
    MODE5_MDVA_REGISTRY_VERSION, score_task_result,
)
from evalsys.taskgen.unity.unity_fidelity import _criteria
from evalsys.taskgen.unity.unity_probe import UnityProbeRun
from evalsys.verdict import Verdict, inconclusive, passed


def behavior_run(ident, missing=False):
    return UnityProbeRun(
        run_id=ident, kind="hidden_behavior", status="pass", detail="clean process",
        command=(), returncode=0, plan_path="", result_path="", log_path="",
        reading={"behavior_result": {
            "status": "fail" if missing else "pass", "goal_reached": not missing,
            "missing_observations": ["device:goal#0"] if missing else [],
        }},
    )


def score(*overrides):
    ids = set(_MODE5_INFRASTRUCTURE_GATES + _MODE5_SUBMISSION_GATES + _MODE5_VALIDITY_GATES)
    ids.update(("unity_mechanic_trace", "causal_witness", "unity_runtime_stability",
                "unity_hidden_behavior", "unity_structure_fidelity", "unity_vlm"))
    items = {ident: passed(ident) for ident in ids}
    items.update({item.id: item for item in overrides})
    return score_task_result(SimpleNamespace(
        package=SimpleNamespace(manifest={"mode": "port", "game_id": "fixture"}),
        items=list(items.values()), resolved=True,
    ), MODE5_MDVA_REGISTRY_VERSION)


@pytest.mark.parametrize("item_id", ["unity_hidden_behavior", "unity_source_behavior"])
def test_one_missing_scenario_withholds_score(item_id):
    item = _aggregate_behavior_item(item_id, [behavior_run("observed"), behavior_run("missing", True)])
    assert item.verdict == Verdict.UNOBSERVABLE
    assert item.credit == 0.0
    assert "missing" in item.detail
    assert item.evidence["statuses"] == {"observed": "pass", "missing": "unobservable"}

    hidden = _aggregate_behavior_item("unity_hidden_behavior", [behavior_run("observed"), behavior_run("missing", True)])
    card = score(item, hidden)
    assert card["weighted_total"]["score"] is None
    assert not card["ranking_eligible"]
    assert not card["functional_complete"]


@pytest.mark.parametrize("missing", ["asset_identity", "scene_progression"])
def test_each_missing_visual_criterion_withholds_its_group(missing):
    criteria = _criteria({"criteria": [
        {"id": ident, "verdict": "pass", "credit": 1.0,
         "rationale": "paired visible evidence", "reference_timestamps": [0.0],
         "candidate_timestamps": [0.0]}
        for ident in ("asset_identity", "scene_progression")
        if ident != missing
    ]}, [0.0], [0.0])
    items = _unity_fidelity_score_items({"criteria": [item.to_dict() for item in criteria]})
    incomplete = [item for item in items if item.verdict == Verdict.INCONCLUSIVE]
    assert len(incomplete) == 1
    assert missing in incomplete[0].detail
    assert incomplete[0].credit == 0.0
    card = score(*items)
    assert card["weighted_total"]["score"] is None
    assert not card["ranking_eligible"]
    assert card["diagnostics"]["unmeasured_weight"] == 15.0


def test_complete_evidence_still_allows_full_score():
    hidden = _aggregate_behavior_item("unity_hidden_behavior", [behavior_run("first"), behavior_run("second")])
    assert hidden.verdict == Verdict.PASSED
    card = score(hidden)
    assert card["objective_total"]["score"] == 70.0
    assert card["structure_vlm_total"]["score"] == 15.0
    assert card["mdva_vlm_total"]["score"] == 15.0
    assert card["vlm_total"]["score"] == 30.0
    assert card["weighted_total"]["score"] == 100.0
    assert card["ranking_eligible"]


def test_vlm_inconclusive_keeps_objective_score_readable():

    from evalsys.verdict import inconclusive
    card = score(inconclusive(
        "unity_vlm",
        detail="VLM provider unavailable; evaluator-owned capture retained",
        evidence={"owner": "harness", "retryable": True},
    ))
    assert card["objective_total"]["score"] == 70.0
    assert card["objective_total"]["status"] == "complete"
    assert card["structure_vlm_total"]["score"] == 15.0
    assert card["mdva_vlm_total"]["score"] is None
    assert card["vlm_total"]["score"] is None
    assert card["vlm_total"]["status"] == "retry_required"
    assert card["weighted_total"]["score"] is None
    assert card["ranking_eligible"] is False
    assert card["gates"]["vlm"]["status"] == "inconclusive"


def test_candidate_build_failure_converts_blocked_runtime_reads_to_zeroes():

    from evalsys.verdict import Attribution, failed

    items = _attribute_mode5_items([
        failed("unity_build"),
        inconclusive("unity_mechanic_trace", detail="Unity build did not produce a runnable player"),
        inconclusive("unity_runtime_stability", detail="Unity build did not produce a runnable player"),
        inconclusive("unity_structure_fidelity", detail="Unity build did not produce a runnable player"),
    ])
    by_id = {item.id: item for item in items}
    for item_id in ("unity_mechanic_trace", "unity_runtime_stability", "unity_structure_fidelity"):
        assert by_id[item_id].verdict == Verdict.FAILED
        assert by_id[item_id].credit == 0.0
        assert by_id[item_id].attribution is Attribution.SUBMISSION


_UNCERTIFIED = {
    "score_eligible": False,
    "certified": False,
    "certification_status": "pending",
    "environment_class": "local-wsl-dev",
    "profile_id": "local-wsl-dev",
    "detail": "WSL2 is development-smoke only and never score eligible",
}
_CERTIFIED = {
    "score_eligible": True,
    "certified": True,
    "certification_status": "certified",
    "environment_class": "linux-vm-certified",
    "profile_id": "ubuntu2404-unity6000.3.23f1",
    "detail": "certified disposable VM",
}


def _on_host(environment, *overrides):

    from evalsys.verdict import failed, inconclusive
    ids = set(_MODE5_INFRASTRUCTURE_GATES + _MODE5_SUBMISSION_GATES + _MODE5_VALIDITY_GATES)
    ids.update(("unity_mechanic_trace", "causal_witness", "unity_runtime_stability",
                "unity_hidden_behavior", "unity_structure_fidelity", "unity_vlm"))
    items = {ident: passed(ident) for ident in ids}

    items["unity_build"] = (
        passed("unity_build", evidence={"environment": environment})
        if environment["score_eligible"] else
        inconclusive(
            "unity_build", detail=environment["detail"],
            evidence={"environment": environment, "attribution": "infrastructure"},
        )
    )
    items.update({item.id: item for item in overrides})
    return score_task_result(SimpleNamespace(
        package=SimpleNamespace(manifest={"mode": "port", "game_id": "fixture"}),
        items=list(items.values()), resolved=True,
    ), MODE5_MDVA_REGISTRY_VERSION)


def test_uncertified_host_is_an_infrastructure_gap_not_a_candidate_zero():


    from evalsys.verdict import failed
    card = _on_host(
        _UNCERTIFIED,
        failed("ops_present", detail="ops.json is missing"),
        failed("build_recipe", detail="BUILD.md is missing"),
    )
    assert card["gates"]["infrastructure"]["status"] == "incomplete"
    assert "unity_environment_score_eligible" in card["gates"]["infrastructure"]["unmeasured_items"]
    assert card["outcome_status"] == "infrastructure_inconclusive"
    assert card["weighted_total"]["score"] is None
    assert not card["ranking_eligible"]


def test_certified_host_still_books_a_candidate_zero():


    from evalsys.verdict import failed
    card = _on_host(
        _CERTIFIED,
        failed("ops_present", detail="ops.json is missing"),
        failed("build_recipe", detail="BUILD.md is missing"),
    )
    assert card["gates"]["infrastructure"]["status"] == "passed"
    assert card["outcome_status"] == "candidate_delivery_failure"
    assert card["weighted_total"]["score"] == 0.0
    assert card["ranking_eligible"]

    assert card["diagnostics"]["unmeasured_weight"] == 0.0
    assert card["ranking_note"] == ""


def test_candidate_failure_does_not_short_circuit_unmeasured_capability_weight():


    from evalsys.verdict import failed, inconclusive
    card = _on_host(
        _CERTIFIED,
        failed("ops_present", detail="ops.json is missing"),
        inconclusive("unity_runtime_stability", detail="no cold run produced a reading"),
    )
    assert card["outcome_status"] == "evaluation_incomplete"
    assert card["weighted_total"]["score"] is None
    assert card["weighted_total"]["status"] == "evaluation_incomplete"
    assert not card["ranking_eligible"]
    assert card["diagnostics"]["unmeasured_weight"] > 0.0
    assert "Do not rank" in card["ranking_note"]


def test_a_card_with_no_environment_evidence_is_unchanged():


    card = score()
    assert card["gates"]["infrastructure"]["status"] == "passed"
    assert all(
        row["id"] != "unity_environment_score_eligible"
        for row in card["gates"]["infrastructure"]["items"]
    )


def test_environment_profiles_are_deduplicated_and_conflicts_are_inconclusive():
    certified = {"environment": {"profile_id": "vm", "score_eligible": True, "certified": True}}
    uncertified = {"environment": {"profile_id": "wsl", "score_eligible": False, "certified": False}}
    rows = _mode5_environment_rows({
        "unity_build": passed("unity_build", evidence=certified),
        "unity_probe": inconclusive("unity_probe", evidence=uncertified),
    })
    assert len(rows) == 1
    assert rows[0]["status"] == "inconclusive"
    assert len(rows[0]["profiles"]) == 2
    assert rows[0]["attribution"] == "harness"


def test_mode5_item_attribution_is_promoted_from_evidence():
    from evalsys.verdict import Attribution, failed, inconclusive
    items = _attribute_mode5_items([
        inconclusive(
            "unity_build",
            evidence={"attribution": "infrastructure", "environment": _UNCERTIFIED},
        ),
        failed(
            "unity_source_behavior",
            evidence={"attributions": {"c1": "candidate_failed"}},
        ),
    ])
    assert items[0].attribution is Attribution.HARNESS
    assert items[1].attribution is Attribution.SUBMISSION


def test_mode5_configuration_unobservable_is_harness_owned():
    from evalsys.verdict import Attribution, unobservable
    items = _attribute_mode5_items([
        unobservable(
            "unity_vlm",
            detail="Unity visual judging was disabled by evaluator configuration",
        ),
        unobservable(
            "extended_mash_no_win",
            detail="Unity manifest v1 declares no task-specific extra actions to mash",
        ),
    ])
    assert items[0].attribution is Attribution.HARNESS
    assert items[1].attribution is Attribution.UNATTRIBUTABLE


def test_a_submission_build_failure_owns_the_silence_it_causes():


    from evalsys.verdict import Attribution, failed, inconclusive
    reason = "Unity batch build exited with code 1"
    items = _attribute_mode5_items([
        failed("unity_build", detail=reason,
               evidence={"attribution": "submission"}),
        inconclusive("unity_probe", detail=reason),
        inconclusive("unity_mechanic_trace", detail=reason),
        inconclusive("causal_witness", detail=reason),
        inconclusive("null_no_win", detail=reason),
    ])
    by_id = {item.id: item for item in items}
    assert by_id["unity_build"].attribution is Attribution.SUBMISSION
    for dependent in ("unity_probe", "unity_mechanic_trace",
                      "causal_witness", "null_no_win"):
        assert by_id[dependent].attribution is Attribution.SUBMISSION, dependent
        assert by_id[dependent].evidence["dependency_root"] == "unity_build"


def test_an_evaluator_side_gap_stays_ours_even_behind_a_candidate_failure():

    from evalsys.verdict import Attribution, failed, inconclusive
    items = _attribute_mode5_items([
        failed("unity_probe", detail="input dispatch failed",
               evidence={"attribution": "submission"}),
        inconclusive("unity_hidden_behavior",
                     detail="package has no runnable hidden behavior scenarios"),
        inconclusive("unity_counterfactual",
                     detail="package has no runnable action counterfactuals"),
        inconclusive("unity_mechanic_trace",
                     detail="Unity witness produced no complete semantic row stream"),
    ])
    by_id = {item.id: item for item in items}
    assert by_id["unity_hidden_behavior"].attribution is Attribution.HARNESS
    assert "dependency_root" not in by_id["unity_hidden_behavior"].evidence
    assert by_id["unity_counterfactual"].attribution is Attribution.HARNESS

    assert by_id["unity_mechanic_trace"].attribution is Attribution.SUBMISSION


def test_an_infrastructure_root_failure_does_not_cascade_to_the_submission():

    from evalsys.verdict import Attribution, failed, inconclusive
    reason = "evaluator host is not score eligible"
    items = _attribute_mode5_items([
        failed("unity_build", detail=reason,
               evidence={"attribution": "infrastructure"}),
        inconclusive("unity_probe", detail=reason),
        inconclusive("unity_runtime_stability", detail=reason),
    ])
    by_id = {item.id: item for item in items}
    assert by_id["unity_build"].attribution is Attribution.HARNESS
    assert by_id["unity_probe"].attribution is Attribution.HARNESS
    assert by_id["unity_runtime_stability"].attribution is Attribution.HARNESS
    assert all("dependency_root" not in item.evidence for item in items)


def _delivery_zero_card(*overrides, registry=None):

    from evalsys.taskgen.scorecard import MODE5_DELIVERY_ZERO_REGISTRY_VERSION
    ids = set(_MODE5_INFRASTRUCTURE_GATES + _MODE5_SUBMISSION_GATES + _MODE5_VALIDITY_GATES)
    ids.update(("unity_mechanic_trace", "causal_witness", "unity_runtime_stability",
                "unity_hidden_behavior", "unity_structure_fidelity", "unity_visual_fidelity"))
    items = {ident: passed(ident) for ident in ids}
    items.update({item.id: item for item in overrides})
    return score_task_result(SimpleNamespace(
        package=SimpleNamespace(manifest={"mode": "port", "game_id": "fixture"}),
        items=list(items.values()), resolved=False,
    ), registry or MODE5_DELIVERY_ZERO_REGISTRY_VERSION)


def test_a_project_that_will_not_build_scores_a_ranked_zero():


    from evalsys.verdict import Attribution, failed, inconclusive
    reason = "Unity batch build exited with code 1"
    card = _delivery_zero_card(
        failed("unity_build", detail=reason, attribution=Attribution.SUBMISSION),
        inconclusive("unity_mechanic_trace", detail=reason,
                     attribution=Attribution.SUBMISSION),
        inconclusive("causal_witness", detail=reason,
                     attribution=Attribution.SUBMISSION),
        inconclusive("unity_runtime_stability", detail=reason,
                     attribution=Attribution.SUBMISSION),
        inconclusive("unity_hidden_behavior", detail=reason,
                     attribution=Attribution.SUBMISSION),
    )
    assert card["outcome_status"] == "candidate_delivery_failure"
    assert card["weighted_total"]["score"] == 0.0
    assert card["evaluation_incomplete"] is False


def test_an_evaluator_hole_still_withholds_the_headline():

    from evalsys.verdict import Attribution, failed, inconclusive
    card = _delivery_zero_card(
        failed("unity_build", detail="Unity batch build exited with code 1",
               attribution=Attribution.SUBMISSION),

        inconclusive("unity_hidden_behavior",
                     detail="package has no runnable hidden behavior scenarios",
                     attribution=Attribution.HARNESS),
    )
    assert card["outcome_status"] == "evaluation_incomplete"
    assert card["weighted_total"]["score"] is None


def test_the_old_mode5_row_keeps_withholding_the_headline():

    from evalsys.taskgen.scorecard import MODE5_REGISTRY_VERSION
    from evalsys.verdict import Attribution, failed, inconclusive
    reason = "Unity batch build exited with code 1"
    card = _delivery_zero_card(
        failed("unity_build", detail=reason, attribution=Attribution.SUBMISSION),
        inconclusive("unity_mechanic_trace", detail=reason,
                     attribution=Attribution.SUBMISSION),
        registry=MODE5_REGISTRY_VERSION,
    )
    assert card["weighted_total"]["score"] is None
    assert card["evaluation_incomplete"] is True


def test_delivery_zero_uses_evidence_attribution_and_publishes_its_rule():

    from evalsys.taskgen.scorecard import (
        MODE5_DELIVERY_ZERO_REGISTRY_VERSION,
        headline_rule,
    )
    from evalsys.verdict import Attribution, failed, inconclusive
    reason = "controller protocol failed: action has no enabled ButtonControl"
    card = _delivery_zero_card(
        failed("unity_probe", detail=reason,
               evidence={"attribution": Attribution.SUBMISSION.value}),
        inconclusive("unity_mechanic_trace", detail=reason,
                     evidence={"attribution": Attribution.SUBMISSION.value}),
        inconclusive("causal_witness", detail=reason,
                     evidence={"attribution": Attribution.SUBMISSION.value}),
    )
    assert card["outcome_status"] == "candidate_delivery_failure"
    assert card["weighted_total"]["rule"] == headline_rule(
        MODE5_DELIVERY_ZERO_REGISTRY_VERSION
    )


def test_a_refused_ops_tape_costs_the_witness_not_the_whole_port():


    from evalsys.verdict import Attribution, failed, skipped
    card = _delivery_zero_card(
        failed("ops_valid", detail="ops refused: unknown_action: ['toggle_flaps']",
               attribution=Attribution.SUBMISSION),
        skipped("ops_not_idle", detail="ops were refused"),
        skipped("causal_witness", detail="ops were refused"),
    )
    assert card["gates"]["submission"]["status"] == "passed"
    assert card["gates"]["deliverables"]["status"] == "failed"
    assert card["gates"]["deliverables"]["failed_items"] == ["ops_valid", "ops_not_idle"]
    assert card["outcome_status"] == "scored"

    assert card["weighted_total"]["score"] == 90.0
    playability = next(row for row in card["categories"]
                       if row["id"] == "playability_progression")
    assert playability["score"]["score"] == 60.0


def test_a_missing_ops_tape_is_still_charged_to_the_submission():

    from evalsys.verdict import Attribution, failed, skipped
    card = _delivery_zero_card(
        failed("ops_present", detail="ops.json is missing",
               attribution=Attribution.SUBMISSION),
        skipped("ops_valid", detail="no ops to validate"),
        skipped("ops_not_idle", detail="no ops to inspect"),
        skipped("causal_witness", detail="no ops to replay"),
    )
    assert card["weighted_total"]["score"] == 90.0
    assert card["evaluation_incomplete"] is False
    witness = next(
        leaf
        for row in card["categories"] if row["id"] == "playability_progression"
        for crit in row["criteria"] if crit["id"] == "whole_game_completion"
        for leaf in crit["sources"]
    )
    assert witness["point"] == 0.0
    assert card["weighted_total"].get("unmeasured") is None


def test_a_build_failure_still_voids_everything():

    from evalsys.verdict import Attribution, failed, inconclusive
    reason = "Unity batch build exited with code 1"
    card = _delivery_zero_card(
        failed("unity_build", detail=reason, attribution=Attribution.SUBMISSION),
        *[inconclusive(ident, detail=reason, attribution=Attribution.SUBMISSION)
          for ident in ("unity_mechanic_trace", "causal_witness",
                        "unity_runtime_stability", "unity_hidden_behavior")],
    )
    assert card["gates"]["submission"]["status"] == "failed"
    assert card["outcome_status"] == "candidate_delivery_failure"
    assert card["weighted_total"]["score"] == 0.0


def test_the_old_mode5_row_still_voids_a_refused_tape():

    from evalsys.taskgen.scorecard import MODE5_REGISTRY_VERSION
    from evalsys.verdict import Attribution, failed, skipped
    card = _delivery_zero_card(
        failed("ops_valid", detail="ops refused", attribution=Attribution.SUBMISSION),
        skipped("ops_not_idle", detail="ops were refused"),
        skipped("causal_witness", detail="ops were refused"),
        registry=MODE5_REGISTRY_VERSION,
    )
    assert "deliverables" not in card["gates"]
    assert card["gates"]["submission"]["status"] == "failed"
    assert card["weighted_total"]["score"] == 0.0


def test_partial_hidden_success_keeps_earned_credit_in_headline():
    from dataclasses import replace
    failed_run = replace(behavior_run("failed"), reading={"behavior_result": {
        "status": "fail", "goal_reached": False, "missing_observations": [],
    }})
    item = _aggregate_behavior_item("unity_hidden_behavior", [behavior_run("passed"), failed_run])
    assert item.verdict is Verdict.FAILED
    assert item.credit == 0.5
    card = score(item)
    assert card["weighted_total"]["score"] == 92.5
    assert card["ranking_eligible"]


@pytest.mark.parametrize("candidate_failed", [False, True])
def test_stability_keeps_every_scheduled_run_in_coverage(candidate_failed):
    from dataclasses import replace
    from evalsys.taskgen.evaluate import _unity_runtime_stability_item
    from evalsys.taskgen.unity.unity_probe import UnityRuntimeSuite
    clean = replace(behavior_run("clean"), reading={"rows": [{"f": 0}]})
    missing = replace(behavior_run("no-stream"), status="fail" if candidate_failed else "inconclusive",
                      reading={"attribution": "submission" if candidate_failed else "infrastructure"})
    item = _unity_runtime_stability_item(UnityRuntimeSuite(
        "pass", "fixture", clean, clean, None, hidden_behaviors=(missing,),
    ))
    card = score(item)
    if candidate_failed:
        assert item.credit == 0.5
        assert card["weighted_total"]["score"] == 95.0
        assert card["ranking_eligible"]
    else:
        assert item.verdict is Verdict.INCONCLUSIVE
        assert item.evidence["unmeasured_runs"] == ["no-stream"]
        assert card["weighted_total"]["score"] is None
        assert not card["ranking_eligible"]


@pytest.mark.parametrize("via_producer", [False, True])
@pytest.mark.parametrize("ownership", ["item", "evidence", "owner", "unknown"])
def test_independent_evaluator_gap_survives_candidate_root_failure(via_producer, ownership):
    from evalsys.verdict import Attribution, failed
    kwargs = {
        "item": {"attribution": Attribution.HARNESS},
        "evidence": {"evidence": {"attribution": "infrastructure"}},
        "owner": {"evidence": {"owner": "harness"}},
        "unknown": {"attribution": Attribution.UNATTRIBUTABLE},
    }[ownership]
    items = [failed("unity_build", attribution=Attribution.SUBMISSION),
             inconclusive("unity_vlm", detail="provider request timed out", **kwargs)]
    if via_producer:
        items = _attribute_mode5_items(items)
        assert items[1].verdict is Verdict.INCONCLUSIVE
    card = score(*items)
    assert card["weighted_total"]["score"] is None
    assert not card["ranking_eligible"]


@pytest.mark.parametrize("kind", ["candidate_exit", "dependency_skipped", "controller_timeout"])
def test_stability_honors_runtime_producer_failure_shapes(kind):
    from dataclasses import replace
    from evalsys.taskgen.evaluate import _unity_runtime_stability_item
    from evalsys.taskgen.unity.unity_probe import UnityRuntimeSuite
    clean = replace(behavior_run("clean"), reading={"rows": [{"f": 0}]})
    status, reading = {
        "candidate_exit": ("fail", {}),
        "dependency_skipped": ("inconclusive", {"attribution": "submission", "dependency_root": "witness"}),
        "controller_timeout": ("inconclusive", {"rows": [{"f": 0}], "errors": ["controller timed out"]}),
    }[kind]
    interrupted = replace(behavior_run(kind), status=status, reading=reading)
    item = _unity_runtime_stability_item(UnityRuntimeSuite(
        "pass", "fixture", clean, clean, None, hidden_behaviors=(interrupted,),
    ))
    if kind == "controller_timeout":
        assert item.verdict is Verdict.INCONCLUSIVE
        assert score(item)["weighted_total"]["score"] is None
    else:
        assert item.credit == 0.5
        assert score(item)["weighted_total"]["score"] == 95.0


def test_missing_paired_judge_criterion_keeps_its_independent_cause():
    from evalsys.verdict import Attribution, failed
    structure = _unity_fidelity_score_items({"criteria": [
        {"id": "asset_identity", "credit": 1.0},
    ]})[0]
    items = _attribute_mode5_items([
        failed("unity_probe", attribution=Attribution.SUBMISSION), structure,
    ])
    assert items[1].verdict is Verdict.INCONCLUSIVE
    assert items[1].attribution is Attribution.HARNESS
    assert score(*items)["weighted_total"]["score"] is None
