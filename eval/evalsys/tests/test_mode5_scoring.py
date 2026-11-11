from copy import deepcopy
import math

import pytest

from evalsys.taskgen.mode5.score import (
    COMPONENT_WEIGHTS, CRITERIA, Observation, aggregate_model, score_evidence,
)


def rubric():
    return {name: [name + "/required"] for name in CRITERIA}


def evidence(level="static_supported"):
    return {name: [Observation(name + "/required", level, ("controller/fact:1",))]
            for name in CRITERIA if name != "playability.final_goal"}


def record(game, level="static_supported"):
    return {**score_evidence(rubric(), evidence(level)), "game_id": game}


def test_exact_weights_and_subcriteria():
    assert len(CRITERIA) == 20
    assert sum(CRITERIA.values()) == 100
    for component, weight in COMPONENT_WEIGHTS.items():
        assert sum(value for name, value in CRITERIA.items()
                   if name.startswith(component + ".")) == weight


def test_static_scores_are_discounted_not_runtime():
    result = record("a")
    assert result["total"] == 57
    assert result["components"]["mechanics"]["points"] == 21
    assert result["components"]["playability"]["points"] == 12
    assert result["components"]["visual"]["points"] == 9
    assert result["score_mode"] == "static_offline_proxy"
    assert result["official_total"] is None
    assert result["runtime_verified"] is False
    assert result["paper_compatible"] is False


def test_editor_caps_apply_without_actual_runtime():
    observed = evidence("editor_verified")
    for name in list(observed):
        if name.startswith("visual."):
            observed[name] = evidence()[name]
    result = score_evidence(rubric(), observed)
    assert result["components"]["mechanics"]["points"] == 24.5
    assert result["components"]["playability"]["points"] == 15
    assert result["components"]["mechanics"]["cap_applied"]
    assert result["total"] == 68.5


def test_cold_start_does_not_remove_mechanics_or_playability_caps():
    observed = evidence("editor_verified")
    for name in list(observed):
        if name.startswith("visual."):
            observed[name] = evidence()[name]
    observed["stability.lifecycle"] = [Observation(
        "stability.lifecycle/required", "runtime_verified", ("controller/cold_start",),
    )]
    result = score_evidence(rubric(), observed)
    assert result["runtime_verified"] is True
    assert result["components"]["mechanics"]["points"] == 24.5
    assert result["components"]["playability"]["points"] == 15


def test_runtime_does_not_upgrade_unmeasured_visual_proxy():
    observed = evidence("runtime_verified")
    observed["playability.final_goal"] = [Observation(
        "playability.final_goal/required", "runtime_verified", ("probe/clear+matched-null",),
    )]
    for name in list(observed):
        if name.startswith("visual."):
            observed[name] = evidence()[name]
    result = score_evidence(rubric(), observed, runtime_attempted=True)
    assert result["total"] == 94
    assert result["visual_mode"] == "visual_implementation_correspondence"
    assert result["official_total"] is None


@pytest.mark.parametrize("level", ["static_supported", "editor_verified", "file_presence_only"])
def test_final_goal_cannot_be_inferred_from_artifacts(level):
    observed = evidence()
    observed["playability.final_goal"] = [Observation(
        "playability.final_goal/required", level, ("Assets/Win.cs",),
    )]
    with pytest.raises(ValueError, match="final goal"):
        score_evidence(rubric(), observed)


@pytest.mark.parametrize("level", ["runtime_verified", "editor_verified"])
def test_visual_artifacts_are_not_verified_appearance(level):
    observed = evidence()
    observed["visual.referenced_assets"] = [Observation(
        "visual.referenced_assets/required", level, ("probe/capture.png",),
    )]
    with pytest.raises(ValueError, match="appearance"):
        score_evidence(rubric(), observed)


def test_objective_visual_obligations_can_reach_full_scale():
    from evalsys.taskgen.mode5.visual_measure import VERIFICATIONS
    observed = evidence("runtime_verified")
    observed["playability.final_goal"] = [Observation(
        "playability.final_goal/required", "runtime_verified", ("controller/witness+null",))]
    for name, verification in VERIFICATIONS.items():
        observed[name] = [Observation(name + "/required", "runtime_verified",
            ("controller/visual/witness/checkpoint",), verification=verification)]
    result = score_evidence(rubric(), observed)
    assert result["total"] == 100
    assert result["components"]["visual"]["points"] == 15
    assert result["official_total"] is None


def test_visual_proof_cannot_be_reused_for_another_criterion():
    observed = evidence()
    observed["visual.reference_layout"] = [Observation(
        "visual.reference_layout/required", "runtime_verified", ("controller/visual/witness/start",),
        verification="actual-render-output.v1")]
    with pytest.raises(ValueError, match="criterion-specific"):
        score_evidence(rubric(), observed)


