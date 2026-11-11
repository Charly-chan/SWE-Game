


from __future__ import annotations

import json
import os
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

import numpy as np
from PIL import Image

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from evalsys.render.modes import DEFAULT_RESOLUTION
from evalsys.scard import judge as J
from evalsys.scard import scorer as S
from evalsys.scard.rubric import (
    CALIBRATION_FIXTURES,
    OUT_OF_SCOPE,
    RUBRICS,
    HumanRecord,
    OutOfScope,
    assert_in_scope,
    score_s4,
)
from evalsys.verdict import Interval, Verdict

W, H = DEFAULT_RESOLUTION


def textured_frame(seed: int = 0, hue: tuple[int, int, int] = (70, 80, 95)) -> np.ndarray:

    rng = np.random.default_rng(seed)
    img = np.zeros((H, W, 3), dtype=np.uint8)
    img[:, :] = hue
    noise = rng.integers(-28, 28, size=(H, W, 1))
    img = np.clip(img.astype(int) + noise, 0, 255).astype(np.uint8)
    for y in range(0, H, 24):
        img[y:y + 2, :] = np.clip(np.array(hue) + 45, 0, 255).astype(np.uint8)
    for x in range(0, W, 32):
        img[:, x:x + 2] = np.clip(np.array(hue) - 30, 0, 255).astype(np.uint8)
    return img


