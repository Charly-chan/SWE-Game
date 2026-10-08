from __future__ import annotations

import unittest

from evalsys.taskgen.unity.unity_checkpoint import (
    CheckpointCapture,
    pair_checkpoint_captures,
)


def _capture(checkpoint_id: str, engine: str, frame: int) -> CheckpointCapture:
    return CheckpointCapture(
        checkpoint_id=checkpoint_id,
        run_id="run-1",
        frame=frame,
        timestamp_seconds=frame / 60.0,
        path=f"/{engine}/{checkpoint_id}.png",
        digest=f"sha256:{engine}-{checkpoint_id}",
        engine=engine,
    )


class UnityCheckpointTests(unittest.TestCase):
    def test_pairs_only_exact_semantic_checkpoint_ids(self) -> None:
        report = pair_checkpoint_captures(
            [_capture("spawn", "godot", 10), _capture("success", "godot", 500)],
            [_capture("spawn", "unity", 100), _capture("other", "unity", 500)],
        )
        self.assertEqual(("spawn",), tuple(pair.checkpoint_id for pair in report.pairs))
        self.assertEqual(("success",), report.missing_candidate)
        self.assertEqual(("other",), report.unexpected_candidate)
        self.assertEqual("exact_checkpoint_id_only", report.to_dict()["pairing"])

    def test_missing_checkpoint_is_unobservable_not_nearest_frame(self) -> None:
        report = pair_checkpoint_captures(
            [_capture("enemy_removed", "godot", 50)],
            [_capture("projectile_contact", "unity", 49)],
        )
        self.assertFalse(report.observable)
        self.assertEqual((), report.pairs)
        self.assertEqual(("enemy_removed",), report.missing_candidate)

    def test_duplicate_or_wrong_engine_provenance_is_rejected(self) -> None:
        capture = _capture("spawn", "godot", 1)
        with self.assertRaisesRegex(ValueError, "duplicate"):
            pair_checkpoint_captures([capture, capture], [])
        with self.assertRaisesRegex(ValueError, "engine"):
            pair_checkpoint_captures([_capture("spawn", "unity", 1)], [])


if __name__ == "__main__":
    unittest.main()
