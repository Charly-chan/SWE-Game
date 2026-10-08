
import json
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

import pytest

from evalsys.taskgen.submission import Submission, parse_demos, load_submission
from evalsys.taskgen.demonstrations import (
    summarize_demonstrations, demonstration_items, run_demonstrations,
)
from evalsys.taskgen.evaluate import _ops_items, BEHAVIOR_ITEM_IDS
from evalsys.taskgen.modes import parse_mode
from evalsys.scard.replay import ReplayFilm, build_replay_prompt
from evalsys.verdict import Verdict
from evalsys.taskgen.matrix import collect_submission
from _interface_fixture import write_conformant_project
from evalsys.interface.model import AnalogAxis


def rubric():
    return {"mechanic_checks": [
        {"id": name, "observable": {"kind": "trace_checkpoint", "predicate": predicate}}
        for name, predicate in (("pickup", "count_delta(gb_collectible) < 0"),
                                ("progress", "numeric_delta(progress) > 0"))
    ]}


def tape():
    return [{"op": "hold", "action": "gb_right", "frames": 20}]


def demo_play(name, observed, null=(), stop="ops_exhausted", flagged=None):
    def reading(checks, reason=stop):
        return {"observations_reached": list(checks), "stop_reason": reason, "segment_clean": True}
    return {"id": name, "description": name, "play": {
        "ran": True, "ops_env": reading(observed), "matched_null": reading(null),
        "ops_flagged": reading(observed if flagged is None else flagged),
    }}


def test_parse_load_and_collection_preserve_demos(tmp_path):
    root = tmp_path / "workspace" / "submission"
    project = write_conformant_project(root / "game")
    path = root / "demos.json"
    path.write_text(json.dumps({"schema_version": 1, "demos": [{"id": "pickup", "ops": tape()}]}))
    sub = load_submission(root)
    assert sub.ops_path is None and len(sub.demos) == 1
    assert all(item.verdict is Verdict.PASSED for item in _ops_items(sub))
    collected = collect_submission(root.parent, tmp_path / "collected", mode="gdd")
    assert (collected / "demos.json").read_text() == path.read_text()


@pytest.mark.parametrize("rows", [[], [{"id": "bad", "ops": [{"op": "wait", "frames": 2}]}],
    [{"id": "bad", "ops": tape(), "state": {"score": 10}}],
    [{"id": "bad", "ops": tape(), "start": "secret_scene"}],
    [{"id": "same", "ops": tape()}, {"id": "same", "ops": tape()}],
    [{"id": "bad", "ops": [{"op": "tap", "action": "gb_reset", "frames": 1}]}]])
def test_bad_demos_refused(tmp_path, rows):
    path = tmp_path / "demos.json"
    path.write_text(json.dumps({"schema_version": 1, "demos": rows}))
    with pytest.raises(ValueError):
        parse_demos(path)


def test_independent_partial_coverage_and_no_duplicate_credit():
    first = demo_play("first", ["pickup"])
    summary = summarize_demonstrations({"demonstrations": [first, first]}, rubric())
    assert summary["causal_coverage"] == .5
    assert summary["segments"][0]["score"] == 50
    items = demonstration_items(summary)
    assert items[0].credit == .5 and items[1].credit == .5
    assert items[2].verdict is Verdict.FAILED
    assert "demonstrations_complete" in BEHAVIOR_ITEM_IDS


def test_complementary_segments_need_no_whole_game_clear():
    summary = summarize_demonstrations({"demonstrations": [
        demo_play("pickup", ["pickup"]), demo_play("next-room", ["progress"]),
    ]}, rubric())
    assert summary["complete"] and summary["causal_coverage"] == 1
    assert all(i.verdict is Verdict.PASSED for i in demonstration_items(summary))


def test_no_input_feature_is_not_causal():
    summary = summarize_demonstrations({"demonstrations": [
        demo_play("timed", ["pickup", "progress"], null=["progress"]),
    ]}, rubric())
    assert summary["coverage"] == 1 and summary["causal_coverage"] == .5
    assert summary["not_action_caused"] == ["progress"]

    assert summary["complete"]


def test_all_effects_without_input_do_not_complete_contract():
    summary = summarize_demonstrations({"demonstrations": [
        demo_play("timed", ["pickup", "progress"], null=["pickup", "progress"]),
    ]}, rubric())
    assert summary["coverage"] == 1 and summary["causal_coverage"] == 0
    assert not summary["complete"]


