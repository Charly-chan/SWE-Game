


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


def enable_community_engine_modules(project: str | Path) -> None:
    """Enable pinned, offline built-in engine modules in a Community copy.

    Unity 6000.3.23f1 ships these packages in BuiltInPackages, so ordinary UI, Tilemap, audio,
    animation, particle, navigation and terrain code compiles without registry
    access. Both package files enter the hidden immutable scaffold digest.
    The Community copy also seeds the missing SDK script .meta, otherwise the
    Agent's first Unity import generates an unexpected file in the immutable
    SDK directory and the evaluator rejects an otherwise unchanged scaffold.
    """

    packages = Path(project) / "Packages"
    manifest_path = packages / "manifest.json"
    lock_path = packages / "packages-lock.json"
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    lock = json.loads(lock_path.read_text(encoding="utf-8"))
    dependencies = manifest.get("dependencies")
    locked = lock.get("dependencies")
    if not isinstance(dependencies, dict) or not isinstance(locked, dict):
        raise ValueError("Unity scaffold package metadata is malformed")
    if "com.unity.modules.ui" not in locked or "com.unity.modules.imgui" not in locked:
        raise ValueError("Unity scaffold lacks built-in UI prerequisites")
    for name, required in COMMUNITY_BUILTIN_DEPENDENCIES.items():
        version = "2.0.0" if name == "com.unity.ugui" else "1.0.0"
        dependencies[name] = version
        locked[name] = {
            "version": version,
            "depth": 0,
            "source": "builtin",
            "dependencies": dict(required),
        }
    locked["com.unity.modules.ui"]["depth"] = 1
    manifest["dependencies"] = dict(sorted(dependencies.items()))
    lock["dependencies"] = dict(sorted(locked.items()))
    manifest_path.write_text(json.dumps(manifest, indent=2) + "\n", encoding="utf-8")
    lock_path.write_text(json.dumps(lock, indent=2) + "\n", encoding="utf-8")
    sdk_root = Path(project) / "Assets" / "GameBenchmarkSDK"
    if not (sdk_root / "GBObservableState.cs").is_file():
        raise FileNotFoundError("Community scaffold is missing GBObservableState.cs")
    (sdk_root / "GBObservableState.cs.meta").write_text(
        _COMMUNITY_OBSERVABLE_META, encoding="utf-8",
    )


_COMMUNITY_OBSERVABLE_META = """fileFormatVersion: 2
guid: 9ed673e25c767f688b51aa07532277cb
MonoImporter:
  externalObjects: {}
  serializedVersion: 2
  defaultReferences: []
  executionOrder: 0
  icon: {instanceID: 0}
  userData:
  assetBundleName:
  assetBundleVariant:
"""


COMMUNITY_BUILTIN_DEPENDENCIES: Mapping[str, Mapping[str, str]] = {
    "com.unity.ugui": {
        "com.unity.modules.ui": "1.0.0",
        "com.unity.modules.imgui": "1.0.0",
    },
    "com.unity.modules.ai": {},
    "com.unity.modules.animation": {},
    "com.unity.modules.audio": {},
    "com.unity.modules.director": {
        "com.unity.modules.audio": "1.0.0",
        "com.unity.modules.animation": "1.0.0",
    },
    "com.unity.modules.particlesystem": {},
    "com.unity.modules.screencapture": {"com.unity.modules.imageconversion": "1.0.0"},
    "com.unity.modules.terrain": {},
    "com.unity.modules.terrainphysics": {
        "com.unity.modules.physics": "1.0.0",
        "com.unity.modules.terrain": "1.0.0",
    },
    "com.unity.modules.tilemap": {"com.unity.modules.physics2d": "1.0.0"},
    "com.unity.modules.unitywebrequest": {},
    "com.unity.modules.video": {
        "com.unity.modules.audio": "1.0.0",
        "com.unity.modules.ui": "1.0.0",
        "com.unity.modules.unitywebrequest": "1.0.0",
    },
}


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
    *, reference_project: str | Path | None = None,
) -> ScaffoldIntegrityReport:
    root = Path(project).resolve()
    normalized = {_safe_relative(path): str(digest) for path, digest in expected.items()}
    missing: list[str] = []
    changed: list[str] = []
    for relative, digest in sorted(normalized.items()):
        path = root / Path(relative)
        if reference_project is not None and path.suffix == ".meta":
            continue  # Unity owns importer metadata, not benchmark gameplay.
        if not path.is_file():
            missing.append(relative)
        elif reference_project is not None:
            reference = Path(reference_project) / relative
            if relative.endswith("packages-lock.json"):
                continue  # generated resolution metadata; manifest is checked
            if path.suffix in {".json", ".asmdef", ".inputactions"}:
                if json.loads(path.read_text(encoding="utf-8")) != json.loads(reference.read_text(encoding="utf-8")):
                    changed.append(relative)
            elif relative.endswith("ProjectVersion.txt"):
                wanted = next(line for line in reference.read_text().splitlines() if line.startswith("m_EditorVersion:"))
                if wanted not in path.read_text().splitlines():
                    changed.append(relative)
            elif path.suffix == ".cs":
                if path.read_text(encoding="utf-8").strip() != reference.read_text(encoding="utf-8").strip():
                    changed.append(relative)
            elif sha256_file(path) != digest:
                changed.append(relative)
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
        tuple(sorted(path for path in actual_sdk - expected_sdk
                     if reference_project is None or not path.endswith(".meta"))),
    )


__all__ = [
    "FIXED_INPUT_ACTIONS",
    "LOCKED_PROJECT_FILES",
    "SDK_PREFIX",
    "TARGET_UNITY_ROOT",
    "ScaffoldIntegrityReport",
    "build_scaffold_digest_manifest",
    "COMMUNITY_BUILTIN_DEPENDENCIES",
    "enable_community_engine_modules",
    "immutable_scaffold_paths",
    "scaffold_manifest_digest",
    "sha256_file",
    "validate_scaffold_integrity",
]