def flat_frame(hue: tuple[int, int, int] = (70, 80, 95)) -> np.ndarray:

    img = np.zeros((H, W, 3), dtype=np.uint8)
    img[:, :] = hue
    rng = np.random.default_rng(7)
    for cy, cx in ((H // 2, W // 3), (H // 2, 2 * W // 3)):
        img[cy - 40:cy + 40, cx - 40:cx + 40] = rng.integers(
            0, 255, size=(80, 80, 3), dtype=np.uint8
        )
    return img


def clipped_text_frame() -> np.ndarray:


    img = textured_frame(4, (30, 32, 40))
    for k in range(14):
        x = 40 + k * 9
        img[H - 6:H, x:x + 5] = 245
    return img


def write_png(arr: np.ndarray, path: Path) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    Image.fromarray(arr).save(path)
    return path


class StubJudge:


    def __init__(self, scripted: list[dict[str, float | None]], judge_id: str = "stub/v1",
                 judge_kind: str = "local_heuristic") -> None:
        self.scripted = scripted
        self.judge_id = judge_id
        self.judge_kind = judge_kind
        self.model = "stub"
        self.calls = 0

    def judge(self, frames, context) -> J.JudgeResult:
        answers = self.scripted[min(self.calls, len(self.scripted) - 1)]
        self.calls += 1
        channels = {}
        for cid, credit in answers.items():
            if credit is None:
                channels[cid] = J.ChannelJudgement(cid, Verdict.INCONCLUSIVE, None, "scripted")
            else:
                channels[cid] = J.ChannelJudgement(cid, Verdict.PASSED, credit, "scripted")
        channels |= J._s4_placeholder()
        return J.JudgeResult(
            judge_id=self.judge_id, judge_kind=self.judge_kind, model="stub",
            prompt_sha256="0" * 64, frames=list(frames), channels=channels,
        )


class FrameFixtureMixin:


    def make_frames(self, root: Path) -> list[J.Frame]:
        a = write_png(textured_frame(1), root / "gb_render_out" / "p1" / "frame_00040.png")
        b = write_png(textured_frame(2, (60, 95, 80)),
                      root / "gb_render_out" / "p2" / "frame_00040.png")
        return [
            J.load_frame(a, point_id="p1", level="res://level_1.tscn",
                         declared_resolution=DEFAULT_RESOLUTION),
            J.load_frame(b, point_id="p2", level="res://level_2.tscn",
                         declared_resolution=DEFAULT_RESOLUTION),
        ]


class TestProvenance(unittest.TestCase):
    def test_bridge_output_is_rejected(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            p = write_png(textured_frame(), Path(td) / "submission" / "bridge" / "obs_00.png")
            with self.assertRaises(J.ProvenanceError) as ctx:
                J.load_frame(p)
            self.assertIn("bridge", str(ctx.exception))
            self.assertIn("does not fill in its own report card", str(ctx.exception))

    def test_session_output_is_rejected(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            p = write_png(textured_frame(), Path(td) / "gb_session_04" / "shot.png")
            with self.assertRaises(J.ProvenanceError):
                J.load_frame(p)

    def test_unknown_source_label_is_rejected(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            p = write_png(textured_frame(), Path(td) / "gb_render_out" / "frame.png")
            with self.assertRaises(J.ProvenanceError):
                J.load_frame(p, source="submission.self_report")

    def test_evaluator_capture_is_accepted(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            p = write_png(textured_frame(), Path(td) / "gb_render_out" / "p1" / "f.png")
            f = J.load_frame(p, declared_resolution=DEFAULT_RESOLUTION)
            self.assertEqual((f.width, f.height), DEFAULT_RESOLUTION)
            self.assertFalse(f.downscaled)
            self.assertEqual(len(f.sha256), 64)

    def test_judge_refuses_a_frame_from_a_submission_path(self) -> None:

        with tempfile.TemporaryDirectory() as td:
            p = write_png(textured_frame(), Path(td) / "bridge_out" / "f.png")
            smuggled = J.Frame(path=str(p), sha256="x" * 64, width=W, height=H,
                               source="evalsys.render.capture.B2")
            with self.assertRaises(J.ProvenanceError):
                J.LocalHeuristicJudge().judge([smuggled], J.JudgeContext())


class TestNoDownscale(unittest.TestCase):
    def test_resized_frame_is_rejected(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            small = np.asarray(
                Image.fromarray(textured_frame()).resize((320, 180)), dtype=np.uint8
            )
            p = write_png(small, Path(td) / "gb_render_out" / "p1" / "frame_00040.png")
            with self.assertRaises(J.DownscaleError) as ctx:
                J.load_frame(p, declared_resolution=DEFAULT_RESOLUTION)
            self.assertIn("FEWER FRAMES", str(ctx.exception))

    def test_non_strict_load_flags_instead_of_raising(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            small = np.asarray(
                Image.fromarray(textured_frame()).resize((320, 180)), dtype=np.uint8
            )
            p = write_png(small, Path(td) / "gb_render_out" / "p1" / "f.png")
            f = J.load_frame(p, declared_resolution=DEFAULT_RESOLUTION, strict=False)
            self.assertTrue(f.downscaled)
            with self.assertRaises(J.DownscaleError):
                J.assert_deliverable([f])
            with self.assertRaises(J.DownscaleError):
                J.LocalHeuristicJudge().judge([f], J.JudgeContext())

    def test_result_cannot_be_constructed_with_a_downscaled_frame(self) -> None:
        f = J.Frame(path="/tmp/gb_render_out/x.png", sha256="a" * 64, width=320,
                    height=180, source="evalsys.render.capture.B2", downscaled=True)
        with self.assertRaises(J.DownscaleError):
            J.JudgeResult(judge_id="x", judge_kind="vlm", model="m",
                          prompt_sha256="b" * 64, frames=[f], channels={})

    def test_budget_pressure_drops_frames_and_never_shrinks_them(self) -> None:
        frames = [
            J.Frame(path=f"/tmp/gb_render_out/f{i}.png", sha256=f"{i:064d}", width=W,
                    height=H, source="evalsys.render.capture.B2",
                    level=f"res://level_{i % 2}.tscn")
            for i in range(10)
        ]
        kept, dropped = J.select_frames(frames, 4)
        self.assertEqual(len(kept), 4)
        self.assertEqual(len(dropped), 6)
        self.assertTrue(all(f.width == W and f.height == H for f in kept))
        self.assertEqual(len({f.level for f in kept}), 2)


class TestProviderErrors(FrameFixtureMixin, unittest.TestCase):
    def test_provider_exception_is_inconclusive(self) -> None:
        def boom(payload, key, url):
            raise RuntimeError("connection reset by peer")

        with tempfile.TemporaryDirectory() as td:
            frames = self.make_frames(Path(td))
            judge = J.AnthropicSCardJudge(api_key="k", requester=boom)
            r = judge.judge(frames, J.JudgeContext(project="p"))

        self.assertFalse(r.provider_available)
        self.assertIn("connection reset", r.provider_error)
        for cid in ("S1", "S2", "S3"):
            self.assertIs(r.channels[cid].verdict, Verdict.INCONCLUSIVE)
            self.assertIsNone(r.channels[cid].credit)
        self.assertNotIn(Verdict.PASSED, [c.verdict for c in r.channels.values()])

    def test_missing_api_key_is_inconclusive_not_a_pass(self) -> None:
        saved = {k: os.environ.pop(k, None)
                 for k in ("ANTHROPIC_API_KEY", "CLAUDE_API_KEY", "AUTO_CODE_API_KEY")}
        try:
            judge = J.AnthropicSCardJudge.from_env()
            self.assertFalse(judge.available)
            with tempfile.TemporaryDirectory() as td:
                frames = self.make_frames(Path(td))
                r = judge.judge(frames, J.JudgeContext())
            self.assertFalse(r.provider_available)
            self.assertTrue(all(r.channels[c].verdict is Verdict.INCONCLUSIVE
                                for c in ("S1", "S2", "S3")))
        finally:
            for k, v in saved.items():
                if v is not None:
                    os.environ[k] = v

    def test_inconclusive_channels_leave_the_denominator(self) -> None:

        def boom(payload, key, url):
            raise RuntimeError("HTTP 500")

        with tempfile.TemporaryDirectory() as td:
            frames = self.make_frames(Path(td))
            r = J.AnthropicSCardJudge(api_key="k", requester=boom).judge(
                frames, J.JudgeContext())
        card = S.score_scard([r], [], S.O1Gate(True, "runs"))
        self.assertEqual(card.interval.denominator, 0.0)
        self.assertIsNone(card.score_lo)
        self.assertNotEqual(card.score_lo, 0.0)

    def test_a_good_response_is_parsed(self) -> None:
        def ok(payload, key, url):
            return {"content": [{"text": '{"S1": {"credit": 0.5, "rationale": "flat walls",'
                                         ' "confident": true},'
                                         ' "S2": {"credit": 0.9, "rationale": "clean"},'
                                         ' "S3": {"credit": null, "rationale": "one level"}}'}]}

        with tempfile.TemporaryDirectory() as td:
            frames = self.make_frames(Path(td))
            r = J.AnthropicSCardJudge(api_key="k", requester=ok).judge(
                frames, J.JudgeContext(project="p"))
        self.assertTrue(r.provider_available)
        self.assertAlmostEqual(r.channels["S1"].credit, 0.5)
        self.assertIs(r.channels["S3"].verdict, Verdict.INCONCLUSIVE)
        self.assertIs(r.channels["S4"].verdict, Verdict.UNOBSERVABLE)
        self.assertEqual(len(r.prompt_sha256), 64)
        self.assertFalse(r.downscaled)
        self.assertTrue(all(f.sha256 and f.resolution == f"{W}x{H}" for f in r.frames))

    def test_openai_responses_image_payload_and_output_are_parsed(self) -> None:
        calls = []

        def ok(payload, key, url):
            calls.append((payload, key, url))
            return {
                "output": [{
                    "type": "message",
                    "content": [{
                        "type": "output_text",
                        "text": '{"S1":{"credit":0.6,"rationale":"textured"},'
                                '"S2":{"credit":0.8,"rationale":"clear"},'
                                '"S3":{"credit":null,"rationale":"one level"}}',
                    }],
                }]
            }

        with tempfile.TemporaryDirectory() as td:
            frames = self.make_frames(Path(td))
            r = J.OpenAIResponsesSCardJudge(
                api_key="test-key",
                base_url="https://example.invalid/v1",
                model="gpt-5.6",
                requester=ok,
            ).judge(frames, J.JudgeContext(project="p"))
        self.assertTrue(r.provider_available)
        self.assertAlmostEqual(0.6, r.channels["S1"].credit or 0.0)
        payload, key, url = calls[0]
        self.assertEqual("test-key", key)
        self.assertEqual("https://example.invalid/v1/responses", url)
        self.assertFalse(payload["store"])
        blocks = payload["input"][0]["content"]
        self.assertEqual("input_text", blocks[0]["type"])
        self.assertTrue(any(block.get("type") == "input_image" for block in blocks))
        self.assertEqual("responses", r.context["wire_api"])

    def test_responses_requester_passes_verified_context_to_urlopen(self) -> None:
        ssl_context = object()
        response = mock.MagicMock()
        response.__enter__.return_value.read.return_value = b'{"id":"response"}'
        with mock.patch.object(
            J, "_verified_https_context", return_value=ssl_context
        ), mock.patch.object(
            J.urllib.request, "urlopen", return_value=response
        ) as urlopen:
            result = J.default_openai_responses_requester(
                {"input": []}, "test-key", "https://example.invalid/v1/responses"
            )
        self.assertEqual({"id": "response"}, result)
        _, kwargs = urlopen.call_args
        self.assertEqual(180, kwargs["timeout"])
        self.assertIs(ssl_context, kwargs["context"])
        request = urlopen.call_args.args[0]
        self.assertEqual("gamebench-evalsys/1.0", request.get_header("User-agent"))

    def test_verified_context_prefers_ssl_cert_file_then_certifi(self) -> None:
        context = object()
        with mock.patch.dict(
            os.environ, {"SSL_CERT_FILE": "/trusted/ca.pem"}, clear=True
        ), mock.patch.object(
            J.ssl, "create_default_context", return_value=context
        ) as create_context:
            self.assertIs(context, J._verified_https_context())
        create_context.assert_called_once_with(cafile="/trusted/ca.pem")

        fake_certifi = mock.Mock()
        fake_certifi.where.return_value = "/certifi/cacert.pem"
        with mock.patch.dict(os.environ, {}, clear=True), mock.patch.dict(
            sys.modules, {"certifi": fake_certifi}
        ), mock.patch.object(
            J.ssl, "create_default_context", return_value=context
        ) as create_context:
            self.assertIs(context, J._verified_https_context())
        create_context.assert_called_once_with(cafile="/certifi/cacert.pem")

    def test_vlm_factory_defaults_to_responses_but_allows_anthropic(self) -> None:
        with mock.patch.dict(os.environ, {"GAMEBENCH_VLM_PROVIDER": "responses"}, clear=True):
            self.assertIsInstance(
                J.vlm_scard_judge_from_env(), J.OpenAIResponsesSCardJudge
            )
        with mock.patch.dict(os.environ, {"GAMEBENCH_VLM_PROVIDER": "anthropic"}, clear=True):
            self.assertIsInstance(
                J.vlm_scard_judge_from_env(), J.AnthropicSCardJudge
            )

    def test_responses_vlm_uses_selected_key_env_and_route(self) -> None:
        selected = {
            "GAMEBENCH_VLM_PROVIDER": "responses",
            "GAMEBENCH_VLM_KEY_ENV": "GB_TEST_VLM_KEY",
            "GAMEBENCH_VLM_BASE_URL": "https://proxy.invalid/v1",
            "GAMEBENCH_VLM_MODEL": "gpt-5.6",
            "GB_TEST_VLM_KEY": "test-secret",
        }
        with mock.patch.dict(os.environ, selected, clear=True):
            judge = J.vlm_scard_judge_from_env()
        self.assertIsInstance(judge, J.OpenAIResponsesSCardJudge)
        self.assertEqual("test-secret", judge.api_key)
        self.assertEqual("https://proxy.invalid/v1", judge.base_url)
        self.assertEqual("gpt-5.6", judge.model)

    def test_anthropic_vlm_uses_selected_key_env_and_route(self) -> None:
        selected = {
            "GAMEBENCH_VLM_PROVIDER": "anthropic",
            "GAMEBENCH_VLM_KEY_ENV": "GB_TEST_CLAUDE_VLM_KEY",
            "GAMEBENCH_VLM_BASE_URL": "https://messages-proxy.invalid/v1",
            "GAMEBENCH_VLM_MODEL": "opus",
            "GB_TEST_CLAUDE_VLM_KEY": "test-secret",
        }
        with mock.patch.dict(os.environ, selected, clear=True):
            judge = J.vlm_scard_judge_from_env()
        self.assertIsInstance(judge, J.AnthropicSCardJudge)
        self.assertEqual("test-secret", judge.api_key)
        self.assertEqual("https://messages-proxy.invalid/v1", judge.base_url)
        self.assertEqual("opus", judge.model)

    def test_claude_code_transport_reads_only_materialized_evaluator_frames(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            fake = root / "claude"
            capture = root / "capture.json"
            fake.write_text(
                "#!/usr/bin/env python3\n"
                "import json, os, sys\n"
                "from pathlib import Path\n"
                "frames=sorted(p.name for p in Path.cwd().glob('frame_*.png'))\n"
                "Path(os.environ['GB_CLAUDE_VLM_CAPTURE']).write_text(json.dumps({"
                "'argv':sys.argv[1:], 'stdin':sys.stdin.read(), 'frames':frames}))\n"
                "print(json.dumps({'result':'{\"S1\":{\"credit\":0.75,\"rationale\":\"surface\"},'"
                "'\"S2\":{\"credit\":0.5,\"rationale\":\"ui\"},'"
                "'\"S3\":{\"credit\":null,\"rationale\":\"one level\"}}'}))\n",
                encoding="utf-8",
            )
            fake.chmod(0o755)
            selected = {
                "GAMEBENCH_VLM_PROVIDER": "anthropic",
                "GAMEBENCH_VLM_TRANSPORT": "claude_code",
                "GAMEBENCH_VLM_KEY_ENV": "GB_TEST_CLAUDE_VLM_KEY",
                "GAMEBENCH_VLM_BASE_URL": "https://messages-proxy.invalid/v1",
                "GAMEBENCH_VLM_MODEL": "opus",
                "GAMEBENCH_CLAUDE_CODE_BIN": str(fake),
                "GB_TEST_CLAUDE_VLM_KEY": "test-secret",
                "GB_CLAUDE_VLM_CAPTURE": str(capture),
            }
            frames = self.make_frames(root / "evaluator_captures")
            with mock.patch.dict(os.environ, selected, clear=True):
                result = J.vlm_scard_judge_from_env().judge(
                    frames, J.JudgeContext(project="fixture")
                )
            self.assertTrue(result.provider_available)
            self.assertAlmostEqual(0.75, result.channels["S1"].credit or 0.0)
            recorded = json.loads(capture.read_text())
            self.assertEqual(len(frames), len(recorded["frames"]))
            self.assertIn("Use the Read tool", recorded["stdin"])
            self.assertIn("Read", recorded["argv"])
            self.assertNotIn("Write", recorded["argv"])

    def test_a_vlm_may_not_score_s4(self) -> None:
        with self.assertRaises(ValueError):
            J.JudgeResult(
                judge_id="x", judge_kind="vlm", model="m", prompt_sha256="c" * 64,
                frames=[], channels={"S4": J.ChannelJudgement(
                    "S4", Verdict.PASSED, 0.8, "felt good to me")},
            )

    def test_judge_kind_must_be_declared(self) -> None:
        with self.assertRaises(ValueError):
            J.JudgeResult(judge_id="x", judge_kind="magic", model="m",
                          prompt_sha256="d" * 64, frames=[], channels={})


class TestLocalHeuristic(FrameFixtureMixin, unittest.TestCase):
    def test_it_never_calls_itself_a_vlm(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            frames = self.make_frames(Path(td))
            r = J.LocalHeuristicJudge().judge(frames, J.JudgeContext(project="p"))
            self.assertEqual(r.judge_kind, "local_heuristic")
            self.assertIn("local_heuristic", r.judge_id)
            self.assertNotIn("vlm", r.judge_id.lower())
            path = J.write_judge_report(r, Path(td) / "out")
            self.assertIn("local_heuristic", path.name)
            self.assertIn("local_heuristic", path.read_text(encoding="utf-8"))

    def test_flat_surfaces_score_below_textured_ones_on_s1(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            root = Path(td) / "gb_render_out"
            good = J.load_frame(write_png(textured_frame(3), root / "g" / "f.png"),
                                level="res://l1.tscn",
                                declared_resolution=DEFAULT_RESOLUTION)
            bad = J.load_frame(write_png(flat_frame(), root / "b" / "f.png"),
                               level="res://l1.tscn",
                               declared_resolution=DEFAULT_RESOLUTION)
            jd = J.LocalHeuristicJudge()
            g = jd.judge([good], J.JudgeContext())
            b = jd.judge([bad], J.JudgeContext())
        self.assertGreater(g.channels["S1"].credit, b.channels["S1"].credit)
        self.assertLessEqual(b.channels["S1"].credit, 0.25)

    def test_text_clipped_by_the_frame_edge_lowers_s2(self) -> None:


        with tempfile.TemporaryDirectory() as td:
            root = Path(td) / "gb_render_out"
            clean = J.load_frame(write_png(textured_frame(4, (30, 32, 40)),
                                           root / "clean" / "f.png"),
                                 level="l1", declared_resolution=DEFAULT_RESOLUTION)
            clipped = J.load_frame(write_png(clipped_text_frame(), root / "clip" / "f.png"),
                                   level="l1", declared_resolution=DEFAULT_RESOLUTION)
            jd = J.LocalHeuristicJudge()
            a = jd.judge([clean], J.JudgeContext())
            b = jd.judge([clipped], J.JudgeContext())
        self.assertGreater(
            b.channels["S2"].evidence["mean_clipped_components"],
            a.channels["S2"].evidence["mean_clipped_components"],
        )
        self.assertLess(b.channels["S2"].credit, a.channels["S2"].credit)

    def test_it_is_deterministic(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            frames = self.make_frames(Path(td))
            report = J.judge_n_times(J.LocalHeuristicJudge(), frames, J.JudgeContext(), n=3)
        self.assertTrue(report.deterministic)
        self.assertEqual(report.per_channel["S1"].range, 0.0)
        self.assertEqual(report.unstable_channels, [])

    def test_null_control_pair_shows_no_difference(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            frames = self.make_frames(Path(td))
            pair = J.judge_pair(J.LocalHeuristicJudge(), frames, frames,
                                J.JudgeContext(), expect="no_difference", label="JC-A0")
        self.assertFalse(pair.any_difference)
        self.assertTrue(pair.meets_expectation)

    def test_one_level_makes_s3_inconclusive(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            f = J.load_frame(
                write_png(textured_frame(), Path(td) / "gb_render_out" / "f.png"),
                level="res://only.tscn", declared_resolution=DEFAULT_RESOLUTION)
            r = J.LocalHeuristicJudge().judge([f], J.JudgeContext())
        self.assertIs(r.channels["S3"].verdict, Verdict.INCONCLUSIVE)

    def test_empty_frame_set_is_not_a_clean_bill(self) -> None:
        with self.assertRaises(ValueError):
            J.LocalHeuristicJudge().judge([], J.JudgeContext())


class TestS4(unittest.TestCase):
    def test_no_human_record_is_unmeasurable(self) -> None:
        item = score_s4([])
        self.assertIs(item.verdict, Verdict.UNMEASURABLE)
        self.assertEqual(item.credit, 0.0)
        self.assertFalse(item.verdict.in_denominator)
        self.assertIn("human-scored only", item.detail)

    def test_a_human_record_scores_it(self) -> None:
        item = score_s4([HumanRecord("S4", 0.6, rater_id="rater-a", session_minutes=12)])
        self.assertIs(item.verdict, Verdict.PASSED)
        self.assertAlmostEqual(item.credit, 0.6)

    def test_an_anonymous_record_is_not_a_record(self) -> None:
        with self.assertRaises(ValueError):
            HumanRecord("S4", 0.6, rater_id="")

    def test_card_without_human_record_reports_s4_unmeasurable(self) -> None:
        stub = StubJudge([{"S1": 0.8, "S2": 0.8, "S3": 0.8}])
        r = stub.judge([], J.JudgeContext())
        card = S.score_scard([r], [], S.O1Gate(True, "runs"))
        self.assertEqual(card.per_channel["S4"]["verdict"], "unmeasurable")
        self.assertIsNone(card.per_channel["S4"]["credit"] or None)
        self.assertAlmostEqual(card.interval.by_verdict["unmeasurable"], 0.15)

        self.assertAlmostEqual(card.interval.denominator, 0.85)

        self.assertAlmostEqual(card.interval.coverage, 1.0)
        self.assertAlmostEqual(card.measured_weight_share, 0.85)


class TestO1Gate(unittest.TestCase):
    def test_a_game_that_does_not_run_gets_na_not_zero(self) -> None:
        stub = StubJudge([{"S1": 0.9, "S2": 0.9, "S3": 0.9}])
        r = stub.judge([], J.JudgeContext())
        dead = Interval(lo=0.25, hi=0.25, coverage=1.0, denominator=1.0,
                        by_verdict={"passed": 0.25, "failed": 0.75})
        card = S.score_scard([r], [], dead, project="broken")
        self.assertFalse(card.applicable)
        self.assertIsNone(card.score_lo)
        self.assertEqual(card.weight_in_total, 0.0)
        self.assertEqual(card.interval.denominator, 0.0)
        self.assertIn("not scored 0", " ".join(card.notes))

    def test_na_card_leaves_the_weighting_rather_than_dragging_the_total(self) -> None:
        stub = StubJudge([{"S1": 0.9, "S2": 0.9, "S3": 0.9}])
        good = S.score_scard([stub.judge([], J.JudgeContext())], [],
                             S.O1Gate(True, "runs"), project="alive")
        dead_card = S.score_scard(
            [stub.judge([], J.JudgeContext())], [],
            Interval(0.0, 0.0, 1.0, 1.0, {"failed": 1.0}), project="dead")
        table = S.score_totals({"alive": 0.60, "dead": 0.60},
                               {"alive": good, "dead": dead_card})
        self.assertEqual(table.combined["dead"], 0.60)
        self.assertEqual(table.scard_weight_used["dead"], 0.0)
        self.assertGreater(table.combined["alive"], 0.0)
        self.assertIn("not an S-card of 0", " ".join(table.notes))

    def test_unmeasured_o1_does_not_open_the_gate(self) -> None:
        empty = Interval(0.0, 0.0, 0.0, 0.0, {})
        self.assertFalse(S.gate_from_interval(empty).passed)
        self.assertFalse(S._as_gate(None).passed)

    def test_gate_needs_the_draws_nontrivial_rung(self) -> None:
        booted_black = Interval(0.5, 0.5, 1.0, 1.0, {"passed": 0.5, "failed": 0.5})
        self.assertFalse(S.gate_from_interval(booted_black).passed)
        drew = Interval(0.75, 0.75, 1.0, 1.0, {"passed": 0.75, "skipped": 0.25})
        self.assertTrue(S.gate_from_interval(drew).passed)


class TestRetest(unittest.TestCase):
    def test_five_disagreeing_runs_report_their_spread(self) -> None:
        stub = StubJudge([
            {"S1": 0.20, "S2": 0.80, "S3": 0.50},
            {"S1": 0.90, "S2": 0.80, "S3": 0.50},
            {"S1": 0.45, "S2": 0.80, "S3": 0.50},
            {"S1": 0.75, "S2": 0.80, "S3": 0.50},
            {"S1": 0.10, "S2": 0.80, "S3": 0.50},
        ])
        report = J.judge_n_times(stub, [], J.JudgeContext(), n=5)
        s1 = report.per_channel["S1"]
        self.assertEqual(s1.n_scored, 5)
        self.assertAlmostEqual(s1.range, 0.80)
        self.assertGreater(s1.stdev, 0.2)
        self.assertGreater(len(s1.distinct_bands), 1)
        self.assertFalse(s1.stable)
        self.assertFalse(report.deterministic)
        self.assertIn("S1", report.unstable_channels)
        self.assertNotIn("S2", report.unstable_channels)

        self.assertIn("range", report.to_dict()["per_channel"]["S1"])

    def test_an_unstable_channel_is_not_averaged_into_the_score(self) -> None:
        stub = StubJudge([
            {"S1": 0.10, "S2": 0.80, "S3": 0.50},
            {"S1": 0.90, "S2": 0.80, "S3": 0.50},
            {"S1": 0.30, "S2": 0.80, "S3": 0.50},
            {"S1": 0.70, "S2": 0.80, "S3": 0.50},
            {"S1": 0.50, "S2": 0.80, "S3": 0.50},
        ])
        report = J.judge_n_times(stub, [], J.JudgeContext(), n=5)
        card = S.score_scard(report, [], S.O1Gate(True, "runs"), project="noisy")
        self.assertEqual(card.per_channel["S1"]["verdict"], "inconclusive")
        self.assertEqual(card.per_channel["S2"]["verdict"], "passed")
        self.assertIn("S1", card.retest_spread)
        self.assertAlmostEqual(card.retest_spread["S1"]["range"], 0.80)

        self.assertAlmostEqual(card.interval.denominator, 0.45)

    def test_report_only_policy_keeps_the_number_but_still_reports_spread(self) -> None:
        stub = StubJudge([{"S1": 0.10, "S2": 0.8, "S3": 0.5},
                          {"S1": 0.90, "S2": 0.8, "S3": 0.5}])
        report = J.judge_n_times(stub, [], J.JudgeContext(), n=2)
        card = S.score_scard(report, [], S.O1Gate(True, "runs"),
                             unstable_policy="report_only")
        self.assertEqual(card.per_channel["S1"]["verdict"], "passed")
        self.assertAlmostEqual(card.retest_spread["S1"]["range"], 0.80)


class TestCalibration(FrameFixtureMixin, unittest.TestCase):
    def test_uncalibrated_card_cannot_enter_a_main_table(self) -> None:
        stub = StubJudge([{"S1": 0.9, "S2": 0.9, "S3": 0.9}])
        card = S.score_scard([stub.judge([], J.JudgeContext())], [],
                             S.O1Gate(True, "runs"), project="p")
        self.assertIs(card.calibration_state, S.CalibrationState.UNCALIBRATED)
        self.assertFalse(card.main_table_eligible)
        with self.assertRaises(S.UncalibratedError):
            S.main_table([card])

    def test_jc_a1_is_the_existence_test_for_s1(self) -> None:

        with tempfile.TemporaryDirectory() as td:
            root = Path(td) / "gb_render_out"
            good = [J.load_frame(write_png(textured_frame(5), root / "a" / "f.png"),
                                 level="l1", declared_resolution=DEFAULT_RESOLUTION)]
            defect = [J.load_frame(write_png(flat_frame(), root / "b" / "f.png"),
                                   level="l1", declared_resolution=DEFAULT_RESOLUTION)]

            class BlindJudge(StubJudge):
                def judge(self, frames, context):
                    return StubJudge.judge(self, frames, context)

            blind = BlindJudge([{"S1": 0.9, "S2": 0.9, "S3": 0.9}])
            rep = J.calibrate(blind, {"JC-A1": (good, defect)}, J.JudgeContext())
            self.assertFalse(rep.outcomes[0].passed)
            self.assertFalse(rep.channel_passed("S1"))

            seeing = J.LocalHeuristicJudge()
            rep2 = J.calibrate(
                seeing,
                {"JC-A1": (good, defect), "JC-A0": (good, good)},
                J.JudgeContext(),
            )
            by_id = {o.fixture_id: o for o in rep2.outcomes}
            self.assertTrue(by_id["JC-A1"].passed)
            self.assertTrue(by_id["JC-A0"].passed)
            self.assertTrue(rep2.channel_passed("S1"))

    def test_a_judge_that_cries_wolf_fails_the_null_control(self) -> None:
        class AlwaysWorse(StubJudge):
            def judge(self, frames, context):
                self.scripted = [{"S1": 0.9, "S2": 0.9, "S3": 0.9},
                                 {"S1": 0.1, "S2": 0.1, "S3": 0.1}]
                return StubJudge.judge(self, frames, context)

        judge = AlwaysWorse([{"S1": 0.9, "S2": 0.9, "S3": 0.9}])
        rep = J.calibrate(judge, {"JC-A0": ([], [])}, J.JudgeContext())
        self.assertFalse(rep.outcomes[0].passed)
        self.assertIn("unattributable", rep.outcomes[0].detail)


class TestSpearman(unittest.TestCase):
    def test_identical_orderings(self) -> None:
        a = [0.1, 0.4, 0.6, 0.9]
        self.assertAlmostEqual(S.spearman(a, [2 * v + 1 for v in a]), 1.0)

    def test_reversed_orderings(self) -> None:
        a = [0.1, 0.4, 0.6, 0.9]
        self.assertAlmostEqual(S.spearman(a, list(reversed(a))), -1.0)

    def test_ties_use_average_ranks(self) -> None:
        self.assertAlmostEqual(S.spearman([1, 1, 2, 3], [1, 1, 2, 3]), 1.0)

    def test_a_constant_vector_is_not_an_ordering(self) -> None:
        self.assertTrue(np.isnan(S.spearman([1, 1, 1], [1, 2, 3])))

    def test_score_totals_names_who_moved(self) -> None:
        stub_hi = StubJudge([{"S1": 1.0, "S2": 1.0, "S3": 1.0}])
        stub_lo = StubJudge([{"S1": 0.0, "S2": 0.0, "S3": 0.0}])
        good = S.score_scard([stub_hi.judge([], J.JudgeContext())], [],
                             S.O1Gate(True, "runs"), project="b")
        bad = S.score_scard([stub_lo.judge([], J.JudgeContext())], [],
                            S.O1Gate(True, "runs"), project="a")
        table = S.score_totals({"a": 0.500, "b": 0.495}, {"a": bad, "b": good})
        self.assertTrue(table.ranking_changed)
        self.assertEqual(table.combined_order[0], "b")
        self.assertEqual(table.objective_order[0], "a")
        moved = {m.project: m.delta for m in table.moves}
        self.assertEqual(moved, {"a": -1, "b": 1})
        self.assertIn("the S-card changed the ranking", " ".join(table.notes))
        self.assertAlmostEqual(table.spearman, -1.0)

    def test_objective_only_is_always_emitted_beside_the_combined(self) -> None:
        stub = StubJudge([{"S1": 0.5, "S2": 0.5, "S3": 0.5}])
        card = S.score_scard([stub.judge([], J.JudgeContext())], [],
                             S.O1Gate(True, "runs"), project="a")
        table = S.score_totals({"a": 0.8, "b": 0.4, "c": 0.6}, {"a": card})
        d = table.to_dict()
        self.assertEqual(d["objective_only"]["a"], 0.8)
        self.assertAlmostEqual(d["combined"]["a"], 0.9 * 0.8 + 0.1 * 0.5)
        self.assertEqual(d["combined"]["b"], 0.4)


def subjects_with(dim_values: dict[str, float], weights: dict[str, float],
                  base: dict[str, dict[str, float]]) -> list[S.Subject]:
    return [
        S.Subject(project=p, dimensions=dict(dims, extra=dim_values[p]), weights=weights)
        for p, dims in base.items()
    ]


class TestDart(unittest.TestCase):
    def make(self, extra: dict[str, float], extra_weight: float) -> list[S.Subject]:
        base = {
            "p1": {"o": 0.90},
            "p2": {"o": 0.70},
            "p3": {"o": 0.50},
            "p4": {"o": 0.30},
            "p5": {"o": 0.10},
        }
        weights = {"o": 0.90, "extra": extra_weight}
        return subjects_with(extra, weights, base)

    def test_a_dimension_that_does_not_move_the_ranking_is_inert(self) -> None:
        subjects = self.make({p: 0.5 for p in ("p1", "p2", "p3", "p4", "p5")}, 0.10)
        finding = S.dart_zero(subjects, "extra")
        self.assertTrue(finding.inert)
        self.assertFalse(finding.dominant)
        self.assertEqual(finding.moves, [])
        self.assertAlmostEqual(finding.spearman_vs_baseline, 1.0)
        self.assertIn("carries no ranking information", " ".join(finding.notes))
        self.assertAlmostEqual(finding.weight_share, 0.1)

        perm = S.dart_permute(subjects, "extra", n=200, seed=3)
        self.assertTrue(perm.inert)
        self.assertAlmostEqual(perm.permutation_p5, 1.0)

    def test_a_dimension_that_determines_the_ranking_is_dominant(self) -> None:
        subjects = self.make(
            {"p1": 0.0, "p2": 0.25, "p3": 0.5, "p4": 0.75, "p5": 1.0}, 8.0)
        finding = S.dart_zero(subjects, "extra")
        self.assertTrue(finding.dominant)
        self.assertFalse(finding.inert)
        self.assertGreater(len(finding.moves), 0)
        self.assertIn("decorative", " ".join(finding.notes))

        perm = S.dart_permute(subjects, "extra", n=200, seed=3)
        self.assertTrue(perm.dominant)
        self.assertLess(perm.permutation_p5, 0.0)

    def test_findings_are_structured_not_a_verdict_string(self) -> None:
        subjects = self.make({p: 0.5 for p in ("p1", "p2", "p3", "p4", "p5")}, 0.10)
        d = S.dart_zero(subjects, "extra").to_dict()
        for key in ("inert", "dominant", "weight_share", "spearman_vs_baseline",
                    "baseline_order", "variant_order", "moves", "notes"):
            self.assertIn(key, d)
        self.assertNotIn("verdict", d)

    def test_dart_refuses_a_sample_too_small_to_rank(self) -> None:
        subjects = self.make({p: 0.5 for p in ("p1", "p2", "p3", "p4", "p5")}, 0.1)[:2]
        with self.assertRaises(ValueError):
            S.dart_zero(subjects, "extra")


class TestRubric(unittest.TestCase):
    def test_the_three_exclusions_are_enforced_in_code(self) -> None:
        self.assertEqual(
            set(OUT_OF_SCOPE),
            {"resemblance_to_reference", "fun", "duplicate_of_objective_channel"},
        )
        for excluded in OUT_OF_SCOPE:
            with self.assertRaises(OutOfScope):
                assert_in_scope(excluded)
        for cid in RUBRICS:
            assert_in_scope(cid)
        with self.assertRaises(KeyError):
            assert_in_scope("S9")

    def test_s1_docstring_states_the_evidence_base_honestly(self) -> None:
        text = RUBRICS["S1"].rationale
        self.assertIn("FOUND BY A HUMAN, NOT BY A VLM", text)
        self.assertIn("JC-A1", text)
        self.assertIn("REMOVED", text)
        self.assertIn("72 texture files", text)

    def test_every_channel_has_discriminating_bands(self) -> None:
        for cid, r in RUBRICS.items():
            self.assertGreaterEqual(len(r.bands), 4, cid)
            self.assertEqual(len({b.descriptor for b in r.bands}), len(r.bands), cid)
            for b in r.bands:
                self.assertGreater(len(b.descriptor), 60, f"{cid}/{b.label}")

    def test_calibration_fixtures_include_a_null_control(self) -> None:
        kinds = {f.kind for f in CALIBRATION_FIXTURES.values()}
        self.assertIn("null_control", kinds)
        self.assertIn("JC-A1", RUBRICS["S1"].calibration_fixtures)
        self.assertIn("JC-A0", RUBRICS["S1"].calibration_fixtures)


if __name__ == "__main__":
    unittest.main()
