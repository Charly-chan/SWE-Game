from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path

from evalsys.conformance import ACTIONS, GROUPS, scan_conformance
from evalsys.interface.loader import load_submission_interface
from evalsys.ocard.channels import interface_blocked
from evalsys.verdict import Verdict


class SubmissionConformanceTests(unittest.TestCase):
    def _project(self, root: Path) -> None:
        actions = "[input]\n\n" + "\n".join(
            f'{name}={{\n"deadzone": 0.5,\n"events": [Object()]\n}}' for name in ACTIONS
        )
        (root / "project.godot").write_text(
            '[application]\nrun/main_scene="res://level.tscn"\n' + actions,
            encoding="utf-8",
        )
        (root / "level.tscn").write_text(
            '[gd_scene format=3]\n[node name="Player" type="Node"]\n' +
            "\n".join(f'; vocabulary {name}' for name in GROUPS),
            encoding="utf-8",
        )
        (root / "player.gd").write_text(
            "\n".join(
                f'func _poll_{name}(): return Input.is_action_pressed("{name}")'
                for name in ACTIONS
            ),
            encoding="utf-8",
        )
        (root / "win.tscn").write_text('[gd_scene format=3]\n', encoding="utf-8")
        (root / "lose.tscn").write_text('[gd_scene format=3]\n', encoding="utf-8")
        (root / "gb_levels.json").write_text(json.dumps({
            "levels": ["res://level.tscn"],
            "endings": {"victory": "res://win.tscn", "defeat": "res://lose.tscn"},
            "numeric": {"progress": "game_progress"},
        }), encoding="utf-8")

    def test_complete_generic_interface_is_green(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            self._project(root)
            report = scan_conformance(root)
            self.assertTrue(report.ok, report.to_dict())
            self.assertNotIn("observe()", report.missing)

    def test_endings_routed_to_one_screen_is_not_conformant(self) -> None:


        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            self._project(root)
            manifest = json.loads((root / "gb_levels.json").read_text(encoding="utf-8"))
            manifest["endings"] = {
                "victory": "res://win.tscn",
                "defeat": "res://win.tscn",
            }
            (root / "gb_levels.json").write_text(json.dumps(manifest), encoding="utf-8")

            report = scan_conformance(root)
            self.assertFalse(report.ok, report.to_dict())
            endings = report.checks["endings"]
            self.assertEqual("fail", endings["status"])
            self.assertEqual(["res://win.tscn"], endings["shared_scenes"])
            self.assertEqual([], endings["discriminating_scenes"])
            self.assertIn("gb_levels.json.endings", report.missing)

    def test_level_clear_syntax_error_fails_its_check_but_is_not_a_hard_gate(self) -> None:


        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            self._project(root)
            manifest = json.loads((root / "gb_levels.json").read_text(encoding="utf-8"))
            manifest["level_clear"] = {"predicate": "not_a_function()"}
            (root / "gb_levels.json").write_text(json.dumps(manifest), encoding="utf-8")
            report = scan_conformance(root)
            self.assertTrue(report.ok, report.to_dict())
            self.assertEqual("fail", report.checks["level_clear"]["status"])
            self.assertNotIn("gb_levels.json.level_clear(valid predicate)", report.missing)
            self.assertIsNone(load_submission_interface(root).level_clear)

            manifest["level_clear"] = {"predicate": "count_delta(gb_enemy) <= -8"}
            (root / "gb_levels.json").write_text(json.dumps(manifest), encoding="utf-8")
            report = scan_conformance(root)
            self.assertTrue(report.ok, report.to_dict())
            self.assertEqual("pass", report.checks["level_clear"]["status"])

    def test_bound_but_unread_alias_is_not_conformant(self) -> None:


        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            self._project(root)
            (root / "player.gd").write_text(
                "func _ready(): pass\n", encoding="utf-8"
            )
            report = scan_conformance(root)
            self.assertFalse(report.ok, report.to_dict())
            actions = report.checks["actions"]
            self.assertEqual("fail", actions["status"])
            self.assertEqual(list(ACTIONS), actions["unread"])
            self.assertTrue(set(ACTIONS).issubset(report.missing))

    def test_stripped_interface_lists_the_contract_not_mechanics(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            (root / "project.godot").write_text("[application]\n", encoding="utf-8")
            report = scan_conformance(root)
            self.assertFalse(report.ok)
            self.assertIn("gb_player", report.missing)
            self.assertIn("gb_levels.json", report.missing)
            self.assertTrue(set(ACTIONS).issubset(report.missing))
            rendered = json.dumps(report.to_dict())
            self.assertNotIn("mechanic failed", rendered)

    def test_mechanic_specific_numeric_slot_is_rejected(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            self._project(root)
            manifest = json.loads((root / "gb_levels.json").read_text(encoding="utf-8"))
            manifest["numeric"] = {"cells_delivered": "cells_delivered"}
            (root / "gb_levels.json").write_text(json.dumps(manifest), encoding="utf-8")
            report = scan_conformance(root)
            self.assertTrue(report.ok, report.to_dict())
            self.assertEqual(report.checks["numeric"]["status"], "fail")
            self.assertEqual({}, dict(load_submission_interface(root).numeric))

    def test_numeric_values_report_their_form_and_driver_resolution(self) -> None:


        from evalsys.interface.loader import classify_numeric_value

        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            self._project(root)
            (root / "state.gd").write_text("extends Node\nvar health := 3\nvar stage_index := 0\n", encoding="utf-8")
            manifest = json.loads((root / "gb_levels.json").read_text(encoding="utf-8"))
            manifest["numeric"] = {
                "health": "/root/GB:health",
                "progress": "stage_index",
                "score": "gb_player.score",
                "timer": "res://level.tscn::Hud:timer",
            }
            (root / "gb_levels.json").write_text(json.dumps(manifest), encoding="utf-8")
            report = scan_conformance(root)

            self.assertTrue(report.ok, report.to_dict())
            numeric = report.checks["numeric"]
            self.assertEqual("fail", numeric["status"])
            self.assertIn("cannot resolve them as declared", numeric["detail"])
            self.assertIn("health='/root/GB:health' (node_path)", numeric["detail"])
            values = numeric["values"]
            self.assertEqual("node_path", values["health"]["form"])
            self.assertFalse(values["health"]["driver_resolves"])
            self.assertEqual("health", values["health"]["property"])
            self.assertTrue(values["health"]["declared_in_source"])
            self.assertEqual("property_name", values["progress"]["form"])
            self.assertTrue(values["progress"]["driver_resolves"])
            self.assertEqual("dotted_owner_path", values["score"]["form"])
            self.assertEqual("scene_address", values["timer"]["form"])
            self.assertIn("bare property name", numeric["accepted_form"])

            self.assertEqual(manifest["numeric"], dict(load_submission_interface(root).numeric))

            manifest["numeric"] = {"health": "health"}
            (root / "gb_levels.json").write_text(json.dumps(manifest), encoding="utf-8")
            numeric = scan_conformance(root).checks["numeric"]
            self.assertEqual("pass", numeric["status"])
            self.assertTrue(numeric["values"]["health"]["driver_resolves"])
        self.assertEqual("other", classify_numeric_value("health - 1")["form"])

    def test_device_ids_reject_reference_specific_vocabulary(self) -> None:

        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            self._project(root)
            manifest_path = root / "gb_levels.json"
            manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
            manifest["device_ids"] = {
                "energycell#0": "Cells/EastEnergyCell",
            }
            manifest_path.write_text(json.dumps(manifest), encoding="utf-8")

            report = scan_conformance(root)
            self.assertTrue(report.ok, report.to_dict())
            check = report.checks["device_ids"]
            self.assertEqual("fail", check["status"])
            self.assertEqual(["energycell#0"], check["invalid"])
            self.assertNotIn(
                "gb_levels.json.device_ids(valid generic mapping)", report.missing
            )

    def test_capture_addresses_are_optional_and_malformed_ones_are_ignored(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            self._project(root)
            manifest_path = root / "gb_levels.json"
            manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
            manifest["anchor_camera"] = "AnchorCamera"
            manifest["audio_buses"] = []
            manifest_path.write_text(json.dumps(manifest), encoding="utf-8")
            report = scan_conformance(root)
            self.assertTrue(report.ok, report.to_dict())
            self.assertEqual("fail", report.checks["anchor_camera"]["status"])
            self.assertEqual("fail", report.checks["audio_buses"]["status"])
            self.assertIsNone(load_submission_interface(root).anchor_camera)
            self.assertEqual((), load_submission_interface(root).audio_buses)

            manifest["anchor_camera"] = "res://level.tscn::AnchorCamera"
            manifest["audio_buses"] = ["Master", "SFX"]
            manifest_path.write_text(json.dumps(manifest), encoding="utf-8")
            report = scan_conformance(root)
            self.assertTrue(report.ok, report.to_dict())

    def test_interface_gap_is_attributed_once_not_called_bad_mechanics(self) -> None:
        result = interface_blocked("O3", ("observe.anchor", "observe.travel_t"))
        self.assertFalse(result.measured)
        self.assertTrue(all(item.verdict is Verdict.UNMEASURABLE for item in result.items))
        self.assertEqual(result.evidence["attributed_to"], "evaluator")
        self.assertIn("rather than a false submission failure", result.detail)


if __name__ == "__main__":
    unittest.main()
