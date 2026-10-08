from __future__ import annotations

import json
import unittest
from pathlib import Path
from types import SimpleNamespace

from evalsys.taskgen.scorecard import (
    BRIEF_NOT_APPLICABLE,
    CALIB4_REGISTRY_VERSION,
    CALIB_REGISTRY_VERSION,
    BUGFIX_NOT_APPLICABLE,
    GRADED_CHANNEL_EXPONENT,
    MODE34_REGISTRY_VERSION,
    MODE34B_REGISTRY_VERSION,
    DEFAULT_REGISTRY_BY_MODE,
    default_registry_for_mode,
    EVIDENCE_REGISTRY_VERSION,
    VISUAL_REGISTRY_VERSION,
    MODE1_REDESIGN_REGISTRY_VERSION,
    MODE2_REDESIGN_REGISTRY_VERSION,
    MODE3_REDESIGN_REGISTRY_VERSION,
    MODE1_VLM_REGISTRY_VERSION,
    MODE2_VLM_REGISTRY_VERSION,
    MODE3_VLM_REGISTRY_VERSION,
    MODE4_F2P_P2P_REGISTRY_VERSION,
    MODE4_REDESIGN_REGISTRY_VERSION,
    MODE4_GATES,
    MODE4_REGISTRY_VERSION,
    MODE5_REGISTRY_VERSION,
    MODE5_DELIVERY_ZERO_REGISTRY_VERSION,
    MODE5_MDVA_REGISTRY_VERSION,
    MODE5_SCORECARD_SCHEMA,
    MODE4_REGRESSION_WEIGHTS,
    MODE4_REPAIR_CREDIT,
    SKELETON_NOOP_HEADLINE_CAP,
    SKELETON_NOT_APPLICABLE,
    PILOT_REGISTRY_VERSION,
    REGISTRY_VERSION,
    REGISTRY_VERSIONS,
    SCORECARD_SCHEMA,
    _category_specs,
    brief_layout_constraint,
    headline_rule,
    registry_policy,
    score_task_result,
)
from evalsys.verdict import Attribution, Interval, Item, failed, inconclusive, passed, skipped, unobservable

FIXTURES = Path(__file__).parent / "fixtures" / "scoring34"


def _channel(channel: str, score: float = 1.0) -> dict:
    return {
        "channel": channel,
        "name": channel,
        "lo": score,
        "hi": score,
        "coverage": 1.0,
        "denominator": 1.0,
        "by_verdict": {"passed": 1.0},
    }


def _result(*, calibrated: bool = False, resolved: bool = True, scard: float = 0.8):
    item_ids = {
        "layout",
        "interface",
        "ops_present",
        "ops_valid",
        "ops_not_idle",
        "anti_grant_static",
        "auto_win_ready",
        "health_writable",
        "verifier_profile_complete",
        "task_gdd_contract",
        "gdd_mechanics_observable",
        "rubric_interface",
        "mechanic_trace",
        "causal_witness",
        "null_no_win",
        "extended_mash_no_win",
        "anti_grant_diff",
    }
    card = {
        "channels": [_channel(f"O{i}") for i in range(1, 10)],
        "scard_state": "calibrated" if calibrated else "uncalibrated",
        "scard": {
            "lo": scard,
            "hi": scard,
            "coverage": 1.0,
            "denominator": 1.0,
            "by_verdict": {"passed": 1.0},
        },
    }
    items = [passed(item_id) for item_id in sorted(item_ids)]
    items.append(passed("reproduction", evidence={"card": card}))
    return SimpleNamespace(
        package=SimpleNamespace(manifest={"mode": "gdd", "game_id": "fixture"}),
        items=items,
        resolved=resolved,
    )


def _channel_row(result, channel: str) -> dict:
    reproduction = next(item for item in result.items if item.id == "reproduction")
    return next(
        row for row in reproduction.evidence["card"]["channels"] if row["channel"] == channel
    )


def _category(card, category_id: str) -> dict:
    return next(row for row in card["categories"] if row["id"] == category_id)


def _criterion(card, category_id: str, criterion_id: str) -> dict:
    return next(row for row in _category(card, category_id)["criteria"] if row["id"] == criterion_id)


def score_pilot2(result):

    return score_task_result(result, registry_version=PILOT_REGISTRY_VERSION)


