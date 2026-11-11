
from copy import deepcopy

import pytest

from evalsys.taskgen.content.verifier_profiles import verifier_profile
from evalsys.taskgen.evaluate import (
    ENGINE_ITEM_IDS, FIDELITY_ITEM_IDS, _engine_skipped,
    repair_restoration_graded_item, repair_restoration_item,
)
from evalsys.taskgen.modes import parse_mode
from evalsys.taskgen.scorecard import (
    MODE1_REDESIGN_REGISTRY_VERSION,
    MODE2_REDESIGN_REGISTRY_VERSION,
    MODE3_REDESIGN_REGISTRY_VERSION,
    MODE1_VLM_REGISTRY_VERSION, MODE2_VLM_REGISTRY_VERSION, MODE3_VLM_REGISTRY_VERSION,
    MODE4_F2P_P2P_REGISTRY_VERSION,
    MODE4_REDESIGN_REGISTRY_VERSION,
    MODE34B_REGISTRY_VERSION, MODE34_REGISTRY_VERSION, MODE4_REGISTRY_VERSION,
    REGISTRY_VERSION, REGISTRY_VERSIONS, _category_specs, headline_rule,
    registry_policy, score_task_result,
    EVIDENCE_REGISTRY_VERSION,
    VISUAL_REGISTRY_VERSION,
    MODE5_RELEASE_REGISTRY_VERSION,
)
from evalsys.verdict import Verdict, failed, inconclusive, passed
from test_taskgen_scorecard import _fixture, _criterion, _replace_item


FORMULA = "repair_credit x gates x regression_factor x regression_mean"


def _reading(reached=False, milestones=(), invariants=(), observations=()):
    return dict(reached=reached, milestones_reached=list(milestones),
                invariants_failed=list(invariants), observations_reached=list(observations),
                stop_reason="budget_frames")


def _case(*, reading=None, preserved=0, preflight_readings=True):
    ids = [f"preserved/{i}" for i in range(preserved)]
    preflight = {
        "ready": True,
        "partition": {"target_route_ids": ["target"],
                      "regression_route_ids": ids[:-1],
                      "negative_control_route_ids": ids[-1:]},
        "source": {r: {"tier": 1, "status": "pass"} for r in ["target", *ids]},
        "mutant": {"target": {"tier": 1, "status": "behavioral_fail"}},
    }
    if preflight_readings:
        preflight["source"]["target"]["reading"] = _reading(True, ["checkpoint"])
        preflight["mutant"]["target"]["reading"] = _reading()
    repaired = {"ran": True, "readings": [
        {"route_id": "target", "baseline": "honest", "ok": False,
         "reading": reading if reading is not None else _reading(milestones=["checkpoint"])},
        *[{"route_id": r, "baseline": "honest", "ok": True, "reading": _reading(True)} for r in ids],
    ]}
    definitions = [{"route_id": "target", "goal": {
        "predicate": "win", "milestones": [{"name": "checkpoint", "predicate": "visited"}],
    }}]
    return preflight, repaired, definitions


def test_submission_assertions_with_route_only_preflight_fall_back():
    item = repair_restoration_graded_item(*_case(preflight_readings=False))
    assert item.verdict is Verdict.FAILED
    assert item.credit == 0
    assert item.evidence == {
        "repair_granularity": "route", "target_assertions_total": None,
        "target_assertions_restored": None, "restored_assertions": [],
        "unrestored_assertions": [], "target_routes_total": 1,
        "target_routes_restored": 0, "preserved_total": 0, "preserved_passing": 0,
        "preserved_failed": [], "regression_factor": 1.0, "regression_free": True,
    }


