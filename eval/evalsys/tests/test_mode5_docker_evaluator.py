import json
import hashlib
import os
import subprocess
from pathlib import Path
from types import SimpleNamespace

import pytest

from evalsys.taskgen.mode5.community_profile import load_default_profile, task_environment_lock
from evalsys.taskgen.mode5.docker_environment import CommunityDockerError
from evalsys.taskgen.mode5.docker_evaluator import (
    _mark_development_unscored, _package_hidden_runtime_ready,
    evaluate_in_container, verify_artifact_manifest,
)
from evalsys.taskgen.mode5.docker_license import LicenseProvider
from evalsys.taskgen.mode5.scoring import REGISTRY_VERSION
from evalsys.taskgen.scorecard import score_task_result
from mode5_test_support import controller_engine
from evalsys.verdict import passed
from evalsys.taskgen.unity.unity_suite_compiler import suite_content_digest


IMAGE_ID = "sha256:" + "a" * 64


class FakeEvaluatorDocker:
    def __init__(self):
        self.calls = []

    def image_id(self, image):
        self.calls.append(("image_id", image))
        return IMAGE_ID

    def run(self, args, *, timeout=600, check=True):
        args = [str(value) for value in args]
        self.calls.append(args)
        stdout = ""
        if args[:3] == ["inspect", "--format", "{{json .NetworkSettings.Networks}}"]:
            stdout = "{}\n"
        return subprocess.CompletedProcess(args, 0, stdout, "")

    def copy_into_directory(self, source, container, destination, *, timeout=600):
        self.calls.append(["archive-in", str(source), container, destination])

    def copy_from_directory(self, container, source, destination, *, timeout=600):
        self.calls.append(["archive-out", container, source, str(destination)])
        target = Path(destination)
        target.mkdir(parents=True, exist_ok=True)
        (target / "report.json").write_text(json.dumps({
            "scorecard": score_task_result(SimpleNamespace(
                package=SimpleNamespace(manifest={"mode": "port", "game_id": "fixture"}),
                engine=controller_engine(), items=[passed(ident) for ident in (
                    "task_gdd_contract", "verifier_profile_complete", "unity_layout", "unity_interface",
                    "unity_sdk_integrity", "port_contract_alignment", "no_eval_smuggling",
                    "no_bundled_godot_runtime", "build_recipe", "unity_anti_grant_static", "unity_build",
                    "unity_probe", "unity_input_dispatch", "ops_present", "ops_valid", "ops_not_idle",
                    "unity_auto_win_ready", "null_no_win", "unity_counterfactual", "unity_mechanic_trace",
                    "causal_witness", "unity_hidden_behavior", "unity_runtime_stability",
                )], resolved=True,
            ), REGISTRY_VERSION),
            "engine": controller_engine(),
            "items": [passed(ident).to_dict() for ident in (
                "task_gdd_contract", "verifier_profile_complete", "unity_layout", "unity_interface",
                "unity_sdk_integrity", "port_contract_alignment", "no_eval_smuggling",
                "no_bundled_godot_runtime", "build_recipe", "unity_anti_grant_static", "unity_build",
                "unity_probe", "unity_input_dispatch", "ops_present", "ops_valid", "ops_not_idle",
                "unity_auto_win_ready", "null_no_win", "unity_counterfactual", "unity_mechanic_trace",
                "causal_witness", "unity_hidden_behavior", "unity_runtime_stability",
            )],
        }), encoding="utf-8")


def _write_task_lock(package: Path, *, backend: str | None = None) -> None:
    (package / "hidden").mkdir(exist_ok=True)
    lock = task_environment_lock(load_default_profile())
    if backend is not None:
        lock["scripting_backend"] = backend
    path = package / "visible" / "environment.lock.json"
    path.parent.mkdir(parents=True, exist_ok=True)
    for ready in (package / "HANDOFF.md", package / "PROMPT.md", package / "visible/PROMPT.md"):
        ready.write_text("fixture\n", encoding="utf-8")
    path.write_text(json.dumps(lock), encoding="utf-8")
    (package / "manifest.json").write_text(json.dumps({
        "schema_version": 1, "mode": "port", "game_id": "fixture",
        "environment_lock": {
            "path": "visible/environment.lock.json",
            "sha256": hashlib.sha256(path.read_bytes()).hexdigest(),
        }
    }), encoding="utf-8")
    (package / "hidden").mkdir(exist_ok=True)
    for relative in ("HANDOFF.md", "PROMPT.md", "visible/PROMPT.md"):
        (package / relative).write_text("fixture", encoding="utf-8")