def test_timeout_is_not_a_zero_score():
    summary = summarize_demonstrations({"demonstrations": [
        demo_play("timeout", [], stop="timeout"),
    ]}, rubric())
    assert not summary["measured"]
    assert summary["segments"][0]["score"] is None
    assert all(i.verdict is Verdict.INCONCLUSIVE for i in demonstration_items(summary))


def test_every_segment_receives_all_task_checks_and_own_tape():
    demos = [{"id": "one", "description": "one", "ops": [object()]},
             {"id": "two", "description": "two", "ops": [object()]}]
    with patch("evalsys.taskgen.demonstrations.run_self_play", return_value={"ran": True}) as run:
        result = run_demonstrations(Path("game"), mode=parse_mode("gdd"), demos=demos,
                                    interface=SimpleNamespace(), rubric=rubric())
    assert result["ran"] and run.call_count == 2
    assert run.call_args_list[0].kwargs["ops"] is demos[0]["ops"]
    assert run.call_args_list[1].kwargs["ops"] is demos[1]["ops"]
    assert all(len(c.kwargs["milestones"]) == 2 for c in run.call_args_list)


def test_feature_video_does_not_require_ending():
    prompt = build_replay_prompt(ReplayFilm(directory="demo"), project="demo", full_frames=[],
                                 context={"feature_demo": True, "description": "jump"})
    ids = [c["id"] for c in prompt["criteria"]]
    assert "ending_shown" not in ids and "progression_visible" not in ids
    assert "feature_visible" in ids
    assert "NOT a full-game clear" in prompt["scope"]
    assert "JSON object" in prompt["task"]


def test_partial_demos_are_in_strict_failure_report(tmp_path):
    from evalsys.taskgen.evaluate import TaskEvalResult
    from evalsys.taskgen.package import TaskPackage
    from evalsys.taskgen.content.verifier_profiles import verifier_profile
    from evalsys.verdict import passed, failed
    package = TaskPackage(tmp_path, {"mode": "gdd", "game_id": "fixture"})
    sub = Submission(tmp_path, tmp_path, demos_path=tmp_path / "demos.json")
    items = [passed(item_id) for item_id in verifier_profile("gdd").strict_ids]
    items.append(failed("demonstrations_complete", detail="one feature missing"))
    result = TaskEvalResult(package, sub, items=items).to_dict()
    assert result["resolved"] is False
    assert "demonstrations_complete" in result["verifier_profile"]["behavior_ids"]
    assert result["resolution"]["failing_strict_items"]
    assert result["scorecard"]["strict"]["failing_items"]


def test_axis_only_feature_is_player_input_and_uses_declared_grid(tmp_path):
    axis = AnalogAxis("look_y", "continuous camera pitch", -1, 1, 17)
    path = tmp_path / "demos.json"
    path.write_text(json.dumps({"schema_version": 1, "demos": [{
        "id": "look-around", "ops": [{"op": "state", "frames": 30, "axes": {"look_y": .53}}],
    }]}))
    demos = parse_demos(path, extra_axes=(axis,))
    assert demos[0]["ops"][0].axes == (("look_y", .5),)
    with pytest.raises(ValueError, match="outside the frozen analog axes"):
        parse_demos(path)


def test_load_submission_passes_interface_axes_to_both_protocols(tmp_path):
    root = write_conformant_project(tmp_path / "submission")
    ops = [{"op": "state", "frames": 60, "axes": {"look_y": .5}}]
    (root / "ops.json").write_text(json.dumps({"ops": ops}))
    (root / "demos.json").write_text(json.dumps({"schema_version": 1, "demos": [{"id": "look", "ops": ops}]}))
    interface = SimpleNamespace(extended_action_ids=(), analog_axes=(AnalogAxis("look_y", "continuous pitch", -1, 1, 17),))
    with patch("evalsys.interface.load_submission_interface", return_value=interface):
        sub = load_submission(root)
    assert not sub.ops_error and not sub.demos_error
    assert sub.ops[0].axes == sub.demos[0]["ops"][0].axes == (("look_y", .5),)
    assert all(item.verdict is Verdict.PASSED for item in _ops_items(sub))


def test_demo_axis_value_outside_declared_range_is_refused(tmp_path):
    path = tmp_path / "demos.json"
    path.write_text(json.dumps({"schema_version": 1, "demos": [{
        "id": "look", "ops": [{"op": "state", "frames": 30, "axes": {"look_y": 2}}],
    }]}))
    with pytest.raises(ValueError, match="outside a declared axis range"):
        parse_demos(path, extra_axes=(AnalogAxis("look_y", "continuous pitch", -1, 1, 17),))
