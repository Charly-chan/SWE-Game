"""Trusted Unity-verifier adapter to the fixed Mode 5 release proxy contract.

Use inside the independent evaluator BEFORE candidate execution to snapshot
static facts, then finish with controller-generated build/runtime items. No
candidate-authored report.json or model identity is accepted by this interface.
Conservative replacements for unprovable source claims are explicit zeros,
not global penalties and not model-specific assumptions.
"""
from __future__ import annotations

import json
from pathlib import Path
import re
from typing import Any, Mapping, Sequence

from .evidence import Graph, Limits, collect_reference_graph
from .score import CRITERIA, Observation, REGISTRY_VERSION, score_evidence
from .source import inspect_sources, supports_predicate
from .reference import collect_reference, role_layout_support
from .visual_reference import freeze_visual_reference


SNAPSHOT_SCHEMA = "gamebench.mode5.controller-static.v1.visual1"
RENDER_CLASSES = {20, 23, 25, 96, 120, 212}
FEEDBACK_CLASSES = {82, 95, 198}


def frozen_obligations(rubric: Mapping[str, Any], reference: Mapping[str, Any] | None = None) -> dict[str, list[str]]:
    """Freeze denominators from evaluator reference requirements, not output.

    A check declared unmeasurable remains a missing obligation rather than
    disappearing from mechanics. References to roles/actions are vocabulary
    requirements, never original engine paths or implementation names.
    """
    actions = _ids(rubric.get("required_actions"))
    checks = _ids([row.get("id") for row in rubric.get("mechanic_checks") or []])
    roles = _ids(rubric.get("required_groups"))
    slots = _ids(rubric.get("required_numeric_slots"))
    if not actions or not checks or not roles:
        raise ValueError("release needs a complete evaluator-owned reference rubric")
    count = rubric.get("min_levels")
    if isinstance(count, bool) or not isinstance(count, int) or not 1 <= count <= 100:
        raise ValueError("invalid reference level count")
    endings = ["success", "reset"]
    if rubric.get("require_failure_ending"):
        endings.append("failure")
    if "gb_checkpoint" in roles:
        endings.append("checkpoint")
    reference = reference or collect_reference(None, rubric)
    interactions = [row["id"] for row in rubric.get("mechanic_checks") or []
                    if re.search(r"(?:count_delta|numeric_delta|overlap)\(", str((row.get("observable") or {}).get("predicate") or ""))]
    progression = [row["id"] for row in rubric.get("mechanic_checks") or []
                   if re.search(r"numeric(?:_delta)?\((?:progress|score)\)|levels_visited\(\)|whole_game_clear\(\)", str((row.get("observable") or {}).get("predicate") or ""))]
    result = {
        "mechanics.input_mapping": actions,
        "mechanics.core_mechanics": checks,
        "mechanics.interactions": interactions or ["state_interaction"],
        "mechanics.outcomes": endings,
        "playability.ops": ["valid_nonidle_ops"],
        "playability.input_chain": ["causal_input_chain"],
        "playability.progression": progression or ["intermediate_progression"],
        "playability.final_goal": ["causal_whole_game_clear"],
        "playability.scene_flow": ["pause", "reset", "scene_transition"],
        "structure.scenes_entities": [f"level:{index}" for index in range(1, count + 1)] + roles,
        "structure.referenced_content": roles,
        "structure.ui_hierarchy": reference["ui_requirements"],
        "visual.referenced_assets": roles,
        "visual.ui_feedback": slots or ["visible_state_feedback"],
        "visual.render_configuration": reference["render_requirements"],
        "visual.animation_audio": reference["feedback_requirements"],
        "visual.reference_layout": [f"level:{index}" for index in range(1, count + 1)],
        "stability.packages_build": ["package_manifest", "editor_version", "independent_build"],
        "stability.lifecycle": ["cold_start", "reset", "scene_transition"],
        "stability.resource_safety": ["serialized_references", "runtime_exceptions", "runtime_errors"],
    }
    assert set(result) == set(CRITERIA)
    return result


