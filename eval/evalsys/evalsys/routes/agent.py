


from __future__ import annotations

import abc
import json
import os
import re
from dataclasses import dataclass, field
from typing import Any, Callable, Iterable, Sequence


CANONICAL_ACTIONS: tuple[str, ...] = (
    "gb_left",
    "gb_right",
    "gb_up",
    "gb_down",
    "gb_jump",
    "gb_action",
)

FORBIDDEN_ACTIONS: tuple[str, ...] = ("gb_pause", "gb_reset")


MAX_OP_FRAMES = 600
MIN_OP_FRAMES = 1


from ..interface.model import AnalogAxis
from .schema import OP_TABLE, OpSpec


@dataclass(frozen=True)
class Op:


    op: str
    action: str = ""
    frames: int = 0
    actions: tuple[str, ...] = ()


    dir: tuple[float, float] = ()
    jump: bool = False


    axes: tuple[tuple[str, float], ...] = ()

    def to_dict(self) -> dict[str, Any]:
        out: dict[str, Any] = {"op": self.op, "frames": self.frames}
        if self.actions:
            out["actions"] = list(self.actions)
        elif self.action:
            out["action"] = self.action
        if self.dir:
            out["dir"] = [float(self.dir[0]), float(self.dir[1])]
        if self.jump:
            out["jump"] = True
        if self.axes:
            out["axes"] = {name: value for name, value in self.axes}
        return out

    @classmethod
    def from_dict(cls, raw: Any) -> "Op":
        if not isinstance(raw, dict):
            return cls(op=str(raw))
        raw_actions = raw.get("actions", [])
        actions = (
            tuple(str(v) for v in raw_actions if str(v))
            if isinstance(raw_actions, (list, tuple))
            else ("__invalid_actions_shape__",)
        )
        raw_dir = raw.get("dir", ())
        direction: tuple[float, float] = ()
        if isinstance(raw_dir, (list, tuple)) and len(raw_dir) >= 2:
            try:
                direction = (float(raw_dir[0]), float(raw_dir[1]))
            except (TypeError, ValueError):
                direction = ()
        raw_axes = raw.get("axes", {})
        axes: tuple[tuple[str, float], ...] = ()
        if raw_axes in (None, {}, ()):
            axes = ()
        elif isinstance(raw_axes, dict):
            pairs: list[tuple[str, float]] = []
            for key, value in raw_axes.items():
                try:
                    pairs.append((str(key), float(value)))
                except (TypeError, ValueError):
                    pairs.append((str(key), float("nan")))
            axes = tuple(sorted(pairs))
        else:
            axes = (("__invalid_axes_shape__", 0.0),)
        return cls(
            op=str(raw.get("op", "")),
            action=str(raw.get("action", "") or ""),
            frames=int(raw.get("frames", 0) or 0),
            actions=actions,
            dir=direction,
            jump=bool(raw.get("jump", False)),
            axes=axes,
        )

    def __str__(self) -> str:
        if self.actions:
            return f"{self.op}({'+'.join(self.actions)}, {self.frames}f)"
        if self.action:
            return f"{self.op}({self.action}, {self.frames}f)"
        return f"{self.op}({self.frames}f)"


@dataclass
class Refusal:


    index: int
    op: dict[str, Any]
    reason: str
    detail: str

    def to_dict(self) -> dict[str, Any]:
        return {
            "index": self.index,
            "op": self.op,
            "reason": self.reason,
            "detail": self.detail,
        }


@dataclass
class OpValidation:
    accepted: list[Op] = field(default_factory=list)
    refusals: list[Refusal] = field(default_factory=list)

    @property
    def ok(self) -> bool:
        return not self.refusals

    @property
    def frames(self) -> int:
        return sum(op.frames for op in self.accepted)

    def to_dict(self) -> dict[str, Any]:
        return {
            "accepted": [o.to_dict() for o in self.accepted],
            "refusals": [r.to_dict() for r in self.refusals],
            "frames": self.frames,
        }


def axis_specs(extra_axes: Sequence[Any] = ()) -> tuple[AnalogAxis, ...]:

    out: list[AnalogAxis] = []
    seen: set[str] = set()
    for item in extra_axes:
        spec = item if isinstance(item, AnalogAxis) else AnalogAxis.from_dict(item)
        if spec is None or not spec.id or spec.id in seen:
            continue
        seen.add(spec.id)
        out.append(spec)
    return tuple(out)


def allowed_action_set(extra_actions: Sequence[str] = ()) -> frozenset[str]:


    extra = [
        action for action in extra_actions
        if action and action not in FORBIDDEN_ACTIONS and not action.startswith("gb_")
    ]
    return frozenset(CANONICAL_ACTIONS) | frozenset(extra)


