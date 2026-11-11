from __future__ import annotations

import unittest
import json
from pathlib import Path

from evalsys.taskgen.unity.unity_observer import UnitySemanticReportBuilder
from evalsys.taskgen.unity.unity_semantic import validate_unity_semantic_report


def _row(frame: int, enemies: int, *, level: int = 1, clear: bool = False) -> dict:
    return {
        "f": frame,
        "g": {"gb_player": 1, "gb_enemy": enemies},
        "o": {"gb_enemy": False},
        "px": float(frame), "py": 0.0, "pz": 0.0,
        "n": {"score": float((1 - enemies) * 100)},
        "wgc": clear, "lv": level, "d": {}, "c": [], "so": "",
        "vx": 1.0, "vy": 0.0, "vz": 0.0,
        "s": {
            "numeric": {"score": float((1 - enemies) * 100)},
            "audio_events": 0, "anim": 0, "node_count": 2,
            "visible_count": 2, "text": 0,
        },
    }


class UnityObserverAggregationTests(unittest.TestCase):
    def test_saved_real_unity_contact_trace_uses_production_adapter(self) -> None:


        fixture = Path(__file__).parent / "fixtures/unity_semantic/real_observer_contact_20260908.json"
        payload = json.loads(fixture.read_text(encoding="utf-8"))
        raw = payload["report"]
        builder = UnitySemanticReportBuilder("saved-real-contact", declared_level_count=1)
        builder.add_session(raw["rows"], [{"kind": "bounds_frozen", **raw["bounds"]}])
        report = builder.build(stop_reason="goal_reached")
        from evalsys.routes.runner import observation_from_row
        from evalsys.routes.schema import parse_predicate
        predicate = parse_predicate("contact(goal, 0)")
        values = [predicate.evaluate(observation_from_row(row, report["group_totals"]))
                  for row in report["rows"]]
        self.assertFalse(values[0])
        self.assertTrue(any(values[1:]))
        self.assertGreater(report["device_contacts"]["goal#0"], 0)
        self.assertIn("goal#0", report["device_index"])

    def test_device_contact_survives_scene_unload(self) -> None:
        builder = UnitySemanticReportBuilder("goal", declared_level_count=1)
        contact = _row(1, 0)
        contact.update(d={"goal#0": {"present": True}}, c=["goal#0"])
        builder.add_observation({"row": contact, "events": []})
        builder.add_observation({"row": _row(2, 0), "events": []})
        report = builder.build(stop_reason="goal_reached")
        self.assertIn("goal#0", report["device_index"])
        self.assertEqual(1, report["device_contacts"]["goal#0"])

    def test_builds_complete_authoritative_report_from_raw_stream(self) -> None:
        builder = UnitySemanticReportBuilder("scenario/positive", declared_level_count=2)
        builder.steps = 1
        builder.add_observation({
            "row": _row(10, 1),
            "events": [{
                "kind": "bounds_frozen",
                "min": {"x": -5.0, "y": -2.0, "z": 0.0},
                "max": {"x": 20.0, "y": 5.0, "z": 1.0},
            }],
        })
        builder.add_observation({
            "row": _row(13, 0, clear=True),
            "events": [{"kind": "outcome_success", "frame": 13}],
        })

        report = builder.build(stop_reason="goal_reached")

        validate_unity_semantic_report(report)
        self.assertEqual({"gb_player": 1, "gb_enemy": 1}, report["group_totals"])
        self.assertEqual(-5.0, report["bounds"]["min"]["x"])
        self.assertTrue(report["success_before_all_levels"])
        self.assertEqual(1, report["steps"])

    def test_rejects_non_monotonic_frames(self) -> None:
        builder = UnitySemanticReportBuilder("scenario", declared_level_count=1)
        builder.add_observation({"row": _row(4, 1), "events": []})
        with self.assertRaisesRegex(ValueError, "strictly increasing"):
            builder.add_observation({"row": _row(4, 1), "events": []})


if __name__ == "__main__":
    unittest.main()
