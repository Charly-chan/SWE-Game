

from __future__ import annotations

import json
import os
import shutil
import tempfile
import unittest
from pathlib import Path

from evalsys.interface import load_submission_interface
from evalsys.interface.model import AnalogAxis
from evalsys.routes.agent import (
    mash_ops,
    op_table_for_agent,
    validate_ops,
)
from evalsys.taskgen.engine import MASH_PROBE_FRAMES, run_extended_mash
from evalsys.tasks import eval_root

from _interface_fixture import read_manifest, write_conformant_project, write_manifest

_SCRATCH = Path("/tmp/swe-game/gb_taskgen_scratch")
_WHY = "pitch is a continuous camera axis; six verbs cannot look 89 degrees"


def _axis_binding(name: str, axis: int = 2) -> str:
    return (
        f'{name}={{\n"deadzone": 0.2,\n"events": ['
        f'Object(InputEventJoypadMotion,"resource_local_to_scene":false,"resource_name":"",'
        f'"device":-1,"axis":{axis},"axis_value":1.0,"script":null)\n]\n}}'
    )


def _add_axis(
    root: Path,
    ident: str,
    *,
    why: str = _WHY,
    lo: float = -1.0,
    hi: float = 1.0,
    steps: int = 17,
    read: bool = True,
    bind: bool = True,
) -> None:
    godot = root / "project.godot"
    text = godot.read_text(encoding="utf-8")
    if bind:
        text = text.rstrip() + "\n" + _axis_binding(ident) + "\n"
    else:
        text = text.rstrip() + (
            f'\n{ident}={{\n"deadzone": 0.5,\n"events": [Object()]\n}}\n'
        )
    godot.write_text(text, encoding="utf-8")
    if read:
        player = root / "player.gd"
        player.write_text(
            player.read_text(encoding="utf-8")
            + f'\nfunc _poll_axis(): return Input.get_action_strength("{ident}")\n',
            encoding="utf-8",
        )
    manifest = read_manifest(root)
    axes = list(manifest.get("analog_axes") or [])
    axes.append({"id": ident, "why": why, "min": lo, "max": hi, "steps": steps})
    manifest["analog_axes"] = axes
    write_manifest(root, manifest)


