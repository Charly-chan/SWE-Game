from __future__ import annotations

import json
import os
from pathlib import Path
import subprocess
import sys
from types import SimpleNamespace
from unittest.mock import patch

import pytest

from evalsys.taskgen.content.verifier_profiles import BRIEF_DESIGN_ITEMS, verifier_profile
from evalsys.taskgen.evaluate import TaskEvalResult, evaluate_task
from evalsys.taskgen.scorecard import (
    MODE1_VLM_REGISTRY_VERSION, MODE1_REDESIGN_WEIGHTS, score_task_result,
)
from evalsys.verdict import failed, inconclusive, passed
from test_game_visual import reading
from test_mode1_redesign_scorecard import _result


ROOT = Path(__file__).resolve().parents[3]


@pytest.mark.parametrize("replacement", [failed, inconclusive, None])
def test_disabled_design_cannot_earn_points_or_create_missing_axes(replacement):
    result = _result(qwen_like=False)
    result.items.append(reading(.8))
    enabled = score_task_result(result, brief_design=True)
    disabled = score_task_result(result, brief_design=False)
    assert sum(row["weight_in_total"] for row in disabled["axes"]) == 100
    assert sum(row["weight_in_total"] for row in disabled["axes"] if row["id"] != "task_visual") == 85
    assert disabled["visual_total"] == enabled["visual_total"]
    assert disabled["visual_total"]["score"] == 12
    assert not any(row["id"] == "authored_design" for row in disabled["categories"])
    assert {"gdd_quality", "gdd_interface"} <= set(disabled["not_applicable_criteria"])
    expected = sum(
        row["credit"] * weight
        for row in enabled["axes"]
        for axis, weight in {
            "universal_mechanics": 6.25, "asset_realization": 22.5,
            "rubric_interface": 8.75, "numeric_contract": 5,
            "content_census": 12.5, "causal_witness": 7.5,
            "task_checkpoints": 11.25, "hidden_scenarios": 5,
            "demo_validity": 3.75, "demo_coverage": 2.5,
        }.items() if row["id"] == axis
    )
    assert disabled["objective_total"]["score"] == pytest.approx(expected, abs=.001)
    original_points = disabled["objective_total"]
    result.items = [
        replacement(item.id) if item.id in BRIEF_DESIGN_ITEMS else item
        for item in result.items if replacement is not None or item.id not in BRIEF_DESIGN_ITEMS
    ]
    after = score_task_result(result, brief_design=False)
    assert after["objective_total"] == original_points
    assert after["weighted_total"] == disabled["weighted_total"]
    assert not any("authored_gdd" in row["id"] for row in after["axes"])


def test_design_on_retains_previous_scores_and_weights():
    result = _result(qwen_like=False)
    result.items.append(reading(.8))
    original = score_task_result(result)
    enabled = score_task_result(result, brief_design=True)
    assert enabled == original
    axes = {row["id"]: row for row in enabled["axes"]}
    assert axes["gdd_quality"]["earned_points"] == MODE1_REDESIGN_WEIGHTS["gdd_quality"]
    assert axes["gdd_interface"]["earned_points"] == MODE1_REDESIGN_WEIGHTS["gdd_interface"]
    result.items = [failed(item.id) if item.id in BRIEF_DESIGN_ITEMS else item for item in result.items]
    failed_design = score_task_result(result, brief_design=True)
    assert enabled["objective_total"]["score"] - failed_design["objective_total"]["score"] == pytest.approx(2.428)


def _diagnostic_result(tmp_path, enabled):
    profile = verifier_profile("brief")
    items = [
        failed(name) if name in BRIEF_DESIGN_ITEMS else passed(name)
        for name in sorted(profile.strict_ids)
    ]
    return TaskEvalResult(
        SimpleNamespace(root=tmp_path / "package", manifest={"mode": "brief", "game_id": "fixture"}),
        SimpleNamespace(root=tmp_path / "submission", demos_path=None),
        items=items, brief_design=enabled,
    )


def test_disabled_design_does_not_fail_eligibility_but_gdd_is_still_required(tmp_path):
    result = _diagnostic_result(tmp_path, False)
    wire = result.to_dict()
    assert result.resolved is True
    assert wire["brief_design"] is wire["scorecard"]["brief_design"] is False
    assert wire["eligibility"]["status"] == "passed"
    assert not BRIEF_DESIGN_ITEMS.intersection(wire["verifier_profile"]["eligibility_ids"])
    assert not any(row["id"] in BRIEF_DESIGN_ITEMS for row in wire["resolution"]["failing_strict_items"])
    assert BRIEF_DESIGN_ITEMS <= {row["id"] for row in wire["items"]}
    result.brief_design = True
    assert result.resolved is False
    result.brief_design = False
    result.items = [failed("gdd") if item.id == "gdd" else item for item in result.items]
    assert result.resolved is False


