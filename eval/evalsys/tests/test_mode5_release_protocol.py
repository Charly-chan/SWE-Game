import json
from argparse import Namespace
from types import SimpleNamespace

import pytest

from evalsys.taskgen.mode5.claude_settings import provider_settings
from evalsys.taskgen.mode5.fidelity import retained_path
from evalsys.taskgen.mode5.score import COMPONENT_WEIGHTS, CRITERIA, REGISTRY_VERSION
from evalsys.taskgen.scorecard import default_registry_for_mode
from evalsys.verdict import failed


def test_community_agent_budget_defaults_to_ninety_minutes():
    from evalsys.taskgen.mode5.community_cli import build_parser
    args = build_parser().parse_args(["run", "--game", "cat_defense", "--out", "/tmp/result"])
    assert args.agent_timeout == 7200


def test_retained_rejudge_never_enables_vlm_implicitly():
    from evalsys.taskgen.mode5.community_cli import build_parser
    base = ["rejudge", "--evaluation", "old", "--package", "package", "--out", "new"]
    assert build_parser().parse_args(base).fidelity_judge == "none"
    with pytest.raises(SystemExit):
        build_parser().parse_args([*base, "--fidelity-judge", "vlm"])


@pytest.mark.parametrize("gate", ["unity_sdk_integrity", "no_eval_smuggling",
                                  "no_bundled_godot_runtime", "unity_anti_grant_static"])
def test_release_integrity_failure_blocks_candidate_build(tmp_path, monkeypatch, gate):
    from test_mode5_evaluator import _package, _unity_submission
    from evalsys.taskgen import evaluate as evaluator
    pkg, submission = _package(tmp_path), _unity_submission(tmp_path)
    providers = {"unity_sdk_integrity": "_unity_sdk_integrity_item",
                 "no_eval_smuggling": "_port_no_smuggling_item",
                 "no_bundled_godot_runtime": "_no_bundled_godot_runtime_item",
                 "unity_anti_grant_static": "_unity_anti_grant_static_item"}
    monkeypatch.setattr(evaluator, providers[gate], lambda *a: failed(gate))
    monkeypatch.setattr(evaluator, "build_unity_submission",
                        lambda *a, **kw: pytest.fail("release executed an integrity-invalid candidate"))
    result = evaluator.evaluate_task(pkg.root, submission, engine="on", registry_version=REGISTRY_VERSION)
    assert result.engine["build"]["status"] == "skipped"
    assert result.engine["build"]["blocked_by"] == gate


def test_unity_cold_checks_have_a_separate_bounded_child_budget():
    from evalsys.taskgen.matrix import _child_timeout_cap, _deadline_prompt
    assert _child_timeout_cap(7200, mode="port") == 1200
    assert _child_timeout_cap(7200, mode="brief") == 120
    assert "above 1200 seconds" in _deadline_prompt(7200, mode="port")


@pytest.mark.parametrize("budget", ["0", "-1", "unlimited"])
def test_community_agent_budget_rejects_ambiguous_unlimited_values(budget):
    from evalsys.taskgen.mode5.community_cli import build_parser
    with pytest.raises(SystemExit):
        build_parser().parse_args(["run", "--game", "cat_defense", "--out", "/tmp/result",
                                  "--agent-timeout", budget])


def test_release_default_is_single_five_component_protocol():
    assert default_registry_for_mode("port") == REGISTRY_VERSION
    assert COMPONENT_WEIGHTS == {
        "mechanics": 35, "playability": 25, "structure": 15, "visual": 15, "stability": 10,
    }
    assert len(CRITERIA) == 20


def test_control_fixture_restore_omits_only_our_injected_error(tmp_path, monkeypatch):
    import runpy
    from pathlib import Path
    source = tmp_path / "controlled-negative"
    authored = source / "Assets/Game/Original.cs"
    authored.parent.mkdir(parents=True)
    authored.write_text("// controlled human source\n", encoding="utf-8")
    injected = authored.with_name("GBReleaseIntentionalSyntaxError.cs")
    injected.write_text("public class GBReleaseIntentionalSyntaxError { this is invalid C#; }\n", encoding="utf-8")
    out = tmp_path / "restored"
    monkeypatch.setattr("sys.argv", ["fixture", "--state-dir", str(tmp_path),
                        "--package", str(tmp_path), "--positive-fixture", str(source),
                        "--out", str(out), "--restore-positive-control"])
    script = Path(__file__).resolve().parents[3] / "scripts/mode5_release_negative_fixture.py"
    assert runpy.run_path(str(script))["main"]() == 0
    assert (out / "submission/Assets/Game/Original.cs").read_bytes() == authored.read_bytes()
    assert not (out / "submission/Assets/Game/GBReleaseIntentionalSyntaxError.cs").exists()
    assert injected.exists()  # never rewrite the source fixture


