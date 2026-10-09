

import csv
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys

import pytest


ROOT = Path(__file__).resolve().parents[3]


@pytest.mark.parametrize("mode", ["brief", "gdd", "skeleton", "bugfix"])
def test_docker_modes_select_image_without_host_unity(entrypoint, mode):
    run, capture, out = entrypoint
    # The tags come from the environment (gb_env.sh ships the site defaults), so
    # pin both here: what this asserts is the mode-to-engine rule, not one site's
    # registry path.
    godot_image, unity_image = "probe-registry/godot:test", "probe-registry/unity:test"
    result = run("--game", "canopy_dash", "--mode", mode, "--sandbox", "docker",
                 "--eval", "off", "--dry-run",
                 extra_env={"UNITY_BIN": "/missing/Unity",
                            "GB_SANDBOX_IMAGE": godot_image,
                            "GB_UNITY_SANDBOX_IMAGE": unity_image})
    assert result.returncode == 0, result.stderr
    image = unity_image if mode == "port" else godot_image
    calls = [json.loads(line) for line in capture.read_text().splitlines()]
    assert calls
    for args in calls:
        assert args[args.index("--agent-sandbox") + 1] == "docker"
        assert args[args.index("--agent-docker-image") + 1] == image
    manifest = json.loads((out / "run.json").read_text())
    assert manifest["params"]["agent_docker_image"] == image
    assert manifest["harness_versions_source"] == "per-cell agent/env.json"
    assert manifest["harness_cli_versions"] == {"claude": None, "codex": None}




@pytest.mark.parametrize("mode,expected", [
    ("brief", "gamebench-agent:godot-4.5.1"),
])
def test_docker_defaults_match_locally_built_images(entrypoint, mode, expected):
    run, capture, _out = entrypoint
    result = run("--game", "canopy_dash", "--mode", mode, "--sandbox", "docker",
                 "--eval", "off", "--dry-run",
                 extra_env={"GB_SANDBOX_IMAGE": "", "GB_UNITY_SANDBOX_IMAGE": ""})
    assert result.returncode == 0, result.stderr
    for args in map(json.loads, capture.read_text().splitlines()):
        assert args[args.index("--agent-docker-image") + 1] == expected
        assert args[args.index("--agent-docker-image") + 1] == expected


def test_docker_resume_cannot_switch_toolchain_image(entrypoint):
    run, capture, out = entrypoint
    options = ("--game", "canopy_dash", "--mode", "brief", "--sandbox", "docker", "--dry-run")
    first = run(*options, "--docker-image", "custom:one")
    assert first.returncode == 0, first.stderr
    second = run(*options, "--docker-image", "custom:two", "--resume")
    assert second.returncode != 0
    assert "agent_docker_image differs" in second.stderr


FAKE_BENCH = """import json, os, sys
from pathlib import Path
args = sys.argv[1:]
with open(os.environ['CAPTURE'], 'a', encoding='utf-8') as fh:
    fh.write(json.dumps(args) + '\\n')
out = Path(args[args.index('--out') + 1])
(out / 'package').mkdir(parents=True, exist_ok=True)
game = args[args.index('--game') + 1]
if game == os.environ.get('BLOCK_GAME'):
    # What run-task-matrix does when generate_task refuses: a manifest with
    # blockers, state.json status=package_blocked, no request, exit 1.
    (out / 'package' / 'manifest.json').write_text(json.dumps({'blockers': ['stub blocker']}))
    (out / 'state.json').write_text(json.dumps(
        {'status': 'package_blocked', 'phase': 'package_blocked', 'detail': 'stub blocker: film too large'}))
    raise SystemExit(1)
(out / 'package' / 'manifest.json').write_text('{}')
(out / 'state.json').write_text(json.dumps({'status': 'generated', 'phase': 'generated'}))
(out / 'agent').mkdir(exist_ok=True)
(out / 'agent' / 'request.json').write_text(json.dumps({'command': ['fake-harness', *args]}))
"""