@pytest.mark.parametrize("choice", [None, False, True])
def test_new_evaluation_defaults_off_and_persists_explicit_choice(tmp_path, choice):
    from evalsys.taskgen.package import TaskPackage
    from _interface_fixture import write_conformant_project

    root = tmp_path / "package"
    (root / "visible").mkdir(parents=True)
    (root / "hidden").mkdir()
    (root / "visible/PROMPT.md").write_text("Build a game.")
    (root / "hidden/oracle.json").write_text("{}")
    package = TaskPackage(root, {"schema_version": 1, "mode": "brief", "game_id": "fixture"})
    package.write_manifest()
    sub = write_conformant_project(tmp_path / "sub")
    (sub / "GDD.md").write_text("# Game\nMove to the goal.")
    (sub / "ops.json").write_text(json.dumps({"ops": [{"op": "hold", "action": "gb_right", "frames": 30}]}))
    result = evaluate_task(root, sub, engine="off", brief_design=choice, out=tmp_path / "result")
    expected = choice is True
    assert result.brief_design is expected
    wire = json.loads((tmp_path / "result/report.json").read_text())
    assert wire["brief_design"] is wire["scorecard"]["brief_design"] is expected
    assert f"Brief Design scoring: {'on' if expected else 'off'}" in (tmp_path / "result/report.md").read_text()


@pytest.mark.parametrize("stored,expected", [({}, True), ({"brief_design": False}, False),
                                          ({"scorecard": {"brief_design": False}}, False)])
def test_rescoring_preserves_saved_or_historical_design_setting(stored, expected):
    result = _result(qwen_like=False)
    result.stored_report = stored
    assert score_task_result(result)["brief_design"] is expected
    assert score_task_result(result, brief_design=not expected)["brief_design"] is not expected


def test_rescore_write_updates_setting_headline_and_eligibility(tmp_path):
    source = tmp_path / "report.json"
    old = _diagnostic_result(tmp_path, True).to_dict()
    old["headline"]["score"] = 999
    source.write_text(json.dumps(old))
    original = source.read_bytes()
    command = [sys.executable, str(ROOT / "eval/tools/rescore.py"), str(source), "--write"]
    env = os.environ | {"PYTHONPATH": str(ROOT / "eval/evalsys")}
    completed = subprocess.run(command + ["--brief-design", "off"], env=env, capture_output=True, text=True)
    assert completed.returncode == 0, completed.stderr
    assert source.with_name("report.json.pre-rescore").read_bytes() == original
    new = json.loads(source.read_text())
    assert new["brief_design"] is new["scorecard"]["brief_design"] is False
    assert new["resolved"] is True and new["eligibility"]["status"] == "passed"
    assert new["headline"]["score"] == new["scorecard"]["weighted_total"]["score"]
    assert new["resolution"]["failing_strict_items"] == []
    completed = subprocess.run(command, env=env, capture_output=True, text=True)
    assert completed.returncode == 0, completed.stderr
    assert json.loads(source.read_text())["brief_design"] is False


def test_adding_visual_scores_preserves_design_off(tmp_path):
    from evalsys.taskgen.visual_rescore import rescore_visuals
    from test_visual_rescore import saved_report

    source, _film = saved_report(tmp_path)
    data = json.loads(source.read_text())
    data.update(mode="brief", brief_design=False, registry_version=MODE1_VLM_REGISTRY_VERSION)
    source.write_text(json.dumps(data))
    inputs = tmp_path / "demonstrations/visual_inputs.json"
    manifest = json.loads(inputs.read_text())
    manifest.update(game_rubric={"requirements": []}, game_id="fixture", mode="brief")
    inputs.write_text(json.dumps(manifest))
    with patch("evalsys.scard.game_visual.judge_game_visual", return_value=reading(.8)), \
         patch("evalsys.taskgen.visual_rescore.capture_task_visuals", side_effect=AssertionError("No engine rerun")):
        result = rescore_visuals(source, tmp_path / "new")
    assert result["brief_design"] is result["scorecard"]["brief_design"] is False
    assert result["scorecard"]["visual_total"]["score"] == 12


def test_matrix_resume_cannot_switch_design_setting(tmp_path):
    from evalsys.taskgen.matrix import AgentConfig, MatrixError, _write_or_validate_run

    cases = [SimpleNamespace(game_id="fixture", game_path=tmp_path,
                             mode=SimpleNamespace(id="brief"), case_id="")]
    kwargs = dict(agent=AgentConfig(), engine="off", visual_judge="none")
    _write_or_validate_run(tmp_path, cases, resume=False, brief_design=True, **kwargs)
    path = tmp_path / "run.json"
    saved = json.loads(path.read_text())
    assert saved["brief_design"] == "on"
    _write_or_validate_run(tmp_path, cases, resume=True, brief_design=True, **kwargs)
    with pytest.raises(MatrixError, match="configuration differs"):
        _write_or_validate_run(tmp_path, cases, resume=True, brief_design=False, **kwargs)
    saved.pop("brief_design")
    path.write_text(json.dumps(saved))
    _write_or_validate_run(tmp_path, cases, resume=True, brief_design=True, **kwargs)
    with pytest.raises(MatrixError, match="configuration differs"):
        _write_or_validate_run(tmp_path, cases, resume=True, brief_design=False, **kwargs)
