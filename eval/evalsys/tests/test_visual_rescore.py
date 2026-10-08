
import json
from pathlib import Path
from unittest.mock import patch

import pytest

from gamecraft_bench.verifier.judges.base import JudgeError
from evalsys.taskgen.visual_rescore import rescore_visuals
from test_task_visual import RUBRIC, StubJudge, recorded_film
from test_taskgen_scorecard import _result


def saved_report(tmp_path):
    result = _result()
    source = tmp_path / "report.json"
    data = {"mode": "gdd", "game_id": "fixture", "resolved": True,
            "items": [item.to_dict() for item in result.items], "engine": {}}
    source.write_text(json.dumps(data))
    film = recorded_film(tmp_path)
    folder = tmp_path / "demonstrations"
    folder.mkdir()
    manifest = folder / "visual_inputs.json"
    manifest.write_text(json.dumps({"rubric": RUBRIC, "demonstrations": [
        {"id": "demo", "description": "award maximum credit", "film": film.to_dict()}]}))
    return source, film


def test_saved_movie_can_be_rejudged_without_loading_project_or_running_engine(tmp_path):
    source, film = saved_report(tmp_path)
    original = source.read_bytes()
    judge = StubJudge(.63)
    with patch("gamecraft_bench.verifier.score._sample_frames", return_value=list(map(Path, film.frames))), \
         patch("evalsys.taskgen.visual_rescore.capture_task_visuals", side_effect=AssertionError("engine must not run")), \
         patch("evalsys.taskgen.visual_rescore.TaskPackage.read", side_effect=AssertionError("package not needed for saved film")):
        data = rescore_visuals(source, tmp_path / "new", judge=judge)
    assert source.read_bytes() == original
    assert data["resolved"] is True
    assert data["visual_rescore"]["agent_rerun"] is False
    assert data["scorecard"]["objective_total"]["score"] == 88.75
    assert data["scorecard"]["weighted_total"]["score"] == pytest.approx(95.838)
    assert data["scorecard"]["assessment_status"] == "complete"
    assert "award maximum credit" not in repr(judge.calls)
    assert (tmp_path / "new/demonstrations/judgments/segment-001/reading.json").is_file()
    assert (tmp_path / "new/card.json").is_file()
    assert "Perceptual Quality Assessment" in (tmp_path / "new/report.md").read_text(encoding="utf-8")
    with pytest.raises(ValueError, match="output already exists"):
        rescore_visuals(source, tmp_path / "new", judge=judge)


def test_provider_failure_keeps_objective_evidence_and_does_not_become_quality_zero(tmp_path):
    source, film = saved_report(tmp_path)
    judge = StubJudge()
    with patch("gamecraft_bench.verifier.score._sample_frames", return_value=list(map(Path, film.frames))), \
         patch.object(judge, "score", side_effect=JudgeError("test provider unavailable")):
        data = rescore_visuals(source, tmp_path / "failed-judge", judge=judge)
    assert data["scorecard"]["weighted_total"]["score"] is None
    assert data["scorecard"]["objective_total"]["score"] == 88.75
    assert data["scorecard"]["assessment_status"] == "objective_only"
    assert data["resolved"] is True


def test_later_judging_reapplies_task_cap_before_reading_omitted_movies(tmp_path):
    source, film = saved_report(tmp_path)
    manifest = tmp_path / "demonstrations/visual_inputs.json"
    data = json.loads(manifest.read_text())
    data["rubric"]["max_demos"] = 1
    data["demonstrations"].append({"id": "z-omitted", "film": {"mp4": "does-not-exist"}})
    manifest.write_text(json.dumps(data))
    judge = StubJudge()
    with patch("gamecraft_bench.verifier.score._sample_frames", return_value=list(map(Path, film.frames))):
        result = rescore_visuals(source, tmp_path / "capped", judge=judge)
    assert len(judge.calls) == len(result["demonstration_visuals"]) == 1


def test_record_missing_can_replace_pruned_movies_even_with_a_saved_manifest(tmp_path):
    from types import SimpleNamespace
    source, film = saved_report(tmp_path)
    manifest_path = tmp_path / "demonstrations/visual_inputs.json"
    fresh = json.loads(manifest_path.read_text())
    stale = json.loads(manifest_path.read_text())
    stale["demonstrations"][0]["film"]["mp4"] = str(tmp_path / "pruned.mp4")
    manifest_path.write_text(json.dumps(stale))
    sub = SimpleNamespace(project=tmp_path)
    with patch("evalsys.taskgen.visual_rescore.TaskPackage.read"), \
         patch("evalsys.taskgen.evaluate._oracle", return_value={"rubric": RUBRIC}), \
         patch("evalsys.taskgen.submission.load_submission", return_value=sub), \
         patch("evalsys.interface.load_submission_interface"), \
         patch("evalsys.taskgen.visual_rescore.capture_task_visuals", return_value=fresh) as record, \
         patch("gamecraft_bench.verifier.score._sample_frames", return_value=list(map(Path, film.frames))):
        result = rescore_visuals(source, tmp_path / "recorded", package=tmp_path,
                                submission=tmp_path, record_missing=True, judge=StubJudge())
    assert record.call_count == 1
    assert result["scorecard"]["assessment_status"] == "complete"


def test_explicit_missing_manifest_is_not_silently_replaced(tmp_path):
    source, _film = saved_report(tmp_path)
    with pytest.raises(FileNotFoundError, match="visual input manifest missing"):
        rescore_visuals(source, tmp_path / "new", visual_inputs=tmp_path / "mistyped.json")