class TaskgenScorecardTests(unittest.TestCase):


    def test_port_probe_and_capture_do_not_award_quality_points(self) -> None:
        ids = {
            "unity_layout", "unity_build", "unity_interface", "ops_present", "ops_valid",
            "ops_not_idle", "no_eval_smuggling", "no_bundled_godot_runtime",
            "verifier_profile_complete", "causal_witness", "null_no_win",
            "unity_auto_win_ready", "task_gdd_contract", "port_contract_alignment",
            "build_recipe",
            "unity_sdk_integrity", "unity_anti_grant_static", "unity_input_dispatch",
            "unity_source_behavior",
        }
        items = [passed(item_id) for item_id in ids]
        items.extend([
            passed("unity_probe"),
            passed("unity_evaluator_capture"),
            passed("unity_mechanic_trace", credit=1 / 3),
            passed("unity_runtime_stability"),
            unobservable("unity_hidden_behavior"),
            unobservable("unity_counterfactual"),
            unobservable("unity_structure_fidelity"),
            unobservable("unity_visual_fidelity"),
            unobservable("cross_engine_fidelity"),
        ])
        result = SimpleNamespace(
            package=SimpleNamespace(manifest={"mode": "port", "game_id": "fixture"}),
            items=items,
            resolved=True,
        )
        card = score_task_result(result)
        mechanics = _category(card, "core_mechanics")
        self.assertEqual(33.333, mechanics["score"]["score"])
        self.assertEqual(MODE5_SCORECARD_SCHEMA, card["schema"])
        self.assertEqual(MODE5_MDVA_REGISTRY_VERSION, card["registry_version"])
        self.assertIsNone(card["weighted_total"]["score"])
        self.assertEqual("evaluation_incomplete", card["weighted_total"]["status"])
        self.assertEqual(0.55, card["measured_weight_share"])
        self.assertEqual({"lo": 31.667, "hi": 76.667, "scale": "0-100"},
                         card["diagnostics"]["fixed_weight_bounds"])
        self.assertIn("measured_subset_rate", card["diagnostics"])
        self.assertNotIn("measured_only_score", card["diagnostics"])
        self.assertFalse(card["ranking_eligible"])
        self.assertEqual("evaluation_incomplete", card["outcome_status"])

    def test_complete_mode5_card_scores_only_five_capability_categories(self) -> None:
        gates = {
            "task_gdd_contract", "verifier_profile_complete", "unity_layout",
            "unity_interface", "unity_sdk_integrity", "port_contract_alignment",
            "ops_present", "ops_valid", "ops_not_idle", "no_eval_smuggling",
            "no_bundled_godot_runtime", "build_recipe", "unity_anti_grant_static",
            "unity_build", "unity_probe", "unity_input_dispatch",
            "unity_auto_win_ready", "null_no_win", "unity_counterfactual",
            "unity_source_behavior",
        }
        items = [passed(item_id) for item_id in gates]
        items.extend([
            passed("unity_mechanic_trace", credit=0.8),
            passed("causal_witness"),
            passed("unity_hidden_behavior", credit=0.5),
            passed("unity_structure_fidelity", credit=0.6),
            passed("unity_vlm", credit=0.4),
            passed("unity_runtime_stability", credit=0.75),
        ])
        result = SimpleNamespace(
            package=SimpleNamespace(manifest={"mode": "port", "game_id": "fixture"}),
            items=items,
            resolved=False,
        )
        card = score_task_result(result)
        self.assertEqual(68.0, card["weighted_total"]["score"])
        self.assertEqual("scored", card["outcome_status"])
        self.assertEqual(1.0, card["measured_weight_share"])
        self.assertTrue(card["ranking_eligible"])
        self.assertFalse(card["functional_complete"])
        self.assertEqual(
            {"core_mechanics", "playability_progression", "content_structure",
             "visual_feedback", "stability_lifecycle"},
            {row["id"] for row in card["categories"]},
        )

    def test_mode5_build_failure_is_a_candidate_zero_not_an_evaluator_hole(self) -> None:
        items = [passed(item_id) for item_id in {
            "task_gdd_contract", "verifier_profile_complete", "unity_layout",
            "unity_interface", "unity_sdk_integrity", "port_contract_alignment",
            "ops_present", "ops_valid", "ops_not_idle", "no_eval_smuggling",
            "no_bundled_godot_runtime", "build_recipe", "unity_anti_grant_static",
            "unity_probe", "unity_input_dispatch", "unity_auto_win_ready",
            "null_no_win", "unity_counterfactual", "unity_source_behavior",
            "unity_runtime_stability", "unity_hidden_behavior",
            "unity_structure_fidelity", "unity_vlm",
        }]
        items.append(failed("unity_build"))
        items.extend([
            inconclusive("unity_probe", detail="Unity build did not produce a runnable player"),
            inconclusive("unity_mechanic_trace", detail="Unity build did not produce a runnable player"),
            inconclusive("unity_runtime_stability", detail="Unity build did not produce a runnable player"),
            inconclusive("unity_auto_win_ready", detail="Unity build did not produce a runnable player"),
            inconclusive("unity_input_dispatch", detail="Unity build did not produce a runnable player"),
            inconclusive("unity_hidden_behavior", detail="Unity build did not produce a runnable player"),
            inconclusive("unity_source_behavior", detail="Unity build did not produce a runnable player"),
            inconclusive("unity_counterfactual", detail="Unity build did not produce a runnable player"),
            inconclusive("causal_witness", detail="Unity build did not produce a runnable player"),
            inconclusive("null_no_win", detail="Unity build did not produce a runnable player"),
            inconclusive("unity_evaluator_capture", detail="Unity build did not produce a runnable player"),
            inconclusive("unity_structure_fidelity", detail="Unity build did not produce a runnable player"),
            inconclusive("unity_vlm", detail="Unity build did not produce a runnable player"),
        ])
        result = SimpleNamespace(
            package=SimpleNamespace(manifest={"mode": "port", "game_id": "fixture"}),
            items=items,
            resolved=False,
        )
        card = score_task_result(result)
        self.assertEqual(0.0, card["weighted_total"]["score"])
        self.assertEqual("candidate_delivery_failure", card["outcome_status"])
        self.assertTrue(card["ranking_eligible"])
        assert card["leaderboard"]["status"] == "complete"

    def test_six_categories_are_each_reported_on_a_100_point_scale(self) -> None:
        card = score_pilot2(_result(calibrated=True))
        self.assertEqual(SCORECARD_SCHEMA, card["schema"])
        self.assertEqual(PILOT_REGISTRY_VERSION, card["registry_version"])
        self.assertEqual(6, len(card["categories"]))
        self.assertEqual(100.0, sum(row["weight_in_total"] for row in card["categories"]))
        for row in card["categories"]:
            self.assertEqual("0-100", row["score"]["scale"])
            self.assertEqual("complete", row["score"]["status"])
            self.assertGreaterEqual(row["score"]["score"], 0.0)
            self.assertLessEqual(row["score"]["score"], 100.0)
        self.assertEqual(98.75, card["weighted_total"]["score"])
        self.assertEqual("complete", card["weighted_total"]["status"])
        self.assertEqual(100.0, card["weighted_total"]["headline_ceiling"])
        self.assertFalse(card["evaluation_incomplete"])
        self.assertTrue(card["ranking_eligible"])

    def test_old_registry_stays_loadable_and_unknown_versions_are_refused(self) -> None:


        self.assertEqual(EVIDENCE_REGISTRY_VERSION, REGISTRY_VERSION)
        self.assertEqual(
            (PILOT_REGISTRY_VERSION, CALIB_REGISTRY_VERSION, CALIB4_REGISTRY_VERSION,
             MODE4_REGISTRY_VERSION, MODE34_REGISTRY_VERSION, MODE34B_REGISTRY_VERSION,
             EVIDENCE_REGISTRY_VERSION, VISUAL_REGISTRY_VERSION,
             MODE1_REDESIGN_REGISTRY_VERSION, MODE2_REDESIGN_REGISTRY_VERSION,
             MODE3_REDESIGN_REGISTRY_VERSION,
             MODE1_VLM_REGISTRY_VERSION, MODE2_VLM_REGISTRY_VERSION, MODE3_VLM_REGISTRY_VERSION,
             MODE4_REDESIGN_REGISTRY_VERSION,
             MODE4_F2P_P2P_REGISTRY_VERSION,
             MODE5_REGISTRY_VERSION, MODE5_DELIVERY_ZERO_REGISTRY_VERSION,
             MODE5_MDVA_REGISTRY_VERSION),
            REGISTRY_VERSIONS,
        )


        self.assertEqual(
            {
                "brief": MODE1_VLM_REGISTRY_VERSION,
                "gdd": MODE2_VLM_REGISTRY_VERSION,
                "skeleton": MODE3_VLM_REGISTRY_VERSION,
                "bugfix": MODE4_REDESIGN_REGISTRY_VERSION,
                "port": MODE5_MDVA_REGISTRY_VERSION,
            },
            DEFAULT_REGISTRY_BY_MODE,
        )
        self.assertEqual(REGISTRY_VERSION, default_registry_for_mode("unknown-mode"))
        default = score_task_result(_result(calibrated=True))
        self.assertEqual(MODE2_VLM_REGISTRY_VERSION, default["registry_version"])


        self.assertEqual(
            EVIDENCE_REGISTRY_VERSION,
            score_task_result(
                _result(calibrated=True), EVIDENCE_REGISTRY_VERSION,
            )["registry_version"],
        )
        mode34 = score_task_result(_result(calibrated=True), registry_version=MODE34_REGISTRY_VERSION)
        self.assertEqual(MODE34_REGISTRY_VERSION, mode34["registry_version"])
        self.assertEqual(
            "mode4_rules_plus_mode34_applicability_not_blind", mode34["registry_status"],
        )
        calib3 = score_task_result(_result(calibrated=True), registry_version=CALIB_REGISTRY_VERSION)
        self.assertEqual(CALIB_REGISTRY_VERSION, calib3["registry_version"])
        self.assertEqual("calibrated_on_live0903_not_blind", calib3["registry_status"])
        old = score_task_result(_result(calibrated=True), registry_version=PILOT_REGISTRY_VERSION)
        self.assertEqual(PILOT_REGISTRY_VERSION, old["registry_version"])
        self.assertEqual("pilot_not_blind_formal_weights", old["registry_status"])
        self.assertEqual(98.75, old["weighted_total"]["score"])
        with self.assertRaises(ValueError):
            score_task_result(_result(), registry_version="2020-01-01.nope")

    def test_uncalibrated_scard_is_visible_and_ranking_is_objective_only(self) -> None:


        card = score_pilot2(_result(calibrated=False))
        visual = next(
            row for row in card["categories"]
            if row["id"] == "visual_experience"
        )
        surface = next(
            row for row in visual["criteria"]
            if row["id"] == "calibrated_surface"
        )
        self.assertEqual("uncalibrated", surface["sources"][0]["status"])
        self.assertFalse(surface["applicable"])
        self.assertEqual(1.0, visual["measured_weight_share"])
        self.assertEqual(37.5, visual["applicable_weight_within_category"])
        self.assertTrue(card["ranking_eligible"])
        self.assertEqual("objective_only", card["ranking_basis"])
        self.assertIn("visual_experience/calibrated_surface", card["not_applicable_criteria"])
        self.assertEqual(100.0, visual["score"]["score"])

    def test_unobservable_channel_leaves_the_denominator_but_a_hole_withholds_the_headline(self) -> None:
        result = _result(calibrated=True)
        _channel_row(result, "O6").update(
            lo=0.0, hi=0.0, coverage=0.0, denominator=0.0, by_verdict={"unobservable": 1.0}
        )
        _channel_row(result, "O4").update(
            lo=0.0, hi=0.0, coverage=0.0, denominator=0.0, by_verdict={"unmeasurable": 1.0},
            note="level probe crashed",
        )
        card = score_pilot2(result)
        design = next(row for row in card["categories"] if row["id"] == "structure_content")
        by_id = {row["id"]: row for row in design["criteria"]}
        self.assertFalse(by_id["asset_realization"]["applicable"])
        self.assertTrue(by_id["topology_progression"]["applicable"])


        self.assertEqual(65.0, design["applicable_weight_within_category"])
        self.assertAlmostEqual(30 / 65, design["measured_weight_share"], places=6)
        self.assertAlmostEqual(30 / 65 * 100, design["interval"]["lo"], places=2)
        self.assertEqual(100.0, design["interval"]["hi"])
        self.assertIsNone(design["score"]["score"])
        self.assertEqual("evaluation_incomplete", design["score"]["status"])
        self.assertIsNone(card["weighted_total"]["score"])
        self.assertEqual("evaluation_incomplete", card["weighted_total"]["status"])
        self.assertTrue(card["evaluation_incomplete"])
        self.assertFalse(card["ranking_eligible"])
        unmeasured = card["weighted_total"]["unmeasured"]
        self.assertEqual(1, len(unmeasured))
        self.assertEqual("ocard:O4", unmeasured[0]["source"])
        self.assertIn("level probe crashed", unmeasured[0]["step"])
        self.assertNotIn("lo", card["weighted_total"])
        self.assertIn("lo", card["diagnostic"]["weighted_total_interval"])

    def test_a_stored_o10_row_changes_no_headline_under_either_registry(self) -> None:


        for scorer in (score_task_result, score_pilot2):
            baseline = scorer(_result(calibrated=True))
            result = _result(calibrated=True)
            reproduction = next(item for item in result.items if item.id == "reproduction")
            reproduction.evidence["card"]["channels"].append(
                _ocard_row("O10", 0.0, unobservable_w=1.0)
            )
            with_o10 = scorer(result)
            self.assertEqual(baseline["weighted_total"], with_o10["weighted_total"])
            self.assertEqual(baseline["categories"], with_o10["categories"])
        for version in REGISTRY_VERSIONS:
            modes = ("port",) if version in {
                MODE5_REGISTRY_VERSION, MODE5_DELIVERY_ZERO_REGISTRY_VERSION,
                MODE5_MDVA_REGISTRY_VERSION,
            } else (
                "brief", "gdd", "skeleton", "bugfix", "port"
            )
            for mode in modes:
                sources = {
                    source.id
                    for category in _category_specs(mode, version)
                    for criterion in category.criteria
                    for source in criterion.sources
                    if source.kind == "ocard"
                }
                self.assertNotIn("O10", sources, (version, mode))
                self.assertTrue(sources <= {f"O{i}" for i in range(1, 10)}, (version, mode))

    def test_strict_failure_is_not_hidden_by_a_high_weighted_total(self) -> None:
        result = _result(calibrated=True, resolved=False)
        result.items = [
            inconclusive("causal_witness", detail="witness launch_failed")
            if item.id == "causal_witness" else item
            for item in result.items
        ]
        card = score_task_result(result, registry_version=EVIDENCE_REGISTRY_VERSION)
        self.assertEqual("failed", card["strict"]["status"])
        self.assertIn("causal_witness", card["strict"]["not_measured_required_items"])


        self.assertIsNone(card["weighted_total"]["score"])
        self.assertGreater(card["diagnostic"]["weighted_total_interval"]["lo"], 80.0)
        steps = [entry["step"] for entry in card["weighted_total"]["unmeasured"]]
        self.assertTrue(any("causal_witness" in step and "launch_failed" in step for step in steps))


