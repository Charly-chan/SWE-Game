


from __future__ import annotations

import time
import hashlib
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Mapping, Sequence

from .assertions import AssertionSet, Ceiling, channel_overrides_for
from .harness import provenance as harness_provenance
from .ocard import adapters, channels as ch
from .ocard.scorer import ShortcutFinding, score_ocard
from .report.cards import Card, ChannelRecord
from .verdict import Interval
from .weights import CHANNELS
from .context import AssetAssignment, CaptureBundle, Diagnostic, EvaluationContext
from .interface import (
    TaskInterfaceRequirements, load_submission_interface, report_for,
)


@dataclass
class PassA:


    cold: adapters.ColdImportResult | None = None
    probe: adapters.LevelProbeResult | None = None
    deliverables: adapters.DeliverablesResult | None = None
    cf10: Any = None


    truth: Any = None
    expectations: Any = None
    routes: list[Any] = None
    route_readings: list[Any] = None
    route_gold: list[Any] = None
    bot_skip_reason: str | None = None
    seconds: float = 0.0
    notes: list[str] = None

    def __post_init__(self) -> None:
        if self.notes is None:
            self.notes = []
        if self.routes is None:
            self.routes = []
        if self.route_readings is None:
            self.route_readings = []
        if self.route_gold is None:
            self.route_gold = []


@dataclass
class PassB:


    capture: adapters.FrameCaptureResult | None = None
    anchor_frame: str = ""
    seconds: float = 0.0
    notes: list[str] = None

    level_captures: list[adapters.FrameCaptureResult] = None

    def __post_init__(self) -> None:
        if self.notes is None:
            self.notes = []
        if self.level_captures is None:
            self.level_captures = []


def run_pass_a(
    project: str | Path,
    *,
    quick: bool = False,
    task_id: str | None = None,
    expectations_dir: str | Path | None = None,
    truth: Any = None,
    interface: Any = None,
    expectations: Any = None,
    registered_routes: Any = None,
    task_mode: str = "",
) -> PassA:
    t0 = time.time()
    out = PassA()
    if interface is not None:
        project = interface.project_root

    out.cold = adapters.run_cold_import(project)


    out.probe = adapters.run_level_probe(project, mode="H", interface=interface)


    out.truth, out.expectations, notes = _truth_reading(
        project, task_id=task_id, expectations_dir=expectations_dir,
        truth=truth, quick=quick, interface=interface, expectations=expectations,
        task_mode=task_mode,
    )
    out.notes.extend(notes)
    if task_id and not quick:
        routes, readings, gold, route_notes = _registered_route_reading(
            project, task_id, interface=interface, registered=registered_routes
        )
        out.routes = routes
        out.route_readings = readings
        out.route_gold = gold
        out.notes.extend(route_notes)
    out.seconds = time.time() - t0
    return out


def expectations_path(task_id: str, root: str | Path | None = None) -> Path:
    if root is not None:
        return Path(root) / f"{task_id}.json"
    from .tasks import expectations_path as task_expectations_path

    return task_expectations_path(task_id)


def routes_path(task_id: str, root: str | Path | None = None) -> Path:
    if root is not None:
        return Path(root) / f"{task_id}.json"
    from .tasks import route_path as task_route_path

    return task_route_path(task_id)


def _truth_reading(
    project: str | Path,
    *,
    task_id: str | None,
    expectations_dir: str | Path | None,
    truth: Any,
    quick: bool,
    interface: Any = None,
    expectations: Any = None,
    task_mode: str = "",
) -> tuple[Any, Any, list[str]]:
    from .truth.expectations import Expectations

    notes: list[str] = []
    if truth is None and not quick:
        from .probe.runner import take_snapshot


        probe_task_id = task_id if task_mode in {"", "bugfix"} else None
        truth = take_snapshot(project, interface=interface, task_id=probe_task_id)
        notes.extend(truth.notes)
    elif truth is None:
        notes.append(
            "quick mode: no engine-truth snapshot was taken, so O3, O4 and O5 report "
            "`unmeasurable` rather than a number. They are not zeros."
        )

    expected = expectations
    if task_id and expected is None:
        path = expectations_path(task_id, expectations_dir)
        if path.is_file():
            expected = Expectations.read(path)
        else:
            notes.append(
                f"no frozen expectations at {path}, so the channels that compare "
                "against the reference have nothing to compare with"
            )
    return truth, expected, notes


