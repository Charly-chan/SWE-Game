


from __future__ import annotations

import importlib.util
import io
import os
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from evalsys.engine import hostenv

REPO = Path(__file__).resolve().parents[3]
GATES = REPO / "eval" / "tools" / "gates"


def _load(name: str):
    spec = importlib.util.spec_from_file_location(f"_gate_{name}", GATES / f"{name}.py")
    assert spec and spec.loader
    mod = importlib.util.module_from_spec(spec)


    sys.modules[spec.name] = mod
    spec.loader.exec_module(mod)
    return mod


def _win():
    return mock.patch.object(sys, "platform", "win32")


class PlatformSwitchTests(unittest.TestCase):
    def test_is_windows_reads_sys_platform(self) -> None:
        self.assertFalse(hostenv.is_windows("linux"))
        self.assertFalse(hostenv.is_windows("darwin"))
        self.assertTrue(hostenv.is_windows("win32"))
        with _win():
            self.assertTrue(hostenv.is_windows())


class ScratchRootTests(unittest.TestCase):
    def test_linux_prefers_data2_then_tmp_exactly_as_before(self) -> None:
        self.assertEqual(
            hostenv.scratch_root("x", platform="linux", environ={}, isdir=lambda p: p == "/data2"),
            "/tmp/swe-game/x")
        self.assertEqual(
            hostenv.scratch_root("x", platform="linux", environ={}, isdir=lambda p: False),
            "/tmp/x")

    def test_windows_uses_the_platform_tempdir_never_a_bare_tmp(self) -> None:
        root = hostenv.scratch_root("gb", platform="win32", environ={},
                                    tempdir=lambda: r"C:\Users\dev\AppData\Local\Temp")
        self.assertTrue(root.startswith(r"C:\Users\dev\AppData\Local\Temp"))
        self.assertTrue(root.endswith("gb"))
        self.assertNotIn("/tmp", root)

    def test_windows_non_ascii_tempdir_falls_back_to_the_drive_root(self) -> None:
        root = hostenv.scratch_root("gb", platform="win32", environ={"SystemDrive": "D:"},
                                    tempdir=lambda: "C:\\Users\\津\\AppData\\Local\\Temp")
        self.assertTrue(root.startswith("D:" + os.sep + "gb_scratch"), root)
        root.encode("ascii")

    def test_override_env_wins_on_every_platform(self) -> None:
        for plat in ("linux", "win32"):
            self.assertEqual(
                hostenv.scratch_root("gb", platform=plat, environ={"GB_GATES_SCRATCH": "/mnt/big"}),
                os.path.join("/mnt/big", "gb"))

    def test_check_runtime_keeps_the_old_name_and_the_linux_answer(self) -> None:
        cr = _load("check_runtime")
        with mock.patch.dict(os.environ, {}, clear=False):
            os.environ.pop("GB_GATES_SCRATCH", None)
            expected = ("/tmp/swe-game/gb_x" if os.path.isdir("/data2") else "/tmp/gb_x")
            self.assertEqual(cr.ascii_safe_scratch_root("gb_x"), expected)
        self.assertEqual(cr.POSIX_SCRATCH_VOLUME, "/data2")


class ContainmentTests(unittest.TestCase):
    def test_posix_is_a_plain_prefix_compare(self) -> None:
        with tempfile.TemporaryDirectory() as d:
            inside = os.path.join(d, "child")
            os.mkdir(inside)
            self.assertTrue(hostenv.is_within(inside, d, platform="linux"))
            self.assertFalse(hostenv.is_within(d, inside, platform="linux"))
            self.assertFalse(hostenv.is_within(d + "_sibling", d, platform="linux"))

    def test_windows_compare_is_case_insensitive(self) -> None:
        with tempfile.TemporaryDirectory() as d:
            inside = os.path.join(d, "Child")
            os.mkdir(inside)
            swapped_root = d.upper() if d != d.upper() else d.lower()
            with mock.patch("os.path.normcase", side_effect=lambda s: s.lower()):
                self.assertTrue(hostenv.is_within(inside, swapped_root, platform="win32"))
                self.assertFalse(hostenv.is_within(inside, swapped_root, platform="linux"))


class CopytreeTests(unittest.TestCase):
    def test_symlinks_reproduced_on_posix_followed_on_windows(self) -> None:
        self.assertEqual(hostenv.copytree_kwargs(platform="linux"), {"symlinks": True})
        self.assertEqual(hostenv.copytree_kwargs(platform="win32"),
                         {"symlinks": False, "ignore_dangling_symlinks": True})


