"""Standalone release CLI for Mode 5 Community Docker."""

from __future__ import annotations

import argparse
import csv
import json
import os
import shutil
import sys
import tempfile
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Mapping

from .community_profile import load_default_profile
from .docker_environment import (
    CommunityDockerError, DockerClient, build_images, export_manual_activation_request,
    run_doctor, write_private_json,
)
from .docker_license import LicenseConfigurationError, LicenseProvider
from .docker_evaluator import DEVELOPMENT_RANKING_SCOPE


RUN_STATE_SCHEMA = "gamebench.mode5-community-run-state.v1"


def _now() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat()


def _record_phase(
    run_root: Path, phase: str, *, status: str = "running",
    failure: Mapping[str, Any] | None = None,
) -> dict[str, Any]:
    """Atomically append a Community-only phase without changing matrix state."""
    path = run_root / "mode5-state.json"
    try:
        state = _read_json(path)
    except OSError:
        state = {"schema": RUN_STATE_SCHEMA, "created_at": _now(), "history": []}
    if state.get("schema") != RUN_STATE_SCHEMA:
        raise CommunityDockerError("unsupported mode5-state.json schema")
    history = list(state.get("history") or [])
    if not history or history[-1].get("phase") != phase or history[-1].get("status") != status:
        history.append({"phase": phase, "status": status, "at": _now()})
    state.update({"phase": phase, "status": status, "updated_at": _now(), "history": history})
    if failure is not None:
        state["failure"] = dict(failure)
    else:
        state.pop("failure", None)
    write_private_json(path, state)
    return state


def _load_verified_report(evaluation: Path) -> dict[str, Any] | None:
    from .docker_evaluator import verify_artifact_manifest
    if not evaluation.is_dir():
        return None
    verify_artifact_manifest(evaluation)
    report = _read_json(evaluation / "report.json")
    from .scoring import REGISTRY_VERSION
    if (report.get("scorecard") or {}).get("registry_version") != REGISTRY_VERSION:
        raise CommunityDockerError("Mode 5 report registry does not match this release")
    return report


def _quarantine_incomplete_evaluation(evaluation: Path) -> Path:
    suffix = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    target = evaluation.with_name(f"{evaluation.name}.incomplete-{suffix}")
    counter = 1
    while target.exists():
        target = evaluation.with_name(f"{evaluation.name}.incomplete-{suffix}-{counter}")
        counter += 1
    evaluation.replace(target)
    return target


def _failure_record(
    *, phase: str, attribution: str, reason_code: str, detail: str,
    log: str = "",
) -> dict[str, Any]:
    row = {
        "phase": phase, "status": "failed", "attribution": attribution,
        "reason_code": reason_code, "detail": detail,
    }
    if log:
        row["log"] = log
    return row


def _evaluator_failure(exc: Exception) -> dict[str, Any]:
    detail = str(exc)
    lowered = detail.lower()
    if "digest" in lowered or "image-lock" in lowered:
        reason = "image_digest_mismatch"
    elif "license" in lowered or "entitlement" in lowered:
        reason = "unity_license_invalid"
    elif "artifact" in lowered:
        reason = "artifact_verification_failed"
    elif "player" in lowered and "launch" in lowered:
        reason = "unity_player_launch_failed"
    else:
        reason = "evaluator_infrastructure_failed"
    return _failure_record(
        phase="evaluator", attribution="evaluator_infrastructure",
        reason_code=reason, detail=detail,
    )


def _agent_completion(run_root: Path, matrix_state: Mapping[str, Any]) -> dict[str, Any]:
    """Preserve budget exhaustion when resuming only the delivered submission."""
    kind = str(matrix_state.get("failure_kind") or "")
    exit_code = matrix_state.get("agent_exit_code")
    if not kind and exit_code == 124:
        stderr_path = run_root / "agent" / "stderr.log"
        if stderr_path.is_file():
            with stderr_path.open("rb") as stream:
                stream.seek(max(0, stderr_path.stat().st_size - 4096))
                if b"agent timeout after " in stream.read():
                    kind = "agent_timeout"
    return {"exit_code": exit_code, "failure_kind": kind,
            "budget_exhausted": kind == "agent_timeout"}


