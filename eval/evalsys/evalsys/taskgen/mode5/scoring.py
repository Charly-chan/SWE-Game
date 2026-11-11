"""The single protocol used by new public Mode 5 runs.

The evidence registry is fixed, with no selectable scoring fallback.
Execution environment equivalence is deliberately not asserted.
"""
from __future__ import annotations

from typing import Any

from .score import REGISTRY_VERSION
from .report import RANKING_SCOPE

COMPONENTS = {
    "core_mechanics": ("mechanics", 35.0),
    "playability_progression": ("playability", 25.0),
    "content_structure": ("structure", 15.0),
    "visual_feedback": ("visual", 15.0),
    "stability_lifecycle": ("stability", 10.0),
}


def annotate_report(report: dict[str, Any]) -> dict[str, Any]:
    card = report["scorecard"]
    if card.get("registry_version") != REGISTRY_VERSION:
        raise ValueError("Mode 5 report is not from the release five-component registry")
    rows = {row["id"]: row for row in card["categories"]}
    for ident, (name, weight) in COMPONENTS.items():
        row = rows[ident]
        if row["weight_in_total"] != weight:
            raise ValueError(f"Mode 5 component weight drift: {name}")
    headline = {
        **card["weighted_total"], "ranking_eligible": card["ranking_eligible"],
        "ranking_scope": RANKING_SCOPE,
    }
    card.update(headline=headline, ranking_scope=RANKING_SCOPE)
    report.update(headline=headline, score=headline, registry_version=REGISTRY_VERSION,
                  score_mode=card["score_mode"], visual_protocol=card["visual_protocol"],
                  official_total=None, paper_compatible=False)
    return report
