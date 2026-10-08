"""Scoped release-file credential check; prints paths/counts, never secret values."""
from __future__ import annotations

import argparse
import json
from pathlib import Path


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--claude-settings", required=True)
    parser.add_argument("--license-provider-config")
    parser.add_argument("paths", nargs="+")
    args = parser.parse_args()
    private = json.loads(Path(args.claude_settings).read_text(encoding="utf-8"))
    secrets = [("provider_credential", str(value).encode()) for key, value in (private.get("env") or {}).items()
               if key in {"ANTHROPIC_AUTH_TOKEN", "ANTHROPIC_API_KEY"} and value]
    if args.license_provider_config:
        licensing = json.loads(Path(args.license_provider_config).read_text(encoding="utf-8"))
        if licensing.get("source"):
            # A standard runtime HOME is public configuration, not a private
            # operator location. Only flag an actual operator-specific path.
            if licensing["source"] not in {"/opt/gb-unity-home", "/home/unity-runner", "/home/agent", "/root"}:
                secrets.append(("private_license_source", str(licensing["source"]).encode()))
    files = set()
    for value in args.paths:
        path = Path(value)
        if not path.exists():
            parser.error("a requested scan target is missing")
        files.update([path] if path.is_file() else (entry for entry in path.rglob("*") if entry.is_file()))
    failures = []
    for path in sorted(files):
        if path.suffix.lower() in {".ulf", ".alf"}:
            failures.append(str(path))
            continue
        # Chunked exact-match scanning also covers large binary captures without
        # printing their contents or copying the operator's private credential.
        overlap = max((len(secret) for _, secret in secrets), default=1) - 1
        tail = b""
        with path.open("rb") as handle:
            while chunk := handle.read(1024 * 1024):
                data = tail + chunk
                kinds = [kind for kind, secret in secrets if secret in data]
                if kinds:
                    failures.append({"path": str(path), "categories": kinds})
                    break
                tail = data[-overlap:] if overlap else b""
    print(json.dumps({"scanned_files": len(files), "secret_matches": len(failures), "affected_paths": failures}, indent=2))
    return int(bool(failures))


if __name__ == "__main__":
    raise SystemExit(main())