class LoaderAnalogAxisTests(unittest.TestCase):
    def test_absent_and_empty_do_not_enter_the_digest(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = write_conformant_project(Path(tmp))
            loaded = load_submission_interface(root)
            self.assertTrue(loaded.report.ok, loaded.report.to_dict())
            self.assertEqual((), loaded.analog_axes)
            self.assertNotIn("analog_axes", loaded.provenance())
            payload_absent = loaded.normalized_sha256
            manifest = read_manifest(root)
            manifest["analog_axes"] = []
            write_manifest(root, manifest)
            again = load_submission_interface(root)
            self.assertEqual(payload_absent, again.normalized_sha256)
            self.assertNotIn("analog_axes", again.provenance())

    def test_harvest_ledger_digest_is_unchanged_by_the_axis_channel(self) -> None:
        game = eval_root().parent / "games" / "harvest_ledger"
        cert = eval_root() / "tasks" / "harvest_ledger" / "certificate.rt1.json"
        if not game.is_dir() or not cert.is_file():
            self.skipTest("harvest_ledger gold is not on disk")
        loaded = load_submission_interface(game)
        self.assertEqual((), loaded.analog_axis_ids)
        self.assertNotIn("analog_axes", loaded.provenance())
        frozen = json.loads(cert.read_text(encoding="utf-8"))["interface"]["normalized_sha256"]
        self.assertEqual(frozen, loaded.normalized_sha256)

    def test_a_justified_bound_axis_passes_and_changes_the_digest(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = write_conformant_project(Path(tmp))
            before = load_submission_interface(root)
            _add_axis(root, "look_y")
            after = load_submission_interface(root)
            self.assertTrue(after.report.ok, after.report.to_dict())
            self.assertEqual(("look_y",), after.analog_axis_ids)
            self.assertNotEqual(before.normalized_sha256, after.normalized_sha256)
            self.assertEqual(
                [{"id": "look_y", "why": _WHY, "min": -1.0, "max": 1.0, "steps": 17}],
                after.provenance()["analog_axes"],
            )

    def test_more_than_four_axes_is_rejected(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = write_conformant_project(Path(tmp))
            manifest = read_manifest(root)
            manifest["analog_axes"] = [
                {"id": f"look_{index}", "why": _WHY, "min": -1, "max": 1, "steps": 17}
                for index in range(5)
            ]
            write_manifest(root, manifest)
            loaded = load_submission_interface(root)
            self.assertIn("gb_levels.json.analog_axes", loaded.report.missing)
            self.assertIn("max is 4", loaded.report.checks["analog_axes"]["detail"])

    def test_even_steps_on_a_signed_range_are_rejected(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = write_conformant_project(Path(tmp))
            _add_axis(root, "look_x", steps=16)
            loaded = load_submission_interface(root)
            self.assertFalse(loaded.report.ok)
            self.assertIn("0 on the grid", loaded.report.checks["analog_axes"]["detail"])

    def test_a_stub_axis_without_joypad_motion_is_rejected(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = write_conformant_project(Path(tmp))
            _add_axis(root, "look_x", bind=False)
            loaded = load_submission_interface(root)
            self.assertIn("JoypadMotion", loaded.report.checks["analog_axes"]["detail"])

    def test_an_axis_must_not_reuse_an_extended_action_id(self) -> None:
        from evalsys.interface.loader import action_has_human_binding

        with tempfile.TemporaryDirectory() as tmp:
            root = write_conformant_project(Path(tmp))
            godot = root / "project.godot"
            godot.write_text(
                godot.read_text(encoding="utf-8").rstrip()
                + "\n"
                + (
                    'look_x={\n"deadzone": 0.5,\n"events": ['
                    'Object(InputEventKey,"resource_local_to_scene":false,"resource_name":"",'
                    '"device":-1,"window_id":0,"alt_pressed":false,"shift_pressed":false,'
                    '"ctrl_pressed":false,"meta_pressed":false,"pressed":false,'
                    '"keycode":90,"physical_keycode":0,"key_label":0,"unicode":0,'
                    '"location":0,"echo":false,"script":null)\n]\n}\n'
                )
                + _axis_binding("look_x")
                + "\n",
                encoding="utf-8",
            )
            self.assertTrue(
                action_has_human_binding(godot.read_text(encoding="utf-8"), "look_x")
            )
            player = root / "player.gd"
            player.write_text(
                player.read_text(encoding="utf-8")
                + '\nfunc _poll(): return Input.is_action_pressed("look_x")\n',
                encoding="utf-8",
            )
            manifest = read_manifest(root)
            manifest["extended_actions"] = [{"id": "look_x", "why": _WHY}]
            manifest["analog_axes"] = [
                {"id": "look_x", "why": _WHY, "min": -1.0, "max": 1.0, "steps": 17}
            ]
            write_manifest(root, manifest)
            loaded = load_submission_interface(root)
            self.assertIn("collides", loaded.report.checks["analog_axes"]["detail"])


class ValidateOpsAxisTests(unittest.TestCase):
    def test_axes_on_an_undeclaring_game_are_unknown_axis(self) -> None:
        check = validate_ops([
            {"op": "wait", "frames": 8, "axes": {"look_y": -1.0}}
        ])
        self.assertEqual("unknown_axis", check.refusals[0].reason)
        empty = validate_ops([{"op": "hold", "action": "gb_right", "frames": 8}])
        self.assertTrue(empty.ok)
        self.assertNotIn("axes", empty.accepted[0].to_dict())

    def test_declared_axes_are_quantized_and_range_checked(self) -> None:
        spec = AnalogAxis("look_y", _WHY, -1.0, 1.0, 17)
        allowed = validate_ops(
            [{"op": "wait", "frames": 10, "axes": {"look_y": -0.9}}],
            extra_axes=(spec,),
        )
        self.assertTrue(allowed.ok, allowed.to_dict())
        self.assertEqual((("look_y", spec.quantize(-0.9)),), allowed.accepted[0].axes)
        over = validate_ops(
            [{"op": "wait", "frames": 10, "axes": {"look_y": -2.0}}],
            extra_axes=(spec,),
        )
        self.assertEqual("bad_axis_value", over.refusals[0].reason)

    def test_op_table_lists_frozen_axes_without_a_seventh_verb(self) -> None:
        table = op_table_for_agent()
        self.assertEqual([], table["analog_axes"])
        spec = AnalogAxis("look_x", _WHY, -1.0, 1.0, 17)
        listed = op_table_for_agent(extra_axes=(spec,))
        self.assertEqual(["look_x"], [item["id"] for item in listed["analog_axes"]])
        self.assertNotIn("look_x", listed["actions"])
        self.assertIn("frozen into the interface digest", listed["note"])

    def test_mash_ops_park_every_axis_at_max(self) -> None:
        spec = AnalogAxis("look_y", _WHY, -1.0, 1.0, 17)
        ops = mash_ops((), 700, extra_axes=(spec,))
        self.assertEqual(2, len(ops))
        self.assertEqual("state", ops[0].op)
        self.assertEqual((("look_y", 1.0),), ops[0].axes)
        self.assertEqual([], mash_ops((), 1100))
        both = mash_ops(("place",), 100, extra_axes=(spec,))
        self.assertEqual("hold", both[0].op)
        self.assertEqual(("place",), both[0].actions)
        self.assertEqual((("look_y", 1.0),), both[0].axes)


class FrozenAxisDispatchTests(unittest.TestCase):
    def test_session_dispatch_defaults_to_the_loaded_interface(self) -> None:
        from evalsys.routes.runner import RouteSession

        with tempfile.TemporaryDirectory() as tmp:
            root = write_conformant_project(Path(tmp))
            _add_axis(root, "look_y")
            session = RouteSession(root)
            self.assertEqual(("look_y",), tuple(item.id for item in session.extra_axes()))
            stolen = RouteSession(root, dispatch_axes=())
            self.assertEqual((), stolen.extra_axes())
            check = stolen.check_ops(
                [{"op": "wait", "frames": 4, "axes": {"look_y": 1.0}}]
            )
            self.assertEqual("unknown_axis", check.refusals[0].reason)


class EngineAxisMashTests(unittest.TestCase):


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

    def _runnable(self, root: Path, ident: str, script: str) -> Path:
        from evalsys.interface.contract import ACTIONS

        root.mkdir(parents=True, exist_ok=True)
        bindings = "\n".join(
            (
                f'{name}={{\n"deadzone": 0.5,\n"events": ['
                f'Object(InputEventKey,"resource_local_to_scene":false,"resource_name":"",'
                f'"device":-1,"window_id":0,"alt_pressed":false,"shift_pressed":false,'
                f'"ctrl_pressed":false,"meta_pressed":false,"pressed":false,'
                f'"keycode":{65 + index},"physical_keycode":0,"key_label":0,"unicode":0,'
                f'"location":0,"echo":false,"script":null)\n]\n}}'
            )
            for index, name in enumerate(ACTIONS)
        )
        extra = _axis_binding(ident)
        (root / "project.godot").write_text(
            "config_version=5\n\n"
            "[application]\n"
            'config/name="AxisMash"\n'
            'run/main_scene="res://level.tscn"\n'
            'config/features=PackedStringArray("4.5", "GL Compatibility")\n\n'
            "[rendering]\n"
            'renderer/rendering_method="gl_compatibility"\n'
            'renderer/rendering_method.mobile="gl_compatibility"\n\n'
            "[physics]\n"
            "common/physics_ticks_per_second=60\n\n"
            "[input]\n\n"
            f"{bindings}\n{extra}\n",
            encoding="utf-8",
        )
        (root / "player.gd").write_text(
            "\n".join(
                f'func _poll_{name}(): return Input.is_action_pressed("{name}")'
                for name in ACTIONS
            )
            + f'\nfunc _poll_axis(): return Input.get_action_strength("{ident}")\n',
            encoding="utf-8",
        )
        (root / "level.gd").write_text(script, encoding="utf-8")
        (root / "level.tscn").write_text(
            "[gd_scene load_steps=2 format=3]\n\n"
            '[ext_resource type="Script" path="res://level.gd" id="1"]\n\n'
            '[node name="Level" type="Node"]\n'
            'script = ExtResource("1")\n\n'
            '[node name="Player" type="Node" parent="." groups=["gb_player"]]\n',
            encoding="utf-8",
        )
        (root / "win.tscn").write_text(
            "[gd_scene format=3]\n\n[node name=\"Win\" type=\"Node\"]\n",
            encoding="utf-8",
        )
        (root / "lose.tscn").write_text(
            "[gd_scene format=3]\n\n[node name=\"Lose\" type=\"Node\"]\n",
            encoding="utf-8",
        )
        write_manifest(
            root,
            {
                "levels": ["res://level.tscn"],
                "endings": {"victory": "res://win.tscn", "defeat": "res://lose.tscn"},
                "analog_axes": [
                    {"id": ident, "why": _WHY, "min": -1.0, "max": 1.0, "steps": 17}
                ],
            },
        )
        return root

    def _mash(self, root: Path, frames: int = 180) -> dict:
        from evalsys.interface import load_submission_interface
        from evalsys.routes.runner import RouteSession
        from evalsys.taskgen.engine import ensure_taskgen_scratch

        ensure_taskgen_scratch()
        interface = load_submission_interface(root)
        self.assertTrue(interface.report.ok, interface.report.to_dict())
        scratch = _SCRATCH / "gb_routes_scratch" / (root.name + "_routes")
        self._drop(scratch)
        session = RouteSession(root, interface=interface, scratch=scratch)
        session.prepare()
        try:
            self.assertTrue(
                session.prep is not None and session.prep.ok,
                getattr(session, "driver_error", "") or "prepare failed",
            )
            return run_extended_mash(
                session,
                extra_actions=(),
                extra_axes=interface.analog_axes,
                predicate="whole_game_clear()",
                frames=frames,
                task_id=root.name,
            )
        finally:
            self._drop(scratch)

    def test_positive_control_axis_at_max_is_caught(self) -> None:
        self._require_451()
        _SCRATCH.mkdir(parents=True, exist_ok=True)
        with tempfile.TemporaryDirectory(dir="/tmp/swe-game", prefix="gb_axis_win_") as tmp:
            root = self._runnable(
                Path(tmp),
                "look_x",
                "extends Node\n"
                "func _physics_process(_d):\n"
                "    var probe = get_node_or_null(\"/root/GBHarnessProbe\")\n"
                "    var v := 0.0\n"
                '    if probe != null and probe.has_method("axis_value"):\n'
                '        v = float(probe.axis_value("look_x"))\n'
                "    else:\n"
                '        v = Input.get_action_strength("look_x")\n'
                "    if v >= 0.99:\n"
                '        get_tree().change_scene_to_file("res://win.tscn")\n',
            )
            mash = self._mash(root)
            reading = mash.get("reading") or {}
            self.assertTrue(mash["applicable"], mash)
            self.assertTrue(mash["won"], mash)
            self.assertIn(
                str(reading.get("stop_reason") or ""),
                {"scene_changed", "goal_reached", "success", "victory"},
                mash,
            )

    def test_clean_control_non_winning_axis_stays_quiet(self) -> None:
        self._require_451()
        _SCRATCH.mkdir(parents=True, exist_ok=True)
        with tempfile.TemporaryDirectory(dir="/tmp/swe-game", prefix="gb_axis_ok_") as tmp:
            root = self._runnable(
                Path(tmp),
                "look_y",
                "extends Node\n"
                "var _n := 0\n"
                "func _physics_process(_d):\n"
                "    var probe = get_node_or_null(\"/root/GBHarnessProbe\")\n"
                '    if probe != null and probe.has_method("axis_value"):\n'
                '        if abs(probe.axis_value("look_y")) > 0.01:\n'
                "            _n += 1\n",
            )
            mash = self._mash(root)
            reading = mash.get("reading") or {}
            self.assertTrue(mash["applicable"], mash)
            self.assertFalse(mash["won"], mash)
            self.assertIn(
                str(reading.get("stop_reason") or ""),
                {"budget_frames", "ops_exhausted"},
                mash,
            )


if __name__ == "__main__":
    unittest.main()