def snapshot_static(
    project: Path, *, baseline: Path, rubric: Mapping[str, Any],
    interface: Mapping[str, Any], ops_valid: bool, ops_nonidle: bool,
    limits: Limits = Limits(), reference_project: Path | None = None,
) -> dict[str, Any]:
    """Snapshot controller evidence; caller persists it outside candidate root.

    These strict checks replace keyword-based source inference where an actual
    mechanism/consumer/UI fidelity cannot be established by serialized facts.
    Such unsupported obligations retain zero until independently verified.
    """
    reference = collect_reference(reference_project, rubric)
    visual_reference = freeze_visual_reference(reference_project, rubric)
    expected = frozen_obligations(rubric, reference)
    scenes = [str(row.get("scene") or "") for row in interface.get("levels") or []]
    entry = interface.get("entry_scene")
    if isinstance(entry, str) and entry:
        scenes.append(entry)
    graph = collect_reference_graph(project, scenes, baseline_project=baseline, limits=limits)
    sources = inspect_sources(project, graph, limits=limits)
    observed: dict[str, list[Observation]] = {name: [] for name in CRITERIA}

    def add(name: str, ident: str, ref: str, *, level: str = "static_supported", note: str = "") -> None:
        observed[name].append(Observation(ident, level, (ref,), note))

    for check in rubric.get("mechanic_checks") or []:
        refs = supports_predicate(str((check.get("observable") or {}).get("predicate") or ""), sources)
        for name in ("mechanics.core_mechanics", "mechanics.interactions", "playability.progression"):
            if refs and check["id"] in expected[name]:
                add(name, check["id"], refs[0], note="Reachable source supports this specific predicate; execution is not verified.")
    for unit, refs in sources.outcomes.items():
        if unit in expected["mechanics.outcomes"]:
            add("mechanics.outcomes", unit, refs[0])
    for unit, refs in sources.flow.items():
        add("playability.scene_flow", unit, refs[0])
        if unit in expected["stability.lifecycle"]:
            add("stability.lifecycle", unit, refs[0])

    if ops_valid and ops_nonidle:
        add("playability.ops", "valid_nonidle_ops", "controller/items/ops_valid+ops_not_idle",
            note="Artifact validity does not establish input causality or completion.")
    # The mapping criterion asks for a configured mapping, not a functioning
    # consumer. The latter is independently scored under input_chain.
    supported = set(interface.get("supported_actions") or [])
    input_path = project / "Assets/GameBenchmarkSDK/GameBenchmarkInput.inputactions"
    actions = _json(input_path, limits.file_bytes)
    for action_map in actions.get("maps") or []:
        if not isinstance(action_map, Mapping):
            continue
        declared = {row.get("name") for row in action_map.get("actions") or []
                    if isinstance(row, Mapping)}
        bound = {row.get("action") for row in action_map.get("bindings") or []
                 if isinstance(row, Mapping) and str(row.get("path") or "").startswith("<")}
        for action in expected["mechanics.input_mapping"]:
            if action in declared & bound & supported:
                add("mechanics.input_mapping", action, str(input_path.relative_to(project)),
                    note="Configured Input Actions mapping; not consumer verification.")
                if action in sources.consumed_actions:
                    add("playability.input_chain", "causal_input_chain", sources.consumed_actions[action][0],
                        note="Static action-binding-consumer support, not actual delivery.")
    levels = interface.get("levels") or []
    used_scenes: set[str] = set()
    for index, row in enumerate(levels[:int(rubric["min_levels"])], 1):
        scene = row.get("scene")
        if scene not in used_scenes and scene in graph.authored_files and graph.documents.get(scene):
            add("structure.scenes_entities", f"level:{index}", scene)
            used_scenes.add(scene)
    entity_guid = _meta_guid(baseline / "Assets/GameBenchmarkSDK/GBEntity.cs.meta")
    role_refs: dict[str, list[tuple[str, dict[str, Any]]]] = {}
    for scene, documents in graph.documents.items():
        if scene not in graph.authored_files:
            continue
        for document in documents:
            role = document["scalars"].get("role")
            if entity_guid and document["script_guid"] == entity_guid and role in rubric["required_groups"]:
                role_refs.setdefault(role, []).append((scene, document))
        if any(document["class_id"] == 223 for document in documents) and any(
            document["class_id"] == 224 for document in documents
        ):
            add("structure.ui_hierarchy", "hud_hierarchy", scene,
                note="Canvas and RectTransform hierarchy only; no outcome/interaction claim.")
        native = {document["class_id"] for document in documents}
        if 20 in native:
            add("visual.render_configuration", "camera", scene)
        if native & (RENDER_CLASSES - {20}):
            add("visual.render_configuration", "renderer", scene)
        for kind, label in ((95, "animation"), (82, "audio"), (198, "particles")):
            if kind in native and label in expected["visual.animation_audio"]:
                add("visual.animation_audio", label, scene,
                    note="Configured component; rendered/audio effect not verified.")
    used_visual_assets: set[str] = set()
    for role, entries in sorted(role_refs.items()):
        add("structure.scenes_entities", role, entries[0][1]["audit_reference"])
        for scene, marker in entries:
            owner = marker["owner_file_id"]
            documents = graph.documents[scene]
            rendered = any(document["owner_file_id"] == owner
                           and document["class_id"] in RENDER_CLASSES - {20}
                           for document in documents)
            if rendered:
                add("structure.referenced_content", role, marker["audit_reference"],
                    note="Required semantic entity has an attached render component.")
                render_docs = [document for document in documents
                               if document["owner_file_id"] == owner
                               and document["class_id"] in RENDER_CLASSES - {20}]
                assets = sorted({asset for document in render_docs
                                 for asset in document["referenced_assets"]
                                 if asset in graph.authored_files
                                 and Path(asset).suffix.lower() in {".png", ".jpg", ".jpeg", ".tga", ".psd", ".mat", ".fbx", ".obj"}})
                for asset in assets:
                    if asset not in used_visual_assets and _valid_visual_asset(project / asset, graph, asset, limits):
                        used_visual_assets.add(asset)
                        add("visual.referenced_assets", role, asset,
                            note="Unique asset referenced by the required entity's renderer; appearance not verified.")
                        break
    material_refs = [value for value in graph.authored_files if value.endswith(".mat")]
    if material_refs and "material" in expected["visual.render_configuration"]:
        add("visual.render_configuration", "material", sorted(material_refs)[0])
    constructed = sources.construction
    ui_types = {"Text", "TMP_Text", "TextMeshProUGUI", "Slider", "Image", "Button"}
    has_ui = any(row["class_id"] == 223 for docs in graph.documents.values() for row in docs)
    has_ui |= "Canvas" in constructed and bool(ui_types & set(constructed))
    if "Canvas" in constructed and ui_types & set(constructed):
        add("structure.ui_hierarchy", "hud_hierarchy", constructed["Canvas"][0])
    if has_ui:
        for unit, refs in sources.ui.items():
            if unit in expected["structure.ui_hierarchy"]:
                add("structure.ui_hierarchy", unit, refs[0])
            if unit in expected["visual.ui_feedback"]:
                add("visual.ui_feedback", unit, refs[0])
        if sources.ui and "gameplay_feedback" in expected["visual.animation_audio"]:
            add("visual.animation_audio", "gameplay_feedback", next(iter(sources.ui.values()))[0])
    for component, unit in (("Camera", "camera"), ("SpriteRenderer", "renderer"),
                            ("MeshRenderer", "renderer"), ("Animator", "animation"),
                            ("AudioSource", "audio"), ("ParticleSystem", "particles")):
        name = "visual.render_configuration" if unit in {"camera", "renderer"} else "visual.animation_audio"
        if component in constructed and unit in expected[name]:
            add(name, unit, constructed[component][0])
    for role, refs in sources.dynamic_roles.items():
        if role in expected["structure.scenes_entities"]:
            add("structure.scenes_entities", role, refs[0], note="Reachable explicit GBEntity construction.")
    used_layouts: set[str] = set()
    used_candidate_scenes: set[str] = set()
    for index, row in enumerate(levels[:int(rubric["min_levels"])], 1):
        scene = str(row.get("scene") or "")
        if scene in used_candidate_scenes:
            continue
        layout = _unity_layout(graph.documents.get(row.get("scene"), []))
        for path, ref_layout in reference["layouts"].items():
            if path not in used_layouts and role_layout_support(ref_layout, layout):
                used_layouts.add(path)
                used_candidate_scenes.add(scene)
                add("visual.reference_layout", f"level:{index}", str(row["scene"]),
                    note=f"Static semantic-role ordering corresponds to frozen reference {path}; not pixel similarity.")
                break
    package = _json(project / "Packages/manifest.json", limits.file_bytes)
    if isinstance(package.get("dependencies"), dict) and package["dependencies"]:
        add("stability.packages_build", "package_manifest", "Packages/manifest.json")
    version = project / "ProjectSettings/ProjectVersion.txt"
    if _regular(version) and version.stat().st_size <= limits.file_bytes and re.search(
        r"^m_EditorVersion:\s*6000\.3\.23f1\s*$", version.read_text(encoding="utf-8-sig"), re.M,
    ):
        add("stability.packages_build", "editor_version", "ProjectSettings/ProjectVersion.txt")
    # Clean reference resolution can support static resource integrity only
    # if actual authored references were inspected, not an empty scaffold.
    if (graph.complete and graph.authored_files and not graph.unresolved_references
            and any(row["class_id"] == 1 for docs in graph.documents.values() for row in docs)):
        add("stability.resource_safety", "serialized_references", "controller/static_graph",
            note="No unresolved serialized asset reference; dynamic loads not checked.")
    return {
        "schema": SNAPSHOT_SCHEMA, "registry_version": REGISTRY_VERSION,
        "obligations": expected, "graph": graph.to_dict(),
        "input_independent_checks": [row["id"] for row in rubric.get("mechanic_checks") or []
                                     if (row.get("observable") or {}).get("requires_player_action") is False],
        "source_facts": sources.to_dict(), "reference_facts": reference,
        "visual_reference": visual_reference,
        "inspection_complete": graph.complete and (
            sources.complete or all(issue.startswith("unbalanced method body:") for issue in sources.issues)
        ) and (
            reference_project is None or (reference["complete"] is True and visual_reference["complete"] is True)),
        "observations": {name: [_observation_dict(row) for row in rows]
                         for name, rows in observed.items()},
        "strict_replacements": [
            "Static mechanics require reachable operations supporting a specific reference predicate, not keywords.",
            "Goal requires causal whole-game clear; no declared success credit.",
            "UI, feedback and layout require connected artifact evidence and frozen reference applicability.",
            "Static flow support is discounted; only calibrated execution can earn runtime credit.",
        ],
    }


