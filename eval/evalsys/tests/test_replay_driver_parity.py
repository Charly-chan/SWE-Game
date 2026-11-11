


from __future__ import annotations

import json
import os
import re
import shutil
import subprocess
import tempfile
import unittest
from pathlib import Path

from evalsys.interface.contract import ACTIONS
from evalsys.tasks import eval_root

_SCRATCH = Path("/tmp/swe-game/gb_taskgen_scratch")
REPLAY_DRIVER = eval_root() / "evalsys" / "evalsys" / "taskgen" / "replay_ops.gd"
ROUTE_DRIVER = eval_root() / "harness" / "gb_route_driver.gd"


TAPE = [
    {"op": "wait", "frames": 5},
    {"op": "tap", "action": "gb_right", "frames": 1},
    {"op": "wait", "frames": 3},
    {"op": "hold", "action": "gb_jump", "frames": 4},
    {"op": "state", "actions": ["gb_left", "gb_action"], "frames": 2},
    {"op": "release", "actions": ["gb_left"], "frames": 2},
    {"op": "tap", "actions": ["gb_up", "gb_down"], "frames": 3},
    {"op": "noop", "frames": 1},
]

_LEVEL_GD = """extends Node
var _tick := 0
var _events: Array = []
func _unhandled_input(ev):
    if ev is InputEventAction:
        _events.append("%s%s" % ["+" if ev.pressed else "-", ev.action])
func _physics_process(_d):
    _tick += 1
    var state := []
    for a in ["gb_left", "gb_right", "gb_up", "gb_down", "gb_jump", "gb_action"]:
        if Input.is_action_pressed(a):
            state.append(a + ("!" if Input.is_action_just_pressed(a) else ""))
        elif Input.is_action_just_released(a):
            state.append("~" + a)
    print("PARITY tick=%d state=%s events=%s" % [_tick, ",".join(state), ",".join(_events)])
    _events.clear()
"""

_TRACE_RE = re.compile(r"PARITY tick=(\d+) state=(.*) events=(.*)")


def _key_binding(name: str, keycode: int) -> str:
    return (
        f'{name}={{\n"deadzone": 0.5,\n"events": ['
        f'Object(InputEventKey,"resource_local_to_scene":false,"resource_name":"",'
        f'"device":-1,"window_id":0,"alt_pressed":false,"shift_pressed":false,'
        f'"ctrl_pressed":false,"meta_pressed":false,"pressed":false,'
        f'"keycode":{keycode},"physical_keycode":0,"key_label":0,"unicode":0,'
        f'"location":0,"echo":false,"script":null)\n]\n}}'
    )


def write_parity_fixture(root: Path) -> Path:
    root.mkdir(parents=True, exist_ok=True)
    bindings = "\n".join(_key_binding(name, 65 + i) for i, name in enumerate(ACTIONS))
    (root / "project.godot").write_text(
        "config_version=5\n\n[application]\nconfig/name=\"Parity\"\n"
        'run/main_scene="res://level.tscn"\n'
        'config/features=PackedStringArray("4.5", "GL Compatibility")\n\n'
        '[rendering]\nrenderer/rendering_method="gl_compatibility"\n\n'
        "[physics]\ncommon/physics_ticks_per_second=60\n\n"
        f"[input]\n\n{bindings}\n",
        encoding="utf-8",
    )
    (root / "level.gd").write_text(_LEVEL_GD, encoding="utf-8")
    (root / "player.gd").write_text(
        "\n".join(
            f'func _poll_{name}(): return Input.is_action_pressed("{name}")'
            for name in ACTIONS
        ) + "\n",
        encoding="utf-8",
    )
    (root / "level.tscn").write_text(
        "[gd_scene load_steps=2 format=3]\n\n"
        '[ext_resource type="Script" path="res://level.gd" id="1"]\n\n'
        '[node name="Level" type="Node"]\nscript = ExtResource("1")\n\n'
        '[node name="Player" type="Node" parent="." groups=["gb_player"]]\n',
        encoding="utf-8",
    )
    (root / "win.tscn").write_text('[gd_scene format=3]\n\n[node name="Win" type="Node"]\n', encoding="utf-8")
    (root / "lose.tscn").write_text('[gd_scene format=3]\n\n[node name="Lose" type="Node"]\n', encoding="utf-8")
    (root / "gb_levels.json").write_text(
        json.dumps({
            "levels": ["res://level.tscn"],
            "endings": {"victory": "res://win.tscn", "defeat": "res://lose.tscn"},
        }),
        encoding="utf-8",
    )
    (root / "ops.json").write_text(json.dumps({"ops": TAPE}), encoding="utf-8")
    return root


def observed_input_trace(text: str) -> list[tuple[int, str, str]]:

    rows: list[tuple[int, str, str]] = []
    for line in text.splitlines():
        match = _TRACE_RE.search(line)
        if match and (match.group(2) or match.group(3)):
            rows.append((int(match.group(1)), match.group(2), match.group(3)))
    return rows