class NoIntervalHeadlineTests(unittest.TestCase):


    def test_exempt_item_is_a_point_in_the_headline_and_a_width_in_the_diagnostic(self) -> None:

        interval = Interval(5 / 7, 6 / 7, 1.0, 7.0,
                            {"passed": 5.0, "failed": 1.0, "exempt": 1.0, "unobservable": 1.0})
        self.assertAlmostEqual(5 / 6, interval.point, places=9)
        self.assertEqual(1.0, interval.exempt_weight)

        result = _result(calibrated=True)
        _channel_row(result, "O3").update(
            lo=round(5 / 7, 6), hi=round(6 / 7, 6), denominator=7.0,
            by_verdict={"passed": 5.0, "failed": 1.0, "exempt": 1.0, "unobservable": 1.0},
        )
        card = score_pilot2(result)
        self.assertEqual("complete", card["weighted_total"]["status"])
        mechanics = next(row for row in card["categories"] if row["id"] == "mechanics_requirements")
        deterministic = next(row for row in mechanics["criteria"] if row["id"] == "deterministic_mechanics")
        source = deterministic["sources"][0]
        self.assertAlmostEqual(5 / 6 * 100, source["point"], places=2)
        self.assertEqual(1.0, source["exempt_weight"])
        self.assertAlmostEqual(5 / 7 * 100, source["interval"]["lo"], places=2)
        self.assertAlmostEqual(6 / 7 * 100, source["interval"]["hi"], places=2)

        self.assertAlmostEqual(98.75 - (1 / 6) * 0.6 * 25, card["weighted_total"]["score"], places=2)
        self.assertGreater(card["diagnostic"]["weighted_total_interval"]["width"], 0.0)
        self.assertIn(
            "mechanics_requirements/deterministic_mechanics/O3",
            card["diagnostic"]["exempt_removed_from_headline"],
        )
        self.assertTrue(card["ranking_eligible"])

    def test_skipped_items_read_pessimistically_as_a_point(self) -> None:

        interval = Interval(0.5, 1.0, 0.5, 2.0, {"passed": 1.0, "skipped": 1.0})
        self.assertEqual(0.5, interval.point)
        result = _result(calibrated=True)
        _channel_row(result, "O7").update(lo=0.5, hi=1.0, coverage=0.5, denominator=2.0,
                                          by_verdict={"passed": 1.0, "skipped": 1.0})
        card = score_pilot2(result)
        self.assertEqual("complete", card["weighted_total"]["status"])
        causal = next(row for row in card["categories"] if row["id"] == "causal_playability")
        route = next(row for row in causal["criteria"] if row["id"] == "end_to_end_route")
        self.assertEqual(50.0, route["sources"][0]["point"])
        self.assertEqual(50.0, route["score"]["score"])

        route3 = _criterion(score_task_result(result, registry_version=EVIDENCE_REGISTRY_VERSION), "causal_playability", "end_to_end_route")
        self.assertEqual(50.0, route3["sources"][0]["point"])
        self.assertEqual(25.0, route3["sources"][0]["headline_credit"])
        self.assertEqual(25.0, route3["score"]["score"])

    def test_all_exempt_channel_is_not_applicable_rather_than_a_hole(self) -> None:
        self.assertIsNone(Interval(0.0, 1.0, 1.0, 1.0, {"exempt": 1.0}).point)
        result = _result(calibrated=True)
        _channel_row(result, "O5").update(lo=0.0, hi=1.0, denominator=1.0,
                                          by_verdict={"exempt": 1.0})
        card = score_task_result(result, registry_version=EVIDENCE_REGISTRY_VERSION)
        self.assertEqual("complete", card["weighted_total"]["status"])
        self.assertIn("structure_content/spatial_relations", card["not_applicable_criteria"])

    def test_a_partially_read_channel_withholds_the_headline_and_names_the_rung(self) -> None:


        result = _result(calibrated=True)
        _channel_row(result, "O1").update(
            lo=1.0, hi=1.0, coverage=1.0, denominator=0.5,
            by_verdict={"passed": 0.5, "inconclusive": 0.5},
            note="cold_import=passed; boots=passed; draws_nontrivial=inconclusive; "
                 "responds_to_input=inconclusive",
        )
        card = score_task_result(result, registry_version=EVIDENCE_REGISTRY_VERSION)
        self.assertEqual("evaluation_incomplete", card["weighted_total"]["status"])
        self.assertIsNone(card["weighted_total"]["score"])
        self.assertFalse(card["ranking_eligible"])
        unmeasured = card["weighted_total"]["unmeasured"]
        self.assertEqual(["ocard:O1"], [entry["source"] for entry in unmeasured])
        self.assertIn("partial reading", unmeasured[0]["step"])
        self.assertIn("inconclusive weight 0.5", unmeasured[0]["step"])
        self.assertIn("draws_nontrivial=inconclusive", unmeasured[0]["step"])
        observability = next(row for row in card["categories"] if row["id"] == "artifact_observability")
        runnable = next(row for row in observability["criteria"] if row["id"] == "runtime_viability")
        self.assertEqual("partial", runnable["sources"][0]["status"])
        self.assertEqual(0.5, runnable["sources"][0]["inconclusive_weight"])
        self.assertTrue(runnable["applicable"])

        self.assertEqual(100.0, runnable["sources"][0]["interval"]["lo"])


        clean = _result(calibrated=True)
        _channel_row(clean, "O1").update(
            lo=0.5, hi=1.0, coverage=0.5, denominator=1.0,
            by_verdict={"passed": 0.5, "skipped": 0.5},
        )
        self.assertEqual("complete", score_task_result(clean, registry_version=EVIDENCE_REGISTRY_VERSION)["weighted_total"]["status"])

    def test_missing_reproduction_card_names_the_failed_step_per_channel(self) -> None:
        result = _result(calibrated=True)
        result.items = [
            inconclusive("reproduction", detail="evaluate error: import timed out")
            if item.id == "reproduction" else item
            for item in result.items
        ]
        card = score_task_result(result, registry_version=EVIDENCE_REGISTRY_VERSION)
        self.assertEqual("evaluation_incomplete", card["weighted_total"]["status"])
        self.assertFalse(card["ranking_eligible"])
        sources = {entry["source"] for entry in card["weighted_total"]["unmeasured"]}
        self.assertIn("ocard:O1", sources)
        self.assertIn("ocard:O3", sources)
        steps = {entry["step"] for entry in card["weighted_total"]["unmeasured"]}
        self.assertTrue(all("import timed out" in step for step in steps if step.startswith("O")))
        self.assertIn("evaluation_incomplete", card["weighted_total"]["detail"])
        self.assertIsNone(card["weighted_total"]["score"])


def _ocard_row(channel: str, passed_w: float, failed_w: float = 0.0, exempt_w: float = 0.0,
               unobservable_w: float = 0.0) -> dict:


    denominator = passed_w + failed_w + exempt_w
    by_verdict = {}
    for key, value in (("passed", passed_w), ("failed", failed_w), ("exempt", exempt_w),
                       ("unobservable", unobservable_w)):
        if value:
            by_verdict[key] = value
    if denominator <= 0:
        return {"channel": channel, "name": channel, "lo": 0.0, "hi": 0.0, "coverage": 0.0,
                "denominator": 0.0, "by_verdict": by_verdict or {"unobservable": 1.0}}
    return {
        "channel": channel, "name": channel,
        "lo": passed_w / denominator, "hi": (passed_w + exempt_w) / denominator,
        "coverage": 1.0, "denominator": denominator, "by_verdict": by_verdict,
    }