@pytest.mark.parametrize("milestones,reached,credit,verdict", [
    (["first", "second"], False, 0, Verdict.FAILED),
    (["first", "second", "last"], False, 0.5, Verdict.PASSED),
    (["first", "second", "last"], True, 1.0, Verdict.PASSED),
])
def test_only_newly_restored_checks_earn_credit(milestones, reached, credit, verdict):
    preflight, repaired, definitions = _case(reading=_reading(reached, milestones))
    definitions[0]["goal"]["milestones"] = [{"name": n} for n in ("first", "second", "last")]
    preflight["source"]["target"]["reading"] = _reading(True, ["first", "second", "last"])
    preflight["mutant"]["target"]["reading"] = _reading(False, ["first", "second"])
    repaired["readings"][0]["ok"] = reached
    item = repair_restoration_graded_item(preflight, repaired, definitions)
    assert item.verdict is verdict
    assert item.credit == credit
    assert item.evidence["repair_granularity"] == "assertion"
    assert item.evidence["target_assertions_total"] == 2
    assert item.evidence["target_assertions_restored"] == int(credit * 2)
    assert set(item.evidence["restored_assertions"] + item.evidence["unrestored_assertions"]) == {
        "target:goal", "target:milestones:last"}


@pytest.mark.parametrize("build", ["source", "mutant", "submission"])
def test_one_target_missing_checks_forces_whole_item_route_fallback(build):
    preflight, repaired, definitions = _case()
    preflight["partition"]["target_route_ids"].append("second")
    definitions.append({**definitions[0], "route_id": "second"})
    for state in ("source", "mutant"):
        preflight[state]["second"] = deepcopy(preflight[state]["target"])
    repaired["readings"].append({"route_id": "second", "ok": True, "reading": _reading(True, ["checkpoint"])})
    if build == "submission":
        repaired["readings"][-1]["reading"].pop("milestones_reached")
    else:
        preflight[build]["second"]["reading"].pop("milestones_reached")
    item = repair_restoration_graded_item(preflight, repaired, definitions)
    assert item.credit == 0.5
    assert item.evidence["repair_granularity"] == "route"
    assert item.evidence["target_assertions_total"] is None
    assert item.evidence["target_assertions_restored"] is None
    assert item.evidence["restored_assertions"] == item.evidence["unrestored_assertions"] == []


def test_only_source_pass_mutant_fail_checks_are_targets():
    preflight, repaired, definitions = _case()
    definitions[0]["goal"].update(invariants=[{"name": "alive"}], observations=[{"name": "seen"}])
    preflight["source"]["target"]["reading"] = _reading(True, ["checkpoint"], observations=["seen"])
    preflight["mutant"]["target"]["reading"] = _reading(False, ["checkpoint"], invariants=["alive"], observations=["seen"])


    item = repair_restoration_graded_item(preflight, repaired, definitions)
    assert item.credit == 0.5
    assert item.evidence["target_assertions_total"] == 2
    assert item.evidence["restored_assertions"] == ["target:invariants:alive"]
    assert item.evidence["unrestored_assertions"] == ["target:goal"]


def test_all_check_kinds_read_named_outcomes_and_ignore_undeclared_names():
    preflight, repaired, definitions = _case(reading=_reading(True, ["checkpoint", "extra"], ["alive"], ["seen"]))
    definitions[0]["goal"].update(invariants=[{"name": "alive"}], observations=[{"name": "seen"}])
    preflight["source"]["target"]["reading"]["observations_reached"] = ["seen"]
    preflight["mutant"]["target"]["reading"]["invariants_failed"] = ["alive"]
    item = repair_restoration_graded_item(preflight, repaired, definitions)
    assert item.credit == 3 / 4
    assert item.evidence["target_assertions_total"] == 4
    assert item.evidence["unrestored_assertions"] == ["target:invariants:alive"]
    assert "target:observations:seen" in item.evidence["restored_assertions"]


def test_route_only_submission_fallback():
    preflight, repaired, definitions = _case(reading={"stop_reason": "budget_frames"})

    preflight["partition"]["target_route_ids"].append("second")
    preflight["source"]["second"] = {"tier": 1, "status": "pass"}
    repaired["readings"].append({"route_id": "second", "ok": True})
    item = repair_restoration_graded_item(preflight, repaired, definitions)
    assert item.credit == 0.5
    assert item.evidence["repair_granularity"] == "route"
    assert item.evidence["target_assertions_total"] is None
    assert item.evidence["target_routes_restored"] == 1
    assert item.evidence["target_routes_total"] == 2


