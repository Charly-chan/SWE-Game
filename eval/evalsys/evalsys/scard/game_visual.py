"""Game-specific visual readings for generation and cross-engine port tasks."""
from __future__ import annotations

import json
import math
from pathlib import Path
from typing import Any, Mapping

from ..verdict import Item, inconclusive, passed

PROTOCOL = "2026-09-19.game-rubric-v1"
GROUP_WEIGHTS = {"M": 0.10, "D": 0.18, "V": 0.27, "A": 0.45}
GROUP_NAMES = {"M": "Visible mechanics", "D": "Design and content",
               "V": "Functional visual communication", "A": "Art"}
PROMPT_PATH = Path(__file__).with_name("game_visual_judge.md")
RESPONSE_SCHEMA_VERSION = "2026-09-20.game-rubric-v2"


def _whole_item_condition(text):
    normalized = str(text or "").lower().replace("_", "-")
    return any(marker in normalized for marker in (
        "not-applicable", "not applicable", "不适用",
    ))


def _conditional_subclause(text):
    normalized = str(text or "").lower()
    return any(marker in normalized for marker in (
        "若", "如果", "仅当", "当候选", "when ", "if ", "only if", "unless ",
    ))


def _requires_event_witness(item):

    if str(item.get("id") or "") != "V3":
        return False
    text = " ".join(str(item.get(key) or "") for key in (
        "title", "description", "full_credit", "partial_credit", "zero_credit",
    )).lower()
    return any(marker in text for marker in (
        "事件", "碰撞", "拾取", "collision", "pickup", "event feedback",
    ))


def _v2_requirement(item):

    item_id = item["id"]


    return {**item, "item_id": item_id, "response_ids": {
        "whole_item_applicability": [
            {"condition_id": f"{item_id}.{field}", "source": field,
             "text": item.get(field, "")}
            for field in ("description", "full_credit", "zero_credit")
            if item.get(field) and _whole_item_condition(item.get(field))
        ],
        "deduction_clauses": [
            {"clause_id": f"{item_id}.{field}", "source": field,
             "text": item.get(field, "")}
            for field in ("full_credit", "partial_credit") if item.get(field)
        ] + [
            {"clause_id": f"{item_id}.high.{index}",
             "source": "high_score_requirements", "text": text}
            for index, text in enumerate(item.get("high_score_requirements", []), 1)
        ],
        "high_score_requirements": [
            {"requirement_id": f"{item_id}.high.{index}", "text": text,
             "may_be_not_applicable": _conditional_subclause(text)}
            for index, text in enumerate(item.get("high_score_requirements", []), 1)
        ],
        "caps": [{"cap_id": cap["id"]} for cap in item.get("caps", [])],
    }}


def build_response_contract(rubric):

    return {
        "schema_version": RESPONSE_SCHEMA_VERSION,
        "rubric_version": rubric.get("rubric_version"),
        "game_id": rubric.get("game_id"),
        **({"scoring_policy": rubric["scoring_policy"]} if "scoring_policy" in rubric else {}),
        "requirements": [_v2_requirement(item) for item in rubric.get("requirements", [])],
    }