def finish_scoring(
    snapshot: Mapping[str, Any], *, items: Sequence[Mapping[str, Any]],
    engine: Mapping[str, Any],
) -> dict[str, Any]:
    """Combine a private controller snapshot with trusted runtime verifiers.

    The caller provides in-memory controller items, not arbitrary candidate
    report files. Stored snapshots may be replayed only from retained evaluator
    output, whose ownership is established by the surrounding Docker workflow.
    """
    if snapshot.get("schema") != SNAPSHOT_SCHEMA or snapshot.get("registry_version") != REGISTRY_VERSION:
        raise ValueError("wrong controller evidence snapshot protocol")
    expected = snapshot["obligations"]
    observed = {
        name: [Observation(row["obligation"], row["level"], tuple(row["references"]), row.get("note", ""), row.get("coverage", 1.0))
               for row in rows]
        for name, rows in snapshot["observations"].items()
    }
    by_id = {str(row["id"]): row for row in items}
    if len(by_id) != len(items):
        raise ValueError("duplicate verifier item identity")

    def add(name: str, ident: str, ref: str, level: str = "runtime_verified", note: str = "") -> None:
        observed[name].append(Observation(ident, level, (ref,), note))

    integrity = all(by_id.get(name, {}).get("verdict") == "passed" for name in (
        "unity_sdk_integrity", "no_eval_smuggling", "no_bundled_godot_runtime", "unity_anti_grant_static",
    ))
    build = engine.get("build") or {}
    candidate_build_failed = build.get("status") == "fail" and build.get("attribution") == "submission"
    infra_ok = build.get("status") == "pass" or candidate_build_failed
    environment = build.get("environment") or {}
    if environment and environment.get("score_eligible") is not True:
        infra_ok = False
    runtime = engine.get("runtime") or {}
    witness = runtime.get("witness") or {}
    null = runtime.get("matched_null") or {}
    positive_runs = ([witness] if witness else []) + list(runtime.get("hidden_behaviors") or [])
    run_ids = [run.get("run_id") for run in positive_runs]
    if any(not isinstance(ident, str) or not ident for ident in run_ids) or len(run_ids) != len(set(run_ids)):
        raise ValueError("invalid or duplicated positive run identity")
    measured_runs = [run for run in positive_runs if run.get("status") in {"pass", "fail"}
                     and int((run.get("reading") or {}).get("row_count") or 0) > 0]
    if build.get("status") == "pass":
        add("stability.packages_build", "independent_build", "controller/build", "editor_verified")
        # Missing collector coverage or required runtime/control observations
        # are evaluator gaps, not conveniently discarded candidate failures.
        infra_ok &= bool(positive_runs) and bool(null)
        infra_ok &= all(run.get("status") != "inconclusive"
                        for run in [*positive_runs, null])
        # Missing optional geometry observations do not erase artifact proxy
        # points. Only actual environment/transport failures withhold ranking;
        # unavailable dynamic obligations remain explicit zero evidence.
    elif candidate_build_failed:
        add("stability.packages_build", "independent_build", "controller/build", "failed")
    graph = snapshot.get("graph") or {}
    infra_ok &= graph.get("complete") is True
    infra_ok &= snapshot.get("inspection_complete", True) is True
    visual = engine.get("mode5_visual")
    if build.get("status") == "pass":
        if not isinstance(visual, Mapping) or visual.get("schema") != "gamebench.mode5.visual-reading.v1":
            infra_ok = False
        else:
            infra_ok &= visual.get("complete") is True
            for row in visual.get("observations") or []:
                name = row["criterion"]
                if not name.startswith("visual.") or name not in expected:
                    raise ValueError("visual measurement outside rubric")
                observed[name].append(Observation(row["obligation"], row["level"], tuple(row["references"]),
                                                 row.get("note", ""), row.get("coverage", 1.0), row.get("verification")))
    # A trigger alone does not verify the effect. Controller records explicitly
    # reached checks; static evidence is admitted separately with its discount.
    mechanic = by_id.get("unity_mechanic_trace") or {}
    readings = (mechanic.get("evidence") or {}).get("runs") or []
    measured_ids = {run["run_id"] for run in measured_runs}
    reached = {str(check) for row in readings if row.get("run_id") in measured_ids
               for check in row.get("reached") or []}
    control_reached = set((mechanic.get("evidence") or {}).get("matched_null_reached") or [])
    control_measured = null.get("status") == "pass" and int((null.get("reading") or {}).get("row_count") or 0) > 0
    for check in expected["mechanics.core_mechanics"]:
        if check in reached and measured_runs:
            if not control_measured and check not in snapshot.get("input_independent_checks", []):
                continue
            if check in control_reached and check not in snapshot.get("input_independent_checks", []):
                continue
            reference = f"controller/items/unity_mechanic_trace:{check}"
            for name in ("mechanics.core_mechanics", "mechanics.interactions", "playability.progression"):
                if check in expected[name]:
                    add(name, check, reference)
    dispatch = by_id.get("unity_input_dispatch") or {}
    if dispatch.get("verdict") == "passed" and measured_runs:
        add("playability.input_chain", "causal_input_chain", "controller/items/unity_input_dispatch")
    elif dispatch.get("verdict") in {"failed", "malformed"}:
        add("playability.input_chain", "causal_input_chain", "controller/items/unity_input_dispatch", "failed")
    causal = by_id.get("causal_witness") or {}
    causal_data = causal.get("evidence") or {}
    if (witness.get("status") == "pass" and null.get("status") == "pass"
            and witness.get("reached") is True and null.get("reached") is False
            and dispatch.get("verdict") == "passed" and measured_runs and control_measured
            and causal.get("verdict") == "passed"
            and by_id.get("unity_auto_win_ready", {}).get("verdict") == "passed"
            and by_id.get("unity_counterfactual", {}).get("verdict") not in {"failed", "malformed"}):
        add("playability.final_goal", "causal_whole_game_clear", "controller/witness+matched-null")
        add("mechanics.outcomes", "success", "controller/witness+matched-null")
    live = engine.get("mode5_runtime") or {}
    for ident in live.get("runs") or []:
        if ident.get("run_id") not in measured_ids:
            continue
        ref = "controller/runtime/" + ident["run_id"]
        for role in ident.get("roles") or []:
            if role in expected["structure.scenes_entities"]:
                add("structure.scenes_entities", role, ref,
                    note="Independent observer census confirms a live semantic role, including procedural content.")
        for role in ident.get("rendered_roles") or []:
            if role in expected["structure.referenced_content"]:
                add("structure.referenced_content", role, ref,
                    note="Observer confirms a nonempty enabled native renderer on this semantic entity; not appearance fidelity.")
        if ident.get("failure_with_health_zero") and "failure" in expected["mechanics.outcomes"]:
            add("mechanics.outcomes", "failure", ref, note="Failure event corroborated by observed nonpositive health.")
    # Observing lv=1 then lv=2 does not prove reset or a causal scene flow:
    # automatic advancement can manufacture that sequence. Existing generic
    # progression items therefore do not populate these flow obligations.
    # A cold start is not a restart, and no-errors is not absence of null risks.
    # Keep the criterion's three frozen obligations. Cold-start and error
    # coverage use ALL scheduled positive runs, including failed runs. Merely
    # adding many cold runs cannot dilute missing reset/scene obligations.
    if positive_runs:
        lifecycle_passed = exceptions_passed = errors_passed = 0
        references = []
        for run in positive_runs:
            ident = str(run["run_id"])
            reading = run.get("reading") or {}
            rows = int(reading.get("row_count") or 0)
            completed = run.get("status") == "pass" and rows > 0
            ref = "controller/runtime/" + ident
            references.append(ref)
            lifecycle_passed += int(completed)
            error_count = int(reading.get("error_count") or 0)
            examples = reading.get("error_examples") or []
            exception = any("exception" in str(value).lower() or "stack overflow" in str(value).lower()
                            or "assert" in str(value).lower() for value in examples)
            # Truncated error lists cannot prove absence of exceptions.
            exceptions_clear = not exception and error_count == len(examples)
            exceptions_passed += int(completed and exceptions_clear)
            errors_passed += int(completed and error_count == 0)
        for name, ident, numerator in (
            ("stability.lifecycle", "cold_start", lifecycle_passed),
            ("stability.resource_safety", "runtime_exceptions", exceptions_passed),
            ("stability.resource_safety", "runtime_errors", errors_passed),
        ):
            observed[name].append(Observation(
                ident, "runtime_verified" if numerator else "failed", tuple(references),
                f"{numerator}/{len(positive_runs)} scheduled positive runs verified; failures retained.",
                numerator / len(positive_runs),
            ))
    # Guard against caller-side mutation: the serialized snapshot stays equal
    # before and after scoring; no candidate or report renderer can change it
    # by retaining a reference to our returned diagnostic record.
    import copy
    result = score_evidence(expected, observed, runtime_attempted=bool(runtime),
                            infrastructure_complete=bool(infra_ok), integrity_passed=integrity)
    result["static_evidence"] = copy.deepcopy(dict(snapshot))
    return result


