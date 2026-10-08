import asyncio
import json
from pathlib import Path
import sys
from types import SimpleNamespace

import pytest

from evalsys.harbor.adapter import export_task, grade, result_record, summarize
from evalsys.taskgen.mode5.score import REGISTRY_VERSION as MODE5_REGISTRY_VERSION
from evalsys.taskgen.mode5.report import RANKING_SCOPE
from mode5_test_support import summary_reading
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
    kwargs = {}
    expected_timeout = 1800
    if mode == "port":
        state = tmp_path / "mode5-state"
        image_id = "sha256:" + "a" * 64
        write_json(state / "image-lock.json", {"agent_image": {"id": image_id}})
        write_json(state / "last-preflight.json", {"status": "pass", "image_ids": {"agent": image_id}})
        write_json(state / "license-provider.json", {"provider": "floating"})
        kwargs = {"mode5_state_dir": state}
        expected_timeout = 7200
    task_dir = export_task(source, tmp_path / "task", **kwargs)
    task = task_class(task_dir)
    assert task.config.metadata["mode"] == mode
    assert task.config.verifier.environment_mode.value == "separate"
    assert task.config.verifier.environment.docker_image
    assert task.config.agent.timeout_sec == expected_timeout
    assert not (task_dir / "environment/hidden").exists()
    assert not (task_dir / "environment/evaluator").exists()
    for path in (task_dir / "environment").rglob("*"):
        if path.is_file():
            assert b"evaluator-only-answer" not in path.read_bytes()
    oracle = json.loads(
        (task_dir / "evaluator/package/hidden/oracle.json").read_text(encoding="utf-8")
    )
    assert oracle["route"] == str(task_dir / "evaluator/package/hidden/route.json")
    assert oracle["source"] == "/corpus/games/example"
    original = json.loads(
        (source / "hidden/oracle.json").read_text(encoding="utf-8")
    )
    assert original["route"] == str(source / "hidden/route.json")


def test_blocked_package_is_not_exported(tmp_path):
    source = package(tmp_path)
    pkg = TaskPackage.read(source)
    pkg.manifest["blockers"] = ["missing reference video"]
    pkg.write_manifest()
    with pytest.raises(ValueError, match="blocked"):
        export_task(source, tmp_path / "task")
    assert not (tmp_path / "task").exists()


def test_community_export_uses_digest_pinned_local_agent_image(tmp_path):
    task_class = pytest.importorskip("harbor.models.task.task").Task
    source = package(tmp_path, "port")
    state = tmp_path / "state"
    image_id = "sha256:" + "a" * 64
    write_json(state / "image-lock.json", {
        "agent_image": {"tag": "mutable:ignored", "id": image_id},
    })
    write_json(state / "last-preflight.json", {
        "status": "pass", "image_ids": {"agent": image_id},
    })
    endpoint = "https://private-license.example:8080"
    write_json(state / "license-provider.json", {
        "provider": "floating", "endpoint": endpoint,
    })
    task = export_task(
        source, tmp_path / "task", mode5_profile="community-docker",
        mode5_state_dir=state,
    )
    assert not (task / "environment/Dockerfile").exists()
    assert (task / "environment/PROMPT.md").is_file()
    task_config = (task / "task.toml").read_text(encoding="utf-8")
    assert f'docker_image = "{image_id}"' in task_config
    assert "mutable:ignored" not in task_config
    assert endpoint not in task_config
    assert '${GB_UNITY_FLOATING_ENDPOINT}' in task_config
    assert 'environment_profile = "community-docker"' in task_config
    assert (task / "environment/.gamebench/unity-license-healthcheck.sh").is_file()
    parsed = task_class(task)
    assert parsed.config.environment.docker_image == image_id
    assert parsed.config.environment.env == {
        "UNITY_LICENSE_SERVER": "${GB_UNITY_FLOATING_ENDPOINT}",
    }
    assert parsed.config.environment.healthcheck.timeout_sec == 300
    assert parsed.config.verifier.environment_mode.value == "separate"