def repo_root() -> Path:
    return Path(__file__).resolve().parents[5]


def state_root(value: str = "") -> Path:
    configured = value or os.environ.get("GB_MODE5_HOME") or ""
    return Path(configured).expanduser().resolve() if configured else (
        Path.home() / ".cache" / "gamebench" / "mode5"
    )


def _provider(args: argparse.Namespace, config: Mapping[str, Any] | None = None) -> LicenseProvider:
    source = dict(config or {})
    kind = str(getattr(args, "license_provider", "") or source.get("provider") or "")
    if kind == "file":
        value = str(getattr(args, "license_file", "") or source.get("source") or "")
        if not value:
            raise LicenseConfigurationError("--license-file is required for provider=file")
        return LicenseProvider.file(Path(value))
    if kind == "existing-home":
        value = str(getattr(args, "unity_config_root", "") or source.get("source") or "")
        if not value:
            raise LicenseConfigurationError(
                "--unity-config-root is required for provider=existing-home"
            )
        return LicenseProvider.existing_home(Path(value))
    if kind == "floating":
        value = str(getattr(args, "floating_endpoint", "") or source.get("endpoint") or "")
        if not value:
            raise LicenseConfigurationError(
                "--floating-endpoint is required for provider=floating"
            )
        return LicenseProvider.floating(value)
    raise LicenseConfigurationError(
        "--license-provider must be file, existing-home, or floating"
    )


def _private_provider_dict(provider: LicenseProvider) -> dict[str, Any]:
    row: dict[str, Any] = {"provider": provider.kind}
    if provider.source is not None:
        row["source"] = str(provider.source)
    if provider.endpoint is not None:
        row["endpoint"] = provider.endpoint
    return row


def _read_json(path: Path) -> dict[str, Any]:
    raw = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(raw, dict):
        raise CommunityDockerError(f"{path.name} root must be an object")
    return raw


def _catalog_game_path(game_id: str) -> Path:
    catalog = json.loads((repo_root() / "catalog.json").read_text(encoding="utf-8"))
    for row in catalog:
        if str(row.get("id")) == game_id:
            path = (repo_root() / str(row["path"])).resolve()
            # The frozen task installer supplies source, assets and reference
            # video. Resolving an ID must not restore a second source corpus.
            from ...frozen_data import load_manifest
            if game_id not in load_manifest(repo_root())["games"]:
                raise CommunityDockerError(f"game is absent from the released dataset: {game_id}")
            return path
    raise CommunityDockerError(f"unknown game id: {game_id}")


def cmd_setup(args: argparse.Namespace) -> int:
    profile = load_default_profile()
    root = state_root(args.state_dir)
    archives = (
        Path(args.unity_archives).expanduser().resolve()
        if args.unity_archives else root / "archives"
    )
    lock = build_images(
        repo_root=repo_root(), archives=archives,
        profile=profile, docker=DockerClient(args.docker),
        download_missing=not args.no_download,
        reuse_image_lock=_read_json(root / "image-lock.json") if (root / "image-lock.json").is_file() else None,
    )
    # Image construction is useful independent of entitlement activation.  Keep
    # this lock even when D05 later fails so the operator only has to authenticate
    # Unity and rerun doctor, not rebuild a 17 GB image.
    write_private_json(root / "image-lock.json", lock)
    write_private_json(root / "active-profile.json", profile.public_dict())
    if not args.license_provider:
        print(json.dumps({
            "status": "built_needs_license", "profile_id": profile.profile_id,
            "state_dir": str(root), "image_ids": {
                "agent": lock["agent_image"]["id"],
                "evaluator": lock["evaluator_image"]["id"],
            },
            "next": "run gb mode5 doctor with a Unity license provider",
        }, indent=2, sort_keys=True))
        return 0
    provider = _provider(args)
    write_private_json(root / "license-provider.json", _private_provider_dict(provider))
    report = run_doctor(
        repo_root=repo_root(), profile=profile, image_lock=lock,
        license_provider=provider, docker=DockerClient(args.docker),
    )
    write_private_json(root / "last-preflight.json", report)
    if report["status"] != "pass":
        print(json.dumps(report, indent=2, sort_keys=True))
        return 1
    print(json.dumps({
        "status": "ready", "profile_id": profile.profile_id,
        "state_dir": str(root), "license": provider.public_metadata(probe_passed=True),
        "image_ids": report["image_ids"],
    }, indent=2, sort_keys=True))
    return 0


