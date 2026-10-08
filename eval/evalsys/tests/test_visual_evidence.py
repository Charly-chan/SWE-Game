from __future__ import annotations

import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace

from evalsys.visual_evidence import SCHEMA, build_visual_evidence_manifest


class VisualEvidenceManifestTests(unittest.TestCase):
    def test_candidate_frames_keep_timestamps_without_claiming_video(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            package = Path(tmp)
            capture_dir = package / "captures"
            capture_dir.mkdir()
            frame = capture_dir / "candidate__f0000042.png"
            frame.write_bytes(b"png")
            invocation = SimpleNamespace(
                to_dict=lambda: {
                    "tool": "godot",
                    "argv": ["godot", "--write-movie", "f.png"],
                    "outcome": "reported",
                }
            )
            capture = SimpleNamespace(
                level="res://levels/one.tscn",
                mode="X",
                fixed_fps=30.0,
                frames_captured=120,
                frame_evidence=({
                    "path": str(frame),
                    "source_frame": "f0000042.png",
                    "frame_index": 42,
                    "timestamp_seconds": 1.4,
                },),
                invocation=invocation,
            )

            manifest = build_visual_evidence_manifest(capture, package=package)

            self.assertEqual(SCHEMA, manifest["schema"])
            self.assertFalse(manifest["reference_input"]["is_candidate_evidence"])
            candidate = manifest["candidate_evaluator_capture"]
            self.assertEqual("evaluator", candidate["owner"])
            self.assertEqual("frame_sequence", candidate["capture_kind"])
            self.assertEqual("not_recorded", candidate["video_status"])
            self.assertIsNone(candidate["video_file"])
            self.assertEqual(30.0, candidate["timeline"]["fixed_fps"])
            self.assertEqual("captures/candidate__f0000042.png", candidate["frames"][0]["path"])
            self.assertEqual(42, candidate["frames"][0]["frame_index"])
            self.assertEqual(1.4, candidate["frames"][0]["timestamp_seconds"])
            self.assertEqual("godot", candidate["provenance"]["tool"])

    def test_missing_capture_is_explicitly_not_recorded(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            manifest = build_visual_evidence_manifest(None, package=tmp)
        candidate = manifest["candidate_evaluator_capture"]
        self.assertEqual("none", candidate["capture_kind"])
        self.assertEqual("not_recorded", candidate["video_status"])
        self.assertEqual([], candidate["frames"])


if __name__ == "__main__":
    unittest.main()