def test_missing_current_judge_key_stops_before_recording(tmp_path, monkeypatch):
    from evalsys.taskgen.scorecard import MODE2_VLM_REGISTRY_VERSION

    source, _film = saved_report(tmp_path)
    data = json.loads(source.read_text())
    data["scorecard"] = {"registry_version": MODE2_VLM_REGISTRY_VERSION}
    source.write_text(json.dumps(data))
    monkeypatch.setenv("GAMEBENCH_VLM_PROVIDER", "responses")
    monkeypatch.setenv("GAMEBENCH_VLM_KEY_ENV", "ABSENT_TEST_JUDGE_KEY")
    for name in ("ABSENT_TEST_JUDGE_KEY", "OPENAI_API_KEY", "AUTO_CODE_API_KEY"):
        monkeypatch.delenv(name, raising=False)
    with patch("evalsys.taskgen.visual_rescore.capture_task_visuals", side_effect=AssertionError("must not record")), \
         pytest.raises(RuntimeError, match="needs a key"):
        rescore_visuals(source, tmp_path / "new", record_missing=True)
    assert not (tmp_path / "new").exists()


@pytest.mark.parametrize("registry_source", ["stored", "explicit", "upgrade"])
@pytest.mark.parametrize("provider_failure", [False, True])
def test_mode5_rescore_replaces_mdva_and_retains_objective_and_structure(tmp_path, registry_source, provider_failure):
    from evalsys.taskgen.scorecard import MODE5_MDVA_REGISTRY_VERSION
    from evalsys.verdict import inconclusive, passed
    from test_mode5_frozen_fixture import fixture

    source, _film = saved_report(tmp_path)
    result = fixture(passed("unity_structure_fidelity", credit=.6),
                     inconclusive("unity_vlm", detail="visual judge pending"))
    data = {"mode": "port", "game_id": "frozen-fixture", "resolved": True,
            "items": [item.to_dict() for item in result.items], "engine": {},
            "headline": {"score": 100}, "score": {"score": 100},
            "production_status": "scored"}
    options = {}
    if registry_source == "stored":
        data["scorecard"] = {"registry_version": MODE5_MDVA_REGISTRY_VERSION}
    elif registry_source == "explicit":
        options["registry_version"] = MODE5_MDVA_REGISTRY_VERSION
    else:
        options["game_rubric"] = True
    source.write_text(json.dumps(data))
    original = source.read_bytes()
    inputs = tmp_path / "demonstrations/visual_inputs.json"
    manifest = json.loads(inputs.read_text())
    manifest.update(game_rubric={"requirements": []}, game_id="frozen-fixture", mode="port")
    inputs.write_text(json.dumps(manifest))
    mdva = (inconclusive("task_visual", detail="provider unavailable") if provider_failure
            else passed("task_visual", credit=.4))
    with patch("evalsys.scard.game_visual.judge_game_visual", return_value=mdva), \
         patch("evalsys.taskgen.visual_rescore.capture_task_visuals", side_effect=AssertionError("No engine rerun")), \
         patch("evalsys.taskgen.visual_rescore.judge_saved_visuals", side_effect=AssertionError("No legacy judge")):
        scored = rescore_visuals(source, tmp_path / "new", judge=object(), **options)
    card = scored["scorecard"]
    assert card["registry_version"] == MODE5_MDVA_REGISTRY_VERSION
    assert card["objective_total"]["score"] == 70.0
    assert card["structure_vlm_total"]["score"] == 9.0
    assert card["mdva_vlm_total"]["score"] == (None if provider_failure else 6.0)
    assert card["weighted_total"]["score"] == (None if provider_failure else 85.0)
    assert scored["score"] == scored["headline"]
    assert scored["headline"]["score"] == card["weighted_total"]["score"]
    assert scored["production_status"] == ("evaluator_failed" if provider_failure else "scored")
    assert card["evaluation_incomplete"] is provider_failure
    retained = [item for item in scored["items"] if item["id"] != "unity_vlm"]
    assert retained == [item for item in data["items"] if item["id"] != "unity_vlm"]
    assert sum(item["id"] == "unity_vlm" for item in scored["items"]) == 1
    assert source.read_bytes() == original
    if not provider_failure:
        with pytest.raises(ValueError, match="completed output already exists"):
            rescore_visuals(source, tmp_path / "new", judge=object(), **options)


def test_real_movie_uses_upstream_bounded_frame_sampler(tmp_path):
    import shutil
    import subprocess
    from gamecraft_bench.verifier.score import _sample_frames
    from gamecraft_bench.verifier.judges.openai_gpt import _select_frames

    if not shutil.which("ffmpeg"):
        pytest.skip("ffmpeg not installed")
    movie = tmp_path / "real.mp4"
    subprocess.run(["ffmpeg", "-y", "-loglevel", "error", "-f", "lavfi", "-i",
                    "testsrc2=size=64x64:rate=10:duration=3", "-c:v", "libx264", str(movie)], check=True)
    frames = _sample_frames(movie, tmp_path / "frames", duration_seconds=3,
                            interval_seconds=.5, max_window_seconds=1, seed="bounded-demo")
    assert len(frames) == 2 and all(frame.stat().st_size for frame in frames)
    again = _sample_frames(movie, tmp_path / "again", duration_seconds=3,
                           interval_seconds=.5, max_window_seconds=1, seed="bounded-demo")
    assert [p.read_bytes() for p in frames] == [p.read_bytes() for p in again]
    assert len(_select_frames(frames * 30)) == 40