def mash_ops(
    extra_actions: Sequence[str],
    frames: int,
    extra_axes: Sequence[Any] = (),
) -> list[Op]:


    names = tuple(dict.fromkeys(action for action in extra_actions if action))
    parked = tuple((spec.id, spec.max) for spec in axis_specs(extra_axes))
    if not names and not parked:
        return []
    remaining = max(int(frames), MIN_OP_FRAMES)
    out: list[Op] = []
    while remaining > 0:
        chunk = min(MAX_OP_FRAMES, remaining)
        if names:
            out.append(Op(op="hold", actions=names, frames=chunk, axes=parked))
        else:
            out.append(Op(op="state", frames=chunk, axes=parked))
        remaining -= chunk
    return out


def validate_ops(
    ops: Iterable[Any],
    *,
    op_cap: int | None = None,
    frame_cap: int | None = None,
    extra_actions: Sequence[str] = (),
    extra_axes: Sequence[Any] = (),
) -> OpValidation:


    out = OpValidation()
    specs = {spec.id: spec for spec in axis_specs(extra_axes)}
    for index, raw in enumerate(ops):
        op = raw if isinstance(raw, Op) else Op.from_dict(raw)
        raw_dict = op.to_dict()

        spec = OP_TABLE.get(op.op)
        if spec is None:
            out.refusals.append(
                Refusal(
                    index,
                    raw_dict,
                    "unknown_op",
                    f"{op.op!r} is not in the harness op table {sorted(OP_TABLE)}; "
                    "the action space is not the subject's to declare",
                )
            )
            continue

        if op.action and op.actions:
            out.refusals.append(
                Refusal(
                    index,
                    raw_dict,
                    "ambiguous_actions",
                    "use either `action` or `actions`, never both",
                )
            )
            continue

        frames = op.frames or spec.default_frames
        actions = op.actions or ((op.action,) if op.action else ())

        if spec.needs_action and not actions:
            out.refusals.append(
                Refusal(index, raw_dict, "missing_action", f"{op.op} needs an action")
            )
            continue
        forbidden = [action for action in actions if action in FORBIDDEN_ACTIONS]
        if forbidden:
            out.refusals.append(
                Refusal(
                    index,
                    raw_dict,
                    "forbidden_action",
                    f"{forbidden!r} destroys the state being measured and is never "
                    "dispatched",
                )
            )
            continue
        allowed = allowed_action_set(extra_actions)
        unknown = [action for action in actions if action not in allowed]
        if unknown:
            extras = [a for a in extra_actions if a]
            space = (
                f"{list(CANONICAL_ACTIONS)} plus frozen extras {extras}"
                if extras else f"{list(CANONICAL_ACTIONS)}"
            )
            out.refusals.append(
                Refusal(
                    index,
                    raw_dict,
                    "unknown_action",
                    f"{unknown!r} is outside the dispatched action space {space}",
                )
            )
            continue
        if not (MIN_OP_FRAMES <= frames <= MAX_OP_FRAMES):
            out.refusals.append(
                Refusal(
                    index,
                    raw_dict,
                    "bad_frames",
                    f"{frames} frames is outside [{MIN_OP_FRAMES}, {MAX_OP_FRAMES}]",
                )
            )
            continue
        if op_cap is not None and len(out.accepted) >= op_cap:
            out.refusals.append(
                Refusal(
                    index,
                    raw_dict,
                    "over_op_cap",
                    f"the measured render cost affords {op_cap} ops for this route",
                )
            )
            continue
        if frame_cap is not None and out.frames + frames > frame_cap:
            out.refusals.append(
                Refusal(
                    index,
                    raw_dict,
                    "over_frame_cap",
                    f"{out.frames} + {frames} frames exceeds the {frame_cap}-frame "
                    "budget for this route",
                )
            )
            continue

        if len(set(actions)) != len(actions):
            out.refusals.append(
                Refusal(index, raw_dict, "duplicate_action", "actions must be unique")
            )
            continue

        accepted_axes: tuple[tuple[str, float], ...] = ()
        if op.axes:
            if any(name == "__invalid_axes_shape__" for name, _value in op.axes):
                out.refusals.append(
                    Refusal(
                        index, raw_dict, "bad_axes_shape",
                        "`axes` must be an object mapping declared axis ids to numbers",
                    )
                )
                continue
            unknown_axes = [name for name, _value in op.axes if name not in specs]
            if unknown_axes:
                out.refusals.append(
                    Refusal(
                        index, raw_dict, "unknown_axis",
                        f"{unknown_axes!r} is outside the frozen analog axes "
                        f"{[spec.id for spec in specs.values()]}",
                    )
                )
                continue
            bad_values = [
                name for name, value in op.axes
                if value != value
                or value < specs[name].min - 1e-9
                or value > specs[name].max + 1e-9
            ]
            if bad_values:
                out.refusals.append(
                    Refusal(
                        index, raw_dict, "bad_axis_value",
                        f"{bad_values!r} is outside a declared axis range "
                        "or is not a number",
                    )
                )
                continue
            accepted_axes = tuple(
                (name, specs[name].quantize(value)) for name, value in op.axes
            )

        out.accepted.append(
            Op(
                op=spec.name,
                action=op.action if not op.actions else "",
                frames=frames,
                actions=tuple(actions) if op.actions else (),
                dir=op.dir,
                jump=op.jump,
                axes=accepted_axes,
            )
        )
    return out


