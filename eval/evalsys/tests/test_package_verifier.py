from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path

from evalsys.package_verifier import (
    PackageVerificationError, verify_interface_provenance,
)


class PackageVerifierTests(unittest.TestCase):
    def test_five_artifact_faces_must_share_one_interface_triple(self) -> None:
        triple = {
            "interface_version": 2,
            "source_sha256": "a" * 64,
            "normalized_sha256": "b" * 64,
        }
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            (root / "routes").mkdir()
            (root / "truth_snapshot.json").write_text(
                json.dumps({"interface": triple}), encoding="utf-8"
            )
            (root / "routes" / "readings.json").write_text(
                json.dumps([{"interface": triple}]), encoding="utf-8"
            )
            (root / "capture_manifest.json").write_text(json.dumps(triple), encoding="utf-8")
            (root / "card.json").write_text(
                json.dumps({"provenance": {"interface": triple}}), encoding="utf-8"
            )
            (root / "env.json").write_text(
                json.dumps({"interface": triple}), encoding="utf-8"
            )
            self.assertEqual(triple, verify_interface_provenance(root))

            poisoned = dict(triple, normalized_sha256="0" * 64)
            (root / "capture_manifest.json").write_text(
                json.dumps(poisoned), encoding="utf-8"
            )
            with self.assertRaisesRegex(PackageVerificationError, "mixes interface instances"):
                verify_interface_provenance(root)


if __name__ == "__main__":
    unittest.main()