class FindGodotLinuxParityTests(unittest.TestCase):


    def test_godot_bin_that_exists_wins(self) -> None:
        self.assertEqual(
            hostenv.find_godot(platform="linux", environ={"GODOT_BIN": "/x/godot"},
                               exists=lambda p: p == "/x/godot"),
            "/x/godot")

    def test_opt_build_when_no_env(self) -> None:
        self.assertEqual(
            hostenv.find_godot(platform="linux", environ={},
                               exists=lambda p: p == "/opt/godot451-bin/godot"),
            "/opt/godot451-bin/godot")

    def test_bare_name_when_nothing_exists(self) -> None:
        self.assertEqual(hostenv.find_godot(platform="linux", environ={}, exists=lambda p: False),
                         "godot")

    def test_dangling_godot_bin_falls_to_bare_name_not_to_opt(self) -> None:

        self.assertEqual(
            hostenv.find_godot(platform="linux", environ={"GODOT_BIN": "/nope"},
                               exists=lambda p: p == "/opt/godot451-bin/godot"),
            "godot")


class FindGodotWindowsTests(unittest.TestCase):
    ENV = {"LOCALAPPDATA": r"C:\Users\dev\AppData\Local", "ProgramFiles": r"C:\Program Files",
           "USERPROFILE": r"C:\Users\dev", "SystemDrive": "C:"}

    def test_godot_bin_first(self) -> None:
        got = hostenv.find_godot(platform="win32",
                                 environ={**self.ENV, "GODOT_BIN": r"D:\godot\Godot_v4.5.1-stable_win64_console.exe"},
                                 exists=lambda p: p.startswith(r"D:\godot"), which=lambda n: None)
        self.assertEqual(got, r"D:\godot\Godot_v4.5.1-stable_win64_console.exe")

    def test_then_path_with_console_build_preferred(self) -> None:
        plain = r"C:\bin\Godot_v4.5.1-stable_win64.exe"
        console = r"C:\bin\Godot_v4.5.1-stable_win64_console.exe"

        def which(name):
            return plain if name == "Godot_v4.5.1-stable_win64.exe" else None

        got = hostenv.find_godot(platform="win32", environ=self.ENV, which=which,
                                 exists=lambda p: p in (plain, console))
        self.assertEqual(got, console)

    def test_then_common_install_dirs(self) -> None:
        target = os.path.join(self.ENV["LOCALAPPDATA"], "Programs", "Godot",
                              "Godot_v4.5.1-stable_win64_console.exe")
        got = hostenv.find_godot(platform="win32", environ=self.ENV, which=lambda n: None,
                                 exists=lambda p: p == target, iglob=lambda pat: [])
        self.assertEqual(got, target)

    def test_then_globbed_other_versions(self) -> None:
        drive_godot = os.path.join("C:" + os.sep, "Godot")
        found = os.path.join(drive_godot, "Godot_v4.4.1-stable_win64_console.exe")

        def iglob(pat):
            return [found] if pat.startswith(drive_godot) and "console" in pat else []

        got = hostenv.find_godot(platform="win32", environ=self.ENV, which=lambda n: None,
                                 exists=lambda p: p == found, iglob=iglob)
        self.assertEqual(got, found)

    def test_bare_name_and_a_hint_when_nothing_is_found(self) -> None:
        got = hostenv.find_godot(platform="win32", environ=self.ENV, which=lambda n: None,
                                 exists=lambda p: False, iglob=lambda pat: [])
        self.assertEqual(got, "godot")
        hint = hostenv.godot_not_found_hint(got, platform="win32", environ=self.ENV)
        self.assertIn("GODOT_BIN", hint)
        self.assertIn("_console.exe", hint)
        self.assertIn(r"C:\Users\dev\AppData\Local", hint)

    def test_prefer_console_build_only_swaps_when_the_sibling_exists(self) -> None:
        plain = r"C:\g\godot.windows.editor.x86_64.exe"
        self.assertEqual(hostenv.prefer_console_build(plain, exists=lambda p: False), plain)
        self.assertEqual(
            hostenv.prefer_console_build(plain, exists=lambda p: p.endswith(".console.exe")),
            r"C:\g\godot.windows.editor.x86_64.console.exe")
        already = r"C:\g\Godot_v4.5.1-stable_win64_console.exe"
        self.assertEqual(hostenv.prefer_console_build(already, exists=lambda p: True), already)

    def test_windows_install_dirs_skip_unset_variables(self) -> None:
        dirs = hostenv.windows_install_dirs({"SystemDrive": "C:"})
        self.assertTrue(all(d.startswith("C:") for d in dirs), dirs)
        self.assertIn(os.path.join("C:" + os.sep, "Godot"), dirs)


