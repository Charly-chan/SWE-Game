


from __future__ import annotations

import json
import os
import re
import shutil
import subprocess
import tempfile
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any, Literal, Mapping, Sequence

from .unity_interface import UnityInterfaceManifest
from .unity_environment import (
    UnityEnvironmentProfile,
    coerce_environment_profile,
)
from .unity_sandbox_process import (
    candidate_process_command,
    grant_candidate_access,
    run_candidate_process,
)


BuildStatus = Literal["pass", "fail", "inconclusive"]

_VERSION_RE = re.compile(r"\b(?:20\d{2}|6000)\.\d+\.\d+[abfp]\d+\b")
_INFRASTRUCTURE_PATTERNS = (
    "no valid unity editor license",
    "failed to activate/update license",
    "license is invalid",
    "licensing client timed out",
    "failed to connect to licensing client",
)
_EVALUATOR_BUILTIN_MODULES = {


    "com.unity.modules.audio": "1.0.0",
}


def _ensure_evaluator_builtin_modules(project: Path) -> None:
    manifest_path = project / "Packages" / "manifest.json"
    payload = json.loads(manifest_path.read_text(encoding="utf-8"))
    dependencies = payload.get("dependencies")
    if not isinstance(dependencies, dict):
        raise ValueError("Packages/manifest.json dependencies must be an object")
    changed = False
    for package_id, version in _EVALUATOR_BUILTIN_MODULES.items():
        if package_id not in dependencies:
            dependencies[package_id] = version
            changed = True
    if changed:
        payload["dependencies"] = dict(sorted(dependencies.items()))
        manifest_path.write_text(
            json.dumps(payload, indent=2) + "\n", encoding="utf-8"
        )


@dataclass(frozen=True)
class UnityBuildResult:
    status: BuildStatus
    detail: str
    unity_bin: str | None
    requested_version: str
    observed_version: str | None
    command: tuple[str, ...]
    returncode: int | None
    log_path: str | None
    executable: str | None
    probe_protocol: str | None = None
    attribution: str = "infrastructure"
    environment: Mapping[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, object]:
        return asdict(self)


def unity_available(explicit: str | Path | None = None) -> Path | None:

    requested = str(explicit or os.environ.get("UNITY_BIN") or "").strip()
    if requested:
        path = Path(requested).expanduser()
        if path.is_file() and os.access(path, os.X_OK):
            return path.resolve()
        found = shutil.which(requested)
        return Path(found).resolve() if found else None
    for name in ("unity-editor", "Unity"):
        found = shutil.which(name)
        if found:
            return Path(found).resolve()
    for path in (
        Path("/opt/Unity/Editor/Unity"),
        Path("/opt/unity/Editor/Unity"),
    ):
        if path.is_file() and os.access(path, os.X_OK):
            return path.resolve()
    return None


def requested_unity_version(project: Path) -> str:
    path = project / "ProjectSettings" / "ProjectVersion.txt"
    if not path.is_file():
        return ""
    for line in path.read_text(encoding="utf-8", errors="replace").splitlines():
        if line.strip().startswith("m_EditorVersion:"):
            return line.partition(":")[2].strip()
    return ""


def observed_unity_version(binary: Path, *, timeout: int = 30) -> str | None:
    try:
        process = run_candidate_process(
            candidate_process_command([str(binary), "-version"]), timeout=timeout
        )
    except (OSError, subprocess.TimeoutExpired):
        return None
    match = _VERSION_RE.search((process.stdout or "") + "\n" + (process.stderr or ""))
    return match.group(0) if match else None


