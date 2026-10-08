
from __future__ import annotations

import json
from pathlib import Path
import re
import subprocess
import tempfile
import unittest

import importlib.util
import os
import sys
from evalsys.tasks import eval_root
_SCRATCH = Path(os.environ.get("GB_SCRATCH_ROOT", tempfile.gettempdir())) / "swe-game-tests"
REPLAY_DRIVER = eval_root() / "evalsys/evalsys/taskgen/replay_ops.gd"
def _load(name):
    source = REPLAY_DRIVER.parent / "playtest_kit" / (name + ".py")
    spec = importlib.util.spec_from_file_location("test_playtest_" + name, source)
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


def fixture(root: Path) -> Path:
    root.mkdir(parents=True)
    actions = ["gb_left", "gb_right", "gb_up", "gb_down", "gb_action"]
    inputs = "\n".join(f'{name}={{"deadzone":0.5,"events":[]}}' for name in actions)
    (root / "project.godot").write_text(
        'config_version=5\n[application]\nrun/main_scene="res://level.tscn"\n[input]\n' + inputs + '\n')
    (root / "level.tscn").write_text(
        '[gd_scene load_steps=2 format=3]\n[ext_resource type="Script" path="res://level.gd" id="1"]\n'
        '[node name="Level" type="Node2D"]\nscript=ExtResource("1")\n')
    (root / "gb_levels.json").write_text(json.dumps({"levels": ["res://level.tscn"], "endings": {}}))
    (root / "level.gd").write_text('''extends Node2D
var score := 0
var target: Node2D
func _ready():
    add_to_group("gb_player")
    target = Node2D.new()
    get_tree().root.add_child.call_deferred(target)
    target.position = Vector2(120, 40)
    target.add_to_group("target")
func _physics_process(delta):
    var d := Vector2(Input.get_action_strength("gb_right") - Input.get_action_strength("gb_left"), Input.get_action_strength("gb_down") - Input.get_action_strength("gb_up"))
    position += d.normalized() * 120 * delta
    if Input.is_action_just_pressed("gb_action") and position.distance_to(target.position) <= 6:
        score += 1
        target.queue_free()
func _exit_tree():
    print("FIXTURE_RESULT " + JSON.stringify({"score": score, "x": position.x, "y": position.y}))
''')
    return root


