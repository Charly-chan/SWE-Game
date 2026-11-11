

import json
import os
from pathlib import Path
import shutil
import subprocess
import sys

import pytest


ROOT = Path(__file__).resolve().parents[3]


@pytest.fixture
def entrypoint(tmp_path):
    repo = tmp_path / "repo"
    (repo / "eval/tools").mkdir(parents=True)
    (repo / "eval/evalsys/bin").mkdir(parents=True)
    shutil.copyfile(ROOT / "evaluate.sh", repo / "evaluate.sh")
    shutil.copyfile(ROOT / "eval/tools/gb_env.sh", repo / "eval/tools/gb_env.sh")
    (repo / "eval/evalsys/bin/bench").write_text(
        """import json, os, sys
from pathlib import Path
args = sys.argv[1:]
Path(os.environ['CAPTURE']).write_text(json.dumps({
    'args': args, 'key_loaded': bool(os.environ.get('ENTRYPOINT_TEST_KEY')),
}))
if os.environ.get('NO_REPORT') != '1':
    out = Path(args[args.index('--out') + 1])
    out.mkdir(parents=True)
    report = json.loads(os.environ['REPORT_DATA']) if 'REPORT_DATA' in os.environ else {
        'resolved': False, 'scorecard': {},
        'replay_reading': {'status': 'judged'},
    }
    (out / 'report.json').write_text(json.dumps(report))
sys.exit(int(os.environ.get('BENCH_RC', '0')))
""",
        encoding="utf-8",
    )
    package = tmp_path / "package"
    submission = tmp_path / "submission"
    package.mkdir()
    submission.mkdir()
    (package / "manifest.json").write_text(json.dumps({"mode": "brief", "game_id": "fixture"}))
    config = tmp_path / "api.env"
    config.write_text(
        "GAMEBENCH_VLM_KEY_ENV=ENTRYPOINT_TEST_KEY\n"
        "ENTRYPOINT_TEST_KEY=test-only-not-a-provider-key\n",
        encoding="utf-8",
    )
    env = os.environ.copy()
    for name in tuple(env):
        if name.startswith(("GAMEBENCH_VLM_", "GAMECRAFT_BENCH_JUDGE_", "GB_VISUAL_")) or name in {
            "MICU_API_KEY", "OPENAI_API_KEY", "AUTO_CODE_API_KEY",
            "ANTHROPIC_API_KEY", "CLAUDE_API_KEY", "ENTRYPOINT_TEST_KEY",
        }:
            env.pop(name)
    env.update(
        GB_VENV=str(tmp_path / "no-venv"),
        GB_PYTHON=sys.executable,
        GB_API_ENV=str(config),
        PYTHONPATH=str(ROOT / "eval/evalsys"),
        CAPTURE=str(tmp_path / "capture.json"),
    )

    def run(*options, extra_env=None, positional=True):
        args = ["bash", str(repo / "evaluate.sh")]
        if positional:
            args.extend([str(package), str(submission), "--out", str(tmp_path / "out")])
        return subprocess.run(
            [*args, *options], env=env | (extra_env or {}),
            capture_output=True, text=True, check=False,
        )

    return run, Path(env["CAPTURE"])


@pytest.mark.parametrize("judge", [None, "vlm", "local", "none"])
def test_visual_mode_forwarding_and_reproduction(entrypoint, judge):
    run, capture = entrypoint
    result = run(*(["--visual-judge", judge] if judge else []))
    assert result.returncode == 0, result.stderr
    called = json.loads(capture.read_text())
    args = called["args"]
    expected = judge or "none"
    assert args[args.index("--visual-judge") + 1] == expected

    assert "--score-against-source" not in args
    assert called["key_loaded"] == (expected == "vlm")
    assert "replay_visual_status=judged" in result.stdout
    assert "test-only-not-a-provider-key" not in result.stdout + result.stderr


def test_reproduction_stage_is_an_explicit_opt_in(entrypoint):
    run, capture = entrypoint
    result = run("--visual-judge", "none", "--score-against-source")
    assert result.returncode == 0, result.stderr
    args = json.loads(capture.read_text())["args"]
    assert "--score-against-source" in args
    assert args[args.index("--visual-judge") + 1] == "none"


