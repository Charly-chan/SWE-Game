
from __future__ import annotations

import math
import os
from pathlib import Path
from typing import Any, Mapping, Sequence

from gamecraft_bench.verifier.judges.base import RequirementSpec

from ..verdict import Item, inconclusive, passed

UPSTREAM_REVISION = "a43347534374df9a0c1a6c001aa9380862783f6d"
RUBRIC_VERSION = "2026-09-11.gamecraft-visual1"
GROUP_WEIGHTS = {"requirements": 40, "feedback": 20, "readability": 20, "presentation": 20}
PRESENTATION = {
    "feedback": "Actions and game-state changes have clear visible feedback through animation, effects, or changing indicators.",
    "readability": "The player, relevant objects and required interface are readable without misleading occlusion, clipping or illegible text. No HUD is required unless the task calls for it.",
    "presentation": "The visible assets, backgrounds and effects form a complete and coherent presentation appropriate to the task.",
}


def visual_budget(rubric: Mapping[str, Any]) -> dict[str, int | float]:

    count = rubric.get("max_demos", 10)
    seconds = rubric.get("max_demo_seconds", 20.0)
    if isinstance(count, bool) or not isinstance(count, int) or count < 1:
        raise ValueError("rubric max_demos must be a positive integer")
    if (isinstance(seconds, bool) or not isinstance(seconds, (float, int))
            or not math.isfinite(seconds) or seconds <= 0):
        raise ValueError("rubric max_demo_seconds must be positive and finite")
    return {"max_demos": count, "max_demo_seconds": float(seconds)}


def select_demonstrations(demos: Sequence[Mapping[str, Any]], rubric: Mapping[str, Any]):

    ordered = sorted(demos, key=lambda row: str(row["id"]))
    count = int(visual_budget(rubric)["max_demos"])
    return ordered[:count], [str(row["id"]) for row in ordered[count:]]


def visual_criteria(rubric: Mapping[str, Any]) -> tuple[RequirementSpec, ...]:

    requirements = [RequirementSpec("requirement:" + str(check["id"]),
                                   "The gameplay visibly demonstrates: " + str(check["claim"]))
                    for check in rubric.get("mechanic_checks", []) if check.get("id") and check.get("claim")]
    return tuple(requirements + [RequirementSpec(key, claim) for key, claim in PRESENTATION.items()])


def gamecraft_judge():
    from gamecraft_bench.verifier.judges.openai_gpt import OpenAIJudge


    return OpenAIJudge(model=os.environ.get("GAMECRAFT_BENCH_JUDGE_MODEL") or None)


def judge_task_visual(film, judge=None, *, rubric: Mapping[str, Any], demo_id: str) -> dict[str, Any]:
    from gamecraft_bench.verifier.judges.base import JudgeError, JudgeRequest
    from gamecraft_bench.verifier.score import _sample_frames

    criteria = visual_criteria(rubric)
    budget = visual_budget(rubric)
    metadata = {
        "id": "task_visual", "rubric_version": RUBRIC_VERSION,
        "upstream_revision": UPSTREAM_REVISION,
        "validation_status": "not_independently_validated", "human_scoring": False,
        "aggregation_role": "segment_evidence_for_task_visual", "budget": budget,
        "reference_images_sent": False, "submitter_description_sent": False,
    }
    movie = Path(film.mp4) if film.mp4 else None
    if movie is None or not movie.is_file():
        return {**metadata, "status": "unavailable", "detail": film.error or "no evaluator recording"}
    frames = _sample_frames(movie, Path(film.directory) / "gamecraft_frames",
                            duration_seconds=film.duration_s, interval_seconds=0.5,
                            max_window_seconds=float(budget["max_demo_seconds"]), seed=demo_id)
    judge = judge or gamecraft_judge()
    metadata["judge"] = {"model": judge.model, "backend": judge.name}


    metadata["film"] = {**film.to_dict(), "frames": [str(path) for path in frames],
                        "frame_times_s": [], "contact_sheet": "",
                        "sampled_frames": len(frames), "sample_interval_s": 0.5,
                        "sampling": "GameCraft deterministic window, seeded by demo id"}
    try:
        response = judge.score(JudgeRequest(demo_id=demo_id, video_path=movie,
                                           frame_paths=frames, requirements=list(criteria)))
    except JudgeError as exc:
        return {**metadata, "status": "unavailable", "detail": str(exc)}
    return {**metadata, "status": "measured", "raw": response.raw,
            "criteria": {c.id: {"credit": response.scores[c.id],
                                "rationale": response.rationales.get(c.id, "")}
                         for c in criteria}}


