

from __future__ import annotations

import json
import os
import tempfile
import unittest
from pathlib import Path

from evalsys.taskgen.unity.unity_interface import (
    UnityEnding,
    UnityInterfaceManifest,
    UnityLevel,
)
from evalsys.taskgen.unity.unity_runtime import _render_builder, build_unity_submission
from evalsys.taskgen.unity.unity_environment import UnityEnvironmentProfile


LOCAL_LINUX = UnityEnvironmentProfile(
    profile_id="test-local-linux",
    environment_class="local-linux-uncertified",
    platform="test",
)


def _project(root: Path) -> Path:
    project = root / "project"
    (project / "Assets").mkdir(parents=True)
    (project / "Packages").mkdir()
    (project / "ProjectSettings").mkdir()
    (project / "ProjectSettings" / "ProjectVersion.txt").write_text(
        "m_EditorVersion: 6000.3.23f1\n", encoding="utf-8"
    )
    (project / "Packages" / "manifest.json").write_text(
        json.dumps({"dependencies": {}}), encoding="utf-8"
    )
    (project / "BUILD.md").write_text(
        "do-not-execute-this-candidate-command\n", encoding="utf-8"
    )
    return project


def _manifest() -> UnityInterfaceManifest:
    return UnityInterfaceManifest(
        schema_version=1,
        engine="unity",
        levels=(UnityLevel("L1", "Assets/Scenes/L1.unity"),),
        actions={},
        objects=(),
        numeric=(),
        endings=(
            UnityEnding("success", "Assets/Scenes/Win.unity"),
            UnityEnding("failure", "Assets/Scenes/Lose.unity"),
        ),
        source="fixture",
    )


def _fake_unity(root: Path, mode: str) -> Path:
    binary = root / f"fake-unity-{mode}"
    binary.write_text(
        """#!/usr/bin/env python3
import pathlib
import re
import sys

if "-version" in sys.argv:
    print("6000.3.23f1")
    raise SystemExit(0)
mode = pathlib.Path(sys.argv[0]).name.rsplit("-", 1)[-1]
log = pathlib.Path(sys.argv[sys.argv.index("-logFile") + 1])
if mode == "license":
    log.write_text("No valid Unity Editor license", encoding="utf-8")
    raise SystemExit(1)
if mode == "fail":
    log.write_text("C# compiler error", encoding="utf-8")
    raise SystemExit(1)
if mode == "killed":
    log.write_text("build interrupted", encoding="utf-8")
    raise SystemExit(137)
if mode == "empty":
    log.write_text("returned zero without a player", encoding="utf-8")
    raise SystemExit(0)
project = pathlib.Path(sys.argv[sys.argv.index("-projectPath") + 1])
packages = __import__('json').loads((project / "Packages/manifest.json").read_text())
if packages.get("dependencies", {}).get("com.unity.modules.audio") != "1.0.0":
    raise SystemExit(5)
source = (project / "Assets/Editor/GameBenchmarkEvaluatorBuild.cs").read_text(encoding="utf-8")
assert "GB_UNITY_SCRIPTING_BACKEND" in source
assert "if (!string.IsNullOrWhiteSpace(backend))" in source
probe = project / "Assets/GameBenchmarkEvaluator/GameBenchmarkEvaluatorProbe.cs"
if not probe.is_file() or "gamebench.unity-controller.v1" not in probe.read_text(encoding="utf-8"):
    raise SystemExit(4)
match = re.search(r'locationPathName = "([^"]+)"', source)
if not match:
    raise SystemExit(3)
output = pathlib.Path(match.group(1))
output.parent.mkdir(parents=True, exist_ok=True)
output.write_text("player", encoding="utf-8")
log.write_text("build succeeded", encoding="utf-8")
raise SystemExit(0)
""",
        encoding="utf-8",
    )
    binary.chmod(0o755)
    return binary