@pytest.mark.parametrize("route_only", [False, True])
def test_four_of_five_preserved_does_not_zero_repair(route_only):
    preflight, repaired, definitions = _case(preserved=5)
    if route_only:
        repaired["readings"][0].update(ok=True, reading={"stop_reason": "goal_reached"})

    repaired["readings"][-1].update(ok=False, reading=_reading(False))
    item = repair_restoration_graded_item(preflight, repaired, definitions)
    assert item.credit == (1.0 if route_only else 0.5)
    assert item.evidence["preserved_total"] == 5
    assert item.evidence["preserved_passing"] == 4
    assert item.evidence["preserved_failed"] == ["preserved/4"]
    assert item.evidence["regression_factor"] == 0.8
    assert item.evidence["regression_free"] is False
    assert repair_restoration_item(preflight, repaired).credit == 0


@pytest.mark.parametrize("gap", ["preflight", "not_ran", "infrastructure", "missing_route", "zero_targets"])
def test_same_inconclusive_conditions(gap):
    preflight, repaired, definitions = _case()
    if gap == "preflight":
        preflight["ready"] = False
    elif gap == "not_ran":
        repaired["ran"] = False
    elif gap == "infrastructure":
        repaired["readings"][0]["reading"] = {}
    elif gap == "missing_route":
        repaired["readings"] = []
    else:
        preflight["partition"]["target_route_ids"] = []
    assert repair_restoration_item(preflight, repaired).verdict is Verdict.INCONCLUSIVE
    assert repair_restoration_graded_item(preflight, repaired, definitions).verdict is Verdict.INCONCLUSIVE


def test_no_differential_assertions_is_inconclusive():
    preflight, repaired, definitions = _case()
    for build in ("source", "mutant"):
        preflight[build]["target"]["reading"] = _reading(True, ["checkpoint"])
    assert repair_restoration_graded_item(preflight, repaired, definitions).verdict is Verdict.INCONCLUSIVE


def test_registry_and_fidelity_registration():
    assert REGISTRY_VERSION == EVIDENCE_REGISTRY_VERSION
    assert MODE34B_REGISTRY_VERSION in REGISTRY_VERSIONS
    assert registry_policy(MODE34B_REGISTRY_VERSION).mode4_graded
    assert registry_policy(MODE34B_REGISTRY_VERSION).mode34_applicability
    assert FORMULA in headline_rule(MODE34B_REGISTRY_VERSION)
    assert "x edit_radius" not in headline_rule(MODE34B_REGISTRY_VERSION)
    assert "edit_radius is reported with weight 0" in headline_rule(MODE34B_REGISTRY_VERSION)
    for version in REGISTRY_VERSIONS:
        if version not in {
            MODE1_REDESIGN_REGISTRY_VERSION,
            MODE2_REDESIGN_REGISTRY_VERSION,
            MODE3_REDESIGN_REGISTRY_VERSION,
            MODE1_VLM_REGISTRY_VERSION, MODE2_VLM_REGISTRY_VERSION, MODE3_VLM_REGISTRY_VERSION,
            MODE4_REDESIGN_REGISTRY_VERSION,
            MODE4_F2P_P2P_REGISTRY_VERSION,
            MODE34B_REGISTRY_VERSION,
            EVIDENCE_REGISTRY_VERSION,
            VISUAL_REGISTRY_VERSION,
        }:
            assert not registry_policy(version).mode4_graded
    for mode in ("brief", "gdd", "skeleton", "bugfix"):
        before = _category_specs(mode, MODE34_REGISTRY_VERSION)
        after = _category_specs(mode, MODE34B_REGISTRY_VERSION)
        assert [(s.id, s.weight, [(c.id, c.weight) for c in s.criteria]) for s in before] == [
            (s.id, s.weight, [(c.id, c.weight) for c in s.criteria]) for s in after]
    item_id = "repair_restoration_graded"
    assert item_id in ENGINE_ITEM_IDS & FIDELITY_ITEM_IDS
    assert item_id in verifier_profile("bugfix").fidelity_ids
    assert item_id not in verifier_profile("bugfix").strict_ids
    assert item_id in {i.id for i in _engine_skipped(parse_mode("bugfix"), "no engine")}


