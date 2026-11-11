


from __future__ import annotations

from typing import Sequence

from ..truth.expectations import (
    BANDS,
    DISTANCE_TOLERANCE,
    Expectations,
    goal_point,
    norm_distance,
    occupied_bands,
)
from ..truth.snapshot import LevelTruth, TruthSnapshot
from ..verdict import Item, Verdict, skipped, unobservable


def _family(items: Sequence[Item]) -> list[Item]:

    items = list(items)
    if not items:
        return []
    unit = 1.0 / len(items)
    for it in items:
        it.weight = unit
    return items


def _sign(value: float) -> int:
    return (value > 0) - (value < 0)


def canonical_digraph(
    order: Sequence[str], edges: dict[str, str]
) -> tuple[tuple[str, str], ...]:


    symbol = {scene: f"L{i}" for i, scene in enumerate(order)}
    out: list[tuple[str, str]] = []
    externals = 0
    for scene in order:
        target = edges.get(scene)
        if not target:
            continue
        if target not in symbol:
            symbol[target] = f"X{externals}"
            externals += 1
        out.append((symbol[scene], symbol[target]))
    return tuple(out)


def e1_level_count(snapshot: TruthSnapshot, expected: Expectations) -> list[Item]:
    got = len(snapshot.levels)
    want = expected.level_count
    ok = got == want
    return _family([
        Item(
            id="E1/level_count",
            verdict=Verdict.PASSED if ok else Verdict.FAILED,
            credit=1.0 if ok else 0.0,
            detail=(
                f"{got} declared levels, reference has {want}"
                if ok
                else f"{got} declared levels; the reference declares {want}. The level "
                "manifest is the interface, so this is a count of what the submission "
                "itself says it built, not of what we managed to load"
            ),
            evidence={"measured": got, "expected": want},
        )
    ])


def e2_collectible_order(snapshot: TruthSnapshot, expected: Expectations) -> list[Item]:


    want = expected.collectible_counts()
    got = tuple(lv.census.get("gb_collectible", 0) for lv in snapshot.levels)
    if len(want) < 2:
        return _family([
            unobservable(
                "E2/collectible_order",
                detail="the reference has fewer than two levels, so there is no "
                       "ordering between levels to reproduce",
            )
        ])
    if len(got) != len(want):
        return _family([
            skipped(
                "E2/collectible_order",
                detail=f"the submission declares {len(got)} levels and the reference "
                       f"{len(want)}, so the two orderings cannot be lined up; E1 is "
                       "where that difference is scored",
            )
        ])

    disagreements: list[str] = []
    pairs = 0
    for i in range(len(want)):
        for j in range(i + 1, len(want)):
            pairs += 1
            if _sign(want[i] - want[j]) != _sign(got[i] - got[j]):
                disagreements.append(
                    f"levels {i} and {j}: reference {want[i]} vs {want[j]}, "
                    f"submission {got[i]} vs {got[j]}"
                )
    ok = not disagreements
    return _family([
        Item(
            id="E2/collectible_order",
            verdict=Verdict.PASSED if ok else Verdict.FAILED,
            credit=1.0 if ok else 0.0,
            detail=(
                f"all {pairs} level pairs rank the same way as the reference "
                f"(counts {got} against {want}); the counts themselves are not compared, "
                "so a correct reproduction at a different scale is not penalised"
                if ok
                else f"{len(disagreements)}/{pairs} level pairs rank differently: "
                + "; ".join(disagreements[:3])
            ),
            evidence={"measured": list(got), "expected": list(want),
                      "disagreements": disagreements},
        )
    ])


