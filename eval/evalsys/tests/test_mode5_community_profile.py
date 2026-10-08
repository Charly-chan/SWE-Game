import json
import importlib.machinery
import importlib.util
import os
from pathlib import Path

import pytest

from evalsys.taskgen.mode5.community_profile import (
    CommunityProfileError, PROFILE_ID, load_profile, task_environment_lock,
)
from evalsys.taskgen.package import sha256_file
from evalsys.taskgen.mode5.docker_evaluator import _runtime_profile
from evalsys.taskgen.mode5.docker_license import LicenseProvider
from evalsys.taskgen.unity.unity_environment import coerce_environment_profile


def profile_path():
    return (__import__("pathlib").Path(__file__).parents[2] / "infra" / "unity" /
            "profiles" / "mode5-community-docker-v1.json")


def test_checked_in_community_profile_is_valid_and_secret_free():
    profile = load_profile(profile_path())
    assert profile.profile_id == PROFILE_ID
    assert profile.paper_compatible is False
    serialized = json.dumps(profile.public_dict()).lower()
    for forbidden in ("license_file", "license_content", "token", "username", "home_dir", "absolute_path"):
        assert forbidden not in serialized


def test_task_environment_lock_has_requirements_not_host_state():
    lock = task_environment_lock(load_profile(profile_path()))
    assert lock["unity_version"] == "6000.3.23f1"
    assert lock["profile_family"] == "mode5-community-docker"
    assert lock["required_self_checks"] == ["import", "compile", "player_build"]
    assert "image" not in repr(lock).lower()
    assert "license" not in repr(lock).lower()


def test_community_environment_explains_long_unity_selfcheck(tmp_path):
    from evalsys.taskgen.mode5.package_release import _write_mode5_environment_declaration

    _write_mode5_environment_declaration(tmp_path, community_scaffold=True)
    guidance = (tmp_path / "ENVIRONMENT.md").read_text(encoding="utf-8")
    assert "gb-unity check" in guidance
    assert "per-command timeout" in guidance
    assert "poll for its actual exit code" in guidance
    assert "not a C# compiler result" in guidance
    assert "evaluator starts only after the Agent exits" in guidance


def test_agent_image_installs_public_unity_selfcheck_tool():
    docker_root = profile_path().parents[4] / "docker"
    dockerfile = (docker_root / "Dockerfile.unity-local").read_text(encoding="utf-8")
    assert "mode5/gb-unity /usr/local/bin/gb-unity" in dockerfile
    assert (docker_root / "mode5/gb-unity").is_file()
    assert (docker_root / "mode5/GBCommunityBuild.cs").is_file()
    assert "UNITY_DISABLE_LINUX_AUTO_TOOLCHAIN_INSTALLATION=1" in dockerfile


def test_public_helper_disables_optional_linux_sdk_downloads(tmp_path, monkeypatch):
    helper_path = profile_path().parents[4] / "docker/mode5/gb-unity"
    loader = importlib.machinery.SourceFileLoader("gb_unity_offline_test", str(helper_path))
    spec = importlib.util.spec_from_loader(loader.name, loader)
    module = importlib.util.module_from_spec(spec)
    loader.exec_module(module)

    def fake_run(command, **kwargs):
        assert kwargs["env"]["UNITY_DISABLE_LINUX_TOOLCHAIN_MIGRATOR"] == "1"
        assert kwargs["env"]["UNITY_DISABLE_LINUX_AUTO_TOOLCHAIN_INSTALLATION"] == "1"
        return __import__("types").SimpleNamespace(returncode=0)

    monkeypatch.setattr(module.subprocess, "run", fake_run)
    module._unity(tmp_path, [], 30)


