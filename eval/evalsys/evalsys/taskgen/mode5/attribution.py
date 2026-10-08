from __future__ import annotations
from dataclasses import replace
from ..scorecard import _mode5_item_attribution, _mode5_normalize_candidate_blocked_items
from ...verdict import Attribution, Item
def _attribute_mode5_items(items: list[Item]) -> list[Item]:


    attributed: list[Item] = []


    for item in _cascade_mode5_root_failure(items):
        if item.attribution is not None:
            attributed.append(item)
            continue
        raw = _mode5_item_attribution(item)
        if raw == Attribution.HARNESS.value:
            attributed.append(replace(item, attribution=Attribution.HARNESS))
        elif raw == Attribution.SUBMISSION.value:
            attributed.append(replace(item, attribution=Attribution.SUBMISSION))
        elif raw == Attribution.UNATTRIBUTABLE.value:
            attributed.append(replace(item, attribution=Attribution.UNATTRIBUTABLE))
        elif raw is not None:
            attributed.append(replace(item, attribution=Attribution(raw)))
        else:
            attributed.append(item)
    return attributed


def _cascade_mode5_root_failure(items: list[Item]) -> list[Item]:

    normalized = _mode5_normalize_candidate_blocked_items({item.id: item for item in items})
    return [normalized[item.id] for item in items]