def build_response_json_schema(requirements):

    item_schemas = []
    for item in requirements:
        response_ids = item["response_ids"]
        condition_ids = [row["condition_id"] for row in response_ids["whole_item_applicability"]]
        clause_ids = [row["clause_id"] for row in response_ids["deduction_clauses"]]
        requirement_ids = [row["requirement_id"] for row in response_ids["high_score_requirements"]]
        cap_ids = [row["cap_id"] for row in response_ids["caps"]]
        item_schemas.append({
            "type": "object", "additionalProperties": True,
            "required": [
                "item_id", "outcome", "applicability", "attainment", "evidence_frames",
                "strengths", "deficiencies", "deficiency_checks", "high_score_checks",
                "cap_checks", "applied_caps", "missing_evidence", "score_rationale",
            ] + (["event_witnesses"] if _requires_event_witness(item) else []),
            "properties": {
                "item_id": {"const": item["id"]},
                "outcome": {"enum": ["measured", "not_applicable"]},
                "applicability": {
                    "type": "object", "additionalProperties": False,
                    "required": ["status", "condition_id", "observation"],
                    "properties": {
                        "status": {"enum": ["applicable", "not_applicable"]},
                        "condition_id": {"enum": [None, *condition_ids]},
                        "observation": {"type": "string"},
                    },
                },
                "attainment": {"type": ["number", "null"], "minimum": 0, "maximum": 1},
                "evidence_frames": {"type": "array", "items": {"type": "string"}},
                "strengths": {"type": "array", "items": {"type": "string"}},
                "deficiencies": {"type": "array", "items": {"type": "string"}},
                "deficiency_checks": {
                    "type": "array", "items": {"type": "object", "required": [
                        "clause_id", "frames", "why_rule_applies"], "properties": {
                            "clause_id": {"enum": clause_ids},
                            "frames": {"type": "array", "items": {"type": "string"}},
                            "why_rule_applies": {"type": "string"},
                        },
                    },
                },
                "high_score_checks": {
                    "type": "array", "minItems": len(requirement_ids),
                    "maxItems": len(requirement_ids), "items": {
                        "type": "object", "required": [
                            "requirement_id", "status", "frames", "observation"],
                        "properties": {
                            "requirement_id": {"enum": requirement_ids},
                            "status": {"enum": [
                                "supported", "partial", "absent", "unknown", "not_applicable"]},
                            "frames": {"type": "array", "items": {"type": "string"}},
                            "observation": {"type": "string"},
                        },
                    },
                },
                "cap_checks": {
                    "type": "array", "minItems": len(cap_ids), "maxItems": len(cap_ids),
                    "items": {"type": "object", "required": [
                        "cap_id", "triggered", "frames", "reason"], "properties": {
                            "cap_id": {"enum": cap_ids}, "triggered": {"type": "boolean"},
                            "frames": {"type": "array", "items": {"type": "string"}},
                            "reason": {"type": "string"},
                        },
                    },
                },
                "applied_caps": {"type": "array", "items": {"enum": cap_ids}},
                "missing_evidence": {"type": "string"},
                "score_rationale": {"type": "string"},
                "event_witnesses": {
                    "type": "array",
                    "items": {
                        "type": "object", "additionalProperties": False,
                        "required": ["event_type", "event_frames", "feedback_frames", "linkage"],
                        "properties": {
                            "event_type": {"type": "string"},
                            "event_frames": {"type": "array", "items": {"type": "string"}},
                            "feedback_frames": {"type": "array", "items": {"type": "string"}},
                            "linkage": {"type": "string"},
                        },
                    },
                },
            },
        })
    return {
        "type": "object", "additionalProperties": False,
        "required": ["schema_version", "items"],
        "properties": {
            "schema_version": {"const": RESPONSE_SCHEMA_VERSION},
            "items": {"type": "array", "minItems": len(item_schemas),
                      "maxItems": len(item_schemas), "items": {"oneOf": item_schemas}},
        },
    }


def _v2_clause(item, clause_id):

    item_id = item["id"]
    if clause_id == f"{item_id}.description":
        return "description", None, item.get("description", "")
    if clause_id == f"{item_id}.full_credit":
        return "full_credit", None, item.get("full_credit", "")
    if clause_id == f"{item_id}.partial_credit":
        return "partial_credit", None, item.get("partial_credit", "")
    if clause_id == f"{item_id}.zero_credit":
        return "zero_credit", None, item.get("zero_credit", "")
    prefix = f"{item_id}.high."
    if str(clause_id).startswith(prefix):
        try:
            index = int(str(clause_id)[len(prefix):])
        except ValueError:
            index = 0
        if 1 <= index <= len(item.get("high_score_requirements", [])):
            return "high_score_requirements", index, item["high_score_requirements"][index - 1]
    return None, None, None