def _registered_route_reading(
    project: str | Path,
    task_id: str,
    *,
    interface: Any = None,
    registered: Any = None,
) -> tuple[list[Any], list[Any], list[Any], list[str]]:

    from .routes.budget import derive
    from .routes.registry import load_registered_routes
    from .routes.runner import GoldValidation, RouteSession, replay_authored_route

    notes: list[str] = []
    if registered is None:
        try:
            registered = load_registered_routes(task_id)
        except (OSError, ValueError) as exc:
            notes.append(f"registered routes for {task_id!r} refused: {exc}")
            return [], [], [], notes
    if registered is None:
        notes.append(f"no human-reviewed route suite is registered for {task_id!r}")
        return [], [], [], notes

    routes = list(registered.routes)
    gold = [
        GoldValidation(
            route.route_id,
            route.route_id in registered.certified_ids,
            (
                f"frozen RT-1 certificate {registered.certificate_path.name}; "
                f"route sha256={registered.route_sha256}"
            ),
        )
        for route in routes
    ]
    session = RouteSession(
        project,
        interface=interface,
        dispatch_extended=getattr(registered, "extended_actions", ()),
        dispatch_axes=getattr(registered, "analog_axes", ()),
    )
    prep = session.prepare()
    if not prep.ok:
        notes.append(
            "route scratch preparation failed once; retrying one fresh copy/import before "
            "accepting a harness-attributed launch failure"
        )
        prep = session.prepare()
    if not prep.ok:
        notes.append(
            "route scratch preparation failed twice; route readings remain inconclusive "
            "and leave the submission denominator"
        )
    readings = []
    for route in routes:
        if route.route_id not in registered.certified_ids:
            continue
        budget = derive(
            route.tier,
            21.6,
            declared_steps=route.budget.steps,
            declared_frames=route.budget.frames,
        )
        reading = replay_authored_route(route, session, budget)


        if (
            route.tier == 5
            and reading.stop_reason in {"timeout", "no_report", "driver_error"}
        ):
            notes.append(
                f"L5 {route.route_id} stopped with {reading.stop_reason}; "
                "retrying once before accepting an inconclusive O7"
            )
            reading = replay_authored_route(route, session, budget)
        readings.append(reading)
    notes.append(
        f"replayed {len(readings)} certified route(s) from a suite reviewed by "
        f"{registered.reviewer!r} on {registered.reviewed_at or 'date not recorded'}"
    )
    return routes, readings, gold, notes


def capture_registered_observables(
    project: str | Path, *, mode: str = "X_CPU", interface: Any = None
) -> tuple[str, list[str]]:

    notes: list[str] = []
    if mode == "H":
        return "", [
            "registered pixel observables require X_CPU or X_GPU; H has no rasteriser"
        ]
    if interface is None:
        interface = load_submission_interface(project)
    project = interface.project_root
    anchor_address = interface.anchor_camera.text if interface.anchor_camera else ""
    if anchor_address:
        from .render.capture import CapturePoint, capture
        from .render.modes import RenderMode

        scene, separator, node_path = anchor_address.partition("::")
        if not separator:
            scene = interface.levels[0].scene if interface.levels else ""
            node_path = ""
        point = CapturePoint(
            id="registered_anchor",
            frame=40,
            level=str(scene),
            purpose="registered O9 anchor",
            anchor_camera=node_path,
        )
        from .probe.inject import SCRATCH_ROOT


        evidence_root = SCRATCH_ROOT.parent / "gb_anchor_output"
        evidence_root.mkdir(parents=True, exist_ok=True)
        project_key = hashlib.sha256(
            str(Path(project).resolve()).encode("utf-8")
        ).hexdigest()[:12]
        anchor_out = evidence_root / f"{project_key}_{time.time_ns()}"
        anchor_run = capture(
            project,
            [point],
            anchor_out,
            mode=RenderMode(mode),
            interface=interface,
        )
        if anchor_run.shots:
            shot = anchor_run.shots[0]
            if shot.ok and (not node_path or shot.anchor_camera_applied):
                anchor_frame = shot.path
            else:
                anchor_frame = ""
                if shot.ok:

                    notes.append(
                        "registered anchor capture rejected: the declared "
                        f"anchor_camera node {node_path!r} was not applied in "
                        f"{scene or 'the first level'} (frame captured: {shot.detail})"
                    )
                else:
                    notes.append("registered anchor capture failed: " + shot.detail)
            return anchor_frame, notes
        notes.append("registered observable run produced no shot record")
    return "", notes


