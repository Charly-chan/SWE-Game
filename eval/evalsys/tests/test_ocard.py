


from __future__ import annotations

import json
import os
import tempfile
import unittest
from pathlib import Path

from evalsys.assertions import AssertionSet, DEFAULT_CHANNEL_OVERRIDES
from evalsys.report.cards import Card, ChannelRecord
from evalsys.verdict import Interval, Verdict, failed, passed, score_items, unmeasurable
from evalsys.weights import CHANNELS, O2_LADDER
from evalsys.ocard import adapters, channels, manifest, scorer
from evalsys.truth.snapshot import ActionLiveness, LevelTruth, TruthSnapshot


def _invocation(outcome: adapters.ToolOutcome = adapters.ToolOutcome.REPORTED,
                rc: int = 0) -> adapters.ToolInvocation:
    return adapters.ToolInvocation(
        tool="fake", tool_path="/fake", tool_sha256="0" * 64, argv=["fake"],
        cwd="/tmp", returncode=rc, seconds=0.0, outcome=outcome,
    )


def _deliverables(**overrides) -> adapters.DeliverablesResult:

    checks = {
        "bridge": {"id": "bridge", "status": "pass", "gb_player": True,
                   "l3_actions": ["gb_left", "gb_right", "gb_up", "gb_down",
                                  "gb_jump", "gb_action", "gb_pause", "gb_reset"],
                   "b3": {"problems": []}, "detail": "ok"},
        "gb_tags": {"id": "gb_tags", "status": "pass", "per_file": {}, "detail": "ok"},
        "gb_levels": {"id": "gb_levels", "status": "pass", "detail": "all 3 levels resolve"},
        "b3_negative": {"id": "b3_negative", "status": "pass", "detail": "archived"},
    }
    checks.update(overrides)
    return adapters.DeliverablesResult(
        project="fake", path="/fake",
        record={"project": "fake", "verdict": "PASS", "failing": [], "checks": checks},
        invocation=_invocation(),
    )


class VerdictIsNotEvidence(unittest.TestCase):
    BOT_OUTPUT = """
[bot] mode=certify
[bot] PASS certify/player_spawns          got=1 want=1
[bot] PASS certify/goal_reachable         path found
[bot] ---
[bot] checks: 2 run, 0 failed
[bot] VERDICT: PASS
"""

    def test_missing_manifest_ids_score_failed_not_absent(self):
        man = manifest.CheckManifest("t", [
            manifest.ManifestEntry(id, "O2") for id in (
                "certify/player_spawns", "certify/goal_reachable",
                "certify/hazard_kills", "certify/level_advances")
        ])
        out = manifest.parse_bot_records(self.BOT_OUTPUT)
        rec = man.reconcile(out.by_id)

        self.assertEqual(rec.missing, ["certify/hazard_kills", "certify/level_advances"])
        by_id = {i.id: i for i in rec.items}
        for gone in rec.missing:
            self.assertIs(by_id[gone].verdict, Verdict.FAILED,
                          "a manifest id with no reported result must be failed, never absent")
        iv = score_items(rec.items)
        self.assertAlmostEqual(iv.lo, 0.5)
        self.assertAlmostEqual(iv.hi, 0.5)
        self.assertLess(iv.lo, 1.0, "a VERDICT: PASS must not produce a passing score")

    def test_verdict_is_quarantined_from_scoring(self):
        out = manifest.parse_bot_records(self.BOT_OUTPUT)
        self.assertEqual(out.self_report.verdict, "PASS")


        self.assertTrue(all(not hasattr(r, "verdict") for r in out.records))
        self.assertIn("never scored", out.self_report.to_dict()["note"])

    def test_a_failing_line_is_believed(self):
        out = manifest.parse_bot_records(
            "[bot] FAIL certify/goal_reachable no path\n[bot] VERDICT: PASS\n")
        self.assertFalse(out.by_id["certify/goal_reachable"].ok,
                         "refusing a self-reported pass and refusing a self-reported failure "
                         "are not symmetric; nobody cheats by claiming to be broken")

    def test_cf10_remains_evaluator_integrity_and_cannot_cap_o2(self):
        man = manifest.CheckManifest("t", [
            manifest.ManifestEntry(i, "O2") for i in
            ("certify/player_spawns", "certify/goal_reachable", "certify/hazard_kills")
        ])
        out = manifest.parse_bot_records(self.BOT_OUTPUT)
        cf10 = manifest.evaluate_cf10(man, out.by_id)
        self.assertFalse(cf10.satisfied)

        ids = {gate.id for gate in O2_LADDER.gates}
        self.assertFalse(ids & {
            "bridge_files_generated", "b1_b2_certified", "b3_b4_certified", "cf10"
        })


