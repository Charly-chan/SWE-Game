"""Score a submission against a taskgen package.

Static gates always run. Engine gates run when Godot is on PATH / GODOT_BIN
and `--engine` is not `off`. A missing engine is `inconclusive`, never a
zero: that is our gap, not the submission's (same rule as a missing LLM key).
"""

from __future__ import annotations

import hashlib
import json
import os
import re
import shutil
import tempfile
from dataclasses import dataclass, field, replace
from pathlib import Path
from typing import Any, Collection, Mapping

from ..interface import load_submission_interface
from ..interface.contract import REQUIRED_GROUPS
from ..render.modes import scaled_budget
from ..routes.runner import reading_from_report
from ..routes.schema import Route
from ..verdict import (
    Attribution,
    Item,
    Verdict,
    failed,
    inconclusive,
    passed,
    score_items,
    skipped,
    unobservable,
)
from .antigrant import (
    LIMITS,
    declared_health_property,
    health_write_binding,
    is_blocking_finding,
    new_blocking_sites,
    scan_auto_win_ready,
    scan_tree,
)
from .engine import (
    KEEP_SCRATCH_ENV,
    _stop,
    clear_predicate,
    godot_available,
    isolated_taskgen_scratch,
    reading_unmeasured,
    reading_won,
    run_bugfix_gates,
    run_self_play,
)
from .modes import Mode, needs_gdd, needs_ops, parse_mode
from .edit_radius import edit_radius_item
from .mutations import declared_groups
from .package import TaskPackage, write_json
from .submission import (
    Submission,
    load_submission,
    load_unity_submission,
    ops_have_player_action,
)
from .gdd_quality import audit_authored_gdd, declared_interface_contract
from .materials import o6_denominator
from .content.rubrics import (
    extended_action_count_finding,
    interface_rubric_findings,
    rubric_milestones,
)
from .skeleton import STUB_MARKER, scene_script_attachments
from .transformation import (
    TransformationManifest,
    audit_mode3_submission,
    audit_mode4_submission,
)
from .content.verifier_profiles import BRIEF_DESIGN_ITEMS, missing_strict_items, verifier_profile
from .mode5.attribution import _attribute_mode5_items, _cascade_mode5_root_failure
from .mode5.pipeline import phase_order as mode5_phase_order
from .scorecard import (
    _mode4_candidate_import_items,
    _mode5_item_attribution,
    brief_layout_constraint,
    default_registry_for_mode,
    registry_policy,
    score_task_result,
)
from ..report.terminology import OBJECTIVE_EVALUATION, PERCEPTUAL_ASSESSMENT, display_terms
from .demonstrations import (
    demonstration_items, film_demonstrations, film_task_visuals, run_demonstrations, summarize_demonstrations,
)
from .unity.unity_interface import UnityInterfaceReport, validate_unity_interface
from .unity.unity_runtime import build_unity_submission
from .unity.unity_probe import (
    PROTOCOL as UNITY_PROBE_PROTOCOL,
    UnityProbeRun,
    UnityRuntimeSuite,
    run_unity_runtime_suite,
)
from .mode5.adapter import snapshot_static, snapshot_runtime
from .unity.unity_antigrant import scan_unity_antigrant
from .unity.unity_sdk import validate_scaffold_integrity

SCRIPT_COLLAPSE_RATIO = 0.5
REPORT_SCHEMA = "gamebench.taskgen.report.v4"
_UNRESOLVED_VERDICTS = frozenset({
    Verdict.FAILED,
    Verdict.MALFORMED,
    Verdict.SKIPPED,
    Verdict.EXEMPT,
})


def _walk_evidence(value: Any):
    """Yield nested evidence values without imposing producer-specific shapes."""
    if isinstance(value, Mapping):
        yield value
        for nested in value.values():
            yield from _walk_evidence(nested)
    elif isinstance(value, (list, tuple)):
        for nested in value:
            yield from _walk_evidence(nested)


def _root_cause_metadata(item: Item) -> dict[str, str] | None:
    """Stable report-only cluster and cause class for an unresolved item.

    Producers may supply ``root_cause_class`` explicitly. Otherwise a repeated
    script error or route stop supplies the cluster signature, which lets the
    strict trace/witness pair report one runtime defect without changing either
    item's score.
    """
    if item.verdict not in _UNRESOLVED_VERDICTS:
        return None
    mappings = list(_walk_evidence(item.evidence))
    explicit = next(
        (
            str(row["root_cause_class"])
            for row in mappings
            if row.get("root_cause_class")
        ),
        "",
    )
    errors = sorted({
        str(error).strip()
        for row in mappings
        for error in (row.get("errors") or [])
        if str(error).strip()
    })
    stops = sorted({
        str(row["stop_reason"])
        for row in mappings
        if row.get("stop_reason")
    })
    routes = sorted({
        str(row["route_id"])
        for row in mappings
        if row.get("route_id")
    })
    if errors:
        signature = "script_error|" + errors[0]
        cause_class = explicit or "submission_runtime"
    elif explicit:
        signature = "explicit|" + explicit
        cause_class = explicit
    elif stops:
        signature = "route_stop|" + ",".join(stops) + "|" + ",".join(routes)
        cause_class = (
            "evaluator_or_route"
            if item.attribution is Attribution.HARNESS
            else "submission_runtime"
        )
    else:
        signature = f"item|{item.id}|{item.verdict.value}"
        cause_class = (
            "evaluator"
            if item.attribution is Attribution.HARNESS
            else "submission_contract"
            if item.verdict is Verdict.MALFORMED
            else "submission"
        )
    digest = hashlib.sha256(signature.encode("utf-8")).hexdigest()[:12]
    return {
        "cluster_id": f"rc-{digest}",
        "cause_class": cause_class,
    }


def _reported_item(item: Item) -> dict[str, Any]:
    row = item.to_dict()
    root_cause = _root_cause_metadata(item)
    if root_cause is not None:
        row["root_cause"] = root_cause
    return row


def _reporting_summary(
    items: list[Item],
    *,
    strict_ids: Collection[str],
    resolved: bool | None,
) -> dict[str, Any]:
    unresolved = [
        item for item in items
        if item.id in strict_ids and item.verdict in _UNRESOLVED_VERDICTS
    ]
    failing = [
        {"id": item.id, **(_root_cause_metadata(item) or {})}
        for item in unresolved
    ]
    clusters: dict[str, dict[str, Any]] = {}
    for item in items:
        metadata = _root_cause_metadata(item)
        if metadata is None:
            continue
        cluster = clusters.setdefault(
            metadata["cluster_id"],
            {
                "cluster_id": metadata["cluster_id"],
                "cause_class": metadata["cause_class"],
                "item_ids": [],
            },
        )
        cluster["item_ids"].append(item.id)
    return {
        "resolved": resolved,
        "failing_strict_items": failing,
        "root_cause_clusters": list(clusters.values()),
        "rule": "reporting metadata only; clusters do not change verdicts, credit, or weights",
    }


def _finding_loci(findings: list[Any]) -> str:
    """file:line list for CLI — do not hide the grant site behind a 120-char cut."""
    bits: list[str] = []
    for item in findings[:8]:
        path = getattr(item, "path", "") or ""
        line = getattr(item, "line", None)
        if path and line not in (None, ""):
            bits.append(f"{path}:{line}")
        elif path:
            bits.append(str(path))
    return ", ".join(bits)


class EvalTaskError(RuntimeError):
    """Package or submission could not be opened; not a scored failure."""


@dataclass
class TaskEvalResult:
    package: TaskPackage
    submission: Submission
    items: list[Item] = field(default_factory=list)
    engine: dict[str, Any] = field(default_factory=dict)
    limits: tuple[str, ...] = LIMITS
    #: Brief mode: what the statement asked for, as far as the scorecard
    #: needs it (`layout_constraint` keeps O5 a score axis under calib4).
    brief_context: dict[str, Any] = field(default_factory=dict)
    #: `S4_replay`: the VLM's reading of the submission's own replay
    #: (`scard.replay`).  Reported separately, weight 0; never in the headline.
    replay_reading: dict[str, Any] | None = None
    #: Scorecard registry row this result is scored under (``None`` = the
    #: scorecard default, ``2026-09-11.evidence1``).  `bench eval-task --registry`
    #: / `rescore.py --registry` opt into older loadable rows, including
    #: ``2026-09-05.mode4`` and the intermediate ``2026-09-08.mode34``.
    registry_version: str | None = None
    #: Historical constructed results include Design; evaluate_task selects the
    #: default for new evaluations and writes the choice into report.json.
    brief_design: bool = True

    @property
    def interval(self):
        """Diagnostic interval over behavioral checks only.

        Static contract compliance and optional reference fidelity are not
        exchangeable with playability.  Keeping them out of this interval
        prevents a submission from compensating for a failed clear with files
        that merely exist.
        """
        return score_items(self.behavior_items)

    @property
    def behavior_items(self) -> list[Item]:
        return [item for item in self.items if item.id in BEHAVIOR_ITEM_IDS]

    @property
    def fidelity_items(self) -> list[Item]:
        return [item for item in self.items if item.id in FIDELITY_ITEM_IDS]

    @property
    def eligibility_items(self) -> list[Item]:
        excluded = BEHAVIOR_ITEM_IDS | FIDELITY_ITEM_IDS
        if self.package.manifest.get("mode") == "brief" and not self.brief_design:
            excluded = excluded | BRIEF_DESIGN_ITEMS
        return [item for item in self.items if item.id not in excluded]

    @property
    def eligibility_status(self) -> str:
        return _eligibility_status(self.eligibility_items)

    @property
    def comparable(self) -> bool:
        return _comparable(self.behavior_items)

    @property
    def resolved(self) -> bool | None:
        """Strict task outcome; diagnostic scores never substitute for a failed gate."""
        mode = str(self.package.manifest.get("mode") or "")
        if mode == "port":
            # Mode 5 is fail-closed: an unobservable evaluator-private behavior
            # or counterfactual is not a PASS.  Older code collected these ids
            # as `not_measured_required_items` but still returned True because
            # the generic interval silently dropped out-of-denominator items.
            required = verifier_profile(mode).strict_ids
            by_id = {item.id: item for item in self.items}
            for item_id in required:
                item = by_id.get(item_id)
                if item is not None and item.verdict in {
                    Verdict.FAILED,
                    Verdict.MALFORMED,
                    Verdict.SKIPPED,
                    Verdict.EXEMPT,
                }:
                    return False
            if any(
                item_id not in by_id or by_id[item_id].verdict is not Verdict.PASSED
                for item_id in required
            ):
                return None
            return True
        if self.eligibility_status == "failed":
            return False
        for item in self.behavior_items:
            if item.verdict in {
                Verdict.FAILED,
                Verdict.MALFORMED,
                Verdict.SKIPPED,
                Verdict.EXEMPT,
            }:
                return False
        if self.eligibility_status != "passed" or not self.comparable:
            return None
        return bool(self.behavior_items)

    def to_dict(self) -> dict[str, Any]:
        interval = self.interval
        mode = str(self.package.manifest.get("mode") or "")
        profile = verifier_profile(mode, brief_design=self.brief_design)
        scorecard = score_task_result(self, registry_version=self.registry_version)
        scorecard["witness_protocol"] = "gamebench.feature-demos.v1" if self.submission.demos_path else "whole-game-ops"
        strict_ids = profile.strict_ids
        profile_payload = profile.to_dict()
        if self.submission.demos_path is not None:
            strict_ids = strict_ids | {"demonstrations_complete"}
            profile_payload["behavior_ids"] = sorted(set(profile_payload["behavior_ids"]) | {"demonstrations_complete"})
            profile_payload["purpose"] = "independent task-feature demonstrations with matched controls and reference-conditioned fidelity"
        reporting = _reporting_summary(
            self.items,
            strict_ids=strict_ids,
            resolved=self.resolved,
        )
        scorecard["reporting"] = reporting
        scorecard["strict"]["failing_items"] = reporting["failing_strict_items"]
        weighted = dict(scorecard.get("weighted_total") or {})
        headline = {
            "status": (
                scorecard.get("outcome_status")
                if mode == "port" else weighted.get("status")
            ) or "evaluation_incomplete",
            "score": weighted.get("score"),
            "scale": weighted.get("scale") or "0-100",
            "ranking_eligible": bool(scorecard.get("ranking_eligible")),
            "score_scope": str(scorecard.get("score_scope") or "task_capability"),
        }
        if mode == "port":
            top_score: dict[str, Any] = dict(headline)
            score_scope = "mode5_evidence_adjusted_proxy"
        else:
            top_score = interval.to_dict()
            score_scope = "behavior_only_diagnostic"
        return {
            "report_schema": REPORT_SCHEMA,
            "metric_schema": "gamebench.taskgen.outcome.v2",
            "mode": mode,
            **({"brief_design": self.brief_design} if mode == "brief" else {}),
            "verifier_profile": profile_payload,
            "game_id": self.package.manifest.get("game_id"),
            "package": str(self.package.root),
            "submission": str(self.submission.root),
            "headline": headline,
            "score": top_score,
            "score_scope": score_scope,
            "comparable": self.comparable,
            "resolved": self.resolved,
            "resolution": reporting,
            "eligibility": {
                "status": self.eligibility_status,
                "items": [_reported_item(item) for item in self.eligibility_items],
            },
            "behavior": {
                "score": interval.to_dict(),
                "score_scope": "behavior_only_diagnostic",
                "items": [_reported_item(item) for item in self.behavior_items],
            },
            "fidelity": {
                "items": [_reported_item(item) for item in self.fidelity_items],
            },
            "scorecard": scorecard,
            "items": [_reported_item(item) for item in self.items],
            "engine": self.engine,
            "limits": list(self.limits),
            "blockers": list(self.package.manifest.get("blockers") or []),
            "brief_context": dict(self.brief_context),
            "replay_reading": self.replay_reading,
            "demonstrations": self.engine.get("demonstration_summary"),
            "demonstration_visuals": self.engine.get("demonstration_visuals", []),
            "witness_protocol": "gamebench.feature-demos.v1" if self.submission.demos_path else "whole-game-ops",
        }

    def write(self, out: str | Path) -> Path:
        dest = Path(out).resolve()
        dest.mkdir(parents=True, exist_ok=True)
        write_json(dest / "report.json", self.to_dict())
        (dest / "report.md").write_text(render_report(self), encoding="utf-8")
        return dest


def evaluate_task(
    package: str | Path,
    submission: str | Path,
    *,
    engine: str = "auto",
    score_against_source: bool | None = None,
    visual_judge: str = "none",
    out: str | Path | None = None,
    registry_version: str | None = None,
    brief_design: bool | None = None,
) -> TaskEvalResult:
    pkg = TaskPackage.read(package)
    mode = parse_mode(str(pkg.manifest.get("mode") or ""))
    registry_version = registry_version or default_registry_for_mode(mode.id)
    policy = registry_policy(registry_version, mode=mode.id)
    if mode.id == "brief":
        if brief_design is None:
            brief_design = not policy.mode1_redesign
        elif not brief_design and not policy.mode1_redesign:
            raise ValueError("--brief-design off requires a Mode-1 redesign or VLM registry")
    else:
        brief_design = True
    sub = (
        load_unity_submission(submission)
        if mode.id == "port"
        else load_submission(submission)
    )
    oracle = _oracle(pkg)
    demo_selection = {}
    if sub.demos and mode.id in {"brief", "gdd", "skeleton"}:
        from ..scard.task_visual import select_demonstrations, visual_budget

        sub.demos, omitted = select_demonstrations(sub.demos, oracle.get("rubric") or {})
        demo_selection = {**visual_budget(oracle.get("rubric") or {}),
                          "selected": [d["id"] for d in sub.demos], "omitted": omitted,
                          "selection": "first max_demos in demo-id order"}
    if mode.id == "port":
        return _evaluate_unity_port(
            pkg,
            sub,
            oracle,
            engine=engine,
            visual_judge=visual_judge,
            out=out,
            registry_version=registry_version,
        )
    items: list[Item] = []

    items.append(_layout_item(sub))
    if needs_gdd(mode):
        items.append(_gdd_item(sub))
        items.append(_authored_gdd_quality_item(sub))
        items.append(_authored_gdd_interface_item(sub))
        items.append(_brief_gdd_grounding_item(sub, oracle))
    if mode.id == "gdd":
        items.append(_task_gdd_contract_item(oracle))
    if mode.id in {"brief", "gdd", "skeleton"}:
        items.append(_rubric_interface_item(sub, oracle, mode=mode.id))
    if needs_ops(mode):
        items.extend(_ops_items(sub))
    items.append(_interface_item(sub))
    items.append(_anti_grant_item(sub, mode, oracle))
    items.append(_auto_win_item(sub))
    items.append(_health_item(sub))
    if mode.id == "bugfix":
        items.extend(_bugfix_items(sub, oracle))
        items.append(edit_radius_item(pkg.visible / "game", sub.project, oracle))
    elif mode.id == "skeleton":
        items.extend(_skeleton_items(sub, oracle))

    engine_payload: dict[str, Any] = {"requested": engine, "ran": False,
                                     "demo_selection": demo_selection}
    want_engine = _want_engine(engine)
    want_repro = (
        score_against_source
        if score_against_source is not None
        else bool(oracle.get("score_against_source") or pkg.manifest.get("score_against_source"))
    )
    reproduction_out = Path(out).resolve() / "reproduction" if out else None
    if reproduction_out is not None and reproduction_out.exists():
        # ``eval-task --out`` is an overwrite-style report destination.  Keep
        # its evaluator-owned reproduction subtree consistent with the new
        # report instead of retaining a card from an earlier attempt.
        shutil.rmtree(reproduction_out)
    scratch_tag = f"eval-{pkg.manifest.get('game_id') or 'task'}-{mode.id}"
    with isolated_taskgen_scratch(scratch_tag) as scratch_root:
        engine_payload["scratch"] = str(scratch_root)
        engine_payload["scratch_kept"] = os.environ.get(
            KEEP_SCRATCH_ENV, ""
        ).strip() in {"1", "true", "yes"}
        if want_engine:
            engine_payload.update(_run_engine(mode, pkg, sub, oracle, items))
            stops = evaluator_side_stops(engine_payload)
            if stops:
                # One retry, in its own scratch root and with every wall
                # deadline scaled up. An evaluator-side stop (timeout, launch
                # failure, scratch collision) says nothing about the
                # submission; if it repeats, the headline is withheld as
                # `evaluation_incomplete` rather than read as an interval.
                first_attempt = {
                    "scratch": str(scratch_root),
                    "evaluator_side_stops": stops,
                    "items": [
                        item.to_dict() for item in items if item.id in ENGINE_ITEM_IDS
                    ],
                }
                retry_items: list[Item] = []
                with isolated_taskgen_scratch(f"{scratch_tag}-retry") as retry_root, \
                        scaled_budget(RETRY_BUDGET_SCALE):
                    retry_payload = _run_engine(mode, pkg, sub, oracle, retry_items)
                items[:] = [item for item in items if item.id not in ENGINE_ITEM_IDS]
                items.extend(retry_items)
                engine_payload.update(retry_payload)
                engine_payload["scratch"] = str(retry_root)
                engine_payload["retry"] = {
                    "budget_scale": RETRY_BUDGET_SCALE,
                    "first_attempt": first_attempt,
                    "evaluator_side_stops": evaluator_side_stops(retry_payload),
                }
        else:
            items.extend(_engine_skipped(mode, "engine disabled"))

        if sub.demos_path is not None and mode.id in {"brief", "gdd", "skeleton"}:
            if not any(item.id == "demonstrations_complete" for item in items):
                items.append(inconclusive("demonstrations_complete", detail="feature demonstrations were not executed"))
            if not policy.task_visual and visual_judge == "vlm" and want_engine and engine_payload.get("demonstrations"):
                visual_out = (Path(out).resolve() if out else scratch_root) / "demonstrations"
                try:
                    engine_payload["demonstration_visuals"] = film_demonstrations(
                        sub, load_submission_interface(sub.project), oracle.get("rubric") or {},
                        engine_payload, visual_out,
                    )
                except Exception as exc:
                    engine_payload["demonstration_visuals"] = [{"reading": {"status": "unavailable", "detail": str(exc)}}]

        if policy.task_visual and mode.id in {"brief", "gdd", "skeleton"}:
            from ..scard.task_visual import aggregate_task_visual

            visual_rows = []
            game_visual = policy.mode1_redesign or bool(policy.progressive_redesign_mode)
            visual_item = None
            if visual_judge == "vlm" and want_engine:
                visual_out = (Path(out).resolve() if out else scratch_root) / "demonstrations"
                try:
                    if game_visual:
                        from .demonstrations import capture_task_visuals
                        from .visual_materials import prepare_visual_manifest
                        from ..scard.game_visual import judge_game_visual

                        manifest = capture_task_visuals(
                            sub, load_submission_interface(sub.project), oracle.get("rubric") or {},
                            engine_payload, visual_out,
                        )
                        manifest = prepare_visual_manifest(pkg, manifest, visual_out)
                        visual_item = judge_game_visual(manifest, visual_out / "judgments")
                        visual_rows = manifest["demonstrations"]
                    else:
                        visual_rows = film_task_visuals(
                            sub, load_submission_interface(sub.project), oracle.get("rubric") or {},
                            engine_payload, visual_out,
                        )
                except Exception as exc:
                    visual_rows = [{"id": "capture", "reading": {"status": "unavailable", "detail": str(exc)}}]
                    if game_visual:
                        visual_item = inconclusive("task_visual", detail=f"Game VLM evidence unavailable: {exc}")
            if game_visual:
                items.append(visual_item or inconclusive(
                    "task_visual", detail="Game VLM requires --visual-judge vlm and an enabled engine"))
            else:
                items.append(aggregate_task_visual(visual_rows, oracle.get("rubric") or {}))
            engine_payload["demonstration_visuals"] = visual_rows

        if want_repro and mode.id in {"brief", "gdd", "skeleton", "bugfix"}:
            items.append(
                _reproduction_item_with_retry(
                    sub,
                    pkg,
                    want_engine,
                    visual_judge="none" if policy.task_visual else visual_judge,
                    out=reproduction_out,
                    design=_submission_design(mode, sub, oracle, engine_payload),
                )
            )

    if mode.id == "bugfix" and policy.progressive_redesign_mode == "bugfix":
        normalized = _mode4_candidate_import_items({item.id: item for item in items}, engine_payload)
        items = [normalized[item.id] for item in items]

    if mode.id in {"skeleton", "bugfix"}:
        items.append(_transformation_contract_item(mode, pkg, sub, items))

    items.append(_verifier_profile_item(mode, items, brief_design=brief_design))

    result = TaskEvalResult(
        pkg, sub, items=items, engine=engine_payload,
        brief_context=_brief_context(pkg) if mode.id == "brief" else {},
        replay_reading=_replay_reading_from_items(items),
        registry_version=registry_version,
        brief_design=brief_design,
    )
    if out:
        result.write(out)
    return result


