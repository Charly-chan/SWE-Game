


from __future__ import annotations

from typing import Sequence

from ..conformance import FAILURE_ENDINGS, SUCCESS_ENDINGS
from ..truth.snapshot import LevelTruth, TruthSnapshot
from ..verdict import Item, Verdict, inconclusive, skipped, unobservable


def _family(items: Sequence[Item]) -> list[Item]:


    items = list(items)
    if not items:
        return []
    unit = 1.0 / len(items)
    for it in items:
        it.weight = unit
    return items


def _decided(item_id: str, ok: bool, detail: str, **evidence) -> Item:
    return Item(
        id=item_id,
        verdict=Verdict.PASSED if ok else Verdict.FAILED,
        credit=1.0 if ok else 0.0,
        detail=detail,
        evidence=dict(evidence),
    )


def _readable(snapshot: TruthSnapshot) -> list[LevelTruth]:

    return [lv for lv in snapshot.levels if lv.reached]


def u1_single_player(snapshot: TruthSnapshot, player_counts: Sequence[int] = (1,)) -> list[Item]:


    items: list[Item] = []
    for index, lv in enumerate(_readable(snapshot)):
        count = lv.census.get("gb_player", 0)
        items.append(_decided(
            f"U1/single_player[{index}]",
            count in player_counts,
            f"{count} nodes tagged gb_player in {lv.scene}"
            + f"; public task contract permits player counts {list(player_counts)}",
            measured=count,
            expected=list(player_counts),
        ))
    if not items:
        return _family([skipped(
            "U1/single_player",
            detail="no declared level could be entered, so no census was taken",
        )])
    return _family(items)


def u2_actions_live(snapshot: TruthSnapshot) -> list[Item]:


    if not snapshot.declared_actions:
        return _family([_decided(
            "U2/actions_live",
            False,
            "the project binds none of the eight declared action names, so nothing "
            "can be injected at all; 15/15 projects were once measured at zero",
            measured=[],
        )])
    if not snapshot.liveness:
        return _family([inconclusive(
            "U2/actions_live",
            detail="no liveness sweep was run for this snapshot, so whether the "
                   "controls do anything was never measured. This is our gap, not the "
                   "submission's, and it must not read as dead bindings",
        )])
    live = [a.action for a in snapshot.liveness if a.live]
    return _family([_decided(
        "U2/actions_live",
        bool(live),
        f"{len(live)}/{len(snapshot.liveness)} declared actions move the state "
        f"signature away from the matched idle control ({', '.join(live)})"
        if live
        else f"none of {len(snapshot.liveness)} declared actions changed any component "
             "of the state signature relative to a zero-input control of the same "
             "length -- dead bindings",
        live=live,
        via={a.action: a.via for a in snapshot.liveness},
    )])


def u3_collect_removes_one(snapshot: TruthSnapshot) -> list[Item]:


    items: list[Item] = []
    for index, lv in enumerate(_readable(snapshot)):
        item_id = f"U3/collect_removes_one[{index}]"
        if lv.census.get("gb_collectible", 0) == 0:


            items.append(_decided(
                item_id, False,
                f"{lv.scene} declares no gb_collectible: there is nothing to pick up, "
                "which is this level not implementing collection rather than a "
                "reading the evaluator could not take",
                census_gb_collectible=0,
            ))
            continue
        if not lv.collect.attempted:
            items.append(skipped(
                item_id,
                detail="the player could not be placed on any collectible in this "
                       f"level: {lv.collect.detail}",
            ))
            continue
        effect = lv.collect.effect


        if "expected_delta" in effect:
            ok = lv.collect.delta == effect["expected_delta"]
        else:
            ok = bool(effect.get("observed"))
        if effect.get("activation_missing") or effect.get("observation_missing") or (
            not ok and not effect.get("complete_trigger")
        ):


            items.append(unobservable(
                item_id, detail=lv.collect.detail + "; no verified activation/effect pair: contact alone does not establish this mechanic, and this task declares no activation probe_plan to drive it",
                evidence={"group_delta": lv.collect.delta, "effect": effect,
                          "attributed_to": "evaluator"},
            ))
        else:
            items.append(_decided(
                item_id, ok, lv.collect.detail + "; evaluated the declared collection effect",
                measured=lv.collect.delta, effect=effect,
            ))
    if not items:
        return _family([skipped(
            "U3/collect_removes_one",
            detail="no declared level could be entered, so no pickup was attempted",
        )])
    return _family(items)