def test_failure_of_same_obligation_dominates_static_support():
    observed = evidence()
    observed["playability.input_chain"].append(Observation(
        "playability.input_chain/required", "failed", ("probe/matched-control:no-effect",),
    ))
    result = score_evidence(rubric(), observed)
    row = next(row for row in result["criteria"] if row["id"] == "playability.input_chain")
    assert row["points"] == 0
    assert row["evidence"][0]["level"] == "failed"
    assert result["total"] == 54


def test_failure_is_local_not_all_or_nothing():
    observed = evidence()
    observed["playability.final_goal"] = [Observation("playability.final_goal/required", "failed")]
    result = score_evidence(rubric(), observed, runtime_attempted=True)
    assert result["components"]["playability"]["points"] == 12
    assert result["components"]["structure"]["points"] == 9
    assert result["runtime_verified"] is False


def test_missing_obligations_remain_in_denominator():
    expected = rubric()
    expected["mechanics.core_mechanics"].append("missing-mechanic")
    result = score_evidence(expected, evidence())
    assert result["components"]["mechanics"]["points"] == 17.4
    row = next(row for row in result["criteria"] if row["id"] == "mechanics.core_mechanics")
    assert row["denominator"] == 2
    assert row["evidence"][1]["observed"] is False


def test_file_presence_discount():
    result = record("a", "file_presence_only")
    assert result["total"] == 23.75


def test_infrastructure_failure_is_not_a_candidate_zero():
    result = score_evidence(rubric(), evidence(), infrastructure_complete=False)
    assert result["total"] is None
    assert result["earned_proxy_points"] == 57
    assert result["status"] == "infrastructure_incomplete"
    assert not result["ranking_eligible"]


def test_integrity_failure_is_unrankable():
    result = score_evidence(rubric(), evidence(), integrity_passed=False)
    assert result["total"] is None
    assert result["status"] == "integrity_failed"


def test_duplicate_observations_do_not_add_credit():
    observed = evidence()
    observed["mechanics.core_mechanics"] *= 100
    assert score_evidence(rubric(), observed)["total"] == 57


@pytest.mark.parametrize("bad", [[], ["a", "a"], "only-one", [False]])
def test_denominator_validation(bad):
    expected = rubric()
    expected["mechanics.core_mechanics"] = bad
    with pytest.raises(ValueError, match="denominator"):
        score_evidence(expected, {})


def test_unknown_obligation_rejected():
    observed = evidence()
    observed["mechanics.core_mechanics"] = [Observation("easy-extra", "failed")]
    with pytest.raises(ValueError, match="outside frozen"):
        score_evidence(rubric(), observed)


def test_positive_evidence_requires_audit_reference():
    observed = evidence()
    observed["mechanics.core_mechanics"] = [Observation(
        "mechanics.core_mechanics/required", "static_supported",
    )]
    with pytest.raises(ValueError, match="audit references"):
        score_evidence(rubric(), observed)


def test_robust_model_average_uses_common_task_tail():
    reports = [record("a"), record("b", "file_presence_only"), record("c"), record("d")]
    result = aggregate_model(reports, expected_games=["a", "b", "c", "d"])
    assert result["tail_games"] == ["b"]
    assert result["lowest_quartile_mean"] == 23.75
    expected = .7 * ((57 * 3 + 23.75) / 4) + .3 * 23.75
    assert result["total"] == (57 * 3 + 23.75) / 4
    assert result["reliability_score"] == expected
    assert math.isclose(sum(row["score"] * row["weight"] / 100
                            for row in result["components"].values()), result["total"])


def test_41_game_tail_has_11_tasks():
    games = [f"g{index:02d}" for index in range(41)]
    result = aggregate_model([record(game) for game in reversed(games)], expected_games=games)
    assert result["tail_games"] == games[:11]


def test_missing_game_does_not_disappear_into_full_model_average():
    result = aggregate_model([record("a")], expected_games=["a", "b"])
    assert result["total"] is None
    assert result["missing_games"] == ["b"]


def test_unrankable_task_cannot_be_ignored():
    report = record("a")
    report["ranking_eligible"] = False
    result = aggregate_model([report], expected_games=["a"])
    assert result["total"] is None
    assert result["unscored_games"] == ["a"]


@pytest.mark.parametrize("mutation", ["duplicate", "registry", "reconciliation", "nan", "boolean"])
def test_invalid_model_records_rejected(mutation):
    report = deepcopy(record("a"))
    reports = [report]
    if mutation == "duplicate":
        reports.append(report)
    elif mutation == "registry":
        report["registry_version"] = "some-other-version"
    elif mutation == "reconciliation":
        report["total"] = 80
    elif mutation == "nan":
        report["total"] = float("nan")
    elif mutation == "boolean":
        report["total"] = True
    with pytest.raises(ValueError):
        aggregate_model(reports, expected_games=["a"])
