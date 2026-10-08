import json
import os
import subprocess
from pathlib import Path

import pytest

from evalsys.taskgen.mode5.docker_license import (
    LicenseConfigurationError, LicenseProvider, _validate_windows_acl,
    host_machine_identity_mount, license_log_error,
)
from evalsys.taskgen.docker_sandbox import DockerSandbox
from evalsys.taskgen import matrix as matrix_module


@pytest.fixture(autouse=True)
def isolate_host_acl_encoding(monkeypatch):
    # Codex's embedded Windows Python represents this non-ASCII test profile
    # with replacement characters. ACL behavior itself is covered below with
    # stable subprocess fixtures; production Python receives the real path.
    if os.name == "nt":
        monkeypatch.setattr(
            "evalsys.taskgen.mode5.docker_license._validate_windows_acl",
            lambda path: None,
        )


def test_file_provider_reports_no_path_content_or_digest(tmp_path):
    path = tmp_path / "private.ulf"
    path.write_text("TOP-SECRET-LICENSE", encoding="utf-8")
    path.chmod(0o600)
    provider = LicenseProvider.file(path)
    metadata = provider.public_metadata(probe_passed=True)
    assert metadata == {
        "provider": "file", "configured": True, "format": "ulf", "probe_passed": True,
    }
    serialized = json.dumps(metadata)
    assert str(path) not in serialized
    assert "TOP-SECRET" not in serialized


def test_file_provider_rejects_wrong_format_and_symlink(tmp_path):
    wrong = tmp_path / "license.txt"
    wrong.write_text("x", encoding="utf-8")
    with pytest.raises(LicenseConfigurationError, match=".ulf or .xml"):
        LicenseProvider.file(wrong)
    target = tmp_path / "license.ulf"
    target.write_text("x", encoding="utf-8")
    link = tmp_path / "linked.ulf"
    try:
        link.symlink_to(target)
    except OSError:
        pytest.skip("symlinks unavailable to this Windows test identity")
    with pytest.raises(LicenseConfigurationError, match="symlink"):
        LicenseProvider.file(link)


@pytest.mark.parametrize("message", [
    "No valid Unity Editor license",
    "Failed to activate/update license",
    "LICENSE IS INVALID",
    "Licensing Client timed out",
    "Failed to connect to licensing client",
])
def test_license_probe_recognizes_infrastructure_failures(message):
    assert license_log_error(f"prefix\n{message}\nsuffix") is not None


def test_floating_metadata_does_not_expose_endpoint():
    provider = LicenseProvider.floating("https://licenses.internal.invalid:8080")
    assert provider.public_metadata() == {"provider": "floating", "configured": True}


def test_only_existing_home_mounts_same_host_machine_identity():
    mount = host_machine_identity_mount("existing-home")
    assert mount == [
        "--mount", "type=bind,source=/etc/machine-id,target=/etc/machine-id,readonly",
    ]
    assert host_machine_identity_mount("file") == []
    assert host_machine_identity_mount("floating") == []


def test_agent_mounts_host_identity_only_when_requested(tmp_path, monkeypatch):
    observed = []
    monkeypatch.setattr(DockerSandbox, "_run", lambda self, args, **kwargs: (
        observed.append(list(args)) or subprocess.CompletedProcess(args, 0, "ok", "")
    ))
    for method in ("_await_running", "_record_image", "_bootstrap"):
        monkeypatch.setattr(DockerSandbox, method, lambda self: None)
    for enabled in (False, True):
        sandbox = DockerSandbox(
            image="fixture", log_path=tmp_path / "sandbox.log", cell="fixture",
            host_machine_identity=enabled,
        )
        sandbox.start()
        launch = next(args for args in observed if args[0] == "run")
        assert ("/etc/machine-id" in " ".join(launch)) is enabled
        observed.clear()


@pytest.mark.skipif(os.name != "nt", reason="Windows ACL policy")
def test_windows_acl_rejects_broad_read_principals(monkeypatch):
    def broad(*args, **kwargs):
        return subprocess.CompletedProcess(args, 0, "S-1-5-11\n", "")

    monkeypatch.setattr(
        "evalsys.taskgen.mode5.docker_license.subprocess.run", broad,
    )
    with pytest.raises(LicenseConfigurationError, match="broad Windows principal"):
        _validate_windows_acl(Path("C:/private/license.ulf"))