def test_scoring_registry_is_explicitly_forwarded(entrypoint):
    run, capture = entrypoint
    result = run("--visual-judge", "none", "--registry", "2026-09-10.mode34b")
    assert result.returncode == 0, result.stderr
    args = json.loads(capture.read_text())["args"]
    assert args[args.index("--registry") + 1] == "2026-09-10.mode34b"


def test_explicit_mode_overrides_environment(entrypoint):
    run, capture = entrypoint
    result = run("--visual-judge", "local", extra_env={"GB_VISUAL_JUDGE": "none"})
    assert result.returncode == 0
    args = json.loads(capture.read_text())["args"]
    assert args[args.index("--visual-judge") + 1] == "local"


def test_environment_default_can_disable_api(entrypoint):
    run, capture = entrypoint
    result = run(extra_env={"GB_VISUAL_JUDGE": "none"})
    assert result.returncode == 0
    called = json.loads(capture.read_text())
    assert called["args"][-1] == "none"
    assert not called["key_loaded"]


@pytest.mark.parametrize("options", [["--visual-judge"], ["--visual-judge", "invalid"]])
def test_invalid_visual_option_stops_before_engine(entrypoint, options):
    run, capture = entrypoint
    result = run(*options)
    assert result.returncode == 2
    assert "--visual-judge" in result.stderr
    assert not capture.exists()


def test_missing_key_stops_before_engine(entrypoint, tmp_path):
    run, capture = entrypoint
    result = run("--visual-judge", "vlm", extra_env={"GB_API_ENV": str(tmp_path / "missing.env")})
    assert result.returncode == 2
    assert "needs a key" in result.stderr
    assert "./evaluate.sh <package_dir> <submission_dir> --visual-judge none" in result.stderr
    assert not capture.exists()


def test_invalid_provider_stops_before_engine(entrypoint):
    run, capture = entrypoint
    result = run("--visual-judge", "vlm", extra_env={"GAMEBENCH_VLM_PROVIDER": "not-a-provider"})
    assert result.returncode == 2
    assert "GAMEBENCH_VLM_PROVIDER" in result.stderr
    assert not capture.exists()


def test_strict_failure_exit_code_is_preserved(entrypoint):
    run, _ = entrypoint
    result = run("--visual-judge", "none", extra_env={"BENCH_RC": "1"})
    assert result.returncode == 1
    assert "resolved=no" in result.stdout


def test_paper_labels_leave_report_and_card_json_unchanged(entrypoint):
    run, capture = entrypoint
    data = {
        "resolved": False,
        "scorecard": {
            "registry_version": "2026-09-10.mode34b",
            "weighted_total": {"score": 70, "headline_ceiling": 85},
            "ranking_note": "S-card uncalibrated; O-card measured",
            "ranking_basis": "objective_and_calibrated_perceptual",
        },
        "replay_reading": {"status": "measured", "detail": "S-card replay evidence"},
    }
    result = run("--visual-judge", "none", extra_env={"REPORT_DATA": json.dumps(data)})
    assert result.returncode == 0, result.stderr
    assert "Objective Behavioral Evaluation" in result.stdout
    assert "Perceptual Quality Assessment" in result.stdout
    assert "O-card" not in result.stdout and "S-card" not in result.stdout
    args = json.loads(capture.read_text())["args"]
    out = Path(args[args.index("--out") + 1])
    assert json.loads((out / "report.json").read_text()) == data
    assert json.loads((out / "card.json").read_text()) == data["scorecard"]


def test_missing_report_is_an_evaluation_error(entrypoint):
    run, _ = entrypoint
    result = run("--visual-judge", "none", extra_env={"BENCH_RC": "7", "NO_REPORT": "1"})
    assert result.returncode == 2
    assert "evaluation exited 7 without writing" in result.stderr


def test_help_works_without_paths_or_keys(entrypoint):
    run, capture = entrypoint
    result = run("--help", positional=False)
    assert result.returncode == 0
    assert "--visual-judge vlm|local|none" in result.stdout
    assert not capture.exists()


def test_visual1_uses_upstream_key_not_the_legacy_provider(entrypoint):
    run, capture = entrypoint
    result = run("--registry", "2026-09-11.visual1", "--visual-judge", "vlm",
                 extra_env={"GAMECRAFT_BENCH_JUDGE_OPENAI_API_KEY": "gamecraft-test-only",
                            "GAMEBENCH_VLM_PROVIDER": "not-used"})
    assert result.returncode == 0, result.stderr
    assert capture.exists()
    assert "gamecraft-test-only" not in result.stdout + result.stderr


