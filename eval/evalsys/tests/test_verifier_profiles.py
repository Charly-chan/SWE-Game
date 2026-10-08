from __future__ import annotations

import unittest

from evalsys.taskgen.content.verifier_profiles import (
    missing_strict_items,
    verifier_profile,
)


class ModeVerifierProfileTests(unittest.TestCase):
    def test_each_mode_has_a_distinct_strict_contract(self) -> None:
        brief = verifier_profile("brief")
        gdd = verifier_profile("gdd")
        skeleton = verifier_profile("skeleton")
        bugfix = verifier_profile("bugfix")
        port = verifier_profile("port")

        self.assertIn("authored_gdd_quality", brief.eligibility_ids)
        self.assertIn("brief_gdd_grounding", brief.eligibility_ids)
        self.assertIn("rubric_interface", brief.eligibility_ids)
        self.assertIn("mechanic_trace", brief.behavior_ids)
        self.assertIn("task_gdd_contract", gdd.eligibility_ids)
        self.assertIn("mechanic_trace", gdd.behavior_ids)
        self.assertIn("skeleton_integrity", skeleton.eligibility_ids)
        self.assertIn("stub_completion", skeleton.eligibility_ids)
        self.assertIn("transformation_contract", skeleton.eligibility_ids)
        self.assertIn("repair_differential", bugfix.behavior_ids)
        self.assertIn("feature_kept", bugfix.eligibility_ids)
        self.assertIn("transformation_contract", bugfix.eligibility_ids)
        self.assertNotIn("unity_route_replay", port.strict_ids)
        self.assertNotIn("legacy_reference_trace", port.strict_ids)
        self.assertIn("legacy_reference_trace", port.fidelity_ids)
        self.assertIn("unity_hidden_behavior", port.behavior_ids)
        self.assertNotIn("unity_source_behavior", port.behavior_ids)
        self.assertIn("unity_source_behavior", port.fidelity_ids)
        self.assertIn("unity_counterfactual", port.behavior_ids)

    def test_missing_items_are_reported_in_stable_order(self) -> None:
        profile = verifier_profile("bugfix")
        observed = set(profile.strict_ids) - {"feature_kept", "repair_differential"}
        self.assertEqual(
            ("feature_kept", "repair_differential"),
            missing_strict_items("bugfix", observed),
        )

    def test_fidelity_is_expected_but_not_a_strict_gate(self) -> None:
        for mode in ("brief", "gdd", "skeleton", "bugfix"):
            profile = verifier_profile(mode)
            expected = {"reproduction"}
            if mode == "bugfix":
                expected.add("repair_restoration_graded")
                expected.add("edit_radius")
            self.assertEqual(frozenset(expected), profile.fidelity_ids)
            for item_id in expected:
                self.assertNotIn(item_id, profile.strict_ids)


if __name__ == "__main__":
    unittest.main()
