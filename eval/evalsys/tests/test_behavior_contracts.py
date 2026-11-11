
from dataclasses import replace

from evalsys.assertions.behavior_contracts import probe_plan, route_progress_items, campaign_progress_items
from evalsys.assertions.universal import u3_collect_removes_one, u4_goal_changes_scene
from evalsys.routes.runner import GoldValidation, RouteReading
from evalsys.routes.schema import Budget, Goal, Milestone, Route, RouteStart
from evalsys.truth.snapshot import CollectProbe, GoalProbe, GroupTruth, LevelTruth, TruthSnapshot
from evalsys.verdict import Verdict


def test_public_two_avatar_contract_and_brief_alternative():
    from evalsys.assertions.exemptions import evaluate_universal
    for count, expected in ((1, Verdict.FAILED), (2, Verdict.PASSED), (3, Verdict.FAILED)):
        snap = snapshot()
        lv = replace(snap.levels[0], groups={**snap.levels[0].groups,
                     "gb_player": GroupTruth(total=count, alive=count)})
        snap = replace(snap, levels=(lv,))
        item = evaluate_universal(snap, task_id="ember_and_tide")[0]
        assert item.verdict == expected
        brief = evaluate_universal(snap, task_id="ember_and_tide", player_counts=(1, 2))[0]
        assert brief.verdict == (Verdict.PASSED if count in (1, 2) else Verdict.FAILED)


def test_failure_transition_is_not_a_progression_edge_or_frozen_expectation():
    from evalsys.truth.expectations import extract
    failed = replace(snapshot().levels[0], goal=GoalProbe(
        attempted=True, changed=True, scene_before="res://main.tscn",
        scene_after="res://failed.tscn"))
    succeeded = replace(failed, declared_scene="res://next.tscn", scene="res://next.tscn",
                        goal=replace(failed.goal, scene_before="res://next.tscn", scene_after="res://won.tscn"))
    snap = TruthSnapshot(project="two_avatar", levels=(failed, succeeded),
                         endings={"defeat": "res://failed.tscn", "victory": "res://won.tscn"})
    assert snap.digraph == {"res://next.tscn": "res://won.tscn"}
    frozen = extract(snap)
    assert frozen.levels[0].goal_transition is None
    assert frozen.levels[1].goal_transition == "res://won.tscn"


def test_stale_reference_edges_use_gt_endings_not_submission_names():
    from evalsys.assertions.behavior_contracts import reference_failure_edges
    from evalsys.truth.expectations import Expectations
    from evalsys.tasks import expectations_path
    from evalsys.ocard.channels import score_o4_level_topology
    expected = Expectations.read(expectations_path("ember_and_tide"))
    stale = reference_failure_edges("ember_and_tide", expected)
    assert len(stale) == 2 and set(stale.values()) == {"res://ui/chamber_failed.tscn"}
    renamed = replace(snapshot(), endings={"defeat": "res://my_own_failure.tscn"})
    scored = score_o4_level_topology(renamed, expected, task_id="ember_and_tide")
    item = next(i for i in scored.items if i.id == "E4/digraph")
    assert item.verdict == Verdict.INCONCLUSIVE


def test_scene_transition_during_pickup_keeps_incomplete_evidence_visible():
    reading = CollectProbe(attempted=True, target="Gem", effect={
        "kind": "target_changed", "observed": False, "observation_missing": True})
    assert u3_collect_removes_one(snapshot(collect=reading))[0].verdict == Verdict.UNOBSERVABLE


def test_death_during_incomplete_goal_probe_is_not_broken_game():
    reading = GoalProbe(attempted=True, changed=True, scene_before="res://main.tscn",
                        scene_after="res://failed.tscn")
    snap = replace(snapshot(goal=reading), endings={"defeat": "res://failed.tscn"})
    assert u4_goal_changes_scene(snap)[0].verdict == Verdict.UNOBSERVABLE
    complete = replace(reading, effect={"complete_trigger": True})
    assert u4_goal_changes_scene(replace(snap, levels=(replace(snap.levels[0], goal=complete),)))[0].verdict == Verdict.FAILED