def cmd_doctor(args: argparse.Namespace) -> int:
    root = state_root(args.state_dir)
    profile = load_default_profile()
    try:
        lock = _read_json(root / "image-lock.json")
        provider_config = root / "license-provider.json"
        provider = _provider(
            args, _read_json(provider_config) if provider_config.is_file() else None,
        )
        report = run_doctor(
            repo_root=repo_root(), profile=profile, image_lock=lock,
            license_provider=provider, docker=DockerClient(args.docker),
        )
    except (OSError, json.JSONDecodeError, CommunityDockerError,
            LicenseConfigurationError) as exc:
        report = {
            "schema": "gamebench.mode5-preflight.v1", "profile_id": profile.profile_id,
            "environment_class": "community-docker", "paper_compatible": False,
            "status": "fail", "checks": [], "detail": str(exc),
        }
    write_private_json(root / "last-preflight.json", report)
    if report["status"] == "pass":
        write_private_json(root / "license-provider.json", _private_provider_dict(provider))
        write_private_json(root / "active-profile.json", profile.public_dict())
    if args.json:
        print(json.dumps(report, indent=2, sort_keys=True))
    else:
        print(f"Mode 5 doctor: {report['status']}")
        for row in report.get("checks", []):
            print(f"  {row['id']} {row['status']}: {row.get('detail', '')}")
        if report.get("detail"):
            print(f"  {report['detail']}")
    return 0 if report["status"] == "pass" else 1


def cmd_license_request(args: argparse.Namespace) -> int:
    root = state_root(args.state_dir)
    request = export_manual_activation_request(
        image_lock=_read_json(root / "image-lock.json"),
        out=Path(args.out), docker=DockerClient(args.docker),
    )
    print(json.dumps({
        "status": "activation_request_ready", "request": str(request),
        "next": "Only Unity plans that support manual activation can exchange this ALF "
                "for a license file (ULF/XML). Unity Personal and Pro assigned seats "
                "do not support manual activation. If your plan supports it, pass "
                "the returned file to gb mode5 doctor --license-provider file; "
                "otherwise use a supported provider and verify it with doctor.",
    }, indent=2, sort_keys=True))
    return 0


def _active_state(args: argparse.Namespace):
    root = state_root(args.state_dir)
    profile = load_default_profile()
    lock = _read_json(root / "image-lock.json")
    preflight = _read_json(root / "last-preflight.json")
    provider = _provider(args, _read_json(root / "license-provider.json"))
    if preflight.get("status") != "pass":
        raise CommunityDockerError("active Mode 5 profile has no passing doctor result")
    return root, profile, lock, preflight, provider


def cmd_generate(args: argparse.Namespace) -> int:
    from ..generate import generate_task
    package = generate_task(
        _catalog_game_path(args.game), mode="port", out=args.out, playtest_kit=False,
        community_scaffold=True,
    )
    print(f"Mode 5 package -> {package.root}")
    return 0


