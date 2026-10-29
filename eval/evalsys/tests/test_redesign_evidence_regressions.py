
from dataclasses import replace

import pytest

from evalsys.assertions.behavior_contracts import player_counts_for_mode, probe_plan
from evalsys.assertions.exemptions import evaluate_universal
from evalsys.taskgen.scorecard import (
    MODE1_REDESIGN_REGISTRY_VERSION, MODE2_REDESIGN_REGISTRY_VERSION,
    MODE3_REDESIGN_REGISTRY_VERSION, score_task_result,
)
from evalsys.truth.snapshot import GroupTruth
from evalsys.verdict import failed, score_items
from test_behavior_contracts import snapshot
from test_mode1_redesign_scorecard import _result as brief_result
from test_progressive_redesign_scorecard import _axis, _gdd_with_atoms
from test_taskgen_mode34b import _fixture


def _complete_result():
    result = _gdd_with_atoms(observed=["group:gb_player"], missing=[])
    for row in result.mode1_context["route_readings"]:
        row["reached"] = True
        row["stop_reason"] = "goal_reached"
    result.mode1_context["route_readings"][1]["milestones_reached"] = ["opened", "crossed"]
    result.mode1_context["route_readings"][2]["milestones_reached"] = ["opened", "finished"]
    return result


def _score(result):
    return score_task_result(result, MODE2_REDESIGN_REGISTRY_VERSION)


def test_complete_routes_score_and_inapplicable_numeric_axis_does_not_block_ranking():
    card = _score(_complete_result())
    assert card["ranking_eligible"]
    assert not card["evaluation_incomplete"]
    for name in ("hidden_scenarios", "task_checkpoints"):
        assert _axis(card, name)["credit"] == 1.0
    assert _axis(card, "numeric_contract")["status"] == "empty_denominator"
    assert _axis(card, "visual_placeholder")["earned_points"] == 5


@pytest.mark.parametrize("gate", ["segment_clean", "used_required"])
def test_invalid_route_keeps_its_weight_but_cannot_earn_completion_or_checkpoint_credit(gate):
    result = _complete_result()
    result.mode1_context["route_readings"][1][gate] = False
    card = _score(result)
    for name in ("hidden_scenarios", "task_checkpoints"):
        axis = _axis(card, name)
        assert axis["credit"] == 0.5
        assert axis["status"] == "measured"
    assert card["ranking_eligible"]


@pytest.mark.parametrize("stop", ["timeout", "driver_error", "setup_failed", "reference_observation_missing"])
@pytest.mark.parametrize("reading_index", [0, 1])
def test_evaluator_failure_on_control_or_positive_route_withholds_ranking(stop, reading_index):
    result = _complete_result()
    row = result.mode1_context["route_readings"][reading_index]
    row.update(stop_reason=stop, reached=False, milestones_reached=[])
    card = _score(result)
    assert card["evaluation_incomplete"]
    assert not card["ranking_eligible"]
    for name in ("hidden_scenarios", "task_checkpoints"):
        axis = _axis(card, name)
        assert axis["status"] == "not_instrumented_zero"
        assert row["route_id"] in axis["evidence"]["unmeasured_routes"]


@pytest.mark.parametrize("reading_index", [0, 1])
def test_absent_replay_cannot_reduce_the_expected_denominator(reading_index):
    result = _complete_result()
    missing = result.mode1_context["route_readings"].pop(reading_index)
    card = _score(result)
    assert not card["ranking_eligible"]
    for name in ("hidden_scenarios", "task_checkpoints"):
        assert missing["route_id"] in _axis(card, name)["evidence"]["unmeasured_routes"]


def test_submission_failure_remains_a_measured_zero():
    result = _complete_result()
    result.mode1_context["route_readings"][1].update(
        reached=False, stop_reason="no_player", milestones_reached=[],
    )
    card = _score(result)
    assert card["ranking_eligible"]
    for name in ("hidden_scenarios", "task_checkpoints"):
        axis = _axis(card, name)
        assert axis["status"] == "measured"
        assert axis["credit"] == 0.5


