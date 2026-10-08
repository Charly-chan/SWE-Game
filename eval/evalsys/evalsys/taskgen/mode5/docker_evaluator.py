"""Independent, offline evaluator container for Mode 5 community submissions."""

from __future__ import annotations

import json
import os
import re
import shutil
import tempfile
import uuid
from pathlib import Path
from typing import Any, Mapping

from .community_profile import CommunityProfile, task_environment_lock
from .docker_environment import CommunityDockerError, DockerClient, assert_offline_networks
from .docker_license import (
    LicenseProvider, host_machine_identity_mount, license_log_error,
)
from .scoring import REGISTRY_VERSION
from .path_safety import reject_links


ARTIFACT_MANIFEST_SCHEMA = "gamebench.mode5-community-artifacts.v1"
DEVELOPMENT_RANKING_SCOPE = "mode5-community-docker-v1-development"


def write_artifact_manifest(root: Path) -> None:
    artifacts = [
        {"path": path.relative_to(root).as_posix(), "bytes": path.stat().st_size}
        for path in sorted(root.rglob("*")) if path.is_file() and path.name != "artifact-manifest.json"
    ]
    (root / "artifact-manifest.json").write_text(json.dumps({
        "schema": ARTIFACT_MANIFEST_SCHEMA,
        "producer": "community-docker-evaluator", "artifacts": artifacts,
    }, indent=2, sort_keys=True) + "\n", encoding="utf-8")