def build_unity_submission(
    project: Path,
    manifest: UnityInterfaceManifest,
    out_dir: Path,
    *,
    unity_bin: str | Path | None = None,
    timeout: int = 900,
    environment_profile: UnityEnvironmentProfile | Mapping[str, Any] | None = None,
    trusted_fixture: bool = False,
) -> UnityBuildResult:

    project = project.resolve()
    out_dir = out_dir.resolve()
    out_dir.mkdir(parents=True, exist_ok=True)
    requested = requested_unity_version(project)
    profile = coerce_environment_profile(environment_profile)
    if not profile.ready_for_untrusted_execution and not (
        trusted_fixture and profile.environment_class == "local-linux-uncertified"
    ):
        return UnityBuildResult(
            "inconclusive",
            profile.detail or "certified disposable Unity VM profile is unavailable",
            None,
            requested,
            None,
            (),
            None,
            None,
            None,
            attribution="infrastructure",
            environment=profile.to_dict(),
        )
    binary = unity_available(unity_bin)
    if binary is None:
        return UnityBuildResult(
            "inconclusive",
            "Unity editor is unavailable; set UNITY_BIN to the required editor",
            None,
            requested,
            None,
            (),
            None,
            None,
            None,
        )

    observed = observed_unity_version(binary)
    if requested and observed and requested != observed:
        return UnityBuildResult(
            "inconclusive",
            f"evaluator Unity version {observed} does not match project version {requested}",
            str(binary),
            requested,
            observed,
            (str(binary), "-version"),
            None,
            None,
            None,
        )

    scratch = Path(tempfile.mkdtemp(prefix="unity-mode5-", dir=out_dir))
    workspace = scratch / "project"
    log_path = out_dir / "unity_build.log"
    player_dir = out_dir / "player"
    executable = player_dir / "game.x86_64"
    try:


        if player_dir.exists():
            shutil.rmtree(player_dir)
        if log_path.exists():
            log_path.unlink()
        shutil.copytree(
            project,
            workspace,
            ignore=shutil.ignore_patterns("Library", "Temp", "Logs", "obj", "Build", "Builds"),
        )
        _ensure_evaluator_builtin_modules(workspace)
        editor_dir = workspace / "Assets" / "Editor"
        editor_dir.mkdir(parents=True, exist_ok=True)
        probe_dir = workspace / "Assets" / "GameBenchmarkEvaluator"
        probe_dir.mkdir(parents=True, exist_ok=True)
        shutil.copy2(
            Path(__file__).with_name("unity_runtime_probe.cs"),
            probe_dir / "GameBenchmarkEvaluatorProbe.cs",
        )
        scene_paths = _build_scenes(manifest)
        (editor_dir / "GameBenchmarkEvaluatorBuild.cs").write_text(
            _render_builder(scene_paths, executable), encoding="utf-8"
        )


        grant_candidate_access((workspace, player_dir, log_path.parent))
        command = candidate_process_command((
            str(binary),
            "-batchmode",
            "-nographics",
            "-quit",
            "-projectPath",
            str(workspace),
            "-executeMethod",
            "GameBenchmarkEvaluatorBuild.BuildLinux",
            "-logFile",
            str(log_path),
        ))
        try:
            process = run_candidate_process(command, timeout=timeout)
        except subprocess.TimeoutExpired:
            return UnityBuildResult(
                "inconclusive",
                f"Unity batch build exceeded evaluator timeout ({timeout}s)",
                str(binary),
                requested,
                observed,
                command,
                None,
                str(log_path),
                None,
            )
        except OSError as exc:
            return UnityBuildResult(
                "inconclusive",
                f"Unity editor could not be executed: {exc}",
                str(binary),
                requested,
                observed,
                command,
                None,
                str(log_path),
                None,
            )

        combined = "\n".join(
            part for part in (process.stdout, process.stderr, _read_text(log_path)) if part
        )
        lowered = combined.lower()
        if any(pattern in lowered for pattern in _INFRASTRUCTURE_PATTERNS):
            return UnityBuildResult(
                "inconclusive",
                "Unity licensing infrastructure prevented the evaluator build",
                str(binary),
                requested,
                observed,
                command,
                process.returncode,
                str(log_path),
                None,
            )
        if process.returncode != 0:
            return UnityBuildResult(
                "fail",
                f"Unity batch build exited with code {process.returncode}",
                str(binary),
                requested,
                observed,
                command,
                process.returncode,
                str(log_path),
                None,
                attribution="submission",
            )
        if not executable.is_file():
            return UnityBuildResult(
                "fail",
                "Unity reported success but the fixed Linux player output is missing",
                str(binary),
                requested,
                observed,
                command,
                process.returncode,
                str(log_path),
                None,
                attribution="submission",
            )
        return UnityBuildResult(
            "pass",
            "evaluator-owned Unity Linux batch build completed",
            str(binary),
            requested,
            observed,
            command,
            process.returncode,
            str(log_path),
            str(executable),
            "gamebench.unity-controller.v1",
        )
    finally:
        shutil.rmtree(scratch, ignore_errors=True)


def _build_scenes(manifest: UnityInterfaceManifest) -> tuple[str, ...]:
    values = [item.scene for item in manifest.levels]
    if manifest.entry_scene:
        values.insert(0, manifest.entry_scene)
    values.extend(item.scene for item in manifest.endings)
    return tuple(dict.fromkeys(values))


def _csharp_string(value: str) -> str:
    return '"' + value.replace("\\", "\\\\").replace('"', '\\"') + '"'


def _render_builder(scenes: Sequence[str], executable: Path) -> str:
    scene_array = ",\n            ".join(_csharp_string(item) for item in scenes)
    output = _csharp_string(str(executable))
    return f"""using System;
using UnityEditor;
using UnityEditor.Build.Reporting;

public static class GameBenchmarkEvaluatorBuild
{{
    public static void BuildLinux()
    {{
        string[] scenes = new string[] {{
            {scene_array}
        }};
        var options = new BuildPlayerOptions
        {{
            scenes = scenes,
            locationPathName = {output},
            target = BuildTarget.StandaloneLinux64,
            options = BuildOptions.None
        }};
        BuildReport report = BuildPipeline.BuildPlayer(options);
        if (report.summary.result != BuildResult.Succeeded)
            throw new Exception("GameBenchmark Unity build failed: " + report.summary.result);
    }}
}}
"""


def _read_text(path: Path) -> str:
    if not path.is_file():
        return ""
    return path.read_text(encoding="utf-8", errors="replace")


__all__ = [
    "BuildStatus",
    "UnityBuildResult",
    "build_unity_submission",
    "observed_unity_version",
    "requested_unity_version",
    "unity_available",
]