def test_checkpoint_local_devices_allow_valid_partial_progress():
    result = _complete_result()
    route = result.mode1_context["route_specs"][1]
    route["required_devices"] = ["late_goal#0"]
    for milestone in route["goal"]["milestones"]:
        milestone["required_devices"] = ["early_door#0"]
    result.mode1_context["route_readings"][1].update(
        reached=False, used_required=False, stop_reason="ops_exhausted",
        milestones_reached=["opened"], devices_used=["early_door#0"],
    )
    card = _score(result)
    assert _axis(card, "task_checkpoints")["credit"] == 0.75
    assert _axis(card, "hidden_scenarios")["credit"] == 0.5


@pytest.mark.parametrize("mode", ["brief", "gdd", "skeleton"])
def test_missing_native_axes_are_incomplete_even_when_their_credit_is_already_zero(mode):
    if mode == "brief":
        result = brief_result(qwen_like=False)
        registry = MODE1_REDESIGN_REGISTRY_VERSION
    elif mode == "gdd":
        result = _complete_result()
        result.mode1_context.pop("candidate_snapshot")
        result.mode1_context["route_readings"] = []
        registry = MODE2_REDESIGN_REGISTRY_VERSION
    else:
        result = _fixture("skeleton_noop_canopy_dash")
        registry = MODE3_REDESIGN_REGISTRY_VERSION
    card = score_task_result(result, registry)
    assert card["evaluation_incomplete"]
    assert card["weighted_total"]["status"] == "evaluation_incomplete"
    assert not card["ranking_eligible"]
    sources = {row["source"] for row in card["weighted_total"]["unmeasured"]}
    assert {"universal_mechanics", "task_checkpoints", "hidden_scenarios"} <= sources
    category = next(row for row in card["categories"] if any(
        criterion["id"] == "universal_mechanics" for criterion in row["criteria"]
    ))
    assert category["measured_weight_share"] == 0.0


def test_missing_required_numeric_samples_are_not_an_inapplicable_axis():
    result = _complete_result()
    result.mode1_context["rubric"]["required_numeric_slots"] = ["score"]
    card = _score(result)
    assert _axis(card, "numeric_contract")["status"] == "unverified_zero"
    assert not card["ranking_eligible"]


@pytest.mark.parametrize("field", ["expected", "reached", "missing"])
def test_diagnostic_mechanic_names_do_not_satisfy_requirement_coverage(field):
    result = _gdd_with_atoms(observed=["group:gb_player"], missing=["mechanic:forward_run"])
    result.items = [item for item in result.items if item.id != "mechanic_trace"]
    result.items.append(failed("mechanic_trace", evidence={field: ["forward_run"]}))
    card = _score(result)
    coverage = _axis(card, "gdd_requirement_alignment")["evidence"]["coverage"]
    assert coverage["atoms_orphaned"] == 1
    assert coverage["orphans"][0]["requirement_id"] == "mechanic:forward_run"
    assert not card["ranking_eligible"]


def test_requirement_that_is_an_actual_checkpoint_is_covered_even_when_failed():
    result = _gdd_with_atoms(observed=["group:gb_player"], missing=["mechanic:opened"])
    card = _score(result)
    assert _axis(card, "task_checkpoints")["credit"] == 0.0
    assert _axis(card, "gdd_requirement_alignment")["evidence"]["coverage"]["atoms_orphaned"] == 0
    assert card["ranking_eligible"]


@pytest.mark.parametrize("mode", ["brief", "gdd"])
@pytest.mark.parametrize("count", [1, 2, 3])
def test_universal_score_respects_the_modes_published_player_count_contract(mode, count):
    snap = snapshot()
    level = replace(snap.levels[0], groups={
        **snap.levels[0].groups, "gb_player": GroupTruth(total=count, alive=count),
    })
    snap = replace(snap, levels=(level,))
    result = brief_result(qwen_like=False) if mode == "brief" else _complete_result()
    result.package.manifest["game_id"] = "ember_and_tide"
    result.mode1_context["candidate_snapshot"] = snap.to_dict()
    registry = MODE1_REDESIGN_REGISTRY_VERSION if mode == "brief" else MODE2_REDESIGN_REGISTRY_VERSION
    card = score_task_result(result, registry)
    expected = evaluate_universal(
        snap, task_id="ember_and_tide",
        player_counts=player_counts_for_mode(probe_plan("ember_and_tide"), mode),
    )
    assert _axis(card, "universal_mechanics")["credit"] == pytest.approx(score_items(expected).point)