def run_pass_b(
    project: str | Path,
    *,
    mode: str = "X_CPU",
    interface: Any = None,
    evidence_dir: str | Path | None = None,
    extra_levels: Sequence[str] = (),
) -> PassB:
    t0 = time.time()
    out = PassB()
    if interface is not None:
        project = interface.project_root
    if mode == "H":
        out.notes.append(
            "Pass B was requested in H mode. H mode installs a dummy renderer and "
            "produces no pixels at all, so every pixel channel is unmeasurable "
            "rather than failed. Re-run with X_CPU or X_GPU for a real reading."
        )
        out.seconds = time.time() - t0
        return out
    out.anchor_frame, observable_notes = capture_registered_observables(
        project, mode=mode, interface=interface
    )
    out.notes.extend(observable_notes)
    try:
        out.capture = adapters.capture_level_frames(
            project,
            interface=interface,
            keep_dir=evidence_dir,
        )
    except (ValueError, OSError) as exc:


        out.notes.append(f"pass B capture refused: {exc}")
        out.capture = None
    out.level_captures = capture_extra_levels(
        project, extra_levels, interface=interface, evidence_dir=evidence_dir, notes=out.notes
    )
    out.seconds = time.time() - t0
    return out


def capture_extra_levels(
    project: str | Path,
    levels: Sequence[str],
    *,
    interface: Any = None,
    evidence_dir: str | Path | None = None,
    notes: list[str] | None = None,
) -> list[adapters.FrameCaptureResult]:


    out: list[adapters.FrameCaptureResult] = []
    for index, level in enumerate(levels, start=2):
        keep = Path(evidence_dir) / f"level{index}" if evidence_dir else None
        try:
            result = adapters.capture_level_frames(
                project, level=level, interface=interface, keep_dir=keep,
            )
        except (ValueError, OSError) as exc:
            if notes is not None:
                notes.append(f"pass B capture of level {level!r} refused: {exc}")
            continue
        if not result.frame_paths and notes is not None:
            notes.append(f"pass B capture of level {level!r} persisted no frames")
        out.append(result)
    return out


def scard_capture_levels(truth: Any, interface: Any = None) -> list[str]:


    declared: list[str] = []
    if interface is not None:
        declared = [lv.scene for lv in getattr(interface, "levels", ())]
    reached: list[str] = []
    for lv in getattr(truth, "levels", ()) or ():
        if getattr(lv, "reached", False) and getattr(lv, "scene", ""):
            if not getattr(lv, "declared_scene", "") or lv.declared_scene == lv.scene:
                reached.append(lv.scene)
    order = declared or reached
    first = order[0] if order else ""
    return [lv for lv in order if lv != first and lv in reached]