def snapshot(*, collect=None, goal=None):
    return TruthSnapshot(project="arbitrary_submission_name", levels=(LevelTruth(
        "res://main.tscn", scene="res://main.tscn", reached=True,
        groups={"gb_collectible": GroupTruth(total=4, alive=4), "gb_goal": GroupTruth(total=1, alive=1)},
        collect=collect or CollectProbe(attempted=True),
        goal=goal or GoalProbe(attempted=True, scene_before="res://main.tscn", scene_after="res://main.tscn"),
    ),))


def test_persistent_resource_passes_without_removing_node():
    effect = {"kind": "numeric_increase", "slot": "score", "before": 120, "after": 140, "observed": True, "complete_trigger": True}
    reading = CollectProbe(attempted=True, delta=0, effect=effect)
    assert u3_collect_removes_one(snapshot(collect=reading))[0].verdict == Verdict.PASSED
    broken = replace(reading, effect={**effect, "after": 120, "observed": False})
    assert u3_collect_removes_one(snapshot(collect=broken))[0].verdict == Verdict.FAILED


def test_carried_or_multi_item_pickups_are_not_a_fixed_count():
    for delta in (0, -2, -4):
        reading = CollectProbe(attempted=True, delta=delta, effect={"observed": True, "kind": "target_changed"})
        assert u3_collect_removes_one(snapshot(collect=reading))[0].verdict == Verdict.PASSED


def test_missing_interact_is_not_a_broken_pickup_or_a_pass():
    assert u3_collect_removes_one(snapshot())[0].verdict == Verdict.UNOBSERVABLE
    reading = CollectProbe(attempted=True, effect={"complete_trigger": True, "activation_missing": "interact"})
    assert u3_collect_removes_one(snapshot(collect=reading))[0].verdict == Verdict.UNOBSERVABLE
    unreadable = replace(reading, effect={"complete_trigger": True, "observation_missing": True, "slot": "score"})
    assert u3_collect_removes_one(snapshot(collect=unreadable))[0].verdict == Verdict.UNOBSERVABLE


def test_in_place_room_progress_and_broken_goal():
    reading = GoalProbe(attempted=True, scene_before="res://main.tscn", scene_after="res://main.tscn",
                        effect={"kind": "progress_increase", "progress_before": 0, "progress_after": 1, "observed": True, "complete_trigger": True})
    assert u4_goal_changes_scene(snapshot(goal=reading))[0].verdict == Verdict.PASSED
    broken = replace(reading, effect={**reading.effect, "observed": False, "progress_after": 0})
    assert u4_goal_changes_scene(snapshot(goal=broken))[0].verdict == Verdict.FAILED


def test_standing_on_unarmed_goal_is_not_evidence_of_failure():
    assert u4_goal_changes_scene(snapshot())[0].verdict == Verdict.UNOBSERVABLE


def test_absent_public_progress_mapping_is_a_measurement_gap():
    reading = GoalProbe(attempted=True, effect={"kind": "progress_increase", "complete_trigger": True, "observation_missing": True})
    assert u4_goal_changes_scene(snapshot(goal=reading))[0].verdict == Verdict.UNOBSERVABLE


def test_route_progress_pass_failure_and_harness_gap():
    route = Route("test/progress", tier=2, start=RouteStart(level=0), goal=Goal("numeric(progress) >= 1"), budget=Budget(steps=2, frames=120))
    gold = [GoldValidation(route.route_id, True, "fixture")]
    reading = RouteReading(route.route_id, tier=2, reached=True, segment_clean=True, used_required=True, stop_reason="goal")
    assert route_progress_items(snapshot(), [route], [reading], gold)[0].verdict == Verdict.PASSED
    reading.segment_clean = False
    assert route_progress_items(snapshot(), [route], [reading], gold)[0].verdict == Verdict.FAILED
    reading.segment_clean = True
    reading.reached = False
    reading.stop_reason = "budget_frames"
    assert route_progress_items(snapshot(), [route], [reading], gold)[0].verdict == Verdict.FAILED
    reading.stop_reason = "timeout"
    assert route_progress_items(snapshot(), [route], [reading], gold)[0].verdict == Verdict.INCONCLUSIVE
    assert route_progress_items(snapshot(), [route], [], gold)[0].verdict == Verdict.INCONCLUSIVE


