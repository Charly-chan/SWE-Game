import json

from evalsys.taskgen.matrix import AGENT_DRY_RUN_EXIT, CaseResult, _write_matrix_summary


def test_preview_does_not_count_as_an_attempt_but_signal_exit_does(tmp_path):
    cases = []
    for game, status, code in (
        ("preview", "generated", AGENT_DRY_RUN_EXIT),
        ("not_started", "generated", None),
        ("signal_exit", "agent_failed", -1),
        ("real_run", "completed", 0),
    ):
        root = tmp_path / game
        (root / "package").mkdir(parents=True)
        (root / "package/manifest.json").write_text(json.dumps({"blockers": []}))
        cases.append(CaseResult(game_id=game, mode="brief", root=root,
                                status=status, agent_exit_code=code))
    summary = _write_matrix_summary(tmp_path, cases, total=4)
    assert summary["denominators"]["material_ready_cells"] == 4
    assert summary["denominators"]["agent_attempted_cells"] == 2
    assert summary["game_mode_denominators"]["agent_attempted_game_modes"] == 2
    assert summary["per_mode"]["brief"]["agent_attempted_cells"] == 2
    assert summary["per_mode"]["brief"]["agent_attempted_case_attempts"] == 2
