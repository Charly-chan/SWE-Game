


from __future__ import annotations

import json
import re
from dataclasses import dataclass, fields, replace
from pathlib import Path
from typing import Any, Iterable, Mapping, Sequence

from ..routes.schema import Route
from ..verdict import (
    Attribution, Interval, Item, Verdict, score_items, failed, inconclusive, passed,
)
from .content.verifier_profiles import verifier_profile
from .content.rubrics import rubric_milestones


def _walk_evidence(value: Any):

    if isinstance(value, Mapping):
        yield value
        for nested in value.values():
            yield from _walk_evidence(nested)
    elif isinstance(value, (list, tuple)):
        for nested in value:
            yield from _walk_evidence(nested)


PILOT_REGISTRY_VERSION = "2026-09-02.pilot2"
CALIB_REGISTRY_VERSION = "2026-09-04.calib3"
CALIB4_REGISTRY_VERSION = "2026-09-04.calib4"


MODE4_REGISTRY_VERSION = "2026-09-05.mode4"


MODE34_REGISTRY_VERSION = "2026-09-08.mode34"


MODE34B_REGISTRY_VERSION = "2026-09-10.mode34b"
EVIDENCE_REGISTRY_VERSION = "2026-09-11.evidence1"
VISUAL_REGISTRY_VERSION = "2026-09-11.visual1"
MODE1_REDESIGN_REGISTRY_VERSION = "2026-09-15.mode1-redesign1"
MODE2_REDESIGN_REGISTRY_VERSION = "2026-09-15.mode2-redesign1"
MODE3_REDESIGN_REGISTRY_VERSION = "2026-09-15.mode3-redesign1"
MODE1_VLM_REGISTRY_VERSION = "2026-09-19.mode1-vlm1"
MODE2_VLM_REGISTRY_VERSION = "2026-09-19.mode2-vlm1"
MODE3_VLM_REGISTRY_VERSION = "2026-09-19.mode3-vlm1"
MODE4_REDESIGN_REGISTRY_VERSION = "2026-09-15.mode4-redesign1"
MODE4_F2P_P2P_REGISTRY_VERSION = "2026-09-15.mode4-f2p-p2p1"
REGISTRY_VERSION = EVIDENCE_REGISTRY_VERSION


MODE5_REGISTRY_VERSION = "2026-09-11.mode5-capability-v1"


MODE5_DELIVERY_ZERO_REGISTRY_VERSION = "2026-09-19.mode5-delivery-zero1"
MODE5_MDVA_REGISTRY_VERSION = "2026-09-20.mode5-mdva-domain1"
HISTORICAL_MODE5_REGISTRIES = (
    MODE5_REGISTRY_VERSION,
    MODE5_DELIVERY_ZERO_REGISTRY_VERSION,
)
REGISTRY_VERSIONS = (
    PILOT_REGISTRY_VERSION,
    CALIB_REGISTRY_VERSION,
    CALIB4_REGISTRY_VERSION,
    MODE4_REGISTRY_VERSION,
    MODE34_REGISTRY_VERSION,
    MODE34B_REGISTRY_VERSION,
    EVIDENCE_REGISTRY_VERSION,
    VISUAL_REGISTRY_VERSION,
    MODE1_REDESIGN_REGISTRY_VERSION,
    MODE2_REDESIGN_REGISTRY_VERSION,
    MODE3_REDESIGN_REGISTRY_VERSION,
    MODE1_VLM_REGISTRY_VERSION,
    MODE2_VLM_REGISTRY_VERSION,
    MODE3_VLM_REGISTRY_VERSION,
    MODE4_REDESIGN_REGISTRY_VERSION,
    MODE4_F2P_P2P_REGISTRY_VERSION,
    MODE5_REGISTRY_VERSION,
    MODE5_DELIVERY_ZERO_REGISTRY_VERSION,
    MODE5_MDVA_REGISTRY_VERSION,
)
SCORECARD_SCHEMA = "gamebench.taskgen.scorecard.v2"
MODE5_SCORECARD_SCHEMA = "gamebench.mode5.capability-scorecard.v1"
MIN_RANKING_COVERAGE = 0.80


GRADED_CHANNEL_EXPONENT = 2.0


BRIEF_NOT_APPLICABLE: dict[tuple[str, str], str] = {
    ("causal_playability", "end_to_end_route"): (
        "brief mode: the submission designs its own game, so the reference's "
        "authored whole-game route (O7) is not a contract it could meet; the "
        "reading stays on the O-card, its 30 causal points leave the denominator "
        "(renormalisation is worth the same as passing)"
    ),
    ("structure_content", "spatial_relations"): (
        "brief mode: the statement names no layout constraint, so dispersion "
        "against the reference layout (O5) is reported, not scored; its 30 "
        "structure points leave the denominator"
    ),
}

_SHIPPED_BY_SCAFFOLD = (
    "skeleton mode: the unmodified scaffold already ships {what}; a submission "
    "cannot earn it, so the reading is reported and its {weight} artifact points "
    "leave the denominator (renormalisation is worth the same as passing)"
)
_SHIPPED_BY_FAULTY_BUILD = (
    "bugfix mode: the unmodified faulty build already ships {what}; the reading is "
    "reported and its {weight} {category} points leave the denominator; a fix that "
    "breaks it is charged through the regression readings, not here"
)


SKELETON_NOT_APPLICABLE: dict[tuple[str, str], str] = {
    ("artifact_observability", "project_integrity"):
        _SHIPPED_BY_SCAFFOLD.format(what="the project layout", weight=10),
    ("artifact_observability", "evaluator_addressability"):
        _SHIPPED_BY_SCAFFOLD.format(
            what="the evaluator interface (gb_* surface, gb_levels shape, O2)", weight=20),
    ("structure_content", "topology_progression"): (
        "skeleton mode: the unmodified scaffold already ships the level list and "
        "one scene per reference level, so level count and the inter-level digraph "
        "(O4) read 1.0 before any work is done; the reading is reported and its 30 "
        "structure points leave the denominator (renormalisation is worth the same "
        "as passing)"
    ),
}


SKELETON_NOOP_HEADLINE_CAP = 25.0


BUGFIX_NOT_APPLICABLE: dict[tuple[str, str], str] = {
    ("artifact_observability", "project_integrity"):
        _SHIPPED_BY_FAULTY_BUILD.format(what="the project layout", weight=10, category="artifact"),
    ("artifact_observability", "runtime_viability"):
        _SHIPPED_BY_FAULTY_BUILD.format(what="a launchable build (O1)", weight=40, category="artifact"),
    ("artifact_observability", "evaluator_addressability"):
        _SHIPPED_BY_FAULTY_BUILD.format(
            what="the evaluator interface (O2)", weight=20, category="artifact"),
    ("mechanics_requirements", "deterministic_mechanics"): (
        "bugfix mode: O3 is an absolute reading of the reference's universal assertions "
        "(the faulty build and the source both fail some rungs), so it would charge a "
        "clean revert for what the reference lacks; reported, not scored -- regressions "
        "are charged through repair_restoration (a broken preserved route earns no "
        "restoration credit) and feature_kept"
    ),
    ("structure_content", "topology_progression"):
        _SHIPPED_BY_FAULTY_BUILD.format(what="the levels (O4)", weight=30, category="structure"),
    ("structure_content", "spatial_relations"):
        _SHIPPED_BY_FAULTY_BUILD.format(what="the layout (O5)", weight=30, category="structure"),
    ("structure_content", "asset_realization"):
        _SHIPPED_BY_FAULTY_BUILD.format(what="the asset use (O6)", weight=40, category="structure"),
    ("visual_experience", "composition"):
        _SHIPPED_BY_FAULTY_BUILD.format(what="the composition (O9)", weight=25, category="visual"),
    ("visual_experience", "calibrated_surface"): (
        "bugfix mode: a repair has no visual deliverable, so the perceptual S-card is off "
        "(not applicable, not unearned weight); the headline ceiling is 100"
    ),
    ("mode_specific", "publication"): (
        "bugfix mode: bugfix_publication is the package-side source-pass -> mutant-fail "
        "preflight of the evaluator's own certified defect, constant across submissions; "
        "reported, not scored"
    ),
}


MODE4_REPAIR_CREDIT = ("causal_playability", "repair_restoration")
MODE4_GATES: tuple[tuple[str, str], ...] = (
    ("artifact_observability", "integrity_controls"),
    ("causal_playability", "matched_null"),
    ("causal_playability", "anti_grant_controls"),
    ("mode_specific", "no_smuggling"),
)
MODE4_REGRESSION_WEIGHTS: dict[tuple[str, str], float] = {
    ("mechanics_requirements", "feature_preservation"): 40,
    ("mode_specific", "product_surface"): 15,
    ("mode_specific", "transformation"): 15,
}
MODE4_REDESIGN_GATES: tuple[tuple[str, str], ...] = (
    ("artifact_observability", "project_integrity"),
    ("artifact_observability", "runtime_viability"),
    ("artifact_observability", "evaluator_addressability"),
    *MODE4_GATES,
)
MODE4_REDESIGN_REGRESSION_WEIGHTS: dict[tuple[str, str], float] = {
    ("mechanics_requirements", "feature_preservation"): 50,
    ("mode_specific", "product_surface"): 25,
    ("mode_specific", "transformation"): 25,
}


MODE4_PROVISIONAL_TIER_POINTS: dict[str, float] = {
    "easy": 1.0,
    "medium": 2.0,
    "hard": 3.0,
}


_LAYOUT_CONSTRAINT_WORDS = (
    "layout", "spread", "spacing", "dispers", "scatter", "spatial",
    "vertical", "horizontal", "far apart", "distributed", "bands",
    "placement", "arranged across", "cover the",
)


def brief_layout_constraint(statement: str) -> str:


    text = statement or ""
    for sentence in re.split(r"(?<=[.!?。])\s+|\n+", text):
        lowered = sentence.lower()
        if any(word in lowered for word in _LAYOUT_CONSTRAINT_WORDS):
            return sentence.strip()
    return ""


@dataclass(frozen=True)
class SourceSpec:
    kind: str
    id: str


@dataclass(frozen=True)
class CriterionSpec:
    id: str
    name: str
    weight: float
    sources: tuple[SourceSpec, ...]

    credit_exponent: float = 1.0


@dataclass(frozen=True)
class CategorySpec:
    id: str
    name: str
    weight: float
    criteria: tuple[CriterionSpec, ...]


@dataclass(frozen=True)
class RegistryPolicy:


    version: str
    status: str


    scard_unearned: bool

    register_o8: bool

    graded_exponent: float


    brief_own_structure: bool = False


    gdd_mechanics_observable: bool = False


    o6_linear: bool = False


    mode4_single_restoration: bool = False


    mode34_applicability: bool = False

    mode4_graded: bool = False

    evidence_consistency: bool = False

    task_visual: bool = False


    mode5_capability_only: bool = False


    mode1_redesign: bool = False

    progressive_redesign_mode: str = ""


    mode4_f2p_p2p: bool = False


    mode5_delivery_zero: bool = False


    mode5_deliverable_gates_nonfatal: bool = False


    mode5_mdva_domain: bool = False


_POLICIES: dict[str, RegistryPolicy] = {
    PILOT_REGISTRY_VERSION: RegistryPolicy(
        version=PILOT_REGISTRY_VERSION,
        status="pilot_not_blind_formal_weights",
        scard_unearned=False,
        register_o8=True,
        graded_exponent=1.0,
    ),
    CALIB_REGISTRY_VERSION: RegistryPolicy(
        version=CALIB_REGISTRY_VERSION,
        status="calibrated_on_live0903_not_blind",
        scard_unearned=True,
        register_o8=False,
        graded_exponent=GRADED_CHANNEL_EXPONENT,
    ),
    CALIB4_REGISTRY_VERSION: RegistryPolicy(
        version=CALIB4_REGISTRY_VERSION,
        status="calibrated_on_live0903_live0904_not_blind",
        scard_unearned=True,
        register_o8=False,
        graded_exponent=GRADED_CHANNEL_EXPONENT,
        brief_own_structure=True,
        gdd_mechanics_observable=True,
        o6_linear=True,
    ),
    MODE4_REGISTRY_VERSION: RegistryPolicy(
        version=MODE4_REGISTRY_VERSION,
        status="calib4_rules_plus_mode4_single_restoration_not_blind",
        scard_unearned=True,
        register_o8=False,
        graded_exponent=GRADED_CHANNEL_EXPONENT,
        brief_own_structure=True,
        gdd_mechanics_observable=True,
        o6_linear=True,
        mode4_single_restoration=True,
    ),
    MODE34_REGISTRY_VERSION: RegistryPolicy(
        version=MODE34_REGISTRY_VERSION,
        status="mode4_rules_plus_mode34_applicability_not_blind",
        scard_unearned=True,
        register_o8=False,
        graded_exponent=GRADED_CHANNEL_EXPONENT,
        brief_own_structure=True,
        gdd_mechanics_observable=True,
        o6_linear=True,
        mode4_single_restoration=True,
        mode34_applicability=True,
    ),
    MODE34B_REGISTRY_VERSION: RegistryPolicy(
        version=MODE34B_REGISTRY_VERSION,
        status="mode34_rules_plus_graded_mode4_not_blind",
        scard_unearned=True,
        register_o8=False,
        graded_exponent=GRADED_CHANNEL_EXPONENT,
        brief_own_structure=True,
        gdd_mechanics_observable=True,
        o6_linear=True,
        mode4_single_restoration=True,
        mode34_applicability=True,
        mode4_graded=True,
    ),
    MODE5_REGISTRY_VERSION: RegistryPolicy(
        version=MODE5_REGISTRY_VERSION,
        status="mode5_capability_design_not_blind_calibrated",
        scard_unearned=False,
        register_o8=False,
        graded_exponent=1.0,
        mode5_capability_only=True,
    ),
}

_POLICIES[EVIDENCE_REGISTRY_VERSION] = replace(
    _POLICIES[MODE34B_REGISTRY_VERSION],
    version=EVIDENCE_REGISTRY_VERSION,
    status="mode34b_plus_evidence_consistency_not_blind",
    evidence_consistency=True,
)
_POLICIES[VISUAL_REGISTRY_VERSION] = replace(
    _POLICIES[EVIDENCE_REGISTRY_VERSION], version=VISUAL_REGISTRY_VERSION,
    status="task_visual_vlm_not_independently_validated", task_visual=True,
)
_POLICIES[MODE1_REDESIGN_REGISTRY_VERSION] = replace(
    _POLICIES[EVIDENCE_REGISTRY_VERSION],
    version=MODE1_REDESIGN_REGISTRY_VERSION,
    status="mode1_fixed_weights_visual_placeholder_not_blind",
    mode1_redesign=True,
)
for _version, _mode in (
    (MODE2_REDESIGN_REGISTRY_VERSION, "gdd"),
    (MODE3_REDESIGN_REGISTRY_VERSION, "skeleton"),
    (MODE4_REDESIGN_REGISTRY_VERSION, "bugfix"),
):
    _POLICIES[_version] = replace(
        _POLICIES[EVIDENCE_REGISTRY_VERSION],
        version=_version,
        status=f"{_mode}_progressive_redesign_not_blind",
        progressive_redesign_mode=_mode,
    )
_POLICIES[MODE4_F2P_P2P_REGISTRY_VERSION] = replace(
    _POLICIES[MODE4_REDESIGN_REGISTRY_VERSION],
    version=MODE4_F2P_P2P_REGISTRY_VERSION,
    status="mode4_deterministic_f2p_p2p_not_blind",
    mode4_f2p_p2p=True,
)


for _base, _vlm in (
    (MODE1_REDESIGN_REGISTRY_VERSION, MODE1_VLM_REGISTRY_VERSION),
    (MODE2_REDESIGN_REGISTRY_VERSION, MODE2_VLM_REGISTRY_VERSION),
    (MODE3_REDESIGN_REGISTRY_VERSION, MODE3_VLM_REGISTRY_VERSION),
):
    _POLICIES[_vlm] = replace(_POLICIES[_base], version=_vlm, task_visual=True,
                             status="game_rubric_vlm_not_independently_calibrated")


def registry_policy(version: str | None = None, *, mode: str | None = None) -> RegistryPolicy:
    key = version or REGISTRY_VERSION
    try:
        policy = _POLICIES[key]
    except KeyError:
        raise ValueError(
            f"unknown scorecard registry version {key!r}; loadable: {REGISTRY_VERSIONS}"
        ) from None
    if mode and policy.task_visual and (policy.mode1_redesign or policy.progressive_redesign_mode):
        target = "brief" if policy.mode1_redesign else policy.progressive_redesign_mode
        if mode != target:
            raise ValueError(f"registry {key!r} is defined only for mode {target}")
    return policy


_WEIGHTS: dict[str, dict[str, float]] = {
    PILOT_REGISTRY_VERSION: {
        "artifact": 15, "mechanics": 25, "causal": 25, "structure": 15, "visual": 10, "mode": 10,
        "project_integrity": 15, "runtime_viability": 25, "evaluator_addressability": 25,
        "witness_contract": 15, "integrity_controls": 20,

        "verified_witness": 30, "matched_null": 20, "anti_grant_controls": 20,
        "end_to_end_route": 20, "external_play": 10,
        "topology_progression": 35, "spatial_relations": 30, "asset_realization": 35,
        "composition": 37.5, "calibrated_surface": 62.5,
    },
    CALIB_REGISTRY_VERSION: {
        "artifact": 10, "mechanics": 30, "causal": 25, "structure": 15, "visual": 15, "mode": 5,
        "project_integrity": 10, "runtime_viability": 40, "evaluator_addressability": 20,
        "witness_contract": 10, "integrity_controls": 20,
        "verified_witness": 50, "matched_null": 10, "anti_grant_controls": 10,
        "end_to_end_route": 30, "external_play": 0,
        "topology_progression": 30, "spatial_relations": 30, "asset_realization": 40,
        "composition": 25, "calibrated_surface": 75,
    },
}

_WEIGHTS[CALIB4_REGISTRY_VERSION] = dict(_WEIGHTS[CALIB_REGISTRY_VERSION])


_WEIGHTS[MODE4_REGISTRY_VERSION] = dict(_WEIGHTS[CALIB_REGISTRY_VERSION])
_WEIGHTS[MODE5_REGISTRY_VERSION] = dict(_WEIGHTS[CALIB_REGISTRY_VERSION])


_WEIGHTS[MODE5_DELIVERY_ZERO_REGISTRY_VERSION] = dict(_WEIGHTS[MODE5_REGISTRY_VERSION])


_WEIGHTS[MODE5_MDVA_REGISTRY_VERSION] = dict(_WEIGHTS[MODE5_REGISTRY_VERSION])
_POLICIES[MODE5_DELIVERY_ZERO_REGISTRY_VERSION] = replace(
    _POLICIES[MODE5_REGISTRY_VERSION],
    version=MODE5_DELIVERY_ZERO_REGISTRY_VERSION,
    status="mode5_capability_with_candidate_delivery_zero_not_blind_calibrated",
    mode5_delivery_zero=True,
    mode5_deliverable_gates_nonfatal=True,
)
_POLICIES[MODE5_MDVA_REGISTRY_VERSION] = replace(
    _POLICIES[MODE5_DELIVERY_ZERO_REGISTRY_VERSION],
    version=MODE5_MDVA_REGISTRY_VERSION,
    status="mode5_objective70_plus_structure15_plus_mdva15_corpus_calibrated",
    mode5_mdva_domain=True,
)


_WEIGHTS[MODE34_REGISTRY_VERSION] = dict(_WEIGHTS[CALIB_REGISTRY_VERSION])
_WEIGHTS[MODE34B_REGISTRY_VERSION] = dict(_WEIGHTS[MODE34_REGISTRY_VERSION])
_WEIGHTS[EVIDENCE_REGISTRY_VERSION] = dict(_WEIGHTS[MODE34B_REGISTRY_VERSION])
_WEIGHTS[VISUAL_REGISTRY_VERSION] = dict(_WEIGHTS[EVIDENCE_REGISTRY_VERSION])
_WEIGHTS[MODE1_REDESIGN_REGISTRY_VERSION] = dict(_WEIGHTS[EVIDENCE_REGISTRY_VERSION])
_WEIGHTS[MODE2_REDESIGN_REGISTRY_VERSION] = dict(_WEIGHTS[EVIDENCE_REGISTRY_VERSION])
_WEIGHTS[MODE3_REDESIGN_REGISTRY_VERSION] = dict(_WEIGHTS[EVIDENCE_REGISTRY_VERSION])
_WEIGHTS[MODE4_REDESIGN_REGISTRY_VERSION] = dict(_WEIGHTS[EVIDENCE_REGISTRY_VERSION])
_WEIGHTS[MODE4_F2P_P2P_REGISTRY_VERSION] = dict(_WEIGHTS[MODE4_REDESIGN_REGISTRY_VERSION])
for _version in (MODE1_VLM_REGISTRY_VERSION, MODE2_VLM_REGISTRY_VERSION, MODE3_VLM_REGISTRY_VERSION):
    _WEIGHTS[_version] = dict(_WEIGHTS[EVIDENCE_REGISTRY_VERSION])


def _item_source(*ids: str) -> tuple[SourceSpec, ...]:
    return tuple(SourceSpec("item", item_id) for item_id in ids)


def _ocard_source(*ids: str) -> tuple[SourceSpec, ...]:
    return tuple(SourceSpec("ocard", channel_id) for channel_id in ids)


def _criterion(
    id: str,
    name: str,
    weight: float,
    *sources: SourceSpec,
    credit_exponent: float = 1.0,
) -> CriterionSpec:
    return CriterionSpec(
        id=id, name=name, weight=weight, sources=tuple(sources), credit_exponent=credit_exponent,
    )