def _unity_layout(documents: Sequence[Mapping[str, Any]]) -> dict[str, list[list[float]]]:
    result: dict[str, list[list[float]]] = {}
    for marker in documents:
        role = marker.get("scalars", {}).get("role")
        if not role or not str(marker.get("script_path") or "").endswith("/GBEntity.cs"):
            continue
        for row in documents:
            if row["class_id"] not in {4, 224} or row.get("owner_file_id") != marker.get("owner_file_id"):
                continue
            # Relative local coordinates are comparable only for scene-root transforms.
            if row.get("parent_file_id") != 0:
                continue
            position = row.get("scalars", {}).get("m_LocalPosition", "")
            match = re.fullmatch(r"\{x:\s*([-+\d.eE]+),\s*y:\s*([-+\d.eE]+),\s*z:\s*([-+\d.eE]+)\}", position)
            if match:
                import math
                values = [float(match[1]), float(match[2])]
                if all(math.isfinite(value) for value in values):
                    result.setdefault(role, []).append(values)
    return result


def snapshot_runtime(suite: Any) -> dict[str, Any]:
    """Retain minimal facts from in-memory observer rows before compression.

    Never load a submission-provided JSON census. Role presence proves structure,
    not rendering, mechanics, interaction or goal completion.
    """
    import math
    runs = []
    for run in [suite.witness, *suite.hidden_behaviors]:
        rows = [row for row in run.reading.get("rows") or [] if isinstance(row, Mapping)]
        roles = sorted({role for row in rows for role, count in (row.get("g") or {}).items()
                        if isinstance(count, int) and not isinstance(count, bool) and count > 0})
        rendered_roles = sorted({role for row in rows for role, count in (row.get("rc") or {}).items()
                                 if isinstance(count, int) and not isinstance(count, bool) and count > 0})
        health_zero = any(isinstance((row.get("n") or {}).get("health"), (float, int))
                          and not isinstance((row.get("n") or {}).get("health"), bool)
                          and math.isfinite(row["n"]["health"]) and row["n"]["health"] <= 0 for row in rows)
        failed = any(event.get("kind") == "outcome_failure" for event in run.reading.get("events") or [])
        runs.append({"run_id": run.run_id, "roles": roles, "rendered_roles": rendered_roles,
                     "failure_with_health_zero": failed and health_zero})
    return {"schema": "gamebench.mode5.controller-runtime.v1", "runs": runs}