@pytest.fixture
def entrypoint(tmp_path):
    repo = tmp_path / "repo"
    (repo / "eval/tools").mkdir(parents=True)
    (repo / "eval/evalsys/bin").mkdir(parents=True)
    for rel in (
        "run_benchmark.sh", "eval/tools/gb_env.sh", "eval/tools/summarize_results.py",
        "catalog.json",
    ):
        shutil.copyfile(ROOT / rel, repo / rel)
    (repo / "eval/evalsys/bin/bench").write_text(FAKE_BENCH, encoding="utf-8")
    (repo / "gb").write_text(
        "#!/usr/bin/env bash\n"
        "\"$GB_PYTHON\" - \"$@\" <<'PY'\n"
        "import json, os, sys\n"
        "from pathlib import Path\n"
        "if '--out' in sys.argv: Path(sys.argv[sys.argv.index('--out') + 1]).mkdir(parents=True, exist_ok=True)\n"
        "with open(os.environ['CAPTURE'], 'a', encoding='utf-8') as fh:\n"
        "    fh.write(json.dumps(sys.argv[1:]) + '\\n')\n"
        "if sys.argv[1:3] == ['mode5', 'run'] and os.environ.get('FAKE_GB_RUN_RC'):\n"
        "    raise SystemExit(int(os.environ['FAKE_GB_RUN_RC']))\n"
        "PY\n",
        encoding="utf-8",
    )
    (repo / "gb").chmod(0o755)
    (repo / "scripts").mkdir()
    (repo / "scripts/fetch_reference_data.py").write_text(
        "import json, os, sys\n"
        "with open(os.environ['DOWNLOAD_CAPTURE'], 'a') as f: f.write(json.dumps(sys.argv[1:]) + '\\n')\n"
    )
    # run_benchmark.sh records `git rev-parse HEAD` of its own checkout.
    git_env = {
        "GIT_AUTHOR_NAME": "t", "GIT_AUTHOR_EMAIL": "t@t", "GIT_COMMITTER_NAME": "t",
        "GIT_COMMITTER_EMAIL": "t@t", "HOME": str(tmp_path), "GIT_CONFIG_GLOBAL": "/dev/null",
    }
    subprocess.run(["git", "init", "-q"], cwd=repo, check=True, env=os.environ | git_env)
    subprocess.run(["git", "add", "."], cwd=repo, check=True, env=os.environ | git_env)
    subprocess.run(
        ["git", "commit", "-q", "-m", "fixture"], cwd=repo, check=True, env=os.environ | git_env,
    )
    env = os.environ.copy()
    for name in tuple(env):
        if name in {"MICU_API_KEY", "OPENAI_API_KEY", "ANTHROPIC_API_KEY", "ANTHROPIC_AUTH_TOKEN", "UNITY_BIN",
                    "OPENAI_BASE_URL", "ANTHROPIC_BASE_URL", "CODEX_AUTH_FILE"} or name.startswith(
                        ("GB_CODEX_", "GB_CLAUDE_")):
            env.pop(name)
    env.update(
        GB_VENV=str(tmp_path / "no-venv"),
        GB_PYTHON=sys.executable,
        GB_API_ENV=str(tmp_path / "missing.env"),
        GB_SCRATCH_ROOT=str(tmp_path / "scratch"),
        PYTHONPATH=str(ROOT / "eval/evalsys"),
        CAPTURE=str(tmp_path / "capture.jsonl"),
        DOWNLOAD_CAPTURE=str(tmp_path / "downloads.jsonl"),
    )

    def run(*options, extra_env=None):
        return subprocess.run(
            ["bash", str(repo / "run_benchmark.sh"), "--harness", "codex", "--model", "m",
             "--out", str(tmp_path / "out"), *options],
            env=env | (extra_env or {}), capture_output=True, text=True, check=False,
        )

    return run, Path(env["CAPTURE"]), tmp_path / "out"


def test_port_dry_run_enumerates_one_cell_per_catalog_game(entrypoint):
    run, capture, out = entrypoint
    catalog = json.loads((ROOT / "catalog.json").read_text(encoding="utf-8"))
    # Packaging needs no Unity: a bogus UNITY_BIN must not stop a dry-run.
    result = run("--game", "all", "--mode", "port", "--dry-run",
                 extra_env={"UNITY_BIN": "/nonexistent/Editor/Unity"})
    assert result.returncode == 0, result.stderr
    calls = [json.loads(line) for line in capture.read_text().splitlines()]
    assert len(calls) == len(catalog) == 41
    assert all(args[:2] == ["mode5", "generate"] for args in calls)
    assert {args[args.index("--game") + 1] for args in calls} == {row["id"] for row in catalog}
    assert result.stdout.count("Mode 5 package preview:") == 41
    assert "no harness launched" in result.stdout
    # Same cell layout as the other per-game modes: <game>__port__<harness>__<model>.
    cells = sorted(p.name for p in (out / "cells").iterdir())
    assert cells[0] == f"{sorted(row['id'] for row in catalog)[0]}__port__codex__m"
    assert json.loads((out / "run.json").read_text())["params"]["mode"] == "port"


def test_port_dry_run_single_game(entrypoint):
    run, capture, _ = entrypoint
    result = run("--game", "shadow_walker", "--mode", "port", "--dry-run")
    assert result.returncode == 0, result.stderr
    calls = [json.loads(line) for line in capture.read_text().splitlines()]
    assert [args[args.index("--game") + 1] for args in calls] == ["shadow_walker"]


def test_port_community_profile_delegates_to_standalone_mode5_cli(entrypoint):
    run, capture, out = entrypoint
    result = run(
        "--game", "shadow_walker", "--mode", "port",
        "--profile", "community-docker", "--mode5-state-dir", "/private/state",
        "--docker", "docker-custom",
    )
    assert result.returncode == 0, result.stderr
    calls = [json.loads(line) for line in capture.read_text(encoding="utf-8").splitlines()]
    run_call = calls[0]
    assert run_call[:2] == ["mode5", "run"]
    assert run_call[run_call.index("--state-dir") + 1] == "/private/state"
    assert run_call[run_call.index("--docker") + 1] == "docker-custom"
    assert calls[-1][:2] == ["mode5", "summarize"]
    manifest = json.loads((out / "run.json").read_text(encoding="utf-8"))
    assert manifest["profile"] == "community-docker"
    assert manifest["paper_compatible"] is False