class BotSkipLeavesAMark(unittest.TestCase):
    def _runtime(self, bot_status: str) -> adapters.RuntimeResult:
        return adapters.RuntimeResult(
            project="fake", path="/fake",
            record={"project": "fake", "verdict": "PASS", "failing": [],
                    "checks": {
                        "boot": {"id": "boot", "status": "pass",
                                 "detail": "ran 300 frames clean"},
                        "bot": {"id": "bot", "status": bot_status, "harness": None,
                                "detail": "no driveable bot harness found"},
                    }},
            invocation=_invocation(),
        )

    def test_skip_is_detected(self):
        self.assertTrue(self._runtime("skip").bot_skipped)
        self.assertFalse(self._runtime("pass").bot_skipped)

    def test_a_check_nobody_asked_for_is_not_a_submission_skip(self):


        backfilled = adapters.RuntimeResult(
            project="fake", path="/fake",
            record={"checks": {"bot": {"id": "bot", "status": "skip", "gating": False,
                                       "detail": "not run"}}},
            invocation=_invocation())
        self.assertFalse(backfilled.bot_requested)
        self.assertFalse(backfilled.bot_skipped,
                         "charging an unrequested check to the submission invents a finding")

    def test_skip_emits_skipped_items_for_every_lost_detector(self):
        rt = self._runtime("skip")
        items = manifest.bot_skip_items(rt.bot["detail"])
        self.assertEqual(len(items), len(manifest.BOT_SKIP_DOWNSTREAM))
        self.assertTrue(all(i.verdict is Verdict.SKIPPED for i in items))
        ids = {i.id for i in items}
        self.assertIn("bot/script_errors_scanned", ids)
        self.assertIn("bot/check_count_compared", ids)

    def test_skip_drives_coverage_well_below_one(self):
        rt = self._runtime("skip")
        items = [passed("O2/something_we_did_measure")] + manifest.bot_skip_items(rt.bot["detail"])
        iv = score_items(items)
        self.assertLess(iv.coverage, 0.8, "a bot skip must show up as lost coverage")
        self.assertGreater(iv.hi - iv.lo, 0.5, "and must widen the interval")
        self.assertAlmostEqual(iv.lo, 1 / 6)

    def test_skip_never_aggregates_as_a_pass(self):
        rt = self._runtime("skip")
        cf10 = manifest.evaluate_cf10(
            manifest.CheckManifest("t", []), {}, bot_skip_reason=rt.bot["detail"])
        self.assertFalse(cf10.satisfied)
        self.assertTrue(any("skipped" in r for r in cf10.reasons))
        self.assertTrue(all(i.verdict is Verdict.SKIPPED for i in cf10.items))


class SelfConsistentTotalIsCaught(unittest.TestCase):


    def _output(self, n: int) -> str:
        checks = [{"check": f"certify/check_{i}", "ok": True} for i in range(n)]
        return json.dumps({"checks": checks, "passed": n, "total": n}) + "\nVERDICT: PASS\n"

    def _manifest(self, n: int) -> manifest.CheckManifest:
        return manifest.CheckManifest("t", [
            manifest.ManifestEntry(f"certify/check_{i}", "O2") for i in range(n)
        ])

    def test_healthy_and_hollowed_runs_are_indistinguishable_from_the_numbers(self):
        healthy = manifest.parse_bot_records(self._output(15)).self_report
        hollow = manifest.parse_bot_records(self._output(13)).self_report
        self.assertTrue(healthy.self_consistent_but_uninformative)
        self.assertTrue(hollow.self_consistent_but_uninformative)

    def test_the_manifest_sees_the_hole(self):
        out = manifest.parse_bot_records(self._output(13))
        self.assertEqual(len(out.records), 13, "JSON check objects must be parsed as records")
        rec = self._manifest(15).reconcile(out.by_id)
        self.assertEqual(rec.missing, ["certify/check_13", "certify/check_14"])
        self.assertTrue(all(
            i.verdict is Verdict.FAILED for i in rec.items if i.id in rec.missing))
        self.assertLess(score_items(rec.items).lo, 1.0)

    def test_positional_binding_catches_it_without_a_name_vocabulary(self):
        out = manifest.parse_bot_records(self._output(13))
        man = manifest.CheckManifest.from_expectations("t", "O2", {"certify": 15})
        bound = manifest.bind_positional(man, out.records)
        rec = man.reconcile(bound)
        self.assertEqual(len(rec.missing), 2)
        self.assertTrue(all(i.verdict is Verdict.FAILED for i in rec.items
                            if i.id in rec.missing))

    def test_a_complete_run_reconciles_clean(self):
        out = manifest.parse_bot_records(self._output(15))
        rec = self._manifest(15).reconcile(out.by_id)
        self.assertTrue(rec.complete)
        self.assertAlmostEqual(score_items(rec.items).lo, 1.0)


class ShortfallWidensTheInterval(unittest.TestCase):
    def test_shortfall_produces_skipped_items_and_a_wider_interval(self):
        full = manifest.check_count_floor("certify", 12, 12)
        short = manifest.check_count_floor("certify", 8, 12)
        self.assertTrue(full.satisfied)
        self.assertFalse(short.satisfied)
        self.assertEqual(short.shortfall, 4)
        self.assertTrue(all(i.verdict is Verdict.SKIPPED for i in short.items))

        ran = [passed(f"certify/check_{i}") for i in range(8)]
        tight = score_items(ran + full.items[:0] + [passed(f"certify/check_{i}")
                                                    for i in range(8, 12)])
        wide = score_items(ran + short.items)
        self.assertAlmostEqual(tight.hi - tight.lo, 0.0)
        self.assertGreater(wide.hi - wide.lo, 0.3)
        self.assertAlmostEqual(wide.lo, 8 / 12)
        self.assertAlmostEqual(wide.hi, 1.0)
        self.assertAlmostEqual(wide.coverage, 8 / 12)

    def test_shortfall_stays_in_evaluator_integrity_not_o2(self):
        man = manifest.CheckManifest.from_expectations("t", "O2", {"certify": 12})
        records = [manifest.CheckRecord(id=f"certify/check[{i}]", ok=True) for i in range(12)]
        cf10_ok = manifest.evaluate_cf10(
            man, {r.id: r for r in records},
            floors=[manifest.check_count_floor("certify", 12, 12)])
        cf10_short = manifest.evaluate_cf10(
            man, {r.id: r for r in records},
            floors=[manifest.check_count_floor("certify", 8, 12)])

        self.assertTrue(cf10_ok.satisfied)
        self.assertFalse(cf10_short.satisfied)
        self.assertEqual(
            {"groups", "actions", "levels", "endings", "optional_fields_valid",
             "task_required_observables"},
            {gate.id for gate in O2_LADDER.gates},
        )

    def test_a_shortfall_widens_where_a_plain_failure_does_not(self):


        man = manifest.CheckManifest.from_expectations("t", "O2", {"certify": 12})
        eleven = {f"certify/check[{i}]": manifest.CheckRecord(f"certify/check[{i}]", True)
                  for i in range(11)}
        missing_only = manifest.evaluate_cf10(man, eleven)
        with_shortfall = manifest.evaluate_cf10(
            man, eleven, floors=[manifest.check_count_floor("certify", 8, 12)])

        flat = score_items(missing_only.items)
        wide = score_items(with_shortfall.items)
        self.assertAlmostEqual(flat.hi - flat.lo, 0.0)
        self.assertGreater(wide.hi - wide.lo, 0.0)
        self.assertLess(wide.coverage, 1.0)


