"""Docker build, image-lock and offline preflight for Mode 5 community runs."""

from __future__ import annotations

import hashlib
import json
import os
import re
import shutil
import subprocess
import tarfile
import tempfile
import time
import urllib.error
import urllib.request
import uuid
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable, Mapping, Sequence

from .community_profile import CommunityProfile
from .docker_license import (
    LicenseProvider, host_machine_identity_mount, license_log_error,
)
from ..unity.unity_sdk import (
    TARGET_UNITY_ROOT, enable_community_engine_modules, immutable_scaffold_paths,
)


IMAGE_LOCK_SCHEMA = "gamebench.mode5-image-lock.v1"
PREFLIGHT_SCHEMA = "gamebench.mode5-preflight.v1"
AGENT_TAG = "gamebench-mode5-agent:6000.3.23f1-v1"
EVALUATOR_TAG = "gamebench-mode5-evaluator:6000.3.23f1-v1"


class CommunityDockerError(RuntimeError):
    pass


def assert_offline_networks(raw: str) -> None:
    """Accept Docker's explicit `none` network placeholder, reject connectivity."""
    try:
        networks = json.loads(raw or "{}")
    except json.JSONDecodeError as exc:
        raise CommunityDockerError("could not inspect container networks") from exc
    if not isinstance(networks, Mapping):
        raise CommunityDockerError("container network inspection was not an object")
    if not networks:
        return
    if set(networks) != {"none"}:
        raise CommunityDockerError(f"container still has an attached network: {raw}")
    none = networks.get("none")
    if not isinstance(none, Mapping) or any(
        none.get(key) for key in ("Gateway", "IPAddress", "IPv6Gateway", "GlobalIPv6Address")
    ):
        raise CommunityDockerError(f"container none network unexpectedly has connectivity: {raw}")


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(4 * 1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def load_install_manifest(path: Path) -> dict[str, Any]:
    raw = json.loads(path.read_text(encoding="utf-8"))
    if raw.get("schema") != "gamebench.mode5-unity-install.v1":
        raise CommunityDockerError("unsupported Unity install manifest")
    return raw


def verify_archives(directory: Path, manifest: Mapping[str, Any]) -> dict[str, Any]:
    result: dict[str, Any] = {}
    for name, spec in dict(manifest.get("archives") or {}).items():
        path = directory / str(spec["file"])
        # Removable/virtual Windows volumes can briefly disappear while the
        # device is remounted. Treat that as source availability, not a corrupt
        # Unity bundle, and retry before emitting a stable failure.
        for attempt in range(5):
            try:
                available = path.is_file()
            except OSError:
                available = False
            if available:
                break
            if attempt < 4:
                time.sleep(2)
        if not available:
            raise CommunityDockerError(f"missing required Unity archive: {path.name}")
        for attempt in range(5):
            try:
                size = path.stat().st_size
                break
            except OSError as exc:
                if attempt == 4:
                    raise CommunityDockerError(
                        f"archive_source_unavailable: {path.name}"
                    ) from exc
                time.sleep(2)
        if size != int(spec["bytes"]):
            raise CommunityDockerError(
                f"Unity archive size mismatch for {path.name}: expected {spec['bytes']}, got {size}"
            )
        digest = sha256_file(path)
        if digest.lower() != str(spec["sha256"]).lower():
            raise CommunityDockerError(f"Unity archive SHA256 mismatch for {path.name}")
        result[name] = {"file": path.name, "bytes": size, "sha256": digest}
    return result


def _download_archive(url: str, target: Path, *, attempts: int = 5) -> None:
    """Download to ``.part`` with HTTP range resume and atomic publication."""
    target.parent.mkdir(parents=True, exist_ok=True)
    partial = target.with_suffix(target.suffix + ".part")
    last_error: Exception | None = None
    for attempt in range(attempts):
        offset = partial.stat().st_size if partial.is_file() else 0
        request = urllib.request.Request(
            url, headers={
                "User-Agent": "GameBenchmark-Mode5/1",
                **({"Range": f"bytes={offset}-"} if offset else {}),
            },
        )
        try:
            with urllib.request.urlopen(request, timeout=60) as response:
                resumed = offset > 0 and getattr(response, "status", None) == 206
                mode = "ab" if resumed else "wb"
                with partial.open(mode) as handle:
                    while True:
                        chunk = response.read(4 * 1024 * 1024)
                        if not chunk:
                            break
                        handle.write(chunk)
            partial.replace(target)
            return
        except (OSError, urllib.error.URLError) as exc:
            last_error = exc
            if attempt + 1 < attempts:
                time.sleep(min(30, 2 ** attempt))
    raise CommunityDockerError(
        f"could not download official Unity archive {target.name}; "
        "provide --unity-archives with the two verified files"
    ) from last_error


def ensure_archives(
    directory: Path, manifest: Mapping[str, Any], *, allow_download: bool = True,
) -> dict[str, Any]:
    """Locate verified archives, downloading missing official files if allowed."""
    directory.mkdir(parents=True, exist_ok=True)
    for spec in dict(manifest.get("archives") or {}).values():
        target = directory / str(spec["file"])
        if target.is_file():
            continue
        if not allow_download:
            raise CommunityDockerError(f"missing required Unity archive: {target.name}")
        url = str(spec.get("url") or "")
        if not url.startswith("https://download.unity3d.com/"):
            raise CommunityDockerError(f"official download URL is missing for {target.name}")
        _download_archive(url, target)
    return verify_archives(directory, manifest)


@dataclass
class DockerClient:
    executable: str = "docker"
    run_command: Callable[..., subprocess.CompletedProcess[str]] = subprocess.run

    def run(self, args: Sequence[str], *, timeout: int = 600, check: bool = True) -> subprocess.CompletedProcess[str]:
        child_env = os.environ.copy()
        executable_parent = str(Path(self.executable).expanduser().parent)
        if executable_parent not in {"", "."}:
            child_env["PATH"] = executable_parent + os.pathsep + child_env.get("PATH", "")
        try:
            proc = self.run_command(
                [self.executable, *args], capture_output=True, text=True,
                encoding="utf-8", errors="replace",
                timeout=timeout, check=False, env=child_env,
            )
        except (OSError, subprocess.TimeoutExpired) as exc:
            raise CommunityDockerError(f"Docker command could not complete: {args[0]}") from exc
        if check and proc.returncode:
            detail = (proc.stderr or proc.stdout or "no output").strip().splitlines()[-1]
            raise CommunityDockerError(f"docker {args[0]} failed: {detail}")
        return proc

    def image_id(self, image: str) -> str:
        return self.run(["image", "inspect", "--format", "{{.Id}}", image], timeout=60).stdout.strip()

    def _child_env(self) -> dict[str, str]:
        child_env = os.environ.copy()
        executable_parent = str(Path(self.executable).expanduser().parent)
        if executable_parent not in {"", "."}:
            child_env["PATH"] = executable_parent + os.pathsep + child_env.get("PATH", "")
        return child_env

    def copy_into_directory(
        self, source: Path, container: str, destination: str, *, timeout: int = 600,
    ) -> None:
        """Copy through ``docker exec`` so read-only-rootfs tmpfs mounts work."""
        source = source.resolve()
        if not source.exists():
            raise CommunityDockerError(f"copy source does not exist: {source}")
        archive_path: Path | None = None
        try:
            with tempfile.NamedTemporaryFile(prefix="gb-docker-in-", suffix=".tar", delete=False) as raw:
                archive_path = Path(raw.name)
            with tarfile.open(archive_path, "w", dereference=True) as archive:
                if source.is_dir():
                    for child in sorted(source.iterdir(), key=lambda path: path.name):
                        archive.add(child, arcname=child.name, recursive=True)
                else:
                    archive.add(source, arcname=source.name, recursive=False)
            with archive_path.open("rb") as payload:
                proc = subprocess.run(
                    [self.executable, "exec", "-i", "--user", "0:0", container,
                     "tar", "-C", destination, "-xf", "-"],
                    stdin=payload, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                    timeout=timeout, check=False, env=self._child_env(),
                )
        except (OSError, subprocess.TimeoutExpired, tarfile.TarError) as exc:
            raise CommunityDockerError("Docker archive upload could not complete") from exc
        finally:
            if archive_path is not None:
                archive_path.unlink(missing_ok=True)
        if proc.returncode:
            stderr = proc.stderr.decode("utf-8", "replace")
            detail = stderr.strip().splitlines()
            reason = (
                "no_space" if "No space left on device" in stderr else
                "permission_denied" if "Permission denied" in stderr else
                "read_only" if "Read-only file system" in stderr else
                "missing_target" if "Cannot open: No such file or directory" in stderr else
                "tar_error"
            )
            raise CommunityDockerError(
                f"Docker archive upload to {destination} failed ({reason}): "
                + (detail[-1] if detail else "no output")
            )

    def copy_from_directory(
        self, container: str, source: str, destination: Path, *, timeout: int = 600,
        exclude: Sequence[str] = (),
    ) -> None:
        """Stream a mounted directory out and reject unsafe archive members."""
        destination.mkdir(parents=True, exist_ok=True)
        archive_path: Path | None = None
        try:
            with tempfile.NamedTemporaryFile(prefix="gb-docker-out-", suffix=".tar", delete=False) as raw:
                archive_path = Path(raw.name)
                proc = subprocess.run(
                    [self.executable, "exec", "--user", "0:0", container,
                     "tar", "-C", source, *["--exclude=" + value for value in exclude],
                     "-cf", "-", "."],
                    stdout=raw, stderr=subprocess.PIPE, timeout=timeout,
                    check=False, env=self._child_env(),
                )
            if proc.returncode:
                detail = proc.stderr.decode("utf-8", "replace").strip().splitlines()
                raise CommunityDockerError(
                    "Docker archive download failed: " + (detail[-1] if detail else "no output")
                )
            with tarfile.open(archive_path, "r:") as archive:
                for member in archive.getmembers():
                    relative = Path(member.name)
                    if (relative.is_absolute() or ".." in relative.parts
                            or member.issym() or member.islnk()
                            or member.isdev() or member.isfifo()):
                        raise CommunityDockerError("Docker output archive contains an unsafe member")
                archive.extractall(destination)
        except (OSError, subprocess.TimeoutExpired, tarfile.TarError) as exc:
            raise CommunityDockerError("Docker archive download could not complete") from exc
        finally:
            if archive_path is not None:
                archive_path.unlink(missing_ok=True)


def _stage_file(source: Path, target: Path) -> None:
    try:
        os.link(source, target)
    except OSError:
        shutil.copy2(source, target)


def build_images(
    *, repo_root: Path, archives: Path, profile: CommunityProfile,
    docker: DockerClient, download_missing: bool = True,
    reuse_image_lock: Mapping[str, Any] | None = None,
) -> dict[str, Any]:
    """Build local images from verified official archives; never accept a license."""
    manifest_path = repo_root / "docker" / "mode5-unity-install-manifest.json"
    manifest = load_install_manifest(manifest_path)
    verified = ensure_archives(archives, manifest, allow_download=download_missing)
    docker.run(["version"], timeout=60)

    # A locally attested, unactivated image can serve as the exact immutable
    # Unity base when only public helpers changed. This avoids duplicating the
    # 17 GB Editor on systems that imported images without their build cache.
    reusable = dict(reuse_image_lock or {})
    existing = dict(reusable.get("agent_image") or {})
    archive_hashes = {"editor_sha256": verified["editor"]["sha256"],
                      "il2cpp_sha256": verified["linux_il2cpp"]["sha256"]}
    if (reusable.get("schema") == IMAGE_LOCK_SCHEMA
            and reusable.get("profile_id") == profile.profile_id
            and reusable.get("unity_archives") == archive_hashes
            and re.fullmatch(r"[A-Za-z0-9_./:@-]+", str(existing.get("id") or ""))):
        inspected = docker.run(["image", "inspect", "--format", "{{.Id}}", existing["id"]],
                               timeout=60, check=False)
        if inspected.returncode == 0 and inspected.stdout.strip() == existing["id"]:
            with tempfile.TemporaryDirectory(prefix="gamebench-mode5-public-helpers-") as temp:
                context = Path(temp)
                shutil.copytree(repo_root / "docker" / "mode5", context / "mode5")
                # This is a generated build artifact, not a repository edit.
                (context / "Dockerfile").write_text(
                    "FROM " + existing["id"] + "\nUSER root\n"
                    "COPY mode5/GBCommunityBuild.cs /opt/gamebench/mode5/GBCommunityBuild.cs\n"
                    "COPY mode5/gb-unity /usr/local/bin/gb-unity\n"
                    "ENV UNITY_DISABLE_LINUX_TOOLCHAIN_MIGRATOR=1 UNITY_DISABLE_LINUX_AUTO_TOOLCHAIN_INSTALLATION=1\n"
                    "RUN chmod 0755 /usr/local/bin/gb-unity\nUSER agent\n", encoding="utf-8",
                )
                docker.run(["build", "--platform", "linux/amd64", "-t", AGENT_TAG, str(context)], timeout=600)
            docker.run(["tag", AGENT_TAG, EVALUATOR_TAG], timeout=60)
            return {"schema": IMAGE_LOCK_SCHEMA, "profile_id": profile.profile_id,
                    "agent_image": {"tag": AGENT_TAG, "id": docker.image_id(AGENT_TAG)},
                    "evaluator_image": {"tag": EVALUATOR_TAG, "id": docker.image_id(EVALUATOR_TAG)},
                    "unity_archives": archive_hashes, "public_helper_base_image_id": existing["id"]}

    # The Unity image intentionally inherits the shared public agent toolchain.
    # Build it when absent so setup is one command on a clean checkout.
    base = "gamebench-agent:godot-4.5.1"
    if docker.run(["image", "inspect", base], timeout=60, check=False).returncode:
        with tempfile.TemporaryDirectory(prefix="gamebench-mode5-base-") as base_temp:
            base_context = Path(base_temp)
            shutil.copy2(repo_root / "docker" / "Dockerfile.godot", base_context / "Dockerfile")
            shutil.copy2(repo_root / "eval" / "evalsys" / "requirements.txt",
                         base_context / "requirements.txt")
            docker.run([
                "build", "--platform", "linux/amd64", "-t", base,
                str(base_context),
            ], timeout=3600)

    with tempfile.TemporaryDirectory(prefix="gamebench-mode5-build-", dir=str(archives)) as temp:
        context = Path(temp)
        shutil.copy2(repo_root / "docker" / "Dockerfile.unity-local", context / "Dockerfile")
        shutil.copytree(repo_root / "docker" / "mode5", context / "mode5")
        for spec in manifest["archives"].values():
            _stage_file(archives / spec["file"], context / spec["file"])
        docker.run([
            "build", "--platform", "linux/amd64", "--network", "host",
            "-t", AGENT_TAG, str(context),
        ], timeout=7200)
    docker.run(["tag", AGENT_TAG, EVALUATOR_TAG], timeout=60)
    return {
        "schema": IMAGE_LOCK_SCHEMA,
        "profile_id": profile.profile_id,
        "agent_image": {"tag": AGENT_TAG, "id": docker.image_id(AGENT_TAG)},
        "evaluator_image": {"tag": EVALUATOR_TAG, "id": docker.image_id(EVALUATOR_TAG)},
        "unity_archives": {
            "editor_sha256": verified["editor"]["sha256"],
            "il2cpp_sha256": verified["linux_il2cpp"]["sha256"],
        },
    }


def write_private_json(path: Path, payload: Mapping[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temp = path.with_suffix(path.suffix + ".tmp")
    temp.write_text(json.dumps(payload, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    if os.name != "nt":
        temp.chmod(0o600)
    temp.replace(path)


def export_manual_activation_request(
    *, image_lock: Mapping[str, Any], out: Path, docker: DockerClient,
) -> Path:
    """Generate an offline ALF bound to the pinned Community image."""
    image = dict(image_lock.get("agent_image") or {})
    image_id = str(image.get("id") or "")
    tag = str(image.get("tag") or "")
    if not image_id or not tag or docker.image_id(tag) != image_id:
        raise CommunityDockerError("agent image does not match image-lock")
    out = out.expanduser().resolve()
    if out.exists():
        raise CommunityDockerError(f"activation request output already exists: {out}")
    if out.suffix.lower() != ".alf":
        raise CommunityDockerError("activation request output must end in .alf")
    out.parent.mkdir(parents=True, exist_ok=True)
    container = f"gb-mode5-license-request-{uuid.uuid4().hex[:10]}"
    created = False
    stage = Path(tempfile.mkdtemp(prefix="gamebench-mode5-license-request-"))
    try:
        docker.run([
            "create", "--name", container, "--network", "none",
            "--platform", "linux/amd64", "--read-only", "--cap-drop", "ALL",
            "--cap-add", "CHOWN", "--cap-add", "DAC_OVERRIDE",
            "--security-opt", "no-new-privileges:true",
            "--tmpfs", "/home/unity-runner:rw,nosuid,nodev,size=256m",
            "--tmpfs", "/tmp:rw,nosuid,nodev,size=256m",
            image_id, "sh", "-c", "while :; do sleep 30; done",
        ], timeout=120)
        created = True
        docker.run(["start", container], timeout=120)
        docker.run(["exec", "--user", "0:0", container, "sh", "-c",
                    "chown unity-runner:unity-runner /home/unity-runner /tmp"], timeout=60)
        networks = docker.run([
            "inspect", "--format", "{{json .NetworkSettings.Networks}}", container,
        ], timeout=60).stdout.strip()
        assert_offline_networks(networks)
        process = docker.run([
            "exec", "--user", "unity-runner:unity-runner", "--workdir",
            "/home/unity-runner", container, "unity", "-batchmode", "-nographics",
            "-createManualActivationFile", "-logFile", "/tmp/unity.log",
        ], timeout=300, check=False)
        if process.returncode:
            raise CommunityDockerError("Unity could not generate a manual activation request")
        names = docker.run([
            "exec", "--user", "0:0", container, "find", "/home/unity-runner",
            "-maxdepth", "1", "-type", "f", "-name", "*.alf", "-printf", "%f\n",
        ], timeout=60).stdout.splitlines()
        names = [name.strip() for name in names if name.strip()]
        if len(names) != 1 or Path(names[0]).name != names[0]:
            raise CommunityDockerError("Unity did not produce exactly one activation request")
        docker.copy_from_directory(container, "/home/unity-runner", stage, timeout=120)
        request = stage / names[0]
        if not request.is_file() or request.stat().st_size < 100:
            raise CommunityDockerError("Unity activation request is missing or truncated")
        shutil.copy2(request, out)
        if os.name != "nt":
            out.chmod(0o600)
        return out
    finally:
        if created:
            docker.run(["rm", "-f", container], timeout=120, check=False)
        shutil.rmtree(stage, ignore_errors=True)


def _check(checks: list[dict[str, str]], ident: str, action: Callable[[], str]) -> bool:
    try:
        detail = action()
    except Exception as exc:
        checks.append({"id": ident, "status": "fail", "detail": str(exc)})
        return False
    checks.append({"id": ident, "status": "pass", "detail": detail})
    return True


def run_doctor(
    *, repo_root: Path, profile: CommunityProfile, image_lock: Mapping[str, Any],
    license_provider: LicenseProvider, docker: DockerClient,
) -> dict[str, Any]:
    """Run D01-D10 in order in one disposable, offline evaluator container."""
    checks: list[dict[str, str]] = []
    images = {role: dict(image_lock[f"{role}_image"]) for role in ("agent", "evaluator")}
    _check(checks, "D01", lambda: (docker.run(["version"], timeout=60), "Docker daemon responded")[1])

    def platform_check() -> str:
        observed = docker.run([
            "image", "inspect", "--format", "{{.Os}}/{{.Architecture}}",
            images["evaluator"]["tag"],
        ], timeout=60).stdout.strip()
        if observed != "linux/amd64":
            raise CommunityDockerError(f"expected linux/amd64, observed {observed}")
        return observed
    _check(checks, "D02", platform_check)

    def digest_check() -> str:
        for role, row in images.items():
            observed = docker.image_id(row["tag"])
            if observed != row["id"]:
                raise CommunityDockerError(f"{role} image digest mismatch")
        return "agent/evaluator image IDs match image-lock"
    _check(checks, "D03", digest_check)

    image = images["evaluator"]["id"]
    container = f"gb-mode5-doctor-{uuid.uuid4().hex[:10]}"
    created = False
    fixture = repo_root / "eval" / "infra" / "unity" / "fixtures" / "blank_protocol"
    output = tempfile.mkdtemp(prefix="gamebench-mode5-doctor-output-")
    staged_package_lock_digest = ""
    license_meta = license_provider.public_metadata(probe_passed=False)
    try:
        network_mode = "bridge" if license_provider.kind == "floating" else "none"
        create = [
            "create", "--name", container, "--network", network_mode,
            "--platform", "linux/amd64",
            "-e", "HOME=/opt/gb-unity-home", "-e", "GB_FIXTURE_BUILD_DIR=/work/out/build",
        ]
        create += host_machine_identity_mount(license_provider.kind)
        if license_provider.kind == "floating":
            create += ["-e", f"UNITY_LICENSE_SERVER={license_provider.endpoint}"]
        create += [image, "sh", "-c", "while :; do sleep 30; done"]
        docker.run(create, timeout=120)
        created = True
        docker.run(["start", container], timeout=120)
        docker.run(["exec", "--user", "0:0", container, "mkdir", "-p",
                    "/work/fixture", "/work/out", "/opt/gb-unity-home"], timeout=60)
        # D06 exercises the actual Community dependency set, including uGUI and Tilemap.
        # Stage a disposable copy for the Community dependency preflight.
        with tempfile.TemporaryDirectory(prefix="gb-mode5-doctor-fixture-") as fixture_temp:
            staged_fixture = Path(fixture_temp) / "fixture"
            shutil.copytree(fixture, staged_fixture)
            shutil.copytree(
                TARGET_UNITY_ROOT / "Packages", staged_fixture / "Packages",
                dirs_exist_ok=True,
            )
            shutil.copytree(
                TARGET_UNITY_ROOT / "Assets" / "GameBenchmarkSDK",
                staged_fixture / "Assets" / "GameBenchmarkSDK",
                dirs_exist_ok=True,
            )
            enable_community_engine_modules(staged_fixture)
            staged_immutable = immutable_scaffold_paths(staged_fixture)
            staged_dependencies = json.loads(
                (staged_fixture / "Packages/manifest.json").read_text(encoding="utf-8")
            )["dependencies"]
            (staged_fixture / "Assets" / "CommunityUguiProbe.cs").write_text(
                "using UnityEngine.UI; using UnityEngine.Tilemaps; "
                "public sealed class CommunityModulesProbe : UnityEngine.MonoBehaviour "
                "{ public Text label; public Image icon; public Tilemap map; "
                "public UnityEngine.AudioSource audioSource; }\n",
                encoding="utf-8",
            )
            docker.run(["cp", f"{staged_fixture}{os.sep}.",
                        f"{container}:/work/fixture/"], timeout=300)
        if license_provider.kind == "file" and license_provider.source:
            docker.run(["cp", str(license_provider.source), f"{container}:/work/license.ulf"], timeout=60)
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
                docker.run(["cp", f"{staged_home}{os.sep}.",
                            f"{container}:/opt/gb-unity-home/"], timeout=300)
        docker.run([
            "exec", "--user", "0:0", container, "sh", "-c",
            "chown -R agent:agent /work /opt/gb-unity-home && "
            "chmod -R go-rwx /opt/gb-unity-home",
        ], timeout=120)

        def unity_version() -> str:
            text = docker.run(["exec", container, "unity", "-version"], timeout=120).stdout.strip()
            if profile.unity_editor not in text:
                raise CommunityDockerError(f"expected {profile.unity_editor}, observed {text!r}")
            return text.splitlines()[-1] if text else profile.unity_editor
        _check(checks, "D04", unity_version)

        def apply_license() -> str:
            argv = ["exec", container, "unity", "-batchmode", "-nographics", "-quit"]
            if license_provider.kind == "file":
                argv += ["-manualLicenseFile", "/work/license.ulf"]
            argv += ["-logFile", "-"]
            proc = docker.run(argv, timeout=300, check=False)
            combined = (proc.stdout or "") + "\n" + (proc.stderr or "")
            marker = license_log_error(combined)
            if proc.returncode or marker:
                raise CommunityDockerError(
                    "Unity license probe failed" + (f": {marker}" if marker else "")
                )
            license_meta.update(license_provider.public_metadata(probe_passed=True))
            return "Unity batchmode accepted configured entitlement"
        _check(checks, "D05", apply_license)

        # A floating server is reachable only while acquiring the entitlement.
        # Disconnect before any fixture compilation so D06-D08 exercise the
        # same offline boundary as the real evaluator.  File/home providers
        # start offline and never receive a network interface.
        offline_error: Exception | None = None
        try:
            if license_provider.kind == "floating":
                docker.run(["network", "disconnect", "bridge", container], timeout=60)
            networks = docker.run([
                "inspect", "--format", "{{json .NetworkSettings.Networks}}", container,
            ], timeout=60).stdout.strip()
            assert_offline_networks(networks)
        except Exception as exc:
            offline_error = exc

        build_result: dict[str, subprocess.CompletedProcess[str]] = {}
        def build_fixture() -> str:
            if offline_error is not None:
                raise CommunityDockerError(
                    f"offline transition failed: {offline_error}"
                )
            proc = docker.run([
                "exec", container, "unity", "-batchmode", "-nographics", "-quit",
                "-projectPath", "/work/fixture", "-executeMethod",
                "GameBench.Fixtures.Editor.BuildBlankFixture.BuildLinuxPlayer",
                "-logFile", "-",
            ], timeout=1800, check=False)
            build_result["proc"] = proc
            combined = (proc.stdout or "") + "\n" + (proc.stderr or "")
            if proc.returncode or "error cs" in combined.lower():
                raise CommunityDockerError("blank fixture import/compile failed")
            observed_manifest = docker.run([
                "exec", container, "cat", "/work/fixture/Packages/manifest.json",
            ], timeout=60).stdout
            if json.loads(observed_manifest).get("dependencies") != staged_dependencies:
                raise CommunityDockerError("Unity scaffold dependency versions changed during import")
            sdk_files = docker.run([
                "exec", container, "find", "/work/fixture/Assets/GameBenchmarkSDK", "-type", "f",
            ], timeout=60).stdout.splitlines()
            expected_sdk = {"/work/fixture/" + name for name in staged_immutable
                            if name.startswith("Assets/GameBenchmarkSDK/") and not name.endswith(".meta")}
            if {name for name in sdk_files if not name.endswith(".meta")} != expected_sdk:
                raise CommunityDockerError("required Unity SDK files are missing or unexpected")
            return "blank fixture/UI/Tilemap/audio compiled; dependency versions and SDK file structure valid"
        build_ok = _check(checks, "D06", build_fixture)

        def player_exists() -> str:
            if not build_ok:
                raise CommunityDockerError("skipped because D06 failed")
            docker.run(["exec", container, "test", "-x", "/work/out/build/BlankFixture.x86_64"], timeout=60)
            return "StandaloneLinux64 Player is executable"
        _check(checks, "D07", player_exists)

        def runtime_smoke() -> str:
            proc = docker.run([
                "exec", container, "xvfb-run", "-a", "-s", "-screen 0 960x540x24",
                "/work/out/build/BlankFixture.x86_64", "--gb-output=/work/out/runtime",
                "-screen-fullscreen", "0", "-screen-width", "960", "-screen-height", "540",
                "-logFile", "-",
            ], timeout=180, check=False)
            if proc.returncode:
                raise CommunityDockerError("blank fixture Player failed")
            docker.run(["exec", container, "test", "-s", "/work/out/runtime/blank-fixture.png"], timeout=60)
            docker.run(["exec", container, "test", "-s", "/work/out/runtime/runtime-result.json"], timeout=60)
            return "offline Xvfb Player produced screenshot and runtime result"
        _check(checks, "D08", runtime_smoke)
        def offline_check() -> str:
            if offline_error is not None:
                raise CommunityDockerError(str(offline_error))
            return (
                "floating entitlement acquired before network disconnect"
                if license_provider.kind == "floating"
                else "container was created with --network none"
            )
        _check(checks, "D09", offline_check)
        docker.run(["cp", f"{container}:/work/out/.", output], timeout=300, check=False)
    finally:
        if created:
            docker.run(["rm", "-f", container], timeout=120, check=False)
        shutil.rmtree(output, ignore_errors=True)

    def cleanup_check() -> str:
        found = docker.run(["inspect", container], timeout=60, check=False)
        if found.returncode == 0:
            raise CommunityDockerError("doctor container was not removed")
        return "doctor container removed"
    _check(checks, "D10", cleanup_check)
    passed = len(checks) == 10 and all(row["status"] == "pass" for row in checks)
    return {
        "schema": PREFLIGHT_SCHEMA,
        "profile_id": profile.profile_id,
        "environment_class": profile.environment_class,
        "paper_compatible": False,
        "status": "pass" if passed else "fail",
        "checked_at": datetime.now(timezone.utc).isoformat(),
        "image_ids": {role: images[role]["id"] for role in images},
        "unity": {"version": profile.unity_editor, "changeset": profile.unity_changeset},
        "license": license_meta,
        "checks": checks,
    }
