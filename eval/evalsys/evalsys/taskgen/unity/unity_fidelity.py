


from __future__ import annotations

import base64
import hashlib
import json
import os
import re
import shutil
import subprocess
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any, Callable, Literal, Mapping, Sequence

from ...scard.judge import (
    default_anthropic_requester,
    default_claude_code_requester,
    default_openai_responses_requester,
    extract_responses_text,
    load_frame,
)


FidelityStatus = Literal["measured", "inconclusive"]
CRITERIA = (
    "asset_identity",
    "scene_progression",
)


@dataclass(frozen=True)
class FidelityCriterion:
    id: str
    verdict: str
    credit: float | None
    rationale: str
    reference_timestamps: tuple[float, ...] = ()
    candidate_timestamps: tuple[float, ...] = ()

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass(frozen=True)
class UnityFidelityResult:
    status: FidelityStatus
    detail: str
    provider_available: bool
    model: str
    prompt_sha256: str
    reference_video: str
    reference_frames: tuple[str, ...]
    candidate_frames: tuple[str, ...]
    criteria: tuple[FidelityCriterion, ...]
    provider_error: str = ""
    wire_api: str = ""

    @property
    def credit(self) -> float | None:
        values = [item.credit for item in self.criteria if item.credit is not None]
        return (sum(values) / len(CRITERIA)) if len(values) == len(CRITERIA) else None

    def to_dict(self) -> dict[str, Any]:
        return asdict(self) | {
            "credit": self.credit, "calibration_state": "calibrated",
            "calibration_detail": "Mode5-v2 paired cross-engine protocol is admitted for the calibrated 41-game corpus",
        }


def unity_fidelity_preflight(*, requested: bool) -> dict[str, Any]:


    if not requested:
        return {
            "status": "disabled",
            "requested": False,
            "credential_available": False,
        }
    provider = (os.environ.get("GAMEBENCH_VLM_PROVIDER") or "responses").strip().lower()
    if provider in {"openai", "openai_responses"}:
        provider = "responses"
    if provider in {"messages", "anthropic_messages"}:
        provider = "anthropic"
    key_env = (os.environ.get("GAMEBENCH_VLM_KEY_ENV") or "").strip()
    fallback = (
        ("OPENAI_API_KEY", "AUTO_CODE_API_KEY")
        if provider == "responses"
        else ("ANTHROPIC_API_KEY", "CLAUDE_API_KEY", "AUTO_CODE_API_KEY")
    )
    selected = key_env if key_env and os.environ.get(key_env) else next(
        (name for name in fallback if os.environ.get(name)), ""
    )
    model = (
        os.environ.get("GAMEBENCH_VLM_MODEL")
        or (os.environ.get("OPENAI_VLM_MODEL") if provider == "responses" else None)
        or (os.environ.get("ANTHROPIC_MODEL") if provider == "anthropic" else None)
        or ("gpt-5.6" if provider == "responses" else "opus-4-7")
    )
    valid_provider = provider in {"responses", "anthropic"}
    available = bool(selected) and valid_provider
    return {
        "status": "ready" if available else "missing_credential" if valid_provider else "invalid_provider",
        "requested": True,
        "provider": provider,
        "model": model,
        "credential_available": available,
        "credential_env": selected or key_env or None,
    }


