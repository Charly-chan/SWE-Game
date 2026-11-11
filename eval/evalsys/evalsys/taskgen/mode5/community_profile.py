"""Validated, secret-free environment contract for Mode 5 Community Docker."""

from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Mapping


PROFILE_SCHEMA = "gamebench.mode5-environment-profile.v1"
PROFILE_ID = "mode5-community-docker-v1"
EXPECTED_UNITY_VERSION = "6000.3.23f1"
EXPECTED_UNITY_CHANGESET = "09d2ecc7fb28"
REQUIRED_PREFLIGHT = (
    "image_digest", "unity_version", "license", "scaffold_import",
    "script_compile", "linux_player_build", "player_smoke",
)


class CommunityProfileError(ValueError):
    """The public environment profile is missing or unsafe."""


@dataclass(frozen=True)
class CommunityProfile:
    profile_id: str
    environment_class: str
    paper_compatible: bool
    platform: str
    unity_editor: str
    unity_changeset: str
    build_target: str
    scripting_backend: str
    network_policy: str
    renderer: str
    display: str
    limits: Mapping[str, int]
    required_preflight: tuple[str, ...]

    @classmethod
    def from_dict(cls, raw: Mapping[str, Any]) -> "CommunityProfile":
        if raw.get("schema") != PROFILE_SCHEMA:
            raise CommunityProfileError(f"unsupported profile schema: {raw.get('schema')!r}")
        required_strings = (
            "profile_id", "environment_class", "platform", "unity_editor",
            "unity_changeset", "build_target", "scripting_backend",
            "network_policy", "renderer", "display",
        )
        for key in required_strings:
            if not isinstance(raw.get(key), str) or not str(raw[key]).strip():
                raise CommunityProfileError(f"profile field {key!r} must be a non-empty string")
        if raw["profile_id"] != PROFILE_ID:
            raise CommunityProfileError(f"unexpected profile_id: {raw['profile_id']!r}")
        if raw["environment_class"] != "community-docker":
            raise CommunityProfileError("community profile must use environment_class community-docker")
        if raw.get("paper_compatible") is not False:
            raise CommunityProfileError("community Docker must explicitly declare paper_compatible=false")
        if raw["unity_editor"] != EXPECTED_UNITY_VERSION:
            raise CommunityProfileError(f"Unity version must be {EXPECTED_UNITY_VERSION}")
        if raw["unity_changeset"] != EXPECTED_UNITY_CHANGESET:
            raise CommunityProfileError(f"Unity changeset must be {EXPECTED_UNITY_CHANGESET}")
        limits = raw.get("limits")
        if not isinstance(limits, Mapping):
            raise CommunityProfileError("limits must be an object")
        clean_limits: dict[str, int] = {}
        for key in ("cpus", "memory_mb", "wall_seconds"):
            value = limits.get(key)
            if not isinstance(value, int) or isinstance(value, bool) or value <= 0:
                raise CommunityProfileError(f"limits.{key} must be a positive integer")
            clean_limits[key] = value
        checks = raw.get("required_preflight")
        if not isinstance(checks, list) or any(not isinstance(v, str) for v in checks):
            raise CommunityProfileError("required_preflight must be a string array")
        if tuple(checks) != REQUIRED_PREFLIGHT:
            raise CommunityProfileError(
                "required_preflight must exactly match the release fail-closed order"
            )
        return cls(
            profile_id=raw["profile_id"], environment_class=raw["environment_class"],
            paper_compatible=False, platform=raw["platform"], unity_editor=raw["unity_editor"],
            unity_changeset=raw["unity_changeset"], build_target=raw["build_target"],
            scripting_backend=raw["scripting_backend"], network_policy=raw["network_policy"],
            renderer=raw["renderer"], display=raw["display"], limits=clean_limits,
            required_preflight=tuple(checks),
        )

    def public_dict(self) -> dict[str, Any]:
        return {
            "schema": PROFILE_SCHEMA,
            "profile_id": self.profile_id,
            "environment_class": self.environment_class,
            "paper_compatible": self.paper_compatible,
            "platform": self.platform,
            "unity_editor": self.unity_editor,
            "unity_changeset": self.unity_changeset,
            "build_target": self.build_target,
            "scripting_backend": self.scripting_backend,
            "network_policy": self.network_policy,
            "renderer": self.renderer,
            "display": self.display,
            "limits": dict(self.limits),
            "required_preflight": list(self.required_preflight),
        }


def load_profile(path: Path) -> CommunityProfile:
    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise CommunityProfileError(f"cannot read profile {path}: {exc}") from exc
    if not isinstance(raw, Mapping):
        raise CommunityProfileError("profile root must be an object")
    return CommunityProfile.from_dict(raw)


def default_profile_path() -> Path:
    """Return the checked-in profile path in a source checkout."""
    return (Path(__file__).resolve().parents[4] / "infra" / "unity" / "profiles" /
            "mode5-community-docker-v1.json")


def load_default_profile() -> CommunityProfile:
    return load_profile(default_profile_path())


def task_environment_lock(profile: CommunityProfile) -> dict[str, Any]:
    """Public requirements copied into a task; deliberately no host/image state."""
    return {
        "schema": "gamebench.mode5-task-environment.v1",
        "profile_family": "mode5-community-docker",
        "unity_version": profile.unity_editor,
        "unity_changeset": profile.unity_changeset,
        "build_target": profile.build_target,
        "scripting_backend": profile.scripting_backend,
        "required_self_checks": ["import", "compile", "player_build"],
    }