def op_table_for_agent(
    extra_actions: Sequence[str] = (),
    extra_axes: Sequence[Any] = (),
) -> dict[str, Any]:


    extras = [action for action in extra_actions if action]
    axes = [spec.to_dict() for spec in axis_specs(extra_axes)]
    actions = list(CANONICAL_ACTIONS)
    for action in extras:
        if action not in actions and action not in FORBIDDEN_ACTIONS:
            actions.append(action)
    note = (
        "This table is fixed by the evaluator. An op or action outside it is "
        "refused and recorded as a refusal against you."
    )
    if extras or axes:
        note += (
            " Extra actions and analog axes were declared by the game in "
            "gb_levels.json and frozen into the interface digest; they are "
            "not yours to add. An op may carry `axes` for frozen axis ids."
        )
    return {
        "ops": {
            name: {
                "needs_action": spec.needs_action,
                "accepts_actions_chord": spec.needs_action,
                "default_frames": spec.default_frames,
                "doc": spec.doc,
            }
            for name, spec in OP_TABLE.items()
        },
        "actions": actions,
        "frames_range": [MIN_OP_FRAMES, MAX_OP_FRAMES],
        "extended_actions": extras,
        "analog_axes": axes,
        "note": note,
    }


class RouteAgent(abc.ABC):


    name: str = "agent"


    provider_available: bool = True
    provider_error: str = ""

    @abc.abstractmethod
    def propose(
        self,
        observation: dict[str, Any],
        route_player_view: dict[str, Any],
        history: Sequence[dict[str, Any]],
    ) -> list[Op]:
        ...

    def describe(self) -> dict[str, Any]:
        return {
            "name": self.name,
            "provider_available": self.provider_available,
            "provider_error": self.provider_error,
        }


class ScriptedAgent(RouteAgent):


    name = "scripted"

    def __init__(self, ops: Sequence[Any], *, name: str = "scripted") -> None:
        self.ops = [o if isinstance(o, Op) else Op.from_dict(o) for o in ops]
        self.name = name
        self._served = False

    def propose(
        self,
        observation: dict[str, Any],
        route_player_view: dict[str, Any],
        history: Sequence[dict[str, Any]],
    ) -> list[Op]:
        if self._served:
            return []
        self._served = True
        return list(self.ops)


class NullAgent(RouteAgent):


    name = "null"

    def propose(
        self,
        observation: dict[str, Any],
        route_player_view: dict[str, Any],
        history: Sequence[dict[str, Any]],
    ) -> list[Op]:
        return []


_JSON_OBJECT = re.compile(r"\{.*\}", re.S)


def extract_json_object(text: str) -> dict[str, Any]:


    text = (text or "").strip()
    if text.startswith("```"):
        text = re.sub(r"^```[a-zA-Z]*\n?", "", text)
        text = re.sub(r"\n?```$", "", text).strip()
    try:
        parsed = json.loads(text)
    except ValueError:
        m = _JSON_OBJECT.search(text)
        if m is None:
            raise ValueError(f"no JSON object in the reply: {text[:200]!r}")
        parsed = json.loads(m.group())
    if not isinstance(parsed, dict):
        raise ValueError(f"reply was {type(parsed).__name__}, expected an object")
    return parsed


def extract_response_text(response: dict[str, Any]) -> str:

    content = response.get("content")
    if isinstance(content, list):
        parts = [
            str(block.get("text", ""))
            for block in content
            if isinstance(block, dict) and block.get("type") in (None, "text")
        ]
        if parts:
            return "\n".join(parts)
    choices = response.get("choices")
    if isinstance(choices, list) and choices:
        message = choices[0].get("message") if isinstance(choices[0], dict) else None
        if isinstance(message, dict):
            return str(message.get("content", ""))
    if isinstance(content, str):
        return content
    raise ValueError(f"cannot find text in the provider reply: {str(response)[:200]!r}")


def anthropic_api_url(base_url: str) -> str:
    base = (base_url or "").rstrip("/")
    return base if base.endswith("/messages") else f"{base}/messages"


