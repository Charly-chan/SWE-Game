


from __future__ import annotations

from typing import Any

from ..tasks import task_metadata


def probe_plan(task_id: str | None) -> dict[str, Any]:
    if not task_id:
        return {}
    plan = task_metadata(task_id).get("probe_plan")
    if plan is None:
        return {}
    if not isinstance(plan, dict):
        raise ValueError(f"probe_plan in eval/tasks/{task_id}/task.json must be a JSON object")
    return plan


def player_counts_for_mode(plan: dict[str, Any], task_mode: str) -> tuple[int, ...] | None:

    by_mode = plan.get("player_counts_by_mode") or {}
    counts = by_mode.get(task_mode, plan.get("player_counts"))
    if counts is None:
        return None
    return tuple(int(c) for c in counts)


def reference_failure_edges(task_id, expected):


    import json
    from ..conformance import FAILURE_ENDINGS
    from ..tasks import snapshot_path

    if not task_id:
        return {}
    path = snapshot_path(task_id)
    if not path.is_file():
        return {}
    endings = json.loads(path.read_text(encoding="utf-8")).get("endings", {})
    failures = {scene for name, scene in endings.items() if name in FAILURE_ENDINGS}
    return {scene: target for scene, target in expected.digraph.items() if target in failures}


def campaign_progress_items(routes, readings, gold):

    from ..routes.runner import HARNESS_STOPS
    from ..verdict import Item, Verdict, inconclusive

    item_id = "U4/whole_campaign_goal"
    certified = {g.route_id for g in gold if g.ok}
    candidates = [r for r in routes if r.route_id in certified and r.tier == 5
                  and not r.start.inject and r.start.level == 0
                  and r.goal.predicate.strip() == "whole_game_clear()"]
    by_id = {r.route_id: r for r in readings}
    if not candidates:
        return [inconclusive(item_id, detail="no certified zero-inject whole-campaign route; goal prerequisites remain unobserved")]
    observed = [by_id.get(r.route_id) for r in candidates]
    if any(r is None or r.stop_reason in HARNESS_STOPS for r in observed):
        return [inconclusive(item_id, detail="whole-campaign goal has no complete engine reading")]
    ok = all(by_id[r.route_id].all_three and by_id[r.route_id].clean_clear
             and not by_id[r.route_id].invariants_failed
             and all(m.name in by_id[r.route_id].milestones_reached for m in r.goal.milestones)
             for r in candidates)
    return [Item(id=item_id, verdict=Verdict.PASSED if ok else Verdict.FAILED,
                 credit=float(ok), detail="complete goal prerequisites evaluated by certified input-only whole-campaign replay; this is one campaign observation, not independent per-level evidence",
                 evidence={"source": "registered_whole_campaign", "routes": [
                     {"id": r.route_id, "predicate": r.goal.predicate,
                      "reached": by_id[r.route_id].reached,
                      "segment_clean": by_id[r.route_id].segment_clean,
                      "used_required": by_id[r.route_id].used_required,
                      "milestones_reached": by_id[r.route_id].milestones_reached,
                      "stop_reason": by_id[r.route_id].stop_reason} for r in candidates]})]


def route_progress_items(snapshot, routes, readings, gold):


    from ..routes.runner import HARNESS_STOPS
    from ..verdict import Item, Verdict, inconclusive

    certified = {g.route_id for g in gold if g.ok}
    by_id = {r.route_id: r for r in readings}
    items = []
    levels = [lv for lv in snapshot.levels if lv.reached]
    for index, level in enumerate(levels):
        candidates = [r for r in routes if r.route_id in certified
                      and 1 <= r.tier < 5 and not r.start.inject
                      and r.start.level == snapshot.levels.index(level)]
        item_id = f"U4/goal_changes_scene[{index}]"
        if not candidates:
            items.append(inconclusive(item_id, detail="goal requires a prerequisite sequence; no certified zero-inject progress route covers this entry"))
            continue
        observed = [by_id.get(r.route_id) for r in candidates]
        if any(r is None or r.stop_reason in HARNESS_STOPS for r in observed):
            items.append(inconclusive(item_id, detail="goal requires a prerequisite sequence; its progress route has no complete engine reading"))
            continue
        ok = all(by_id[r.route_id].all_three and not by_id[r.route_id].invariants_failed
                 and all(m.name in by_id[r.route_id].milestones_reached for m in r.goal.milestones)
                 for r in candidates)
        items.append(Item(
            id=item_id, verdict=Verdict.PASSED if ok else Verdict.FAILED,
            credit=float(ok), detail="declared progress predicates replayed through certified input-only routes; no scene-change assumption",
            evidence={"source": "registered_routes", "routes": [
                {"id": r.route_id, "predicate": r.goal.predicate,
                 "reached": by_id[r.route_id].reached,
                 "used_required": by_id[r.route_id].used_required,
                 "milestones_reached": by_id[r.route_id].milestones_reached,
                 "stop_reason": by_id[r.route_id].stop_reason}
                for r in candidates
            ]},
        ))
    for item in items:
        item.weight = 1.0 / len(items)
    return items
