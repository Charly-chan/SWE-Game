


from __future__ import annotations

import json
import math
import shutil
import tarfile
from pathlib import Path
from typing import Any, Mapping

from ..evaluate import (
    TaskEvalResult,
    _oracle,
    _reference_video,
    _unity_fidelity_score_items,
    _visual_task_context,
)
from ..package import TaskPackage, write_json
from ..submission import Submission
from ..scorecard import (
    MODE5_MDVA_REGISTRY_VERSION, score_task_result,
    _mode5_item_attribution, _mode5_evidence_attribution,
)
from ...verdict import Attribution, Item, Verdict, failed


def _safe_extract(archive: Path, destination: Path) -> None:
    destination.mkdir(parents=True, exist_ok=True)
    root = destination.resolve()
    with tarfile.open(archive, "r:gz") as tf:
        for member in tf.getmembers():
            name = Path(member.name)
            target = (root / name).resolve()
            if target != root and root not in target.parents:
                raise ValueError(f"artifact archive path escapes extraction root: {member.name}")
            if member.issym() or member.islnk():
                raise ValueError(f"artifact archive contains a link: {member.name}")
        tf.extractall(root)


def _item(raw: Mapping[str, Any]) -> Item:
    attribution = raw.get("attribution")
    return Item(
        id=str(raw.get("id") or ""),
        weight=float(raw.get("weight") or 1.0),
        verdict=Verdict(str(raw.get("verdict") or "inconclusive")),
        credit=float(raw.get("credit") or 0.0),
        detail=str(raw.get("detail") or ""),
        attribution=Attribution(str(attribution)) if attribution else None,
        evidence=dict(raw.get("evidence") or {}),
    )


def _replace(items: list[Item], replacement: Item) -> None:
    for index, current in enumerate(items):
        if current.id == replacement.id:
            items[index] = replacement
            return
    items.append(replacement)


def _candidate_files(extracted: Path) -> tuple[Path | None, list[str]]:
    runtime = extracted / "runtime"
    videos = sorted(runtime.rglob("*.mp4")) if runtime.is_dir() else []
    frames = sorted(
        [*runtime.rglob("*.png"), *runtime.rglob("*.jpg"), *runtime.rglob("*.jpeg")]
    ) if runtime.is_dir() else []
    return (videos[0] if videos else None, [str(path) for path in frames])


def _candidate_failed(items: list[Item]) -> bool:


    root_failed = any(
        item.id in {"unity_build", "unity_probe"}
        and item.verdict in {Verdict.FAILED, Verdict.MALFORMED}
        and _mode5_item_attribution(item) == Attribution.SUBMISSION.value
        for item in items
    )
    if not root_failed:
        return False
    for item in items:
        if item.id not in {"unity_evaluator_capture", "unity_vlm", "unity_structure_fidelity"}:
            continue
        if item.id == "unity_evaluator_capture" and item.verdict is Verdict.PASSED:
            return False
        if item.verdict not in {Verdict.INCONCLUSIVE, Verdict.UNMEASURABLE, Verdict.UNOBSERVABLE}:
            continue
        if any(marker in (item.detail or "").lower() for marker in (
            "disabled by evaluator configuration", "not requested", "requires visual_judge",
        )):
            continue
        owner = (item.attribution.value if item.attribution is not None
                 else _mode5_evidence_attribution(item.evidence))
        if owner in {Attribution.HARNESS.value, Attribution.UNATTRIBUTABLE.value}:
            return False
    return True


def _zero_visual(item_id: str, detail: str) -> Item:
    return failed(
        item_id,
        detail=detail,
        attribution=Attribution.SUBMISSION,
        evidence={"cause": "candidate_delivery_failure", "score_policy": "candidate_zero"},
    )