def test_port_community_profile_propagates_a_failed_agent_run(entrypoint):
    run, capture, _out = entrypoint
    result = run(
        "--game", "shadow_walker", "--mode", "port",
        extra_env={"FAKE_GB_RUN_RC": "2"},
    )
    assert result.returncode == 2
    assert "Mode 5 run failed" in result.stderr
    calls = [json.loads(line) for line in capture.read_text().splitlines()]
    assert len(calls) == 1 and calls[0][:2] == ["mode5", "run"]


def test_port_resume_only_applies_to_started_game(entrypoint):
    run, capture, out = entrypoint
    options = ("--game", "shadow_walker", "--mode", "port", "--resume")
    first = run(*options)
    assert first.returncode == 0, first.stderr
    first_args = [json.loads(line) for line in capture.read_text().splitlines()]
    assert "--resume" not in first_args[0]
    cell = out / "cells/shadow_walker__port__codex__m"
    (cell / "run.json").write_text("{}\n")
    second = run(*options)
    assert second.returncode == 0, second.stderr
    second_args = [json.loads(line) for line in capture.read_text().splitlines()]
    assert "--resume" in second_args[2]


@pytest.mark.parametrize("judge", ["vlm", "local"])
def test_port_community_profile_rejects_nonprotocol_visual_judge(entrypoint, judge):
    run, capture, _out = entrypoint
    result = run(
        "--game", "shadow_walker", "--mode", "port",
        "--profile", "community-docker", "--visual-judge", judge,
    )
    assert result.returncode == 2
    assert "--visual-judge must be none" in result.stderr
    assert not capture.exists()


def test_bugfix_does_not_download_an_agent_invisible_reference_movie(entrypoint, tmp_path):
    run, capture, _out = entrypoint
    env_script = tmp_path / "repo/eval/tools/gb_env.sh"
    with env_script.open("a") as stream:
        stream.write('\ngb_ensure_reference_film() { echo "unexpected reference download" >&2; return 91; }\n')
    result = run("--game", "canopy_dash", "--mode", "bugfix", "--dry-run")
    assert result.returncode == 0, result.stderr
    assert capture.exists()
    assert "unexpected reference download" not in result.stderr
    downloads = [json.loads(line) for line in (tmp_path / "downloads.jsonl").read_text().splitlines()]
    assert downloads and all(args == ["--game", "canopy_dash"] for args in downloads)


def test_mode1_no_video_dry_run_records_condition_and_preserves_visual_judge(entrypoint):
    run, capture, out = entrypoint
    result = run("--game", "canopy_dash", "--mode", "brief", "--reference-video", "off",
                 "--visual-judge", "vlm", "--dry-run")
    assert result.returncode == 0, result.stderr
    calls = [json.loads(line) for line in capture.read_text().splitlines()]
    assert len(calls) == 1
    assert calls[0][calls[0].index("--reference-video") + 1] == "off"
    assert calls[0][calls[0].index("--visual-judge") + 1] == "vlm"
    assert json.loads((out / "run.json").read_text())["params"]["reference_video"] == "off"


@pytest.mark.parametrize("choice", [None, "off", "on"])
def test_brief_design_choice_reaches_worker_and_is_frozen(entrypoint, choice):
    run, capture, out = entrypoint
    options = [] if choice is None else ["--brief-design", choice]
    result = run("--game", "canopy_dash", "--mode", "brief", "--dry-run", *options)
    assert result.returncode == 0, result.stderr
    expected = choice or "off"
    calls = [json.loads(line) for line in capture.read_text().splitlines()]
    assert calls[0][calls[0].index("--brief-design") + 1] == expected
    assert json.loads((out / "run.json").read_text())["params"]["brief_design"] == expected
    other = "on" if expected == "off" else "off"
    resumed = run("--game", "canopy_dash", "--mode", "brief", "--dry-run", "--resume",
                  "--brief-design", other)
    assert resumed.returncode != 0
    assert "brief_design differs" in resumed.stderr


def test_mode1_cannot_resume_video_arm_in_the_no_video_directory(entrypoint):
    run, _capture, _out = entrypoint
    first = run("--game", "canopy_dash", "--mode", "brief", "--reference-video", "off", "--dry-run")
    assert first.returncode == 0, first.stderr
    second = run("--game", "canopy_dash", "--mode", "brief", "--reference-video", "on", "--resume", "--dry-run")
    assert second.returncode != 0
    assert "use a separate --out directory" in second.stderr


