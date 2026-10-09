


from __future__ import annotations

from dataclasses import asdict, dataclass
from typing import Any

from ..modes import Mode, parse_mode


COMMON_ELIGIBILITY = frozenset({
    "layout",
    "interface",
    "anti_grant_static",
    "auto_win_ready",
    "health_writable",
})
OPS_ELIGIBILITY = frozenset({"ops_present", "ops_valid", "ops_not_idle"})
COMMON_BEHAVIOR = frozenset({
    "null_no_win",
    "extended_mash_no_win",
    "anti_grant_diff",
})


@dataclass(frozen=True)
class ModeVerifierProfile:
    mode: str
    purpose: str
    eligibility_ids: frozenset[str]
    behavior_ids: frozenset[str]
    fidelity_ids: frozenset[str] = frozenset({"reproduction"})

    @property
    def strict_ids(self) -> frozenset[str]:
        return self.eligibility_ids | self.behavior_ids

    def to_dict(self) -> dict[str, Any]:
        payload = asdict(self)
        for key in ("eligibility_ids", "behavior_ids", "fidelity_ids"):
            payload[key] = sorted(payload[key])
        return payload


PROFILES: dict[str, ModeVerifierProfile] = {
    "brief": ModeVerifierProfile(
        mode="brief",
        purpose=(
            "judge evidence interpretation, authored GDD quality, executable "
            "self-clear causality, and GT-conditioned product fidelity"
        ),
        eligibility_ids=COMMON_ELIGIBILITY
        | OPS_ELIGIBILITY
        | frozenset({
            "gdd",
            "authored_gdd_quality",
            "authored_gdd_interface",
            "brief_gdd_grounding",
            "rubric_interface",
        }),
        behavior_ids=COMMON_BEHAVIOR
        | frozenset({"causal_witness", "mechanic_trace"}),
    ),
    "gdd": ModeVerifierProfile(
        mode="gdd",
        purpose=(
            "judge conformance to the reviewed GDD, observable mechanic "
            "milestones, self-clear causality, and GT-conditioned fidelity"
        ),
        eligibility_ids=COMMON_ELIGIBILITY
        | OPS_ELIGIBILITY
        | frozenset({"task_gdd_contract", "rubric_interface"}),
        behavior_ids=COMMON_BEHAVIOR
        | frozenset({"causal_witness", "mechanic_trace"}),
    ),
    "skeleton": ModeVerifierProfile(
        mode="skeleton",
        purpose=(
            "judge preservation of the supplied integration points, completion "
            "of task stubs, observable mechanics, self-clear causality, and fidelity"
        ),
        eligibility_ids=COMMON_ELIGIBILITY
        | OPS_ELIGIBILITY
        | frozenset({
            "rubric_interface",
            "skeleton_integrity",
            "stub_completion",
            "transformation_contract",
        }),
        behavior_ids=COMMON_BEHAVIOR
        | frozenset({"causal_witness", "mechanic_trace"}),
    ),
    "bugfix": ModeVerifierProfile(
        mode="bugfix",
        purpose=(
            "judge a publication-proven failure-to-pass repair, preservation of "
            "the product surface, target restoration, and positive/negative regressions"
        ),
        eligibility_ids=COMMON_ELIGIBILITY
        | frozenset({
            "no_eval_smuggling",
            "bugfix_publication",
            "surface",
            "feature_kept",
            "transformation_contract",
        }),
        behavior_ids=COMMON_BEHAVIOR
        | frozenset({"gold_replay", "repair_differential"}),
        fidelity_ids=frozenset({"reproduction", "repair_restoration_graded", "edit_radius"}),
    ),
    "port": ModeVerifierProfile(
        mode="port",
        purpose=(
            "judge a Godot-to-Unity port's static submission contract, "
            "self-clear causality, executable runtime behavior, and "
            "reference-grounded evidence-adjusted artifact and runtime fidelity"
        ),
        eligibility_ids=OPS_ELIGIBILITY
        | frozenset({
            "unity_layout",
            "unity_interface",
            "unity_sdk_integrity",
            "task_gdd_contract",
            "port_contract_alignment",
            "no_eval_smuggling",
            "no_bundled_godot_runtime",
            "build_recipe",
            "unity_anti_grant_static",
        }),
        behavior_ids=frozenset({
            "unity_build",
            "unity_probe",
            "unity_input_dispatch",
            "unity_auto_win_ready",
            "causal_witness",
            "null_no_win",
            "unity_hidden_behavior",
            "unity_counterfactual",
        }),
        fidelity_ids=frozenset({
            "legacy_reference_trace",
            "unity_evaluator_capture",
            "unity_runtime_stability",
            "unity_source_behavior",
        }),
    ),
}


def verifier_profile(mode: str | Mode) -> ModeVerifierProfile:
    resolved = mode if isinstance(mode, Mode) else parse_mode(mode)
    return PROFILES[resolved.id]


def missing_strict_items(
    mode: str | Mode,
    observed_ids: set[str] | frozenset[str],
) -> tuple[str, ...]:
    profile = verifier_profile(mode)
    return tuple(sorted(profile.strict_ids - set(observed_ids)))


__all__ = [
    "ModeVerifierProfile",
    "PROFILES",
    "missing_strict_items",
    "verifier_profile",
]