def _live_cell(mode: str, channels: dict, *, failed_items=(), resolved: bool = True):


    common = [
        "layout", "interface", "ops_present", "ops_valid", "ops_not_idle",
        "anti_grant_static", "auto_win_ready", "health_writable", "verifier_profile_complete",
        "rubric_interface", "mechanic_trace", "causal_witness", "null_no_win", "anti_grant_diff",
    ]
    mode_items = {
        "gdd": ["task_gdd_contract", "gdd_mechanics_observable"],
        "brief": ["gdd", "authored_gdd_quality", "authored_gdd_interface", "brief_gdd_grounding"],
    }[mode]
    items = [
        failed(item_id) if item_id in failed_items else passed(item_id)
        for item_id in common + mode_items
    ]
    items.append(unobservable("extended_mash_no_win"))
    rows = [channels.get(f"O{i}") or _ocard_row(f"O{i}", 1.0) for i in range(1, 8)]
    rows += [_ocard_row(f"O{i}", 0.0, unobservable_w=1.0) for i in (8, 9)]
    card = {"channels": rows, "scard_state": "absent", "scard": None}
    items.append(passed("reproduction", evidence={"card": card}))
    return SimpleNamespace(
        package=SimpleNamespace(manifest={"mode": mode, "game_id": "fixture"}),
        items=items,
        resolved=resolved,
    )


LIVE_POST_FIX_CELLS = (
    ("arc_wing/gdd/claude", 71.51, _live_cell("gdd", {
        "O3": _ocard_row("O3", 6, 0, 1, 1), "O6": _ocard_row("O6", 7, 214),
        "O7": _ocard_row("O7", 0, 1),
    })),
    ("cat_defense/gdd/claude", 68.30, _live_cell("gdd", {
        "O3": _ocard_row("O3", 5, 1, 1, 1), "O5": _ocard_row("O5", 2, 1),
        "O6": _ocard_row("O6", 0, 8), "O7": _ocard_row("O7", 0.8, 0.2),
    })),
    ("cat_defense/gdd/codex", 63.80, _live_cell("gdd", {
        "O3": _ocard_row("O3", 5, 1, 1, 1), "O5": _ocard_row("O5", 2, 1),
        "O6": _ocard_row("O6", 0, 8), "O7": _ocard_row("O7", 0.2, 0.8),
    })),
    ("canopy_dash/brief/codex", 62.62, _live_cell("brief", {
        "O3": _ocard_row("O3", 5, 1, 2), "O5": _ocard_row("O5", 1, 1),
        "O6": _ocard_row("O6", 0, 3), "O7": _ocard_row("O7", 0, 1),
    })),
    ("canopy_dash/brief/claude", 53.75, _live_cell("brief", {
        "O1": _ocard_row("O1", 0.75, 0.25), "O3": _ocard_row("O3", 4, 2, 2),
        "O4": _ocard_row("O4", 0, 1), "O6": _ocard_row("O6", 0, 3), "O7": _ocard_row("O7", 0, 1),
    }, failed_items=("authored_gdd_quality",), resolved=False)),
    ("arc_wing/gdd/codex", 41.50, _live_cell("gdd", {
        "O3": _ocard_row("O3", 5, 1, 1, 1), "O6": _ocard_row("O6", 1, 220),
        "O7": _ocard_row("O7", 0, 1),
    }, failed_items=("mechanic_trace", "causal_witness"), resolved=False)),
)


class CalibratedRegistryTests(unittest.TestCase):


    def test_weights_sum_to_100_in_every_mode_and_version(self) -> None:
        for version in REGISTRY_VERSIONS:
            modes = ("port",) if version in {
                MODE5_REGISTRY_VERSION, MODE5_DELIVERY_ZERO_REGISTRY_VERSION,
                MODE5_MDVA_REGISTRY_VERSION,
            } else (
                "brief", "gdd", "skeleton", "bugfix", "port"
            )
            for mode in modes:
                specs = _category_specs(mode, version)
                self.assertEqual(5 if version in {
                    MODE5_REGISTRY_VERSION, MODE5_DELIVERY_ZERO_REGISTRY_VERSION,
                    MODE5_MDVA_REGISTRY_VERSION,
                } else 6,
                                 len(specs), (version, mode))
                self.assertAlmostEqual(100.0, sum(spec.weight for spec in specs), msg=(version, mode))

        for mode in ("brief", "gdd", "skeleton"):
            for spec in _category_specs(mode, CALIB_REGISTRY_VERSION):
                self.assertAlmostEqual(
                    100.0, sum(criterion.weight for criterion in spec.criteria), msg=(mode, spec.id),
                )
        by_id = {spec.id: spec for spec in _category_specs("gdd", CALIB_REGISTRY_VERSION)}
        self.assertEqual(
            {"artifact_observability": 10, "mechanics_requirements": 30, "causal_playability": 25,
             "structure_content": 15, "visual_experience": 15, "mode_specific": 5},
            {cid: spec.weight for cid, spec in by_id.items()},
        )
        causal = {c.id: c for c in by_id["causal_playability"].criteria}
        self.assertNotIn("external_play", causal)
        self.assertEqual(50, causal["verified_witness"].weight)
        self.assertEqual(30, causal["end_to_end_route"].weight)
        graded = {
            ("artifact_observability", "runtime_viability"),
            ("mechanics_requirements", "deterministic_mechanics"),
            ("causal_playability", "end_to_end_route"),
            ("structure_content", "topology_progression"),
            ("structure_content", "spatial_relations"),
            ("structure_content", "asset_realization"),
        }
        for spec in by_id.values():
            for criterion in spec.criteria:
                expected = GRADED_CHANNEL_EXPONENT if (spec.id, criterion.id) in graded else 1.0
                self.assertEqual(expected, criterion.credit_exponent, (spec.id, criterion.id))
        for spec in _category_specs("gdd", PILOT_REGISTRY_VERSION):
            for criterion in spec.criteria:
                self.assertEqual(1.0, criterion.credit_exponent)

    def test_six_live_cells_order_as_the_fault_analysis_and_land_in_the_band(self) -> None:


        scores = []
        for name, expected, result in LIVE_POST_FIX_CELLS:
            card = score_task_result(result, registry_version=CALIB_REGISTRY_VERSION)
            self.assertEqual("complete", card["weighted_total"]["status"], name)
            self.assertTrue(card["ranking_eligible"], name)
            self.assertEqual(85.0, card["weighted_total"]["headline_ceiling"], name)
            self.assertEqual("objective_only", card["ranking_basis"], name)
            score = card["weighted_total"]["score"]
            self.assertAlmostEqual(expected, score, delta=0.02, msg=name)
            scores.append((name, score, card["strict"]["resolved"]))

        for (name_a, a, _), (name_b, b, _) in zip(scores, scores[1:]):
            self.assertGreater(a, b + 1.0, (name_a, name_b))
        self.assertTrue(41 <= scores[-1][1] <= 72 and scores[0][1] <= 72)
        self.assertEqual([True, True, True, True, False, False], [r for _, _, r in scores])

        pilot = [score_pilot2(result)["weighted_total"]["score"] for _, _, result in LIVE_POST_FIX_CELLS]
        self.assertGreater(pilot[1], pilot[0])
        self.assertAlmostEqual(88.18, pilot[0], delta=0.02)
        self.assertAlmostEqual(88.49, pilot[1], delta=0.02)

    def test_perfect_card_reaches_the_ceiling_without_and_90_plus_with_a_calibrated_scard(self) -> None:

        objective_only = score_task_result(_live_cell("gdd", {}), registry_version=EVIDENCE_REGISTRY_VERSION)
        self.assertEqual(85.0, objective_only["weighted_total"]["score"])
        self.assertEqual(85.0, objective_only["weighted_total"]["headline_ceiling"])
        self.assertEqual(["visual_experience/calibrated_surface"], objective_only["weighted_total"]["unearned"])
        visual = _category(objective_only, "visual_experience")
        self.assertEqual("unearned", visual["score"]["status"])
        self.assertEqual(0.0, visual["score"]["score"])
        self.assertFalse(visual["applicable"])
        self.assertEqual(1.0, visual["unearned_weight_share"])

        self.assertTrue(objective_only["ranking_eligible"])
        self.assertEqual("objective_only", objective_only["ranking_basis"])
        self.assertIn("visual_experience/calibrated_surface", objective_only["not_applicable_criteria"])

        self.assertEqual(100.0, objective_only["diagnostic"]["weighted_total_interval"]["hi"])
        self.assertAlmostEqual(85.0, objective_only["diagnostic"]["weighted_total_interval"]["lo"], places=2)

        with_o9 = score_task_result(_result(calibrated=False), registry_version=EVIDENCE_REGISTRY_VERSION)
        self.assertEqual(88.75, with_o9["weighted_total"]["score"])
        self.assertEqual(88.75, with_o9["weighted_total"]["headline_ceiling"])
        self.assertEqual(25.0, _category(with_o9, "visual_experience")["score"]["score"])
        self.assertEqual(0.75, _category(with_o9, "visual_experience")["unearned_weight_share"])

        calibrated = score_task_result(_result(calibrated=True, scard=0.8), registry_version=EVIDENCE_REGISTRY_VERSION)
        self.assertEqual(100.0, calibrated["weighted_total"]["headline_ceiling"])
        self.assertNotIn("unearned", calibrated["weighted_total"])
        self.assertEqual(97.75, calibrated["weighted_total"]["score"])
        self.assertEqual("objective_and_calibrated_perceptual", calibrated["ranking_basis"])
        self.assertEqual(100.0, score_task_result(_result(calibrated=True, scard=1.0), registry_version=EVIDENCE_REGISTRY_VERSION)["weighted_total"]["score"])

    def test_graded_channels_enter_squared_and_items_stay_linear(self) -> None:
        result = _live_cell("gdd", {"O3": _ocard_row("O3", 5, 1, 1, 1)})
        card = score_task_result(result, registry_version=EVIDENCE_REGISTRY_VERSION)
        deterministic = _criterion(card, "mechanics_requirements", "deterministic_mechanics")
        source = deterministic["sources"][0]
        self.assertAlmostEqual(5 / 6 * 100, source["point"], places=2)
        self.assertAlmostEqual((5 / 6) ** 2 * 100, source["headline_credit"], places=2)
        self.assertEqual(2.0, source["credit_exponent"])
        self.assertAlmostEqual((5 / 6) ** 2 * 100, deterministic["score"]["score"], places=2)

        self.assertAlmostEqual(85.0 - 30 * 0.6 * (1 - (5 / 6) ** 2), card["weighted_total"]["score"], places=2)

        failed_witness = _live_cell("gdd", {}, failed_items=("causal_witness",), resolved=False)
        card = score_task_result(failed_witness, registry_version=EVIDENCE_REGISTRY_VERSION)
        self.assertEqual(85.0 - 25 * 0.5, card["weighted_total"]["score"])
        witness = _criterion(card, "causal_playability", "verified_witness")
        self.assertNotIn("credit_exponent", witness)
        self.assertEqual("failed", card["strict"]["status"])

    def test_ranking_note_names_the_contract_failure_beside_ranking_eligible(self) -> None:


        result = _live_cell(
            "gdd", {}, failed_items=("health_writable", "mechanic_trace"), resolved=False
        )
        result.eligibility_status = "failed"
        result.eligibility_items = [item for item in result.items if item.id == "health_writable"]
        card = score_task_result(result, registry_version=EVIDENCE_REGISTRY_VERSION)
        self.assertTrue(card["ranking_eligible"])
        self.assertEqual("failed", card["strict"]["status"])
        note = card["ranking_note"]
        self.assertIn("measurement coverage only", note)
        self.assertIn("eligibility=failed (health_writable)", note)
        self.assertIn("failed required items: mechanic_trace", note)
        self.assertIn("NOT resolved", note)

        self.assertEqual(85.0 - 30 * 0.4, card["weighted_total"]["score"])

        clean = score_task_result(_live_cell("gdd", {}), registry_version=EVIDENCE_REGISTRY_VERSION)
        self.assertTrue(clean["ranking_eligible"])
        self.assertEqual("", clean["ranking_note"])
        hole = _live_cell("gdd", {}, failed_items=("causal_witness",), resolved=False)
        _channel_row(hole, "O4").update(
            lo=0.0, hi=0.0, coverage=0.0, denominator=0.0, by_verdict={"unmeasurable": 1.0}
        )
        incomplete = score_task_result(hole, registry_version=EVIDENCE_REGISTRY_VERSION)
        self.assertFalse(incomplete["ranking_eligible"])
        self.assertEqual("", incomplete["ranking_note"])

    def test_unearned_scard_does_not_hide_an_evaluator_hole(self) -> None:
        result = _live_cell("gdd", {})
        _channel_row(result, "O4").update(
            lo=0.0, hi=0.0, coverage=0.0, denominator=0.0, by_verdict={"unmeasurable": 1.0},
            note="level probe crashed",
        )
        card = score_task_result(result, registry_version=EVIDENCE_REGISTRY_VERSION)
        self.assertEqual("evaluation_incomplete", card["weighted_total"]["status"])
        self.assertIsNone(card["weighted_total"]["score"])
        self.assertEqual(85.0, card["weighted_total"]["headline_ceiling"])
        self.assertEqual(["ocard:O4"], [entry["source"] for entry in card["weighted_total"]["unmeasured"]])


