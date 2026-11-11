"""Deterministic, model-independent Mode 5 evidence-adjusted proxy scoring.

Only evaluator-owned observations belong here. This module does not read a
candidate's claimed scores, invent dynamic credit, or execute candidate code.
The caller must keep the rubric and observation records outside the candidate
sandbox. A provenance string is an audit reference, not an authentication seal.
"""
from __future__ import annotations

from dataclasses import dataclass
import math
from typing import Any, Mapping, Sequence


REGISTRY_VERSION = "2026-10.mode5-evidence-five-visual1"
SCHEMA = "gamebench.mode5.evidence-score.v2"
EVIDENCE_COEFFICIENTS = {
    "runtime_verified": 1.0,
    "editor_verified": 0.8,
    "static_supported": 0.6,
    "file_presence_only": 0.25,
    "failed": 0.0,
}
COMPONENT_WEIGHTS = {
    "mechanics": 35.0, "playability": 25.0, "structure": 15.0,
    "visual": 15.0, "stability": 10.0,
}
# All obligations within a criterion have equal weight. The evaluator supplies
# their identities before observing the submission; candidates cannot shrink
# a denominator or choose their own easy obligations.
CRITERIA = {
    "mechanics.input_mapping": 6.0,
    "mechanics.core_mechanics": 12.0,
    "mechanics.interactions": 10.0,
    "mechanics.outcomes": 7.0,
    "playability.ops": 5.0,
    "playability.input_chain": 5.0,
    "playability.progression": 7.0,
    "playability.final_goal": 5.0,
    "playability.scene_flow": 3.0,
    "structure.scenes_entities": 5.0,
    "structure.referenced_content": 5.0,
    "structure.ui_hierarchy": 5.0,
    "visual.referenced_assets": 4.0,
    "visual.ui_feedback": 3.0,
    "visual.render_configuration": 3.0,
    "visual.animation_audio": 3.0,
    "visual.reference_layout": 2.0,
    "stability.packages_build": 4.0,
    "stability.lifecycle": 3.0,
    "stability.resource_safety": 3.0,
}
NON_RUNTIME_CAPS = {"mechanics": 24.5, "playability": 15.0}
LEVEL_ORDER = tuple(EVIDENCE_COEFFICIENTS)


@dataclass(frozen=True)
class Observation:
    """One independently checked obligation, not a free-form model judgement.

    Explicit failure of the *same* obligation dominates positive evidence.
    Split artifact configuration and actual runtime behavior into different
    obligations if a build failure should not erase a valid artifact fact.
    """
    obligation: str
    level: str
    references: tuple[str, ...] = ()
    note: str = ""
    coverage: float = 1.0
    verification: str | None = None

    def validate(self, criterion: str) -> None:
        if self.level not in EVIDENCE_COEFFICIENTS:
            raise ValueError(f"unknown evidence level: {self.level}")
        if (isinstance(self.coverage, bool) or not isinstance(self.coverage, (int, float))
                or not math.isfinite(self.coverage) or not 0 <= self.coverage <= 1):
            raise ValueError("invalid independently verified obligation coverage")
        if criterion == "playability.final_goal" and self.coverage not in {0, 1}:
            raise ValueError("a final goal has binary coverage")
        if not self.obligation or not isinstance(self.obligation, str):
            raise ValueError("an observation needs an obligation identity")
        if self.level != "failed" and not self.references:
            raise ValueError("positive evidence requires controller audit references")
        if any(not isinstance(ref, str) or not ref.strip() for ref in self.references):
            raise ValueError("invalid audit reference")
        if criterion.startswith("visual.") and self.level in {
            "runtime_verified", "editor_verified",
        }:
            from .visual_measure import VERIFICATIONS
            if (self.level != "runtime_verified" or self.verification != VERIFICATIONS[criterion]
                    or not all(ref.startswith("controller/visual/") for ref in self.references)):
                raise ValueError("visual verification requires a criterion-specific controller measurement; not verified appearance")
        if criterion == "playability.final_goal" and self.level not in {
            "runtime_verified", "failed",
        }:
            raise ValueError("a final goal requires independently observed runtime evidence")


