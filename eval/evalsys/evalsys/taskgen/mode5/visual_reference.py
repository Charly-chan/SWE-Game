"""Frozen, implementation-neutral visual requirements; no model or judge calls.

Fingerprints are lossy image features, NOT security checksums. Their thresholds
are versioned policy, never fitted to a candidate or historical model scores.
Godot resources are followed only inside the evaluator's reference project.
"""
from __future__ import annotations

import io
import re
from pathlib import Path
from typing import Any, Mapping

import numpy as np
from PIL import Image

_LANCZOS = getattr(getattr(Image, "Resampling", Image), "LANCZOS")
_BOX = getattr(getattr(Image, "Resampling", Image), "BOX")

SCHEMA = "gamebench.mode5.visual-reference.v1"
POLICY = {
    "version": "visual-correspondence-1",
    "difference_channel_threshold": 12,
    "minimum_attributed_pixels": 8,
    "maximum_restore_drift_fraction": 0.01,
    "maximum_asset_feature_distance": 0.12,
    "maximum_asset_color_distance": 0.18,
    "minimum_layout_roles": 2,
    "maximum_capture_count": 32,
}


def image_features(image: Image.Image) -> dict[str, Any]:
    """Crop transparent borders, preserve shape, and compare content not names."""
    rgba = image.convert("RGBA")
    box = rgba.getchannel("A").getbbox()
    if not box:
        raise ValueError("fully transparent visual asset")
    rgba = rgba.crop(box)
    if rgba.width * rgba.height > 16_000_000:
        raise ValueError("visual asset pixel budget exceeded")
    # Fixed background makes transparent sprites and their GPU readbacks
    # comparable. Letterboxing avoids squeezing distinct aspect ratios alike.
    rgba.thumbnail((48, 48), _LANCZOS)
    canvas = Image.new("RGBA", (48, 48), (127, 127, 127, 255))
    canvas.alpha_composite(rgba, ((48 - rgba.width) // 2, (48 - rgba.height) // 2))
    rgb = np.asarray(canvas.convert("RGB"), dtype=np.float64) / 255.0
    small = np.asarray(canvas.convert("L").resize((12, 12), _BOX), dtype=np.float64) / 255.0
    # Flat-color primitives do not establish asset correspondence, even if
    # their average color happens to match a sprite's dominant color.
    return {"luma": np.round(small.flatten(), 5).tolist(),
            "color": np.round(rgb.mean(axis=(0, 1)), 5).tolist(),
            "detail": round(float(np.std(small)), 5)}


def matching_asset(candidate: Mapping[str, Any], references: list[Mapping[str, Any]], policy: Mapping[str, Any]) -> bool:
    try:
        x = np.asarray(candidate["luma"], dtype=float)
        color = np.asarray(candidate["color"], dtype=float)
        if x.shape != (144,) or color.shape != (3,) or not np.isfinite(x).all() or not np.isfinite(color).all():
            return False
        if min(x.min(), color.min()) < 0 or max(x.max(), color.max()) > 1:
            return False
        for reference in references:
            y = np.asarray(reference["luma"], dtype=float)
            c = np.asarray(reference["color"], dtype=float)
            if y.shape != x.shape or c.shape != color.shape:
                continue
            detail = float(reference["detail"])
            if detail > 0.03 and float(candidate.get("detail", 0)) < detail * 0.4:
                continue
            if (float(np.abs(x - y).mean()) <= policy["maximum_asset_feature_distance"]
                    and float(np.abs(color - c).mean()) <= policy["maximum_asset_color_distance"]):
                return True
    except (KeyError, TypeError, ValueError):
        return False
    return False


def freeze_visual_reference(root: Path | None, rubric: Mapping[str, Any]) -> dict[str, Any]:
    """Traverse serialized instances and resource dependencies, with bounds.

    Unsupported dynamic resource expressions stay unmeasured; no arbitrary
    source PNG can become a player's reference simply because it is nearby.
    """
    result: dict[str, Any] = {"schema": SCHEMA, "policy": dict(POLICY), "assets": {},
                              "layouts": {}, "complete": bool(root and root.is_dir()), "issues": []}
    if not result["complete"]:
        return result
    assert root is not None
    root = root.resolve()
    roles = set(rubric.get("required_groups") or [])
    texts: dict[str, str] = {}
    feature_cache: dict[str, dict[str, Any] | None] = {}
    budget = 24 * 1024 * 1024

    def safe(relative: str) -> Path:
        path = root / relative
        if path.is_symlink() or any(parent.is_symlink() for parent in path.parents):
            raise ValueError("linked reference resource")
        if not path.resolve().is_relative_to(root):
            raise ValueError("reference resource escapes project")
        return path

    def text(relative: str) -> str:
        nonlocal budget
        if relative not in texts:
            path = safe(relative)
            if not path.is_file():
                return ""
            size = path.stat().st_size
            if size > 2_000_000 or size > budget or len(texts) >= 5000:
                raise ValueError("reference text budget exceeded")
            budget -= size
            texts[relative] = path.read_text(encoding="utf-8-sig", errors="replace")
        return texts[relative]

    def feature(relative: str) -> dict[str, Any] | None:
        if relative not in feature_cache:
            if len(feature_cache) >= 2000:
                raise ValueError("reference image budget exceeded")
            path = safe(relative)
            value = None
            if path.is_file() and path.stat().st_size <= 8_000_000:
                try:
                    with Image.open(path) as image:
                        if image.width * image.height <= 16_000_000:
                            value = {"path": relative, **image_features(image)}
                except (ValueError, OSError):
                    pass
            feature_cache[relative] = value
        return feature_cache[relative]

    def dependencies(relative: str, seen: set[str]) -> list[str]:
        if relative in seen or len(seen) > 256:
            return []
        seen = seen | {relative}
        if Path(relative).suffix.lower() in {".png", ".jpg", ".jpeg", ".webp"}:
            return [relative]
        if Path(relative).suffix.lower() not in {".tscn", ".tres", ".gd"}:
            return []
        return [asset for child in re.findall(r'"res://([^"\n]+)"', text(relative))
                for asset in dependencies(child, seen)]

    def scene(relative: str, inherited: set[str], offset: tuple[float, float], stack: tuple[str, ...], layout: dict[str, list[list[float]]]) -> None:
        if relative in stack or len(stack) >= 24:
            return
        source = text(relative)
        external = {match[2]: match[1] for match in re.finditer(
            r'^\[ext_resource\s+[^\n]*path="res://([^"\n]+)"[^\n]*id="([^"\n]+)"[^\n]*\]', source, re.M)}
        sections = list(re.finditer(r'^\[node\s+([^\n]+)\]\s*$', source, re.M))
        node_roles: dict[str, set[str]] = {}
        positions: dict[str, tuple[float, float]] = {}
        for index, match in enumerate(sections):
            body = source[match.end():sections[index + 1].start() if index + 1 < len(sections) else len(source)]
            name = re.search(r'name="([^"]+)"', match[1])
            parent = re.search(r'parent="([^"]+)"', match[1])
            if not name:
                continue
            parent_path = parent[1] if parent else ""
            node_path = "." if not parent else (name[1] if parent_path == "." else parent_path + "/" + name[1])
            own = set(re.findall(r'"(gb_\w+)"', (re.search(r'groups=\[([^\]]*)\]', match[1]) or ["", ""])[1])) & roles
            active_roles = own or node_roles.get(parent_path, inherited)
            node_roles[node_path] = active_roles
            origin = positions.get(parent_path, offset)
            position = re.search(r'^position\s*=\s*Vector[23]\(([^)]+)\)', body, re.M)
            xy = origin
            if position:
                values = [float(value.strip()) for value in position[1].split(",")]
                xy = origin[0] + values[0], origin[1] - values[1]
            positions[node_path] = xy
            for role in own:
                layout.setdefault(role, []).append(list(xy))
            instance = re.search(r'instance=ExtResource\("([^"]+)"\)', match[1])
            if instance and instance[1] in external:
                scene(external[instance[1]], active_roles, xy, (*stack, relative), layout)
            if active_roles:
                referenced = [external[ident] for ident in re.findall(r'ExtResource\("([^"]+)"\)', body) if ident in external]
                # SpriteFrames are often embedded subresources. Only include
                # their dependencies when this role node actually uses one.
                if re.search(r'(?:texture|sprite_frames)\s*=\s*SubResource', body):
                    referenced += list(external.values())
                for child in referenced:
                    for asset in sorted(set(dependencies(child, set()))):
                        value = feature(asset)
                        if value:
                            for role in active_roles:
                                result["assets"].setdefault(role, {})[asset] = value
    try:
        for path in sorted(root.rglob("*.tscn")):
            relative = path.relative_to(root).as_posix()
            layout: dict[str, list[list[float]]] = {}
            scene(relative, set(), (0.0, 0.0), (), layout)
            if len(layout) >= 2:
                result["layouts"][relative] = layout
        result["assets"] = {role: list(values.values()) for role, values in result["assets"].items()}
    except (OSError, ValueError) as exc:
        result["complete"] = False
        result["issues"].append(str(exc))
    return result
