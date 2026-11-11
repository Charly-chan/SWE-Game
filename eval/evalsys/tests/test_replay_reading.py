
from __future__ import annotations

import json
import shutil
import subprocess
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace

from PIL import Image

from evalsys.scard import replay
from evalsys.scard.judge import EVALUATOR_SOURCES, OpenAIResponsesSCardJudge
from evalsys.scard.rubric import RUBRICS, S4
from evalsys.taskgen.evaluate import gdd_mechanics_observable_item, render_report
from evalsys.weights import S_CARD_SUBWEIGHTS


def _png(path: Path, size=(1152, 648), colour=(30, 90, 160)) -> str:
    Image.new("RGB", size, colour).save(path)
    return str(path)


def _film(tmp: Path, frames: int = 4) -> replay.ReplayFilm:
    paths = [_png(tmp / f"replay_t{i * 2000:07d}ms.png", colour=(20 * i, 80, 120)) for i in range(frames)]
    goal = _png(tmp / "replay_goal.png", colour=(200, 200, 40))
    sheet = replay.build_contact_sheet(paths + [goal], [f"#{i}" for i in range(frames)] + ["GOAL"],
                                       tmp / "contact_sheet.png")
    return replay.ReplayFilm(
        directory=str(tmp), frames=paths, frame_times_s=[i * 2.0 for i in range(frames)],
        goal_frame=goal, goal_time_s=7.5, contact_sheet=sheet, fps=60,
        sample_interval_s=2.0, movie_frames=480, duration_s=8.0, resolution="1152x648",
        stop_reason="goal_reached", goal_frame_index=450,
        witness_stop_reason="goal_reached", witness_goal_frame=450,
    )


class ReplayRubricTests(unittest.TestCase):
    def test_reading_is_not_an_s_card_channel_and_s4_stays_human_only(self) -> None:
        self.assertNotIn(replay.REPLAY_READING_ID, RUBRICS)
        self.assertNotIn(replay.REPLAY_READING_ID, S_CARD_SUBWEIGHTS)
        self.assertTrue(S4.human_only)
        doc = replay.replay_rubric_document()
        self.assertEqual(0.0, doc["weight_in_headline"])
        self.assertEqual(
            ["motion_legible", "action_feedback_visible", "progression_visible", "ending_shown"],
            [c["id"] for c in doc["criteria"]],
        )
        for criterion in replay.REPLAY_CRITERIA:
            credits = [b.credit for b in criterion.bands]
            self.assertEqual(sorted(credits, reverse=True), credits)
            self.assertEqual("mostly", criterion.band_for(0.75).label)
            self.assertEqual("absent", criterion.band_for(0.0).label)
        self.assertIn(replay.REPLAY_FRAME_SOURCE, EVALUATOR_SOURCES)

    def test_sample_times_hold_the_interval_until_the_cap_widens_it(self) -> None:
        times, interval = replay.sample_times(20.0)
        self.assertEqual(2.0, interval)
        self.assertEqual([0.0, 2.0, 4.0, 6.0, 8.0, 10.0, 12.0, 14.0, 16.0, 18.0], times)
        times, interval = replay.sample_times(290.5)
        self.assertLessEqual(len(times), replay.MAX_SAMPLED_FRAMES)
        self.assertGreater(interval, 2.0)
        self.assertEqual(0.0, times[0])
        self.assertEqual(([], replay.SAMPLE_INTERVAL_S), replay.sample_times(0.0))