def score_evidence(
    obligations: Mapping[str, Sequence[str]],
    observations: Mapping[str, Sequence[Observation]],
    *,
    runtime_attempted: bool = False,
    infrastructure_complete: bool = True,
    integrity_passed: bool = True,
) -> dict[str, Any]:
    """Score the complete frozen rubric; missing evidence never disappears.

    A failed candidate build leaves static proxy points intact. An evaluator
    infrastructure failure instead makes the headline unrankable, with earned
    proxy points retained only as diagnostics. Integrity failure is a hard gate.
    """
    if set(obligations) != set(CRITERIA):
        raise ValueError("release requires exactly the twenty published criteria")
    if set(observations) - set(CRITERIA):
        raise ValueError("unknown observation criterion")
    for criterion, ids in obligations.items():
        if (not ids or isinstance(ids, (str, bytes))
                or any(not isinstance(ident, str) or not ident for ident in ids)
                or len(ids) != len(set(ids))):
            raise ValueError(f"invalid frozen obligation denominator: {criterion}")
    rows = []
    runtime_verified = False
    runtime_components: set[str] = set()
    for criterion, weight in CRITERIA.items():
        expected = obligations[criterion]
        grouped: dict[str, list[Observation]] = {ident: [] for ident in expected}
        for observation in observations.get(criterion, ()):
            observation.validate(criterion)
            if observation.obligation not in grouped:
                raise ValueError(f"observation outside frozen rubric: {criterion}")
            grouped[observation.obligation].append(observation)
        evidence = []
        for ident in expected:
            candidates = grouped[ident]
            if any(value.level == "failed" for value in candidates):
                chosen = next(value for value in candidates if value.level == "failed")
            elif candidates:
                chosen = max(candidates, key=lambda value: (
                    EVIDENCE_COEFFICIENTS[value.level] * value.coverage,
                    -LEVEL_ORDER.index(value.level),
                ))
            else:
                chosen = Observation(ident, "failed", note="no admissible evidence")
            runtime_verified |= chosen.level == "runtime_verified" and chosen.coverage > 0
            if chosen.level == "runtime_verified" and chosen.coverage > 0:
                runtime_components.add(criterion.split(".")[0])
            evidence.append({
                "obligation": ident, "level": chosen.level,
                "coefficient": EVIDENCE_COEFFICIENTS[chosen.level],
                "coverage": chosen.coverage,
                "effective_credit": EVIDENCE_COEFFICIENTS[chosen.level] * chosen.coverage,
                "references": sorted(set(chosen.references)), "note": chosen.note,
                "observed": bool(candidates),
                "verification": chosen.verification,
            })
        coefficient = sum(value["effective_credit"] for value in evidence) / len(expected)
        rows.append({
            "id": criterion, "weight": weight, "denominator": len(expected),
            "points": weight * coefficient, "evidence": evidence,
        })
    components = {}
    for name, weight in COMPONENT_WEIGHTS.items():
        raw = sum(row["points"] for row in rows if row["id"].startswith(name + "."))
        cap = NON_RUNTIME_CAPS.get(name) if name not in runtime_components else None
        points = min(raw, cap) if cap is not None else raw
        components[name] = {
            "weight": weight, "uncapped_points": round(raw, 9),
            "points": round(points, 9), "score": round(100 * points / weight, 9),
            "non_runtime_cap": cap, "cap_applied": points < raw,
        }
    earned = sum(value["points"] for value in components.values())
    eligible = bool(infrastructure_complete and integrity_passed)
    return {
        "schema": SCHEMA, "registry_version": REGISTRY_VERSION,
        "score_mode": "evidence_adjusted_proxy" if runtime_verified else "static_offline_proxy",
        "visual_mode": "visual_implementation_correspondence", "vlm_runtime_verified": False,
        "runtime_attempted": runtime_attempted, "runtime_verified": runtime_verified,
        "environment_class": "community-docker", "paper_compatible": False,
        "criteria": rows, "components": components,
        "earned_proxy_points": round(earned, 9),
        "total": round(earned, 9) if eligible else None,
        "official_total": None, "ranking_eligible": eligible,
        "status": "scored_proxy" if eligible else (
            "integrity_failed" if not integrity_passed else "infrastructure_incomplete"
        ),
    }


