

from __future__ import annotations

import json
import os
import tempfile
import unittest
from pathlib import Path

from evalsys.routes.agent import Op
from evalsys.routes.schema import Goal
from evalsys.taskgen.package import write_json
from evalsys.taskgen.unity.unity_behavior import (
    UnityCounterfactual,
    UnityHiddenBehaviorScenario,
)
from evalsys.taskgen.unity.unity_interface import (
    UnityEnding,
    UnityInterfaceManifest,
    UnityLevel,
)
from evalsys.taskgen.unity.unity_probe import (
    PROTOCOL,
    UnityProbeRun,
    _counterfactual_budget,
    _diagnostic_smoke_ops,
    _probe_wall_timeout,
    _select_hidden_behavior_inputs,
    run_unity_runtime_suite,
)
from evalsys.taskgen.unity.unity_environment import UnityEnvironmentProfile


LOCAL_LINUX = UnityEnvironmentProfile(
    profile_id="test-local-linux",
    environment_class="local-linux-uncertified",
    platform="test",
    graphical_profile="test-xvfb",
)


def _manifest() -> UnityInterfaceManifest:
    return UnityInterfaceManifest(
        schema_version=1,
        engine="unity",
        levels=(
            UnityLevel("L1", "Assets/Scenes/Level1.unity"),
            UnityLevel("L2", "Assets/Scenes/Level2.unity"),
        ),
        actions={
            action: "Player/" + action.removeprefix("gb_").title()
            for action in (
                "gb_left", "gb_right", "gb_up", "gb_down",
                "gb_jump", "gb_action", "gb_attack", "gb_dash",
            )
        },
        objects=(),
        numeric=(),
        endings=(
            UnityEnding("success", "Assets/Scenes/Victory.unity"),
            UnityEnding("failure", "Assets/Scenes/Defeat.unity"),
        ),
        source="fixture",
    )


def _fake_player(root: Path) -> Path:
    player = root / "fake-player"
    player.write_text(
        """#!/usr/bin/env python3
import base64, hashlib, json, socket, struct, sys

def arg(prefix):
    return next(value[len(prefix):] for value in sys.argv if value.startswith(prefix))

def send(sock, value):
    payload = json.dumps(value, separators=(',', ':')).encode()
    sock.sendall(struct.pack('!I', len(payload)) + payload)

def exact(sock, size):
    value = b''
    while len(value) < size:
        part = sock.recv(size - len(value))
        if not part: raise EOFError()
        value += part
    return value

def receive(sock):
    return json.loads(exact(sock, struct.unpack('!I', exact(sock, 4))[0]))

run_id = arg('--gb-run-id=')
frame = 6
sequence = 0
won = False
png = base64.b64decode('iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAQAAAC1HAwCAAAAC0lEQVR42mNk+A8AAQUBAScY42YAAAAASUVORK5CYII=')

def observation(sock):
    global sequence
    sequence += 1
    row = {
        'f': frame, 'g': {'gb_player': 1}, 'o': {},
        'px': float(frame), 'py': 0.0, 'pz': 0.0, 'n': {},
        'wgc': won, 'lv': 1, 'd': {}, 'c': [], 'so': '',
        'vx': 0.0, 'vy': 0.0, 'vz': 0.0,
        's': {'numeric': {}, 'audio_events': 0, 'anim': 0,
              'node_count': 2, 'visible_count': 1, 'text': 0},
    }
    events = []
    if sequence == 1:
        events.append({'kind': 'bounds_frozen', 'min': {'x': 0, 'y': 0, 'z': 0},
                       'max': {'x': 100, 'y': 10, 'z': 1}})
    if won:
        events.append({'kind': 'outcome_success', 'frame': frame})
    send(sock, {'type': 'observation', 'sequence': sequence, 'row': row, 'events': events})

with socket.create_connection(('127.0.0.1', int(arg('--gb-controller-port=')))) as sock:
    send(sock, {'type': 'hello', 'protocol': 'gamebench.unity-controller.v1',
                'run_id': run_id, 'nonce': arg('--gb-nonce='),
                'build_digest': arg('--gb-build-digest=')})
    send(sock, {'type': 'ready', 'scene': arg('--gb-start-scene='), 'frame': frame})
    observation(sock)
    while True:
        command = receive(sock)
        if command['type'] == 'action':
            start = frame
            frame += int(command['hold_frames'])
            if command.get('canonical_action') and run_id not in {'matched_null', 'auto_win_ready'}:
                won = True
            observation(sock)
            send(sock, {'type': 'action_ack', 'sequence': command['sequence'],
                        'start_frame': start, 'end_frame': frame})
        elif command['type'] == 'capture':
            send(sock, {'type': 'capture_ack', 'sequence': command['sequence'],
                        'frame': frame, 'checkpoint_id': command['checkpoint_id'],
                        'bytes_digest': 'sha256:' + hashlib.sha256(png).hexdigest(),
                        'png_base64': base64.b64encode(png).decode()})
        elif command['type'] == 'stop':
            send(sock, {'type': 'stop_ack', 'reason': command.get('reason', '')})
            break
""",
        encoding="utf-8",
    )
    player.chmod(0o755)
    return player