def _submission_design(
    mode: Mode, sub: Submission, oracle: dict[str, Any], engine_payload: Mapping[str, Any],
) -> dict[str, Any]:
    """Submission-authored inputs for the reproduction step (brief O4, S4_replay)."""
    design: dict[str, Any] = {
        "witness": dict(engine_payload.get("ops_env") or {}) or None,
    }
    if sub.gdd_path is not None and Path(sub.gdd_path).is_file():
        design["gdd_text"] = Path(sub.gdd_path).read_text(encoding="utf-8", errors="replace")
    if needs_ops(mode) and sub.ops and engine_payload.get("ran"):
        try:
            interface = load_submission_interface(sub.project)
        except Exception:  # an unreadable interface already failed `interface`
            interface = None
        if interface is not None:
            design.update({
                "ops": list(sub.ops),
                "interface": interface,
                "predicate": clear_predicate(interface),
                "milestones": (
                    rubric_milestones(oracle.get("rubric") or {})
                    if mode.id in {"brief", "gdd", "skeleton"} else ()
                ),
            })
    return design


def _replay_reading_from_items(items: list[Item]) -> dict[str, Any] | None:
    reproduction = next((item for item in items if item.id == "reproduction"), None)
    if reproduction is None or not isinstance(reproduction.evidence, dict):
        return None
    card = reproduction.evidence.get("card")
    reading = card.get("replay_reading") if isinstance(card, dict) else None
    return dict(reading) if isinstance(reading, dict) else None


def _brief_context(pkg: TaskPackage) -> dict[str, Any]:
    """Read the brief statement once for the scorecard's brief-mode rules."""
    statement = pkg.visible / "statement.md"
    text = statement.read_text(encoding="utf-8", errors="replace") if statement.is_file() else ""
    return {
        "statement": str(statement) if statement.is_file() else "",
        "layout_constraint": brief_layout_constraint(text),
    }


def _evaluate_unity_port(
    pkg: TaskPackage,
    sub: Submission,
    oracle: dict[str, Any],
    *,
    engine: str,
    visual_judge: str,
    out: str | Path | None,
    registry_version: str | None = None,
) -> TaskEvalResult:
    """Evaluate M5 with static, build, intervention and visual evidence layers."""
    report = validate_unity_interface(sub.project)
    items: list[Item] = []
    if visual_judge not in {"none", "off", ""}:
        raise ValueError("Mode 5 uses the fixed non-VLM evidence proxy")
    static_items = _unity_static_items(report)
    items.extend(static_items)
    items.append(_task_gdd_contract_item(oracle))
    items.append(_port_contract_alignment_item(report, oracle))
    items.extend(_ops_items(sub))
    no_smuggling_item = _port_no_smuggling_item(sub)
    no_godot_item = _no_bundled_godot_runtime_item(sub)
    items.append(no_smuggling_item)
    items.append(no_godot_item)
    items.append(_build_recipe_item(sub))
    sdk_integrity_item = _unity_sdk_integrity_item(pkg, sub)
    items.append(sdk_integrity_item)
    anti_grant_item = _unity_anti_grant_static_item(sub)
    items.append(anti_grant_item)

    static_snapshot = snapshot_static(
        sub.project, baseline=pkg.visible / "target_unity",
        rubric=oracle.get("rubric") or {},
        interface=report.manifest.to_dict() if report.manifest else {},
        ops_valid=any(item.id == "ops_valid" and item.verdict is Verdict.PASSED for item in items),
        ops_nonidle=any(item.id == "ops_not_idle" and item.verdict is Verdict.PASSED for item in items),
        reference_project=pkg.visible / "source_godot",
    )
    ephemeral = out is None
    provisioned_runtime_root = os.environ.get("GB_UNITY_RUNTIME_ROOT", "").strip()
    runtime_out = (
        Path(provisioned_runtime_root).resolve()
        if provisioned_runtime_root
        else Path(out).resolve() / "unity_runtime"
        if out is not None
        else Path(tempfile.mkdtemp(prefix="gamebench-unity-eval-"))
    )
    if not ephemeral and runtime_out.exists():
        shutil.rmtree(runtime_out)
    runtime_out.mkdir(parents=True, exist_ok=True)
    suite: UnityRuntimeSuite | None = None
    visual_reading: dict[str, Any] | None = None
    try:
        # SDK/evaluator integrity is an execution boundary, not a capability
        # reading. Never build code that replaced the probe or smuggled evaluator
        # expectations to obtain a visual capture.
        hard_gate_ids = {"unity_layout", "unity_sdk_integrity", "no_eval_smuggling",
                         "no_bundled_godot_runtime", "unity_anti_grant_static"}
        runtime_blockers = [
            item for item in (
                *static_items,
                no_smuggling_item,
                no_godot_item,
                sdk_integrity_item,
                anti_grant_item,
            )
            if item.id in hard_gate_ids and item.verdict is Verdict.FAILED
        ]
        if runtime_blockers:
            blocked_by = runtime_blockers[0].id
            detail = f"Unity build refused by static hard gate: {blocked_by}"
            build_item = skipped("unity_build", detail=detail)
            build_payload = {
                "status": "skipped",
                "detail": detail,
                "blocked_by": blocked_by,
            }
        else:
            build_item, build_payload = _unity_build_item(
                report,
                sub,
                engine=engine,
                runtime_out=runtime_out,
            )
        items.append(build_item)
        if (
            build_payload.get("status") == "pass"
            and build_payload.get("probe_protocol") == UNITY_PROBE_PROTOCOL
            and build_payload.get("executable")
        ):
            suite = run_unity_runtime_suite(
                str(build_payload["executable"]),
                report.manifest,  # type: ignore[arg-type]
                sub.ops,
                runtime_out / "runs",
                hidden_route_path=(
                    None
                    if os.environ.get("GB_UNITY_SKIP_LEGACY_ROUTES", "").strip() == "1"
                    else pkg.hidden / "reference" / "route.json"
                ),
                hidden_behavior_dir=pkg.hidden / "unity" / "behavior",
                diagnostic_smoke=(
                    os.environ.get("GB_UNITY_DIAGNOSTIC_SMOKE", "").strip() == "1"
                ),
                visual_capture_requested=(
                    os.environ.get("GB_UNITY_SKIP_VISUAL_CAPTURE", "").strip() != "1"
                ),
            )
            write_json(runtime_out / "suite.json", suite.to_dict())
            items.extend(
                _unity_runtime_items(
                    suite,
                    rubric=oracle.get("rubric") or {},
                    visual_judge=visual_judge,
                    out=runtime_out / "visual",
                    game_id=str(pkg.manifest.get("game_id") or ""),
                    levels=[item.scene for item in report.manifest.levels],  # type: ignore[union-attr]
                    reference_video=_reference_video(pkg),
                    task_context=_visual_task_context(pkg, oracle),
                )
            )
            from .mode5.visual_measure import measure_visual
            try:
                visual_reading = measure_visual(static_snapshot, suite)
            except Exception as exc:  # optional visual measurement must retain the report
                visual_reading = {
                    "schema": "gamebench.mode5.visual-reading.v1",
                    "complete": False,
                    "infrastructure_complete": False,
                    "metric": "visual_implementation_correspondence",
                    "policy": {},
                    "observations": [],
                    "diagnostics": [{
                        "status": "error",
                        "reason": f"visual measurement unavailable: {type(exc).__name__}: {exc}",
                        "attribution": "infrastructure",
                    }],
                    "measurements": {},
                    "ocr_binary": None,
                    "vlm_used": False,
                    "perceptual_similarity_measured": False,
                }
        else:
            reason = _unity_runtime_unavailable_reason(build_payload)
            items.extend(_unity_runtime_unavailable_items(reason, visual_judge))
            if registry_policy(registry_version).task_visual:
                items.append(inconclusive("task_visual", detail=reason))
        items.append(_verifier_profile_item(parse_mode("port"), items))
        items = _attribute_mode5_items(items)
        result = TaskEvalResult(
            pkg,
            sub,
            items=items,
            engine={
                "requested": engine,
                "engine": "unity",
                "mode5_phase_order": list(mode5_phase_order()),
                "ran": build_payload.get("status") in {"pass", "fail"},
                "build": build_payload,
                "runtime_probe_ran": suite is not None,
                "mode5_static": static_snapshot,
                "mode5_runtime": snapshot_runtime(suite) if suite is not None else {},
                "mode5_visual": visual_reading,
                "runtime": suite.to_dict() if suite is not None else None,
            },
            registry_version=registry_version,
        )
        if out:
            result.write(out)
        return result
    finally:
        if ephemeral:
            shutil.rmtree(runtime_out, ignore_errors=True)


def _unity_build_item(
    report: UnityInterfaceReport,
    sub: Submission,
    *,
    engine: str,
    runtime_out: Path,
) -> tuple[Item, dict[str, Any]]:
    flag = (engine or "auto").strip().lower()
    if flag in {"off", "never", "no", "0", "false"}:
        detail = "Unity build disabled by evaluator configuration"
        return inconclusive("unity_build", detail=detail), {
            "status": "inconclusive",
            "detail": detail,
        }
    if report.manifest is None:
        detail = "Unity interface is statically invalid; evaluator build was not attempted"
        return skipped("unity_build", detail=detail), {
            "status": "skipped",
            "detail": detail,
        }

    build = build_unity_submission(sub.project, report.manifest, runtime_out)
    evidence = build.to_dict()
    if build.status == "pass":
        item = passed("unity_build", detail=build.detail, evidence=evidence)
    elif build.status == "fail":
        item = failed("unity_build", detail=build.detail, evidence=evidence)
    else:
        item = inconclusive("unity_build", detail=build.detail, evidence=evidence)
    return item, evidence


def _unity_runtime_unavailable_reason(build: dict[str, Any]) -> str:
    status = str(build.get("status") or "")
    if status != "pass":
        return str(build.get("detail") or "Unity build did not produce a runnable player")
    if build.get("probe_protocol") != UNITY_PROBE_PROTOCOL:
        return "evaluator build did not attest the injected Unity runtime probe protocol"
    return "evaluator build produced no player executable"


def _unity_runtime_unavailable_items(reason: str, visual_judge: str) -> list[Item]:
    return [inconclusive(ident, detail=reason) for ident in (
        "unity_probe", "unity_mechanic_trace", "unity_runtime_stability", "unity_auto_win_ready",
        "unity_input_dispatch", "unity_hidden_behavior", "unity_source_behavior", "unity_counterfactual",
        "legacy_reference_trace", "causal_witness", "null_no_win", "unity_evaluator_capture",
    )]


def _unity_runtime_items(
    suite: UnityRuntimeSuite,
    *,
    rubric: dict[str, Any],
    visual_judge: str,
    out: Path,
    game_id: str,
    levels: list[str],
    reference_video: Path | None,
    task_context: str,
) -> list[Item]:
    witness = suite.witness
    items: list[Item] = [_unity_probe_item(witness)]
    items.append(_unity_mechanic_obligations_item(suite, rubric))
    items.append(_unity_runtime_stability_item(suite))
    items.append(_unity_auto_win_item(suite.auto_win))
    input_item = _unity_input_dispatch_item(witness, suite.matched_null)
    items.append(input_item)
    items.extend(_unity_hidden_behavior_items(suite))
    items.append(_unity_route_item(suite))

    if witness.status == "pass" and suite.matched_null.status == "pass":
        causal_item = _causal_witness_item(
            _unity_reading(witness), _unity_reading(suite.matched_null),
        )
        items.append(causal_item)
        items.append(_null_item(_unity_reading(suite.matched_null)))
    else:
        detail = "witness and matched-null probe executions did not both complete"
        causal_item = (
            failed(
                "causal_witness", detail=detail,
                attribution=Attribution.SUBMISSION, evidence=suite.to_dict(),
            )
            if any(run.status == "fail" for run in (witness, suite.matched_null))
            else inconclusive("causal_witness", detail=detail, evidence=suite.to_dict())
        )
        items.append(causal_item)
        items.append(inconclusive("null_no_win", detail=detail, evidence=suite.matched_null.to_dict()))

    if suite.mash is None:
        items.append(
            unobservable(
                "extended_mash_no_win",
                detail="Unity manifest v1 declares no task-specific extra actions to mash",
            )
        )
    elif suite.mash.status != "pass":
        items.append(
            inconclusive(
                "extended_mash_no_win",
                detail=suite.mash.detail,
                evidence=suite.mash.to_dict(),
            )
        )
    else:
        items.append(
            _mash_item({
                "applicable": True,
                "won": suite.mash.won,
                "extras": [],
                "analog_axes": [],
                "reading": _unity_reading(suite.mash),
            })
        )

    capture_evidence = {
        "owner": "evaluator",
        "subject": "candidate_submission",
        "capture_kind": "sampled_frame_video" if suite.video_path else "frame_sequence",
        "frame_paths": list(suite.frame_paths),
        "video_path": suite.video_path,
        "timeline_basis": "probe frame / fixed 60 FPS",
    }
    if suite.frame_paths:
        items.append(
            passed(
                "unity_evaluator_capture",
                detail=(
                    f"captured {len(suite.frame_paths)} evaluator-owned PNG sample(s)"
                    + (" and encoded a sampled MP4" if suite.video_path else "")
                ),
                evidence=capture_evidence,
            )
        )
    else:
        items.append(
            inconclusive(
                "unity_evaluator_capture",
                detail=(
                    "Unity probe completed without a persistent evaluator-owned PNG"
                ),
                evidence=capture_evidence,
            )
        )

    return items


def _unity_mechanic_trace_item(run: UnityProbeRun, rubric: dict[str, Any]) -> Item:
    """Grade GDD mechanics from the submitted whole-game witness stream.

    Probe completion is only a runtime gate.  The mechanic score comes from
    the implementation-neutral rubric predicates actually observed in rows.
    A predicate that cannot be observed earns no credit; removing it from the
    denominator made a successful probe indistinguishable from perfect game
    mechanics.
    """
    checkpoints = rubric_milestones(rubric)
    if not checkpoints:
        return inconclusive(
            "unity_mechanic_trace", detail="hidden rubric has no executable mechanic checks"
        )
    if not run.reading.get("rows"):
        return inconclusive(
            "unity_mechanic_trace",
            detail="Unity witness produced no complete semantic row stream",
            evidence=run.to_dict(),
        )
    route = Route.from_dict({
        "route_id": "unity/witness/mechanics",
        "tier": 2,
        "goal": {
            "predicate": "whole_game_clear()",
            "observations": [checkpoint.to_dict() for checkpoint in checkpoints],
        },
    })
    reading = reading_from_report(route, run.reading, log="")
    reached = set(reading.observations_reached)
    expected = [checkpoint.name for checkpoint in checkpoints]
    credit = len(reached) / len(expected)
    evidence = {
        "expected": expected,
        "reached": sorted(reached),
        "missing": [name for name in expected if name not in reached],
        "triggered": list(reading.observations_triggered),
        "missing_observations": list(reading.missing_groups),
        "witness_artifact": run.result_path,
    }
    return passed(
        "unity_mechanic_trace",
        credit=credit,
        detail=f"witness observed {len(reached)}/{len(expected)} rubric mechanic checks",
        evidence=evidence,
    )


