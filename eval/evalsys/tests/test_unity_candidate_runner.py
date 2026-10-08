from __future__ import annotations

import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import time
import unittest
from unittest.mock import patch

from evalsys.taskgen.unity.unity_environment import coerce_environment_profile
from evalsys.taskgen.unity.unity_sandbox_process import (
    candidate_identity,
    candidate_process_command,
    run_candidate_process,
    terminate_candidate_process_tree,
)
from evalsys.taskgen.unity.unity_probe import _matched_null_ops
from evalsys.routes.agent import Op


class UnityCandidateRunnerTests(unittest.TestCase):
    def test_process_prefix_is_inactive_on_ordinary_hosts(self) -> None:
        with patch.dict(os.environ, {}, clear=True):
            self.assertEqual(("Unity", "-version"), candidate_process_command(["Unity", "-version"]))
            self.assertIsNone(candidate_identity())

    def test_certified_guest_prefix_is_argv_not_a_shell_string(self) -> None:
        env = {
            "GB_UNITY_CANDIDATE_PROCESS_PREFIX_JSON": json.dumps(
                ["runuser", "-u", "unity-runner", "--", "aa-exec", "-p", "candidate", "--"]
            ),
            "GB_UNITY_CANDIDATE_UID": "1201",
            "GB_UNITY_CANDIDATE_GID": "1201",
        }
        with patch.dict(os.environ, env, clear=True):
            self.assertEqual((1201, 1201), candidate_identity())
            self.assertEqual(
                ("runuser", "-u", "unity-runner", "--", "aa-exec", "-p", "candidate", "--", "Unity"),
                candidate_process_command(["Unity"]),
            )

    @unittest.skipUnless(os.name == "posix", "POSIX process-group behavior")
    def test_candidate_timeout_kills_children_holding_capture_pipes(self) -> None:
        command = [
            sys.executable,
            "-c",
            "import subprocess,sys,time; "
            "subprocess.Popen([sys.executable, '-c', 'import time; time.sleep(60)']); "
            "time.sleep(60)",
        ]
        started = time.monotonic()
        with self.assertRaises(subprocess.TimeoutExpired):
            run_candidate_process(command, timeout=1)
        self.assertLess(time.monotonic() - started, 5)

    @unittest.skipUnless(os.name == "posix", "POSIX process-group behavior")
    def test_non_capturing_process_returns_when_parent_exits_with_orphan_child(self) -> None:
        command = [
            sys.executable, "-c",
            "import subprocess,sys; "
            "subprocess.Popen([sys.executable, '-c', 'import time; time.sleep(60)'])",
        ]
        started = time.monotonic()
        result = run_candidate_process(command, timeout=3, capture_output=False)
        self.assertEqual(0, result.returncode)
        self.assertLess(time.monotonic() - started, 3)

    @unittest.skipUnless(os.name == "posix", "POSIX process-group behavior")
    def test_tree_cleanup_kills_orphan_holding_capture_pipes(self) -> None:
        process = subprocess.Popen(
            [
                sys.executable,
                "-c",
                "import subprocess,sys; "
                "subprocess.Popen([sys.executable, '-c', 'import time; time.sleep(60)']);",
            ],
            text=True,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            start_new_session=True,
        )
        with self.assertRaises(subprocess.TimeoutExpired):
            process.communicate(timeout=1)
        terminate_candidate_process_tree(process, force=True)
        started = time.monotonic()
        process.communicate(timeout=3)
        self.assertLess(time.monotonic() - started, 3)

    def test_environment_profile_can_only_be_provisioned_by_a_complete_file(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "profile.json"
            path.write_text(json.dumps({"environment_class": "community-docker"}), encoding="utf-8")
            with patch.dict(os.environ, {"GB_UNITY_ENVIRONMENT_PROFILE": str(path)}, clear=True):
                profile = coerce_environment_profile(None)
            self.assertEqual("community-docker", profile.environment_class)
            self.assertFalse(profile.ready_for_untrusted_execution)
            self.assertIn("preflight_passed", profile.readiness_errors)

    def test_matched_null_coalesces_round_trips_but_preserves_horizon(self) -> None:
        witness = [Op(op="hold", action="gb_right", frames=400) for _ in range(4)]
        null = _matched_null_ops(witness)
        self.assertEqual(1600, sum(op.frames for op in null))
        self.assertEqual([600, 600, 400], [op.frames for op in null])
        self.assertTrue(all(op.op == "wait" and not op.action for op in null))

if __name__ == "__main__":
    unittest.main()
