
from pathlib import Path

import pytest

from evalsys.interface import load_submission_interface
from evalsys.routes.agent import Op
from evalsys.routes.schema import Milestone
from evalsys.taskgen.engine import godot_available, isolated_taskgen_scratch, run_self_play
from evalsys.taskgen.modes import parse_mode


@pytest.mark.skipif(godot_available() is None, reason="Godot engine not installed")
def test_failure_retry_runs_only_for_feature_protocol():
    project = Path(__file__).parent / "fixtures/feature_retry"
    interface = load_submission_interface(project)
    ops = [Op(op="tap", action="gb_action", frames=4), Op(op="wait", frames=20),
           Op(op="tap", action="gb_jump", frames=4), Op(op="wait", frames=20),
           Op(op="tap", action="gb_right", frames=4), Op(op="wait", frames=10)]
    kwargs = dict(mode=parse_mode("gdd"), ops=ops, predicate="whole_game_clear()",
                  interface=interface, milestones=[Milestone("play_after_retry", "numeric_delta(score) > 0")])
    with isolated_taskgen_scratch("test-feature-retry"):
        legacy = run_self_play(project, **kwargs)
        demo = run_self_play(project, **kwargs, feature_demo=True)
    assert legacy["ops_env"]["stop_reason"] == "wrong_ending"
    assert "play_after_retry" not in legacy["ops_env"]["observations_reached"]
    assert demo["ops_env"]["segment_clean"]
    assert demo["ops_env"]["stop_reason"] != "wrong_ending"
    assert "play_after_retry" in demo["ops_env"]["observations_reached"]
    assert "play_after_retry" not in demo["matched_null"]["observations_reached"]