def _ids(values: Any) -> list[str]:
    if not isinstance(values, (list, tuple)):
        return []
    if any(not isinstance(value, str) or not value for value in values):
        raise ValueError("invalid rubric identifier")
    if len(values) != len(set(values)):
        raise ValueError("duplicate rubric identifier")
    return list(values)


def _regular(path: Path) -> bool:
    from .evidence import _link
    if not path.is_file():
        return False
    # A regular-looking file may still sit below an Assets/Packages junction.
    # Check every existing ancestor before any read, not only the leaf.
    return all(not _link(parent) for parent in (path, *path.parents))


def _json(path: Path, limit: int) -> dict[str, Any]:
    if not _regular(path) or path.stat().st_size > limit:
        return {}
    try:
        value = json.loads(path.read_text(encoding="utf-8-sig"))
    except (OSError, ValueError, UnicodeError):
        return {}
    return value if isinstance(value, dict) else {}


def _meta_guid(path: Path) -> str | None:
    if not _regular(path) or path.stat().st_size > 8192:
        return None
    match = re.search(r"^guid:\s*([0-9a-fA-F]{32})\s*$", path.read_text(encoding="utf-8-sig"), re.M)
    return match.group(1).lower() if match else None


def _valid_visual_asset(path: Path, graph: Graph, relative: str, limits: Limits) -> bool:
    if not _regular(path) or not 0 < path.stat().st_size <= limits.file_bytes:
        return False
    if path.suffix.lower() == ".mat":
        return any(row["class_id"] == 21 for row in graph.documents.get(relative, []))
    if path.suffix.lower() in {".png", ".jpg", ".jpeg", ".tga", ".psd"}:
        from PIL import Image
        import warnings
        try:
            with warnings.catch_warnings():
                warnings.simplefilter("error", Image.DecompressionBombWarning)
                with Image.open(path) as image:
                    if image.width * image.height > 16000000:
                        return False
                    image.verify()
            return True
        except (OSError, ValueError, SyntaxError, Image.DecompressionBombError, Image.DecompressionBombWarning):
            return False
    # A mesh suffix/header alone cannot establish a valid imported mesh.
    return False


def _observation_dict(row: Observation) -> dict[str, Any]:
    return {"obligation": row.obligation, "level": row.level,
            "references": list(row.references), "note": row.note, "coverage": row.coverage,
            "verification": row.verification}
