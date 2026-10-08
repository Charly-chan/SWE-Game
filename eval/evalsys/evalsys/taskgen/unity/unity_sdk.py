


from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass
from pathlib import Path, PurePosixPath
from typing import Any, Mapping, Sequence


SDK_PREFIX = "Assets/GameBenchmarkSDK/"
FIXED_INPUT_ACTIONS = SDK_PREFIX + "GameBenchmarkInput.inputactions"
TARGET_UNITY_ROOT = Path(__file__).with_name("target_unity")
LOCKED_PROJECT_FILES = (
    "Packages/manifest.json",
    "Packages/packages-lock.json",
    "Packages/com.unity.inputsystem-1.14.2.tgz",
    "ProjectSettings/ProjectVersion.txt",
)


def immutable_scaffold_paths(project: str | Path) -> tuple[str, ...]:

    root = Path(project).resolve()
    sdk_root = root / "Assets" / "GameBenchmarkSDK"
    if not sdk_root.is_dir():
        raise FileNotFoundError("target scaffold SDK directory is missing")
    sdk_files = tuple(
        path.relative_to(root).as_posix()
        for path in sorted(sdk_root.rglob("*"))
        if path.is_file()
    )
    return tuple(sorted((*sdk_files, *LOCKED_PROJECT_FILES)))


@dataclass(frozen=True)
class ScaffoldIntegrityReport:
    missing: tuple[str, ...] = ()
    changed: tuple[str, ...] = ()
    unexpected_sdk_files: tuple[str, ...] = ()

    @property
    def ok(self) -> bool:
        return not (self.missing or self.changed or self.unexpected_sdk_files)

    def to_dict(self) -> dict[str, Any]:
        return {
            "ok": self.ok,
            "missing": list(self.missing),
            "changed": list(self.changed),
            "unexpected_sdk_files": list(self.unexpected_sdk_files),
        }


def _safe_relative(value: str) -> str:
    normalized = value.replace("\\", "/")
    path = PurePosixPath(normalized)
    if path.is_absolute() or ".." in path.parts or not path.parts:
        raise ValueError(f"unsafe scaffold path: {value!r}")
    return path.as_posix()


def sha256_file(path: str | Path) -> str:
    digest = hashlib.sha256()
    with Path(path).open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    return "sha256:" + digest.hexdigest()


def build_scaffold_digest_manifest(
    project: str | Path,
    immutable_paths: Sequence[str],
) -> dict[str, str]:
    root = Path(project).resolve()
    manifest: dict[str, str] = {}
    for raw in sorted(set(immutable_paths)):
        relative = _safe_relative(raw)
        path = root / Path(relative)
        if not path.is_file():
            raise FileNotFoundError(f"immutable scaffold file is missing: {relative}")
        manifest[relative] = sha256_file(path)
    return manifest


def scaffold_manifest_digest(manifest: Mapping[str, str]) -> str:
    payload = json.dumps(dict(sorted(manifest.items())), separators=(",", ":"), ensure_ascii=True)
    return "sha256:" + hashlib.sha256(payload.encode("utf-8")).hexdigest()


def validate_scaffold_integrity(
    project: str | Path,
    expected: Mapping[str, str],
) -> ScaffoldIntegrityReport:
    root = Path(project).resolve()
    normalized = {_safe_relative(path): str(digest) for path, digest in expected.items()}
    missing: list[str] = []
    changed: list[str] = []
    for relative, digest in sorted(normalized.items()):
        path = root / Path(relative)
        if not path.is_file():
            missing.append(relative)
        elif sha256_file(path) != digest:
            changed.append(relative)

    sdk_root = root / "Assets" / "GameBenchmarkSDK"
    actual_sdk = {
        path.relative_to(root).as_posix()
        for path in sdk_root.rglob("*")
        if path.is_file()
    } if sdk_root.is_dir() else set()
    expected_sdk = {path for path in normalized if path.startswith(SDK_PREFIX)}
    return ScaffoldIntegrityReport(
        tuple(missing),
        tuple(changed),
        tuple(sorted(actual_sdk - expected_sdk)),
    )


__all__ = [
    "FIXED_INPUT_ACTIONS",
    "LOCKED_PROJECT_FILES",
    "SDK_PREFIX",
    "TARGET_UNITY_ROOT",
    "ScaffoldIntegrityReport",
    "build_scaffold_digest_manifest",
    "immutable_scaffold_paths",
    "scaffold_manifest_digest",
    "sha256_file",
    "validate_scaffold_integrity",
]
