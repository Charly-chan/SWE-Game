


from __future__ import annotations

import json
import os
import re
import sys
import tempfile
import unittest
from pathlib import Path

from PIL import Image

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from evalsys.render.modes import DEFAULT_RESOLUTION
from evalsys.scard import judge as J
from evalsys.scard import replay
from evalsys.scard import rubric as R
from evalsys.verdict import Verdict
from evalsys.weights import S_CARD_SUBWEIGHTS, S_CARD_VLM_CHANNELS

W, H = DEFAULT_RESOLUTION


def _norm(text: str) -> str:
    return re.sub(r"\s+", " ", text).strip()


def _sections(markdown: str) -> dict[str, str]:

    out: dict[str, str] = {}
    current = None
    for line in markdown.splitlines():
        if line.startswith("## "):
            current = line[3:].strip()
            out[current] = ""
        elif current is not None:
            out[current] += line + "\n"
    return out


def _table_rows(body: str) -> list[list[str]]:
    rows = []
    for line in body.splitlines():
        if not line.startswith("|") or set(line.replace("|", "").strip()) <= {"-", ":", " "}:
            continue
        cells = [c.strip() for c in line.strip().strip("|").split("|")]
        rows.append(cells)
    return rows[1:]


class TestRubricMarkdownMatchesCode(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.md = R.rubric_markdown()
        cls.sections = _sections(cls.md)

    def test_level_scale_table_is_the_code_mapping(self) -> None:
        rows = _table_rows(self.sections["Level scale"])
        table = {int(r[0]): float(r[1]) for r in rows}
        self.assertEqual(table, {level: level / 4 for level in R.LEVELS})
        for level in R.LEVELS:
            self.assertAlmostEqual(R.level_to_credit(level), level / 4)
            self.assertEqual(R.credit_to_level(level / 4), level)
        self.assertEqual(R.credit_to_level(0.6), 2)
        self.assertEqual(R.credit_to_level(0.99), 3)
        self.assertEqual(R.DEFAULT_LEVEL, 2)
        with self.assertRaises(ValueError):
            R.level_to_credit(5)

    def test_channel_tables_match_band_descriptors(self) -> None:
        for cid, rubric in R.RUBRICS.items():
            heading = next(h for h in self.sections if h.startswith(f"{cid} "))
            with self.subTest(channel=cid):
                weight = float(re.search(r"weight ([0-9.]+)", heading).group(1))
                self.assertAlmostEqual(weight, S_CARD_SUBWEIGHTS[cid])
                self.assertIn(rubric.name, heading)
                rows = _table_rows(self.sections[heading])
                self.assertEqual(len(rows), 5)
                for row, band in zip(rows, rubric.bands):
                    self.assertEqual(int(row[0]), band.level)
                    self.assertEqual(row[1], band.label)
                    self.assertEqual(_norm(row[2]), _norm(band.descriptor))

    def test_judging_rules_are_read_verbatim_from_the_markdown(self) -> None:
        rules = R.judging_rules()
        self.assertGreaterEqual(len(rules), 4)
        flat = _norm(self.sections["Judging rules"])
        for rule in rules:
            self.assertIn(_norm(rule), flat)
        self.assertTrue(any("level 2" in r for r in rules))
        self.assertTrue(any("frames_cited" in r for r in rules))
        self.assertTrue(any("reference" in r.lower() for r in rules))

    def test_rubric_document_carries_the_markdown_identity(self) -> None:
        doc = R.rubric_document()
        self.assertEqual(doc["rubric_markdown_sha256"], R.rubric_markdown_sha256())
        self.assertEqual(len(doc["rubric_markdown_sha256"]), 64)
        self.assertEqual(doc["judging_rules"], R.judging_rules())
        self.assertEqual(doc["levels"], {"0": 0.0, "1": 0.25, "2": 0.5, "3": 0.75, "4": 1.0})
        for cid in R.RUBRICS:
            self.assertEqual([b["level"] for b in doc["channels"][cid]["bands"]], [4, 3, 2, 1, 0])


class _Frames:
    def make_frames(self, root: Path, n: int = 2) -> list[J.Frame]:
        out = []
        for i in range(n):
            p = root / "gb_render_out" / f"f{i}.png"
            p.parent.mkdir(parents=True, exist_ok=True)
            Image.new("RGB", (W, H), (40 * i + 20, 90, 120)).save(p)
            out.append(J.load_frame(p, point_id=f"p{i}", level=f"res://level_{i}.tscn"))
        return out


class TestPromptWiring(_Frames, unittest.TestCase):
    def test_prompt_carries_rubric_and_asks_for_levels(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            frames = self.make_frames(Path(td))
            prompt = J.build_prompt(frames, J.JudgeContext(project="p"))
        self.assertEqual(prompt["judging_rules"], R.judging_rules())
        self.assertEqual(prompt["rubric_markdown_sha256"], R.rubric_markdown_sha256())
        self.assertEqual(prompt["rubric_version"], R.RUBRIC_VERSION)
        self.assertEqual(prompt["level_scale"]["default_level"], 2)
        self.assertEqual(set(prompt["channels"]), set(S_CARD_VLM_CHANNELS))
        for cid in S_CARD_VLM_CHANNELS:
            levels = prompt["channels"][cid]["levels"]
            self.assertEqual([l["level"] for l in levels], [4, 3, 2, 1, 0])
            self.assertEqual([l["anchor"] for l in levels],
                             [b.descriptor for b in R.RUBRICS[cid].bands])
        self.assertIn("level", prompt["schema"]["S1"])
        self.assertIn("frames_cited", prompt["schema"]["S1"])
        self.assertNotIn("credit", prompt["schema"]["S1"])
        self.assertNotIn("reference_frames", prompt)

        text = json.dumps(prompt, sort_keys=True)
        self.assertEqual(text, json.dumps(J.build_prompt(frames, J.JudgeContext(project="p")),
                                          sort_keys=True))

    def test_reference_frames_are_listed_and_sent_after_submission_frames(self) -> None:
        calls = []

        def ok(payload, key, url):
            calls.append(payload)
            return {"output": [{"type": "message", "content": [{
                "type": "output_text",
                "text": json.dumps({
                    "S1": {"level": 4, "frames_cited": [0, 1], "rationale": "same as ref", "confident": True},
                    "S2": {"level": 2, "frames_cited": [], "rationale": "HUD empty"},
                    "S3": {"level": None, "rationale": "one level"},
                }),
            }]}]}

        with tempfile.TemporaryDirectory() as td:
            frames = self.make_frames(Path(td))
            ref = self.make_frames(Path(td) / "ref", n=1)
            ctx = J.JudgeContext(project="p", reference_frames=tuple(ref))
            r = J.OpenAIResponsesSCardJudge(api_key="k", requester=ok).judge(frames, ctx)
        prompt = json.loads(calls[0]["input"][0]["content"][0]["text"])
        self.assertEqual(len(prompt["reference_frames"]), 1)
        self.assertIn("images 3-3", prompt["images"])
        images = [b for b in calls[0]["input"][0]["content"] if b["type"] == "input_image"]
        self.assertEqual(len(images), 3)
        self.assertEqual(len(r.context["reference_frames"]), 1)
        self.assertAlmostEqual(r.channels["S1"].credit, 1.0)
        self.assertEqual(r.channels["S1"].evidence["level"], 4)
        self.assertEqual(r.channels["S1"].confidence, "medium")
        self.assertIs(r.channels["S3"].verdict, Verdict.INCONCLUSIVE)


class TestLevelAnswers(unittest.TestCase):
    def test_level_maps_to_band_credit(self) -> None:
        for level in R.LEVELS:
            cj = J._parse_channel("S1", {"level": level, "frames_cited": [0], "rationale": "x"})
            self.assertIs(cj.verdict, Verdict.PASSED)
            self.assertAlmostEqual(cj.credit, level / 4)
            self.assertEqual(cj.evidence["level"], level)
            self.assertEqual(cj.band, R.RUBRICS["S1"].bands[4 - level].label)

    def test_out_of_scale_level_is_inconclusive(self) -> None:
        for bad in (5, -1, "three"):
            cj = J._parse_channel("S2", {"level": bad, "rationale": "x"})
            self.assertIs(cj.verdict, Verdict.INCONCLUSIVE)

    def test_bare_credit_is_still_read_and_marked(self) -> None:
        cj = J._parse_channel("S1", {"credit": 0.6, "rationale": "x", "confident": True})
        self.assertAlmostEqual(cj.credit, 0.6)
        self.assertEqual(cj.evidence["answered_by"], "credit")
        self.assertEqual(cj.evidence["level"], 2)

    def test_no_cited_frame_is_low_confidence(self) -> None:
        cj = J._parse_channel("S1", {"level": 3, "rationale": "x", "confident": True})
        self.assertEqual(cj.confidence, "low")
        cj = J._parse_channel("S1", {"level": 3, "frames_cited": [2], "rationale": "x",
                                     "confident": True})
        self.assertEqual(cj.confidence, "medium")

    def test_null_level_is_declined(self) -> None:
        cj = J._parse_channel("S3", {"level": None, "rationale": "one level only"})
        self.assertIs(cj.verdict, Verdict.INCONCLUSIVE)
        self.assertEqual(cj.rationale, "one level only")


class TestReplayPromptWiring(unittest.TestCase):
    def test_replay_prompt_carries_the_same_rules(self) -> None:
        film = replay.ReplayFilm(directory="/tmp/x", frames=["/tmp/x/a.png"], duration_s=4.0)
        prompt = replay.build_replay_prompt(film, project="p", full_frames=["/tmp/x/a.png"])
        self.assertEqual(prompt["judging_rules"], R.judging_rules())
        self.assertEqual(prompt["rubric_markdown_sha256"], R.rubric_markdown_sha256())
        self.assertEqual(prompt["scard_rubric_version"], R.RUBRIC_VERSION)
        self.assertIn("level", prompt["answer_format"]["<criterion id>"])
        self.assertIn("frames_cited", prompt["answer_format"]["<criterion id>"])


if __name__ == "__main__":
    unittest.main()