def test_default_registry_accepts_the_claude_code_rubric_transport(entrypoint):
    run, capture = entrypoint
    result = run("--visual-judge", "vlm", extra_env={
        "GAMEBENCH_VLM_PROVIDER": "claude_code", "GAMEBENCH_CLAUDE_CODE_BIN": sys.executable,
    })
    assert result.returncode == 0, result.stderr
    assert capture.exists()


def test_rubric_claude_missing_executable_stops_before_engine(entrypoint):
    run, capture = entrypoint
    result = run("--visual-judge", "vlm", extra_env={
        "GAMEBENCH_VLM_PROVIDER": "anthropic", "GAMEBENCH_CLAUDE_CODE_BIN": "/missing/claude",
    })
    assert result.returncode == 2
    assert "GAMEBENCH_CLAUDE_CODE_BIN" in result.stderr
    assert not capture.exists()


def test_rubric_preflight_does_not_accept_an_unused_legacy_key(entrypoint, tmp_path):
    run, capture = entrypoint
    result = run("--visual-judge", "vlm", extra_env={
        "GB_API_ENV": str(tmp_path / "missing.env"), "MICU_API_KEY": "legacy-test-only",
    })
    assert result.returncode == 2
    assert "needs a key" in result.stderr
    assert not capture.exists()


def test_explicit_historical_registry_keeps_legacy_key_support(entrypoint, tmp_path):
    run, capture = entrypoint
    result = run("--registry", "2026-09-11.evidence1", "--visual-judge", "vlm", extra_env={
        "GB_API_ENV": str(tmp_path / "missing.env"), "MICU_API_KEY": "legacy-test-only",
    })
    assert result.returncode == 0, result.stderr
    assert capture.exists()








def test_mode5_rejects_vlm_before_engine(entrypoint, tmp_path):
    run, capture = entrypoint
    (tmp_path / "package/manifest.json").write_text(json.dumps({"mode": "port", "game_id": "fixture"}))
    result = run("--visual-judge", "vlm", extra_env={"GAMEBENCH_VLM_PROVIDER": "responses"})
    assert result.returncode == 2
    assert "does not use a VLM judge" in result.stderr
    assert not capture.exists()


def test_objective_only_label_and_number_are_printed(entrypoint):
    run, _ = entrypoint
    report = {"resolved": False, "scorecard": {
        "weighted_total": {"score": None}, "assessment_status": "objective_only",
        "objective_total": {"score": 42, "headline_ceiling": 85},
    }}
    result = run(extra_env={"REPORT_DATA": json.dumps(report)})
    assert result.returncode == 0
    assert "objective_total=42" in result.stdout
    assert "assessment_status=objective_only" in result.stdout
    assert "not a complete composite score" in result.stdout


def test_demo_visuals_and_coverage_without_whole_run_reading(entrypoint):
    run, _ = entrypoint
    report = {
        "resolved": False, "scorecard": {}, "replay_reading": None,
        "demonstrations": {
            "expected": ["jump", "pickup"], "observed": ["jump"],
            "action_caused": ["jump"], "missing": ["pickup"], "measured": True,
            "segments": [{"id": "jump-demo", "observed": ["jump"], "action_caused": ["jump"]}],
        },
        "demonstration_visuals": [{"id": "jump-demo", "reading": {"status": "judged", "detail": "jump is visible"}}],
    }
    result = run(extra_env={"REPORT_DATA": json.dumps(report)})
    assert result.returncode == 0, result.stderr
    assert "replay_visual_status=not_measured" in result.stdout
    assert "independent feature-demo visual results are listed separately" in result.stdout
    assert "demonstration_feature_coverage=1/2" in result.stdout
    assert "demonstration_action_caused_coverage=1/2" in result.stdout
    assert "demonstration_missing_features=pickup" in result.stdout
    assert "demonstration[jump-demo].feature_coverage=1/2" in result.stdout
    assert "demonstration[jump-demo].visual_status=judged" in result.stdout
    assert "demonstration[jump-demo].visual_note=jump is visible" in result.stdout
