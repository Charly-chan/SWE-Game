from __future__ import annotations

from evalsys.taskgen.scorecard import (
    MODE2_REDESIGN_REGISTRY_VERSION,
    MODE2_REDESIGN_WEIGHTS,
    MODE3_REDESIGN_REGISTRY_VERSION,
    MODE3_REDESIGN_WEIGHTS,
    MODE4_F2P_P2P_REGISTRY_VERSION,
    MODE4_REDESIGN_REGISTRY_VERSION,
    score_task_result,
)
from evalsys.verdict import passed
from test_taskgen_mode34b import _fixture
from test_taskgen_scorecard import _result


def _truth() -> dict:
    return {"levels": [{"groups": {"gb_player": {"alive": 1}}}]}


def test_progressive_additive_weights_are_fixed_at_100() -> None:
    assert sum(MODE2_REDESIGN_WEIGHTS.values()) == 100
    assert sum(MODE3_REDESIGN_WEIGHTS.values()) == 100
    assert MODE2_REDESIGN_WEIGHTS["visual_placeholder"] == 15
    assert MODE3_REDESIGN_WEIGHTS["visual_placeholder"] == 15


def test_mode2_inherits_mode1_shape_without_candidate_gdd_points() -> None:
    result = _result()
    result.mode1_context = {
        "rubric": {"required_groups": ["gb_player"], "required_numeric_slots": []},
        "candidate_truth": _truth(),
        "reference_truth": _truth(),
        "feature_demos": False,
    }
    card = score_task_result(result, MODE2_REDESIGN_REGISTRY_VERSION)
    assert card["mode"] == "gdd"


    assert card["weighted_total"]["score"] == 62.5
    named_zeros = {
        axis["id"]: axis for axis in card["axes"]
        if axis["id"] in {"universal_mechanics", "task_checkpoints", "hidden_scenarios"}
    }
    assert len(named_zeros) == 3
    assert all(axis["earned_points"] == 0 for axis in named_zeros.values())
    assert card["weighted_total"]["headline_ceiling"] == 90
    assert {axis["id"] for axis in card["axes"]} == set(MODE2_REDESIGN_WEIGHTS)
    assert not {"gdd_quality", "gdd_interface"} & {axis["id"] for axis in card["axes"]}
    alignment = next(axis for axis in card["axes"] if axis["id"] == "gdd_requirement_alignment")
    assert alignment["earned_points"] == 0
    assert "not counted a second time" in alignment["evidence"]["detail"]
    checkpoints = next(axis for axis in card["axes"] if axis["id"] == "task_checkpoints")
    assert checkpoints["status"] == "not_instrumented_zero"
    assert checkpoints["earned_points"] == 0


def test_mode3_noop_does_not_receive_scaffold_baseline_points() -> None:
    card = score_task_result(
        _fixture("skeleton_noop_canopy_dash"), MODE3_REDESIGN_REGISTRY_VERSION
    )
    assert card["mode"] == "skeleton"
    assert card["weighted_total"]["score"] <= 15
    by_id = {axis["id"]: axis for axis in card["axes"]}
    assert by_id["universal_mechanics"]["earned_points"] == 0
    assert by_id["content_structure"]["earned_points"] == 0
    assert by_id["hidden_scenarios"]["earned_points"] == 0
    assert by_id["visual_placeholder"]["earned_points"] == 5


