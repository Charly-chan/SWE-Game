

from __future__ import annotations

import hashlib
import json
from pathlib import Path
from typing import Sequence

from ..context import AssetObservation
from ..harness import face_path
from ..interface import SubmissionInterface
from ..interface.runtime import runtime_args, write_runtime_interface
from ..render.modes import RenderMode, build_command, build_env, run
from .inject import SCRATCH_ROOT, cold_import, inject_autoload, prepare_scratch

ASSET_PROBE = face_path("gb_asset_probe.gd")
SHARED_PROBE = face_path("gb_probe.gd")


def run_asset_probe(
    project: str | Path,
    interface: SubmissionInterface,
    *,
    timeout: float = 900.0,
) -> AssetObservation:


    key = hashlib.sha256(str(interface.project_root).encode("utf-8")).hexdigest()[:12]
    scratch = SCRATCH_ROOT.parent / "gb_asset_scratch" / key
    prep = prepare_scratch(
        interface.project_root,
        scratch,
        script_src=ASSET_PROBE,
        autoload_name="GBAssetProbe",
        do_import=False,
    )
    if not prep.scratch.ok or prep.inject is None or not prep.inject.ok:
        return AssetObservation(detail="asset scratch preparation failed")
    shared = inject_autoload(scratch, SHARED_PROBE, "GBHarnessProbe")
    if not shared.ok:
        return AssetObservation(detail="asset runtime probe injection failed: " + shared.detail)
    imported = cold_import(scratch)
    retried = ""
    if not imported.ok and imported.timed_out:


        imported = cold_import(scratch)
        retried = " (after one retry of a timed-out import)"
    if not imported.ok:
        return AssetObservation(detail="asset scratch import failed" + retried + ": " + imported.detail)

    runtime_file = write_runtime_interface(interface, scratch.parent / "_interface_io")
    scene = interface.levels[0].scene if interface.levels else ""
    if not scene:
        return AssetObservation(detail="normalized interface has no level address")


    frames = max(300, len(interface.levels) * 240 + 120)
    command = build_command(
        RenderMode.H,
        scratch,
        scene=scene,
        quit_after=frames,
        fixed_fps=60,
        user_args=runtime_args(interface, runtime_file),
    )
    environment = build_env(RenderMode.H)
    environment["GB_ASSET_SAMPLING"] = "1"
    result = run(command, cwd=scratch, timeout=timeout, env=environment)
    prefix = "GB_ASSET_USAGE "
    payload = next(
        (line[len(prefix):] for line in result.log.splitlines() if line.startswith(prefix)),
        "",
    )
    if not payload:
        detail = next(
            (line for line in result.log.splitlines() if line.startswith("GB_ASSET_ERROR ")),
            "asset probe produced no engine usage report",
        )
        detail += f" (returncode={result.returncode}, timed_out={result.timed_out}, seconds={result.seconds:.1f})"
        errors = [line for line in result.log.splitlines()
                  if "ERROR" in line or "TIMEOUT" in line]
        if errors:
            detail += ": " + " | ".join(errors[-5:])[:1500]
        return AssetObservation(detail=detail)
    try:
        parsed = json.loads(payload)
        paths = tuple(str(path) for path in parsed.get("observed_resource_paths") or ())
    except (TypeError, ValueError, AttributeError) as exc:
        return AssetObservation(detail=f"asset usage report did not parse: {exc}")
    return AssetObservation(
        loaded_assets=paths,
        report_produced=True,
        detail=f"observed {len(paths)} live resource path(s) in an isolated pass",
        loaded_asset_digests=digest_resources(scratch, paths),
    )


def digest_resources(project_root: Path, resource_paths: Sequence[str]) -> dict[str, str]:


    digests: dict[str, str] = {}
    root = Path(project_root)
    for res_path in resource_paths:
        text = str(res_path)
        if not text.startswith("res://"):
            continue
        rel = text[len("res://"):]
        candidate = root / rel
        try:
            if not candidate.is_file():
                continue
            digests[text] = hashlib.sha256(candidate.read_bytes()).hexdigest()
        except OSError:
            continue
    return digests