def report(score, eligible=True, mode="gdd", registry="evidence1", resolved=None):
    result = {"mode": mode, "game_id": "example", "score": 99,
              "headline": {"score": score, "status": "scored", "scale": "0-100",
                           "ranking_eligible": eligible, "score_scope": "complete"},
              "resolved": resolved, "scorecard": {"registry_version": registry}}
    if mode == "port":
        reading = summary_reading("shadow_walker", score or 0)
        reading.update(ranking_eligible=eligible and score is not None, total=score)
        result.update(game_id="shadow_walker", environment={
            "environment_class": "community-docker", "profile_id": "mode5-community-docker-v1"})
        result["headline"].update(ranking_scope=RANKING_SCOPE, score_scope="mode5_evidence_adjusted_proxy")
        result["scorecard"].update(registry_version=MODE5_REGISTRY_VERSION, evidence_score=reading)
    return result


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




def test_summary_never_mixes_mode5_image_digests_or_budget_configuration(tmp_path):
    for index, (image, timeout) in enumerate((("sha256:agent-a", 1800),
                                               ("sha256:agent-b", 3600))):
        trial = tmp_path / f"trial-{index}"
        write_json(trial / "result.json", {
            "agent_info": {"name": "codex", "model_info": {
                "name": "fixture", "provider": "openai",
            }},
            "config": {"agent": {"timeout_sec": timeout}},
        })
        data = report(50, mode="port")
        data["environment"] = {
            "environment_class": "community-docker",
            "profile_id": "mode5-community-docker-v1",
            "agent_image_id": image,
            "evaluator_image_id": "sha256:evaluator",
        }
        data["run_config"] = {
            "budget": {},
            "input": {"reference_video": True},
        }
        write_json(trial / "verifier/swe-game.json", result_record(data))
    groups = summarize(tmp_path)["groups"]
    assert len(groups) == 2
    assert {row["agent_image_id"] for row in groups} == {
        "sha256:agent-a", "sha256:agent-b",
    }
    assert {row["budget_configuration"]["agent_timeout_seconds"]
            for row in groups} == {1800, 3600}


def test_summary_never_mixes_community_and_certified_environments(tmp_path):
    for index, environment in enumerate((
        {"environment_class": "community-docker", "profile_id": "mode5-community-docker-v1"},
        {"environment_class": "linux-vm-certified", "profile_id": "paper-vm"},
    )):
        trial = tmp_path / f"trial-{index}"
        write_json(trial / "result.json", {"agent_info": {"name": "oracle"}})
        data = report(50, mode="port")
        data["environment"] = environment
        data["headline"]["ranking_scope"] = environment["profile_id"]
        write_json(trial / "verifier/swe-game.json", result_record(data))
    groups = summarize(tmp_path)["groups"]
    assert len(groups) == 2
    assert {row["environment_class"] for row in groups} == {
        "community-docker", "linux-vm-certified",
    }


@pytest.mark.parametrize("option", ["visual", "registry"])
def test_mode5_community_rejects_legacy_scoring_options(tmp_path, option):
    source = package(tmp_path, "port")
    kwargs = {"visual_judge": "local"} if option == "visual" else {
        "registry_version": "legacy-registry",
    }
    with pytest.raises(ValueError, match="fixed non-VLM|requires registry"):
        grade(
            source, tmp_path / "workspace", tmp_path / "out",
            mode5_profile="community-docker", **kwargs,
        )