def _category_specs(mode: str, version: str | None = None) -> tuple[CategorySpec, ...]:


    policy = registry_policy(version)
    if policy.mode5_capability_only:
        if mode != "port":
            raise ValueError(
                f"registry {policy.version!r} is defined only for Mode 5 / port"
            )
        return (
            CategorySpec(
                "core_mechanics",
                "Core mechanics and semantic fidelity",
                35,
                (
                    _criterion(
                        "mechanic_obligations",
                        "Evaluator-observed, source-grounded mechanic obligations",
                        100,
                        *_item_source("unity_mechanic_trace"),
                    ),
                ),
            ),
            CategorySpec(
                "playability_progression",
                "End-to-end playability and progression",
                25,
                (
                    _criterion(
                        "whole_game_completion",
                        "Player-caused whole-game completion witness",
                        40,
                        *_item_source("causal_witness"),
                    ),
                    _criterion(
                        "hidden_behavior_generalisation",
                        "Evaluator-private behavior and progression scenarios",
                        60,
                        *_item_source("unity_hidden_behavior"),
                    ),
                ),
            ),
            CategorySpec(
                "content_structure",
                "Content and spatial-structure fidelity",
                15,
                (
                    _criterion(
                        "cross_engine_structure",
                        "Cross-engine content, identity, and progression fidelity",
                        100,
                        *_item_source("unity_structure_fidelity"),
                    ),
                ),
            ),
            CategorySpec(
                "visual_feedback",
                "Visual quality",
                15,
                (
                    _criterion(
                        "mdva_visual_quality" if policy.mode5_mdva_domain
                        else "cross_engine_visual_feedback",
                        "Game-rubric visual quality" if policy.mode5_mdva_domain
                        else "Cross-engine visual, UI, and feedback fidelity",
                        100,
                        *_item_source("unity_vlm" if policy.mode5_mdva_domain
                                      else "unity_visual_fidelity"),
                    ),
                ),
            ),
            CategorySpec(
                "stability_lifecycle",
                "Runtime stability and lifecycle completeness",
                10,
                (
                    _criterion(
                        "clean_cold_runs",
                        "Clean independent candidate cold runs",
                        100,
                        *_item_source("unity_runtime_stability"),
                    ),
                ),
            ),
        )
    w = _WEIGHTS[policy.version]
    graded = policy.graded_exponent
    unity = mode == "port"
    repair = mode == "bugfix"

    artifact = [
        _criterion(
            "project_integrity", "Project/package integrity", w["project_integrity"],
            *_item_source("unity_layout" if unity else "layout"),
        ),
        _criterion(
            "runtime_viability", "Build, launch, and non-trivial runtime", w["runtime_viability"],
            *(_item_source("unity_build") if unity else _ocard_source("O1")),
            credit_exponent=1.0 if unity else graded,
        ),
        _criterion(
            "evaluator_addressability", "Evaluator addressability", w["evaluator_addressability"],
            *(
                _item_source("unity_interface")
                if unity else _item_source("interface") + _ocard_source("O2")
            ),
        ),
    ]
    if not repair:
        artifact.append(_criterion(
            "witness_contract", "Submitted operation/witness contract", w["witness_contract"],
            *_item_source("ops_present", "ops_valid", "ops_not_idle"),
        ))
    integrity_ids = (
        ("no_eval_smuggling", "no_bundled_godot_runtime", "verifier_profile_complete")
        if unity else
        ("anti_grant_static", "auto_win_ready", "verifier_profile_complete")
    )
    artifact.append(_criterion(
        "integrity_controls", "Anti-grant and verifier-integrity controls", w["integrity_controls"],
        *_item_source(*integrity_ids),
    ))

    mechanics = [
        _criterion(
            "deterministic_mechanics", "Reference-grounded deterministic mechanics", 60,
            *_ocard_source("O3"),
            credit_exponent=graded,
        ),
    ]
    if mode in {"brief", "gdd", "skeleton"}:
        mechanics.append(_criterion(
            "task_checkpoints", "Task-specific mechanic checkpoints", 40,
            *_item_source("mechanic_trace"),
        ))
    elif repair:
        mechanics.append(_criterion(
            "feature_preservation", "Non-target feature preservation", 40,
            *_item_source("feature_kept"),
        ))
    else:
        mechanics.append(_criterion(
            "unity_state_probe", "Unity runtime state and mechanic probe", 40,
            *_item_source("unity_probe"),
        ))
        if policy.task_visual:


            mechanics = mechanics[1:]

    if mode in {"brief", "gdd", "skeleton"}:
        causal_list = [
            _criterion("verified_witness", "Action-caused goal witness", w["verified_witness"],
                       *_item_source("causal_witness")),
            _criterion("matched_null", "Matched-horizon no-action control", w["matched_null"],
                       *_item_source("null_no_win")),
            _criterion("anti_grant_controls", "Mash and harness counterfactuals", w["anti_grant_controls"],
                       *_item_source("extended_mash_no_win", "anti_grant_diff")),
            _criterion("end_to_end_route", "Evaluator end-to-end route", w["end_to_end_route"],
                       *_ocard_source("O7"), credit_exponent=graded),
        ]
        if policy.register_o8:
            causal_list.append(_criterion(
                "external_play", "Independent external play", w["external_play"],
                *_ocard_source("O8"),
            ))
        causal = tuple(causal_list)
    elif repair and policy.mode4_single_restoration:


        causal_list = [
            _criterion("repair_restoration", "Failure-to-pass target restoration (graded)", 70,
                       *_item_source("repair_restoration_graded" if policy.mode4_graded else "repair_restoration")),
            _criterion("matched_null", "No-action control", 10,
                       *_item_source("null_no_win")),
            _criterion("anti_grant_controls", "Mash and harness counterfactuals", 10,
                       *_item_source("extended_mash_no_win", "anti_grant_diff")),
        ]
        if policy.register_o8:
            causal_list.append(_criterion(
                "external_play", "Independent external play", 10, *_ocard_source("O8"),
            ))
        causal = tuple(causal_list)
    elif repair:
        causal_list = [
            _criterion("repair_target", "Failure-to-pass target restoration", 40,
                       *_item_source("repair_differential")),
            _criterion("gold_replay", "Honest repaired-product replay", 30,
                       *_item_source("gold_replay")),
            _criterion("matched_null", "No-action control", 10,
                       *_item_source("null_no_win")),
            _criterion("anti_grant_controls", "Mash and harness counterfactuals", 10,
                       *_item_source("extended_mash_no_win", "anti_grant_diff")),
        ]
        if policy.register_o8:
            causal_list.append(_criterion(
                "external_play", "Independent external play", 10, *_ocard_source("O8"),
            ))
        causal = tuple(causal_list)
    else:
        causal = (
            _criterion("unity_route", "Evaluator-hidden Unity route", 35,
                       *_item_source("unity_route_replay")),
            _criterion("verified_witness", "Action-caused Unity witness", 25,
                       *_item_source("causal_witness")),
            _criterion("matched_null", "Matched-horizon no-action control", 20,
                       *_item_source("null_no_win")),
            _criterion("anti_grant_controls", "Extended-action negative control", 20,
                       *_item_source("extended_mash_no_win")),
        )

    if unity:
        structure = (
            _criterion("cross_engine_structure", "Cross-engine structure/content preservation", 100,
                       *_item_source("cross_engine_fidelity")),
        )
        visual = (
            _criterion("evaluator_capture", "Evaluator-owned Unity capture", 25,
                       *_item_source("unity_evaluator_capture")),
            _criterion("paired_visual_fidelity", "Reference-conditioned visual quality"
                       if policy.evidence_consistency else "Paired cross-engine visual fidelity", 75,
                       *_item_source("unity_vlm")),
        )
    else:
        structure = (
            _criterion("topology_progression", "Level topology and progression",
                       w["topology_progression"], *_ocard_source("O4"), credit_exponent=graded),
            _criterion("spatial_relations", "Scale-invariant spatial relations",
                       w["spatial_relations"], *_ocard_source("O5"), credit_exponent=graded),


            _criterion("asset_realization", "Evaluator-observed supplied-asset use",
                       w["asset_realization"], *_ocard_source("O6"),
                       credit_exponent=1.0 if policy.o6_linear else graded),
        )
        visual = (
            _criterion("composition", "Objective composition/readability", w["composition"],
                       *_ocard_source("O9")),
            _criterion("calibrated_surface", "Calibrated surface/UI/atmosphere/feel",
                       w["calibrated_surface"], SourceSpec("scard", "calibrated_total")),
        )

    if policy.task_visual and not repair:
        visual = (visual[0], _criterion(
            "task_visual", "Task-conditioned demonstration visual quality", 75,
            *_item_source("task_visual"),
        ))

    mode_criteria: dict[str, tuple[CriterionSpec, ...]] = {
        "brief": (
            _criterion("gdd_present", "Candidate-authored GDD present", 10,
                       *_item_source("gdd")),
            _criterion("gdd_quality", "Authored GDD quality", 30,
                       *_item_source("authored_gdd_quality")),
            _criterion("gdd_interface", "Authored GDD observable contract", 20,
                       *_item_source("authored_gdd_interface")),
            _criterion("brief_grounding", "Brief/video/asset grounding", 40,
                       *_item_source("brief_gdd_grounding")),
        ),
        "gdd": (


            _criterion("gdd_mechanics_observable",
                       "Task-GDD mechanics observable in the submission", 100,
                       *_item_source("gdd_mechanics_observable"))
            if policy.gdd_mechanics_observable else
            _criterion("task_gdd", "Published Task-GDD compliance", 100,
                       *_item_source("task_gdd_contract")),
        ),
        "skeleton": (
            _criterion("scaffold_integrity", "Scaffold integration preservation", 30,
                       *_item_source("skeleton_integrity")),
            _criterion("stub_completion", "Required stub completion", 30,
                       *_item_source("stub_completion")),
            _criterion("transformation", "Hidden transformation contract", 40,
                       *_item_source("transformation_contract")),
        ),
        "bugfix": tuple(
            criterion for criterion in (
                _criterion("publication", "Certified defect publication contract", 20,
                           *_item_source("bugfix_publication")),
                _criterion("no_smuggling", "No evaluator material smuggling", 15,
                           *_item_source("no_eval_smuggling")),
                _criterion("product_surface", "Product surface preserved", 20,
                           *_item_source("surface")),


                None if policy.mode34_applicability else
                _criterion("feature_contract", "Public feature contract preserved", 20,
                           *_item_source("feature_kept")),
                _criterion("transformation", "Hidden transformation contract", 25,
                           *_item_source("transformation_contract")),
            ) if criterion is not None
        ),
        "port": tuple(criterion for criterion in (
            _criterion("task_gdd", "Published Task-GDD compliance", 15,
                       *_item_source("task_gdd_contract")),
            _criterion("port_alignment", "Godot-to-Unity port contract alignment", 25,
                       *_item_source("port_contract_alignment")),
            _criterion("no_smuggling", "No evaluator material smuggling", 15,
                       *_item_source("no_eval_smuggling")),
            _criterion("no_bundled_runtime", "No bundled Godot runtime", 15,
                       *_item_source("no_bundled_godot_runtime")),
            _criterion("build_recipe", "Reproducible Unity build recipe", 10,
                       *_item_source("build_recipe")),
            None if policy.evidence_consistency else
            _criterion("cross_engine_fidelity", "Cross-engine semantic preservation", 20,
                       *_item_source("cross_engine_fidelity")),
        ) if criterion is not None),
    }

    return (
        CategorySpec("artifact_observability", "Engineering validity and observability",
                     w["artifact"], tuple(artifact)),
        CategorySpec("mechanics_requirements", "Mechanics and task fulfillment",
                     w["mechanics"], tuple(mechanics)),
        CategorySpec("causal_playability", "Causal and end-to-end playability",
                     w["causal"], tuple(causal)),
        CategorySpec("structure_content", "Structure, content, and asset realization",
                     w["structure"], tuple(structure)),
        CategorySpec("visual_experience", "Visual quality and experience",
                     w["visual"], tuple(visual)),
        CategorySpec("mode_specific", "Mode-specific obligations",
                     w["mode"], mode_criteria[mode]),
    )


def _empty_interval(status: str) -> Interval:
    return Interval(0.0, 0.0, 0.0, 0.0, {status: 1.0})


def _registered_combine(weighted: Sequence[tuple[float, Interval]]) -> Interval:


    denominator = sum(weight for weight, _interval in weighted)
    if denominator <= 0:
        return Interval(0.0, 0.0, 0.0, 0.0, {})
    lo = 0.0
    hi = 0.0
    covered = 0.0
    by_verdict: dict[str, float] = {}
    for weight, interval in weighted:
        for verdict, value in interval.by_verdict.items():
            by_verdict[verdict] = by_verdict.get(verdict, 0.0) + value
        if interval.denominator <= 0:
            hi += weight
            continue
        lo += weight * interval.lo
        hi += weight * interval.hi
        covered += weight * interval.coverage
    return Interval(
        lo=lo / denominator,
        hi=hi / denominator,
        coverage=covered / denominator,
        denominator=denominator,
        by_verdict=by_verdict,
    )


def _interval_dict_100(interval: Interval) -> dict[str, Any]:
    raw = interval.to_dict()
    raw["lo"] = round(interval.lo * 100.0, 3)
    raw["hi"] = round(interval.hi * 100.0, 3)
    raw["width"] = round(interval.width * 100.0, 3)
    raw["scale"] = "0-100"
    return raw


def _items_by_id(items: Iterable[Item]) -> dict[str, Item]:
    return {item.id: item for item in items}


def _reproduction_card(items: Mapping[str, Item]) -> dict[str, Any]:
    item = items.get("reproduction")
    if item is None:
        return {}
    evidence = item.evidence if isinstance(item.evidence, dict) else {}
    card = evidence.get("card")
    return dict(card) if isinstance(card, dict) else {}


def _reproduction_step(items: Mapping[str, Item]) -> str:

    item = items.get("reproduction")
    if item is None:
        return "reproduction: not run"
    if item.verdict is not Verdict.PASSED:
        return f"reproduction ({item.verdict.value}): {item.detail}"
    return "reproduction: channel produced no reading"


def _legacy_graded_restoration(items: Mapping[str, Item]) -> Item | None:

    legacy = items.get("repair_restoration")
    if legacy is None:
        return None
    item_id = "repair_restoration_graded"
    if legacy.verdict not in {Verdict.PASSED, Verdict.FAILED}:
        return inconclusive(item_id, detail=legacy.detail, evidence=legacy.evidence)
    evidence = legacy.evidence or {}
    restored = set(evidence.get("restored_targets") or [])
    targets = restored | set(evidence.get("unrestored_targets") or [])
    if not targets or len(targets) != evidence.get("target_count", len(targets)):
        return inconclusive(item_id, detail="stored repair_restoration has no complete target route census")
    differential = items.get("repair_differential")
    diff = differential.evidence if differential is not None else {}
    diff = diff or {}
    cells = evidence.get("routes") or diff.get("routes") or {}
    publication = items.get("bugfix_publication")
    preflight = (publication.evidence or {}) if publication is not None else {}
    partition = preflight.get("partition") or {}
    preserved: set[str] | None = None
    denominator_source = "route_cells"


    if "regression_routes" in diff and "negative_control_routes" in diff:
        preserved = set(diff["regression_routes"]) | set(diff["negative_control_routes"])
        denominator_source = "repair_differential route ids"
    elif cells and targets <= set(cells):
        preserved = set(cells) - targets
    elif "regression_route_ids" in partition and "negative_control_route_ids" in partition:
        preserved = set(partition["regression_route_ids"]) | set(partition["negative_control_route_ids"])
        denominator_source = "bugfix_publication.partition"
    if preserved is None:
        return inconclusive(item_id, detail="preserved route total cannot be recovered from stored routes, repair_differential or publication partition")
    preserved_total = len(preserved)
    if any(cell.get("status") in {"infrastructure_error", "not_run"} for cell in cells.values()):
        return inconclusive(item_id, detail="stored repair suite had infrastructure gaps")
    if not all(key in evidence or key in diff for key in ("regression_failed", "negative_control_failed")):
        return inconclusive(item_id, detail="stored preserved route failure census is missing")
    broken = set(evidence.get("regression_failed", diff.get("regression_failed", []))) | set(
        evidence.get("negative_control_failed", diff.get("negative_control_failed", [])))
    if (preserved is not None and not broken <= preserved) or len(broken) > preserved_total:
        return inconclusive(item_id, detail="stored preserved route census omits failed routes")
    factor = (preserved_total - len(broken)) / preserved_total if preserved_total else 1.0
    detail = {
        "repair_granularity": "route",
        "target_assertions_total": None,
        "target_assertions_restored": None,
        "restored_assertions": [],
        "unrestored_assertions": [],
        "target_routes_total": len(targets),
        "target_routes_restored": len(restored),
        "preserved_total": preserved_total,
        "preserved_passing": preserved_total - len(broken),
        "preserved_denominator_source": denominator_source,
        "preserved_failed": sorted(broken),
        "regression_factor": factor,
        "regression_free": not broken,
        "restored_targets": sorted(restored),
        "unrestored_targets": sorted(targets - restored),
    }
    note = f"stored route fallback: restored {len(restored)}/{len(targets)} targets"
    if not restored:
        return failed(item_id, detail=note, evidence=detail)
    return passed(item_id, credit=len(restored) / len(targets), detail=note, evidence=detail)


def _source_interval(
    source: SourceSpec,
    items: Mapping[str, Item],
    card: Mapping[str, Any],
    policy: RegistryPolicy,
) -> tuple[Interval, dict[str, Any]]:


    if source.kind == "item":
        item = items.get(source.id)
        if policy.evidence_consistency and source.id in {"unity_vlm", "cross_engine_fidelity"}:
            evidence = (item.evidence or {}) if item else {}
            state = str(evidence.get("calibration_state") or "uncalibrated")
            if state != "calibrated":
                return _empty_interval("inconclusive"), {
                    "kind": source.kind, "id": source.id, "status": state,
                    "applicable": False, "headline": "unearned",
                    "detail": "uncalibrated perceptual readings cannot enter the headline score",
                }
            if source.id == "unity_vlm":


                interval, row = _source_interval(
                    SourceSpec("scard", "calibrated_total"), items,
                    {"scard_state": state, "scard": (evidence.get("scard") or {}).get("interval")},
                    policy,
                )
                row.update(kind=source.kind, id=source.id, evidence=evidence)
                return interval, row
        if (policy.evidence_consistency and source.id == "mechanic_trace"
                and item is not None and item.verdict in {Verdict.PASSED, Verdict.FAILED}):
            evidence = item.evidence or {}
            expected = set(evidence.get("expected") or [])
            reached = set(evidence.get("reached") or evidence.get("observed") or [])
            if expected:
                credit = len(expected & reached) / len(expected)
                if evidence.get("segment_clean") is False:
                    credit = 0.0
                item = passed(item.id, credit=credit, evidence=evidence,
                              detail="graded task-check coverage; strict verdict is reported separately")
        if item is None:
            return _empty_interval("inconclusive"), {
                "kind": source.kind, "id": source.id, "status": "missing",
                "applicable": True, "point": None,
                "unmeasured_step": f"item {source.id}: not emitted by the evaluator",
            }
        interval = score_items([item])
        row: dict[str, Any] = {
            "kind": source.kind,
            "id": source.id,
            "status": item.verdict.value,
            "applicable": item.verdict not in {Verdict.UNOBSERVABLE, Verdict.EXEMPT},
            "credit": item.credit if item.verdict.is_decided else 0.0,
            "detail": item.detail,
            "evidence": item.evidence,
        }
        if row["applicable"]:
            row["point"] = interval.point
            if interval.point is None:
                row["unmeasured_step"] = f"item {source.id} ({item.verdict.value}): {item.detail}"
        return interval, row

    if source.kind == "ocard":
        channels = card.get("channels") or []
        row = next(
            (entry for entry in channels
             if isinstance(entry, dict) and entry.get("channel") == source.id),
            None,
        )
        if not isinstance(row, dict):
            return _empty_interval("inconclusive"), {
                "kind": source.kind, "id": source.id, "status": "missing",
                "applicable": True, "point": None,
                "unmeasured_step": f"{source.id}: {_reproduction_step(items)}",
            }
        interval = Interval(
            float(row.get("lo") or 0.0),
            float(row.get("hi") or 0.0),
            float(row.get("coverage") or 0.0),
            float(row.get("denominator") or 0.0),
            dict(row.get("by_verdict") or {}),
        )


        unobservable = (
            interval.denominator <= 0
            and bool(interval.by_verdict.get(Verdict.UNOBSERVABLE.value))
        )
        all_exempt = interval.denominator > 0 and interval.point is None


        partial_weight = (
            interval.by_verdict.get(Verdict.INCONCLUSIVE.value, 0.0)
            if interval.denominator > 0 else 0.0
        )
        status = (
            "partial" if partial_weight > 0
            else "measured" if interval.denominator > 0
            else "unobservable" if unobservable
            else "not_measured"
        )
        out: dict[str, Any] = {
            "kind": source.kind,
            "id": source.id,
            "status": status,
            "applicable": not unobservable and not all_exempt,
            "name": row.get("name"),
            "note": row.get("note"),
            "interval": _interval_dict_100(interval),
            "exempt_weight": round(interval.exempt_weight, 6),
        }
        if all_exempt:
            out["detail"] = "every measured item is genre-exempt; not a score axis on this task"
        if out["applicable"]:
            point = None if partial_weight > 0 else interval.point
            out["point"] = None if point is None else round(point * 100.0, 3)
            if partial_weight > 0:
                out["inconclusive_weight"] = round(partial_weight, 6)
                out["unmeasured_step"] = (
                    f"{source.id}: partial reading, inconclusive weight "
                    f"{partial_weight:g} of {interval.denominator + partial_weight:g} "
                    f"left unread by the evaluator; {row.get('note') or _reproduction_step(items)}"
                )
            elif point is None:
                out["unmeasured_step"] = (
                    f"{source.id}: {row.get('note') or _reproduction_step(items)}"
                )
        return interval, out

    if source.kind == "scard":
        state = str(card.get("scard_state") or "uncalibrated")
        raw = card.get("scard") or {}
        if state != "calibrated" or not isinstance(raw, dict):


            row = {
                "kind": source.kind,
                "id": source.id,
                "status": state,
                "applicable": False,
                "detail": "uncalibrated perceptual readings cannot enter the headline score",
            }
            if policy.scard_unearned:
                row["headline"] = "unearned"
                row["detail"] += "; registered weight counted 0 in the headline (see headline_ceiling)"
            return _empty_interval("inconclusive"), row
        interval = Interval(
            float(raw.get("lo") or 0.0),
            float(raw.get("hi") or 0.0),
            float(raw.get("coverage") or 0.0),
            float(raw.get("denominator") or 0.0),
            dict(raw.get("by_verdict") or {}),
        )
        out = {
            "kind": source.kind,
            "id": source.id,
            "status": "measured" if interval.denominator > 0 else "not_measured",
            "applicable": True,
            "interval": _interval_dict_100(interval),
            "point": None if interval.point is None else round(interval.point * 100.0, 3),
        }
        if interval.point is None:
            out["unmeasured_step"] = "calibrated_total: S-card judge produced no reading"
        if policy.evidence_consistency and interval.by_verdict.get(Verdict.INCONCLUSIVE.value, 0) > 0:
            out.update(status="partial", point=None,
                       unmeasured_step="calibrated_total: a visual channel was not measured in every repeat")
        return interval, out

    raise ValueError(f"unknown scorecard source kind {source.kind!r}")


def _weighted_point(
    weighted: Sequence[tuple[float, float | None]],
) -> float | None:


    total = sum(weight for weight, _point in weighted)
    if total <= 0:
        return None
    acc = 0.0
    for weight, point in weighted:
        if point is None:
            return None
        acc += weight * point
    return acc / total


def _point_dict_100(point: float | None, status: str | None = None) -> dict[str, Any]:
    return {
        "score": None if point is None else round(point * 100.0, 3),
        "status": status or ("complete" if point is not None else "evaluation_incomplete"),
        "scale": "0-100",
    }


def _headline_credit(point: float, exponent: float) -> float:
    return point if exponent == 1.0 else point ** exponent


def _strict_status(result: Any, items: Mapping[str, Item], mode: str) -> dict[str, Any]:
    required = verifier_profile(mode).strict_ids
    if "demonstrations_complete" in items:
        required = required | {"demonstrations_complete"}
    failed: list[str] = []
    not_measured: list[str] = []
    for item_id in sorted(required):
        item = items.get(item_id)
        if item is None or item.verdict in {
            Verdict.INCONCLUSIVE,
            Verdict.UNMEASURABLE,
            Verdict.UNOBSERVABLE,
        }:
            not_measured.append(item_id)
        elif item.verdict is not Verdict.PASSED:
            failed.append(item_id)
    resolved = result.resolved
    return {
        "resolved": resolved,
        "status": "passed" if resolved is True else "failed" if resolved is False else "not_measured",
        "failed_required_items": failed,
        "not_measured_required_items": not_measured,
        "rule": "strict mode profile is conjunctive; the weighted score cannot override it",
    }


def _ranking_note(result: Any, strict: Mapping[str, Any], ranking_eligible: bool) -> str:


    if not ranking_eligible:
        return ""
    eligibility = str(getattr(result, "eligibility_status", "") or "")
    failed_eligibility = sorted(
        item.id for item in (getattr(result, "eligibility_items", None) or [])
        if item.verdict in {Verdict.FAILED, Verdict.MALFORMED, Verdict.SKIPPED, Verdict.EXEMPT}
    )
    failed_required = [
        item_id for item_id in (strict.get("failed_required_items") or [])
        if item_id not in failed_eligibility
    ]
    if strict.get("resolved") is False:
        reasons: list[str] = []
        if eligibility == "failed":
            reasons.append("eligibility=failed (" + ", ".join(failed_eligibility or ["?"]) + ")")
        if failed_required:
            reasons.append("failed required items: " + ", ".join(failed_required))
        return (
            "ranking_eligible=true reports measurement coverage only (every applicable "
            "source read, >=80% per category); it does not consult the strict verdict. "
            "This cell is NOT resolved: " + "; ".join(reasons or ["strict verdict failed"])
            + ". A leaderboard may sort only cells that are both resolved and "
            "ranking-eligible (HIERARCHICAL_MULTI_EVIDENCE_SCORECARD.md §3-4)."
        )
    if strict.get("resolved") is None:
        pending = list(strict.get("not_measured_required_items") or [])
        return (
            "ranking_eligible=true reports measurement coverage only; the strict verdict is "
            "not measured (eligibility=" + (eligibility or "not_measured")
            + (", unmeasured required items: " + ", ".join(pending) if pending else "")
            + "). Do not rank this cell until resolved is decided."
        )
    return ""


def _brief_context(result: Any, items: Mapping[str, Item]) -> dict[str, Any]:


    context = getattr(result, "brief_context", None)
    if isinstance(context, Mapping):
        return dict(context)
    item = items.get("reproduction")
    evidence = item.evidence if item is not None and isinstance(item.evidence, dict) else {}
    context = evidence.get("brief_context")
    return dict(context) if isinstance(context, Mapping) else {}


def _forced_not_applicable(
    policy: RegistryPolicy, mode: str, result: Any, items: Mapping[str, Item],
) -> dict[tuple[str, str], str]:


    if policy.mode34_applicability and mode == "skeleton":
        return dict(SKELETON_NOT_APPLICABLE)
    if policy.mode34_applicability and mode == "bugfix":
        forced = dict(BUGFIX_NOT_APPLICABLE)
        if policy.progressive_redesign_mode == "bugfix":


            for key in (
                ("artifact_observability", "project_integrity"),
                ("artifact_observability", "runtime_viability"),
                ("artifact_observability", "evaluator_addressability"),
            ):
                forced.pop(key, None)
        if policy.mode4_graded:
            forced[("mechanics_requirements", "deterministic_mechanics")] = (
                "bugfix mode: O3 is an absolute reading of the reference's universal assertions; "
                "reported, not scored, as in mode34. Regressions are charged through the "
                "proportional preserved-route regression_factor and feature_kept"
            )
        return forced
    if not policy.brief_own_structure or mode != "brief":
        return {}
    forced = dict(BRIEF_NOT_APPLICABLE)
    constraint = str(_brief_context(result, items).get("layout_constraint") or "")
    if constraint:
        forced.pop(("structure_content", "spatial_relations"), None)
    return forced