@pytest.mark.parametrize("mode", ["gdd", "skeleton", "bugfix", "port"])
def test_no_video_cli_refuses_modes_other_than_brief(entrypoint, mode):
    run, capture, _out = entrypoint
    result = run("--game", "canopy_dash", "--mode", mode, "--reference-video", "off", "--dry-run")
    assert result.returncode == 2
    assert "only to --mode brief" in result.stderr
    assert not capture.exists()








def test_port_rejects_unknown_profile_before_any_work(entrypoint):
    run, capture, out = entrypoint
    result = run("--game", "shadow_walker", "--mode", "port", "--profile", "unknown")
    assert result.returncode == 2
    assert "invalid --profile" in result.stderr
    assert not capture.exists()
    assert not out.exists()


def test_port_live_run_uses_community_without_host_editor(entrypoint):
    run, capture, _ = entrypoint
    result = run("--game", "shadow_walker", "--mode", "port",
                 extra_env={"UNITY_BIN": "/nonexistent/Editor/Unity"})
    assert result.returncode == 0, result.stderr
    calls = [json.loads(line) for line in capture.read_text().splitlines()]
    assert calls[0][:2] == ["mode5", "run"]
    # No override: the canonical Python CLI owns the 7200-second default.
    assert "--agent-timeout" not in calls[0]
    assert calls[0][calls[0].index("--fidelity-judge") + 1] == "none"


def _route(capture):
    calls = [json.loads(line) for line in capture.read_text().splitlines()]
    assert len(calls) == 1
    args = calls[0]
    opt = lambda name: args[args.index(name) + 1] if name in args else None
    return args, opt


def test_env_file_base_url_selects_the_codex_gateway_route(entrypoint, tmp_path):

    run, capture, _ = entrypoint
    env_file = tmp_path / "gb_api.env"
    env_file.write_text(
        "OPENAI_API_KEY=test-only-not-a-key-0001\nOPENAI_BASE_URL=https://gw.example/v1\n",
        encoding="utf-8",
    )
    result = run("--game", "canopy_dash", "--mode", "brief", "--dry-run",
                 extra_env={"GB_API_ENV": str(env_file)})
    assert result.returncode == 0, result.stderr
    args, opt = _route(capture)
    assert opt("--agent-provider") == "custom"
    assert opt("--agent-base-url") == "https://gw.example/v1"
    assert opt("--agent-key-env") == "OPENAI_API_KEY"
    assert opt("--agent-env-file") == str(env_file)
    assert "--agent-auth-file" not in args
    assert "route: codex -> https://gw.example/v1, key from $OPENAI_API_KEY" in result.stdout
    assert "test-only-not-a-key-0001" not in result.stdout + result.stderr + capture.read_text()


def test_env_file_without_base_url_selects_the_native_codex_provider(entrypoint, tmp_path):
    run, capture, _ = entrypoint
    env_file = tmp_path / "gb_api.env"
    env_file.write_text("OPENAI_API_KEY=\nOPENAI_BASE_URL=\nANTHROPIC_API_KEY=\n", encoding="utf-8")
    result = run("--game", "canopy_dash", "--mode", "brief", "--dry-run",
                 extra_env={"GB_API_ENV": str(env_file)})
    assert result.returncode == 0, result.stderr
    args, opt = _route(capture)
    assert opt("--agent-provider") == "openai"
    assert "--agent-base-url" not in args and "--agent-key-env" not in args
    assert "route: codex native openai provider" in result.stdout


def test_missing_env_file_dry_run_needs_no_key_and_passes_no_env_file(entrypoint):
    run, capture, _ = entrypoint
    result = run("--game", "canopy_dash", "--mode", "brief", "--dry-run")
    assert result.returncode == 0, result.stderr
    args, opt = _route(capture)
    assert opt("--agent-provider") == "openai"
    assert "--agent-env-file" not in args


def test_legacy_micu_key_name_still_routes_to_the_micu_gateway(entrypoint, tmp_path):
    run, capture, _ = entrypoint
    env_file = tmp_path / "gb_api.env"
    env_file.write_text("export MICU_API_KEY=test-only-not-a-key-0002\n", encoding="utf-8")
    result = run("--game", "canopy_dash", "--mode", "brief", "--dry-run",
                 extra_env={"GB_API_ENV": str(env_file)})
    assert result.returncode == 0, result.stderr
    _, opt = _route(capture)
    assert opt("--agent-provider") == "custom"
    assert opt("--agent-base-url") == "https://www.micuapi.ai/v1"
    assert opt("--agent-key-env") == "MICU_API_KEY"