def judge_cross_engine_fidelity(
    reference_video: str | Path | None,
    candidate_frames: Sequence[str],
    *,
    candidate_video: str | Path | None = None,
    out_dir: str | Path,
    game_id: str,
    task_context: str = "",
    requester: Callable[[dict, str, str], dict] | None = None,
    api_key: str | None = None,
    base_url: str | None = None,
    model: str | None = None,
    provider: str | None = None,
    max_frames: int = 6,
) -> UnityFidelityResult:

    destination = Path(out_dir).resolve()
    destination.mkdir(parents=True, exist_ok=True)
    video = Path(reference_video).resolve() if reference_video else None
    candidate_film = Path(candidate_video).resolve() if candidate_video else None
    wire = (provider or os.environ.get("GAMEBENCH_VLM_PROVIDER") or "responses").strip().lower()
    if wire in {"openai", "openai_responses"}:
        wire = "responses"
    if wire in {"messages", "anthropic_messages"}:
        wire = "anthropic"
    if wire not in {"responses", "anthropic"}:
        return _inconclusive(f"unknown VLM provider {wire!r}", model or "", video, wire)
    if wire == "responses":
        resolved_model = model or os.environ.get("GAMEBENCH_VLM_MODEL") or os.environ.get("OPENAI_VLM_MODEL") or "gpt-5.6"
        selected_key_env = (os.environ.get("GAMEBENCH_VLM_KEY_ENV") or "").strip()
        selected_key = os.environ.get(selected_key_env, "") if selected_key_env else ""
        key = api_key if api_key is not None else (
            selected_key
            or os.environ.get("OPENAI_API_KEY")
            or os.environ.get("AUTO_CODE_API_KEY")
            or ""
        )
        endpoint = _responses_url(
            base_url
            or os.environ.get("GAMEBENCH_VLM_BASE_URL")
            or os.environ.get("OPENAI_BASE_URL")
            or os.environ.get("AUTO_CODE_BASE_URL")
            or "https://vip.auto-code.net/v1"
        )
        request_fn = requester or default_openai_responses_requester
    else:
        resolved_model = model or os.environ.get("GAMEBENCH_VLM_MODEL") or os.environ.get("ANTHROPIC_MODEL") or os.environ.get("CLAUDE_MODEL") or "opus-4-7"
        selected_key_env = (os.environ.get("GAMEBENCH_VLM_KEY_ENV") or "").strip()
        selected_key = os.environ.get(selected_key_env, "") if selected_key_env else ""
        key = api_key if api_key is not None else (
            selected_key
            or os.environ.get("ANTHROPIC_API_KEY")
            or os.environ.get("CLAUDE_API_KEY")
            or os.environ.get("AUTO_CODE_API_KEY")
            or ""
        )
        endpoint = _messages_url(
            base_url
            or os.environ.get("GAMEBENCH_VLM_BASE_URL")
            or os.environ.get("ANTHROPIC_BASE_URL")
            or os.environ.get("CLAUDE_BASE_URL")
            or "https://vip.auto-code.net/v1"
        )
        transport = (os.environ.get("GAMEBENCH_VLM_TRANSPORT") or "messages").strip().lower()
        request_fn = requester or (
            default_claude_code_requester
            if transport == "claude_code"
            else default_anthropic_requester
        )
    if video is None or not video.is_file():
        return _inconclusive("canonical reference video is unavailable", resolved_model, video, wire)
    chosen_candidate: tuple[str, ...]
    candidate_times: tuple[float, ...]
    alignment = "witness_samples_fallback"
    if candidate_film is not None and candidate_film.is_file():
        chosen_candidate, candidate_times = extract_reference_frames(
            candidate_film, destination / "candidate", max_frames=max_frames,
        )
        alignment = "normalized_whole_run"
    else:
        chosen_candidate = _select_paths(candidate_frames, max_frames)
        candidate_times = tuple(
            _candidate_timestamp(path, index)
            for index, path in enumerate(chosen_candidate)
        )
    if not chosen_candidate:
        return _inconclusive("evaluator-owned Unity witness frames are unavailable", resolved_model, video, wire)
    reference, reference_times = extract_reference_frames(video, destination / "reference", max_frames=max_frames)
    if not reference:
        return _inconclusive("ffmpeg could not extract native reference frames", resolved_model, video, wire)

    try:
        reference_objects = [
            load_frame(
                path,
                source="evalsys.taskgen.reference_video",
                point_id=f"reference_{index}",
                level="reference",
            )
            for index, path in enumerate(reference)
        ]
        candidate_objects = [
            load_frame(
                path,
                source="evalsys.taskgen.unity_probe",
                point_id=f"candidate_{index}",
                level="unity",
            )
            for index, path in enumerate(chosen_candidate)
        ]
    except Exception as exc:
        return _inconclusive(f"paired frames are not judge-deliverable: {exc}", resolved_model, video, wire)

    candidate_contexts = tuple(
        _candidate_context(path, index, timestamp=candidate_times[index], alignment=alignment)
        for index, path in enumerate(chosen_candidate)
    )
    prompt = _prompt(
        game_id=game_id,
        task_context=task_context,
        reference_times=reference_times,
        candidate_times=candidate_times,
        candidate_contexts=candidate_contexts,
        alignment=alignment,
    )
    prompt_text = json.dumps(prompt, ensure_ascii=False, sort_keys=True)
    prompt_sha = hashlib.sha256(prompt_text.encode("utf-8")).hexdigest()
    if not key:
        return UnityFidelityResult(
            "inconclusive",
            "no VLM credential in the evaluator environment; paired fidelity was not run",
            False,
            resolved_model,
            prompt_sha,
            str(video),
            tuple(reference),
            tuple(chosen_candidate),
            (),
            "missing evaluator VLM credential",
            wire,
        )

    if wire == "responses":
        content = [{"type": "input_text", "text": prompt_text}]
        for index, frame in enumerate(reference_objects):
            content.append({"type": "input_text", "text": f"REFERENCE frame {index} at {reference_times[index]:.3f}s"})
            content.append(_responses_image_block(Path(frame.path)))
        for index, frame in enumerate(candidate_objects):
            content.append({"type": "input_text", "text": f"CANDIDATE frame {index}: {candidate_contexts[index]}"})
            content.append(_responses_image_block(Path(frame.path)))
        payload = {
            "model": resolved_model,
            "store": False,
            "max_output_tokens": 2500,
            "input": [{"role": "user", "content": content}],
        }
        effort = (os.environ.get("GAMEBENCH_VLM_EFFORT") or "").strip().lower()
        if effort in {"none", "minimal", "low", "medium", "high", "xhigh"}:
            payload["reasoning"] = {"effort": effort}
    else:
        content = [{"type": "text", "text": prompt_text}]
        for index, frame in enumerate(reference_objects):
            content.append({"type": "text", "text": f"REFERENCE frame {index} at {reference_times[index]:.3f}s"})
            content.append(_image_block(Path(frame.path)))
        for index, frame in enumerate(candidate_objects):
            content.append({"type": "text", "text": f"CANDIDATE frame {index}: {candidate_contexts[index]}"})
            content.append(_image_block(Path(frame.path)))
        payload = {
            "model": resolved_model,
            "max_tokens": 2500,
            "messages": [{"role": "user", "content": content}],
        }
    try:
        response = request_fn(payload, key, endpoint)
        data = _response_json(response)
        criteria = _criteria(data, reference_times, candidate_times)
    except Exception as exc:
        return UnityFidelityResult(
            "inconclusive",
            f"paired VLM provider error: {exc}",
            False,
            resolved_model,
            prompt_sha,
            str(video),
            tuple(reference),
            tuple(chosen_candidate),
            (),
            str(exc),
            wire,
        )
    if any(item.credit is None for item in criteria):
        status: FidelityStatus = "inconclusive"
        detail = "paired VLM returned incomplete fidelity criteria"
    else:
        status = "measured"
        detail = "corpus-calibrated GT-conditioned cross-engine visual evidence produced"
    result = UnityFidelityResult(
        status,
        detail,
        True,
        resolved_model,
        prompt_sha,
        str(video),
        tuple(reference),
        tuple(chosen_candidate),
        tuple(criteria),
        "",
        wire,
    )
    (destination / "cross_engine_fidelity.json").write_text(
        json.dumps(result.to_dict(), indent=2, sort_keys=True, ensure_ascii=False),
        encoding="utf-8",
    )
    return result


