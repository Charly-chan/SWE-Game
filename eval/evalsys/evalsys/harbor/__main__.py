
from __future__ import annotations

import argparse
from datetime import datetime
from importlib.metadata import version
import json
from pathlib import Path
import subprocess
import sys

from .adapter import export_task, grade, summarize


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    commands = parser.add_subparsers(dest="command", required=True)
    export = commands.add_parser("export", help="Export an existing gen-task package")
    export.add_argument("--package", type=Path, required=True)
    export.add_argument("--out", type=Path, required=True)
    export.add_argument("--image")
    export.add_argument("--agent-timeout", type=int, default=1800)
    export.add_argument("--verifier-timeout", type=int, default=3600)
    score = commands.add_parser("grade", help="Collect and evaluate one workspace")
    score.add_argument("--package", type=Path, required=True)
    score.add_argument("--workspace", type=Path, required=True)
    score.add_argument("--out", type=Path, required=True)
    run = commands.add_parser("run", help="Run Harbor with the SWE-Game verifier")
    run.add_argument("--path", type=Path, required=True)
    run.add_argument("--jobs-dir", type=Path, required=True)
    run.add_argument("--job-name", default=datetime.now().strftime("swe-game-%Y%m%d-%H%M%S"))
    run.add_argument("--n-concurrent", type=int, default=1)
    run.add_argument("harbor_args", nargs=argparse.REMAINDER,
                     help="Harbor options after --, e.g. -- --agent codex --model gpt-5.4")
    for sub in (score, run):
        sub.add_argument("--engine", choices=("auto", "on", "off"), default="auto")
        sub.add_argument("--visual-judge", choices=("none", "local", "vlm"), default="none")
        sub.add_argument("--registry-version")
        sub.add_argument("--collect-only", action="store_true")
    summary = commands.add_parser("summarize")
    summary.add_argument("job", type=Path)
    args = vars(parser.parse_args())
    command = args.pop("command")
    if command == "export":
        print(export_task(**args))
    elif command == "grade":
        print(json.dumps(grade(**args), indent=2))
    elif command == "summarize":
        print(json.dumps(summarize(args["job"])["groups"], indent=2))
    else:
        if version("harbor") != "0.23.0":
            parser.error("Install requirements-harbor.txt (Harbor 0.23.0)")
        extra = args.pop("harbor_args")
        if extra[:1] == ["--"]:
            extra = extra[1:]
        job = args["jobs_dir"].resolve() / args["job_name"]
        if job.exists():
            parser.error(f"Job already exists: {job}; choose a new --job-name")
        cli = [sys.executable, "-c", "from harbor.cli.main import app; app()",
               "run", *extra, "--path", str(args["path"].resolve()),
               "--jobs-dir", str(args["jobs_dir"].resolve()),
               "--job-name", args["job_name"], "--n-concurrent", str(args["n_concurrent"]),
               "--verifier", "evalsys.harbor.verifier:SWEGameVerifier"]
        for key in ("engine", "visual_judge", "registry_version", "collect_only"):
            if args[key] is not None:
                cli += ["--verifier-kwarg", f"{key}={json.dumps(args[key])}"]
        result = None
        try:
            code = subprocess.call(cli)
        finally:
            if job.exists():
                result = summarize(job)
                print(json.dumps(result["groups"], indent=2))
                print(f"SWE-Game summary: {job / 'swe-game-summary.json'}")
        return code or int(bool(result and any(t["exception"] for t in result["trials"])))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