def aggregate_task_visual(rows: Sequence[Mapping[str, Any]], rubric: Mapping[str, Any]) -> Item:

    from gamecraft_bench.verifier.score import _aggregate, _safe_eval_formula

    criteria = visual_criteria(rubric)
    evidence: dict[str, Any] = {
        "rubric_version": RUBRIC_VERSION, "upstream_revision": UPSTREAM_REVISION,
        "validation_status": "not_independently_validated", "human_scoring": False,
        "group_weights": GROUP_WEIGHTS, "budget": visual_budget(rubric),
        "aggregation": "requirements: max across segments; presentation: arithmetic mean across segments",
        "segments": list(rows), "criteria": {},
    }
    if not rows:
        return inconclusive("task_visual", detail="no VLM demonstration readings; objective results remain available", evidence=evidence)
    missing = []
    for criterion in criteria:
        values = {}
        for index, row in enumerate(rows):
            reading = row.get("reading") or {}
            value = (reading.get("criteria", {}).get(criterion.id) or {}).get("credit")
            if (reading.get("rubric_version") != RUBRIC_VERSION or reading.get("status") != "measured"
                    or not isinstance(value, (int, float)) or isinstance(value, bool)
                    or not math.isfinite(value) or not 0 <= value <= 1):
                missing.append(f"{row.get('id', index)}:{criterion.id}")
            else:
                values[str(index)] = float(value)
        if len(values) == len(rows):
            aggregation = "max" if criterion.id.startswith("requirement:") else "mean"
            evidence["criteria"][criterion.id] = {
                "question": criterion.description, "credit": _aggregate(aggregation, values),
                "aggregation": aggregation, "per_segment": list(values.values()),
            }
    if missing:
        evidence["unmeasured"] = missing
        return inconclusive("task_visual", detail="incomplete VLM readings: " + ", ".join(missing), evidence=evidence)
    requirements = [v["credit"] for k, v in evidence["criteria"].items() if k.startswith("requirement:")]
    groups = {key: evidence["criteria"][key]["credit"] for key in PRESENTATION}
    if requirements:
        groups["requirements"] = sum(requirements) / len(requirements)
    denominator = sum(GROUP_WEIGHTS[key] for key in groups)
    formula = "(" + " + ".join(f"{GROUP_WEIGHTS[key]} * {key}" for key in groups) + f") / {denominator}"
    credit = _safe_eval_formula(formula, groups)
    evidence.update(groups=groups, score_formula=formula)
    return passed("task_visual", credit=credit, detail="GameCraft-adapted VLM reading; independent validation pending", evidence=evidence)


def visual_report(item: Item) -> list[str]:
    evidence = item.evidence or {}
    lines = ["## Perceptual Quality Assessment / 感知质量评审", "",
             "GameCraft-adapted judge; no GT reference images or submitter description. "
             "Experimental: not independently validated on this benchmark.", "",
             f"Status: {item.verdict.value}; visual credit: "
             + (f"{100 * item.credit:.2f}/100" if item.verdict.value == "passed" else "not measured"), "",
             "| Requirement / dimension | Aggregation | Credit / 100 |", "|---|---|---:|"]
    for row in evidence.get("criteria", {}).values():
        question = row["question"].replace("|", "/").replace("\n", " ")
        lines.append(f"| {question} | {row['aggregation']} | {100 * row['credit']:.2f} |")
    lines.extend(["", item.detail, ""])
    for row in evidence.get("segments", []):
        reading = row.get("reading") or {}
        film = reading.get("film") or {}
        lines.extend([f"### Visual evidence: {row.get('id', '?')}", "",
                      f"Status: {reading.get('status', 'unavailable')}. {reading.get('detail', '')}",
                      f"Video: {film.get('mp4') or 'not recorded'}.", ""])
        for key, value in reading.get("criteria", {}).items():
            lines.append(f"- {key}: {value.get('credit')}; {value.get('rationale', '')}")
        lines.append("")
    return lines
