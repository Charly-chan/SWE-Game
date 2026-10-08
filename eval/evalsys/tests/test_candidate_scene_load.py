
from types import SimpleNamespace

import pytest

from evalsys.engine.scene_load import scene_load_failure
from evalsys.probe.runner import LevelRun, level_truth
from evalsys.routes.agent import NullAgent
from evalsys.routes.budget import derive
from evalsys.routes.runner import RouteSession, verdict_for
from evalsys.taskgen.engine import self_clear_route
from evalsys.taskgen.scorecard import score_task_result
from evalsys.taskgen.visual_materials import VisualEvidenceError, prepare_candidate_frames
from evalsys.verdict import Attribution, Verdict, inconclusive
from test_candidate_import_attribution import result_for
from test_progressive_redesign_scorecard import _axis


SCENE = "res://levels/level_01.tscn"
SCENE_ERROR = f"ERROR: Failed loading scene: {SCENE}."
LOG = (f"ERROR: {SCENE}:1 - Parse Error: Unrecognized file type 'gdscript'.\n"
       f"{SCENE_ERROR}\n   at: start (main/main.cpp:4560)\n")


@pytest.mark.parametrize("log,matched", [
    (LOG, True),
    ("ERROR: Failed loading scene: res://evaluator_driver.tscn.", False),
    ("ERROR: Failed loading resource: " + SCENE + ".", False),
    ("driver quit without a record", False),
])
def test_only_explicit_declared_scene_load_failure_matches(log, matched):
    assert bool(scene_load_failure(log, SCENE)) == matched


@pytest.mark.parametrize("timed_out,launch_failed,expected", [
    (False, "", "scene_load_failed"),
    (True, "", "no_record"),
    (False, "Godot did not launch", "no_record"),
])
def test_truth_retains_candidate_scene_failure_but_not_host_failures(timed_out, launch_failed, expected):
    reading = level_truth(LevelRun(declared_scene=SCENE, returncode=1, log_tail=LOG,
                                  timed_out=timed_out, launch_failed=launch_failed))
    assert not reading.reached
    assert reading.stop_reason == expected
    if expected == "scene_load_failed":
        assert SCENE_ERROR in reading.errors


@pytest.mark.parametrize("log,timed_out,expected", [
    (LOG, False, "scene_load_failed"),
    (LOG, True, "timeout"),
    ("driver exited without a report", False, "no_report"),
    ("ERROR: Failed loading scene: res://evaluator_driver.tscn.", False, "no_report"),
])
def test_route_producer_preserves_scene_failure_attribution(tmp_path, monkeypatch, log, timed_out, expected):
    interface = SimpleNamespace(project_root=tmp_path, provenance=lambda: {})
    session = RouteSession(tmp_path, interface=interface, scratch=tmp_path / "scratch",
                           io_root=tmp_path / "io")
    session.prep = SimpleNamespace(ok=True, path=tmp_path)
    monkeypatch.setattr(session, "level_scene", lambda level: SCENE)
    monkeypatch.setattr(session, "level_scenes", lambda: [SCENE])
    monkeypatch.setattr(session, "level_entry", lambda: {})
    monkeypatch.setattr(session, "ending_scenes", lambda: SimpleNamespace(
        success=(), failure=(), continuation=(), usable=False))
    monkeypatch.setattr(session, "_drive", lambda *args, **kwargs: (1, log, timed_out))
    route = self_clear_route(task_id="broken-scene", ops=[], predicate="whole_game_clear()", frames=120)
    budget = derive(5, 21.6, declared_steps=1, declared_frames=120)
    reading = session.run(route, NullAgent(), budget)
    assert reading.stop_reason == expected
    verdict = verdict_for(route, reading, 1.0)
    if expected == "scene_load_failed":
        assert verdict.attribution == Attribution.SUBMISSION
        assert SCENE_ERROR in reading.detail
    else:
        assert verdict.verdict == Verdict.INCONCLUSIVE


@pytest.mark.parametrize("mode", ["brief", "gdd", "skeleton"])
def test_scene_failure_scores_runtime_zeros_after_successful_cold_import(mode):
    result = result_for(mode, cold_status="passed")
    result.mode1_context["candidate_truth"] = {
        "levels": [level_truth(LevelRun(declared_scene=SCENE, log_tail=LOG)).to_dict()]}
    card = score_task_result(result)
    asset = _axis(card, "asset_realization")
    assert asset["credit"] == 0
    assert asset["status"] == "candidate_failure"
    assert asset["evidence"]["failure_code"] == "candidate_scene_load_failed"
    assert not any(row.get("axis") == "asset_realization" for row in card["evaluator_failures"])


@pytest.mark.parametrize("second_level", [
    {"stop_reason": "no_record", "reached": False},
    {"stop_reason": "", "reached": True},
])
def test_partial_scene_failure_does_not_explain_other_missing_probes(second_level):
    result = result_for("brief", cold_status="passed")
    result.mode1_context["candidate_truth"] = {"levels": [
        {"stop_reason": "scene_load_failed", "reached": False}, second_level]}
    card = score_task_result(result)
    assert not card["ranking_eligible"]
    assert _axis(card, "asset_realization")["status"] == "not_instrumented_zero"


def test_scene_failure_keeps_independent_static_and_visual_gaps():
    result = result_for("brief", cold_status="passed")
    result.mode1_context["candidate_truth"] = {"levels": [
        {"stop_reason": "scene_load_failed", "reached": False}]}
    result.items = [inconclusive(item.id, detail="independent evaluator request failed")
                    if item.id in {"authored_gdd_quality", "task_visual"} else item
                    for item in result.items]
    card = score_task_result(result)
    assert not card["ranking_eligible"]
    assert _axis(card, "gdd_quality")["status"] == "not_instrumented_zero"
    assert _axis(card, "task_visual")["credit"] is None


@pytest.mark.parametrize("stop_reason,owner,code", [
    ("scene_load_failed", "candidate", "candidate_scene_load_failed"),
    ("no_report", "evaluator", "recorder_failed"),
    ("timeout", "evaluator", "recorder_failed"),
])
def test_missing_film_uses_recorded_scene_failure_not_generic_recorder_error(tmp_path, stop_reason, owner, code):
    demo = {"id": "pickup_relic", "film": {
        "stop_reason": stop_reason, "error": "Movie Maker wrote no video", "mp4": ""}}
    with pytest.raises(VisualEvidenceError) as caught:
        prepare_candidate_frames([demo], tmp_path)
    assert caught.value.owner == owner
    assert caught.value.code == code