def _unity_mechanic_obligations_item(
    suite: UnityRuntimeSuite, rubric: dict[str, Any],
) -> Item:
    """Best independently observed evidence per calibrated GDD obligation.

    A trigger is worth half only when that obligation declares a trigger; mere
    manifest claims, elapsed time, and input dispatch never count as mechanics.
    """
    checkpoints = rubric_milestones(rubric)
    if not checkpoints:
        return inconclusive(
            "unity_mechanic_trace", detail="hidden rubric has no executable mechanic checks",
        )
    runs = [suite.witness, *suite.hidden_behaviors]
    readings: list[dict[str, Any]] = []
    credit_by_check = {check.name: 0.0 for check in checkpoints}
    route = Route.from_dict({
        "route_id": "unity/mechanic-obligations", "tier": 2,
        "goal": {"predicate": "whole_game_clear()",
                 "observations": [check.to_dict() for check in checkpoints]},
    })
    for run in runs:
        if not run.reading.get("rows"):
            continue
        observed = reading_from_report(route, run.reading, log="")
        reached = set(observed.observations_reached)
        triggered = set(observed.observations_triggered)
        for check in checkpoints:
            point = 1.0 if check.name in reached else 0.0
            credit_by_check[check.name] = max(credit_by_check[check.name], point)
        readings.append({
            "run_id": run.run_id, "reached": sorted(reached),
            "triggered": sorted(triggered),
        })
    if not readings:
        if any(run.status == "fail" for run in runs):
            return failed(
                "unity_mechanic_trace", detail="candidate produced no usable mechanic stream",
                attribution=Attribution.SUBMISSION,
            )
        return inconclusive(
            "unity_mechanic_trace", detail="no evaluator-observable mechanic stream",
        )
    credit = sum(credit_by_check.values()) / len(checkpoints)
    null_reading = reading_from_report(route, suite.matched_null.reading, log="") if suite.matched_null.reading.get("rows") else None
    return passed(
        "unity_mechanic_trace", credit=credit,
        detail=f"{sum(value > 0 for value in credit_by_check.values())}/{len(checkpoints)} mechanic obligations have observable progress",
        evidence={"credits": credit_by_check, "runs": readings,
                  "matched_null_reached": sorted(null_reading.observations_reached) if null_reading else [],
                  "rule": "verified effect=1; trigger alone or absent=0"},
    )


def _unity_runtime_stability_item(suite: UnityRuntimeSuite) -> Item:
    """Grade clean independent candidate cold runs, not evaluator compliance.

    The witness and evaluator-private positive scenarios each start a fresh
    Player process.  Behavior success is scored elsewhere; this item asks only
    whether the candidate stayed alive, emitted a semantic stream and avoided
    runtime errors throughout those independent lifecycles.
    """
    runs = [suite.witness, *suite.hidden_behaviors]
    if not runs:
        return inconclusive(
            "unity_runtime_stability",
            detail="no candidate positive cold runs were scheduled",
        )
    rows: list[dict[str, Any]] = []
    graded: list[dict[str, Any]] = []
    clean = 0
    for run in runs:
        semantic_rows = list(run.reading.get("rows") or ())
        errors = list(run.reading.get("errors") or ())
        healthy = run.status == "pass" and bool(semantic_rows) and not errors
        # Explicit candidate launch failures are measured zeros even when
        # they produce no stream. Missing evaluator readings retain coverage.
        owner = str(run.reading.get("attribution") or "")
        candidate_failure = (
            run.status == "fail" and owner not in {"infrastructure", "harness", "unattributable"}
        ) or (
            owner == "submission" and bool(run.reading.get("dependency_root"))
        )
        measured = candidate_failure or (
            run.status == "pass" and (bool(semantic_rows) or bool(errors))
        )
        row = {
            "run_id": run.run_id,
            "kind": run.kind,
            "status": run.status,
            "semantic_rows": len(semantic_rows),
            "error_count": len(errors),
            "error_examples": errors[:8],
            "clean": healthy,
            "measured": measured,
        }
        rows.append(row)
        if measured:
            graded.append(row)
            clean += int(healthy)
    unmeasured = [row["run_id"] for row in rows if not row["measured"]]
    evidence: dict[str, Any] = {
        "runs": rows, "scheduled_runs": len(runs), "measured_runs": len(graded),
        "clean_runs": clean, "coverage": len(graded) / len(runs),
    }
    if unmeasured:
        evidence.update(
            unmeasured_runs=unmeasured,
            attribution=Attribution.HARNESS.value,
            rule="every scheduled cold run stays in coverage; missing evaluator readings require retry",
        )
        return inconclusive(
            "unity_runtime_stability",
            detail=f"{len(unmeasured)}/{len(runs)} candidate cold runs have no reading: " + ", ".join(unmeasured),
            evidence=evidence,
        )
    credit = clean / len(runs)
    if clean == 0:
        return failed(
            "unity_runtime_stability",
            detail=f"0/{len(runs)} candidate cold runs completed cleanly",
            evidence=evidence,
        )
    return passed(
        "unity_runtime_stability",
        credit=credit,
        detail=f"{clean}/{len(runs)} candidate cold runs completed with semantic rows and no runtime errors",
        evidence=evidence,
    )


def _unity_input_dispatch_item(run: UnityProbeRun, matched_null: UnityProbeRun) -> Item:
    if run.status != "pass":
        return inconclusive(
            "unity_input_dispatch",
            detail="input dispatch could not be attested because the witness run did not complete",
            evidence=run.to_dict(),
        )
    events = [
        event for event in run.reading.get("events") or []
        if isinstance(event, dict) and event.get("kind") == "input_dispatched"
    ]
    pressed = [
        event for event in events
        if (
            event.get("active") is True
            and float(event.get("control_value") or 0.0) > 0.0
        ) or (
            (
                bool(event.get("actions"))
                or any(abs(float(value)) > 0.0 for value in dict(event.get("axes") or {}).values())
            )
            and int(event.get("resolved_device_count") or 0) > 0
            and int(event.get("enabled_device_count") or 0) > 0
        )
    ]
    released = [
        event for event in events
        if (
            event.get("active") is False
            and float(event.get("control_value") or 0.0) == 0.0
        ) or (
            "actions" in event
            and not event.get("actions")
            and not any(abs(float(value)) > 0.0 for value in dict(event.get("axes") or {}).values())
            and int(event.get("resolved_device_count") or 0) > 0
            and int(event.get("enabled_device_count") or 0) > 0
        )
    ]
    active_rows = run.reading.get("rows")
    idle_rows = matched_null.reading.get("rows") if matched_null.status == "pass" else None
    if not isinstance(active_rows, list) or not isinstance(idle_rows, list) or not active_rows or not idle_rows:
        return inconclusive(
            "unity_input_dispatch",
            detail="matched-horizon evaluator observations were unavailable",
            evidence={"active": run.to_dict(), "idle": matched_null.to_dict()},
        )

    def primitive_trace(rows: list[object]) -> list[object]:
        values = []
        for raw in rows:
            if not isinstance(raw, Mapping):
                continue
            values.append({
                "g": raw.get("g"), "o": raw.get("o"),
                "position": [raw.get("px"), raw.get("py"), raw.get("pz")],
                "wgc": raw.get("wgc"), "lv": raw.get("lv"),
                "d": raw.get("d"), "c": raw.get("c"),
            })
        return [
            value for index, value in enumerate(values)
            if index == 0 or value != values[index - 1]
        ]

    effect = primitive_trace(active_rows) != primitive_trace(idle_rows)
    if pressed and released and effect:
        return passed(
            "unity_input_dispatch",
            detail=(
                f"Input System observed {len(pressed)} active dispatch(es) and "
                f"{len(released)} release dispatch(es), with evaluator-observed effect versus matched idle"
            ),
            evidence={"events": events, "primitive_effect": True},
        )
    if pressed and released:
        return failed(
            "unity_input_dispatch",
            detail="Input System received the command but evaluator primitives matched idle",
            evidence={"events": events, "primitive_effect": False},
        )
    return failed(
        "unity_input_dispatch",
        detail="controller commands did not produce both an active Input System value and release",
        evidence={"events": events},
    )


def _behavior_run_verdict(run: UnityProbeRun) -> str:
    if run.status != "pass":
        return "inconclusive" if run.status == "inconclusive" else "fail"
    result = run.reading.get("behavior_result") or {}
    if isinstance(result, Mapping) and result.get("missing_observations"):
        return "fail" if _missing_public_contract_observations(run) else "unobservable"
    if (not isinstance(result, Mapping)
            or result.get("status") not in {"pass", "fail"}
            or not isinstance(result.get("goal_reached"), bool)):
        return "inconclusive"
    if result["status"] == "pass" and not result["goal_reached"]:
        return "inconclusive"
    return result["status"]


def _behavior_run_attribution(run: UnityProbeRun) -> str:
    """Separate candidate evidence from policy/observer/infrastructure holes.

    Hidden behavior inputs are admitted only after the independent calibration
    gate in ``unity_probe._hidden_behavior_inputs``. A clean, observable miss
    therefore belongs to the candidate; an unavailable observation or malformed
    evaluator result never does.
    """

    if run.status != "pass":
        return "infrastructure_failed" if run.status == "inconclusive" else "candidate_failed"
    result = run.reading.get("behavior_result") or {}
    if isinstance(result, Mapping) and result.get("missing_observations"):
        return "candidate_failed" if _missing_public_contract_observations(run) else "observation_inconclusive"
    if (
        not isinstance(result, Mapping)
        or result.get("status") not in {"pass", "fail"}
        or not isinstance(result.get("goal_reached"), bool)
        or (result.get("status") == "pass" and not result.get("goal_reached"))
    ):
        return "policy_inconclusive"
    return "passed" if result["status"] == "pass" else "candidate_failed"


def _missing_public_contract_observations(run: UnityProbeRun) -> bool:
    """A declared public telemetry slot missing from a completed run is candidate-owned.

    Optional geometry/device observations still remain evaluator-unobservable.
    The controller command carries the public manifest's required slots/roles,
    so this classification does not inspect any hidden scenario expectation.
    """
    result = run.reading.get("behavior_result") or {}
    missing = result.get("missing_observations") if isinstance(result, Mapping) else None
    if not isinstance(missing, list) or not missing or not run.reading.get("rows"):
        return False
    declared: set[str] = set()
    for argument in run.command:
        if argument.startswith("--gb-numeric-slots="):
            declared.update("numeric:" + name for name in argument.split("=", 1)[1].split(",") if name)
        elif argument.startswith("--gb-required-roles="):
            declared.update("role:" + name for name in argument.split("=", 1)[1].split(",") if name)
    return all(str(name) in declared for name in missing)


def _aggregate_behavior_item(
    item_id: str, runs: list[UnityProbeRun],
) -> Item:
    if not runs:
        return inconclusive(item_id, detail="package has no runnable hidden behavior scenarios")
    statuses = {run.run_id: _behavior_run_verdict(run) for run in runs}
    attributions = {run.run_id: _behavior_run_attribution(run) for run in runs}
    evidence = {
        "runs": [run.to_dict() for run in runs],
        "statuses": statuses,
        "attributions": attributions,
        "attribution_rule": (
            "candidate_failed is available only because draft policies are excluded "
            "until their independent calibration gate is runtime-ready"
        ),
    }
    unobserved_ids = [run_id for run_id, status in statuses.items() if status == "unobservable"]
    if unobserved_ids:
        return unobservable(
            item_id,
            detail=(
                "hidden scenarios are missing required geometry/device observations: "
                + ", ".join(unobserved_ids)
            ),
            evidence=evidence,
        )
    if any(status == "inconclusive" for status in statuses.values()):
        return inconclusive(
            item_id,
            detail="one or more evaluator-owned policies were inconclusive",
            evidence=evidence,
        )
    failed_ids = [
        run_id for run_id, status in statuses.items()
        if status not in {"pass", "unobservable"}
    ]
    if failed_ids:
        passed_count = sum(status == "pass" for status in statuses.values())
        credit = passed_count / len(statuses)
        return failed(
            item_id,
            credit=credit,
            detail=(
                f"{passed_count}/{len(statuses)} hidden semantic goals passed; failed: "
                + ", ".join(failed_ids)
            ),
            evidence=evidence,
        )
    return passed(
        item_id,
        detail=f"all {len(runs)} evaluator-owned semantic scenarios passed",
        evidence=evidence,
    )


def _unity_hidden_behavior_items(
    suite: UnityRuntimeSuite,
) -> list[Item]:
    runs = list(suite.hidden_behaviors)
    hidden_item = _aggregate_behavior_item(
        "unity_hidden_behavior", runs,
    )
    source_runs = [
        run for run in runs
        if "source_derived" in set(run.reading.get("evidence_basis") or ())
    ]
    source_item = _aggregate_behavior_item("unity_source_behavior", source_runs)

    counterfactuals = list(suite.counterfactuals)
    if not counterfactuals:
        counterfactual_item = inconclusive(
            "unity_counterfactual",
            detail="package has no runnable action counterfactuals",
        )
    else:
        observable_counterfactuals = [
            run for run in counterfactuals
            if _behavior_run_verdict(run) != "unobservable"
        ]
        if not observable_counterfactuals:
            counterfactual_item = unobservable(
                "unity_counterfactual",
                detail=(
                    "counterfactual scenarios require geometry/device observations that "
                    "the public Unity marker contract does not require"
                ),
                evidence={"runs": [run.to_dict() for run in counterfactuals]},
            )
            return [hidden_item, source_item, counterfactual_item]
        infrastructure = [
            run for run in observable_counterfactuals
            if run.status != "pass"
            or not isinstance(run.reading.get("behavior_result"), Mapping)
            or run.reading["behavior_result"].get("status") not in {"pass", "fail"}
            or run.reading["behavior_result"].get("missing_observations")
            or not isinstance(run.reading["behavior_result"].get("goal_reached"), bool)
            or not isinstance(run.reading.get("counterfactual_expect_goal"), bool)
        ]
        mismatches = []
        for run in observable_counterfactuals:
            result = run.reading.get("behavior_result") or {}
            if not isinstance(result, Mapping):
                continue
            reached = result.get("goal_reached")
            expected = bool(run.reading.get("counterfactual_expect_goal", False))
            if run.status == "pass" and reached != expected:
                mismatches.append(run.run_id)
        candidate_runtime_failures = [
            run for run in infrastructure
            if run.status == "fail"
            and (
                "input dispatch failed: action has no enabled buttoncontrol:"
                in run.detail.lower()
                or run.detail.startswith("Unity runtime emitted errors:")
                or run.detail.startswith("candidate player exited with code ")
            )
        ]
        if infrastructure and len(candidate_runtime_failures) == len(infrastructure):
            counterfactual_item = failed(
                "unity_counterfactual",
                detail=(
                    "counterfactual commands could not complete because the "
                    "candidate failed at runtime"
                ),
                attribution=Attribution.SUBMISSION,
                evidence={"runs": [run.to_dict() for run in counterfactuals]},
            )
        elif infrastructure:
            counterfactual_item = inconclusive(
                "unity_counterfactual",
                detail="one or more counterfactual executions did not complete",
                evidence={"runs": [run.to_dict() for run in counterfactuals]},
            )
        elif mismatches:
            counterfactual_item = failed(
                "unity_counterfactual",
                detail="action ablation violated expected causality: " + ", ".join(mismatches),
                evidence={"runs": [run.to_dict() for run in counterfactuals]},
            )
        else:
            counterfactual_item = passed(
                "unity_counterfactual",
                detail=f"all {len(counterfactuals)} action ablations matched expectation",
                evidence={"runs": [run.to_dict() for run in counterfactuals]},
            )
    return [hidden_item, source_item, counterfactual_item]
def _unity_probe_item(run: UnityProbeRun) -> Item:
    if run.status == "pass":
        return passed("unity_probe", detail=run.detail, evidence=run.to_dict())
    if run.status == "fail":
        return failed("unity_probe", detail=run.detail, evidence=run.to_dict())
    return inconclusive("unity_probe", detail=run.detail, evidence=run.to_dict())


def _unity_auto_win_item(run: UnityProbeRun | None) -> Item:
    if run is None:
        return inconclusive(
            "unity_auto_win_ready",
            detail="evaluator-owned bounded no-input run was not available",
        )
    if run.won:
        return failed(
            "unity_auto_win_ready",
            detail="candidate reached success during the bounded cold-start no-input horizon",
            evidence=run.to_dict(),
        )
    if run.status != "pass":
        return inconclusive("unity_auto_win_ready", detail=run.detail, evidence=run.to_dict())
    return passed(
        "unity_auto_win_ready",
        detail="bounded cold-start no-input horizon completed without success",
        evidence=run.to_dict(),
    )


def _unity_route_item(suite: UnityRuntimeSuite) -> Item:
    runs = (suite.witness, *suite.hidden_routes)
    failed_runs = [run for run in runs if run.status == "fail"]
    if failed_runs:
        return failed(
            "legacy_reference_trace",
            detail="candidate runtime failed during: " + ", ".join(run.run_id for run in failed_runs),
            evidence=suite.to_dict(),
        )
    inconclusive_runs = [run for run in runs if run.status == "inconclusive"]
    if inconclusive_runs:
        return inconclusive(
            "legacy_reference_trace",
            detail="evaluator could not complete: " + ", ".join(run.run_id for run in inconclusive_runs),
            evidence=suite.to_dict(),
        )
    if not suite.hidden_routes:
        return inconclusive(
            "legacy_reference_trace",
            detail="task package has no evaluator-hidden positive routes",
            evidence=suite.to_dict(),
        )
    missed = [run.run_id for run in runs if not run.won]
    if missed:
        return failed(
            "legacy_reference_trace",
            detail="Unity port did not reach the expected next/success scene on: " + ", ".join(missed),
            evidence=suite.to_dict(),
        )
    return passed(
        "legacy_reference_trace",
        detail=(
            f"diagnostic only: submitted witness plus {len(suite.hidden_routes)} "
            "legacy exact route(s) reached their evaluator-owned targets"
        ),
        evidence=suite.to_dict(),
    )


