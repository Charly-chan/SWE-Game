
from __future__ import annotations

from pathlib import Path
from typing import Any, Mapping

from ..verdict import Item, Verdict, failed, inconclusive, passed, unobservable
from .content.rubrics import action_required_checkpoints, rubric_milestones
from .engine import clear_predicate, reading_unmeasured, run_self_play

PROTOCOL = "gamebench.feature-demos.v1"


def run_demonstrations(project, *, mode, demos, interface, rubric):


    rows = []
    for demo in demos:
        play = run_self_play(
            project, mode=mode, ops=demo["ops"], interface=interface,
            predicate=clear_predicate(interface), milestones=rubric_milestones(rubric),
            feature_demo=True,
        )
        rows.append({"id": demo["id"], "description": demo["description"], "play": play})
    return {"protocol": PROTOCOL, "ran": bool(rows) and all(r["play"].get("ran") for r in rows),
            "demonstrations": rows}


def summarize_demonstrations(payload: Mapping[str, Any], rubric: dict) -> dict:
    expected = [m.name for m in rubric_milestones(rubric)]
    action_required = action_required_checkpoints(rubric)
    observed: set[str] = set()
    causal: set[str] = set()
    rows = []
    for demo in payload.get("demonstrations", []):
        play = demo["play"]
        witness, null, flagged = (play.get(k) or {} for k in ("ops_env", "matched_null", "ops_flagged"))
        measured = all(not reading_unmeasured(r) for r in (witness, null, flagged))
        reached = set(witness.get("observations_reached") or []) & set(expected)
        controls = set(null.get("observations_reached") or [])
        flagged_reached = set(flagged.get("observations_reached") or [])
        clean = witness.get("segment_clean") is not False
        verified = (reached - controls) & action_required if clean and measured else set()
        if clean and measured:
            observed.update(reached)
            causal.update(verified)
        rows.append({
            "id": demo["id"], "description": demo["description"],
            "status": "measured" if measured else "inconclusive",
            "observed": sorted(reached), "action_caused": sorted(verified),
            "same_without_input": sorted(reached & controls),
            "flag_only_checks": sorted(flagged_reached - reached),
            "not_observed": sorted(set(expected) - reached),
            "feature_scores": {name: {"observed": 100 if name in reached and clean else 0,
                                      "requires_player_action": name in action_required,
                                      "action_caused": (100 if name in verified else 0) if name in action_required else None}
                               for name in expected} if measured else None,
            "score": round(100 * len(verified) / len(action_required), 3) if measured and action_required else None,
            "score_basis": "fraction of task-declared input-dependent checks causally demonstrated by this segment",
            "segment_clean": clean,
        })
    measured = bool(rows) and all(r["status"] == "measured" for r in rows)
    return {"protocol": PROTOCOL, "expected": expected, "observed": sorted(observed),
            "action_caused": sorted(causal), "missing": sorted(set(expected) - observed),
            "action_required": sorted(action_required),
            "automatic_checks": sorted(set(expected) - action_required),
            "not_action_caused": sorted(action_required - causal),
            "measured": measured, "segments": rows,
            "coverage": len(observed) / len(expected) if expected else None,
            "causal_coverage": len(causal) / len(action_required) if action_required else None,
            "complete": measured and bool(expected) and set(expected) <= observed and (bool(causal) or not action_required),
            "aggregation": "union over task checks; repeated demonstrations earn no extra credit"}


def demonstration_items(summary: dict) -> list[Item]:

    ids = ("mechanic_trace", "causal_witness", "demonstrations_complete")
    if not summary["measured"] or not summary["expected"]:
        return [inconclusive(i, detail="feature demonstrations or task predicates were not measured",
                             evidence=summary) for i in ids]
    n = len(summary["expected"])
    items = [
        Item(id="mechanic_trace", verdict=Verdict.PASSED, credit=summary["coverage"],
             detail=f"graded feature coverage: {len(summary['observed'])}/{n}", evidence=summary),
    ]
    if summary["causal_coverage"] is None:
        items.append(unobservable("causal_witness", detail="task declares only automatic checkpoints", evidence=summary))
    else:
        items.append(Item(id="causal_witness", verdict=Verdict.PASSED, credit=summary["causal_coverage"],
                          detail=f"graded action-caused feature coverage: {len(summary['action_caused'])}/{len(summary['action_required'])}; whole-game clear not required",
                          evidence=summary))
    decide = passed if summary["complete"] else failed
    items.append(decide("demonstrations_complete", detail="all required features observed; input-dependent causal obligation satisfied" if summary["complete"]
                        else "incomplete feature coverage or no action-caused evidence: " + ", ".join(summary["missing"]), evidence=summary))
    return items


