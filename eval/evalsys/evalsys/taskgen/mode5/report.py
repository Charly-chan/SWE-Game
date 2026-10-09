"""Community scorecard/report views of controller-owned Mode 5 release evidence.

These helpers do not launch an alternate evaluator or select a second profile.
The Community evaluator retains one private static snapshot in engine evidence
and dispatches its publication through this adapter.
"""
from __future__ import annotations

import json
from types import SimpleNamespace
from typing import Any, Mapping, Sequence

from .adapter import finish_scoring
from .score import CRITERIA, REGISTRY_VERSION, SCHEMA, aggregate_model


RANKING_SCOPE = "mode5-community-evidence-five-visual1"
CATEGORIES = {
    "core_mechanics": "mechanics",
    "playability_progression": "playability",
    "content_structure": "structure",
    "visual_feedback": "visual",
    "stability_lifecycle": "stability",
}


def make_scorecard(result: Any) -> dict[str, Any]:
    """Render the shared TaskEvalResult contract from controller evidence.

    An absent controller snapshot is not repaired by reading mutable candidate
    files after execution, nor by falling back to another score version.
    """
    if result.package.manifest.get("mode") != "port":
        raise ValueError("release registry is defined only for Mode 5 / port")
    engine = getattr(result, "engine", {}) or {}
    snapshot = engine.get("mode5_static")
    if not isinstance(snapshot, Mapping):
        raise ValueError("release requires a pre-execution controller static snapshot")
    raw_items = [item.to_dict() for item in result.items]
    score = finish_scoring(snapshot, items=raw_items, engine=engine)
    score["game_id"] = str(result.package.manifest.get("game_id") or "")
    categories = []
    measured_weight = 0.0
    for ident, component in CATEGORIES.items():
        reading = score["components"][component]
        rows = [row for row in score["criteria"] if row["id"].startswith(component + ".")]
        criteria = []
        category_measured = 0.0
        for row in rows:
            share = sum(value["observed"] for value in row["evidence"]) / row["denominator"]
            category_measured += row["weight"] * share
            criteria.append({
                **row, "name": row["id"],
                "weight_within_category": row["weight"] / reading["weight"] * 100,
                "score": {"score": 100 * row["points"] / row["weight"], "scale": "0-100"},
                "measured_source_share": share,
            })
        measured_weight += category_measured
        categories.append({
            "id": ident, "name": component.capitalize(), "weight_in_total": reading["weight"],
            "score": {"score": reading["score"], "scale": "0-100"},
            "points": reading["points"], "criteria": criteria,
            "measured_weight_share": category_measured / reading["weight"],
            "non_runtime_cap": reading["non_runtime_cap"], "cap_applied": reading["cap_applied"],
        })
    weighted = {
        "score": score["total"], "scale": "0-100", "headline_ceiling": 100.0,
        "status": score["status"],
        "rule": "Fixed 35/25/15/15/10 evidence-adjusted proxy; no VLM or paper equivalence.",
    }
    resolved = result.resolved
    from ..content.verifier_profiles import verifier_profile
    strict_ids = verifier_profile("port").strict_ids
    verdicts = {row["id"]: row.get("verdict") for row in raw_items}
    return {
        "schema": "gamebench.mode5.capability-scorecard.v1",
        "registry_version": REGISTRY_VERSION, "registry_status": "release",
        "mode": "port", "game_id": score["game_id"],
        "score_scope": "mode5_evidence_adjusted_proxy",
        "score_mode": score["score_mode"], "visual_protocol": score["visual_mode"],
        "outcome_status": score["status"], "official_total": None,
        "paper_compatible": False, "ranking_scope": RANKING_SCOPE,
        "ranking_eligible": score["ranking_eligible"],
        "ranking_note": "Evidence-adjusted implementation/correspondence score, not perceptual similarity or a paper-equivalent total.",
        "components": score["components"], "categories": categories,
        "weighted_total": weighted, "headline": {**weighted,
            "ranking_eligible": score["ranking_eligible"], "ranking_scope": RANKING_SCOPE},
        "evidence_score": score, "evaluation_incomplete": score["total"] is None,
        "measured_weight_share": measured_weight / 100,
        "strict": {
            "resolved": resolved,
            "status": "passed" if resolved is True else "failed" if resolved is False else "not_measured",
            "failed_required_items": sorted(ident for ident in strict_ids if verdicts.get(ident) in {"failed", "malformed", "skipped", "exempt"}),
            "not_measured_required_items": sorted(ident for ident in strict_ids if verdicts.get(ident) not in {"passed", "failed", "malformed", "skipped", "exempt"}),
            "rule": "Proxy points cannot override the conjunctive runtime outcome.",
        },
    }