@pytest.mark.parametrize("kind,repair,factor,mean,headline,old_headline,radius", [
    ("noop", 0, 1, 55 / 70, 0, 0, None),
    ("revert", 1, 1, 1, 100, 100, 100.0),
    ("revert_wide", 1, 1, 1, 100, 100, 75.0),


    ("regress", 1 / 2, 2 / 3, 55 / 70, 26.190, 0, 90.0),
])
def test_stored_fixtures(kind, repair, factor, mean, headline, old_headline, radius):
    result = _fixture(f"bugfix_{kind}_shadow_walker")
    original_items = deepcopy(result.items)
    old = score_task_result(result, MODE34_REGISTRY_VERSION)
    card = score_task_result(result, MODE34B_REGISTRY_VERSION)


    assert score_task_result(result)["registry_version"] == MODE4_REDESIGN_REGISTRY_VERSION
    assert score_task_result(result)["strict"] == card["strict"]
    assert card["weighted_total"]["status"] == "complete"
    assert card["weighted_total"]["score"] == headline
    assert old["weighted_total"]["score"] == old_headline
    assert score_task_result(result, MODE34_REGISTRY_VERSION) == old
    assert result.items == original_items
    assert card["strict"] == old["strict"]
    composition = card["weighted_total"]["composition"]
    assert composition["formula"] == FORMULA
    assert composition["repair_credit"] == round(repair * 100, 3)
    assert composition["repair_granularity"] == "route"
    assert composition["gates"] == 100
    assert composition["regression_factor"] == round(factor * 100, 3)
    assert composition["regression_free"] == (factor == 1)
    assert composition["edit_radius"] == radius
    assert composition["edit_radius_weight"] == 0
    radius_item = next(i for i in result.items if i.id == "edit_radius")
    assert composition["repair_detail"]["edit_radius"] == radius_item.evidence
    assert composition["regression_mean"] == round(mean * 100, 3)
    assert composition["repair_detail"]["repair_credit"] == repair
    assert composition["repair_detail"]["regression_factor"] == factor
    assert composition["repair_detail"]["regression_mean"] == mean
    assert headline == round(100 * repair * factor * mean, 3)
    assert composition["repair_detail"]["preserved_total"] == 3
    assert _criterion(card, "causal_playability", "repair_restoration")["sources"][0]["id"] == "repair_restoration_graded"
    assert _criterion(old, "causal_playability", "repair_restoration")["sources"][0]["id"] == "repair_restoration"


def test_wide_edit_reports_less_focus_without_headline_penalty():
    tight = score_task_result(_fixture("bugfix_revert_shadow_walker"), MODE34B_REGISTRY_VERSION)["weighted_total"]
    wide = score_task_result(_fixture("bugfix_revert_wide_shadow_walker"), MODE34B_REGISTRY_VERSION)["weighted_total"]
    assert wide["composition"]["edit_radius"] < tight["composition"]["edit_radius"]
    assert tight["score"] == wide["score"] == 100


@pytest.mark.parametrize("kind", ["revert", "regress", "noop", "revert_wide"])
def test_new_metric_does_not_change_older_rows(kind):
    result = _fixture(f"bugfix_{kind}_shadow_walker")
    without_metric = deepcopy(result)
    without_metric.items = [i for i in without_metric.items if i.id != "edit_radius"]
    for version in REGISTRY_VERSIONS:
        if version == MODE5_RELEASE_REGISTRY_VERSION:
            with pytest.raises(ValueError, match="only for Mode 5 / port"):
                score_task_result(result, version)
        elif version not in {
            MODE1_REDESIGN_REGISTRY_VERSION,
            MODE2_REDESIGN_REGISTRY_VERSION,
            MODE3_REDESIGN_REGISTRY_VERSION,
            MODE1_VLM_REGISTRY_VERSION, MODE2_VLM_REGISTRY_VERSION, MODE3_VLM_REGISTRY_VERSION,
            MODE4_REDESIGN_REGISTRY_VERSION,
            MODE4_F2P_P2P_REGISTRY_VERSION,
            MODE34B_REGISTRY_VERSION,
            EVIDENCE_REGISTRY_VERSION,
            VISUAL_REGISTRY_VERSION,
        }:
            assert score_task_result(result, version) == score_task_result(without_metric, version)