def normalize_v2_judgment(row, item):

    if row.get("schema_version") not in {None, RESPONSE_SCHEMA_VERSION}:
        raise ValueError("Unsupported visual response schema")
    out = dict(row)
    out["id"] = row.get("item_id", row.get("id"))
    outcome = row.get("outcome")
    if outcome not in {"measured", "not_applicable"}:
        raise ValueError("v2 item needs explicit measured/not_applicable outcome; unshown achievement scores 0")
    applicability = row.get("applicability")
    if isinstance(applicability, dict):
        out["applicability"] = applicability.get("status", "applicable")
        condition_id = applicability.get("condition_id")
        if condition_id:
            field, _index, quote = _v2_clause(item, condition_id)
            if (quote is None or field not in {"description", "full_credit", "zero_credit"}
                    or not _whole_item_condition(quote)):
                raise ValueError("Unknown applicability condition_id")
            out["applicability_quote"] = quote
        out["applicability_reason"] = applicability.get("observation") or applicability.get("reason", "")
    checks = row.get("high_score_checks", row.get("high_score_evidence"))
    if "high_score_checks" in row and isinstance(checks, list):
        converted = []
        for check in checks:
            rid = str(check.get("requirement_id", ""))
            prefix = f"{item['id']}.high."
            try:
                index = int(rid[len(prefix):]) if rid.startswith(prefix) else 0
            except ValueError:
                index = 0
            if not 1 <= index <= len(item.get("high_score_requirements", [])):
                raise ValueError("Unknown high-score requirement_id")
            if (check.get("status") == "not_applicable"
                    and out.get("applicability") == "applicable"
                    and not _conditional_subclause(
                item["high_score_requirements"][index - 1]
                    )):
                raise ValueError("High-score requirement is not a registered conditional clause")
            converted.append({
                "requirement_index": index, "status": check.get("status"),
                "frames": check.get("frames", []), "observation": check.get("observation", ""),
                "applicability_quote": item["high_score_requirements"][index - 1],
            })
        out["high_score_evidence"] = converted
    deductions = row.get("deficiency_checks", row.get("deduction_checks"))
    if "deficiency_checks" in row and isinstance(deductions, list):
        converted = []
        for index, check in enumerate(deductions, 1):
            field, req_index, quote = _v2_clause(item, check.get("clause_id"))
            if field not in {"full_credit", "partial_credit", "high_score_requirements"}:
                raise ValueError("Unknown deficiency clause_id")
            converted.append({
                "deficiency_index": index, "field": field,
                **({"requirement_index": req_index} if req_index else {}),
                "quote": quote, "frames": check.get("frames", []),
                "why_rule_applies": check.get("why_rule_applies", ""),
            })
        out["deduction_checks"] = converted
    caps = row.get("cap_checks")
    if isinstance(caps, list) and "cap_checks" in row and (
        not caps or isinstance(caps[0], dict) and "cap_id" in caps[0]
    ):
        out["cap_checks"] = [{"id": check.get("cap_id"), "triggered": check.get("triggered"),
                              "frames": check.get("frames", []), "reason": check.get("reason", "")}
                             for check in caps]
    out["applied_caps"] = row.get("applied_caps", [])
    return out


def final_credit(attainment, applied_caps, caps):
    """Use the rubric judgment directly, bounded by observed deficiency caps."""
    if attainment is None:
        if applied_caps:
            raise ValueError("An unscored item cannot trigger a deficiency cap")
        return None
    if (type(attainment) not in (int, float) or not math.isfinite(attainment)
            or not 0 <= attainment <= 1):
        raise ValueError("attainment must be finite and within [0, 1]")
    ceilings = {cap["id"]: cap["max_credit"] for cap in caps}
    if any(key not in ceilings for key in applied_caps):
        raise ValueError("Unknown cap id")
    return min([attainment] + [ceilings[key] for key in applied_caps])


