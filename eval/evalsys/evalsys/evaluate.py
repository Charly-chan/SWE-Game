

from __future__ import annotations

import contextlib
import io
import json
import os
import re
import subprocess
from dataclasses import asdict, dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable, Mapping, Sequence

from .interface import (
    ConformanceReport, TaskInterfaceRequirements, load_submission_interface,
)
from .routes.registry import RegisteredRoutes, load_registered_routes
from .tasks import expectations_path, runs_dir
from .truth.expectations import EXPECTATIONS_VERSION, Expectations
from .weights import REGISTRY_VERSION


class EvaluationRefused(RuntimeError):
    pass


@dataclass(frozen=True)
class EvaluationPackage:
    path: Path
    card: Any
    conformance: ConformanceReport


def _slug(value: str) -> str:
    cleaned = re.sub(r"[^A-Za-z0-9._-]+", "_", value).strip("._")
    return cleaned or "submission"


def _commit() -> str:
    try:
        proc = subprocess.run(
            ["git", "rev-parse", "HEAD"], cwd=Path(__file__).resolve().parents[3],
            capture_output=True, text=True, encoding="utf-8", errors="replace", timeout=15,
        )
    except (OSError, subprocess.SubprocessError):
        return ""
    return proc.stdout.strip() if proc.returncode == 0 else ""


def preflight(project: str | Path, task_id: str) -> tuple[Any, TaskInterfaceRequirements, RegisteredRoutes, Expectations]:
    expected_path = expectations_path(task_id)
    if not expected_path.is_file():
        raise EvaluationRefused(f"missing expectations: {expected_path}")
    expected = Expectations.read(expected_path)
    if expected.version != EXPECTATIONS_VERSION:
        raise EvaluationRefused(
            f"expectations version mismatch: file={expected.version!r}, evaluator={EXPECTATIONS_VERSION!r}"
        )
    try:
        registered = load_registered_routes(task_id)
    except (OSError, ValueError) as exc:
        raise EvaluationRefused(f"registered route SHA mismatch: {exc}") from exc
    if registered is None:
        raise EvaluationRefused(f"task {task_id!r} is not present in the route registry")
    requirements = TaskInterfaceRequirements.from_dict(expected.interface_requirements)
    interface = load_submission_interface(project, requirements=requirements)


    return interface, requirements, registered, expected


