

from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from evalsys.taskgen.unity.unity_antigrant import scan_unity_antigrant


class UnityAntiGrantTests(unittest.TestCase):
    @staticmethod
    def _scan(source: str, *, relative: str = "Assets/Game/Gameplay.cs"):
        temporary = tempfile.TemporaryDirectory()
        root = Path(temporary.name)
        path = root / relative
        path.parent.mkdir(parents=True)
        path.write_text(source, encoding="utf-8")
        return temporary, scan_unity_antigrant(root)

    def test_unconditional_lifecycle_success_blocks(self) -> None:
        temporary, report = self._scan(
            "class Gameplay { void Start() { GBOutcome.ReportSuccess(); } }"
        )
        with temporary:
            self.assertFalse(report.ok)
            self.assertIn("unconditional_lifecycle_success", {f.code for f in report.findings})

    def test_ambiguous_lifecycle_control_flow_is_informational(self) -> None:
        temporary, report = self._scan(
            "class Gameplay { void Start() { if (WonNormally()) GBOutcome.ReportSuccess(); } }"
        )
        with temporary:
            self.assertTrue(report.ok)
            finding = next(f for f in report.findings if f.code == "ambiguous_lifecycle_success")
            self.assertFalse(finding.blocking)

    def test_explicit_evaluator_environment_grant_blocks(self) -> None:
        temporary, report = self._scan(
            'class Gameplay { void Tick() { if (Environment.GetEnvironmentVariable('
            '"GB_EVALUATOR_RUN") != null) GBOutcome.ReportSuccess(); } }'
        )
        with temporary:
            self.assertFalse(report.ok)
            self.assertIn("evaluator_aware_success", {f.code for f in report.findings})

    def test_evaluator_words_in_comments_do_not_block(self) -> None:
        temporary, report = self._scan(
            "class Gameplay { void Win() { // GB_EVALUATOR_RUN\n GBOutcome.ReportSuccess(); } }"
        )
        with temporary:
            self.assertTrue(report.ok)

    def test_candidate_evaluator_path_and_certificate_block(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            script = root / "Assets/GameBenchmarkEvaluator/Fake.cs"
            script.parent.mkdir(parents=True)
            script.write_text("class Fake {}", encoding="utf-8")
            certificate = root / "Assets/Game/evaluator_certificate.json"
            certificate.parent.mkdir(parents=True)
            certificate.write_text("{}", encoding="utf-8")
            report = scan_unity_antigrant(root)
            self.assertFalse(report.ok)
            self.assertEqual(
                {"candidate_evaluator_override", "bundled_evaluator_artifact"},
                {finding.code for finding in report.findings},
            )


if __name__ == "__main__":
    unittest.main()