def _mode4_candidate_import_items(
    items: Mapping[str, Item], engine: Mapping[str, Any],
) -> dict[str, Item]:


    normalized = dict(items)
    publication = items.get("bugfix_publication")
    if publication is None or publication.verdict is not Verdict.PASSED:
        return normalized
    cold = next((row for row in _reproduction_card(items).get("channels", [])
                 if row.get("channel") == "O1"), {})
    if cold.get("measured") is not True or "cold_import=failed" not in {
        part.strip() for part in str(cold.get("note") or "").split(";")
    }:
        return normalized
    prep = engine.get("prepare") or {}
    attempts = prep.get("attempts") or []
    if prep.get("ok") is not False or not attempts:
        return normalized
    attempt = attempts[-1]
    imported = attempt.get("import") or {}
    if (not (attempt.get("scratch") or {}).get("ok")
            or not (attempt.get("inject") or {}).get("ok")
            or imported.get("ok") is not False
            or imported.get("timed_out") is not False
            or not isinstance(imported.get("returncode"), int)
            or imported["returncode"] < 0):
        return normalized
    for ident in ("null_no_win", "anti_grant_diff",
                  "repair_differential", "repair_restoration", "repair_restoration_graded"):
        item = normalized.get(ident)
        if (item is None or item.verdict not in {Verdict.INCONCLUSIVE, Verdict.UNMEASURABLE}
                or item.attribution in {Attribution.HARNESS, Attribution.UNATTRIBUTABLE}):
            continue
        if ident == "null_no_win":
            readings = [item.evidence]
        elif ident == "anti_grant_diff":
            readings = [item.evidence.get(key) for key in (
                "idle_env", "idle_flagged", "ops_env", "ops_flagged",
            )]
        else:
            readings = list((item.evidence.get("routes") or {}).values())
        if not readings or any(not row or row.get("stop_reason") != "launch_failed"
                               for row in readings):
            continue
        evidence = {
            **item.evidence, "failure_code": "candidate_cold_import_failed",
            "candidate_cold_import": dict(cold), "route_import": dict(imported),
        }
        if ident == "repair_restoration_graded":

            evidence.update(regression_factor=0.0, regression_free=False)
        normalized[ident] = replace(
            item, verdict=Verdict.FAILED, credit=0.0, attribution=Attribution.SUBMISSION,
            detail=item.detail + "; candidate cold import failed and prevented this replay",
            evidence=evidence,
        )
    return normalized


def _mode4_product_headline(
    category_rows: Sequence[Mapping[str, Any]],
    *, graded: bool = False, repair_item: Item | None = None,
    edit_radius_item: Item | None = None,
    gate_keys: Sequence[tuple[str, str]] = MODE4_GATES,
    regression_weights: Mapping[tuple[str, str], float] = MODE4_REGRESSION_WEIGHTS,
) -> tuple[float | None, dict[str, Any]]:


    points: dict[tuple[str, str], float | None] = {}
    applicable: dict[tuple[str, str], bool] = {}
    details: dict[tuple[str, str], str] = {}
    for category in category_rows:
        for criterion in category["criteria"]:
            key = (str(category["id"]), str(criterion["id"]))
            applicable[key] = bool(criterion["applicable"])
            score = criterion["score"]["score"]
            points[key] = None if score is None else float(score) / 100.0
            details[key] = "; ".join(
                str(source.get("detail") or "")
                for source in criterion.get("sources") or [] if source.get("detail")
            )

    def _read(key: tuple[str, str]) -> float | None:
        return points.get(key) if applicable.get(key) else None

    repair = _read(MODE4_REPAIR_CREDIT)
    gate_points = [(key, _read(key)) for key in gate_keys if applicable.get(key)]
    regression = [
        (weight, _read(key)) for key, weight in regression_weights.items()
        if applicable.get(key)
    ]
    gates = None
    if gate_points and all(point is not None for _key, point in gate_points):

        gates = 1.0 if all(point >= 1.0 for _key, point in gate_points) else 0.0
    regression_mean = _weighted_point(regression) if regression else None
    complete = (
        repair is not None and applicable.get(MODE4_REPAIR_CREDIT, False)
        and gates is not None and regression_mean is not None
    )
    headline = repair * gates * regression_mean if complete else None
    composition = {
        "formula": "repair_credit x gates x regression_mean",
        "repair_credit": None if repair is None else round(repair * 100.0, 3),


        "repair_detail": details.get(MODE4_REPAIR_CREDIT, ""),
        "gates": None if gates is None else round(gates * 100.0, 3),
        "failed_gates": sorted(
            f"{cat}/{crit}" for (cat, crit), point in gate_points
            if point is not None and point < 1.0
        ),
        "gate_readings": {
            f"{cat}/{crit}": {
                "applicable": bool(applicable.get((cat, crit))),
                "credit": points.get((cat, crit)),
            }
            for cat, crit in gate_keys
        },
        "regression_mean": (
            None if regression_mean is None else round(regression_mean * 100.0, 3)
        ),
        "regression_weights": {
            f"{cat}/{crit}": weight for (cat, crit), weight in regression_weights.items()
            if applicable.get((cat, crit))
        },
    }
    if graded:
        evidence = dict(repair_item.evidence or {}) if repair_item is not None else {}
        factor = evidence.get("regression_factor")


        if repair_item is not None and repair_item.verdict in {Verdict.PASSED, Verdict.FAILED}:
            repair = repair_item.credit
        edit_radius = (edit_radius_item.credit
                       if edit_radius_item is not None and edit_radius_item.verdict is Verdict.PASSED
                       else None)
        headline = (repair * gates * factor * regression_mean
                    if complete and factor is not None else None)
        evidence.update(repair_credit=repair, gates=gates,
                        edit_radius=dict(edit_radius_item.evidence) if edit_radius_item is not None else None,
                        regression_mean=regression_mean)
        composition.update({
            "formula": "repair_credit x gates x regression_factor x regression_mean",
            "repair_credit": None if repair is None else round(repair * 100.0, 3),
            "repair_granularity": evidence.get("repair_granularity"),
            "gates": None if gates is None else round(gates * 100.0, 3),
            "regression_factor": None if factor is None else round(factor * 100.0, 3),
            "regression_free": evidence.get("regression_free"),
            "edit_radius": None if edit_radius is None else round(edit_radius * 100.0, 3),
            "edit_radius_weight": 0,
            "regression_mean": None if regression_mean is None else round(regression_mean * 100.0, 3),
            "repair_detail": evidence,
        })
    return headline, composition


def _mode4_f2p_p2p_outcome(
    category_rows: Sequence[Mapping[str, Any]],
    *,
    repair_item: Item | None,
    strict: Mapping[str, Any],
) -> dict[str, Any]:


    points: dict[tuple[str, str], float | None] = {}
    applicable: dict[tuple[str, str], bool] = {}
    for category in category_rows:
        for criterion in category.get("criteria") or []:
            key = (str(category.get("id")), str(criterion.get("id")))
            applicable[key] = bool(criterion.get("applicable"))
            raw = (criterion.get("score") or {}).get("score")
            points[key] = None if raw is None else float(raw) / 100.0

    gate_readings = {
        f"{category}/{criterion}": (
            points.get((category, criterion)) if applicable.get((category, criterion)) else None
        )
        for category, criterion in MODE4_REDESIGN_GATES
    }
    applicable_gates = {
        key: value for key, value in gate_readings.items()
        if applicable.get(tuple(key.split("/", 1)), False)
    }
    preservation_keys = tuple(MODE4_REDESIGN_REGRESSION_WEIGHTS)
    preservation_readings = {
        f"{category}/{criterion}": (
            points.get((category, criterion)) if applicable.get((category, criterion)) else None
        )
        for category, criterion in preservation_keys
    }
    applicable_preservation = {
        key: value for key, value in preservation_readings.items()
        if applicable.get(tuple(key.split("/", 1)), False)
    }

    evidence = dict(repair_item.evidence or {}) if repair_item is not None else {}
    repair_decided = (
        repair_item is not None
        and repair_item.verdict in {Verdict.PASSED, Verdict.FAILED}
    )
    f2p = float(repair_item.credit) if repair_decided else None
    p2p_raw = evidence.get("regression_factor")
    p2p = float(p2p_raw) if isinstance(p2p_raw, (int, float)) else None
    readings_complete = (
        f2p is not None
        and p2p is not None
        and bool(applicable_gates)
        and all(value is not None for value in applicable_gates.values())
        and bool(applicable_preservation)
        and all(value is not None for value in applicable_preservation.values())
    )
    passed = lambda value: value is not None and value >= 1.0 - 1e-12
    failed_gates = sorted(key for key, value in applicable_gates.items() if not passed(value))
    failed_preservation = sorted(
        key for key, value in applicable_preservation.items() if not passed(value)
    )


    target_owned_strict = {"repair_differential", "gold_replay"}
    other_strict_failures = sorted(
        set(strict.get("failed_required_items") or []) - target_owned_strict
    )
    preservation_ok = (
        readings_complete
        and passed(p2p)
        and not failed_preservation
        and not failed_gates
        and not other_strict_failures
    )

    if not readings_complete:
        status = "INCONCLUSIVE"
        score = None
    elif preservation_ok and passed(f2p) and strict.get("resolved") is True:
        status = "FULL"
        score = 100.0
    elif preservation_ok and f2p is not None and 0.0 < f2p < 1.0:
        status = "PARTIAL"
        score = 0.0
    else:
        status = "NO"
        score = 0.0

    granularity = str(evidence.get("repair_granularity") or "unknown")
    f2p_total = (
        evidence.get("target_assertions_total")
        if granularity == "assertion" else evidence.get("target_routes_total")
    )
    f2p_passed = (
        evidence.get("target_assertions_restored")
        if granularity == "assertion" else evidence.get("target_routes_restored")
    )
    return {
        "resolution_status": status,
        "resolved": status == "FULL",
        "score": score,
        "f2p_rate": None if f2p is None else round(f2p * 100.0, 3),
        "p2p_rate": None if p2p is None else round(p2p * 100.0, 3),
        "f2p": {
            "rate": f2p,
            "granularity": granularity,
            "passed": f2p_passed,
            "total": f2p_total,
            "restored_assertions": evidence.get("restored_assertions") or [],
            "unrestored_assertions": evidence.get("unrestored_assertions") or [],
        },
        "p2p": {
            "rate": p2p,
            "granularity": "route",
            "passed": evidence.get("preserved_passing"),
            "total": evidence.get("preserved_total"),
            "failed_routes": evidence.get("preserved_failed") or [],
            "preservation_contracts": preservation_readings,
            "failed_preservation_contracts": failed_preservation,
        },
        "integrity_gates": gate_readings,
        "failed_integrity_gates": failed_gates,
        "other_strict_failures": other_strict_failures,
        "formula": "FULL iff all F2P, all P2P, integrity gates and strict repair contract pass",
    }


def _not_applicable_reason(criterion_row: Mapping[str, Any]) -> str:

    reasons: list[str] = []
    for source in criterion_row.get("sources") or []:
        if source.get("not_applicable_reason"):
            reasons.append(str(source["not_applicable_reason"]))
        elif source.get("headline") == "unearned":
            reasons.append(f"{source['id']}: uncalibrated perceptual S-card, unearned weight")
        elif source.get("status") == "unobservable":
            reasons.append(f"{source['id']}: unobservable ({source.get('note') or source.get('detail') or 'no reading possible on this task'})")
        elif source.get("detail") and "genre-exempt" in str(source.get("detail")):
            reasons.append(f"{source['id']}: {source['detail']}")
        elif source.get("status") in {"unobservable", "exempt"}:
            reasons.append(f"{source['id']}: {source.get('detail') or source['status']}")
        elif source.get("applicable") is False:
            reasons.append(f"{source['id']}: {source.get('detail') or source.get('status') or 'not applicable'}")
    return "; ".join(dict.fromkeys(reasons)) or "no applicable source"


_MODE5_INFRASTRUCTURE_GATES = (
    "task_gdd_contract",
    "verifier_profile_complete",
)


_MODE5_ADMISSIBILITY_GATES = (
    "unity_layout",
    "unity_interface",
    "unity_sdk_integrity",
    "port_contract_alignment",
    "no_eval_smuggling",
    "no_bundled_godot_runtime",
    "build_recipe",
    "unity_anti_grant_static",
    "unity_build",
    "unity_probe",
    "unity_input_dispatch",
)


_MODE5_DELIVERABLE_GATES = (
    "ops_present",
    "ops_valid",
    "ops_not_idle",
)

_MODE5_SUBMISSION_GATES = _MODE5_ADMISSIBILITY_GATES + _MODE5_DELIVERABLE_GATES

_MODE5_VALIDITY_GATES = (
    "unity_auto_win_ready",
    "null_no_win",
    "unity_counterfactual",
)


def _mode5_environment_rows(items: Mapping[str, Item]) -> list[dict[str, Any]]:


    profiles: list[tuple[str, Mapping[str, Any]]] = []
    for item in items.values():
        for node in _walk_evidence(item.evidence):
            environment = node.get("environment")
            if isinstance(environment, Mapping) and "score_eligible" in environment:
                profiles.append((item.id, environment))
    if not profiles:
        return []


    unique: dict[tuple[Any, ...], tuple[str, Mapping[str, Any]]] = {}
    for item_id, profile in profiles:
        key = (
            profile.get("profile_id"), profile.get("environment_class"),
            profile.get("score_eligible"), profile.get("certified"),
            profile.get("certification_status"),
        )
        unique.setdefault(key, (item_id, profile))
    rows = list(unique.values())
    eligible = [
        bool(profile.get("score_eligible")) and bool(profile.get("certified", True))
        for _, profile in rows
    ]
    if all(eligible):
        status = "passed"
        detail = "host is certified for score-bearing Unity readings"
    elif not any(eligible):
        status = "inconclusive"
        detail = next(
            (
                str(profile.get("detail"))
                for _, profile in rows
                if profile.get("detail")
            ),
            "host cannot produce score-bearing Unity readings",
        )
    else:
        status = "inconclusive"
        detail = "runtime evidence contains conflicting Unity environment profiles"
    return [{
        "id": "unity_environment_score_eligible",
        "status": status,
        "detail": detail,
        "profiles": [
            {
                "source_item": item_id,
                "profile_id": profile.get("profile_id"),
                "environment_class": profile.get("environment_class"),
                "score_eligible": profile.get("score_eligible"),
                "certified": profile.get("certified"),
                "certification_status": profile.get("certification_status"),
            }
            for item_id, profile in rows
        ],
        "attribution": "harness" if status != "passed" else None,
    }]


def _mode5_evidence_attribution(evidence: Any) -> str | None:

    values: list[str] = []
    for node in _walk_evidence(evidence):
        for key in ("attribution", "owner"):
            raw = node.get(key)
            if isinstance(raw, str):
                values.append(raw.strip().lower())
        nested = node.get("attributions")
        if isinstance(nested, Mapping):
            values.extend(
                str(value).strip().lower()
                for value in nested.values()
                if isinstance(value, str)
            )
        environment = node.get("environment")
        if isinstance(environment, Mapping) and environment.get("score_eligible") is False:
            values.append("infrastructure")
    if any(value in {"harness", "infrastructure", "infrastructure_failed",
                    "evaluator", "observation_inconclusive", "policy_inconclusive"}
           for value in values):
        return Attribution.HARNESS.value
    if any(value in {"submission", "submission_failed", "candidate_failed"}
           for value in values):
        return Attribution.SUBMISSION.value
    if any(value == Attribution.UNATTRIBUTABLE.value for value in values):
        return Attribution.UNATTRIBUTABLE.value
    return None


def _mode5_item_attribution(item: Item) -> str | None:


    if item.attribution is not None:
        return item.attribution.value
    evidence_attribution = _mode5_evidence_attribution(item.evidence)
    if evidence_attribution is not None:
        return evidence_attribution
    detail = (item.detail or "").lower()
    if item.verdict in {Verdict.INCONCLUSIVE, Verdict.UNMEASURABLE}:
        return Attribution.HARNESS.value
    if item.verdict is Verdict.UNOBSERVABLE:
        if any(marker in detail for marker in (
            "disabled by evaluator configuration",
            "not requested",
            "requires visual_judge=",
        )):
            return Attribution.HARNESS.value
        return Attribution.UNATTRIBUTABLE.value
    if item.verdict in {
        Verdict.FAILED, Verdict.MALFORMED, Verdict.SKIPPED, Verdict.EXEMPT,
    }:
        return Attribution.SUBMISSION.value
    return None


def _mode5_gate(
    name: str, ids: Sequence[str], items: Mapping[str, Item],
    *, extra_rows: Sequence[Mapping[str, Any]] = (),
) -> dict[str, Any]:
    rows: list[dict[str, Any]] = []
    failed_ids: list[str] = []
    pending_ids: list[str] = []
    for item_id in ids:
        item = items.get(item_id)
        if item is None:
            status = "missing"
            detail = "required evaluator item was not emitted"
            pending_ids.append(item_id)
        else:
            status = item.verdict.value
            detail = item.detail
            if item.verdict in {
                Verdict.FAILED, Verdict.MALFORMED, Verdict.SKIPPED, Verdict.EXEMPT,
            }:
                failed_ids.append(item_id)
            elif item.verdict is not Verdict.PASSED:
                pending_ids.append(item_id)
        attribution = _mode5_item_attribution(item) if item is not None else None
        rows.append({
            "id": item_id, "status": status, "detail": detail,
            **({"attribution": attribution} if attribution else {}),
        })
    for extra in extra_rows:
        row = dict(extra)
        rows.append(row)
        if row.get("status") == "failed":
            failed_ids.append(str(row.get("id")))
        elif row.get("status") != "passed":
            pending_ids.append(str(row.get("id")))
    state = "failed" if failed_ids else "incomplete" if pending_ids else "passed"
    return {
        "id": name,
        "status": state,
        "failed_items": failed_ids,
        "unmeasured_items": pending_ids,
        "items": rows,
    }


def _mode5_leaf(item_id: str, items: Mapping[str, Item]) -> tuple[float | None, dict[str, Any]]:
    item = items.get(item_id)
    if item is None:
        return None, {
            "kind": "item", "id": item_id, "status": "missing", "point": None,
            "detail": "score-bearing evaluator item was not emitted",
        }
    decided = item.verdict in {
        Verdict.PASSED, Verdict.FAILED, Verdict.MALFORMED, Verdict.SKIPPED,
    }
    point = item.credit if item.verdict in {Verdict.PASSED, Verdict.FAILED} else 0.0 if decided else None
    return point, {
        "kind": "item",
        "id": item_id,
        "status": item.verdict.value,
        "point": None if point is None else round(point * 100.0, 3),
        "credit": item.credit,
        "detail": item.detail,
        "evidence": item.evidence,


        "attribution": _mode5_item_attribution(item),
    }


def _mode5_evaluator_owned_holes(
    unmeasured: Sequence[Mapping[str, Any]], items: Mapping[str, Item],
) -> list[dict[str, Any]]:


    owed: list[dict[str, Any]] = []
    for row in unmeasured:
        source = str(row.get("source") or "")
        ident = source.split("item:", 1)[1] if source.startswith("item:") else source
        item = items.get(ident)
        if item is not None and _mode5_item_attribution(item) == Attribution.SUBMISSION.value:
            continue
        owed.append(dict(row))
    return owed


def _mode5_table_summary(category_rows: Sequence[Mapping[str, Any]]) -> dict[str, Any]:


    by_id = {str(row.get("id")): row for row in category_rows}

    def points(*ids: str) -> float | None:
        total = 0.0
        for ident in ids:
            row = by_id.get(ident)
            if not row:
                return None
            score = row.get("score") or {}
            value = score.get("score")
            if value is None:
                return None
            total += float(row.get("weight_in_total") or 0.0) * float(value) / 100.0
        return round(total, 3)

    columns = {
        "mechanism_and_requirements": {
            "weight": 35.0,
            "score": points("core_mechanics"),
            "sources": ["unity_mechanic_trace"],
        },
        "content_and_assets": {
            "weight": 15.0,
            "score": points("content_structure"),
            "sources": ["unity_structure_fidelity"],
            "note": (
                "asset identity and supplied-content realization are included "
                "here; visual correspondence may use VLM, but assets are not "
                "a separate leaderboard column"
            ),
        },
        "playability_and_demo": {
            "weight": 35.0,
            "score": points("playability_progression", "stability_lifecycle"),
            "sources": ["causal_witness", "unity_hidden_behavior", "unity_runtime_stability"],
            "note": "stability is aggregated here but remains a separate diagnostic leaf",
        },
        "mode_specific": {
            "weight": 15.0,
            "score": points("visual_feedback"),
            "sources": ["unity_vlm"],
            "note": "Mode-5 visual quality",
        },
    }
    complete = all(column["score"] is not None for column in columns.values())
    measured = round(sum(float(column["score"] or 0.0) for column in columns.values()), 3)
    return {
        "schema": "gamebench.mode5.leaderboard-columns.v1",
        "columns": columns,
        "total": measured if complete else None,
        "measured_points": measured,
        "status": "complete" if complete else "partial",
    }


_MODE5_BLOCKED_BY_BUILD = frozenset({
    "unity_probe", "unity_mechanic_trace", "unity_runtime_stability",
    "unity_auto_win_ready", "unity_input_dispatch", "unity_hidden_behavior",
    "unity_source_behavior", "unity_counterfactual", "legacy_reference_trace",
    "causal_witness", "null_no_win", "unity_evaluator_capture", "unity_vlm",
    "unity_structure_fidelity", "unity_visual_fidelity", "cross_engine_fidelity",
})
_MODE5_EVALUATOR_OWNED_MARKERS = (
    "no runnable", "not requested", "disabled by evaluator configuration",
    "requires visual_judge",
)


def _mode5_normalize_candidate_blocked_items(items: Mapping[str, Item]) -> dict[str, Item]:


    normalized = dict(items)
    root: Item | None = None
    for ident in ("unity_build", "unity_probe"):
        candidate = normalized.get(ident)
        if candidate is None:
            continue
        owner = _mode5_item_attribution(candidate)
        if (candidate.verdict in {Verdict.FAILED, Verdict.SKIPPED}
                and owner == Attribution.SUBMISSION.value):
            root = candidate
            break
    if root is None:
        return normalized
    for ident in _MODE5_BLOCKED_BY_BUILD:
        item = normalized.get(ident)
        if item is None or item.verdict not in {Verdict.INCONCLUSIVE, Verdict.UNMEASURABLE}:
            continue


        owner = (item.attribution.value if item.attribution is not None
                 else _mode5_evidence_attribution(item.evidence))
        if owner in {Attribution.HARNESS.value, Attribution.UNATTRIBUTABLE.value}:
            continue
        detail = (item.detail or "").lower()
        if any(marker in detail for marker in _MODE5_EVALUATOR_OWNED_MARKERS):
            continue
        normalized[ident] = replace(
            item,
            verdict=Verdict.FAILED,
            credit=0.0,
            attribution=Attribution.SUBMISSION,
            detail=(item.detail or "") + f"; candidate failure propagated from {root.id}",
            evidence={**item.evidence, "dependency_root": root.id},
        )
    return normalized