def build_evaluation_context(
    project: str | Path,
    *,
    task_id: str,
    tier: str = "D1",
    mode: str = "X_CPU",
    quick: bool = False,
    expectations_dir: str | Path | None = None,
    truth: Any = None,
    interface: Any = None,
    requirements: TaskInterfaceRequirements | None = None,
    expectations: Any = None,
    registered_routes: Any = None,
    asset_assignment: AssetAssignment | None = None,
    evidence_dir: str | Path | None = None,
    capture_levels: str = "first",
    task_mode: str = "",
    submission_design: Mapping[str, Any] | None = None,
) -> EvaluationContext:


    from dataclasses import replace
    from .truth.expectations import Expectations

    project_path = Path(project).resolve()
    if expectations is None and task_id:
        path = expectations_path(task_id, expectations_dir)
        if path.is_file():
            expectations = Expectations.read(path)
    if requirements is None:
        requirements = TaskInterfaceRequirements.from_dict(
            getattr(expectations, "interface_requirements", {}) or {}
        )
    if interface is None:
        interface = load_submission_interface(project_path, requirements=requirements)
    else:
        interface = replace(
            interface,
            report=report_for(interface, requirements),
        )
    runtime_project = interface.project_root

    if registered_routes is None and task_id:
        from .routes.registry import load_registered_routes
        try:
            registered_routes = load_registered_routes(task_id)
        except (OSError, ValueError):


            registered_routes = None

    a = run_pass_a(
        runtime_project, quick=quick, task_id=task_id,
        expectations_dir=expectations_dir, truth=truth,
        interface=interface, expectations=expectations,
        registered_routes=registered_routes,
        task_mode=task_mode,
    )
    if capture_levels not in ("first", "reached"):
        raise ValueError(f"capture_levels must be 'first' or 'reached', got {capture_levels!r}")
    extra_levels = (
        scard_capture_levels(a.truth, interface) if capture_levels == "reached" else []
    )
    b = run_pass_b(
        runtime_project,
        mode=mode,
        interface=interface,
        evidence_dir=evidence_dir,
        extra_levels=extra_levels,
    )
    if (
        tier != "D1"
        and asset_assignment is not None
        and asset_assignment.supplied_assets
        and asset_assignment.observation is None
    ):


        from dataclasses import replace as dataclass_replace
        from .probe.assets import run_asset_probe

        asset_assignment = dataclass_replace(
            asset_assignment,
            observation=run_asset_probe(runtime_project, interface),
        )
    diagnostics = [Diagnostic("pass_a", note) for note in a.notes]
    diagnostics.extend(Diagnostic("pass_b", note) for note in b.notes)
    capture_manifest = {
        **interface.provenance(),
        "mode": mode,
        "anchor_requested": bool(interface.anchor_camera),
        "anchor_frame": b.anchor_frame,
        "probe_reported": bool(b.anchor_frame) or not interface.anchor_camera,
        "frame_paths": list(getattr(b.capture, "frame_paths", ()) or ()),
        "level_frame_paths": {
            str(getattr(cap, "level", "")): list(getattr(cap, "frame_paths", ()) or ())
            for cap in b.level_captures
        },
        "notes": list(b.notes),
    }
    return EvaluationContext(
        project=interface.project_root,
        task_id=task_id,
        tier=tier,
        mode=mode,
        interface=interface,
        requirements=requirements,
        expectations=expectations,
        registered_routes=registered_routes,
        truth=a.truth,
        route_readings=tuple(a.route_readings),
        route_gold=tuple(a.route_gold),
        capture=CaptureBundle(
            frame_capture=b.capture,
            anchor_frame=b.anchor_frame,
            manifest=capture_manifest,
            level_captures=tuple(b.level_captures),
        ),
        asset_assignment=asset_assignment,
        diagnostics=diagnostics,
        cold_import=a.cold,
        level_probe=a.probe,
        pass_a_seconds=a.seconds,
        pass_b_seconds=b.seconds,
        bot_skip_reason=a.bot_skip_reason,
        task_mode=str(task_mode or ""),
        submission_design=dict(submission_design or {}),
    )


def score_project(
    project: str | Path,
    *,
    submission: str,
    task_id: str,
    tier: str = "D1",
    modality: str = "Mv",
    mode: str = "X_CPU",
    ceiling_path: str | Path | None = None,
    quick: bool = False,
    expectations_dir: str | Path | None = None,
    truth: Any = None,
    asset_assignment: AssetAssignment | None = None,
) -> Card:
    ctx = build_evaluation_context(
        project, task_id=task_id, tier=tier, mode=mode, quick=quick,
        expectations_dir=expectations_dir, truth=truth,
        asset_assignment=asset_assignment,
    )
    return score_context(
        ctx, submission=submission, modality=modality, ceiling_path=ceiling_path
    )


def _item_summary(item: Any) -> dict[str, Any]:

    verdict = getattr(item, "verdict", None)
    attribution = getattr(item, "attribution", None)
    return {
        "id": str(getattr(item, "id", "")),
        "verdict": getattr(verdict, "value", str(verdict)),
        "weight": getattr(item, "weight", None),
        "credit": getattr(item, "credit", None),
        "attribution": getattr(attribution, "value", attribution) if attribution is not None else None,
        "detail": str(getattr(item, "detail", "") or "")[:600],
    }


