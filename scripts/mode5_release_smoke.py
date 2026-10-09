"""Run a bounded licensed Mode 5 smoke using private CC provider settings.

Run on the Linux Docker coordinator (WSL is supported). No credentials are
copied or printed by this wrapper. Host unit tests use the user's Python.
"""
from __future__ import annotations

import argparse
from pathlib import Path
import sys


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--state-dir", required=True)
    parser.add_argument("--claude-settings")
    parser.add_argument("--game", default="shadow_walker")
    parser.add_argument("--out")
    parser.add_argument("--timeout", type=int, default=7200)
    parser.add_argument("--doctor", action="store_true")
    parser.add_argument("--resume", action="store_true")
    args = parser.parse_args()
    root = Path(__file__).resolve().parents[1]
    sys.path.insert(0, str(root / "eval/evalsys"))
    from evalsys.taskgen.mode5.community_cli import main as mode5_main
    if args.doctor:
        return mode5_main(["doctor", "--state-dir", args.state_dir, "--json"])
    if not args.claude_settings or not args.out or args.timeout <= 0:
        parser.error("run needs --claude-settings, --out and a positive --timeout")
    command = [
        "run", "--state-dir", args.state_dir, "--game", args.game,
        "--out", args.out, "--harness", "claude", "--claude-settings", args.claude_settings,
        "--agent-timeout", str(args.timeout), "--fidelity-judge", "none",
    ]
    if args.resume:
        command.append("--resume")
    return mode5_main(command)


if __name__ == "__main__":
    raise SystemExit(main())