def _check_release_suite(game_id: str, *, allow_unscored: bool) -> None:
    """Verify the pinned Mode 5 release before starting a paid Agent."""
    from .release_data import load_released_suite, suite_ready
    from ..unity.unity_suite_compiler import suite_content_digest
    _catalog_game_path(game_id)
    try:
        suite, gate = load_released_suite(repo_root(), game_id)
    except (OSError, ValueError, KeyError, RuntimeError) as exc:
        raise CommunityDockerError(f"Mode 5 hidden suite cannot be prepared: {exc}") from exc
    if suite_ready(suite, gate, game_id):
        return
    detail = (
        f"Mode 5 released suite for {game_id} is {suite.get('status')}, runtime_ready="
        f"{str(suite.get('runtime_ready')).lower()}; digest {suite_content_digest(suite)} "
        "has no matching calibration. The maintainer must release a calibrated task."
    )
    if not allow_unscored:
        raise CommunityDockerError(detail + " Use --allow-unscored only for a development smoke run.")
    print("warning: " + detail, file=sys.stderr)



def cmd_evaluate(args: argparse.Namespace) -> int:
    from .docker_evaluator import evaluate_in_container
    _, profile, lock, preflight, provider = _active_state(args)
    report = evaluate_in_container(
        repo_root=repo_root(), package=Path(args.package).resolve(),
        submission=Path(args.submission).resolve(), out=Path(args.out).resolve(),
        profile=profile, image_lock=lock, preflight=preflight,
        license_provider=provider, docker=DockerClient(args.docker),
        fidelity_judge=getattr(args, "fidelity_judge", "none"),
        run_config={
            "harness": "external-submission", "model": "unknown",
            "provider": "unknown", "budget": {"agent_timeout_seconds": None},
            "input": {"source": "external-submission"},
        },
    )
    card = report["scorecard"]
    print(json.dumps({
        "status": card["weighted_total"]["status"],
        "score": card["weighted_total"]["score"],
        "components": card["components"],
        "ranking_eligible": card["ranking_eligible"],
        "environment_class": "community-docker", "paper_compatible": False,
        "report": str(Path(args.out).resolve() / "report.json"),
    }, indent=2, sort_keys=True))
    return 0


def cmd_rejudge(args: argparse.Namespace) -> int:
    from .docker_evaluator import verify_artifact_manifest, write_artifact_manifest, relocate_artifact_paths, _mark_development_unscored
    from .fidelity import complete_fidelity
    from .path_safety import reject_links
    source, out, package = Path(args.evaluation).resolve(), Path(args.out).resolve(), Path(args.package).resolve()
    if out.exists() or out.is_relative_to(source):
        raise CommunityDockerError("rejudge needs a new output directory outside the original evaluation")
    reject_links(source, label="retained evaluation")
    verify_artifact_manifest(source)
    report = _load_verified_report(source)
    assert report is not None
    identity = _read_json(package / "manifest.json")
    if report.get("task_package_identity") != {key: identity.get(key) for key in ("schema_version", "mode", "game_id")}:
        raise CommunityDockerError("rejudge package does not match retained evaluation")
    out.parent.mkdir(parents=True, exist_ok=True)
    stage = Path(tempfile.mkdtemp(prefix="gamebench-mode5-rejudge-", dir=out.parent))
    try:
        shutil.copytree(source, stage, dirs_exist_ok=True)
        report = complete_fidelity(
            report, package, Path(report["submission"]), stage / "runtime-evidence", stage / "fidelity",
            judge=getattr(args, "fidelity_judge", "none"),
        )
        write_private_json(stage / "report.json", report)
        from .report import render_scorecard
        (stage / "report.md").write_text(render_scorecard(report["scorecard"]), encoding="utf-8")
        if (report.get("run_config") or {}).get("development_unscored") is True:
            _mark_development_unscored(report, stage)
            write_private_json(stage / "report.json", report)
        relocate_artifact_paths(stage, out)
        report = _read_json(stage / "report.json")
        write_artifact_manifest(stage)
        verify_artifact_manifest(stage)
        stage.replace(out)
    finally:
        if stage.exists():
            shutil.rmtree(stage)
    print(json.dumps({"report": str(out / "report.json"), "headline": report["headline"]}, indent=2))
    return 0


