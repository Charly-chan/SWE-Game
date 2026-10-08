


from __future__ import annotations

import gzip
import hashlib
import json
import re
import zipfile
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Mapping, Sequence

from .unity_suite_compiler import CALIBRATION_SCHEMA, suite_content_digest


_SHA256 = re.compile(r"^sha256:[0-9a-f]{64}$")


def _artifact_bytes(raw_path: str, artifact_root: Path) -> tuple[bytes | None, list[str]]:


    archive_path, separator, member = raw_path.partition("#")
    relative = Path(archive_path)
    if relative.is_absolute() or ".." in relative.parts:
        return None, ["path must stay under the evidence root"]
    root = artifact_root.resolve()
    target = (root / relative).resolve()
    try:
        target.relative_to(root)
    except ValueError:
        return None, ["path escapes the evidence root"]
    if not target.is_file():
        return None, [f"artifact is missing: {archive_path}"]
    if not separator:
        payload = target.read_bytes()
        if target.suffix.lower() == ".gz":
            try:
                payload = gzip.decompress(payload)
            except (OSError, EOFError):
                return None, [f"gzip artifact is invalid: {raw_path}"]
        return payload, []
    member_path = Path(member)
    if (
        not member or member_path.is_absolute() or ".." in member_path.parts
        or target.suffix.lower() != ".zip"
    ):
        return None, ["archive member reference is invalid"]
    try:
        with zipfile.ZipFile(target) as bundle:
            payload = bundle.read(member)
        if member_path.suffix.lower() == ".gz":
            try:
                payload = gzip.decompress(payload)
            except (OSError, EOFError):
                return None, [f"gzip artifact is invalid: {raw_path}"]
        return payload, []
    except (KeyError, zipfile.BadZipFile):
        return None, [f"archive member is missing or invalid: {raw_path}"]


def _artifact_reference(
    row: Mapping[str, Any], field: str, artifact_root: Path | None,
) -> list[str]:
    reference = row.get(field)
    if not isinstance(reference, Mapping):
        return [f"{field} artifact reference is missing"]
    raw_path = str(reference.get("path") or "")
    expected = str(reference.get("digest") or reference.get("sha256") or "")
    findings: list[str] = []
    if not raw_path:
        findings.append(f"{field} path is missing")
    if not _SHA256.fullmatch(expected):
        findings.append(f"{field} digest is missing")
    if artifact_root is None or not raw_path:
        return findings
    payload, read_findings = _artifact_bytes(raw_path, artifact_root)
    findings.extend(f"{field} {item}" for item in read_findings)
    if payload is not None and _SHA256.fullmatch(expected):
        actual = "sha256:" + hashlib.sha256(payload).hexdigest()
        if actual != expected:
            findings.append(f"{field} artifact digest mismatch")
    return findings


def _semantic_recomputation(
    row: Mapping[str, Any], scenario: Mapping[str, Any], artifact_root: Path | None,
) -> list[str]:


    if artifact_root is None or not isinstance(scenario.get("goal"), Mapping):
        return []
    reference = row.get("semantic_report")
    if not isinstance(reference, Mapping) or not str(reference.get("path") or ""):
        return []
    payload, findings = _artifact_bytes(str(reference["path"]), artifact_root)
    if findings or payload is None:
        return []
    try:
        report = json.loads(payload)
    except (UnicodeDecodeError, json.JSONDecodeError):
        return ["semantic_report is not valid JSON"]
    if not isinstance(report, Mapping):
        return ["semantic_report must contain an object"]
    if str(report.get("run_id") or "") != str(row.get("run_id") or row.get("run") or ""):
        return ["semantic_report run_id does not match the evidence row"]
    try:
        from ...routes.runner import reading_from_report
        from ...routes.schema import Route
        from .unity_semantic import validate_unity_semantic_report

        if row.get("engine") == "unity":
            validate_unity_semantic_report(report)
        route = Route.from_dict({
            "route_id": str(row.get("scenario_id") or "calibration"),
            "tier": 2,
            "goal": dict(scenario["goal"]),
        })
        reading = reading_from_report(route, dict(report), log="")
        reached = reading.reached and not reading.missing_groups
        source_goal = scenario.get("source_goal")
        if isinstance(source_goal, Mapping):
            source_route = Route.from_dict({
                "route_id": route.route_id + "#source", "tier": 2,
                "goal": dict(source_goal),
            })
            source = reading_from_report(source_route, dict(report), log="")
            reached = reached and source.reached and not source.missing_groups
    except (KeyError, TypeError, ValueError) as exc:
        return [f"semantic_report cannot be evaluated: {exc}"]
    declared = bool(row.get("goal_reached", False))
    if reached != declared:
        return [
            f"semantic_report recomputes goal_reached={str(reached).lower()} "
            f"but row declares {str(declared).lower()}"
        ]
    vector = row.get("result_vector")
    if isinstance(vector, list) and len(vector) >= 3:
        declared_milestones = tuple(str(item) for item in (vector[2] or []))
        if declared_milestones != tuple(reading.milestones_reached):
            return ["semantic_report milestones do not match result_vector"]
    return []