@pytest.mark.skipif(os.name != "nt", reason="Windows ACL policy")
def test_windows_acl_accepts_owner_system_and_administrators(monkeypatch):
    def private(*args, **kwargs):
        return subprocess.CompletedProcess(
            args, 0, "S-1-5-18\nS-1-5-32-544\nS-1-5-21-1-2-3-1001\n", "",
        )

    monkeypatch.setattr(
        "evalsys.taskgen.mode5.docker_license.subprocess.run", private,
    )
    _validate_windows_acl(Path("C:/private/license.ulf"))


class FakeSandbox:
    def __init__(self):
        self.calls = []
        self.exec_calls = []
        self.name = "fixture"
        self.unity_license = {}

    def _run(self, args, **kwargs):
        self.calls.append(list(args))
        return subprocess.CompletedProcess(args, 0, "", "")

    def _exec(self, args, **kwargs):
        self.exec_calls.append(list(args))
        return subprocess.CompletedProcess(args, 0, "", "")


def test_agent_floating_provider_is_actually_probed():
    sandbox = FakeSandbox()
    result = DockerSandbox.configure_unity_license(
        sandbox, provider="floating", endpoint="https://license.invalid:8080",
    )
    command = sandbox.exec_calls[-1]
    assert "UNITY_LICENSE_SERVER=https://license.invalid:8080" in command
    assert "unity" in command and "-batchmode" in command
    assert result["probe_passed"] is True


def test_agent_process_scrubs_coordinator_license_source_variables(monkeypatch):
    private = (
        "GB_UNITY_LICENSE_FILE", "GB_UNITY_CONFIG_ROOT",
        "GB_UNITY_FLOATING_ENDPOINT",
    )
    for name in private:
        monkeypatch.setenv(name, "must-not-enter-agent")
    monkeypatch.setenv("ANTHROPIC_API_KEY", "agent-needs-this")
    env = matrix_module._agent_process_environment()
    assert all(name not in env for name in private)
    assert env["ANTHROPIC_API_KEY"] == "agent-needs-this"


def test_agent_selfcheck_status_is_bounded_and_normalized():
    class Sandbox:
        def read_text(self, path):
            assert path == "/workspace/.selfcheck/status.json"
            return json.dumps({
                "schema": "gamebench.mode5-agent-selfcheck.v1",
                "scaffold_import": "pass",
                "script_compile": "pass",
                "linux_player_build": "pass",
                "player_smoke": "unexpected-value",
                "private_path": "must-not-be-recorded",
            })

    assert matrix_module._unity_selfcheck_snapshot(Sandbox()) == {
        "scaffold_import": "pass",
        "script_compile": "pass",
        "linux_player_build": "pass",
        "player_smoke": "invalid",
    }


def test_public_agent_runner_forwards_mode_to_inner_environment(tmp_path, monkeypatch):
    observed = {}

    class Sandbox:
        transfer = {}

        def close(self, **kwargs):
            return None

    monkeypatch.setattr(matrix_module, "_start_docker_sandbox", lambda *a, **k: Sandbox())

    def inner(*args, **kwargs):
        observed.update(kwargs)
        return 0

    monkeypatch.setattr(matrix_module, "_run_coding_agent", inner)
    config = matrix_module.AgentConfig(
        backend="command", command="true", sandbox="docker", docker_image="fixture",
    )
    assert matrix_module.run_coding_agent(
        tmp_path / "workspace", tmp_path / "logs", config, mode="port",
    ) == 0
    assert observed["mode"] == "port"


def test_mode5_collection_rejects_symlinks(tmp_path):
    workspace = tmp_path / "workspace"
    project = workspace / "submission"
    for name in ("Assets", "Packages", "ProjectSettings"):
        (project / name).mkdir(parents=True, exist_ok=True)
    (project / "ProjectSettings/ProjectVersion.txt").write_text(
        "m_EditorVersion: 6000.3.23f1\n", encoding="utf-8",
    )
    private = tmp_path / "private.txt"
    private.write_text("not part of the submission", encoding="utf-8")
    try:
        (project / "Assets/escape.txt").symlink_to(private)
    except OSError:
        pytest.skip("symlinks unavailable to this test identity")
    with pytest.raises(matrix_module.MatrixError, match="symlink or reparse"):
        matrix_module.collect_submission(workspace, tmp_path / "collected", mode="port")


def test_agent_file_provider_removes_raw_license_after_probe(tmp_path):
    source = tmp_path / "private.ulf"
    source.write_text("fixture", encoding="utf-8")
    sandbox = FakeSandbox()
    DockerSandbox.configure_unity_license(sandbox, provider="file", source=source)
    assert any(call[:5] == ["exec", "--user", "0:0", "fixture", "rm"]
               and "/opt/gb-agent-home/license.ulf" in call for call in sandbox.calls)