def test_cc_settings_use_provider_model_and_restore_private_environment(tmp_path, monkeypatch):
    monkeypatch.setenv("ANTHROPIC_AUTH_TOKEN", "previous")
    source = tmp_path / "settings.json"
    source.write_text(json.dumps({"model": "glm-fixture", "env": {
        "ANTHROPIC_AUTH_TOKEN": "private-token", "ANTHROPIC_BASE_URL": "https://example.test",
        "UNRELATED_SECRET": "do-not-import",
    }}), encoding="utf-8")
    args = Namespace(claude_settings=str(source), harness="claude", model="")
    with provider_settings(args):
        import os
        assert os.environ["ANTHROPIC_AUTH_TOKEN"] == "private-token"
        assert "UNRELATED_SECRET" not in os.environ
        assert args.model == "glm-fixture"
        assert args.agent_key_env == "ANTHROPIC_AUTH_TOKEN"
    assert os.environ["ANTHROPIC_AUTH_TOKEN"] == "previous"


@pytest.mark.parametrize("value", ["/etc/passwd", "/runtime/work/runs/../../secrets", "C:/private/key"])
def test_capture_path_cannot_escape_trusted_media(tmp_path, value):
    with pytest.raises(ValueError):
        retained_path(value, tmp_path)


def test_public_unity_log_retains_compiler_errors_not_license_identifiers():
    import runpy
    from pathlib import Path
    helper = Path(__file__).resolve().parents[3] / "docker/mode5/gb-unity"
    public_log = runpy.run_path(str(helper))["public_unity_log"]
    log = public_log("[Licensing::Client] serial=private\nAccessToken=private\nAssets/Game/Foo.cs: error CS1002\n")
    assert "private" not in log
    assert "error CS1002" in log


@pytest.mark.parametrize("text,expected", [("Player initialized\n", "pass"),
    ("NullReferenceException: Object reference not set\n", "fail"),
    ("UnityEngine.Debug:LogError (object)\n", "fail")])
def test_player_smoke_reads_runtime_errors_and_cleans_up_at_horizon(tmp_path, monkeypatch, text, expected):
    import runpy
    import subprocess
    from pathlib import Path
    helper = runpy.run_path(str(Path(__file__).resolve().parents[3] / "docker/mode5/gb-unity"))
    smoke = helper["smoke_player"]
    player = tmp_path / "player"
    player.write_bytes(b"controlled fixture")
    updates, stopped = {}, []

    class Player:
        returncode = None
        def __init__(self, command, **kwargs):
            kwargs["stdout"].write(text.encode())
        def wait(self, timeout):
            raise subprocess.TimeoutExpired("controlled smoke", timeout)

    monkeypatch.setitem(smoke.__globals__, "subprocess", SimpleNamespace(
        Popen=Player, TimeoutExpired=subprocess.TimeoutExpired, STDOUT=subprocess.STDOUT))
    monkeypatch.setitem(smoke.__globals__, "_write_status", lambda **values: updates.update(values))
    monkeypatch.setitem(smoke.__globals__, "_stop_player", lambda process: stopped.append(process))
    if expected == "fail":
        with pytest.raises(RuntimeError, match="runtime errors"):
            smoke(player, 1)
    else:
        smoke(player, 1)
    assert updates["player_smoke"] == expected
    assert len(stopped) == 1


def test_player_cleanup_signals_group_even_after_wrapper_exit(monkeypatch):
    import runpy
    from pathlib import Path
    helper = runpy.run_path(str(Path(__file__).resolve().parents[3] / "docker/mode5/gb-unity"))
    stop = helper["_stop_player"]
    signals = []
    monkeypatch.setitem(stop.__globals__, "os", SimpleNamespace(
        name="posix", killpg=lambda pid, sig: signals.append((pid, sig))))
    monkeypatch.setitem(stop.__globals__, "signal", SimpleNamespace(SIGTERM=15, SIGKILL=9))
    stop(SimpleNamespace(pid=123, wait=lambda timeout: 0))
    assert signals == [(123, 15), (123, 9)]