def extract_reference_frames(
    video: str | Path,
    out_dir: str | Path,
    *,
    max_frames: int = 6,
    ffmpeg_bin: str | Path | None = None,
    ffprobe_bin: str | Path | None = None,
) -> tuple[tuple[str, ...], tuple[float, ...]]:

    source = Path(video).resolve()
    destination = Path(out_dir).resolve()
    if destination.exists():
        shutil.rmtree(destination)
    destination.mkdir(parents=True)
    ffmpeg = _tool(ffmpeg_bin, "ffmpeg")
    ffprobe = _tool(ffprobe_bin, "ffprobe")
    if ffmpeg is None or ffprobe is None or not source.is_file():
        return (), ()
    probe = subprocess.run(
        [
            str(ffprobe), "-v", "error", "-show_entries", "format=duration",
            "-of", "default=noprint_wrappers=1:nokey=1", str(source),
        ],
        text=True,
        capture_output=True,
        check=False,
    )
    try:
        duration = float((probe.stdout or "").strip())
    except ValueError:
        return (), ()
    if probe.returncode != 0 or duration <= 0:
        return (), ()
    count = max(1, int(max_frames))
    times = tuple(duration * (index + 0.5) / count for index in range(count))
    frames: list[str] = []
    kept_times: list[float] = []
    for index, timestamp in enumerate(times):
        output = destination / f"reference_{index:03d}.png"
        process = subprocess.run(
            [
                str(ffmpeg), "-hide_banner", "-loglevel", "error", "-y",
                "-ss", f"{timestamp:.6f}", "-i", str(source),
                "-frames:v", "1", "-vsync", "0", str(output),
            ],
            text=True,
            capture_output=True,
            check=False,
        )
        if process.returncode == 0 and output.is_file():
            frames.append(str(output))
            kept_times.append(timestamp)
    return tuple(frames), tuple(kept_times)


