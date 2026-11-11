


from __future__ import annotations

import json
import re
import tempfile
import unittest
from dataclasses import replace
from pathlib import Path
from unittest.mock import patch

from evalsys.assertions.exemptions import evaluate_universal
from evalsys.assertions.extracted import (
    canonical_digraph,
    level_topology,
    spatial_ordinal,
)
from evalsys.assertions.universal import UNIVERSAL, all_universal
from evalsys.truth.expectations import Expectations, extract, occupied_bands
from evalsys.truth.snapshot import (
    ActionLiveness,
    Bounds,
    CollectCycle,
    CollectProbe,
    GoalProbe,
    GroupTruth,
    LevelTruth,
    NormPoint,
    TruthSnapshot,
    Vec3,
)
from evalsys.verdict import Verdict, score_items


def _points(pairs) -> tuple[NormPoint, ...]:
    return tuple(NormPoint(x, y, True) for x, y in pairs)


def _level(
    scene: str,
    *,
    collectibles,
    goal=(0.9, 0.5),
    spawn=(0.1, 0.5),
    transition: str | None = None,
) -> LevelTruth:
    groups = {
        "gb_player": GroupTruth(1, 1, _points([spawn]), (Vec3(*spawn, 0.0),)),
        "gb_collectible": GroupTruth(
            len(collectibles), len(collectibles), _points(collectibles), ()
        ),
        "gb_goal": GroupTruth(1, 1, _points([goal]), ()),
    }
    return LevelTruth(
        declared_scene=scene,
        scene=scene,
        reached=True,
        groups=groups,
        bounds=Bounds(True, "xy", Vec3(0, 0, 0), Vec3(10, 10, 0)),
        spawn=Vec3(*spawn, 0.0),
        spawn_norm=NormPoint(*spawn, True),
        bound_actions=("gb_left", "gb_right", "gb_jump"),
        collect=CollectProbe(True, "Coin0", len(collectibles), len(collectibles) - 1, -1,
                             "one cycle removed one", {"expected_delta": -1, "complete_trigger": True}),
        goal=GoalProbe(
            attempted=True,
            target="Exit",
            scene_before=scene,
            scene_after=transition or scene,
            changed=transition is not None,
            frames_to_change=12 if transition else -1,
            detail="observed",
            effect={"kind": "scene_transition", "complete_trigger": True},
        ),
    )


def reference_snapshot() -> TruthSnapshot:

    return TruthSnapshot(
        project="fixture",
        levels=(
            _level(
                "res://l1.tscn",
                collectibles=[(0.1, 0.5), (0.35, 0.5), (0.6, 0.5), (0.85, 0.5)],
                transition="res://results.tscn",
            ),
            _level(
                "res://l2.tscn",
                collectibles=[(0.1, 0.2), (0.3, 0.4), (0.5, 0.6), (0.7, 0.8), (0.9, 0.9)],
                transition="res://results.tscn",
            ),
            _level(
                "res://l3.tscn",
                collectibles=[
                    (0.05, 0.1), (0.25, 0.3), (0.45, 0.5), (0.65, 0.7), (0.85, 0.9),
                    (0.95, 0.95),
                ],
                transition="res://complete.tscn",
            ),
        ),
        liveness=(
            ActionLiveness("gb_left", True, "position"),
            ActionLiveness("gb_right", True, "position"),
            ActionLiveness("gb_jump", False, "none"),
        ),
        declared_actions=("gb_left", "gb_right", "gb_jump"),
        endings={"victory": "res://results.tscn", "defeat": "res://failed.tscn"},
        no_input_no_win=True,
        no_input_stop_reason="budget_frames",
        probe_sha256="deadbeef",
        engine="4.5.1",
    )


def failing_ids(items) -> set[str]:

    return {
        it.id.split("/")[0]
        for it in items
        if it.verdict in (Verdict.FAILED, Verdict.MALFORMED)
    }


def all_items(snapshot: TruthSnapshot, expected: Expectations):
    return (
        evaluate_universal(snapshot)
        + level_topology(snapshot, expected)
        + spatial_ordinal(snapshot, expected)
    )


class GoldIsGreen(unittest.TestCase):


    def test_the_reference_passes_every_assertion_against_itself(self):
        snap = reference_snapshot()
        expected = extract(snap)
        items = all_items(snap, expected)
        self.assertEqual(failing_ids(items), set())
        for label, interval in (
            ("O3", score_items(evaluate_universal(snap))),
            ("O4", score_items(level_topology(snap, expected))),
            ("O5", score_items(spatial_ordinal(snap, expected))),
        ):
            with self.subTest(channel=label):
                self.assertEqual(interval.lo, 1.0)
                self.assertEqual(interval.coverage, 1.0)