class StatePlanTests(unittest.TestCase):
    def test_compaction_preserves_taps_and_frame_limit(self):
        module = _load("plan")
        ops = [{"op": "state", "actions": ["gb_right"], "frames": 1} for _ in range(603)]
        ops += [{"op": "tap", "actions": ["gb_action"], "frames": 1},
                {"op": "tap", "actions": ["gb_action"], "frames": 1},
                {"op": "state", "actions": [], "frames": 1}]
        out = module.compact(ops)
        self.assertEqual([600, 3, 1, 1, 1], [op["frames"] for op in out])
        self.assertEqual(ops[-3:], out[-3:])

    def test_generated_tape_matches_cold_stock_replay_and_missing_target_stays_failed(self):
        from evalsys.taskgen.engine import PREFERRED_GODOT, godot_version
        if not Path(PREFERRED_GODOT).is_file() or "4.5.1" not in godot_version(PREFERRED_GODOT):
            self.skipTest("needs Godot 4.5.1")
        module = _load("plan")
        _SCRATCH.mkdir(parents=True, exist_ok=True)
        with tempfile.TemporaryDirectory(dir=_SCRATCH, prefix="gb_state_plan_") as tmp:
            out = Path(tmp)
            project = fixture(out / "submission")
            before = {p.relative_to(project): p.read_bytes() for p in project.rglob("*") if p.is_file()}
            plan = out / "plan.json"
            plan.write_text(json.dumps({"steps": [
                {"name": "long idle", "kind": "input", "actions": [], "frames": 603},
                {"name": "approach", "kind": "move", "target": {"group": "target"}, "distance": 5},
                {"name": "interact", "kind": "input", "actions": ["gb_action"], "repeat_every": 8,
                 "until": {"read": {"node": "@scene", "property": "score"}, "cmp": "eq", "value": 1}},
                {"name": "already consumed", "kind": "move", "target": {"group": "target"},
                 "skip_if": {"vanished": {"group": "target"}}},
            ]}))
            result = module.run(project, plan, out / "adaptive", godot=PREFERRED_GODOT, driver=REPLAY_DRIVER)
            self.assertEqual("complete", result["status"], result)
            self.assertEqual(0, result["script_errors"])
            self.assertEqual(4, result["completed_steps"])
            approach = result["history"][1]["observation"]
            self.assertEqual(1, approach["actor_count"])
            self.assertEqual(1, approach["target_count"])
            self.assertLessEqual(approach["distance"], approach["distance_limit"])
            interacted = result["history"][2]["observation"]["until"]
            self.assertTrue(interacted["observed"])
            self.assertEqual(1, interacted["actual"])
            self.assertTrue(interacted["satisfied"])
            consumed = result["history"][3]["observation"]["target_history"]
            self.assertGreaterEqual(consumed["first_seen_tick"], 0)
            self.assertGreater(consumed["first_absent_after_seen_tick"], consumed["first_seen_tick"])
            self.assertEqual(0, consumed["last_count"])
            self.assertEqual(before, {p.relative_to(project): p.read_bytes() for p in project.rglob("*") if p.is_file()})
            for args in [["--editor", "--import", "--quit"],
                         ["--fixed-fps", "60", "-s", str(REPLAY_DRIVER), "--", str(out / "adaptive/generated-ops.json")]]:
                proc = subprocess.run([PREFERRED_GODOT, "--headless", "--path", str(project), *args],
                                      capture_output=True, text=True, timeout=110)
                self.assertEqual(0, proc.returncode, proc.stdout + proc.stderr)
                self.assertNotIn("SCRIPT ERROR", proc.stdout + proc.stderr)
            def reading(text):
                return json.loads(re.findall(r"^FIXTURE_RESULT (.*)$", text, re.M)[-1])
            controlled = reading((out / "adaptive/execution.log").read_text())
            stock = reading(proc.stdout)
            self.assertEqual(controlled, stock)
            self.assertEqual(1, stock["score"])
            self.assertNotIn("gb_playtest_plan", (project / "project.godot").read_text())
            missing = out / "missing.json"
            missing.write_text(json.dumps({"steps": [{
                "name": "missing", "kind": "move", "target": {"group": "absent"},
                "skip_if": {"vanished": {"group": "absent"}}}]}))
            failed = module.run(project, missing, out / "missing", godot=PREFERRED_GODOT, driver=REPLAY_DRIVER)
            self.assertEqual("failed", failed["status"])
            self.assertEqual(0, failed["completed_steps"])
            self.assertEqual(0, failed["exported_frames"])
            missing_observation = failed["pending_observation"]
            self.assertEqual(0, missing_observation["target_count"])
            self.assertFalse(missing_observation["destination_observed"])
            self.assertFalse(missing_observation["skip_if"]["previously_seen"])
            self.assertEqual(-1, missing_observation["target_history"]["first_seen_tick"])

            condition = out / "unmet-condition.json"
            condition.write_text(json.dumps({"steps": [{
                "name": "score stays zero without an interaction", "kind": "input",
                "actions": [], "max_frames": 4,
                "until": {"read": {"node": "@scene", "property": "score"},
                          "cmp": "ge", "value": 1}}]}))
            unmet = module.run(project, condition, out / "unmet", godot=PREFERRED_GODOT, driver=REPLAY_DRIVER)
            self.assertEqual("failed", unmet["status"])
            value = unmet["pending_observation"]["until"]
            self.assertTrue(value["observed"])
            self.assertEqual(0, value["actual"])
            self.assertEqual(1, value["expected"])
            self.assertFalse(value["satisfied"])


if __name__ == "__main__":
    unittest.main()
