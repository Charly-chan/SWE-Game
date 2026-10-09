"""Offline contract checks for the public shard and five-mode shell wrappers."""

from __future__ import annotations

import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile
import unittest


ROOT = Path(__file__).resolve().parents[3]


@unittest.skipUnless(sys.platform == "linux", "shell runner requires Linux Bash semantics")
class ShardRunnerTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        (self.root / "eval/tools").mkdir(parents=True)
        shutil.copy2(ROOT / "eval/tools/gb_shard_run.sh", self.root / "eval/tools/gb_shard_run.sh")
        shutil.copy2(ROOT / "run_five_modes.sh", self.root / "run_five_modes.sh")
        (self.root / "catalog.json").write_text(
            json.dumps([{"id": "alpha"}, {"id": "beta"}]), encoding="utf-8"
        )
        runner = self.root / "run_benchmark.sh"
        runner.write_text(
            """#!/usr/bin/env bash
set -u
game= mode= out= eval= sandbox= resume=0
while [ $# -gt 0 ]; do
  case "$1" in
    --game) game="$2"; shift 2 ;;
    --mode) mode="$2"; shift 2 ;;
    --out) out="$2"; shift 2 ;;
    --eval) eval="$2"; shift 2 ;;
    --sandbox) sandbox="$2"; shift 2 ;;
    --resume) resume=1; shift ;;
    --*) shift 2 ;;
  esac
done
if [ -f "$out/run.json" ] && [ "$resume" != 1 ]; then exit 2; fi
if [ ! -f "$out/run.json" ] && [ "$resume" = 1 ]; then exit 2; fi
printf '%s\t%s\t%s\t%s\n' "$game" "$mode" "$eval" "$sandbox" >> "$FAKE_RUN_LOG"
case "$game" in alpha) rc="${FAKE_RC_ALPHA:-0}" ;; beta) rc="${FAKE_RC_BETA:-0}" ;; esac
if [ "$rc" -eq 0 ]; then
  if [ "$game" = alpha ] && [ "${FAKE_EMPTY_ALPHA:-0}" = 1 ]; then exit 0; fi
  mkdir -p "$out/cells/${game}__${mode}/submission"
  printf '{}\n' > "$out/run.json"
  if [ "$eval" = on ]; then
    mkdir -p "$out/cells/${game}__${mode}/evaluation"
    printf '{}\n' > "$out/cells/${game}__${mode}/evaluation/report.json"
  fi
fi
exit "$rc"
""",
            encoding="utf-8",
        )
        runner.chmod(0o755)
        self.env = dict(os.environ, GB_PYTHON=sys.executable, FAKE_RUN_LOG=str(self.root / "runs.tsv"))

    def shard(self, *args, env=None):
        return subprocess.run(
            ["bash", "eval/tools/gb_shard_run.sh", *args],
            cwd=self.root, env=env or self.env, text=True, capture_output=True,
        )

    def test_defaults_and_explicit_deferred_evaluation(self):
        out = self.root / "results"
        command = ("--mode", "brief", "--model", "model", "--out", str(out),
                   "--games", "alpha beta", "--jobs", "2")
        result = self.shard(*command)
        self.assertEqual(result.returncode, 0, result.stderr)
        rows = (self.root / "runs.tsv").read_text().splitlines()
        self.assertEqual({row.split("\t")[2] for row in rows}, {"on"})
        self.assertTrue(all(row.endswith("\ton\tunshare") for row in rows))
        self.assertEqual(len((out / "shard-status.tsv").read_text().splitlines()), 2)
        self.assertEqual(self.shard("--mode", "brief", "--out", str(out), "--merge").returncode, 0)
        self.assertTrue((out / "merged/cells/alpha__brief").is_symlink())
        self.assertTrue((out / "shards/alpha/cells/alpha__brief").is_dir())
        merged = json.loads((out / "merged/run.json").read_text())
        self.assertEqual(merged["schema"], "gamebench.shard-merge.v1")
        self.assertEqual(merged["included_games"], ["alpha", "beta"])
        self.assertFalse(merged["partial"])
        deferred = self.shard("--mode", "brief", "--model", "model",
                              "--out", str(self.root / "deferred"), "--games", "alpha",
                              "--eval", "off")
        self.assertEqual(deferred.returncode, 0, deferred.stderr)
        self.assertTrue((self.root / "runs.tsv").read_text().splitlines()[-1].endswith("\toff\tunshare"))

    def test_mode5_rejects_eval_off_and_uses_one_docker_job_by_default(self):
        out = self.root / "port"
        base = ("--mode", "port", "--model", "model", "--out", str(out), "--games", "alpha beta")
        rejected = self.shard(*base, "--eval", "off")
        self.assertEqual(rejected.returncode, 2)
        self.assertFalse((self.root / "runs.tsv").exists())
        result = self.shard(*base)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn("jobs=1 concurrency=1 eval=on sandbox=docker", result.stdout)
        self.assertTrue(all(row.endswith("\ton\tdocker")
                            for row in (self.root / "runs.tsv").read_text().splitlines()))
        self.assertEqual(self.shard(*base, "--concurrency", "2").returncode, 2)

    def test_failure_codes_and_partial_merge(self):
        for child_rc, expected in ((1, 1), (2, 2)):
            out = self.root / f"failed-{child_rc}"
            base = ("--mode", "brief", "--model", "model", "--out", str(out),
                    "--games", "alpha beta", "--jobs", "2")
            env = dict(self.env, FAKE_RC_ALPHA=str(child_rc))
            result = self.shard(*base, env=env)
            self.assertEqual(result.returncode, expected, result.stderr)
            self.assertEqual(self.shard("--mode", "brief", "--out", str(out), "--merge").returncode, 1)
            self.assertFalse((out / "merged/cells/beta__brief").exists())
            partial = self.shard("--mode", "brief", "--out", str(out), "--merge", "--allow-partial")
            self.assertEqual(partial.returncode, 0, partial.stderr)
            self.assertTrue((out / "merged/cells/beta__brief").is_symlink())
            merged = json.loads((out / "merged/run.json").read_text())
            self.assertEqual(merged["incomplete_games"], ["alpha"])
            self.assertTrue(merged["partial"])
        # New runs replace old status records; old failures cannot taint success.
        clean = self.shard(*base)
        self.assertEqual(clean.returncode, 0, clean.stderr)
        self.assertEqual(self.shard("--mode", "brief", "--out", str(out), "--merge").returncode, 0)
        failed_again = self.shard(*base, env=env)
        self.assertEqual(failed_again.returncode, expected)
        self.assertFalse((out / "merged/run.json").exists())

    def test_empty_and_invalid_game_lists_and_job_counts(self):
        base = ("--mode", "brief", "--model", "model", "--out", str(self.root / "invalid"))
        for extra in (("--games", ""), ("--games", "unknown"), ("--games", "alpha alpha"),
                      ("--jobs", "0"), ("--concurrency", "abc")):
            with self.subTest(extra=extra):
                self.assertEqual(self.shard(*base, *extra).returncode, 2)
        self.assertEqual(
            self.shard("--mode", "brief", "--out", str(self.root / "empty"), "--merge").returncode, 2
        )
        self.assertEqual(self.shard(*base, "--jobs").returncode, 2)
        self.assertEqual(self.shard(*base, "--concurrency").returncode, 2)

    def test_zero_exit_with_empty_cells_is_failure(self):
        out = self.root / "empty-cell"
        base = ("--mode", "brief", "--model", "model", "--out", str(out),
                "--games", "alpha beta")
        result = self.shard(*base, env=dict(self.env, FAKE_EMPTY_ALPHA="1"))
        self.assertEqual(result.returncode, 1, result.stderr)
        self.assertIn("returned success without complete cells", result.stderr)
        self.assertEqual(self.shard("--mode", "brief", "--out", str(out), "--merge").returncode, 1)
        self.assertEqual(self.shard("--mode", "brief", "--out", str(out), "--merge",
                                    "--allow-partial").returncode, 0)
        self.assertTrue((out / "merged/cells/beta__brief").is_symlink())
        self.assertFalse((out / "merged/cells/alpha__brief").exists())

    def test_five_mode_wrapper_stops_at_failed_mode(self):
        fake = self.root / "eval/tools/gb_shard_run.sh"
        fake.write_text(
            """#!/usr/bin/env bash
mode=
while [ $# -gt 0 ]; do
  case "$1" in --mode) mode="$2"; shift 2 ;; --merge) shift ;; *) shift ;; esac
done
printf '%s\n' "$mode" >> "$FAKE_RUN_LOG"
[ "$mode" != gdd ] || exit 2
""",
            encoding="utf-8",
        )
        result = subprocess.run(
            ["bash", "run_five_modes.sh", "codex", "model", str(self.root / "five")],
            cwd=self.root, env=self.env, text=True, capture_output=True,
        )
        self.assertEqual(result.returncode, 2, result.stderr)
        self.assertEqual((self.root / "runs.tsv").read_text().splitlines(), ["brief", "brief", "gdd"])

    def test_five_mode_wrapper_isolates_unity_concurrency(self):
        fake = self.root / "eval/tools/gb_shard_run.sh"
        fake.write_text(
            """#!/usr/bin/env bash
mode= jobs= concurrency= sandbox= eval= merge=no
while [ $# -gt 0 ]; do
  case "$1" in
    --mode) mode="$2"; shift 2 ;;
    --jobs) jobs="$2"; shift 2 ;;
    --concurrency) concurrency="$2"; shift 2 ;;
    --sandbox) sandbox="$2"; shift 2 ;;
    --eval) eval="$2"; shift 2 ;;
    --merge) merge=yes; shift ;;
    --*) shift 2 ;;
  esac
done
printf '%s\t%s\t%s\t%s\t%s\t%s\n' "$mode" "$jobs" "$concurrency" "$sandbox" "$eval" "$merge" >> "$FAKE_RUN_LOG"
""",
            encoding="utf-8",
        )
        result = subprocess.run(
            ["bash", "run_five_modes.sh", "codex", "model", str(self.root / "five"),
             "4", "3"],
            cwd=self.root, env=self.env, text=True, capture_output=True,
        )
        self.assertEqual(result.returncode, 0, result.stderr)
        rows = (self.root / "runs.tsv").read_text().splitlines()
        self.assertEqual(len(rows), 10)
        self.assertEqual(rows[0], "brief\t4\t3\tunshare\ton\tno")
        self.assertEqual(rows[8], "port\t1\t1\tdocker\ton\tno")
        self.assertEqual(rows[9], "port\t\t\t\t\tyes")
        bad = subprocess.run(
            ["bash", "run_five_modes.sh", "codex", "model", str(self.root / "other"),
             "0"],
            cwd=self.root, env=self.env, text=True, capture_output=True,
        )
        self.assertEqual(bad.returncode, 2)
        self.assertEqual(len((self.root / "runs.tsv").read_text().splitlines()), 10)


if __name__ == "__main__":
    unittest.main()