class NegativeControls(unittest.TestCase):


    def setUp(self) -> None:
        self.snap = reference_snapshot()
        self.expected = extract(self.snap)

    def assertOnlyFails(self, broken: TruthSnapshot, family: str) -> None:
        items = all_items(broken, self.expected)
        got = failing_ids(items)
        self.assertIn(
            family, got,
            f"{family} was supposed to go red on this control and did not; the "
            "assertion has not been shown capable of failing",
        )
        self.assertEqual(
            got, {family},
            f"{family} was the target but {sorted(got - {family})} went red too; an "
            "assertion that fires on somebody else's defect cannot localise anything",
        )

    def _with_levels(self, levels) -> TruthSnapshot:
        return replace(self.snap, levels=tuple(levels))

    def test_u1_two_players(self):
        levels = list(self.snap.levels)
        groups = dict(levels[0].groups)
        groups["gb_player"] = GroupTruth(2, 2, _points([(0.1, 0.5), (0.2, 0.5)]), ())
        levels[0] = replace(levels[0], groups=groups)
        self.assertOnlyFails(self._with_levels(levels), "U1")

    def test_u2_every_action_dead(self):
        dead = tuple(replace(a, live=False, via="none") for a in self.snap.liveness)
        self.assertOnlyFails(replace(self.snap, liveness=dead), "U2")

    def test_u3_pickup_removes_nothing(self):
        levels = list(self.snap.levels)
        levels[1] = replace(
            levels[1],
            collect=replace(levels[1].collect, alive_after=5, delta=0,
                            detail="the group did not move"),
        )
        self.assertOnlyFails(self._with_levels(levels), "U3")

    def test_u3_pickup_clears_the_whole_group(self):


        levels = list(self.snap.levels)
        levels[1] = replace(
            levels[1],
            collect=replace(levels[1].collect, alive_after=0, delta=-5,
                            detail="the whole group vanished at once"),
        )
        self.assertOnlyFails(self._with_levels(levels), "U3")

    def test_u4_goal_is_decorative(self):
        levels = list(self.snap.levels)
        levels[0] = replace(
            levels[0],
            goal=replace(levels[0].goal, changed=False,
                         scene_after=levels[0].scene, frames_to_change=-1,
                         detail="stood on the goal, nothing happened"),
        )


        items = all_items(self._with_levels(levels), self.expected)
        self.assertEqual(failing_ids(items), {"U4", "E4"})

    def test_u5_the_game_wins_itself(self):
        broken = replace(
            self.snap, no_input_no_win=False, no_input_stop_reason="goal_reached"
        )
        self.assertOnlyFails(broken, "U5")

    def test_u6_a_level_cannot_be_entered_directly(self):
        levels = list(self.snap.levels)
        levels[2] = replace(
            levels[2], reached=False, scene="res://main_menu.tscn",
            stop_reason="no_record",
        )


        items = all_items(self._with_levels(levels), self.expected)
        self.assertEqual(failing_ids(items), {"U6", "E4"})

    def test_u7_one_ending_for_everything(self):

        broken = replace(self.snap, endings={"victory": "res://over.tscn",
                                             "defeat": "res://over.tscn"})
        self.assertOnlyFails(broken, "U7")
        missing = replace(self.snap, endings={})
        self.assertOnlyFails(missing, "U7")
        u7 = [i for i in all_universal(self.snap) if i.id.startswith("U7")]
        self.assertEqual(len(u7), 1)
        self.assertIs(u7[0].verdict, Verdict.PASSED)

    def test_e1_a_level_is_missing(self):


        broken = self._with_levels(self.snap.levels[:2])
        items = all_items(broken, self.expected)
        self.assertEqual(failing_ids(items), {"E1", "E4"})

    def test_e2_the_levels_are_ordered_the_wrong_way(self):
        levels = list(self.snap.levels)
        levels[0], levels[2] = levels[2], levels[0]
        broken = self._with_levels(
            [replace(lv, declared_scene=s, scene=s)
             for lv, s in zip(levels, ["res://l1.tscn", "res://l2.tscn", "res://l3.tscn"])]
        )
        items = all_items(broken, self.expected)

        self.assertIn("E2", failing_ids(items))

    def test_e3_the_goal_is_underfoot(self):


        levels = list(self.snap.levels)
        groups = dict(levels[1].groups)
        groups["gb_goal"] = GroupTruth(1, 1, _points([(0.1, 0.5)]), ())
        levels[1] = replace(levels[1], groups=groups)
        self.assertOnlyFails(self._with_levels(levels), "E3")

    def test_e4_the_last_level_exits_to_the_shared_results_screen(self):


        levels = list(self.snap.levels)
        levels[2] = replace(
            levels[2],
            goal=replace(levels[2].goal, scene_after="res://results.tscn"),
        )
        self.assertOnlyFails(self._with_levels(levels), "E4")

    def test_e5_the_collectibles_are_piled_in_one_place(self):
        levels = list(self.snap.levels)
        groups = dict(levels[2].groups)
        groups["gb_collectible"] = GroupTruth(
            6, 6, _points([(0.5, 0.5)] * 6), ()
        )
        levels[2] = replace(levels[2], groups=groups)
        self.assertOnlyFails(self._with_levels(levels), "E5")