def _score_mode5_result(result: Any, policy: RegistryPolicy) -> dict[str, Any]:


    mode = str(result.package.manifest.get("mode") or "")
    if mode != "port":
        raise ValueError(f"registry {policy.version!r} is defined only for Mode 5 / port")
    game_id = str(result.package.manifest.get("game_id") or "")
    items = _items_by_id(result.items)
    if policy.mode5_delivery_zero:
        items = _mode5_normalize_candidate_blocked_items(items)
    specs = _category_specs(mode, policy.version)
    infrastructure = _mode5_gate(
        "infrastructure_readiness", _MODE5_INFRASTRUCTURE_GATES, items,
        extra_rows=_mode5_environment_rows(items),
    )
    if policy.mode5_deliverable_gates_nonfatal:
        submission_gate = _mode5_gate(
            "submission_admissibility", _MODE5_ADMISSIBILITY_GATES, items,
        )


        deliverable_gate = _mode5_gate(
            "candidate_deliverables", _MODE5_DELIVERABLE_GATES, items,
        )
        deliverable_gate["scoring_effect"] = (
            "non-fatal: a refused or missing tape zeroes causal_witness and "
            "stays in the denominator; the remaining 90 points are read normally"
        )
    else:
        submission_gate = _mode5_gate(
            "submission_admissibility", _MODE5_SUBMISSION_GATES, items,
        )
        deliverable_gate = None
    validity_gate = _mode5_gate(
        "causal_evidence_validity", _MODE5_VALIDITY_GATES, items,
    )

    category_rows: list[dict[str, Any]] = []
    unmeasured: list[dict[str, Any]] = []
    measured_weight = 0.0
    earned_points = 0.0
    for category in specs:
        criteria: list[dict[str, Any]] = []
        category_measured = 0.0
        category_earned = 0.0
        category_complete = True
        for criterion in category.criteria:
            leaves: list[dict[str, Any]] = []
            points: list[float] = []
            for source in criterion.sources:
                point, leaf = _mode5_leaf(source.id, items)
                leaves.append(leaf)
                if point is None:
                    category_complete = False
                    unmeasured.append({
                        "category": category.id,
                        "criterion": criterion.id,
                        "source": f"item:{source.id}",
                        "step": leaf["detail"],
                    })
                else:
                    points.append(point)
            criterion_point = (
                sum(points) / len(points)
                if len(points) == len(criterion.sources) and points else None
            )
            if criterion_point is not None:
                category_measured += criterion.weight
                category_earned += criterion.weight * criterion_point
            criteria.append({
                "id": criterion.id,
                "name": criterion.name,
                "weight_within_category": criterion.weight,
                "applicable": True,
                "score": _point_dict_100(criterion_point),
                "measured_source_share": (
                    round(len(points) / len(criterion.sources), 6)
                    if criterion.sources else 0.0
                ),
                "sources": leaves,
            })
        category_point = category_earned / 100.0 if category_complete else None
        measured_fraction = category_measured / 100.0
        measured_weight += category.weight * measured_fraction
        earned_points += category.weight * category_earned / 100.0
        category_rows.append({
            "id": category.id,
            "name": category.name,
            "weight_in_total": category.weight,
            "applicable": True,
            "applicable_weight_within_category": 100.0,
            "score": _point_dict_100(category_point),
            "measured_weight_share": round(measured_fraction, 6),
            "low_measurement_coverage": measured_fraction < 1.0,
            "criteria": criteria,
        })

    quality_complete = not unmeasured
    objective_category_ids = {
        "core_mechanics", "playability_progression", "stability_lifecycle",
    }
    vlm_category_ids = {"content_structure", "visual_feedback"}
    objective_rows = [row for row in category_rows if row["id"] in objective_category_ids]
    vlm_rows = [row for row in category_rows if row["id"] in vlm_category_ids]
    structure_rows = [row for row in category_rows if row["id"] == "content_structure"]
    mdva_rows = [row for row in category_rows if row["id"] == "visual_feedback"]
    objective_missing = [row for row in unmeasured
                         if row.get("category") in objective_category_ids]
    vlm_missing = [row for row in unmeasured
                    if row.get("category") in vlm_category_ids]
    objective_complete = not objective_missing
    vlm_complete = not vlm_missing
    objective_points = sum(
        float(row["weight_in_total"]) * float((row.get("score") or {}).get("score") or 0.0) / 100.0
        for row in objective_rows
    )
    vlm_points = sum(
        float(row["weight_in_total"]) * float((row.get("score") or {}).get("score") or 0.0) / 100.0
        for row in vlm_rows
    )
    gates_complete = all(
        gate["status"] == "passed"
        for gate in (infrastructure, submission_gate, validity_gate)
    )
    candidate_failure = submission_gate["status"] == "failed"
    invalid_evidence = validity_gate["status"] == "failed"
    infrastructure_incomplete = infrastructure["status"] != "passed"
    gate_incomplete = (
        not infrastructure_incomplete
        and not candidate_failure
        and not invalid_evidence
        and not gates_complete
    )


    if policy.mode5_delivery_zero:
        evaluator_holes = _mode5_evaluator_owned_holes(unmeasured, items)
        coverage_incomplete = bool(evaluator_holes)
    else:
        coverage_incomplete = not quality_complete
    if infrastructure_incomplete:
        outcome_status = "infrastructure_inconclusive"
        headline_point: float | None = None
    elif candidate_failure and not coverage_incomplete:
        outcome_status = "candidate_delivery_failure"
        headline_point = 0.0
    elif invalid_evidence and not coverage_incomplete:
        outcome_status = "invalid_causal_evidence"
        headline_point = 0.0
    elif gate_incomplete or coverage_incomplete:
        outcome_status = "evaluation_incomplete"
        headline_point = None
    else:
        outcome_status = "scored"
        headline_point = earned_points / 100.0

    mechanic = items.get("unity_mechanic_trace")
    witness = items.get("causal_witness")
    hidden = items.get("unity_hidden_behavior")
    functional_inputs = (mechanic, witness, hidden)
    if any(item is None or item.verdict not in {Verdict.PASSED, Verdict.FAILED, Verdict.MALFORMED}
           for item in functional_inputs):
        functional_complete: bool | None = None
    else:
        functional_complete = all(
            item is not None and item.verdict is Verdict.PASSED and item.credit >= 1.0
            for item in functional_inputs
        )

    lower = earned_points
    upper = earned_points + (100.0 - measured_weight)
    measured_subset_rate = earned_points / measured_weight * 100.0 if measured_weight else None
    weighted_total: dict[str, Any] = {
        **_point_dict_100(headline_point, "complete" if headline_point is not None else "evaluation_incomplete"),
        "headline_ceiling": 100.0,
        "rule": headline_rule(policy.version),
    }
    if headline_point is None:
        weighted_total["unmeasured"] = unmeasured
        weighted_total["detail"] = outcome_status

    strict_resolved = (
        False if candidate_failure or invalid_evidence
        else None if not gates_complete
        else functional_complete
    )
    return {
        "schema": MODE5_SCORECARD_SCHEMA,
        "registry_version": policy.version,
        "registry_status": policy.status,
        "game_id": game_id,
        "mode": mode,
        "score_scope": "mode5_model_capability_only",
        "outcome_status": outcome_status,
        "gates": {
            "infrastructure": infrastructure,
            "submission": submission_gate,
            "validity": validity_gate,
            **({"deliverables": deliverable_gate} if deliverable_gate else {}),
            **({
                "vlm": {
                    "status": "passed" if vlm_complete else "inconclusive",
                    "domain": "visual",
                    "failed_items": [],
                    "unmeasured_items": vlm_missing,
                    "scoring_effect": (
                        "independent structure and visual-quality VLM domains, 15 points each; evaluator gaps require retry"
                        if not vlm_complete else "measured"
                    ),
                }
            } if policy.mode5_mdva_domain else {}),
        },
        "categories": category_rows,
        "leaderboard": _mode5_table_summary(category_rows),
        "weighted_total": weighted_total,
        **({
            "objective_total": {
                "score": round(objective_points, 3) if objective_complete else None,
                "ceiling": 70.0,
                "headline_ceiling": 70.0,
                "status": "complete" if objective_complete else "evaluation_incomplete",
                "unmeasured": objective_missing,
            },
            "vlm_total": {
                "score": round(vlm_points, 3) if vlm_complete else None,
                "ceiling": 30.0,
                "headline_ceiling": 30.0,
                "status": "complete" if vlm_complete else "retry_required",
                "protocols": {
                    "structure": "paired-cross-engine-structure-v2",
                    "mdva": "2026-09-20.game-rubric-v2",
                },
                "unmeasured": vlm_missing,
            },
            "structure_vlm_total": {
                "score": round(sum(
                    float(row["weight_in_total"]) * float((row.get("score") or {}).get("score") or 0.0) / 100.0
                    for row in structure_rows
                ), 3) if not any(
                    row.get("category") == "content_structure" for row in vlm_missing
                ) else None,
                "ceiling": 15.0,
                "status": "complete" if not any(
                    row.get("category") == "content_structure" for row in vlm_missing
                ) else "retry_required",
            },
            "mdva_vlm_total": {
                "score": round(sum(
                    float(row["weight_in_total"]) * float((row.get("score") or {}).get("score") or 0.0) / 100.0
                    for row in mdva_rows
                ), 3) if not any(
                    row.get("category") == "visual_feedback" for row in vlm_missing
                ) else None,
                "ceiling": 15.0,
                "status": "complete" if not any(
                    row.get("category") == "visual_feedback" for row in vlm_missing
                ) else "retry_required",
                "groups": ((items.get("unity_vlm").evidence or {}).get("groups", {})
                           if items.get("unity_vlm") else {}),
            },
        } if policy.mode5_mdva_domain else {}),
        "evaluation_incomplete": headline_point is None,
        "diagnostics": {
            "fixed_weight_bounds": {
                "lo": round(lower, 3),
                "hi": round(upper, 3),
                "scale": "0-100",
            },
            "measured_subset_rate": (
                None if measured_subset_rate is None else round(measured_subset_rate, 3)
            ),
            "earned_points_floor": round(earned_points, 3),
            "unmeasured_weight": round(100.0 - measured_weight, 3),
            "note": (
                "Diagnostics only; measured_subset_rate is not a score. Missing capability "
                "weight is never removed or renormalised into the formal score."
            ),
        },
        "measured_weight_share": round(measured_weight / 100.0, 6),
        "ranking_eligible": (
            headline_point is not None
            and not infrastructure_incomplete
            and quality_complete
        ),
        "ranking_basis": "mode5_full_capability" if quality_complete else "incomplete_capability_evidence",
        "ranking_note": (
            "Do not rank an incomplete Mode-5 capability card."
            if headline_point is None
            else ""
        ),
        "functional_complete": functional_complete,
        "not_applicable_criteria": [],
        "not_applicable_reasons": {},
        "strict": {
            "resolved": strict_resolved,
            "status": "passed" if strict_resolved is True else "failed" if strict_resolved is False else "not_measured",
            "failed_required_items": submission_gate["failed_items"] + validity_gate["failed_items"],
            "not_measured_required_items": (
                infrastructure["unmeasured_items"]
                + submission_gate["unmeasured_items"]
                + validity_gate["unmeasured_items"]
            ),
            "rule": "Mode-5 gates are conjunctive and zero-point; functional completeness cannot be bought back by presentation quality.",
        },
        "reading_rule": (
            "Read zero-point gates first, then the five fixed-weight capability categories. "
            "Unmeasured or evaluator-inconclusive capability evidence withholds the full score; "
            "only pre-registered source-game inapplicability may alter criterion applicability."
        ),
    }


UNMEASURED_AXIS_STATUSES = frozenset({
    "placeholder_uncalibrated",
    "unverified_zero",
    "baseline_unavailable_zero",
    "not_instrumented_zero",
    "legacy_composite",


    "empty_denominator",
})


INCOMPLETE_AXIS_STATUSES = frozenset({
    "unverified_zero", "baseline_unavailable_zero", "not_instrumented_zero",
    "evaluation_incomplete",
})

VISUAL_PLACEHOLDER_WEIGHT = 15.0
VISUAL_PLACEHOLDER_EARNED_POINTS = 5.0


def _reachable_ceiling(axes: Mapping[str, Mapping[str, Any]]) -> dict[str, Any]:


    reachable = legacy = placeholder = unreachable = 0.0
    for row in axes.values():
        weight = float(row.get("weight_in_total") or 0.0)
        status = str(row.get("status") or "")
        if status == "placeholder_uncalibrated":
            placeholder += float(row.get("earned_points") or 0.0)
        elif status == "legacy_composite":
            legacy += weight
        elif status in UNMEASURED_AXIS_STATUSES:
            unreachable += weight
        else:
            reachable += weight
    return {
        "currently_reachable_ceiling": round(reachable + legacy + placeholder, 3),
        "breakdown": {
            "natively_measured_weight": round(reachable, 3),
            "legacy_fallback_weight": round(legacy, 3),
            "placeholder_fixed_award": round(placeholder, 3),
            "unreachable_weight": round(unreachable, 3),
            "note": (
                "currently_reachable_ceiling = natively measured + legacy fallback + "
                "the placeholder's fixed award. Unreachable weight belongs to axes the "
                "evaluator has not implemented; no submission can earn it."
            ),
        },
    }
VISUAL_PLACEHOLDER_CREDIT = VISUAL_PLACEHOLDER_EARNED_POINTS / VISUAL_PLACEHOLDER_WEIGHT
_REDESIGN_OBJECTIVE_WEIGHT_SCALE = 85.0 / 70.0


_MODE2_OBJECTIVE_WEIGHT_SCALE = 85.0 / 68.0


def _redesign_objective_weight(base_weight: float) -> float:

    return round(base_weight * _REDESIGN_OBJECTIVE_WEIGHT_SCALE, 3)


def _mode2_objective_weight(base_weight: float) -> float:

    return round(base_weight * _MODE2_OBJECTIVE_WEIGHT_SCALE, 3)


MODE1_REDESIGN_WEIGHTS: dict[str, float] = {
    "universal_mechanics": _redesign_objective_weight(5.0),
    "asset_realization": _redesign_objective_weight(18.0),
    "rubric_interface": _redesign_objective_weight(7.0),
    "numeric_contract": _redesign_objective_weight(4.0),
    "content_census": _redesign_objective_weight(10.0),
    "causal_witness": _redesign_objective_weight(6.0),
    "task_checkpoints": _redesign_objective_weight(9.0),
    "hidden_scenarios": _redesign_objective_weight(4.0),
    "demo_validity": _redesign_objective_weight(3.0),
    "demo_coverage": _redesign_objective_weight(2.0),
    "gdd_interface": _redesign_objective_weight(1.0),
    "gdd_quality": _redesign_objective_weight(1.0),
    "visual_placeholder": VISUAL_PLACEHOLDER_WEIGHT,
}

MODE1_REDESIGN_CATEGORY_AXES: tuple[tuple[str, str, tuple[str, ...]], ...] = (
    (
        "mechanics_requirements",
        "Mechanics and task fulfillment",
        ("universal_mechanics", "numeric_contract", "task_checkpoints",
         "hidden_scenarios"),
    ),
    (
        "content_realization",
        "Interface, content, and asset realization",
        ("asset_realization", "rubric_interface", "content_census"),
    ),
    (
        "causal_demonstration",
        "Causal playability and demonstrations",
        ("causal_witness", "demo_validity", "demo_coverage"),
    ),
    (
        "authored_design",
        "Candidate-authored design contract",
        ("gdd_interface", "gdd_quality"),
    ),
    (
        "visual_experience",
        "Visual quality placeholder",
        ("visual_placeholder",),
    ),
)

MODE2_REDESIGN_WEIGHTS: dict[str, float] = {
    "universal_mechanics": _mode2_objective_weight(5.0),
    "asset_realization": _mode2_objective_weight(18.0),
    "rubric_interface": _mode2_objective_weight(7.0),
    "numeric_contract": _mode2_objective_weight(4.0),
    "content_structure": _mode2_objective_weight(10.0),
    "causal_witness": _mode2_objective_weight(6.0),
    "task_checkpoints": _mode2_objective_weight(9.0),
    "hidden_scenarios": _mode2_objective_weight(4.0),
    "demo_validity": _mode2_objective_weight(3.0),
    "demo_coverage": _mode2_objective_weight(2.0),


    "gdd_requirement_alignment": 0.0,
    "visual_placeholder": VISUAL_PLACEHOLDER_WEIGHT,
}

MODE2_REDESIGN_CATEGORY_AXES: tuple[tuple[str, str, tuple[str, ...]], ...] = (
    ("mechanics_requirements", "Mechanics and frozen-GDD fulfillment",
     ("universal_mechanics", "numeric_contract", "task_checkpoints",
      "hidden_scenarios")),
    ("content_realization", "Interface, content, and asset realization",
     ("asset_realization", "rubric_interface", "content_structure")),
    ("causal_demonstration", "Causal playability and demonstrations",
     ("causal_witness", "demo_validity", "demo_coverage")),
    ("gdd_alignment", "Non-duplicated frozen-GDD alignment",
     ("gdd_requirement_alignment",)),
    ("visual_experience", "Visual quality placeholder", ("visual_placeholder",)),
)

MODE3_REDESIGN_WEIGHTS: dict[str, float] = {
    "universal_mechanics": _redesign_objective_weight(5.0),
    "asset_realization": _redesign_objective_weight(18.0),
    "numeric_contract": _redesign_objective_weight(4.0),
    "content_structure": _redesign_objective_weight(10.0),
    "task_checkpoints": _redesign_objective_weight(9.0),
    "causal_witness": _redesign_objective_weight(4.0),
    "hidden_scenarios": _redesign_objective_weight(8.0),
    "demo_validity": _redesign_objective_weight(2.0),
    "demo_coverage": _redesign_objective_weight(1.0),
    "stub_completion": _redesign_objective_weight(4.0),
    "transformation_contract": _redesign_objective_weight(3.0),
    "scaffold_integration": _redesign_objective_weight(2.0),
    "visual_placeholder": VISUAL_PLACEHOLDER_WEIGHT,
}

MODE3_REDESIGN_CATEGORY_AXES: tuple[tuple[str, str, tuple[str, ...]], ...] = (
    ("mechanics_requirements", "Incremental mechanics and task fulfillment",
     ("universal_mechanics", "numeric_contract", "task_checkpoints", "hidden_scenarios")),
    ("content_realization", "Incremental content and asset realization",
     ("asset_realization", "content_structure")),
    ("causal_demonstration", "Causal playability and demonstrations",
     ("causal_witness", "demo_validity", "demo_coverage")),
    ("scaffold_completion", "Stub, transformation, and scaffold integration",
     ("stub_completion", "transformation_contract", "scaffold_integration")),
    ("visual_experience", "Visual quality placeholder", ("visual_placeholder",)),
)


def _read_json_mapping(path: Path | None) -> dict[str, Any]:
    if path is None:
        return {}
    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError, TypeError):
        return {}
    return dict(raw) if isinstance(raw, dict) else {}


def _mode4_difficulty_context(result: Any) -> dict[str, Any]:


    injected = getattr(result, "mode4_context", None)
    if isinstance(injected, Mapping):
        return dict(injected)
    package = getattr(result, "package", None)
    root = getattr(package, "root", None)
    if root:
        oracle = _read_json_mapping(Path(root) / "hidden" / "oracle.json")
        case = oracle.get("mode4_case")
        if isinstance(case, Mapping):
            return dict(case)
    stored = getattr(result, "stored_report", None)
    if isinstance(stored, Mapping):
        previous = (stored.get("scorecard") or {}).get("difficulty_evidence")
        if isinstance(previous, Mapping):
            return {
                "case_id": previous.get("case_id"),
                "difficulty": {"tier": previous.get("tier")},
            }
    return {}


def _mode4_difficulty_evidence(
    result: Any, repair_score: float | None,
) -> dict[str, Any]:


    context = _mode4_difficulty_context(result)
    difficulty = context.get("difficulty") or {}
    tier = str(difficulty.get("tier") or "").strip().lower()
    tier_points = MODE4_PROVISIONAL_TIER_POINTS.get(tier)
    base = {
        "case_id": str(context.get("case_id") or ""),
        "tier": tier or None,
        "calibration_status": "author_provisional_not_ranking_eligible",
        "ranking_eligible": False,
        "rule": (
            "earned_evidence_points = repair_correctness_fraction x provisional "
            "tier_points (easy=1, medium=2, hard=3); ordinal development diagnostic "
            "only, never a replacement for repair correctness"
        ),
    }
    if tier_points is None:
        return {**base, "status": "not_measured", "detail": "package has no recognised Mode-4 tier"}
    earned = None if repair_score is None else tier_points * float(repair_score) / 100.0
    return {
        **base,
        "status": "evaluation_incomplete" if earned is None else "complete",
        "tier_points": tier_points,
        "earned_evidence_points": None if earned is None else round(earned, 6),
        "max_evidence_points": tier_points,
    }


def _mode1_evidence_context(result: Any, items: Mapping[str, Item]) -> dict[str, Any]:


    injected = getattr(result, "mode1_context", None)
    context = dict(injected) if isinstance(injected, Mapping) else {}
    stored = getattr(result, "stored_report", None)
    stored = dict(stored) if isinstance(stored, Mapping) else {}
    report_path_raw = getattr(result, "report_path", None)
    report_path = Path(report_path_raw) if report_path_raw else None

    package_root_raw = getattr(getattr(result, "package", None), "root", None)
    package_root = Path(package_root_raw) if package_root_raw else None
    if (package_root is None or not package_root.exists()) and report_path is not None:
        candidate = report_path.parent.parent / "package"
        if candidate.exists():
            package_root = candidate

    oracle = context.get("oracle")
    if not isinstance(oracle, Mapping):
        oracle = _read_json_mapping(
            package_root / "hidden" / "oracle.json" if package_root is not None else None
        )
    oracle = dict(oracle) if isinstance(oracle, Mapping) else {}
    rubric = context.get("rubric")
    if not isinstance(rubric, Mapping):
        rubric = oracle.get("rubric") or {}
    context["rubric"] = dict(rubric) if isinstance(rubric, Mapping) else {}

    candidate_truth = context.get("candidate_truth")
    if not isinstance(candidate_truth, Mapping):
        truth_paths: list[Path] = []
        reproduction = items.get("reproduction")
        evidence = reproduction.evidence if reproduction and isinstance(reproduction.evidence, dict) else {}
        reproduction_path = evidence.get("path")
        if reproduction_path:
            truth_paths.append(Path(str(reproduction_path)) / "truth_snapshot.json")
        if report_path is not None:
            truth_paths.append(report_path.parent / "reproduction" / "truth_snapshot.json")
        candidate_truth = next(
            (payload for payload in (_read_json_mapping(path) for path in truth_paths) if payload),
            {},
        )
    context["candidate_truth"] = dict(candidate_truth) if isinstance(candidate_truth, Mapping) else {}

    reference_truth = context.get("reference_truth")
    if not isinstance(reference_truth, Mapping):
        reference_paths: list[Path] = []
        registered = oracle.get("registered_task") or {}
        if isinstance(registered, Mapping) and registered.get("task_dir"):
            reference_paths.append(Path(str(registered["task_dir"])) / "snapshot.json")
        game_id = str(getattr(result.package, "manifest", {}).get("game_id") or "")
        if game_id:
            reference_paths.append(
                Path(__file__).resolve().parents[4] / "eval" / "tasks" / game_id / "snapshot.json"
            )
        reference_truth = next(
            (payload for payload in (_read_json_mapping(path) for path in reference_paths) if payload),
            {},
        )
    context["reference_truth"] = dict(reference_truth) if isinstance(reference_truth, Mapping) else {}

    demonstrations = context.get("demonstrations")
    if demonstrations is None:
        demonstrations = stored.get("demonstrations")
    if demonstrations is None:
        engine = getattr(result, "engine", None)
        demonstrations = engine.get("demonstration_summary") if isinstance(engine, Mapping) else None
    context["demonstrations"] = demonstrations

    if "feature_demos" not in context:
        protocol = str(stored.get("witness_protocol") or "")
        submission = getattr(result, "submission", None)
        demos_path = getattr(submission, "demos_path", None)
        context["feature_demos"] = protocol == "gamebench.feature-demos.v1" or demos_path is not None


    if report_path is not None and "report_path" not in context:
        context["report_path"] = str(report_path)
    return context


def _truth_group_counts(snapshot: Mapping[str, Any]) -> dict[str, int]:
    counts: dict[str, int] = {}
    for level in snapshot.get("levels") or []:
        if not isinstance(level, Mapping):
            continue
        for group, reading in (level.get("groups") or {}).items():
            if not isinstance(reading, Mapping):
                continue
            value = reading.get("alive", reading.get("total", 0))
            try:
                counts[str(group)] = counts.get(str(group), 0) + max(0, int(value or 0))
            except (TypeError, ValueError):
                continue
    return counts


def _mode1_item_credit(items: Mapping[str, Item], item_id: str) -> float | None:
    item = items.get(item_id)
    if item is None:
        return None
    if item.verdict in {
        Verdict.INCONCLUSIVE, Verdict.UNMEASURABLE, Verdict.UNOBSERVABLE,
    }:


        if item.attribution is Attribution.SUBMISSION:
            return 0.0
        return None
    if item.verdict in {Verdict.PASSED, Verdict.FAILED}:
        return float(item.credit)
    return 0.0


def _mode1_rubric_group_credit(
    context: Mapping[str, Any], items: Mapping[str, Item],
) -> tuple[float | None, dict[str, Any]]:
    required = [str(value) for value in (context.get("rubric") or {}).get("required_groups") or []]
    if not required:
        return None, {"detail": "hidden rubric has no recoverable required_groups denominator"}
    item = items.get("rubric_interface")
    if item is None:
        return None, {"detail": "rubric_interface item is missing"}
    if _mode1_item_credit(items, "rubric_interface") is None:
        return None, {"required": required, "detail": item.detail,
                      "rubric_check": dict(item.evidence or {})}
    evidence = item.evidence or {}
    missing = set(str(value) for value in evidence.get("missing_required_groups") or [])
    if not missing:
        findings = evidence.get("findings") or []
        for group in required:
            if any(f"required group {group} is absent" in str(finding) for finding in findings):
                missing.add(group)
    if item.verdict is Verdict.PASSED:
        missing.clear()
    observed = [group for group in required if group not in missing]
    return len(observed) / len(required), {
        "required": required,
        "observed": observed,
        "missing": sorted(missing),
        "formula": "observed required semantic groups / required semantic groups",
    }


def _mode1_content_census_credit(
    context: Mapping[str, Any], *, scaffold: Mapping[str, int] | None = None,
) -> tuple[float | None, dict[str, Any]]:


    rubric = context.get("rubric") or {}
    required = [str(value) for value in rubric.get("required_groups") or []]
    candidate_truth = context.get("candidate_truth") or {}
    reference_truth = context.get("reference_truth") or {}
    if not required or not candidate_truth or not reference_truth:
        return None, {"detail": "rubric, candidate truth, or reference truth snapshot is missing"}
    candidate = _truth_group_counts(candidate_truth)
    reference = _truth_group_counts(reference_truth)
    rows: list[dict[str, Any]] = []
    credits: list[float] = []
    for group in required:
        given = int((scaffold or {}).get(group, 0))
        raw_candidate = int(candidate.get(group, 0))
        remaining = int(reference.get(group, 0)) - given
        row: dict[str, Any] = {"group": group}
        if scaffold is not None:
            row["scaffold_given"] = given
            row["candidate_raw"] = raw_candidate
        if scaffold is not None and remaining <= 0:


            row.update({"applicable": False,
                        "detail": "the scaffold already meets the reference for this role"})
            rows.append(row)
            continue
        actual = max(0, raw_candidate - given)
        target = max(1, remaining) if scaffold is not None else max(1, int(reference.get(group, 0)))
        credit = min(actual, target) / max(actual, target) if max(actual, target) else 1.0
        credits.append(credit)
        row.update({"candidate": actual, "target": target, "credit": round(credit, 6)})
        rows.append(row)
    evidence: dict[str, Any] = {
        "roles": rows,
        "formula": "macro mean of min(candidate,target)/max(candidate,target); rubric-required target floor is 1",
    }
    if scaffold is not None:
        evidence["baseline"] = "candidate and target are both net of what the scaffold already shipped"
        evidence["graded_roles"] = len(credits)
    if not credits:
        return None, {
            **evidence,
            "detail": "the scaffold already meets the reference for every required role",
        }
    return sum(credits) / len(credits), evidence


def _scaffold_group_counts(context: Mapping[str, Any], game_id: str) -> dict[str, int] | None:


    injected = context.get("scaffold_truth")
    if isinstance(injected, Mapping):
        return _truth_group_counts(injected)
    if not game_id:
        return None
    path = (
        Path(__file__).resolve().parents[4]
        / "eval" / "tasks" / game_id / "scaffold_snapshot.json"
    )
    payload = _read_json_mapping(path)
    if not payload:
        return None
    return _truth_group_counts(payload)