def _license_environment(provider: LicenseProvider) -> dict[str, str]:
    values = {
        "GB_UNITY_LICENSE_PROVIDER": provider.kind,
        "GB_UNITY_LICENSE_PROBE_PASSED": "1",
    }
    if provider.kind == "file" and provider.source:
        values["GB_UNITY_LICENSE_FILE"] = str(provider.source)
    elif provider.kind == "existing-home" and provider.source:
        values["GB_UNITY_CONFIG_ROOT"] = str(provider.source)
    elif provider.kind == "floating" and provider.endpoint:
        values["GB_UNITY_FLOATING_ENDPOINT"] = provider.endpoint
    return values


def cmd_run(args: argparse.Namespace) -> int:
    from .claude_settings import provider_settings
    with provider_settings(args):
        return _run(args)


def _run(args: argparse.Namespace) -> int:
    if not args.model:
        raise CommunityDockerError("--model is required unless --claude-settings declares it")
    _check_release_suite(args.game, allow_unscored=args.allow_unscored)
    # matrix imports the Docker sandbox module, whose client path is frozen at
    # import time.  Make the explicit Community CLI option authoritative for
    # both the Agent container and the evaluator container.
    if args.docker:
        os.environ["GB_DOCKER_BIN"] = args.docker
    from ..matrix import AgentConfig, run_matrix
    from .docker_evaluator import evaluate_in_container

    _, profile, lock, preflight, provider = _active_state(args)
    run_root = Path(args.out).resolve()
    image_id = str((lock.get("agent_image") or {}).get("id") or "")
    if not image_id:
        raise CommunityDockerError("agent image-lock entry is empty")
    agent = AgentConfig(
        backend=args.harness, command=args.agent_command, model=args.model,
        env_file=args.agent_env_file,
        base_url=args.agent_base_url, key_env=args.agent_key_env,
        effort=args.agent_effort, timeout_s=args.agent_timeout,
        sandbox="docker", docker_image=image_id, provider=args.agent_provider,
        auth_file=args.agent_auth_file,
    )
    previous = {key: os.environ.get(key) for key in _license_environment(provider)}
    os.environ.update(_license_environment(provider))
    try:
        # ``run_matrix`` owns initial directory creation, so the Community
        # journal is materialized immediately after its resumable Agent phase.
        run_matrix(
            run_root, game_ids=[args.game], modes=["port"], agent=agent,
            engine="off", visual_judge="none", resume=args.resume,
            generate_only=False, playtest_kit=False, single_cell=True,
            reference_video=True, evaluate=False,
            community_scaffold=True,
        )
    finally:
        for key, value in previous.items():
            if value is None:
                os.environ.pop(key, None)
            else:
                os.environ[key] = value
    submission = run_root / "submission"
    package = run_root / "package"
    matrix_state = _read_json(run_root / "state.json") if (run_root / "state.json").is_file() else {}
    # A budget exhaustion is a model outcome, not a transport failure. Only
    # harvest when the complete container workspace was attested as returned;
    # otherwise the original scaffold could masquerade as an Agent submission.
    if not submission.is_dir() and matrix_state.get("failure_kind") == "agent_timeout":
        transfer_path = run_root / "agent" / "transfer.json"
        transfer = _read_json(transfer_path) if transfer_path.is_file() else {}
        if isinstance(transfer.get("out"), dict):
            from ..matrix import collect_submission
            try:
                collect_submission(run_root / "workspace", submission, mode="port")
            except (ValueError, RuntimeError):
                # Keep the original timeout diagnostic when no valid project
                # exists. A malformed partial tree is not a complete artifact.
                pass
    if not submission.is_dir():
        try:
            matrix_state = _read_json(run_root / "state.json")
        except OSError:
            matrix_state = {}
        kind = str(matrix_state.get("failure_kind") or "agent_transport_error")
        attribution = (
            "agent_environment" if kind == "agent_environment_invalid" else
            "submission" if kind == "agent_timeout" else "orchestrator"
        )
        _record_phase(run_root, (
            "agent_environment_invalid" if attribution == "agent_environment" else "agent_failed"
        ), status="failed", failure=_failure_record(
            phase="agent", attribution=attribution,
            reason_code=(
                "unity_toolchain_incomplete"
                if attribution == "agent_environment" else
                "agent_budget_exhausted_without_submission"
                if kind == "agent_timeout" else "agent_transport_failed"
            ),
            detail=str(matrix_state.get("detail") or "Agent produced no Unity submission"),
            log="agent/sandbox.log",
        ))
        raise CommunityDockerError("Agent completed without a collected Unity submission")
    for phase in (
        "created", "package_ready", "environment_ready", "agent_running",
        "agent_complete", "submission_collected",
    ):
        _record_phase(run_root, phase)
    evaluation = run_root / "evaluation"
    report: dict[str, Any] | None = None
    if args.resume and evaluation.exists():
        try:
            report = _load_verified_report(evaluation)
        except CommunityDockerError:
            quarantined = _quarantine_incomplete_evaluation(evaluation)
            _record_phase(run_root, "evaluator_running", failure={
                "phase": "evaluator", "status": "retrying",
                "attribution": "orchestrator",
                "reason_code": "artifact_verification_failed",
                "detail": f"incomplete output quarantined as {quarantined.name}",
            })
    elif evaluation.exists():
        raise CommunityDockerError(
            f"evaluation output already exists; pass --resume: {evaluation}"
        )
    if report is None:
        _record_phase(run_root, "evaluator_running")
        try:
            report = evaluate_in_container(
                repo_root=repo_root(), package=package, submission=submission, out=evaluation,
                profile=profile, image_lock=lock, preflight=preflight,
                license_provider=provider, docker=DockerClient(args.docker),
                fidelity_judge=getattr(args, "fidelity_judge", "none"),
                run_config={
                    "harness": args.harness, "model": args.model,
                    "provider": args.agent_provider,
                    "development_unscored": args.allow_unscored,
                    "agent_completion": _agent_completion(run_root, matrix_state),
                    "budget": {
                        "agent_timeout_seconds": args.agent_timeout,
                        "reasoning_effort": args.agent_effort,
                    },
                    "input": {
                        "game_id": args.game, "reference_video": True,
                        "playtest_kit": False, "task_source": "catalog",
                    },
                },
            )
        except Exception as exc:
            _record_phase(
                run_root, "evaluation_infrastructure_inconclusive", status="failed",
                failure=_evaluator_failure(exc),
            )
            raise
        _record_phase(run_root, "objective_complete")
    _record_phase(run_root, "reported", status="completed")
    card = report["scorecard"]
    print(json.dumps({
        "status": "reported", "game": args.game, "model": args.model,
        "score": card["weighted_total"]["score"],
        "components": card["components"],
        "score_mode": card["score_mode"], "official_total": None,
        "ranking_eligible": card["ranking_eligible"],
        "environment_class": "community-docker", "paper_compatible": False,
        "report": str(evaluation / "report.json"),
    }, indent=2, sort_keys=True))
    return 0


