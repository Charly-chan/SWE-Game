import json
from mode5_test_support import summary_reading
import shutil
from argparse import Namespace
from types import SimpleNamespace

import pytest

from evalsys.taskgen.unity.unity_sdk import (
    enable_community_engine_modules, immutable_scaffold_paths,
)
from evalsys.taskgen.mode5.community_cli import (
    RUN_STATE_SCHEMA, _evaluator_failure, _load_verified_report,
    _catalog_game_path, _check_release_suite, _quarantine_incomplete_evaluation,
    _record_phase, _summarize_reports, build_parser, cmd_doctor, cmd_generate, cmd_run,
    cmd_setup, cmd_summarize,
)
from evalsys.taskgen.mode5.docker_environment import CommunityDockerError
from evalsys.taskgen.mode5.docker_evaluator import write_artifact_manifest
from evalsys.taskgen.mode5.scoring import REGISTRY_VERSION


@pytest.fixture(autouse=True)
def offline_catalog(tmp_path, monkeypatch):
    """CLI unit tests must not download the public corpus or call HF."""
    root = tmp_path / "catalog-root"
    project = root / "games/shadow_walker"
    project.mkdir(parents=True)
    (project / "project.godot").write_text("[application]\n", encoding="utf-8")
    (root / "catalog.json").write_text(json.dumps([
        {"id": "shadow_walker", "path": "games/shadow_walker"},
    ]), encoding="utf-8")
    calls = []

    def ensure(game_id, *, videos, root):
        calls.append((game_id, videos, root))
        return project

    monkeypatch.setattr("evalsys.taskgen.mode5.community_cli.repo_root", lambda: root)
    monkeypatch.setattr("evalsys.reference_data.ensure_game", ensure)
    monkeypatch.setattr("evalsys.frozen_data.load_manifest", lambda root: {
        "games": {"shadow_walker": {
            "primary_video": "games/shadow_walker/recording/playthrough_route.mp4",
        }},
    })
    return root, calls


def test_catalog_restores_hf_project_and_primary_film(offline_catalog):
    root, calls = offline_catalog
    assert _catalog_game_path("shadow_walker") == root / "games/shadow_walker"
    assert calls == []


def test_unknown_game_never_starts_a_download(offline_catalog):
    _, calls = offline_catalog
    with pytest.raises(CommunityDockerError, match="unknown game id"):
        _catalog_game_path("unknown")
    assert calls == []


def test_complete_local_materials_do_not_need_hf_or_content_hash(offline_catalog):
    root, calls = offline_catalog
    film = root / "games/shadow_walker/recording/playthrough_route.mp4"
    film.parent.mkdir()
    film.write_bytes(b"locally prepared reference film")
    _catalog_game_path("shadow_walker")
    assert calls == []


def test_reference_lfs_pointer_is_downloaded(offline_catalog):
    root, calls = offline_catalog
    film = root / "games/shadow_walker/recording/playthrough_route.mp4"
    film.parent.mkdir()
    film.write_bytes(b"version https://git-lfs.github.com/spec/v1\n")
    _catalog_game_path("shadow_walker")
    assert calls == []


def test_cli_exposes_release_commands_and_command_harness_option():
    parser = build_parser()
    for command in (
        "setup", "doctor", "license-request", "generate", "evaluate", "run", "summarize",
    ):
        with pytest.raises(SystemExit) as result:
            parser.parse_args([command, "--help"])
        assert result.value.code == 0
    parsed = parser.parse_args([
        "run", "--game", "shadow_walker", "--out", "run", "--model", "fixture",
        "--harness", "command", "--agent-command", "true",
    ])
    assert parsed.agent_command == "true"
    assert parsed.allow_unscored is False
    assert parsed.agent_timeout == 7200
    assert parsed.fidelity_judge == "none"


def test_run_refuses_uncalibrated_suite_before_agent_or_output(tmp_path, monkeypatch):
    suite = {"schema": "gamebench.unity-hidden-suite.v1", "game_id": "shadow_walker",
             "status": "pending", "runtime_ready": False}
    monkeypatch.setattr("evalsys.taskgen.mode5.release_data.load_released_suite", lambda *a: (suite, {}))
    args = build_parser().parse_args([
        "run", "--game", "shadow_walker", "--out", str(tmp_path / "run"),
        "--model", "fixture", "--harness", "command", "--agent-command", "true",
    ])
    with pytest.raises(CommunityDockerError, match="--allow-unscored"):
        cmd_run(args)
    assert not (tmp_path / "run").exists()
    _check_release_suite("shadow_walker", allow_unscored=True)