def _unreadable_reason(items: Mapping[str, Item] | None, axis_id: str) -> str | None:


    if not items:
        return None
    unread = {
        item.id: str(item.detail or "")
        for item in items.values()
        if item.verdict in {
            Verdict.INCONCLUSIVE, Verdict.UNMEASURABLE, Verdict.ERROR,
        } and item.detail and (axis_id == "task_visual" or item.id != "task_visual")
    }
    if axis_id in unread:
        return unread[axis_id]
    shared = set(unread.values())
    return shared.pop() if len(shared) == 1 else None


def _attribute_candidate_runtime_failure(
    axes: Mapping[str, dict[str, Any]], card: Mapping[str, Any],
    context: Mapping[str, Any],
) -> None:


    cold = next((row for row in card.get("channels", [])
                 if row.get("channel") == "O1"), {})
    interface = next((row for row in card.get("channels", [])
                      if row.get("channel") == "O2"), {})
    assets = next((row for row in card.get("channels", [])
                   if row.get("channel") == "O6"), {})
    levels = (context.get("candidate_truth") or {}).get("levels") or []
    runtime_axes = (
        "universal_mechanics", "asset_realization", "numeric_contract",
        "content_census", "content_structure", "causal_witness",
        "task_checkpoints", "hidden_scenarios",
    )
    if cold.get("measured") is True and "cold_import=failed" in {
        part.strip() for part in str(cold.get("note") or "").split(";")
    }:
        failure = "candidate_cold_import_failed"
        explanation = "candidate cold import failed, preventing this runtime measurement"
        evidence = {"candidate_cold_import": dict(cold)}
    elif levels and all(level.get("stop_reason") == "scene_load_failed"
                        and not level.get("reached") for level in levels):
        failure = "candidate_scene_load_failed"
        explanation = "every declared candidate scene failed to load, preventing this runtime measurement"
        evidence = {"candidate_scene_load": levels}
    elif (levels and levels[0].get("stop_reason") == "scene_load_failed"
          and not levels[0].get("reached")
          and str(assets.get("note") or "").startswith(
              "no engine reading: asset probe produced no engine usage report "
              "(returncode=1, timed_out=False,"
          )):


        runtime_axes = ("asset_realization",)
        failure = "candidate_asset_start_scene_failed"
        explanation = "the asset probe's starting scene failed to load"
        evidence = {"candidate_asset_start_scene": levels[0], "asset_probe": dict(assets)}
    elif (interface.get("measured") is True and "levels=failed" in {
        part.strip() for part in str(interface.get("note") or "").split(";")
    } and levels and all(
        isinstance(level.get("declared_scene"), str)
        and bool(level["declared_scene"])
        and not level["declared_scene"].startswith("res://")
        and level.get("stop_reason") == "no_record"
        and not level.get("reached") for level in levels
    )):


        failure = "candidate_invalid_level_manifest"
        explanation = "every declared candidate level has an invalid scene address, preventing this runtime measurement"
        evidence = {"candidate_interface": dict(interface), "candidate_levels": levels}
    elif (interface.get("measured") is True and "levels=failed" in {
        part.strip() for part in str(interface.get("note") or "").split(";")
    } and assets.get("note") == "no engine reading: normalized interface has no level address"):


        runtime_axes = ("asset_realization",)
        failure = "candidate_missing_level_manifest"
        explanation = "the candidate declared no playable level for the asset probe"
        evidence = {"candidate_interface": dict(interface), "asset_probe": dict(assets)}
    else:
        return
    for axis_id in runtime_axes:
        row = axes.get(axis_id)
        if row is None or (row["credit"] is not None
                           and row["status"] not in INCOMPLETE_AXIS_STATUSES):
            continue
        row.update(credit=0.0, earned_points=0.0, status="candidate_failure")
        row["detail"] += "; " + explanation
        row["evidence"] = {
            **row["evidence"], **evidence, "failure_code": failure,
        }


def _name_unreadable_axes(
    axes: Mapping[str, dict[str, Any]],
    items: Mapping[str, Item] | None = None,
) -> list[dict[str, Any]]:


    named: list[dict[str, Any]] = []
    for row in axes.values():
        if row["credit"] is not None and row["status"] not in INCOMPLETE_AXIS_STATUSES:
            continue
        reason = (
            _unreadable_reason(items, str(row["id"]))
            or (row.get("evidence") or {}).get("detail")
        )
        named.append({
            "category": "unreadable_axis",
            "criterion": row["id"],
            "source": row["id"],


            "step": reason or row["detail"],
            "axis_measures": row["detail"],
            **({"reason": reason} if reason else {}),
        })
        row["credit"] = 0.0
        row["earned_points"] = 0.0
        if row["status"] not in UNMEASURED_AXIS_STATUSES:
            row["status"] = "not_instrumented_zero"
        row["detail"] = (
            (row["detail"] + "; " if row["detail"] else "")
            + "no reading was produced, so this axis earns nothing and its weight "
              "leaves the reachable ceiling rather than voiding the whole card"
        )
    return named


def _fixed_axis_row(
    weights: Mapping[str, float],
    axis_id: str,
    credit: float | None,
    *,
    status: str = "measured",
    detail: str = "",
    evidence: Mapping[str, Any] | None = None,
) -> dict[str, Any]:
    weight = weights[axis_id]
    return {
        "id": axis_id,
        "name": axis_id.replace("_", " ").title(),
        "weight_in_total": weight,
        "credit": None if credit is None else round(credit, 6),
        "earned_points": None if credit is None else round(weight * credit, 6),
        "status": "evaluation_incomplete" if credit is None else status,
        "detail": detail,
        "evidence": dict(evidence or {}),
    }


def _mode1_axis_row(
    axis_id: str,
    credit: float | None,
    **kwargs: Any,
) -> dict[str, Any]:
    return _fixed_axis_row(MODE1_REDESIGN_WEIGHTS, axis_id, credit, **kwargs)


def _mode1_gate_status(
    items: Mapping[str, Item], card: Mapping[str, Any], policy: RegistryPolicy,
) -> tuple[str, list[dict[str, Any]]]:


    rows: list[dict[str, Any]] = []
    o1_interval, o1 = _source_interval(SourceSpec("ocard", "O1"), items, card, policy)
    o1_point = o1_interval.point if o1.get("point") is not None else None
    if o1_point is None:
        rows.append({"id": "runtime_viability", "status": "inconclusive", "scope": "domain_local", "detail": o1.get("unmeasured_step", "O1 missing")})
    elif o1_point < 0.75:
        rows.append({"id": "runtime_viability", "status": "failed", "scope": "domain_local", "detail": f"O1 point {o1_point:.3f} is below the import/boot/draw threshold"})
    else:
        rows.append({"id": "runtime_viability", "status": "passed", "scope": "domain_local", "detail": f"O1 point {o1_point:.3f}"})

    integrity_ids = {"layout", "no_eval_smuggling", "anti_grant_static", "auto_win_ready"}
    for item_id in (
        "layout", "no_eval_smuggling", "anti_grant_static", "auto_win_ready",
        "interface", "null_no_win", "anti_grant_diff", "verifier_profile_complete",
    ):
        item = items.get(item_id)
        scope = "integrity" if item_id in integrity_ids else "domain_local"
        if item is None:
            rows.append({"id": item_id, "status": "not_instrumented", "scope": scope,
                         "detail": "item missing from this registry/report"})
        elif item.verdict in {
            Verdict.INCONCLUSIVE, Verdict.UNMEASURABLE, Verdict.UNOBSERVABLE,
        }:
            rows.append({"id": item_id, "status": "inconclusive", "scope": scope, "detail": item.detail})
        elif item.verdict is Verdict.PASSED:
            rows.append({"id": item_id, "status": "passed", "scope": scope, "detail": item.detail})
        else:
            rows.append({"id": item_id, "status": "failed", "scope": scope, "detail": item.detail})

    mash = items.get("extended_mash_no_win")
    if mash is None or mash.verdict in {Verdict.INCONCLUSIVE, Verdict.UNMEASURABLE}:
        rows.append({"id": "extended_mash_no_win", "status": "inconclusive", "scope": "domain_local", "detail": mash.detail if mash else "item missing"})
    elif mash.verdict in {Verdict.UNOBSERVABLE, Verdict.EXEMPT}:
        rows.append({"id": "extended_mash_no_win", "status": "not_applicable", "scope": "domain_local", "detail": mash.detail})
    elif mash.verdict is Verdict.PASSED:
        rows.append({"id": "extended_mash_no_win", "status": "passed", "scope": "domain_local", "detail": mash.detail})
    else:
        rows.append({"id": "extended_mash_no_win", "status": "failed", "scope": "domain_local", "detail": mash.detail})

    integrity = [row for row in rows if row.get("scope") == "integrity"]
    if any(row["status"] == "failed" for row in integrity):
        return "gated", rows
    if any(row["status"] == "inconclusive" for row in integrity):
        return "inconclusive", rows
    return "complete", rows


def _mode1_strict_status(
    items: Mapping[str, Item], evaluation_status: str, *, feature_demos: bool,
) -> dict[str, Any]:
    required = [
        "gdd", "authored_gdd_interface", "brief_gdd_grounding", "rubric_interface",
        "ops_present", "ops_valid", "ops_not_idle", "mechanic_trace", "causal_witness",
    ]
    if feature_demos:
        required.append("demonstrations_complete")
    failed: list[str] = []
    unmeasured: list[str] = []
    for item_id in required:
        item = items.get(item_id)
        if item is None or item.verdict in {
            Verdict.INCONCLUSIVE, Verdict.UNMEASURABLE, Verdict.UNOBSERVABLE,
        }:
            unmeasured.append(item_id)
        elif item.verdict is not Verdict.PASSED or item.credit < 1.0:
            failed.append(item_id)
    if evaluation_status == "gated" or failed:
        resolved: bool | None = False
    elif evaluation_status == "inconclusive" or unmeasured:
        resolved = None
    else:
        resolved = True
    return {
        "resolved": resolved,
        "status": "passed" if resolved is True else "failed" if resolved is False else "not_measured",
        "failed_required_items": sorted(failed),
        "not_measured_required_items": sorted(unmeasured),
        "rule": "Mode-1 completion is conjunctive and separate from the fixed-weight capability score",
    }


def _score_mode1_redesign(result: Any, policy: RegistryPolicy) -> dict[str, Any]:
    items = _items_by_id(result.items)
    card = _reproduction_card(items)
    context = _mode1_evidence_context(result, items)

    game_id = str(result.package.manifest.get("game_id") or "")
    o3_interval, o3_source = _source_interval(SourceSpec("ocard", "O3"), items, card, policy)
    mechanics, mechanics_evidence, mechanics_status = _universal_mechanics_credit(
        context, items, game_id, mode="brief", scaffold_relative=False,
    )
    mechanics_evidence["superseded_source"] = {
        "note": "the mixed U+H O3 reading this axis used to square, kept for comparison",
        **o3_source,
    }
    o6_interval, o6_source = _source_interval(SourceSpec("ocard", "O6"), items, card, policy)
    assets = o6_interval.point if o6_source.get("point") is not None else None
    rubric_credit, rubric_evidence = _mode1_rubric_group_credit(context, items)
    census_credit, census_evidence = _mode1_content_census_credit(context)
    checkpoint = _mode1_item_credit(items, "mechanic_trace")
    causal = _mode1_item_credit(items, "causal_witness")
    milestone, milestone_evidence, milestone_status = _gt_milestone_credit(
        context, items, game_id,
    )
    hidden, hidden_evidence, hidden_status = _hidden_scenario_credit(context, items, game_id)
    numeric, numeric_evidence, numeric_status = _numeric_contract_credit(context, items)

    ops_valid = _mode1_item_credit(items, "ops_valid")
    ops_not_idle = _mode1_item_credit(items, "ops_not_idle")
    ops_present = _mode1_item_credit(items, "ops_present")
    candidate_ops_failure = any(value is not None and value <= 0
                                for value in (ops_present, ops_valid, ops_not_idle))
    demo_validity, demo_coverage, demo_evidence = _demo_credits(context, items, checkpoint)
    demo_status = "candidate_failure" if demo_evidence.get("failure_code") else "measured"

    axes = {
        "universal_mechanics": _mode1_axis_row(
            "universal_mechanics",
            mechanics if mechanics_status == "measured" else 0.0,
            status=mechanics_status,
            detail=(
                "universal assertions U1-U7 only; the H class is scored on task_checkpoints"
                if mechanics_status == "measured"
                else "no readable engine-truth snapshot, so the U class was not measured"
            ),
            evidence=mechanics_evidence,
        ),
        "asset_realization": _mode1_axis_row(
            "asset_realization", assets,
            detail="runtime-loaded reference asset coverage (O6), linear",
            evidence={"source": o6_source},
        ),
        "rubric_interface": _mode1_axis_row(
            "rubric_interface", rubric_credit,
            detail="required semantic-group coverage; base actions/endings are zero-point gates",
            evidence=rubric_evidence,
        ),


        # `unverified_zero` in all 41 cells and left the reachable ceiling --


        "numeric_contract": _mode1_axis_row(
            "numeric_contract",
            numeric if numeric_status == "measured" else 0.0,
            status=("candidate_failure" if numeric_status in INCOMPLETE_AXIS_STATUSES and candidate_ops_failure
                    else numeric_status),
            detail=(
                "required slots the candidate's own frames move, gated on separation "
                "from the matched null"
                if numeric_status == "measured"
                else "declared/resolvable slots do not prove candidate-caused delta against matched null"
            ),
            evidence=numeric_evidence,
        ),
        "content_census": _mode1_axis_row(
            "content_census", census_credit,
            detail="required-role runtime census against the frozen reference snapshot",
            evidence=census_evidence,
        ),
        "causal_witness": _mode1_axis_row(
            "causal_witness", causal,
            detail="candidate action-caused coverage against a matched no-input control",
            evidence=(items.get("causal_witness").evidence if items.get("causal_witness") else {}),
        ),
        "task_checkpoints": _mode1_axis_row(
            "task_checkpoints",
            milestone if milestone_status in {"measured", "empty_denominator"} else 0.0,
            status=milestone_status,
            detail=(
                "certified-route checkpoints reached; the brief's own mechanic_checks "
                "are recorded in evidence but not scored"
                if milestone_status == "measured"
                else "no certified-route checkpoint reading; the disclosed mechanic_checks are not substituted"
            ),
            evidence=milestone_evidence,
        ),
        "hidden_scenarios": _mode1_axis_row(
            "hidden_scenarios",
            hidden if hidden_status in {"measured", "empty_denominator"} else 0.0,
            status=hidden_status,
            detail=(
                "certified routes, authored against the reference rather than the brief, "
                "that the submission can be driven through; the no-input control gates"
                if hidden_status == "measured"
                else "no certified-route replay reading was emitted"
            ),
            evidence=hidden_evidence,
        ),
        "demo_validity": _mode1_axis_row(
            "demo_validity", demo_validity,
            status=demo_status,
            detail="valid and non-idle submitted demonstration contract",
            evidence=demo_evidence if demo_status == "candidate_failure" else None,
        ),
        "demo_coverage": _mode1_axis_row(
            "demo_coverage", demo_coverage,
            status=demo_status,
            detail="required features covered by valid feature demonstrations",
            evidence=demo_evidence,
        ),
        "gdd_interface": _mode1_axis_row(
            "gdd_interface", _mode1_item_credit(items, "authored_gdd_interface"),
            detail="authored GDD declarations agree with the submitted interface",
        ),
        "gdd_quality": _mode1_axis_row(
            "gdd_quality", _mode1_item_credit(items, "authored_gdd_quality"),
            detail="structured authored-GDD quality audit",
        ),
        "visual_placeholder": _mode1_axis_row(
            "visual_placeholder", VISUAL_PLACEHOLDER_CREDIT,
            status="placeholder_uncalibrated",
            detail="fixed 5/15 points; not a visual quality judgment",
            evidence={"earned": VISUAL_PLACEHOLDER_EARNED_POINTS,
                      "ceiling": VISUAL_PLACEHOLDER_WEIGHT},
        ),
    }

    _attribute_candidate_runtime_failure(axes, card, context)
    missing = _name_unreadable_axes(axes, items)
    total = sum(float(row["earned_points"]) for row in axes.values())
    categories: list[dict[str, Any]] = []
    for category_id, name, axis_ids in MODE1_REDESIGN_CATEGORY_AXES:
        rows = [axes[axis_id] for axis_id in axis_ids]
        weight = sum(row["weight_in_total"] for row in rows)
        category_score = (
            None if any(row["credit"] is None for row in rows)
            else 100.0 * sum(float(row["earned_points"]) for row in rows) / weight
        )
        categories.append({
            "id": category_id,
            "name": name,
            "weight_in_total": weight,
            "score": _point_dict_100(None if category_score is None else category_score / 100.0),
            "measured_weight_share": round(
                sum(row["weight_in_total"] for row in rows
                    if row["credit"] is not None and row["status"] not in UNMEASURED_AXIS_STATUSES)
                / weight, 6
            ),
            "criteria": [
                {
                    **row,
                    "weight_within_category": round(100.0 * row["weight_in_total"] / weight, 6),
                    "score": _point_dict_100(row["credit"], row["status"]),
                }
                for row in rows
            ],
        })

    evaluation_status, gates = _mode1_gate_status(items, card, policy)
    strict = _mode1_strict_status(
        items, evaluation_status, feature_demos=bool(context.get("feature_demos")),
    )


    score_complete = not missing
    reachable = _reachable_ceiling(axes)
    weighted_total: dict[str, Any] = {
        "score": round(total, 3),
        "status": "complete" if score_complete else "evaluation_incomplete",
        "scale": "0-100",
        "headline_ceiling": 90.0,
        "currently_reachable_ceiling": reachable["currently_reachable_ceiling"],
        "reachable_ceiling_breakdown": reachable["breakdown"],
        "rule": headline_rule(policy.version),
    }
    if missing:
        weighted_total["unmeasured"] = [
            {**row, "category": "mode1_fixed_axes"} for row in missing
        ]

    measured_weight = sum(
        row["weight_in_total"] for row in axes.values()
        if row["credit"] is not None and row["status"] not in UNMEASURED_AXIS_STATUSES
    )
    ranking_eligible = score_complete and evaluation_status == "complete"
    return {
        "schema": SCORECARD_SCHEMA,
        "registry_version": policy.version,
        "registry_status": policy.status,
        "score_scope": "mode1_fixed_weight_capability_with_placeholders",
        "visual_protocol": "uncalibrated fixed placeholder: 5 earned points of 15",
        "game_id": str(result.package.manifest.get("game_id") or ""),
        "mode": "brief",
        "evaluation_status": evaluation_status,
        "gates": gates,
        "categories": categories,
        "axes": list(axes.values()),
        "weighted_total": weighted_total,
        "evaluation_incomplete": not score_complete,
        "diagnostic": {
            "measured_capability_weight": measured_weight,


            "placeholder_weight": VISUAL_PLACEHOLDER_WEIGHT,
            "note": (
                "visual is fixed at 5/15; numeric_contract is measured from the "
                "candidate-versus-matched-null contrast and reports its own status"
            ),
        },
        "measured_weight_share": round(measured_weight / 100.0, 6),
        "ranking_eligible": ranking_eligible,
        "ranking_basis": "objective_plus_fixed_uncalibrated_visual_placeholder",
        "ranking_note": (
            "The provisional score may be compared only within Mode 1 under this exact registry; "
            "strict completion remains separate."
            if ranking_eligible else
            "The score is diagnostic because a hard gate or evaluator measurement is incomplete."
        ),
        "not_applicable_criteria": [],
        "not_applicable_reasons": {},
        "strict": strict,
        "reading_rule": (
            "Read evaluation_status first, strict.resolved second, then the twelve fixed-weight axes; "
            "no per-cell renormalisation is performed."
        ),
    }


def _ocard_credit(
    channel: str, items: Mapping[str, Item], card: Mapping[str, Any], policy: RegistryPolicy,
) -> tuple[float | None, dict[str, Any]]:
    interval, source = _source_interval(SourceSpec("ocard", channel), items, card, policy)
    return (interval.point if source.get("point") is not None else None), source


def _demo_credits(
    context: Mapping[str, Any], items: Mapping[str, Item], checkpoint: float | None,
) -> tuple[float | None, float | None, dict[str, Any]]:
    valid = _mode1_item_credit(items, "ops_valid")
    non_idle = _mode1_item_credit(items, "ops_not_idle")
    validity = None if valid is None or non_idle is None else min(valid, non_idle)
    demonstrations = context.get("demonstrations")
    evidence = dict(demonstrations) if isinstance(demonstrations, Mapping) else {}
    failed_inputs = [
        name for name in ("ops_present", "ops_valid", "ops_not_idle")
        if (credit := _mode1_item_credit(items, name)) is not None and credit <= 0
    ]
    if failed_inputs:


        evidence.update(
            failure_code="candidate_demo_input_failed", input_failures=failed_inputs,
            detail="required demonstration input is missing, invalid, or idle",
            measured=False, coverage=0.0, observed=[],
        )
        if context.get("feature_demos") and "expected" not in evidence:
            evidence["expected"] = [m.name for m in rubric_milestones(context.get("rubric") or {})]
        return 0.0, 0.0, evidence
    if context.get("feature_demos"):
        coverage = (
            float(demonstrations["coverage"])
            if isinstance(demonstrations, Mapping)
            and demonstrations.get("measured") is not False
            and demonstrations.get("coverage") is not None
            else None
        )
    else:
        coverage = checkpoint if validity is not None else None
    return validity, coverage, evidence


def _content_structure_credit(
    context: Mapping[str, Any], items: Mapping[str, Item], card: Mapping[str, Any],
    policy: RegistryPolicy, *, scaffold: Mapping[str, int] | None = None,
) -> tuple[float | None, dict[str, Any]]:
    census, census_evidence = _mode1_content_census_credit(context, scaffold=scaffold)
    topology, topology_source = _ocard_credit("O4", items, card, policy)
    spatial, spatial_source = _ocard_credit("O5", items, card, policy)


    census_applicable = not (
        scaffold is not None and census is None and census_evidence.get("graded_roles") == 0
    )
    parts = [(5.0, census), (3.0, topology), (2.0, spatial)] if census_applicable else [
        (3.0, topology), (2.0, spatial)
    ]
    credit = None if any(value is None for _weight, value in parts) else (
        sum(weight * float(value) for weight, value in parts)
        / sum(weight for weight, _value in parts)
    )
    formula = (
        "(5*census + 3*topology + 2*stated_spatial_relations) / 10"
        if census_applicable
        else "(3*topology + 2*stated_spatial_relations) / 5; census is not applicable"
    )
    return credit, {
        "components": {
            "census": {
                "weight": 5.0 if census_applicable else 0.0,
                "credit": census,
                "applicable": census_applicable,
                "evidence": census_evidence,
            },
            "topology": {"weight": 3.0, "credit": topology, "source": topology_source},
            "stated_spatial_relations": {
                "weight": 2.0, "credit": spatial, "source": spatial_source,
            },
        },
        "formula": formula,
    }


GDD_ATOM_SINKS: dict[str, tuple[tuple[str, ...], ...]] = {
    "task_checkpoints": (
        ("milestones", "[]", "milestone"),
    ),
    "rubric_interface": (
        ("required",), ("present",), ("missing",), ("groups",), ("observed",),
    ),
    "numeric_contract": (
        ("slots", "[]", "slot"), ("required",),
    ),
    "demo_coverage": (("expected",),),
}


def _axis_graded_names(axes: Mapping[str, Mapping[str, Any]], axis_id: str) -> set[str]:

    evidence = (axes.get(axis_id) or {}).get("evidence") or {}
    names: set[str] = set()
    for path in GDD_ATOM_SINKS.get(axis_id, ()):
        node: Any = evidence
        field: str | None = None
        for index, key in enumerate(path):
            if key == "[]":
                field = path[index + 1] if index + 1 < len(path) else None
                break
            if not isinstance(node, Mapping):
                node = None
                break
            node = node.get(key)
        if field is not None:
            if isinstance(node, list):
                for row in node:
                    if isinstance(row, Mapping) and row.get(field) is not None:
                        names.add(str(row[field]))
            continue
        if isinstance(node, list):
            names.update(str(x) for x in node)
        elif isinstance(node, Mapping):
            names.update(str(x) for x in node)
    return names


def _gdd_atom_coverage(
    items: Mapping[str, Item], axes: Mapping[str, Mapping[str, Any]],
) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:


    item = items.get("gdd_mechanics_observable")
    evidence = dict(item.evidence or {}) if item is not None else {}
    unmeasurable = {str(x) for x in (evidence.get("unmeasurable_mechanics") or ())}
    graded: list[dict[str, Any]] = []
    orphaned: list[dict[str, Any]] = []
    cache: dict[str, set[str]] = {}
    for row in _gdd_atom_rows(evidence):
        axis_id = row["primary_axis"]
        if axis_id == "gdd_requirement_alignment":
            continue
        rid = row["requirement_id"]
        bare = rid.partition(":")[2] or rid
        if axis_id not in cache:
            cache[axis_id] = _axis_graded_names(axes, axis_id)
        names = cache[axis_id]
        entry = {"requirement_id": rid, "owning_axis": axis_id, "status": row["status"]}
        if bare in names or rid in names:
            graded.append(entry)
        elif (rid.startswith("mechanic:")
              and {bare, rid} & _axis_graded_names(axes, "demo_coverage")):


            entry.update(declared_axis=axis_id, owning_axis="demo_coverage")
            graded.append(entry)
        elif bare in unmeasurable or rid in unmeasurable:
            entry["reason"] = "the rubric declares this check unmeasurable"
            graded.append(entry)
        else:
            orphaned.append(entry)
    return graded, orphaned