def _row(channel: str, lo: float, hi: float, denominator: float, by_verdict: dict,
         coverage: float = 1.0) -> dict:

    return {"channel": channel, "name": channel, "lo": lo, "hi": hi, "coverage": coverage,
            "denominator": denominator, "by_verdict": dict(by_verdict)}


def _live0904_cell(mode: str, channels: dict, *, failed_items=(), resolved: bool,
                   mechanics_credit: float | None = None):


    cell = _live_cell(mode, channels, failed_items=failed_items, resolved=resolved)
    if mode == "gdd" and mechanics_credit is not None:
        cell.items = [
            passed("gdd_mechanics_observable", credit=mechanics_credit)
            if item.id == "gdd_mechanics_observable" else item
            for item in cell.items
        ]
    return cell


_O3_ARC_CLAUDE = _row("O3", 0.8095, 0.9524, 7.0,
                      {"exempt": 1.0, "failed": 1 / 3, "passed": 17 / 3, "unobservable": 1.0})
LIVE0904_CELLS = (
    ("arc_wing/gdd/claude (levelsfix CF)", 71.472, 71.928, _live0904_cell("gdd", {
        "O3": _O3_ARC_CLAUDE,
        "O6": _row("O6", 18 / 217, 18 / 217, 217.0, {"failed": 199.0, "passed": 18.0}),
        "O7": _row("O7", 0.5, 0.5, 1.0, {"passed": 1.0}),
    }, resolved=True, mechanics_credit=1.0)),
    ("cat_defense/gdd/claude", 67.612, 68.612, _live0904_cell("gdd", {
        "O3": _row("O3", 5 / 7, 6 / 7, 7.0, {"exempt": 1.0, "failed": 1.0, "passed": 5.0, "unobservable": 1.0}),
        "O4": _row("O4", 0.5, 1.0, 2.0, {"exempt": 1.0, "passed": 1.0}),
        "O5": _row("O5", 0.875, 0.875, 3.0, {"failed": 0.375, "passed": 2.625}),
        "O6": _row("O6", 2 / 3, 2 / 3, 6.0, {"failed": 2.0, "passed": 4.0}),
        "O7": _row("O7", 0.0, 0.0, 1.0, {"failed": 1.0}),
    }, failed_items=("health_writable",), resolved=False, mechanics_credit=14 / 15)),
    ("arc_wing/gdd/codex", 58.464, 57.555, _live0904_cell("gdd", {
        "O2": _row("O2", 0.95, 0.95, 1.0, {"failed": 0.05, "passed": 0.95}),
        "O3": _row("O3", 5 / 6, 41 / 42, 7.0, {"exempt": 1.0, "passed": 6.0, "unobservable": 1.0}),
        "O6": _row("O6", 0.0, 0.0, 217.0, {"failed": 217.0}),
        "O7": _row("O7", 0.0, 0.0, 1.0, {"failed": 1.0}),
    }, failed_items=("health_writable", "mechanic_trace"), resolved=False, mechanics_credit=9 / 11)),
    ("cat_defense/gdd/codex", 51.024, 50.691, _live0904_cell("gdd", {
        "O3": _row("O3", 0.75, 0.875, 8.0, {"exempt": 1.0, "failed": 1.0, "passed": 6.0}),
        "O4": _row("O4", 0.5, 1.0, 2.0, {"exempt": 1.0, "passed": 1.0}),
        "O5": _row("O5", 1 / 3, 2 / 3, 3.0, {"failed": 1.0, "passed": 1.0, "skipped": 1.0}, coverage=2 / 3),
        "O6": _row("O6", 0.0, 0.0, 6.0, {"failed": 6.0}),
        "O7": _row("O7", 0.2, 0.2, 1.0, {"passed": 1.0}),
    }, failed_items=("health_writable", "mechanic_trace"), resolved=False, mechanics_credit=14 / 15)),
    ("canopy_dash/brief/claude", 56.844, 69.647, _live0904_cell("brief", {
        "O3": _row("O3", 0.6818, 0.8182, 22 / 3,
                   {"exempt": 1.0, "failed": 4 / 3, "passed": 5.0, "unmeasurable": 2 / 3}),
        "O4": _row("O4", 0.0, 0.0, 1.0, {"failed": 1.0, "unobservable": 1.0}),
        "O5": _row("O5", 0.5, 0.5, 1.0, {"failed": 0.5, "passed": 0.5, "unobservable": 2.0}),
        "O6": _row("O6", 0.0, 0.0, 3.0, {"failed": 3.0}),
        "O7": _row("O7", 0.0, 0.0, 1.0, {"failed": 1.0}),
    }, resolved=True)),
    ("canopy_dash/brief/codex", 40.270, 39.520, _live0904_cell("brief", {
        "O2": _row("O2", 0.95, 0.95, 1.0, {"failed": 0.05, "passed": 0.95}),
        "O3": _row("O3", 0.7895, 0.9474, 19 / 3,
                   {"exempt": 1.0, "failed": 1 / 3, "passed": 5.0, "unmeasurable": 2 / 3, "unobservable": 1.0}),
        "O4": _row("O4", 1.0, 1.0, 1.0, {"passed": 1.0, "unobservable": 1.0}),
        "O5": _row("O5", 0.0, 1.0, 1.0, {"skipped": 1.0, "unobservable": 2.0}, coverage=0.0),
        "O6": _row("O6", 0.0, 0.0, 3.0, {"failed": 3.0}),
        "O7": _row("O7", 0.0, 0.0, 1.0, {"failed": 1.0}),
    }, failed_items=("rubric_interface", "health_writable", "mechanic_trace", "causal_witness"),
        resolved=False)),
)