def test_evaluator_bootstraps_floating_license_then_is_offline_and_secret_free(tmp_path, monkeypatch):
    package, submission, out = tmp_path / "package", tmp_path / "submission", tmp_path / "out"
    package.mkdir(); submission.mkdir()
    _write_task_lock(package)
    (submission / "ProjectSettings").mkdir()
    monkeypatch.setenv("ANTHROPIC_API_KEY", "must-not-enter-evaluator")
    docker = FakeEvaluatorDocker()
    report = evaluate_in_container(
        repo_root=Path(__file__).parents[3], package=package, submission=submission,
        out=out, profile=load_default_profile(),
        image_lock={
            "agent_image": {"tag": "agent:test", "id": "sha256:" + "b" * 64},
            "evaluator_image": {"tag": "evaluator:test", "id": IMAGE_ID},
        },
        preflight={"status": "pass", "image_ids": {"evaluator": IMAGE_ID}},
        license_provider=LicenseProvider.floating("http://license:8080"), docker=docker,
    )
    create = next(call for call in docker.calls if isinstance(call, list) and call[:1] == ["create"])
    assert create[create.index("--network") + 1] == "bridge"
    assert "--read-only" in create
    tmpfs = [create[i + 1] for i, value in enumerate(create[:-1]) if value == "--tmpfs"]
    assert any(value.startswith("/candidate:") for value in tmpfs)
    assert any(value.startswith("/output:") for value in tmpfs)
    assert "/runtime:rw,nosuid,nodev,exec,size=4g" in tmpfs
    assert any(value.startswith("/home/unity-runner:") for value in tmpfs)
    assert "--cpus" in create and "--memory" in create and "--pids-limit" in create
    assert [create[i + 1] for i, value in enumerate(create[:-1]) if value == "--cap-drop"] == ["ALL"]
    assert "KILL" in [create[i + 1] for i, value in enumerate(create[:-1]) if value == "--cap-add"]
    wire = json.dumps(docker.calls)
    assert "must-not-enter-evaluator" not in wire
    assert "ANTHROPIC_API_KEY" not in wire
    disconnect = next(i for i, call in enumerate(docker.calls)
                      if isinstance(call, list) and call[:3] == ["network", "disconnect", "bridge"])
    candidate_copy = next(i for i, call in enumerate(docker.calls)
                          if isinstance(call, list) and call[:1] == ["archive-in"]
                          and call[-1] == "/candidate/submission")
    assert disconnect < candidate_copy
    assert report["environment"]["environment_class"] == "community-docker"
    assert report["environment"]["paper_compatible"] is False
    assert report["environment"]["build_target"] == "StandaloneLinux64"
    assert report["environment"]["scripting_backend"] == "Mono"
    assert "preflight_checked_at" in report["environment"]
    assert "preflight_digest" not in report["environment"]
    assert report["scorecard"]["ranking_eligible"] is False
    assert report["run_config"]["development_reason"] == "hidden_suite_not_release_ready"
    assert report["environment"]["agent_image_id"] == "sha256:" + "b" * 64
    assert report["environment"]["evaluator_image_id"] == IMAGE_ID
    assert report["run_config"]["harness"] == "external-submission"
    verify_artifact_manifest(out)


def test_hidden_package_requires_current_verified_source_digest(tmp_path, monkeypatch):
    repo, package = tmp_path / "repo", tmp_path / "package"
    behavior = package / "hidden/unity/behavior"
    behavior.mkdir(parents=True)
    (package / "manifest.json").write_text('{"game_id":"example_game"}', encoding="utf-8")
    suite = {"schema": "gamebench.unity-hidden-suite.v1", "game_id": "example_game", "scenarios": [],
             "status": "calibrated", "runtime_ready": True}
    suite["content_digest"] = suite_content_digest(suite)
    (behavior / "suite.json").write_text(json.dumps(suite), encoding="utf-8")
    gate = {"schema": "gamebench.unity-calibration.v1", "game_id": "example_game",
            "status": "calibrated", "runtime_ready": True, "suite_digest": suite["content_digest"]}
    (behavior / "calibration_status.json").write_text(json.dumps(gate), encoding="utf-8")
    monkeypatch.setattr("evalsys.taskgen.mode5.release_data.load_released_suite", lambda *a: (suite, gate))
    assert _package_hidden_runtime_ready(repo, package)
    changed = dict(gate, suite_digest="sha256:stale")
    (behavior / "calibration_status.json").write_text(json.dumps(changed), encoding="utf-8")
    assert not _package_hidden_runtime_ready(repo, package)
    (behavior / "calibration_status.json").write_text(json.dumps(gate), encoding="utf-8")
    monkeypatch.setattr("evalsys.taskgen.mode5.release_data.load_released_suite", lambda *a: (dict(suite, status="pending"), gate))
    assert not _package_hidden_runtime_ready(repo, package)



