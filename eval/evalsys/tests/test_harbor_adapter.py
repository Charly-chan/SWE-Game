import asyncio
import json
from pathlib import Path
import sys
from types import SimpleNamespace

import pytest

from evalsys.harbor.adapter import export_task, grade, result_record, summarize
from evalsys.taskgen.package import TaskPackage, write_json


def package(tmp_path, mode="gdd"):
    root = tmp_path / "package"
    (root / "visible").mkdir(parents=True)
    (root / "hidden").mkdir()
    (root / "visible/PROMPT.md").write_text("Make a game.")
    write_json(root / "hidden/oracle.json", {
        "route": str(root / "hidden/route.json"),
        "source": "/corpus/games/example",
        "answer": "evaluator-only-answer",
    })
    pkg = TaskPackage(root, {"schema_version": 1, "mode": mode,
                             "game_id": "example", "blockers": []})
    pkg.write_manifest()
    return root


@pytest.mark.parametrize("mode", ["brief", "gdd", "skeleton", "bugfix", "port"])
def test_export_only_visible_and_valid_harbor_task(tmp_path, mode):
    task_class = pytest.importorskip("harbor.models.task.task").Task
    source = package(tmp_path, mode)
    task_dir = export_task(source, tmp_path / "task")
    task = task_class(task_dir)
    assert task.config.metadata["mode"] == mode
    assert task.config.verifier.environment_mode.value == "separate"
    assert task.config.verifier.environment.docker_image
    assert task.config.agent.timeout_sec == 1800
    assert not (task_dir / "environment/hidden").exists()
    assert not (task_dir / "environment/evaluator").exists()
    for path in (task_dir / "environment").rglob("*"):
        if path.is_file():
            assert b"evaluator-only-answer" not in path.read_bytes()
    oracle = json.loads((task_dir / "evaluator/package/hidden/oracle.json").read_text())
    assert oracle["route"] == str(task_dir / "evaluator/package/hidden/route.json")
    assert oracle["source"] == "/corpus/games/example"
    original = json.loads((source / "hidden/oracle.json").read_text())
    assert original["route"] == str(source / "hidden/route.json")


def test_blocked_package_is_not_exported(tmp_path):
    source = package(tmp_path)
    pkg = TaskPackage.read(source)
    pkg.manifest["blockers"] = ["missing reference video"]
    pkg.write_manifest()
    with pytest.raises(ValueError, match="blocked"):
        export_task(source, tmp_path / "task")
    assert not (tmp_path / "task").exists()


def report(score, eligible=True, mode="gdd", registry="evidence1", resolved=None):
    return {"mode": mode, "game_id": "example", "score": 99,
            "headline": {"score": score, "status": "scored", "scale": "0-100",
                         "ranking_eligible": eligible, "score_scope": "complete"},
            "resolved": resolved, "scorecard": {"registry_version": registry}}


def test_headline_zero_and_unscored_are_distinct():
    assert result_record(report(0))["rewards"] == {"swe_game_score": 0}
    assert result_record(report(20))["rewards"] == {"swe_game_score": 20}
    assert result_record(report(20, eligible=False))["rewards"] is None
    assert result_record(report(None))["rewards"] is None
    assert result_record(report(20, resolved=False))["resolved"] is False


def test_missing_project_is_contract_failure(tmp_path):
    source = package(tmp_path)
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    record = grade(source, workspace, tmp_path / "out")
    assert record["status"] == "submission_contract_failed"
    assert record["rewards"] is None
    assert not (tmp_path / "out/report.json").exists()


def test_mode5_collects_for_vm_without_linux_grading(tmp_path, monkeypatch):
    source = package(tmp_path, "port")
    workspace = tmp_path / "workspace/submission"
    for name in ("Assets", "Packages", "ProjectSettings"):
        (workspace / name).mkdir(parents=True)
    (workspace / "ProjectSettings/ProjectVersion.txt").write_text("m_EditorVersion: 6000.3.23f1\n")
    monkeypatch.setattr("evalsys.taskgen.evaluate.evaluate_task",
                        lambda *a, **kw: pytest.fail("Linux Mode-5 grading called"))
    result = grade(source, workspace.parent, tmp_path / "out")
    assert result["status"] == "deferred"
    assert result["reason"] == "certified_unity_vm_required"
    assert result["rewards"] is None
    assert (Path(result["submission"]) / "ProjectSettings/ProjectVersion.txt").exists()


def test_summary_preserves_denominators_and_groups(tmp_path):
    examples = [(report(20), "model-a"), (report(0), "model-a"),
                (report(80, eligible=False), "model-a"),
                (report(90, mode="bugfix"), "model-a"),
                (report(70, registry="redesign"), "model-a"),
                (report(60), "model-b")]
    for index, (data, model) in enumerate(examples):
        trial = tmp_path / f"trial-{index}"
        write_json(trial / "result.json", {
            "agent_info": {"name": "codex", "model_info": {"name": model}},
            "exception_info": None,
        })
        write_json(trial / "verifier/swe-game.json", result_record(data))
    summary = summarize(tmp_path)
    assert len(summary["groups"]) == 4
    group = next(g for g in summary["groups"] if g["trials"] == 3)
    assert group["scored"] == 2 and group["unscored"] == 1
    assert group["mean_score"] == 10
    assert group["resolution_measured"] == 0


