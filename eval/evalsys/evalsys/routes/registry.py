


from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from .schema import Route, load_route_file


@dataclass(frozen=True)
class RegisteredRoutes:
    task_id: str
    routes: tuple[Route, ...]
    certified_ids: frozenset[str]
    route_sha256: str
    reviewer: str
    reviewed_at: str
    route_path: Path
    certificate_path: Path
    analog_axes: tuple[Any, ...] = ()
    extended_actions: tuple[str, ...] = ()


def routes_root() -> Path:

    from ..tasks import registry_path

    return registry_path().parent


def file_sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def load_registered_routes(
    task_id: str,
    root: str | Path | None = None,
) -> RegisteredRoutes | None:


    if root is None:
        from ..tasks import eval_root, registry_path as default_registry_path

        base = eval_root()
        registry_path = default_registry_path()
    else:
        base = Path(root)
        registry_path = base / "registry.json"
    if not registry_path.is_file():
        return None
    raw = json.loads(registry_path.read_text(encoding="utf-8"))
    entry: dict[str, Any] | None = (raw.get("tasks") or {}).get(task_id)
    if not entry:
        return None

    route_path = base / str(entry.get("route_file", ""))
    certificate_path = base / str(entry.get("certificate", ""))
    if not route_path.is_file() or not certificate_path.is_file():
        raise ValueError(f"registered route assets missing for {task_id!r}")
    actual_hash = file_sha256(route_path)
    registered_hash = str(entry.get("sha256", ""))
    certificate = json.loads(certificate_path.read_text(encoding="utf-8"))
    certificate_hash = str(certificate.get("route_sha256", ""))
    if not registered_hash or actual_hash != registered_hash or actual_hash != certificate_hash:
        raise ValueError(
            f"route hash mismatch for {task_id!r}: actual={actual_hash}, "
            f"registry={registered_hash}, certificate={certificate_hash}"
        )

    routes = tuple(load_route_file(route_path))
    certified_ids = frozenset(str(x) for x in certificate.get("passed_route_ids") or [])
    unknown = certified_ids - {r.route_id for r in routes}
    if unknown:
        raise ValueError(f"certificate for {task_id!r} names unknown routes: {sorted(unknown)}")
    if not str(entry.get("reviewer", "")).strip():
        raise ValueError(f"registered route suite {task_id!r} has no human reviewer")
    iface = certificate.get("interface") if isinstance(certificate.get("interface"), dict) else {}
    raw_extended = iface.get("extended_actions") or ()
    extra_ids: list[str] = []
    if isinstance(raw_extended, list):
        for item in raw_extended:
            if isinstance(item, dict) and item.get("id"):
                extra_ids.append(str(item["id"]))
            elif isinstance(item, str) and item:
                extra_ids.append(item)
    from ..interface.model import AnalogAxis

    raw_axes = iface.get("analog_axes") or ()
    axis_specs: list[AnalogAxis] = []
    if isinstance(raw_axes, list):
        for item in raw_axes:
            spec = AnalogAxis.from_dict(item)
            if spec is not None:
                axis_specs.append(spec)
    return RegisteredRoutes(
        task_id=task_id,
        routes=routes,
        certified_ids=certified_ids,
        route_sha256=actual_hash,
        reviewer=str(entry["reviewer"]),
        reviewed_at=str(entry.get("reviewed_at", "")),
        route_path=route_path,
        certificate_path=certificate_path,
        analog_axes=tuple(axis_specs),
        extended_actions=tuple(extra_ids),
    )
