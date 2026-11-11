

from __future__ import annotations

import os
import tempfile
import unittest
from contextlib import ExitStack
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock, patch

from evalsys.taskgen.engine import run_bugfix_gates


class Mode4EngineIsolationTests(unittest.TestCase):
    def run_suite(self, *, fail_on: str | None = None) -> tuple[dict | None, list[str]]:

        seen: list[tuple[str, Path]] = []
        result = None
        with tempfile.TemporaryDirectory() as directory, ExitStack() as stack:
            root = Path(directory)
            original_store = root / "collaborator-store"
            original_store.mkdir()
            original_save = original_store / "save.txt"
            original_save.write_text("collaborator progress", encoding="utf-8")
            route_file = root / "route.json"
            route_file.write_text("{}", encoding="utf-8")
            stack.enter_context(patch.dict(os.environ, {
                "GB_SCRATCH_ROOT": str(root / "scratch"),
                "XDG_DATA_HOME": str(original_store),
            }))

            def visit(label: str) -> None:
                store = Path(os.environ["XDG_DATA_HOME"])
                sentinel = store / "save.txt"
                self.assertNotEqual(original_store, store)
                self.assertFalse(sentinel.exists(), f"{label} inherited another replay's save")
                self.assertNotIn(store, [path for _, path in seen])
                sentinel.write_text(label, encoding="utf-8")
                seen.append((label, store))


                for operation in ("continue", "retry", "reload"):
                    self.assertEqual(str(store), os.environ["XDG_DATA_HOME"], operation)
                    self.assertEqual(label, sentinel.read_text(encoding="utf-8"), operation)
                if fail_on == label:
                    raise RuntimeError("fixture replay failed")

            def reading(route_id: str, reached: bool = False) -> SimpleNamespace:
                return SimpleNamespace(
                    route_id=route_id,
                    reached=reached,
                    stop_reason="goal_reached" if reached else "budget_frames",
                    detail="fixture",
                )

            session = Mock()

            def run_control(route, agent, budget, *, run_tag, expose_cmdline_plan):
                visit(run_tag)
                self.assertEqual(run_tag == "bugfix_idle_flag", expose_cmdline_plan)
                return reading(route.route_id)

            session.run.side_effect = run_control

            def mash(current_session, **kwargs):
                self.assertIs(session, current_session)
                visit("extended_mash")
                return {"ran": True, "reached": False, "stop_reason": "budget_frames"}

            routes = [
                SimpleNamespace(route_id=route_id, tier=tier,
                                budget=SimpleNamespace(steps=1, frames=1800))
                for route_id, tier in (
                    ("fixture/repair/retry", 5),
                    ("fixture/L0/no_input", 0),
                    ("fixture/C1/basic", 2),
                    ("fixture/L5/whole_game_clear", 5),
                )
            ]

            def validate(route, current_session, budget, *, expose_cmdline_plan):
                self.assertIs(session, current_session)
                suffix = "flagged" if expose_cmdline_plan else "honest"
                visit(f"{route.route_id}:{suffix}")
                return SimpleNamespace(ok=True, detail="fixture passed",
                                       reading=reading(route.route_id, reached=True))

            stack.enter_context(patch("evalsys.taskgen.engine.ensure_taskgen_scratch"))
            stack.enter_context(patch("evalsys.taskgen.engine.godot_available", return_value=root / "godot"))
            stack.enter_context(patch("evalsys.taskgen.engine.godot_version", return_value="4.5.1.fixture"))
            stack.enter_context(patch("evalsys.taskgen.engine.prepare_session", return_value=True))
            stack.enter_context(patch("evalsys.routes.runner.RouteSession", return_value=session))
            stack.enter_context(patch("evalsys.routes.schema.load_route_file", return_value=routes))
            stack.enter_context(patch("evalsys.routes.runner.validate_route_on_gold", side_effect=validate))
            stack.enter_context(patch("evalsys.taskgen.engine.run_extended_mash", side_effect=mash))

            def invoke():
                return run_bugfix_gates(
                    root / "project", route_file, interface=SimpleNamespace(),
                    game_id="fixture", genuine_override={"present": True},
                )

            if fail_on:
                with self.assertRaisesRegex(RuntimeError, "fixture replay failed"):
                    invoke()
            else:
                result = invoke()
            self.assertEqual(str(original_store), os.environ["XDG_DATA_HOME"])
            self.assertEqual("collaborator progress", original_save.read_text(encoding="utf-8"))
            self.assertTrue(all(not path.exists() for _, path in seen))
        return result, [label for label, _ in seen]

    def test_controls_each_honest_route_and_flagged_l5_have_fresh_saves(self):
        result, labels = self.run_suite()
        self.assertEqual([
            "bugfix_null",
            "bugfix_idle_env",
            "bugfix_idle_flag",
            "extended_mash",
            "fixture/repair/retry:honest",
            "fixture/L0/no_input:honest",
            "fixture/C1/basic:honest",
            "fixture/L5/whole_game_clear:honest",
            "fixture/L5/whole_game_clear:flagged",
        ], labels)
        self.assertTrue(result["ran"])
        self.assertEqual([], result["gold"]["rt1_failed"])
        self.assertEqual(4, len(result["gold"]["rt1_passed"]))

    def test_original_user_data_is_restored_when_a_route_raises(self):
        result, labels = self.run_suite(fail_on="fixture/C1/basic:honest")
        self.assertIsNone(result)
        self.assertEqual("fixture/C1/basic:honest", labels[-1])
        self.assertNotIn("fixture/L5/whole_game_clear:honest", labels)


    def test_each_bugfix_stage_has_its_own_user_data(self):
        seen = []
        def unavailable():
            directory = Path(os.environ["XDG_DATA_HOME"])
            self.assertFalse((directory / "previous_save").exists())
            (directory / "previous_save").write_text("fixture")
            seen.append(str(directory))
            return None
        with tempfile.TemporaryDirectory() as directory:
            with patch.dict(os.environ, {"GB_SCRATCH_ROOT": directory, "XDG_DATA_HOME": "collaborator-store"}):
                with patch("evalsys.taskgen.engine.godot_available", side_effect=unavailable):
                    for _ in range(2):
                        run_bugfix_gates(Path(directory), Path(directory) / "route.json", interface=None)
                self.assertEqual("collaborator-store", os.environ["XDG_DATA_HOME"])
        self.assertNotEqual(seen[0], seen[1])

if __name__ == "__main__":
    unittest.main()