class ReplayDriverMirrorTests(unittest.TestCase):


    def test_stock_driver_mirrors_route_driver_input_path(self) -> None:
        stock = REPLAY_DRIVER.read_text(encoding="utf-8")
        route = ROUTE_DRIVER.read_text(encoding="utf-8")


        self.assertIn("func _physics_process(", stock)
        self.assertNotIn("process_frame.connect", stock)
        self.assertIn("PROCESS_MODE_ALWAYS", stock)

        for call in ("Input.action_press(", "Input.action_release(", "Input.parse_input_event(",
                     "Input.flush_buffered_events()"):
            self.assertIn(call, stock, call)

        for phrase in ('"boot"', '"settle"', '"run"', "BOOT_DEADLINE := 900",
                       "_settle_left -= 1", "_tail_left"):
            self.assertIn(phrase, stock, phrase)
        self.assertIn('"boot_deadline": 900', (eval_root() / "evalsys" / "evalsys" / "routes" / "runner.py").read_text(encoding="utf-8"))

        self.assertIn('if kind == "tap" and frames == 1:', stock)
        self.assertIn('if name == "tap" and frames == 1:', route)

        self.assertIn("_first_declared_level(manifest)", stock)
        self.assertIn("REPLAY_WARNING", stock)

    def test_stock_driver_accepts_exactly_the_loaders_level_shapes(self) -> None:

        from evalsys.interface import loader

        stock = REPLAY_DRIVER.read_text(encoding="utf-8")
        self.assertNotIn('get("scene"', stock)
        self.assertNotIn('get("path"', stock)
        self.assertIn('scene.begins_with("res://") and FileAccess.file_exists(scene)', stock)

        gd_const = re.search(
            r'const LEVELS_ACCEPTED_FORM := \((.*?)\)\n', stock, re.S
        )
        self.assertIsNotNone(gd_const)
        pieces = re.findall(r'"((?:[^"\\]|\\.)*)"', gd_const.group(1))
        self.assertEqual(loader.LEVELS_ACCEPTED_FORM, "".join(pieces).replace('\\"', '"'))
        for phrase in (
            "levels[%d] is a JSON %s, not a string",
            "levels[%d] '%s' is not a res:// path",
            "levels[%d] '%s' does not name a shipped scene file",
            "REPLAY_WARNING gb_levels.json invalid: unresolved entries: ",
        ):
            self.assertIn(phrase, stock, phrase)
        self.assertEqual("levels[0] is a JSON object, not a string",
                         loader.describe_bad_level(0, {"scene": "res://x.tscn"}))
        self.assertEqual("levels[1] 'x.tscn' is not a res:// path", loader.describe_bad_level(1, "x.tscn"))
        self.assertEqual("levels[2] 'res://x.tscn' does not name a shipped scene file",
                         loader.describe_bad_level(2, "res://x.tscn"))


