from __future__ import annotations

import unittest

from evalsys.taskgen.unity.unity_behavior import (
    SCHEMA,
    UnityHiddenBehaviorScenario,
)
from evalsys.taskgen.unity.unity_counterfactual import (
    apply_counterfactual,
    counterfactual_matches_expectation,
)


def _scenario() -> dict:
    return {
        "schema": SCHEMA,
        "id": "cat_defense_first_kill",
        "evidence_basis": ["source_derived"],
        "authoring_record": "sha256:1111111111111111111111111111111111111111111111111111111111111111",
        "start_level": 0,
        "budget_frames": 1800,
        "policy": "seek_and_fire",
        "required_actions": ["gb_left", "gb_action"],
        "required_roles": ["gb_player", "gb_enemy", "gb_projectile"],
        "required_numeric": ["health"],
        "goal": {
            "predicate": "count(gb_enemy) == 0",
            "milestones": [
                {"name": "contact", "predicate": "overlap(gb_projectile, gb_enemy)"},
                {"name": "removed", "predicate": "count_delta(gb_enemy) < 0"},
            ],
            "invariants": [{"name": "alive", "predicate": "alive(gb_player)"}],
        },
        "source_goal": {
            "predicate": "count(gb_enemy) == 0",
            "milestones": [
                {"name": "source_contact", "predicate": "overlap(gb_projectile, gb_enemy)"},
            ],
        },
        "counterfactuals": [
            {"id": "no_fire", "remove": "gb_action", "expect_goal": False},
            {
                "id": "wrong_action",
                "replace": "gb_action",
                "with": "gb_left",
                "expect_goal": False,
            },
        ],
        "capture_checkpoints": [
            {"id": "spawn", "kind": "run_start"},
            {"id": "contact", "predicate": "overlap(gb_projectile, gb_enemy)"},
        ],
    }


class UnityBehaviorSchemaTests(unittest.TestCase):
    def test_valid_source_derived_scenario_reuses_goal_contract(self) -> None:
        scenario = UnityHiddenBehaviorScenario.from_dict(_scenario())
        self.assertEqual("count(gb_enemy) == 0", scenario.goal.predicate)
        self.assertEqual(["contact", "removed"], [item.name for item in scenario.goal.milestones])
        self.assertEqual(_scenario(), scenario.to_dict())

    def test_source_derived_requires_evidence_record(self) -> None:
        payload = _scenario()
        payload.pop("authoring_record")
        with self.assertRaisesRegex(ValueError, "evidence record"):
            UnityHiddenBehaviorScenario.from_dict(payload)

    def test_unknown_action_and_invalid_predicate_are_rejected(self) -> None:
        payload = _scenario()
        payload["required_actions"] = ["gb_reset"]
        payload["goal"]["predicate"] = "teleport_won()"
        with self.assertRaisesRegex(ValueError, "invalid predicate"):
            UnityHiddenBehaviorScenario.from_dict(payload)

    def test_remove_and_replace_preserve_action_horizon(self) -> None:
        scenario = UnityHiddenBehaviorScenario.from_dict(_scenario())
        actions = (
            {"canonical_action": "gb_left", "value": 1, "hold_frames": 5},
            {"canonical_action": "gb_action", "value": 1, "hold_frames": 7},
        )
        removed = apply_counterfactual(actions, scenario.counterfactuals[0])
        replaced = apply_counterfactual(actions, scenario.counterfactuals[1])
        self.assertEqual([5, 7], [item["hold_frames"] for item in removed])
        self.assertEqual("wait", removed[1]["op"])
        self.assertNotIn("canonical_action", removed[1])
        self.assertEqual("gb_left", replaced[1]["canonical_action"])
        self.assertTrue(
            counterfactual_matches_expectation(
                scenario.counterfactuals[0], goal_reached=False
            )
        )
        self.assertFalse(
            counterfactual_matches_expectation(
                scenario.counterfactuals[0], goal_reached=True
            )
        )

    def test_manifest_v3_extended_action_and_axis_counterfactual(self) -> None:
        payload = _scenario()
        payload["required_actions"] = ["reload"]
        payload["required_axes"] = ["look_y"]
        payload["counterfactuals"] = [{
            "id": "neutral-look",
            "neutralize_axis": "look_y",
            "with_value": 0.0,
            "expect_goal": False,
        }]
        scenario = UnityHiddenBehaviorScenario.from_dict(
            payload,
            allowed_actions=("reload",),
            allowed_axes=("look_y",),
        )
        transformed = apply_counterfactual(({
            "canonical_action": "reload",
            "hold_frames": 5,
            "axes": {"look_y": 0.5},
        },), scenario.counterfactuals[0])
        self.assertEqual(0.0, transformed[0]["axes"]["look_y"])
        self.assertEqual(5, transformed[0]["hold_frames"])


if __name__ == "__main__":
    unittest.main()