def test_claude_route_defaults_to_official_and_honours_anthropic_base_url(entrypoint, tmp_path):
    run, capture, _ = entrypoint
    env_file = tmp_path / "gb_api.env"
    env_file.write_text("ANTHROPIC_API_KEY=test-only-not-a-key-0003\nANTHROPIC_BASE_URL=\n", encoding="utf-8")
    result = run("--game", "canopy_dash", "--mode", "brief", "--dry-run", "--harness", "claude",
                 extra_env={"GB_API_ENV": str(env_file)})
    assert result.returncode == 0, result.stderr
    _, opt = _route(capture)
    assert opt("--agent-backend") == "claude"
    assert opt("--agent-base-url") == "https://api.anthropic.com"
    assert opt("--agent-key-env") == "ANTHROPIC_API_KEY"
    capture.unlink()
    env_file.write_text("ANTHROPIC_API_KEY=k\nANTHROPIC_BASE_URL=https://api.moonshot.ai/anthropic\n")
    result = run("--game", "canopy_dash", "--mode", "brief", "--dry-run", "--harness", "claude",
                 "--out", str(tmp_path / "out2"), extra_env={"GB_API_ENV": str(env_file)})
    assert result.returncode == 0, result.stderr
    _, opt = _route(capture)
    assert opt("--agent-base-url") == "https://api.moonshot.ai/anthropic"


def test_claude_route_uses_anthropic_auth_token_only_when_the_api_key_is_empty(entrypoint, tmp_path):
    run, capture, _ = entrypoint
    env_file = tmp_path / "gb_api.env"
    env_file.write_text("ANTHROPIC_API_KEY=\nANTHROPIC_AUTH_TOKEN=test-only-not-a-key-0004\n"
                        "ANTHROPIC_BASE_URL=https://api.moonshot.ai/anthropic\n", encoding="utf-8")
    result = run("--game", "canopy_dash", "--mode", "brief", "--dry-run", "--harness", "claude",
                 extra_env={"GB_API_ENV": str(env_file)})
    assert result.returncode == 0, result.stderr
    _, opt = _route(capture)
    assert opt("--agent-key-env") == "ANTHROPIC_AUTH_TOKEN"
    assert opt("--agent-base-url") == "https://api.moonshot.ai/anthropic"
    assert "key from $ANTHROPIC_AUTH_TOKEN" in result.stdout
    assert "test-only-not-a-key-0004" not in result.stdout + result.stderr + capture.read_text()

    capture.unlink()
    env_file.write_text("ANTHROPIC_API_KEY=k\nANTHROPIC_AUTH_TOKEN=t\n", encoding="utf-8")
    result = run("--game", "canopy_dash", "--mode", "brief", "--dry-run", "--harness", "claude",
                 "--out", str(tmp_path / "out2"), extra_env={"GB_API_ENV": str(env_file)})
    assert result.returncode == 0, result.stderr
    _, opt = _route(capture)
    assert opt("--agent-key-env") == "ANTHROPIC_API_KEY"


CLAUDE_MICU_HINT = ("hint: Claude route resolves to the official endpoint (api.anthropic.com). To use the "
                    "MICU gateway set ANTHROPIC_BASE_URL=https://www.micuapi.ai in .gb_api.env, or pass "
                    "--provider micu.")
CODEX_MICU_HINT = "hint: Codex route resolves to Codex's native provider"


def test_micu_key_with_empty_claude_base_url_prints_the_route_hint_once(entrypoint, tmp_path):
    run, capture, _ = entrypoint
    env_file = tmp_path / "gb_api.env"
    env_file.write_text("MICU_API_KEY=test-only-not-a-key-0005\n", encoding="utf-8")
    result = run("--game", "canopy_dash", "--mode", "brief", "--dry-run", "--harness", "claude",
                 extra_env={"GB_API_ENV": str(env_file)})
    assert result.returncode == 0, result.stderr
    _, opt = _route(capture)
    assert opt("--agent-base-url") == "https://api.anthropic.com"
    assert result.stderr.count(CLAUDE_MICU_HINT) == 1
    assert "test-only-not-a-key-0005" not in result.stdout + result.stderr

    for text, options in (
        ("MICU_API_KEY=k\nANTHROPIC_BASE_URL=https://www.micuapi.ai\n", ()),
        ("MICU_API_KEY=k\n", ("--provider", "micu")),
    ):
        capture.unlink()
        env_file.write_text(text, encoding="utf-8")
        result = run("--game", "canopy_dash", "--mode", "brief", "--dry-run", "--harness", "claude",
                     "--out", str(tmp_path / f"out{len(options)}"), *options,
                     extra_env={"GB_API_ENV": str(env_file)})
        assert result.returncode == 0, result.stderr
        assert "hint:" not in result.stderr
        _, opt = _route(capture)
        assert opt("--agent-base-url") == "https://www.micuapi.ai"


def test_micu_key_beside_openai_key_prints_the_codex_route_hint(entrypoint, tmp_path):
    run, capture, _ = entrypoint
    env_file = tmp_path / "gb_api.env"


    env_file.write_text("MICU_API_KEY=test-only-not-a-key-0006\nOPENAI_API_KEY=test-only-not-a-key-0007\n",
                        encoding="utf-8")
    result = run("--game", "canopy_dash", "--mode", "brief", "--dry-run",
                 extra_env={"GB_API_ENV": str(env_file)})
    assert result.returncode == 0, result.stderr
    _, opt = _route(capture)
    assert opt("--agent-provider") == "openai"
    assert result.stderr.count(CODEX_MICU_HINT) == 1
    assert "OPENAI_BASE_URL=https://www.micuapi.ai/v1" in result.stderr and "--provider micu" in result.stderr
    assert "test-only-not-a-key-000" not in result.stdout + result.stderr
    capture.unlink()
    env_file.write_text("MICU_API_KEY=k\n", encoding="utf-8")
    result = run("--game", "canopy_dash", "--mode", "brief", "--dry-run", "--out", str(tmp_path / "out2"),
                 extra_env={"GB_API_ENV": str(env_file)})
    assert result.returncode == 0, result.stderr
    assert "hint:" not in result.stderr
    _, opt = _route(capture)
    assert opt("--agent-base-url") == "https://www.micuapi.ai/v1"


