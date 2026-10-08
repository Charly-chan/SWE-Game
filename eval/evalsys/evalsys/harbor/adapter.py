
from __future__ import annotations

from collections import Counter, defaultdict
import json
import math
from pathlib import Path
import re
import shutil

from ..taskgen.modes import parse_mode
from ..taskgen.mode5.scoring import REGISTRY_VERSION as MODE5_REGISTRY_VERSION


from ..taskgen.package import TaskPackage, write_json

GODOT_IMAGE = "ghcr.io/charly-chan/swe-game:godot-4.5.1"
UNITY_IMAGE = "ghcr.io/charly-chan/swe-game:unity-6000.3.23f1"


def export_task(package: Path, out: Path, *, image: str | None = None,
                agent_timeout: int | None = None, verifier_timeout: int = 3600,
                mode5_profile: str = "auto",
                mode5_state_dir: Path | None = None) -> Path:
    package, out = package.resolve(), out.resolve()
    pkg = TaskPackage.read(package)
    mode = parse_mode(pkg.manifest["mode"])
    if agent_timeout is None:
        agent_timeout = 7200 if mode.id == "port" else 1800
    if mode5_profile == "auto" and mode.id == "port":
        mode5_profile = "community-docker"
    if pkg.manifest.get("blockers"):
        raise ValueError(f"Package is blocked: {pkg.manifest['blockers']}")
    if out.is_relative_to(package):
        raise ValueError("Export destination must be outside the source package")
    if agent_timeout <= 0 or verifier_timeout <= 0:
        raise ValueError("Timeouts must be positive")
    if mode5_profile not in {"auto", "community-docker"}:
        raise ValueError("mode5_profile must be auto or community-docker")
    if mode.id != "port" and mode5_profile != "auto":
        raise ValueError("community-docker profile applies only to Mode 5")
    community_provider: str | None = None
    if mode.id == "port" and mode5_profile == "community-docker":
        state = (Path(mode5_state_dir).expanduser().resolve()
                 if mode5_state_dir else Path.home() / ".cache" / "gamebench" / "mode5")
        lock = json.loads((state / "image-lock.json").read_text(encoding="utf-8"))
        locked_image = str((lock.get("agent_image") or {}).get("id") or "")
        if not locked_image:
            raise ValueError("active Mode 5 image-lock has no agent image ID")
        if image is not None and image != locked_image:
            raise ValueError("explicit image does not match the active Mode 5 image-lock")
        image = locked_image
        preflight = json.loads(
            (state / "last-preflight.json").read_text(encoding="utf-8")
        )
        if preflight.get("status") != "pass":
            raise ValueError("Mode 5 community export requires a passing doctor result")
        if dict(preflight.get("image_ids") or {}).get("agent") != image:
            raise ValueError("Mode 5 doctor result does not match the agent image")
        license_config = json.loads(
            (state / "license-provider.json").read_text(encoding="utf-8")
        )
        community_provider = str(license_config.get("provider") or "")
        if community_provider not in {"file", "existing-home", "floating"}:
            raise ValueError("active Mode 5 state has an unsupported license provider")
        if community_provider != "floating":
            raise ValueError(
                "Harbor Mode 5 agent containers require a floating Unity provider; "
                "Harbor does not inject private file/existing-home entitlements. "
                "Use gb mode5 run for the validated Personal/existing-home workflow."
            )
    image = image or (UNITY_IMAGE if mode.id == "port" else GODOT_IMAGE)
    if not re.fullmatch(r"[A-Za-z0-9_./:@-]+", image):
        raise ValueError("Expected a Docker image reference")
    out.mkdir(parents=True, exist_ok=False)
    frozen = out / "evaluator" / "package"
    shutil.copytree(package, frozen)

    # Mode-4 oracle records include absolute paths inside the package. Rebase
    # those when copying; corpus references outside it remain host prerequisites.
    def relocate(value):
        if isinstance(value, str):
            candidate = Path(value)
            if candidate.is_absolute() and candidate.is_relative_to(package):
                return str(frozen / candidate.relative_to(package))
        if isinstance(value, list):
            return [relocate(v) for v in value]
        if isinstance(value, dict):
            return {k: relocate(v) for k, v in value.items()}
        return value

    for path in frozen.joinpath("hidden").rglob("*.json"):
        data = json.loads(path.read_text(encoding="utf-8"))
        updated = relocate(data)
        if updated != data:
            write_json(path, updated)

    env = out / "environment"
    if mode.id == "port" and mode5_profile == "community-docker":
        # Harbor accepts a pre-built image ID directly. With no Dockerfile it
        # uploads environment/ into the container workdir, avoiding BuildKit's
        # incorrect treatment of ``FROM sha256:...`` as a Docker Hub name.
        shutil.copytree(pkg.visible, env)
        if community_provider == "floating":
            helper = env / ".gamebench" / "unity-license-healthcheck.sh"
            helper.parent.mkdir(parents=True, exist_ok=True)
            helper.write_text(
                "#!/bin/sh\n"
                "set -eu\n"
                "log=$(mktemp)\n"
                "trap 'rm -f \"$log\"' EXIT\n"
                "set +e\n"
                "unity -batchmode -nographics -quit -logFile \"$log\"\n"
                "rc=$?\n"
                "set -e\n"
                "test \"$rc\" -eq 0\n"
                "! grep -Eiq 'No valid Unity Editor license|Failed to activate/update license|license is invalid|Licensing Client timed out|Failed to connect to licensing client' \"$log\"\n",
                encoding="utf-8",
            )
    else:
        shutil.copytree(pkg.visible, env / "visible")
        (env / "Dockerfile").write_text(
            f"FROM {image}\nUSER root\n"
            "COPY --chown=agent:agent visible/ /workspace/\n"
            "USER agent\nWORKDIR /workspace\n", encoding="utf-8")
    instruction = (pkg.visible / "PROMPT.md").read_text(encoding="utf-8")
    (out / "instruction.md").write_text(
        instruction + "\n\n## Harbor workspace\n\n"
        "Your provided materials are in `/workspace`. Follow the existing "
        "submission contract and save the completed submission under "
        "`/workspace/submission/`. Files outside `/workspace` are not submitted.\n",
        encoding="utf-8")
    environment_extra = (
        '\n[environment.env]\n'
        'UNITY_LICENSE_SERVER = "${GB_UNITY_FLOATING_ENDPOINT}"\n\n'
        '[environment.healthcheck]\n'
        'command = "sh /workspace/.gamebench/unity-license-healthcheck.sh"\n'
        'timeout_sec = 300\nretries = 1\n'
        if community_provider == "floating" else "\n"
    )
    task_config = (
        'schema_version = "1.4"\nartifacts = ["/workspace"]\n\n'
        '[metadata]\nbenchmark = "SWE-Game"\n'
        f'mode = {json.dumps(mode.id)}\n'
        f'game_id = {json.dumps(pkg.manifest["game_id"])}\n\n'
        f'environment_profile = {json.dumps(mode5_profile if mode.id == "port" else "default")}\n\n'
        f'[agent]\ntimeout_sec = {agent_timeout}\nuser = "agent"\n\n'
        '[environment]\ncpus = 4\nmemory_mb = 8192\n'
        + (f'docker_image = {json.dumps(image)}\n'
           if mode.id == "port" and mode5_profile == "community-docker" else '') +
        'storage_mb = 32768\n'
        + environment_extra
        + f'[verifier]\ntimeout_sec = {verifier_timeout}\n'
        'environment_mode = "separate"\n\n'
        '[verifier.environment]\n'
        f'docker_image = {json.dumps(image)}\n'
    )
    (out / "task.toml").write_text(task_config, encoding="utf-8")
    tests = out / "tests"
    tests.mkdir()
    (tests / "test.sh").write_text(
        '#!/bin/sh\necho "Use scripts/run_harbor.sh run: '
        'SWE-Game requires its host verifier." >&2\nexit 1\n')
    return out