def _unity_reading(run: UnityProbeRun) -> dict[str, Any]:
    # `won` is the only win signal. The final scene is evidence, not a stop
    # reason: a scene named `GoalRoom` must not read as a win.
    return {
        "reached": run.won,
        "stop_reason": "goal_reached" if run.won else "horizon",
        "final_scene": str(run.reading.get("final_scene") or ""),
        "frames": run.reading.get("frames"),
        "scenes_visited": run.reading.get("scenes_visited") or [],
        "probe": run.to_dict(),
    }


def _reference_video(pkg: TaskPackage) -> Path | None:
    video_dir = pkg.visible / "video"
    if not video_dir.is_dir():
        return None
    videos = sorted(video_dir.rglob("*.mp4"))
    return videos[0] if videos else None


def _visual_task_context(pkg: TaskPackage, oracle: dict[str, Any]) -> str:
    parts: list[str] = []
    gdd = pkg.visible / "GDD.md"
    if gdd.is_file():
        parts.append(gdd.read_text(encoding="utf-8", errors="replace")[:10000])
    claims = [
        str(item.get("claim") or "")
        for item in (oracle.get("rubric") or {}).get("mechanic_checks") or []
        if isinstance(item, dict) and item.get("claim")
    ]
    if claims:
        parts.append("Observable task requirements:\n- " + "\n- ".join(claims))
    return "\n\n".join(parts)


def _unity_static_items(report: UnityInterfaceReport) -> list[Item]:
    layout = report.by_id("static/project_layout")
    layout_item = (passed if layout.status == "pass" else failed)(
        "unity_layout",
        detail=layout.detail,
        evidence=layout.to_dict(),
    )
    remaining = [
        item for item in report.static_diagnostics
        if item.id != "static/project_layout"
    ]
    failures = [item for item in remaining if item.status == "fail"]
    if failures:
        interface_item = failed(
            "unity_interface",
            detail="; ".join(f"{item.id}: {item.detail}" for item in failures),
            evidence=report.to_dict(),
        )
    else:
        interface_item = passed(
            "unity_interface",
            detail="Unity GB manifest and all declared static addresses are valid",
            evidence=report.to_dict(),
        )
    return [layout_item, interface_item]


def _port_contract_alignment_item(
    report: UnityInterfaceReport,
    oracle: dict[str, Any],
) -> Item:
    manifest = report.manifest
    if manifest is None:
        return skipped(
            "port_contract_alignment",
            detail="Unity interface failed, so task-specific port duties cannot be checked",
        )
    rubric = oracle.get("rubric") or {}
    input_contract = oracle.get("port_input_contract") or {}
    audit = oracle.get("rubric_audit") or {}
    if not rubric or not audit.get("ready"):
        return inconclusive(
            "port_contract_alignment",
            detail="package has no audited hidden rubric for the cross-engine port",
            evidence={"rubric_audit": audit},
        )

    problems: list[str] = []
    min_levels = int(rubric.get("min_levels") or 1)
    if len(manifest.levels) < min_levels:
        problems.append(
            f"declares {len(manifest.levels)} level(s), task requires at least {min_levels}"
        )
    action_names = set(manifest.actions)
    for action in rubric.get("required_actions") or []:
        if action not in action_names:
            problems.append(f"required action {action} is not mapped")
    declared_roles = manifest.declared_roles
    required_roles = {str(role) for role in rubric.get("required_groups") or []}
    for role in sorted(required_roles):
        if role not in declared_roles:
            problems.append(f"required semantic role {role} is not declared")
    declared_numeric = manifest.declared_numeric_slots
    required_numeric = {
        str(slot) for slot in rubric.get("required_numeric_slots") or []
    }
    for slot in sorted(required_numeric):
        if slot not in declared_numeric:
            problems.append(f"required numeric slot {slot} is not declared")

    manifest_extras = set(manifest.extended_actions)
    required_extras = set(rubric.get("required_extended_actions") or [])
    for action in sorted(required_extras - manifest_extras):
        problems.append(f"required extended action {action} is not mapped")
    for action in sorted(manifest_extras - required_extras):
        problems.append(f"undeclared extra action {action} is mapped")
    required_axes = set(str(item) for item in rubric.get("required_analog_axes") or [])
    manifest_axes = set(item.id for item in manifest.analog_axes)
    for axis in sorted(required_axes - manifest_axes):
        problems.append(f"required analog axis {axis} is not mapped")
    for axis in sorted(manifest_axes - required_axes):
        problems.append(f"undeclared analog axis {axis} is mapped")
    expected_supported = tuple(input_contract.get("supported_actions") or ())
    if expected_supported and tuple(manifest.supported_actions) != expected_supported:
        problems.append("supported_actions differs from the compiler-frozen input table")
    expected_axes = list(input_contract.get("analog_axes") or [])
    if expected_axes and [item.to_dict() for item in manifest.analog_axes] != expected_axes:
        problems.append("analog axis range/grid differs from the compiler-frozen input table")

    if problems:
        return failed(
            "port_contract_alignment",
            detail="; ".join(problems[:16]),
            evidence={
                "problems": problems,
                "rubric_audit": audit,
                "unscored_declared": {
                    "object_roles": sorted(declared_roles - required_roles),
                    "numeric_slots": sorted(declared_numeric - required_numeric),
                    "manifest_fields": report.by_id("static/schema").evidence.get(
                        "ignored_fields", []
                    ),
                },
            },
        )
    return passed(
        "port_contract_alignment",
        detail="Unity manifest covers the audited task-specific levels and observables",
        evidence={
            "rubric_audit": audit,
            "unscored_declared": {
                "object_roles": sorted(declared_roles - required_roles),
                "numeric_slots": sorted(declared_numeric - required_numeric),
                "manifest_fields": report.by_id("static/schema").evidence.get(
                    "ignored_fields", []
                ),
            },
        },
    )


def _build_recipe_item(sub: Submission) -> Item:
    if sub.build_path is None:
        return failed("build_recipe", detail="BUILD.md is missing")
    text = sub.build_path.read_text(encoding="utf-8", errors="replace").strip()
    if len(text) < 20:
        return failed(
            "build_recipe",
            detail="BUILD.md is empty or too short to describe a Linux build",
        )
    missing: list[str] = []
    if re.search(r"\b(?:20\d{2}|6000)\.\d+\.\d+[abfp]\d+\b", text) is None:
        missing.append("exact Unity editor version")
    lowered = text.lower()
    if "-batchmode" not in lowered:
        missing.append("Unity -batchmode command")
    if "linux" not in lowered:
        missing.append("Linux build target")
    output_words = ("output", "artifact", "player")
    output_path = re.search(
        r"(?:^|\s|`)(?:\./|/|[A-Za-z0-9_.-]+/)[^\s`]+", text
    )
    if not any(word in lowered for word in output_words) or output_path is None:
        missing.append("build output path")
    if missing:
        return failed(
            "build_recipe",
            detail="BUILD.md omits: " + ", ".join(missing),
            evidence={"path": str(sub.build_path), "missing": missing},
        )
    return passed(
        "build_recipe",
        detail=(
            f"BUILD.md pins an editor, Linux batch command, and output path "
            f"({len(text)} chars); execution remains evaluator-owned"
        ),
        evidence={"path": str(sub.build_path)},
    )


def _unity_anti_grant_static_item(sub: Submission) -> Item:
    try:
        report = scan_unity_antigrant(sub.project)
    except OSError as exc:
        return inconclusive(
            "unity_anti_grant_static",
            detail=f"static anti-grant scan could not read the Unity project: {exc}",
        )
    evidence = report.to_dict()
    blocking = [finding for finding in report.findings if finding.blocking]
    if blocking:
        return failed(
            "unity_anti_grant_static",
            detail=(
                f"{len(blocking)} high-confidence anti-grant finding(s); "
                "Unity import/build is refused"
            ),
            evidence=evidence,
        )
    informational = len(report.findings)
    return passed(
        "unity_anti_grant_static",
        detail=(
            "no high-confidence static grant found"
            + (f"; retained {informational} ambiguous finding(s) for dynamic verification"
               if informational else "")
        ),
        evidence=evidence,
    )


def _unity_sdk_integrity_item(pkg: TaskPackage, sub: Submission) -> Item:
    manifest_path = pkg.hidden / "unity" / "scaffold_integrity.json"
    if not manifest_path.is_file():
        return inconclusive(
            "unity_sdk_integrity",
            detail="package has no evaluator-owned hidden scaffold digest manifest",
        )
    try:
        payload = json.loads(manifest_path.read_text(encoding="utf-8"))
        expected = payload.get("files") if isinstance(payload, dict) else None
        if not isinstance(expected, dict) or not expected:
            raise ValueError("files must be a non-empty object")
        community = payload.get("profile_id") == "mode5-community-docker-v1"
        report = validate_scaffold_integrity(
            sub.project, expected,
            reference_project=pkg.visible / "target_unity" if community else None,
        )
    except (OSError, ValueError, json.JSONDecodeError) as exc:
        return inconclusive(
            "unity_sdk_integrity",
            detail=f"evaluator-owned scaffold manifest is invalid: {exc}",
        )
    evidence = {"manifest": str(manifest_path), **report.to_dict()}
    if not report.ok:
        return failed(
            "unity_sdk_integrity",
            detail="protected Unity SDK or required scaffold configuration was changed; Unity import/build is refused",
            evidence=evidence,
        )
    return passed(
        "unity_sdk_integrity",
        detail="protected Unity SDK and required scaffold configuration are valid",
        evidence=evidence,
    )


def _port_no_smuggling_item(sub: Submission) -> Item:
    """Reject evaluator artefacts while allowing ordinary in-game route data."""
    blocked: list[str] = []
    for value in sub.smuggled:
        rel = Path(value)
        name = rel.name.lower()
        if name != "route.json":
            blocked.append(value)
            continue
        parents = {part.lower() for part in rel.parts[:-1]}
        if len(rel.parts) == 1 or parents & {"eval", "evaluator", "hidden", "gold"}:
            blocked.append(value)
    if blocked:
        return failed(
            "no_eval_smuggling",
            detail="submission contains evaluator-owned artefacts: " + ", ".join(blocked[:12]),
            evidence={"paths": blocked},
        )
    return passed(
        "no_eval_smuggling",
        detail="no evaluator route, certificate, expectation, or registry artefact is shipped",
    )


def _no_bundled_godot_runtime_item(sub: Submission) -> Item:
    """A Mode-5 result must be a Unity port, not a Unity launcher for Godot."""
    bundled: list[str] = []
    for path in sub.project.rglob("*"):
        if not path.is_file():
            continue
        name = path.name.lower()
        if path.suffix.lower() == ".pck" or name in {"godot", "godot.exe"}:
            bundled.append(str(path.relative_to(sub.project)).replace("\\", "/"))
    if bundled:
        return failed(
            "no_bundled_godot_runtime",
            detail="Unity submission bundles a Godot player/package: " + ", ".join(bundled[:12]),
            evidence={"paths": bundled},
        )
    return passed(
        "no_bundled_godot_runtime",
        detail="no bundled Godot player or PCK was found in the Unity project",
    )


def _transformation_contract_item(
    mode: Mode,
    pkg: TaskPackage,
    sub: Submission,
    items: list[Item],
) -> Item:
    """Resolve static restoration duties and existing runtime verdicts together."""
    path = pkg.hidden / "transformation.json"
    if not path.is_file():
        return inconclusive(
            "transformation_contract",
            detail="package has no evaluator-owned transformation manifest",
        )
    try:
        manifest = TransformationManifest.read(path)
    except (OSError, ValueError, json.JSONDecodeError) as exc:
        return inconclusive(
            "transformation_contract",
            detail=f"transformation manifest is unreadable: {exc}",
        )

    runtime: dict[str, Any] = {
        item.id: {"status": item.verdict.value, "detail": item.detail}
        for item in items
        if item.id in {"causal_witness", "mechanic_trace", "repair_differential", "gold_replay"}
    }
    mechanic = next((item for item in items if item.id == "mechanic_trace"), None)
    if mechanic is not None:
        reached = {str(value) for value in mechanic.evidence.get("reached") or []}
        expected = {str(value) for value in mechanic.evidence.get("expected") or []}
        for check_id in expected:
            if mechanic.verdict in {Verdict.INCONCLUSIVE, Verdict.UNMEASURABLE, Verdict.SKIPPED}:
                status = "unverified"
            else:
                status = "pass" if check_id in reached else "fail"
            runtime[check_id] = {
                "status": status,
                "detail": f"checkpoint {'reached' if check_id in reached else 'not reached'}",
            }

    if mode.id == "skeleton":
        audit = audit_mode3_submission(sub.project, manifest, runtime=runtime)
    else:
        audit = audit_mode4_submission(sub.project, manifest, runtime=runtime)
    evidence = audit.to_dict()
    if audit.status == "pass":
        return passed(
            "transformation_contract",
            detail="all packaging, restoration, and runtime transformation duties passed",
            evidence=evidence,
        )
    if audit.status == "fail":
        failed_ids = [
            item.id for item in audit.diagnostics if item.status == "fail"
        ]
        return failed(
            "transformation_contract",
            detail="transformation duties failed: " + ", ".join(failed_ids[:12]),
            evidence=evidence,
        )
    return inconclusive(
        "transformation_contract",
        detail="transformation duties are not fully measured at runtime",
        evidence=evidence,
    )


def _verifier_profile_item(
    mode: Mode, items: list[Item], *, brief_design: bool = True,
) -> Item:
    """Fail the evaluator closed when a mode-specific verifier stage vanished."""
    profile = verifier_profile(mode, brief_design=brief_design)
    observed = {item.id for item in items}
    missing = missing_strict_items(mode, observed, brief_design=brief_design)
    if missing:
        return inconclusive(
            "verifier_profile_complete",
            detail=(
                f"{mode.id} evaluator omitted required checks: " + ", ".join(missing)
            ),
            evidence={"profile": profile.to_dict(), "observed": sorted(observed)},
        )
    return passed(
        "verifier_profile_complete",
        detail=(
            f"{mode.id} emitted all {len(profile.strict_ids)} strict profile checks"
        ),
        evidence={"profile": profile.to_dict()},
    )


def _oracle(pkg: TaskPackage) -> dict[str, Any]:
    path = pkg.hidden / "oracle.json"
    if not path.is_file():
        return {}
    raw = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(raw, dict):
        return {}
    return refresh_rubric_observables(raw, str(pkg.manifest.get("game_id") or ""))


def refresh_rubric_observables(
    oracle: dict[str, Any],
    game_id: str,
    catalog: dict[str, dict[str, Any]] | None = None,
) -> dict[str, Any]:
    """Re-read each checkpoint's *measurement* from the current rubric catalog.

    The frozen oracle records what was generated. A checkpoint's `observable`
    (predicate, baseline) is the evaluator's hidden measurement method, not
    part of the contract disclosed to the agent, so a corrected method applies
    to a re-evaluation from raw artifacts. Claims, ids, required groups, slots
    and levels are the disclosed contract and are left exactly as frozen; a
    check that no longer exists in the catalog, or a game the catalog does not
    know, keeps its frozen observable. The report carries what was swapped.
    """
    rubric = oracle.get("rubric")
    if not isinstance(rubric, dict) or not game_id:
        return oracle
    if catalog is None:
        try:
            from .content.rubrics import load_rubric_catalog

            catalog = load_rubric_catalog()
        except Exception:
            return oracle
    current = catalog.get(game_id)
    if not isinstance(current, dict):
        return oracle
    by_id = {
        str(check.get("id")): check.get("observable")
        for check in current.get("mechanic_checks") or []
        if isinstance(check, dict) and isinstance(check.get("observable"), dict)
    }
    refreshed: list[str] = []
    checks: list[Any] = []
    for check in rubric.get("mechanic_checks") or []:
        if not isinstance(check, dict):
            checks.append(check)
            continue
        observable = by_id.get(str(check.get("id")))
        if observable is None or observable == check.get("observable"):
            checks.append(check)
            continue
        checks.append({**check, "observable": dict(observable)})
        refreshed.append(str(check.get("id")))
    if not refreshed:
        return oracle
    out = dict(oracle)
    out["rubric"] = {**rubric, "mechanic_checks": checks}
    out["rubric_frozen"] = rubric
    out["rubric_observables_refreshed"] = refreshed
    return out


def _want_engine(engine: str) -> bool:
    flag = (engine or "auto").strip().lower()
    if flag in {"off", "never", "no", "0", "false"}:
        return False
    if flag in {"on", "always", "yes", "1", "true"}:
        return True
    return godot_available() is not None


def _layout_item(sub: Submission) -> Item:
    ok = (sub.project / "project.godot").is_file()
    return (passed if ok else failed)(
        "layout",
        detail="project.godot resolved" if ok else "no engine project in the submission",
        evidence={"project": str(sub.project)},
    )


def _gdd_item(sub: Submission) -> Item:
    if sub.gdd_path is None:
        return failed("gdd", detail="mode brief requires GDD.md in the submission")
    text = sub.gdd_path.read_text(encoding="utf-8", errors="replace").strip()
    if len(text) < 80:
        return failed("gdd", detail="GDD.md is empty or too short to be a design document")
    return passed("gdd", detail=f"GDD.md present ({len(text)} chars)")


def _authored_gdd_quality_item(sub: Submission) -> Item:
    if sub.gdd_path is None:
        return skipped("authored_gdd_quality", detail="GDD.md is missing")
    report = audit_authored_gdd(sub.gdd_path)
    if not report["ready"]:
        return failed(
            "authored_gdd_quality",
            detail="; ".join(report["errors"]),
            evidence=report,
        )
    return passed(
        "authored_gdd_quality",
        detail="self-contained design, failure/recovery, progression, and acceptance present",
        evidence=report,
    )


def _authored_gdd_interface_item(sub: Submission) -> Item:
    if sub.gdd_path is None:
        return skipped("authored_gdd_interface", detail="GDD.md is missing")
    declared = declared_interface_contract(sub.gdd_path)
    problems: list[str] = []
    if declared["vague_declarations"]:
        problems.append("extended/analog controls are mentioned without stable ids")
    if not declared["canonical_actions"]:
        problems.append("GDD declares no canonical gb_* control ids")
    interface = load_submission_interface(sub.project)
    bound = set(interface.actions.bound)
    unread = set(interface.actions.unread)
    for action in declared["canonical_actions"]:
        if action not in bound:
            problems.append(f"GDD action {action} is unbound")
        elif action in unread:
            problems.append(f"GDD action {action} is bound but unread")
    extras = set(interface.extended_action_ids)
    for action in declared["extended_actions"]:
        if action not in extras:
            problems.append(f"GDD extended action {action} is not declared by the game")
    axes = set(interface.analog_axis_ids)
    for axis in declared["analog_axes"]:
        if axis not in axes:
            problems.append(f"GDD analog axis {axis} is not declared by the game")
    if problems:
        return failed(
            "authored_gdd_interface",
            detail="; ".join(problems[:12]),
            evidence={"declared": declared, "problems": problems},
        )
    return passed(
        "authored_gdd_interface",
        detail="GDD control declarations agree with the submitted GB interface",
        evidence={"declared": declared},
    )