def finalize_mode5(report: str | Path, package: str | Path, artifacts: str | Path,
                   out: str | Path) -> dict[str, Any]:
    source = json.loads(Path(report).read_text(encoding="utf-8"))
    if str(source.get("mode") or "") != "port":
        raise ValueError("Mode 5 finalizer requires a port report")
    pkg = TaskPackage.read(package)
    destination = Path(out).resolve()
    destination.mkdir(parents=True, exist_ok=True)
    extracted = destination / "extracted"
    _safe_extract(Path(artifacts).resolve(), extracted)
    candidate_video, candidate_frames = _candidate_files(extracted)
    items = [_item(raw) for raw in source.get("items") or []]
    candidate_failed = _candidate_failed(items)
    if candidate_failed and candidate_video is None and not candidate_frames:


        _replace(items, _zero_visual("unity_vlm", "candidate produced no renderable Unity witness"))
        _replace(items, _zero_visual("unity_structure_fidelity", "candidate produced no renderable Unity witness"))
        _replace(items, _zero_visual("unity_evaluator_capture", "candidate produced no renderable Unity witness"))
    elif candidate_video is None:
        raise RuntimeError("evaluator_failed: no retained Unity witness attributable to a candidate build/probe failure")
    else:
        oracle = _oracle(pkg)
        reference = _reference_video(pkg)
        if reference is None:
            raise RuntimeError("evaluator_failed: canonical reference video is unavailable")
        from ..unity.unity_fidelity import judge_cross_engine_fidelity
        fidelity = judge_cross_engine_fidelity(
            reference, candidate_frames, candidate_video=candidate_video,
            out_dir=destination / "structure", game_id=str(pkg.manifest.get("game_id") or ""),
            task_context=_visual_task_context(pkg, oracle),
        )
        fidelity_data = fidelity.to_dict()
        structure_items = _unity_fidelity_score_items(fidelity_data)
        for measured in structure_items:
            _replace(items, measured)
        from ..visual_materials import prepare_visual_manifest
        from ...scard.game_visual import judge_game_visual
        manifest = prepare_visual_manifest(
            pkg,
            {"demonstrations": [{"id": "unity-runtime", "film": {"mp4": str(candidate_video)}}],
             "runtime_facts": {"candidate_frames": candidate_frames}},
            destination / "mdva",
        )
        mdva = judge_game_visual(manifest, destination / "mdva" / "judgments")
        mdva.id = "unity_vlm"
        _replace(items, mdva)
        capture = next((i for i in items if i.id == "unity_evaluator_capture"), None)
        if capture and capture.verdict is Verdict.INCONCLUSIVE:
            capture.verdict = Verdict.PASSED
            capture.credit = 1.0
            capture.detail = "verified candidate Unity witness retained by host artifact handoff"
    submission_root = Path(source.get("submission") or destination).resolve()
    result = TaskEvalResult(
        pkg,
        Submission(root=submission_root, project=submission_root, engine="unity"),
        items=items,
        engine=dict(source.get("engine") or {}),
        registry_version=MODE5_MDVA_REGISTRY_VERSION,
    )
    card = score_task_result(result, registry_version=MODE5_MDVA_REGISTRY_VERSION)
    score = (card.get("weighted_total") or {}).get("score")
    if not isinstance(score, (int, float)) or not math.isfinite(float(score)):
        raise RuntimeError("evaluator_failed: final Mode 5 score is null")
    final = dict(source)
    final["registry_version"] = MODE5_MDVA_REGISTRY_VERSION
    final["items"] = [item.to_dict() for item in items]
    final["scorecard"] = card
    final["headline"] = {"status": card.get("outcome_status", "scored"), "score": score,
                          "scale": "0-100", "ranking_eligible": bool(card.get("ranking_eligible")),
                          "score_scope": "mode5_model_capability_only"}
    final["score"] = dict(final["headline"])
    final["score_scope"] = "mode5_model_capability_only"
    final["production_status"] = "scored"
    write_json(destination / "report.json", final)
    write_json(destination / "card.json", card)
    write_json(destination / "production_status.json", {"status": "scored", "score": score,
        "registry_version": MODE5_MDVA_REGISTRY_VERSION})
    return final


__all__ = ["finalize_mode5"]