def score_context(
    ctx: EvaluationContext,
    *,
    submission: str,
    modality: str = "Mv",
    ceiling_path: str | Path | None = None,
) -> Card:

    project = ctx.project
    task_id = ctx.task_id
    tier = ctx.tier
    mode = ctx.mode
    interface = ctx.interface.report

    class _PassView:
        pass

    a = _PassView()
    a.cold = ctx.cold_import
    a.probe = ctx.level_probe
    a.truth = ctx.truth
    a.expectations = ctx.expectations
    a.routes = list(getattr(ctx.registered_routes, "routes", ()) or ())
    a.route_readings = list(ctx.route_readings)
    a.route_gold = list(ctx.route_gold)
    a.seconds = ctx.pass_a_seconds
    a.notes = [diagnostic.detail for diagnostic in ctx.diagnostics]
    a.bot_skip_reason = ctx.bot_skip_reason
    b = _PassView()
    b.capture = ctx.capture.frame_capture if ctx.capture else None
    b.anchor_frame = ctx.capture.anchor_frame if ctx.capture else ""
    b.seconds = ctx.pass_b_seconds
    b.notes = []

    results = [
        ch.score_o1_context(ctx),
        ch.score_o2_context(ctx),
        ch.score_o3_context(ctx),
        ch.score_o4_context(ctx),
        ch.score_o5_context(ctx),
        ch.score_o6_context(ctx),
        ch.score_o7_context(ctx),
        ch.score_o9_context(ctx),
        ch.score_o8_context(ctx),
    ]
    overrides = channel_overrides_for(tier, task_id)
    if ceiling_path:
        ceil_total, ceil_fid = _load_ceiling(ceiling_path, tier, modality)
        ceiling: float | Ceiling = ceil_total
    else:


        ceiling = AssertionSet(task_id, []).ceiling(
            tier, modality, channel_overrides=overrides,
        )
        ceil_total, ceil_fid = ceiling.total, ceiling.fidelity_subset

    shortcuts = _shortcut_findings(a.truth)
    o = score_ocard(
        results, tier, modality, ceiling, project=submission, shortcuts=shortcuts,
    )

    card = Card(
        submission=submission,
        task_id=task_id,
        tier=tier,
        modality=modality,
        ocard=Interval(o.score_lo, o.score_hi, o.coverage, o.denominator, o.by_verdict),
        scard_state="absent",
        ceiling_total=ceil_total,
        ceiling_fidelity=ceil_fid,


        restricted_ceiling=o.restricted_ceiling,
        shortcut_detected=o.shortcut_detected,
        shortcut_evidence=[s.detail for s in o.shortcuts],
        unmeasurable_channels=list(o.unmeasurable_channels),
        inconclusive_rate=o.inconclusive_rate,
        unattributable_error_rate=o.unattributable_error_rate,
        exempt_count=o.exempt_count,
        exempt_weight=o.exempt_weight,
        exempt_items=list(o.exempt_items),
    )
    for r in results:
        card.channels.append(
            ChannelRecord(
                channel=r.channel,
                interval=r.interval,
                weight=CHANNELS[r.channel].weight,
                mode_used=mode if CHANNELS[r.channel].mode in ("X", "either") else "H",
                note=(r.detail or "")[:160],
            )
        )
    card.notes.extend(a.notes)
    card.notes.extend(b.notes)
    card.notes.extend(o.notes)
    failed_axes: list[str] = []
    channel_items: dict[str, list[dict[str, Any]]] = {}
    for r in results:
        rows: list[dict[str, Any]] = []
        for it in getattr(r, "items", []) or []:
            if getattr(getattr(it, "verdict", None), "value", None) == "failed":
                family = str(it.id).split("/", 1)[0]
                if family and family not in failed_axes:
                    failed_axes.append(family)
            rows.append(_item_summary(it))
        channel_items[r.channel] = rows
    card.provenance = {
        "pass_a_seconds": round(a.seconds, 1),
        "pass_b_seconds": round(b.seconds, 1),
        "render_mode": mode,
        "bot_skip_reason": a.bot_skip_reason,
        "failed_axes": failed_axes,


        "channel_items": channel_items,
        "interface_conformance": interface.to_dict(),
        "interface": ctx.interface.provenance(),
        "route_readings": [reading.to_dict() for reading in a.route_readings],
        "harness": dict(getattr(a.truth, "harness", {}) or {}) or harness_provenance(),
    }
    if not interface.ok:
        card.notes.append(
            "Submission diagnostic interface is incomplete. Missing interface fields are "
            "submission obligations: O2 records the conformance defect and every dependent "
            "assertion keeps its own measured failed/skipped result in the denominator. "
            "Only missing reference-only observe/anchor/travel_t capabilities are "
            "unmeasurable."
        )
    if a.bot_skip_reason:
        card.notes.append(
            f"Bot reported skip ({a.bot_skip_reason}). Its downstream checks are "
            "scored `skipped`, which keeps them in the denominator and widens the "
            "interval. A skip that disabled its own detectors and then summed as a "
            "pass is the defect this path exists to prevent."
        )
    return card