def film_demonstrations(sub, interface, rubric, payload, out: Path) -> list[dict]:

    from ..scard.judge import vlm_scard_judge_from_env
    from ..scard.replay import judge_replay
    from .replay_film import film_submission_replay
    from .package import write_json

    judge = vlm_scard_judge_from_env()
    result = []
    for index, (demo, run) in enumerate(zip(sub.demos, payload.get("demonstrations", []))):
        directory = out / f"segment-{index + 1:03d}"
        try:
            film = film_submission_replay(
                sub.project, directory, interface=interface, ops=demo["ops"],
                predicate=clear_predicate(interface), milestones=rubric_milestones(rubric),
                witness=run["play"].get("ops_env"),
                feature_demo=True,
            )
            reading = judge_replay(film, judge, project=str(sub.project),
                                   context={"feature_demo": True,
                                            "task_checks": [m.name for m in rubric_milestones(rubric)]}).to_dict()
        except Exception as exc:
            reading = {"status": "unavailable", "detail": str(exc), "weight_in_headline": 0.0}
        row = {"id": demo["id"], "reading": reading}
        write_json(directory / "reading.json", row)
        result.append(row)
    return result


def capture_task_visuals(sub, interface, rubric, payload, out: Path):

    from ..scard.task_visual import select_demonstrations, visual_budget
    from .replay_film import film_submission_replay
    from .package import write_json

    demos = sub.demos or [{"id": "whole-run", "description": "Submitted whole-game replay", "ops": sub.ops}]
    demos, omitted = select_demonstrations(demos, rubric)
    witnesses = ({row["id"]: row["play"].get("ops_env") for row in payload.get("demonstrations", [])}
                 if sub.demos else {"whole-run": payload.get("ops_env")})
    rows = []
    for index, demo in enumerate(demos):
        directory = out / f"segment-{index + 1:03d}"
        try:
            film = film_submission_replay(
                sub.project, directory, interface=interface, ops=demo["ops"],
                predicate=clear_predicate(interface), milestones=rubric_milestones(rubric),
                witness=witnesses.get(demo["id"]), feature_demo=bool(sub.demos),
            )
            row = {"id": demo["id"], "film": film.to_dict()}
        except Exception as exc:
            row = {"id": demo["id"], "error": str(exc)}
        rows.append(row)
    manifest = {"schema_version": 1, "rubric": rubric, "demonstrations": rows,
                "budget": visual_budget(rubric), "omitted": omitted}
    write_json(out / "visual_inputs.json", manifest)
    return manifest


def judge_saved_visuals(manifest, out: Path, *, judge=None):

    from dataclasses import fields
    from ..scard.replay import ReplayFilm
    from ..scard.task_visual import gamecraft_judge, judge_task_visual, select_demonstrations
    from .package import write_json

    rubric = manifest["rubric"]
    demos, _omitted = select_demonstrations(manifest["demonstrations"], rubric)
    judge = judge or gamecraft_judge()
    film_fields = {field.name for field in fields(ReplayFilm)}
    rows = []
    for index, demo in enumerate(demos):
        directory = out / f"segment-{index + 1:03d}"
        directory.mkdir(parents=True, exist_ok=True)
        try:
            if demo.get("error"):
                raise ValueError(demo["error"])
            film_data = {key: value for key, value in demo["film"].items() if key in film_fields}
            film_data["directory"] = str(directory)
            film = ReplayFilm(**film_data)
            reading = judge_task_visual(film, judge, rubric=rubric, demo_id=demo["id"])
        except Exception as exc:
            reading = {"status": "unavailable", "detail": str(exc)}
        row = {"id": demo["id"], "reading": reading}
        write_json(directory / "reading.json", row)
        rows.append(row)
    return rows


def film_task_visuals(sub, interface, rubric, payload, out: Path):
    manifest = capture_task_visuals(sub, interface, rubric, payload, out)
    return judge_saved_visuals(manifest, out / "judgments")