def _vector(run: Mapping[str, Any]) -> tuple[Any, ...]:
    if "result_vector" in run and isinstance(run["result_vector"], list):
        return tuple(
            json.dumps(item, sort_keys=True) if isinstance(item, (dict, list)) else item
            for item in run["result_vector"]
        )
    return (
        str(run.get("status") or run.get("verdict") or ""),
        bool(run.get("goal_reached", False)),
        str(run.get("primary_item") or ""),
    )


def _capture_ids(
    row: Mapping[str, Any],
    *,
    engine: str,
    artifact_root: Path | None = None,
) -> tuple[set[str], list[str]]:
    captures = row.get("checkpoint_captures") or []
    if not isinstance(captures, list):
        return set(), ["checkpoint_captures must be an array"]
    ids: set[str] = set()
    findings: list[str] = []
    for index, capture in enumerate(captures):
        if not isinstance(capture, Mapping):
            findings.append(f"checkpoint_captures[{index}] must be an object")
            continue
        checkpoint_id = str(capture.get("checkpoint_id") or "")
        if not checkpoint_id or checkpoint_id in ids:
            findings.append("checkpoint captures need unique non-empty checkpoint_id")
        ids.add(checkpoint_id)
        if capture.get("engine") != engine:
            findings.append(f"{checkpoint_id}: capture engine must be {engine}")
        frame = capture.get("frame")
        if isinstance(frame, bool) or not isinstance(frame, int) or frame < 0:
            findings.append(f"{checkpoint_id}: capture frame must be non-negative")
        raw_path = str(capture.get("path") or "")
        if not raw_path:
            findings.append(f"{checkpoint_id}: capture path is missing")
        expected_digest = str(capture.get("digest") or "")
        if not _SHA256.fullmatch(expected_digest):
            findings.append(f"{checkpoint_id}: capture digest is missing")
        if artifact_root is not None and raw_path:
            payload, read_findings = _artifact_bytes(raw_path, artifact_root)
            findings.extend(f"{checkpoint_id}: capture {item}" for item in read_findings)
            if payload is not None and _SHA256.fullmatch(expected_digest):
                actual = "sha256:" + hashlib.sha256(payload).hexdigest()
                if actual != expected_digest:
                    findings.append(f"{checkpoint_id}: capture artifact digest mismatch")
    return ids, findings


@dataclass(frozen=True)
class CalibrationReport:
    status: str
    runtime_ready: bool
    required_repeats: int
    scenario_ids: tuple[str, ...]
    blockers: tuple[str, ...]
    vectors: Mapping[str, tuple[tuple[Any, ...], ...]]
    suite_digest: str = ""
    evidence_digest: str = ""

    def to_dict(self) -> dict[str, Any]:
        return {
            "schema": CALIBRATION_SCHEMA,
            "status": self.status,
            "runtime_ready": self.runtime_ready,
            "required_repeats": self.required_repeats,
            "scenario_ids": list(self.scenario_ids),
            "blockers": list(self.blockers),
            "vectors": {key: [list(vector) for vector in values] for key, values in self.vectors.items()},
            "suite_digest": self.suite_digest,
            "evidence_digest": self.evidence_digest,
        }

    def write(self, path: str | Path) -> Path:
        target = Path(path)
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(json.dumps(self.to_dict(), indent=2, sort_keys=True, ensure_ascii=False) + "\n", encoding="utf-8")
        return target