def result_record(report: dict) -> dict:
    """Use the headline, never the legacy behavior-only top-level score."""
    headline = report["headline"]
    score = headline.get("score")
    eligible = headline.get("ranking_eligible") is True
    scored = eligible and isinstance(score, (int, float)) and not isinstance(score, bool) and math.isfinite(score)
    if report["mode"] == "port":
        from ..taskgen.mode5.score import aggregate_model
        reading = report.get("scorecard", {}).get("evidence_score")
        if reading:
            if reading.get("game_id") != report["game_id"]:
                raise ValueError("inconsistent Mode 5 game identity")
            aggregate_model([reading], expected_games=[report["game_id"]])
        scored = bool(scored and reading and reading.get("ranking_eligible") is True
                      and reading.get("total") == score
                      and report.get("scorecard", {}).get("registry_version") == MODE5_REGISTRY_VERSION)
    environment = dict(report.get("environment") or {})
    return {
        "status": "scored" if scored else "unscored",
        "mode": report["mode"], "game_id": report["game_id"],
        "registry_version": report.get("scorecard", {}).get("registry_version"),
        "ranking_scope": (
            headline.get("ranking_scope")
            or report.get("scorecard", {}).get("ranking_scope")
        ),
        "environment": environment or None,
        "environment_class": environment.get("environment_class"),
        "environment_profile": environment.get("profile_id"),
        "agent_image_id": environment.get("agent_image_id"),
        "evaluator_image_id": environment.get("evaluator_image_id"),
        "paper_compatible": environment.get("paper_compatible"),
        "run_config": report.get("run_config"),
        "headline": headline, "resolved": report.get("resolved"),
        **({"evidence_score": report.get("scorecard", {}).get("evidence_score")}
           if report["mode"] == "port" else {}),
        "rewards": {"swe_game_score": score} if scored else None,
    }