def validate_judgment(row, item, frame_ids):

    q = row.get("attainment")
    applicability = row.get("applicability", "applicable")
    if applicability not in {"applicable", "not_applicable"}:
        raise ValueError("Invalid applicability")
    refs = row.get("evidence_frames")
    if not isinstance(refs, list) or not set(refs) <= frame_ids:
        raise ValueError("Evidence must cite candidate frame ids")
    strengths, deficiencies = row.get("strengths"), row.get("deficiencies")
    if not isinstance(strengths, list) or not isinstance(deficiencies, list):
        raise ValueError("Explicit strengths and deficiencies required")
    caps = row.get("applied_caps")
    if not isinstance(caps, list):
        raise ValueError("applied_caps must be a list")
    if applicability == "not_applicable":
        quote = row.get("applicability_quote", "")
        clauses = "\n".join(item.get(k, "") for k in ("description", "full_credit", "zero_credit"))
        if (not refs or not row.get("applicability_reason") or len(quote.strip()) < 6
                or quote not in clauses):
            raise ValueError("Not-applicable needs its conditional clause and candidate evidence")
        if q is not None or row.get("missing_evidence") or caps or deficiencies or row.get("score_rationale"):
            raise ValueError("Not-applicable is neither a score nor missing evidence")
    checks = row.get("high_score_evidence")
    if not isinstance(checks, list) or len(checks) != len(item["high_score_requirements"]):
        raise ValueError("Every high-score condition needs evidence assessment")
    for i, check in enumerate(checks):
        state = check.get("status")
        if check.get("requirement_index") != i + 1 or state not in {
            "supported", "partial", "absent", "unknown", "not_applicable"
        }:
            raise ValueError("Invalid high-score assessment")
        frames = check.get("frames")
        if not isinstance(frames, list) or not set(frames) <= frame_ids:
            raise ValueError("Invalid high-score evidence frames")
        if state not in {"unknown", "not_applicable"} and not frames:
            raise ValueError("Visible claims need frame evidence")
        if state == "not_applicable" and applicability == "applicable":
            quote = check.get("applicability_quote", "")
            if (not check.get("observation") or len(quote.strip()) < 6
                    or quote not in item["high_score_requirements"][i]):
                raise ValueError("Inapplicable subcondition needs its exact conditional clause")
    if applicability == "not_applicable" and any(c["status"] != "not_applicable" for c in checks):
        raise ValueError("Not-applicable item cannot claim attainment")
    if q is None:
        if applicability == "applicable":
            raise ValueError("Applicable items need numeric attainment; entirely unshown achievement scores 0")
    else:
        missing = row.get("missing_evidence", "")
        unshown = [c for c in checks if c["status"] == "unknown"]
        positive = [c for c in checks if c["status"] in {"supported", "partial"}]
        entirely_unshown = bool(unshown) and all(
            c["status"] in {"unknown", "not_applicable"} for c in checks
        )
        if (not row.get("score_rationale")
                or (not refs and not (q == 0 and entirely_unshown and missing))):
            raise ValueError("Measured attainment needs evidence and a rationale")
        if unshown and (not missing or any(not c.get("observation") for c in unshown)):
            raise ValueError("Unshown conditions need specific missing_evidence and observations")
        if q > 0 and (not strengths or not positive):
            raise ValueError("Positive attainment needs demonstrated strengths and a supported or partial condition")
        if q == 1:
            if (deficiencies or caps or missing or not strengths
                    or not any(c["status"] == "supported" for c in checks)
                    or any(c["status"] not in {"supported", "not_applicable"} for c in checks)):
                raise ValueError("Full attainment lacks full-credit evidence")
        elif not deficiencies and not missing:
            raise ValueError("Sub-full attainment needs an observed deficiency or a specific unshown requirement")
    if _requires_event_witness(item) and q == 1:
        witnesses = row.get("event_witnesses")
        if not isinstance(witnesses, list) or not witnesses:
            raise ValueError(
                "Full event-feedback attainment requires a visible event witness; "
                "a counter delta alone is insufficient"
            )
        for witness in witnesses:
            event_frames = witness.get("event_frames") if isinstance(witness, dict) else None
            feedback_frames = witness.get("feedback_frames") if isinstance(witness, dict) else None
            if (not witness.get("event_type") or not witness.get("linkage")
                    or not isinstance(event_frames, list) or not event_frames
                    or not isinstance(feedback_frames, list) or not feedback_frames
                    or not set(event_frames + feedback_frames) <= frame_ids):
                raise ValueError(
                    "Event witnesses need candidate event frames, feedback frames, and linkage"
                )
    deductions = row.get("deduction_checks")
    if not isinstance(deductions, list) or len(deductions) != len(deficiencies):
        raise ValueError("Every deficiency needs its normative clause")
    for i, check in enumerate(deductions):
        if check.get("deficiency_index") != i + 1:
            raise ValueError("Invalid deficiency mapping")
        field = check.get("field")
        if field == "high_score_requirements":
            index = check.get("requirement_index")
            if type(index) is not int or not 1 <= index <= len(item[field]):
                raise ValueError("Invalid high-score clause index")
            clause = item[field][index - 1]
        elif field in {"full_credit", "partial_credit"}:
            clause = item[field]
        else:
            raise ValueError("A deduction cannot turn GT description into an obligation")
        quote = check.get("quote", "")
        if len(quote.strip()) < 6 or quote not in clause:
            raise ValueError("Deduction quote is not in the criterion")
        if (not check.get("frames") or not set(check["frames"]) <= frame_ids
                or not check.get("why_rule_applies")):
            raise ValueError("Deduction needs candidate evidence and applicability")
    cap_checks = row.get("cap_checks")
    if (not isinstance(cap_checks, list) or len(cap_checks) != len(item["caps"])
            or {c.get("id") for c in cap_checks} != {c["id"] for c in item["caps"]}):
        raise ValueError("Every cap must be checked exactly once")
    for check in cap_checks:
        if (type(check.get("triggered")) is not bool or not check.get("reason")
                or not isinstance(check.get("frames"), list)
                or not set(check["frames"]) <= frame_ids):
            raise ValueError("Invalid cap assessment")
        if check["triggered"] and not check["frames"]:
            raise ValueError("Triggered caps need candidate evidence")
    if {c["id"] for c in cap_checks if c["triggered"]} != set(caps):
        raise ValueError("cap_checks and applied_caps disagree")
    return {**row, "applicability": applicability,
            "status": "not_applicable" if applicability == "not_applicable" else "measured",
            "credit": final_credit(q, caps, item["caps"])}