def test_provider_micu_keeps_the_historical_defaults(entrypoint, tmp_path):
    run, capture, _ = entrypoint
    env_file = tmp_path / "gb_api.env"
    env_file.write_text("OPENAI_BASE_URL=https://gw.example/v1\n", encoding="utf-8")
    result = run("--game", "canopy_dash", "--mode", "brief", "--dry-run", "--provider", "micu",
                 extra_env={"GB_API_ENV": str(env_file)})
    assert result.returncode == 0, result.stderr
    _, opt = _route(capture)
    assert opt("--agent-base-url") == "https://www.micuapi.ai/v1"
    assert opt("--agent-key-env") == "MICU_API_KEY"


def test_live_run_fails_fast_when_the_route_has_no_credential(entrypoint, tmp_path):
    run, capture, out = entrypoint
    env_file = tmp_path / "gb_api.env"
    env_file.write_text("OPENAI_API_KEY=\nOPENAI_BASE_URL=https://gw.example/v1\n", encoding="utf-8")
    result = run("--game", "canopy_dash", "--mode", "brief", extra_env={"GB_API_ENV": str(env_file)})
    assert result.returncode == 2
    assert "OPENAI_API_KEY is empty" in result.stderr and "--check-auth" in result.stderr
    assert not (out / "scheduler.sh").exists()

    shutil.rmtree(out)
    env_file.write_text("OPENAI_API_KEY=\n", encoding="utf-8")
    result = run("--game", "canopy_dash", "--mode", "brief",
                 extra_env={"GB_API_ENV": str(env_file), "CODEX_AUTH_FILE": str(tmp_path / "no-auth.json")})
    assert result.returncode == 2
    assert "codex login" in result.stderr
    assert not (out / "scheduler.sh").exists()


def test_case_id_still_rejected_outside_bugfix(entrypoint):
    run, capture, _ = entrypoint
    result = run("--game", "all", "--mode", "port", "--case-id", "x", "--dry-run")
    assert result.returncode == 2
    assert "--case-id applies only to --mode bugfix" in result.stderr
    assert not capture.exists()


def test_one_blocked_package_does_not_stop_the_run(entrypoint):

    run, capture, out = entrypoint
    catalog = json.loads((ROOT / "catalog.json").read_text(encoding="utf-8"))
    games = sorted(row["id"] for row in catalog)
    blocked = games[1]
    result = run("--game", "all", "--mode", "brief", "--dry-run", extra_env={"BLOCK_GAME": blocked})


    calls = [json.loads(line) for line in capture.read_text().splitlines()]
    assert [args[args.index("--game") + 1] for args in calls] == games
    assert result.returncode == 1, result.stderr
    assert f"blocked: {blocked}: stub blocker: film too large" in result.stderr
    assert result.stdout.count("harness argv:") == len(games) - 1
    assert "blocked cells: 1" in result.stdout
    assert (out / "blocked.tsv").read_text().splitlines() == [
        f"{blocked}\t\t{out / 'cells' / f'{blocked}__brief__codex__m'}\tstub blocker: film too large"
    ]

    with (out / "summary.csv").open(encoding="utf-8", newline="") as handle:
        rows = list(csv.DictReader(handle))
    assert [(r["game"], r["ranking_note"]) for r in rows] == [
        (blocked, "package_blocked: stub blocker: film too large")
    ]

    shutil.rmtree(out)
    capture.unlink()
    result = run("--game", games[0], "--mode", "brief", "--dry-run")
    assert result.returncode == 0, result.stderr
    assert "blocked cells: 0" in result.stdout
    assert (out / "blocked.tsv").read_text() == ""


def test_sandbox_flag_reaches_bench_argv(entrypoint):
    run, capture, out = entrypoint
    result = run("--game", "canopy_dash", "--mode", "brief", "--sandbox", "docker", "--dry-run")
    assert result.returncode == 0, result.stderr
    calls = [json.loads(line) for line in capture.read_text().splitlines()]
    assert calls, "no bench invocation captured"
    for argv in calls:
        assert argv[argv.index("--agent-sandbox") + 1] == "docker"
    assert json.loads((out / "run.json").read_text())["params"]["agent_sandbox"] == "docker"