def grade(package: Path, workspace: Path, out: Path, *, engine: str = "auto",
          visual_judge: str = "none", registry_version: str | None = None,
          collect_only: bool = False, mode5_profile: str = "auto",
          mode5_state_dir: Path | None = None, docker: str = "docker") -> dict:
    from ..taskgen.evaluate import evaluate_task
    from ..taskgen.matrix import MatrixError, collect_submission

    pkg = TaskPackage.read(package)
    mode = parse_mode(pkg.manifest["mode"])
    if mode5_profile == "auto" and mode.id == "port":
        mode5_profile = "community-docker"
    if mode5_profile not in {"auto", "community-docker"}:
        raise ValueError("mode5_profile must be auto or community-docker")
    if mode.id != "port" and mode5_profile != "auto":
        raise ValueError("community-docker profile applies only to Mode 5")
    if mode.id == "port" and mode5_profile == "community-docker":
        if visual_judge != "none":
            raise ValueError("Mode 5 uses the fixed non-VLM evidence proxy")
        if registry_version not in {None, MODE5_REGISTRY_VERSION}:
            raise ValueError(
                f"Mode 5 community scoring requires registry {MODE5_REGISTRY_VERSION}"
            )
    out.mkdir(parents=True, exist_ok=True)
    record = {"mode": mode.id, "game_id": pkg.manifest["game_id"],
              "rewards": None, "resolved": None, "registry_version": registry_version}
    try:
        submission = collect_submission(workspace, out / "submission", mode=mode)
    except MatrixError as exc:
        record.update(status="submission_contract_failed", error=str(exc))
    else:
        if collect_only:
            record.update(status="deferred", submission=str(submission),
                          package=str(package.resolve()), reason="collect_only")
        elif mode.id == "port" and mode5_profile == "community-docker":
            from ..taskgen.mode5.community_profile import load_default_profile
            from ..taskgen.mode5.docker_environment import DockerClient
            from ..taskgen.mode5.docker_evaluator import evaluate_in_container
            from ..taskgen.mode5.docker_license import LicenseProvider

            state = (Path(mode5_state_dir).expanduser().resolve()
                     if mode5_state_dir else Path.home() / ".cache" / "gamebench" / "mode5")
            image_lock = json.loads((state / "image-lock.json").read_text(encoding="utf-8"))
            preflight = json.loads((state / "last-preflight.json").read_text(encoding="utf-8"))
            license_config = json.loads(
                (state / "license-provider.json").read_text(encoding="utf-8")
            )
            provider_kind = license_config.get("provider")
            if provider_kind == "file":
                provider = LicenseProvider.file(Path(license_config["source"]))
            elif provider_kind == "existing-home":
                provider = LicenseProvider.existing_home(Path(license_config["source"]))
            elif provider_kind == "floating":
                provider = LicenseProvider.floating(str(license_config["endpoint"]))
            else:
                raise ValueError("active Mode 5 state has an unsupported license provider")
            report = evaluate_in_container(
                repo_root=Path(__file__).resolve().parents[4], package=package,
                submission=submission, out=out / "evaluation",
                profile=load_default_profile(), image_lock=image_lock,
                preflight=preflight, license_provider=provider,
                docker=DockerClient(docker),
                fidelity_judge=visual_judge,
                run_config={
                    "harness": "harbor", "model": "recorded-by-harbor",
                    "provider": "recorded-by-harbor", "budget": {},
                    "input": {
                        "game_id": pkg.manifest.get("game_id"),
                        "reference_video": pkg.manifest.get("reference_video", "unknown"),
                        "playtest_kit": pkg.manifest.get("playtest_kit", "unknown"),
                        "engine": "on", "visual_judge": "none",
                        "mode5_profile": "community-docker",
                    },
                },
            )
            record = result_record(report)
        else:
            report = evaluate_task(package, submission, engine=engine,
                                   visual_judge=visual_judge, out=out,
                                   registry_version=registry_version).to_dict()
            record = result_record(report)
    record["case_id"] = pkg.manifest.get("case_id", "")
    write_json(out / "swe-game.json", record)
    return record


