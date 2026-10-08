
from copy import deepcopy

import pytest

from evalsys.taskgen.evaluate import (
    _diff_item, _null_item, _repair_differential_item,
    repair_restoration_item, repair_restoration_graded_item,
)
from evalsys.taskgen.scorecard import score_task_result
from evalsys.verdict import Attribution, Verdict, inconclusive
from test_taskgen_mode34b import _case
from test_taskgen_scorecard import _fixture


def _replace_item(result, item):
    result.items = [row for row in result.items if row.id != item.id] + [item]


def broken_import_result(*, cold_status="failed", prep_stage="import", stop="launch_failed"):
    result = _fixture("bugfix_revert_shadow_walker")
    result.resolved = False
    reproduction = next(i for i in result.items if i.id == "reproduction")
    channel = next(c for c in reproduction.evidence["card"]["channels"] if c["channel"] == "O1")
    channel.update(measured=True, lo=0, hi=0, coverage=1, denominator=1,
                   by_verdict={cold_status: 1}, note=f"cold_import={cold_status}")
    prep = {
        "scratch": {"ok": True}, "inject": {"ok": True},
        "import": {"ok": False, "timed_out": False, "returncode": 0,
                   "errors": ["SCRIPT ERROR: Parse Error: malformed candidate script"]},
    }
    if prep_stage == "timeout":
        prep["import"]["timed_out"] = True
    elif prep_stage == "crash":
        prep["import"]["returncode"] = -11
    elif prep_stage in {"scratch", "inject"}:
        prep[prep_stage]["ok"] = False
    result.engine = {"prepare": {"ok": False, "attempts": [prep]}}
    reading = {"stop_reason": stop, "reached": False, "segment_clean": False,
               "detail": "scratch preparation failed at cold_import"}
    preflight, repaired, definitions = _case(reading=reading)
    for item in (
        _null_item(reading), _diff_item({key: reading for key in (
            "idle_env", "idle_flagged", "ops_env", "ops_flagged")}),
        _repair_differential_item(preflight, repaired),
        repair_restoration_item(preflight, repaired),
        repair_restoration_graded_item(preflight, repaired, definitions),
    ):
        _replace_item(result, item)
    return result


def test_mode4_confirmed_broken_import_is_a_rankable_zero():
    result = broken_import_result()
    before = deepcopy([i.to_dict() for i in result.items])
    card = score_task_result(result)
    assert card["weighted_total"]["score"] == 0.0
    assert card["ranking_eligible"]
    assert not card["evaluation_incomplete"]
    assert not set(card["strict"]["not_measured_required_items"]) & {
        "null_no_win", "anti_grant_diff", "repair_differential",
    }
    repair = next(c for category in card["categories"] for c in category["criteria"]
                  if c["id"] == "repair_restoration")
    assert repair["sources"][0]["status"] == Verdict.FAILED.value
    assert [i.to_dict() for i in result.items] == before


@pytest.mark.parametrize("change", [
    {"cold_status": "passed"}, {"cold_status": "inconclusive"},
    {"prep_stage": "timeout"}, {"prep_stage": "crash"},
    {"prep_stage": "scratch"}, {"prep_stage": "inject"},
    {"stop": "timeout"},
])
def test_mode4_needs_both_independent_import_failure_and_linked_launch_failure(change):
    card = score_task_result(broken_import_result(**change))
    assert card["weighted_total"]["score"] is None
    assert not card["ranking_eligible"]


@pytest.mark.parametrize("gap", ["publication", "route", "static", "control", "missing_route", "missing_pair"])
def test_mode4_confirmed_import_does_not_hide_independent_gaps(gap):
    result = broken_import_result()
    if gap == "publication":
        _replace_item(result, inconclusive("bugfix_publication", detail="preflight was not ready"))
    elif gap == "route":
        _replace_item(result, inconclusive("repair_restoration_graded", detail="route file is missing"))
    elif gap == "static":
        _replace_item(result, inconclusive("feature_kept", detail="static evaluator failed"))
    elif gap == "missing_route":
        repair = next(i for i in result.items if i.id == "repair_restoration_graded")
        repair.evidence["routes"]["unavailable"] = {"status": "not_run", "stop_reason": ""}
    elif gap == "missing_pair":
        control = next(i for i in result.items if i.id == "anti_grant_diff")
        control.evidence["ops_env"] = None
    else:
        _replace_item(result, inconclusive("anti_grant_diff", detail="recorder unavailable",
                                         attribution=Attribution.HARNESS))
    card = score_task_result(result)
    assert card["weighted_total"]["score"] is None
    assert not card["ranking_eligible"]