class O1MeasuresTheDeclaredLevel(unittest.TestCase):
    def _probe(self, reached: bool, fatal=(), per_action=None) -> adapters.LevelProbeResult:
        return adapters.LevelProbeResult(
            level="res://levels/level_1.tscn", reached=reached,
            entry_method="pressed button 'PLAY'",
            report={"entered_game": reached, "actions_tested": ["gb_left", "gb_right"],
                    "idle_baseline_per_frame": 0.1366, "idle_sig_delta": 0.0,
                    "bend_floor": 0.04, "live_actions": 0,
                    "per_action": per_action or {}},
            fatal=list(fatal), warnings=[], invocation=_invocation(),
        )

    def _cold(self, ok: bool = True) -> adapters.ColdImportResult:
        return adapters.ColdImportResult(ok=ok, cold=True, fatal=[] if ok else ["boom"],
                                         warnings=[], invocation=_invocation())

    def _capture(self, *, imported: bool = True) -> adapters.FrameCaptureResult:
        best = {"colours": 240, "dominant_share": 0.62, "edge_energy": 3.1, "frame": "f.png"}
        return adapters.FrameCaptureResult(
            level="res://levels/level_1.tscn", mode="X", frames_captured=120,
            measures=[best], best=best, frame_paths=[], invocation=_invocation(),
            import_invocation=_invocation() if imported else None)

    def test_capture_without_import_is_our_fault_not_theirs(self):


        result = channels.score_o1_runnable(
            self._cold(), self._probe(True), self._capture(imported=False), mode="X")
        rungs = {i.id: i for i in result.items}
        self.assertIs(rungs["O1/draws_nontrivial"].verdict, Verdict.INCONCLUSIVE)
        self.assertNotIn(
            "flat", rungs["O1/draws_nontrivial"].detail,
            "an un-imported capture must not be described as the game drawing badly")

    def test_rungs_above_an_inconclusive_rung_are_inconclusive_not_skipped(self):


        from evalsys.verdict import Gate, Ladder

        ladder = Ladder("L", [Gate("a", 0.25), Gate("b", 0.25), Gate("c", 0.25), Gate("d", 0.25)])
        items = {i.id: i for i in ladder.resolve({
            "a": (Verdict.PASSED, ""), "b": (Verdict.PASSED, ""),
            "c": (Verdict.INCONCLUSIVE, "capture ran without importing the project first"),
            "d": (Verdict.PASSED, "measured live"),
        })}
        self.assertIs(items["L/d"].verdict, Verdict.INCONCLUSIVE)
        self.assertEqual("blocked by c", items["L/d"].detail)
        interval = score_items(list(items.values()))
        self.assertEqual(0.5, interval.denominator)
        self.assertEqual(1.0, interval.lo)
        self.assertEqual(0.5, interval.by_verdict["inconclusive"])
        self.assertNotIn("skipped", interval.by_verdict)


        skipped = {i.id: i for i in ladder.resolve({
            "a": (Verdict.PASSED, ""), "b": (Verdict.SKIPPED, "never reached the level"),
        })}
        self.assertIs(skipped["L/c"].verdict, Verdict.SKIPPED)
        failed = {i.id: i for i in ladder.resolve({
            "a": (Verdict.FAILED, "3 fatal lines"),
        })}
        self.assertIs(failed["L/d"].verdict, Verdict.FAILED)


        pressed = {"gb_left": {"per_frame": 0.0, "bend_vs_idle": 0.0, "threshold": 0.04,
                               "sig_delta": 9, "live": False, "via": "none"}}
        result = channels.score_o1_runnable(
            self._cold(), self._probe(True, per_action=pressed),
            self._capture(imported=False), mode="X")
        rungs = {i.id: i for i in result.items}
        self.assertIs(rungs["O1/responds_to_input"].verdict, Verdict.INCONCLUSIVE)
        self.assertEqual(result.evidence["measured_outcomes"]["responds_to_input"][0], "passed")
        self.assertEqual(0.5, result.interval.denominator)
        self.assertNotIn("skipped", result.interval.by_verdict)

    def test_boot_pass_on_main_scene_is_not_a_level_reading(self):
        rt = adapters.RuntimeResult(
            project="fake", path="/fake",
            record={"checks": {"boot": {"id": "boot", "status": "pass",
                                        "detail": "ran 300 frames clean"}}},
            invocation=_invocation())
        self.assertTrue(rt.boot_passed_on_main_scene)

        result = channels.score_o1_runnable(self._cold(), self._probe(reached=False))
        rungs = {i.id: i for i in result.items}
        self.assertIs(rungs["O1/boots"].verdict, Verdict.SKIPPED,
                      "a level that was never reached is skipped, never passed, whatever the "
                      "tool's main_scene boot check says")
        self.assertLess(result.interval.lo, 0.5)
        self.assertLess(result.interval.coverage, 1.0)

    def test_idle_drift_alone_is_not_a_response(self):

        drifting = {
            "jump": {"per_frame": 0.0, "bend_vs_idle": 0.1366, "threshold": 0.04,
                     "sig_delta": 0, "live": True, "via": "motion"},
        }
        result = channels.score_o1_runnable(
            self._cold(), self._probe(True, per_action=drifting), self._capture(), mode="X")
        rungs = {i.id: i for i in result.items}
        self.assertIs(rungs["O1/responds_to_input"].verdict, Verdict.FAILED,
                      "an action that moved nothing must not be scored live because the world "
                      "drifted underneath it")

    def test_state_only_response_counts(self):

        rhythm = {
            "gb_left": {"per_frame": 0.0, "bend_vs_idle": 0.0, "threshold": 0.04,
                        "sig_delta": 9, "live": False, "via": "none"},
        }
        result = channels.score_o1_runnable(
            self._cold(), self._probe(True, per_action=rhythm), self._capture(), mode="X")
        rungs = {i.id: i for i in result.items}
        self.assertIs(rungs["O1/responds_to_input"].verdict, Verdict.PASSED)
        self.assertIn("state", result.evidence["liveness"]["via"].values())

    def test_a_measured_rung_above_a_blocked_one_is_still_recorded(self):


        rhythm = {"gb_left": {"per_frame": 0.0, "bend_vs_idle": 0.0, "threshold": 0.04,
                              "sig_delta": 9, "live": False, "via": "none"}}
        result = channels.score_o1_runnable(
            self._cold(), self._probe(True, per_action=rhythm), mode="H")
        rungs = {i.id: i for i in result.items}
        self.assertIs(rungs["O1/responds_to_input"].verdict, Verdict.SKIPPED)
        self.assertEqual(result.evidence["measured_outcomes"]["responds_to_input"][0], "passed")

    def test_pixels_are_unmeasurable_in_h_mode(self):
        result = channels.score_o1_runnable(self._cold(), self._probe(True), mode="H")
        rungs = {i.id: i for i in result.items}
        self.assertIs(rungs["O1/draws_nontrivial"].verdict, Verdict.UNMEASURABLE)

    def test_a_harness_side_failure_is_not_charged_to_the_submission(self):
        broken = adapters.ColdImportResult(
            ok=False, cold=True, fatal=[], warnings=[],
            invocation=_invocation(adapters.ToolOutcome.CRASHED, rc=2))
        result = channels.score_o1_runnable(broken, self._probe(True))
        self.assertTrue(all(i.verdict is Verdict.INCONCLUSIVE for i in result.items))
        self.assertEqual(result.interval.denominator, 0.0,
                         "an empty denominator is not a zero score")

    def _truth(
        self,
        *,
        first_reached: bool = True,
        scene_ok: bool = True,
        later_reached: bool = True,
        live: bool = True,
        sweep: bool = True,
        declared: bool = True,
    ) -> TruthSnapshot:
        declared_scene = "res://levels/level_1.tscn"
        scene = declared_scene if scene_ok else "res://scenes/main_menu.tscn"
        actions = ("gb_left", "gb_jump") if declared else ()
        liveness: tuple[ActionLiveness, ...] = ()
        if sweep and declared:
            liveness = (
                ActionLiveness("gb_left", live=live, via="motion" if live else "none"),
                ActionLiveness("gb_jump", live=False, via="none"),
            )
        return TruthSnapshot(
            project="fake",
            levels=(
                LevelTruth(
                    declared_scene=declared_scene,
                    scene=scene,
                    reached=first_reached,
                    entry_method="pressed button 'PLAY'",
                ),
                LevelTruth(
                    declared_scene="res://levels/level_2.tscn",
                    scene="res://levels/level_2.tscn",
                    reached=later_reached,
                    entry_method="none",
                ),
            ),
            liveness=liveness,
            declared_actions=actions,
        )

    def test_truth_overrides_a_skipped_legacy_probe(self):


        result = channels.score_o1_runnable(
            self._cold(), self._probe(reached=False), self._capture(),
            mode="X", truth=self._truth(),
        )
        rungs = {i.id: i for i in result.items}
        self.assertIs(rungs["O1/boots"].verdict, Verdict.PASSED)
        self.assertIs(rungs["O1/responds_to_input"].verdict, Verdict.PASSED)
        self.assertIs(rungs["O1/draws_nontrivial"].verdict, Verdict.PASSED)
        self.assertAlmostEqual(result.interval.lo, 1.0)
        self.assertAlmostEqual(result.interval.coverage, 1.0)
        self.assertEqual(result.evidence["boots_source"], "truth")
        self.assertEqual(result.evidence["responds_source"], "truth")

    def test_o1_reads_gb_levels_zero_not_a_later_reached_level(self):
        result = channels.score_o1_runnable(
            self._cold(), self._probe(reached=False), self._capture(),
            mode="X", truth=self._truth(first_reached=False, later_reached=True),
        )
        rungs = {i.id: i for i in result.items}
        self.assertIs(rungs["O1/boots"].verdict, Verdict.SKIPPED)
        self.assertLess(result.interval.lo, 0.5)

    def test_truth_scene_mismatch_is_not_a_level_boot(self):
        result = channels.score_o1_runnable(
            self._cold(), self._probe(reached=False),
            truth=self._truth(scene_ok=False),
        )
        rungs = {i.id: i for i in result.items}
        self.assertIs(rungs["O1/boots"].verdict, Verdict.SKIPPED)
        self.assertIn("main_scene fallback", rungs["O1/boots"].detail)

    def test_missing_liveness_sweep_is_our_gap(self):
        result = channels.score_o1_runnable(
            self._cold(), self._probe(reached=False), self._capture(),
            mode="X", truth=self._truth(sweep=False),
        )
        self.assertEqual(
            result.evidence["measured_outcomes"]["responds_to_input"][0],
            "inconclusive",
        )
        rungs = {i.id: i for i in result.items}
        self.assertIs(rungs["O1/responds_to_input"].verdict, Verdict.INCONCLUSIVE)
        self.assertAlmostEqual(result.interval.lo, 1.0)
        self.assertAlmostEqual(result.interval.denominator, 0.75)
        self.assertAlmostEqual(result.interval.coverage, 1.0)

    def test_dead_truth_liveness_fails_responds(self):
        result = channels.score_o1_runnable(
            self._cold(), self._probe(reached=False), self._capture(),
            mode="X", truth=self._truth(live=False),
        )
        rungs = {i.id: i for i in result.items}
        self.assertIs(rungs["O1/responds_to_input"].verdict, Verdict.FAILED)

    def test_frozen_3d_platformer_snapshot_fills_the_skipped_rungs(self):

        snap_path = (
            Path(__file__).resolve().parents[2] / "tasks" / "3d_platformer" / "snapshot.json"
        )
        if not snap_path.is_file():
            self.skipTest(f"no frozen snapshot at {snap_path}")
        truth = TruthSnapshot.read(snap_path)
        result = channels.score_o1_runnable(
            self._cold(), self._probe(reached=False), self._capture(),
            mode="X", truth=truth,
        )
        rungs = {i.id: i for i in result.items}
        self.assertIs(rungs["O1/boots"].verdict, Verdict.PASSED, rungs["O1/boots"].detail)
        self.assertIs(rungs["O1/responds_to_input"].verdict, Verdict.PASSED,
                      rungs["O1/responds_to_input"].detail)
        self.assertAlmostEqual(result.interval.lo, 1.0)


