

from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from evalsys.taskgen.evaluate import (
    evaluate_task,
    _unity_hidden_behavior_items,
    _unity_mechanic_trace_item,
    _unity_runtime_items,
    _unity_runtime_stability_item,
)
from evalsys.taskgen.package import TaskPackage, write_json
from evalsys.taskgen.scorecard import MODE5_RELEASE_REGISTRY_VERSION


from evalsys.taskgen.submission import load_unity_submission, parse_unity_ops
from evalsys.taskgen.unity.unity_interface import MANIFEST_RELATIVE, UNITY_ACTIONS
from evalsys.taskgen.unity.unity_runtime import UnityBuildResult
from evalsys.taskgen.unity.unity_probe import PROTOCOL, UnityProbeRun, UnityRuntimeSuite
from evalsys.taskgen.unity.unity_sdk import build_scaffold_digest_manifest
from evalsys.interface.model import AnalogAxis
from evalsys.verdict import Attribution, Verdict


def _unity_submission(root: Path) -> Path:
    submission = root / "submission"
    project = submission / "game"
    (project / "Assets" / "Scenes").mkdir(parents=True)
    (project / "Assets" / "GameBenchmark").mkdir(parents=True)
    (project / "Packages").mkdir()
    (project / "ProjectSettings").mkdir()
    (project / "ProjectSettings" / "ProjectVersion.txt").write_text(
        "m_EditorVersion: 6000.3.23f1\n", encoding="utf-8"
    )
    for name in ("Level1", "Victory", "Defeat"):
        (project / "Assets" / "Scenes" / f"{name}.unity").write_text(
            "%YAML 1.1\n", encoding="utf-8"
        )
    write_json(
        project / MANIFEST_RELATIVE,
        {
            "schema_version": 1,
            "engine": "unity",
            "levels": [{"id": "L1", "scene": "Assets/Scenes/Level1.unity"}],
            "actions": {
                action: f"Player/{action}" for action in UNITY_ACTIONS
            },
            "objects": {
                "gb_player": [{"level": "L1", "path": "World/Player"}],
                "gb_goal": [{"level": "L1", "path": "World/Goal"}],
            },
            "numeric": {
                "health": {
                    "level": "L1",
                    "path": "World/Player",
                    "component": "PlayerHealth",
                    "member": "Current",
                }
            },
            "endings": {
                "success": {"scene": "Assets/Scenes/Victory.unity"},
                "failure": {"scene": "Assets/Scenes/Defeat.unity"},
            },
        },
    )
    write_json(
        submission / "ops.json",
        {"ops": [{"op": "tap", "action": "gb_attack", "frames": 6}]},
    )
    (submission / "BUILD.md").write_text(
        "# Linux build\n\n"
        "Unity editor: 6000.3.23f1\n\n"
        "`Unity -batchmode -quit -projectPath . -executeMethod BuildLinux`\n\n"
        "Player output: `Build/Linux/game.x86_64`\n",
        encoding="utf-8",
    )
    return submission


def _package(root: Path) -> TaskPackage:
    package = TaskPackage(
        root / "package",
        {
            "schema_version": 1,
            "mode": "port",
            "mode_number": 5,
            "game_id": "fixture",
            "blockers": [],
            "score_against_source": True,
        },
    )
    package.visible.mkdir(parents=True)
    package.hidden.mkdir(parents=True)
    (package.visible / "PROMPT.md").write_text("Port this game.\n", encoding="utf-8")
    write_json(
        package.hidden / "oracle.json",
        {
            "task_gdd_audit": {"ready": True, "errors": []},
            "rubric_audit": {"ready": True, "errors": []},
            "rubric": {
                "min_levels": 1,
                "required_actions": ["gb_left", "gb_jump"],
                "required_extended_actions": [],
                "required_analog_axes": [],
                "required_groups": ["gb_player", "gb_goal"],
                "required_numeric_slots": ["health"],
                "mechanic_checks": [{"id": "damage", "measurable": True, "observable": {"predicate": "numeric_delta(health) < 0"}}],
                "require_failure_ending": True,
            },
        },
    )
    package.write_manifest()
    return package