def e3_goal_distance(snapshot: TruthSnapshot, expected: Expectations) -> list[Item]:
    items: list[Item] = []
    for index, want in enumerate(expected.levels):
        item_id = f"E3/goal_distance[{index}]"
        if want.spawn_goal_distance is None:
            items.append(unobservable(
                item_id,
                detail="the reference level has no spawn-to-goal distance to reproduce "
                       "(no gb_goal, or no gb_player to measure from)",
            ))
            continue
        if index >= len(snapshot.levels):
            items.append(skipped(
                item_id, detail="the submission declares no level at this index"))
            continue
        level = snapshot.levels[index]
        got = norm_distance(level.spawn_norm, goal_point(level))
        if got is None:
            items.append(skipped(
                item_id,
                detail="no normalised spawn-to-goal distance could be read from this "
                       "level: it has no gb_goal, no gb_player, or degenerate bounds",
            ))
            continue
        floor = want.spawn_goal_distance * DISTANCE_TOLERANCE
        ok = got >= floor
        items.append(Item(
            id=item_id,
            verdict=Verdict.PASSED if ok else Verdict.FAILED,
            credit=1.0 if ok else 0.0,
            detail=(
                f"the goal sits {got:.3f} of the level away from the spawn, against a "
                f"floor of {floor:.3f} ({want.spawn_goal_distance:.3f} x "
                f"{DISTANCE_TOLERANCE}). Normalised against this level's own bounds, so "
                "a smaller or larger level is not the question -- a goal underfoot is"
            ),
            evidence={"measured": got, "reference": want.spawn_goal_distance,
                      "floor": floor},
        ))
    return _family(items)


def e4_digraph(snapshot: TruthSnapshot, expected: Expectations) -> list[Item]:
    want = canonical_digraph(
        [lv.scene for lv in expected.levels], expected.digraph
    )
    got = canonical_digraph(
        [lv.scene or lv.declared_scene for lv in snapshot.levels], snapshot.digraph
    )
    if not want:
        return _family([
            unobservable(
                "E4/digraph",
                detail="no level in the reference led anywhere when its goal was "
                       "reached, so there is no inter-level graph to reproduce. This is "
                       "a reading about the reference and belongs in gold triage, not a "
                       "finding about the submission",
            )
        ])
    ok = want == got
    return _family([
        Item(
            id="E4/digraph",
            verdict=Verdict.PASSED if ok else Verdict.FAILED,
            credit=1.0 if ok else 0.0,
            detail=(
                f"the level graph is isomorphic to the reference: {_render(got)}"
                if ok
                else f"the level graph differs: measured {_render(got)}, reference "
                     f"{_render(want)}. Scene paths are replaced by position before "
                     "comparing, so this is about structure and not about file names"
            ),
            evidence={"measured": [list(e) for e in got],
                      "expected": [list(e) for e in want]},
        )
    ])


def _render(edges: Sequence[tuple[str, str]]) -> str:
    return ", ".join(f"{a}->{b}" for a, b in edges) or "(no edges)"


def e5_dispersion(snapshot: TruthSnapshot, expected: Expectations) -> list[Item]:


    items: list[Item] = []
    for index, want_level in enumerate(expected.levels):
        for group, (axis, want_bands) in sorted(want_level.dispersion.items()):
            item_id = f"E5/dispersion[{index}/{group}]"
            if int(want_bands) < 2:
                items.append(unobservable(
                    item_id,
                    detail=f"the reference confines {group} to a single band on the "
                           f"{axis} axis, so there is no spread to reproduce",
                ))
                continue
            if index >= len(snapshot.levels):
                items.append(skipped(
                    item_id, detail="the submission declares no level at this index"))
                continue
            got_group = snapshot.levels[index].groups.get(group)
            if got_group is None or not got_group.normalised:
                items.append(skipped(
                    item_id,
                    detail=f"no normalised {group} positions were read from this level",
                ))
                continue
            got_bands = occupied_bands(got_group.normalised, axis)
            ok = got_bands >= int(want_bands)
            items.append(Item(
                id=item_id,
                verdict=Verdict.PASSED if ok else Verdict.FAILED,
                credit=1.0 if ok else 0.0,
                detail=(
                    f"{len(got_group.normalised)} {group} spread over {got_bands} of "
                    f"{BANDS} normalised bands on the {axis} axis, against the "
                    f"reference's {want_bands}"
                ),
                evidence={"measured": got_bands, "expected": int(want_bands),
                          "axis": axis, "bands": BANDS},
            ))
    if not items:
        return _family([
            unobservable(
                "E5/dispersion",
                detail="the reference declares no multi-band entity groups, so there "
                       "is no layout spread to reproduce",
            )
        ])
    return _family(items)


def level_topology(snapshot: TruthSnapshot, expected: Expectations) -> list[Item]:

    return e1_level_count(snapshot, expected) + e4_digraph(snapshot, expected)


def spatial_ordinal(snapshot: TruthSnapshot, expected: Expectations) -> list[Item]:

    return (
        e2_collectible_order(snapshot, expected)
        + e3_goal_distance(snapshot, expected)
        + e5_dispersion(snapshot, expected)
    )