def _criteria(
    data: Mapping[str, Any],
    reference_times: Sequence[float],
    candidate_times: Sequence[float],
) -> list[FidelityCriterion]:
    raw_criteria = data.get("criteria")
    by_id = {
        str(item.get("id")): item
        for item in raw_criteria
        if isinstance(item, Mapping) and item.get("id")
    } if isinstance(raw_criteria, list) else {}
    out: list[FidelityCriterion] = []
    for criterion_id in CRITERIA:
        raw = by_id.get(criterion_id) or {}
        verdict = str(raw.get("verdict") or "unverified").lower()
        if verdict not in {"pass", "partial", "fail", "unverified"}:
            verdict = "unverified"
        try:
            credit = float(raw["credit"]) if raw.get("credit") is not None else None
        except (TypeError, ValueError):
            credit = None
        if credit is not None:
            credit = min(1.0, max(0.0, credit))
        rationale = str(raw.get("rationale") or "").strip()
        reference_support = _float_tuple(raw.get("reference_timestamps"), ())
        candidate_support = _float_tuple(raw.get("candidate_timestamps"), ())


        if (
            verdict == "unverified"
            or not rationale
            or not reference_support
            or not candidate_support
        ):
            credit = None
        out.append(
            FidelityCriterion(
                criterion_id,
                verdict,
                credit,
                rationale or "criterion was not returned with valid evidence",
                reference_support,
                candidate_support,
            )
        )
    return out


def _prompt(
    *,
    game_id: str,
    task_context: str,
    reference_times: Sequence[float],
    candidate_times: Sequence[float],
    candidate_contexts: Sequence[str],
    alignment: str,
) -> dict[str, Any]:
    return {
        "task": (
            "Compare a canonical Godot gameplay sequence with an independently "
            "implemented Unity port. Judge visible cross-engine fidelity only."
        ),
        "game_id": game_id,
        "task_context": task_context[:12000],
        "reference_timestamps": list(reference_times),
        "candidate_timestamps": list(candidate_times),
        "candidate_frame_contexts": list(candidate_contexts),
        "temporal_alignment": alignment,
        "criteria": {
            "asset_identity": "Are the recognizable characters, environment assets and visual motifs preserved?",
            "scene_progression": "Do the sampled scenes show corresponding gameplay stages and progression?",
        },
        "schema": {
            "criteria": [{
                "id": "one criterion id",
                "verdict": "pass|partial|fail|unverified",
                "credit": "number 0-1 or null",
                "rationale": "visible evidence with frame indices",
                "reference_timestamps": "list of supporting seconds",
                "candidate_timestamps": "list of supporting seconds",
            }]
        },
        "rules": [
            "Return one JSON object and nothing else.",
            "Do not judge code structure, hidden state, input causality or whether the game truly cleared.",
            "Candidate frames may come from separate evaluator-owned scenario runs; use their run/checkpoint labels and never pretend they are one continuous tape.",
            "When temporal_alignment is normalized_whole_run, compare corresponding normalized progress positions, while allowing modest pacing differences between engines.",
            "Do not infer absent frames. Use unverified when the sampled evidence cannot support a criterion.",
            "Large internal implementation differences are allowed; compare observable product behavior and appearance.",
        ],
    }