def cmd_summarize(args: argparse.Namespace) -> int:
    root = Path(args.run).resolve()
    candidates = [path for path in (
        root / "evaluation" / "report.json", root / "report.json",
    ) if path.is_file()]
    if not candidates:
        candidates = sorted(root.glob("cells/*/evaluation/report.json"))
    if not candidates:
        raise CommunityDockerError(f"no Mode 5 report under {root}")
    reports = []
    for report_path in candidates:
        evaluation = report_path.parent
        if (evaluation / "artifact-manifest.json").is_file():
            report = _load_verified_report(evaluation)
            assert report is not None
        else:
            raise CommunityDockerError("release summarize requires an artifact manifest")
        reports.append(report)
    summary = _summarize_reports(reports)
    write_private_json(root / "summary.json", summary)
    csv_columns = [
        "mode", "registry_version", "ranking_scope", "environment_class",
        "environment_profile", "agent_image_id", "evaluator_image_id",
        "harness", "model", "provider", "budget_configuration",
        "input_configuration", "game_count", "scored", "unscored", "mean_score",
        "lowest_quartile_mean", "reliability_score", "partial_mean_score", "expected_game_count", "coverage",
    ]
    with (root / "summary.csv").open("w", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=csv_columns)
        writer.writeheader()
        for group in summary["groups"]:
            writer.writerow({
                **{column: group.get(column) for column in csv_columns},
                "budget_configuration": json.dumps(
                    group["budget_configuration"], sort_keys=True, separators=(",", ":"),
                ),
                "input_configuration": json.dumps(
                    group["input_configuration"], sort_keys=True, separators=(",", ":"),
                ),
            })
    lines = [
        "# Mode 5 Community Docker leaderboard", "",
        "Community Docker results are not paper-compatible.", "",
        "Main score is the full-catalog arithmetic mean. Tail and reliability columns are diagnostics.", "",
        "| Model | Ranking scope | Scored / expected tasks | Mean score | Low-tail mean | Reliability (diagnostic) | Partial mean (diagnostic) |",
        "|---|---|---:|---:|---:|---:|---:|",
    ]
    for group in summary["groups"]:
        if group["ranking_scope"] == DEVELOPMENT_RANKING_SCOPE:
            continue
        mean = "—" if group["mean_score"] is None else f"{group['mean_score']:.3f}"
        tail = "—" if group["lowest_quartile_mean"] is None else f"{group['lowest_quartile_mean']:.3f}"
        reliability = "—" if group["reliability_score"] is None else f"{group['reliability_score']:.3f}"
        partial = "—" if group["partial_mean_score"] is None else f"{group['partial_mean_score']:.3f}"
        from .report import _safe_label
        lines.append(
            f"| {_safe_label(group['model'])} | {group['ranking_scope']} | "
            f"{group['scored']} / {group['expected_game_count']} | {mean} | "
            f"{tail} | {reliability} | {partial} |"
        )
    (root / "leaderboard.md").write_text("\n".join(lines) + "\n", encoding="utf-8")
    print(json.dumps(summary, indent=2, sort_keys=True))
    return 0


