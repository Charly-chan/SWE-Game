from __future__ import annotations

import unittest

from evalsys.taskgen.unity.unity_behavior import SCHEMA, UnityHiddenBehaviorScenario
from evalsys.taskgen.unity.unity_policy import (
    DeclarativeClosedLoopPolicy,
    PolicyAction,
    run_closed_loop_policy,
)


def _scenario(*, budget: int = 12) -> UnityHiddenBehaviorScenario:
    return UnityHiddenBehaviorScenario.from_dict({
        "schema": SCHEMA,
        "id": "enemy_clear",
        "evidence_basis": ["gdd_explicit"],
        "start_level": 0,
        "budget_frames": budget,
        "policy": "attack_until_clear",
        "required_actions": ["gb_attack"],
        "goal": {
            "predicate": "count(gb_enemy) == 0",
            "milestones": [{"name": "enemy_removed", "predicate": "count_delta(gb_enemy) < 0"}],
            "invariants": [{"name": "player_alive", "predicate": "alive(gb_player)"}],
        },
    })


class AttackPolicy:
    def step(self, observation):
        return PolicyAction("gb_attack", 1.0, 5)


class UnityPolicyTests(unittest.TestCase):
    def test_fallback_only_policy_batches_without_changing_action_sequence(self) -> None:
        policy = DeclarativeClosedLoopPolicy(
            "fallback", (),
            (PolicyAction("gb_left"), PolicyAction("gb_right")),
        )
        calls: list[list[PolicyAction]] = []
        frame = 0

        def dispatch_batch(actions):
            nonlocal frame
            calls.append(list(actions))
            rows = []
            for action in actions:
                frame += action.hold_frames
                rows.append({"f": frame, "g": {"gb_player": 1, "gb_enemy": 1}})
            return rows

        result = run_closed_loop_policy(
            _scenario(budget=130), policy,
            {"f": 0, "g": {"gb_player": 1, "gb_enemy": 1}},
            lambda _action: self.fail("single dispatch should not be used"),
            dispatch_batch=dispatch_batch,
            group_totals={"gb_player": 1, "gb_enemy": 1},
        )
        self.assertEqual("fail", result.status)
        self.assertEqual([64, 64, 2], [len(batch) for batch in calls])
        self.assertEqual(130, len(result.actions))
        self.assertEqual(
            ["gb_left", "gb_right", "gb_left", "gb_right"],
            [action.canonical_action for action in result.actions[:4]],
        )

    def test_observation_trigger_and_later_consequence_use_shared_reader(self) -> None:
        payload = _scenario(budget=3).to_dict()
        payload["goal"]["observations"] = [{
            "name": "shot_then_kill", "trigger": "overlap(gb_projectile, gb_enemy)",
            "predicate": "count_delta(gb_enemy) < 0", "baseline": "previous_row",
        }]
        result = run_closed_loop_policy(
            UnityHiddenBehaviorScenario.from_dict(payload), AttackPolicy(),
            {"f": 0, "g": {"gb_player": 1, "gb_enemy": 1},
             "o": {"gb_projectile|gb_enemy": False}},
            lambda action: [
                {"f": 1, "g": {"gb_player": 1, "gb_enemy": 1},
                 "o": {"gb_projectile|gb_enemy": True}},
                {"f": 3, "g": {"gb_player": 1, "gb_enemy": 0},
                 "o": {"gb_projectile|gb_enemy": False}},
            ], group_totals={"gb_player": 1, "gb_enemy": 1},
        )
        self.assertEqual("pass", result.status)
        self.assertEqual(("shot_then_kill",), result.observations_triggered)
        self.assertEqual(("shot_then_kill",), result.observations_reached)

    def test_end_predicate_cannot_be_ignored(self) -> None:
        payload = _scenario(budget=3).to_dict()
        payload["goal"]["end_predicate"] = "numeric(score) >= 100"
        for numeric, expected in (({"score": 0}, "fail"), ({}, "inconclusive"), ({"score": 100}, "pass")):
            with self.subTest(numeric=numeric):
                result = run_closed_loop_policy(
                    UnityHiddenBehaviorScenario.from_dict(payload), AttackPolicy(),
                    {"f": 0, "g": {"gb_player": 1, "gb_enemy": 1}},
                    lambda action: {"f": 3, "g": {"gb_player": 1, "gb_enemy": 0}, "n": numeric},
                    group_totals={"gb_player": 1, "gb_enemy": 1},
                )
                self.assertEqual(expected, result.status)

    def test_goal_on_final_budget_frame_is_evaluated(self) -> None:
        result = run_closed_loop_policy(
            _scenario(budget=3), AttackPolicy(),
            {"f": 0, "g": {"gb_player": 1, "gb_enemy": 1}},
            lambda action: {"f": 3, "g": {"gb_player": 1, "gb_enemy": 0}},
            group_totals={"gb_player": 1, "gb_enemy": 1},
        )
        self.assertEqual("pass", result.status)

    def test_transient_collision_inside_action_is_not_lost(self) -> None:
        payload = _scenario(budget=3).to_dict()
        payload["goal"]["milestones"] = [{
            "name": "hit", "predicate": "overlap(gb_projectile, gb_enemy)"
        }]
        result = run_closed_loop_policy(
            UnityHiddenBehaviorScenario.from_dict(payload), AttackPolicy(),
            {"f": 0, "g": {"gb_player": 1, "gb_enemy": 1},
             "o": {"gb_projectile|gb_enemy": False}},
            lambda action: [
                {"f": 1, "g": {"gb_player": 1, "gb_enemy": 1},
                 "o": {"gb_projectile|gb_enemy": True}},
                {"f": 3, "g": {"gb_player": 1, "gb_enemy": 0},
                 "o": {"gb_projectile|gb_enemy": False}},
            ],
            group_totals={"gb_player": 1, "gb_enemy": 1},
        )
        self.assertEqual("pass", result.status)
        self.assertEqual(("hit",), result.milestones_reached)

    def test_missing_device_is_inconclusive_not_negative_evidence(self) -> None:
        payload = _scenario(budget=3).to_dict()
        payload["goal"] = {"predicate": "contact(goal, 0)"}
        result = run_closed_loop_policy(
            UnityHiddenBehaviorScenario.from_dict(payload), AttackPolicy(),
            {"f": 0, "g": {"gb_player": 1}},
            lambda action: {"f": 3, "g": {"gb_player": 1}},
            group_totals={"gb_player": 1},
        )
        self.assertEqual("inconclusive", result.status)
        self.assertTrue(result.missing_observations)

    def test_interior_invariant_failure_is_not_hidden_by_recovery(self) -> None:
        result = run_closed_loop_policy(
            _scenario(budget=3), AttackPolicy(),
            {"f": 0, "g": {"gb_player": 1, "gb_enemy": 1}},
            lambda action: [
                {"f": 1, "g": {"gb_player": 0, "gb_enemy": 1}},
                {"f": 3, "g": {"gb_player": 1, "gb_enemy": 0}},
            ],
            group_totals={"gb_player": 1, "gb_enemy": 1},
        )
        self.assertEqual("fail", result.status)
        self.assertEqual(("player_alive",), result.invariant_failures)

    def test_closed_loop_reaches_goal_without_sending_hidden_goal(self) -> None:
        state = {"frame": 0, "enemies": 1}
        dispatched: list[dict] = []

        def dispatch(action: PolicyAction):
            dispatched.append(action.to_dict())
            state["frame"] += action.hold_frames
            state["enemies"] = 0
            return {"f": state["frame"], "g": {"gb_player": 1, "gb_enemy": 0}}

        result = run_closed_loop_policy(
            _scenario(),
            AttackPolicy(),
            {"f": 0, "g": {"gb_player": 1, "gb_enemy": 1}},
            dispatch,
            group_totals={"gb_player": 1, "gb_enemy": 1},
        )
        self.assertEqual("pass", result.status)
        self.assertTrue(result.goal_reached)
        self.assertEqual(("enemy_removed",), result.milestones_reached)
        self.assertNotIn("goal", dispatched[0])
        self.assertNotIn("predicate", dispatched[0])

    def test_action_horizon_is_clamped_to_budget(self) -> None:
        dispatched: list[PolicyAction] = []

        def dispatch(action: PolicyAction):
            dispatched.append(action)
            return {"f": action.hold_frames, "g": {"gb_player": 1, "gb_enemy": 1}}

        result = run_closed_loop_policy(
            _scenario(budget=3),
            AttackPolicy(),
            {"f": 0, "g": {"gb_player": 1, "gb_enemy": 1}},
            dispatch,
            group_totals={"gb_player": 1, "gb_enemy": 1},
        )
        self.assertEqual("fail", result.status)
        self.assertEqual(3, result.frames_used)
        self.assertEqual(3, dispatched[0].hold_frames)

    def test_invariant_failure_stops_before_dispatch(self) -> None:
        called = False

        def dispatch(action: PolicyAction):
            nonlocal called
            called = True
            return {"f": 1, "g": {"gb_player": 0, "gb_enemy": 1}}

        result = run_closed_loop_policy(
            _scenario(),
            AttackPolicy(),
            {"f": 0, "g": {"gb_player": 0, "gb_enemy": 1}},
            dispatch,
            group_totals={"gb_player": 1, "gb_enemy": 1},
        )
        self.assertEqual("fail", result.status)
        self.assertEqual(("player_alive",), result.invariant_failures)
        self.assertFalse(called)

    def test_previous_row_milestone_detects_spawn_then_removal(self) -> None:
        payload = _scenario().to_dict()
        payload["goal"]["milestones"][0]["baseline"] = "previous_row"
        scenario = UnityHiddenBehaviorScenario.from_dict(payload)
        samples = iter(
            [
                {"f": 1, "g": {"gb_player": 1, "gb_enemy": 1}},
                {"f": 2, "g": {"gb_player": 1, "gb_enemy": 0}},
            ]
        )
        result = run_closed_loop_policy(
            scenario,
            AttackPolicy(),
            {"f": 0, "g": {"gb_player": 1, "gb_enemy": 0}},
            lambda _action: next(samples),
            group_totals={"gb_player": 1, "gb_enemy": 1},
        )
        self.assertEqual("pass", result.status)
        self.assertEqual(("enemy_removed",), result.milestones_reached)

if __name__ == "__main__":
    unittest.main()