def default_anthropic_requester(
    payload: dict[str, Any], api_key: str, api_url: str, timeout: float = 120.0
) -> dict[str, Any]:

    import urllib.request

    request = urllib.request.Request(
        api_url,
        data=json.dumps(payload).encode("utf-8"),
        headers={
            "content-type": "application/json",
            "x-api-key": api_key,
            "anthropic-version": "2023-06-01",
        },
        method="POST",
    )
    with urllib.request.urlopen(request, timeout=timeout) as response:
        return json.loads(response.read().decode("utf-8"))


_KEY_ENV = ("ANTHROPIC_API_KEY", "CLAUDE_API_KEY", "AUTO_CODE_API_KEY")


@dataclass
class LLMRouteAgent(RouteAgent):


    api_key: str
    base_url: str = "https://vip.auto-code.net/v1"
    model: str = "opus-4-7"
    requester: Callable[[dict[str, Any], str, str], dict[str, Any]] = (
        default_anthropic_requester
    )
    max_tokens: int = 1024
    max_ops_per_turn: int = 8
    name: str = "llm"
    provider_available: bool = True
    provider_error: str = ""

    transcript: list[dict[str, Any]] = field(default_factory=list)

    @property
    def api_url(self) -> str:
        return anthropic_api_url(self.base_url)

    @classmethod
    def from_env(
        cls,
        requester: Callable[[dict[str, Any], str, str], dict[str, Any]] | None = None,
    ) -> "LLMRouteAgent":


        api_key = next((os.environ[k] for k in _KEY_ENV if os.environ.get(k)), "")
        if not api_key:
            raise RuntimeError(
                f"no LLM key in the environment; set one of {list(_KEY_ENV)}"
            )
        return cls(
            api_key=api_key,
            base_url=os.environ.get("ANTHROPIC_BASE_URL")
            or os.environ.get("CLAUDE_BASE_URL")
            or "https://vip.auto-code.net/v1",
            model=os.environ.get("ANTHROPIC_MODEL")
            or os.environ.get("CLAUDE_MODEL")
            or "opus-4-7",
            requester=requester or default_anthropic_requester,
        )

    def _prompt(
        self,
        observation: dict[str, Any],
        route_player_view: dict[str, Any],
        history: Sequence[dict[str, Any]],
    ) -> dict[str, Any]:
        return {
            "task": (
                "You are playing a short segment of a video game through a fixed "
                "macro-instruction interface. Emit the next few operations."
            ),
            "route": route_player_view,
            "observation": observation,
            "history": list(history)[-6:],
            "action_space": op_table_for_agent(),
            "schema": {
                "ops": "list of {op, action, frames}; at most "
                f"{self.max_ops_per_turn} entries",
                "rationale": "one sentence",
            },
            "rules": [
                "Return JSON only, no prose and no code fences.",
                "Every op must come from action_space.ops and every action from "
                "action_space.actions.",
                "Emit an empty ops list only if you intend to stand still.",
            ],
        }

    def propose(
        self,
        observation: dict[str, Any],
        route_player_view: dict[str, Any],
        history: Sequence[dict[str, Any]],
    ) -> list[Op]:
        payload = {
            "model": self.model,
            "max_tokens": self.max_tokens,
            "messages": [
                {
                    "role": "user",
                    "content": json.dumps(
                        self._prompt(observation, route_player_view, history),
                        ensure_ascii=False,
                    ),
                }
            ],
        }
        try:
            response = self.requester(payload, self.api_key, self.api_url)
            data = extract_json_object(extract_response_text(response))
        except Exception as exc:


            self.provider_available = False
            self.provider_error = f"{type(exc).__name__}: {exc}"
            self.transcript.append({"error": self.provider_error})
            return []
        raw_ops = data.get("ops")
        if not isinstance(raw_ops, list):
            self.provider_available = False
            self.provider_error = "reply had no `ops` list"
            self.transcript.append({"reply": data, "error": self.provider_error})
            return []
        self.transcript.append({"reply": data})
        return [Op.from_dict(o) for o in raw_ops[: self.max_ops_per_turn]]


@dataclass
class AgentProvision:


    agent: RouteAgent | None
    available: bool
    provider: str
    model: str = ""
    error: str = ""

    def to_dict(self) -> dict[str, Any]:
        return {
            "available": self.available,
            "provider": self.provider,
            "model": self.model,
            "error": self.error,
            "provider_available": self.available,
        }


def llm_agent_from_env(
    requester: Callable[[dict[str, Any], str, str], dict[str, Any]] | None = None,
) -> AgentProvision:


    try:
        agent = LLMRouteAgent.from_env(requester)
    except RuntimeError as exc:
        return AgentProvision(
            None,
            False,
            "anthropic",
            os.environ.get("ANTHROPIC_MODEL", "") or os.environ.get("CLAUDE_MODEL", ""),
            str(exc),
        )
    return AgentProvision(agent, True, "anthropic", agent.model)
