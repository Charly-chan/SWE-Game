


from __future__ import annotations

from typing import Iterable

from ...routes.schema import parse_predicate


OBSERVATION_CONTRACT_SCHEMA = "gamebench.mode5.unity-observation-contract.v1"
OBSERVATION_CONTRACT_VERSION = "mode5-unity-observation-v2"


OBSERVATION_CAPABILITIES = frozenset({
    "entity_census",
    "entity_geometry",
    "role_overlap",
    "stable_contact",
    "entity_lifecycle",
    "entity_state",
    "entity_numeric_state",
    "numeric_telemetry",
    "scene_lifecycle",
    "outcome_events",
})

PREDICATE_CAPABILITIES = {
    "overlap": frozenset({"entity_geometry", "role_overlap"}),
    "contact": frozenset({"entity_geometry", "stable_contact"}),
    "standing_on": frozenset({"entity_geometry", "stable_contact"}),
    "collected_all": frozenset({"entity_census"}),
    "device_present": frozenset({"entity_census"}),
    "device_state": frozenset({"entity_census", "entity_state"}),
    "device_norm_delta": frozenset({"entity_geometry", "stable_contact"}),
    "device_numeric": frozenset({"entity_census", "entity_numeric_state"}),
    "device_numeric_delta": frozenset({"entity_census", "entity_numeric_state"}),
    "count": frozenset({"entity_census"}),
    "alive": frozenset({"entity_census"}),
    "count_delta": frozenset({"entity_census", "entity_lifecycle"}),
    "numeric": frozenset({"numeric_telemetry"}),
    "numeric_delta": frozenset({"numeric_telemetry"}),
    "norm_delta": frozenset({"entity_geometry"}),
    "norm_in": frozenset({"entity_geometry"}),
    "levels_visited": frozenset({"scene_lifecycle"}),
    "whole_game_clear": frozenset({"scene_lifecycle", "outcome_events"}),
}


def required_observation_capabilities(predicates: Iterable[str]) -> frozenset[str]:


    required: set[str] = set()
    for source in predicates:
        if not source:
            continue
        parsed = parse_predicate(source)
        for call in parsed.calls:
            required.update(PREDICATE_CAPABILITIES.get(call.name, ()))
    return frozenset(required)


def validate_observation_predicates(
    predicates: Iterable[str],
    *,
    available: Iterable[str] = OBSERVATION_CAPABILITIES,
) -> tuple[str, ...]:


    available_set = set(available)
    findings: list[str] = []
    for source in predicates:
        if not source:
            continue
        parsed = parse_predicate(source)
        for call in parsed.calls:
            required = set(PREDICATE_CAPABILITIES.get(call.name, ()))
            missing = sorted(required - available_set)
            if missing:
                findings.append(
                    f"predicate {source!r} uses {call.name} but public observation "
                    f"contract lacks: {', '.join(missing)}"
                )
    return tuple(dict.fromkeys(findings))


def observation_contract_payload() -> dict[str, object]:


    return {
        "schema": OBSERVATION_CONTRACT_SCHEMA,
        "version": OBSERVATION_CONTRACT_VERSION,
        "capabilities": sorted(OBSERVATION_CAPABILITIES),
        "predicate_capabilities": {
            name: sorted(values) for name, values in sorted(PREDICATE_CAPABILITIES.items())
        },
        "geometry_resolution": [
            "enabled_colliders_in_marker_subtree",
            "enabled_renderers_in_marker_subtree",
        ],
        "identity": "GBEntity role plus stableId; route selectors are evaluator-owned",
        "state_surface": "evaluator-versioned GBObservableState; claims require causal corroboration",
        "trust": "probe_observed; candidate state and telemetry are corroborating, not authoritative",
    }


__all__ = [
    "OBSERVATION_CAPABILITIES",
    "OBSERVATION_CONTRACT_SCHEMA",
    "OBSERVATION_CONTRACT_VERSION",
    "PREDICATE_CAPABILITIES",
    "observation_contract_payload",
    "required_observation_capabilities",
    "validate_observation_predicates",
]