def test_mode5_community_profile_calls_independent_docker_evaluator(tmp_path, monkeypatch):
    monkeypatch.setattr(
        "evalsys.taskgen.mode5.docker_license._validate_windows_acl", lambda path: None,
    )
    source = package(tmp_path, "port")
    workspace = tmp_path / "workspace/submission"
    for name in ("Assets", "Packages", "ProjectSettings"):
        (workspace / name).mkdir(parents=True)
    (workspace / "ProjectSettings/ProjectVersion.txt").write_text(
        "m_EditorVersion: 6000.3.23f1\n"
    )
    license_file = tmp_path / "license.ulf"
    license_file.write_text("fixture")
    license_file.chmod(0o600)
    state = tmp_path / "state"
    write_json(state / "image-lock.json", {
        "agent_image": {"tag": "agent:test", "id": "sha256:agent"},
        "evaluator_image": {"tag": "evaluator:test", "id": "sha256:evaluator"},
    })
    write_json(state / "last-preflight.json", {
        "status": "pass", "image_ids": {"evaluator": "sha256:evaluator"},
    })
    write_json(state / "license-provider.json", {
        "provider": "file", "source": str(license_file),
    })
    observed = {}

    def fake_evaluate(**kwargs):
        observed.update(kwargs)
        return {
            "mode": "port", "game_id": "example", "resolved": True,
            "headline": {"score": 50, "status": "scored", "scale": "0-100",
                         "ranking_eligible": True,
                         "score_scope": "mode5_evidence_adjusted_proxy",
                         "ranking_scope": RANKING_SCOPE},
            "scorecard": {"registry_version": MODE5_REGISTRY_VERSION,
                          "evidence_score": summary_reading("example", 50)},
            "environment": {"environment_class": "community-docker",
                            "profile_id": "mode5-community-docker-v1",
                            "paper_compatible": False},
        }

    monkeypatch.setattr(
        "evalsys.taskgen.mode5.docker_evaluator.evaluate_in_container", fake_evaluate,
    )
    result = grade(
        source, workspace.parent, tmp_path / "out",
        mode5_profile="community-docker", mode5_state_dir=state, docker="docker-test",
    )
    assert result["status"] == "scored"
    assert result["ranking_scope"] == RANKING_SCOPE
    assert result["environment"]["paper_compatible"] is False
    assert observed["submission"].name == "submission"
    assert observed["docker"].executable == "docker-test"


def test_mode5_rejects_unknown_profile_without_grading(tmp_path, monkeypatch):
    source = package(tmp_path, "port")
    monkeypatch.setattr("evalsys.taskgen.evaluate.evaluate_task",
                        lambda *a, **kw: pytest.fail("Mode 5 grading called"))
    with pytest.raises(ValueError, match="mode5_profile"):
        grade(source, tmp_path / "workspace", tmp_path / "out",
              mode5_profile="unknown")


def test_summary_never_mixes_community_environment_profiles(tmp_path):
    for index, environment in enumerate((
        {"environment_class": "community-docker", "profile_id": "community-a"},
        {"environment_class": "community-docker", "profile_id": "community-b"},
    )):
        trial = tmp_path / f"trial-{index}"
        write_json(trial / "result.json", {"agent_info": {"name": "oracle"}})
        data = report(50, mode="port")
        data["environment"] = environment
        data["headline"]["ranking_scope"] = environment["profile_id"]
        write_json(trial / "verifier/swe-game.json", result_record(data))
    groups = summarize(tmp_path)["groups"]
    assert len(groups) == 2
    assert {row["environment_profile"] for row in groups} == {"community-a", "community-b"}


def test_summary_never_mixes_mode5_image_digests_or_budget_configuration(tmp_path):
    for index, (image, timeout) in enumerate((("sha256:agent-a", 1800),
                                               ("sha256:agent-b", 3600))):
        trial = tmp_path / f"trial-{index}"
        write_json(trial / "result.json", {
            "agent_info": {"name": "codex", "model_info": {
                "name": "fixture", "provider": "openai",
            }},
            "config": {"agent": {"timeout_sec": timeout}},
        })
        data = report(50, mode="port")
        data["environment"] = {
            "environment_class": "community-docker",
            "profile_id": "mode5-community-docker-v1",
            "agent_image_id": image,
            "evaluator_image_id": "sha256:evaluator",
        }
        data["run_config"] = {
            "budget": {},
            "input": {"reference_video": True},
        }
        write_json(trial / "verifier/swe-game.json", result_record(data))
    groups = summarize(tmp_path)["groups"]
    assert len(groups) == 2
    assert {row["agent_image_id"] for row in groups} == {
        "sha256:agent-a", "sha256:agent-b",
    }
    assert {row["budget_configuration"]["agent_timeout_seconds"]
            for row in groups} == {1800, 3600}


