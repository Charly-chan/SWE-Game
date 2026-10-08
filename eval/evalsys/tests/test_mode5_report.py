from copy import deepcopy
from types import SimpleNamespace

import pytest

from evalsys.taskgen.mode5.report import make_scorecard, render_scorecard, summarize_reports
from evalsys.taskgen.mode5.score import REGISTRY_VERSION
from test_mode5_adapter import fixture, runtime_engine
from test_mode5_scoring import record


class Item:
    def __init__(self, data):
        self.data = data

    def to_dict(self):
        return dict(self.data)


def envelope(game="a", **config):
    reading = record(game)
    return {"mode": "port", "game_id": game,
            "scorecard": {"registry_version": REGISTRY_VERSION, "game_id": game, "evidence_score": reading},
            "environment": {"environment_class": "community-docker", "profile_id": "profile",
                            "agent_image_id": "agent", "evaluator_image_id": "evaluator"},
            "run_config": {"model": "test-model", "provider": "test-provider", "harness": "claude-code",
                           "budget": {"agent_seconds": 5400}, "input": {"game_id": game}, **config}}


def test_scorecard_consumes_pre_execution_snapshot(tmp_path):
    snapshot, items = fixture(tmp_path)
    result = SimpleNamespace(package=SimpleNamespace(manifest={"mode": "port", "game_id": "a"}),
                             engine={**runtime_engine(), "mode5_static": snapshot},
                             items=[Item(row) for row in items], resolved=False)
    card = make_scorecard(result)
    assert card["registry_version"] == REGISTRY_VERSION
    assert card["weighted_total"]["score"] is not None
    assert card["official_total"] is None
    assert card["strict"]["resolved"] is False
    assert len(card["categories"]) == 5
    assert sum(row["weight_in_total"] for row in card["categories"]) == 100
    markdown = render_scorecard(card)
    assert "implementation and reference correspondence" in markdown
    assert "Evidence by obligation" in markdown
    assert "coefficient=" in markdown


def test_scorecard_does_not_silently_fallback_to_mutable_source():
    result = SimpleNamespace(package=SimpleNamespace(manifest={"mode": "port"}), engine={})
    with pytest.raises(ValueError, match="snapshot"):
        make_scorecard(result)


def test_summary_uses_all_games_in_one_protocol_group():
    reports = [envelope("a"), envelope("b")]
    summary = summarize_reports(reports, expected_games=["a", "b"])
    assert len(summary["groups"]) == 1
    group = summary["groups"][0]
    assert group["reliability_score"] == 57
    assert group["mean_score"] == 57
    assert group["coverage"] == 1
    assert group["game_count"] == 2


@pytest.mark.parametrize("dimension", ["model", "budget", "harness", "provider", "image"])
def test_summary_does_not_mix_execution_protocols(dimension):
    first, second = envelope("a"), envelope("b")
    if dimension == "image":
        second["environment"]["evaluator_image_id"] = "different-image"
    else:
        second["run_config"][dimension] = {"agent_seconds": 60} if dimension == "budget" else "different"
    summary = summarize_reports([first, second], expected_games=["a", "b"])
    assert len(summary["groups"]) == 2
    assert all(group["reliability_score"] is None for group in summary["groups"])


def test_development_report_not_leaderboard_eligible():
    summary = summarize_reports([envelope("a", development_unscored=True)], expected_games=["a"])
    group = summary["groups"][0]
    assert group["reliability_score"] is None
    assert group["unscored_games"] == ["a"]
    assert group["ranking_scope"] == "mode5-community-docker-v1-development"


@pytest.mark.parametrize("value", [float("nan"), float("inf"), True, 101])
def test_partial_summary_rejects_invalid_scored_values(value):
    report = envelope("a")
    report["scorecard"]["evidence_score"]["total"] = value
    with pytest.raises(ValueError):
        summarize_reports([report], expected_games=["a", "b"])


@pytest.mark.parametrize("bad", ["registry", "environment", "mode", "identity"])
def test_summary_rejects_malformed_envelope(bad):
    report = deepcopy(envelope("a"))
    if bad == "registry":
        report["scorecard"]["registry_version"] = "other"
    elif bad == "environment":
        report["environment"]["environment_class"] = "other"
    elif bad == "mode":
        report["mode"] = "bugfix"
    elif bad == "identity":
        report["scorecard"]["evidence_score"]["game_id"] = "b"
    with pytest.raises(ValueError):
        summarize_reports([report], expected_games=["a"])