def test_retained_media_maps_controller_path(tmp_path):
    movie = tmp_path / "witness" / "capture.mp4"
    movie.parent.mkdir()
    movie.write_bytes(b"retained-capture")
    assert retained_path("/runtime/work/runs/witness/capture.mp4", tmp_path) == movie
    assert retained_path("/runtime/work/runs/missing.mp4", tmp_path) is None


def test_unity_workspace_return_excludes_caches_not_authored_files(tmp_path, monkeypatch):
    from evalsys.taskgen.docker_sandbox import DockerSandbox
    from evalsys.taskgen.mode5.docker_environment import DockerClient
    seen = {}
    def copy(client, container, source, destination, **kwargs):
        seen.update(kwargs)
        destination.mkdir(parents=True)
        (destination / "authored.cs").write_text("model source", encoding="utf-8")
    monkeypatch.setattr(DockerClient, "copy_from_directory", copy)
    sandbox = DockerSandbox(image="fixture", log_path=tmp_path / "sandbox.log", cell="fixture")
    sandbox._docker = "docker"
    sandbox.unity_workspace = True
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    (workspace / "stale.cs").write_text("stale", encoding="utf-8")
    sandbox.copy_out(workspace)
    assert (workspace / "authored.cs").read_text() == "model source"
    assert not (workspace / "stale.cs").exists()
    assert "./Library" in seen["exclude"] and "./submission/Library" in seen["exclude"]
    assert "Library" not in seen["exclude"]  # preserve Assets/Game/Library authored assets
    assert sandbox.transfer["omitted_unity_caches"] is True


def test_relocated_report_media_and_markdown_use_final_directory(tmp_path):
    from evalsys.taskgen.mode5.docker_evaluator import relocate_artifact_paths
    stage, out = tmp_path / "stage", tmp_path / "published"
    stage.mkdir()
    (stage / "report.json").write_text(json.dumps({"movie": str(stage / "media.mp4")}), encoding="utf-8")
    (stage / "report.md").write_text(str(stage / "media.mp4"), encoding="utf-8")
    relocate_artifact_paths(stage, out)
    assert json.loads((stage / "report.json").read_text(encoding="utf-8"))["movie"] == str(out / "media.mp4")
    assert (stage / "report.md").read_text(encoding="utf-8") == str(out / "media.mp4")


def test_community_scaffold_tolerates_unity_metadata_without_hash_checks(tmp_path, monkeypatch):
    import shutil
    from evalsys.taskgen.unity import unity_sdk as sdk
    reference, candidate = tmp_path / "reference", tmp_path / "candidate"
    shutil.copytree(sdk.TARGET_UNITY_ROOT, reference)
    shutil.copytree(reference, candidate)
    expected = {name: "unused-legacy-field" for name in sdk.immutable_scaffold_paths(reference)}
    monkeypatch.setattr(sdk, "sha256_file", lambda path: pytest.fail("Community must not hash files"))
    generated = candidate / "Assets/GameBenchmarkSDK/GameBenchmarkInput.inputactions.meta"
    generated.write_text("Unity generated metadata\n", encoding="utf-8")
    lock = candidate / "Packages/packages-lock.json"
    lock.write_text('{"generated": true}\n', encoding="utf-8")
    manifest = candidate / "Packages/manifest.json"
    manifest.write_text(json.dumps(json.loads(manifest.read_text()), indent=7), encoding="utf-8")
    assert sdk.validate_scaffold_integrity(candidate, expected, reference_project=reference).ok
    source = candidate / "Assets/GameBenchmarkSDK/GBEntity.cs"
    source.write_text("changed SDK interface", encoding="utf-8")
    assert not sdk.validate_scaffold_integrity(candidate, expected, reference_project=reference).ok


def test_agent_license_probe_does_not_create_project_in_task_workspace(tmp_path, monkeypatch):
    import subprocess
    from evalsys.taskgen.docker_sandbox import DockerSandbox
    sandbox = DockerSandbox(image="fixture", log_path=tmp_path / "sandbox.log", cell="fixture")
    calls = []
    def run(args, **kwargs):
        calls.append(args)
        return subprocess.CompletedProcess(args, 0, "", "")
    monkeypatch.setattr(sandbox, "_run", run)
    monkeypatch.setattr(sandbox, "_exec", run)
    sandbox.configure_unity_license(provider="floating", endpoint="https://license.example.test")
    unity = next(args for args in calls if "unity" in args)
    assert unity[unity.index("-projectPath") + 1] == "/tmp/gamebench-agent-license-probe"
    assert "/workspace" not in unity
