from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from evalsys.taskgen.unity.unity_sdk import (
    TARGET_UNITY_ROOT,
    build_scaffold_digest_manifest,
    immutable_scaffold_paths,
    scaffold_manifest_digest,
    sha256_file,
    validate_scaffold_integrity,
)


class UnitySDKIntegrityTests(unittest.TestCase):
    def test_committed_target_scaffold_is_complete_and_self_validating(self) -> None:
        immutable = immutable_scaffold_paths(TARGET_UNITY_ROOT)
        expected = build_scaffold_digest_manifest(TARGET_UNITY_ROOT, immutable)
        report = validate_scaffold_integrity(TARGET_UNITY_ROOT, expected)
        self.assertTrue(report.ok)
        self.assertIn("Assets/GameBenchmarkSDK/GBEntity.cs", immutable)
        self.assertIn("Assets/GameBenchmarkSDK/GameBenchmarkInput.inputactions", immutable)
        self.assertIn("Packages/packages-lock.json", immutable)
        self.assertEqual(
            "sha256:6401eb245caeeead56498830fb414c689d7a1f36d6a6d60340a17b701581703f",
            scaffold_manifest_digest(expected),
        )
        self.assertEqual(
            "sha256:875008478396009708fdcd333d8b3108097a575652e36e9f3ecde66e0af21f26",
            sha256_file(TARGET_UNITY_ROOT / "Packages/com.unity.inputsystem-1.14.2.tgz"),
        )
        self.assertEqual(
            "sha256:ccddb289e34e557e669cca082bfd821d1773f21ec61090e97172264e65dcf3b0",
            sha256_file(TARGET_UNITY_ROOT / "Packages/packages-lock.json"),
        )

    @staticmethod
    def _project(root: Path) -> tuple[Path, list[str]]:
        project = root / "project"
        files = [
            "Assets/GameBenchmarkSDK/GBEntity.cs",
            "Assets/GameBenchmarkSDK/GBOutcome.cs",
            "Assets/GameBenchmarkSDK/GBTelemetry.cs",
            "Assets/GameBenchmarkSDK/GameBenchmarkInput.inputactions",
            "Packages/manifest.json",
            "Packages/packages-lock.json",
            "ProjectSettings/ProjectVersion.txt",
        ]
        for relative in files:
            path = project / relative
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(relative + "\n", encoding="utf-8")
        return project, files

    def test_exact_evaluator_owned_manifest_passes(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            project, files = self._project(Path(tmp))
            expected = build_scaffold_digest_manifest(project, files)
            report = validate_scaffold_integrity(project, expected)
            self.assertTrue(report.ok)
            self.assertEqual((), report.missing)
            self.assertEqual((), report.changed)
            self.assertEqual((), report.unexpected_sdk_files)

    def test_tamper_missing_and_extra_sdk_file_are_distinct(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            project, files = self._project(Path(tmp))
            expected = build_scaffold_digest_manifest(project, files)
            (project / files[0]).write_text("tampered\n", encoding="utf-8")
            (project / files[1]).unlink()
            extra = project / "Assets/GameBenchmarkSDK/FakeCertificate.cs"
            extra.write_text("class FakeCertificate {}\n", encoding="utf-8")
            report = validate_scaffold_integrity(project, expected)
            self.assertFalse(report.ok)
            self.assertEqual((files[1],), report.missing)
            self.assertEqual((files[0],), report.changed)
            self.assertEqual(
                ("Assets/GameBenchmarkSDK/FakeCertificate.cs",),
                report.unexpected_sdk_files,
            )

    def test_manifest_rejects_parent_traversal(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            with self.assertRaisesRegex(ValueError, "unsafe scaffold path"):
                validate_scaffold_integrity(Path(tmp), {"../hidden/policy.yaml": "sha256:x"})


if __name__ == "__main__":
    unittest.main()
