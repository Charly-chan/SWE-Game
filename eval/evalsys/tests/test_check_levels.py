

from __future__ import annotations

import importlib.util
import os
import shutil
import tempfile
import unittest
from pathlib import Path
from unittest import mock


SCRIPT = Path(__file__).resolve().parents[2] / "tools" / "gates" / "check_levels.py"
SPEC = importlib.util.spec_from_file_location("check_levels", SCRIPT)
assert SPEC and SPEC.loader
check_levels = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(check_levels)


class DeclaredPathsTests(unittest.TestCase):
    def test_accepts_ordered_object_entries_and_endings(self) -> None:
        manifest = {
            "levels": [
                {"path": "res://late.tscn", "order": 2},
                {"scene": "res://early.tscn", "order": 1},
            ],
            "main_menu": "res://menu.tscn",
            "endings": {
                "victory": "res://result.tscn",
                "defeat": "res://result.tscn",
            },
        }
        self.assertEqual(
            check_levels.declared_paths(manifest),
            [
                ("level", "res://early.tscn"),
                ("level", "res://late.tscn"),
                ("menu", "res://menu.tscn"),
                ("ending:victory", "res://result.tscn"),
            ],
        )

    def test_first_fatal_ignores_unrelated_engine_noise(self) -> None:
        log = "\n".join([
            "WARNING: audio driver unavailable",
            "ERROR: glTF: Binary file not found: mesh.bin",
            "ERROR: Failed loading resource: later.tres",
        ])
        self.assertEqual(
            check_levels.first_fatal(log),
            "ERROR: glTF: Binary file not found: mesh.bin",
        )

    def test_import_allows_one_bounded_windows_settle_retry(self) -> None:
        missing = "ERROR: Cannot open file 'res://.godot/imported/font.fontdata'."
        with mock.patch.object(
            check_levels,
            "run_godot",
            side_effect=[(0, missing), (0, missing), (0, "")],
        ) as run:
            ok, detail = check_levels.import_project(Path("scratch"))
        self.assertTrue(ok)
        self.assertIn("pass 3 clean", detail)
        self.assertEqual(run.call_count, 3)

    def test_import_rejects_persistent_resource_error(self) -> None:
        missing = "ERROR: glTF: Binary file not found: mesh.bin"
        with mock.patch.object(
            check_levels,
            "run_godot",
            side_effect=[(0, missing), (0, missing), (0, missing)],
        ):
            ok, detail = check_levels.import_project(Path("scratch"))
        self.assertFalse(ok)
        self.assertEqual(detail, missing)


class ScratchTests(unittest.TestCase):
    def test_cold_copy_excludes_cache_and_evidence_bulk(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            source = root / "source"
            source.mkdir()
            (source / "project.godot").write_text("[application]\n", encoding="utf-8")
            (source / ".godot").mkdir()
            (source / ".godot" / "cache").write_text("derived", encoding="utf-8")
            (source / "recording").mkdir()
            (source / "recording" / "driver.gd").write_text("extends Node\n", encoding="utf-8")
            (source / "recording" / "capture.mp4").write_bytes(b"large")

            scratch_parent = root / "scratch"
            old = os.environ.get("GB_LEVELS_SCRATCH")
            os.environ["GB_LEVELS_SCRATCH"] = str(scratch_parent)
            try:
                copied = check_levels.make_scratch(source)
                self.assertTrue((copied / "project.godot").is_file())
                self.assertFalse((copied / ".godot").exists())
                self.assertTrue((copied / "recording" / "driver.gd").is_file())
                self.assertFalse((copied / "recording" / "capture.mp4").exists())
            finally:
                if old is None:
                    os.environ.pop("GB_LEVELS_SCRATCH", None)
                else:
                    os.environ["GB_LEVELS_SCRATCH"] = old
                if "copied" in locals():
                    shutil.rmtree(copied, ignore_errors=True)


if __name__ == "__main__":
    unittest.main()