def _gdd_unmapped_alignment_credit(
    items: Mapping[str, Item],
    axes: Mapping[str, Mapping[str, Any]] | None = None,
) -> tuple[float, dict[str, Any]]:


    item = items.get("gdd_mechanics_observable")
    if item is None:
        return 0.0, {"detail": "gdd_mechanics_observable item is missing"}
    evidence = dict(item.evidence or {})
    total = evidence.get("unmapped_total")
    observed = evidence.get("unmapped_observed")
    source = "evaluator"
    try:
        total_i = int(total)
        observed_i = int(observed)
    except (TypeError, ValueError):


        rows = [row for row in _gdd_atom_rows(evidence)
                if row["primary_axis"] == "gdd_requirement_alignment"]
        total_i = len(rows)
        observed_i = sum(1 for row in rows if row["status"] == "pass")
        source = "derived_from_atom_lists"
    if total_i <= 0:
        coverage: dict[str, Any] = {}
        if axes is not None:
            graded, orphaned = _gdd_atom_coverage(items, axes)
            coverage = {
                "atoms_graded_by_owner": len(graded),
                "atoms_orphaned": len(orphaned),
                "orphans": orphaned,
            }
            if orphaned:
                return 0.0, {
                    "unmapped_total": 0,
                    "unmapped_observed": 0,
                    "source": source,
                    "coverage": coverage,
                    "detail": (
                        "%d frozen-GDD requirement(s) are owned by an axis that never put "
                        "them in a denominator, so they are graded by nothing: %s. The "
                        "weight stays in the reachable ceiling because this is the "
                        "evaluator missing a requirement, not a submission failing one"
                        % (len(orphaned),
                           ", ".join(row["requirement_id"] for row in orphaned[:6]))
                    ),
                }


        return 0.0, {
            "unmapped_total": 0,
            "unmapped_observed": 0,
            "source": source,
            **({"coverage": coverage} if coverage else {}),
            "detail": (
                "every executable atom in the frozen GDD is already charged on another "
                "axis and graded there, so this axis has an empty denominator and is not "
                "counted a second time; its weight is unreachable rather than earned"
            ),
        }
    return max(0.0, min(1.0, observed_i / total_i)), {
        "unmapped_total": total_i,
        "unmapped_observed": observed_i,
        "source": source,
        "unmapped_atoms": [row["requirement_id"] for row in rows] if source != "evaluator" else None,
    }


GDD_ATOM_AXIS_BY_PREFIX = {
    "mechanic": "task_checkpoints",
    "group": "rubric_interface",
    "numeric": "numeric_contract",
}


GDD_ATOM_FIELDS = (
    ("observed", "pass", None),
    ("missing", "fail", None),
    ("untriggered", "unverified", None),


    ("unmeasurable_mechanics", "unverified", "task_checkpoints"),
)


def _gdd_atom_rows(evidence: Mapping[str, Any]) -> list[dict[str, Any]]:

    rows: list[dict[str, Any]] = []
    for field, status, owner in GDD_ATOM_FIELDS:
        for raw in evidence.get(field) or []:
            requirement_id = str(raw)
            prefix = requirement_id.partition(":")[0]
            rows.append({
                "requirement_id": requirement_id,
                "status": status,
                "primary_axis": owner or GDD_ATOM_AXIS_BY_PREFIX.get(
                    prefix, "gdd_requirement_alignment"
                ),
                "evidence_path": f"item:gdd_mechanics_observable/evidence/{field}",
            })
    return rows


def _gdd_requirement_coverage(
    items: Mapping[str, Item], axes: Mapping[str, Mapping[str, Any]] | None = None,
) -> list[dict[str, Any]]:
    item = items.get("gdd_mechanics_observable")
    evidence = dict(item.evidence or {}) if item is not None else {}
    rows = _gdd_atom_rows(evidence)
    if axes is None:
        return rows
    graded, _orphans = _gdd_atom_coverage(items, axes)
    owners = {row["requirement_id"]: row["owning_axis"] for row in graded}
    demo_axis = axes.get("demo_coverage") or {}
    demos = demo_axis.get("evidence") or {}
    for row in rows:
        if owners.get(row["requirement_id"]) != "demo_coverage":
            continue
        row["declared_axis"] = row["primary_axis"]
        row["primary_axis"] = "demo_coverage"
        bare = row["requirement_id"].partition(":")[2]
        row["status"] = (
            "fail" if demo_axis.get("status") == "candidate_failure" else
            "unverified" if demos.get("measured") is not True else
            "pass" if {bare, row["requirement_id"]} & set(demos.get("observed") or [])
            else "fail"
        )
        row["evidence_path"] = "axis:demo_coverage/evidence"
    return rows


def _graded_stub_credit(item: Item | None) -> tuple[float | None, dict[str, Any]]:
    if item is None or item.verdict in {
        Verdict.INCONCLUSIVE, Verdict.UNMEASURABLE, Verdict.UNOBSERVABLE,
    }:
        return None, {"detail": item.detail if item else "stub_completion item missing"}
    evidence = dict(item.evidence or {})
    total = evidence.get("total")
    unfinished = evidence.get("unfinished") or []
    try:
        total_i = int(total)
    except (TypeError, ValueError):
        match = re.search(r"all (\d+) supplied script", item.detail or "")
        total_i = int(match.group(1)) if match else 0
    if total_i > 0:
        return max(0.0, (total_i - len(unfinished)) / total_i), evidence
    return float(item.credit), evidence


NUMERIC_NULL_SEPARATION = 3.0


def _numeric_series_movement(truth: Mapping[str, Any]) -> dict[str, dict[str, Any]]:

    moved: dict[str, dict[str, Any]] = {}
    for level in truth.get("levels") or []:
        if not isinstance(level, Mapping):
            continue
        samples: dict[str, list[float]] = {}
        for row in level.get("series") or []:
            if not isinstance(row, Mapping):
                continue
            for slot, value in (row.get("numeric") or {}).items():
                if isinstance(value, (int, float)):
                    samples.setdefault(str(slot), []).append(float(value))
        for slot, values in samples.items():
            if not values:
                continue
            span = max(values) - min(values)
            row = moved.setdefault(slot, {"levels_seen": 0, "levels_moved": 0, "max_span": 0.0})
            row["levels_seen"] += 1
            row["max_span"] = max(row["max_span"], span)
            if span > 1e-9:
                row["levels_moved"] += 1
    return moved


def _numeric_null_separation(items: Mapping[str, Item]) -> dict[str, Any]:

    item = items.get("anti_grant_diff")
    evidence = dict(item.evidence or {}) if item is not None else {}
    segments = evidence.get("segments")
    if not isinstance(segments, list):
        segments = [{"evidence": evidence}]
    idle_max = ops_max = None
    for segment in segments:
        inner = (segment or {}).get("evidence") or {}
        for key, sink in (("idle_env", "idle"), ("ops_env", "ops")):
            env = inner.get(key)
            if not isinstance(env, Mapping):
                continue
            delta = env.get("state_delta")
            if not isinstance(delta, (int, float)):
                continue
            if sink == "idle":
                idle_max = delta if idle_max is None else max(idle_max, float(delta))
            else:
                ops_max = delta if ops_max is None else max(ops_max, float(delta))
    if idle_max is None or ops_max is None:
        return {"available": False, "detail": "no matched-null state delta was recorded"}
    separated = ops_max > max(idle_max * NUMERIC_NULL_SEPARATION, 1e-9)
    return {
        "available": True,
        "idle_state_delta": round(idle_max, 3),
        "ops_state_delta": round(ops_max, 3),
        "required_factor": NUMERIC_NULL_SEPARATION,
        "separated": separated,
    }


def _numeric_contract_credit(
    context: Mapping[str, Any], items: Mapping[str, Item],
) -> tuple[float | None, dict[str, Any], str]:


    required = [str(slot) for slot in (context.get("rubric") or {}).get("required_numeric_slots") or []]
    if not required:
        return 0.0, {"detail": "the rubric requires no numeric slot"}, "empty_denominator"
    truth = context.get("candidate_truth") or {}
    movement = _numeric_series_movement(truth)
    if not movement:
        return 0.0, {
            "required": required,
            "detail": "no per-frame numeric samples were recorded for the candidate run",
        }, "unverified_zero"
    null = _numeric_null_separation(items)
    rows: list[dict[str, Any]] = []
    credited = 0
    for slot in required:
        seen = movement.get(slot)
        driven = bool(seen and seen["levels_moved"] > 0 and null.get("separated"))
        if driven:
            credited += 1
        rows.append({
            "slot": slot,
            "observed": bool(seen),
            "levels_moved": (seen or {}).get("levels_moved", 0),
            "levels_seen": (seen or {}).get("levels_seen", 0),
            "max_span": round((seen or {}).get("max_span", 0.0), 6),
            "credited": driven,
        })
    if not null.get("available"):
        return 0.0, {
            "required": required, "slots": rows, "matched_null": null,
            "detail": "no matched-null run to separate candidate-caused change from idle drift",
        }, "unverified_zero"
    return credited / len(required), {
        "required": required,
        "slots": rows,
        "matched_null": null,
        "formula": "required slots that both move in the candidate run and clear the matched-null separation / required slots",
        "limitation": (
            "separation from the matched null is measured per run, not per slot: a run that "
            "clears it credits every slot that moved, so this bounds rather than isolates "
            "candidate-caused numeric change"
        ),
    }, "measured"


def _gt_route_readings(context: Mapping[str, Any], items: Mapping[str, Item]) -> list[Mapping[str, Any]]:

    injected = context.get("route_readings")
    if isinstance(injected, list):
        return [row for row in injected if isinstance(row, Mapping)]
    reproduction = items.get("reproduction")
    evidence = reproduction.evidence if reproduction and isinstance(reproduction.evidence, Mapping) else {}
    candidates: list[Path] = []
    base = evidence.get("path")
    if base:
        candidates.append(Path(str(base)) / "routes" / "readings.json")
    report_path = context.get("report_path")
    if report_path:
        candidates.append(Path(str(report_path)).parent / "reproduction" / "routes" / "readings.json")
    for path in candidates:
        try:
            payload = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            continue
        if isinstance(payload, list):
            return [row for row in payload if isinstance(row, Mapping)]
    return []


def _gt_route_specs(context: Mapping[str, Any], game_id: str) -> list[Route] | None:

    raw = context.get("route_specs")
    if not isinstance(raw, list):
        if not game_id:
            return None
        from ..tasks import route_path

        raw = _read_json_mapping(route_path(game_id)).get("routes")
    if not isinstance(raw, list):
        return None
    return [Route.from_dict(dict(row)) for row in raw]


def _gt_route_verdicts(
    routes: Sequence[Route], context: Mapping[str, Any], items: Mapping[str, Item],
) -> dict[str, Item]:

    from ..routes.runner import RouteReading, verdict_for

    readings = {str(row.get("route_id") or ""): row for row in _gt_route_readings(context, items)}
    field_names = {field.name for field in fields(RouteReading)}
    verdicts: dict[str, Item] = {}
    for route in routes:
        row = readings.get(route.route_id)
        if row is None:
            verdicts[route.route_id] = inconclusive(
                route.route_id, attribution=Attribution.HARNESS,
                detail="the registered route produced no replay reading",
                evidence={"route_id": route.route_id, "tier": route.tier},
            )
        else:
            reading = RouteReading(**{key: value for key, value in row.items() if key in field_names})
            verdicts[route.route_id] = verdict_for(route, reading)
    return verdicts


def _hidden_scenario_credit(
    context: Mapping[str, Any], items: Mapping[str, Item], game_id: str,
) -> tuple[float | None, dict[str, Any], str]:


    routes = _gt_route_specs(context, game_id)
    if routes is None:
        return None, {"detail": "no certified route file is registered for this task"}, "not_instrumented_zero"
    verdicts = _gt_route_verdicts(routes, context, items)
    negative = [route for route in routes if route.tier == 0]
    positive = [route for route in routes if route.scored and route.tier > 0]
    missing = [route.route_id for route in negative + positive
               if not verdicts[route.route_id].verdict.in_denominator]
    if not negative:
        missing.append("negative_control")
    rows = [
        {
            "route_id": route.route_id,
            "tier": route.tier,
            "reached": bool(verdicts[route.route_id].evidence.get("reached")),
            "passed": verdicts[route.route_id].verdict is Verdict.PASSED,
            "verdict": verdicts[route.route_id].verdict.value,
            "detail": verdicts[route.route_id].detail,
            "attribution": (verdicts[route.route_id].attribution.value
                            if verdicts[route.route_id].attribution else None),
        }
        for route in positive
    ]
    control_ok = (
        all(verdicts[route.route_id].verdict is Verdict.PASSED for route in negative)
        if negative and all(verdicts[route.route_id].verdict.in_denominator for route in negative)
        else None
    )
    evidence = {
        "positive_routes": rows,
        "negative_control": {
            "routes": [route.route_id for route in negative],
            "passed": control_ok,
        },
        "unmeasured_routes": missing,
        "route_verdicts": {route.route_id: verdicts[route.route_id].to_dict()
                           for route in negative + positive},
        "formula": "certified scored routes with tier > 0 that passed the route verdict / those routes",
        "source": "evaluation/reproduction/routes/readings.json",
    }
    if not positive:
        evidence["detail"] = "the task registers no certified route above the no-input control"
        return 0.0, evidence, "empty_denominator"
    if missing:
        evidence["detail"] = "certified-route evidence is incomplete: " + ", ".join(missing)
        return None, evidence, "not_instrumented_zero"
    if control_ok is False:
        evidence["detail"] = "the no-input control did not behave, so positive routes are not credited"
        return 0.0, evidence, "measured"
    reached = sum(1 for row in rows if row["passed"])
    evidence["reached"] = reached
    evidence["total"] = len(rows)
    return reached / len(rows), evidence, "measured"


def _candidate_truth_snapshot(
    context: Mapping[str, Any], items: Mapping[str, Item],
) -> dict[str, Any] | None:

    injected = context.get("candidate_snapshot")
    if isinstance(injected, Mapping):
        return dict(injected)
    candidates: list[Path] = []
    reproduction = items.get("reproduction")
    evidence = (
        reproduction.evidence
        if reproduction is not None and isinstance(reproduction.evidence, Mapping)
        else {}
    )
    base = evidence.get("path")
    if base:
        candidates.append(Path(str(base)) / "truth_snapshot.json")
    report_path = context.get("report_path")
    if report_path:
        candidates.append(
            Path(str(report_path)).parent / "reproduction" / "truth_snapshot.json"
        )
    for path in candidates:
        payload = _read_json_mapping(path)
        if payload:
            return payload
    return None


def _scaffold_truth_snapshot(
    context: Mapping[str, Any], game_id: str,
) -> dict[str, Any] | None:

    injected = context.get("scaffold_snapshot")
    if isinstance(injected, Mapping):
        return dict(injected)
    if not game_id:
        return None
    payload = _read_json_mapping(
        Path(__file__).resolve().parents[4]
        / "eval" / "tasks" / game_id / "scaffold_snapshot.json"
    )
    return payload or None


def _u_class_point(payload: Mapping[str, Any], game_id: str, *, mode: str) -> float | None:


    from ..assertions.exemptions import evaluate_universal
    from ..assertions.behavior_contracts import player_counts_for_mode, probe_plan
    from ..truth.snapshot import TruthSnapshot
    from ..verdict import score_items

    try:
        snapshot = TruthSnapshot.from_dict(dict(payload))
        rows = evaluate_universal(
            snapshot, task_id=game_id or None,
            player_counts=player_counts_for_mode(probe_plan(game_id or None), mode),
        )
    except (ValueError, TypeError, KeyError, AttributeError):
        return None
    return score_items(rows).point


def _universal_mechanics_credit(
    context: Mapping[str, Any],
    items: Mapping[str, Item],
    game_id: str,
    *,
    mode: str,
    scaffold_relative: bool,
) -> tuple[float | None, dict[str, Any], str]:


    payload = _candidate_truth_snapshot(context, items)
    if payload is None:
        return None, {
            "detail": "no engine-truth snapshot was stored for this submission",
        }, "not_instrumented_zero"
    point = _u_class_point(payload, game_id, mode=mode)
    if point is None:
        return None, {
            "detail": "the stored engine-truth snapshot could not be read",
        }, "not_instrumented_zero"
    evidence: dict[str, Any] = {
        "candidate": round(point, 6),
        "families": "U1-U7",
        "excluded": (
            "the H class is scored once, on task_checkpoints, against certified-route "
            "milestones; it is not counted here a second time"
        ),
        "source": "evaluation/reproduction/truth_snapshot.json",
    }
    if not scaffold_relative:
        evidence["formula"] = "passing universal assertion weight / non-exempt weight"
        return point, evidence, "measured"

    baseline = _scaffold_truth_snapshot(context, game_id)
    if baseline is None:
        evidence["detail"] = "no frozen scaffold baseline for this task"
        return None, evidence, "baseline_unavailable_zero"
    base = _u_class_point(baseline, game_id, mode=mode)
    if base is None:
        evidence["detail"] = "the frozen scaffold baseline could not be read"
        return None, evidence, "baseline_unavailable_zero"
    headroom = 1.0 - base
    evidence["scaffold"] = round(base, 6)
    evidence["headroom"] = round(headroom, 6)
    evidence["formula"] = "(candidate - scaffold) / (1 - scaffold), floored at 0"
    if headroom <= 1e-9:
        evidence["detail"] = (
            "the scaffold already satisfies every non-exempt universal assertion, "
            "so there is no headroom this submission could add"
        )
        return (1.0 if point >= base else 0.0), evidence, "measured"
    return max(0.0, (point - base) / headroom), evidence, "measured"


def _gt_milestone_credit(
    context: Mapping[str, Any], items: Mapping[str, Item], game_id: str,
) -> tuple[float | None, dict[str, Any], str]:


    routes = _gt_route_specs(context, game_id)
    if routes is None:
        return None, {
            "detail": "no certified route file is registered for this task",
        }, "not_instrumented_zero"
    roster = [
        (route.route_id, name)
        for route in routes if route.scored and route.tier > 0
        for name in dict.fromkeys(milestone.name for milestone in route.goal.milestones)
        if name
    ]
    _, route_evidence, route_status = _hidden_scenario_credit(context, items, game_id)
    verdicts = route_evidence.get("route_verdicts") or {}
    rows: list[dict[str, Any]] = []
    for route_id, name in roster:
        verdict = verdicts[route_id]
        reading = verdict.get("evidence") or {}
        observed = name in (reading.get("milestones_reached") or [])
        valid = verdict["verdict"] in {"passed", "failed"} and verdict["credit"] > 0
        rows.append({
            "route_id": route_id,
            "milestone": name,
            "replayed": "stop_reason" in reading,
            "measured": route_id not in route_evidence["unmeasured_routes"],
            "observed": observed,
            "reached": observed and valid,
        })
    trace = items.get("mechanic_trace")
    disclosed = dict(trace.evidence or {}) if trace is not None else {}
    evidence: dict[str, Any] = {
        "milestones": rows,
        "negative_control": route_evidence.get("negative_control"),
        "unmeasured_routes": route_evidence.get("unmeasured_routes", []),
        "route_verdicts": verdicts,
        "disclosed_checkpoints": {
            "recorded_not_scored": (
                "the brief's own mechanic_checks, kept for comparison with the "
                "certified-route reading this axis grades on"
            ),
            **{key: disclosed[key] for key in ("expected", "reached", "missing", "coverage")
               if key in disclosed},
        },
        "formula": "valid certified-route milestones reached / registered scored-route milestones",
        "source": "eval/tasks/<game>/route.json against evaluation/reproduction/routes/readings.json",
    }
    if not rows:
        evidence["detail"] = "the task's certified routes declare no intermediate milestone"
        return 0.0, evidence, "empty_denominator"
    if route_status in INCOMPLETE_AXIS_STATUSES:
        evidence["detail"] = route_evidence["detail"]
        return None, evidence, route_status
    if not route_evidence["negative_control"]["passed"]:
        evidence["detail"] = "the no-input control did not behave, so route milestones are not credited"
        return 0.0, evidence, "measured"
    hit = sum(1 for row in rows if row["reached"])
    evidence["reached"] = hit
    evidence["total"] = len(rows)
    return hit / len(rows), evidence, "measured"


def _graded_transformation_credit(item: Item | None) -> tuple[float | None, dict[str, Any]]:


    if item is None or item.verdict in {
        Verdict.INCONCLUSIVE, Verdict.UNMEASURABLE, Verdict.UNOBSERVABLE,
    }:
        if item is not None and item.attribution is Attribution.SUBMISSION:
            return 0.0, dict(item.evidence or {})
        return None, {"detail": item.detail if item else "transformation_contract item missing"}
    evidence = dict(item.evidence or {})
    diagnostics = evidence.get("diagnostics")
    if not isinstance(diagnostics, list) or not diagnostics:
        return float(item.credit), evidence
    graded = [row for row in diagnostics if isinstance(row, Mapping)
              and str(row.get("status")) in {"pass", "fail"}]
    if not graded:
        return float(item.credit), evidence
    passed = sum(1 for row in graded if str(row.get("status")) == "pass")
    by_kind: dict[str, dict[str, int]] = {}
    for row in graded:
        kind = str(row.get("id") or "").split("/")[0] or "other"
        bucket = by_kind.setdefault(kind, {"passed": 0, "total": 0})
        bucket["total"] += 1
        if str(row.get("status")) == "pass":
            bucket["passed"] += 1
    evidence["graded"] = {
        "passed": passed,
        "total": len(graded),
        "by_kind": by_kind,
        "formula": "passed duties / graded duties",
    }
    return passed / len(graded), evidence


def _fixed_categories(
    axes: Mapping[str, Mapping[str, Any]],
    category_specs: Sequence[tuple[str, str, tuple[str, ...]]],
) -> list[dict[str, Any]]:
    categories: list[dict[str, Any]] = []
    for category_id, name, axis_ids in category_specs:
        rows = [dict(axes[axis_id]) for axis_id in axis_ids]
        weight = sum(float(row["weight_in_total"]) for row in rows)


        scored = weight > 0.0
        score = (
            None if not scored or any(row["credit"] is None for row in rows)
            else 100.0 * sum(float(row["earned_points"]) for row in rows) / weight
        )
        categories.append({
            "id": category_id,
            "name": name,
            "weight_in_total": weight,
            "reported_only": not scored,
            "score": _point_dict_100(None if score is None else score / 100.0),
            "measured_weight_share": (
                None if not scored else round(
                    sum(float(row["weight_in_total"]) for row in rows
                        if row["credit"] is not None and row["status"] not in UNMEASURED_AXIS_STATUSES)
                    / weight, 6,
                )
            ),
            "criteria": [{
                **row,
                "weight_within_category": (
                    None if not scored else round(
                        100.0 * float(row["weight_in_total"]) / weight, 6
                    )
                ),
                "score": _point_dict_100(row["credit"], str(row["status"])),
            } for row in rows],
        })
    return categories