def _task_gdd_contract_item(oracle: dict[str, Any]) -> Item:
    report = oracle.get("task_gdd_audit") or {}
    if not report:
        return inconclusive("task_gdd_contract", detail="package has no task GDD audit")
    if not report.get("ready"):
        return failed(
            "task_gdd_contract",
            detail="published task GDD failed its input audit",
            evidence=report,
        )
    return passed(
        "task_gdd_contract",
        detail="evaluator-authored GDD is self-contained and implementation-neutral",
        evidence=report,
    )


def _brief_gdd_grounding_item(sub: Submission, oracle: dict[str, Any]) -> Item:
    """Require the authored Mode-1 GDD to retain the hidden public control contract."""
    if sub.gdd_path is None:
        return skipped("brief_gdd_grounding", detail="GDD.md is missing")
    rubric = oracle.get("rubric") or {}
    audit = oracle.get("rubric_audit") or {}
    if not rubric or not audit.get("ready"):
        return inconclusive(
            "brief_gdd_grounding",
            detail="package has no audited reference-conditioned rubric",
        )
    declared = declared_interface_contract(sub.gdd_path)
    missing: list[str] = []
    for key, rubric_key in (
        ("canonical_actions", "required_actions"),
        ("analog_axes", "required_analog_axes"),
    ):
        absent = sorted(set(rubric.get(rubric_key) or ()) - set(declared.get(key) or ()))
        missing.extend(f"{key}:{value}" for value in absent)
    # The brief never names extended-action ids, so the authored GDD is held to
    # the count the statement published rather than to the reference game's ids.
    count_finding = extended_action_count_finding(
        len(declared.get("extended_actions") or ()), rubric
    )
    if count_finding:
        missing.append(f"extended_actions: GDD {count_finding}")
    if missing:
        return failed(
            "brief_gdd_grounding",
            detail="authored GDD omits reference-conditioned controls: "
            + ", ".join(missing[:12]),
            evidence={"declared": declared, "missing": missing},
        )
    return passed(
        "brief_gdd_grounding",
        detail="authored GDD retains every reference-conditioned control obligation",
        evidence={"declared": declared},
    )


def _rubric_interface_item(
    sub: Submission, oracle: dict[str, Any], *, mode: str = ""
) -> Item:
    rubric = oracle.get("rubric") or {}
    audit = oracle.get("rubric_audit") or {}
    if not rubric or not audit:
        return inconclusive(
            "rubric_interface", detail="package has no audited hidden rubric"
        )
    if not audit.get("ready"):
        return inconclusive(
            "rubric_interface",
            detail="evaluator-owned rubric failed publication audit",
            evidence=audit,
        )
    interface = load_submission_interface(sub.project)
    findings = interface_rubric_findings(interface, rubric, mode=mode)
    required_groups = [str(value) for value in rubric.get("required_groups") or []]
    missing_required_groups = [
        group for group in required_groups
        if any(f"required group {group} is absent" in str(finding) for finding in findings)
    ]
    extended_evidence = {
        "declared": list(interface.extended_action_ids),
        "required_count": int(rubric.get("required_extended_action_count") or 0),
        "required_ids": list(rubric.get("required_extended_actions") or [])
        if mode != "brief" else [],
    }
    if findings:
        return failed(
            "rubric_interface",
            detail="; ".join(findings[:12]),
            evidence={
                "findings": findings,
                "required_groups": required_groups,
                "missing_required_groups": missing_required_groups,
                "rubric_audit": audit,
                "extended_actions": extended_evidence,
            },
        )
    return passed(
        "rubric_interface",
        detail=(
            "submission exposes the levels, controls, roles, counters, and endings "
            "required by the implementation-neutral rubric"
        ),
        evidence={
            "rubric_audit": audit,
            "extended_actions": extended_evidence,
            "required_groups": required_groups,
            "missing_required_groups": [],
        },
    )


def _ops_items(sub: Submission) -> list[Item]:
    items: list[Item] = []
    if sub.demos_path is not None:
        items.append(passed("ops_present", detail=f"feature demonstration protocol: {sub.demos_path}"))
        if sub.demos_error:
            items.append(failed("ops_valid", detail=sub.demos_error))
            items.append(skipped("ops_not_idle", detail="demonstrations were refused"))
        else:
            items.append(passed("ops_valid", detail=f"{len(sub.demos)} independent input-only demonstrations accepted"))
            items.append(passed("ops_not_idle", detail="each demonstration contains player input"))
        return items
    if sub.ops_path is None:
        items.append(failed("ops_present", detail="ops.json is missing"))
        items.append(skipped("ops_valid", detail="no ops to validate"))
        items.append(skipped("ops_not_idle", detail="no ops to inspect"))
        return items
    items.append(passed("ops_present", detail=str(sub.ops_path)))
    if sub.ops_error:
        items.append(failed("ops_valid", detail=sub.ops_error))
        items.append(skipped("ops_not_idle", detail="ops were refused"))
        return items
    items.append(passed("ops_valid", detail=f"{len(sub.ops)} ops accepted"))
    if ops_have_player_action(sub.ops):
        items.append(passed("ops_not_idle", detail="tape contains a canonical action"))
    else:
        items.append(
            failed(
                "ops_not_idle",
                detail="ops are only wait/noop; that is how a timed auto-clear "
                "disguises itself as a playthrough",
            )
        )
    return items


def _interface_item(sub: Submission) -> Item:
    interface = load_submission_interface(sub.project)
    if interface.report.ok:
        return passed("interface", detail="Interface v2 conformance PASS")
    return failed(
        "interface",
        detail="conformance FAIL: " + ", ".join(interface.report.missing),
        evidence=interface.report.to_dict(),
    )


def _anti_grant_item(sub: Submission, mode: Mode, oracle: dict[str, Any]) -> Item:
    report = scan_tree(sub.project)
    # Modes 1–3: only blocking harness_flags. auto_win_ready is a separate
    # evaluate item so a _ready victory jump is not counted twice.
    flags = [
        f
        for f in report.findings
        if f.kind == "harness_flag" and is_blocking_finding(f)
    ]
    concessions = [
        f
        for f in report.findings
        if f.kind == "harness_flag" and f.classification == "concession"
    ]
    if mode.id == "bugfix":
        new_flags = new_blocking_sites(report, oracle.get("grant_baseline") or {})
        inherited = sum(1 for f in report.findings if is_blocking_finding(f)) - len(new_flags)
        if new_flags:
            loci = _finding_loci(new_flags)
            where = f" at {loci}" if loci else ""
            return failed(
                "anti_grant_static",
                detail=(
                    f"{len(new_flags)} new blocking grant site(s){where} vs the "
                    "shipped bugged copy; adding a self-grant is not a fix"
                ),
                evidence={"findings": [f.to_dict() for f in new_flags[:20]]},
            )
        return passed(
            "anti_grant_static",
            detail=(
                f"no new blocking grant sites ({inherited} inherited from the "
                "corpus copy via harness_scan, which is a known debt and not "
                f"this task; {len(concessions)} concession(s) ignored)"
            ),
        )
    if not flags:
        extra = (
            f"; {len(concessions)} concession(s) acknowledged"
            if concessions
            else ""
        )
        return passed(
            "anti_grant_static",
            detail=(
                f"no blocking harness-flag on the load graph "
                f"({report.files_scanned} files, authenticity="
                f"{report.authenticity or 'no_harness_flag_in_gameplay'})"
                f"{extra}"
            ),
        )
    loci = _finding_loci(flags)
    where = f" at {loci}" if loci else ""
    return failed(
        "anti_grant_static",
        detail=(
            f"{len(flags)} blocking harness-flag site(s) on the load graph{where}; "
            "a new submission must not test --gb-route-plan / --gb-route-out "
            "as a grant"
        ),
        evidence={"findings": [f.to_dict() for f in flags[:20]], "shared": report.shared},
    )


def _auto_win_item(sub: Submission) -> Item:
    ready = scan_auto_win_ready(sub.project)
    if ready:
        return failed(
            "auto_win_ready",
            detail="_ready jumps to a declared victory ending before input",
            evidence={"findings": [f.to_dict() for f in ready]},
        )
    return passed("auto_win_ready", detail="no declared-victory change in _ready")


def _health_item(sub: Submission) -> Item:
    interface = load_submission_interface(sub.project)
    numeric = dict(interface.numeric or {})
    if "health" not in numeric:
        return unobservable(
            "health_writable",
            detail="gb_levels.json does not declare numeric.health",
        )
    declared = declared_health_property(numeric.get("health"))
    binding = health_write_binding(
        sub.project,
        numeric.get("health"),
        levels=[level.scene for level in interface.levels],
    )
    hits = list(binding["hits"])
    evidence = {
        "hits": [h.to_dict() for h in hits[:8]],
        "declared_property": declared,
        "declared_owner": binding["owner_hint"],
        "scanned_names": binding["scanned_names"],
        "owner_scripts": binding["owner_scripts"][:12],
        "binding_counts": {
            key: binding[key] for key in ("owner", "unattributed", "other_object", "mirror")
        },
        "mirrors": list(binding.get("mirrors", []))[:8],
    }
    if binding["writable"]:
        accepted = [h for h in hits if h.classification in {"owner", "unattributed"}]
        via_mirror = (
            f", {binding['mirror']} through a mirrored source the owner assigns from"
            if binding.get("mirror") else ""
        )
        return passed(
            "health_writable",
            detail=(
                f"{len(accepted)} decreasing write(s) to declared `{declared or 'health'}` "
                f"on its owner ({binding['owner']} owner, {binding['unattributed']} unattributed"
                f"{via_mirror})"
            ),
            evidence=evidence,
        )
    if hits:
        loci = ", ".join(f"{h.path}:{h.line}" for h in hits[:4])
        return failed(
            "health_writable",
            detail=(
                f"numeric.health declares `{declared or 'health'}` but the only decreasing "
                f"writes to it are on other objects ({loci}); writes to enemy/hazard fields "
                "do not make the player's health writable"
            ),
            evidence=evidence,
        )
    return failed(
        "health_writable",
        detail=(
            f"numeric.health declares `{declared or 'health'}` but no decreasing assignment "
            f"to that property was found (scanned for {declared or 'health'} -= / = ..-.. / "
            "literal-zero / subtraction operand, and for decreasing writes to a source the "
            f"owner assigns `{declared or 'health'}` from; other health-like names such as an "
            "enemy's `hp` do not count)"
        ),
        evidence=evidence,
    )


def _bugfix_items(sub: Submission, oracle: dict[str, Any]) -> list[Item]:
    items: list[Item] = []
    if sub.smuggled:
        items.append(
            failed(
                "no_eval_smuggling",
                detail="submission contains evaluator artefacts: " + ", ".join(sub.smuggled),
            )
        )
    else:
        items.append(passed("no_eval_smuggling", detail="no certificate/route/expectations planted"))

    surface = oracle.get("surface") or {}
    items.append(_bugfix_publication_item(oracle.get("bugfix_preflight") or {}))
    items.append(_surface_item(sub, surface))
    items.append(_feature_item(sub, oracle.get("mutations") or []))
    return items


def _bugfix_publication_item(preflight: dict[str, Any]) -> Item:
    if not preflight:
        return inconclusive(
            "bugfix_publication",
            detail="package has no source-pass -> mutant-fail publication preflight",
        )
    if not preflight.get("ready"):
        return inconclusive(
            "bugfix_publication",
            detail=str(preflight.get("reason") or "mutation was not publication-ready"),
            evidence=preflight,
        )
    return passed(
        "bugfix_publication",
        detail=(
            f"source passed {len(preflight.get('source_passed_routes') or [])} route(s); "
            f"the bootable mutant failed {len(preflight.get('mutant_failed_routes') or [])}"
        ),
        evidence=preflight,
    )


def _skeleton_items(sub: Submission, oracle: dict[str, Any]) -> list[Item]:
    contract = oracle.get("skeleton") or {}
    scripts = [str(item) for item in contract.get("scripts_stubbed") or []]
    scenes = [str(item) for item in contract.get("scene_paths") or []]
    resources = [str(item) for item in contract.get("resource_paths") or []]
    attachments = dict(contract.get("scene_script_attachments") or {})
    missing_scripts = [item for item in scripts if not (sub.project / item).is_file()]
    missing_scenes = [item for item in scenes if not (sub.project / item).is_file()]
    missing_resources = [item for item in resources if not (sub.project / item).is_file()]
    changed_attachments: dict[str, dict[str, list[str]]] = {}
    for relative, expected in attachments.items():
        scene = sub.project / relative
        if not scene.is_file():
            continue
        actual = set(scene_script_attachments(scene))
        missing = sorted(set(str(item) for item in expected) - actual)
        if missing:
            changed_attachments[str(relative)] = {
                "missing": missing,
                "actual": sorted(actual),
            }
    integration_total = len(scripts) + len(scenes) + len(resources) + len(attachments)
    integration_missing = (
        len(missing_scripts) + len(missing_scenes) + len(missing_resources)
        + len(changed_attachments)
    )
    integration_credit = (
        max(0.0, (integration_total - integration_missing) / integration_total)
        if integration_total else 0.0
    )
    integration_evidence = {
        "total": integration_total,
        "preserved": max(0, integration_total - integration_missing),
        "missing_scripts": missing_scripts[:50],
        "missing_scenes": missing_scenes[:50],
        "missing_resources": missing_resources[:50],
        "changed_attachments": changed_attachments,
    }
    if missing_scripts or missing_scenes or missing_resources or changed_attachments:
        integrity = failed(
            "skeleton_integrity",
            credit=integration_credit,
            detail=(
                f"missing {len(missing_scripts)} scaffold scripts and "
                f"{len(missing_scenes)} scaffold scenes, "
                f"{len(missing_resources)} resources; "
                f"changed attachments in {len(changed_attachments)} scenes"
            ),
            evidence=integration_evidence,
        )
    else:
        integrity = passed(
            "skeleton_integrity",
            detail=(
                f"preserved {len(scripts)} script plug points, {len(scenes)} scenes, "
                f"{len(resources)} resources, and {len(attachments)} attachment sets"
            ),
            evidence=integration_evidence,
        )
    unfinished: list[str] = []
    for relative in scripts:
        path = sub.project / relative
        if path.is_file() and STUB_MARKER in path.read_text(encoding="utf-8", errors="replace"):
            unfinished.append(relative)
    if unfinished:
        completion = failed(
            "stub_completion",
            credit=max(0.0, (len(scripts) - len(unfinished)) / len(scripts)) if scripts else 0.0,
            detail=f"{len(unfinished)} supplied script stubs still carry the task marker",
            evidence={"total": len(scripts), "unfinished": unfinished[:50]},
        )
    elif not scripts:
        completion = inconclusive(
            "stub_completion", detail="package did not record supplied script stubs"
        )
    else:
        completion = passed(
            "stub_completion",
            detail=f"all {len(scripts)} supplied script markers were replaced",
            evidence={"total": len(scripts), "unfinished": []},
        )
    return [integrity, completion]


