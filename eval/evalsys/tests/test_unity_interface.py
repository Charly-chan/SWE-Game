

from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path

from evalsys.taskgen.unity.unity_interface import (
    MANIFEST_RELATIVE,
    UNITY_EDITOR_VERSION,
    UNITY_ACTIONS,
    UnityInterfaceError,
    find_unity_project,
    load_unity_interface,
    validate_unity_interface,
)


def _unity_project(root: Path) -> Path:
    project = root / "unity_port"
    (project / "Assets" / "Scenes").mkdir(parents=True)
    (project / "Assets" / "GameBenchmark").mkdir(parents=True)
    (project / "Packages").mkdir()
    (project / "ProjectSettings").mkdir()
    (project / "ProjectSettings" / "ProjectVersion.txt").write_text(
        "m_EditorVersion: 6000.3.23f1\n", encoding="utf-8"
    )
    for name in ("Entry", "Level1", "Level2", "Victory", "Defeat"):
        (project / "Assets" / "Scenes" / f"{name}.unity").write_text(
            "%YAML 1.1\n", encoding="utf-8"
        )
    payload = {
        "schema_version": 1,
        "engine": "unity",
        "levels": [
            {"id": "L1", "scene": "Assets/Scenes/Level1.unity"},
            {"id": "L2", "scene": "Assets/Scenes/Level2.unity"},
        ],
        "actions": {action: f"Player/{action}" for action in UNITY_ACTIONS},
        "objects": {
            "gb_player": [
                {"level": "L1", "path": "World/Player"},
                {"level": "L2", "path": "World/Player"},
            ],
            "gb_goal": [
                {"level": "L1", "path": "World/Goal"},
                {"level": "L2", "path": "World/Goal"},
            ],
        },
        "numeric": {
            "health": {
                "level": "L1",
                "path": "World/Player",
                "component": "PlayerHealth",
                "member": "Current",
            }
        },
        "endings": {
            "success": {"scene": "Assets/Scenes/Victory.unity"},
            "failure": {"scene": "Assets/Scenes/Defeat.unity"},
        },
    }
    (project / MANIFEST_RELATIVE).write_text(
        json.dumps(payload, indent=2), encoding="utf-8"
    )
    return project


