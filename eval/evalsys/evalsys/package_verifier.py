

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

PROVENANCE_KEYS = (
    "interface_version",
    "source_sha256",
    "normalized_sha256",
)


class PackageVerificationError(ValueError):
    pass


def _read(path: Path) -> Any:
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError) as exc:
        raise PackageVerificationError(f"cannot read package artifact {path.name}: {exc}") from exc


def _triple(value: Any, source: str) -> dict[str, Any]:
    if not isinstance(value, dict):
        raise PackageVerificationError(f"{source} has no interface provenance object")
    result = {key: value.get(key) for key in PROVENANCE_KEYS}
    if any(result[key] in (None, "") for key in PROVENANCE_KEYS):
        raise PackageVerificationError(f"{source} has incomplete interface provenance: {result}")
    return result


def verify_interface_provenance(package: str | Path) -> dict[str, Any]:

    root = Path(package)
    truth = _read(root / "truth_snapshot.json")
    routes = _read(root / "routes" / "readings.json")
    capture = _read(root / "capture_manifest.json")
    card = _read(root / "card.json")
    env = _read(root / "env.json")
    readings = routes if isinstance(routes, list) else []
    triples = [
        ("truth_snapshot.json", _triple(truth.get("interface"), "truth_snapshot.json")),
        ("capture_manifest.json", _triple(capture, "capture_manifest.json")),
        ("card.json.provenance", _triple(
            (card.get("provenance") or {}).get("interface"),
            "card.json.provenance",
        )),
        ("env.json", _triple(env.get("interface"), "env.json")),
    ]
    for index, reading in enumerate(readings):
        triples.append((
            f"routes/readings.json[{index}]",
            _triple(reading.get("interface") if isinstance(reading, dict) else None,
                    f"routes/readings.json[{index}]"),
        ))
    expected = triples[0][1]
    mismatches = [(source, value) for source, value in triples[1:] if value != expected]
    if mismatches:
        rendered = "; ".join(f"{source}={value}" for source, value in mismatches)
        raise PackageVerificationError(
            f"evaluation package mixes interface instances; expected {expected}; {rendered}"
        )
    return expected