def validate_response_items(response, items, frame_ids, group):

    lookup = {item["id"]: item for item in items}
    if not isinstance(response, dict):
        raise ValueError("Expected an object with an items array")
    rows = response.get("items", [])
    if not isinstance(rows, list) or not all(isinstance(row, dict) for row in rows):
        raise ValueError("items must be an array of item judgments")
    normalized, errors, seen = {}, {}, set()
    response_schema = response.get("schema_version")
    if response_schema not in {None, RESPONSE_SCHEMA_VERSION}:
        raise ValueError("Unsupported visual response schema")
    for original in rows:
        item_id = original.get("item_id", original.get("id"))
        if item_id not in lookup:
            continue
        if item_id in seen:
            errors[item_id] = "Item was returned more than once"
            normalized.pop(item_id, None)
            continue
        seen.add(item_id)
        try:
            is_v2 = (
                response_schema == RESPONSE_SCHEMA_VERSION
                or original.get("schema_version") == RESPONSE_SCHEMA_VERSION
                or isinstance(original.get("applicability"), dict)
                or "high_score_checks" in original
                or "deficiency_checks" in original
            )
            row = normalize_v2_judgment(
                {**original, "schema_version": original.get("schema_version", response_schema)},
                lookup[item_id],
            ) if is_v2 else dict(original)
            if isinstance(row.get("attainment"), str):
                try:
                    row["attainment"] = float(row["attainment"])
                except ValueError:
                    pass
            value = validate_judgment(row, lookup[item_id], frame_ids)
            if is_v2:
                if row["outcome"] != value["status"]:
                    raise ValueError(
                        f"Explicit outcome {row['outcome']} disagrees with validated status {value['status']}"
                    )


                shadow = validate_judgment(dict(value), lookup[item_id], frame_ids)
                keys = ("applicability", "attainment", "applied_caps", "credit")
                if any(shadow.get(key) != value.get(key) for key in keys):
                    raise ValueError("v1/v2 shadow validation mismatch")
                value["validation_shadow"] = {
                    "v2_adapter": "passed", "canonical_v1_validator": "passed",
                    "score_equivalent": True,
                }
            normalized[item_id] = {
                **value, "title": lookup[item_id]["title"], "group": group,
                "owner": "candidate", "failure_code": None,
            }
        except (ValueError, TypeError, KeyError) as exc:
            errors[item_id] = str(exc)
    for item_id in set(lookup) - seen:
        errors[item_id] = "Item was not returned"
    return normalized, errors


def aggregate_game_visual(rubric, judgments, *, detail="", evidence=None) -> Item:
    """Average applicable items, including unshown zeros; retain technical gaps."""
    # Resumed judgments can retain obsolete diagnostic fields. Preserve their
    # actual credit while emitting the current direct-score report.
    judgments = {key: {field: value for field, value in row.items() if field != "variants"}
                 for key, row in judgments.items()}
    evidence = {**(evidence or {}), "protocol": PROTOCOL,
                "response_schema_version": RESPONSE_SCHEMA_VERSION,
                "rubric_version": rubric.get("rubric_version"),
                "validation_status": "mode5_v2_corpus_calibrated",
                "group_weights": GROUP_WEIGHTS,
                "scoring": {"method": "direct_continuous", "range": [0, 1],
                            "caps_scale": "item_credit",
                            "policy_version": (rubric.get("scoring_policy") or {}).get("version")},
                "criteria": judgments}
    evidence.pop("variants", None)
    evidence.pop("score_curve", None)
    groups = {}
    missing = []
    for group, weight in GROUP_WEIGHTS.items():
        requirements = [r for r in rubric.get("requirements", []) if r["group"] == group]
        applicable = []
        for item in requirements:
            row = judgments.get(item["id"], {})
            if row.get("status") == "not_applicable":
                continue
            applicable.append(item["id"])
            if row.get("status") != "measured":
                missing.append(item["id"])
        measured = applicable and all(key not in missing for key in applicable)
        groups[group] = {"name": GROUP_NAMES[group], "weight": weight, "applicable_items": applicable,
                         "status": "complete" if measured else "retry_required" if applicable else "not_applicable",
                         "credit": sum(judgments[k]["credit"] for k in applicable) / len(applicable) if measured else None}
    evidence.update(groups=groups, unmeasured=missing)
    active = [g for g in groups.values() if g["status"] != "not_applicable"]
    if missing or not active:
        return inconclusive("task_visual", detail=detail or "Missing VLM evidence: " + ", ".join(missing), evidence=evidence)
    denom = sum(g["weight"] for g in active)
    return passed("task_visual", credit=sum(g["weight"] * g["credit"] for g in active) / denom,
                  detail="Game-specific GT-conditioned VLM assessment; Mode5-v2 corpus calibration is complete", evidence=evidence)