class UnityRuntimeTests(unittest.TestCase):
    def test_community_builder_includes_candidate_owned_ending_scenes_only_in_community(self) -> None:
        scenes = ("Assets/Game/Scenes/Level1.unity",)
        default = _render_builder(scenes, Path("/out/game.x86_64"))
        community = _render_builder(
            scenes, Path("/out/game.x86_64"), include_candidate_scenes=True,
        )
        self.assertNotIn('AssetDatabase.FindAssets("t:Scene"', default)
        self.assertIn('AssetDatabase.FindAssets("t:Scene"', community)
        self.assertIn("Distinct(StringComparer.Ordinal)", community)
        self.assertIn('"Assets/Game/Scenes/Level1.unity"', community)

    def test_injected_probe_forces_background_player_loop(self) -> None:
        probe = (
            Path(__file__).resolve().parents[1]
            / "evalsys" / "taskgen" / "unity" / "unity_runtime_probe.cs"
        ).read_text(encoding="utf-8")
        self.assertIn("Application.runInBackground = true;", probe)
        self.assertIn("InputSystem.ListEnabledActions()", probe)
        self.assertIn('Fatal("input dispatch failed: "', probe)

    def test_probe_resolves_marker_subtree_geometry_without_hierarchy_paths(self) -> None:
        probe = (
            Path(__file__).resolve().parents[1]
            / "evalsys" / "taskgen" / "unity" / "unity_runtime_probe.cs"
        ).read_text(encoding="utf-8")
        self.assertIn("GetComponentsInChildren<Collider>(true)", probe)
        self.assertIn("GetComponentsInChildren<Collider2D>(true)", probe)
        self.assertIn("GetComponentsInChildren<Renderer>(true)", probe)
        self.assertIn("ResolvedContact", probe)

    def test_probe_reads_only_the_versioned_observable_state_component(self) -> None:
        unity_root = (
            Path(__file__).resolve().parents[1]
            / "evalsys" / "taskgen" / "unity"
        )
        probe = (unity_root / "unity_runtime_probe.cs").read_text(encoding="utf-8")
        sdk = (
            unity_root / "target_unity" / "Assets" / "GameBenchmarkSDK"
            / "GBObservableState.cs"
        ).read_text(encoding="utf-8")
        self.assertIn("GetComponentInChildren<GBObservableState>", probe)
        self.assertIn("SnapshotBooleans", probe)
        self.assertIn("SnapshotText", probe)
        self.assertIn("SnapshotNumeric", probe)
        self.assertIn("public sealed class GBObservableState", sdk)
        self.assertNotIn("System.Reflection", probe + sdk)

    def test_missing_editor_is_inconclusive(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            result = build_unity_submission(
                _project(root), _manifest(), root / "out", unity_bin=root / "missing",
                environment_profile=LOCAL_LINUX, trusted_fixture=True,
            )
            self.assertEqual("inconclusive", result.status)
            self.assertIn("unavailable", result.detail)

    @unittest.skipIf(os.name == "nt", "POSIX fake executable; Windows refusal is tested separately")
    def test_fixed_evaluator_build_can_pass(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            project = _project(root)
            result = build_unity_submission(
                project,
                _manifest(),
                root / "out",
                unity_bin=_fake_unity(root, "pass"),
                environment_profile=LOCAL_LINUX,
                trusted_fixture=True,
            )
            self.assertEqual("pass", result.status)
            self.assertTrue(Path(result.executable or "").is_file())
            self.assertNotIn("do-not-execute", " ".join(result.command))
            self.assertFalse(any("BUILD.md" in part for part in result.command))
            self.assertEqual("gamebench.unity-controller.v1", result.probe_protocol)

    @unittest.skipIf(os.name == "nt", "POSIX fake executable; Windows refusal is tested separately")
    def test_project_build_error_is_failure(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            result = build_unity_submission(
                _project(root),
                _manifest(),
                root / "out",
                unity_bin=_fake_unity(root, "fail"),
                environment_profile=LOCAL_LINUX,
                trusted_fixture=True,
            )
            self.assertEqual("fail", result.status)
            self.assertEqual(1, result.returncode)
            self.assertEqual("submission", result.attribution)

    @unittest.skipIf(os.name == "nt", "POSIX fake executable; Windows refusal is tested separately")
    def test_killed_editor_is_infrastructure_not_submission_failure(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            result = build_unity_submission(
                _project(root), _manifest(), root / "out",
                unity_bin=_fake_unity(root, "killed"),
                environment_profile=LOCAL_LINUX, trusted_fixture=True,
            )
            self.assertEqual("inconclusive", result.status)
            self.assertEqual(137, result.returncode)
            self.assertEqual("infrastructure", result.attribution)

    @unittest.skipIf(os.name == "nt", "POSIX fake executable; Windows refusal is tested separately")
    def test_repeated_build_cannot_reuse_old_player(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            project = _project(root)
            out = root / "out"
            first = build_unity_submission(
                project, _manifest(), out, unity_bin=_fake_unity(root, "pass"),
                environment_profile=LOCAL_LINUX, trusted_fixture=True,
            )
            self.assertEqual("pass", first.status)
            second = build_unity_submission(
                project, _manifest(), out, unity_bin=_fake_unity(root, "empty"),
                environment_profile=LOCAL_LINUX, trusted_fixture=True,
            )
            self.assertEqual("fail", second.status)
            self.assertIn("missing", second.detail)
            self.assertEqual("submission", second.attribution)

    @unittest.skipIf(os.name == "nt", "POSIX fake executable; Windows refusal is tested separately")
    def test_license_error_is_evaluator_inconclusive(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            result = build_unity_submission(
                _project(root),
                _manifest(),
                root / "out",
                unity_bin=_fake_unity(root, "license"),
                environment_profile=LOCAL_LINUX,
                trusted_fixture=True,
            )
            self.assertEqual("inconclusive", result.status)
            self.assertIn("licensing", result.detail.lower())
            self.assertEqual("infrastructure", result.attribution)

    def test_windows_profile_refuses_build_as_infrastructure(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            profile = UnityEnvironmentProfile(
                profile_id="local-windows",
                environment_class="local-windows",
                platform="Windows",
                detail="Windows refusal: certified Linux VM required",
            )
            result = build_unity_submission(
                _project(root), _manifest(), root / "out", environment_profile=profile
            )
            self.assertEqual("inconclusive", result.status)
            self.assertEqual("infrastructure", result.attribution)
            self.assertEqual("local-windows", result.environment["environment_class"])
            self.assertFalse(result.environment["score_eligible"])
            self.assertEqual((), result.command)


if __name__ == "__main__":
    unittest.main()
