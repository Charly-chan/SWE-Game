


from __future__ import annotations

import unittest
from pathlib import Path
from unittest import mock

from evalsys.probe import inject
from evalsys.render.modes import RunResult


def _run(returncode: int = 0, timed_out: bool = False) -> RunResult:
    return RunResult(
        cmd=["godot", "--import"],
        returncode=returncode,
        stdout="",
        stderr="",
        seconds=1.0,
        timed_out=timed_out,
    )


FONT = "ERROR: Cannot open file 'res://.godot/imported/font.fontdata'."
BROKEN = "ERROR: glTF: Binary file not found: mesh.bin"


class SettlingPassRetryTests(unittest.TestCase):


    def _cold_import(self, passes: list[tuple[RunResult, list[str]]]):
        with mock.patch.object(inject, "_import_once", side_effect=passes) as once, \
             mock.patch.object(Path, "is_dir", return_value=True):
            result = inject.cold_import("scratch")
        return result, once.call_count

    def test_a_crashed_settling_pass_is_retried_once(self) -> None:


        result, calls = self._cold_import([
            (_run(0), [FONT]),
            (_run(-11), [FONT]),
            (_run(0), []),
        ])
        self.assertTrue(result.ok, result.detail)
        self.assertEqual(3, calls)
        self.assertEqual(0, result.returncode)

    def test_the_retry_is_recorded_and_not_swallowed(self) -> None:

        result, _ = self._cold_import([
            (_run(0), [FONT]),
            (_run(-11), [FONT]),
            (_run(0), []),
        ])
        self.assertTrue(
            any("engine crash on the settling pass" in e for e in result.errors),
            result.errors,
        )
        self.assertTrue(any("rc=-11" in e for e in result.errors), result.errors)

    def test_a_project_that_crashes_every_time_still_fails(self) -> None:

        result, calls = self._cold_import([
            (_run(0), [FONT]),
            (_run(-11), [FONT]),
            (_run(-11), [FONT]),
        ])
        self.assertFalse(result.ok)
        self.assertEqual(3, calls)
        self.assertEqual(-11, result.returncode)

    def test_a_crashed_first_pass_is_not_in_the_verdict_if_the_settle_is_clean(
        self,
    ) -> None:


        result, calls = self._cold_import([
            (_run(-11), [FONT]),
            (_run(-11), [FONT]),
            (_run(0), []),
        ])
        self.assertTrue(result.ok, result.detail)
        self.assertEqual(3, calls)
        self.assertEqual(0, result.returncode)

    def test_a_crashed_first_pass_is_recorded_even_when_it_is_absorbed(self) -> None:

        result, _ = self._cold_import([
            (_run(-11), [FONT]),
            (_run(-11), [FONT]),
            (_run(0), []),
        ])
        self.assertTrue(
            any("engine crash on the first pass" in e for e in result.errors),
            result.errors,
        )

    def test_a_first_pass_crash_does_not_excuse_a_settling_pass_that_fails(
        self,
    ) -> None:

        result, _ = self._cold_import([
            (_run(-11), [FONT]),
            (_run(-11), [FONT]),
            (_run(-11), [FONT]),
            (_run(-11), [FONT]),
        ])
        self.assertFalse(result.ok)
        self.assertEqual(-11, result.returncode)

    def test_a_scratch_with_no_godot_cache_says_so(self) -> None:

        passes = [(_run(0), []), (_run(0), [])]
        with mock.patch.object(inject, "_import_once", side_effect=passes), \
             mock.patch.object(Path, "is_dir", return_value=False):
            result = inject.cold_import("scratch")
        self.assertFalse(result.ok)
        self.assertIn("no .godot cache", result.detail)

    def test_a_resource_error_that_never_settles_still_fails(self) -> None:

        result, _ = self._cold_import([
            (_run(0), [BROKEN]),
            (_run(0), [BROKEN]),
            (_run(0), [BROKEN]),
        ])
        self.assertFalse(result.ok)
        self.assertIn(BROKEN, result.errors)

    def test_a_timeout_on_the_settling_pass_is_not_retried(self) -> None:

        result, calls = self._cold_import([
            (_run(0), []),
            (_run(1, timed_out=True), []),
        ])
        self.assertFalse(result.ok)
        self.assertTrue(result.timed_out)
        self.assertEqual(2, calls)


if __name__ == "__main__":
    unittest.main()