class GodotArgvTests(unittest.TestCase):
    def test_paths_get_forward_slashes_only_on_windows(self) -> None:
        self.assertEqual(hostenv.godot_arg_path(r"C:\tmp\p\f.png", platform="win32"), "C:/tmp/p/f.png")
        self.assertEqual(hostenv.godot_arg_path(r"a\b", platform="linux"), r"a\b")

    def test_returncode_wording(self) -> None:
        self.assertEqual(hostenv.describe_returncode(1, platform="linux"), "exit code 1")
        self.assertEqual(hostenv.describe_returncode(3221225477, platform="linux"), "exit code 3221225477")
        self.assertEqual(hostenv.describe_returncode(3221225477, platform="win32"),
                         "exit code 3221225477 (0xC0000005)")


class DisplayTests(unittest.TestCase):
    def test_linux_needs_xvfb_run_from_path(self) -> None:
        d = hostenv.display_status(platform="linux", environ={}, which=lambda n: None)
        self.assertFalse(d.available)
        self.assertIn("xvfb-run", d.detail)

    def test_linux_reports_a_path_shim(self) -> None:
        d = hostenv.display_status(platform="linux", environ={"DISPLAY": ":57"},
                                   which=lambda n: "/tmp/cd209/bin/xvfb-run")
        self.assertTrue(d.available)
        self.assertIn("shim", d.detail)
        self.assertIn(":57", d.detail)
        plain = hostenv.display_status(platform="linux", environ={}, which=lambda n: "/usr/bin/xvfb-run")
        self.assertNotIn("shim", plain.detail)

    def test_windows_never_asks_for_xvfb(self) -> None:

        d = hostenv.display_status(platform="win32", environ={}, which=lambda n: None)
        self.assertTrue(d.available)
        self.assertNotIn("xvfb", d.detail.lower())

    def test_check_runtime_x_launcher_is_empty_off_linux(self) -> None:
        cr = _load("check_runtime")
        with _win():
            self.assertEqual(cr.x_launcher(), [])


class StdioTests(unittest.TestCase):
    def test_noop_on_posix(self) -> None:
        self.assertFalse(hostenv.configure_stdio(platform="linux", environ={}))

    def test_reconfigures_utf8_on_windows_unless_operator_did(self) -> None:
        out, err = mock.Mock(), mock.Mock()
        with mock.patch.object(sys, "stdout", out), mock.patch.object(sys, "stderr", err):
            self.assertTrue(hostenv.configure_stdio(platform="win32", environ={}))
            out.reconfigure.assert_called_once_with(encoding="utf-8", errors="replace")
            err.reconfigure.assert_called_once_with(encoding="utf-8", errors="replace")
            out.reset_mock()
            self.assertFalse(hostenv.configure_stdio(platform="win32", environ={"PYTHONUTF8": "1"}))
            out.reconfigure.assert_not_called()