O4_BRIEF = {
    "canopy_dash/brief/claude": _row("O4", 1.0, 1.0, 2.0, {"passed": 2.0}),
    "canopy_dash/brief/codex": _row("O4", 0.5, 1.0, 2.0, {"passed": 1.0, "skipped": 1.0}, coverage=0.5),
}


class Calib4RegistryTests(unittest.TestCase):


    def test_registry_identity_and_policy(self) -> None:
        policy = registry_policy(CALIB4_REGISTRY_VERSION)
        self.assertEqual(CALIB4_REGISTRY_VERSION, policy.version)
        self.assertEqual("calibrated_on_live0903_live0904_not_blind", policy.status)
        self.assertTrue(policy.brief_own_structure and policy.gdd_mechanics_observable and policy.o6_linear)
        self.assertTrue(policy.scard_unearned)
        self.assertFalse(policy.register_o8)
        self.assertFalse(policy.mode4_single_restoration)
        for version in (PILOT_REGISTRY_VERSION, CALIB_REGISTRY_VERSION):
            old = registry_policy(version)
            self.assertFalse(old.brief_own_structure or old.gdd_mechanics_observable or old.o6_linear)
        self.assertIn("O6 asset coverage enters linearly", headline_rule(CALIB4_REGISTRY_VERSION))
        self.assertNotIn("O6 asset coverage", headline_rule(CALIB_REGISTRY_VERSION))

        for mode in ("brief", "gdd", "skeleton"):
            for c3, c4 in zip(_category_specs(mode, CALIB_REGISTRY_VERSION),
                              _category_specs(mode, CALIB4_REGISTRY_VERSION)):
                self.assertEqual(c3.weight, c4.weight, (mode, c3.id))
                self.assertEqual([c.weight for c in c3.criteria], [c.weight for c in c4.criteria], (mode, c3.id))
        gdd_mode = {spec.id: spec for spec in _category_specs("gdd", CALIB4_REGISTRY_VERSION)}
        mode_specific = gdd_mode["mode_specific"].criteria
        self.assertEqual(["gdd_mechanics_observable"], [c.id for c in mode_specific])
        self.assertEqual(("item", "gdd_mechanics_observable"),
                         (mode_specific[0].sources[0].kind, mode_specific[0].sources[0].id))
        structure = {c.id: c for c in gdd_mode["structure_content"].criteria}
        self.assertEqual(1.0, structure["asset_realization"].credit_exponent)
        self.assertEqual(GRADED_CHANNEL_EXPONENT, structure["topology_progression"].credit_exponent)
        self.assertEqual(GRADED_CHANNEL_EXPONENT, structure["spatial_relations"].credit_exponent)

    def test_o6_asset_coverage_enters_linearly(self) -> None:
        result = _live_cell("gdd", {"O6": _ocard_row("O6", 4, 2)})
        card = score_task_result(result, registry_version=EVIDENCE_REGISTRY_VERSION)
        assets = _criterion(card, "structure_content", "asset_realization")
        self.assertAlmostEqual(2 / 3 * 100, assets["sources"][0]["point"], places=2)
        self.assertNotIn("headline_credit", assets["sources"][0])
        self.assertNotIn("credit_exponent", assets)
        self.assertAlmostEqual(2 / 3 * 100, assets["score"]["score"], places=2)

        self.assertAlmostEqual(85.0 - 15 * 0.4 * (1 / 3), card["weighted_total"]["score"], places=2)
        calib3 = score_task_result(result, registry_version=CALIB_REGISTRY_VERSION)
        self.assertAlmostEqual(85.0 - 15 * 0.4 * (1 - (2 / 3) ** 2), calib3["weighted_total"]["score"], places=2)

    def test_brief_mode_drops_o5_and_o7_with_reasons_unless_the_statement_names_layout(self) -> None:
        result = _live_cell("brief", {"O5": _ocard_row("O5", 0, 1), "O7": _ocard_row("O7", 0, 1)})
        card = score_task_result(result, registry_version=EVIDENCE_REGISTRY_VERSION)
        self.assertEqual("complete", card["weighted_total"]["status"])

        self.assertEqual(85.0, card["weighted_total"]["score"])
        self.assertIn("causal_playability/end_to_end_route", card["not_applicable_criteria"])
        self.assertIn("structure_content/spatial_relations", card["not_applicable_criteria"])
        reasons = card["not_applicable_reasons"]
        self.assertIn("brief mode", reasons["causal_playability/end_to_end_route"])
        self.assertIn("renormalisation is worth the same as passing", reasons["causal_playability/end_to_end_route"])
        self.assertIn("layout constraint", reasons["structure_content/spatial_relations"])
        self.assertIn("uncalibrated", reasons["visual_experience/calibrated_surface"])
        route = _criterion(card, "causal_playability", "end_to_end_route")
        self.assertFalse(route["applicable"])

        self.assertEqual(0.0, route["sources"][0]["point"])
        self.assertEqual(reasons["causal_playability/end_to_end_route"], route["not_applicable_reason"])
        self.assertEqual(100.0, _category(card, "causal_playability")["score"]["score"])
        self.assertEqual(70.0, _category(card, "causal_playability")["applicable_weight_within_category"])
        self.assertTrue(card["ranking_eligible"])

        calib3 = score_task_result(result, registry_version=CALIB_REGISTRY_VERSION)
        self.assertEqual(85.0 - 25 * 0.3 - 15 * 0.3, calib3["weighted_total"]["score"])

        result.brief_context = {"layout_constraint": "collectibles spread over the full height"}
        constrained = score_task_result(result, registry_version=EVIDENCE_REGISTRY_VERSION)
        self.assertNotIn("structure_content/spatial_relations", constrained["not_applicable_criteria"])
        self.assertIn("causal_playability/end_to_end_route", constrained["not_applicable_criteria"])
        self.assertEqual(85.0 - 15 * 0.3, constrained["weighted_total"]["score"])

        gdd = score_task_result(_live_cell("gdd", {"O7": _ocard_row("O7", 0, 1)}), registry_version=EVIDENCE_REGISTRY_VERSION)
        self.assertEqual(85.0 - 25 * 0.3, gdd["weighted_total"]["score"])
        self.assertNotIn("causal_playability/end_to_end_route", gdd["not_applicable_criteria"])
        self.assertEqual(sorted(BRIEF_NOT_APPLICABLE), [
            ("causal_playability", "end_to_end_route"), ("structure_content", "spatial_relations"),
        ])

    def test_brief_layout_constraint_detection(self) -> None:
        self.assertEqual("", brief_layout_constraint(
            "Run forward through a narrow 3D canopy route, make rapid lane, jump, slide, or "
            "turn decisions, and complete a bounded course."))
        self.assertIn("spread", brief_layout_constraint(
            "A platformer. Collectibles must be spread over the whole height of each level."))
        self.assertEqual("", brief_layout_constraint(""))

    def test_gdd_mode_scores_the_fraction_of_task_gdd_mechanics_observed(self) -> None:
        result = _live_cell("gdd", {})
        result.items = [
            passed("gdd_mechanics_observable", credit=0.8) if item.id == "gdd_mechanics_observable"
            else item for item in result.items
        ]
        card = score_task_result(result, registry_version=EVIDENCE_REGISTRY_VERSION)
        criterion = _criterion(card, "mode_specific", "gdd_mechanics_observable")
        self.assertEqual(80.0, criterion["score"]["score"])
        self.assertEqual(80.0, _category(card, "mode_specific")["score"]["score"])
        self.assertEqual(85.0 - 5 * 0.2, card["weighted_total"]["score"])

        calib3 = score_task_result(result, registry_version=CALIB_REGISTRY_VERSION)
        self.assertEqual(["task_gdd"], [c["id"] for c in _category(calib3, "mode_specific")["criteria"]])
        self.assertEqual(85.0, calib3["weighted_total"]["score"])

        missing = _live_cell("gdd", {})
        missing.items = [item for item in missing.items if item.id != "gdd_mechanics_observable"]
        hole = score_task_result(missing, registry_version=EVIDENCE_REGISTRY_VERSION)
        self.assertEqual("evaluation_incomplete", hole["weighted_total"]["status"])
        self.assertEqual(["item:gdd_mechanics_observable"],
                         [u["source"] for u in hole["weighted_total"]["unmeasured"]])
        self.assertEqual("complete", score_task_result(
            missing, registry_version=CALIB_REGISTRY_VERSION)["weighted_total"]["status"])

    def test_six_live0904_cells_before_and_after(self) -> None:


        after: list[tuple[str, float, bool]] = []
        for name, calib3_expected, calib4_expected, result in LIVE0904_CELLS:
            before = score_task_result(result, registry_version=CALIB_REGISTRY_VERSION)
            self.assertEqual("complete", before["weighted_total"]["status"], name)
            self.assertAlmostEqual(calib3_expected, before["weighted_total"]["score"], delta=0.02, msg=name)
            if name in O4_BRIEF:
                _channel_row(result, "O4").update(O4_BRIEF[name])
            card = score_task_result(result, registry_version=CALIB4_REGISTRY_VERSION)
            self.assertEqual(CALIB4_REGISTRY_VERSION, card["registry_version"])
            self.assertEqual("complete", card["weighted_total"]["status"], name)
            self.assertTrue(card["ranking_eligible"], name)
            self.assertAlmostEqual(calib4_expected, card["weighted_total"]["score"], delta=0.02, msg=name)
            after.append((name, card["weighted_total"]["score"], card["strict"]["resolved"]))
        by_name = {name: score for name, score, _ in after}


        # 70 -> 100) and moves above cat_defense/Claude, which is unresolved.
        self.assertGreater(by_name["arc_wing/gdd/claude (levelsfix CF)"], by_name["arc_wing/gdd/codex"])
        self.assertGreater(by_name["cat_defense/gdd/claude"], by_name["cat_defense/gdd/codex"])
        self.assertGreater(by_name["canopy_dash/brief/claude"], by_name["canopy_dash/brief/codex"])
        self.assertGreater(by_name["canopy_dash/brief/claude"], by_name["cat_defense/gdd/claude"])
        self.assertLess(by_name["canopy_dash/brief/claude"], by_name["arc_wing/gdd/claude (levelsfix CF)"])
        for name, calib3_expected, calib4_expected, _result in LIVE0904_CELLS:
            if "gdd" in name:
                self.assertLess(abs(calib4_expected - calib3_expected), 1.01, name)

    def test_brief_o4_declared_vs_built_reading(self) -> None:
        from types import SimpleNamespace as NS

        from evalsys.ocard.channels import gdd_declared_level_count, score_o4_declared_topology

        self.assertEqual((3, "Three zones"), gdd_declared_level_count(
            "## Scope\nThree zones, one bounded course. 3 zones of 11/12/14 chunks."))
        self.assertEqual((None, ""), gdd_declared_level_count("A runner with obstacles."))

        def level(scene, reached=True, actual=None):
            return NS(declared_scene=scene, scene=scene if actual is None else actual,
                      reached=reached, scene_matches_declaration=(actual is None and reached))

        snapshot = NS(read_ok=True, read_failure="", project="p", probe_sha256="x", engine="4.5.1",
                      levels=(level("res://a.tscn"), level("res://b.tscn"), level("res://c.tscn")))
        witness = {"reached": True, "success_before_all_levels": False, "goal_frame": 3038,
                   "stop_reason": "goal_reached"}
        result = score_o4_declared_topology(
            snapshot, declared_levels=["res://a.tscn", "res://b.tscn", "res://c.tscn"],
            gdd_text="Three zones, one finish line.", witness=witness,
        )
        verdicts = {i.id: i.verdict.value for i in result.items}
        self.assertEqual({"E1/manifest_vs_built": "passed", "E1/gdd_vs_manifest": "passed",
                          "E4/declared_progression": "passed"}, verdicts)
        self.assertEqual(1.0, result.interval.point)


        broken = NS(read_ok=True, read_failure="", project="p", probe_sha256="x", engine="4.5.1",
                    levels=(level("res://a.tscn"), level("res://b.tscn", reached=False)))
        result = score_o4_declared_topology(
            broken, declared_levels=["res://a.tscn", "res://b.tscn"], gdd_text="three stages",
            witness={"reached": True, "success_before_all_levels": True},
        )
        verdicts = {i.id: i.verdict.value for i in result.items}
        self.assertEqual({"E1/manifest_vs_built": "failed", "E1/gdd_vs_manifest": "failed",
                          "E4/declared_progression": "failed"}, verdicts)
        self.assertEqual(0.0, result.interval.point)

        result = score_o4_declared_topology(
            snapshot, declared_levels=["res://a.tscn", "res://b.tscn", "res://c.tscn"],
            gdd_text="", witness={"reached": False, "stop_reason": "wrong_ending"},
        )
        verdicts = {i.id: i.verdict.value for i in result.items}
        self.assertEqual("unobservable", verdicts["E1/gdd_vs_manifest"])
        self.assertEqual("skipped", verdicts["E4/declared_progression"])
        self.assertEqual(0.5, result.interval.point)

        result = score_o4_declared_topology(
            snapshot, declared_levels=["res://a.tscn", "res://b.tscn", "res://c.tscn"], gdd_text="",
        )
        self.assertEqual("unobservable", {i.id: i.verdict.value for i in result.items}["E4/declared_progression"])
        self.assertEqual(1.0, result.interval.point)