class EveryAssertionHasBeenSeenToFail(unittest.TestCase):
    def test_no_family_is_uncertified(self):


        controls = {
            name.split("test_")[1].split("_")[0].upper()
            for name in dir(NegativeControls)
            if name.startswith("test_")
        }
        families = {fid for fid, _fn in UNIVERSAL} | {"E1", "E2", "E3", "E4", "E5"}
        self.assertEqual(
            families - controls, set(),
            "these assertion families have no negative control, so nothing shows "
            "they are capable of going red",
        )


class SnapshotShape(unittest.TestCase):
    def test_live_actions_strips_forbidden_pause_and_reset(self):
        snap = reference_snapshot()
        snap = TruthSnapshot.from_dict(snap.to_dict())

        payload = snap.to_dict()
        payload["liveness"] = list(payload["liveness"]) + [
            {"action": "gb_pause", "live": True, "via": "numeric", "detail": "",
             "idle_baseline_id": ""},
            {"action": "gb_reset", "live": True, "via": "numeric", "detail": "",
             "idle_baseline_id": ""},
        ]
        again = TruthSnapshot.from_dict(payload)
        self.assertNotIn("gb_pause", again.live_actions)
        self.assertNotIn("gb_reset", again.live_actions)
        self.assertTrue(again.live_actions["gb_left"])

    def test_json_round_trip_is_lossless(self):
        snap = reference_snapshot()
        again = TruthSnapshot.from_dict(json.loads(snap.to_json()))
        self.assertEqual(again.to_dict(), snap.to_dict())

    def test_expectations_round_trip_is_lossless(self):
        expected = extract(reference_snapshot())
        again = Expectations.from_dict(json.loads(json.dumps(expected.to_dict())))
        self.assertEqual(again.to_dict(), expected.to_dict())

    def test_an_unread_level_is_not_a_level_with_nothing_in_it(self):
        empty = LevelTruth(declared_scene="res://x.tscn", stop_reason="no_record")
        self.assertIsNone(empty.spawn)
        self.assertFalse(empty.reached)
        self.assertEqual(empty.census["gb_player"], 0)
        self.assertIsNone(empty.goal_transition)

    def test_a_refused_normalisation_is_not_the_origin(self):
        self.assertFalse(NormPoint.from_any({"x": 0.0, "y": 0.0}).valid)
        self.assertFalse(NormPoint.from_any(None).valid)
        self.assertTrue(NormPoint.from_any({"x": 0.0, "y": 0.0, "valid": True}).valid)

    def test_the_digraph_is_compared_by_shape_not_by_file_name(self):
        a = canonical_digraph(
            ["res://l1.tscn", "res://l2.tscn"],
            {"res://l1.tscn": "res://l2.tscn", "res://l2.tscn": "res://end.tscn"},
        )
        b = canonical_digraph(
            ["res://stage_one.tscn", "res://stage_two.tscn"],
            {"res://stage_one.tscn": "res://stage_two.tscn",
             "res://stage_two.tscn": "res://fin.tscn"},
        )
        self.assertEqual(a, b)

    def test_two_exits_to_one_screen_differ_from_two_exits_to_two(self):
        shared = canonical_digraph(
            ["a", "b"], {"a": "res://end.tscn", "b": "res://end.tscn"}
        )
        split = canonical_digraph(
            ["a", "b"], {"a": "res://end.tscn", "b": "res://other.tscn"}
        )
        self.assertNotEqual(shared, split)

    def test_bands_count_occupancy_not_points(self):
        piled = _points([(0.5, 0.5)] * 20)
        spread = _points([(0.05, 0.0), (0.45, 0.0), (0.95, 0.0)])
        self.assertEqual(occupied_bands(piled, "x"), 1)
        self.assertEqual(occupied_bands(spread, "x"), 3)