class RunCapturedTests(unittest.TestCase):
    def test_posix_shape(self) -> None:
        rc, out, err = hostenv.run_captured(
            [sys.executable, "-c", "import sys; print('héllo'); sys.stderr.write('e')"],
            os.getcwd(), 30, platform="linux")
        self.assertEqual((rc, out.strip(), err), (0, "héllo", "e"))

    def test_posix_missing_binary_is_minus_two_and_names_it(self) -> None:
        rc, _out, err = hostenv.run_captured(["no-such-binary-gb", "x"], os.getcwd(), 5, platform="linux")
        self.assertEqual(rc, -2)
        self.assertIn("no-such-binary-gb", err)

    def test_posix_timeout_is_minus_nine(self) -> None:
        rc, _out, err = hostenv.run_captured(
            [sys.executable, "-c", "import time; time.sleep(30)"], os.getcwd(), 1, platform="linux")
        self.assertEqual(rc, -9)
        self.assertIn("TIMEOUT after 1s", err)

    def test_windows_branch_runs_and_returns_the_same_shape(self) -> None:

        rc, out, err = hostenv.run_captured(
            [sys.executable, "-c", "print('w')"], os.getcwd(), 30, platform="win32")
        self.assertEqual((rc, out.strip(), err), (0, "w", ""))
        rc, _o, err = hostenv.run_captured(["no-such-binary-gb"], os.getcwd(), 5, platform="win32")
        self.assertEqual(rc, -2)

    def test_windows_timeout_kills_the_whole_tree(self) -> None:
        killed: list[int] = []

        def fake_kill(pid, *, platform=None):
            killed.append(pid)
            os.kill(pid, 9)

        with mock.patch.object(hostenv, "kill_tree", fake_kill):
            rc, _o, err = hostenv.run_captured(
                [sys.executable, "-c", "import time; time.sleep(30)"], os.getcwd(), 1, platform="win32")
        self.assertEqual(rc, -9)
        self.assertEqual(len(killed), 1)
        self.assertIn("TIMEOUT after 1s", err)

    def test_kill_tree_uses_taskkill_on_windows(self) -> None:
        with mock.patch.object(hostenv.subprocess, "run") as run:
            hostenv.kill_tree(4242, platform="win32")
        argv = run.call_args[0][0]
        self.assertEqual(argv[:4], ["taskkill", "/F", "/T", "/PID"])
        self.assertEqual(argv[4], "4242")


class DoctorTests(unittest.TestCase):
    def _rows(self, version: str, *, godot: str = sys.executable, platform: str = "linux"):
        def fake_run(cmd, cwd, timeout, **kw):
            return 0, version + "\n", ""

        with tempfile.TemporaryDirectory() as d:
            return hostenv.doctor(scratch=os.path.join(d, "s"), godot=godot, platform=platform,
                                  run=fake_run)

    def test_matching_engine_and_writable_scratch_pass(self) -> None:
        rows = {r.name: r for r in self._rows("4.5.1.stable.official.f6f3b5d5b")}
        self.assertTrue(rows["godot"].ok, rows["godot"].detail)
        self.assertTrue(rows["scratch"].ok, rows["scratch"].detail)
        self.assertTrue(rows["python_deps"].ok)

    def test_wrong_engine_version_is_a_blocking_row(self) -> None:
        rows = {r.name: r for r in self._rows("4.4.1.stable.official")}
        self.assertFalse(rows["godot"].ok)
        self.assertTrue(rows["godot"].required)
        self.assertIn("4.5.1", rows["godot"].detail)

    def test_missing_engine_is_blocking_with_the_hint(self) -> None:
        rows = {r.name: r for r in self._rows("", godot="no-such-godot-gb")}
        self.assertFalse(rows["godot"].ok)
        self.assertIn("GODOT_BIN", rows["godot"].detail)

    def test_windows_warns_about_a_non_console_build(self) -> None:
        with tempfile.TemporaryDirectory() as d:
            exe = os.path.join(d, "Godot_v4.5.1-stable_win64.exe")
            Path(exe).write_bytes(b"")
            rows = {r.name: r for r in self._rows("4.5.1.stable", godot=exe, platform="win32")}
        self.assertIn("godot_console", rows)
        self.assertFalse(rows["godot_console"].ok)
        self.assertFalse(rows["godot_console"].required)

    def test_print_doctor_exit_status_follows_required_rows(self) -> None:
        ok = [hostenv.DoctorRow("a", True, True, "x"), hostenv.DoctorRow("b", False, False, "advice")]
        buf = io.StringIO()
        self.assertEqual(hostenv.print_doctor(ok, buf), 0)
        self.assertIn("warn b", buf.getvalue())
        bad = ok + [hostenv.DoctorRow("c", False, True, "broken")]
        buf = io.StringIO()
        self.assertEqual(hostenv.print_doctor(bad, buf), 1)
        self.assertIn("FAIL c", buf.getvalue())
        self.assertIn("1 blocking problem", buf.getvalue())

    def test_check_runtime_doctor_flag_runs_end_to_end(self) -> None:
        cr = _load("check_runtime")
        buf = io.StringIO()
        with mock.patch.object(sys, "argv", ["check_runtime.py", "--doctor"]), \
                mock.patch.object(sys, "stdout", buf):
            rc = cr.main()
        self.assertIn(rc, (0, 1))
        text = buf.getvalue()
        for name in ("os", "godot", "display", "scratch", "python_deps", "probe", "x_launcher"):
            self.assertIn(name, text)

    def test_check_runtime_refuses_to_run_without_an_engine(self) -> None:
        cr = _load("check_runtime")
        err = io.StringIO()
        with mock.patch.object(cr, "GODOT", "no-such-godot-gb"), \
                mock.patch.object(sys, "argv", ["check_runtime.py", "/nonexistent/project"]), \
                mock.patch.object(sys, "stderr", err):
            self.assertEqual(cr.main(), 2)
        self.assertIn("GODOT_BIN", err.getvalue())