def test_mode4_uses_registered_repair_product_and_50_25_25_preservation() -> None:
    clean_result = _fixture("bugfix_revert_shadow_walker")
    clean_result.mode4_context = {
        "case_id": "fixture-hard-v1", "difficulty": {"tier": "hard"},
    }
    regressed_result = _fixture("bugfix_regress_shadow_walker")
    regressed_result.mode4_context = {
        "case_id": "fixture-medium-v1", "difficulty": {"tier": "medium"},
    }
    clean = score_task_result(clean_result, MODE4_REDESIGN_REGISTRY_VERSION)
    regressed = score_task_result(regressed_result, MODE4_REDESIGN_REGISTRY_VERSION)
    assert clean["weighted_total"]["score"] == 100
    assert clean["weighted_total"]["headline_ceiling"] == 100
    assert clean["weighted_total"]["composition"]["regression_weights"] == {
        "mechanics_requirements/feature_preservation": 50,
        "mode_specific/product_surface": 25,
        "mode_specific/transformation": 25,
    }

    assert regressed["weighted_total"]["score"] == 25
    assert regressed["weighted_total"]["composition"]["regression_mean"] == 75
    assert regressed["evaluation_status"] == "complete"
    assert "visual_placeholder" not in str(regressed)
    assert clean["repair_correctness"]["score"] == 100
    assert clean["difficulty_evidence"]["earned_evidence_points"] == 3
    assert clean["difficulty_evidence"]["ranking_eligible"] is False
    assert regressed["repair_correctness"]["score"] == 25
    assert regressed["difficulty_evidence"]["earned_evidence_points"] == 0.5


def test_mode4_missing_tier_never_invents_difficulty_points() -> None:
    card = score_task_result(
        _fixture("bugfix_revert_shadow_walker"), MODE4_REDESIGN_REGISTRY_VERSION
    )
    assert card["repair_correctness"]["score"] == 100
    assert card["difficulty_evidence"]["status"] == "not_measured"
    assert "earned_evidence_points" not in card["difficulty_evidence"]


def test_mode4_f2p_p2p_reports_full_and_regression_as_non_exchangeable() -> None:
    clean = score_task_result(
        _fixture("bugfix_revert_shadow_walker"), MODE4_F2P_P2P_REGISTRY_VERSION
    )
    regressed = score_task_result(
        _fixture("bugfix_regress_shadow_walker"), MODE4_F2P_P2P_REGISTRY_VERSION
    )
    assert clean["resolution_status"] == "FULL"
    assert clean["resolved"] is True
    assert clean["weighted_total"]["score"] == 100
    assert clean["f2p_rate"] == clean["p2p_rate"] == 100
    assert regressed["resolution_status"] == "NO"
    assert regressed["resolved"] is False
    assert regressed["weighted_total"]["score"] == 0
    assert regressed["f2p_rate"] == 50
    assert regressed["p2p_rate"] == 66.667


def test_mode4_f2p_p2p_partial_is_diagnostic_not_resolved_credit() -> None:
    result = _fixture("bugfix_revert_shadow_walker")
    result.resolved = False
    result.items.append(passed(
        "repair_restoration_graded",
        credit=0.5,
        evidence={
            "repair_granularity": "assertion",
            "target_assertions_restored": 1,
            "target_assertions_total": 2,
            "restored_assertions": ["target:a"],
            "unrestored_assertions": ["target:b"],
            "preserved_passing": 3,
            "preserved_total": 3,
            "preserved_failed": [],
            "regression_factor": 1.0,
        },
    ))
    card = score_task_result(result, MODE4_F2P_P2P_REGISTRY_VERSION)
    assert card["resolution_status"] == "PARTIAL"
    assert card["weighted_total"]["score"] == 0
    assert card["f2p_rate"] == 50
    assert card["p2p_rate"] == 100


def _routed(*, reached: tuple[str, ...], control_reached: bool = True) -> object:

    result = _result()
    result.mode1_context = {
        "rubric": {"required_groups": ["gb_player"], "required_numeric_slots": []},
        "candidate_truth": _truth(),
        "reference_truth": _truth(),
        "feature_demos": False,
        "route_specs": [
            {"route_id": "game/L0/no_input", "tier": 0, "scored": False},
            *[
                {"route_id": ident, "tier": tier, "goal": {
                    "predicate": "whole_game_clear()",
                    "milestones": [
                        {"name": name, "predicate": f"levels_visited() >= {i + 1}"}
                        for i, name in enumerate(names)
                    ],
                }}
                for ident, tier, names in (
                    ("game/C1/first", 2, ["opened", "crossed"]),
                    ("game/C2/second", 3, ["opened", "finished"]),
                )
            ],
        ],
        "route_readings": [
            {"route_id": "game/L0/no_input", "tier": 0, "reached": control_reached,
             "segment_clean": True, "used_required": True, "stop_reason": "ops_exhausted"},
            {
                "route_id": "game/C1/first", "tier": 2, "reached": False,
                "segment_clean": True, "used_required": True, "stop_reason": "ops_exhausted",
                "milestones_reached": [name for name in reached if name in {"opened", "crossed"}],
            },
            {
                "route_id": "game/C2/second", "tier": 3, "reached": False,
                "segment_clean": True, "used_required": True, "stop_reason": "ops_exhausted",
                "milestones_reached": ["opened", "finished"] if "finished" in reached else [],
            },
        ],
    }
    return result


