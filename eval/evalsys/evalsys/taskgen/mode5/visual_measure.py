"""Objective visual implementation/correspondence, not perceptual aesthetics.

Only feed this controller-owned in-memory captures. It never opens a candidate
report or calls an API. Removal/restoration pixels establish screen contribution;
component flags alone, arbitrary frame changes and unreadable HUDs earn nothing.
"""
from __future__ import annotations

import base64
import io
import math
import re
import shutil
import subprocess
import tempfile
from pathlib import Path
from typing import Any, Mapping, Sequence

import numpy as np
from PIL import Image

from .reference import role_layout_support
from .visual_reference import POLICY, image_features, matching_asset

SCHEMA = "gamebench.mode5.visual-reading.v1"
VERIFICATIONS = {
    "visual.referenced_assets": "asset-and-pixel-correspondence.v1",
    "visual.ui_feedback": "causal-visible-hud.v1",
    "visual.render_configuration": "actual-render-output.v1",
    "visual.animation_audio": "observed-feedback.v1",
    "visual.reference_layout": "runtime-role-layout.v1",
}


def pixels(value: str, *, size: tuple[int, int] | None = None) -> np.ndarray:
    if not isinstance(value, str) or len(value) > 1_000_000:
        raise ValueError("invalid visual pixel payload")
    raw = base64.b64decode(value, validate=True)
    with Image.open(io.BytesIO(raw)) as image:
        if image.format != "PNG" or image.width * image.height > 600_000:
            raise ValueError("visual pixel format/budget exceeded")
        if size and image.size != size:
            raise ValueError("unexpected visual image dimensions")
        image.load()
        return np.asarray(image.convert("RGB"), dtype=np.int16)


def contribution(before: str, removed: str, restored: str, policy: Mapping[str, Any],
                 *, size: tuple[int, int] = (96, 54)) -> dict[str, Any] | None:
    a, b, c = (pixels(value, size=size) for value in (before, removed, restored))
    threshold = policy["difference_channel_threshold"]
    drift = np.max(np.abs(a - c), axis=2) > threshold
    if float(drift.mean()) > policy["maximum_restore_drift_fraction"]:
        return None
    changed = np.max(np.abs(a - b), axis=2) > threshold
    changed &= ~drift
    yy, xx = np.where(changed)
    if len(xx) < policy["minimum_attributed_pixels"]:
        return None
    box = (int(xx.min()), int(yy.min()), int(xx.max()) + 1, int(yy.max()) + 1)
    crop = Image.fromarray(a[box[1]:box[3], box[0]:box[2]].astype(np.uint8))
    return {"pixels": int(len(xx)), "fraction": float(changed.mean()),
            "center": [float(xx.mean() / size[0]), float(1 - yy.mean() / size[1])],
            "box": list(box), "features": image_features(crop), "mask": changed}


def ocr_lines(encoded: str, binary: str) -> list[dict[str, Any]]:
    """Read actual displayed pixels, including IMGUI; never candidate text fields."""
    array = pixels(encoded, size=(480, 270)).astype(np.uint8)
    with tempfile.TemporaryDirectory(prefix="gb-controller-visual-ocr-") as directory:
        path = Path(directory) / "hud.png"
        Image.fromarray(array).resize((960, 540), Image.Resampling.LANCZOS).save(path)
        result = subprocess.run([binary, str(path), "stdout", "-l", "eng", "--psm", "11", "tsv"],
                                capture_output=True, text=True, timeout=15, check=False)
        if result.returncode:
            raise RuntimeError("independent OCR failed")
    groups: dict[tuple[str, ...], list[dict[str, Any]]] = {}
    for line in result.stdout.splitlines()[1:]:
        values = line.split("\t", 11)
        if len(values) != 12 or values[0] != "5" or not values[11].strip():
            continue
        try:
            confidence = float(values[10])
            if confidence < 65:
                continue
            word = {"text": values[11], "x": int(values[6]) / 2, "y": int(values[7]) / 2,
                    "w": int(values[8]) / 2, "h": int(values[9]) / 2}
            groups.setdefault(tuple(values[1:5]), []).append(word)
        except ValueError:
            continue
    return [{"text": " ".join(word["text"] for word in sorted(words, key=lambda row: row["x"])),
             "words": words} for words in groups.values()]


def displayed_value(lines: Sequence[Mapping[str, Any]], mask: np.ndarray, slot: str) -> float | None:
    labels = {"score": "score|points", "health": "health|hp", "progress": "progress"}.get(slot, re.escape(slot))
    pattern = re.compile(rf"(?i)\b(?:{labels})\b\s*[:=]?\s*(-?\d+(?:\.\d+)?)\b")
    for line in lines:
        match = pattern.search(str(line["text"]))
        if not match:
            continue
        # The recognized line must occupy pixels removed by the independent
        # UI intervention. Text in the background is not HUD evidence.
        words = line["words"]
        regions = []
        for word in words:
            x, y, w, h = (int(word[key]) for key in ("x", "y", "w", "h"))
            if x < 0 or y < 0 or x + w > mask.shape[1] or y + h > mask.shape[0]:
                continue
            regions.append(mask[y:y + h, x:x + w])
        if regions and any(region.size and float(region.mean()) >= 0.05 for region in regions):
            return float(match[1])
    return None