def relocate_artifact_paths(stage: Path, out: Path) -> None:
    """Keep persisted media references valid after atomic publication."""
    old, new = str(stage), str(out)

    def rewrite(value):
        if isinstance(value, str):
            return new + value[len(old):] if value == old or value.startswith(old + os.sep) else value
        if isinstance(value, list):
            return [rewrite(item) for item in value]
        if isinstance(value, dict):
            return {key: rewrite(item) for key, item in value.items()}
        return value

    for path in stage.rglob("*.json"):
        if path.name == "artifact-manifest.json":
            continue
        try:
            original = json.loads(path.read_text(encoding="utf-8"))
        except (ValueError, UnicodeError):
            continue
        relocated = rewrite(original)
        if relocated != original:
            path.write_text(json.dumps(relocated, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    for path in stage.rglob("*.md"):
        original = path.read_text(encoding="utf-8")
        relocated = original.replace(old + os.sep, new + os.sep)
        if relocated != original:
            path.write_text(relocated, encoding="utf-8")


def _public_build_diagnostics(log: str) -> str:
    """Keep compiler diagnostics, not raw Unity/licensing logs or identifiers."""
    lines = []
    for raw in log.splitlines():
        if not re.search(r"\berror (?:CS|BC)\d+\b", raw, re.IGNORECASE):
            continue
        line = re.sub(r"\b[^\s@]+@[^\s@]+\b", "[email]", raw.strip())
        line = re.sub(r"[A-Za-z0-9+/=_-]{40,}", "[redacted]", line)
        lines.append(line[:500])
        if len(lines) >= 100:
            break
    return "\n".join(lines) + ("\n" if lines else "")


def _runtime_profile(
    profile: CommunityProfile, *, image_id: str, provider: LicenseProvider,
) -> dict[str, Any]:
    return {
        **profile.public_dict(),
        "editor_version": profile.unity_editor,
        "image_digest": image_id,
        "preflight_passed": True,
        "license_mechanism": provider.kind,
        "score_eligible": True,
        "certified": False,
        "certification_status": "community-validated",
        "unity_modules": ["linux-il2cpp"],
    }


def _mark_development_unscored(report: dict[str, Any], stage: Path) -> None:
    """Keep smoke evidence while excluding it from all ranked aggregates."""
    scorecard = report.get("scorecard")
    if isinstance(scorecard, dict):
        scorecard["ranking_eligible"] = False
        scorecard["ranking_scope"] = DEVELOPMENT_RANKING_SCOPE
        scorecard["ranking_note"] = "diagnostic evaluation excluded from the release leaderboard"
        reading = scorecard.get("evidence_score")
        if isinstance(reading, dict):
            reading["ranking_eligible"] = False
        headline = scorecard.get("headline")
        if isinstance(headline, dict):
            headline["ranking_eligible"] = False
            headline["ranking_scope"] = DEVELOPMENT_RANKING_SCOPE
    headline = report.get("headline")
    if isinstance(headline, dict):
        headline["ranking_eligible"] = False
        headline["ranking_scope"] = DEVELOPMENT_RANKING_SCOPE
    markdown = stage / "report.md"
    if markdown.is_file():
        body = markdown.read_text(encoding="utf-8")
        body = body.replace("ranking_eligible=yes", "ranking_eligible=no (development smoke override)")
        markdown.write_text(
            "Development smoke: not eligible for the Community leaderboard.\n\n" + body,
            encoding="utf-8",
        )


def _package_hidden_runtime_ready(repo_root: Path, package: Path) -> bool:
    """Require the hidden runtime contract from the immutable release dataset."""
    from .release_data import load_released_suite, suite_ready
    try:
        manifest = json.loads((package / "manifest.json").read_text(encoding="utf-8"))
        game_id = str(manifest.get("game_id") or "")
        if not re.fullmatch(r"[a-z0-9_]+", game_id):
            return False
        suite = json.loads((package / "hidden/unity/behavior/suite.json").read_text(encoding="utf-8"))
        gate = json.loads((package / "hidden/unity/behavior/calibration_status.json").read_text(encoding="utf-8"))
        if not isinstance(suite, dict) or not isinstance(gate, dict):
            return False
        canonical, calibration = load_released_suite(repo_root, game_id)
        return (suite_ready(suite, gate, game_id)
                and suite == canonical and gate == calibration)
    except (OSError, ValueError, KeyError, RuntimeError, AttributeError):
        return False



def _validate_task_environment_lock(package: Path, profile: CommunityProfile) -> None:
    lock_path = package / "visible" / "environment.lock.json"
    manifest_path = package / "manifest.json"
    try:
        lock_bytes = lock_path.read_bytes()
        lock = json.loads(lock_bytes)
        manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise CommunityDockerError("Mode 5 task environment lock or package manifest is missing") from exc
    declared = manifest.get("environment_lock") if isinstance(manifest, Mapping) else None
    if not isinstance(declared, Mapping) or declared.get("path") != "visible/environment.lock.json":
        raise CommunityDockerError("package manifest does not pin the task environment lock")
    expected_lock = task_environment_lock(profile)
    if any(lock.get(key) != expected_lock.get(key) for key in (
        "schema", "profile_family", "unity_version", "build_target", "scripting_backend",
    )):
        raise CommunityDockerError(
            "task environment lock does not match active Community profile; regenerate the task package"
        )


def verify_artifact_manifest(root: Path) -> dict[str, Any]:
    """Verify that a published evaluation is complete and has not drifted.

    The manifest intentionally does not list itself.  Every other regular file
    must be listed exactly once with the same byte length;
    missing or truncated recorded files are rejected; extra user notes are fine.
    """
    manifest_path = root / "artifact-manifest.json"
    try:
        manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise CommunityDockerError("artifact manifest is missing or invalid") from exc
    if manifest.get("schema") != ARTIFACT_MANIFEST_SCHEMA:
        raise CommunityDockerError("artifact manifest schema mismatch")
    rows = manifest.get("artifacts")
    if not isinstance(rows, list):
        raise CommunityDockerError("artifact manifest artifacts must be a list")
    expected: dict[str, Mapping[str, Any]] = {}
    for raw in rows:
        if not isinstance(raw, Mapping):
            raise CommunityDockerError("artifact manifest row must be an object")
        relative = str(raw.get("path") or "")
        candidate = (root / relative).resolve()
        try:
            candidate.relative_to(root.resolve())
        except ValueError as exc:
            raise CommunityDockerError("artifact manifest path escapes output") from exc
        if not relative or relative in expected or relative == "artifact-manifest.json":
            raise CommunityDockerError("artifact manifest contains an invalid path")
        expected[relative] = raw
    actual = {
        path.relative_to(root).as_posix(): path
        for path in root.rglob("*")
        if path.is_file() and path.name != "artifact-manifest.json"
    }
    if not set(expected).issubset(actual):
        raise CommunityDockerError("artifact manifest required file is missing")
    for relative, row in expected.items():
        path = actual[relative]
        if path.stat().st_size != int(row.get("bytes", -1)):
            raise CommunityDockerError(f"artifact size mismatch: {relative}")
    return manifest


def evaluate_in_container(
    *, repo_root: Path, package: Path, submission: Path, out: Path,
    profile: CommunityProfile, image_lock: Mapping[str, Any],
    preflight: Mapping[str, Any], license_provider: LicenseProvider,
    docker: DockerClient, run_config: Mapping[str, Any] | None = None,
    fidelity_judge: str = "none",
) -> dict[str, Any]:
    """Rebuild and score a copied submission; atomically publish verified output."""
    if fidelity_judge != "none":
        raise CommunityDockerError("Mode 5 uses the fixed non-VLM evidence proxy")
    if preflight.get("status") != "pass":
        raise CommunityDockerError("Mode 5 doctor has not passed")
    image = dict(image_lock.get("evaluator_image") or {})
    image_id = str(image.get("id") or "")
    if not image_id or docker.image_id(str(image.get("tag") or "")) != image_id:
        raise CommunityDockerError("evaluator image does not match image-lock")
    if dict(preflight.get("image_ids") or {}).get("evaluator") != image_id:
        raise CommunityDockerError("doctor result does not match evaluator image")
    if out.exists():
        raise CommunityDockerError(f"evaluation output already exists: {out}")
    for path, label in ((package, "package"), (submission, "submission")):
        if not path.is_dir():
            raise CommunityDockerError(f"{label} directory is missing: {path}")
        try:
            reject_links(path, label=label)
        except ValueError as exc:
            raise CommunityDockerError(str(exc)) from exc
    _validate_task_environment_lock(package, profile)
    hidden_runtime_ready = _package_hidden_runtime_ready(repo_root, package)

    name = f"gb-mode5-eval-{uuid.uuid4().hex[:10]}"
    limits = profile.limits
    prefix = [
        "runuser", "-u", "unity-runner", "--", "env", "-i",
        "HOME=/home/unity-runner",
        "PATH=/usr/local/sbin:/usr/local/bin:/usr/sbin:/usr/bin:/sbin:/bin",
        "LANG=C.UTF-8", "TZ=UTC",
        "UNITY_DISABLE_LINUX_TOOLCHAIN_MIGRATOR=1",
        "UNITY_DISABLE_LINUX_AUTO_TOOLCHAIN_INSTALLATION=1",
    ]
    if license_provider.kind == "floating" and license_provider.endpoint:
        prefix.append(f"UNITY_LICENSE_SERVER={license_provider.endpoint}")
    profile_wire = _runtime_profile(profile, image_id=image_id, provider=license_provider)
    out.parent.mkdir(parents=True, exist_ok=True)
    stage = Path(tempfile.mkdtemp(prefix="gamebench-mode5-eval-stage-", dir=str(out.parent)))
    created = False
    try:
        network_mode = "bridge" if license_provider.kind == "floating" else "none"
        docker.run([
            "create", "--name", name, "--network", network_mode,
            "--platform", "linux/amd64",
            *host_machine_identity_mount(license_provider.kind),
            "--cpus", str(limits["cpus"]), "--memory", f"{limits['memory_mb']}m",
            "--pids-limit", "2048", "--read-only", "--cap-drop", "ALL",
            "--cap-add", "CHOWN", "--cap-add", "DAC_OVERRIDE", "--cap-add", "FOWNER",
            "--cap-add", "SETGID", "--cap-add", "SETUID", "--cap-add", "KILL",
            "--security-opt", "no-new-privileges:true",
            "--tmpfs", "/controller:rw,nosuid,nodev,noexec,size=2g",
            "--tmpfs", "/candidate:rw,nosuid,nodev,size=8g",
            "--tmpfs", "/output:rw,nosuid,nodev,noexec,size=4g",
            # Docker tmpfs defaults to noexec even when omitted. The freshly
            # built Linux Player must execute from this isolated scratch.
            "--tmpfs", "/runtime:rw,nosuid,nodev,exec,size=4g",
            "--tmpfs", "/home/unity-runner:rw,nosuid,nodev,size=2g",
            "--tmpfs", "/home/gb-controller:rw,nosuid,nodev,noexec,size=256m",
            "--tmpfs", "/tmp:rw,nosuid,nodev,size=2g",
            image_id, "sh", "-c", "while :; do sleep 30; done",
        ], timeout=120)
        created = True
        docker.run(["start", name], timeout=120)
        bootstrap = (
            "mkdir -p /controller/package /controller/evalsys /controller/interface "
            "/candidate/submission /output /runtime && "
            "chown -R gb-controller:gb-controller /controller /output && "
            "chown -R unity-runner:unity-runner /candidate /runtime /home/unity-runner"
        )
        docker.run(["exec", "--user", "0:0", name, "sh", "-c", bootstrap], timeout=120)
        if license_provider.kind == "file" and license_provider.source:
            docker.copy_into_directory(
                license_provider.source, name, "/home/unity-runner", timeout=60,
            )
            docker.run(["exec", "--user", "0:0", name, "mv",
                        f"/home/unity-runner/{license_provider.source.name}",
                        "/home/unity-runner/license.ulf"], timeout=60)
        elif license_provider.kind == "existing-home" and license_provider.source:
            with tempfile.TemporaryDirectory(prefix="gb-unity-home-") as license_temp:
                staged_home = Path(license_temp)
                copied = False
                for relative in (Path(".local/share/unity3d"), Path(".config/unity3d")):
                    candidate = license_provider.source / relative
                    if candidate.is_dir():
                        shutil.copytree(candidate, staged_home / relative)
                        copied = True
                if not copied:
                    raise CommunityDockerError(
                        "dedicated Unity home contains no Unity license state"
                    )
                docker.copy_into_directory(
                    staged_home, name, "/home/unity-runner", timeout=300,
                )
        docker.run(["exec", "--user", "0:0", name, "sh", "-c",
                    "chown -R unity-runner:unity-runner /home/unity-runner && "
                    "chmod -R go-rwx /home/unity-runner"], timeout=180)

        # A read-only rootfs leaves /workspace unwritable. Unity interprets its
        # current directory as a project when no project path is supplied and
        # exits before licensing can be assessed. Probe on an empty tmpfs
        # project while the container still contains no benchmark inputs.
        docker.run([
            "exec", "--user", "unity-runner:unity-runner", name,
            "mkdir", "-p", "/runtime/license-probe/Assets",
            "/runtime/license-probe/Packages",
            "/runtime/license-probe/ProjectSettings",
            "/home/unity-runner/.local/share/unity3d",
            "/home/unity-runner/.cache/unity3d",
        ], timeout=60)
        license_cmd = [
            *prefix, "unity", "-batchmode", "-nographics", "-quit",
            "-projectPath", "/runtime/license-probe",
        ]
        if license_provider.kind == "file":
            license_cmd += ["-manualLicenseFile", "/home/unity-runner/license.ulf"]
        license_cmd += ["-logFile", "-"]
        probe = docker.run(["exec", "--user", "0:0", name, *license_cmd],
                           timeout=300, check=False)
        combined = (probe.stdout or "") + "\n" + (probe.stderr or "")
        license_error = license_log_error(combined)
        if probe.returncode or license_error:
            detail = f" (exit {probe.returncode}"
            if license_error:
                detail += f"; {license_error}"
            for code, marker in (
                ("project_read_only", "Project folder or disk is read only"),
                ("preferences_unwritable", "Preferences won't be saved"),
                ("directory_unwritable", "CreateDirectory '"),
                ("unity_abort", "Aborting batchmode"),
                ("unity_crash", "Segmentation fault"),
            ):
                if marker.lower() in combined.lower():
                    detail += f"; {code}"
            raise CommunityDockerError("evaluator Unity license initialization failed" + detail + ")")
        docker.run(["exec", "--user", "0:0", name, "rm", "-rf",
                    "/runtime/license-probe"], timeout=120)
        if license_provider.kind == "file":
            docker.run(["exec", "--user", "0:0", name, "rm", "-f",
                        "/home/unity-runner/license.ulf"], timeout=60)

        # Floating licensing gets a narrow bootstrap window.  No benchmark
        # code, hidden package, or candidate bytes enter the container until
        # Docker confirms that all networks have been detached.
        if license_provider.kind == "floating":
            docker.run(["network", "disconnect", "bridge", name], timeout=60)
        networks = docker.run([
            "inspect", "--format", "{{json .NetworkSettings.Networks}}", name,
        ], timeout=60).stdout.strip()
        assert_offline_networks(networks)

        docker.copy_into_directory(
            repo_root / "eval" / "evalsys", name, "/controller/evalsys", timeout=600,
        )
        docker.copy_into_directory(
            repo_root / "eval" / "interface", name, "/controller/interface", timeout=120,
        )
        docker.copy_into_directory(package, name, "/controller/package", timeout=600)
        docker.copy_into_directory(
            submission, name, "/candidate/submission", timeout=600,
        )
        local_profile = stage / "environment-profile.json"
        local_profile.write_text(json.dumps(profile_wire, indent=2, sort_keys=True) + "\n",
                                 encoding="utf-8")
        docker.copy_into_directory(local_profile, name, "/controller", timeout=60)
        docker.run(["exec", "--user", "0:0", name, "sh", "-c",
                    "chown -R gb-controller:gb-controller /controller /output && "
                    "chmod -R go-rwx /controller && "
                    "chown -R unity-runner:unity-runner /candidate /runtime /home/unity-runner && "
                    "chmod -R go-rwx /candidate /runtime /home/unity-runner"], timeout=180)

        env = [
            "env", "-i", "HOME=/home/gb-controller",
            "PATH=/opt/venv/bin:/usr/local/sbin:/usr/local/bin:/usr/sbin:/usr/bin:/sbin:/bin",
            "LANG=C.UTF-8", "TZ=UTC", "PYTHONPATH=/controller/evalsys",
            "UNITY_BIN=/opt/unity/6000.3.23f1/Editor/Unity",
            "GB_UNITY_ENVIRONMENT_PROFILE=/controller/environment-profile.json",
            "GB_MODE5_OFFLINE_EVALUATOR=1",
            "GB_UNITY_RUNTIME_ROOT=/runtime/work", "GB_UNITY_SKIP_LEGACY_ROUTES=1",
            "GB_UNITY_SKIP_VISUAL_CAPTURE=0",
            "GB_UNITY_CANDIDATE_UID=1100", "GB_UNITY_CANDIDATE_GID=1100",
            "GB_UNITY_SCRIPTING_BACKEND=" + profile.scripting_backend,
            "GB_UNITY_CANDIDATE_PROCESS_PREFIX_JSON=" + json.dumps(prefix, separators=(",", ":")),
        ]
        command = [
            *env, "python3", "/controller/evalsys/bin/bench", "eval-task",
            "--package", "/controller/package", "--submission", "/candidate/submission",
            "--out", "/output/evaluation", "--engine", "on",
            "--visual-judge", "none", "--registry", REGISTRY_VERSION,
        ]
        process = docker.run(["exec", "--user", "0:0", name, *command],
                             timeout=int(limits["wall_seconds"]), check=False)
        build_log = docker.run([
            "exec", "--user", "0:0", name, "tail", "-c", "1048576",
            "/runtime/work/unity_build.log",
        ], timeout=60, check=False)
        report_probe = docker.run(["exec", name, "test", "-s", "/output/evaluation/report.json"],
                                  timeout=60, check=False)
        if report_probe.returncode:
            failure = out.with_name(out.name + "-failure")
            failure.mkdir(parents=True, exist_ok=True)
            (failure / "evaluator-process.stdout.txt").write_text(
                process.stdout or "", encoding="utf-8",
            )
            (failure / "evaluator-process.stderr.txt").write_text(
                process.stderr or "", encoding="utf-8",
            )
            if build_log.returncode == 0:
                (failure / "unity_build.log").write_text(
                    build_log.stdout or "", encoding="utf-8",
                )
            detail = (
                f"exit={process.returncode}; "
                f"stdout={(process.stdout or '')[-700:]}; "
                f"stderr={(process.stderr or '')[-700:]}"
            ).strip()
            if not detail or detail.endswith("stdout=; stderr="):
                detail = "evaluator wrote no report; diagnostics saved under " + str(failure)
            raise CommunityDockerError(f"evaluator infrastructure failed: {detail}")
        docker.copy_from_directory(name, "/output/evaluation", stage, timeout=600)
        if build_log.returncode == 0:
            diagnostics = _public_build_diagnostics(build_log.stdout or "")
            if diagnostics:
                (stage / "unity-build-errors.txt").write_text(diagnostics, encoding="utf-8")

        report_path = stage / "report.json"
        report = json.loads(report_path.read_text(encoding="utf-8"))
        if (report.get("scorecard") or {}).get("registry_version") != REGISTRY_VERSION:
            raise CommunityDockerError("evaluator report used the wrong score registry")
        if (report.get("engine") or {}).get("runtime"):
            docker.copy_from_directory(name, "/runtime/work/runs", stage / "runtime-evidence", timeout=600)
        # Candidate execution has ended; publish the controller snapshot.
        from .fidelity import complete_fidelity
        report = complete_fidelity(
            report, package, submission, stage / "runtime-evidence", stage / "fidelity",
            judge=fidelity_judge,
        )
        report["environment"] = {
            "environment_class": profile.environment_class,
            "paper_compatible": False,
            "profile_id": profile.profile_id,
            "unity_version": profile.unity_editor,
            "unity_changeset": profile.unity_changeset,
            "build_target": profile.build_target,
            "scripting_backend": profile.scripting_backend,
            "agent_image_id": str((image_lock.get("agent_image") or {}).get("id") or ""),
            "evaluator_image_id": image_id,
            "network_policy": profile.network_policy,
            "scoring_protocol": REGISTRY_VERSION,
            "preflight_checked_at": preflight.get("checked_at"),
        }
        report["run_config"] = dict(run_config or {
            "harness": "external-submission",
            "model": "unknown",
            "provider": "unknown",
            "budget": {"agent_timeout_seconds": None},
            "input": {"source": "external-submission"},
        })
        report["task_package_identity"] = {
            key: json.loads((package / "manifest.json").read_text(encoding="utf-8")).get(key)
            for key in ("schema_version", "mode", "game_id")
        }
        if not hidden_runtime_ready:
            report["run_config"]["development_unscored"] = True
            report["run_config"]["development_reason"] = "hidden_suite_not_release_ready"
        if report["run_config"].get("development_unscored") is True:
            _mark_development_unscored(report, stage)
        report_path.write_text(json.dumps(report, indent=2, sort_keys=True) + "\n",
                               encoding="utf-8")
        from ..evaluate import render_report
        from .report_items import item_from_report as _item
        from ..evaluate import TaskEvalResult
        from ..package import TaskPackage
        from ..submission import Submission
        rendered = TaskEvalResult(
            TaskPackage.read(package), Submission(root=submission, project=submission, engine="unity"),
            items=[_item(row) for row in report["items"]], engine=report.get("engine") or {},
            registry_version=REGISTRY_VERSION,
        )
        (stage / "report.md").write_text(render_report(rendered), encoding="utf-8")
        if report["run_config"].get("development_unscored") is True:
            _mark_development_unscored(report, stage)
        relocate_artifact_paths(stage, out)
        report = json.loads(report_path.read_text(encoding="utf-8"))
        write_artifact_manifest(stage)
        verify_artifact_manifest(stage)
        stage.replace(out)
        return report
    finally:
        if created:
            docker.run(["rm", "-f", name], timeout=120, check=False)
        if stage.exists():
            shutil.rmtree(stage, ignore_errors=True)