def _response_json(response: Mapping[str, Any]) -> dict[str, Any]:
    responses_text = extract_responses_text(response)
    if responses_text:
        text = responses_text
    else:
        content = response.get("content")
        if isinstance(content, list):
            text = "\n".join(str(item.get("text") or "") for item in content if isinstance(item, Mapping))
        elif response.get("choices"):
            text = str(response["choices"][0].get("message", {}).get("content", ""))
        else:
            text = str(content or "")
    start, end = text.find("{"), text.rfind("}")
    if start < 0 or end < start:
        raise ValueError("paired VLM response contained no JSON object")
    data = json.loads(text[start:end + 1])
    if not isinstance(data, dict):
        raise ValueError("paired VLM response JSON must be an object")
    return data


def _select_paths(paths: Sequence[str], maximum: int) -> tuple[str, ...]:
    existing = [str(Path(path).resolve()) for path in paths if Path(path).is_file()]
    maximum = max(1, int(maximum))
    if len(existing) <= maximum:
        return tuple(existing)
    if maximum == 1:
        return (existing[len(existing) // 2],)
    indices = [round(index * (len(existing) - 1) / (maximum - 1)) for index in range(maximum)]
    return tuple(existing[index] for index in indices)


def _candidate_timestamp(path: str, index: int) -> float:
    match = re.search(r"frame_(\d+)", Path(path).stem)
    return int(match.group(1)) / 60.0 if match else float(index)


def _candidate_context(
    path: str,
    index: int,
    *,
    timestamp: float | None = None,
    alignment: str = "witness_samples_fallback",
) -> str:
    source = Path(path)
    parents = [part for part in source.parts[-5:-1] if part not in {"capture", "runs"}]
    return (
        f"run/checkpoint={'/'.join(parents) or 'candidate'}; "
        f"frame_file={source.name}; local_time="
        f"{(_candidate_timestamp(path, index) if timestamp is None else timestamp):.3f}s; "
        f"alignment={alignment}"
    )


def _float_tuple(value: Any, fallback: Sequence[float]) -> tuple[float, ...]:
    if not isinstance(value, list):
        return tuple(float(item) for item in fallback)
    out: list[float] = []
    for item in value:
        try:
            out.append(float(item))
        except (TypeError, ValueError):
            continue
    return tuple(out)


def _image_block(path: Path) -> dict[str, Any]:
    return {
        "type": "image",
        "source": {
            "type": "base64",
            "media_type": "image/png",
            "data": base64.b64encode(path.read_bytes()).decode("ascii"),
        },
    }


def _responses_image_block(path: Path) -> dict[str, Any]:
    encoded = base64.b64encode(path.read_bytes()).decode("ascii")
    return {
        "type": "input_image",
        "image_url": f"data:image/png;base64,{encoded}",
        "detail": "high",
    }


def _messages_url(base: str) -> str:
    value = base.rstrip("/")
    if value.endswith("/messages"):
        return value
    return value + ("/messages" if value.endswith("/v1") else "/v1/messages")


def _responses_url(base: str) -> str:
    value = base.rstrip("/")
    if value.endswith("/responses"):
        return value
    return value + ("/responses" if value.endswith("/v1") else "/v1/responses")


def _tool(explicit: str | Path | None, default: str) -> Path | None:
    if explicit:
        path = Path(explicit).expanduser()
        if path.is_file() and os.access(path, os.X_OK):
            return path.resolve()
        found = shutil.which(str(explicit))
        return Path(found).resolve() if found else None
    found = shutil.which(default)
    return Path(found).resolve() if found else None


def _inconclusive(
    detail: str, model: str, video: Path | None, wire_api: str = ""
) -> UnityFidelityResult:
    return UnityFidelityResult(
        "inconclusive",
        detail,
        False,
        model,
        "",
        str(video) if video else "",
        (),
        (),
        (),
        detail,
        wire_api,
    )


__all__ = [
    "CRITERIA",
    "FidelityCriterion",
    "FidelityStatus",
    "UnityFidelityResult",
    "extract_reference_frames",
    "judge_cross_engine_fidelity",
]