def test_sandbox_defaults_to_the_formal_unshare(entrypoint):
    run, capture, out = entrypoint
    result = run("--game", "canopy_dash", "--mode", "brief", "--dry-run")
    assert result.returncode == 0, result.stderr
    calls = [json.loads(line) for line in capture.read_text().splitlines()]
    for argv in calls:
        assert argv[argv.index("--agent-sandbox") + 1] == "unshare"
    assert json.loads((out / "run.json").read_text())["params"]["agent_sandbox"] == "unshare"


def test_invalid_sandbox_is_rejected_before_packaging(entrypoint):
    run, capture, _out = entrypoint
    result = run("--game", "canopy_dash", "--mode", "brief", "--sandbox", "chroot", "--dry-run")
    assert result.returncode == 2
    assert "invalid --sandbox" in result.stderr
    assert not capture.exists()


def test_run_json_refuses_a_sandbox_switch_mid_resume(entrypoint):


    run, _capture, _out = entrypoint
    first = run("--game", "canopy_dash", "--mode", "brief", "--sandbox", "docker", "--dry-run")
    assert first.returncode == 0, first.stderr
    second = run("--game", "canopy_dash", "--mode", "brief", "--sandbox", "unshare",
                 "--resume", "--dry-run")
    assert second.returncode != 0
    assert "use a separate --out directory" in second.stderr


LIVE_BENCH = r'''
import json, os, runpy, sys
from pathlib import Path
from unittest import mock
from evalsys.taskgen.package import TaskPackage

def event(name):
    with open(os.environ["CAPTURE"], "a") as log:
        log.write(json.dumps({"event": name}) + "\n")

if sys.argv[1] == "eval-task":
    event("unexpected_outer_evaluation")
    raise SystemExit(97)

def generate(game, *, mode, out, **kwargs):
    event("generate")
    if os.environ.get("EVALUATOR_OUTCOME") == "generate_error":
        raise RuntimeError("fixture interrupted before package creation")
    package = TaskPackage(Path(out), {
        "schema_version": 1, "mode": mode.id, "game_id": Path(game).name, "blockers": [],
    })
    package.visible.mkdir(parents=True)
    package.hidden.mkdir(parents=True)
    (package.visible / "PROMPT.md").write_text("build\n")
    package.write_manifest()
    return package

def agent(workspace, log_dir, config, **kwargs):
    event("agent")
    project = Path(workspace) / "submission/game"
    project.mkdir(parents=True)
    (project / "project.godot").write_text("[application]\n")
    return 0

class Evaluation:
    resolved = os.environ.get("EVALUATOR_OUTCOME", "pass") != "unresolved"
    eligibility_status = "passed"
    fidelity_items = []
    items = []

    def to_dict(self):
        return {"resolved": self.resolved, "brief_design": self.brief_design,
                "behavior": {"score": {"lo": 1, "hi": 1, "coverage": 1}},
                "comparable": True, "scorecard": {"resolved": self.resolved, "registry_version": "fixture"}}

def evaluate(package, submission, *, out, **kwargs):
    event("evaluate")
    if os.environ.get("EVALUATOR_OUTCOME") == "error":
        raise RuntimeError("fixture evaluator failed")
    result = Evaluation()
    result.brief_design = kwargs["brief_design"]
    Path(out).mkdir(parents=True)
    (Path(out) / "report.json").write_text(json.dumps(result.to_dict()))
    return result

bench = runpy.run_path(os.environ["REAL_BENCH"])
with mock.patch("evalsys.taskgen.matrix.generate_task", side_effect=generate), \
     mock.patch("evalsys.taskgen.matrix.run_coding_agent", side_effect=agent), \
     mock.patch("evalsys.taskgen.matrix.evaluate_task", side_effect=evaluate):
    raise SystemExit(bench["main"]())
'''


@pytest.fixture
def live_entrypoint(entrypoint, tmp_path):
    run, capture, out = entrypoint
    (tmp_path / "repo/eval/evalsys/bin/bench").write_text(LIVE_BENCH)

    bindir = tmp_path / "tools"
    bindir.mkdir()
    awk = bindir / "awk"
    awk.write_text('#!/bin/sh\n[ "${2:-}" = /proc/loadavg ] && exit 1\nexec /usr/bin/awk "$@"\n')
    awk.chmod(0o755)
    docker = bindir / "docker"
    docker.write_text('''#!/bin/sh
case "$1 $2" in
  "version "*) printf 'fixture-daemon\\n' ;;
  "image inspect") exit 0 ;;
  "run --rm") printf '4.5.1\\tfixture-claude\\tfixture-codex\\n' ;;
  "ps "*) exit 0 ;;
  *) printf 'unexpected fixture docker command: %s\\n' "$*" >&2; exit 97 ;;
esac
''')
    docker.chmod(0o755)
    env = {
        "REAL_BENCH": str(ROOT / "eval/evalsys/bin/bench"),
        "GB_TOOLS_BIN": str(bindir), "OPENAI_API_KEY": "fixture-not-a-real-key",


        "DOCKER_HOST": "unix:///nonexistent-swe-game-test-daemon.sock",
    }

    def live(*options, outcome="pass"):
        result = run("--game", "canopy_dash", "--mode", "brief", "--sandbox", "docker",
                     "--reference-video", "off", "--visual-judge", "vlm", "--concurrency", "1",
                     *options, extra_env=env | {"EVALUATOR_OUTCOME": outcome})
        assert capture.is_file(), result.stdout + result.stderr
        events = [json.loads(line)["event"] for line in capture.read_text().splitlines()]
        return result, events

    return live, out, out / "cells/canopy_dash__brief__codex__m"