class ReplayDriverParityTests(unittest.TestCase):


    @classmethod
    def setUpClass(cls) -> None:
        from evalsys.taskgen.engine import PREFERRED_GODOT, godot_available, godot_version

        os.environ["GODOT_BIN"] = PREFERRED_GODOT
        os.environ["GB_SCRATCH_ROOT"] = str(_SCRATCH)
        os.environ["GB_ROUTES_SCRATCH"] = str(_SCRATCH / "gb_routes_scratch")
        cls.bin = godot_available()
        cls.version = godot_version(cls.bin) if cls.bin else ""

    def _require_451(self) -> None:
        if self.bin is None or "4.5.1" not in self.version:
            self.skipTest(f"need Godot 4.5.1, have {self.bin} ({self.version})")

    def _drop(self, path: Path) -> None:
        allowed = _SCRATCH.resolve()
        real = path.resolve()
        try:
            real.relative_to(allowed)
        except ValueError:
            return
        if real != allowed and real.is_dir():
            shutil.rmtree(real, ignore_errors=True)

    def _route_driver_trace(self, root: Path) -> tuple[list[tuple[int, str, str]], str]:
        from evalsys.interface import load_submission_interface
        from evalsys.routes.agent import Op, ScriptedAgent
        from evalsys.routes.budget import derive
        from evalsys.routes.runner import RouteSession
        from evalsys.taskgen.engine import ensure_taskgen_scratch, self_clear_route

        ensure_taskgen_scratch()
        interface = load_submission_interface(root)
        self.assertTrue(interface.report.ok, interface.report.to_dict())
        ops = [Op.from_dict(raw) for raw in TAPE]
        frames = sum(op.frames for op in ops) + 120
        route = self_clear_route(
            task_id=root.name, ops=ops, predicate="whole_game_clear()", frames=frames
        )
        budget = derive(5, 21.6, declared_steps=len(ops), declared_frames=frames)
        scratch = _SCRATCH / "gb_routes_scratch" / (root.name + "_routes")
        self._drop(scratch)
        session = RouteSession(root, interface=interface, scratch=scratch)
        session.prepare()
        try:
            self.assertTrue(
                session.prep is not None and session.prep.ok,
                getattr(session, "driver_error", "") or "prepare failed",
            )
            reading = session.run(
                route,
                ScriptedAgent(ops, name="submission_ops"),
                budget,
                run_tag="replay_parity",
                override_ops=ops,
                expose_cmdline_plan=False,
            )
            log = Path(session.io_root) / "replay_parity" / "run.log"
            text = log.read_text(encoding="utf-8", errors="replace") if log.is_file() else ""
            return observed_input_trace(text), reading.stop_reason
        finally:
            self._drop(scratch)

    def _stock_driver_trace(self, root: Path) -> tuple[list[tuple[int, str, str]], str]:
        proc = subprocess.run(
            [str(self.bin), "--headless", "--path", str(root), "--fixed-fps", "60",
             "-s", str(REPLAY_DRIVER), "--", str(root / "ops.json")],
            capture_output=True, text=True, timeout=180,
        )
        self.assertEqual(0, proc.returncode, proc.stdout[-2000:] + proc.stderr[-2000:])
        final = [line for line in proc.stdout.splitlines() if line.startswith("REPLAY_FINAL")]
        self.assertEqual(1, len(final), proc.stdout[-2000:])
        return observed_input_trace(proc.stdout), final[0]

    def test_same_tape_yields_identical_per_frame_input_state(self) -> None:
        self._require_451()
        _SCRATCH.mkdir(parents=True, exist_ok=True)
        with tempfile.TemporaryDirectory(dir="/tmp/swe-game", prefix="gb_replay_parity_") as tmp:
            root = write_parity_fixture(Path(tmp) / "parity_fixture")
            route_trace, stop_reason = self._route_driver_trace(root)
            stock_trace, final = self._stock_driver_trace(root)
        self.assertEqual("ops_exhausted", stop_reason)
        self.assertTrue(route_trace, "route driver trace is empty; the fixture did not log")


        self.assertEqual(8, route_trace[0][0], route_trace[:3])
        self.assertEqual(route_trace, stock_trace, f"\nroute={route_trace}\nstock={stock_trace}\n{final}")


    LEVEL_SHAPES = (
        ([{"id": "stage1", "name": "Coastal Run", "scene": "res://level.tscn"}], "fail"),
        (["level.tscn"], "fail"),
        (["res://level.tscn", "res://missing.tscn"], "fail"),
        (["res://level.tscn"], "pass"),
    )

    def test_stock_driver_and_loader_reject_the_same_level_shapes(self) -> None:

        from evalsys.interface import load_submission_interface

        self._require_451()
        with tempfile.TemporaryDirectory(dir="/tmp/swe-game", prefix="gb_replay_levels_") as tmp:
            root = write_parity_fixture(Path(tmp) / "levels_fixture")
            (root / "ops.json").write_text(json.dumps({"ops": [{"op": "wait", "frames": 2}]}), encoding="utf-8")
            for levels, expected in self.LEVEL_SHAPES:
                manifest = json.loads((root / "gb_levels.json").read_text(encoding="utf-8"))
                manifest["levels"] = levels
                (root / "gb_levels.json").write_text(json.dumps(manifest), encoding="utf-8")
                check = load_submission_interface(root).report.checks["gb_levels"]
                self.assertEqual(expected, check["status"], (levels, check))
                proc = subprocess.run(
                    [str(self.bin), "--headless", "--path", str(root), "--fixed-fps", "60",
                     "-s", str(REPLAY_DRIVER), "--", str(root / "ops.json")],
                    capture_output=True, text=True, timeout=180,
                )
                warnings = [
                    line[len("REPLAY_WARNING "):] for line in proc.stdout.splitlines()
                    if line.startswith("REPLAY_WARNING gb_levels.json invalid")
                ]
                if expected == "pass":
                    self.assertEqual([], warnings, (levels, proc.stdout[-1500:]))
                    self.assertEqual(0, proc.returncode, proc.stdout[-1500:] + proc.stderr[-1500:])
                    continue
                self.assertEqual(1, len(warnings), (levels, proc.stdout[-1500:], proc.stderr[-1500:]))

                self.assertTrue(
                    warnings[0].startswith(check["detail"] + " -- "),
                    f"\nloader={check['detail']}\ndriver={warnings[0]}",
                )
                self.assertIn("fix gb_levels.json before submitting", warnings[0])


                if levels and isinstance(levels[0], dict):
                    self.assertIn("declares no valid first level", proc.stdout)


if __name__ == "__main__":
    unittest.main()
