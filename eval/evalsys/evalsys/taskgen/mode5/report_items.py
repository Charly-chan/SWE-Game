"""Mode 5 report serialization helpers."""
from __future__ import annotations

from typing import Any, Mapping

from ...verdict import Attribution, Item, Verdict


def item_from_report(raw: Mapping[str, Any]) -> Item:
    attribution = raw.get("attribution")
    return Item(
        id=str(raw.get("id") or ""), weight=float(raw.get("weight") or 1.0),
        verdict=Verdict(str(raw.get("verdict") or "inconclusive")),
        credit=float(raw.get("credit") or 0.0), detail=str(raw.get("detail") or ""),
        attribution=Attribution(str(attribution)) if attribution else None,
        evidence=dict(raw.get("evidence") or {}),
    )


def replace_item(items: list[Item], replacement: Item) -> None:
    for index, current in enumerate(items):
        if current.id == replacement.id:
            items[index] = replacement
            return
    items.append(replacement)