class ReplayJudgeTests(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = Path(tempfile.mkdtemp(prefix="gb_replay_frames_"))

    def tearDown(self) -> None:
        shutil.rmtree(self.tmp, ignore_errors=True)

    def _judge(self, answer: dict | Exception, model: str = "gpt-5.6-sol") -> OpenAIResponsesSCardJudge:
        calls: list[dict] = []

        def requester(payload, api_key, api_url):
            calls.append(payload)
            if isinstance(answer, Exception):
                raise answer
            return {
                "model": model,
                "output": [{"type": "message", "content": [{"type": "output_text",
                                                            "text": json.dumps(answer)}]}],
                "usage": {"input_tokens": 5000, "output_tokens": 500, "total_tokens": 5500},
            }

        judge = OpenAIResponsesSCardJudge(api_key="k", model=model, requester=requester)
        judge.calls = calls
        return judge

    def test_measured_reading_records_bands_cost_and_provenance(self) -> None:
        film = _film(self.tmp)
        answer = {
            "motion_legible": {"credit": 1.0, "rationale": "player visible in every frame", "confident": True},
            "action_feedback_visible": {"credit": 0.75, "rationale": "seed counter changes"},
            "progression_visible": {"credit": 0.5, "rationale": "one zone only"},
            "ending_shown": {"credit": 1.0, "rationale": "results screen at GOAL"},
            "summary": "a readable run with an ending",
        }
        judge = self._judge(answer)
        reading = replay.judge_replay(film, judge, project="fixture/brief")
        self.assertEqual("measured", reading.status)
        self.assertEqual(0.0, reading.weight_in_headline)
        self.assertEqual(replay.REPLAY_RUBRIC_VERSION, reading.rubric_version)
        self.assertAlmostEqual((1.0 + 0.75 + 0.5 + 1.0) / 4, reading.mean_credit, places=4)
        self.assertEqual("partly", reading.criteria["progression_visible"]["band"])
        self.assertEqual("mostly", reading.criteria["action_feedback_visible"]["band"])
        self.assertEqual("a readable run with an ending", reading.detail)
        self.assertIn("5000 input / 500 output tokens", reading.cost_note)
        self.assertEqual("gpt-5.6-sol", reading.judge["model"])
        self.assertEqual(4, reading.judge["images"])
        self.assertEqual(5500, reading.judge["usage"]["total_tokens"])
        self.assertEqual(64, len(reading.prompt_sha256))

        payload = judge.calls[0]
        images = [part for part in payload["input"][0]["content"] if part["type"] == "input_image"]
        self.assertEqual(4, len(images))
        self.assertTrue(all(part["detail"] == "high" for part in images))
        prompt = json.loads(payload["input"][0]["content"][0]["text"])
        self.assertEqual(replay.REPLAY_RUBRIC_VERSION, prompt["rubric_version"])
        self.assertEqual(4, len(prompt["criteria"]))
        self.assertEqual("goal_reached", prompt["film"]["replay_stop_reason"])
        self.assertEqual(film.to_dict(), reading.film)

    def test_provider_failure_and_missing_film_are_statuses_not_numbers(self) -> None:
        film = _film(self.tmp)
        reading = replay.judge_replay(film, self._judge(RuntimeError("HTTP 502")), project="p")
        self.assertEqual("unavailable", reading.status)
        self.assertIsNone(reading.mean_credit)
        self.assertIn("HTTP 502", reading.detail)
        self.assertEqual(2, reading.judge["attempts"])
        empty = replay.ReplayFilm(directory=str(self.tmp), error="Movie Maker wrote no video")
        reading = replay.judge_replay(empty, self._judge({}), project="p")
        self.assertEqual("not_filmed", reading.status)
        self.assertIn("no video", reading.detail)
        no_key = OpenAIResponsesSCardJudge(api_key="", requester=lambda *a: {})
        reading = replay.judge_replay(film, no_key, project="p")
        self.assertEqual("unavailable", reading.status)
        self.assertIn("no API key", reading.detail)
        partial = self._judge({"motion_legible": {"credit": 1.0}, "ending_shown": {"credit": "x"}})
        reading = replay.judge_replay(film, partial, project="p")
        self.assertEqual("partial", reading.status)
        self.assertEqual(1.0, reading.mean_credit)
        self.assertIsNone(reading.criteria["ending_shown"]["credit"])
        self.assertIn("no object", reading.criteria["progression_visible"]["rationale"])

    def test_downscaled_frames_are_refused(self) -> None:
        film = _film(self.tmp)
        film.frames = [_png(self.tmp / "replay_t9999999ms.png", size=(320, 180))]
        reading = replay.judge_replay(film, self._judge({}), project="p")
        self.assertEqual("unavailable", reading.status)
        self.assertIn("provenance", reading.detail)

    def test_write_reading_lands_beside_the_scard_and_in_the_markdown(self) -> None:
        film = _film(self.tmp)
        answer = {c.id: {"credit": 0.75, "rationale": "r"} for c in replay.REPLAY_CRITERIA}
        reading = replay.judge_replay(film, self._judge(answer), project="p")
        package = self.tmp / "package"
        package.mkdir()
        (package / "scard.json").write_text(json.dumps({"score_lo": 0.5, "items": []}))
        out = replay.write_replay_reading(reading, package)
        self.assertTrue(out.is_file())
        scard = json.loads((package / "scard.json").read_text())
        self.assertEqual(0.5, scard["score_lo"])
        self.assertEqual("measured", scard["replay_reading"]["status"])
        self.assertEqual(0.0, scard["replay_reading"]["weight_in_headline"])
        md = replay.replay_reading_markdown(reading.to_dict())
        self.assertIn("S4_replay", md)
        self.assertIn("weight 0", md)
        self.assertIn("| `ending_shown` | 0.75 | mostly |", md)

        result = SimpleNamespace(
            interval=SimpleNamespace(lo=1.0, hi=1.0, coverage=1.0, denominator=1.0),
            comparable=True, resolved=True, eligibility_status="passed",
            package=SimpleNamespace(manifest={"mode": "brief", "game_id": "fixture"}),
            eligibility_items=[], behavior_items=[], fidelity_items=[], limits=(),
            replay_reading=reading.to_dict(),
        )
        from unittest import mock

        card = {"registry_version": "2026-09-04.calib4",
                "weighted_total": {"score": 70.0, "headline_ceiling": 85.0},
                "measured_weight_share": 1.0, "ranking_eligible": True, "categories": [],
                "not_applicable_criteria": ["causal_playability/end_to_end_route"],
                "not_applicable_reasons": {"causal_playability/end_to_end_route": "brief mode: O7 n/a"}}
        with mock.patch("evalsys.taskgen.evaluate.score_task_result", return_value=card):
            report = render_report(result)
        self.assertIn("## Perceptual Quality Assessment — replay evidence (`S4_replay`", report)
        self.assertIn("weight 0, reported separately", report)
        self.assertIn("`causal_playability/end_to_end_route`: brief mode: O7 n/a", report)

    @unittest.skipIf(shutil.which("ffmpeg") is None, "ffmpeg not installed")
    def test_film_from_movie_samples_frames_sheet_and_mp4(self) -> None:
        movie = self.tmp / "raw.avi"
        proc = subprocess.run(
            ["ffmpeg", "-hide_banner", "-loglevel", "error", "-y", "-f", "lavfi",
             "-i", "testsrc=size=1152x648:rate=60:duration=6", "-c:v", "mjpeg", "-q:v", "5", str(movie)],
            capture_output=True, text=True, check=False,
        )
        self.assertEqual(0, proc.returncode, proc.stderr)
        out = self.tmp / "replay_frames"
        film = replay.film_from_movie(
            movie, out, fps=60, goal_frame_index=300, stop_reason="goal_reached",
            witness={"stop_reason": "goal_reached", "goal_frame": 300},
        )
        self.assertTrue(film.ok, film.error)
        self.assertEqual([0.0, 2.0, 4.0], film.frame_times_s)
        self.assertEqual(3, len(film.frames))
        self.assertTrue(film.goal_frame and Path(film.goal_frame).is_file())
        self.assertAlmostEqual(303 / 60, film.goal_time_s, places=2)
        self.assertTrue(Path(film.contact_sheet).is_file())
        self.assertTrue(Path(film.mp4).is_file())
        self.assertEqual("1152x648", film.resolution)
        self.assertFalse(movie.exists(), "the raw AVI is deleted once sampled")
        with Image.open(film.frames[0]) as im:
            self.assertEqual((1152, 648), im.size)


class GddMechanicsObservableTests(unittest.TestCase):
    RUBRIC = {
        "required_groups": ["gb_player", "gb_enemy"],
        "required_numeric_slots": ["health", "score"],
        "mechanic_checks": [
            {"id": "enemy_defeat", "measurable": True,
             "observable": {"kind": "trace_checkpoint", "predicate": "count_delta(gb_enemy) < 0"}},
            {"id": "player_damage", "measurable": True,
             "observable": {"kind": "trace_checkpoint", "predicate": "numeric_delta(health) < 0",
                            "trigger": "overlap(gb_player, gb_enemy)"}},
            {"id": "mood", "measurable": False, "observable": {"kind": "prose"}},
        ],
    }

    def test_fraction_observed_with_untriggered_and_unmeasurable_named(self) -> None:
        item = gdd_mechanics_observable_item(
            self.RUBRIC,
            present_groups=("gb_player", "gb_enemy"),
            numeric_values={"health": {"driver_resolves": True}, "score": {"driver_resolves": False}},
            trace_reading={"observations_reached": ["enemy_defeat"], "observations_triggered": []},
        )
        self.assertEqual("passed", item.verdict.value)
        # 1 mechanic fired + 2 groups + 1 resolving slot = 4 of 6 (player_damage untriggered, score unresolved).
        self.assertAlmostEqual(4 / 6, item.credit, places=6)
        self.assertEqual(["mechanic:player_damage"], item.evidence["untriggered"])
        self.assertEqual(["numeric:score"], item.evidence["missing"])
        self.assertEqual(["mood"], item.evidence["unmeasurable_mechanics"])
        self.assertIn("4/6", item.detail)

        full = gdd_mechanics_observable_item(
            self.RUBRIC, present_groups=("gb_player", "gb_enemy"),
            numeric_values={"health": {"driver_resolves": True}, "score": {"driver_resolves": True}},
            trace_reading={"observations_reached": ["enemy_defeat", "player_damage"],
                           "observations_triggered": ["player_damage"]},
        )
        self.assertEqual(1.0, full.credit)
        none = gdd_mechanics_observable_item(
            self.RUBRIC, present_groups=(), numeric_values={},
            trace_reading={"observations_reached": []},
        )
        self.assertEqual("failed", none.verdict.value)

        hole = gdd_mechanics_observable_item(
            self.RUBRIC, present_groups=("gb_player",), numeric_values={}, trace_reading=None,
        )
        self.assertEqual("inconclusive", hole.verdict.value)
        empty = gdd_mechanics_observable_item({}, present_groups=(), numeric_values={}, trace_reading={})
        self.assertEqual("unobservable", empty.verdict.value)


if __name__ == "__main__":
    unittest.main()
