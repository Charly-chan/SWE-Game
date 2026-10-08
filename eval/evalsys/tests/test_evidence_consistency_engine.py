


import json
import shutil
from pathlib import Path

import pytest

from evalsys.interface import load_submission_interface
from evalsys.routes.agent import Op
from evalsys.taskgen.content.rubrics import rubric_milestones
from evalsys.taskgen.demonstrations import summarize_demonstrations
from evalsys.taskgen.engine import godot_available, isolated_taskgen_scratch, run_self_play
from evalsys.taskgen.modes import parse_mode


RUBRIC = {"mechanic_checks": [
    {"id": "clock", "observable": {"kind": "trace_checkpoint",
        "predicate": "numeric_delta(progress) > 0", "requires_player_action": False,
        "automatic_reason": "Time progresses during idle play."}},
    {"id": "action_score", "observable": {"kind": "trace_checkpoint",
        "predicate": "numeric_delta(score) > 0"}},
]}


def project_variant(root, variant):
    shutil.copytree(Path(__file__).parent / "fixtures/feature_retry", root)
    if variant == "alternative_correct":

        (root / "counter_store.gd").write_text(
            'extends RefCounted\nvar elapsed := 0.0\nvar points := 0\n'
            'func advance(dt: float, pressed: bool) -> void:\n'
            '    elapsed += dt\n    if pressed:\n        points += 1\n')
        script = (
            'extends Node2D\nvar ledger = preload("res://counter_store.gd").new()\n'
            'var run_distance: float:\n    get: return ledger.elapsed\n'
            'var earned_points: int:\n    get: return ledger.points\n'
            'func _physics_process(dt: float) -> void:\n'
            '    ledger.advance(dt, Input.is_action_just_pressed("gb_right"))\n')
        numeric = {"progress": "run_distance", "score": "earned_points"}
    else:
        clock = "    progress += dt\n" if variant != "partial" else ""
        action = ('    if Input.is_action_just_pressed("gb_right"):\n        score += 1\n'
                  if variant != "mutant" else "")
        script = ('extends Node2D\nvar progress := 0.0\nvar score := 0\n'
                  'func _physics_process(dt: float) -> void:\n' + clock + action)
        numeric = {"progress": "progress", "score": "score"}
    (root / "main.gd").write_text(script)
    manifest = json.loads((root / "gb_levels.json").read_text())
    manifest["numeric"] = numeric
    (root / "gb_levels.json").write_text(json.dumps(manifest))
    return root


@pytest.mark.skipif(godot_available() is None, reason="Godot engine not installed")
@pytest.mark.parametrize("variant,coverage,causal,complete", [
    ("reference", 1, 1, True), ("alternative_correct", 1, 1, True),
    ("mutant", .5, 0, False), ("partial", .5, 1, False),
])
def test_engine_evidence_is_independent_of_code_structure(tmp_path, variant, coverage, causal, complete):
    project = project_variant(tmp_path / variant, variant)
    with isolated_taskgen_scratch("test-evidence-" + variant):
        play = run_self_play(
            project, mode=parse_mode("gdd"),
            ops=[Op(op="wait", frames=5), Op(op="tap", action="gb_right", frames=4),
                 Op(op="wait", frames=10)],
            predicate="whole_game_clear()", interface=load_submission_interface(project),
            milestones=rubric_milestones(RUBRIC), feature_demo=True,
        )
    assert play["ran"], play
    assert play["ops_env"]["segment_clean"], play["ops_env"]
    summary = summarize_demonstrations({"demonstrations": [{
        "id": variant, "description": "Show the clock and score action", "play": play,
    }]}, RUBRIC)
    assert summary["measured"], summary
    assert summary["coverage"] == coverage, summary
    assert summary["causal_coverage"] == causal, summary
    assert summary["complete"] is complete, summary