def evaluate_project(
    project: str | Path,
    *,
    task_id: str,
    tier: str = "D1",
    mode: str = "X_CPU",
    modality: str = "Mv",
    out: str | Path | None = None,
    submission: str | None = None,
    supplied_assets: Sequence[str] = (),
    visual_judge: str = "none",
    task_mode: str = "",
    submission_design: Mapping[str, Any] | None = None,
    replay_filmer: Callable[[Path], Any] | None = None,
    reference_video: str | Path | None = None,
    visual_task_context: str = "",
) -> EvaluationPackage:


    from .pipeline import build_evaluation_context, score_context
    from .report.render_md import card_markdown

    project_path = Path(project).resolve()
    interface, requirements, registered, expected = preflight(project_path, task_id)
    submission_name = submission or project_path.name
    timestamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    package = Path(out).resolve() if out else (
        runs_dir(task_id) / _slug(submission_name) / timestamp
    )
    package.mkdir(parents=True, exist_ok=False)
    (package / "routes").mkdir()

    stdout_buffer = io.StringIO()
    stderr_buffer = io.StringIO()
    try:
        with contextlib.redirect_stdout(stdout_buffer), contextlib.redirect_stderr(stderr_buffer):
            from .context import AssetAssignment
            asset_assignment = (
                AssetAssignment(tuple(str(path) for path in supplied_assets))
                if supplied_assets else None
            )
            if expected.runtime_assets is not None:
                asset_assignment = AssetAssignment(
                    tuple(expected.runtime_assets),
                    supplied_digests=expected.runtime_assets,
                    reference_observed=True,
                )
            ctx = build_evaluation_context(
                project_path,
                task_id=task_id,
                tier=tier,
                mode=mode,
                interface=interface,
                requirements=requirements,
                expectations=expected,
                registered_routes=registered,
                asset_assignment=asset_assignment,
                evidence_dir=package / "captures",
                capture_levels="reached" if _scard_requested(visual_judge) else "first",
                task_mode=task_mode,
                submission_design=submission_design,
            )
            from .visual_evidence import build_visual_evidence_manifest
            if ctx.capture is not None:
                ctx.capture.manifest["visual_evidence"] = build_visual_evidence_manifest(
                    getattr(ctx.capture, "frame_capture", None),
                    package=package,
                )
            truth = ctx.truth
            truth.write(package / "truth_snapshot.json")
            card = score_context(ctx, submission=submission_name, modality=modality)
            _attach_scard(
                card,
                ctx,
                package,
                judge_kind=visual_judge,
                project=submission_name,
                tier=tier,
                modality=modality,
                reference_video=reference_video,
                task_context=visual_task_context,
            )
            _attach_replay_reading(
                card,
                package,
                judge_kind=visual_judge,
                project=submission_name,
                replay_filmer=replay_filmer,
            )
    finally:
        (package / "stdout.log").write_text(stdout_buffer.getvalue(), encoding="utf-8")
        (package / "stderr.log").write_text(stderr_buffer.getvalue(), encoding="utf-8")

    card.write(package / "card.json")
    report_md = card_markdown(card)
    replay_reading = getattr(card, "replay_reading", None)
    if replay_reading:
        from .scard.replay import replay_reading_markdown

        report_md = report_md.rstrip("\n") + "\n\n" + replay_reading_markdown(replay_reading)
    (package / "report.md").write_text(report_md, encoding="utf-8")
    summary = (
        f"submission={submission_name}\n"
        f"task_id={task_id}\n"
        f"tier={tier}\nmode={mode}\nmodality={modality}\n"
        f"score_lo={card.objective_lo:.6f}\n"
        f"score_hi={(card.ocard.hi if card.ocard else 0.0):.6f}\n"
        f"measured_weight_share={card.measured_weight_share:.6f}\n"
        f"coverage={card.coverage:.6f}\n"
        f"route_sha256={registered.route_sha256}\n"
    )
    (package / "summary.txt").write_text(summary, encoding="utf-8")
    route_readings = card.provenance.get("route_readings") or []
    (package / "routes" / "readings.json").write_text(
        json.dumps(route_readings, indent=2, sort_keys=True, ensure_ascii=False),
        encoding="utf-8",
    )
    (package / "capture_manifest.json").write_text(
        json.dumps(
            dict(ctx.capture.manifest if ctx.capture else interface.provenance()),
            indent=2,
            sort_keys=True,
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )
    env = {
        "godot_version": getattr(truth, "engine", ""),
        "evalsys_commit": _commit(),
        "weights_registry_version": REGISTRY_VERSION,
        "route_sha256": registered.route_sha256,
        "expectations_generated_at": expected.generated_at,
        "expectations_version": expected.version,
        "reference_runtime_assets": expected.runtime_assets,
        "asset_observation": (
            asdict(ctx.asset_assignment.observation)
            if getattr(ctx, "asset_assignment", None) is not None
            and ctx.asset_assignment.observation is not None else None
        ),
        "task_id": task_id,
        "tier": tier,
        "mode": mode,
        "modality": modality,
        "conformance": interface.report.to_dict(),
        "interface": interface.provenance(),
        "capture": dict(ctx.capture.manifest if ctx.capture else {}),
        "harness": dict(card.provenance.get("harness") or {}),
    }
    (package / "env.json").write_text(
        json.dumps(env, indent=2, sort_keys=True, ensure_ascii=False), encoding="utf-8"
    )
    from .package_verifier import PackageVerificationError, verify_interface_provenance

    try:
        verify_interface_provenance(package)
    except PackageVerificationError as exc:
        raise EvaluationRefused(f"evaluation package integrity error: {exc}") from exc
    return EvaluationPackage(package, card, interface.report)


DEFAULT_VLM_JUDGE_RUNS = 2
JUDGE_RUNS_ENV = "GAMEBENCH_SCARD_JUDGE_RUNS"


def _scard_requested(judge_kind: str) -> bool:
    return (judge_kind or "none").strip().lower() not in {"", "none", "off"}


def _scard_frame_sets(ctx: Any) -> list[tuple[str, list[str]]]:


    bundle = getattr(ctx, "capture", None)
    out: list[tuple[str, list[str]]] = []
    primary = getattr(bundle, "frame_capture", None)
    paths = list(getattr(primary, "frame_paths", ()) or ())
    if paths:
        out.append((str(getattr(primary, "level", "") or ""), paths))
    for capture in getattr(bundle, "level_captures", ()) or ():
        paths = list(getattr(capture, "frame_paths", ()) or ())
        if paths:
            out.append((str(getattr(capture, "level", "") or ""), paths))
    return out


def _judge_runs(kind: str) -> int:
    if kind != "vlm":
        return 1
    raw = (os.environ.get(JUDGE_RUNS_ENV) or "").strip()
    if not raw:
        return DEFAULT_VLM_JUDGE_RUNS
    try:
        return max(1, int(raw))
    except ValueError:
        return DEFAULT_VLM_JUDGE_RUNS


def _attach_scard(
    card: Any,
    ctx: Any,
    package: Path,
    *,
    judge_kind: str,
    project: str,
    tier: str,
    modality: str,
    runs: int | None = None,
    reference_video: str | Path | None = None,
    task_context: str = "",
) -> None:


    kind = (judge_kind or "none").strip().lower()
    if kind in {"", "none", "off"}:
        return
    if kind not in {"local", "vlm"}:
        raise EvaluationRefused(
            f"unknown visual judge {judge_kind!r}; expected none, local, or vlm"
        )
    frame_sets = _scard_frame_sets(ctx)
    if not frame_sets:
        card.notes.append(
            "S-card requested but evaluator-owned frame capture produced no persistent frames"
        )
        return
    from .scard.calibration import calibration_from_env
    from .scard.judge import (
        JudgeContext,
        LocalHeuristicJudge,
        judge_n_times,
        load_frame,
        vlm_scard_judge_from_env,
        write_judge_report,
    )
    from .scard.scorer import score_scard
    from .scard.reference import reference_video_frames

    frames = []
    for level_index, (level, paths) in enumerate(frame_sets, start=1):
        for index, path in enumerate(paths):
            frames.append(
                load_frame(
                    path,
                    source="evalsys.ocard.adapters.capture_level_frames",
                    point_id=f"level{level_index}_{index}",
                    level=level,
                )
            )
    levels = tuple(level for level, _paths in frame_sets)
    reference, reference_note = reference_video_frames(reference_video, package / "reference_frames")
    judge = (
        vlm_scard_judge_from_env()
        if kind == "vlm"
        else LocalHeuristicJudge()
    )
    context = JudgeContext(
        project=project,
        tier=tier,
        modality=modality,
        levels=levels,
        reference_frames=reference,
        note=(
            f"evaluator-owned temporal samples from {len(levels)} declared level(s); "
            "candidate MP4 not yet attached. " + reference_note
            + ("\nTask requirements:\n" + task_context if task_context else "")
        ),
    )
    n_runs = runs if runs is not None else _judge_runs(kind)
    retest = judge_n_times(judge, frames, context, n=n_runs)
    calibration, calibration_note = calibration_from_env(judge)
    o1 = next((channel.interval for channel in card.channels if channel.channel == "O1"), None)
    notes = [
        "Still-frame S-card only; video-native evidence is not implemented.",
        calibration_note,
        reference_note,
    ]
    if len(levels) < 2:
        notes.append(
            "Frames from one level only: S3 cannot be read. "
            + ("The submission declares a single level."
               if len(getattr(getattr(ctx, "interface", None), "levels", ()) or ()) <= 1
               else "Further declared levels were not captured (truth probe did not reach "
                    "them, or their capture failed); see capture_manifest notes.")
        )
    result = score_scard(
        retest,
        objective_o1=o1,
        project=project,
        calibration=calibration,
        notes=tuple(notes),
    )
    card.scard = result.interval
    card.scard_state = result.calibration_state.value
    write_judge_report(retest, package, name="visual_judge")
    result.write(package / "scard.json")


def _attach_replay_reading(
    card: Any,
    package: Path,
    *,
    judge_kind: str,
    project: str,
    replay_filmer: Callable[[Path], Any] | None,
) -> None:


    if replay_filmer is None or (judge_kind or "none").strip().lower() != "vlm":
        return
    from .scard.judge import vlm_scard_judge_from_env
    from .scard.replay import ReplayFilm, ReplayReading, judge_replay, write_replay_reading

    frames_dir = package / "replay_frames"
    try:
        film = replay_filmer(frames_dir)
    except Exception as exc:
        film = ReplayFilm(directory=str(frames_dir), error=f"replay filming failed: {exc}")
    if film is None:
        reading = ReplayReading(status="not_filmed", detail="the filmer produced no film")
    else:
        try:
            judge = vlm_scard_judge_from_env()
        except Exception as exc:
            judge = None
            reading = ReplayReading(
                film=film.to_dict(), status="unavailable", detail=f"no VLM judge: {exc}"
            )
        if judge is not None:
            reading = judge_replay(film, judge, project=project)
    card.replay_reading = reading.to_dict()
    write_replay_reading(reading, package)
