
from __future__ import annotations

import asyncio
import json
import os
import signal
import sys

from harbor.models.verifier.result import VerifierResult
from harbor.verifier.base import BaseVerifier

from ..taskgen.package import write_json


class SWEGameVerifier(BaseVerifier):
    def __init__(self, *, engine="auto", visual_judge="none",
                 registry_version=None, collect_only=False,
                 mode5_profile="auto", mode5_state_dir=None,
                 docker="docker", **kwargs):
        super().__init__(**kwargs)
        self.engine = engine
        self.visual_judge = visual_judge
        self.registry_version = registry_version
        self.collect_only = collect_only
        self.mode5_profile = mode5_profile
        self.mode5_state_dir = mode5_state_dir
        self.docker = docker

    async def verify(self) -> VerifierResult:
        out = self.trial_paths.verifier_dir
        out.mkdir(parents=True, exist_ok=True)
        if self.task.config.verifier.environment_mode.value != "separate":
            raise ValueError("SWE-Game requires a separate verifier environment")
        workspace = out / "workspace"
        await self.environment.download_dir("/workspace", workspace)
        command = [sys.executable, "-m", "evalsys.harbor", "grade",
                   "--package", str(self.task.task_dir / "evaluator" / "package"),
                   "--workspace", str(workspace), "--out", str(out),
                   "--engine", self.engine, "--visual-judge", self.visual_judge]
        if self.registry_version:
            command += ["--registry-version", self.registry_version]
        if self.collect_only:
            command += ["--collect-only"]
        command += ["--mode5-profile", self.mode5_profile, "--docker", self.docker]
        if self.mode5_state_dir:
            command += ["--mode5-state-dir", self.mode5_state_dir]
        # A process group lets Harbor's verifier timeout stop Godot descendants.
        with (out / "eval.stdout.log").open("wb") as stdout, \
                (out / "eval.stderr.log").open("wb") as stderr:
            process = await asyncio.create_subprocess_exec(
                *command, stdout=stdout, stderr=stderr, start_new_session=True)
            try:
                code = await process.wait()
            except asyncio.CancelledError:
                try:
                    os.killpg(process.pid, signal.SIGKILL)
                except ProcessLookupError:
                    pass
                await process.wait()
                write_json(out / "swe-game.json", {
                    **self.task.config.metadata,
                    "status": "verifier_timeout", "rewards": None, "resolved": None})
                raise
        if code:
            write_json(out / "swe-game.json", {
                **self.task.config.metadata,
                "status": "evaluator_error", "rewards": None, "resolved": None,
                "exit_code": code})
            raise RuntimeError(f"evalsys exited {code}; see {out / 'eval.stderr.log'}")
        record = json.loads((out / "swe-game.json").read_text(encoding="utf-8"))
        if record["rewards"] is not None:
            write_json(out / "reward.json", record["rewards"])
        return VerifierResult(rewards=record["rewards"])
