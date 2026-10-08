


from __future__ import annotations

import json
import os
from pathlib import Path
import signal
import subprocess
from typing import Iterable, Sequence


PROCESS_PREFIX_ENV = "GB_UNITY_CANDIDATE_PROCESS_PREFIX_JSON"
CANDIDATE_UID_ENV = "GB_UNITY_CANDIDATE_UID"
CANDIDATE_GID_ENV = "GB_UNITY_CANDIDATE_GID"


def candidate_process_command(command: Sequence[str]) -> tuple[str, ...]:
    raw = os.environ.get(PROCESS_PREFIX_ENV, "").strip()
    if not raw:
        return tuple(str(item) for item in command)
    value = json.loads(raw)
    if not isinstance(value, list) or not value or not all(
        isinstance(item, str) and item for item in value
    ):
        raise ValueError(f"{PROCESS_PREFIX_ENV} must be a non-empty JSON string array")
    return (*value, *(str(item) for item in command))


def run_candidate_process(
    command: Sequence[str], *, timeout: int
) -> subprocess.CompletedProcess[str]:


    argv = tuple(str(item) for item in command)
    process = subprocess.Popen(
        argv,
        text=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        start_new_session=os.name == "posix",
    )
    try:
        stdout, stderr = process.communicate(timeout=timeout)
    except subprocess.TimeoutExpired as exc:
        if os.name == "posix":
            os.killpg(process.pid, signal.SIGKILL)
        else:
            process.kill()
        stdout, stderr = process.communicate()
        raise subprocess.TimeoutExpired(
            argv, timeout, output=stdout, stderr=stderr
        ) from exc
    return subprocess.CompletedProcess(argv, process.returncode, stdout, stderr)


def terminate_candidate_process_tree(
    process: subprocess.Popen[str], *, force: bool = False
) -> None:


    if os.name == "posix":
        try:
            os.killpg(process.pid, signal.SIGKILL if force else signal.SIGTERM)
        except ProcessLookupError:
            pass
        return
    if process.poll() is not None:
        return
    if force:
        process.kill()
    else:
        process.terminate()


def candidate_identity() -> tuple[int, int] | None:
    uid = os.environ.get(CANDIDATE_UID_ENV, "").strip()
    gid = os.environ.get(CANDIDATE_GID_ENV, "").strip()
    if not uid and not gid:
        return None
    if not uid.isdecimal() or not gid.isdecimal():
        raise ValueError("certified VM candidate UID/GID must be decimal integers")
    return int(uid), int(gid)


def grant_candidate_access(paths: Iterable[str | Path]) -> None:


    identity = candidate_identity()
    if identity is None:
        return
    uid, gid = identity
    for supplied in paths:
        path = Path(supplied)
        if not path.exists():
            continue
        targets = [path]
        if path.is_dir():
            targets.extend(path.rglob("*"))
        for target in targets:
            os.chown(target, uid, gid, follow_symlinks=False)


__all__ = [
    "CANDIDATE_GID_ENV",
    "CANDIDATE_UID_ENV",
    "PROCESS_PREFIX_ENV",
    "candidate_identity",
    "candidate_process_command",
    "grant_candidate_access",
    "run_candidate_process",
    "terminate_candidate_process_tree",
]
