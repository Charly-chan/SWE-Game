
from __future__ import annotations

from collections import Counter, defaultdict
import json
from pathlib import Path
import re
import shutil

from ..taskgen.modes import parse_mode
from ..taskgen.package import TaskPackage, write_json

GODOT_IMAGE = "ghcr.io/charly-chan/swe-game:godot-4.5.1"
UNITY_IMAGE = "ghcr.io/charly-chan/swe-game:unity-6000.3.23f1"


def export_task(package: Path, out: Path, *, image: str | None = None,
                agent_timeout: int = 1800, verifier_timeout: int = 3600) -> Path:
    package, out = package.resolve(), out.resolve()
    pkg = TaskPackage.read(package)
    mode = parse_mode(pkg.manifest["mode"])
    if pkg.manifest.get("blockers"):
        raise ValueError(f"Package is blocked: {pkg.manifest['blockers']}")
    if out.is_relative_to(package):
        raise ValueError("Export destination must be outside the source package")
    if agent_timeout <= 0 or verifier_timeout <= 0:
        raise ValueError("Timeouts must be positive")
    image = image or (UNITY_IMAGE if mode.id == "port" else GODOT_IMAGE)
    if not re.fullmatch(r"[A-Za-z0-9_./:@-]+", image):
        raise ValueError("Expected a Docker image reference")
    out.mkdir(parents=True, exist_ok=False)
    frozen = out / "evaluator" / "package"
    shutil.copytree(package, frozen)


    def relocate(value):
        if isinstance(value, str) and value.startswith(str(package) + "/"):
            return str(frozen) + value[len(str(package)):]
        if isinstance(value, list):
            return [relocate(v) for v in value]
        if isinstance(value, dict):
            return {k: relocate(v) for k, v in value.items()}
        return value

    for path in frozen.joinpath("hidden").rglob("*.json"):
        data = json.loads(path.read_text())
        updated = relocate(data)
        if updated != data:
            write_json(path, updated)

    env = out / "environment"
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
    (out / "task.toml").write_text(
        'schema_version = "1.4"\nartifacts = ["/workspace"]\n\n'
        '[metadata]\nbenchmark = "SWE-Game"\n'
        f'mode = {json.dumps(mode.id)}\n'
        f'game_id = {json.dumps(pkg.manifest["game_id"])}\n\n'
        f'[agent]\ntimeout_sec = {agent_timeout}\nuser = "agent"\n\n'
        '[environment]\ncpus = 4\nmemory_mb = 8192\n'
        'storage_mb = 32768\n\n'
        f'[verifier]\ntimeout_sec = {verifier_timeout}\n'
        'environment_mode = "separate"\n\n'
        '[verifier.environment]\n'
        f'docker_image = {json.dumps(image)}\n', encoding="utf-8")
    tests = out / "tests"
    tests.mkdir()
    (tests / "test.sh").write_text(
        '#!/bin/sh\necho "Use scripts/run_harbor.sh run: '
        'SWE-Game requires its host verifier." >&2\nexit 1\n')
    return out


def result_record(report: dict) -> dict:

    headline = report["headline"]
    score = headline.get("score")
    eligible = headline.get("ranking_eligible") is True
    scored = eligible and isinstance(score, (int, float)) and not isinstance(score, bool)
    return {
        "status": "scored" if scored else "unscored",
        "mode": report["mode"], "game_id": report["game_id"],
        "registry_version": report.get("scorecard", {}).get("registry_version"),
        "headline": headline, "resolved": report.get("resolved"),
        "rewards": {"swe_game_score": score} if scored else None,
    }


def grade(package: Path, workspace: Path, out: Path, *, engine: str = "auto",
          visual_judge: str = "none", registry_version: str | None = None,
          collect_only: bool = False) -> dict:
    from ..taskgen.evaluate import evaluate_task
    from ..taskgen.matrix import MatrixError, collect_submission

    pkg = TaskPackage.read(package)
    mode = parse_mode(pkg.manifest["mode"])
    out.mkdir(parents=True, exist_ok=True)
    record = {"mode": mode.id, "game_id": pkg.manifest["game_id"],
              "rewards": None, "resolved": None, "registry_version": registry_version}
    try:
        submission = collect_submission(workspace, out / "submission", mode=mode)
    except MatrixError as exc:
        record.update(status="submission_contract_failed", error=str(exc))
    else:
        if collect_only or mode.id == "port":


            record.update(status="deferred", submission=str(submission),
                          package=str(package.resolve()),
                          reason="certified_unity_vm_required" if mode.id == "port"
                          else "collect_only")
        else:
            report = evaluate_task(package, submission, engine=engine,
                                   visual_judge=visual_judge, out=out,
                                   registry_version=registry_version).to_dict()
            record = result_record(report)
    record["case_id"] = pkg.manifest.get("case_id", "")
    write_json(out / "swe-game.json", record)
    return record


def summarize(job: Path) -> dict:

    from ..taskgen.scorecard import REGISTRY_VERSION, default_registry_for_mode

    groups = defaultdict(list)
    trials = []
    for path in sorted(job.glob("*/result.json")):
        trial = json.loads(path.read_text())
        record_path = path.parent / "verifier" / "swe-game.json"
        if record_path.exists():
            record = json.loads(record_path.read_text())
        else:
            task_path = trial.get("config", {}).get("task", {}).get("path")
            manifest = Path(task_path) / "evaluator/package/manifest.json" if task_path else None
            metadata = json.loads(manifest.read_text()) if manifest and manifest.exists() else {}
            record = {
                "mode": metadata.get("mode"), "game_id": metadata.get("game_id"),
                "case_id": metadata.get("case_id", ""),
                "status": "harness_error" if trial.get("exception_info") else "not_evaluated",
                "rewards": None,
            }
        agent = trial.get("agent_info", {})
        model = agent.get("model_info") or {}
        row = {**record, "trial": path.parent.name,
               "agent": agent.get("name"), "agent_version": agent.get("version"),
               "model": model.get("name"),
               "provider": model.get("provider"),
               "exception": trial.get("exception_info")}
        requested_registry = trial.get("config", {}).get("verifier", {}).get("kwargs", {}).get("registry_version")
        row["registry_version"] = row.get("registry_version") or requested_registry or (
            default_registry_for_mode("port") if row.get("mode") == "port" else REGISTRY_VERSION)
        trials.append(row)


    protocols = defaultdict(set)
    for row in trials:
        headline = row.get("headline", {})
        if headline:
            protocols[(row.get("mode"), row["registry_version"])].add(
                (headline.get("scale"), headline.get("score_scope")))
    for row in trials:
        headline = row.get("headline", {})
        protocol = (headline.get("scale"), headline.get("score_scope"))
        known = protocols[(row.get("mode"), row["registry_version"])]
        if not headline and len(known) == 1:
            protocol = next(iter(known))
        key = (row["agent"], row["agent_version"], row["provider"], row["model"], row.get("mode"),
               row.get("registry_version"), *protocol)
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
            **dict(zip(("agent", "agent_version", "provider", "model", "mode", "registry_version",
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
    result = {"schema": "swe-game.harbor-summary.v1",
              "note": "Means include ranking-eligible headlines only. Always report "
                      "scored/total coverage. Harbor generic Mean is not a SWE-Game score.",
              "total_trials": len(trials),
              "scored_trials": sum(bool(r.get("rewards")) for r in trials),
              "statuses": dict(Counter(r["status"] for r in trials)),
              "groups": summaries, "trials": trials}
    write_json(job / "swe-game-summary.json", result)
    return result