def summarize(job: Path) -> dict:
    """Group scoring protocols and expose missing results without filling zeros."""
    from ..taskgen.scorecard import REGISTRY_VERSION, default_registry_for_mode

    groups = defaultdict(list)
    trials = []
    for path in sorted(job.glob("*/result.json")):
        trial = json.loads(path.read_text(encoding="utf-8"))
        record_path = path.parent / "verifier" / "swe-game.json"
        if record_path.exists():
            record = json.loads(record_path.read_text(encoding="utf-8"))
        else:
            task_path = trial.get("config", {}).get("task", {}).get("path")
            manifest = Path(task_path) / "evaluator/package/manifest.json" if task_path else None
            metadata = (json.loads(manifest.read_text(encoding="utf-8"))
                        if manifest and manifest.exists() else {})
            record = {
                "mode": metadata.get("mode"), "game_id": metadata.get("game_id"),
                "case_id": metadata.get("case_id", ""),
                "status": "harness_error" if trial.get("exception_info") else "not_evaluated",
                "rewards": None,
            }
        agent = trial.get("agent_info", {})
        model = agent.get("model_info") or {}
        trial_config = dict(trial.get("config") or {})
        agent_config = dict(trial_config.get("agent") or {})
        agent_kwargs = dict(agent_config.get("kwargs") or {})
        harbor_timeout = agent_config.get("timeout_sec", agent_kwargs.get("timeout_sec"))
        if not isinstance(harbor_timeout, (int, float)) or isinstance(harbor_timeout, bool):
            harbor_timeout = None
        row = {**record, "trial": path.parent.name,
               "agent": agent.get("name"), "agent_version": agent.get("version"),
               "model": model.get("name"),
               "provider": model.get("provider"),
               "harbor_agent_timeout_seconds": harbor_timeout,
               "exception": trial.get("exception_info")}
        requested_registry = trial.get("config", {}).get("verifier", {}).get("kwargs", {}).get("registry_version")
        row["registry_version"] = row.get("registry_version") or requested_registry or (
            default_registry_for_mode("port") if row.get("mode") == "port" else REGISTRY_VERSION)
        trials.append(row)

    # Failed setup/collection has no headline. Within one scoring registry use
    # the observed protocol to keep these attempts in the same denominator.
    protocols = defaultdict(set)
    for row in trials:
        headline = row.get("headline", {})
        if headline:
            protocols[(row.get("mode"), row["registry_version"],
                       row.get("ranking_scope"), row.get("environment_class"),
                       row.get("environment_profile"))].add(
                (headline.get("scale"), headline.get("score_scope")))
    for row in trials:
        headline = row.get("headline", {})
        protocol = (headline.get("scale"), headline.get("score_scope"))
        known = protocols[(row.get("mode"), row["registry_version"],
                           row.get("ranking_scope"), row.get("environment_class"),
                           row.get("environment_profile"))]
        if not headline and len(known) == 1:
            protocol = next(iter(known))
        environment = dict(row.get("environment") or {})
        run_config = dict(row.get("run_config") or {})
        budget_config = dict(run_config.get("budget") or {})
        input_config = dict(run_config.get("input") or {})
        if row.get("mode") == "port":
            input_config.pop("game_id", None)
        timeout = row.get("harbor_agent_timeout_seconds")
        if isinstance(timeout, (int, float)) and not isinstance(timeout, bool):
            budget_config["agent_timeout_seconds"] = timeout
        key = (row["agent"], row["agent_version"], row["provider"], row["model"],
               row.get("mode"), row.get("registry_version"), row.get("ranking_scope"),
               row.get("environment_class"), row.get("environment_profile"),
               row.get("agent_image_id") or environment.get("agent_image_id"),
               row.get("evaluator_image_id") or environment.get("evaluator_image_id"),
               json.dumps(budget_config, sort_keys=True, separators=(",", ":")),
               json.dumps(input_config, sort_keys=True, separators=(",", ":")),
               *protocol)
        groups[key].append(row)
    summaries = []
    for key, rows in groups.items():
        scores = [r["rewards"]["swe_game_score"] for r in rows if r.get("rewards")]
        cases = defaultdict(list)
        game_trials = Counter(r.get("game_id") for r in rows)
        for row in rows:
            if row.get("rewards"):
                cases[(row.get("game_id"), row.get("case_id", ""))].append(
                    row["rewards"]["swe_game_score"])
        games = defaultdict(list)
        for (game_id, _), case_scores in cases.items():
            games[game_id].append(sum(case_scores) / len(case_scores))
        game_means = {game: sum(values) / len(values) for game, values in games.items()}
        resolved = [r["resolved"] for r in rows if isinstance(r.get("resolved"), bool)]
        summaries.append({
            **dict(zip(("agent", "agent_version", "provider", "model", "mode",
                        "registry_version", "ranking_scope", "environment_class",
                        "environment_profile", "agent_image_id", "evaluator_image_id",
                        "budget_configuration_json", "input_configuration_json",
                        "scale", "score_scope"), key)),
            "trials": len(rows), "scored": len(scores),
            "unscored": len(rows) - len(scores),
            "mean_score": sum(game_means.values()) / len(game_means) if game_means else None,
            "mean_trial_score": sum(scores) / len(scores) if scores else None,
            "scored_games": len(game_means), "total_games": len(game_trials),
            "game_scores": game_means,
            "resolved": sum(resolved), "resolution_measured": len(resolved),
            "statuses": dict(Counter(r["status"] for r in rows)),
            "harness_errors": sum(bool(r["exception"]) for r in rows),
        })
        summaries[-1]["budget_configuration"] = json.loads(
            summaries[-1].pop("budget_configuration_json")
        )
        summaries[-1]["input_configuration"] = json.loads(
            summaries[-1].pop("input_configuration_json")
        )
        if key[4] == "port":
            summaries[-1].update(_mode5_summary(rows))
    result = {"schema": "swe-game.harbor-summary.v1",
              "note": "Means include ranking-eligible headlines only. Always report "
                      "scored/total coverage. Harbor generic Mean is not a SWE-Game score.",
              "total_trials": len(trials),
              "scored_trials": sum(bool(r.get("rewards")) for r in trials),
              "statuses": dict(Counter(r["status"] for r in trials)),
              "groups": summaries, "trials": trials}
    write_json(job / "swe-game-summary.json", result)
    return result