def test_task_checkpoints_grade_on_certified_routes_not_the_disclosed_checks() -> None:
    card = score_task_result(_routed(reached=("opened",)), MODE2_REDESIGN_REGISTRY_VERSION)
    checkpoints = next(axis for axis in card["axes"] if axis["id"] == "task_checkpoints")
    assert checkpoints["status"] == "measured"

    assert checkpoints["credit"] == 0.25
    assert checkpoints["evidence"]["reached"] == 1
    assert checkpoints["evidence"]["total"] == 4

    disclosed = checkpoints["evidence"]["disclosed_checkpoints"]
    assert "recorded_not_scored" in disclosed
    assert checkpoints["credit"] != disclosed.get("coverage")


def test_task_checkpoints_are_not_credited_when_the_no_input_control_misbehaves() -> None:
    card = score_task_result(
        _routed(reached=("opened", "crossed", "finished"), control_reached=False),
        MODE2_REDESIGN_REGISTRY_VERSION,
    )
    checkpoints = next(axis for axis in card["axes"] if axis["id"] == "task_checkpoints")
    assert checkpoints["status"] == "measured"
    assert checkpoints["earned_points"] == 0
    assert "no-input control" in checkpoints["evidence"]["detail"]


_SNAPSHOT_EMPTY = {"project": "scaffold"}


_SNAPSHOT_PARTIAL = {
    "project": "candidate",
    "declared_actions": ["gb_left", "gb_right"],
    "levels": [{"scene": "res://a.tscn", "reached": True}],
    "liveness": [{"action": "gb_left", "moved": True}],
    "no_input_no_win": True,
    "endings": {"success": "res://win.tscn"},
}


def _gdd_with(**context) -> object:
    result = _result()
    result.mode1_context = {
        "rubric": {"required_groups": ["gb_player"], "required_numeric_slots": []},
        "candidate_truth": _truth(),
        "reference_truth": _truth(),
        "feature_demos": False,
        **context,
    }
    return result


def _axis(card: dict, axis_id: str) -> dict:
    return next(axis for axis in card["axes"] if axis["id"] == axis_id)


def test_universal_mechanics_scores_the_u_class_alone() -> None:
    card = score_task_result(
        _gdd_with(candidate_snapshot=_SNAPSHOT_PARTIAL), MODE2_REDESIGN_REGISTRY_VERSION,
    )
    axis = _axis(card, "universal_mechanics")
    assert axis["status"] == "measured"

    assert round(axis["credit"], 6) == round(3 / 7, 6)
    assert axis["evidence"]["families"] == "U1-U7"
    assert "task_checkpoints" in axis["evidence"]["excluded"]

    assert "superseded_source" in axis["evidence"]


def test_mode3_universal_mechanics_credits_only_what_the_scaffold_left() -> None:
    result = _fixture("skeleton_noop_canopy_dash")
    result.mode1_context = {
        "candidate_snapshot": _SNAPSHOT_PARTIAL,
        "scaffold_snapshot": _SNAPSHOT_EMPTY,
    }
    axis = _axis(
        score_task_result(result, MODE3_REDESIGN_REGISTRY_VERSION), "universal_mechanics",
    )
    assert axis["status"] == "measured"


    assert axis["credit"] == 0.5
    assert axis["evidence"]["candidate"] == 0.5
    assert axis["evidence"]["scaffold"] == 0.0