class GenreExemptions(unittest.TestCase):


    def test_no_task_id_means_no_exemption(self):

        levels = list(reference_snapshot().levels)
        levels[0] = replace(
            levels[0],
            goal=replace(levels[0].goal, changed=False, scene_after=levels[0].scene),
        )
        snap = replace(reference_snapshot(), project="racing", levels=tuple(levels))
        self.assertIn("U4", failing_ids(all_universal(snap)))

        scored = evaluate_universal(snap)
        self.assertIn("U4", failing_ids(scored))

    def test_racing_u4_becomes_exempt_and_stays_in_denominator(self):
        levels = list(reference_snapshot().levels)
        levels[0] = replace(
            levels[0],
            goal=replace(levels[0].goal, changed=False, scene_after=levels[0].scene),
        )
        snap = replace(reference_snapshot(), project="other_folder", levels=tuple(levels))
        self.assertIn("U4", failing_ids(all_universal(snap)))
        scored = evaluate_universal(snap, task_id="racing")
        self.assertNotIn("U4", failing_ids(scored))
        u4 = [i for i in scored if i.id.startswith("U4")]
        self.assertTrue(u4)
        self.assertTrue(all(i.verdict is Verdict.EXEMPT for i in u4))
        iv = score_items(scored)
        self.assertLess(iv.lo, 1.0)
        self.assertEqual(iv.hi, 1.0)
        self.assertGreater(iv.by_verdict.get("exempt", 0.0), 0.0)
        u1 = [i for i in scored if i.id.startswith("U1")]
        self.assertTrue(u1)
        self.assertTrue(all(i.verdict is Verdict.PASSED for i in u1))

    def test_canopy_dash_u3_is_not_exempted(self):

        levels = list(reference_snapshot().levels)
        levels[0] = replace(
            levels[0],
            collect=replace(
                levels[0].collect,
                delta=0,
                alive_after=levels[0].collect.alive_before,
            ),
        )
        snap = replace(
            reference_snapshot(), project="canopy_dash", levels=tuple(levels),
        )
        self.assertIn("U3", failing_ids(evaluate_universal(snap, task_id="canopy_dash")))

    def test_wrong_task_id_does_not_inherit_racing_exemption(self):
        levels = list(reference_snapshot().levels)
        levels[0] = replace(
            levels[0],
            goal=replace(levels[0].goal, changed=False, scene_after=levels[0].scene),
        )
        snap = replace(reference_snapshot(), project="racing", levels=tuple(levels))
        scored = evaluate_universal(snap, task_id="3d_platformer")
        self.assertIn("U4", failing_ids(scored))

    def test_taskgen_gated_goal_tasks_exempt_u4_from_the_rubric_catalog(self):

        levels = list(reference_snapshot().levels)
        levels[0] = replace(
            levels[0],
            goal=replace(levels[0].goal, changed=False, scene_after=levels[0].scene),
        )
        for task_id in ("arc_wing", "cat_defense"):
            snap = replace(reference_snapshot(), project="renamed", levels=tuple(levels))
            self.assertIn("U4", failing_ids(all_universal(snap)))
            scored = evaluate_universal(snap, task_id=task_id)
            self.assertNotIn("U4", failing_ids(scored), task_id)
            u4 = [i for i in scored if i.id.startswith("U4")]
            self.assertTrue(u4)
            self.assertTrue(all(i.verdict is Verdict.EXEMPT for i in u4))
            iv = score_items(scored)
            self.assertLess(iv.lo, 1.0)
            self.assertEqual(iv.hi, 1.0)

    def test_u4_exemption_carries_over_to_e4_which_uses_the_same_probe(self):


        from evalsys.assertions.exemptions import apply_goal_probe_exemption_to_e4
        from evalsys.verdict import Item, failed, passed, unobservable

        measured = [
            passed("E1/level_count", detail="3 declared levels, reference has 3"),
            failed("E4/digraph", detail="the level graph differs: measured (no edges)"),
        ]
        out = apply_goal_probe_exemption_to_e4("cat_defense", measured)
        self.assertIs(out[0].verdict, Verdict.PASSED)
        self.assertIs(out[1].verdict, Verdict.EXEMPT)
        self.assertEqual(out[1].evidence["inherited_from"], "U4")
        self.assertEqual(out[1].evidence["original_verdict"], "failed")
        self.assertIn("measured: the level graph differs", out[1].detail)

        self.assertEqual(score_items(out).point, 1.0)

        self.assertIs(apply_goal_probe_exemption_to_e4(None, measured)[1].verdict, Verdict.FAILED)
        self.assertIs(
            apply_goal_probe_exemption_to_e4("3d_platformer", measured)[1].verdict,
            Verdict.FAILED,
        )

        gap = [unobservable("E4/digraph", detail="no edges in the reference")]
        self.assertIs(
            apply_goal_probe_exemption_to_e4("cat_defense", gap)[0].verdict,
            Verdict.UNOBSERVABLE,
        )
        self.assertIsInstance(out[1], Item)