def _score_progressive_additive(
    result: Any, policy: RegistryPolicy, *, mode: str,
) -> dict[str, Any]:


    items = _items_by_id(result.items)
    card = _reproduction_card(items)
    context = _mode1_evidence_context(result, items)
    game_id = str(result.package.manifest.get("game_id") or "")
    legacy_mechanics, mechanics_source = _ocard_credit("O3", items, card, policy)
    mechanics, mechanics_evidence, mechanics_status = _universal_mechanics_credit(
        context, items, game_id, mode=mode, scaffold_relative=(mode == "skeleton"),
    )
    mechanics_evidence["superseded_source"] = {
        "note": "the mixed U+H O3 reading, kept for comparison",
        "point": legacy_mechanics,
        **mechanics_source,
    }
    assets, assets_source = _ocard_credit("O6", items, card, policy)
    checkpoint = _mode1_item_credit(items, "mechanic_trace")
    causal = _mode1_item_credit(items, "causal_witness")
    milestone, milestone_evidence, milestone_status = _gt_milestone_credit(
        context, items, game_id,
    )
    hidden, hidden_evidence, hidden_status = _hidden_scenario_credit(context, items, game_id)
    demo_validity, demo_coverage, demo_evidence = _demo_credits(
        context, items, checkpoint,
    )
    demo_status = "candidate_failure" if demo_evidence.get("failure_code") else "measured"


    ops_credits = (_mode1_item_credit(items, name)
                   for name in ("ops_present", "ops_valid", "ops_not_idle"))
    candidate_ops_failure = any(value is not None and value <= 0 for value in ops_credits)

    if mode == "gdd":
        weights = MODE2_REDESIGN_WEIGHTS
        content, content_evidence = _content_structure_credit(
            context, items, card, policy,
        )
        rubric, rubric_evidence = _mode1_rubric_group_credit(context, items)
        numeric, numeric_evidence, numeric_status = _numeric_contract_credit(context, items)


        alignment, alignment_evidence = _gdd_unmapped_alignment_credit(items, {
            "task_checkpoints": {"evidence": milestone_evidence},
            "rubric_interface": {"evidence": rubric_evidence},
            "numeric_contract": {"evidence": numeric_evidence},
            "demo_coverage": {"evidence": demo_evidence},
        })
        axes = {
            "universal_mechanics": _fixed_axis_row(
                weights, "universal_mechanics",
                mechanics if mechanics_status == "measured" else 0.0,
                status=mechanics_status,
                detail=(
                    "universal assertions U1-U7 only; the H class is scored on task_checkpoints"
                    if mechanics_status == "measured"
                    else "no readable engine-truth snapshot, so the U class was not measured"
                ),
                evidence=mechanics_evidence,
            ),
            "asset_realization": _fixed_axis_row(
                weights, "asset_realization", assets,
                detail="runtime-loaded frozen reference asset coverage (O6), linear",
                evidence={"source": assets_source},
            ),
            "rubric_interface": _fixed_axis_row(
                weights, "rubric_interface", rubric,
                detail="addressable semantic groups required by the frozen GDD/rubric",
                evidence=rubric_evidence,
            ),
            "numeric_contract": _fixed_axis_row(
                weights, "numeric_contract",
                numeric if numeric_status == "measured" else 0.0,
                status=("candidate_failure" if numeric_status in INCOMPLETE_AXIS_STATUSES
                        and candidate_ops_failure else numeric_status),
                detail=(
                    "candidate did not supply valid, non-idle input for the numeric witness"
                    if numeric_status in INCOMPLETE_AXIS_STATUSES and candidate_ops_failure else
                    "required slots the candidate's own frames move, gated on separation "
                    "from the matched null"
                    if numeric_status == "measured"
                    else "declared/resolvable slots do not prove candidate-caused delta against matched null"
                ),
                evidence=numeric_evidence,
            ),
            "content_structure": _fixed_axis_row(
                weights, "content_structure", content,
                detail="census 5 + topology 3 + stated spatial relation 2",
                evidence=content_evidence,
            ),
            "causal_witness": _fixed_axis_row(
                weights, "causal_witness", causal,
                detail="candidate whole-game witness against matched null",
                evidence=(items.get("causal_witness").evidence if items.get("causal_witness") else {}),
            ),
            "task_checkpoints": _fixed_axis_row(
                weights, "task_checkpoints",
                milestone if milestone_status in {"measured", "empty_denominator"} else 0.0,
                status=milestone_status,
                detail=(
                    "certified-route checkpoints reached; the frozen GDD's own disclosed "
                    "checks are recorded in evidence but not scored"
                    if milestone_status == "measured"
                    else "no certified-route checkpoint reading; the disclosed checks are not substituted"
                ),
                evidence=milestone_evidence,
            ),
            "hidden_scenarios": _fixed_axis_row(
                weights, "hidden_scenarios",
                hidden if hidden_status in {"measured", "empty_denominator"} else 0.0,
                status=hidden_status,
                detail=(
                    "certified routes, authored against the reference rather than the frozen "
                    "GDD, that the submission can be driven through; the no-input control gates"
                    if hidden_status == "measured"
                    else "no certified-route replay reading was emitted"
                ),
                evidence=hidden_evidence,
            ),
            "demo_validity": _fixed_axis_row(
                weights, "demo_validity", demo_validity,
                status=demo_status,
                detail="valid, non-idle, independently replayable submitted demonstrations",
                evidence=demo_evidence if demo_status == "candidate_failure" else None,
            ),
            "demo_coverage": _fixed_axis_row(
                weights, "demo_coverage", demo_coverage,
                status=demo_status,
                detail="required features covered by valid demonstrations", evidence=demo_evidence,
            ),
            "gdd_requirement_alignment": _fixed_axis_row(
                weights, "gdd_requirement_alignment", alignment,
                status=(
                    "unmapped_atoms"
                    if alignment_evidence.get("unmapped_total")


                    else "unscored_atoms"
                    if (alignment_evidence.get("coverage") or {}).get("atoms_orphaned")
                    else "empty_denominator"
                    if alignment_evidence.get("unmapped_total") == 0
                    else "unverified_zero"
                ),
                detail=(
                    "only executable GDD atoms not scored on another axis"
                    if alignment_evidence.get("unmapped_total")
                    else str(alignment_evidence.get("detail") or "")
                    if (alignment_evidence.get("coverage") or {}).get("atoms_orphaned")
                    else "no executable GDD atom is charged here alone; empty denominator"
                ),
                evidence=alignment_evidence,
            ),
            "visual_placeholder": _fixed_axis_row(
                weights, "visual_placeholder", VISUAL_PLACEHOLDER_CREDIT,
                status="placeholder_uncalibrated", detail="fixed 5/15 points; not a visual judgment",
                evidence={"earned": VISUAL_PLACEHOLDER_EARNED_POINTS,
                          "ceiling": VISUAL_PLACEHOLDER_WEIGHT},
            ),
        }
        category_specs = MODE2_REDESIGN_CATEGORY_AXES
    else:
        weights = MODE3_REDESIGN_WEIGHTS
        stub, stub_evidence = _graded_stub_credit(items.get("stub_completion"))
        transformation, transformation_evidence = _graded_transformation_credit(
            items.get("transformation_contract")
        )
        integration = _mode1_item_credit(items, "skeleton_integrity")
        numeric, numeric_evidence, numeric_status = _numeric_contract_credit(context, items)
        scaffold_counts = _scaffold_group_counts(context, game_id)
        scaffold_content, scaffold_content_evidence = (
            _content_structure_credit(context, items, card, policy, scaffold=scaffold_counts)
            if scaffold_counts is not None else (None, {})
        )


        scaffold_content_ok = scaffold_counts is not None and scaffold_content is not None
        axes = {
            "universal_mechanics": _fixed_axis_row(
                weights, "universal_mechanics",
                mechanics if mechanics_status == "measured" else 0.0,
                status=mechanics_status,
                detail=(
                    "universal assertions U1-U7 net of what the scaffold already satisfied; "
                    "the H class is scored on task_checkpoints"
                    if mechanics_status == "measured"
                    else "the candidate-minus-scaffold universal reading could not be taken"
                ),
                evidence=mechanics_evidence,
            ),
            "asset_realization": _fixed_axis_row(
                weights, "asset_realization", assets,
                detail="live-scene asset coverage; unused scaffold preloads are not observed",
                evidence={"source": assets_source},
            ),
            "numeric_contract": _fixed_axis_row(
                weights, "numeric_contract",
                numeric if numeric_status == "measured" else 0.0,
                status=("candidate_failure" if numeric_status in INCOMPLETE_AXIS_STATUSES
                        and candidate_ops_failure else numeric_status),
                detail=(
                    "candidate did not supply valid, non-idle input for the numeric witness"
                    if numeric_status in INCOMPLETE_AXIS_STATUSES and candidate_ops_failure else
                    "required slots the candidate's own frames move, gated on separation "
                    "from the matched null"
                    if numeric_status == "measured"
                    else "candidate-caused numeric delta and matched-null evidence not yet emitted"
                ),
                evidence=numeric_evidence,
            ),
            "content_structure": _fixed_axis_row(
                weights, "content_structure",
                scaffold_content if scaffold_content_ok else 0.0,
                status="measured" if scaffold_content_ok else "baseline_unavailable_zero",
                detail=(
                    "census net of what the scaffold shipped, plus topology and stated "
                    "spatial relations"
                    if scaffold_content_ok
                    else "candidate-minus-scaffold census/topology baseline is not yet frozen; absolute O4/O5 are not credited"
                ),
                evidence=(
                    scaffold_content_evidence if scaffold_content_ok
                    else {
                        "detail": (
                            "no frozen scaffold baseline for this task"
                            if scaffold_counts is None
                            else "the baseline is frozen but topology or spatial relations had no reading"
                        ),
                        **({"components": scaffold_content_evidence.get("components")}
                           if scaffold_counts is not None else {}),
                    }
                ),
            ),
            "task_checkpoints": _fixed_axis_row(
                weights, "task_checkpoints",
                milestone if milestone_status in {"measured", "empty_denominator"} else 0.0,
                status=milestone_status,
                detail=(
                    "certified-route checkpoints reached; the skeleton task's own disclosed "
                    "checks are recorded in evidence but not scored"
                    if milestone_status == "measured"
                    else "no certified-route checkpoint reading; the disclosed checks are not substituted"
                ),
                evidence=milestone_evidence,
            ),
            "causal_witness": _fixed_axis_row(
                weights, "causal_witness", causal,
                detail="candidate whole-game witness against matched null",
                evidence=(items.get("causal_witness").evidence if items.get("causal_witness") else {}),
            ),
            "hidden_scenarios": _fixed_axis_row(
                weights, "hidden_scenarios",
                hidden if hidden_status in {"measured", "empty_denominator"} else 0.0,
                status=hidden_status,
                detail=(
                    "certified routes, authored against the reference rather than the brief, "
                    "that the submission can be driven through; the no-input control gates"
                    if hidden_status == "measured"
                    else "no portable evaluator-private semantic scenario reading was emitted; GT O7 is not substituted"
                ),
                evidence=hidden_evidence,
            ),
            "demo_validity": _fixed_axis_row(
                weights, "demo_validity", demo_validity,
                status=demo_status,
                detail="valid, non-idle submitted demonstrations",
                evidence=demo_evidence if demo_status == "candidate_failure" else None,
            ),
            "demo_coverage": _fixed_axis_row(
                weights, "demo_coverage", demo_coverage,
                status=demo_status,
                detail="required features covered by valid demonstrations", evidence=demo_evidence,
            ),
            "stub_completion": _fixed_axis_row(
                weights, "stub_completion", stub,
                detail="completed required stubs / supplied required stubs", evidence=stub_evidence,
            ),
            "transformation_contract": _fixed_axis_row(
                weights, "transformation_contract", transformation,
                detail="graded packaging, path, connection, and runtime transformation duties",
                evidence=transformation_evidence,
            ),
            "scaffold_integration": _fixed_axis_row(
                weights, "scaffold_integration", integration,
                detail="non-score-critical scaffold integration points preserved",
                evidence=(items.get("skeleton_integrity").evidence if items.get("skeleton_integrity") else {}),
            ),
            "visual_placeholder": _fixed_axis_row(
                weights, "visual_placeholder", VISUAL_PLACEHOLDER_CREDIT,
                status="placeholder_uncalibrated", detail="fixed 5/15 points; not a visual judgment",
                evidence={"earned": VISUAL_PLACEHOLDER_EARNED_POINTS,
                          "ceiling": VISUAL_PLACEHOLDER_WEIGHT},
            ),
        }
        category_specs = MODE3_REDESIGN_CATEGORY_AXES

    evaluation_status, gates = _mode1_gate_status(items, card, policy)
    if mode == "skeleton":
        integrity = items.get("skeleton_integrity")
        if integrity is None or integrity.verdict in {
            Verdict.INCONCLUSIVE, Verdict.UNMEASURABLE, Verdict.UNOBSERVABLE,
        }:
            gates.append({"id": "skeleton_integrity", "status": "inconclusive",
                          "detail": integrity.detail if integrity else "item missing"})
            if evaluation_status != "gated":
                evaluation_status = "inconclusive"
        elif integrity.verdict is not Verdict.PASSED:
            gates.append({"id": "skeleton_integrity", "status": "failed", "detail": integrity.detail})
            evaluation_status = "gated"
        else:
            gates.append({"id": "skeleton_integrity", "status": "passed", "detail": integrity.detail})

    _attribute_candidate_runtime_failure(axes, card, context)
    missing = _name_unreadable_axes(axes, items)


    alignment_row = axes.get("gdd_requirement_alignment")
    if alignment_row is not None and alignment_row.get("status") == "unscored_atoms":
        coverage = (alignment_row.get("evidence") or {}).get("coverage") or {}
        missing.append({
            "category": "unscored_requirement",
            "criterion": "gdd_requirement_alignment",
            "source": "gdd_requirement_alignment",
            "step": str(alignment_row.get("detail") or ""),
            "orphans": [row["requirement_id"] for row in (coverage.get("orphans") or [])],
        })
    total = sum(float(row["earned_points"]) for row in axes.values())
    strict = _strict_status(result, items, mode)
    measured_weight = sum(
        float(row["weight_in_total"]) for row in axes.values()
        if row["credit"] is not None and row["status"] not in UNMEASURED_AXIS_STATUSES
    )
    reachable = _reachable_ceiling(axes)
    weighted_total: dict[str, Any] = {
        "score": round(total, 3),
        "status": "evaluation_incomplete" if missing else "complete",
        "scale": "0-100",
        "headline_ceiling": 90.0,
        "currently_reachable_ceiling": reachable["currently_reachable_ceiling"],
        "reachable_ceiling_breakdown": reachable["breakdown"],
        "rule": headline_rule(policy.version),
    }
    if missing:
        weighted_total["unmeasured"] = list(missing)
    ranking_eligible = not missing and evaluation_status == "complete"
    return {
        "schema": SCORECARD_SCHEMA,
        "registry_version": policy.version,
        "registry_status": policy.status,
        "score_scope": f"mode{2 if mode == 'gdd' else 3}_fixed_weight_progressive_capability",
        "visual_protocol": "uncalibrated fixed placeholder: 5 earned points of 15",
        "game_id": str(result.package.manifest.get("game_id") or ""),
        "mode": mode,
        "evaluation_status": evaluation_status,
        "gates": gates,
        "categories": _fixed_categories(axes, category_specs),
        "axes": list(axes.values()),
        "weighted_total": weighted_total,


        "evaluation_incomplete": bool(missing),
        "diagnostic": {
            "measured_capability_weight": measured_weight,
            "note": "named conservative zeros preserve the denominator and never reuse invalid or duplicate evidence",
        },
        "measured_weight_share": round(measured_weight / 100.0, 6),
        "ranking_eligible": ranking_eligible,
        "ranking_basis": "objective_plus_fixed_uncalibrated_visual_placeholder",
        "ranking_note": (
            f"Compare only within Mode {2 if mode == 'gdd' else 3} under this exact registry."
            if ranking_eligible else "Hard-gate or required measurement evidence is incomplete."
        ),
        "not_applicable_criteria": [],
        "not_applicable_reasons": {},
        "strict": strict,
        **({"gdd_requirement_coverage": _gdd_requirement_coverage(items, axes)}
           if mode == "gdd" else {}),
        "reading_rule": "Read zero-point gates and strict completion before the fixed-weight axes.",
    }


DEFAULT_REGISTRY_BY_MODE: dict[str, str] = {
    "brief": MODE1_VLM_REGISTRY_VERSION,
    "gdd": MODE2_VLM_REGISTRY_VERSION,
    "skeleton": MODE3_VLM_REGISTRY_VERSION,
    "bugfix": MODE4_REDESIGN_REGISTRY_VERSION,


    "port": MODE5_MDVA_REGISTRY_VERSION,
}


def _score_redesign_visual(card: dict[str, Any], result: Any) -> dict[str, Any]:


    from ..scard.game_visual import PROTOCOL

    visual = _items_by_id(result.items).get("task_visual")
    evidence = dict(visual.evidence or {}) if visual else {}
    measured = bool(visual and visual.verdict in {Verdict.PASSED, Verdict.FAILED}
                    and evidence.get("protocol") == PROTOCOL)
    credit = float(visual.credit) if measured else None
    if measured and (not 0 <= credit <= 1):
        measured, credit = False, None
    visual_axis = _fixed_axis_row(
        {"task_visual": VISUAL_PLACEHOLDER_WEIGHT}, "task_visual", credit,
        detail=(visual.detail if visual else "No game-specific VLM reading; run with --visual-judge vlm"),
        evidence=evidence,
    )
    objective_axes = [row for row in card["axes"] if row["id"] != "visual_placeholder"]
    card["axes"] = objective_axes + [visual_axis]
    objective_missing = list(card["weighted_total"].get("unmeasured", []))
    missing = list(objective_missing)
    if not measured:
        missing.append({"category": "visual_experience", "criterion": "task_visual",
                        "source": "item:task_visual", "step": visual_axis["detail"]})
    objective_points = sum(float(row["earned_points"] or 0) for row in objective_axes)
    visual_points = VISUAL_PLACEHOLDER_WEIGHT * credit if measured else None
    total = objective_points + (visual_points or 0.0)
    for index, category in enumerate(card["categories"]):
        if category["id"] == "visual_experience":
            card["categories"][index] = {
                "id": "visual_experience", "name": "Game-specific visual quality",
                "weight_in_total": VISUAL_PLACEHOLDER_WEIGHT,
                "score": _point_dict_100(credit), "measured_weight_share": float(measured),
                "criteria": [{**visual_axis, "weight_within_category": 100.0,
                              "score": _point_dict_100(credit)}],
            }
    measured_weight = sum(row["weight_in_total"] for row in objective_axes
                          if row["status"] not in UNMEASURED_AXIS_STATUSES)
    measured_weight += VISUAL_PLACEHOLDER_WEIGHT if measured else 0.0


    reachable_axes = {row["id"]: row for row in objective_axes}
    reachable_axes["task_visual"] = {**visual_axis,
                                     "status": "measured" if measured else "not_instrumented_zero"}
    reachable = _reachable_ceiling(reachable_axes)
    reachable["breakdown"]["note"] = (
        "Currently available scoring weight from measured objective and visual evidence, "
        "plus any reported legacy fallback. No fixed visual award is used. "
        "Unreachable weight includes missing measurements in this report; missing VLM "
        "evidence can be supplied by judging retained recordings. It does not mean the "
        "visual evaluator is unimplemented."
    )
    card["weighted_total"].update(
        score=round(total, 3) if not missing else None,
        status="complete" if not missing else "evaluation_incomplete",
        headline_ceiling=100.0,
        currently_reachable_ceiling=reachable["currently_reachable_ceiling"],
        reachable_ceiling_breakdown=reachable["breakdown"],
        unmeasured=missing,
    )
    card["objective_total"] = {"score": round(objective_points, 3),
                               "earned_points_floor": round(objective_points, 3),
                               "ceiling": 85.0, "headline_ceiling": 85.0, "unmeasured": objective_missing,
                               "status": "evaluation_incomplete" if objective_missing else "complete"}
    card["visual_total"] = {"score": round(visual_points, 3) if visual_points is not None else None,
                            "credit": credit, "scale": "0-15", "ceiling": 15.0,
                            "weight_in_total": 15.0,
                            "earned_points": visual_points,
                            "status": "complete" if measured else "retry_required"}
    card["assessment_status"] = ("evaluation_incomplete" if objective_missing else
                                  "complete" if measured else "objective_only")
    card["evaluation_incomplete"] = bool(missing)
    card["measured_weight_share"] = round(measured_weight / 100, 6)
    card["ranking_eligible"] = not missing and card["evaluation_status"] == "complete"
    card["ranking_basis"] = "objective_and_game_vlm_not_independently_calibrated"
    card["ranking_note"] = "Compare only complete cards within the same mode, rubric and registry; independent calibration remains pending."
    card["score_scope"] = "fixed_objective_85_plus_game_vlm_15"
    card["visual_protocol"] = PROTOCOL + "; game-specific M/D/V/A, GT and asset images, continuous per-item attainment"
    card["reading_rule"] = "Read zero-point gates, objective evidence and VLM coverage before the fixed-weight composite."
    candidate_failures = list(evidence.get("candidate_failures") or [])
    evaluator_failures = list(evidence.get("evaluator_failures") or [])
    for row in objective_axes:
        if row.get("status") == "candidate_failure":
            candidate_failures.append({
                "status": "failed", "owner": "candidate", "domain": "objective",
                "failure_code": row.get("evidence", {}).get("failure_code") or f"candidate_{row['id']}_missing",
                "axis": row["id"], "detail": row.get("detail") or "candidate evidence missing",
            })
        elif row.get("status") in INCOMPLETE_AXIS_STATUSES:
            evaluator_failures.append({
                "status": "retry_required", "owner": "evaluator", "domain": "objective",
                "failure_code": f"objective_{row['id']}_not_measured",
                "axis": row["id"], "detail": row.get("detail") or "objective reading missing",
                "retryable": True,
            })
    if not measured and not any(row.get("domain", "visual") == "visual" for row in evaluator_failures):
        evaluator_failures.append({
            "code": "vlm_evidence_missing", "failure_code": "vlm_evidence_missing",
            "domain": "visual", "owner": "evaluator", "retryable": True,
            "detail": visual_axis["detail"],
        })
    card["candidate_failures"] = candidate_failures
    card["evaluator_failures"] = evaluator_failures
    card["retry_plan"] = [
        {"domain": row.get("domain", "visual"), "failure_code": row.get("failure_code") or row.get("code"),
         "action": ("resume failed VLM item/group using retained video and frames"
                    if row.get("domain", "visual") == "visual"
                    else "rerun only the missing objective probe")}
        for row in evaluator_failures if row.get("retryable", True)
    ]
    if not measured:


        card["weighted_total"]["status"] = (
            "evaluation_incomplete" if objective_missing else "evaluation_pending"
        )
    card["diagnostic"] = {"measured_capability_weight": measured_weight,
                          "earned_points_floor": round(total, 3), "placeholder_weight": 0,
                          "note": "Missing evidence withholds the composite; objective evidence and per-item visual judgments remain available."}
    return card


def default_registry_for_mode(mode: str) -> str:

    return DEFAULT_REGISTRY_BY_MODE.get(mode, REGISTRY_VERSION)


