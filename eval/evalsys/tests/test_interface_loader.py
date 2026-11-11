from __future__ import annotations

import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from evalsys.harness import FACES, face_path, provenance
from evalsys.interface import load_submission_interface
from evalsys.interface import loader
from evalsys.interface.contract import ACTIONS

from _interface_fixture import write_conformant_project


class InterfaceLoaderTests(unittest.TestCase):
    def test_compact_input_dictionaries_are_parsed_per_action(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = write_conformant_project(Path(tmp))
            project = (root / "project.godot").read_text(encoding="utf-8")
            compact = "[input]\n" + "\n".join(
                f'{name}={{"deadzone":0.5,"events":['
                f'Object(InputEventKey,"keycode":{65 + index})]}}'
                for index, name in enumerate(ACTIONS)
            )
            project = project[:project.index("[input]")] + compact + "\n"
            (root / "project.godot").write_text(project, encoding="utf-8")

            loaded = load_submission_interface(root)
            self.assertEqual(tuple(ACTIONS), loaded.actions.bound)
            self.assertEqual((), loaded.actions.missing)

    def test_empty_events_and_object_stub_are_not_human_bindings(self) -> None:
        project = (
            '[input]\n'
            'gb_left={"events":[]}\n'
            'gb_right={"events":[Object()]}\n'
            'gb_up={"events":[Object(InputEventKey,"physical_keycode":87)]}\n'
        )
        self.assertFalse(loader.action_has_human_binding(project, "gb_left"))
        self.assertFalse(loader.action_has_human_binding(project, "gb_right"))
        self.assertTrue(loader.action_has_human_binding(project, "gb_up"))
        self.assertFalse(loader._action_sections(project)["gb_left"])
        self.assertTrue(loader._action_sections(project)["gb_right"])

    def test_asset_probe_is_loadable_but_not_a_scoring_identity_face(self) -> None:
        path = face_path("gb_asset_probe.gd")
        self.assertTrue(path.is_file())
        self.assertNotIn("gb_asset_probe.gd", FACES)
        self.assertNotIn("gb_asset_probe.gd", provenance())

    def test_runtime_faces_do_not_read_the_raw_manifest(self) -> None:
        eval_root = Path(__file__).resolve().parents[2]
        faces = (
            "gb_truth_driver.gd",
            "gb_route_driver.gd",
            "gb_capture_probe.gd",
            "gb_asset_probe.gd",
        )
        for name in faces:
            text = (eval_root / "harness" / name).read_text(encoding="utf-8")
            self.assertNotIn("gb_levels.json", text, name)
            self.assertIn("runtime_interface()", text, name)

    def test_legacy_python_manifest_helpers_are_gone(self) -> None:
        source = (
            Path(__file__).resolve().parents[1]
            / "evalsys" / "probe" / "numeric_props.py"
        ).read_text(encoding="utf-8")
        self.assertNotIn("def parse_numeric_mapping", source)
        self.assertNotIn("def parse_level_clear", source)

    def test_nested_project_root_and_hashes_are_stable(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            outer = Path(tmp) / "submission"
            project = write_conformant_project(outer / "game")
            first = load_submission_interface(outer)
            second = load_submission_interface(outer)
            self.assertEqual(project.resolve(), first.project_root)
            self.assertEqual(first.source_sha256, second.source_sha256)
            self.assertEqual(first.normalized_sha256, second.normalized_sha256)
            self.assertEqual(first.runtime_dict(), second.runtime_dict())

    def test_levels_entries_must_be_res_scene_path_strings(self) -> None:


        import json

        from _interface_fixture import read_manifest, write_manifest

        with tempfile.TemporaryDirectory() as tmp:
            root = write_conformant_project(Path(tmp))
            manifest = read_manifest(root)
            manifest["levels"] = [
                {"id": "stage1", "name": "Coastal Run", "scene": "res://level.tscn"},
                "level.tscn",
                "res://missing.tscn",
                "res://level.tscn",
            ]
            write_manifest(root, manifest)
            loaded = load_submission_interface(root)
            check = loaded.report.checks["gb_levels"]
            self.assertEqual("fail", check["status"])
            self.assertIn("gb_levels.json", loaded.report.missing)
            self.assertEqual(
                [
                    "levels[0] is a JSON object, not a string",
                    "levels[1] 'level.tscn' is not a res:// path",
                    "levels[2] 'res://missing.tscn' does not name a shipped scene file",
                ],
                check["unresolved"],
            )
            self.assertEqual(
                "gb_levels.json invalid: unresolved entries: "
                + ", ".join(check["unresolved"]) + "; " + loader.LEVELS_ACCEPTED_FORM,
                check["detail"],
            )
            self.assertEqual(loader.LEVELS_ACCEPTED_FORM, check["accepted_form"])

            self.assertFalse(loaded.levels[0].valid)
            self.assertTrue(loaded.levels[3].valid)


            manifest["levels"] = ["res://level.tscn"]
            write_manifest(root, manifest)
            self.assertEqual("pass", load_submission_interface(root).report.checks["gb_levels"]["status"])

        schema = json.loads(
            (Path(__file__).resolve().parents[2] / "interface" / "gb_interface.schema.json")
            .read_text(encoding="utf-8")
        )
        self.assertEqual(
            {"type": "string", "pattern": "^res://.+"},
            schema["properties"]["levels"]["items"],
        )

    def test_loader_parses_manifest_once(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = write_conformant_project(Path(tmp))
            original = loader.json.loads
            calls = []

            def counted(value):
                calls.append(value)
                return original(value)

            with patch.object(loader.json, "loads", side_effect=counted):
                loaded = load_submission_interface(root)
            self.assertTrue(loaded.report.ok, loaded.report.to_dict())
            self.assertEqual(1, len(calls))


if __name__ == "__main__":
    unittest.main()