def test_release_suite_gate_accepts_verified_current_digest(monkeypatch):
    from evalsys.taskgen.unity.unity_suite_compiler import suite_content_digest
    suite = {"schema": "gamebench.unity-hidden-suite.v1", "game_id": "shadow_walker",
             "status": "calibrated", "runtime_ready": True}
    suite["content_digest"] = suite_content_digest(suite)
    gate = {"schema": "gamebench.unity-calibration.v1", "game_id": "shadow_walker",
            "status": "calibrated", "runtime_ready": True, "suite_digest": suite["content_digest"]}
    monkeypatch.setattr("evalsys.taskgen.mode5.release_data.load_released_suite", lambda *a: (suite, gate))
    _check_release_suite("shadow_walker", allow_unscored=False)
    gate["suite_digest"] = "sha256:stale"
    with pytest.raises(CommunityDockerError, match="matching calibration"):
        _check_release_suite("shadow_walker", allow_unscored=False)



def test_generate_game_id_resolves_through_repository_catalog():
    project = _catalog_game_path("shadow_walker")
    assert project.name == "shadow_walker"
    with pytest.raises(CommunityDockerError, match="unknown game id"):
        _catalog_game_path("not-a-real-game")


def test_community_scaffold_enables_offline_builtin_modules_without_touching_vm_source(tmp_path):
    from evalsys.taskgen.unity.unity_sdk import TARGET_UNITY_ROOT

    project = tmp_path / "target_unity"
    packages = project / "Packages"
    packages.mkdir(parents=True)
    manifest_source = TARGET_UNITY_ROOT / "Packages" / "manifest.json"
    lock_source = TARGET_UNITY_ROOT / "Packages" / "packages-lock.json"
    (packages / "manifest.json").write_bytes(manifest_source.read_bytes())
    (packages / "packages-lock.json").write_bytes(lock_source.read_bytes())
    sdk_source = TARGET_UNITY_ROOT / "Assets" / "GameBenchmarkSDK"
    sdk_target = project / "Assets" / "GameBenchmarkSDK"
    shutil.copytree(sdk_source, sdk_target)
    original_manifest = manifest_source.read_bytes()
    original_lock = lock_source.read_bytes()

    enable_community_engine_modules(project)
    manifest = json.loads((packages / "manifest.json").read_text(encoding="utf-8"))
    lock = json.loads((packages / "packages-lock.json").read_text(encoding="utf-8"))
    assert manifest["dependencies"]["com.unity.ugui"] == "2.0.0"
    assert lock["dependencies"]["com.unity.ugui"]["source"] == "builtin"
    assert lock["dependencies"]["com.unity.modules.ui"]["depth"] == 1
    assert manifest["dependencies"]["com.unity.modules.tilemap"] == "1.0.0"
    assert lock["dependencies"]["com.unity.modules.tilemap"]["dependencies"] == {
        "com.unity.modules.physics2d": "1.0.0",
    }
    assert lock["dependencies"]["com.unity.modules.audio"]["source"] == "builtin"
    assert manifest_source.read_bytes() == original_manifest
    assert lock_source.read_bytes() == original_lock
    assert not (sdk_source / "GBObservableState.cs.meta").exists()
    generated_meta = sdk_target / "GBObservableState.cs.meta"
    assert generated_meta.is_file()
    assert "MonoImporter:" in generated_meta.read_text(encoding="utf-8")
    assert "Assets/GameBenchmarkSDK/GBObservableState.cs.meta" in immutable_scaffold_paths(project)


def test_community_generate_selects_ugui_scaffold(tmp_path, monkeypatch, capsys):
    received = {}

    def fake_generate(*args, **kwargs):
        received.update(kwargs)
        return SimpleNamespace(root=tmp_path / "package")

    monkeypatch.setattr("evalsys.taskgen.generate.generate_task", fake_generate)
    assert cmd_generate(Namespace(game="shadow_walker", out=str(tmp_path / "package"))) == 0
    assert received["community_scaffold"] is True


def test_setup_can_build_images_before_operator_authenticates(tmp_path, monkeypatch):
    lock = {
        "agent_image": {"tag": "agent:test", "id": "sha256:agent"},
        "evaluator_image": {"tag": "evaluator:test", "id": "sha256:evaluator"},
    }
    monkeypatch.setattr(
        "evalsys.taskgen.mode5.community_cli.build_images", lambda **kwargs: lock,
    )
    args = Namespace(
        state_dir=str(tmp_path), unity_archives="", no_download=False,
        license_provider="", license_file="", unity_config_root="",
        floating_endpoint="", docker="docker",
    )
    assert cmd_setup(args) == 0
    assert json.loads((tmp_path / "image-lock.json").read_text())["agent_image"]["id"] == "sha256:agent"
    assert not (tmp_path / "last-preflight.json").exists()