class GoalTransitionToFailureEnding(unittest.TestCase):


    def _snap(self, scene_after: str, endings: dict[str, str]) -> TruthSnapshot:
        levels = list(reference_snapshot().levels)
        levels[0] = replace(
            levels[0],
            goal=replace(levels[0].goal, changed=True, scene_after=scene_after),
        )
        return replace(reference_snapshot(), levels=tuple(levels), endings=endings)

    def test_declared_failure_scene_is_not_a_goal_transition(self):
        endings = {"victory": "res://results.tscn", "defeat": "res://defeat.tscn"}
        snap = self._snap("res://defeat.tscn", endings)
        items = [i for i in all_universal(snap) if i.id == "U4/goal_changes_scene[0]"]
        self.assertEqual(1, len(items))
        self.assertIs(Verdict.FAILED, items[0].verdict)
        self.assertIn("failure ending", items[0].detail)

    def test_success_scene_and_next_level_still_pass(self):
        endings = {"victory": "res://results.tscn", "defeat": "res://defeat.tscn"}
        for scene_after in ("res://results.tscn", "res://l2.tscn"):
            snap = self._snap(scene_after, endings)
            items = [i for i in all_universal(snap) if i.id == "U4/goal_changes_scene[0]"]
            self.assertIs(Verdict.PASSED, items[0].verdict, scene_after)

    def test_without_declared_endings_the_change_is_still_credited(self):
        snap = self._snap("res://defeat.tscn", {})
        items = [i for i in all_universal(snap) if i.id == "U4/goal_changes_scene[0]"]
        self.assertIs(Verdict.PASSED, items[0].verdict)


class ExemptVerdict(unittest.TestCase):
    def test_exempt_widens_interval_without_shrinking_coverage(self):
        from evalsys.verdict import exempt, passed

        iv = score_items([passed("a"), exempt("b")])
        self.assertEqual(iv.lo, 0.5)
        self.assertEqual(iv.hi, 1.0)
        self.assertEqual(iv.coverage, 1.0)
        self.assertEqual(iv.denominator, 2.0)

    def test_o5_empty_objects_are_unobservable_not_exempt(self):


        from evalsys.truth.expectations import LevelExpectation

        expected = Expectations(
            project="sandbox",
            levels=(LevelExpectation(scene="res://a.tscn"),),
        )
        snap = TruthSnapshot(
            project="sandbox",
            levels=(LevelTruth(
                declared_scene="res://a.tscn",
                scene="res://a.tscn",
                stop_reason="ok",
                reached=True,
            ),),
        )
        items = spatial_ordinal(snap, expected)
        self.assertTrue(items, "O5 must still emit items")
        for item in items:
            self.assertIs(
                item.verdict, Verdict.UNOBSERVABLE,
                f"{item.id} must be unobservable, got {item.verdict}: {item.detail}",
            )
        iv = score_items(items)
        self.assertEqual(iv.denominator, 0.0)

    def test_terraforge_o5_ceiling_is_zero(self):
        from evalsys.assertions.schema import (
            TASK_CHANNEL_OVERRIDES,
            channel_overrides_for,
        )

        self.assertEqual(TASK_CHANNEL_OVERRIDES["terraforge"]["O5"], 0.0)
        merged = channel_overrides_for("D1", "terraforge")
        self.assertEqual(merged["O5"], 0.0)
        self.assertEqual(merged["O6"], 0.0)
        self.assertNotIn("O5", channel_overrides_for("D1", "racing"))


class NumericBagSurvivesGoalTransition(unittest.TestCase):


    def test_level_truth_walks_back_to_the_last_nonempty_numeric(self):
        from evalsys.probe.runner import LevelRun, level_truth

        rec = {
            "base": {"scene_path": "res://level.tscn", "groups": {}, "player": {}},
            "numeric": {},
            "numeric_source": {},
            "series": [
                {"frame": 8, "numeric": {"shields": 3.0, "progress": 1.0},
                 "numeric_source": {"shields": "declared", "progress": "declared"}},
                {"frame": 90, "numeric": {}, "numeric_source": {}},
            ],
            "stop_reason": "goal_transition",
            "collect": {},
            "goal": {},
        }
        lv = level_truth(LevelRun(declared_scene="res://level.tscn", record=rec))
        self.assertEqual(lv.numeric["shields"], 3.0)
        self.assertEqual(lv.numeric["progress"], 1.0)
        self.assertEqual(lv.numeric_source["progress"], "declared")
        self.assertEqual(lv.stop_reason, "goal_transition")