# tool attribution


class ToolFailureAttribution(unittest.TestCase):
    def test_exit_one_is_the_tool_working(self):
        self.assertIs(adapters._classify_exit(0, False), adapters.ToolOutcome.REPORTED)
        self.assertIs(adapters._classify_exit(1, False), adapters.ToolOutcome.REPORTED)

    def test_exit_two_and_timeouts_are_ours(self):
        self.assertIs(adapters._classify_exit(2, False), adapters.ToolOutcome.CRASHED)
        self.assertIs(adapters._classify_exit(-9, True), adapters.ToolOutcome.TIMED_OUT)
        self.assertFalse(adapters.ToolOutcome.CRASHED.ran)

    def test_a_crashed_legacy_static_gate_is_not_an_o2_input(self):
        import inspect

        parameters = tuple(inspect.signature(channels.score_o2_interface).parameters)
        self.assertEqual(("report", "requirements"), parameters)

    def test_tool_hash_is_recorded_for_every_wrapped_tool(self):
        for tool in (adapters.CHECK_DELIVERABLES, adapters.CHECK_RUNTIME,
                     adapters.FRAME_AUDIT, adapters.SCAN_LOCKED, adapters.RUNTIME_PROBE):
            if tool.is_file():
                digest = adapters.sha256_file(tool)
                self.assertEqual(len(digest), 64)

    def test_a_relative_project_reaches_the_tools_as_an_absolute_path(self):


        seen: list[list[str]] = []

        def fake_run(argv, **kwargs):
            seen.append(list(argv))
            return _invocation(), "", ""

        project = Path(__file__).resolve().parents[2] / "tasks"
        relative = os.path.relpath(project, Path.cwd())
        original = adapters._run
        adapters._run = fake_run
        try:
            for call in (
                lambda p: adapters.run_check_deliverables(p, skip_locked=True),
                lambda p: adapters.run_check_runtime(p),
                lambda p: adapters.run_scan_locked_assets(p),
            ):
                seen.clear()
                try:
                    call(relative)
                except adapters.ToolUnavailable:
                    continue
                self.assertTrue(seen, "adapter did not invoke its tool")
                handed = Path(seen[0][2])
                self.assertTrue(handed.is_absolute(), seen[0])
                self.assertEqual(handed, project)
        finally:
            adapters._run = original

    def test_a_crashed_first_import_is_retried_before_the_capture_is_disowned(self):


        calls: list[list[str]] = []

        def flaky_run(argv, **kwargs):
            calls.append(list(argv))
            crashed = len(calls) == 1
            return (
                adapters.ToolInvocation(
                    tool="godot", tool_path="/godot", tool_sha256="0" * 64,
                    argv=list(argv), cwd="/tmp",
                    returncode=3221225477 if crashed else 0, seconds=0.0,
                    outcome=(adapters.ToolOutcome.CRASHED if crashed
                             else adapters.ToolOutcome.REPORTED),
                ),
                "", "",
            )

        with tempfile.TemporaryDirectory() as tmp:
            project = Path(tmp)
            (project / "project.godot").write_text("[application]\n", encoding="utf-8")
            (project / "level_1.tscn").write_text("[gd_scene]\n", encoding="utf-8")
            (project / "gb_levels.json").write_text(
                json.dumps({"levels": ["res://level_1.tscn"]}), encoding="utf-8")
            original = adapters._run
            adapters._run = flaky_run
            try:
                capture = adapters.capture_level_frames(project)
            finally:
                adapters._run = original

        imports = [c for c in calls if "--import" in c]
        self.assertEqual(2, len(imports), "the crashed import was not retried")
        self.assertTrue(capture.imported_first)

    def test_fingerprint_is_content_only(self):


        with tempfile.TemporaryDirectory() as d:
            p = Path(d) / "a.txt"
            p.write_text("hello", encoding="utf-8")
            before = adapters.fingerprint_tree(d)
            os.utime(p, (0, 0))
            self.assertEqual(before, adapters.fingerprint_tree(d))
            p.write_text("hello\n", encoding="utf-8")
            self.assertNotEqual(before, adapters.fingerprint_tree(d))