def test_doctor_accepts_first_explicit_provider_without_saved_config(tmp_path, monkeypatch):
    # This is a CLI forwarding test, not an ACL integration check. Temporary
    # directories may live on non-NTFS release disks; provider security has its
    # own tests and must not be weakened to accommodate such a fixture.
    monkeypatch.setattr(
        "evalsys.taskgen.mode5.docker_license._validate_windows_acl", lambda path: None,
    )
    (tmp_path / "image-lock.json").write_text("{}", encoding="utf-8")
    license_home = tmp_path / "unity-home"
    license_home.mkdir(mode=0o700)
    license_home.chmod(0o700)
    observed = {}

    def fake_doctor(**kwargs):
        observed["provider"] = kwargs["license_provider"].kind
        return {"status": "pass", "checks": []}

    monkeypatch.setattr(
        "evalsys.taskgen.mode5.community_cli.run_doctor", fake_doctor,
    )
    args = Namespace(
        state_dir=str(tmp_path), license_provider="existing-home",
        license_file="", unity_config_root=str(license_home),
        floating_endpoint="", docker="docker", json=True,
    )
    assert cmd_doctor(args) == 0
    assert observed["provider"] == "existing-home"
    assert json.loads((tmp_path / "license-provider.json").read_text())["provider"] == "existing-home"


def test_community_state_journal_is_atomic_and_ordered(tmp_path):
    _record_phase(tmp_path, "created")
    _record_phase(tmp_path, "package_ready")
    _record_phase(tmp_path, "package_ready")
    state = json.loads((tmp_path / "mode5-state.json").read_text(encoding="utf-8"))
    assert state["schema"] == RUN_STATE_SCHEMA
    assert state["phase"] == "package_ready"
    assert [row["phase"] for row in state["history"]] == ["created", "package_ready"]
    assert not (tmp_path / "mode5-state.json.tmp").exists()


def test_resume_never_reuses_half_written_evaluation(tmp_path):
    evaluation = tmp_path / "evaluation"
    evaluation.mkdir()
    (evaluation / "report.json").write_text("{}", encoding="utf-8")
    with pytest.raises(CommunityDockerError, match="manifest"):
        _load_verified_report(evaluation)
    quarantined = _quarantine_incomplete_evaluation(evaluation)
    assert quarantined.is_dir()
    assert not evaluation.exists()
    assert quarantined.name.startswith("evaluation.incomplete-")


def test_resume_preserves_agent_timeout_provenance(tmp_path):
    from evalsys.taskgen.mode5.community_cli import _agent_completion
    agent = tmp_path / 'agent'
    agent.mkdir()
    stderr = agent / 'stderr.log'
    stderr.write_text('agent timeout after 7200s\n', encoding='utf-8')
    assert _agent_completion(tmp_path, {'agent_exit_code': 124, 'failure_kind': ''}) == {
        'exit_code': 124, 'failure_kind': 'agent_timeout', 'budget_exhausted': True,
    }
    stderr.write_text('other failure\n', encoding='utf-8')
    assert _agent_completion(tmp_path, {'agent_exit_code': 124})['budget_exhausted'] is False


@pytest.mark.parametrize(("detail", "reason"), [
    ("evaluator image does not match image-lock", "image_digest_mismatch"),
    ("Unity license initialization failed", "unity_license_invalid"),
    ("artifact manifest file set mismatch", "artifact_verification_failed"),
])
def test_evaluator_failure_record_has_stable_attribution(detail, reason):
    row = _evaluator_failure(CommunityDockerError(detail))
    assert row["attribution"] == "evaluator_infrastructure"
    assert row["reason_code"] == reason
    assert row["status"] == "failed"


def test_summary_never_pools_different_environment_profiles():
    def fixture(profile, score):
        return {
            "mode": "port",
            "scorecard": {
                "game_id": "shadow_walker", "registry_version": REGISTRY_VERSION,
                "evidence_score": summary_reading("shadow_walker", score),
                "headline": {"score": score, "ranking_eligible": True,
                             "ranking_scope": "community-v1"},
            },
            "environment": {"environment_class": "community-docker",
                            "profile_id": profile, "paper_compatible": False},
        }

    summary = _summarize_reports([fixture("profile-a", 25), fixture("profile-b", 75)])
    assert len(summary["groups"]) == 2
    assert {row["partial_mean_score"] for row in summary["groups"]} == {25.0, 75.0}
    assert summary["paper_compatible"] is False