def _surface_item(sub: Submission, surface: dict[str, Any]) -> Item:
    if not surface:
        return inconclusive("surface", detail="package hidden/oracle.json has no surface contract")
    from .generate import _surface_contract

    now = _surface_contract(sub.project)
    original_scripts = set(surface.get("scripts") or [])
    now_scripts = set(now.get("scripts") or [])
    original_n = int(surface.get("script_count") or 0)
    now_n = int(now.get("script_count") or 0)
    if original_n and now_n < original_n * SCRIPT_COLLAPSE_RATIO:
        return failed(
            "surface",
            detail=(
                f"script count collapsed {original_n} -> {now_n}; deleting the "
                "game is not a repair"
            ),
            evidence={"original": original_n, "now": now_n},
        )
    missing = sorted(original_scripts - now_scripts)
    # Allow new files; disallow dropping more than half of named scripts, already
    # covered by the count. Missing a handful of stubs is reported but the hard
    # fail is collapse or missing project.godot, which layout already caught.
    if missing and len(missing) > max(2, original_n // 5):
        return failed(
            "surface",
            detail=f"{len(missing)} original scripts disappeared",
            evidence={"missing": missing[:20]},
        )
    return passed(
        "surface",
        detail=f"script count {now_n} (was {original_n}); {len(missing)} scripts dropped",
    )


def _feature_item(sub: Submission, mutations: list[Any]) -> Item:
    if not mutations:
        return inconclusive("feature_kept", detail="oracle lists no mutations")
    interface = load_submission_interface(sub.project)
    bound = set(interface.actions.bound)
    unread = set(interface.actions.unread)
    present_groups = declared_groups(sub.project)
    problems: list[str] = []
    for raw in mutations:
        if not isinstance(raw, dict) or not raw.get("applied"):
            continue
        for action in raw.get("required_actions") or []:
            if action not in bound:
                problems.append(f"{raw.get('operator_id')}: required action {action} unbound")
            elif action in unread:
                problems.append(
                    f"{raw.get('operator_id')}: required action {action} bound but never read"
                )
        for group in raw.get("required_groups") or []:
            if group in REQUIRED_GROUPS and group not in present_groups:
                problems.append(f"{raw.get('operator_id')}: required group {group} missing")
        for group in raw.get("must_restore_groups") or []:
            if group not in present_groups:
                problems.append(
                    f"{raw.get('operator_id')}: {group} is still missing; "
                    "the fix must restore the group, not delete the mechanic"
                )
        for rel in raw.get("required_scripts") or []:
            if not (sub.project / rel).is_file():
                problems.append(f"{raw.get('operator_id')}: deleted {rel}")
        for rel in raw.get("required_files") or []:
            if not (sub.project / rel).is_file():
                problems.append(f"{raw.get('operator_id')}: deleted {rel}")
    if problems:
        return failed(
            "feature_kept",
            detail="repair deleted the broken feature: " + "; ".join(problems[:8]),
            evidence={"problems": problems},
        )
    return passed("feature_kept", detail="required actions/groups/scripts from mutations remain")


def _run_engine(
    mode: Mode,
    pkg: TaskPackage,
    sub: Submission,
    oracle: dict[str, Any],
    items: list[Item],
) -> dict[str, Any]:
    if godot_available() is None:
        items.extend(_engine_skipped(mode, "no Godot binary"))
        return {"ran": False, "reason": "no Godot binary"}
    interface = load_submission_interface(sub.project)
    payload: dict[str, Any] = {"ran": True}
    if mode.id == "bugfix":
        registered = oracle.get("registered_task") or {}
        frozen = str(registered.get("frozen_route") or "")
        routes = pkg.hidden / frozen if frozen else Path(str(registered.get("route") or ""))
        play = run_bugfix_gates(
            sub.project,
            routes,
            interface=interface,
            game_id=str(pkg.manifest.get("game_id") or ""),
            genuine_override={
                "present": True,
                "reason": "validated and frozen by Mode-4 publication preflight",
            }
            if (oracle.get("bugfix_preflight") or {}).get("ready")
            else None,
        )
        payload.update(play)
        if not play.get("ran"):
            items.extend(_engine_skipped(mode, str(play.get("reason") or "engine did not run")))
            return payload
        items.append(_null_item(play.get("null") or {}))
        items.append(_mash_item(play.get("extended_mash") or {}))
        items.append(_diff_item(play))
        items.append(_gold_item(play.get("gold") or {}))
        items.append(
            _repair_differential_item(
                oracle.get("bugfix_preflight") or {}, play.get("gold") or {}
            )
        )
        # Graded per-target-route restoration (fidelity item, outside `resolved`).
        # Reads the same honest replay as repair_differential so a partial fix
        # scores partially without weakening the strict gate.
        items.append(
            repair_restoration_item(
                oracle.get("bugfix_preflight") or {}, play.get("gold") or {}
            )
        )
        items.append(
            repair_restoration_graded_item(
                oracle.get("bugfix_preflight") or {}, play.get("gold") or {},
                json.loads(routes.read_text(encoding="utf-8")).get("routes", []),
            )
        )
        return payload

    if sub.demos and mode.id in {"brief", "gdd", "skeleton"}:
        play = run_demonstrations(sub.project, mode=mode, demos=sub.demos,
                                  interface=interface, rubric=oracle.get("rubric") or {})
        summary = summarize_demonstrations(play, oracle.get("rubric") or {})
        payload.update(play)
        payload["demonstration_summary"] = summary
        items.extend(demonstration_items(summary))
        # Controls are required for every segment, not merely a favourable one.
        for item_id, factory in (("null_no_win", lambda p: _null_item(p.get("null") or {})),
                                 ("extended_mash_no_win", lambda p: _mash_item(p.get("extended_mash") or {})),
                                 ("anti_grant_diff", _diff_item)):
            readings = [factory(row["play"]) for row in play["demonstrations"]]
            bad = next((r for r in readings if r.verdict is Verdict.FAILED), None)
            hole = next((r for r in readings if r.verdict not in {Verdict.PASSED, Verdict.UNOBSERVABLE}), None)
            item = bad or hole or readings[0]
            # Preserve each independently measured control in report evidence.
            item.evidence = {"segments": [r.to_dict() for r in readings]}
            if item_id == "anti_grant_diff" and any(row["flag_only_checks"] for row in summary["segments"]):
                item = failed(item_id, detail="some feature checks were observed only with harness flags",
                              evidence={"segments": summary["segments"]})
            items.append(item)
        if mode.id == "gdd":
            items.append(_gdd_mechanics_observable_item(sub, oracle, {
                "observations_reached": summary["observed"],
                "observations_triggered": summary["observed"],
            }))
        return payload

    play = run_self_play(
        sub.project,
        mode=mode,
        ops=sub.ops,
        predicate=clear_predicate(interface),
        interface=interface,
        milestones=rubric_milestones(oracle.get("rubric") or {})
        if mode.id in {"brief", "gdd", "skeleton"}
        else (),
    )
    payload.update(play)
    if oracle.get("rubric_observables_refreshed"):
        payload["rubric_observables_refreshed"] = list(oracle["rubric_observables_refreshed"])
        payload["rubric_checkpoints"] = [
            m.to_dict() for m in rubric_milestones(oracle.get("rubric") or {})
        ]
    if not play.get("ran"):
        items.extend(_engine_skipped(mode, str(play.get("reason") or "engine did not run")))
        return payload
    items.append(_null_item(play.get("null") or {}))
    items.append(_mash_item(play.get("extended_mash") or {}))
    if needs_ops(mode):
        if mode.id in {"brief", "gdd", "skeleton"}:
            items.append(
                _mechanic_trace_item(
                    play.get("ops_env") or {}, oracle.get("rubric") or {}
                )
            )
        if mode.id == "gdd":
            items.append(
                _gdd_mechanics_observable_item(sub, oracle, play.get("ops_env") or None)
            )
        items.append(
            _causal_witness_item(
                play.get("ops_env") or {},
                play.get("matched_null") or {},
            )
        )
        items.append(_diff_item(play))
    return payload


#: Items produced by the engine pass; replaced wholesale by a retry.
ENGINE_ITEM_IDS = frozenset({
    "demonstrations_complete",
    "null_no_win",
    "extended_mash_no_win",
    "anti_grant_diff",
    "causal_witness",
    "mechanic_trace",
    "gdd_mechanics_observable",
    "gold_replay",
    "repair_differential",
    "repair_restoration",
    "repair_restoration_graded",
})

#: Every evaluator-owned wall deadline is multiplied by this on the one retry.
RETRY_BUDGET_SCALE = 2.0

#: `run_self_play` / `run_bugfix_gates` reasons that no retry can change.
_NON_RETRYABLE_ENGINE_REASONS = ("no Godot binary",)


def evaluator_side_stops(payload: Mapping[str, Any], *, _depth: int = 0) -> list[str]:
    """Name every evaluator-side stop recorded in an engine payload.

    A reading whose `stop_reason` is in `routes.runner.HARNESS_STOPS` (timeout,
    launch_failed, driver_error, no_report, ...) was ended by the harness, not
    the game; an engine that did not run for a reason other than a missing
    Godot binary is the same kind of stop. The list is empty when every
    reading the engine attempted was taken.
    """
    from ..routes.runner import HARNESS_STOPS

    stops: list[str] = []
    if _depth == 0 and payload.get("ran") is False:
        reason = str(payload.get("reason") or "engine did not run")
        if not any(marker in reason for marker in _NON_RETRYABLE_ENGINE_REASONS):
            stops.append(f"engine: {reason}")
        return stops
    if _depth == 0:
        prepare = payload.get("prepare")
        if isinstance(prepare, dict) and prepare.get("ok") is False:
            stops.append(
                "prepare: " + (str(prepare.get("driver_error") or "scratch not prepared"))[:200]
            )
    for key, value in payload.items():
        if isinstance(value, dict):
            stop = str(value.get("stop_reason") or "")
            if stop in HARNESS_STOPS:
                stops.append(f"{key}: stop_reason={stop}")
            elif _depth < 2:
                stops.extend(
                    f"{key}/{inner}" for inner in evaluator_side_stops(value, _depth=_depth + 1)
                )
        elif isinstance(value, list) and _depth < 2:
            for index, entry in enumerate(value):
                if isinstance(entry, dict):
                    stops.extend(
                        f"{key}[{index}]/{inner}"
                        for inner in evaluator_side_stops(entry, _depth=_depth + 1)
                    )
    return stops


def _engine_skipped(mode: Mode, reason: str) -> list[Item]:
    # An engine that would not run is the evaluator failing to take a reading,
    # never the submission failing to earn one. Say so on the item: without it
    # every consumer downstream has to guess, and the scorecard was guessing
    # "candidate" by default.
    def skipped(item_id: str) -> Item:
        return inconclusive(item_id, detail=reason, attribution=Attribution.HARNESS)

    items = [
        skipped("null_no_win"),
        skipped("extended_mash_no_win"),
        skipped("anti_grant_diff"),
    ]
    if needs_ops(mode):
        items.append(skipped("causal_witness"))
        if mode.id in {"brief", "gdd", "skeleton"}:
            items.append(skipped("mechanic_trace"))
        if mode.id == "gdd":
            items.append(skipped("gdd_mechanics_observable"))
    else:
        items.append(skipped("gold_replay"))
        if mode.id == "bugfix":
            items.append(skipped("repair_differential"))
            items.append(skipped("repair_restoration"))
            items.append(skipped("repair_restoration_graded"))
    return items


def _repair_differential_item(preflight: dict[str, Any], repaired: dict[str, Any]) -> Item:
    if not preflight.get("ready"):
        return inconclusive(
            "repair_differential",
            detail="the evaluator did not establish source-pass -> mutant-fail before release",
            evidence=preflight,
        )
    if not repaired.get("ran"):
        return inconclusive(
            "repair_differential",
            detail=str(repaired.get("reason") or repaired.get("error") or "repair replay did not run"),
        )
    from .generate import _suite_status

    partition = preflight.get("partition") or {}
    target = {str(item) for item in partition.get("target_route_ids") or []}
    contract_target = {
        str(item) for item in partition.get("target_contract_checks") or []
    }
    regression = {str(item) for item in partition.get("regression_route_ids") or []}
    negative = {str(item) for item in partition.get("negative_control_route_ids") or []}
    source_cells = preflight.get("source") or {}
    tiers = {
        str(route_id): int((payload or {}).get("tier") or 0)
        for route_id, payload in source_cells.items()
    }
    cells = _suite_status({"gold": repaired}, tiers)
    infrastructure = sorted(
        route_id
        for route_id, payload in cells.items()
        if payload.get("status") in {"infrastructure_error", "not_run"}
    )
    if infrastructure:
        return inconclusive(
            "repair_differential",
            detail="repair suite had infrastructure gaps: " + ", ".join(infrastructure),
            evidence={"routes": cells},
        )
    target_failed = sorted(
        route_id for route_id in target if cells.get(route_id, {}).get("status") != "pass"
    )
    regression_failed = sorted(
        route_id
        for route_id in regression
        if cells.get(route_id, {}).get("status") != "pass"
    )
    negative_failed = sorted(
        route_id for route_id in negative if cells.get(route_id, {}).get("status") != "pass"
    )
    if target_failed or regression_failed or negative_failed:
        return failed(
            "repair_differential",
            detail=(
                f"repair differential failed: {len(target_failed)} target, "
                f"{len(regression_failed)} regression, and "
                f"{len(negative_failed)} negative-control route(s) not passing"
            ),
            evidence={
                "target_failed": target_failed,
                "regression_failed": regression_failed,
                "negative_control_failed": negative_failed,
                "routes": cells,
            },
        )
    return passed(
        "repair_differential",
        detail=(
            f"the repair restored {len(target)} mutation target route(s); "
            f"{len(contract_target)} public-contract target(s) passed feature_kept; preserved "
            f"{len(regression)} unrelated positive route(s), and kept "
            f"{len(negative)} negative control(s)"
        ),
        evidence={
            "target_routes": sorted(target),
            "target_contract_checks": sorted(contract_target),
            "regression_routes": sorted(regression),
            "negative_control_routes": sorted(negative),
        },
    )


def repair_restoration_item(preflight: dict[str, Any], repaired: dict[str, Any]) -> Item:
    """Graded per-target-route restoration credit for a Mode-4 repair.

    ``repair_differential`` is the strict, binary gate (every target restored
    AND every regression/negative-control preserved) and is unchanged; it, with
    ``gold_replay``, still decides ``resolved``.  This item reads the SAME honest
    repaired replay and reports the *fraction* of preregistered target routes the
    submission actually restored, so a partial fix scores partially.  It is a
    fidelity item (outside ``resolved``), never a gate.

    A repair that restores some targets but breaks a preserved regression or the
    negative control is not a partial success -- it changed behaviour elsewhere --
    so credit is 0 in that case (matching the strict fail), with the breakage in
    the evidence.  Infrastructure gaps or a replay that did not run are
    ``inconclusive``, exactly like the strict item.
    """
    if not preflight.get("ready"):
        return inconclusive(
            "repair_restoration",
            detail="the evaluator did not establish source-pass -> mutant-fail before release",
        )
    if not repaired.get("ran"):
        return inconclusive(
            "repair_restoration",
            detail=str(repaired.get("reason") or repaired.get("error") or "repair replay did not run"),
        )
    from .generate import _suite_status

    partition = preflight.get("partition") or {}
    target = sorted({str(item) for item in partition.get("target_route_ids") or []})
    regression = {str(item) for item in partition.get("regression_route_ids") or []}
    negative = {str(item) for item in partition.get("negative_control_route_ids") or []}
    source_cells = preflight.get("source") or {}
    tiers = {
        str(route_id): int((payload or {}).get("tier") or 0)
        for route_id, payload in source_cells.items()
    }
    cells = _suite_status({"gold": repaired}, tiers)

    def status_of(route_id: str) -> str:
        return str((cells.get(route_id) or {}).get("status") or "not_run")

    infrastructure = sorted(
        route_id for route_id in (target + sorted(regression) + sorted(negative))
        if status_of(route_id) in {"infrastructure_error", "not_run"}
    )
    if infrastructure:
        return inconclusive(
            "repair_restoration",
            detail="repair suite had infrastructure gaps: " + ", ".join(infrastructure),
            evidence={"routes": cells},
        )
    restored = [route_id for route_id in target if status_of(route_id) == "pass"]
    regression_failed = sorted(r for r in regression if status_of(r) != "pass")
    negative_failed = sorted(r for r in negative if status_of(r) != "pass")
    # The contract target (feature_kept) is scored by feature_kept and is not a
    # replay route; targets here are the frozen route targets.
    total = len(target)
    evidence = {
        "restored_targets": restored,
        "unrestored_targets": [r for r in target if r not in restored],
        "regression_failed": regression_failed,
        "negative_control_failed": negative_failed,
        "target_count": total,
        "routes": cells,
    }
    if regression_failed or negative_failed:
        return failed(
            "repair_restoration",
            detail=(
                "repair restored "
                f"{len(restored)}/{total} target route(s) but broke "
                f"{len(regression_failed)} regression and {len(negative_failed)} "
                "negative-control route(s); a repair that changes preserved behaviour "
                "earns no partial credit"
            ),
            evidence=evidence,
        )
    if total == 0:
        return inconclusive(
            "repair_restoration",
            detail="no preregistered route target to grade restoration against",
            evidence=evidence,
        )
    fraction = len(restored) / total
    detail = (
        f"repair restored {len(restored)}/{total} preregistered target route(s)"
        + (f"; still failing: {', '.join(evidence['unrestored_targets'])}"
           if evidence["unrestored_targets"] else "")
    )
    if fraction <= 0.0:
        return failed("repair_restoration", detail=detail, evidence=evidence)
    return passed("repair_restoration", credit=fraction, detail=detail, evidence=evidence)


def repair_restoration_graded_item(
    preflight: dict[str, Any], repaired: dict[str, Any],
    route_definitions: list[dict[str, Any]] | None = None,
) -> Item:
    """Mode34b fidelity: restored target assertions, independent of regressions.

    Definitions come from the evaluator's frozen route file, never submission
    declarations. Assertion grading requires source, mutant, and submission
    checks for every target route; otherwise the whole item uses route credit.
    Strict repair gates are unchanged.
    """
    from ..routes.schema import Milestone

    item_id = "repair_restoration_graded"
    legacy = repair_restoration_item(preflight, repaired)
    if legacy.verdict is Verdict.INCONCLUSIVE:
        return inconclusive(item_id, detail=legacy.detail, evidence=legacy.evidence)
    route_evidence = legacy.evidence
    restored_routes = route_evidence["restored_targets"]
    targets = sorted(restored_routes + route_evidence["unrestored_targets"])
    if not targets:
        return inconclusive(item_id, detail="no preregistered route target to grade restoration against")
    partition = preflight.get("partition") or {}
    preserved = set(partition.get("regression_route_ids") or []) | set(
        partition.get("negative_control_route_ids") or [])
    preserved_failed = sorted(set(route_evidence["regression_failed"]) | set(
        route_evidence["negative_control_failed"]))
    readings = {
        row["route_id"]: row.get("reading") or {}
        for row in repaired.get("readings") or []
        if row.get("baseline", "honest") == "honest"
    }
    definitions = {row["route_id"]: row for row in route_definitions or []}

    def outcomes(route_id: str, reading: dict[str, Any]) -> dict[str, bool] | None:
        if "reached" not in reading or route_id not in definitions:
            return None
        result = {"goal": bool(reading["reached"])}
        goal = definitions[route_id].get("goal") or {}
        for kind, key in (("milestones", "milestones_reached"),
                          ("invariants", "invariants_failed"),
                          ("observations", "observations_reached")):
            checks = goal.get(kind) or []
            if checks and key not in reading:
                return None
            for index, check in enumerate(checks):
                # Use the replay schema's names, including m1/m2 defaults for
                # the supported predicate-string shorthand in frozen files.
                name = Milestone.from_value(check, index).name
                present = name in (reading.get(key) or [])
                result[f"{kind}:{name}"] = not present if kind == "invariants" else present
        return result

    submitted = {r: outcomes(r, readings.get(r, {})) for r in targets}
    preflight_outcomes = {
        build: {r: outcomes(r, ((preflight.get(build) or {}).get(r) or {}).get("reading") or {})
                for r in targets}
        for build in ("source", "mutant")
    }
    assertion_level = all(
        value is not None
        for readings_by_route in (submitted, *preflight_outcomes.values())
        for value in readings_by_route.values()
    )
    restored: list[str] = []
    unrestored: list[str] = []
    if assertion_level:
        for route_id in targets:
            source = preflight_outcomes["source"][route_id]
            mutant = preflight_outcomes["mutant"][route_id]
            for check, ok in submitted[route_id].items():
                if not source[check] or mutant[check]:
                    continue
                (restored if ok else unrestored).append(f"{route_id}:{check}")
    total = len(restored) + len(unrestored) if assertion_level else None
    evidence = {
        "repair_granularity": "assertion" if assertion_level else "route",
        "target_assertions_total": total,
        "target_assertions_restored": len(restored) if assertion_level else None,
        "restored_assertions": restored,
        "unrestored_assertions": unrestored,
        "target_routes_total": len(targets),
        "target_routes_restored": len(restored_routes),
        "preserved_total": len(preserved),
        "preserved_passing": len(preserved) - len(preserved_failed),
        "preserved_failed": preserved_failed,
        "regression_factor": (len(preserved) - len(preserved_failed)) / len(preserved) if preserved else 1.0,
        "regression_free": not preserved_failed,
    }
    if assertion_level and not total:
        return inconclusive(item_id, detail="no source-pass / mutant-fail target assertions", evidence=evidence)
    credit = len(restored) / total if assertion_level else len(restored_routes) / len(targets)
    detail = (f"repair restored {len(restored)}/{total} target assertions" if assertion_level else
              f"repair restored {len(restored_routes)}/{len(targets)} target routes (route-only evidence)")
    if credit == 0:
        return failed(item_id, detail=detail, evidence=evidence)
    return passed(item_id, credit=credit, detail=detail, evidence=evidence)


def _mechanic_trace_item(reading: dict[str, Any], rubric: dict[str, Any]) -> Item:
    milestones = rubric_milestones(rubric)
    expected = [item.name for item in milestones]
    if not expected:
        return inconclusive(
            "mechanic_trace",
            detail="hidden rubric has no executable ordered checkpoints",
        )
    if not reading:
        # The engine ran (checked by the caller), so an absent reading means the
        # candidate's own tape produced nothing to observe. Redesign rule 1.2/3:
        # submission-caused missing evidence is a zero, not an evaluator gap.
        return inconclusive(
            "mechanic_trace",
            detail="no candidate trace reading",
            attribution=Attribution.SUBMISSION,
        )
    if reading_unmeasured(reading):
        return inconclusive("mechanic_trace", detail="candidate trace was not measured", evidence=reading)
    reached = [str(item) for item in reading.get("observations_reached") or []]
    triggered = {str(item) for item in reading.get("observations_triggered") or []}
    conditional = {item.name: item.trigger for item in milestones if item.trigger}
    optional = {
        str(check.get("id")) for check in rubric.get("mechanic_checks") or []
        if (check.get("observable") or {}).get("required_on_witness") is False
    }
    missing = [item for item in expected if item not in set(reached)]
    # A conditional checkpoint ("contact with a hazard reduces health") can
    # only be witnessed on a trace where its trigger happened. A whole-game
    # witness that never met the precondition leaves that checkpoint
    # untriggered: unobservable on this witness, not a missing mechanic. A
    # reading recorded before triggers existed carries no trigger census and
    # keeps the unconditional rule.
    untriggered = (
        [item for item in missing if item in conditional and item not in triggered]
        if "observations_triggered" in reading
        else []
    )
    missing = [item for item in missing if item not in set(untriggered)]
    observed = len(expected) - len(missing) - len(untriggered)
    optional_missing = [item for item in missing if item in optional]
    missing = [item for item in missing if item not in optional]
    evidence: dict[str, Any] = {
        "expected": expected,
        "reached": reached,
        "missing": missing,
        "untriggered": untriggered,
        "optional_missing": optional_missing,
        "triggers": conditional,
        "coverage": observed / len(expected),
        "segment_clean": reading.get("segment_clean") is not False,
    }
    if missing:
        return failed(
            "mechanic_trace",
            detail=(
                f"mechanic checkpoints observed {observed}/{len(expected)}"
                + (
                    f" ({len(untriggered)} untriggered on this witness)"
                    if untriggered else ""
                )
            ),
            evidence=evidence,
        )
    if reading.get("segment_clean") is False:
        return failed(
            "mechanic_trace",
            detail="checkpoints were observed on a trace with engine/script errors",
            evidence={**reading, **evidence},
        )
    if untriggered or optional_missing:
        return passed(
            "mechanic_trace",
            credit=observed / len(expected),
            detail=(
                f"{observed}/{len(expected)} implementation-neutral checkpoints observed; "
                f"{len(untriggered)} conditional checkpoint(s) untriggered on this witness; "
                f"{len(optional_missing)} optional checkpoint(s) not demonstrated "
                f"({', '.join(untriggered + optional_missing)})"
            ),
            evidence=evidence,
        )
    return passed(
        "mechanic_trace",
        detail=f"all {len(expected)} implementation-neutral checkpoints observed",
        evidence=evidence,
    )


def gdd_mechanics_observable_item(
    rubric: dict[str, Any],
    *,
    present_groups: Collection[str],
    numeric_values: Mapping[str, Any],
    trace_reading: Mapping[str, Any] | None,
) -> Item:
    """calib4 gdd-mode ``mode_specific``: the Task GDD's mechanics, observed.

    The Task GDD is the evaluator's document and every submission passed its
    audit (`task_gdd_contract`) at 100, so that criterion never separated two
    submissions.  This one is submission-side.  Everything the Task GDD names
    as a mechanic or observable -- the hidden rubric's executable
    ``mechanic_checks``, ``required_groups`` and ``required_numeric_slots`` --
    must be observable in the submission: a group present in its scenes, a
    declared numeric slot the route driver can resolve, or a trace checkpoint
    that fired on the submission's own witness.  Credit is the fraction
    observed; the item is graded, never a strict gate (it is a fidelity item,
    outside ``resolved``).

    A conditional checkpoint whose trigger never happened on the witness did
    not fire and is therefore not observed here (listed under ``untriggered``);
    `mechanic_trace` forgives it for the strict verdict, this fraction does not
    forgive it for the score, because a mechanic the tape never exercised was
    not shown to exist.  Mechanic checks the rubric marks unmeasurable or
    non-executable are outside the denominator and listed.
    """
    milestones = rubric_milestones(rubric)
    groups = [str(g) for g in rubric.get("required_groups") or []]
    slots = [str(s) for s in rubric.get("required_numeric_slots") or []]
    unmeasurable = [
        str(check.get("id") or "?")
        for check in rubric.get("mechanic_checks") or []
        if isinstance(check, dict) and str(check.get("id") or "") not in {m.name for m in milestones}
    ]
    if not milestones and not groups and not slots:
        return unobservable(
            "gdd_mechanics_observable",
            detail="the hidden rubric names no executable mechanics, groups or numeric slots",
            evidence={"unmeasurable_mechanics": unmeasurable},
        )
    if milestones and not trace_reading:
        # Same attribution as `mechanic_trace`: the engine ran, the candidate
        # tape did not produce a trace, so this is a submission-caused zero.
        return inconclusive(
            "gdd_mechanics_observable",
            detail="no candidate trace reading, so no checkpoint could be observed",
            attribution=Attribution.SUBMISSION,
        )
    reached = {str(x) for x in (trace_reading or {}).get("observations_reached") or []}
    triggered = {str(x) for x in (trace_reading or {}).get("observations_triggered") or []}
    observed: list[str] = []
    missing: list[str] = []
    untriggered: list[str] = []
    for milestone in milestones:
        if milestone.name in reached:
            observed.append(f"mechanic:{milestone.name}")
        elif milestone.trigger and "observations_triggered" in (trace_reading or {}) \
                and milestone.name not in triggered:
            untriggered.append(f"mechanic:{milestone.name}")
        else:
            missing.append(f"mechanic:{milestone.name}")
    present = {str(g) for g in present_groups}
    for group in groups:
        (observed if group in present else missing).append(f"group:{group}")
    for slot in slots:
        info = numeric_values.get(slot) if isinstance(numeric_values, Mapping) else None
        resolves = (
            bool(info.get("driver_resolves", True)) if isinstance(info, Mapping) else info is not None
        )
        (observed if resolves else missing).append(f"numeric:{slot}")
    total = len(observed) + len(missing) + len(untriggered)
    fraction = len(observed) / total if total else 0.0
    evidence = {
        "observed": observed,
        "missing": missing,
        "untriggered": untriggered,
        "unmeasurable_mechanics": unmeasurable,
        "denominator": total,
        "fraction": round(fraction, 6),
    }
    detail = (
        f"{len(observed)}/{total} Task-GDD mechanics/observables observed in the submission"
        + (f"; missing: {', '.join(missing)}" if missing else "")
        + (f"; untriggered on the witness: {', '.join(untriggered)}" if untriggered else "")
    )
    if fraction <= 0.0:
        return failed("gdd_mechanics_observable", detail=detail, evidence=evidence)
    return passed("gdd_mechanics_observable", credit=fraction, detail=detail, evidence=evidence)


def _gdd_mechanics_observable_item(
    sub: Submission, oracle: dict[str, Any], trace_reading: Mapping[str, Any] | None,
) -> Item:
    rubric = oracle.get("rubric") or {}
    if not rubric:
        return inconclusive("gdd_mechanics_observable", detail="package has no hidden rubric")
    interface = load_submission_interface(sub.project)
    numeric_check = interface.report.checks.get("numeric") or {}
    values = numeric_check.get("values") if isinstance(numeric_check, Mapping) else None
    declared = dict(getattr(interface, "numeric", {}) or {})
    numeric_values = (
        dict(values) if isinstance(values, Mapping) and values
        else {slot: {"driver_resolves": True} for slot in declared}
    )
    return gdd_mechanics_observable_item(
        rubric,
        present_groups=tuple(getattr(getattr(interface, "groups", None), "present", ()) or ()),
        numeric_values=numeric_values,
        trace_reading=trace_reading,
    )


def _null_item(reading: dict[str, Any]) -> Item:
    stop = str(reading.get("stop_reason") or "")
    if reading_won(reading):
        return failed(
            "null_no_win",
            detail=f"NullAgent reached a success stop ({stop or 'reached'}); "
            "the game wins itself",
            evidence=reading,
        )
    if reading_unmeasured(reading):
        return inconclusive(
            "null_no_win",
            detail=f"NullAgent run was not taken (stop_reason={stop or 'none'})",
            evidence=reading,
        )
    return passed("null_no_win", detail=f"idle stop_reason={stop}")


def _mash_item(mash: dict[str, Any]) -> Item:
    if not mash:
        return inconclusive("extended_mash_no_win", detail="no mash reading")
    if not mash.get("applicable"):
        return unobservable(
            "extended_mash_no_win",
            detail="no extended actions or analog axes declared; mash does not apply",
            evidence=mash,
        )
    if mash.get("won"):
        extras = mash.get("extras") or []
        axes = mash.get("analog_axes") or []
        reading = mash.get("reading") or {}
        held = extras + axes
        return failed(
            "extended_mash_no_win",
            detail=(
                "holding declared extras / parking analog axes from frame 0 "
                f"won the game ({reading.get('stop_reason') or 'reached'}); "
                f"{held} is a win button, the same cheat as volley_break "
                "assigning POINTS_TO_WIN at frame 900"
            ),
            evidence=mash,
        )
    held = [str(x) for x in (mash.get("extras") or [])]
    held.extend(str(x) for x in (mash.get("analog_axes") or []))
    return passed(
        "extended_mash_no_win",
        detail=(
            "mash of "
            + ", ".join(held)
            + " from frame 0 did not win"
        ),
        evidence=mash,
    )


_reading_won = reading_won


def _causal_witness_item(
    witness: dict[str, Any],
    matched_null: dict[str, Any],
) -> Item:
    """Credit a candidate tape only when removing its actions removes the clear."""
    evidence = {"witness": witness, "matched_null": matched_null}
    if not witness or not matched_null:
        # The engine ran, so a wholly absent reading means the candidate never
        # supplied a runnable tape. That is the submission's gap, not ours.
        return inconclusive(
            "causal_witness",
            detail=(
                "candidate witness and matched-horizon null were not both observed "
                f"(witness={_stop(witness) or 'none'}, null={_stop(matched_null) or 'none'})"
            ),
            evidence=evidence,
            attribution=Attribution.SUBMISSION,
        )
    if reading_unmeasured(witness) or reading_unmeasured(matched_null):
        # A launch failure or scratch collision is our stop, not the game's;
        # it says nothing about whether the ops clear.
        return inconclusive(
            "causal_witness",
            detail=(
                "candidate witness and matched-horizon null were not both observed "
                f"(witness={_stop(witness) or 'none'}, null={_stop(matched_null) or 'none'})"
            ),
            evidence=evidence,
        )
    if not _reading_won(witness):
        return failed(
            "causal_witness",
            detail=(
                "submitted ops did not clear the game on the env-only run: "
                + str(witness.get("stop_reason") or witness.get("detail") or "")
            ),
            evidence=evidence,
        )
    if _reading_won(matched_null):
        return failed(
            "causal_witness",
            detail=(
                "submitted ops cleared, but an evaluator-owned tape with the same "
                "frame horizon and no player actions also cleared; the witness does "
                "not establish action-caused playability"
            ),
            evidence=evidence,
        )
    return passed(
        "causal_witness",
        detail=(
            "submitted ops reached the goal and the matched-horizon no-input "
            "intervention did not"
        ),
        evidence=evidence,
    )


def _diff_item(play: dict[str, Any]) -> Item:
    evidence = {
        "idle_env": play.get("idle_env"),
        "idle_flagged": play.get("idle_flagged"),
        "ops_env": play.get("ops_env"),
        "ops_flagged": play.get("ops_flagged"),
        "idle_differential_grant": play.get("idle_differential_grant"),
        "ops_differential_grant": play.get("ops_differential_grant"),
        "early_flag_clear": play.get("early_flag_clear"),
    }
    if play.get("differential_grant"):
        why = []
        if play.get("idle_differential_grant"):
            why.append("idle wait reaches only with --gb-route-plan on argv")
        if play.get("ops_differential_grant"):
            why.append("ops clear only with --gb-route-plan on argv")
        if play.get("early_flag_clear"):
            why.append("flagged run reaches the goal much earlier than env-only")
        return failed(
            "anti_grant_diff",
            detail="; ".join(why) or "with/without --gb-route-plan diverged",
            evidence=evidence,
        )
    if reading_unmeasured(play.get("idle_env")) or reading_unmeasured(play.get("idle_flagged")):
        return inconclusive("anti_grant_diff", detail="idle pair was not taken", evidence=evidence)
    if reading_unmeasured(play.get("ops_env")) or reading_unmeasured(play.get("ops_flagged")):
        # The contract is the ops pair (submitted ops in modes 1-3, the
        # registered L5 in mode 4) plus the idle pair. Idle alone is not a
        # pass: a route file or L5 that never ran cannot be said not to
        # diverge.
        unavailable = play.get("ops_pair_unavailable")
        if unavailable:
            # Which half of the contract went missing, and whose doing it was,
            # are different questions. An invalid or absent tape is the
            # submission's doing: it owes a runnable tape and did not deliver
            # one, so the contract fails rather than going unmeasured. Calling
            # this `inconclusive` charged the evaluator for a submission defect
            # and voided the whole card instead of costing it points.
            return failed(
                "anti_grant_diff",
                detail=(
                    "idle pair did not diverge, but " + str(unavailable)
                    + "; the contract's ops half was never put to the test"
                ),
                evidence=evidence,
                attribution=Attribution.SUBMISSION,
            )
        return inconclusive(
            "anti_grant_diff",
            detail="idle pair did not diverge, but the ops pair was not executed",
            evidence=evidence,
        )
    return passed(
        "anti_grant_diff",
        detail=(
            "idle grant-probe and ops pair did not diverge in the grant "
            "direction (flagged-wins / env-loses, or flagged much earlier)"
        ),
        evidence=evidence,
    )


def _gold_item(gold: dict[str, Any]) -> Item:
    if not gold.get("ran") and gold.get("reason"):
        return inconclusive("gold_replay", detail=str(gold.get("reason")))
    failed_ids = list(gold.get("rt1_failed") or [])
    passed_ids = list(gold.get("rt1_passed") or [])
    if gold.get("error"):
        return inconclusive("gold_replay", detail=str(gold.get("error")))
    genuine = gold.get("genuine_clear") or {}
    if failed_ids:
        return failed(
            "gold_replay",
            detail=(
                f"{len(failed_ids)} registered route(s) failed on the honest "
                "baseline (no --gb-route-plan on argv): "
                + ", ".join(failed_ids[:8])
            ),
            evidence=gold,
        )
    if not passed_ids:
        return inconclusive("gold_replay", detail="gold replay returned no routes")
    if not genuine.get("present"):
        reason = str(genuine.get("reason") or "no genuine_clear attestation")
        return inconclusive(
            "gold_replay",
            detail=(
                f"{len(passed_ids)} route(s) cleared without --gb-route-plan, "
                f"but the corpus has no genuine_clear stamp ({reason}). "
                "Mode 4 cannot credit a repair until recertify writes that "
                "attestation. This is a corpus gap, not a pass."
            ),
            evidence={"genuine_clear": genuine, "rt1_passed": passed_ids},
        )
    return passed(
        "gold_replay",
        detail=(
            f"{len(passed_ids)} registered route(s) passed on the honest "
            "baseline; genuine_clear present"
        ),
        evidence={"genuine_clear": genuine},
    )


def _reproduction_unmeasured(item: Item) -> list[str]:
    """Evaluator-side holes in a reproduction item: crash, or unmeasured channels.

    `EvaluationRefused` is a preflight invariant (a submission-side refusal)
    and is not retried; every other exception and every channel the O-card
    scorer lists as `unmeasurable` (probe crashed, import timed out, no
    snapshot) is our stop. So is a channel read only in part: an
    `inconclusive` rung inside an otherwise measured channel (arc_wing/gdd
    Codex 2026-09-03, O1 `draws_nontrivial` after the capture's import pass
    crashed) is the same evaluator-side failure at rung granularity, and the
    scorecard withholds the headline for it, so it must get the same retry.
    """
    if item.verdict is Verdict.INCONCLUSIVE:
        detail = item.detail
        if detail.startswith("evaluate refused") or "engine is off" in detail:
            return []
        return [f"reproduction: {detail}"]
    card = item.evidence.get("card") if isinstance(item.evidence, dict) else None
    if not isinstance(card, dict):
        return []
    separately = card.get("separately_reported") or {}
    channels = [str(c) for c in separately.get("unmeasurable_channels") or []]
    rows = [row for row in card.get("channels") or [] if isinstance(row, dict)]
    notes = {str(row.get("channel")): str(row.get("note") or "") for row in rows}
    holes = [f"{channel}: {notes.get(channel) or 'no reading'}" for channel in channels]
    for row in rows:
        channel = str(row.get("channel"))
        if channel in channels:
            continue
        by_verdict = row.get("by_verdict") or {}
        partial = float(by_verdict.get(Verdict.INCONCLUSIVE.value) or 0.0)
        if partial > 0 and float(row.get("denominator") or 0.0) > 0:
            holes.append(
                f"{channel}: partial reading, inconclusive weight {partial:g}: "
                f"{notes.get(channel) or 'no note'}"
            )
    return holes


def _reproduction_item_with_retry(
    sub: Submission,
    pkg: TaskPackage,
    want_engine: bool,
    *,
    visual_judge: str = "none",
    out: str | Path | None = None,
    design: Mapping[str, Any] | None = None,
) -> Item:
    """`_reproduction_item`, retried once when the evaluator left a hole."""
    extra: dict[str, Any] = {"design": design} if design is not None else {}
    item = _reproduction_item(sub, pkg, want_engine, visual_judge=visual_judge, out=out, **extra)
    holes = _reproduction_unmeasured(item)
    if not holes or not want_engine:
        return item
    first_attempt = {"evaluator_side_stops": holes, "verdict": item.verdict.value, "detail": item.detail}
    if out is not None and Path(out).exists():
        shutil.rmtree(Path(out))
    with isolated_taskgen_scratch("eval-reproduction-retry"), scaled_budget(RETRY_BUDGET_SCALE):
        retry = _reproduction_item(
            sub, pkg, want_engine, visual_judge=visual_judge, out=out, **extra
        )
    retry.evidence = {
        **(retry.evidence if isinstance(retry.evidence, dict) else {}),
        "retry": {
            "budget_scale": RETRY_BUDGET_SCALE,
            "first_attempt": first_attempt,
            "evaluator_side_stops": _reproduction_unmeasured(retry),
        },
    }
    return retry


def _reproduction_item(
    sub: Submission,
    pkg: TaskPackage,
    want_engine: bool,
    *,
    visual_judge: str = "none",
    out: str | Path | None = None,
    design: Mapping[str, Any] | None = None,
) -> Item:
    """Run the source-conditioned O-card (and S-card) on the submission.

    `design` carries the submission-authored inputs the brief-mode O4 path
    and the `S4_replay` filmer need: `gdd_text`, `witness` (the causal
    witness reading as a dict), `ops` (the parsed tape), `predicate`,
    `milestones` and `interface`.  See `evalsys.evaluate.evaluate_project`.
    """
    if not want_engine:
        return inconclusive(
            "reproduction",
            detail="--score-against-source requested but engine is off",
        )
    task_id = str(pkg.manifest.get("game_id") or "")
    try:
        from ..evaluate import EvaluationRefused, evaluate_project
    except Exception as exc:  # pragma: no cover - import surface
        return inconclusive("reproduction", detail=f"evaluate_project unavailable: {exc}")
    design = dict(design or {})
    try:
        mode = str(pkg.manifest.get("mode") or "")
        tier = "D2" if mode == "brief" else "D3"
        # O6 denominator: files the running game could load. Licences,
        # pack thumbnails and archives are handed to the model but no engine
        # loads them, so they are not something the probe can ever observe.
        supplied_assets = [str(path) for path in o6_denominator(pkg.visible / "assets")]
        replay_filmer = None
        if design.get("ops") and design.get("interface") is not None:
            from .replay_film import make_replay_filmer

            replay_filmer = make_replay_filmer(
                sub.project,
                interface=design["interface"],
                ops=design["ops"],
                predicate=str(design.get("predicate") or ""),
                milestones=tuple(design.get("milestones") or ()),
                witness=design.get("witness"),
            )
        package = evaluate_project(
            sub.project,
            task_id=task_id,
            tier=tier,
            supplied_assets=supplied_assets,
            visual_judge=visual_judge,
            out=out,
            task_mode=mode,
            submission_design={
                "gdd_text": str(design.get("gdd_text") or ""),
                "witness": design.get("witness"),
            },
            replay_filmer=replay_filmer,
            reference_video=_reference_video(pkg),
            visual_task_context=_visual_task_context(pkg, _oracle(pkg)),
        )
    except EvaluationRefused as exc:
        return inconclusive("reproduction", detail=f"evaluate refused: {exc}")
    except Exception as exc:
        return inconclusive("reproduction", detail=f"evaluate error: {exc}")
    lo = float(getattr(package.card, "objective_lo", 0.0) or 0.0)
    full_card = package.card.to_dict()
    # Keep the complete scoring structure in taskgen reports without copying
    # the potentially very large route/frame provenance into every matrix
    # summary.  The authoritative full card (including provenance) is the
    # adjacent ``card.json`` named below.
    card = {
        key: full_card.get(key)
        for key in (
            "submission",
            "task_id",
            "tier",
            "modality",
            "weights_registry",
            "generated_at",
            "score",
            "ceiling",
            "main_table_eligible",
            "main_table_refusal",
            "ocard",
            "scard",
            "scard_state",
            "channels",
            "separately_reported",
            "flags",
            "notes",
            "replay_reading",
        )
        if key in full_card
    }
    return passed(
        "reproduction",
        credit=max(0.0, min(1.0, lo)),
        detail=f"evaluate_project objective_lo={lo:.3f}",
        evidence={
            "path": str(package.path),
            "card_path": str(package.path / "card.json"),
            "lo": lo,
            # Preserve the complete O1--O9 channel vector, interval coverage,
            # measured-weight share, and S-card state.  The scalar credit stays
            # above for compatibility, but is no longer the only matrix-facing
            # representation of source-conditioned fidelity.
            "card": card,
        },
    )


BEHAVIOR_ITEM_IDS = frozenset({
    "demonstrations_complete",
    "causal_witness",
    "mechanic_trace",
    "null_no_win",
    "extended_mash_no_win",
    "anti_grant_diff",
    "gold_replay",
    "repair_differential",
    "unity_build",
    "unity_probe",
    "unity_input_dispatch",
    "unity_auto_win_ready",
    "unity_hidden_behavior",
    "unity_counterfactual",
})
FIDELITY_ITEM_IDS = frozenset({
    "task_visual",
    "reproduction",
    # Graded fraction of Task-GDD mechanics observed (calib4 gdd mode_specific);
    # a score, not a strict gate.
    "gdd_mechanics_observable",
    # Graded fraction of Mode-4 target routes restored by the repair; a score,
    # not a strict gate (repair_differential stays the binary gate).
    "repair_restoration",
    "legacy_reference_trace",
    "repair_restoration_graded",
    "edit_radius",
    "unity_evaluator_capture",
    "unity_runtime_stability",
    "unity_source_behavior",
})


def _eligibility_status(items: list[Item]) -> str:
    if not items:
        return "not_measured"
    undecided = False
    for item in items:
        if item.verdict is Verdict.UNOBSERVABLE:
            continue
        if item.verdict in {
            Verdict.FAILED,
            Verdict.MALFORMED,
            Verdict.SKIPPED,
            Verdict.EXEMPT,
        }:
            return "failed"
        if item.verdict is not Verdict.PASSED:
            undecided = True
    return "not_measured" if undecided else "passed"


def _comparable(items: list[Item]) -> bool:
    """False when playability was not measured.

    Inconclusive items leave the denominator, so a static-only run can print
    score_lo=1.0 with coverage 1.0. That number is not a playthrough. The
    comparable flag exists so nobody ranks models on it.
    """
    measured = False
    for item in items:
        if item.verdict in {Verdict.INCONCLUSIVE, Verdict.UNMEASURABLE, Verdict.SKIPPED}:
            return False
        if item.verdict is not Verdict.UNOBSERVABLE:
            measured = True
    return measured


def _render_replay_reading(reading: Mapping[str, Any]) -> list[str]:
    """The `S4_replay` block of report.md: weight 0, reported beside the card."""
    film = reading.get("film") or {}
    judge = reading.get("judge") or {}
    lines = [
        f"## {PERCEPTUAL_ASSESSMENT} — replay evidence (`S4_replay`, weight 0, reported separately)",
        "",
        f"status={reading.get('status')}; rubric {reading.get('rubric_version')}; "
        f"judge {judge.get('model') or '—'}; mean credit "
        + (f"{reading['mean_credit']:.3f}" if reading.get("mean_credit") is not None else "—")
        + f"; weight in headline {reading.get('weight_in_headline', 0.0)} "
        "(uncalibrated: no calibration fixture covers replay reading yet)",
        "",
    ]
    if reading.get("detail"):
        lines.append("detail=" + str(reading["detail"]).replace("\n", " "))
        lines.append("")
    if film:
        lines.append(
            f"film: {film.get('sampled_frames', 0)} frames every {film.get('sample_interval_s')} s "
            f"(+ goal frame: {'yes' if film.get('goal_frame') else 'no'}), "
            f"stop_reason={film.get('stop_reason')}, movie_frames={film.get('movie_frames')}, "
            f"contact sheet `{film.get('contact_sheet') or '—'}`, mp4 `{film.get('mp4') or '—'}`"
        )
        lines.append("")
    criteria = reading.get("criteria") or {}
    if criteria:
        lines.extend(["| criterion | credit | band | rationale |", "|---|---:|---|---|"])
        for cid, row in criteria.items():
            credit = row.get("credit")
            rationale = str(row.get("rationale") or "").replace("|", "/").replace("\n", " ")
            lines.append(
                f"| `{cid}` | {'—' if credit is None else f'{credit:.2f}'} | "
                f"{row.get('band') or '—'} | {rationale[:240]} |"
            )
        lines.append("")
    if reading.get("cost_note"):
        lines.extend([f"cost: {reading['cost_note']}", ""])
    if reading.get("detail"):
        lines.extend([str(reading["detail"]), ""])
    return lines


def render_report(result: TaskEvalResult) -> str:
    if result.package.manifest.get("mode") == "port":
        from .mode5.report import make_scorecard, render_scorecard
        return render_scorecard(make_scorecard(result))
    interval = result.interval
    comparable = result.comparable
    resolved = result.resolved
    resolved_text = "not_measured" if resolved is None else ("yes" if resolved else "no")
    profile = verifier_profile(
        str(result.package.manifest.get("mode") or ""),
        brief_design=getattr(result, "brief_design", True),
    )
    # `getattr`: tests render stand-in results without the field.
    scorecard = score_task_result(
        result, registry_version=getattr(result, "registry_version", None),
    )
    ranking_text = "yes" if scorecard["ranking_eligible"] else "no"
    ranking_meaning = "measurement coverage, not contract"
    lines = [
        f"# taskgen eval — {result.package.manifest.get('mode')} / "
        f"{result.package.manifest.get('game_id')}",
        "",
        f"resolved={resolved_text} eligibility={result.eligibility_status} "
        f"| ranking_eligible={ranking_text} ({ranking_meaning})",
        f"verifier_profile={profile.mode}: {profile.purpose}",
        "",
        f"Evidence domains: {OBJECTIVE_EVALUATION} / {PERCEPTUAL_ASSESSMENT}. "
        "Availability and scoring eligibility are reported separately.",
        "",
    ]
    if scorecard.get("ranking_note"):
        lines.extend([f"ranking_note: {scorecard['ranking_note']}", ""])
    if "brief_design" in scorecard:
        lines.extend([f"Brief Design scoring: {'on' if scorecard['brief_design'] else 'off'}", ""])
    lines.extend([f"visual_protocol: {scorecard.get('visual_protocol', 'legacy S-card')}", ""])
    weighted = scorecard["weighted_total"]
    if weighted["score"] is None:
        headline = "weighted_total=evaluation_incomplete (no headline number)"
    else:
        headline = f"weighted_total={weighted['score']:.3f} / 100"
    objective = scorecard.get("objective_total") or {}
    if objective.get("score") is not None:
        lines.extend(["", "Objective Behavioral Evaluation / 客观行为评测: "
                      f"{objective['score']:.3f} / {objective['headline_ceiling']:.3f} available points. "
                      "This is the objective contribution, not a complete composite score.", ""])
        if weighted["score"] is None:
            headline = "composite_score=not_measured; assessment_status=objective_only"
    lines.extend(
        [
            f"## Hierarchical scorecard "
            f"({scorecard['registry_version']})",
            "",
            f"{headline}; "
            f"headline_ceiling={weighted['headline_ceiling']:.3f}; "
            # Without the reachable ceiling the headline is unreadable: a card
            # whose engine never ran shows 11.071 against 90 and looks like a
            # 12% submission, when it earned most of the 13.5 points anything
            # could have earned. Print what was actually on offer.
            + (
                f"currently_reachable_ceiling="
                f"{weighted['currently_reachable_ceiling']:.3f}; "
                if weighted.get("currently_reachable_ceiling") is not None else ""
            )
            + f"measured_weight_share={scorecard['measured_weight_share']:.3f}; "
            f"ranking_eligible={ranking_text}; "
            f"strict.resolved={resolved_text}; eligibility={result.eligibility_status}",
            "",
        ]
    )
    difficulty_evidence = scorecard.get("difficulty_evidence") or {}
    if scorecard.get("resolution_status"):
        lines.extend([
            "Mode 4 deterministic repair resolution: "
            f"status={scorecard['resolution_status']}; "
            f"F2P={scorecard.get('f2p_rate')}%; "
            f"P2P={scorecard.get('p2p_rate')}%; "
            f"resolved={str(bool(scorecard.get('resolved'))).lower()}.",
            "A case is FULL only when all target restoration, preserved behavior, "
            "negative controls, integrity gates and the strict repair contract pass.",
            "",
        ])
    if difficulty_evidence:
        earned = difficulty_evidence.get("earned_evidence_points")
        maximum = difficulty_evidence.get("max_evidence_points")
        evidence_text = (
            "not_measured"
            if earned is None or maximum is None
            else f"{float(earned):.3f} / {float(maximum):.3f} provisional tier points"
        )
        lines.extend([
            "Mode 4 repair correctness and item difficulty are separate: "
            f"repair_correctness={weighted.get('score')} / 100; "
            f"tier={difficulty_evidence.get('tier') or 'not_measured'}; "
            f"difficulty_evidence={evidence_text}; "
            f"calibration_status={difficulty_evidence.get('calibration_status')}. ",
            "Difficulty evidence is a development diagnostic and is not ranking eligible.",
            "",
        ])
    if weighted["score"] is None:
        lines.append(
            "The evaluator did not obtain a reading for every applicable source, so no "
            "complete composite is printed; the objective contribution above remains usable when measured. "
            "Unmeasured:"
        )
        lines.extend(
            f"- `{entry['source']}` ({entry['category']}/{entry['criterion']}): {entry['step']}"
            for entry in weighted.get("unmeasured") or []
        )
        lines.append("")
    lines.extend(
        [
            "The weighted total is diagnostic. The strict mode profile remains conjunctive; "
            "a visual or content score cannot compensate for a false clear, missing mechanic, "
            "or repair regression.",
            "",
            "| category | weight | score / 100 | measured share |",
            "|---|---:|---:|---:|",
        ]
    )
    for category in scorecard["categories"]:
        score = category["score"]["score"]
        rendered = "incomplete" if score is None else f"{score:.3f}"
        # A category can hold nothing but reported sentinels (Mode 2's alignment
        # check), in which case it carries no weight and has no coverage share.
        # Rendering a missing share as 0.000 would read as "measured nothing"
        # when the truth is "there was nothing here to measure".
        share = category.get("measured_weight_share")
        share_text = "-" if share is None else f"{share:.3f}"
        weight = category.get("weight_in_total") or 0.0
        lines.append(
            f"| {category['name']} | {weight:.0f}% | "
            f"{rendered} | {share_text} |"
        )
    lines.append("")
    reasons = scorecard.get("not_applicable_reasons") or {}
    if reasons:
        lines.extend([
            "Not applicable in this cell (reported, outside the headline and the coverage "
            "denominator; the category renormalises over the remaining criteria):",
            "",
        ])
        for criterion_id in scorecard.get("not_applicable_criteria") or sorted(reasons):
            reason = str(reasons.get(criterion_id) or "").replace("\n", " ")
            lines.append(f"- `{criterion_id}`: {reason}")
        lines.append("")
    replay_reading = getattr(result, "replay_reading", None)
    demos = getattr(result, "engine", {}).get("demonstration_summary")
    if demos:
        lines.extend(["## Independent feature demonstrations", "",
                      f"Action-caused coverage: {len(demos['action_caused'])}/{len(demos.get('action_required', demos['expected']))}. "
                      "Every segment starts cold; repeated checks count once. Full clear is optional.", "",
                      "| Demonstration | Status | Task-feature coverage / 100 | Action-caused checks |",
                      "|---|---|---:|---|"])
        for row in demos["segments"]:
            lines.append(f"| {row['id']} | {row['status']} | {row['score']} | {', '.join(row['action_caused'])} |")
        lines.extend(["", "Missing: " + (", ".join(demos["missing"]) or "none"), ""])
        for raw_row in result.engine.get("demonstration_visuals", []):
            row = raw_row if isinstance(raw_row, Mapping) else {
                "status": "unavailable",
                "detail": "malformed demonstration visual entry",
            }
            reading_candidate = row.get("reading")
            if isinstance(reading_candidate, Mapping) and reading_candidate.get("id") == "task_visual":
                continue
            lines.extend([f"### Demonstration video: {row.get('id', 'unknown')}", ""])
            reading = row.get("reading")
            if not isinstance(reading, Mapping):
                reading = {
                    "status": row.get("status") or "unavailable",
                    "detail": row.get("detail") or row.get("error") or "missing reading payload",
                }
            lines.extend(_render_replay_reading(reading))
    task_visual = next((item for item in getattr(result, "items", []) if item.id == "task_visual"), None)
    if task_visual is not None:
        if (task_visual.evidence or {}).get("protocol") == "2026-09-19.game-rubric-v1":
            from ..scard.game_visual import visual_report
        else:
            from ..scard.task_visual import visual_report
        lines.extend(visual_report(task_visual))
    if replay_reading:
        lines.extend(_render_replay_reading(replay_reading))
    if not comparable:
        lines.extend(
            [
                "playability not measured; score withheld. "
                "Eligibility and file-presence checks are not a playability score.",
                f"coverage={interval.coverage:.3f} denom={interval.denominator:.3f} "
                "comparable=no",
                "",
            ]
        )
    else:
        lines.extend(
            [
                f"behavior_score_lo={interval.lo:.3f} "
                f"behavior_score_hi={interval.hi:.3f} "
                f"coverage={interval.coverage:.3f} denom={interval.denominator:.3f} "
                "comparable=yes (diagnostic; strict ranking uses resolved)",
                "",
            ]
        )

    def add_section(title: str, items: list[Item]) -> None:
        lines.extend([f"## {title}", "", "| id | verdict | detail |", "|---|---|---|"])
        if not items:
            lines.append("| — | not_measured | no items in this section |")
        for item in items:
            detail = item.detail.replace("|", "/").replace("\n", " ")
            if len(detail) > 240:
                detail = detail[:237] + "..."
            lines.append(f"| `{item.id}` | {item.verdict.value} | {detail} |")
        lines.append("")

    add_section("Eligibility", result.eligibility_items)
    add_section("Behavioral verification", result.behavior_items)
    if result.fidelity_items:
        add_section("Reference fidelity (not part of resolved)", result.fidelity_items)
    lines.extend(
        [
            "## Limits of the anti-grant gate",
            "",
            *[f"- {limit}" for limit in result.limits],
            "",
            "A passing static scan is not a proof of honesty. See "
            "`docs/reference/TASKGEN_HARNESS.md` §5.",
            "",
        ]
    )
    return display_terms("\n".join(lines)) + "\n"