def missing_snapshot_scorecard(result: Any) -> dict[str, Any]:
    """Return an explicit unrankable envelope for incomplete retained reports.

    This is used only by the generic rescore command. It does not read the
    candidate or select an older rubric; direct Community scoring still raises
    when its controller snapshot is absent.
    """
    from .adapter import SNAPSHOT_SCHEMA
    snapshot = {
        "schema": SNAPSHOT_SCHEMA, "registry_version": REGISTRY_VERSION,
        "obligations": {name: ["unmeasured"] for name in CRITERIA},
        "observations": {name: [] for name in CRITERIA},
        "graph": {"complete": False}, "inspection_complete": False,
    }
    replacement = SimpleNamespace(
        package=result.package, items=result.items,
        engine={"mode5_static": snapshot, "build": {"status": "fail", "attribution": "harness"}},
        resolved=getattr(result, "resolved", None),
    )
    card = make_scorecard(replacement)
    card["ranking_eligible"] = False
    card["headline"]["ranking_eligible"] = False
    card["weighted_total"]["status"] = "infrastructure_incomplete"
    card["weighted_total"]["score"] = None
    card["evidence_score"]["missing_evidence"] = "pre-execution controller static snapshot"
    card["evaluation_incomplete"] = True
    return card


def render_scorecard(card: Mapping[str, Any]) -> str:
    if card.get("registry_version") != REGISTRY_VERSION:
        raise ValueError("wrong registry for Mode 5 release report rendering")
    reading = card["evidence_score"]
    total = reading["total"]
    lines = [
        f"# Mode 5 / {card['game_id']}", "",
        f"Registry: `{REGISTRY_VERSION}`; environment: `community-docker`.", "",
        f"Score mode: `{reading['score_mode']}`; status: `{reading['status']}`.",
        "No VLM. Visual measures implementation and reference correspondence; not perceptual appearance similarity. Paper compatibility is not asserted.", "",
        f"Proxy total: {'unrankable' if total is None else f'{total:.3f} / 100'}. "
        f"Earned proxy points: {reading['earned_proxy_points']:.3f}. Official total: not measured.",
        f"Strict runtime outcome: `{card['strict']['status']}` (separate from proxy points).", "",
        "| Component | Weight | Points | Percent |", "| --- | ---: | ---: | ---: |",
    ]
    for name, component in reading["components"].items():
        lines.append(f"| {name} | {component['weight']:g} | {component['points']:.3f} | {component['score']:.3f} |")
    for name, component in reading["components"].items():
        if component["cap_applied"]:
            lines.extend(["", f"{name}: non-runtime cap {component['non_runtime_cap']:g}; "
                          f"uncapped criterion sum {component['uncapped_points']:.3f}."])
    lines.extend(["", "## Evidence by obligation", ""])
    for row in reading["criteria"]:
        lines.extend([f"### {row['id']} — {row['points']:.3f} / {row['weight']:g}", ""])
        for evidence in row["evidence"]:
            refs = ", ".join(_safe_label(ref) for ref in evidence["references"]) or "no admissible evidence"
            lines.append(f"- {_safe_label(evidence['obligation'])}: {evidence['level']}; "
                         f"coefficient={evidence['coefficient']:.2f}; coverage={evidence['coverage']:.3f}; {refs}.")
        lines.append("")
    return "\n".join(lines)