def test_mode5_harbor_shares_catalog_mean_and_tail_diagnostics(tmp_path, monkeypatch):
    monkeypatch.setattr("evalsys.taskgen.mode5.community_cli._catalog_game_ids", lambda: ["a", "b", "c", "d"])
    for index, (game, score) in enumerate([("a", 20), ("a", 40), ("b", 50), ("c", 60), ("d", 80)]):
        data = report(score, mode="port")
        data["game_id"] = game
        data["scorecard"]["evidence_score"] = summary_reading(game, score)
        trial = tmp_path / f"trial-{index}"
        write_json(trial / "result.json", {"agent_info": {"name": "oracle"}})
        write_json(trial / "verifier/swe-game.json", result_record(data))
    group = summarize(tmp_path)["groups"][0]
    assert group["mean_score"] == group["total"] == 55
    assert group["lowest_quartile_mean"] == 30
    assert group["reliability_score"] == 47.5
    assert group["aggregation"] == "task_arithmetic_mean"


def test_mode5_harbor_missing_attempt_withholds_headline(tmp_path, monkeypatch):
    monkeypatch.setattr("evalsys.taskgen.mode5.community_cli._catalog_game_ids", lambda: ["shadow_walker"])
    for index, eligible in enumerate([True, False]):
        trial = tmp_path / f"trial-{index}"
        write_json(trial / "result.json", {"agent_info": {"name": "oracle"}})
        write_json(trial / "verifier/swe-game.json", result_record(report(50, mode="port", eligible=eligible)))
    group = summarize(tmp_path)["groups"][0]
    assert group["mean_score"] is None
    assert group["unscored_games"] == ["shadow_walker"]


def test_mode5_harbor_cannot_reward_a_headline_without_its_evidence():
    data = report(50, mode="port")
    data["scorecard"].pop("evidence_score")
    assert result_record(data)["rewards"] is None


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
    verifier.mode5_profile, verifier.mode5_state_dir = "auto", None
    verifier.docker = "docker"

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
    assert json.loads((tmp_path / "swe-game.json").read_text())["rewards"] is None


@pytest.mark.parametrize("budget,expected", [(None, 7200), (1200, 1200), (5400, 5400), (7200, 7200)])
def test_mode5_harbor_budget_default_and_explicit_override_without_sdk(tmp_path, budget, expected):
    source = package(tmp_path, "port")
    state = tmp_path / "state"
    image_id = "sha256:" + "a" * 64
    write_json(state / "image-lock.json", {"agent_image": {"id": image_id}})
    write_json(state / "last-preflight.json", {"status": "pass", "image_ids": {"agent": image_id}})
    write_json(state / "license-provider.json", {"provider": "floating", "endpoint": "https://fixture.test"})
    task = export_task(source, tmp_path / "task", agent_timeout=budget, mode5_state_dir=state)
    assert f"[agent]\ntimeout_sec = {expected}\n" in (task / "task.toml").read_text(encoding="utf-8")


@pytest.mark.parametrize("provider", ["file", "existing-home"])
def test_mode5_harbor_does_not_export_an_unlicensed_agent(tmp_path, provider):
    source = package(tmp_path, "port")
    state = tmp_path / "state"
    image_id = "sha256:" + "a" * 64
    write_json(state / "image-lock.json", {"agent_image": {"id": image_id}})
    write_json(state / "last-preflight.json", {"status": "pass", "image_ids": {"agent": image_id}})
    write_json(state / "license-provider.json", {"provider": provider})
    out = tmp_path / "task"
    with pytest.raises(ValueError, match="does not inject private"):
        export_task(source, out, mode5_state_dir=state)
    assert not out.exists()