def _fixture(name: str):


    report = json.loads((FIXTURES / f"{name}.json").read_text(encoding="utf-8"))
    allowed = set(Item.__dataclass_fields__)
    items = []
    for raw in report["items"]:
        row = {key: value for key, value in raw.items() if key in allowed}
        if row.get("attribution"):
            row["attribution"] = Attribution(row["attribution"])
        items.append(Item(**row))
    return SimpleNamespace(
        package=SimpleNamespace(manifest={"mode": report["mode"], "game_id": report["game_id"]}),
        items=items,
        resolved=report["resolved"],
    )


def _mode34(result):
    return score_task_result(result, registry_version=MODE34_REGISTRY_VERSION)


def _replace_item(result, item):
    result.items = [item if existing.id == item.id else existing for existing in result.items]
    return result


class Mode34RegistryTests(unittest.TestCase):


    def test_registry_identity_and_evidence1_is_the_default(self) -> None:
        policy = registry_policy(MODE34_REGISTRY_VERSION)
        self.assertTrue(policy.mode34_applicability)
        self.assertTrue(policy.mode4_single_restoration and policy.o6_linear and policy.scard_unearned)
        self.assertEqual(EVIDENCE_REGISTRY_VERSION, REGISTRY_VERSION)
        for version in (PILOT_REGISTRY_VERSION, CALIB_REGISTRY_VERSION,
                        CALIB4_REGISTRY_VERSION, MODE4_REGISTRY_VERSION):
            self.assertFalse(registry_policy(version).mode34_applicability, version)
        self.assertIn("repair_credit x gates x regression_mean", headline_rule(MODE34_REGISTRY_VERSION))
        self.assertNotIn("repair_credit", headline_rule(MODE4_REGISTRY_VERSION))


        for mode in ("brief", "gdd", "skeleton", "bugfix", "port"):
            for m4, m34 in zip(_category_specs(mode, MODE4_REGISTRY_VERSION),
                               _category_specs(mode, MODE34_REGISTRY_VERSION)):
                self.assertEqual(m4.weight, m34.weight, (mode, m4.id))
                if (mode, m4.id) == ("bugfix", "mode_specific"):
                    continue
                self.assertEqual([(c.id, c.weight) for c in m4.criteria],
                                 [(c.id, c.weight) for c in m34.criteria], (mode, m4.id))
        bugfix_mode = {spec.id: spec for spec in _category_specs("bugfix", MODE34_REGISTRY_VERSION)}
        self.assertEqual(["publication", "no_smuggling", "product_surface", "transformation"],
                         [c.id for c in bugfix_mode["mode_specific"].criteria])
        self.assertEqual({"feature_kept"}, {
            source.id for c in bugfix_mode["mechanics_requirements"].criteria
            for source in c.sources if source.kind == "item"
        })

        registered = {(spec.id, c.id) for spec in bugfix_mode.values() for c in spec.criteria}
        self.assertIn(MODE4_REPAIR_CREDIT, registered)
        self.assertTrue(set(MODE4_GATES) <= registered, set(MODE4_GATES) - registered)
        self.assertTrue(set(MODE4_REGRESSION_WEIGHTS) <= registered)
        self.assertEqual(70, sum(MODE4_REGRESSION_WEIGHTS.values()))

        for name, _c3, _c4, result in LIVE0904_CELLS:
            default = score_task_result(result, registry_version=MODE4_REGISTRY_VERSION)
            opt_in = _mode34(result)
            self.assertEqual(default["weighted_total"]["score"], opt_in["weighted_total"]["score"], name)
            self.assertEqual(default["categories"], opt_in["categories"], name)

    def test_unmodified_skeleton_stays_under_the_noop_cap(self) -> None:


        self.assertLessEqual(SKELETON_NOOP_HEADLINE_CAP, 25.0)
        for name in ("skeleton_noop_shadow_walker", "skeleton_noop_canopy_dash"):
            card = _mode34(_fixture(name))
            self.assertEqual("complete", card["weighted_total"]["status"], name)
            self.assertEqual(85.0, card["weighted_total"]["headline_ceiling"], name)
            self.assertLessEqual(card["weighted_total"]["score"], SKELETON_NOOP_HEADLINE_CAP, name)
            self.assertGreater(card["weighted_total"]["score"], 0.0, name)
            self.assertEqual("failed", card["strict"]["status"], name)
            for category_id, criterion_id in SKELETON_NOT_APPLICABLE:
                key = f"{category_id}/{criterion_id}"
                self.assertIn(key, card["not_applicable_criteria"], (name, key))
                self.assertIn("skeleton mode", card["not_applicable_reasons"][key])
                self.assertIn("renormalisation is worth the same as passing", card["not_applicable_reasons"][key])

            topology = _criterion(card, "structure_content", "topology_progression")
            self.assertFalse(topology["applicable"])
            self.assertEqual(100.0, topology["sources"][0]["point"])

            launch = _criterion(card, "artifact_observability", "runtime_viability")
            self.assertTrue(launch["applicable"])
            self.assertEqual(50.0, launch["sources"][0]["point"], name)
            self.assertTrue(_criterion(card, "artifact_observability", "witness_contract")["applicable"])
            self.assertEqual(0.0, _criterion(card, "mode_specific", "stub_completion")["score"]["score"])
            self.assertEqual(0.0, _criterion(card, "causal_playability", "verified_witness")["score"]["score"])
            self.assertEqual(0.0, _criterion(card, "causal_playability", "end_to_end_route")["score"]["score"])
            self.assertEqual(0.0, _criterion(card, "mechanics_requirements", "task_checkpoints")["score"]["score"])

            default = score_task_result(_fixture(name), registry_version=MODE4_REGISTRY_VERSION)
            self.assertGreater(default["weighted_total"]["score"], card["weighted_total"]["score"], name)
            self.assertLessEqual(default["weighted_total"]["score"], 30.0, name)

        flawless = _fixture("skeleton_noop_shadow_walker")
        for item_id in ("stub_completion", "mechanic_trace", "causal_witness", "rubric_interface",
                        "transformation_contract"):
            _replace_item(flawless, passed(item_id))
        for channel in ("O1", "O3", "O5", "O6", "O7"):
            _channel_row(flawless, channel).update(_ocard_row(channel, 1.0))
        flawless.resolved = True
        self.assertEqual(85.0, _mode34(flawless)["weighted_total"]["score"])

    def test_unfixed_bugfix_build_scores_zero(self) -> None:


        card = _mode34(_fixture("bugfix_noop_shadow_walker"))
        self.assertEqual("complete", card["weighted_total"]["status"])
        self.assertEqual(0.0, card["weighted_total"]["score"])
        self.assertEqual(100.0, card["weighted_total"]["headline_ceiling"])
        self.assertNotIn("unearned", card["weighted_total"])
        composition = card["weighted_total"]["composition"]
        self.assertEqual("repair_credit x gates x regression_mean", composition["formula"])
        self.assertEqual(0.0, composition["repair_credit"])
        self.assertIn("restored 0/2", composition["repair_detail"])
        self.assertEqual(100.0, composition["gates"])
        self.assertEqual([], composition["failed_gates"])
        self.assertEqual(
            {"mechanics_requirements/feature_preservation": 40, "mode_specific/product_surface": 15,
             "mode_specific/transformation": 15},
            composition["regression_weights"],
        )
        for category_id, criterion_id in BUGFIX_NOT_APPLICABLE:
            key = f"{category_id}/{criterion_id}"
            self.assertIn(key, card["not_applicable_criteria"], key)
            self.assertIn("bugfix mode", card["not_applicable_reasons"][key], key)

        visual = _category(card, "visual_experience")
        self.assertFalse(visual["applicable"])
        self.assertNotEqual("unearned", visual["score"]["status"])

        self.assertEqual(100.0, _category(card, "mechanics_requirements")["score"]["score"])
        self.assertEqual(0.0, _criterion(card, "causal_playability", "repair_restoration")["score"]["score"])
        self.assertTrue(card["ranking_eligible"])
        self.assertEqual("failed", card["strict"]["status"])


        default = score_task_result(_fixture("bugfix_noop_shadow_walker"), registry_version=MODE4_REGISTRY_VERSION)
        self.assertEqual("evaluation_incomplete", default["weighted_total"]["status"])
        self.assertEqual(["ocard:O6"], [u["source"] for u in default["weighted_total"]["unmeasured"]])

    def test_clean_revert_earns_full_repair_credit(self) -> None:


        card = _mode34(_fixture("bugfix_revert_shadow_walker"))
        self.assertEqual("complete", card["weighted_total"]["status"])
        self.assertEqual(100.0, card["weighted_total"]["score"])
        self.assertEqual(100.0, card["weighted_total"]["headline_ceiling"])
        composition = card["weighted_total"]["composition"]
        self.assertEqual((100.0, 100.0, 100.0),
                         (composition["repair_credit"], composition["gates"], composition["regression_mean"]))
        self.assertIn("restored 2/2", composition["repair_detail"])
        self.assertTrue(card["strict"]["resolved"])
        self.assertTrue(card["ranking_eligible"])
        self.assertEqual("", card["ranking_note"])
        for category in card["categories"]:
            if category["applicable"]:
                self.assertEqual(100.0, category["score"]["score"], category["id"])
        default = score_task_result(_fixture("bugfix_revert_shadow_walker"), registry_version=MODE4_REGISTRY_VERSION)
        self.assertEqual("evaluation_incomplete", default["weighted_total"]["status"])

    def test_regression_loses_credit(self) -> None:


        regress = _mode34(_fixture("bugfix_regress_shadow_walker"))
        self.assertEqual("complete", regress["weighted_total"]["status"])
        self.assertEqual(0.0, regress["weighted_total"]["score"])
        composition = regress["weighted_total"]["composition"]
        self.assertEqual(0.0, composition["repair_credit"])
        self.assertIn("broke 1 regression", composition["repair_detail"])
        self.assertEqual([], composition["failed_gates"])
        restoration = _criterion(regress, "causal_playability", "repair_restoration")["sources"][0]
        self.assertEqual(["shadow_walker/C1/level_1_chest_and_exit"], restoration["evidence"]["regression_failed"])
        self.assertFalse(regress["strict"]["resolved"])


        broken_contract = _replace_item(_fixture("bugfix_revert_shadow_walker"), failed("feature_kept"))
        card = _mode34(broken_contract)
        self.assertEqual("complete", card["weighted_total"]["status"])
        self.assertAlmostEqual(100 * 30 / 70, card["weighted_total"]["score"], places=2)
        self.assertAlmostEqual(100 * 30 / 70, card["weighted_total"]["composition"]["regression_mean"], places=2)
        self.assertEqual(100.0, card["weighted_total"]["composition"]["repair_credit"])

        dropped = _replace_item(_fixture("bugfix_revert_shadow_walker"), failed("surface"))
        self.assertAlmostEqual(100 * 55 / 70, _mode34(dropped)["weighted_total"]["score"], places=2)
        duty = _replace_item(_fixture("bugfix_revert_shadow_walker"), failed("transformation_contract"))
        self.assertAlmostEqual(100 * 55 / 70, _mode34(duty)["weighted_total"]["score"], places=2)

        for item_id, gate in (("no_eval_smuggling", "mode_specific/no_smuggling"),
                              ("null_no_win", "causal_playability/matched_null"),
                              ("anti_grant_static", "artifact_observability/integrity_controls"),
                              ("anti_grant_diff", "causal_playability/anti_grant_controls")):
            gated = _replace_item(_fixture("bugfix_revert_shadow_walker"), failed(item_id))
            gated_card = _mode34(gated)
            self.assertEqual(0.0, gated_card["weighted_total"]["score"], item_id)
            self.assertEqual(0.0, gated_card["weighted_total"]["composition"]["gates"], item_id)
            self.assertEqual([gate], gated_card["weighted_total"]["composition"]["failed_gates"], item_id)

        partial = _replace_item(_fixture("bugfix_revert_shadow_walker"),
                                passed("repair_restoration", credit=0.5))
        self.assertEqual(50.0, _mode34(partial)["weighted_total"]["score"])

        hole = _fixture("bugfix_revert_shadow_walker")
        hole.items = [item for item in hole.items if item.id != "repair_restoration"]
        hole_card = _mode34(hole)
        self.assertEqual("evaluation_incomplete", hole_card["weighted_total"]["status"])
        self.assertIsNone(hole_card["weighted_total"]["score"])
        self.assertIn("item:repair_restoration", [u["source"] for u in hole_card["weighted_total"]["unmeasured"]])
        self.assertFalse(hole_card["ranking_eligible"])


if __name__ == "__main__":
    unittest.main()