@pytest.mark.parametrize("verdict", [None, "inconclusive", "failed"])
def test_unmeasured_edit_radius_has_no_headline_effect(verdict):
    result = _fixture("bugfix_revert_shadow_walker")
    result.items = [i for i in result.items if i.id != "edit_radius"]
    if verdict is not None:
        result.items.append((inconclusive if verdict == "inconclusive" else failed)("edit_radius"))
    total = score_task_result(result, MODE34B_REGISTRY_VERSION)["weighted_total"]
    assert total["score"] == 100
    assert total["composition"]["edit_radius"] is None
    assert total["composition"]["edit_radius_weight"] == 0


def test_native_item_overrides_legacy_and_preserves_precision():
    result = _fixture("bugfix_revert_shadow_walker")
    preflight, repaired, definitions = _case()
    definitions[0]["goal"]["observations"] = [{"name": "unreached"}]
    preflight["source"]["target"]["reading"]["observations_reached"] = ["unreached"]
    item = repair_restoration_graded_item(preflight, repaired, definitions)
    assert item.credit == 1 / 3
    result.items.append(item)
    card = score_task_result(result, MODE34B_REGISTRY_VERSION)
    assert card["weighted_total"]["composition"]["repair_credit"] == 33.333
    assert card["weighted_total"]["composition"]["repair_detail"]["repair_credit"] == 1 / 3
    assert card["weighted_total"]["composition"]["repair_granularity"] == "assertion"
    assert card["weighted_total"]["score"] == 33.333
    assert score_task_result(result, MODE34_REGISTRY_VERSION)["weighted_total"]["score"] == 100
    _replace_item(result, inconclusive("repair_restoration_graded", detail="replay gap"))

    assert score_task_result(result, MODE34B_REGISTRY_VERSION)["evaluation_incomplete"]


@pytest.mark.parametrize("gate", ["no_eval_smuggling", "null_no_win", "anti_grant_static",
                                  "auto_win_ready", "anti_grant_diff", "extended_mash_no_win"])
def test_existing_gates_still_zero_headline(gate):
    result = _replace_item(_fixture("bugfix_revert_shadow_walker"), failed(gate))
    assert score_task_result(result, MODE34B_REGISTRY_VERSION)["weighted_total"]["score"] == 0


def test_fallback_can_recover_preserved_ids_from_strict_evidence():
    result = _fixture("bugfix_revert_shadow_walker")
    item = next(i for i in result.items if i.id == "repair_restoration")
    item.evidence.pop("routes", None)
    assert score_task_result(result, MODE34B_REGISTRY_VERSION)["weighted_total"]["score"] == 100


def test_missing_preserved_denominator_is_unmeasured():
    result = _fixture("bugfix_regress_shadow_walker")
    for item in result.items:
        if item.id in {"repair_restoration", "repair_differential"}:
            item.evidence.pop("routes", None)
    result.items = [i for i in result.items if i.id != "bugfix_publication"]
    card = score_task_result(result, MODE34B_REGISTRY_VERSION)
    assert card["weighted_total"]["status"] == "evaluation_incomplete"
    assert card["weighted_total"]["score"] is None
    assert not card["ranking_eligible"]
    assert "preserved route total cannot be recovered" in card["weighted_total"]["unmeasured"][0]["step"]