def test_scheduler_without_worker_status_is_failure(live_entrypoint, tmp_path):
    run, _out, _cell = live_entrypoint
    setsid = tmp_path / "tools/setsid"
    setsid.write_text("#!/bin/sh\nexit 0\n")
    setsid.chmod(0o755)
    result, events = run("--eval", "off")
    assert result.returncode == 1
    assert "scheduler reported 0/1 worker results" in result.stderr
    assert events == ["generate"]


def test_summary_failure_is_failure(live_entrypoint, tmp_path):
    run, _out, _cell = live_entrypoint
    (tmp_path / "repo/eval/tools/summarize_results.py").write_text("raise SystemExit(7)\n")
    result, events = run("--eval", "off")
    assert result.returncode == 1
    assert "could not summarize results" in result.stderr
    assert events == ["generate", "agent"]


def test_live_eval_off_then_resume_evaluates_once_without_rerunning_agent(live_entrypoint):
    run, out, cell = live_entrypoint
    result, events = run("--eval", "off")
    assert result.returncode == 0, result.stdout + result.stderr
    assert events == ["generate", "agent"]
    assert not (cell / "evaluation").exists()
    assert json.loads((cell / "state.json").read_text())["status"] == "submitted"
    with (out / "summary.csv").open(newline="") as handle:
        rows = list(csv.DictReader(handle))
    assert len(rows) == 1
    assert rows[0]["weighted_total"] == ""
    assert rows[0]["ranking_note"] == "evaluation deferred (--eval off)"

    result, events = run("--eval", "off", "--resume")
    assert result.returncode == 0, result.stdout + result.stderr
    assert events == ["generate", "agent"]

    result, events = run("--eval", "on", "--resume")
    assert result.returncode == 0, result.stdout + result.stderr
    assert events == ["generate", "agent", "evaluate"]
    assert json.loads((cell / "state.json").read_text())["status"] == "completed"
    assert json.loads((out / "run.json").read_text())["params"]["evaluation"] == "on"


@pytest.mark.parametrize("options", [(), ("--eval", "on"), ("--brief-design", "on")])
def test_live_eval_on_and_completed_resume_keep_the_first_report(live_entrypoint, options):
    run, _out, cell = live_entrypoint
    result, events = run(*options)
    assert result.returncode == 0, result.stdout + result.stderr
    assert events == ["generate", "agent", "evaluate"]
    first = (cell / "evaluation/report.json").read_bytes()
    assert json.loads(first)["brief_design"] is ("--brief-design" in options)
    result, events = run(*options, "--eval", "on", "--resume")
    assert result.returncode == 0, result.stdout + result.stderr
    assert events == ["generate", "agent", "evaluate"]
    assert (cell / "evaluation/report.json").read_bytes() == first


@pytest.mark.parametrize("outcome, expected_rc", [("unresolved", 0), ("error", 1)])
def test_live_candidate_outcome_is_distinct_from_evaluator_failure(live_entrypoint, outcome, expected_rc):
    run, out, cell = live_entrypoint
    result, events = run("--eval", "on", outcome=outcome)
    assert result.returncode == expected_rc, result.stdout + result.stderr
    assert events == ["generate", "agent", "evaluate"]
    assert (out / "worker-status.tsv").read_text().split("\t")[0] == "1"
    manifest = json.loads((out / "run.json").read_text())
    if outcome == "unresolved":
        assert manifest["execution_status"] == "completed"
        card = json.loads((cell / "evaluation/card.json").read_text())
        assert card["resolved"] is False
        result, events = run("--eval", "on", "--resume", outcome=outcome)
        assert result.returncode == 0, result.stdout + result.stderr
        assert events == ["generate", "agent", "evaluate"]
    else:
        assert manifest["execution_status"] == "failed"
        assert "worker exit 1" in result.stderr
        assert json.loads((cell / "state.json").read_text())["failure_kind"] == "verifier_system_error"


def test_resume_retries_interrupted_generation_with_existing_matrix(live_entrypoint):
    run, _out, cell = live_entrypoint
    result, events = run(outcome="generate_error")
    assert result.returncode == 1, result.stdout + result.stderr
    assert events == ["generate"]
    assert (cell / "matrix/run.json").is_file()
    assert not (cell / "package/manifest.json").exists()

    result, events = run("--resume")
    assert result.returncode == 0, result.stdout + result.stderr
    assert events == ["generate", "generate", "agent", "evaluate"]
    assert json.loads((cell / "state.json").read_text())["status"] == "completed"