class OtherChannels(unittest.TestCase):
    def test_o6_is_unobservable_at_d1_not_failed(self):
        result = channels.score_o6_asset_use("D1")
        self.assertTrue(all(i.verdict is Verdict.UNOBSERVABLE for i in result.items))
        self.assertEqual(result.interval.denominator, 0.0)

    def test_o6_scores_coverage_when_a_pack_was_supplied(self):
        result = channels.score_o6_asset_use(
            "D2", supplied_assets=["a.png", "b.png"], observed_assets=["a.png"])
        self.assertAlmostEqual(result.interval.lo, 0.5)
        self.assertEqual(["a.png"], result.evidence["observed_resource_paths"])

    def test_o6_credits_a_supplied_asset_copied_under_a_new_name(self):


        import hashlib
        import tempfile
        from pathlib import Path

        with tempfile.TemporaryDirectory() as tmp:
            pack = Path(tmp) / "assets" / "Ships"
            pack.mkdir(parents=True)
            ship = pack / "ship_0000.png"
            ship.write_bytes(b"\x89PNG ship zero")
            tile = pack / "tile_0006.png"
            tile.write_bytes(b"\x89PNG tile six")
            unused = pack / "ship_0016.png"
            unused.write_bytes(b"\x89PNG ship sixteen")
            digest = hashlib.sha256(ship.read_bytes()).hexdigest()
            result = channels.score_o6_asset_use(
                "D3",
                supplied_assets=[str(ship), str(tile), str(unused)],
                observed_assets=["res://art/ship_player.png", "res://scripts/stage.gd"],
                observed_digests={
                    "res://art/ship_player.png": digest,
                    "res://scripts/stage.gd": "0" * 64,
                },
            )
        by_id = {item.id: item for item in result.items}
        self.assertIs(by_id["O6/uses[ship_0000.png]"].verdict, Verdict.PASSED)
        self.assertIn("res://art/ship_player.png", by_id["O6/uses[ship_0000.png]"].detail)
        self.assertIs(by_id["O6/uses[tile_0006.png]"].verdict, Verdict.FAILED)
        self.assertIs(by_id["O6/uses[ship_0016.png]"].verdict, Verdict.FAILED)
        self.assertAlmostEqual(result.interval.lo, 1 / 3)
        self.assertIn("1 matched by content", result.detail)
        self.assertEqual(
            {"ship_0000.png": "res://art/ship_player.png"}, result.evidence["matched"]
        )

    def test_o6_probe_retries_a_timed_out_import_once(self):
        from types import SimpleNamespace
        from unittest import mock

        from evalsys.probe import assets
        from evalsys.probe.inject import ImportResult

        interface = SimpleNamespace(project_root=Path("/cell/submission/game"), levels=[])
        prep = SimpleNamespace(
            scratch=SimpleNamespace(ok=True), inject=SimpleNamespace(ok=True)
        )
        timed_out = ImportResult("/s", False, "cold import did not finish within 600s", 0, 600.0, True)
        clean = ImportResult("/s", True, "cold import clean in 4.5s", 0, 4.5, False)
        with (
            mock.patch.object(assets, "prepare_scratch", return_value=prep),
            mock.patch.object(assets, "inject_autoload", return_value=SimpleNamespace(ok=True)),
            mock.patch.object(assets, "write_runtime_interface", return_value=Path("/s/iface.json")),
            mock.patch.object(assets, "cold_import", side_effect=[timed_out, clean]) as imp,
        ):
            observation = assets.run_asset_probe("/cell/submission/game", interface)
        self.assertEqual(2, imp.call_count)


        self.assertEqual("normalized interface has no level address", observation.detail)
        with (
            mock.patch.object(assets, "prepare_scratch", return_value=prep),
            mock.patch.object(assets, "inject_autoload", return_value=SimpleNamespace(ok=True)),
            mock.patch.object(assets, "cold_import", side_effect=[timed_out, timed_out]),
        ):
            observation = assets.run_asset_probe("/cell/submission/game", interface)
        self.assertFalse(observation.report_produced)
        self.assertIn("after one retry", observation.detail)

    def test_o6_probe_digests_the_bytes_behind_observed_res_paths(self):
        import hashlib
        import tempfile
        from pathlib import Path

        from evalsys.probe.assets import digest_resources

        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            (root / "art").mkdir()
            (root / "art" / "ship_player.png").write_bytes(b"bytes")
            digests = digest_resources(
                root,
                ["res://art/ship_player.png", "res://missing.png", "user://cache.bin"],
            )
        self.assertEqual(
            {"res://art/ship_player.png": hashlib.sha256(b"bytes").hexdigest()}, digests
        )

    def test_the_register_is_exactly_o1_to_o9_and_sums_to_the_o_card_share(self):


        from evalsys.interface.contract import CHANNEL_INPUTS
        from evalsys.weights import O_CARD_SHARE, REGISTRY_VERSION, RETIRED_CHANNELS

        self.assertEqual([f"O{i}" for i in range(1, 10)], sorted(
            CHANNELS, key=lambda c: int(c[1:])))
        self.assertAlmostEqual(O_CARD_SHARE, sum(c.weight for c in CHANNELS.values()), places=9)
        self.assertEqual(0.9, O_CARD_SHARE)
        self.assertEqual(set(CHANNELS), set(CHANNEL_INPUTS))
        self.assertIn("O10", RETIRED_CHANNELS)
        self.assertNotIn("O10", CHANNELS)
        self.assertNotIn("O10", channels.STUB_CHANNELS)
        self.assertIn("O8", channels.STUB_CHANNELS)
        self.assertFalse(hasattr(channels, "score_o10_context"))
        self.assertFalse(hasattr(channels, "score_o10a_audio_presence"))
        self.assertEqual("2026-09-04.0", REGISTRY_VERSION)
        with self.assertRaises(KeyError):
            channels.stub_channel("O10")

    def test_o10_weight_went_proportionally_to_o1_through_o9(self):


        expected = {"O1": 0.063, "O2": 0.072, "O3": 0.180, "O4": 0.099, "O5": 0.081,
                    "O6": 0.063, "O7": 0.126, "O8": 0.126, "O9": 0.090}
        self.assertEqual(expected, {cid: ch.weight for cid, ch in CHANNELS.items()})

    def test_a_stored_o10_row_is_ignored_when_an_old_card_is_rescored(self):


        from evalsys.verdict import unobservable

        base = [
            channels._result("O1", [passed("a")]),
            channels._result("O2", [passed("b")]),
        ]
        stale = base + [channels._result(
            "O10", [unobservable("O10/live_audio", detail="task has no O10 expectation")])]
        fresh = scorer.score_ocard(base, "D3", "Mv", 1.0, project="fresh")
        old = scorer.score_ocard(stale, "D3", "Mv", 1.0, project="old")
        self.assertEqual(fresh.score_lo, old.score_lo)
        self.assertEqual(fresh.measured_weight_share, old.measured_weight_share)
        self.assertNotIn("O10", old.per_channel)
        self.assertNotIn("O10", old.unobservable_channels)
        self.assertTrue(any("O10 row ignored" in n for n in old.notes))
        by_map = scorer.score_ocard(
            {"O1": base[0].interval, "O10": stale[-1].interval}, "D3", "Mv", 1.0)
        self.assertNotIn("O10", by_map.per_channel)

    def test_stubs_are_unmeasurable_and_say_what_is_missing(self):
        for cr in channels.stub_channels():
            self.assertEqual(cr.interval.denominator, 0.0)
            self.assertTrue(cr.items[0].detail)
            self.assertFalse(CHANNELS[cr.channel].never_unmeasurable)

    def test_o2_may_never_be_stubbed(self):
        with self.assertRaises(KeyError):
            channels.stub_channel("O2")

    def test_o9_measures_at_full_resolution(self):
        import numpy as np
        from PIL import Image

        with tempfile.TemporaryDirectory() as d:
            arr = np.zeros((90, 120, 3), dtype=np.uint8)
            arr[40:50, 55:65] = 255
            path = Path(d) / "anchor.png"
            Image.fromarray(arr).save(path)

            spec = channels.AnchorSpec(frame_path=str(path), expected_bearing="centre",
                                       expected_mass_ratio=0.0045, mass_tolerance=0.30,
                                       expect_mirror_symmetry=True)
            result = channels.score_o9_anchor_composition(spec)
            self.assertEqual(result.evidence["bearing"], "centre")
            self.assertFalse(result.evidence["downsampled"])
            self.assertEqual(result.evidence["resolution"], "120x90")
            verdicts = {i.id: i.verdict for i in result.items}
            self.assertIs(verdicts["O9/bearing"], Verdict.PASSED)
            self.assertIs(verdicts["O9/mirror_similarity"], Verdict.PASSED)

    def test_o9_without_a_registered_expectation_does_not_invent_one(self):
        import numpy as np
        from PIL import Image

        with tempfile.TemporaryDirectory() as d:
            arr = np.zeros((40, 40, 3), dtype=np.uint8)
            arr[5:10, 5:10] = 200
            path = Path(d) / "a.png"
            Image.fromarray(arr).save(path)
            result = channels.score_o9_anchor_composition(
                channels.AnchorSpec(frame_path=str(path)))
            self.assertTrue(all(i.verdict is Verdict.UNMEASURABLE for i in result.items),
                            "a threshold picked after the measurement is not a test")