def judge_game_visual(manifest, out: Path, *, judge=None) -> Item:


    from .rubric_judge import rubric_judge_from_env
    from ..taskgen.package import write_json
    from ..taskgen.visual_materials import VisualEvidenceError, prepare_candidate_frames, evidence_images

    out = Path(out)
    out.mkdir(parents=True, exist_ok=True)
    rubric = manifest["game_rubric"]
    response_contract = manifest.get("response_contract") or build_response_contract(rubric)
    if response_contract != build_response_contract(rubric):
        raise ValueError("Visual response contract does not match the frozen rubric")
    contract_items = {item["id"]: item for item in response_contract["requirements"]}
    judgments: dict[str, Any] = {}
    evidence = {"game_id": manifest["game_id"], "mode": manifest["mode"],
                "reference_images_sent": False, "submitter_description_sent": False,
                "recordings": manifest["demonstrations"], "requests": [],
                "group_results": {}}
    try:
        candidate = prepare_candidate_frames(manifest["demonstrations"], out / "candidate")
        evidence["candidate_frames"] = candidate
        frame_ids = {f["id"] for f in candidate}
        judge = judge or rubric_judge_from_env()
        for group in GROUP_WEIGHTS:
            items = [item for item in rubric["requirements"] if item["group"] == group]
            if not items:
                continue
            folder = out / group
            folder.mkdir(exist_ok=True)
            try:
                all_items = list(items)
                lookup = {i["id"]: i for i in all_items}


                checkpoint = folder / "normalized.json"
                if checkpoint.is_file():
                    retained = json.loads(checkpoint.read_text(encoding="utf-8"))
                    retained = {
                        item_id: row for item_id, row in (retained.items() if isinstance(retained, dict) else [])
                        if item_id in lookup and isinstance(row, dict)
                        and row.get("status") in {"measured", "not_applicable"}
                    }
                    if retained:
                        judgments.update(retained)
                        items = [item for item in all_items if item["id"] not in retained]
                    if retained and not items:
                        (folder / "error.json").unlink(missing_ok=True)
                        evidence["group_results"][group] = {
                            "status": "measured", "attempts": 0,
                            "resumed_from_checkpoint": True,
                            "item_ids": sorted(retained),
                        }
                        continue
                else:
                    retained = {}
                lookup = {i["id"]: i for i in items}
                materials, images = evidence_images(manifest, candidate, items, folder)
                request_requirements = [contract_items[item["id"]] for item in items]
                prompt = PROMPT_PATH.read_text(encoding="utf-8") + "\n\n" + json.dumps({
                    "game_id": manifest["game_id"], "mode": manifest["mode"],
                    "rubric_group": GROUP_NAMES[group],
                    "runtime_facts": manifest.get("runtime_facts", {}),
                    "task_map": rubric.get("task_map", {}),
                    "scoring_policy": rubric.get("scoring_policy", {}),
                    "reference_limits": rubric.get("reference_limits", []),
                    "response_schema_version": RESPONSE_SCHEMA_VERSION,
                    "requirements": request_requirements,
                    "response_json_schema": build_response_json_schema(request_requirements),
                    "evidence": materials,
                }, ensure_ascii=False, indent=2)

                def score_with_transport_retry(request_prompt, request_images, target):
                    try:
                        return (*judge.score(request_prompt, request_images, target), 0)
                    except RuntimeError as exc:
                        message = str(exc).lower()
                        transient = any(token in message for token in (
                            "connection", "econnreset", "transport", "429", "5xx",
                            "gateway", "upstream service", "malformed response", "http 200",
                        ))
                        deterministic = any(token in message for token in (
                            "401", "403", "authentication", "invalid api key", "insufficient balance",
                        ))
                        if not transient or deterministic:
                            raise
                        retry = target / "transport-retry"
                        suffix = 2
                        while retry.exists() and any(retry.iterdir()):
                            retry = target / f"transport-retry-{suffix}"
                            suffix += 1
                        retry.mkdir(exist_ok=True)
                        response, record = judge.score(request_prompt, request_images, retry)
                        evidence.setdefault("transport_retries", []).append({
                            "group": group, "detail": str(exc), "retry_dir": str(retry),
                        })
                        return response, record, 1
                def normalize(response, expected):
                    return validate_response_items(response, expected, frame_ids, group)


                response, record, transport_retries = score_with_transport_retry(prompt, images, folder)
                evidence["reference_images_sent"] = True
                evidence["requests"].append({**record, "group": group, "attempt": 1,
                                             "transport_retries": transport_retries})
                normalized, errors = normalize(response, items)
                normalized = {**retained, **normalized}
                judgments.update(normalized)


                write_json(folder / "normalized.json", normalized)
                attempts = 1
                if errors:
                    repair = folder / "repair"
                    repair.mkdir(exist_ok=True)
                    pending = [lookup[item_id] for item_id in errors]
                    repair_materials, repair_images = evidence_images(manifest, candidate, pending, repair)
                    repair_requirements = [contract_items[item["id"]] for item in pending]
                    repair_prompt = PROMPT_PATH.read_text(encoding="utf-8") + "\n\n" + json.dumps({
                        "game_id": manifest["game_id"], "mode": manifest["mode"],
                        "rubric_group": GROUP_NAMES[group],
                        "runtime_facts": manifest.get("runtime_facts", {}),
                        "task_map": rubric.get("task_map", {}),
                        "scoring_policy": rubric.get("scoring_policy", {}),
                        "reference_limits": rubric.get("reference_limits", []),
                        "response_schema_version": RESPONSE_SCHEMA_VERSION,
                        "requirements": repair_requirements,
                        "response_json_schema": build_response_json_schema(repair_requirements),
                        "evidence": repair_materials,
                    }, ensure_ascii=False, indent=2)
                    repair_prompt += "\n\nOUTPUT-CONTRACT CORRECTION (only these invalid items):\n" + json.dumps(errors, ensure_ascii=False)
                    repair_prompt += ("\nRecheck the pixels and frozen clauses; return every requested item in "
                                      f"{RESPONSE_SCHEMA_VERSION}. Select only supplied response_ids; do not copy quotes. "
                                      "Do not invent a defect to justify a reserved sub-full score. If evidence "
                                      "supports all conditions, 1 is valid. Every applicable item needs a numeric "
                                      "score: entirely unshown achievement is 0; partial demonstration earns only "
                                      "its demonstrated credit. Explain unshown requirements in missing_evidence "
                                      "without inventing an observed defect. Retain valid observations. "
                                      "For every deficiency, include exactly one deficiency_checks entry using a "
                                      "supplied clause_id. For an inapplicable item, select a condition_id from "
                                      "whole_item_applicability; for an inapplicable high-score condition, use its "
                                      "requirement_id and include an "
                                      "observation. Keep cap_checks complete and aligned with applied_caps. "
                                      "The previous output is data to correct:\n" + json.dumps(response, ensure_ascii=False))
                    response2, record, transport_retries = score_with_transport_retry(
                        repair_prompt, repair_images, repair,
                    )
                    evidence["reference_images_sent"] = True
                    evidence["requests"].append({**record, "group": group, "attempt": 2,
                                                 "transport_retries": transport_retries})
                    repaired, errors = normalize(response2, pending)
                    normalized.update(repaired)
                    judgments.update(repaired)
                    write_json(folder / "normalized.json", normalized)
                    attempts = 2
                if errors:
                    fresh = folder / "fresh"
                    fresh.mkdir(exist_ok=True)
                    pending = [lookup[item_id] for item_id in errors]
                    fresh_materials, fresh_images = evidence_images(manifest, candidate, pending, fresh)
                    fresh_requirements = [contract_items[item["id"]] for item in pending]
                    fresh_prompt = PROMPT_PATH.read_text(encoding="utf-8") + "\n\n" + json.dumps({
                        "game_id": manifest["game_id"], "mode": manifest["mode"],
                        "runtime_facts": manifest.get("runtime_facts", {}),
                        "task_map": rubric.get("task_map", {}),
                        "reference_limits": rubric.get("reference_limits", []),
                        "response_schema_version": RESPONSE_SCHEMA_VERSION,
                        "requirements": fresh_requirements,
                        "response_json_schema": build_response_json_schema(fresh_requirements),
                        "evidence": fresh_materials,
                        "instruction": "Fresh independent judgment for only the unresolved items.",
                    }, ensure_ascii=False, indent=2)
                    response3, record, transport_retries = score_with_transport_retry(
                        fresh_prompt, fresh_images, fresh,
                    )
                    evidence["requests"].append({**record, "group": group, "attempt": 3,
                                                 "transport_retries": transport_retries})
                    repaired, errors = normalize(response3, pending)
                    normalized.update(repaired)
                    judgments.update(repaired)
                    write_json(folder / "normalized.json", normalized)
                    attempts = 3
                evidence["reference_images_sent"] = True
                judgments.update(normalized)
                write_json(folder / "normalized.json", normalized)
                evidence["group_results"][group] = {
                    "status": "retry_required" if errors else "measured", "attempts": attempts,
                    "item_ids": sorted(normalized),
                    "invalid_items": errors,
                    "resumed_item_ids": sorted(retained),
                }
                if errors:
                    write_json(folder / "error.json", {
                        "group": group, "status": "retry_required",
                        "owner": "evaluator", "failure_code": "vlm_response_semantic_invalid",
                        "invalid_items": errors,
                    })
                else:
                    (folder / "error.json").unlink(missing_ok=True)
            except Exception as exc:
                detail = f"{type(exc).__name__}: {exc}"
                evidence["group_results"][group] = {
                    "status": "inconclusive", "attempts": len([
                        r for r in evidence["requests"]
                        if str(r.get("group") or "") == group
                    ]),
                    "detail": detail,
                }
                write_json(folder / "error.json", {
                    "group": group, "status": "inconclusive", "detail": detail,
                })
                continue
    except VisualEvidenceError as exc:
        failure = exc.to_dict()
        evidence["recording_health"] = failure
        if exc.owner == "candidate":
            for item in rubric.get("requirements", []):
                judgments[item["id"]] = {
                    "id": item["id"], "title": item["title"], "group": item["group"],
                    "status": "measured", "owner": "candidate", "failure_code": exc.code,
                    "attainment": 0.0, "credit": 0.0,
                    "evidence_frames": [], "strengths": [],
                    "deficiencies": [exc.detail], "applied_caps": [],
                }
            evidence["candidate_failures"] = [failure]
            detail = f"Candidate visual failure: {exc.code}: {exc.detail}"
        else:
            evidence["evaluator_failures"] = [{**failure, "retryable": True}]
            detail = f"VLM assessment incomplete: {exc.code}: {exc.detail}"
    except Exception as exc:

        detail = f"VLM assessment incomplete: {type(exc).__name__}: {exc}"
    else:
        errors = [f"{group}: {row.get('detail') or row.get('invalid_items')}" for group, row in evidence["group_results"].items()
                  if row.get("status") in {"inconclusive", "retry_required"}]
        detail = "VLM assessment incomplete: " + "; ".join(errors) if errors else ""
        if errors:
            evidence["evaluator_failures"] = [{
                "status": "retry_required", "owner": "evaluator",
                "failure_code": "vlm_response_semantic_invalid", "detail": error,
                "retryable": True,
            } for error in errors]
    item = aggregate_game_visual(rubric, judgments, detail=detail, evidence=evidence)
    write_json(out / "reading.json", item.to_dict())
    return item


def visual_report(item: Item) -> list[str]:
    evidence = item.evidence or {}
    lines = ["## Perceptual Quality Assessment", "",
             "Game-specific rubric; reference video and supplied asset previews accompany candidate evidence.",
             "Machine-evaluable Mode5-v2 corpus calibration is complete; any separate human-study calibration is not a task readiness gate.", "",
             f"Status: {item.verdict.value}. Scoring: direct rubric judgment with applicable caps.", "",
             "| Item | Rubric score | Cap ids | Final credit (0–1) |",
             "|---|---:|---|---:|"]
    for key, row in evidence.get("criteria", {}).items():
        lines.append(f"| {key} | {row.get('attainment')} | {', '.join(row.get('applied_caps', []))} | {row.get('credit')} |")
    for group, row in evidence.get("groups", {}).items():
        lines.append(f"- {GROUP_NAMES.get(group, group)}: {row['credit']}; weight {row['weight']:.0%}; {row['status']}")
    lines.extend(["", item.detail, ""])
    return lines