class FrozenExpectationsOnDisk(unittest.TestCase):


    ROOT = Path(__file__).resolve().parents[2] / "tasks"

    def test_every_frozen_file_parses_and_records_its_provenance(self):
        files = sorted(self.ROOT.glob("*/expectations.json")) if self.ROOT.is_dir() else []
        if not files:
            self.skipTest("no expectations have been frozen in this checkout")
        for path in files:
            with self.subTest(task=path.parent.name):
                expected = Expectations.read(path)
                self.assertEqual(expected.project, path.parent.name)
                self.assertTrue(expected.generated_at, "frozen with no date")
                self.assertTrue(expected.probe_sha256, "frozen with no probe hash")
                self.assertGreater(expected.level_count, 0)


class NumericPropertiesStayInSync(unittest.TestCase):


    def _names_from_gd(self, text: str) -> list[str]:
        marker = "NUMERIC_PROPERTIES"
        start = text.find(marker)
        self.assertGreaterEqual(start, 0, "NUMERIC_PROPERTIES missing")
        bracket = text.find("[", start)
        end = text.find("]", bracket)
        chunk = text[bracket : end + 1]
        import re

        return re.findall(r'"([^"]+)"', chunk)

    def test_truth_and_route_drivers_match_python(self):
        from evalsys.probe.numeric_props import NUMERIC_PROPERTIES

        root = Path(__file__).resolve().parents[1] / "evalsys"
        truth_gd = (root.parents[1] / "harness" / "gb_truth_driver.gd").read_text(
            encoding="utf-8"
        )
        route_gd = (root.parents[1] / "harness" / "gb_route_driver.gd").read_text(
            encoding="utf-8"
        )
        self.assertEqual(tuple(self._names_from_gd(truth_gd)), NUMERIC_PROPERTIES)
        self.assertEqual(tuple(self._names_from_gd(route_gd)), NUMERIC_PROPERTIES)
        self.assertIn("fuse_left", NUMERIC_PROPERTIES)
        self.assertIn("heat", NUMERIC_PROPERTIES)
        self.assertIn("shields", NUMERIC_PROPERTIES)


class AuthoredScaffold(unittest.TestCase):
    def test_no_task_id_means_no_h_items(self):
        from evalsys.assertions.authored import evaluate_authored

        snap = reference_snapshot()
        self.assertEqual(evaluate_authored(snap, task_id=None), [])
        self.assertEqual(evaluate_authored(snap, task_id="unknown_game"), [])

    def test_empty_h_does_not_change_o3_for_unregistered_task(self):
        from evalsys.assertions.exemptions import evaluate_universal
        from evalsys.assertions.authored import evaluate_authored
        from evalsys.ocard.channels import score_o3_mechanic_fidelity

        snap = replace(reference_snapshot(), project="racing")
        u_only = score_items(evaluate_universal(snap, task_id="racing"))
        with_h = score_items(
            list(evaluate_universal(snap, task_id="racing"))
            + evaluate_authored(snap, task_id="racing")
        )
        self.assertEqual(u_only.lo, with_h.lo)
        self.assertEqual(u_only.denominator, with_h.denominator)
        card = score_o3_mechanic_fidelity(snap, None, task_id="racing")
        self.assertIn("not registered", card.detail)

    def test_series_round_trip(self):
        from evalsys.truth.snapshot import SeriesSample

        sample = SeriesSample(
            frame=24,
            phase="pick",
            player=Vec3(1.0, 2.0, 3.0),
            numeric={"fuse_left": 12.5, "shields": 3.0},
            scene_path="res://a.tscn",
            alive={"gb_collectible": 9},
        )
        back = SeriesSample.from_dict(sample.to_dict())
        self.assertEqual(back.frame, 24)
        self.assertEqual(back.numeric["fuse_left"], 12.5)
        self.assertEqual(back.alive["gb_collectible"], 9)

    def test_registry_empty_until_human_review(self):


        from evalsys.assertions.authored import REGISTRY, evaluate_authored

        self.assertEqual(REGISTRY, {})
        snap = replace(reference_snapshot(), project="3d_platformer")
        self.assertEqual(evaluate_authored(snap, task_id="3d_platformer"), [])