def _summarize_reports(reports: list[Mapping[str, Any]]) -> dict[str, Any]:
    from .report import summarize_reports
    return summarize_reports(reports, expected_games=_catalog_game_ids())


def _catalog_game_ids() -> list[str]:
    from ..content.rubrics import load_rubric_catalog
    return sorted(load_rubric_catalog())


def _positive_agent_budget(value: str) -> int:
    try:
        seconds = int(value)
    except ValueError as exc:
        raise argparse.ArgumentTypeError("Agent budget must be a positive number of seconds") from exc
    if seconds <= 0:
        raise argparse.ArgumentTypeError("Agent budget must be positive; zero does not mean unlimited")
    return seconds


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="gb mode5")
    sub = parser.add_subparsers(dest="command", required=True)
    setup = sub.add_parser("setup", help="verify archives, build images, activate after doctor")
    setup.add_argument(
        "--unity-archives", default="",
        help="directory containing pinned Unity archives (default: download/cache in state dir)",
    )
    setup.add_argument(
        "--no-download", action="store_true",
        help="fail instead of downloading a missing official Unity archive",
    )
    setup.add_argument("--license-provider", default="",
                       choices=("file", "existing-home", "floating"))
    setup.add_argument("--license-file", default="")
    setup.add_argument("--unity-config-root", default="")
    setup.add_argument("--floating-endpoint", default="")
    setup.add_argument("--state-dir", default="")
    setup.add_argument("--docker", default=os.environ.get("GB_DOCKER_BIN") or "docker")
    setup.set_defaults(func=cmd_setup)
    doctor = sub.add_parser("doctor", help="run the ten offline environment gates")
    doctor.add_argument("--json", action="store_true")
    doctor.add_argument("--state-dir", default="")
    doctor.add_argument("--docker", default=os.environ.get("GB_DOCKER_BIN") or "docker")
    doctor.add_argument("--license-provider", default="")
    doctor.add_argument("--license-file", default="")
    doctor.add_argument("--unity-config-root", default="")
    doctor.add_argument("--floating-endpoint", default="")
    doctor.set_defaults(func=cmd_doctor)
    license_request = sub.add_parser(
        "license-request",
        help="export an offline ALF bound to the pinned Community image",
    )
    license_request.add_argument("--out", required=True)
    license_request.add_argument("--state-dir", default="")
    license_request.add_argument(
        "--docker", default=os.environ.get("GB_DOCKER_BIN") or "docker",
    )
    license_request.set_defaults(func=cmd_license_request)
    generate = sub.add_parser("generate", help="generate a public Mode 5 task package")
    generate.add_argument("--game", required=True)
    generate.add_argument("--out", required=True)
    generate.set_defaults(func=cmd_generate)
    evaluate = sub.add_parser("evaluate", help="offline independent rebuild and Objective evaluation")
    evaluate.add_argument("--package", required=True)
    evaluate.add_argument("--submission", required=True)
    evaluate.add_argument("--out", required=True)
    evaluate.add_argument("--state-dir", default="")
    evaluate.add_argument("--docker", default=os.environ.get("GB_DOCKER_BIN") or "docker")
    evaluate.add_argument("--license-provider", default="")
    evaluate.add_argument("--license-file", default="")
    evaluate.add_argument("--unity-config-root", default="")
    evaluate.add_argument("--floating-endpoint", default="")
    evaluate.add_argument("--fidelity-judge", choices=("none",), default="none")
    evaluate.set_defaults(func=cmd_evaluate)
    rejudge = sub.add_parser("rejudge", help="measure retained fidelity evidence without rerunning Agent or Unity")
    rejudge.add_argument("--evaluation", required=True)
    rejudge.add_argument("--package", required=True)
    rejudge.add_argument("--out", required=True)
    rejudge.add_argument("--fidelity-judge", choices=("none",), default="none",
                         help="republish controller-retained evidence without API calls")
    rejudge.set_defaults(func=cmd_rejudge)
    run = sub.add_parser("run", help="generate, run Agent, rebuild, evaluate, and report")
    run.add_argument("--game", required=True)
    run.add_argument("--out", required=True)
    run.add_argument("--harness", default="codex", choices=("codex", "claude", "command"))
    run.add_argument("--model", default="")
    run.add_argument("--claude-settings", default="", help="read only CC model/provider credentials from a private settings.json")
    run.add_argument("--fidelity-judge", choices=("none",), default="none", help="publish the fixed non-VLM evidence proxy")
    run.add_argument("--agent-command", default="",
                     help="command template required when --harness command")
    run.add_argument("--agent-env-file", default="")
    run.add_argument("--agent-base-url", default="https://vip.auto-code.net/v1")
    run.add_argument("--agent-key-env", default="AUTO_CODE_API_KEY")
    run.add_argument("--agent-effort", default="high", choices=("low", "medium", "high"))
    run.add_argument("--agent-timeout", type=_positive_agent_budget, default=7200,
                     help="Agent wall-clock budget in seconds (default: 7200 / 120 minutes)")
    run.add_argument("--agent-provider", default="custom", choices=("custom", "openai", "claude"))
    run.add_argument("--agent-auth-file", default="")
    run.add_argument("--resume", action="store_true")
    run.add_argument(
        "--allow-unscored", action="store_true",
        help="development smoke only: run even when the hidden suite is not calibrated",
    )
    run.add_argument("--state-dir", default="")
    run.add_argument("--docker", default=os.environ.get("GB_DOCKER_BIN") or "docker")
    run.add_argument("--license-provider", default="")
    run.add_argument("--license-file", default="")
    run.add_argument("--unity-config-root", default="")
    run.add_argument("--floating-endpoint", default="")
    run.set_defaults(func=cmd_run)
    summarize = sub.add_parser("summarize", help="print the release headline and provenance")
    summarize.add_argument("run")
    summarize.set_defaults(func=cmd_summarize)
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    try:
        return int(args.func(args))
    except (CommunityDockerError, LicenseConfigurationError, OSError, ValueError, RuntimeError) as exc:
        print(f"mode5 {args.command} failed: {exc}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