def test_summary_averages_attempts_cases_then_games(tmp_path):
    for index, (game, case, score) in enumerate([
        ("g1", "c1", 20), ("g1", "c1", 40),
        ("g1", "c2", 80), ("g2", "c1", 90),
    ]):
        trial = tmp_path / f"trial-{index}"
        write_json(trial / "result.json", {"agent_info": {"name": "oracle"}})
        record = result_record(report(score, mode="bugfix"))
        record.update(game_id=game, case_id=case)
        write_json(trial / "verifier/swe-game.json", record)
    group = summarize(tmp_path)["groups"][0]
    assert group["game_scores"] == {"g1": 55, "g2": 90}
    assert group["mean_score"] == 72.5
    assert group["mean_trial_score"] == 57.5


def test_harness_error_stays_in_group_coverage(tmp_path):
    task = tmp_path / "exported-task"
    write_json(task / "evaluator/package/manifest.json", {
        "game_id": "example", "mode": "gdd",
    })
    config = {"task": {"path": str(task)},
              "verifier": {"kwargs": {"registry_version": "evidence1"}}}
    for name in ("ok", "error"):
        write_json(tmp_path / name / "result.json", {
            "config": config, "agent_info": {"name": "oracle"},
            "exception_info": {"exception_type": "BuildError"} if name == "error" else None,
        })
    write_json(tmp_path / "ok/verifier/swe-game.json", result_record(report(20)))
    result = summarize(tmp_path)
    assert len(result["groups"]) == 1
    group = result["groups"][0]
    assert group["trials"] == 2 and group["scored"] == 1
    assert group["mean_score"] == 20
    assert group["harness_errors"] == 1


def test_run_surfaces_harbor_trial_errors(tmp_path, monkeypatch):
    from evalsys.harbor import __main__ as cli

    def harbor_returned_zero(command):
        write_json(tmp_path / "jobs/pilot/trial/result.json", {
            "agent_info": {"name": "oracle"},
            "exception_info": {"exception_type": "BuildError"},
        })
        return 0

    monkeypatch.setattr(cli, "version", lambda name: "0.23.0")
    monkeypatch.setattr(cli.subprocess, "call", harbor_returned_zero)
    monkeypatch.setattr(sys, "argv", [
        "run_harbor", "run", "--path", str(tmp_path / "task"),
        "--jobs-dir", str(tmp_path / "jobs"), "--job-name", "pilot",
        "--", "--agent", "oracle",
    ])
    assert cli.main() == 1
    assert (tmp_path / "jobs/pilot/swe-game-summary.json").exists()


@pytest.mark.skipif(sys.platform != "linux", reason="Linux verifier subprocess lifecycle")
def test_verifier_timeout_stops_evaluator_process_group(tmp_path, monkeypatch):
    verifier_module = pytest.importorskip("evalsys.harbor.verifier")
    verifier = object.__new__(verifier_module.SWEGameVerifier)
    verifier.trial_paths = SimpleNamespace(verifier_dir=tmp_path)
    verifier.task = SimpleNamespace(task_dir=tmp_path, config=SimpleNamespace(
        metadata={"mode": "gdd"}, verifier=SimpleNamespace(
            environment_mode=SimpleNamespace(value="separate"))))
    verifier.engine, verifier.visual_judge = "on", "none"
    verifier.registry_version, verifier.collect_only = None, False

    class Environment:
        async def download_dir(self, source, target):
            target.mkdir()

    verifier.environment = Environment()
    create = asyncio.create_subprocess_exec
    heartbeat = tmp_path / "heartbeat"
    child = ("from pathlib import Path; import time\n"
             f"p=Path({str(heartbeat)!r})\n"
             "while True:\n p.write_text(str(time.time())); time.sleep(.02)\n")
    parent = ("import subprocess,sys,time; "
              f"subprocess.Popen([sys.executable,'-c',{child!r}]); time.sleep(60)")

    async def substitute(*args, **kwargs):
        return await create(sys.executable, "-c", parent, **kwargs)

    monkeypatch.setattr(asyncio, "create_subprocess_exec", substitute)

    async def exercise():
        task = asyncio.create_task(verifier.verify())
        for _ in range(100):
            if heartbeat.exists():
                break
            await asyncio.sleep(.02)
        assert heartbeat.exists()
        task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await task
        last = heartbeat.read_text()
        await asyncio.sleep(.15)
        assert heartbeat.read_text() == last

    asyncio.run(exercise())
    assert json.loads((tmp_path / "swe-game.json").read_text())["rewards"] is None