def test_summary_never_pools_different_images_or_run_configuration():
    def fixture(image, timeout, score):
        return {
            "mode": "port",
            "scorecard": {
                "game_id": "shadow_walker", "registry_version": REGISTRY_VERSION,
                "evidence_score": summary_reading("shadow_walker", score),
                "headline": {"score": score, "ranking_eligible": True,
                             "ranking_scope": "community-v1"},
            },
            "environment": {
                "environment_class": "community-docker", "profile_id": "profile-a",
                "agent_image_id": image, "evaluator_image_id": "sha256:evaluator",
                "paper_compatible": False,
            },
            "run_config": {
                "harness": "codex", "model": "fixture", "provider": "openai",
                "budget": {"agent_timeout_seconds": timeout},
                "input": {"reference_video": True},
            },
        }

    summary = _summarize_reports([
        fixture("sha256:agent-a", 1800, 25),
        fixture("sha256:agent-b", 1800, 50),
        fixture("sha256:agent-b", 3600, 75),
    ])
    assert len(summary["groups"]) == 3
    assert {row["agent_image_id"] for row in summary["groups"]} == {
        "sha256:agent-a", "sha256:agent-b",
    }
    assert {row["budget_configuration"]["agent_timeout_seconds"]
            for row in summary["groups"]} == {1800, 3600}


def test_summary_excludes_development_smoke_even_if_scorecard_says_ranked():
    report = {
        "mode": "port",
        "scorecard": {
            "game_id": "shadow_walker", "registry_version": REGISTRY_VERSION,
            "evidence_score": summary_reading("shadow_walker", 0),
            "headline": {"score": 0.0, "ranking_eligible": True,
                         "ranking_scope": "mode5-community-docker-v1"},
        },
        "environment": {"environment_class": "community-docker", "profile_id": "profile-a"},
        "run_config": {"development_unscored": True},
    }
    group = _summarize_reports([report])["groups"][0]
    assert group["ranking_scope"].endswith("-development")
    assert group["scored"] == 0
    assert group["unscored"] == 1
    assert group["mean_score"] is None


def test_development_smoke_is_not_listed_in_leaderboard(tmp_path):
    report = {
        "mode": "port",
        "scorecard": {
            "game_id": "shadow_walker", "registry_version": REGISTRY_VERSION,
            "evidence_score": summary_reading("shadow_walker", 0),
            "headline": {"score": 0.0, "ranking_eligible": True,
                         "ranking_scope": "mode5-community-docker-v1"},
        },
        "environment": {"environment_class": "community-docker", "profile_id": "profile-a"},
        "run_config": {"development_unscored": True},
    }
    (tmp_path / "report.json").write_text(json.dumps(report), encoding="utf-8")
    write_artifact_manifest(tmp_path)
    assert cmd_summarize(Namespace(run=str(tmp_path))) == 0
    assert "mode5-community-docker-v1-development" not in (
        tmp_path / "leaderboard.md"
    ).read_text(encoding="utf-8")


def test_summarize_writes_json_csv_and_leaderboard(tmp_path):
    report = {
        "mode": "port", "game_id": "shadow_walker",
        "scorecard": {
            "game_id": "shadow_walker", "registry_version": REGISTRY_VERSION,
            "evidence_score": summary_reading("shadow_walker", 50),
            "headline": {"score": 50, "ranking_eligible": True,
                         "ranking_scope": "community-v1"},
        },
        "environment": {
            "environment_class": "community-docker", "profile_id": "profile-a",
            "agent_image_id": "sha256:agent", "evaluator_image_id": "sha256:evaluator",
        },
        "run_config": {
            "harness": "codex", "model": "fixture", "provider": "openai",
            "budget": {"agent_timeout_seconds": 1800},
            "input": {"reference_video": True},
        },
    }
    (tmp_path / "report.json").write_text(json.dumps(report), encoding="utf-8")
    write_artifact_manifest(tmp_path)
    assert cmd_summarize(Namespace(run=str(tmp_path))) == 0
    assert (tmp_path / "summary.json").is_file()
    assert (tmp_path / "summary.csv").is_file()
    assert (tmp_path / "leaderboard.md").is_file()
    csv_text = (tmp_path / "summary.csv").read_text(encoding="utf-8")
    assert "sha256:agent" in csv_text
    assert "agent_timeout_seconds" in csv_text
    markdown = (tmp_path / "leaderboard.md").read_text(encoding="utf-8")
    assert "Partial mean (diagnostic)" in markdown
    assert "None" not in markdown
    assert "1 / 41" in markdown
