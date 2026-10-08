"""Verify public Unity helpers in a real licensed Agent container, without a model."""
from __future__ import annotations

import argparse
import json
from pathlib import Path
import shutil
import sys
import tempfile


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--state-dir", required=True)
    parser.add_argument("--package", required=True)
    parser.add_argument("--fixture", required=True)
    parser.add_argument("--out", required=True)
    parser.add_argument("--expected-smoke", choices=("pass", "fail"), default="pass",
                        help="Use fail only for an explicitly controlled runtime-error negative")
    parser.add_argument("--inject-runtime-error", action="store_true",
                        help="Inject a marked startup exception into a disposable control copy only")
    args = parser.parse_args()
    if args.inject_runtime_error and args.expected_smoke != "fail":
        parser.error("--inject-runtime-error requires --expected-smoke fail")
    root = Path(__file__).resolve().parents[1]
    sys.path.insert(0, str(root / "eval/evalsys"))
    from evalsys.taskgen.docker_sandbox import DockerSandbox
    state, out = Path(args.state_dir), Path(args.out).resolve()
    if out.exists():
        parser.error("choose a new output directory")
    out.mkdir(parents=True)
    lock = json.loads((state / "image-lock.json").read_text())
    private = json.loads((state / "license-provider.json").read_text())
    sandbox = DockerSandbox(image=lock["agent_image"]["id"], log_path=out / "sandbox.log",
                            cell="controlled-public-helper", host_machine_identity=private["provider"] == "existing-home")
    try:
        with tempfile.TemporaryDirectory(prefix="gb-mode5-helper-fixture-") as temp:
            workspace = Path(temp) / "workspace"
            # This is explicitly a disposable control, never the GLM submission.
            shutil.copytree(Path(args.fixture), workspace, ignore=shutil.ignore_patterns(
                "GBReleaseIntentionalSyntaxError.cs", "GBReleaseIntentionalSyntaxError.cs.meta"))
            if args.inject_runtime_error:
                target = workspace / "Assets" / "GBReleaseIntentionalRuntimeError.cs"
                if target.exists():
                    raise RuntimeError("reserved controlled runtime-error path already exists")
                target.write_text(
                    "// Explicit release control injection; never a model submission.\n"
                    "using UnityEngine;\n"
                    "public static class GBReleaseIntentionalRuntimeError {\n"
                    " [RuntimeInitializeOnLoadMethod(RuntimeInitializeLoadType.BeforeSceneLoad)]\n"
                    " static void Fail() { throw new System.InvalidOperationException(\n"
                    ' "GB_RELEASE_CONTROL_RUNTIME_ERROR"); }\n}\n', encoding="utf-8")
            shutil.copy2(Path(args.package) / "visible/environment.lock.json", workspace / "environment.lock.json")
            (workspace / "PROMPT.md").write_text("Controlled public-toolchain fixture; not a model score.\n", encoding="utf-8")
            sandbox.start()
            sandbox.copy_in(workspace)
        sandbox.configure_unity_license(provider=private["provider"],
                                        source=Path(private["source"]) if private.get("source") else None,
                                        endpoint=private.get("endpoint", ""))
        env = ["env", "HOME=/opt/gb-agent-home", "GB_TASK_WORKSPACE=/workspace"]
        check = sandbox._exec([*env, "gb-unity", "check", "/workspace", "--timeout", "600"], timeout=700, check=False)
        if check.returncode:
            raise RuntimeError("controlled public Unity build failed; inspect sanitized sandbox log")
        smoke = sandbox._exec([*env, "gb-unity", "smoke", "/workspace", "--timeout", "8"], timeout=30, check=False)
        snapshot = json.loads(sandbox.read_text("/workspace/.selfcheck/status.json") or "{}")
        cleanup = sandbox._exec(["python3", "-c",
            "from pathlib import Path; import json; "
            "rows=[{'name': p.read_text().strip(), 'state': "
            "(p.parent/'stat').read_text().rsplit(')',1)[1].split()[0]} "
            "for p in Path('/proc').glob('[0-9]*/comm') if p.exists()]; "
            "print(json.dumps([r for r in rows if r['name'].startswith('GameBenchSelfCh') "
            "or r['name'] == 'Xvfb']))"],
            timeout=15, check=False)
        cleanup_records = json.loads(cleanup.stdout) if cleanup.returncode == 0 else []
        # Container PID 1 may briefly retain exited children. A zombie cannot
        # execute or retain streams; it is not a surviving Player/Xvfb process.
        cleanup_passed = cleanup.returncode == 0 and all(
            row["state"] == "Z" for row in cleanup_records)
        (out / "status.json").write_text(json.dumps(snapshot, indent=2) + "\n", encoding="utf-8")
        print(json.dumps(snapshot, indent=2))
        build_passed = all(snapshot.get(key) == "pass" for key in (
            "scaffold_import", "script_compile", "linux_player_build"))
        smoke_matched = (snapshot.get("player_smoke") == args.expected_smoke
                         and (smoke.returncode == 0) == (args.expected_smoke == "pass"))
        (out / "control-result.json").write_text(json.dumps({
            "purpose": "public helper control; not a model score",
            "expected_player_smoke": args.expected_smoke,
            "injected_runtime_error": args.inject_runtime_error,
            "player_process_cleanup": cleanup_passed,
            "player_process_records": cleanup_records,
            "matched": build_passed and smoke_matched and cleanup_passed,
        }, indent=2) + "\n", encoding="utf-8")
        return int(not (build_passed and smoke_matched and cleanup_passed))
    finally:
        sandbox.close()


if __name__ == "__main__":
    raise SystemExit(main())