def _fake_xvfb(root: Path) -> Path:
    wrapper = root / "fake-xvfb-run"
    wrapper.write_text(
        "#!/usr/bin/env python3\nimport os, sys\nos.execv(sys.argv[2], sys.argv[2:])\n",
        encoding="utf-8",
    )
    wrapper.chmod(0o755)
    return wrapper


def _routes(path: Path) -> None:
    write_json(
        path,
        {
            "schema_version": 2,
            "routes": [
                {
                    "route_id": "fixture/C1/next_level",
                    "start": {"level": 0},
                    "goal": {"predicate": "contact(goal, 0)"},
                    "authored_solution": {
                        "ops": [{"op": "hold", "action": "gb_right", "frames": 12}]
                    },
                },
                {
                    "route_id": "fixture/L5/clear",
                    "start": {"level": 0},
                    "goal": {"predicate": "whole_game_clear()"},
                    "authored_solution": {
                        "ops": [{"op": "hold", "action": "gb_right", "frames": 24}]
                    },
                },
            ],
        },
    )


class UnityProbeTests(unittest.TestCase):
    @staticmethod
    def _behavior(scenario_id: str, *, budget_frames: int = 2400):
        return UnityHiddenBehaviorScenario(
            id=scenario_id,
            evidence_basis=("video_observable",),
            start_level=1,
            budget_frames=budget_frames,
            policy="fixture",
            goal=Goal("won()"),
        )

    def test_hidden_selection_matches_all_functional_obligations_except_l5_root(self) -> None:
        root = Path("fixture")
        rows = [
            (self._behavior("game-l1-basic"), root),
            (self._behavior("game-l5-whole_game_clear"), root),
            (self._behavior("game-l5-whole_run_clear"), root),
            (self._behavior("game-l5-whole_game_clear--milestone-boss"), root),
            (self._behavior("game-l4-secret"), root),
        ]

        selected = _select_hidden_behavior_inputs(rows)

        self.assertEqual(
            ["game-l1-basic", "game-l5-whole_game_clear--milestone-boss", "game-l4-secret"],
            [scenario.id for scenario, _ in selected],
        )

    def test_counterfactual_window_stops_600_frames_after_intervention(self) -> None:
        scenario = self._behavior("game-l2-switch", budget_frames=2400)
        counterfactual = UnityCounterfactual(id="remove-action", remove="gb_action")
        positive = UnityProbeRun(
            "positive", "hidden_behavior", "pass", "ok", (), 0,
            "plan.json", "result.json", "player.log",
            reading={
                "behavior_result": {
                    "actions": [
                        {"canonical_action": "gb_right", "hold_frames": 120},
                        {"canonical_action": "gb_action", "hold_frames": 6},
                        {"canonical_action": "gb_right", "hold_frames": 900},
                    ]
                }
            },
        )

        self.assertEqual(726, _counterfactual_budget(scenario, counterfactual, positive))

    def test_probe_deadline_contains_declared_fixed_frame_horizon(self) -> None:
        self.assertAlmostEqual(
            871.56,
            _probe_wall_timeout(
                [Op(op="wait", frames=25052)], scenario=None, floor_seconds=240,
            ),
        )

    def test_diagnostic_smoke_is_bounded_and_keeps_first_action(self) -> None:
        smoke = _diagnostic_smoke_ops([
            Op(op="wait", frames=60),
            Op(op="tap", action="gb_action", frames=6),
            Op(op="hold", action="gb_right", frames=300),
        ])
        self.assertEqual(180, sum(op.frames for op in smoke))
        self.assertTrue(any(op.action for op in smoke))

    def test_report_serialization_keeps_stream_in_separate_artifact(self) -> None:
        run = UnityProbeRun(
            "run", "witness", "pass", "ok", (), 0,
            "plan.json", "result.json", "player.log",
            reading={
                "rows": [{"f": 1}, {"f": 2}],
                "events": [{"kind": "input_dispatched"}],
                "stop_reason": "budget_exhausted",
            },
        )
        payload = run.to_dict()
        self.assertNotIn("rows", payload["reading"])
        self.assertNotIn("events", payload["reading"])
        self.assertEqual(2, payload["reading"]["row_count"])
        self.assertEqual("result.json", payload["reading"]["artifact_path"])
        self.assertEqual(
            240.0,
            _probe_wall_timeout(
                [Op(op="wait", frames=60)], scenario=None, floor_seconds=240,
            ),
        )

    @unittest.skipIf(os.name == "nt", "POSIX fake executable; Windows refusal is tested separately")
    def test_diagnostic_smoke_runs_only_one_player_and_is_never_scoreable(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            suite = run_unity_runtime_suite(
                _fake_player(root),
                _manifest(),
                [Op(op="wait", frames=2)],
                root / "runs",
                xvfb_bin=_fake_xvfb(root),
                ffmpeg_bin=root / "no-ffmpeg",
                environment_profile=LOCAL_LINUX,
                trusted_fixture=True,
                diagnostic_smoke=True,
            )
            self.assertEqual("inconclusive", suite.status)
            self.assertEqual("pass", suite.witness.status)
            self.assertEqual("inconclusive", suite.matched_null.status)
            self.assertIsNone(suite.auto_win)
            self.assertIsNone(suite.mash)
            self.assertTrue(suite.environment["diagnostic_smoke"])
            self.assertFalse((root / "runs" / "matched_null").exists())

    @unittest.skipIf(os.name == "nt", "POSIX fake executable; Windows refusal is tested separately")
    def test_suite_uses_raw_scene_trace_and_keeps_hidden_goals_out_of_plan(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            route_path = root / "route.json"
            _routes(route_path)
            ops = [
                Op(op="hold", action="gb_right", frames=10),
                Op(op="tap", action="gb_action", frames=6),
            ]
            suite = run_unity_runtime_suite(
                _fake_player(root),
                _manifest(),
                ops,
                root / "runs",
                hidden_route_path=route_path,
                xvfb_bin=_fake_xvfb(root),
                ffmpeg_bin=root / "no-ffmpeg",
                environment_profile=LOCAL_LINUX,
                trusted_fixture=True,
            )
            self.assertEqual("pass", suite.status)
            self.assertEqual("evaluator_forced", suite.environment["clock_acceleration"]["status"])
            self.assertEqual(8.0, suite.environment["clock_acceleration"]["selected_scale"])
            self.assertIn("--gb-time-scale=8", suite.witness.command)
            self.assertIn("--gb-time-scale=8", suite.matched_null.command)
            self.assertTrue(suite.witness.won)
            self.assertFalse(suite.matched_null.won)
            self.assertIsNotNone(suite.auto_win)
            self.assertFalse(suite.auto_win.won)
            self.assertIsNone(suite.mash)
            self.assertEqual(2, len(suite.hidden_routes))
            self.assertTrue(all(run.won for run in suite.hidden_routes))

            witness_plan = json.loads(
                (root / "runs" / "witness" / "plan.json").read_text(encoding="utf-8")
            )
            self.assertEqual("gamebench.unity-controller-config.v1", witness_plan["schema"])
            self.assertNotIn("ops", witness_plan)
            self.assertNotIn("target_scenes", witness_plan)
            self.assertNotIn("success", witness_plan)
            self.assertNotIn("predicate", json.dumps(witness_plan))

            null_plan = json.loads(
                (root / "runs" / "matched_null" / "plan.json").read_text(encoding="utf-8")
            )
            self.assertNotIn("ops", null_plan)

            auto_plan = json.loads(
                (root / "runs" / "auto_win" / "plan.json").read_text(encoding="utf-8")
            )
            self.assertEqual("auto_win_ready", auto_plan["run_id"])
            self.assertNotIn("ops", auto_plan)

    @unittest.skipIf(os.name == "nt", "POSIX fake executable; Windows refusal is tested separately")
    def test_rerun_cannot_reuse_old_probe_result(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            out = root / "runs"
            stale = out / "witness"
            stale.mkdir(parents=True)
            (stale / "result.json").write_text(
                json.dumps({"schema": PROTOCOL, "run_id": "submitted_witness", "completed": True}),
                encoding="utf-8",
            )
            missing = root / "missing-player"
            suite = run_unity_runtime_suite(
                missing,
                _manifest(),
                [Op(op="wait", frames=2)],
                out,
                xvfb_bin=_fake_xvfb(root),
                ffmpeg_bin=root / "no-ffmpeg",
                environment_profile=LOCAL_LINUX,
                trusted_fixture=True,
                timeout=1,
            )
            self.assertFalse((stale / "result.json").is_file())
            self.assertNotEqual("pass", suite.witness.status)

    def test_windows_profile_returns_structured_infrastructure_refusal(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            profile = UnityEnvironmentProfile(
                profile_id="local-windows",
                environment_class="local-windows",
                platform="Windows",
                detail="Windows runtime refusal",
            )
            suite = run_unity_runtime_suite(
                root / "game.x86_64",
                _manifest(),
                [Op(op="wait", frames=2)],
                root / "runs",
                environment_profile=profile,
            )
            self.assertEqual("inconclusive", suite.status)
            self.assertEqual("infrastructure", suite.attribution)
            self.assertEqual("local-windows", suite.environment["environment_class"])
            self.assertFalse(suite.environment["score_eligible"])
            self.assertEqual((), suite.witness.command)

    def test_missing_graphical_profile_is_infrastructure_inconclusive(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            profile = UnityEnvironmentProfile(
                profile_id="certified-fixture",
                environment_class="linux-vm-certified",
                platform="Ubuntu 24.04",
                score_eligible=True,
                certified=True,
                certification_status="certified",
                graphical_profile="xvfb-mesa",
                image_digest="sha256:fixture-image",
                guest_os="ubuntu-24.04-x86_64",
                kernel="6.8.0-fixture",
                hypervisor="hyper-v",
                unity_changeset="fixture-changeset",
                unity_modules=("linux-il2cpp",),
                package_lock_digest="sha256:fixture-packages",
                display="xvfb",
                renderer="mesa-llvmpipe",
                mesa_version="fixture-mesa",
                network_policy="loopback-only",
                limits={
                    "cpus": 4,
                    "memory_mb": 8192,
                    "disk_mb": 32768,
                    "wall_seconds": 1800,
                },
                license_mechanism="isolated-license-service",
                certification_record_digest="sha256:fixture-certification",
                preflight_passed=True,
            )
            suite = run_unity_runtime_suite(
                root / "game.x86_64",
                _manifest(),
                [Op(op="wait", frames=2)],
                root / "runs",
                xvfb_bin=root / "missing-xvfb",
                environment_profile=profile,
            )
            self.assertEqual("inconclusive", suite.status)
            self.assertEqual("infrastructure", suite.attribution)
            self.assertIn("-nographics fallback is forbidden", suite.detail)


if __name__ == "__main__":
    unittest.main()
