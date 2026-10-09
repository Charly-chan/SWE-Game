from copy import deepcopy
import json

import pytest

from evalsys.taskgen.mode5.adapter import (
    frozen_obligations, snapshot_static, finish_scoring,
)
from evalsys.taskgen.mode5.score import Observation, score_evidence


def rubric():
    return {"required_actions": ["gb_left"], "min_levels": 1,
            "required_groups": ["gb_player"], "required_numeric_slots": ["health"],
            "require_failure_ending": True,
            "mechanic_checks": [{"id": "damage", "measurable": True,
                                 "observable": {"predicate": "numeric_delta(health) < 0"}}]}


def fixture(tmp_path):
    from test_mode5_evidence import scene, put
    project, baseline = tmp_path / "project", tmp_path / "baseline"
    scene(project)
    baseline.mkdir()
    put(project, "Packages/manifest.json", json.dumps({"dependencies": {"com.unity.modules.physics": "1.0.0"}}))
    put(project, "ProjectSettings/ProjectVersion.txt", "m_EditorVersion: 6000.3.23f1\n")
    interface = {"entry_scene": "Assets/Entry.unity", "levels": [{"scene": "Assets/Entry.unity"}],
                 "supported_actions": ["gb_left"]}
    snapshot = snapshot_static(project, baseline=baseline, rubric=rubric(), interface=interface,
                               ops_valid=True, ops_nonidle=True)
    items = [{"id": name, "verdict": "passed", "evidence": {}} for name in (
        "unity_sdk_integrity", "no_eval_smuggling", "no_bundled_godot_runtime", "unity_anti_grant_static",
        "unity_hidden_behavior", "unity_counterfactual", "unity_auto_win_ready",
    )]
    return snapshot, items


def run(ident="witness", *, status="pass", errors=()):
    return {"run_id": ident, "status": status, "reached": False,
            "reading": {"row_count": 3 if status == "pass" else 0,
                        "error_count": len(errors), "error_examples": list(errors)}}


def runtime_engine(positive=None):
    return {"build": {"status": "pass"}, "mode5_visual": {
        "schema": "gamebench.mode5.visual-reading.v1", "complete": True, "observations": []}, "runtime": {
        "witness": run(), "matched_null": run("null"),
        "hidden_behaviors": positive or [],
    }}


def criterion(result, name):
    return next(row for row in result["criteria"] if row["id"] == name)


def test_build_failure_preserves_static_points_without_runtime(tmp_path):
    snapshot, items = fixture(tmp_path)
    result = finish_scoring(snapshot, items=items, engine={
        "build": {"status": "fail", "attribution": "submission"},
    })
    assert result["total"] is not None and result["total"] > 0
    assert not result["runtime_verified"]
    assert result["score_mode"] == "static_offline_proxy"
    assert criterion(result, "mechanics.core_mechanics")["points"] == 0
    assert criterion(result, "stability.packages_build")["points"] == pytest.approx(1.6)


def test_build_pass_is_editor_not_runtime_verification(tmp_path):
    snapshot, items = fixture(tmp_path)
    result = finish_scoring(snapshot, items=items, engine={"build": {"status": "pass"}})
    assert result["total"] is None
    assert result["runtime_verified"] is False
    evidence = criterion(result, "stability.packages_build")["evidence"]
    assert evidence[-1]["level"] == "editor_verified"


def test_trigger_only_gets_no_verified_effect_credit(tmp_path):
    snapshot, items = fixture(tmp_path)
    items.append({"id": "unity_mechanic_trace", "verdict": "passed", "credit": .5,
                  "evidence": {"credits": {"damage": .5}, "runs": [
                      {"run_id": "witness", "reached": [], "triggered": ["damage"]}]}})
    result = finish_scoring(snapshot, items=items, engine=runtime_engine())
    assert criterion(result, "mechanics.core_mechanics")["points"] == 0
    assert criterion(result, "playability.progression")["points"] == 0