def test_public_unity_helper_builds_to_workspace_and_records_status(tmp_path, monkeypatch):
    helper_path = profile_path().parents[4] / "docker/mode5/gb-unity"
    loader = importlib.machinery.SourceFileLoader("gb_unity_helper_test", str(helper_path))
    spec = importlib.util.spec_from_loader(loader.name, loader)
    module = importlib.util.module_from_spec(spec)
    loader.exec_module(module)

    workspace = tmp_path / "workspace"
    project = workspace / "target_unity"
    for name in ("Assets", "Packages", "ProjectSettings"):
        (project / name).mkdir(parents=True, exist_ok=True)
    (workspace / "environment.lock.json").write_text(json.dumps({
        "schema": "gamebench.mode5-task-environment.v1",
        "scripting_backend": "Mono",
    }), encoding="utf-8")
    template = tmp_path / "GBCommunityBuild.cs"
    template.write_text("public class Fixture {}\n", encoding="utf-8")
    monkeypatch.setenv("GB_TASK_WORKSPACE", str(workspace))
    monkeypatch.setattr(module, "BUILD_TEMPLATE", template)

    def fake_unity(candidate, extra, timeout):
        if "-executeMethod" in extra:
            assert (candidate / "Assets/Editor/__GameBenchCommunitySelfCheck.cs").is_file()
            assert os.environ["GB_UNITY_SCRIPTING_BACKEND"] == "Mono"
            output = Path(os.environ["GB_UNITY_BUILD_OUTPUT"])
            output.parent.mkdir(parents=True, exist_ok=True)
            output.write_bytes(b"linux-player")

    monkeypatch.setattr(module, "_unity", fake_unity)
    player = module.build_project(project, workspace / ".selfcheck/build", 30)
    assert player.is_file()
    assert not (project / "Assets/Editor/__GameBenchCommunitySelfCheck.cs").exists()
    status = json.loads((workspace / ".selfcheck/status.json").read_text(encoding="utf-8"))
    assert status["scaffold_import"] == "pass"
    assert status["script_compile"] == "pass"
    assert status["linux_player_build"] == "pass"
    assert "GB_UNITY_SCRIPTING_BACKEND" not in os.environ


def test_public_unity_helper_rejects_missing_environment_lock(tmp_path, monkeypatch):
    helper_path = profile_path().parents[4] / "docker/mode5/gb-unity"
    loader = importlib.machinery.SourceFileLoader("gb_unity_helper_lock_test", str(helper_path))
    spec = importlib.util.spec_from_loader(loader.name, loader)
    module = importlib.util.module_from_spec(spec)
    loader.exec_module(module)
    monkeypatch.setenv("GB_TASK_WORKSPACE", str(tmp_path))
    with pytest.raises(ValueError, match="missing or invalid task environment lock"):
        module._scripting_backend()


def test_public_unity_builder_obeys_declared_backend():
    builder = (profile_path().parents[4] / "docker/mode5/GBCommunityBuild.cs").read_text(
        encoding="utf-8")
    assert "GB_UNITY_SCRIPTING_BACKEND" in builder
    assert "ScriptingImplementation.Mono2x" in builder
    assert "ScriptingImplementation.IL2CPP" in builder


def test_generated_mode5_manifest_pins_public_environment_lock(tmp_path, monkeypatch):
    from evalsys.taskgen.mode5 import package_release as authoring

    visible = tmp_path / "visible"
    visible.mkdir()
    authoring._write_mode5_environment_declaration(visible)
    lock = visible / "environment.lock.json"
    row = {"path": "visible/environment.lock.json", "sha256": sha256_file(lock)}
    assert row["sha256"] == sha256_file(lock)
    assert len(row["sha256"]) == 64


def test_profile_rejects_paper_compatible_claim(tmp_path):
    raw = json.loads(profile_path().read_text(encoding="utf-8"))
    raw["paper_compatible"] = True
    path = tmp_path / "profile.json"
    path.write_text(json.dumps(raw), encoding="utf-8")
    with pytest.raises(CommunityProfileError, match="paper_compatible=false"):
        load_profile(path)


def test_runtime_projection_is_community_ready_but_not_paper_compatible(tmp_path):
    license_file = tmp_path / "license.ulf"
    license_file.write_text("fixture", encoding="utf-8")
    raw = _runtime_profile(
        load_profile(profile_path()), image_id="sha256:" + "a" * 64,
        provider=LicenseProvider("file", source=license_file.resolve()),
    )
    projected = coerce_environment_profile(raw)
    assert projected.ready_for_untrusted_execution is True
    assert projected.paper_compatible is False
    assert projected.environment_class == "community-docker"