class UnityInterfaceTests(unittest.TestCase):
    def test_schema_v3_accepts_separate_bootstrap_entry_scene(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            project = _unity_project(Path(tmp))
            manifest_path = project / MANIFEST_RELATIVE
            manifest_path.write_text(
                json.dumps({
                    "schema_version": 3,
                    "engine": "unity",
                    "unity_editor_version": UNITY_EDITOR_VERSION,
                    "entry_scene": "Assets/Scenes/Entry.unity",
                    "levels": [{"id": "L1", "scene": "Assets/Scenes/Level1.unity"}],
                    "supported_actions": list(UNITY_ACTIONS),
                    "required_actions": ["gb_action"],
                    "analog_axes": [],
                    "semantic_roles": ["gb_player", "gb_goal"],
                    "numeric_slots": ["progress"],
                    "outcome_capabilities": ["success", "failure"],
                    "scaffold_profile_id": "ubuntu2404-unity6000.3.23f1-candidate",
                    "scaffold_digest": "sha256:" + "a" * 64,
                }),
                encoding="utf-8",
            )

            report = validate_unity_interface(project)

            self.assertEqual("pass", report.static_status)
            entry = report.by_id("static/entry_scene")
            self.assertEqual("pass", entry.status)
            self.assertFalse(entry.evidence["is_declared_level"])
            self.assertEqual({"gb_player", "gb_goal"}, report.manifest.declared_roles)
            self.assertEqual({"progress"}, report.manifest.declared_numeric_slots)

    def test_schema_v3_freezes_extended_actions_and_analog_axes(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            project = _unity_project(Path(tmp))
            manifest_path = project / MANIFEST_RELATIVE
            manifest_path.write_text(
                json.dumps({
                    "schema_version": 3,
                    "engine": "unity",
                    "unity_editor_version": UNITY_EDITOR_VERSION,
                    "entry_scene": "Assets/Scenes/Level1.unity",
                    "levels": [{"id": "L1", "scene": "Assets/Scenes/Level1.unity"}],
                    "supported_actions": [*UNITY_ACTIONS, "reload"],
                    "required_actions": ["gb_action", "reload"],
                    "analog_axes": [{
                        "id": "look_x", "why": "horizontal aiming",
                        "min": -1.0, "max": 1.0, "steps": 9,
                    }],
                    "semantic_roles": ["gb_player", "gb_goal"],
                    "numeric_slots": ["progress"],
                    "outcome_capabilities": ["success", "failure"],
                    "scaffold_profile_id": "ubuntu2404-unity6000.3.23f1-candidate",
                    "scaffold_digest": "sha256:" + "a" * 64,
                }),
                encoding="utf-8",
            )

            report = validate_unity_interface(project)

            self.assertEqual("pass", report.static_status)
            self.assertEqual(("reload",), report.manifest.extended_actions)
            self.assertEqual(("look_x",), tuple(x.id for x in report.manifest.analog_axes))

    def test_schema_v2_uses_marker_telemetry_and_outcome_contract(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            project = _unity_project(Path(tmp))
            manifest_path = project / MANIFEST_RELATIVE
            manifest_path.write_text(
                json.dumps({
                    "schema_version": 2,
                    "engine": "unity",
                    "unity_editor_version": "6000.3.23f1",
                    "entry_scene": "Assets/Scenes/Level1.unity",
                    "levels": [
                        {"id": "L1", "scene": "Assets/Scenes/Level1.unity"},
                        {"id": "L2", "scene": "Assets/Scenes/Level2.unity"},
                    ],
                    "supported_actions": list(UNITY_ACTIONS),
                    "required_actions": ["gb_left", "gb_action"],
                    "semantic_roles": ["gb_player", "gb_enemy", "gb_goal"],
                    "numeric_slots": ["health", "score"],
                    "outcome_capabilities": ["success", "failure", "checkpoint"],
                    "scaffold_profile_id": "ubuntu2404-unity6000.3.23f1-candidate",
                    "scaffold_digest": "sha256:" + "a" * 64,
                }),
                encoding="utf-8",
            )

            report = validate_unity_interface(project)

            self.assertEqual("pass", report.static_status)
            self.assertEqual(2, report.manifest.schema_version)
            self.assertEqual({"gb_player", "gb_enemy", "gb_goal"}, report.manifest.declared_roles)
            self.assertEqual({"health", "score"}, report.manifest.declared_numeric_slots)
            self.assertEqual((), report.manifest.objects)
            self.assertEqual((), report.manifest.endings)
            self.assertEqual("Player/gb_action", report.manifest.actions["gb_action"])

    def test_schema_v2_rejects_hidden_shortcuts_and_incomplete_contracts(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            project = _unity_project(Path(tmp))
            manifest_path = project / MANIFEST_RELATIVE
            manifest_path.write_text(
                json.dumps({
                    "schema_version": 2,
                    "engine": "unity",
                    "unity_editor_version": "6000.0.12f1",
                    "entry_scene": "Assets/Scenes/Missing.unity",
                    "levels": [{"id": "L1", "scene": "Assets/Scenes/Level1.unity"}],
                    "supported_actions": ["gb_left", "gb_secret"],
                    "required_actions": ["gb_action"],
                    "semantic_roles": ["gb_goal"],
                    "numeric_slots": ["Bad-Slot"],
                    "outcome_capabilities": ["checkpoint", "self_score"],
                    "scaffold_profile_id": "",
                    "scaffold_digest": "candidate-says-ok",
                    "hidden_policy": "leak",
                }),
                encoding="utf-8",
            )

            report = validate_unity_interface(project)

            self.assertEqual("fail", report.static_status)
            for diagnostic_id in (
                "static/schema", "static/editor_version", "static/entry_scene",
                "static/actions", "static/objects", "static/numeric",
                "static/endings", "static/scaffold",
            ):
                self.assertEqual("fail", report.by_id(diagnostic_id).status, diagnostic_id)

    def test_valid_static_contract_is_runtime_ready_but_not_runtime_verified(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            project = _unity_project(Path(tmp))
            report = validate_unity_interface(project)

            self.assertEqual("pass", report.static_status)
            self.assertEqual("unverified", report.status)
            self.assertTrue(report.ready_for_runtime)
            self.assertEqual("pass", report.by_id("static/actions").status)
            self.assertEqual("unverified", report.by_id("runtime/build").status)
            self.assertEqual("unverified", report.by_id("runtime/route_replay").status)
            self.assertEqual("unverified", report.by_id("runtime/vlm").status)
            self.assertIsNotNone(report.manifest)
            self.assertEqual("PlayerHealth", report.manifest.numeric[0].component)

    def test_editor_version_is_frozen_for_the_first_m5_track(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            project = _unity_project(Path(tmp))
            (project / "ProjectSettings" / "ProjectVersion.txt").write_text(
                "m_EditorVersion: 6000.1.0f1\n", encoding="utf-8"
            )
            report = validate_unity_interface(project)
            item = report.by_id("static/project_layout")
            self.assertEqual("fail", item.status)
            self.assertIn(UNITY_EDITOR_VERSION, item.detail)

    def test_schema_v1_refuses_undispatched_extra_actions(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            project = _unity_project(Path(tmp))
            manifest_path = project / MANIFEST_RELATIVE
            payload = json.loads(manifest_path.read_text(encoding="utf-8"))
            payload["actions"]["reload"] = "Player/Reload"
            manifest_path.write_text(json.dumps(payload), encoding="utf-8")
            report = validate_unity_interface(project)
            action = report.by_id("static/actions")
            self.assertEqual("fail", action.status)
            self.assertIn("does not support extra actions", action.detail)

    def test_task_specific_object_roles_and_numeric_slots_are_resolved(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            project = _unity_project(Path(tmp))
            manifest_path = project / MANIFEST_RELATIVE
            payload = json.loads(manifest_path.read_text(encoding="utf-8"))
            payload["objects"]["gb_alert_cone"] = [
                {"level": "L1", "path": "World/Guard/AlertCone"}
            ]
            payload["numeric"]["stealth_meter"] = {
                "level": "L1",
                "path": "World/Player",
                "component": "PlayerStealth",
                "member": "Exposure",
            }
            payload["self_score"] = 100
            manifest_path.write_text(json.dumps(payload), encoding="utf-8")

            report = validate_unity_interface(project)

            self.assertEqual("pass", report.static_status)
            self.assertEqual(
                ["gb_alert_cone"],
                report.by_id("static/objects").evidence["extension_roles"],
            )
            self.assertEqual(
                ["stealth_meter"],
                report.by_id("static/numeric").evidence["extension_slots"],
            )
            self.assertEqual(
                ["self_score"],
                report.by_id("static/schema").evidence["ignored_fields"],
            )
            self.assertEqual(
                {"gb_alert_cone", "gb_goal", "gb_player"},
                {item.role for item in report.manifest.objects},
            )
            self.assertEqual(
                {"health", "stealth_meter"},
                {item.slot for item in report.manifest.numeric},
            )

    def test_malformed_task_specific_object_and_numeric_ids_fail(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            project = _unity_project(Path(tmp))
            manifest_path = project / MANIFEST_RELATIVE
            payload = json.loads(manifest_path.read_text(encoding="utf-8"))
            payload["objects"]["alert_cone"] = [
                {"level": "L1", "path": "World/Guard/AlertCone"}
            ]
            payload["numeric"]["Stealth-Meter"] = {
                "level": "L1",
                "path": "World/Player",
                "component": "PlayerStealth",
                "member": "Exposure",
            }
            manifest_path.write_text(json.dumps(payload), encoding="utf-8")

            report = validate_unity_interface(project)

            self.assertEqual("fail", report.by_id("static/objects").status)
            self.assertIn("invalid object role", report.by_id("static/objects").detail)
            self.assertEqual("fail", report.by_id("static/numeric").status)
            self.assertIn("invalid numeric slot", report.by_id("static/numeric").detail)

    def test_loader_returns_normalized_dataclasses(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            project = _unity_project(Path(tmp))
            manifest = load_unity_interface(project)

            self.assertEqual(("L1", "L2"), tuple(level.id for level in manifest.levels))
            self.assertEqual(set(UNITY_ACTIONS), set(manifest.actions))
            self.assertIn("gb_attack", manifest.actions)
            self.assertIn("gb_dash", manifest.actions)
            self.assertNotIn("gb_pause", manifest.actions)
            self.assertNotIn("gb_reset", manifest.actions)
            self.assertEqual({"success", "failure"}, {item.kind for item in manifest.endings})

    def test_single_nested_unity_root_is_resolved(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            outer = Path(tmp) / "submission"
            outer.mkdir()
            project = _unity_project(outer)
            self.assertEqual(project.resolve(), find_unity_project(outer))

    def test_missing_layout_and_manifest_are_failures_not_runtime_claims(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            report = validate_unity_interface(Path(tmp))
            self.assertEqual("fail", report.status)
            self.assertEqual("fail", report.by_id("static/project_layout").status)
            self.assertEqual("fail", report.by_id("static/manifest").status)
            self.assertEqual("unverified", report.by_id("runtime/build").status)
            self.assertFalse(report.ready_for_runtime)

    def test_layout_names_must_have_the_expected_file_kinds(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            (root / "Assets").write_text("not a directory", encoding="utf-8")
            (root / "Packages").mkdir()
            (root / "ProjectSettings").mkdir()
            (root / "ProjectSettings" / "ProjectVersion.txt").mkdir()

            report = validate_unity_interface(root)
            self.assertEqual("fail", report.by_id("static/project_layout").status)
            self.assertIn("Assets", report.by_id("static/project_layout").detail)
            self.assertIn("ProjectVersion.txt", report.by_id("static/project_layout").detail)

    def test_missing_action_and_player_locator_fail(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            project = _unity_project(Path(tmp))
            manifest_path = project / MANIFEST_RELATIVE
            payload = json.loads(manifest_path.read_text(encoding="utf-8"))
            payload["actions"].pop("gb_jump")
            payload["objects"]["gb_player"] = [
                {"level": "L1", "path": "World/Player"}
            ]
            manifest_path.write_text(json.dumps(payload), encoding="utf-8")

            report = validate_unity_interface(project)
            self.assertEqual("fail", report.by_id("static/actions").status)
            self.assertIn("gb_jump", report.by_id("static/actions").detail)
            self.assertEqual("fail", report.by_id("static/objects").status)
            self.assertIn("L2", report.by_id("static/objects").detail)
            with self.assertRaises(UnityInterfaceError):
                load_unity_interface(project)

    def test_godot_pause_reset_do_not_substitute_for_unity_attack_dash(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            project = _unity_project(Path(tmp))
            manifest_path = project / MANIFEST_RELATIVE
            payload = json.loads(manifest_path.read_text(encoding="utf-8"))
            payload["actions"].pop("gb_attack")
            payload["actions"].pop("gb_dash")
            payload["actions"]["gb_pause"] = "Player/Pause"
            payload["actions"]["gb_reset"] = "Player/Reset"
            manifest_path.write_text(json.dumps(payload), encoding="utf-8")

            report = validate_unity_interface(project)
            action = report.by_id("static/actions")
            self.assertEqual("fail", action.status)
            self.assertEqual(["gb_attack", "gb_dash"], action.evidence["missing"])
            self.assertEqual(["gb_pause", "gb_reset"], action.evidence["extras"])

    def test_scene_paths_numeric_members_and_endings_are_checked(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            project = _unity_project(Path(tmp))
            manifest_path = project / MANIFEST_RELATIVE
            payload = json.loads(manifest_path.read_text(encoding="utf-8"))
            payload["levels"][0]["scene"] = "../Outside.unity"
            payload["numeric"]["health"]["member"] = ""
            payload["endings"]["failure"] = payload["endings"]["success"]
            manifest_path.write_text(json.dumps(payload), encoding="utf-8")

            report = validate_unity_interface(project)
            self.assertEqual("fail", report.by_id("static/levels").status)
            self.assertEqual("fail", report.by_id("static/numeric").status)
            self.assertEqual("fail", report.by_id("static/endings").status)
            self.assertIn("distinct", report.by_id("static/endings").detail)

    def test_report_is_json_serializable_and_keeps_unverified_scope(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            report = validate_unity_interface(_unity_project(Path(tmp)))
            payload = report.to_dict()
            json.dumps(payload)
            self.assertEqual("gamebench.mode5.unity_interface.v1", payload["schema"])
            self.assertEqual("pass", payload["static_status"])
            self.assertEqual("unverified", payload["status"])


if __name__ == "__main__":
    unittest.main()
