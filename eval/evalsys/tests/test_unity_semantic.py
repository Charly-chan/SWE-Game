

from __future__ import annotations

import copy
import json
import unittest
from pathlib import Path

from evalsys.routes.runner import reading_from_report, verdict_for
from evalsys.routes.schema import Route
from evalsys.taskgen.unity.unity_semantic import (
    UnitySemanticReportError,
    reading_from_unity_report,
    validate_unity_semantic_report,
)
from evalsys.verdict import Attribution, Verdict


FIXTURE_ROOT = Path(__file__).parent / "fixtures" / "unity_semantic"


class UnitySemanticParityTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        payload = json.loads(
            (FIXTURE_ROOT / "semantic_cases.json").read_text(encoding="utf-8")
        )
        cls.cases = {case["id"]: case for case in payload["cases"]}
        cls.expected = json.loads(
            (FIXTURE_ROOT / "expected_readings.json").read_text(encoding="utf-8")
        )["readings"]

    def test_committed_readings_are_frozen_and_unity_path_is_field_equal(self) -> None:
        self.assertEqual(set(self.expected), set(self.cases))
        for case_id, case in self.cases.items():
            with self.subTest(case=case_id):
                route = Route.from_dict(case["route"])
                direct = reading_from_report(route, case["report"], log="").to_dict()
                adapted = reading_from_unity_report(route, case["report"]).to_dict()
                self.assertEqual(self.expected[case_id], direct)
                self.assertEqual(direct, adapted)

    def test_fixture_covers_semantic_success_and_previous_row_delta(self) -> None:
        reading = self.expected["complete_semantics"]
        self.assertTrue(reading["reached"])
        self.assertTrue(reading["clean_clear"])
        self.assertEqual(
            ["projectile_contact", "enemy_removed"], reading["milestones_reached"]
        )
        self.assertEqual(
            ["collected", "score_changed", "progressed", "moved_halfway"],
            reading["observations_reached"],
        )

    def test_fixture_covers_invariant_missing_error_and_stop_attribution(self) -> None:
        invariant = self.expected["invariant_failure"]
        self.assertEqual(["player_alive"], invariant["invariants_failed"])
        self.assertFalse(invariant["segment_clean"])
        missing_case = self.cases["missing_observation_harness_stop"]
        missing_route = Route.from_dict(missing_case["route"])
        missing = reading_from_unity_report(missing_route, missing_case["report"])
        self.assertEqual(["numeric:progress"], missing.missing_groups)
        harness_item = verdict_for(missing_route, missing)
        self.assertEqual(Verdict.INCONCLUSIVE, harness_item.verdict)
        self.assertEqual(Attribution.HARNESS, harness_item.attribution)
        submission_case = self.cases["submission_stop_and_error"]
        submission_route = Route.from_dict(submission_case["route"])
        submission = reading_from_unity_report(
            submission_route, submission_case["report"]
        )
        self.assertFalse(submission.segment_clean)
        submission_item = verdict_for(submission_route, submission)
        self.assertEqual(Verdict.SKIPPED, submission_item.verdict)
        self.assertEqual(Attribution.SUBMISSION, submission_item.attribution)

    def test_schema_rejects_missing_bounds_and_incomplete_liveness_signature(self) -> None:
        report = copy.deepcopy(self.cases["complete_semantics"]["report"])
        del report["bounds"]
        with self.assertRaisesRegex(UnitySemanticReportError, "bounds"):
            validate_unity_semantic_report(report)
        report = copy.deepcopy(self.cases["complete_semantics"]["report"])
        del report["rows"][0]["s"]["visible_count"]
        with self.assertRaisesRegex(UnitySemanticReportError, "visible_count"):
            validate_unity_semantic_report(report)


if __name__ == "__main__":
    unittest.main()