def _shortcut_findings(truth: Any) -> list[ShortcutFinding]:


    if truth is None:
        return []
    no_input = getattr(truth, "no_input_no_win", None)
    if no_input is False:
        return [
            ShortcutFinding(
                id="no_input_auto_win",
                detail=(
                    "zero-input control reached the goal "
                    f"(stop_reason={getattr(truth, 'no_input_stop_reason', '')!r})"
                ),
                evidence={
                    "no_input_no_win": False,
                    "no_input_stop_reason": getattr(truth, "no_input_stop_reason", None),
                },
            )
        ]
    return []


def _route_h_items(a: PassA) -> list[Any]:
    if not a.routes:
        return []
    from .routes.runner import h_items_from_segments

    return h_items_from_segments(a.routes, a.route_readings, a.route_gold)


def _load_ceiling(path: str | Path | None, tier: str, modality: str) -> tuple[float, float]:
    if not path:
        return 1.0, 1.0
    import json

    data = json.loads(Path(path).read_text(encoding="utf-8"))
    c = data.get("ceilings", {}).get(f"{tier}/{modality}")
    if not c:
        return 1.0, 1.0
    return float(c["total"]), float(c["fidelity_subset"])


def run_routes(project: str | Path, *, routes_file: str | Path | None = None,
               agent: str = "scripted", interface: Any = None) -> dict[str, Any]:
    from .ocard.channels import score_o7_authored_clear
    from .routes.agent import NullAgent, llm_agent_from_env
    from .routes.budget import derive
    from .routes.runner import (
        RouteSession,
        check_route_set,
        h_items_from_segments,
        score_o8,
        validate_route_on_gold,
    )
    from .routes.schema import load_route_file

    project = Path(project)
    if not routes_file:
        guessed = routes_path(project.name)
        if guessed.is_file():
            routes_file = guessed
        else:
            return {
                "error": "no routes file supplied",
                "note": (
                    "Routes are per-game assets. There is no default set, because a "
                    "route that has not been replayed successfully on the reference "
                    "implementation silently penalises every correct reproduction."
                ),
            }
    routes = load_route_file(routes_file)

    ag: Any
    if agent == "null":
        ag = NullAgent()
    elif agent == "llm":
        prov = llm_agent_from_env()
        if not getattr(prov, "available", False):
            return {
                "error": "no provider credentials",
                "verdict": "inconclusive",
                "note": "A missing provider is our failure, not the submission's.",
            }
        ag = prov.agent
    else:
        ag = None

    if interface is None:
        interface = load_submission_interface(project)
    session = RouteSession(project, interface=interface)
    session.prepare()
    gold = []
    readings = []


    ms = 21.6
    for r in routes:
        budget = derive(
            r.tier,
            ms,
            declared_steps=r.budget.steps,
            declared_frames=r.budget.frames,
        )
        if agent == "scripted":
            g = validate_route_on_gold(r, session, budget)
            gold.append(g)
            if g.reading is not None:
                readings.append(g.reading)
        else:
            readings.append(session.run(r, ag, budget))
    o8_items, o8_interval = score_o8(routes, readings)
    o7 = score_o7_authored_clear(routes, readings, gold)
    h_preview = h_items_from_segments(routes, readings, gold)
    from .routes.registry import load_registered_routes

    registered = None
    try:
        route_path = Path(routes_file)


        registered_task_id = (
            route_path.parent.name if route_path.name == "route.json" else route_path.stem
        )
        registered = load_registered_routes(registered_task_id)
    except (OSError, ValueError):
        registered = None
    h_registered = bool(
        registered is not None
        and registered.route_path.resolve() == Path(routes_file).resolve()
    )
    findings = check_route_set(routes, gold=gold)
    return {
        "interface": interface.provenance(),
        "harness": harness_provenance(),
        "routes": len(routes),
        "gold": [g.to_dict() for g in gold],
        "rt1_passed": [g.route_id for g in gold if g.ok],
        "rt1_failed": [g.route_id for g in gold if not g.ok],
        "findings": [f.to_dict() for f in findings],
        "o7": o7.to_dict(),
        "o8": {
            "interval": o8_interval.to_dict(),
            "items": [i.to_dict() for i in o8_items],
        },
        "h_preview": [i.to_dict() for i in h_preview],
        "h_registered": h_registered,
        "note": (
            "h_preview is eligible for O3 only through the frozen route registry; "
            "the legacy passive-assertion REGISTRY is a separate mechanism."
            if h_registered
            else "h_preview is not in O3 until this exact route file is human-reviewed, "
            "RT-1-certified and entered in routes/registry.json."
        ),
    }


