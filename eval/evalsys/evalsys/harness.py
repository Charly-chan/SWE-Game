


from __future__ import annotations

import hashlib
import json
import os
import re
import subprocess
from pathlib import Path
from typing import Mapping

CONTRACT_VERSION = "1"
FACES = (
    "gb_probe.gd",
    "gb_truth_driver.gd",
    "gb_capture_probe.gd",
    "gb_route_driver.gd",
)


AUXILIARY_FACES = (
    "gb_asset_probe.gd",
)
LOADABLE_FACES = FACES + AUXILIARY_FACES


IDENTIFYING_KEYS = ("contract_version",) + FACES


def harness_dir() -> Path:
    return Path(
        os.environ.get(
            "GB_HARNESS_DIR", str(Path(__file__).resolve().parents[2] / "harness")
        )
    )


def face_path(name: str) -> Path:
    if name not in LOADABLE_FACES:
        raise ValueError(f"not a registered harness face: {name}")
    return harness_dir() / name


def repo_root() -> Path:

    return Path(__file__).resolve().parents[3]


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for block in iter(lambda: stream.read(1 << 20), b""):
            digest.update(block)
    return "sha256:" + digest.hexdigest()


def normalize_digest(value: object) -> str:

    text = str(value or "").strip()
    if text.startswith("sha256:"):
        text = text[7:]
    return text.lower()


def provenance(*, require: bool = True) -> dict[str, str]:
    result = {"contract_version": CONTRACT_VERSION}
    for name in FACES:
        path = face_path(name)
        if require and not path.is_file():
            raise FileNotFoundError(f"harness face is not on disk: {path}")
        result[name] = _sha256(path) if path.is_file() else ""
    return result


def assert_current(recorded: Mapping[str, object] | None) -> None:


    if not isinstance(recorded, Mapping):
        raise ValueError("truth snapshot has no harness identity")
    current = provenance(require=True)
    mismatches = [
        name
        for name in IDENTIFYING_KEYS
        if str(recorded.get(name, "")) != current[name]
    ]
    if mismatches:
        raise ValueError(
            "truth snapshot harness does not match the current evaluator: "
            + ", ".join(mismatches)
        )


_COMMITTED: dict[str, dict[str, tuple[str, ...]]] = {}


def _committed_blobs(name: str) -> dict[str, tuple[str, ...]]:

    if name not in FACES:
        raise ValueError(f"not a registered harness face: {name}")
    cached = _COMMITTED.get(name)
    if cached is not None:
        return cached
    rel = f"eval/harness/{name}"
    root = repo_root()
    logged = subprocess.run(
        ["git", "log", "--all", "--pretty=%H", "--", rel],
        cwd=root,
        capture_output=True,
        text=True,
        check=False,
    )
    mapping: dict[str, list[str]] = {}
    if logged.returncode == 0:
        for commit in logged.stdout.split():
            shown = subprocess.run(
                ["git", "show", f"{commit}:{rel}"],
                cwd=root,
                capture_output=True,
                check=False,
            )
            if shown.returncode != 0:
                continue
            digest = hashlib.sha256(shown.stdout).hexdigest()
            mapping.setdefault(digest, []).append(commit)
    frozen = {key: tuple(values) for key, values in mapping.items()}
    _COMMITTED[name] = frozen
    return frozen


def locate_committed(name: str, digest: object) -> str | None:

    commits = _committed_blobs(name).get(normalize_digest(digest))
    return commits[0] if commits else _released_identity(name, digest)


def _released_identity(name: str, digest: object) -> str | None:
    root = repo_root()
    revision = subprocess.run(
        ["git", "rev-parse", "HEAD"], cwd=root, capture_output=True, text=True,
    )
    if revision.returncode:
        return None
    commit = revision.stdout.strip()
    manifest = subprocess.run(
        ["git", "show", f"{commit}:eval/harness/released-identities.json"],
        cwd=root, capture_output=True,
    )
    if manifest.returncode:
        return None
    try:
        payload = json.loads(manifest.stdout)
        if payload.get("schema_version") != 1:
            return None
        record = payload["faces"][name][normalize_digest(digest)]
        sha = record["sha256"]
        if not isinstance(sha, str) or not re.fullmatch(r"[0-9a-f]{64}", sha):
            return None
        relative = f"eval/harness/versions/{sha}/{name}"
        if record["path"] != relative:
            return None
    except (ValueError, KeyError, TypeError):
        return None
    source = subprocess.run(
        ["git", "show", f"{commit}:{relative}"], cwd=root, capture_output=True,
    )
    if source.returncode or hashlib.sha256(source.stdout).hexdigest() != sha:
        return None
    return f"{commit}:{relative}"


def recorded_faces(recorded: Mapping[str, object]) -> dict[str, str]:

    out: dict[str, str] = {}
    for name in FACES:
        digest = recorded.get(name, "")
        if digest:
            out[name] = str(digest)
    return out


def identity_from_snapshot(snapshot: object) -> dict[str, str]:


    recorded = {
        str(key): str(value)
        for key, value in (getattr(snapshot, "harness", None) or {}).items()
    }

    def _put(name: str, raw: object) -> None:
        if recorded.get(name):
            return
        text = str(raw or "").strip()
        if not text:
            return
        recorded[name] = text if text.startswith("sha256:") else "sha256:" + text


    _put("gb_probe.gd", getattr(snapshot, "probe_sha256", ""))
    if not recorded.get("contract_version"):
        recorded["contract_version"] = CONTRACT_VERSION
    return recorded


def assert_identifiable(recorded: Mapping[str, object] | None) -> None:


    if not isinstance(recorded, Mapping):
        raise ValueError("truth snapshot has no harness identity")
    version = recorded.get("contract_version", "")
    if version not in ("", None) and str(version) != CONTRACT_VERSION:
        raise ValueError(
            "truth snapshot contract_version is not identifiable: "
            f"{version!r}"
        )
    present = recorded_faces(recorded)
    if not present:
        raise ValueError(
            "truth snapshot recorded no scoring-face SHA; there is "
            "nothing a third party can recover"
        )
    unknown = [
        f"{name}={digest}"
        for name, digest in present.items()
        if locate_committed(name, digest) is None
    ]
    if unknown:
        raise ValueError(
            "harness identity is not a committed revision: "
            + ", ".join(unknown)
            + ". Check the committed harness or its released-identities.json mapping."
        )


def assert_clean_for_measurement() -> None:

    root = repo_root()
    rels = [f"eval/harness/{name}" for name in FACES]
    run = subprocess.run(
        ["git", "status", "--porcelain", "--", *rels],
        cwd=root,
        capture_output=True,
        text=True,
        check=False,
    )
    if run.returncode != 0:
        raise ValueError(
            "cannot determine whether harness faces are committed: "
            + (run.stderr or run.stdout or f"git status rc={run.returncode}")
        )
    dirty = [line for line in run.stdout.splitlines() if line.strip()]
    if dirty:
        raise ValueError(
            "harness faces have uncommitted changes; official evidence "
            "cannot be produced from an uncommitted instrument: "
            + "; ".join(dirty)
            + ". Do NOT run `git checkout`/`git restore` on eval/harness/ to "
            "clear this: the edits are someone else's in-flight instrument "
            "work, discarding them is unrecoverable from here, and it "
            "silently invalidates every measurement other runs have in "
            "flight. Wait for the harness change to land, or ask the "
            "operator whose work it is."
        )
