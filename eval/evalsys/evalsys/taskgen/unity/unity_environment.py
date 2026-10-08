


from __future__ import annotations

import os
import platform
import json
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any, Mapping


UNITY_V2_VERSION_SERIES = "Unity 6.3 LTS"
UNITY_V2_CANDIDATE_VERSION = "6000.3.23f1"


@dataclass(frozen=True)
class UnityEnvironmentProfile:
    profile_id: str
    environment_class: str
    platform: str
    editor_version: str = UNITY_V2_CANDIDATE_VERSION
    score_eligible: bool = False
    certified: bool = False
    graphical_profile: str = ""
    image_digest: str = ""
    detail: str = ""
    certification_status: str = "pending"
    guest_os: str = ""
    kernel: str = ""
    hypervisor: str = ""
    unity_changeset: str = ""
    unity_modules: tuple[str, ...] = ()
    package_lock_digest: str = ""
    display: str = ""
    renderer: str = ""
    mesa_version: str = ""
    resolution: str = "960x540"
    locale: str = "C.UTF-8"
    timezone: str = "UTC"
    network_policy: str = ""
    limits: Mapping[str, int] = field(default_factory=dict)
    license_mechanism: str = ""
    certification_record_digest: str = ""
    preflight_passed: bool = False

    def to_dict(self) -> dict[str, Any]:
        value = asdict(self)
        value["limits"] = dict(self.limits or {})
        return value

    @property
    def readiness_errors(self) -> tuple[str, ...]:
        errors: list[str] = []
        required = {
            "guest_os": self.guest_os,
            "kernel": self.kernel,
            "hypervisor": self.hypervisor,
            "unity_changeset": self.unity_changeset,
            "package_lock_digest": self.package_lock_digest,
            "image_digest": self.image_digest,
            "display": self.display,
            "renderer": self.renderer,
            "mesa_version": self.mesa_version,
            "graphical_profile": self.graphical_profile,
            "license_mechanism": self.license_mechanism,
            "certification_record_digest": self.certification_record_digest,
        }
        errors.extend(name for name, value in required.items() if not value)
        if self.environment_class != "linux-vm-certified":
            errors.append("environment_class")
        if self.editor_version != UNITY_V2_CANDIDATE_VERSION:
            errors.append("editor_version")
        if self.certification_status != "certified" or not self.certified:
            errors.append("certification_status")
        if not self.score_eligible:
            errors.append("score_eligible")
        if not self.preflight_passed:
            errors.append("preflight_passed")
        if not self.unity_modules:
            errors.append("unity_modules")
        if self.network_policy != "loopback-only":
            errors.append("network_policy")
        for name in ("package_lock_digest", "image_digest", "certification_record_digest"):
            if getattr(self, name) and not str(getattr(self, name)).startswith("sha256:"):
                errors.append(name + "_format")
        limits = self.limits or {}
        for name in ("cpus", "memory_mb", "disk_mb", "wall_seconds"):
            if int(limits.get(name, 0) or 0) <= 0:
                errors.append("limits." + name)
        return tuple(dict.fromkeys(errors))

    @property
    def ready_for_untrusted_execution(self) -> bool:
        return not self.readiness_errors


def _is_wsl() -> bool:
    if os.environ.get("WSL_INTEROP") or os.environ.get("WSL_DISTRO_NAME"):
        return True
    version = Path("/proc/version")
    if version.is_file():
        try:
            return "microsoft" in version.read_text(encoding="utf-8", errors="replace").lower()
        except OSError:
            return False
    return False


def detect_unity_environment() -> UnityEnvironmentProfile:
    system = platform.system().lower()
    if system == "windows":
        return UnityEnvironmentProfile(
            profile_id="local-windows",
            environment_class="local-windows",
            platform=platform.platform(),
            detail=(
                "Windows is a static/unit-test host only; score-eligible Unity import, "
                "build, player execution, and capture require linux-vm-certified"
            ),
        )
    if _is_wsl():
        return UnityEnvironmentProfile(
            profile_id="local-wsl-dev",
            environment_class="local-wsl-dev",
            platform=platform.platform(),
            detail=(
                "WSL2 is development-smoke only and never score eligible; do not run "
                "untrusted submissions"
            ),
        )
    if system == "linux":
        return UnityEnvironmentProfile(
            profile_id="local-linux-uncertified",
            environment_class="local-linux-uncertified",
            platform=platform.platform(),
            detail="Linux host has no certified disposable-VM profile",
        )
    return UnityEnvironmentProfile(
        profile_id="unsupported-local-platform",
        environment_class="unsupported-local-platform",
        platform=platform.platform(),
        detail="Mode 5 runtime is supported only by a certified Ubuntu VM",
    )


def coerce_environment_profile(
    value: UnityEnvironmentProfile | Mapping[str, Any] | None,
) -> UnityEnvironmentProfile:
    if value is None:
        provisioned = os.environ.get("GB_UNITY_ENVIRONMENT_PROFILE", "").strip()
        if provisioned:
            return load_environment_profile(provisioned)
        return detect_unity_environment()
    if isinstance(value, UnityEnvironmentProfile):
        return value
    display = str(value.get("display") or "")
    renderer = str(value.get("renderer") or "")
    certification_status = str(value.get("certification_status") or "pending")
    return UnityEnvironmentProfile(
        profile_id=str(value.get("profile_id") or "external-profile"),
        environment_class=str(value.get("environment_class") or ""),
        platform=str(value.get("platform") or ""),
        editor_version=str(
            value.get("editor_version")
            or value.get("unity_editor")
            or UNITY_V2_CANDIDATE_VERSION
        ),
        score_eligible=bool(value.get("score_eligible", False)),
        certified=bool(value.get("certified", certification_status == "certified")),
        graphical_profile=str(
            value.get("graphical_profile") or (f"{display}:{renderer}" if display and renderer else "")
        ),
        image_digest=str(value.get("image_digest") or value.get("base_image_digest") or ""),
        detail=str(value.get("detail") or ""),
        certification_status=certification_status,
        guest_os=str(value.get("guest_os") or ""),
        kernel=str(value.get("kernel") or ""),
        hypervisor=str(value.get("hypervisor") or ""),
        unity_changeset=str(value.get("unity_changeset") or ""),
        unity_modules=tuple(str(item) for item in (value.get("unity_modules") or ())),
        package_lock_digest=str(value.get("package_lock_digest") or ""),
        display=display,
        renderer=renderer,
        mesa_version=str(value.get("mesa_version") or ""),
        resolution=str(value.get("resolution") or "960x540"),
        locale=str(value.get("locale") or "C.UTF-8"),
        timezone=str(value.get("timezone") or "UTC"),
        network_policy=str(value.get("network_policy") or ""),
        limits={str(key): int(item) for key, item in dict(value.get("limits") or {}).items()},
        license_mechanism=str(value.get("license_mechanism") or ""),
        certification_record_digest=str(value.get("certification_record_digest") or ""),
        preflight_passed=bool(value.get("preflight_passed", False)),
    )


def load_environment_profile(path: str | Path) -> UnityEnvironmentProfile:
    profile_path = Path(path).resolve()
    payload = json.loads(profile_path.read_text(encoding="utf-8"))
    if not isinstance(payload, Mapping):
        raise ValueError("Unity environment profile must be a JSON object")
    return coerce_environment_profile(payload)


__all__ = [
    "UNITY_V2_CANDIDATE_VERSION",
    "UNITY_V2_VERSION_SERIES",
    "UnityEnvironmentProfile",
    "coerce_environment_profile",
    "detect_unity_environment",
    "load_environment_profile",
]