@pytest.mark.parametrize("with_partition", [False, True])
def test_publication_denominator_requires_structured_partition(with_partition):
    result = _fixture("bugfix_revert_shadow_walker")
    differential = next(i for i in result.items if i.id == "repair_differential")
    partition = {
        "regression_route_ids": differential.evidence["regression_routes"],
        "negative_control_route_ids": differential.evidence["negative_control_routes"],
    }
    differential.evidence = {}
    next(i for i in result.items if i.id == "repair_restoration").evidence.pop("routes")
    publication = next(i for i in result.items if i.id == "bugfix_publication")

    publication.evidence = {"partition": partition} if with_partition else {}
    card = score_task_result(result, MODE34B_REGISTRY_VERSION)
    if with_partition:
        assert card["weighted_total"]["score"] == 100
        assert card["weighted_total"]["composition"]["repair_detail"]["preserved_denominator_source"] == "bugfix_publication.partition"
    else:
        assert card["evaluation_incomplete"]
        assert card["weighted_total"]["score"] is None


def test_missing_native_regression_factor_is_unmeasured():
    result = _fixture("bugfix_revert_shadow_walker")
    result.items.append(passed("repair_restoration_graded", credit=1))
    card = score_task_result(result, MODE34B_REGISTRY_VERSION)
    assert card["evaluation_incomplete"]
    assert "regression_factor" in card["weighted_total"]["unmeasured"][0]["step"]


@pytest.mark.parametrize("cells_on", ["repair_restoration", "repair_differential"])
def test_fallback_route_cell_census(cells_on):
    result = _fixture("bugfix_regress_shadow_walker")


    restoration = next(i for i in result.items if i.id == "repair_restoration")
    restoration.evidence = {"restored_targets": ["a"], "unrestored_targets": ["b"],
                            "target_count": 2, "regression_failed": ["c"],
                            "negative_control_failed": []}
    cells = {r: {"status": "pass" if r in {"a", "d", "e"} else "behavioral_fail"}
             for r in "abcde"}
    next(i for i in result.items if i.id == cells_on).evidence["routes"] = cells
    result.items = [i for i in result.items if i.id != "bugfix_publication"]
    card = score_task_result(result, MODE34B_REGISTRY_VERSION)
    assert card["weighted_total"]["score"] == 26.190
    detail = card["weighted_total"]["composition"]["repair_detail"]
    assert detail["preserved_total"] == 3
    assert detail["preserved_denominator_source"] == "route_cells"


def test_bugfix_engine_emits_graded_item_from_frozen_definition(tmp_path, monkeypatch):
    import json
    from types import SimpleNamespace
    from evalsys.taskgen import evaluate

    preflight, repaired, definitions = _case()
    frozen = tmp_path / "route.json"
    frozen.write_text(json.dumps({"routes": definitions}))
    pkg = SimpleNamespace(hidden=tmp_path, manifest={"mode": "bugfix"})
    sub = SimpleNamespace(project=tmp_path)
    oracle = {"registered_task": {"frozen_route": "route.json"}, "bugfix_preflight": preflight}
    monkeypatch.setattr(evaluate, "godot_available", lambda: "godot")
    monkeypatch.setattr(evaluate, "load_submission_interface", lambda _: None)
    monkeypatch.setattr(evaluate, "run_bugfix_gates", lambda *args, **kwargs: {"ran": True, "gold": repaired})
    items = []
    evaluate._run_engine(parse_mode("bugfix"), pkg, sub, oracle, items)
    by_id = {i.id: i for i in items}
    assert by_id["repair_restoration_graded"].credit == 0.5
    assert by_id["repair_restoration"] == repair_restoration_item(preflight, repaired)
    assert by_id["repair_differential"].verdict is Verdict.FAILED


def test_frozen_check_shorthand_uses_replay_schema_names():
    preflight, repaired, definitions = _case(reading=_reading(milestones=["m1"]))
    definitions[0]["goal"]["milestones"] = ["numeric(score) >= 1"]
    preflight["source"]["target"]["reading"]["milestones_reached"] = ["m1"]
    item = repair_restoration_graded_item(preflight, repaired, definitions)
    assert item.credit == 0.5
    assert item.evidence["restored_assertions"] == ["target:milestones:m1"]
