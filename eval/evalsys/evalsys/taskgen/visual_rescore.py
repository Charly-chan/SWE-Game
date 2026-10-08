
from __future__ import annotations

import json
from dataclasses import fields
from pathlib import Path
from types import SimpleNamespace

from ..verdict import Attribution, Item
from ..scard.task_visual import aggregate_task_visual, select_demonstrations, visual_report
from .demonstrations import capture_task_visuals, judge_saved_visuals
from .package import TaskPackage, write_json
from .scorecard import (
    VISUAL_REGISTRY_VERSION, MODE1_VLM_REGISTRY_VERSION, MODE2_VLM_REGISTRY_VERSION,
    MODE3_VLM_REGISTRY_VERSION,
    default_registry_for_mode, registry_policy, score_task_result,
)


def rescore_visuals(report_path, out, *, visual_inputs=None, package=None,
                    submission=None, record_missing=False, judge=None, game_rubric=False,
                    registry_version=None):
    source = Path(report_path).resolve()
    out = Path(out).resolve()
    if out.exists() and any((out / name).exists() for name in ("report.json", "card.json")):
        prior_card = json.loads((out / "card.json").read_text(encoding="utf-8")) \
            if (out / "card.json").is_file() else {}
        if (
            (prior_card.get("visual_total") or {}).get("status") == "complete"
            or prior_card.get("assessment_status") == "complete"
            or prior_card.get("evaluation_incomplete") is False
        ):
            raise ValueError(f"completed output already exists: {out}; use a new directory")
    data = json.loads(source.read_text(encoding="utf-8"))
    if data.get("mode") == "port":
        raise ValueError("Mode 5 uses gb mode5 rejudge for retained Community evidence")
    if data.get("mode") == "bugfix":
        raise ValueError("Mode 4 keeps its repair/regression score; no task_visual contribution")
    stored_registry = (data.get("scorecard") or {}).get("registry_version")
    selected_registry = registry_version or (stored_registry if stored_registry in {
        MODE1_VLM_REGISTRY_VERSION, MODE2_VLM_REGISTRY_VERSION, MODE3_VLM_REGISTRY_VERSION,
    } else None)
    policy = registry_policy(selected_registry, mode=data["mode"]) if selected_registry else None
    if policy and not policy.task_visual:
        raise ValueError("Visual rescoring needs a mode-specific VLM registry or 2026-09-11.visual1")
    if judge is None and policy and (policy.mode1_redesign or policy.progressive_redesign_mode):
        from ..scard.rubric_judge import rubric_judge_from_env
        rubric_judge_from_env().validate_configuration()
    manifest_path = Path(visual_inputs) if visual_inputs else source.parent / "demonstrations/visual_inputs.json"
    if not manifest_path.is_file() and not visual_inputs:
        manifest_path = source.parent / "task_visual/visual_inputs.json"
    if visual_inputs and not manifest_path.is_file():
        raise FileNotFoundError(f"visual input manifest missing: {manifest_path}")

    def record_retained():
        from .evaluate import _oracle
        from ..interface import load_submission_interface
        from .submission import load_submission

        pkg = TaskPackage.read(package or data["package"])
        rubric = _oracle(pkg).get("rubric") or {}
        sub = load_submission(submission or data["submission"])
        return capture_task_visuals(sub, load_submission_interface(sub.project), rubric,
                                    data.get("engine") or {}, out / "recordings")

    if manifest_path.is_file():
        manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    else:
        from .evaluate import _oracle

        pkg = TaskPackage.read(package or data["package"])
        rubric = _oracle(pkg).get("rubric") or {}
        recorded = [{"id": row["id"], "film": row["reading"]["film"]}
                    for row in data.get("demonstration_visuals", [])
                    if (row.get("reading") or {}).get("film", {}).get("mp4")]
        if recorded:
            manifest = {"schema_version": 1, "rubric": rubric, "demonstrations": recorded}
        elif record_missing and data.get("mode") in {"brief", "gdd", "skeleton"}:
            manifest = record_retained()
        else:
            raise ValueError("no saved evaluator recording; retain the project/package and use "
                             "--record-missing for Godot, or supply --visual-inputs for a recorded Unity run")
    # Recording paths are the evidence. Raw report numbers cannot replace them.
    game_rubric = (bool(policy.mode1_redesign or policy.progressive_redesign_mode) if policy
                   else game_rubric or bool(manifest.get("game_rubric")))
    scoring_registry = selected_registry or (
        default_registry_for_mode(data["mode"]) if game_rubric else VISUAL_REGISTRY_VERSION)
    visual_item_id = "task_visual"
    selected, _omitted = select_demonstrations(manifest["demonstrations"], manifest["rubric"])
    if (record_missing and data.get("mode") in {"brief", "gdd", "skeleton"}
            and any(not (row.get("film") or {}).get("mp4")
                    or not Path(row["film"]["mp4"]).is_file() for row in selected)):
        # External runners can retain the report/project but prune large videos.
        # A stale manifest should not disable the explicit re-record option.
        manifest = record_retained()
        selected, _omitted = select_demonstrations(manifest["demonstrations"], manifest["rubric"])
    # The game-rubric judge owns failure attribution. In particular, a saved
    # candidate-side "no ops tape" record is a complete visual zero, whereas a
    # missing/undecodable evaluator file is retryable. Do not collapse both to
    # an exception before that classifier can run.
    if not game_rubric:
        for row in selected:
            film = row.get("film") or {}
            if not film.get("mp4") or not Path(film["mp4"]).is_file():
                raise ValueError(f"evaluator recording missing for {row['id']}; restore the retained video")
    if game_rubric and not manifest.get("game_rubric"):
        from .visual_materials import prepare_visual_manifest

        pkg = TaskPackage.read(package or data["package"])
        manifest = prepare_visual_manifest(pkg, manifest, out / "demonstrations")
    if game_rubric:
        from ..scard.game_visual import judge_game_visual, visual_report as game_visual_report

        if manifest.get("game_id") != data["game_id"] or manifest.get("mode") != data["mode"]:
            raise ValueError("Visual evidence manifest belongs to a different game or mode")
        # Apply the task-owned demo cap to this judging scope as well.
        manifest = {**manifest, "demonstrations": selected}
        item = judge_game_visual(manifest, out / "demonstrations/judgments", judge=judge)
        rows = manifest["demonstrations"]
        report_lines = game_visual_report(item)
    else:
        rows = judge_saved_visuals(manifest, out / "demonstrations/judgments", judge=judge)
        item = aggregate_task_visual(rows, manifest["rubric"])
        report_lines = visual_report(item)
    item.id = visual_item_id
    allowed = {field.name for field in fields(Item)}
    items = []
    for stored in data.get("items", []):
        if stored.get("id") == visual_item_id:
            continue
        raw = {key: value for key, value in stored.items() if key in allowed}
        if raw.get("attribution"):
            raw["attribution"] = Attribution(raw["attribution"])
        items.append(Item(**raw))
    items.append(item)
    # Native redesign axes also read retained engine truth and package oracle.
    # Keep those inputs when adding VLM evidence; dropping them changes the
    # objective score even though no behavioral test ran again.
    result = SimpleNamespace(package=SimpleNamespace(
        manifest={"mode": data["mode"], "game_id": data["game_id"]},
        root=Path(package or data.get("package") or "."),
        hidden=Path(package or data.get("package") or ".") / "hidden"),
        items=items, engine=data.get("engine") or {},
        stored_report=data, report_path=source,
        resolved=data.get("resolved"), brief_context=data.get("brief_context") or {})
    card = score_task_result(result, scoring_registry)
    data["items"] = [stored for stored in data.get("items", []) if stored.get("id") != visual_item_id]
    data["items"].append(item.to_dict())
    data["scorecard"] = card
    weighted = card.get("weighted_total") or {}
    data["headline"] = {
        "status": weighted.get("status") or "evaluation_incomplete",
        "score": weighted.get("score"),
        "scale": weighted.get("scale") or "0-100",
        "ranking_eligible": bool(card.get("ranking_eligible")),
        "score_scope": card.get("score_scope") or "task_capability",
    }
    if "registry_version" in data:
        data["registry_version"] = card["registry_version"]
    if "production_status" in data:
        data["production_status"] = "scored" if weighted.get("score") is not None else "evaluator_failed"
    data["demonstration_visuals"] = rows
    data.setdefault("engine", {})["demonstration_visuals"] = rows
    data["visual_rescore"] = {"source_report": str(source), "agent_rerun": False,
                              "record_missing_requested": record_missing,
                              "game_rubric": game_rubric}
    write_json(out / "demonstrations/visual_inputs.json", manifest)
    write_json(out / "report.json", data)
    write_json(out / "card.json", card)
    objective = card.get("objective_total") or {}
    lines = ["# Visual re-evaluation", "", f"Source report: {source}",
             "Agent rerun: no. Objective evidence and resolved verdict retained.",
             f"Objective contribution: {objective.get('score')} / {objective.get('headline_ceiling')} available points.",
             f"Composite score: {card['weighted_total']['score']} / 100.",
             f"Assessment status: {card.get('assessment_status') or card.get('outcome_status')}",
             "", *report_lines]
    (out / "report.md").write_text("\n".join(lines) + "\n", encoding="utf-8")
    return data