def test_actual_effect_gets_runtime_credit(tmp_path):
    snapshot, items = fixture(tmp_path)
    items.append({"id": "unity_mechanic_trace", "verdict": "passed", "evidence": {
        "runs": [{"run_id": "witness", "reached": ["damage"], "triggered": []}]}})
    result = finish_scoring(snapshot, items=items, engine=runtime_engine())
    assert criterion(result, "mechanics.core_mechanics")["points"] == 12
    assert criterion(result, "mechanics.interactions")["points"] == 10
    assert criterion(result, "playability.progression")["points"] == 0


def test_effect_observed_before_crash_remains_local_credit(tmp_path):
    snapshot, items = fixture(tmp_path)
    items.append({"id": "unity_mechanic_trace", "verdict": "passed", "evidence": {
        "runs": [{"run_id": "witness", "reached": ["damage"]}]}})
    engine = runtime_engine()
    engine["runtime"]["witness"]["status"] = "fail"
    engine["runtime"]["witness"]["reading"].update(row_count=5, error_count=1,
                                                 error_examples=["NullReferenceException"])
    result = finish_scoring(snapshot, items=items, engine=engine)
    assert criterion(result, "mechanics.core_mechanics")["points"] == 12
    assert criterion(result, "stability.lifecycle")["points"] == 0
    assert criterion(result, "playability.final_goal")["points"] == 0


def test_candidate_controller_close_keeps_proxy_score_when_visual_capture_is_incomplete(tmp_path):
    """A candidate protocol failure must not be published as evaluator failure."""
    snapshot, items = fixture(tmp_path)
    engine = runtime_engine()
    engine["runtime"]["witness"]["status"] = "fail"
    engine["runtime"]["witness"]["reading"] = {
        "row_count": 0,
        "error_count": 0,
        "error_examples": [],
    }
    engine["runtime"]["witness"]["detail"] = (
        "controller protocol failed: peer closed the controller channel"
    )
    engine["mode5_visual"]["complete"] = False

    result = finish_scoring(snapshot, items=items, engine=engine)

    assert result["status"] == "scored_proxy"
    assert result["total"] is not None
    assert result["ranking_eligible"] is True


@pytest.mark.parametrize("null_won", [True, False])
def test_whole_game_clear_requires_causal_control(tmp_path, null_won):
    snapshot, items = fixture(tmp_path)
    items.extend([
        {"id": "unity_input_dispatch", "verdict": "passed"},
        {"id": "causal_witness", "verdict": "passed", "evidence": {
            "witness_won": True, "matched_null_won": null_won,
            "observed_levels": [1, 2],
        }},
    ])
    engine = runtime_engine()
    engine["runtime"]["witness"]["reached"] = True
    engine["runtime"]["matched_null"]["reached"] = null_won
    result = finish_scoring(snapshot, items=items, engine=engine)
    assert criterion(result, "playability.final_goal")["points"] == (0 if null_won else 5)
    # A level index changing is not proof of reset, pause or causal scene flow.
    assert criterion(result, "playability.scene_flow")["points"] == 0


def test_failed_run_stays_in_stability_denominator(tmp_path):
    snapshot, items = fixture(tmp_path)
    result = finish_scoring(snapshot, items=items, engine=runtime_engine([run("hidden", status="fail")]))
    row = criterion(result, "stability.lifecycle")
    assert row["denominator"] == 3
    assert row["points"] == .5
    cold = next(value for value in row["evidence"] if value["obligation"] == "cold_start")
    assert cold["coverage"] == .5


def test_adding_runs_does_not_dilute_missing_reset(tmp_path):
    snapshot, items = fixture(tmp_path)
    result = finish_scoring(snapshot, items=items,
                            engine=runtime_engine([run(f"hidden-{n}") for n in range(20)]))
    row = criterion(result, "stability.lifecycle")
    assert row["denominator"] == 3
    assert row["points"] == 1


def test_optional_geometry_gap_does_not_erase_static_proxy(tmp_path):
    snapshot, items = fixture(tmp_path)
    by_id = {row["id"]: row for row in items}
    by_id["unity_hidden_behavior"]["verdict"] = "unobservable"
    result = finish_scoring(snapshot, items=items, engine=runtime_engine())
    assert result["total"] is not None
    assert criterion(result, "playability.progression")["points"] == 0