class OmittingTheManifestMustNotPay(unittest.TestCase):


    def _channels(self, snapshot: TruthSnapshot):
        from evalsys.ocard import channels as ch

        expected = extract(reference_snapshot())
        return (
            ch.score_o3_mechanic_fidelity(snapshot, expected, task_id="t"),
            ch.score_o4_level_topology(snapshot, expected),
            ch.score_o5_spatial_ordinal(snapshot, expected),
        )

    def test_a_project_declaring_no_levels_is_scored_not_excused(self):
        empty = TruthSnapshot(project="t", notes=("gb_levels.json is absent",))
        for result in self._channels(empty):
            self.assertGreater(
                result.interval.denominator, 0.0,
                f"{result.channel} left the denominator on a submission-side gap",
            )
            self.assertEqual(result.interval.lo, 0.0)

    def test_score_project_keeps_obligation_failures_in_the_denominator(self):

        from evalsys.pipeline import PassA, PassB, score_project

        empty = TruthSnapshot(project="missing_manifest")
        measured = PassA(truth=empty, expectations=extract(reference_snapshot()))
        with tempfile.TemporaryDirectory() as tmp:
            project = Path(tmp)
            (project / "project.godot").write_text("[application]\n", encoding="utf-8")
            with (
                patch("evalsys.pipeline.run_pass_a", return_value=measured),
                patch("evalsys.pipeline.run_pass_b", return_value=PassB()),
            ):
                card = score_project(
                    project,
                    submission="missing-manifest-control",
                    task_id="unregistered_control",
                    mode="H",
                )
        channels = {record.channel: record for record in card.channels}
        for channel in ("O3", "O4", "O5"):
            with self.subTest(channel=channel):
                self.assertGreater(channels[channel].interval.denominator, 0.0)
                self.assertEqual(channels[channel].interval.lo, 0.0)

    def test_the_reference_still_scores_full_marks(self):
        for result in self._channels(reference_snapshot()):
            self.assertGreater(result.interval.denominator, 0.0)
            self.assertEqual(result.interval.lo, 1.0, result.channel)

    def test_a_read_we_could_not_take_still_leaves_the_denominator(self):
        broken = TruthSnapshot(
            project="t", read_failure="the scratch copy could not be prepared: disk full"
        )
        for result in self._channels(broken):
            self.assertEqual(
                result.interval.denominator, 0.0,
                f"{result.channel} charged the submission for our own failure",
            )

    def test_u6_is_the_assertion_that_answers_it(self):

        empty = TruthSnapshot(project="t")
        ids = failing_ids(all_universal(empty))
        self.assertIn("U6", ids, "the level manifest is the interface; its absence is U6's answer")
        self.assertIn("U2", ids, "no bound actions is also a measured failure, not a gap")

    def test_omission_is_never_cheaper_than_declaring_and_failing(self):

        from evalsys.ocard import channels as ch

        expected = extract(reference_snapshot())
        omitted = TruthSnapshot(project="t")
        declared_but_wrong = replace(
            reference_snapshot(), levels=reference_snapshot().levels[:1]
        )
        omitted_lo = ch.score_o4_level_topology(omitted, expected).interval.lo
        wrong_lo = ch.score_o4_level_topology(declared_but_wrong, expected).interval.lo
        self.assertLessEqual(omitted_lo, wrong_lo)

    def test_read_failure_survives_the_round_trip(self):
        snap = TruthSnapshot(project="t", read_failure="engine would not start")
        self.assertEqual(
            TruthSnapshot.from_dict(snap.to_dict()).read_failure,
            "engine would not start",
        )
        self.assertFalse(TruthSnapshot.from_dict(snap.to_dict()).read_ok)


_AUTOLOAD_NAME = re.compile(r'^\s*(\w+)\s*=\s*"\*?res://', re.M)


def _autoload_names(text: str) -> list[str]:
    return _AUTOLOAD_NAME.findall(text)


def _driver_mapping_after_autoload_ready(
    order: list[str], numeric: dict[str, str]
) -> tuple[dict[str, str], list[str]]:


    probe_ready = False
    mapping: dict[str, str] = {}
    errors: list[str] = []
    for name in order:
        if name == "GBTruthProbe":
            probe_ready = True
        if name == "GBTruthDriver":
            if not probe_ready:
                errors.append("evaluator packaging error: ")
            else:
                mapping = dict(numeric)
    return mapping, errors


