

from __future__ import annotations

import os
import json
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from PIL import Image

from evalsys.taskgen.unity.unity_fidelity import CRITERIA, judge_cross_engine_fidelity


def _png(path: Path, colour: tuple[int, int, int]) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    Image.new("RGB", (640, 360), colour).save(path)
    return path


class UnityFidelityTests(unittest.TestCase):
    def test_paired_vlm_receives_labeled_gt_and_candidate_frames(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            video = root / "canonical.mp4"
            video.write_bytes(b"fixture")
            reference = _png(root / "reference_frames" / "reference_000.png", (10, 20, 30))
            candidate = _png(root / "unity_capture" / "frame_000060.png", (20, 30, 40))
            calls: list[dict] = []

            def requester(payload: dict, _key: str, _url: str) -> dict:
                calls.append(payload)
                return {
                    "output": [{
                        "type": "message",
                        "content": [{
                            "type": "output_text",
                            "text": __import__("json").dumps({
                            "criteria": [
                                {
                                    "id": criterion,
                                    "verdict": "partial",
                                    "credit": 0.75,
                                    "rationale": "reference 0 and candidate 0 remain recognizable",
                                    "reference_timestamps": [1.0],
                                    "candidate_timestamps": [1.0],
                                }
                                for criterion in CRITERIA
                            ]
                            })
                        }],
                    }]
                }

            with mock.patch(
                "evalsys.taskgen.unity.unity_fidelity.extract_reference_frames",
                return_value=((str(reference),), (1.0,)),
            ):
                result = judge_cross_engine_fidelity(
                    video,
                    [str(candidate)],
                    out_dir=root / "out",
                    game_id="fixture",
                    task_context="Preserve the stealth chest and exit feedback.",
                    requester=requester,
                    api_key="test-only-key",
                    base_url="https://example.invalid/v1",
                    model="fixture-vlm",
                    provider="responses",
                )
            self.assertEqual("measured", result.status)
            self.assertAlmostEqual(0.75, result.credit or 0.0)
            self.assertEqual(1, len(calls))
            content = calls[0]["input"][0]["content"]
            labels = [item.get("text", "") for item in content if item.get("type") == "input_text"]
            self.assertTrue(any("REFERENCE frame 0" in item for item in labels))
            self.assertTrue(any("CANDIDATE frame 0" in item for item in labels))
            self.assertFalse(calls[0]["store"])
            self.assertEqual("responses", result.wire_api)
            self.assertTrue((root / "out" / "cross_engine_fidelity.json").is_file())

    def test_partial_provider_response_is_incomplete(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            video = root / "canonical.mp4"
            video.write_bytes(b"fixture")
            reference = _png(root / "reference.png", (10, 20, 30))
            candidate = _png(root / "frame_000060.png", (20, 30, 40))
            response = {"output": [{"type": "message", "content": [{
                "type": "output_text", "text": json.dumps({"criteria": [
                    {"id": "asset_identity", "verdict": "pass", "credit": 1.0,
                     "rationale": "matching visible identity", "reference_timestamps": [1.0],
                     "candidate_timestamps": [1.0]},
                ]}),
            }]}]}
            with mock.patch(
                "evalsys.taskgen.unity.unity_fidelity.extract_reference_frames",
                return_value=((str(reference),), (1.0,)),
            ):
                result = judge_cross_engine_fidelity(
                    video, [str(candidate)], out_dir=root / "out", game_id="fixture",
                    requester=lambda *_: response, api_key="test-only-key",
                    base_url="https://example.invalid/v1", model="fixture-vlm",
                    provider="responses",
                )
            self.assertEqual("inconclusive", result.status)
            self.assertIsNone(result.credit)
            self.assertTrue(result.provider_available)
            self.assertEqual(1, sum(item.credit is not None for item in result.criteria))

    def test_missing_key_is_inconclusive_and_does_not_call_provider(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            video = root / "canonical.mp4"
            video.write_bytes(b"fixture")
            reference = _png(root / "reference_frames" / "reference_000.png", (1, 2, 3))
            candidate = _png(root / "unity_capture" / "frame_000060.png", (4, 5, 6))
            requester = mock.Mock(side_effect=AssertionError("provider must not run"))
            with mock.patch(
                "evalsys.taskgen.unity.unity_fidelity.extract_reference_frames",
                return_value=((str(reference),), (1.0,)),
            ):
                result = judge_cross_engine_fidelity(
                    video,
                    [str(candidate)],
                    out_dir=root / "out",
                    game_id="fixture",
                    requester=requester,
                    api_key="",
                    provider="responses",
                )
            self.assertEqual("inconclusive", result.status)
            self.assertFalse(result.provider_available)
            requester.assert_not_called()

    def test_responses_route_uses_matrix_selected_key_env(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            video = root / "canonical.mp4"
            video.write_bytes(b"fixture")
            reference = _png(root / "reference_frames" / "reference_000.png", (1, 2, 3))
            candidate = _png(root / "unity_capture" / "frame_000060.png", (4, 5, 6))
            seen: list[tuple[str, str, str]] = []

            def requester(_payload: dict, key: str, url: str) -> dict:
                seen.append((key, url, os.environ["GAMEBENCH_VLM_MODEL"]))
                return {
                    "output": [{
                        "type": "message",
                        "content": [{
                            "type": "output_text",
                            "text": __import__("json").dumps({
                                "criteria": [
                                    {
                                        "id": criterion,
                                        "verdict": "pass",
                                        "credit": 1.0,
                                        "rationale": "fixture",
                                        "reference_timestamps": [1.0],
                                        "candidate_timestamps": [1.0],
                                    }
                                    for criterion in CRITERIA
                                ]
                            }),
                        }],
                    }]
                }

            selected = {
                "GAMEBENCH_VLM_PROVIDER": "responses",
                "GAMEBENCH_VLM_KEY_ENV": "GB_TEST_VLM_KEY",
                "GAMEBENCH_VLM_BASE_URL": "https://proxy.invalid/v1",
                "GAMEBENCH_VLM_MODEL": "gpt-5.6",
                "GB_TEST_VLM_KEY": "test-secret",
            }
            with (
                mock.patch.dict(os.environ, selected, clear=True),
                mock.patch(
                    "evalsys.taskgen.unity.unity_fidelity.extract_reference_frames",
                    return_value=((str(reference),), (1.0,)),
                ),
            ):
                result = judge_cross_engine_fidelity(
                    video,
                    [str(candidate)],
                    out_dir=root / "out",
                    game_id="fixture",
                    requester=requester,
                )
            self.assertEqual("measured", result.status)
            self.assertEqual(
                [("test-secret", "https://proxy.invalid/v1/responses", "gpt-5.6")],
                seen,
            )

    def test_anthropic_route_uses_matrix_selected_key_env(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            video = root / "canonical.mp4"
            video.write_bytes(b"fixture")
            reference = _png(root / "reference_frames" / "reference_000.png", (1, 2, 3))
            candidate = _png(root / "unity_capture" / "frame_000060.png", (4, 5, 6))
            seen: list[tuple[str, str]] = []

            def requester(_payload: dict, key: str, url: str) -> dict:
                seen.append((key, url))
                return {
                    "content": [{
                        "type": "text",
                        "text": __import__("json").dumps({
                            "criteria": [
                                {
                                    "id": criterion,
                                    "verdict": "pass",
                                    "credit": 1.0,
                                    "rationale": "fixture",
                                    "reference_timestamps": [1.0],
                                    "candidate_timestamps": [1.0],
                                }
                                for criterion in CRITERIA
                            ]
                        }),
                    }]
                }

            selected = {
                "GAMEBENCH_VLM_PROVIDER": "anthropic",
                "GAMEBENCH_VLM_KEY_ENV": "GB_TEST_CLAUDE_VLM_KEY",
                "GAMEBENCH_VLM_BASE_URL": "https://messages-proxy.invalid/v1",
                "GAMEBENCH_VLM_MODEL": "opus",
                "GB_TEST_CLAUDE_VLM_KEY": "test-secret",
            }
            with (
                mock.patch.dict(os.environ, selected, clear=True),
                mock.patch(
                    "evalsys.taskgen.unity.unity_fidelity.extract_reference_frames",
                    return_value=((str(reference),), (1.0,)),
                ),
            ):
                result = judge_cross_engine_fidelity(
                    video,
                    [str(candidate)],
                    out_dir=root / "out",
                    game_id="fixture",
                    requester=requester,
                )
            self.assertEqual("measured", result.status)
            self.assertEqual(
                [("test-secret", "https://messages-proxy.invalid/v1/messages")],
                seen,
            )
            self.assertEqual("opus", result.model)
            self.assertEqual("anthropic", result.wire_api)

    def test_anthropic_route_selects_claude_code_transport(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            video = root / "canonical.mp4"
            video.write_bytes(b"fixture")
            reference = _png(root / "reference_frames" / "reference_000.png", (1, 2, 3))
            candidate = _png(root / "unity_capture" / "frame_000060.png", (4, 5, 6))
            response = {
                "content": [{
                    "type": "text",
                    "text": __import__("json").dumps({
                        "criteria": [
                            {
                                "id": criterion,
                                "verdict": "pass",
                                "credit": 1.0,
                                "rationale": "fixture",
                                "reference_timestamps": [1.0],
                                "candidate_timestamps": [1.0],
                            }
                            for criterion in CRITERIA
                        ]
                    }),
                }]
            }
            selected = {
                "GAMEBENCH_VLM_PROVIDER": "anthropic",
                "GAMEBENCH_VLM_TRANSPORT": "claude_code",
                "GAMEBENCH_VLM_KEY_ENV": "GB_TEST_CLAUDE_VLM_KEY",
                "GAMEBENCH_VLM_BASE_URL": "https://messages-proxy.invalid/v1",
                "GAMEBENCH_VLM_MODEL": "opus",
                "GB_TEST_CLAUDE_VLM_KEY": "test-secret",
            }
            with (
                mock.patch.dict(os.environ, selected, clear=True),
                mock.patch(
                    "evalsys.taskgen.unity.unity_fidelity.extract_reference_frames",
                    return_value=((str(reference),), (1.0,)),
                ),
                mock.patch(
                    "evalsys.taskgen.unity.unity_fidelity.default_claude_code_requester",
                    return_value=response,
                ) as transport,
            ):
                result = judge_cross_engine_fidelity(
                    video,
                    [str(candidate)],
                    out_dir=root / "out",
                    game_id="fixture",
                )
            self.assertEqual("measured", result.status)
            transport.assert_called_once()


if __name__ == "__main__":
    unittest.main()