class Scoring(unittest.TestCase):
    def _card(self, **kw) -> scorer.OCardResult:
        chans = [
            channels._result("O1", [passed("a"), passed("b")]),
            channels._result("O2", [passed("c")]),
            channels.score_o6_asset_use("D1"),
        ] + [channels.score_o7_authored_clear()] + channels.stub_channels()
        aset = AssertionSet("t", [])
        ceiling = aset.ceiling("D1", "Mv", channel_overrides=DEFAULT_CHANNEL_OVERRIDES["D1"])
        return scorer.score_ocard(chans, "D1", "Mv", ceiling, project="t", **kw)

    def test_shortcut_is_reported_beside_the_score_not_inside_it(self):
        clean = self._card()
        cheat = self._card(shortcuts=[scorer.ShortcutFinding("teleport", "skipped the level")])
        self.assertEqual(clean.score_lo, cheat.score_lo)
        self.assertFalse(clean.shortcut_detected)
        self.assertTrue(cheat.shortcut_detected)

    def test_unmeasurable_channels_are_named_not_zeroed(self):


        card = self._card()
        self.assertIn("O7", card.unmeasurable_channels)
        self.assertIn("O6", card.unobservable_channels)
        for cid in card.unmeasurable_channels:
            self.assertFalse(card.per_channel[cid]["counted"])

    def test_unmeasured_channel_is_a_hole_not_a_dropped_denominator(self):


        chans_common = [
            channels._result("O1", [passed("a"), passed("b")]),
            channels._result("O2", [passed("c")]),
        ] + channels.stub_channels()
        aset = AssertionSet("t", [])
        ceiling = aset.ceiling("D3", "Mv", channel_overrides=DEFAULT_CHANNEL_OVERRIDES["D3"])
        low = scorer.score_ocard(
            chans_common + [channels._result("O6", [passed("x"), failed("y"), failed("z")])],
            "D3", "Mv", ceiling, project="low")
        hole = scorer.score_ocard(
            chans_common + [channels._result(
                "O6", [unmeasurable("O6/asset_probe", detail="probe crashed")])],
            "D3", "Mv", ceiling, project="hole")
        self.assertIn("O6", hole.unmeasurable_channels)
        self.assertNotIn("O6", low.unmeasurable_channels)
        self.assertEqual(low.denominator, hole.denominator)
        self.assertLess(hole.score_lo, low.score_lo)
        self.assertGreater(hole.score_hi, hole.score_lo)
        self.assertGreater(low.coverage, hole.coverage)

    def test_unimplemented_stub_is_unobservable_not_a_permanent_hole(self):
        card = self._card()
        self.assertIn("O8", card.unobservable_channels)
        self.assertNotIn("O8", card.unmeasurable_channels)
        for item in channels.stub_channel("O8").items:
            self.assertIs(item.verdict, Verdict.UNOBSERVABLE)

    def test_partial_cards_are_flagged(self):
        card = self._card()
        self.assertLess(card.measured_weight_share, 0.8)
        self.assertTrue(any("registered O-card weight" in n for n in card.notes))


    def test_ranking_uses_score_lo(self):
        wide = scorer.OCardResult(
            project="wide", tier="D1", modality="Mv", score_lo=0.4, score_hi=0.9,
            coverage=0.5, denominator=1.0, ceiling=1.0, normalised_lo=0.4, normalised_hi=0.9,
            restricted_ceiling=1.0, normalised_lo_restricted=0.4, measured_weight_share=1.0,
            per_channel={}, unmeasurable_channels=[], unobservable_channels=[],
            inconclusive_rate=0.0, unattributable_error_rate=0.0, shortcut_detected=False,
            shortcuts=[], low_coverage_channels=[], low_coverage_total=False, by_verdict={})
        tried = scorer.OCardResult(
            project="tried", tier="D1", modality="Mv", score_lo=0.5, score_hi=0.5,
            coverage=1.0, denominator=1.0, ceiling=1.0, normalised_lo=0.5, normalised_hi=0.5,
            restricted_ceiling=1.0, normalised_lo_restricted=0.5, measured_weight_share=1.0,
            per_channel={}, unmeasurable_channels=[], unobservable_channels=[],
            inconclusive_rate=0.0, unattributable_error_rate=0.0, shortcut_detected=False,
            shortcuts=[], low_coverage_channels=[], low_coverage_total=False, by_verdict={})
        self.assertEqual([r.project for r in scorer.rank([wide, tried])], ["tried", "wide"],
                         "skipping must be strictly worse than trying and failing")

    def test_unregistered_channel_raises_rather_than_inventing_a_weight(self):
        with self.assertRaises(KeyError):
            scorer.score_ocard({"O99": channels._result("O1", [passed("x")]).interval},
                               "D1", "Mv", 1.0)

    def test_artifact_round_trips(self):
        card = self._card()
        with tempfile.TemporaryDirectory() as d:
            path = scorer.write_artifact(card, d, provenance={"tool": "hash"})
            blob = json.loads(Path(path).read_text(encoding="utf-8"))
            self.assertEqual(blob["ocard"]["project"], "t")
            self.assertIn("provenance", blob)
            self.assertIn("shortcut_detected", blob["ocard"])