class UnitySubmissionTests(unittest.TestCase):
    def test_loader_finds_nested_project_and_submission_level_files(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            submission = _unity_submission(Path(tmp))
            loaded = load_unity_submission(submission)
            self.assertEqual("unity", loaded.engine)
            self.assertEqual((submission / "game").resolve(), loaded.project)
            self.assertEqual((submission / "ops.json").resolve(), loaded.ops_path)
            self.assertEqual((submission / "BUILD.md").resolve(), loaded.build_path)
            self.assertEqual("gb_attack", loaded.ops[0].action)

    def test_unity_tape_accepts_attack_dash_but_not_godot_lifecycle_actions(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            tape = root / "ops.json"
            write_json(
                tape,
                {"ops": [
                    {"op": "tap", "action": "gb_attack", "frames": 4},
                    {"op": "hold", "action": "gb_dash", "frames": 8},
                ]},
            )
            self.assertEqual(
                ["gb_attack", "gb_dash"],
                [item.action for item in parse_unity_ops(tape)],
            )
            write_json(
                tape,
                {"ops": [{"op": "tap", "action": "gb_reset", "frames": 4}]},
            )
            with self.assertRaisesRegex(ValueError, "forbidden_action"):
                parse_unity_ops(tape)
            write_json(
                tape,
                {"ops": [{"op": "tap", "action": "unity_attack", "frames": 4}]},
            )
            with self.assertRaisesRegex(ValueError, "unknown_action"):
                parse_unity_ops(tape)

    def test_unity_ops_accept_only_manifest_frozen_extensions_and_axes(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            tape = Path(tmp) / "ops.json"
            write_json(tape, {"ops": [{
                "op": "hold",
                "actions": ["gb_attack", "reload"],
                "axes": {"look_x": 0.76},
                "frames": 8,
            }]})
            ops = parse_unity_ops(
                tape,
                extra_actions=("reload",),
                extra_axes=(AnalogAxis("look_x", "aim", -1.0, 1.0, 9),),
            )
            self.assertEqual(("gb_attack", "reload"), ops[0].actions)
            self.assertEqual((("look_x", 0.75),), ops[0].axes)
            with self.assertRaisesRegex(ValueError, "unknown_action"):
                parse_unity_ops(tape, extra_axes=(AnalogAxis("look_x", "aim", -1, 1, 9),))


class Mode5EvaluatorTests(unittest.TestCase):
    def test_failed_witness_does_not_create_vlm_or_structure_judgments(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            suite = UnityRuntimeSuite(
                "pass", "fixture", self._probe_run(root, "witness", reached=False),
                self._probe_run(root, "null", reached=False), None,
            )
            items = _unity_runtime_items(suite, rubric={}, visual_judge="none", out=root,
                                         game_id="fixture", levels=[], reference_video=None, task_context="")
            by_id = {item.id: item for item in items}
            self.assertEqual(Verdict.FAILED, by_id["causal_witness"].verdict)
            self.assertNotIn("unity_structure_fidelity", by_id)

    def test_unity_mechanics_are_graded_from_rubric_not_probe_completion(self) -> None:
        run = UnityProbeRun(
            "run", "witness", "pass", "ok", (), 0, "plan", "result", "log",
            reading={
                "rows": [
                    {"f": 0, "g": {"gb_enemy": 0}, "n": {"health": 100}},
                    {"f": 1, "g": {"gb_enemy": 1}, "n": {"health": 100}},
                    {"f": 2, "g": {"gb_enemy": 0}, "n": {"health": 90}},
                    {"f": 3, "g": {"gb_enemy": 0}, "n": {"health": 90}, "wgc": True},
                ],
                "group_totals": {"gb_enemy": 1, "gb_hazard": 1},
                "stop_reason": "goal_reached",
            },
        )
        rubric = {"mechanic_checks": [
            {"id": "enemy", "measurable": True, "observable": {
                "kind": "trace_checkpoint", "predicate": "count_delta(gb_enemy) < 0",
                "baseline": "previous_row",
            }},
            {"id": "hazard", "measurable": True, "observable": {
                "kind": "trace_checkpoint", "predicate": "numeric_delta(health) < 0",
                "baseline": "previous_row", "trigger": "overlap(gb_player, gb_hazard)",
            }},
        ]}
        item = _unity_mechanic_trace_item(run, rubric)
        self.assertEqual(Verdict.PASSED, item.verdict)
        self.assertEqual(0.5, item.credit)
        self.assertEqual(["enemy"], item.evidence["reached"])



    def test_runtime_stability_grades_clean_independent_cold_runs(self) -> None:
        from dataclasses import replace
        root = Path("fixture")
        clean = self._probe_run(root, "witness", reached=True)
        noisy = replace(
            self._probe_run(root, "behavior", reached=False, behavior=True),
            reading={"rows": [{"f": 0}], "errors": ["candidate exception"]},
        )
        suite = UnityRuntimeSuite(
            "pass", "fixture", clean, clean, None, hidden_behaviors=(noisy,)
        )
        item = _unity_runtime_stability_item(suite)
        self.assertEqual(Verdict.PASSED, item.verdict)
        self.assertEqual(0.5, item.credit)
        self.assertEqual([True, False], [row["clean"] for row in item.evidence["runs"]])

    def test_unmeasurable_ablation_cannot_pass(self) -> None:
        from dataclasses import replace
        root = Path("fixture")
        base = self._probe_run(root, "witness", reached=True)
        negative = self._probe_run(root, "ablation", reached=False, counterfactual=True)
        for payload in (
            {},
            {"status": "inconclusive", "goal_reached": False},
            {"status": "fail", "goal_reached": False, "missing_observations": ["device:goal#0"]},
            {"status": "fail", "goal_reached": "false"},
        ):
            with self.subTest(payload=payload):
                run = replace(negative, reading={**negative.reading, "behavior_result": payload})
                suite = UnityRuntimeSuite("pass", "fixture", base, base, None,
                                          counterfactuals=(run,))
                item = next(i for i in _unity_hidden_behavior_items(suite) if i.id == "unity_counterfactual")
                expected = (
                    Verdict.UNOBSERVABLE
                    if payload.get("missing_observations") else Verdict.INCONCLUSIVE
                )
                self.assertEqual(expected, item.verdict)

    def test_missing_candidate_input_control_is_not_infrastructure_inconclusive(self) -> None:
        from dataclasses import replace

        root = Path("fixture")
        base = self._probe_run(root, "witness", reached=False)
        ablation = replace(
            self._probe_run(root, "ablation", reached=False, counterfactual=True),
            status="fail",
            detail=(
                "controller protocol failed: input dispatch failed: "
                "action has no enabled ButtonControl: gb_right"
            ),
            reading={},
        )
        suite = UnityRuntimeSuite(
            "pass", "fixture", base, base, None, counterfactuals=(ablation,)
        )
        item = next(
            item for item in _unity_hidden_behavior_items(suite)
            if item.id == "unity_counterfactual"
        )
        self.assertEqual(Verdict.FAILED, item.verdict)
        self.assertEqual(Attribution.SUBMISSION, item.attribution)

        runtime_failure = replace(
            ablation,
            detail=("Unity runtime emitted errors: Scene 'ResultScreen' "
                    "couldn't be loaded because it has not been added to the build"),
        )
        suite = replace(suite, counterfactuals=(runtime_failure,))
        item = next(
            item for item in _unity_hidden_behavior_items(suite)
            if item.id == "unity_counterfactual"
        )
        self.assertEqual(Verdict.FAILED, item.verdict)
        self.assertEqual(Attribution.SUBMISSION, item.attribution)

        transport_failure = replace(ablation, detail="controller protocol failed: socket closed")
        suite = replace(suite, counterfactuals=(transport_failure,))
        item = next(
            item for item in _unity_hidden_behavior_items(suite)
            if item.id == "unity_counterfactual"
        )
        self.assertEqual(Verdict.INCONCLUSIVE, item.verdict)

    def test_source_scenario_failure_keeps_source_provenance(self) -> None:
        from dataclasses import replace

        root = Path("fixture")
        base = self._probe_run(root, "witness", reached=False)
        source = replace(
            self._probe_run(root, "source", reached=False, behavior=True),
            status="fail",
            detail="controller protocol failed: candidate input unavailable",
            reading={"evidence_basis": ["source_derived"]},
        )
        suite = UnityRuntimeSuite(
            "pass", "fixture", base, base, None, hidden_behaviors=(source,)
        )
        item = next(
            item for item in _unity_hidden_behavior_items(suite)
            if item.id == "unity_source_behavior"
        )
        self.assertEqual(Verdict.FAILED, item.verdict)

    @staticmethod
    def _probe_run(
        root: Path,
        run_id: str,
        *,
        reached: bool,
        behavior: bool = False,
        source_derived: bool = False,
        counterfactual: bool = False,
    ) -> UnityProbeRun:
        scene = "Assets/Scenes/Victory.unity" if reached else "Assets/Scenes/Level1.unity"
        return UnityProbeRun(
            run_id=run_id,
            kind="hidden_route" if run_id.startswith("fixture/") else run_id,
            status="pass",
            detail="fixture probe passed",
            command=("/fake/player",),
            returncode=0,
            plan_path=str(root / run_id.replace("/", "_") / "plan.json"),
            result_path=str(root / run_id.replace("/", "_") / "result.json"),
            log_path=str(root / run_id.replace("/", "_") / "player.log"),
            reached=reached,
            reading={
                "schema": PROTOCOL,
                "run_id": run_id,
                "completed": True,
                "final_scene": scene,
                "scenes_visited": ["Assets/Scenes/Level1.unity", scene],
                "rows": [
                    {
                        "f": 0,
                        "g": {"gb_enemy": 0 if reached else 1},
                        "o": {"gb_enemy": bool(reached)},
                        "px": 1 if reached else 0,
                        "py": 0,
                        "pz": 0,
                        "wgc": bool(reached),
                        "lv": 1,
                        "d": {},
                        "c": [],
                    }
                ],
                "events": [
                    {
                        "kind": "input_dispatched", "actions": ["gb_action"], "axes": {},
                        "resolved_device_count": 1, "enabled_device_count": 1,
                    },
                    {
                        "kind": "input_dispatched", "actions": [], "axes": {},
                        "resolved_device_count": 1, "enabled_device_count": 1,
                    },
                ],
                **(
                    {
                        "behavior_result": {
                            "status": "fail" if counterfactual else "pass",
                            "goal_reached": reached,
                        },
                        "evidence_basis": [
                            "source_derived" if source_derived else "gdd_explicit"
                        ],
                    }
                    if behavior or counterfactual
                    else {}
                ),
                **(
                    {
                        "counterfactual_id": "remove_action",
                        "counterfactual_expect_goal": False,
                    }
                    if counterfactual
                    else {}
                ),
            },
        )

    def test_static_pass_is_runtime_inconclusive_not_resolved(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            package = _package(root)
            submission = _unity_submission(root)
            result = evaluate_task(
                package.root, submission, engine="off", visual_judge="none"
            )
            by_id = {item.id: item for item in result.items}

            for check_id in (
                "unity_layout",
                "unity_interface",
                "task_gdd_contract",
                "port_contract_alignment",
                "ops_present",
                "ops_valid",
                "ops_not_idle",
                "no_eval_smuggling",
                "no_bundled_godot_runtime",
                "build_recipe",
                "unity_anti_grant_static",
            ):
                self.assertEqual(Verdict.PASSED, by_id[check_id].verdict, check_id)
            self.assertEqual(
                Verdict.PASSED, by_id["verifier_profile_complete"].verdict
            )
            for check_id in (
                "unity_build",
                "unity_sdk_integrity",
                "unity_probe",
                "unity_auto_win_ready",
                "unity_input_dispatch",
                "unity_hidden_behavior",
                "unity_source_behavior",
                "unity_counterfactual",
                "legacy_reference_trace",
                "causal_witness",
                "null_no_win",
                "unity_evaluator_capture",
            ):
                self.assertEqual(Verdict.INCONCLUSIVE, by_id[check_id].verdict, check_id)
            self.assertEqual("not_measured", result.eligibility_status)
            self.assertFalse(result.comparable)
            self.assertIsNone(result.resolved)
            wire = result.to_dict()
            self.assertEqual("gamebench.taskgen.report.v4", wire["report_schema"])
            self.assertEqual("mode5_evidence_adjusted_proxy", wire["score_scope"])
            self.assertEqual("integrity_failed", wire["headline"]["status"])
            self.assertIsNone(wire["headline"]["score"])
            self.assertFalse(wire["headline"]["ranking_eligible"])
            self.assertEqual(wire["headline"], wire["score"])
            self.assertEqual("behavior_only_diagnostic", wire["behavior"]["score_scope"])

    def test_unrequired_manifest_extensions_are_explicitly_non_scoring(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            package = _package(root)
            submission = _unity_submission(root)
            manifest_path = submission / "game" / MANIFEST_RELATIVE
            payload = json.loads(manifest_path.read_text(encoding="utf-8"))
            payload["objects"]["gb_alert_cone"] = [
                {"level": "L1", "path": "World/Guard/AlertCone"}
            ]
            payload["numeric"]["stealth_meter"] = {
                "level": "L1",
                "path": "World/Player",
                "component": "PlayerStealth",
                "member": "Exposure",
            }
            payload["self_score"] = 100
            write_json(manifest_path, payload)

            result = evaluate_task(package.root, submission, engine="off")
            alignment = next(
                item for item in result.items if item.id == "port_contract_alignment"
            )

            self.assertEqual(Verdict.PASSED, alignment.verdict)
            self.assertEqual(
                {
                    "object_roles": ["gb_alert_cone"],
                    "numeric_slots": ["stealth_meter"],
                    "manifest_fields": ["self_score"],
                },
                alignment.evidence["unscored_declared"],
            )

    def test_high_confidence_static_grant_refuses_unity_import(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            package = _package(root)
            submission = _unity_submission(root)
            script = submission / "game" / "Assets" / "Game" / "AutoWin.cs"
            script.parent.mkdir(parents=True)
            script.write_text(
                "class AutoWin { void Start() { GBOutcome.ReportSuccess(); } }",
                encoding="utf-8",
            )
            with mock.patch("evalsys.taskgen.evaluate.build_unity_submission") as build:
                result = evaluate_task(package.root, submission, engine="on",
                                       registry_version=MODE5_RELEASE_REGISTRY_VERSION)
            by_id = {item.id: item for item in result.items}
            self.assertEqual(Verdict.FAILED, by_id["unity_anti_grant_static"].verdict)
            # Release rejects high-confidence anti-grant before importing
            # candidate code into Unity. Historical score readers stay separate.
            self.assertEqual(Verdict.SKIPPED, by_id["unity_build"].verdict)
            build.assert_not_called()

    def test_sdk_tamper_refuses_unity_import(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            package = _package(root)
            submission = _unity_submission(root)
            project = submission / "game"
            sdk_file = project / "Assets" / "GameBenchmarkSDK" / "GBEntity.cs"
            sdk_file.parent.mkdir(parents=True)
            sdk_file.write_text("class GBEntity {}\n", encoding="utf-8")
            expected = build_scaffold_digest_manifest(
                project, ["Assets/GameBenchmarkSDK/GBEntity.cs"]
            )
            write_json(
                package.hidden / "unity" / "scaffold_integrity.json",
                {"schema": "gamebench.unity-scaffold-integrity.v1", "files": expected},
            )
            sdk_file.write_text("class CandidateReplacement {}\n", encoding="utf-8")
            with mock.patch("evalsys.taskgen.evaluate.build_unity_submission") as build:
                result = evaluate_task(package.root, submission, engine="on",
                                       registry_version=MODE5_RELEASE_REGISTRY_VERSION)
            by_id = {item.id: item for item in result.items}
            self.assertEqual(Verdict.FAILED, by_id["unity_sdk_integrity"].verdict)
            self.assertEqual(Verdict.SKIPPED, by_id["unity_build"].verdict)
            build.assert_not_called()

    def test_missing_build_and_idle_ops_are_submission_failures(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            package = _package(root)
            submission = _unity_submission(root)
            (submission / "BUILD.md").unlink()
            write_json(
                submission / "ops.json",
                {"ops": [{"op": "wait", "frames": 30}]},
            )
            result = evaluate_task(package.root, submission, engine="off")
            by_id = {item.id: item for item in result.items}
            self.assertEqual(Verdict.FAILED, by_id["build_recipe"].verdict)
            self.assertEqual(Verdict.FAILED, by_id["ops_not_idle"].verdict)
            self.assertFalse(result.resolved)

    def test_incomplete_recipe_and_bundled_godot_are_static_failures(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            package = _package(root)
            submission = _unity_submission(root)
            (submission / "BUILD.md").write_text(
                "Use Unity somehow to make Linux.\n", encoding="utf-8"
            )
            (submission / "game" / "Assets" / "StreamingAssets").mkdir()
            (submission / "game" / "Assets" / "StreamingAssets" / "source.pck").write_text(
                "bundled runtime", encoding="utf-8"
            )
            result = evaluate_task(package.root, submission, engine="off")
            by_id = {item.id: item for item in result.items}
            self.assertEqual(Verdict.FAILED, by_id["build_recipe"].verdict)
            self.assertIn("editor version", by_id["build_recipe"].detail)
            self.assertEqual(
                Verdict.FAILED, by_id["no_bundled_godot_runtime"].verdict
            )

    def test_evaluator_artifact_is_refused_but_game_route_data_is_allowed(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            package = _package(root)
            submission = _unity_submission(root)
            data = submission / "game" / "Assets" / "Data"
            data.mkdir(parents=True)
            (data / "route.json").write_text("{}\n", encoding="utf-8")
            result = evaluate_task(package.root, submission, engine="off")
            by_id = {item.id: item for item in result.items}
            self.assertEqual(Verdict.PASSED, by_id["no_eval_smuggling"].verdict)
            (submission / "certificate.rt1.json").write_text("{}\n", encoding="utf-8")
            result = evaluate_task(package.root, submission, engine="off")
            by_id = {item.id: item for item in result.items}
            self.assertEqual(Verdict.FAILED, by_id["no_eval_smuggling"].verdict)

    def test_engine_on_maps_evaluator_build_result_but_does_not_fake_runtime(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            package = _package(root)
            submission = _unity_submission(root)
            build = UnityBuildResult(
                status="pass",
                detail="fixture build passed",
                unity_bin="/fake/Unity",
                requested_version="6000.3.23f1",
                observed_version="6000.3.23f1",
                command=("/fake/Unity", "-batchmode"),
                returncode=0,
                log_path=str(root / "unity.log"),
                executable=str(root / "game.x86_64"),
            )
            with mock.patch(
                "evalsys.taskgen.evaluate.build_unity_submission",
                return_value=build,
            ) as called:
                result = evaluate_task(
                    package.root,
                    submission,
                    engine="on",
                    out=root / "evaluation",
                )
            by_id = {item.id: item for item in result.items}
            self.assertEqual(Verdict.PASSED, by_id["unity_build"].verdict)
            self.assertEqual(Verdict.INCONCLUSIVE, by_id["unity_probe"].verdict)
            self.assertIsNone(result.resolved)
            called.assert_called_once()

    def test_engine_on_maps_build_failure_and_infrastructure_gap(self) -> None:
        expected = {
            "fail": Verdict.FAILED,
            "inconclusive": Verdict.INCONCLUSIVE,
        }
        for status, verdict in expected.items():
            with self.subTest(status=status), tempfile.TemporaryDirectory() as tmp:
                root = Path(tmp)
                package = _package(root)
                submission = _unity_submission(root)
                build = UnityBuildResult(
                    status=status,  # type: ignore[arg-type]
                    detail=f"fixture {status}",
                    unity_bin="/fake/Unity" if status == "fail" else None,
                    requested_version="6000.3.23f1",
                    observed_version="6000.3.23f1" if status == "fail" else None,
                    command=("/fake/Unity", "-batchmode") if status == "fail" else (),
                    returncode=1 if status == "fail" else None,
                    log_path=str(root / "unity.log") if status == "fail" else None,
                    executable=None,
                )
                with mock.patch(
                    "evalsys.taskgen.evaluate.build_unity_submission",
                    return_value=build,
                ):
                    result = evaluate_task(package.root, submission, engine="on")
                by_id = {item.id: item for item in result.items}
                self.assertEqual(verdict, by_id["unity_build"].verdict)
                self.assertEqual(
                    Verdict.INCONCLUSIVE,
                    by_id["unity_probe"].verdict,
                )

    def test_runtime_suite_can_resolve_mechanics_without_visual_compensation(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            package = _package(root)
            submission = _unity_submission(root)
            executable = root / "game.x86_64"
            executable.write_text("fixture", encoding="utf-8")
            build = UnityBuildResult(
                status="pass",
                detail="fixture build passed",
                unity_bin="/fake/Unity",
                requested_version="6000.3.23f1",
                observed_version="6000.3.23f1",
                command=("/fake/Unity", "-batchmode"),
                returncode=0,
                log_path=str(root / "unity.log"),
                executable=str(executable),
                probe_protocol=PROTOCOL,
            )
            suite = UnityRuntimeSuite(
                status="pass",
                detail="fixture suite passed",
                witness=self._probe_run(root, "submitted_witness", reached=True),
                matched_null=self._probe_run(root, "matched_null", reached=False),
                mash=None,
                hidden_routes=(self._probe_run(root, "fixture/L5/clear", reached=True),),
                hidden_behaviors=(
                    self._probe_run(
                        root,
                        "fixture/source_behavior",
                        reached=True,
                        behavior=True,
                        source_derived=True,
                    ),
                    self._probe_run(
                        root,
                        "fixture/gdd_behavior",
                        reached=True,
                        behavior=True,
                    ),
                ),
                counterfactuals=(
                    self._probe_run(
                        root,
                        "fixture/remove_action",
                        reached=False,
                        counterfactual=True,
                    ),
                ),
                auto_win=self._probe_run(root, "auto_win_ready", reached=False),
            )
            with mock.patch(
                "evalsys.taskgen.evaluate.build_unity_submission", return_value=build
            ), mock.patch(
                "evalsys.taskgen.evaluate.run_unity_runtime_suite", return_value=suite
            ):
                result = evaluate_task(
                    package.root,
                    submission,
                    engine="on",
                    visual_judge="none",
                    out=root / "evaluation",
                )
            by_id = {item.id: item for item in result.items}
            for check_id in (
                "unity_build",
                "unity_probe",
                "unity_auto_win_ready",
                "unity_input_dispatch",
                "unity_hidden_behavior",
                "unity_source_behavior",
                "unity_counterfactual",
                "legacy_reference_trace",
                "causal_witness",
                "null_no_win",
            ):
                self.assertEqual(Verdict.PASSED, by_id[check_id].verdict, check_id)
            self.assertEqual(Verdict.UNOBSERVABLE, by_id["extended_mash_no_win"].verdict)
            self.assertEqual(Verdict.INCONCLUSIVE, by_id["unity_evaluator_capture"].verdict)
            self.assertNotIn("unity_vlm", by_id)
            self.assertTrue(result.comparable)
            # The synthetic package deliberately omits a frozen scaffold
            # integrity record, so mechanics are comparable while overall
            # eligibility remains not measured.
            self.assertIsNone(result.resolved)
            self.assertTrue(result.engine["runtime_probe_ran"])


if __name__ == "__main__":
    unittest.main()