class NewlineTests(unittest.TestCase):
    CLASSES = ("*.gd", "*.json", "*.tscn", "*.tres", "*.cfg", "*.godot", "*.md", "*.py", "*.sh")

    def test_gitattributes_pins_every_parsed_or_hashed_class_to_lf(self) -> None:
        rules = {}
        for line in (REPO / ".gitattributes").read_text(encoding="utf-8").splitlines():
            line = line.strip()
            if not line or line.startswith("#"):
                continue
            pattern, _, attrs = line.partition(" ")
            rules[pattern] = attrs.split()
        for cls in self.CLASSES:
            self.assertIn(cls, rules, f"{cls} is not pinned")
            self.assertIn("eol=lf", rules[cls])
            self.assertIn("text", rules[cls])

        self.assertEqual(rules["eval/harness/*.gd"], ["text", "eol=lf"])
        self.assertEqual(rules["eval/tasks/**/*.json"], ["text", "eol=lf"])

    def test_harness_faces_are_lf_on_disk(self) -> None:
        for gd in sorted((REPO / "eval" / "harness").glob("*.gd")):
            self.assertNotIn(b"\r\n", gd.read_bytes(), f"{gd.name} has CRLF on disk")

    def test_register_probe_reads_crlf_and_writes_lf(self) -> None:
        cr = _load("check_runtime")
        with tempfile.TemporaryDirectory() as d:
            pg = Path(d) / "project.godot"
            pg.write_bytes(b"[application]\r\nconfig/name=\"x\"\r\n\r\n[autoload]\r\n\r\nFoo=\"*res://foo.gd\"\r\n")
            self.assertTrue(cr.register_probe(d))
            raw = pg.read_bytes()
        self.assertNotIn(b"\r", raw)
        self.assertIn(b'[autoload]\n\nGBRuntimeProbe="*res://gb_runtime_probe.gd"\n', raw)
        self.assertIn(b'Foo="*res://foo.gd"', raw)

    def test_count_declared_actions_is_newline_insensitive(self) -> None:
        cr = _load("check_runtime")
        body = "[application]\nname=x\n\n[input]\n\njump={\n\"deadzone\": 0.5\n}\nleft={\n}\n"
        with tempfile.TemporaryDirectory() as d:
            (Path(d) / "project.godot").write_bytes(body.encode())
            lf = cr.count_declared_actions(d)
            (Path(d) / "project.godot").write_bytes(body.replace("\n", "\r\n").encode())
            crlf = cr.count_declared_actions(d)
        self.assertEqual((lf, crlf), (2, 2))


class OtherGatesTests(unittest.TestCase):
    def test_check_gdd_absolute_path_test_is_platform_aware(self) -> None:
        gdd = _load("check_gdd")
        src = (GATES / "check_gdd.py").read_text(encoding="utf-8")
        self.assertIn("os.path.isabs(s)", src)
        self.assertNotIn('if s.startswith("/"):', src)
        self.assertTrue(hasattr(gdd, "resolve_path"))

    def test_check_deliverables_official_harness_falls_back_to_the_repo_copy(self) -> None:
        with mock.patch.dict(os.environ, {"GB_OFFICIAL_HARNESS": "/definitely/not/here"}):
            cd = _load("check_deliverables")
        self.assertEqual(Path(cd.OFFICIAL_HARNESS).resolve(), (REPO / "eval" / "harness").resolve())
        self.assertTrue(cd.official_hashes(), "no probe hashes from the repo harness")

    def test_check_levels_uses_shared_discovery(self) -> None:
        cl = _load("check_levels")
        self.assertEqual(cl.GODOT, hostenv.find_godot())

    def test_check_anchors_no_longer_hardcodes_tmp(self) -> None:
        src = (GATES / "check_anchors.py").read_text(encoding="utf-8")
        self.assertNotIn('"/tmp/', src)
        self.assertIn("tempfile.gettempdir()", src)


if __name__ == "__main__":
    unittest.main()