if __name__ == "__main__":
    unittest.main()


class PartialCardsNormaliseAgainstWhatTheyMeasured(unittest.TestCase):


    def _card(self, *, restricted: float | None) -> Card:
        card = Card(submission="s", task_id="t", tier="D1",
                    ocard=Interval(0.949, 1.0, 0.95, 0.6),
                    ceiling_total=0.939, restricted_ceiling=restricted)
        for cid in ("O1", "O2", "O3", "O4", "O5", "O7"):
            card.channels.append(ChannelRecord(
                channel=cid, interval=Interval(1.0, 1.0, 1.0, 1.0),
                weight=CHANNELS[cid].weight))
        for cid in ("O6", "O8", "O9"):
            card.channels.append(ChannelRecord(
                channel=cid, interval=Interval(0.0, 0.0, 0.0, 0.0),
                weight=CHANNELS[cid].weight))
        return card

    def test_the_whole_card_ceiling_is_what_produced_1_011(self):
        card = self._card(restricted=None)
        self.assertTrue(card.is_partial)
        self.assertGreater(card.normalised_lo, 1.0)

    def test_the_restricted_ceiling_puts_it_back_below_one(self):
        card = self._card(restricted=1.0)
        self.assertTrue(card.is_partial)
        self.assertAlmostEqual(card.normalised_lo, 0.949, places=3)
        self.assertAlmostEqual(card.normalising_ceiling, 1.0, places=6)

    def test_a_partial_card_is_refused_by_the_main_table(self):
        ok, why = self._card(restricted=1.0).main_table_eligible
        self.assertFalse(ok)
        self.assertIn("partial-card", why)

    def test_a_complete_card_uses_the_whole_ceiling_and_is_eligible(self):
        card = Card(submission="s", task_id="t", tier="D1",
                    ocard=Interval(0.8, 0.8, 1.0, 1.0),
                    ceiling_total=0.939, restricted_ceiling=0.939)
        for cid, ch in CHANNELS.items():
            card.channels.append(ChannelRecord(
                channel=cid, interval=Interval(1.0, 1.0, 1.0, 1.0), weight=ch.weight))
        self.assertFalse(card.is_partial)
        self.assertAlmostEqual(card.normalising_ceiling, 0.939, places=6)
        self.assertTrue(card.main_table_eligible[0])

    def test_the_value_survives_a_write_and_read(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = self._card(restricted=1.0).write(Path(tmp) / "c.json")
            back = Card.read(path)
        self.assertEqual(back.restricted_ceiling, 1.0)
        self.assertAlmostEqual(back.normalised_lo, 0.949, places=3)