def u4_goal_changes_scene(snapshot: TruthSnapshot) -> list[Item]:


    items: list[Item] = []
    for index, lv in enumerate(_readable(snapshot)):
        item_id = f"U4/goal_changes_scene[{index}]"
        if lv.goal.effect.get("kind") == "route_progress":
            items.append(unobservable(item_id, detail=lv.goal.detail))
            continue
        if lv.census.get("gb_goal", 0) == 0:


            items.append(_decided(
                item_id, False,
                f"{lv.scene} declares no gb_goal: there is no win condition to reach, "
                "which is this level not implementing progression rather than a "
                "reading the evaluator could not take",
                census_gb_goal=0,
            ))
            continue
        if not lv.goal.attempted:
            items.append(skipped(
                item_id,
                detail=f"the player could not be placed on the goal: {lv.goal.detail}",
            ))
            continue


        failure_scenes = {
            v for k, v in snapshot.endings.items() if k in FAILURE_ENDINGS and v
        }
        changed = lv.goal.changed or bool(lv.goal.effect.get("observed"))
        detail = lv.goal.detail
        if changed and lv.goal.scene_after in failure_scenes:
            changed = False
            detail += (
                "; the scene that followed is a declared failure ending, which is "
                "not a goal transition"
            )
        if not changed and (not lv.goal.effect.get("complete_trigger") or lv.goal.effect.get("observation_missing")):


            items.append(unobservable(
                item_id, detail=detail + "; contact did not establish goal prerequisites; progress needs an input-route witness, and the certified routes carry that witness on task_checkpoints",
                evidence={"effect": lv.goal.effect, "attributed_to": "evaluator",
                          "scored_on": "task_checkpoints"},
            ))
            continue
        items.append(_decided(
            item_id,
            changed,
            detail,
            measured=lv.goal.scene_after,
            before=lv.goal.scene_before,
            effect=lv.goal.effect,
        ))
    if not items:
        return _family([skipped(
            "U4/goal_changes_scene",
            detail="no declared level could be entered, so no goal was reached",
        )])
    return _family(items)


def u5_no_input_no_win(snapshot: TruthSnapshot) -> list[Item]:


    if snapshot.no_input_no_win is None:
        return _family([inconclusive(
            "U5/no_input_no_win",
            detail="the zero-input control was never run for this snapshot; without it "
                   "'nothing happened' cannot be distinguished from 'nothing was tried'",
        )])
    return _family([_decided(
        "U5/no_input_no_win",
        snapshot.no_input_no_win,
        f"a zero-input control run stopped with `{snapshot.no_input_stop_reason}`"
        + (
            ""
            if snapshot.no_input_no_win
            else "; the game reached its goal with no input at all"
        ),
        stop_reason=snapshot.no_input_stop_reason,
    )])


def u6_levels_independently_enterable(snapshot: TruthSnapshot) -> list[Item]:


    if not snapshot.levels:
        return _family([_decided(
            "U6/levels_enterable",
            False,
            "the project declares no levels at all; gb_levels.json is the interface, "
            "so its absence is a failure and not a gap",
            measured=0,
        )])
    items: list[Item] = []
    for index, lv in enumerate(snapshot.levels):
        items.append(_decided(
            f"U6/levels_enterable[{index}]",
            lv.reached,
            f"{lv.declared_scene} was entered directly and the engine confirmed it "
            f"was running it"
            if lv.reached
            else f"{lv.declared_scene} was launched directly and the engine reported it "
                 f"was running {lv.scene or '(nothing)'} instead"
                 + (f"; {lv.errors[0][:160]}" if lv.errors else ""),
            declared=lv.declared_scene,
            measured=lv.scene,
        ))
    return _family(items)


def u7_distinct_endings(snapshot: TruthSnapshot) -> list[Item]:


    endings = snapshot.endings
    success_scenes = {v for k, v in endings.items() if k in SUCCESS_ENDINGS and v}
    failure_scenes = {v for k, v in endings.items() if k in FAILURE_ENDINGS and v}
    shared = sorted(success_scenes & failure_scenes)
    success_only = sorted(success_scenes - failure_scenes)
    ok = bool(success_only) and not shared
    if not endings:
        detail = "no endings are declared; gb_levels.json.endings is a submission obligation"
    elif shared:
        detail = "success and failure share scene(s): " + ", ".join(shared)
    elif not success_only:
        detail = "no success-only ending scene is declared"
    else:
        detail = "success-only ending scene(s): " + ", ".join(success_only)
    return _family([_decided(
        "U7/distinct_endings",
        ok,
        detail,
        declared=endings,
        success_only=success_only,
        shared=shared,
    )])


UNIVERSAL = (
    ("U1", u1_single_player),
    ("U2", u2_actions_live),
    ("U3", u3_collect_removes_one),
    ("U4", u4_goal_changes_scene),
    ("U5", u5_no_input_no_win),
    ("U6", u6_levels_independently_enterable),
    ("U7", u7_distinct_endings),
)


def all_universal(snapshot: TruthSnapshot, *, player_counts: Sequence[int] = (1,)) -> list[Item]:


    items: list[Item] = []
    for _id, fn in UNIVERSAL:
        items.extend(fn(snapshot, player_counts) if _id == "U1" else fn(snapshot))
    return items