def aggregate_model(
    reports: Sequence[Mapping[str, Any]], *, expected_games: Sequence[str],
) -> dict[str, Any]:
    """One common low-tail task set, with deterministic game-id tie breaking.

    The headline is the arithmetic task mean. Low-tail and 70/30 reliability
    are diagnostics only, using the same ceil(25% * N) lowest-total tasks.
    No complete leaderboard score is produced for a missing/unscored game.
    The CLI must additionally group by model, environment, harness and budget.
    """
    if not expected_games or len(expected_games) != len(set(expected_games)):
        raise ValueError("expected game catalog must be nonempty and unique")
    indexed: dict[str, Mapping[str, Any]] = {}
    for report in reports:
        game = report.get("game_id")
        if not isinstance(game, str) or game not in expected_games or game in indexed:
            raise ValueError("unknown or duplicate game in release summary")
        if report.get("registry_version") != REGISTRY_VERSION or report.get("schema") != SCHEMA:
            raise ValueError("cannot aggregate a different scoring protocol")
        indexed[game] = report
    missing = sorted(set(expected_games) - set(indexed))
    unscored = sorted(game for game, row in indexed.items()
                      if row.get("ranking_eligible") is not True or row.get("total") is None)
    base: dict[str, Any] = {
        "registry_version": REGISTRY_VERSION, "expected": len(expected_games),
        "present": len(indexed), "missing_games": missing, "unscored_games": unscored,
        "aggregation": "task_arithmetic_mean",
        "reliability_formula": "0.7*task_mean+0.3*lowest_quartile_mean",
        "paper_compatible": False, "total": None, "task_mean": None,
        "lowest_quartile_mean": None, "reliability_score": None, "components": {}, "tail_games": [],
    }
    for row in indexed.values():
        if row.get("ranking_eligible") is not True or row.get("total") is None:
            continue
        total = _finite_score(row["total"])
        component_total = 0.0
        if set(row.get("components") or {}) != set(COMPONENT_WEIGHTS):
            raise ValueError("incomplete component record")
        for name, weight in COMPONENT_WEIGHTS.items():
            component = row["components"][name]
            if component.get("weight") != weight:
                raise ValueError("component weight drift")
            score = _finite_score(component.get("score"))
            component_total += weight * score / 100
        if not math.isclose(total, component_total, abs_tol=1e-7):
            raise ValueError("task total does not reconcile with its five components")
    if missing or unscored:
        return base
    ordered = sorted(indexed, key=lambda game: (indexed[game]["total"], game))
    tail = ordered[:math.ceil(len(expected_games) * 0.25)]
    mean = sum(indexed[game]["total"] for game in ordered) / len(ordered)
    tail_mean = sum(indexed[game]["total"] for game in tail) / len(tail)
    for name, weight in COMPONENT_WEIGHTS.items():
        component_mean = sum(indexed[game]["components"][name]["score"] for game in ordered) / len(ordered)
        component_tail = sum(indexed[game]["components"][name]["score"] for game in tail) / len(tail)
        base["components"][name] = {
            "weight": weight, "task_mean": round(component_mean, 9),
            "lowest_quartile_mean": round(component_tail, 9),
            "score": round(component_mean, 9),
            "reliability_score": round(0.7 * component_mean + 0.3 * component_tail, 9),
        }
    base.update(total=round(mean, 9), reliability_score=round(0.7 * mean + 0.3 * tail_mean, 9),
                task_mean=round(mean, 9), lowest_quartile_mean=round(tail_mean, 9),
                tail_games=tail)
    return base


def _finite_score(value: Any) -> float:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise ValueError("a score must be a finite number, not a flag")
    if not math.isfinite(value) or not 0 <= value <= 100:
        raise ValueError("score outside [0, 100]")
    return float(value)
