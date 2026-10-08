


from __future__ import annotations

import json
from functools import lru_cache
from pathlib import Path
from typing import Sequence

from ..tasks import all_task_metadata
from ..truth.snapshot import TruthSnapshot
from ..verdict import Item, Verdict, exempt
from .universal import all_universal


RUBRIC_CATALOG_PATH = (
    Path(__file__).resolve().parents[1] / "taskgen" / "content" / "rubric_catalog.json"
)


def _catalog_exemptions(path: Path = RUBRIC_CATALOG_PATH) -> dict[str, dict[str, str]]:
    if not path.is_file():
        return {}
    raw = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(raw, dict):
        return {}
    out: dict[str, dict[str, str]] = {}
    for task_id, rubric in raw.items():
        families = rubric.get("genre_exemptions") if isinstance(rubric, dict) else None
        if isinstance(families, dict) and families:
            out[str(task_id)] = {str(k): str(v) for k, v in families.items()}
    return out


def _merged_exemptions() -> dict[str, dict[str, str]]:
    merged: dict[str, dict[str, str]] = {}
    for task_id, families in _catalog_exemptions().items():
        merged.setdefault(task_id, {}).update(families)

    for task_id, metadata in all_task_metadata().items():
        families = metadata.get("genre_exemptions")
        if isinstance(families, dict):
            merged.setdefault(task_id, {}).update(
                {str(k): str(v) for k, v in families.items()}
            )
    return merged


BY_TASK: dict[str, dict[str, str]] = _merged_exemptions()


BY_PROJECT = BY_TASK


def apply_genre_exemptions(task_id: str | None, items: Sequence[Item]) -> list[Item]:


    if not task_id:
        return list(items)
    families = BY_TASK.get(task_id)
    if not families:
        return list(items)
    out: list[Item] = []
    for it in items:
        family = it.id.split("/", 1)[0]
        reason = families.get(family)
        if reason is None:
            out.append(it)
            continue
        evidence = dict(it.evidence)
        evidence["exemption"] = "genre_inapplicable"
        evidence["task_id"] = task_id
        evidence["original_verdict"] = it.verdict.value
        out.append(exempt(
            it.id,
            weight=it.weight,
            detail=f"genre_inapplicable ({task_id}): {reason}. measured: {it.detail}",
            evidence=evidence,
        ))
    return out


CENSUS_FAMILY_GROUP = {"U3": "gb_collectible", "U4": "gb_goal"}


@lru_cache(maxsize=None)
def _reference_declares(task_id: str, group: str) -> bool | None:


    from ..tasks import snapshot_path

    path = snapshot_path(task_id)
    if not path.is_file():
        return None
    try:
        snapshot = TruthSnapshot.from_dict(json.loads(path.read_text(encoding="utf-8")))
    except (OSError, ValueError, TypeError):
        return None
    return any(lv.census.get(group, 0) for lv in snapshot.levels)


def apply_reference_census_exemptions(
    task_id: str | None, items: Sequence[Item],
) -> list[Item]:


    if not task_id:
        return list(items)
    out: list[Item] = []
    for it in items:
        group = CENSUS_FAMILY_GROUP.get(it.id.split("/", 1)[0])
        evidence = dict(it.evidence or {})
        if (group is None or it.verdict is not Verdict.FAILED
                or evidence.get("census_" + group) != 0):
            out.append(it)
            continue
        if _reference_declares(task_id, group) is not False:
            out.append(it)
            continue
        evidence["exemption"] = "reference_declares_no_" + group
        evidence["task_id"] = task_id
        evidence["original_verdict"] = it.verdict.value
        out.append(exempt(
            it.id,
            weight=it.weight,
            detail=(
                f"genre_inapplicable ({task_id}): the frozen reference declares no "
                f"{group} in any level, so this is not one of this game's mechanics. "
                f"measured: {it.detail}"
            ),
            evidence=evidence,
        ))
    return out


def apply_goal_probe_exemption_to_e4(task_id: str | None, items: Sequence[Item]) -> list[Item]:


    if not task_id:
        return list(items)
    reason = (BY_TASK.get(task_id) or {}).get("U4")
    if not reason:
        return list(items)
    out: list[Item] = []
    for it in items:
        if not it.id.startswith("E4/") or not it.verdict.is_decided:
            out.append(it)
            continue
        evidence = dict(it.evidence)
        evidence["exemption"] = "genre_inapplicable"
        evidence["task_id"] = task_id
        evidence["original_verdict"] = it.verdict.value
        evidence["inherited_from"] = "U4"
        out.append(exempt(
            it.id,
            weight=it.weight,
            detail=(
                f"genre_inapplicable ({task_id}, inherited from U4): E4 reads the level "
                f"edge by the same teleport-onto-gb_goal probe as U4, and {reason}. "
                f"measured: {it.detail}"
            ),
            evidence=evidence,
        ))
    return out


def evaluate_universal(
    snapshot: TruthSnapshot,
    *,
    task_id: str | None = None,
    player_counts: Sequence[int] | None = None,
) -> list[Item]:


    if player_counts is None:
        from .behavior_contracts import probe_plan
        player_counts = probe_plan(task_id).get("player_counts", (1,))
    return apply_reference_census_exemptions(
        task_id,
        apply_genre_exemptions(task_id, all_universal(snapshot, player_counts=player_counts)),
    )