def test_mode3_universal_mechanics_gives_an_unchanged_project_nothing() -> None:
    result = _fixture("skeleton_noop_canopy_dash")
    result.mode1_context = {
        "candidate_snapshot": _SNAPSHOT_PARTIAL,
        "scaffold_snapshot": _SNAPSHOT_PARTIAL,
    }
    axis = _axis(
        score_task_result(result, MODE3_REDESIGN_REGISTRY_VERSION), "universal_mechanics",
    )
    assert axis["status"] == "measured"
    assert axis["credit"] == 0.0
    assert axis["earned_points"] == 0


def _gdd_with_atoms(observed, missing, unmeasurable=()) -> object:

    from evalsys.verdict import passed
    result = _routed(reached=())
    result.mode1_context["candidate_snapshot"] = _SNAPSHOT_PARTIAL
    result.items = [i for i in result.items if i.id != "gdd_mechanics_observable"]
    result.items.append(passed(
        "gdd_mechanics_observable",
        detail="fixture",
        evidence={
            "observed": list(observed),
            "missing": list(missing),
            "untriggered": [],
            "unmeasurable_mechanics": list(unmeasurable),
            "denominator": len(observed) + len(missing),
        },
    ))
    return result


def test_alignment_sentinel_is_quiet_when_every_atom_is_graded_by_its_owner() -> None:


    result = _gdd_with_atoms(observed=["group:gb_player"], missing=[])
    result.mode1_context["rubric"] = {
        "required_groups": ["gb_player"], "required_numeric_slots": [],
    }
    card = score_task_result(result, MODE2_REDESIGN_REGISTRY_VERSION)
    axis = _axis(card, "gdd_requirement_alignment")
    coverage = (axis["evidence"] or {}).get("coverage") or {}
    assert coverage.get("atoms_orphaned") == 0, coverage
    assert axis["status"] == "empty_denominator"


def test_alignment_sentinel_reports_an_atom_no_axis_actually_graded() -> None:


    result = _gdd_with_atoms(
        observed=["group:gb_player"], missing=["mechanic:forward_run"],
    )
    result.mode1_context["rubric"] = {
        "required_groups": ["gb_player"], "required_numeric_slots": [],
    }
    card = score_task_result(result, MODE2_REDESIGN_REGISTRY_VERSION)
    axis = _axis(card, "gdd_requirement_alignment")
    coverage = (axis["evidence"] or {}).get("coverage") or {}
    assert coverage.get("atoms_orphaned") == 1, coverage
    assert [row["requirement_id"] for row in coverage["orphans"]] == ["mechanic:forward_run"]
    assert axis["status"] == "unscored_atoms"
    assert axis["earned_points"] == 0


    assert axis["weight_in_total"] == 0.0
    assert card["evaluation_incomplete"]
    assert not card["ranking_eligible"]
    assert [
        row["source"] for row in card["weighted_total"]["unmeasured"]
    ] == ["gdd_requirement_alignment"]

    clean = score_task_result(
        _gdd_with_atoms(observed=["group:gb_player"], missing=[]),
        MODE2_REDESIGN_REGISTRY_VERSION,
    )
    assert not clean["evaluation_incomplete"]
    assert clean["weighted_total"]["headline_ceiling"] == \
        card["weighted_total"]["headline_ceiling"] == 90.0


def test_alignment_sentinel_excuses_a_check_the_rubric_calls_unmeasurable() -> None:
    result = _gdd_with_atoms(
        observed=["group:gb_player"], missing=[], unmeasurable=["delivery_pay"],
    )
    result.mode1_context["rubric"] = {
        "required_groups": ["gb_player"], "required_numeric_slots": [],
    }
    card = score_task_result(result, MODE2_REDESIGN_REGISTRY_VERSION)
    axis = _axis(card, "gdd_requirement_alignment")
    coverage = (axis["evidence"] or {}).get("coverage") or {}
    assert coverage.get("atoms_orphaned") == 0, coverage
    assert axis["status"] == "empty_denominator"