def test_campaign_is_one_observation_and_requires_clean_complete_run():
    route = Route("test/campaign", tier=5, start=RouteStart(level=0),
                  goal=Goal("whole_game_clear()"), budget=Budget(steps=2, frames=120))
    gold = [GoldValidation(route.route_id, True, "fixture")]
    reading = RouteReading(route.route_id, tier=5, reached=True, segment_clean=True,
                           used_required=True, clean_clear=True, stop_reason="goal_reached")
    items = campaign_progress_items([route], [reading], gold)
    assert len(items) == 1 and items[0].verdict == Verdict.PASSED
    assert items[0].evidence["source"] == "registered_whole_campaign"
    reading.used_required = False
    assert campaign_progress_items([route], [reading], gold)[0].verdict == Verdict.FAILED
    reading.used_required = True
    phased = replace(route, goal=Goal("whole_game_clear()", milestones=(Milestone("entered_final", "levels_visited() >= 3"),)))
    assert campaign_progress_items([phased], [reading], gold)[0].verdict == Verdict.FAILED
    reading.milestones_reached = ["entered_final"]
    assert campaign_progress_items([phased], [reading], gold)[0].verdict == Verdict.PASSED
    reading.segment_clean = False
    assert campaign_progress_items([route], [reading], gold)[0].verdict == Verdict.FAILED
    reading.stop_reason = "timeout"
    assert campaign_progress_items([route], [reading], gold)[0].verdict == Verdict.INCONCLUSIVE
    contact = replace(route, goal=Goal("contact(goal, 0)"))
    assert campaign_progress_items([contact], [reading], gold)[0].verdict == Verdict.INCONCLUSIVE
    injected = replace(route, start=RouteStart(level=0, inject={"seed": 1}))
    assert campaign_progress_items([injected], [reading], gold)[0].verdict == Verdict.INCONCLUSIVE


def test_plans_are_task_selected_and_probe_effects_roundtrip():
    assert probe_plan("arbitrary_submission_name") == {}
    assert probe_plan("gunfire_dungeon")["collect"]["actions"] == ["interact"]
    assert probe_plan("tiny_rts")["collect"]["slot"] == "score"
    for cls in (CollectProbe, GoalProbe):
        value = cls(effect={"observed": True, "kind": "test"})
        assert cls.from_dict(value.to_dict()) == value


def test_player_counts_come_from_the_task_plan_per_mode():
    from evalsys.assertions.behavior_contracts import player_counts_for_mode
    plan = probe_plan("ember_and_tide")
    assert player_counts_for_mode(plan, "brief") == (1, 2)
    assert player_counts_for_mode(plan, "gdd") == (2,)
    assert player_counts_for_mode(plan, "") == (2,)
    assert player_counts_for_mode({}, "brief") is None
    assert player_counts_for_mode(probe_plan("tiny_rts"), "brief") is None


def test_updated_rubrics_describe_effects_not_node_deletion():
    from evalsys.taskgen.content.rubrics import load_rubric_catalog, audit_rubric
    catalog = load_rubric_catalog()
    for task_id in ("pixel_platformer_v2", "gunfire_dungeon", "tiny_rts", "terraforge"):
        assert audit_rubric(task_id, catalog[task_id])["ready"]
    tiny = {c["id"]: c for c in catalog["tiny_rts"]["mechanic_checks"]}
    assert "finite_node_depletion" not in tiny
    assert tiny["resource_income"]["observable"]["predicate"] == "numeric_delta(score) > 0"
    for task, check in (("pixel_platformer_v2", "room_flag"), ("terraforge", "artifact_pedestal")):
        checks = {c["id"]: c for c in catalog[task]["mechanic_checks"]}
        assert "numeric_delta(progress)" in checks[check]["observable"]["predicate"]


def test_generated_modes_do_not_apply_reference_activation_inputs():
    from unittest.mock import patch
    from evalsys.pipeline import _truth_reading
    snap = snapshot()
    for mode, expected_task in (("brief", None), ("gdd", None), ("skeleton", None), ("port", None), ("", "tiny_rts"), ("bugfix", "tiny_rts")):
        with patch("evalsys.probe.runner.take_snapshot", return_value=snap) as take:
            _truth_reading("arbitrary_generated_project", task_id="tiny_rts",
                           expectations_dir=None, truth=None, quick=False,
                           expectations=object(), task_mode=mode)
        assert take.call_args.kwargs["task_id"] == expected_task