def measure_visual(snapshot: Mapping[str, Any], suite: Any, *, ocr_binary: str | None = None) -> dict[str, Any]:
    """Measure the same frozen twenty-criterion rubric; no alternative total."""
    reference = snapshot["visual_reference"]
    policy = reference["policy"]
    if policy != POLICY or reference.get("schema") != "gamebench.mode5.visual-reference.v1":
        raise ValueError("unrecognized frozen visual policy")
    expected = snapshot["obligations"]
    observations: list[dict[str, Any]] = []
    diagnostics: list[dict[str, Any]] = []
    complete = reference.get("complete") is True
    binary = ocr_binary or shutil.which("tesseract")
    positive = [suite.witness, *suite.hidden_behaviors]
    controls = [suite.matched_null]
    measurements: dict[str, list[dict[str, Any]]] = {}

    def add(criterion: str, obligation: str, ref: str, note: str) -> None:
        if obligation in expected[criterion]:
            observations.append({"criterion": criterion, "obligation": obligation,
                                 "level": "runtime_verified", "coverage": 1.0,
                                 "verification": VERIFICATIONS[criterion], "references": [ref], "note": note})

    for run in [*positive, *controls]:
        if run.status != "pass":
            continue
        captures = list(run.reading.get("visual_captures") or [])
        if not captures or len(captures) > policy["maximum_capture_count"]:
            complete = False
            diagnostics.append({"run_id": run.run_id, "status": "incomplete", "reason": "missing/over-budget controller captures"})
            continue
        seen: set[str] = set()
        measurements[run.run_id] = []
        for capture in captures:
            record = capture.get("record") or {}
            ident = str(capture.get("checkpoint_id") or "")
            ref = f"controller/visual/{run.run_id}/{ident}"
            if (not ident or ident in seen or capture.get("run_id") != run.run_id
                    or record.get("checkpoint_id") != ident
                    or record.get("schema") != "gamebench.mode5.visual-capture.v1"):
                raise ValueError("invalid/duplicated controller visual identity")
            seen.add(ident)
            measured: dict[str, Any] = {"reference": ref, "checkpoint_id": ident,
                                        "start_level": capture.get("start_level"),
                                        "state": capture.get("state") or {}, "roles": {}, "hud": {}}
            try:
                thumb = pixels(record["thumbnail_png"], size=(96, 54))
                if float(np.std(thumb)) < 2:
                    diagnostics.append({"reference": ref, "status": "failed", "reason": "blank/flat actual output"})
                role_records = record.get("roles") or []
                if len(role_records) > 24 or len({row.get("role") for row in role_records}) != len(role_records):
                    raise ValueError("invalid role capture census")
                for role in role_records:
                    name = role["role"]
                    if name not in expected["visual.referenced_assets"]:
                        raise ValueError("visual role outside frozen rubric")
                    effect = contribution(role["before_png"], role["removed_png"], role["restored_png"], policy)
                    if effect is None:
                        continue
                    sprites = role.get("sprite_pngs") or []
                    if len(sprites) > 4:
                        raise ValueError("asset sample budget exceeded")
                    descriptors = []
                    for encoded in sprites:
                        raw = base64.b64decode(encoded, validate=True)
                        with Image.open(io.BytesIO(raw)) as image:
                            if image.width * image.height > 4096:
                                raise ValueError("sprite pixel budget exceeded")
                            descriptors.append(image_features(image))
                    measured["roles"][name] = {"center": effect["center"], "features": effect["features"],
                                               "materials_valid": role.get("materials_valid") is True,
                                               "animation_state": str(role.get("animation_state") or ""),
                                               "particle_count": role.get("particle_count", 0), "sprites": descriptors}
                    if run in positive and any(matching_asset(value, reference["assets"].get(name, []), policy)
                                             for value in [effect["features"], *descriptors]):
                        add("visual.referenced_assets", name, ref, "Reference asset features correspond to a natively rendered role with removal/restoration pixel evidence.")
                if run in positive and measured["roles"] and float(np.std(thumb)) >= 2:
                    add("visual.render_configuration", "renderer", ref, "Actual attributed role pixels, not renderer presence.")
                    if record.get("camera_active") is True:
                        add("visual.render_configuration", "camera", ref, "Active native camera and actual nonflat output with attributed scene content.")
                    if all(row["materials_valid"] for row in measured["roles"].values()):
                        add("visual.render_configuration", "material", ref, "Loaded supported native materials and their actual pixel contribution.")
                ui = contribution(record["ui_before_png"], record["ui_removed_png"], record["ui_restored_png"], policy, size=(480, 270))
                if ui is not None:
                    if not binary:
                        diagnostics.append({"reference": ref, "status": "unmeasured", "reason": "independent OCR binary unavailable; UI obligation remains zero"})
                    else:
                        lines = ocr_lines(record["ui_before_png"], binary)
                        measured["ocr_lines"] = [line["text"] for line in lines]
                        for slot in expected["visual.ui_feedback"]:
                            value = displayed_value(lines, ui["mask"], slot)
                            observed = measured["state"].get("n", {}).get(slot)
                            if value is not None and isinstance(observed, (int, float)) and not isinstance(observed, bool) and math.isfinite(observed) and abs(value - observed) <= 1e-6:
                                measured["hud"][slot] = value
                pcm = base64.b64decode(record.get("audio_pcm16") or "", validate=True)
                if len(pcm) > 8192 or len(pcm) % 2:
                    raise ValueError("invalid captured PCM samples")
                signal = np.frombuffer(pcm, dtype="<i2").astype(float) / 32768
                measured["audio_rms"] = float(np.sqrt(np.mean(signal ** 2))) if len(signal) >= 64 else 0.0
                if run in positive and measured["audio_rms"] > 0.001 and np.std(signal) > 0.001:
                    add("visual.animation_audio", "audio", ref, "Non-silent variable PCM sampled from the actual listener mix, not AudioSource.isPlaying.")
            except (KeyError, OSError, ValueError, RuntimeError, subprocess.TimeoutExpired) as exc:
                complete = False
                diagnostics.append({"reference": ref, "status": "incomplete", "reason": str(exc)})
                continue
            measurements[run.run_id].append(measured)
    idle = {row["checkpoint_id"]: row for row in measurements.get(suite.matched_null.run_id, [])}
    used_layouts: set[str] = set()
    used_levels: set[int] = set()
    for run in positive:
        rows = measurements.get(run.run_id, [])
        for left, right in zip(rows, rows[1:]):
            ref = right["reference"]
            # A HUD must agree with state at BOTH observations and change.
            # A matched no-input run must not exhibit the same change.
            a, b = idle.get(left["checkpoint_id"]), idle.get(right["checkpoint_id"])
            for slot in expected["visual.ui_feedback"]:
                if (slot in left["hud"] and slot in right["hud"] and left["hud"][slot] != right["hud"][slot]
                        and a and b and slot in a["hud"] and slot in b["hud"]
                        and a["hud"][slot] == b["hud"][slot]):
                    add("visual.ui_feedback", slot, ref, "Displayed OCR values match controller-observed state before/after, differ with input, and remain stable in matched idle control.")
                    add("visual.animation_audio", "gameplay_feedback", ref, "Causal on-screen HUD feedback independently read from actual pixels.")
            for role in set(left["roles"]) & set(right["roles"]):
                first, last = left["roles"][role], right["roles"][role]
                difference = np.abs(np.asarray(first["features"]["luma"]) - np.asarray(last["features"]["luma"])).mean()
                sprites_changed = first["sprites"] != last["sprites"]
                if difference > 0.02 and (sprites_changed or (first["animation_state"] and first["animation_state"] != last["animation_state"])):
                    add("visual.animation_audio", "animation", ref, "Temporal role-local appearance changes corroborate native sprite/animation state; translation alone is insufficient.")
                if difference > 0.02 and first["particle_count"] != last["particle_count"] and max(first["particle_count"], last["particle_count"]) > 0:
                    add("visual.animation_audio", "particles", ref, "Native particle evolution and attributed temporal pixel changes.")
        for row in rows:
            level = row["start_level"]
            if isinstance(level, bool) or not isinstance(level, int) or level in used_levels:
                continue
            layout = {role: [data["center"]] for role, data in row["roles"].items()}
            if len(layout) < policy["minimum_layout_roles"]:
                continue
            for path, required in reference["layouts"].items():
                if path not in used_layouts and role_layout_support(required, layout):
                    add("visual.reference_layout", f"level:{level + 1}", row["reference"], f"Actual attributed screen-space role ordering corresponds to frozen reference {path}; not pixel/style similarity.")
                    used_layouts.add(path)
                    used_levels.add(level)
                    break
    return {"schema": SCHEMA, "complete": complete, "metric": "visual_implementation_correspondence",
            "policy": dict(policy), "observations": observations, "diagnostics": diagnostics,
            "measurements": measurements, "ocr_binary": Path(binary).name if binary else None,
            "vlm_used": False, "perceptual_similarity_measured": False}