def calibrate_suite(
    suite: Mapping[str, Any],
    evidence: Sequence[Mapping[str, Any]],
    *,
    repeats: int = 5,
    artifact_root: str | Path | None = None,
    evidence_digest: str = "",
) -> CalibrationReport:


    required = max(5, int(repeats))
    computed_digest = suite_content_digest(suite)
    declared_digest = str(suite.get("content_digest") or computed_digest)
    scenarios = tuple(str(item.get("id") or "") for item in suite.get("scenarios", []) if isinstance(item, Mapping))
    blockers = list(str(item) for item in suite.get("blockers", []) or [])
    if declared_digest != computed_digest:
        blockers.append("suite content_digest does not match suite content")
    source_count = sum(
        1 for item in suite.get("scenarios", []) or []
        if isinstance(item, Mapping) and "source_derived" in set(item.get("evidence_basis") or ())
    )
    if source_count < 2:
        blockers.append("calibration requires at least two source-derived scenarios")
    grouped: dict[str, list[Mapping[str, Any]]] = {}
    vectors: dict[str, tuple[tuple[Any, ...], ...]] = {}
    for row in evidence:
        sid = str(row.get("scenario_id") or "")
        engine = str(row.get("engine") or "")
        polarity = str(row.get("polarity") or "")
        key = f"{sid}|{engine}|{polarity}"
        grouped.setdefault(key, []).append(row)
    for sid in scenarios:
        scenario = next(
            item for item in suite.get("scenarios", [])
            if isinstance(item, Mapping) and str(item.get("id") or "") == sid
        )
        expected_checkpoints = {
            str(item.get("id") or "")
            for item in scenario.get("capture_checkpoints", []) or []
            if isinstance(item, Mapping) and item.get("id")
        }
        for engine, polarity, expected in (
            ("godot", "positive", True),
            ("unity", "positive", True),
            ("unity", "negative", False),
        ):
            key = f"{sid}|{engine}|{polarity}"
            rows = grouped.get(key, [])
            if len(rows) < required:
                blockers.append(f"{key}: need {required} repeated evidence rows, found {len(rows)}")
                continue
            selected = rows[:required]
            run_ids = [str(row.get("run_id", row.get("run", ""))) for row in selected]
            if any(not run_id for run_id in run_ids) or len(set(run_ids)) != required:
                blockers.append(f"{key}: repeated evidence needs {required} unique run ids")
            row_vectors = tuple(_vector(row) for row in selected)
            vectors[key] = row_vectors
            if len(set(row_vectors)) != 1:
                blockers.append(f"{key}: repeated verdict vector is unstable")
            capture_paths: list[str] = []
            for row in selected:
                if row.get("suite_digest") != declared_digest:
                    blockers.append(f"{key}: evidence suite_digest is stale or missing")
                status = str(row.get("status") or row.get("verdict") or "").lower()
                if row.get("infrastructure") or status in {"inconclusive", "infrastructure"}:
                    blockers.append(f"{key}: infrastructure/inconclusive evidence cannot calibrate")
                passed = bool(row.get("goal_reached", status in {"pass", "passed", "ok", "true"}))
                if passed is not expected:
                    blockers.append(f"{key}: expected {'pass' if expected else 'fail'} but observed {status or passed}")
                blockers.extend(
                    f"{key}: {finding}"
                    for finding in _artifact_reference(
                        row, "semantic_report",
                        Path(artifact_root) if artifact_root is not None else None,
                    )
                )
                blockers.extend(
                    f"{key}: {finding}"
                    for finding in _semantic_recomputation(
                        row, scenario,
                        Path(artifact_root) if artifact_root is not None else None,
                    )
                )
                if polarity == "positive":
                    observed_checkpoints, capture_findings = _capture_ids(
                        row,
                        engine=engine,
                        artifact_root=Path(artifact_root) if artifact_root is not None else None,
                    )
                    missing = sorted(expected_checkpoints - observed_checkpoints)
                    if missing:
                        blockers.append(f"{key}: missing checkpoint captures {', '.join(missing)}")
                    blockers.extend(f"{key}: {finding}" for finding in capture_findings)
                    capture_paths.extend(
                        str(item.get("path") or "")
                        for item in row.get("checkpoint_captures", []) or []
                        if isinstance(item, Mapping)
                    )
                    if engine == "godot" and row.get("reference_validated") is not True:
                        blockers.append(f"{key}: Godot evidence is not reference_validated")
                    if engine == "godot" and row.get("evidence_kind") != "real_godot_reference_trace":
                        blockers.append(f"{key}: Godot evidence kind is not a real reference trace")
                    if engine == "godot":
                        for digest_field in ("route_digest", "certificate_digest", "source_digest"):
                            if not _SHA256.fullmatch(str(row.get(digest_field) or "")):
                                blockers.append(f"{key}: Godot evidence lacks {digest_field}")
                if engine == "unity" and not str(row.get("fixture_id") or ""):
                    blockers.append(f"{key}: Unity evidence lacks fixture_id")
                if engine == "unity" and (
                    row.get("environment_class") != "linux-vm-certified"
                    or row.get("score_eligible") is not True
                ):
                    blockers.append(f"{key}: Unity evidence is not from a certified score-eligible VM")
                if engine == "unity":
                    if row.get("evidence_subject") != "evaluator_contract_fixture":
                        blockers.append(f"{key}: Unity evidence subject is not evaluator_contract_fixture")
                    if row.get("unity_editor") != "6000.3.23f1":
                        blockers.append(f"{key}: Unity editor version is not 6000.3.23f1")
                    for digest_field in (
                        "environment_profile_digest", "base_image_digest",
                        "artifact_manifest_digest", "fixture_recipe_digest",
                        "fixture_build_digest",
                    ):
                        if not _SHA256.fullmatch(str(row.get(digest_field) or "")):
                            blockers.append(f"{key}: Unity evidence lacks {digest_field}")
                if polarity == "negative":
                    if not str(row.get("primary_item") or ""):
                        blockers.append(f"{key}: negative evidence lacks primary_item")
                    if not (
                        str(row.get("ablation_action") or "")
                        or str(row.get("ablation_axis") or "")
                    ):
                        blockers.append(f"{key}: negative evidence lacks action/axis ablation")
                    if row.get("resolved") is not False:
                        blockers.append(f"{key}: negative evidence must record resolved=false")
            if polarity == "positive" and len(capture_paths) != len(set(capture_paths)):
                blockers.append(
                    f"{key}: repeated positive runs must reference distinct checkpoint captures"
                )
    unique = tuple(dict.fromkeys(blockers))
    status = "calibrated" if not unique else "pending"
    return CalibrationReport(
        status, status == "calibrated", required, scenarios, unique, vectors,
        declared_digest,
        evidence_digest,
    )


def calibrate_suite_file(
    suite_path: str | Path,
    evidence_path: str | Path,
    *,
    repeats: int = 5,
    out: str | Path | None = None,
) -> CalibrationReport:
    suite = json.loads(Path(suite_path).read_text(encoding="utf-8"))
    evidence_file = Path(evidence_path)
    evidence = json.loads(evidence_file.read_text(encoding="utf-8"))
    if isinstance(evidence, Mapping):
        evidence = evidence.get("runs") or evidence.get("evidence") or []
    report = calibrate_suite(
        suite,
        evidence,
        repeats=repeats,
        artifact_root=evidence_file.parent,
        evidence_digest="sha256:" + hashlib.sha256(evidence_file.read_bytes()).hexdigest(),
    )
    if out:
        report.write(out)
    return report


__all__ = ["CalibrationReport", "calibrate_suite", "calibrate_suite_file"]