def test_development_smoke_is_never_ranked_even_with_a_zero_score(tmp_path):
    report = {
        "scorecard": {
            "ranking_eligible": True,
            "ranking_scope": "mode5-community-docker-v1",
            "headline": {"score": 0.0, "ranking_eligible": True},
        },
        "headline": {"score": 0.0, "ranking_eligible": True},
    }
    markdown = tmp_path / "report.md"
    markdown.write_text("ranking_eligible=yes\n", encoding="utf-8")
    _mark_development_unscored(report, tmp_path)
    assert report["scorecard"]["ranking_eligible"] is False
    assert report["scorecard"]["headline"]["ranking_eligible"] is False
    assert report["headline"]["ranking_eligible"] is False
    assert report["scorecard"]["ranking_scope"].endswith("-development")
    assert "ranking_eligible=no" in markdown.read_text(encoding="utf-8")


def test_evaluator_publishes_unranked_development_report(tmp_path):
    package, submission, out = tmp_path / "package", tmp_path / "submission", tmp_path / "out"
    package.mkdir(); submission.mkdir()
    _write_task_lock(package)
    report = evaluate_in_container(
        repo_root=Path(__file__).parents[3], package=package, submission=submission,
        out=out, profile=load_default_profile(),
        image_lock={
            "agent_image": {"tag": "agent:test", "id": "sha256:" + "b" * 64},
            "evaluator_image": {"tag": "evaluator:test", "id": IMAGE_ID},
        },
        preflight={"status": "pass", "image_ids": {"evaluator": IMAGE_ID}},
        license_provider=LicenseProvider.floating("http://license:8080"),
        docker=FakeEvaluatorDocker(),
        run_config={"development_unscored": True, "harness": "command"},
    )
    assert report["scorecard"]["ranking_eligible"] is False
    assert report["run_config"]["development_unscored"] is True
    assert json.loads((out / "report.json").read_text())["scorecard"]["ranking_eligible"] is False
    verify_artifact_manifest(out)


def test_evaluator_rejects_stale_task_backend_before_container_creation(tmp_path):
    package, submission = tmp_path / "package", tmp_path / "submission"
    package.mkdir(); submission.mkdir()
    _write_task_lock(package, backend="IL2CPP")
    docker = FakeEvaluatorDocker()
    with pytest.raises(CommunityDockerError, match="regenerate the task package"):
        evaluate_in_container(
            repo_root=Path(__file__).parents[3], package=package, submission=submission,
            out=tmp_path / "out", profile=load_default_profile(),
            image_lock={"evaluator_image": {"tag": "evaluator:test", "id": IMAGE_ID}},
            preflight={"status": "pass", "image_ids": {"evaluator": IMAGE_ID}},
            license_provider=LicenseProvider.floating("http://license:8080"), docker=docker,
        )
    assert not any(isinstance(call, list) and call[:1] == ["create"] for call in docker.calls)


def test_evaluator_rejects_wrong_editor_version_before_container_creation(tmp_path):
    package, submission = tmp_path / "package", tmp_path / "submission"
    package.mkdir(); submission.mkdir()
    _write_task_lock(package)
    lock = package / "visible" / "environment.lock.json"
    payload = json.loads(lock.read_text(encoding="utf-8"))
    payload["unity_version"] = "wrong-editor-version"
    lock.write_text(json.dumps(payload), encoding="utf-8")
    docker = FakeEvaluatorDocker()
    with pytest.raises(CommunityDockerError, match="lock does not match"):
        evaluate_in_container(
            repo_root=Path(__file__).parents[3], package=package, submission=submission,
            out=tmp_path / "out", profile=load_default_profile(),
            image_lock={"evaluator_image": {"tag": "evaluator:test", "id": IMAGE_ID}},
            preflight={"status": "pass", "image_ids": {"evaluator": IMAGE_ID}},
            license_provider=LicenseProvider.floating("http://license:8080"), docker=docker,
    )
    assert not any(isinstance(call, list) and call[:1] == ["create"] for call in docker.calls)