def score_task_result(result: Any, registry_version: str | None = None, *,
                      _objective_only: bool = False) -> dict[str, Any]:


    mode = str(result.package.manifest.get("mode") or "")
    selected_version = registry_version or default_registry_for_mode(mode)
    policy = registry_policy(selected_version, mode=mode)
    if (
        policy.mode5_capability_only
        and mode != "port"
        and selected_version != MODE5_REGISTRY_VERSION
    ):


        selected_version = MODE34_REGISTRY_VERSION
        policy = registry_policy(selected_version, mode=mode)
    if policy.mode5_capability_only:
        return _score_mode5_result(result, policy)
    if policy.mode1_redesign and mode == "brief":
        card = _score_mode1_redesign(result, policy)
        return _score_redesign_visual(card, result) if policy.task_visual else card
    if policy.progressive_redesign_mode == mode and mode in {"gdd", "skeleton"}:
        card = _score_progressive_additive(result, policy, mode=mode)
        return _score_redesign_visual(card, result) if policy.task_visual else card
    game_id = str(result.package.manifest.get("game_id") or "")
    specs = _category_specs(mode, policy.version)
    items = _items_by_id(result.items)
    if policy.progressive_redesign_mode == "bugfix" and mode == "bugfix":
        items = _mode4_candidate_import_items(items, getattr(result, "engine", {}) or {})
    if policy.mode4_graded and mode == "bugfix" and "repair_restoration_graded" not in items:
        fallback = _legacy_graded_restoration(items)
        if fallback is not None:
            items["repair_restoration_graded"] = fallback
    card = _reproduction_card(items)
    forced_not_applicable = _forced_not_applicable(policy, mode, result, items)

    category_rows: list[dict[str, Any]] = []
    weighted_categories: list[tuple[float, Interval]] = []
    weighted_category_points: list[tuple[float, float | None]] = []

    category_unearned: list[tuple[float, float]] = []
    total_registered_weight = sum(spec.weight for spec in specs)
    total_measured_share = 0.0
    unmeasured: list[dict[str, Any]] = []


    for spec in specs:
        criterion_rows: list[dict[str, Any]] = []
        weighted_criteria: list[tuple[float, Interval]] = []
        weighted_criterion_points: list[tuple[float, float | None]] = []
        criterion_unearned: list[tuple[float, float]] = []
        applicable_weight = 0.0
        measured_weight = 0.0
        for criterion in spec.criteria:
            source_rows: list[dict[str, Any]] = []
            source_intervals: list[tuple[float, Interval]] = []
            source_points: list[tuple[float, float | None]] = []
            unearned_sources = 0
            forced_reason = forced_not_applicable.get((spec.id, criterion.id))
            for source in criterion.sources:
                interval, row = _source_interval(source, items, card, policy)
                if _objective_only and (source.kind == "scard" or source.id in {
                    "task_visual", "unity_vlm", "cross_engine_fidelity",
                }):
                    interval = _empty_interval("inconclusive")
                    row = {"kind": source.kind, "id": source.id, "applicable": False,
                           "headline": "unearned", "status": "outside_objective_score",
                           "detail": "perceptual weight is reserved; this is not a complete composite score"}
                if forced_reason:


                    row["applicable"] = False
                    row["not_applicable_reason"] = forced_reason
                    row.pop("headline", None)
                    row.pop("unmeasured_step", None)
                source_rows.append(row)
                if row["applicable"]:
                    source_intervals.append((1.0, interval))


                    point = interval.point if row.get("point") is not None else None
                    if point is None:
                        unmeasured.append({
                            "category": spec.id,
                            "criterion": criterion.id,
                            "source": f"{source.kind}:{source.id}",
                            "step": str(row.get("unmeasured_step") or "no reading"),
                        })
                        source_points.append((1.0, None))
                    else:
                        credit = _headline_credit(point, criterion.credit_exponent)
                        if criterion.credit_exponent != 1.0:
                            row["headline_credit"] = round(credit * 100.0, 3)
                            row["credit_exponent"] = criterion.credit_exponent
                        source_points.append((1.0, credit))
                elif row.get("headline") == "unearned":
                    unearned_sources += 1
            applicable = bool(source_intervals)
            in_headline = applicable or unearned_sources > 0
            headline_sources = len(source_intervals) + unearned_sources
            unearned_share = unearned_sources / headline_sources if headline_sources else 0.0
            criterion_interval = _registered_combine(
                source_intervals + [(1.0, _empty_interval("unearned"))] * unearned_sources
            )
            criterion_point = (
                _weighted_point(source_points + [(1.0, 0.0)] * unearned_sources)
                if in_headline else None
            )
            source_measured_share = (
                sum(interval.denominator > 0 for _weight, interval in source_intervals)
                / len(source_intervals)
                if source_intervals else 0.0
            )
            if applicable:
                applicable_weight += criterion.weight
                measured_weight += criterion.weight * source_measured_share
            if in_headline:
                weighted_criteria.append((criterion.weight, criterion_interval))
                weighted_criterion_points.append((criterion.weight, criterion_point))
                criterion_unearned.append((criterion.weight, unearned_share))
            criterion_row: dict[str, Any] = {
                "id": criterion.id,
                "name": criterion.name,
                "weight_within_category": criterion.weight,
                "applicable": applicable,
                "score": _point_dict_100(
                    criterion_point if in_headline else None,
                    "unearned" if (in_headline and not applicable)
                    else "not_applicable" if not applicable else None,
                ),
                "interval": _interval_dict_100(criterion_interval),
                "measured_source_share": round(source_measured_share, 6),
                "sources": source_rows,
            }
            if criterion.credit_exponent != 1.0:
                criterion_row["credit_exponent"] = criterion.credit_exponent
            if unearned_sources:
                criterion_row["unearned_source_share"] = round(unearned_share, 6)
            if not applicable:
                criterion_row["not_applicable_reason"] = (
                    forced_reason or _not_applicable_reason(criterion_row)
                )
            criterion_rows.append(criterion_row)
        category_applicable = applicable_weight > 0
        category_in_headline = bool(weighted_criterion_points)
        category_interval = _registered_combine(weighted_criteria)
        category_point = _weighted_point(weighted_criterion_points) if category_in_headline else None
        measured_share = measured_weight / applicable_weight if applicable_weight else 0.0
        headline_weight = sum(weight for weight, _share in criterion_unearned)
        category_unearned_share = (
            sum(weight * share for weight, share in criterion_unearned) / headline_weight
            if headline_weight else 0.0
        )
        if category_in_headline:
            weighted_categories.append((spec.weight, category_interval))
            weighted_category_points.append((spec.weight, category_point))
            category_unearned.append((spec.weight, category_unearned_share))
        if category_applicable:
            total_measured_share += spec.weight * measured_share
        else:
            total_registered_weight -= spec.weight
        category_row: dict[str, Any] = {
            "id": spec.id,
            "name": spec.name,
            "weight_in_total": spec.weight,
            "applicable": category_applicable,
            "applicable_weight_within_category": round(applicable_weight, 6),
            "score": _point_dict_100(
                category_point,
                "unearned" if (category_in_headline and not category_applicable)
                else "not_applicable" if not category_applicable else None,
            ),
            "interval": _interval_dict_100(category_interval),
            "measured_weight_share": round(measured_share, 6),
            "low_measurement_coverage": (
                category_applicable and measured_share < MIN_RANKING_COVERAGE
            ),
            "criteria": criterion_rows,
        }
        if category_unearned_share > 0:
            category_row["unearned_weight_share"] = round(category_unearned_share, 6)
        category_rows.append(category_row)

    total = _registered_combine(weighted_categories)
    headline_point = _weighted_point(weighted_category_points)
    mode4_composition: dict[str, Any] | None = None
    if policy.mode34_applicability and mode == "bugfix":


        headline_point, mode4_composition = _mode4_product_headline(
            category_rows, graded=policy.mode4_graded,
            repair_item=items.get("repair_restoration_graded"),
            edit_radius_item=items.get("edit_radius"),
            gate_keys=(MODE4_REDESIGN_GATES
                       if policy.progressive_redesign_mode == "bugfix" else MODE4_GATES),
            regression_weights=(MODE4_REDESIGN_REGRESSION_WEIGHTS
                                if policy.progressive_redesign_mode == "bugfix"
                                else MODE4_REGRESSION_WEIGHTS),
        )
        if headline_point is None and not unmeasured:
            unmeasured.append({
                "category": MODE4_REPAIR_CREDIT[0],
                "criterion": MODE4_REPAIR_CREDIT[1],
                "source": "item:repair_restoration_graded" if policy.mode4_graded else "item:repair_restoration",
                "step": ("graded repair credit or regression_factor is missing; the product needs both"
                         if policy.mode4_graded else
                         "repair credit, a gate or a regression reading is not applicable "
                         "in this cell; the product headline needs all three"),
            })
    headline_weight_total = sum(weight for weight, _share in category_unearned)
    unearned_total_share = (
        sum(weight * share for weight, share in category_unearned) / headline_weight_total
        if headline_weight_total else 0.0
    )
    headline_ceiling = round(100.0 * (1.0 - unearned_total_share), 3)
    measured_weight_share = (
        total_measured_share / total_registered_weight if total_registered_weight else 0.0
    )
    core_ids = {"artifact_observability", "mechanics_requirements", "causal_playability"}
    core_ready = all(
        row["applicable"] and row["measured_weight_share"] >= MIN_RANKING_COVERAGE
        for row in category_rows if row["id"] in core_ids
    )
    all_categories_ready = all(
        row["measured_weight_share"] >= MIN_RANKING_COVERAGE
        for row in category_rows if row["applicable"]
    )
    headline_complete = headline_point is not None
    ranking_eligible = (
        headline_complete
        and measured_weight_share >= MIN_RANKING_COVERAGE
        and core_ready
        and all_categories_ready
    )
    not_applicable = sorted(
        f"{cat['id']}/{crit['id']}"
        for cat in category_rows for crit in cat["criteria"] if not crit["applicable"]
    )
    not_applicable_reasons = {
        f"{cat['id']}/{crit['id']}": str(crit.get("not_applicable_reason") or "")
        for cat in category_rows for crit in cat["criteria"] if not crit["applicable"]
    }
    ranking_basis = (
        "objective_and_calibrated_perceptual"
        if not any(
            c.endswith("/calibrated_surface") or c.startswith("visual_experience/")
            for c in not_applicable
        )
        else "objective_only"
    )
    if policy.evidence_consistency:
        measured_perceptual = any(
            src.get("applicable") and src.get("point") is not None
            and (src["kind"] == "scard" or src["id"] in {"unity_vlm", "cross_engine_fidelity"})
            for cat in category_rows for crit in cat["criteria"] for src in crit["sources"]
        )
        ranking_basis = "objective_and_calibrated_perceptual" if measured_perceptual else "objective_only"
    if policy.task_visual and mode != "bugfix" and not _objective_only:
        visual_item = items.get("task_visual")
        if visual_item is not None and visual_item.verdict is Verdict.PASSED:
            ranking_basis = "objective_and_vlm_not_independently_validated"
    exempt_removed = sorted(
        {
            f"{cat['id']}/{crit['id']}/{src['id']}"
            for cat in category_rows for crit in cat["criteria"] for src in crit["sources"]
            if float(src.get("exempt_weight") or 0.0) > 0
        }
    )

    unearned = sorted(
        f"{cat['id']}/{crit['id']}"
        for cat in category_rows for crit in cat["criteria"]
        if crit.get("unearned_source_share")
    )

    weighted_total: dict[str, Any] = {
        **_point_dict_100(headline_point),
        "headline_ceiling": headline_ceiling,
        "rule": headline_rule(policy.version),
    }
    if mode4_composition is not None:
        weighted_total["composition"] = mode4_composition
    if unearned:
        weighted_total["unearned"] = unearned
    if not headline_complete:
        weighted_total["unmeasured"] = unmeasured
        weighted_total["detail"] = (
            "evaluation_incomplete: "
            + "; ".join(f"{u['source']} <- {u['step']}" for u in unmeasured)
        )

    strict = _strict_status(result, items, mode)
    payload = {
        "schema": SCORECARD_SCHEMA,
        "registry_version": policy.version,
        "registry_status": policy.status,
        "visual_protocol": ("task-conditioned demonstration VLM; no human scoring"
                            if policy.task_visual else "legacy S-card"),
        "game_id": game_id,
        "mode": mode,
        "categories": category_rows,
        "weighted_total": weighted_total,
        "evaluation_incomplete": not headline_complete,
        "diagnostic": {
            "weighted_total_interval": _interval_dict_100(total),
            "exempt_removed_from_headline": exempt_removed,
            "note": (
                "lo/hi are the diagnostic reading: unmeasured applicable sources widen to "
                "[0, weight] and genre-exempt items to [0, full]. They never enter "
                "weighted_total.score."
            ),
        },
        "measured_weight_share": round(measured_weight_share, 6),
        "ranking_eligible": ranking_eligible,
        "ranking_basis": ranking_basis,
        "ranking_note": _ranking_note(result, strict, ranking_eligible),
        "not_applicable_criteria": not_applicable,
        "not_applicable_reasons": not_applicable_reasons,
        "strict": strict,
        "reading_rule": (
            "read the six category scores and evidence first; weighted_total.score is a "
            "single diagnostic number emitted only when every applicable source has a "
            "reading (otherwise status=evaluation_incomplete and ranking_eligible=false); "
            "strict.resolved is non-exchangeable and visual quality cannot compensate for it"
        ),
    }
    if policy.progressive_redesign_mode == "bugfix" and mode == "bugfix":
        if policy.mode4_f2p_p2p:
            outcome = _mode4_f2p_p2p_outcome(
                category_rows,
                repair_item=items.get("repair_restoration_graded"),
                strict=strict,
            )
            score = outcome["score"]
            complete = score is not None
            payload["weighted_total"] = {
                **_point_dict_100(None if score is None else float(score) / 100.0),
                "headline_ceiling": 100.0,
                "currently_reachable_ceiling": 100.0,
                "rule": headline_rule(policy.version),
                "composition": outcome,
            }
            payload.update({
                "score_scope": "mode4_bug_site_resolution",
                "evaluation_status": (
                    "inconclusive" if not complete
                    else "gated" if outcome["failed_integrity_gates"]
                    else "complete"
                ),
                "evaluation_incomplete": not complete,
                "resolution_status": outcome["resolution_status"],
                "resolved": outcome["resolved"],
                "bug_site_id": _mode4_difficulty_context(result).get("case_id"),
                "f2p_rate": outcome["f2p_rate"],
                "p2p_rate": outcome["p2p_rate"],
                "behavioral_tests": {"f2p": outcome["f2p"], "p2p": outcome["p2p"]},
                "gates": [{
                    "id": key,
                    "status": (
                        "not_applicable" if value is None
                        else "passed" if value >= 1.0 - 1e-12 else "failed"
                    ),
                } for key, value in outcome["integrity_gates"].items()],
            })
            payload["ranking_eligible"] = bool(complete and ranking_eligible)
            payload["ranking_note"] = _ranking_note(
                result, strict, payload["ranking_eligible"],
            )
            payload["repair_correctness"] = {
                **payload["weighted_total"],
                "score_scope": "per_case_bug_site_resolution",
                "ranking_eligible": payload["ranking_eligible"],
            }
            payload["difficulty_evidence"] = _mode4_difficulty_evidence(result, score)
        else:
            composition = payload["weighted_total"].get("composition") or {}
            gate_point = composition.get("gates")
            gate_readings = composition.get("gate_readings") or {}
            payload.update({
                "score_scope": "mode4_differential_repair_product",
                "evaluation_status": (
                    "inconclusive" if not headline_complete
                    else "gated" if gate_point is not None and float(gate_point) < 100.0
                    else "complete"
                ),
                "gates": [{
                    "id": f"{category}/{criterion}",
                    "status": (
                        "not_applicable"
                        if not (gate_readings.get(f"{category}/{criterion}") or {}).get("applicable")
                        else "inconclusive"
                        if (gate_readings.get(f"{category}/{criterion}") or {}).get("credit") is None
                        else "failed"
                        if float((gate_readings.get(f"{category}/{criterion}") or {}).get("credit")) < 1.0
                        else "passed"
                    ),
                } for category, criterion in MODE4_REDESIGN_GATES],
            })
            payload["weighted_total"]["headline_ceiling"] = 100.0
            payload["weighted_total"]["currently_reachable_ceiling"] = 100.0
            payload["repair_correctness"] = {
                **payload["weighted_total"],
                "score_scope": "per_case_functional_correctness",
                "ranking_eligible": payload["ranking_eligible"],
            }
            payload["difficulty_evidence"] = _mode4_difficulty_evidence(
                result, payload["weighted_total"].get("score"),
            )
    if policy.task_visual and mode != "bugfix" and not _objective_only:
        objective = score_task_result(result, policy.version, _objective_only=True)
        payload["objective_total"] = {
            **objective["weighted_total"],
            "ranking_eligible": objective["ranking_eligible"],
            "score_scope": "objective_contribution_not_complete_composite",
        }
        payload["assessment_status"] = (
            "complete" if headline_complete else
            "objective_only" if objective["weighted_total"]["score"] is not None else
            "evaluation_incomplete"
        )


        visual_item = items.get("task_visual")
        if visual_item is not None and (visual_item.evidence or {}).get("upstream_revision"):
            payload["visual_protocol"] = "GameCraft-adapted VLM; experimental; no GT or submitter description"
    return payload


HEADLINE_RULE = (
    "single number: pessimistic per-source reading (skipped earns 0), genre-exempt items "
    "removed from the denominator, unobservable/not-applicable sources outside it; "
    "emitted only when every applicable source has a reading, otherwise "
    "status=evaluation_incomplete with the failed evaluator step named in `unmeasured`"
)

CALIB_HEADLINE_RULE = (
    HEADLINE_RULE
    + "; graded O-channel points enter squared (a channel is a conjunction of rungs); "
    "the perceptual S-card keeps its registered weight at 0 while uncalibrated "
    "(`unearned`), so the maximum reachable is `headline_ceiling`, not 100"
)


CALIB4_HEADLINE_RULE = (
    CALIB_HEADLINE_RULE
    + "; O6 asset coverage enters linearly (a ratio, not a conjunction); in brief mode "
    "O4 scores declared-vs-built consistency and O5/O7 are not applicable (reported, "
    "outside the denominator, see not_applicable_reasons); gdd mode's mode_specific "
    "criterion is the fraction of Task-GDD mechanics observable in the submission"
)


MODE4_HEADLINE_RULE = (
    CALIB4_HEADLINE_RULE
    + "; in bugfix mode the causal category scores target restoration once, from the "
    "graded `repair_restoration` item (per-target-route credit), instead of charging the "
    "shared honest repaired replay twice through `repair_differential` and `gold_replay`; "
    "both remain strict behavior gates that decide `resolved` and are reported as evidence"
)


MODE34_HEADLINE_RULE = (
    MODE4_HEADLINE_RULE
    + "; in skeleton mode the scaffold-shipped layout, evaluator interface (O2) and level "
    "topology (O4) are not applicable (reported, outside the denominator); in bugfix mode the "
    "faulty-build-shipped layout/O1/O2, levels (O4/O5/O6), composition (O9), the "
    "S-card, the absolute O3 reading and the package-side publication preflight are not "
    "applicable, `feature_kept` is read once, and the headline is the product "
    "repair_credit x gates x regression_mean (graded repair_restoration, 0 when a preserved "
    "route breaks; 0/1 gates no_eval_smuggling, integrity_controls, null_no_win, "
    "mash/anti-grant; regression mean of feature_kept 40, surface 15, "
    "transformation_contract 15), see weighted_total.composition"
)


MODE34B_HEADLINE_RULE = (
    CALIB4_HEADLINE_RULE
    + "; mode34 applicability and gates are retained; in bugfix mode the headline is "
    "repair_credit x gates x regression_factor x regression_mean; "
    "repair_restoration_graded measures source-pass / mutant-fail target assertions "
    "only with complete preflight and submission readings, otherwise target routes; "
    "regression_factor is the preserved passing fraction; "
    "edit_radius is reported with weight 0 (null when unmeasured); composition factors "
    "are percentages rounded to 3 decimals, with raw fractions in repair_detail; "
    "the headline is 100 times the unrounded fraction product; resolved stays strict"
)


MODE5_HEADLINE_RULE = (
    "Mode-5-only fixed 100-point capability score: core mechanics 35, end-to-end "
    "playability/progression 25, content/structure fidelity 15, visual/UI/feedback "
    "fidelity 15, and runtime stability/lifecycle 10. Unity/editor/package/SDK/build "
    "recipe and anti-grant checks are zero-point gates. Missing or evaluator-"
    "inconclusive score evidence never leaves the denominator and withholds the full "
    "score; candidate-caused delivery or causal-validity gate failure records zero."
)

MODE1_REDESIGN_HEADLINE_RULE = (
    "Mode-1-only fixed 100-point registry: zero-point execution/integrity gates surround "
    "85 objective capability points and a 15-point visual axis; the original objective "
    "ratios are preserved while no per-cell renormalisation is applied. Until visual "
    "calibration, visual earns a fixed 5/15 placeholder and the reachable ceiling is 90. "
    "Declared-only numeric slots earn zero until "
    "candidate-caused drive is proven. Strict completion is reported separately."
)

MODE2_REDESIGN_HEADLINE_RULE = (
    "Mode-2-only fixed 100-point registry inherited from Mode 1: frozen-GDD "
    "requirements replace candidate-authored GDD quality, content expands to census 5 + "
    "topology 3 + stated spatial relations 2, and no requirement is counted on two axes. "
    "Zero-point execution/integrity gates remain separate. Until visual calibration, "
    "visual earns a fixed 5/15 placeholder and the reachable ceiling is 90."
)

MODE3_REDESIGN_HEADLINE_RULE = (
    "Mode-3-only fixed 100-point incremental registry inherited from Mode 2: scaffold-"
    "provided layout/interface/topology earn no absolute points; capability axes measure "
    "stub completion, transformation, integration, candidate witness and portable hidden "
    "scenarios. Missing baseline/portable-scenario evidence is a named zero, never a "
    "renormalised or duplicated legacy reading. Visual earns the provisional fixed 5/15 "
    "and the reachable ceiling is 90."
)

MODE4_REDESIGN_HEADLINE_RULE = (
    "Mode-4 differential repair headline: 100 x repair_credit x integrity_gates x "
    "preserved_route_factor x preservation_mean. preservation_mean weights feature_kept "
    "50%, product surface 25%, and transformation contract 25%. Project/runtime/interface, "
    "null/mash/anti-grant and no-smuggling are zero-point gates; absolute inherited product "
    "quality and visual placeholder earn no points; edit_radius remains diagnostic weight 0. "
    "The same value is reported explicitly as repair_correctness. A separate, non-ranking "
    "difficulty_evidence diagnostic multiplies the correctness fraction by provisional ordinal "
    "tier points (easy=1, medium=2, hard=3); it must not be read as a percentage or a calibrated "
    "ability score."
)

MODE4_F2P_P2P_HEADLINE_RULE = (
    "Mode-4 deterministic single-bug-site resolution: FULL=100 iff every registered "
    "failure-to-pass target assertion, every preserved pass-to-pass route/contract, all "
    "negative controls, integrity gates and the strict repair contract pass. PARTIAL and "
    "NO are unresolved and score 0; F2P/P2P rates remain explicit diagnostics. Missing "
    "required evaluator evidence is INCONCLUSIVE with no score. No LLM judge, visual "
    "placeholder, edit-size reward or provisional difficulty weight enters correctness."
)


MODE5_DELIVERY_ZERO_HEADLINE_RULE = (
    MODE5_HEADLINE_RULE
    + " A submission that fails a candidate admissibility gate scores a ranked 0 and "
    "stays in the denominator, including when it left no capability readings behind: "
    "a project that will not build, or will not answer the controller protocol, caused "
    "that silence itself. Readings the evaluator owes -- an uncertified host, an "
    "uncalibrated scenario suite, a judge that was not requested -- still withhold the "
    "headline as INCONCLUSIVE, so an evaluator gap is never reported as a candidate zero. "
    "The candidate's own demonstration tape is no longer one of those admissibility "
    "gates: a missing or refused ops tape zeroes causal_witness, worth 10 of the 100 "
    "points, and leaves the other 90 to be read by the evaluator driving the build."
)


def headline_rule(version: str | None = None) -> str:
    resolved = registry_policy(version).version
    if resolved in {MODE1_VLM_REGISTRY_VERSION, MODE2_VLM_REGISTRY_VERSION, MODE3_VLM_REGISTRY_VERSION}:
        return ("Fixed objective axes retain 85 points. Game-specific VLM quality contributes 15 points; "
                "M/D/V/A internal weights are 10/18/27/45 percent. Direct per-item attainment is limited "
                "by triggered caps, using the rubric's direct quality criteria. Missing objective or visual evidence withholds "
                "the composite and ranking; strict completion remains independent.")
    if resolved == MODE4_F2P_P2P_REGISTRY_VERSION:
        return MODE4_F2P_P2P_HEADLINE_RULE
    if resolved == MODE5_DELIVERY_ZERO_REGISTRY_VERSION:
        return MODE5_DELIVERY_ZERO_HEADLINE_RULE
    if resolved == MODE5_MDVA_REGISTRY_VERSION:
        return (
            "Mode-5-v2 corpus-calibrated headline: Objective capability is a fixed "
            "70-point domain. Cross-engine structure and game-rubric visual quality are "
            "independent 15-point VLM domains. Candidate failures score only the "
            "affected domain; missing evaluator VLM evidence requires retry without "
            "zeroing objective points or renormalising the denominator."
        )
    if resolved == MODE5_REGISTRY_VERSION:
        return MODE5_HEADLINE_RULE
    if resolved == MODE1_REDESIGN_REGISTRY_VERSION:
        return MODE1_REDESIGN_HEADLINE_RULE
    if resolved == MODE2_REDESIGN_REGISTRY_VERSION:
        return MODE2_REDESIGN_HEADLINE_RULE
    if resolved == MODE3_REDESIGN_REGISTRY_VERSION:
        return MODE3_REDESIGN_HEADLINE_RULE
    if resolved == MODE4_REDESIGN_REGISTRY_VERSION:
        return MODE4_REDESIGN_HEADLINE_RULE
    if resolved == VISUAL_REGISTRY_VERSION:
        return (HEADLINE_RULE
                + "; evidence1 objective rules and mode34b repair product retained; "
                "non-bugfix visual quality uses task_visual instead of the S-card: "
                "requirement presentation max across demos, feedback/readability/presentation mean; "
                "no human score or human-weight reserve; VLM independent validation pending; "
                "missing VLM readings withhold the headline; Unity uses its native state probe; "
                "paired cross-engine fidelity still reserves its uncalibrated weight")
    if resolved == EVIDENCE_REGISTRY_VERSION:
        return (MODE34B_HEADLINE_RULE + "; mechanic checkpoint credit is observed / required, "
                "including untriggered checks in the denominator; Unity perceptual items "
                "require calibration; paired fidelity is scored once, not twice")
    if resolved == MODE34B_REGISTRY_VERSION:
        return MODE34B_HEADLINE_RULE
    if resolved == MODE34_REGISTRY_VERSION:
        return MODE34_HEADLINE_RULE
    if resolved == MODE4_REGISTRY_VERSION:
        return MODE4_HEADLINE_RULE
    if resolved == CALIB4_REGISTRY_VERSION:
        return CALIB4_HEADLINE_RULE
    if resolved == CALIB_REGISTRY_VERSION:
        return CALIB_HEADLINE_RULE
    return HEADLINE_RULE


__all__ = [
    "MODE1_REDESIGN_HEADLINE_RULE",
    "MODE1_REDESIGN_REGISTRY_VERSION",
    "DEFAULT_REGISTRY_BY_MODE",
    "default_registry_for_mode",
    "MODE1_REDESIGN_WEIGHTS",
    "MODE2_REDESIGN_HEADLINE_RULE",
    "MODE2_REDESIGN_REGISTRY_VERSION",
    "MODE2_REDESIGN_WEIGHTS",
    "MODE3_REDESIGN_HEADLINE_RULE",
    "MODE3_REDESIGN_REGISTRY_VERSION",
    "MODE3_REDESIGN_WEIGHTS",
    "MODE4_REDESIGN_HEADLINE_RULE",
    "MODE4_REDESIGN_REGISTRY_VERSION",
    "MODE4_F2P_P2P_HEADLINE_RULE",
    "MODE4_F2P_P2P_REGISTRY_VERSION",
    "MODE4_PROVISIONAL_TIER_POINTS",
    "VISUAL_REGISTRY_VERSION",
    "EVIDENCE_REGISTRY_VERSION",
    "BRIEF_NOT_APPLICABLE",
    "BUGFIX_NOT_APPLICABLE",
    "CALIB4_HEADLINE_RULE",
    "CALIB4_REGISTRY_VERSION",
    "CALIB_HEADLINE_RULE",
    "CALIB_REGISTRY_VERSION",
    "MODE34B_HEADLINE_RULE",
    "MODE34B_REGISTRY_VERSION",
    "MODE34_HEADLINE_RULE",
    "MODE34_REGISTRY_VERSION",
    "MODE4_GATES",
    "MODE4_HEADLINE_RULE",
    "MODE4_REGISTRY_VERSION",
    "MODE5_HEADLINE_RULE",
    "MODE5_REGISTRY_VERSION",
    "MODE5_DELIVERY_ZERO_REGISTRY_VERSION",
    "MODE5_MDVA_REGISTRY_VERSION",
    "HISTORICAL_MODE5_REGISTRIES",
    "MODE5_SCORECARD_SCHEMA",
    "MODE4_REGRESSION_WEIGHTS",
    "MODE4_REPAIR_CREDIT",
    "SKELETON_NOOP_HEADLINE_CAP",
    "SKELETON_NOT_APPLICABLE",
    "GRADED_CHANNEL_EXPONENT",
    "brief_layout_constraint",
    "HEADLINE_RULE",
    "PILOT_REGISTRY_VERSION",
    "REGISTRY_VERSION",
    "REGISTRY_VERSIONS",
    "RegistryPolicy",
    "SCORECARD_SCHEMA",
    "headline_rule",
    "registry_policy",
    "score_task_result",
]
