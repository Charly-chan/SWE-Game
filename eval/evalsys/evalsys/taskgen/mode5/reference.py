"""Evaluator-owned task applicability and implementation-neutral layout facts.

Read only the frozen reference material. Candidate assets never determine the
denominator. Static correspondence compares semantic role relations, not Godot
filenames, pixel coordinates, or an assumed need for every Unity effect class.
"""
from __future__ import annotations

from pathlib import Path
import math
import re
from typing import Any, Mapping


REFERENCE_SCHEMA = "gamebench.mode5.reference-facts.v1"
FEATURES = {
    "animation": r"\b(?:AnimatedSprite2D|AnimationPlayer|AnimationTree)\b|\.play\s*\(",
    "audio": r"\bAudioStreamPlayer(?:2D|3D)?\b",
    "particles": r"\b(?:GPUParticles|CPUParticles)(?:2D|3D)\b",
}


def collect_reference(root: Path | None, rubric: Mapping[str, Any]) -> dict[str, Any]:
    result: dict[str, Any] = {
        "schema": REFERENCE_SCHEMA, "feedback_requirements": ["gameplay_feedback"],
        "ui_requirements": ["hud_hierarchy", "outcome_ui", "interactive_ui"],
        "render_requirements": ["camera", "renderer"],
        "layouts": {}, "complete": root is not None and root.is_dir(),
    }
    if not result["complete"]:
        result["note"] = "Reference unavailable; generic game feedback, no mandatory particle/audio/Animator."
        return result
    from .source import strip_comments, mask_literals
    from .evidence import _link
    text_parts = []
    files = sorted(root.rglob("*.tscn")) + sorted(root.rglob("*.gd"))
    budget = 24 * 1024 * 1024
    if len(files) > 10000:
        result["complete"] = False
        return result
    for path in files:
        if any(_link(parent) for parent in (path, *path.parents)) or path.stat().st_size > budget:
            result["complete"] = False
            continue
        budget -= path.stat().st_size
        text = path.read_text(encoding="utf-8-sig", errors="replace")
        if path.suffix == ".gd":
            text = re.sub(r"#[^\n]*", "", mask_literals(text))
        text_parts.append(text)
        if path.suffix == ".tscn":
            layout = _godot_roles(text)
            if len(layout) >= 2:
                result["layouts"][path.relative_to(root).as_posix()] = layout
    combined = "\n".join(text_parts)
    required = [name for name, pattern in FEATURES.items() if re.search(pattern, combined)]
    result["feedback_requirements"] = required or ["gameplay_feedback"]
    result["material_required"] = bool(re.search(r"\b(?:ShaderMaterial|StandardMaterial3D|ORMMaterial3D)\b", combined))
    if result["material_required"]:
        result["render_requirements"].append("material")
    if not re.search(r"\b(?:Button|TextureButton|CheckButton)\b", combined):
        result["ui_requirements"].remove("interactive_ui")
    return result


def _godot_roles(text: str) -> dict[str, list[list[float]]]:
    sections = list(re.finditer(r"^\[node\s+([^\n]+)\]\s*$", text, re.M))
    out: dict[str, list[list[float]]] = {}
    for index, match in enumerate(sections):
        body = text[match.end():sections[index + 1].start() if index + 1 < len(sections) else len(text)]
        groups = re.search(r"groups=\[([^\]]*)\]", match[1])
        position = re.search(r"^position\s*=\s*Vector([23])\(([^)]+)\)", body, re.M)
        if not groups or not position:
            continue
        try:
            values = [float(value.strip()) for value in position[2].split(",")]
        except ValueError:
            continue
        if len(values) not in {2, 3} or not all(math.isfinite(value) for value in values):
            continue
        if len(values) == 2:
            values[1] *= -1  # Godot 2D screen Y to Unity's upward Y.
        for role in re.findall(r'"(gb_\w+)"', groups[1]):
            out.setdefault(role, []).append(values[:2])
    return out


def role_layout_support(reference: Mapping[str, Any], candidate: Mapping[str, Any]) -> bool:
    """Require nondegenerate relative role ordering, not exact coordinates.

    This is limited spatial support, not a runtime or visual-similarity score.
    At least two semantic roles and one informative axis must agree.
    """
    roles = sorted(set(reference) & set(candidate))
    if len(roles) < 2:
        return False
    for values in (reference, candidate):
        for role in roles:
            points = values[role]
            if not isinstance(points, (list, tuple)) or not points:
                return False
            for point in points:
                if not isinstance(point, (list, tuple)) or len(point) < 2:
                    return False
                if any(isinstance(value, bool) or not isinstance(value, (int, float))
                       or not math.isfinite(value) for value in point[:2]):
                    return False
    def centers(values: Mapping[str, Any]) -> dict[str, tuple[float, float]]:
        return {role: (sum(point[0] for point in values[role]) / len(values[role]),
                       sum(point[1] for point in values[role]) / len(values[role])) for role in roles}
    a, b = centers(reference), centers(candidate)
    checks = 0
    for i, role in enumerate(roles):
        for other in roles[i + 1:]:
            for axis in (0, 1):
                delta = a[role][axis] - a[other][axis]
                if abs(delta) <= 1e-6:
                    continue
                checks += 1
                if delta * (b[role][axis] - b[other][axis]) <= 0:
                    return False
    return checks > 0