def test_evaluator_accepts_task_lock_whitespace_without_byte_fingerprint(tmp_path):
    package, submission = tmp_path / "package", tmp_path / "submission"
    package.mkdir(); submission.mkdir()
    _write_task_lock(package)
    lock = package / "visible" / "environment.lock.json"
    lock.write_text(lock.read_text(encoding="utf-8") + "\n", encoding="utf-8")
    docker = FakeEvaluatorDocker()
    evaluate_in_container(
        repo_root=Path(__file__).parents[3], package=package, submission=submission,
        out=tmp_path / "out", profile=load_default_profile(),
        image_lock={"evaluator_image": {"tag": "evaluator:test", "id": IMAGE_ID}},
        preflight={"status": "pass", "image_ids": {"evaluator": IMAGE_ID}},
        license_provider=LicenseProvider.floating("http://license:8080"), docker=docker,
    )
    assert any(isinstance(call, list) and call[:1] == ["create"] for call in docker.calls)


def test_artifact_manifest_checks_size_not_same_length_content(tmp_path):
    root = tmp_path / "evaluation"
    root.mkdir()
    payload = root / "report.json"
    payload.write_text("original", encoding="utf-8")
    import hashlib
    digest = hashlib.sha256(payload.read_bytes()).hexdigest()
    (root / "artifact-manifest.json").write_text(json.dumps({
        "schema": "gamebench.mode5-community-artifacts.v1",
        "producer": "test",
        "artifacts": [{"path": "report.json", "bytes": 8, "sha256": digest}],
    }), encoding="utf-8")
    verify_artifact_manifest(root)
    payload.write_text("tampered", encoding="utf-8")
    verify_artifact_manifest(root)
    payload.write_text("truncated", encoding="utf-8")
    with pytest.raises(CommunityDockerError, match="size mismatch"):
        verify_artifact_manifest(root)


def test_evaluator_rejects_digest_mismatch_before_container_creation(tmp_path):
    package, submission = tmp_path / "package", tmp_path / "submission"
    package.mkdir(); submission.mkdir()
    docker = FakeEvaluatorDocker()
    with pytest.raises(CommunityDockerError, match="does not match image-lock"):
        evaluate_in_container(
            repo_root=Path(__file__).parents[3], package=package, submission=submission,
            out=tmp_path / "out", profile=load_default_profile(),
            image_lock={
                "agent_image": {"tag": "agent:test", "id": IMAGE_ID},
                "evaluator_image": {"tag": "evaluator:test", "id": "sha256:" + "c" * 64},
            }, preflight={"status": "pass", "image_ids": {"evaluator": IMAGE_ID}},
            license_provider=LicenseProvider.floating("http://license:8080"), docker=docker,
        )
    assert not any(isinstance(call, list) and call[:1] == ["create"] for call in docker.calls)


def test_evaluator_rejects_submission_symlink_before_container_creation(tmp_path):
    package, submission = tmp_path / "package", tmp_path / "submission"
    package.mkdir(); submission.mkdir()
    target = tmp_path / "private"
    target.write_text("do not follow", encoding="utf-8")
    link = submission / "escape"
    try:
        link.symlink_to(target)
    except OSError:
        pytest.skip("symlinks unavailable to this test identity")
    docker = FakeEvaluatorDocker()
    with pytest.raises(CommunityDockerError, match="symlink or reparse"):
        evaluate_in_container(
            repo_root=Path(__file__).parents[3], package=package, submission=submission,
            out=tmp_path / "out", profile=load_default_profile(),
            image_lock={
                "agent_image": {"tag": "agent:test", "id": IMAGE_ID},
                "evaluator_image": {"tag": "evaluator:test", "id": IMAGE_ID},
            }, preflight={"status": "pass", "image_ids": {"evaluator": IMAGE_ID}},
            license_provider=LicenseProvider.floating("http://license:8080"), docker=docker,
        )
    assert not any(isinstance(call, list) and call[:1] == ["create"] for call in docker.calls)