_SCARD_AXIS = {
    "S1": "surface_completeness",
    "S2": "ui_hygiene",
    "S3": "atmosphere_consistency",
    "S4": "feel",
}


def selfcheck() -> tuple[bool, list[str]]:


    from .verdict import Verdict, exempt, failed, passed, score_items, skipped

    lines: list[str] = []
    ok = True

    def check(name: str, cond: bool, detail: str) -> None:
        nonlocal ok
        lines.append(f"  {'ok  ' if cond else 'FAIL'} {name}: {detail}")
        if not cond:
            ok = False

    lines.append("scoring kernel")
    iv = score_items([passed("a"), passed("b")])
    check("can go green", iv.lo == 1.0 and iv.coverage == 1.0, f"all-pass -> {iv}")
    iv = score_items([failed("a"), failed("b")])
    check("can go red", iv.lo == 0.0 and iv.hi == 0.0, f"all-fail -> {iv}")

    iv = score_items([passed("a"), skipped("b")])
    check(
        "a skip is not a pass",
        iv.lo == 0.5 and iv.hi == 1.0 and iv.coverage == 0.5,
        f"one pass + one skip -> {iv}; lo != hi, so the gap is visible",
    )

    iv = score_items([passed("a"), exempt("b")])
    check(
        "an exemption is not a pass and does not leave the denominator",
        iv.lo == 0.5 and iv.hi == 1.0 and iv.coverage == 1.0 and iv.denominator == 2.0,
        f"one pass + one exempt -> {iv}; width records the exemption, coverage stays 1",
    )

    iv_skip = score_items([passed("a"), skipped("b")])
    iv_fail = score_items([passed("a"), failed("b")])
    check(
        "a skip is never better than a failure on the ranked score",
        iv_skip.lo <= iv_fail.lo,
        f"skip lo={iv_skip.lo:.3f} <= fail lo={iv_fail.lo:.3f}",
    )

    iv = score_items([])
    check(
        "an empty denominator is not a zero",
        iv.denominator == 0.0,
        "no items -> denominator 0, which the report must render as 'not measured'",
    )

    lines.append("error attribution")
    try:
        from .verdict import Item

        Item(id="x", verdict=Verdict.ERROR)
        check("an unattributed error is refused", False, "it was accepted")
    except ValueError:
        check("an unattributed error is refused", True,
              "constructing one raises; an error without a blame target is a gap")

    lines.append("weight registry")
    from .weights import CHANNELS, O_CARD_SHARE

    total = sum(c.weight for c in CHANNELS.values())
    check("weights sum to the registered share", abs(total - O_CARD_SHARE) < 5e-4,
          f"{total:.4f} == {O_CARD_SHARE}")

    lines.append("ladder propagation")
    from .weights import O1_LADDER

    items = O1_LADDER.resolve({"cold_import": (Verdict.PASSED, "")})
    blocked = [i for i in items if i.verdict is Verdict.SKIPPED]
    check("unreported rungs are skipped, never passed", len(blocked) == 3,
          f"{len(blocked)} of 4 rungs marked skipped when only the first reported")


    lines.append("evaluator reconciliation")
    from .ocard.manifest import CheckManifest, evaluate_cf10

    manifest = CheckManifest.from_expectations("selfcheck", "tool", {"mode": 1})
    cf10 = evaluate_cf10(manifest, {})
    check(
        "CF-10 catches a vanished evaluator check",
        not cf10.satisfied,
        f"missing expected record -> satisfied={cf10.satisfied}",
    )

    lines.append("")
    lines.append("PASS" if ok else "FAIL — the harness cannot be trusted in this state")
    return ok, lines