def summarize_reports(
    reports: Sequence[Mapping[str, Any]], *, expected_games: Sequence[str],
) -> dict[str, Any]:
    """Full-catalog arithmetic model means, with separate tail diagnostics."""
    groups: dict[tuple[str, ...], list[Mapping[str, Any]]] = {}
    for report in reports:
        card = report.get("scorecard") or {}
        reading = card.get("evidence_score") or {}
        environment = report.get("environment") or {}
        config = report.get("run_config") or {}
        if (card.get("registry_version") != REGISTRY_VERSION or reading.get("schema") != SCHEMA
                or report.get("mode") != "port"
                or environment.get("environment_class") != "community-docker"):
            raise ValueError("release summary requires Community reports from the current registry")
        input_config = dict(config.get("input") or {})
        input_config.pop("game_id", None)
        key = (
            str(environment.get("profile_id") or "unknown"),
            str(environment.get("agent_image_id") or "unknown"),
            str(environment.get("evaluator_image_id") or "unknown"),
            str(config.get("harness") or "unknown"),
            str(config.get("model") or "unknown"),
            str(config.get("provider") or "unknown"),
            json.dumps(config.get("budget") or {}, sort_keys=True, separators=(",", ":")),
            json.dumps(input_config, sort_keys=True, separators=(",", ":")),
            "development" if config.get("development_unscored") is True else "release",
        )
        game = str(card.get("game_id") or report.get("game_id") or "")
        if reading.get("game_id") != game:
            raise ValueError("inconsistent game identity in v4 report")
        item = dict(reading)
        if config.get("development_unscored") is True:
            item.update(ranking_eligible=False)
        groups.setdefault(key, []).append(item)
    rows = []
    for key, records in sorted(groups.items()):
        aggregate = aggregate_model(records, expected_games=expected_games)
        complete = aggregate["total"] is not None
        rows.append({
            **aggregate, "mode": "port", "environment_class": "community-docker",
            "ranking_scope": RANKING_SCOPE if key[8] == "release" else "mode5-community-docker-v1-development",
            "environment_profile": key[0], "agent_image_id": key[1], "evaluator_image_id": key[2],
            "harness": key[3], "model": key[4], "provider": key[5],
            "budget_configuration": json.loads(key[6]), "input_configuration": json.loads(key[7]),
            "game_count": len(records),
            "scored": sum(record.get("ranking_eligible") is True and record.get("total") is not None for record in records),
            "unscored": sum(record.get("ranking_eligible") is not True or record.get("total") is None for record in records),
            "mean_score": aggregate["task_mean"],
            "partial_mean_score": _partial_mean(records),
            "reliability_score": aggregate["reliability_score"],
            "complete_mode_mean": aggregate["task_mean"] if complete else None,
            "expected_game_count": len(expected_games),
            "coverage": sum(record.get("ranking_eligible") is True and record.get("total") is not None for record in records) / len(expected_games),
            "games": [{"game_id": record["game_id"], "score": record["total"],
                       "ranking_eligible": record["ranking_eligible"]} for record in records],
        })
    return {"schema": "gamebench.mode5-community-summary.v2", "registry_version": REGISTRY_VERSION,
            "environment_class": "community-docker", "paper_compatible": False,
            "total": len(reports), "groups": rows}


def _partial_mean(records: Sequence[Mapping[str, Any]]) -> float | None:
    values = [record["total"] for record in records
              if record.get("ranking_eligible") is True and record.get("total") is not None]
    return sum(values) / len(values) if values else None


def _safe_label(value: str) -> str:
    import html
    text = html.escape(str(value)).replace("\n", " ").replace("\r", " ")
    for symbol in "\\`|[]()*_":
        text = text.replace(symbol, "\\" + symbol)
    return text
