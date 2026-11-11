

from __future__ import annotations

from typing import Any, Mapping, Sequence

from .unity_behavior import UnityCounterfactual


def apply_counterfactual(
    actions: Sequence[Mapping[str, Any]],
    counterfactual: UnityCounterfactual,
) -> tuple[dict[str, Any], ...]:


    transformed: list[dict[str, Any]] = []
    for original in actions:
        action = dict(original)
        name = str(action.get("canonical_action") or "")
        if counterfactual.remove and name == counterfactual.remove:
            action.pop("canonical_action", None)
            action.pop("value", None)
            action["op"] = "wait"
        elif counterfactual.replace and name == counterfactual.replace:
            action["canonical_action"] = counterfactual.with_action
        if counterfactual.neutralize_axis:
            axes = dict(action.get("axes") or {})
            if counterfactual.neutralize_axis in axes:
                axes[counterfactual.neutralize_axis] = counterfactual.with_value
                action["axes"] = axes
        transformed.append(action)
    return tuple(transformed)


def counterfactual_matches_expectation(
    counterfactual: UnityCounterfactual,
    *,
    goal_reached: bool,
) -> bool:
    return goal_reached is counterfactual.expect_goal


__all__ = ["apply_counterfactual", "counterfactual_matches_expectation"]