def _mode5_summary(rows: list[dict]) -> dict:
    """Use the same release catalog arithmetic mean, including failed attempts.

    Repeated attempts are averaged within a game only when every attempt has
    a valid reading. A missing attempt never improves the complete headline.
    """
    from ..taskgen.mode5.community_cli import _catalog_game_ids
    from ..taskgen.mode5.score import aggregate_model, COMPONENT_WEIGHTS, SCHEMA
    catalog = _catalog_game_ids()
    by_game = defaultdict(list)
    for row in rows:
        game = row.get("game_id")
        if game not in catalog:
            raise ValueError("unknown game in Mode 5 Harbor summary")
        reading = row.get("evidence_score")
        if reading is not None:
            if reading.get("game_id") != game:
                raise ValueError("inconsistent Mode 5 game identity")
            aggregate_model([reading], expected_games=[game])
        by_game[game].append((row, reading))
    records = []
    for game, attempts in sorted(by_game.items()):
        complete = all(reading and reading.get("ranking_eligible") is True
                       and reading.get("total") is not None and row.get("rewards")
                       for row, reading in attempts)
        record = {"game_id": game, "schema": SCHEMA, "registry_version": MODE5_REGISTRY_VERSION,
                  "ranking_eligible": bool(complete), "total": None, "components": {}}
        if complete:
            record["total"] = sum(reading["total"] for _, reading in attempts) / len(attempts)
            record["components"] = {
                name: {"weight": weight, "score": sum(reading["components"][name]["score"]
                                                     for _, reading in attempts) / len(attempts)}
                for name, weight in COMPONENT_WEIGHTS.items()
            }
        records.append(record)
    result = aggregate_model(records, expected_games=catalog)
    partial = [record["total"] for record in records if record["ranking_eligible"]]
    return {**result, "mean_score": result["task_mean"],
            "partial_mean_score": sum(partial) / len(partial) if partial else None,
            "expected_game_count": len(catalog), "coverage": len(partial) / len(catalog)}