def test_candidate_contract_or_integrity_failure_cannot_be_ranked(tmp_path):
    snapshot, items = fixture(tmp_path)
    items[0]["verdict"] = "failed"
    result = finish_scoring(snapshot, items=items, engine=runtime_engine())
    assert result["status"] == "integrity_failed"


def test_snapshot_version_validation(tmp_path):
    snapshot, items = fixture(tmp_path)
    snapshot["registry_version"] = "old-version"
    with pytest.raises(ValueError, match="protocol"):
        finish_scoring(snapshot, items=items, engine=runtime_engine())


def test_incomplete_source_or_reference_inspection_is_not_candidate_zero(tmp_path):
    snapshot, items = fixture(tmp_path)
    snapshot["inspection_complete"] = False
    result = finish_scoring(snapshot, items=items, engine=runtime_engine())
    assert result["total"] is None
    assert result["ranking_eligible"] is False
    assert result["status"] == "infrastructure_incomplete"


def test_duplicate_verifier_items_rejected(tmp_path):
    snapshot, items = fixture(tmp_path)
    with pytest.raises(ValueError, match="duplicate"):
        finish_scoring(snapshot, items=items + [items[0]], engine=runtime_engine())


def test_duplicate_runtime_ids_rejected(tmp_path):
    snapshot, items = fixture(tmp_path)
    with pytest.raises(ValueError, match="positive run"):
        finish_scoring(snapshot, items=items, engine=runtime_engine([run("witness")]))


def test_mechanic_evidence_cannot_borrow_an_unrelated_run(tmp_path):
    snapshot, items = fixture(tmp_path)
    items.append({"id": "unity_mechanic_trace", "verdict": "passed", "evidence": {
        "runs": [{"run_id": "not-a-measured-run", "reached": ["damage"]}]}})
    result = finish_scoring(snapshot, items=items, engine=runtime_engine())
    assert criterion(result, "mechanics.core_mechanics")["points"] == 0


def test_duplicate_scene_claims_do_not_fill_level_structure(tmp_path):
    from test_mode5_evidence import scene
    project, baseline = tmp_path / "project", tmp_path / "baseline"
    scene(project)
    baseline.mkdir()
    source = rubric()
    source["min_levels"] = 3
    snapshot = snapshot_static(project, baseline=baseline, rubric=source,
                               interface={"entry_scene": "Assets/Entry.unity",
                                          "levels": [{"scene": "Assets/Entry.unity"}] * 3},
                               ops_valid=False, ops_nonidle=False)
    ids = [row["obligation"] for row in snapshot["observations"]["structure.scenes_entities"]]
    assert ids == ["level:1"]


def test_invalid_image_suffix_is_not_verified_visual_asset(tmp_path):
    from evalsys.taskgen.mode5.adapter import _valid_visual_asset
    from evalsys.taskgen.mode5.evidence import Graph, Limits
    fake = tmp_path / "fake.png"
    fake.write_bytes(b"not an image")
    assert not _valid_visual_asset(fake, Graph(), "fake.png", Limits())


def test_valid_image_can_be_checked_without_unity(tmp_path):
    from PIL import Image
    from evalsys.taskgen.mode5.adapter import _valid_visual_asset
    from evalsys.taskgen.mode5.evidence import Graph, Limits
    path = tmp_path / "real.png"
    Image.new("RGB", (1, 1)).save(path)
    assert _valid_visual_asset(path, Graph(), "real.png", Limits())


def test_reference_unmeasurable_check_not_removed():
    source = rubric()
    source["mechanic_checks"].append({"id": "unavailable", "measurable": False})
    assert frozen_obligations(source)["mechanics.core_mechanics"] == ["damage", "unavailable"]


def test_partial_verified_coverage_is_explicit_and_bounded():
    expected = frozen_obligations(rubric())
    result = score_evidence(expected, {"stability.lifecycle": [Observation(
        "cold_start", "runtime_verified", ("controller/runs:1-of-2",), coverage=.5,
    )]})
    assert criterion(result, "stability.lifecycle")["points"] == .5
    for coverage in (True, float("nan"), -1, 2):
        with pytest.raises(ValueError, match="coverage"):
            score_evidence(expected, {"stability.lifecycle": [Observation(
                "cold_start", "runtime_verified", ("controller/run",), coverage=coverage,
            )]})