class TruthScratchAutoloadOrder(unittest.TestCase):


    DECLARED = {"progress": "distance_m", "score": "coins"}

    def _project_with_game_autoload(self, root: Path) -> Path:
        root.mkdir(parents=True, exist_ok=True)
        (root / "boot.gd").write_text("extends Node\n", encoding="utf-8")
        (root / "project.godot").write_text(
            '[application]\nconfig/name="t"\nrun/main_scene="res://level.tscn"\n\n'
            '[autoload]\n\nGameBoot="*res://boot.gd"\n',
            encoding="utf-8",
        )
        (root / "level.tscn").write_text("[gd_scene format=3]\n", encoding="utf-8")
        (root / "gb_levels.json").write_text(
            json.dumps(
                {
                    "levels": ["res://level.tscn"],
                    "endings": {
                        "victory": "res://win.tscn",
                        "defeat": "res://lose.tscn",
                    },
                    "numeric": dict(self.DECLARED),
                }
            ),
            encoding="utf-8",
        )
        return root

    def test_truth_prepare_puts_probe_ready_before_driver_reads_declared_numeric(self):
        from evalsys.probe.runner import DRIVER_AUTOLOAD, PROBE_AUTOLOAD, prepare

        with tempfile.TemporaryDirectory() as tmp:
            src = self._project_with_game_autoload(Path(tmp) / "game")
            dest = Path(tmp) / "scratch"
            prep = prepare(src, dest, do_import=False)
            self.assertIsNotNone(prep.scratch)
            self.assertTrue(prep.scratch.inject.ok, prep.scratch.inject.detail)
            self.assertTrue(prep.driver_inject.ok, prep.driver_inject.detail)
            names = _autoload_names((dest / "project.godot").read_text(encoding="utf-8"))
            self.assertIn(PROBE_AUTOLOAD, names)
            self.assertIn(DRIVER_AUTOLOAD, names)
            mapping, errors = _driver_mapping_after_autoload_ready(names, self.DECLARED)
            self.assertEqual(
                errors,
                [],
                "driver _ready ran before probe loaded the interface; "
                f"autoload order={names}",
            )
            self.assertEqual(
                mapping.get("progress"),
                "distance_m",
                "declared progress channel missing from the mapping the snapshot "
                f"would record; autoload order={names}",
            )
            self.assertEqual(
                mapping.get("score"),
                "coins",
                "declared score channel missing from the mapping the snapshot "
                f"would record; autoload order={names}",
            )
            self.assertLess(
                names.index(PROBE_AUTOLOAD),
                names.index(DRIVER_AUTOLOAD),
            )

    def test_inject_autoload_where_back_is_independent_of_call_order(self):
        from evalsys.probe.inject import inject_autoload

        with tempfile.TemporaryDirectory() as tmp:
            scripts = Path(tmp) / "scripts"
            scratch = Path(tmp) / "scratch"
            scripts.mkdir()
            scratch.mkdir()
            (scripts / "a.gd").write_text("extends Node\n", encoding="utf-8")
            (scripts / "b.gd").write_text("extends Node\n", encoding="utf-8")
            (scratch / "project.godot").write_text(
                '[application]\nconfig/name="t"\n\n[autoload]\n\n'
                'GameBoot="*res://boot.gd"\n',
                encoding="utf-8",
            )
            first = inject_autoload(
                scratch, scripts / "a.gd", "GBTruthDriver", where="back"
            )
            second = inject_autoload(
                scratch, scripts / "b.gd", "GBTruthProbe", where="front"
            )
            self.assertTrue(first.ok, first.detail)
            self.assertTrue(second.ok, second.detail)
            names = _autoload_names(
                (scratch / "project.godot").read_text(encoding="utf-8")
            )
            self.assertEqual(names[0], "GBTruthProbe")
            self.assertEqual(names[-1], "GBTruthDriver")
            self.assertIn("GameBoot", names)

    def test_default_where_front_still_prepends(self):

        from evalsys.probe.inject import inject_autoload

        with tempfile.TemporaryDirectory() as tmp:
            scripts = Path(tmp) / "scripts"
            scratch = Path(tmp) / "scratch"
            scripts.mkdir()
            scratch.mkdir()
            (scripts / "driver.gd").write_text("extends Node\n", encoding="utf-8")
            (scripts / "probe.gd").write_text("extends Node\n", encoding="utf-8")
            (scratch / "project.godot").write_text(
                '[application]\nconfig/name="t"\n',
                encoding="utf-8",
            )
            driver = inject_autoload(scratch, scripts / "driver.gd", "GBRouteDriver")
            probe = inject_autoload(scratch, scripts / "probe.gd", "GBHarnessProbe")
            self.assertTrue(driver.ok, driver.detail)
            self.assertTrue(probe.ok, probe.detail)
            names = _autoload_names(
                (scratch / "project.godot").read_text(encoding="utf-8")
            )
            self.assertEqual(names[0], "GBHarnessProbe")
            self.assertEqual(names[1], "GBRouteDriver")
