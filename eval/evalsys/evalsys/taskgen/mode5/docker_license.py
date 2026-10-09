"""Unity license provider validation without persisting or reporting secrets."""

from __future__ import annotations

import os
import stat
import subprocess
from dataclasses import dataclass
from pathlib import Path
from typing import Any


LICENSE_ERROR_PATTERNS = tuple(text.lower() for text in (
    "No valid Unity Editor license",
    "Failed to activate/update license",
    "license is invalid",
    "Licensing Client timed out",
    "Failed to connect to licensing client",
))


def host_machine_identity_mount(provider: str) -> list[str]:
    """Use the activating Linux host's identity for an existing-home license.

    Named-user entitlements are activated for the current computer. The Unity
    image has its own machine-id, so copying only the Unity config directory
    makes a valid host activation fail in a disposable container. This bind is
    read-only, stays out of image layers, and is never used for file/floating
    providers. Doctor remains the authority on whether the host and daemon
    actually refer to the same licensed computer.
    """
    if provider != "existing-home":
        return []
    return [
        "--mount", "type=bind,source=/etc/machine-id,target=/etc/machine-id,readonly",
    ]


class LicenseConfigurationError(ValueError):
    """A configured provider is invalid before Unity is launched."""


@dataclass(frozen=True)
class LicenseProvider:
    kind: str
    source: Path | None = None
    endpoint: str | None = None

    @classmethod
    def file(cls, path: Path) -> "LicenseProvider":
        suffix = Path(path).suffix.lower().lstrip(".")
        if suffix not in {"ulf", "xml"}:
            raise LicenseConfigurationError("license file must end in .ulf or .xml")
        checked = _private_path(path, want_directory=False)
        return cls("file", checked)

    @classmethod
    def existing_home(cls, path: Path) -> "LicenseProvider":
        return cls("existing-home", _private_path(path, want_directory=True))

    @classmethod
    def floating(cls, endpoint: str) -> "LicenseProvider":
        value = endpoint.strip()
        if not value or any(ch in value for ch in "\r\n\0"):
            raise LicenseConfigurationError("floating endpoint must be a non-empty single line")
        return cls("floating", endpoint=value)

    def public_metadata(self, *, probe_passed: bool | None = None) -> dict[str, Any]:
        result: dict[str, Any] = {"provider": self.kind, "configured": True}
        if self.kind == "file" and self.source is not None:
            result["format"] = self.source.suffix.lower().lstrip(".")
        if probe_passed is not None:
            result["probe_passed"] = probe_passed
        return result


def _private_path(path: Path, *, want_directory: bool) -> Path:
    candidate = Path(path).expanduser()
    try:
        info = candidate.lstat()
    except OSError as exc:
        raise LicenseConfigurationError("license source does not exist or is unreadable") from exc
    is_reparse = bool(getattr(info, "st_file_attributes", 0) & 0x400)
    if stat.S_ISLNK(info.st_mode) or candidate.is_symlink() or is_reparse:
        raise LicenseConfigurationError("license source may not be a symlink or reparse point")
    if want_directory != candidate.is_dir():
        expected = "directory" if want_directory else "regular file"
        raise LicenseConfigurationError(f"license source must be a {expected}")
    if not want_directory and not candidate.is_file():
        raise LicenseConfigurationError("license source must be a regular file")
    if os.name == "nt":
        _validate_windows_acl(candidate)
    elif info.st_mode & (stat.S_IRGRP | stat.S_IROTH):
        raise LicenseConfigurationError("license source must not be group/world-readable")
    return candidate.resolve(strict=True)


def _validate_windows_acl(path: Path) -> None:
    """Fail closed when a Windows license source grants broad read access."""
    script = r"""
$ErrorActionPreference = 'Stop'
Import-Module Microsoft.PowerShell.Security -ErrorAction Stop
$acl = Get-Acl -LiteralPath $env:GB_LICENSE_ACL_PATH
foreach ($ace in $acl.Access) {
  if ($ace.AccessControlType -ne [System.Security.AccessControl.AccessControlType]::Allow) { continue }
  try { $sid = $ace.IdentityReference.Translate([System.Security.Principal.SecurityIdentifier]).Value }
  catch { continue }
  $rights = [int64]$ace.FileSystemRights
  if (($rights -band 1) -or ($rights -band 8) -or ($rights -band 128) -or ($rights -band 131072)) {
    Write-Output $sid
  }
}
"""
    try:
        child_env = os.environ.copy()
        child_env["GB_LICENSE_ACL_PATH"] = str(path)
        # The Codex runtime can prepend its own PowerShell modules.  Those
        # modules ship duplicate type-data entries and make the inbox
        # Microsoft.PowerShell.Security module fail to load.  ACL validation
        # must run against the Windows inbox module set only.
        child_env["PSModulePath"] = (
            r"C:\Windows\System32\WindowsPowerShell\v1.0\Modules"
        )
        result = subprocess.run(
            ["powershell.exe", "-NoProfile", "-NonInteractive", "-Command", script],
            capture_output=True, text=True, encoding="utf-8", errors="replace",
            timeout=30, check=False, env=child_env,
        )
    except (OSError, subprocess.TimeoutExpired) as exc:
        raise LicenseConfigurationError(
            "could not validate Windows ACL for license source"
        ) from exc
    if result.returncode:
        raise LicenseConfigurationError("could not validate Windows ACL for license source")
    broad_readers = {
        "S-1-1-0",       # Everyone
        "S-1-5-11",      # Authenticated Users
        "S-1-5-32-545",  # BUILTIN\\Users
        "S-1-5-32-546",  # BUILTIN\\Guests
    }
    if broad_readers.intersection(result.stdout.splitlines()):
        raise LicenseConfigurationError(
            "license source ACL grants read access to a broad Windows principal"
        )


def license_log_error(log_text: str) -> str | None:
    lowered = log_text.lower()
    return next((pattern for pattern in LICENSE_ERROR_PATTERNS if pattern in lowered), None)
