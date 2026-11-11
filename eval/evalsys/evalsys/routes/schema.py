


from __future__ import annotations

import json
import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Iterable, Sequence


@dataclass(frozen=True)
class OpSpec:
    name: str
    needs_action: bool
    default_frames: int
    doc: str


OP_TABLE: dict[str, OpSpec] = {
    "noop": OpSpec("noop", False, 1, "do nothing for one frame"),
    "wait": OpSpec("wait", False, 30, "hold no input for `frames`"),
    "tap": OpSpec("tap", True, 6, "press `action` or the `actions` chord briefly, then release"),
    "hold": OpSpec("hold", True, 30, "hold `action` or every action in `actions` for `frames`, then release"),
    "state": OpSpec(
        "state", False, 1,
        "maintain this exact canonical action set for `frames`; adjacent state ops "
        "change only keys whose state differs",
    ),
    "release": OpSpec("release", True, 1, "release `action` or every action in `actions`"),
}


CANONICAL_GROUPS = (
    "gb_player",
    "gb_goal",
    "gb_collectible",
    "gb_hazard",
    "gb_enemy",
    "gb_checkpoint",
    "gb_door",
    "gb_interactive",
)


PREDICATE_FUNCTIONS: dict[str, int] = {
    "overlap": 2,
    "collected_all": 1,
    "count": 1,
    "alive": 1,
    "norm_delta": 1,
    "norm_in": 3,
    "numeric": 1,
    "numeric_delta": 1,
    "count_delta": 1,
    "whole_game_clear": 0,
    "levels_visited": 0,
    "contact": 2,
    "device_present": 2,
    "standing_on": 2,
    "device_norm_delta": 3,
    "device_state": 4,
    "device_numeric": 3,
    "device_numeric_delta": 3,
}

_BOOLEAN_FUNCTIONS = frozenset(
    {"overlap", "collected_all", "alive", "norm_in", "whole_game_clear",
     "contact", "device_present", "standing_on", "device_state"}
)
_NUMERIC_FUNCTIONS = frozenset(
    {"count", "norm_delta", "numeric", "numeric_delta", "count_delta",
     "levels_visited", "device_norm_delta", "device_numeric",
     "device_numeric_delta"}
)

_FLOAT_NUMERIC_FUNCTIONS = frozenset(
    {"norm_delta", "numeric", "numeric_delta", "device_norm_delta",
     "device_numeric", "device_numeric_delta"}
)
_AXES = frozenset({"x", "y", "z"})
_NUMERIC_NAME = re.compile(r"^[a-z][a-z0-9_]*$")
_NUMBER_ARG = re.compile(r"^-?\d+(?:\.\d+)?(?:[eE][-+]?\d+)?$")
_STATE_NODE_PATH = re.compile(
    r"^[A-Za-z0-9_.-]+(?:/[A-Za-z0-9_.-]+)*$"
)
_STATE_PROPERTY = re.compile(r"^_?[A-Za-z][A-Za-z0-9_]*$")


EVALUATOR_ONLY_FIELDS = ("required_devices", "authored_solution")


DEVICE_ID = re.compile(r"^(?:L\d+/)?[a-z][a-z0-9_]*#\d+$")

_GROUP_NAME = re.compile(r"^gb_[a-z][a-z0-9_]*$")

TIERS = (0, 1, 2, 3, 4, 5)


PROVENANCE_KINDS = ("E", "H", "M", "withheld")


class PredicateError(ValueError):
    pass


@dataclass(frozen=True)
class Call:
    name: str
    args: tuple[str, ...]

    def __str__(self) -> str:
        return f"{self.name}({', '.join(self.args)})"


@dataclass(frozen=True)
class Compare:
    call: Call
    op: str
    number: float

    def __str__(self) -> str:
        n = self.number
        rendered = str(int(n)) if n == int(n) else str(n)
        return f"{self.call} {self.op} {rendered}"


@dataclass(frozen=True)
class Not:
    operand: Any

    def __str__(self) -> str:
        return f"NOT ({self.operand})"


@dataclass(frozen=True)
class BinOp:
    op: str
    left: Any
    right: Any

    def __str__(self) -> str:
        return f"({self.left} {self.op} {self.right})"


def _requires_nonzero_state_change(node: Any) -> bool:

    if isinstance(node, BinOp):
        left = _requires_nonzero_state_change(node.left)
        right = _requires_nonzero_state_change(node.right)
        return (left or right) if node.op == "AND" else (left and right)
    if not isinstance(node, Compare) or node.call.name not in {
        "numeric_delta", "device_numeric_delta", "count_delta",
    }:
        return False
    return (
        (node.op == "==" and node.number != 0)
        or (node.op == "!=" and node.number == 0)
        or (node.op == ">" and node.number >= 0)
        or (node.op == ">=" and node.number > 0)
        or (node.op == "<" and node.number <= 0)
        or (node.op == "<=" and node.number < 0)
    )


_TOKEN = re.compile(
    r"""
      (?P<ws>\s+)
    | (?P<lparen>\()
    | (?P<rparen>\))
    | (?P<comma>,)
    | (?P<cmp>>=|<=|==|!=|>|<)
    | (?P<number>-?\d+(?:\.\d+)?(?:[eE][-+]?\d+)?)
    | (?P<ident>[A-Za-z_][A-Za-z0-9_]*)
    """,
    re.X,
)


@dataclass(frozen=True)
class Token:
    kind: str
    text: str
    pos: int


def tokenize(text: str) -> list[Token]:
    out: list[Token] = []
    i = 0
    while i < len(text):
        m = _TOKEN.match(text, i)
        if m is None:
            raise PredicateError(
                f"character {text[i]!r} at offset {i} is not part of the "
                "predicate grammar"
            )
        i = m.end()
        kind = m.lastgroup or ""
        if kind == "ws":
            continue
        out.append(Token(kind, m.group(), m.start()))
    return out


class _Parser:


    def __init__(self, tokens: Sequence[Token]) -> None:
        self.tokens = list(tokens)
        self.i = 0

        self.numbers: list[tuple[Token, bool]] = []
        self.calls: list[Call] = []

    def peek(self) -> Token | None:
        return self.tokens[self.i] if self.i < len(self.tokens) else None

    def take(self) -> Token:
        if self.i >= len(self.tokens):
            raise PredicateError("predicate ended early")
        tok = self.tokens[self.i]
        self.i += 1
        return tok

    def _is_keyword(self, tok: Token | None, word: str) -> bool:
        return tok is not None and tok.kind == "ident" and tok.text.upper() == word

    def parse(self) -> Any:
        node = self.or_expr()
        if self.i != len(self.tokens):
            tok = self.tokens[self.i]
            raise PredicateError(f"unexpected {tok.text!r} at offset {tok.pos}")
        return node

    def or_expr(self) -> Any:
        node = self.and_expr()
        while self._is_keyword(self.peek(), "OR"):
            self.take()
            node = BinOp("OR", node, self.and_expr())
        return node

    def and_expr(self) -> Any:
        node = self.unary()
        while self._is_keyword(self.peek(), "AND"):
            self.take()
            node = BinOp("AND", node, self.unary())
        return node

    def unary(self) -> Any:
        tok = self.peek()
        if self._is_keyword(tok, "NOT"):
            self.take()
            return Not(self.unary())
        if tok is not None and tok.kind == "lparen":
            self.take()
            node = self.or_expr()
            closing = self.take()
            if closing.kind != "rparen":
                raise PredicateError(f"expected ')' at offset {closing.pos}")
            return node
        return self.atom()

    def atom(self) -> Any:
        tok = self.take()
        if tok.kind == "number":


            self.numbers.append((tok, False))
            raise PredicateError(
                f"numeric literal {tok.text!r} at offset {tok.pos} is not a "
                "condition; predicates may not name positions"
            )
        if tok.kind != "ident":
            raise PredicateError(f"expected a condition at offset {tok.pos}")
        name = tok.text
        if self.peek() is None or self.peek().kind != "lparen":
            raise PredicateError(
                f"{name!r} at offset {tok.pos} is not a call; the only legal "
                f"conditions are {sorted(PREDICATE_FUNCTIONS)}"
            )
        self.take()
        args: list[str] = []
        while True:
            nxt = self.take()
            if nxt.kind == "rparen":
                break
            if nxt.kind == "number":

                self.numbers.append((nxt, False))
                args.append(nxt.text)
            elif nxt.kind == "ident":
                args.append(nxt.text)
            else:
                raise PredicateError(f"bad argument at offset {nxt.pos}")
            after = self.peek()
            if after is not None and after.kind == "comma":
                self.take()
                continue
            closing = self.take()
            if closing.kind != "rparen":
                raise PredicateError(f"expected ')' at offset {closing.pos}")
            break
        call = Call(name, tuple(args))
        self.calls.append(call)

        nxt = self.peek()
        if nxt is not None and nxt.kind == "cmp":
            self.take()
            num = self.take()
            if num.kind != "number":
                raise PredicateError(
                    f"comparison at offset {nxt.pos} needs a number on the right"
                )
            legal = False
            if name in ("count", "count_delta") and "." not in num.text and "e" not in num.text.lower():
                legal = True
            elif name in _FLOAT_NUMERIC_FUNCTIONS:
                legal = True
            self.numbers.append((num, legal))
            if name not in _NUMERIC_FUNCTIONS:
                raise PredicateError(
                    f"{name!r} yields a truth value and cannot be compared "
                    "against a number"
                )
            if name in ("count", "count_delta") and ("." in num.text or "e" in num.text.lower()):
                raise PredicateError(
                    f"{num.text!r} at offset {num.pos} is not an integer count; "
                    "a fractional literal in a predicate is a coordinate"
                )
            value = float(num.text)
            if name == "norm_delta" and not -1.0 <= value <= 1.0:
                raise PredicateError(
                    f"{num.text!r} at offset {num.pos}: norm_delta compares a "
                    "signed fraction of the level extent, so the right-hand side "
                    "must be in [-1, 1], never a world coordinate"
                )
            return Compare(call, nxt.text, value)

        if name in _NUMERIC_FUNCTIONS:
            raise PredicateError(
                f"{name}(...) yields a number and must be compared, e.g. "
                f"'{name}({', '.join(args) or 'gb_collectible'}) >= 1'"
            )
        return call


@dataclass
class Predicate:


    source: str
    ast: Any
    groups: tuple[str, ...]
    calls: tuple[Call, ...]

    def evaluate(self, observation: dict[str, Any]) -> bool:
        return bool(self.evaluate_detail(observation)[0])

    def evaluate_detail(self, observation: dict[str, Any]) -> tuple[bool, list[str]]:


        missing: list[str] = []
        value = _eval(self.ast, observation, missing)
        return value, sorted(set(missing))

    def stop_hints(self) -> dict[str, list[str]]:


        overlap: list[str] = []
        collect: list[str] = []
        for call in self.calls:
            if call.name == "overlap":
                overlap.extend(a for a in call.args if a != "gb_player")
            elif call.name == "collected_all":
                collect.extend(call.args)
        return {
            "overlap_groups": sorted(set(overlap)),
            "collect_all_groups": sorted(set(collect)),
        }


def _level_relative_device_id(device_id: str, obs: dict[str, Any]) -> str:


    if "/" in device_id:
        return device_id
    n = int(obs.get("levels_visited") or 0)
    if n <= 0:
        return device_id
    return f"L{n}/{device_id}"


def _lookup_device_entry(devices: dict[str, Any], device_id: str) -> dict[str, Any] | None:
    if not isinstance(devices, dict):
        return None
    entry = devices.get(device_id)
    if isinstance(entry, dict):
        return entry
    bare = device_id.split("/", 1)[-1]
    matches = [devices[k] for k in devices if k == bare or k.endswith("/" + bare)]
    if len(matches) == 1 and isinstance(matches[0], dict):
        return matches[0]
    return None


def _bare_or_qualified_in(device_id: str, contacts: set[str]) -> bool:
    bare = device_id.split("/", 1)[-1]
    return any(c == device_id or c == bare or c.endswith("/" + bare) for c in contacts)


def _eval(node: Any, obs: dict[str, Any], missing: list[str]) -> bool:
    if isinstance(node, BinOp):
        left = _eval(node.left, obs, missing)
        right = _eval(node.right, obs, missing)
        return (left and right) if node.op == "AND" else (left or right)
    if isinstance(node, Not):
        return not _eval(node.operand, obs, missing)
    if isinstance(node, Compare):
        n = _numeric_value(node.call, obs, missing)
        return _compare(n, node.op, node.number)
    if isinstance(node, Call):
        if node.name == "overlap":
            return _overlap(node.args[0], node.args[1], obs, missing)
        if node.name == "collected_all":
            g = node.args[0]
            entry = _group(g, obs, missing)
            if entry is None:
                return False
            return int(entry.get("total", 0)) > 0 and int(entry.get("alive", 0)) == 0
        if node.name == "alive":
            return _count(node.args[0], obs, missing) > 0
        if node.name == "norm_in":
            axis, lo_s, hi_s = node.args
            pos = _norm_axis(axis, obs, missing)
            if pos is None:
                return False
            return float(lo_s) <= pos <= float(hi_s)
        if node.name == "whole_game_clear":
            return bool(obs.get("whole_game_clear", False))
        if node.name in ("contact", "device_present", "standing_on"):
            kind, index = node.args
            device_id = f"{kind}#{int(index)}"
            device_id = _level_relative_device_id(device_id, obs)
            if node.name == "contact":
                contacts = set(obs.get("contacts") or [])
                return device_id in contacts or _bare_or_qualified_in(device_id, contacts)
            if node.name == "standing_on":
                stood = str(obs.get("standing_on", ""))
                return stood == device_id or stood.endswith("/" + device_id.split("/", 1)[-1])
            devices = obs.get("devices") or {}
            entry = _lookup_device_entry(devices, device_id)
            if entry is None:
                missing.append(f"device:{device_id}")
                return False
            return bool(entry.get("present", False))
        if node.name == "device_state":
            kind, index, field, expected = node.args
            device_id = _level_relative_device_id(f"{kind}#{int(index)}", obs)
            entry = _lookup_device_entry(obs.get("devices") or {}, device_id)
            if not isinstance(entry, dict) or field not in entry:
                missing.append(f"device_state:{device_id}:{field}")
                return False
            return str(entry[field]).lower() == expected.lower()
    raise PredicateError(f"cannot evaluate node {node!r}")


def _numeric_value(call: Call, obs: dict[str, Any], missing: list[str]) -> float:
    if call.name == "levels_visited":


        value = obs.get("levels_visited")
        if value is None:
            missing.append("levels_visited")
            return 0.0
        return float(value)
    if call.name == "count":
        return float(_count(call.args[0], obs, missing))
    if call.name == "numeric":
        bag = obs.get("numeric") or {}
        key = call.args[0]
        if key not in bag:
            missing.append(f"numeric:{key}")
            return 0.0
        return float(bag[key])
    if call.name == "numeric_delta":
        bag = obs.get("numeric") or {}
        start = obs.get("start_numeric") or {}
        key = call.args[0]
        if key not in bag:
            missing.append(f"numeric:{key}")
            return 0.0
        if key not in start:
            missing.append(f"start_numeric:{key}")
            return 0.0
        return float(bag[key]) - float(start[key])
    if call.name == "count_delta":
        key = call.args[0]
        now = _count(key, obs, missing)
        start = obs.get("start_groups") or {}
        if key not in start:
            missing.append(f"start_group:{key}")
            return 0.0
        return float(now - int(start[key]))
    if call.name == "norm_delta":
        axis = call.args[0]
        now = _player_axis(axis, obs, missing)
        start = _start_axis(axis, obs, missing)
        extent = _extent_axis(axis, obs, missing)
        if now is None or start is None or extent is None or extent <= 1e-6:
            return 0.0
        return (now - start) / extent
    if call.name == "device_norm_delta":
        kind, index, axis = call.args
        device_id = f"{kind}#{int(index)}"
        devices = obs.get("devices") or {}
        start_devices = obs.get("start_devices") or {}
        now = devices.get(device_id)
        start = start_devices.get(device_id)
        extent = _extent_axis(axis, obs, missing)
        if not isinstance(now, dict) or not isinstance(start, dict):
            missing.append(f"device:{device_id}")
            return 0.0
        if axis not in now or axis not in start or extent is None or extent <= 1e-6:
            missing.append(f"device_axis:{device_id}:{axis}")
            return 0.0
        return (float(now[axis]) - float(start[axis])) / extent
    if call.name in ("device_numeric", "device_numeric_delta"):
        kind, index, field = call.args
        device_id = f"{kind}#{int(index)}"
        devices = obs.get("devices") or {}
        entry = devices.get(device_id)
        if not isinstance(entry, dict) or not isinstance(entry.get(field), (int, float)):
            missing.append(f"device_numeric:{device_id}:{field}")
            return 0.0
        value = float(entry[field])
        if call.name == "device_numeric_delta":
            start = (obs.get("start_devices") or {}).get(device_id)
            if not isinstance(start, dict) or not isinstance(start.get(field), (int, float)):
                missing.append(f"start_device_numeric:{device_id}:{field}")
                return 0.0
            value -= float(start[field])
        return value
    raise PredicateError(f"cannot take a numeric value from {call!r}")


def _compare(left: float, op: str, right: float) -> bool:
    return {
        ">=": left >= right,
        "<=": left <= right,
        "==": left == right,
        "!=": left != right,
        ">": left > right,
        "<": left < right,
    }[op]


def _player_axis(axis: str, obs: dict[str, Any], missing: list[str]) -> float | None:
    player = obs.get("player")
    if not isinstance(player, dict) or axis not in player:
        missing.append("player")
        return None
    return float(player[axis])


def _start_axis(axis: str, obs: dict[str, Any], missing: list[str]) -> float | None:
    start = obs.get("start_player") or obs.get("player")
    if not isinstance(start, dict) or axis not in start:
        missing.append("start_player")
        return None
    return float(start[axis])


def _extent_axis(axis: str, obs: dict[str, Any], missing: list[str]) -> float | None:
    extent = obs.get("extent") or {}
    if not isinstance(extent, dict) or axis not in extent:
        missing.append("extent")
        return None
    return float(extent[axis])


def _norm_axis(axis: str, obs: dict[str, Any], missing: list[str]) -> float | None:
    now = _player_axis(axis, obs, missing)
    origin = obs.get("origin") or {}
    extent = _extent_axis(axis, obs, missing)
    if now is None or extent is None or extent <= 1e-6:
        return None
    if not isinstance(origin, dict) or axis not in origin:
        missing.append("origin")
        return None
    return (now - float(origin[axis])) / extent


def _group(name: str, obs: dict[str, Any], missing: list[str]) -> dict[str, Any] | None:
    groups = obs.get("groups") or {}
    entry = groups.get(name)
    if not isinstance(entry, dict):
        missing.append(name)
        return None
    return entry


def _count(name: str, obs: dict[str, Any], missing: list[str]) -> int:
    entry = _group(name, obs, missing)
    return 0 if entry is None else int(entry.get("alive", 0))


def _overlap(a: str, b: str, obs: dict[str, Any], missing: list[str]) -> bool:
    overlaps = obs.get("overlaps") or {}
    if a == "gb_player" or b == "gb_player":
        other = b if a == "gb_player" else a
        if other not in overlaps:
            missing.append(other)
            return False
        return bool(overlaps.get(other))
    for key in (f"{a}|{b}", f"{b}|{a}"):
        if key in overlaps:
            return bool(overlaps[key])
    missing.append(f"{a}|{b}")
    return False


@dataclass
class PredicateCheck:


    source: str
    ok: bool
    predicate: Predicate | None
    findings: list[str] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return {
            "source": self.source,
            "ok": self.ok,
            "findings": self.findings,
            "warnings": self.warnings,
            "groups": list(self.predicate.groups) if self.predicate else [],
        }


def parse_predicate(text: str) -> Predicate:

    parser = _Parser(tokenize(text))
    ast = parser.parse()
    groups: list[str] = []
    for call in parser.calls:
        arity = PREDICATE_FUNCTIONS.get(call.name)
        if arity is None:
            raise PredicateError(
                f"{call.name!r} is not a predicate function; legal functions "
                f"are {sorted(PREDICATE_FUNCTIONS)}"
            )
        if len(call.args) != arity:
            raise PredicateError(
                f"{call.name} takes {arity} argument(s), got {len(call.args)}"
            )
        _validate_call_args(call)
        for arg in call.args:
            if _GROUP_NAME.match(arg):
                groups.append(arg)
    return Predicate(text, ast, tuple(sorted(set(groups))), tuple(parser.calls))


def _validate_call_args(call: Call) -> None:


    name, args = call.name, call.args
    if name in ("overlap", "collected_all", "count", "count_delta", "alive"):
        for arg in args:
            if not _GROUP_NAME.match(arg):
                raise PredicateError(
                    f"{arg!r} is not a gb_* group. A predicate may only name the "
                    "generic vocabulary, because it has to run against a "
                    "submission whose node names are its own."
                )
        return
    if name == "norm_delta":
        if args[0] not in _AXES:
            raise PredicateError(
                f"norm_delta axis must be one of {sorted(_AXES)}, got {args[0]!r}"
            )
        return
    if name in ("numeric", "numeric_delta"):
        if not _NUMERIC_NAME.match(args[0]):
            raise PredicateError(
                f"{args[0]!r} is not a numeric property name "
                "(lowercase identifier such as fuse_left, shields, heat)"
            )
        return
    if name in ("contact", "device_present", "standing_on"):
        kind, index = args
        if not _NUMERIC_NAME.match(kind) or not index.isdigit():
            raise PredicateError(
                f"{name}(kind, index) needs a lowercase semantic kind and integer index"
            )
        return
    if name == "device_norm_delta":
        kind, index, axis = args
        if not _NUMERIC_NAME.match(kind) or not index.isdigit() or axis not in _AXES:
            raise PredicateError(
                "device_norm_delta(kind, index, axis) needs a semantic kind, "
                "integer index and x/y/z axis"
            )
        return
    if name in ("device_numeric", "device_numeric_delta"):
        kind, index, field = args
        if not _NUMERIC_NAME.match(kind) or not index.isdigit() or not _NUMERIC_NAME.match(field):
            raise PredicateError(
                f"{name}(kind, index, field) needs lowercase identifiers and integer index"
            )
        return
    if name == "device_state":
        kind, index, field, value = args
        if (
            not _NUMERIC_NAME.match(kind)
            or not index.isdigit()
            or not _NUMERIC_NAME.match(field)
            or not _NUMERIC_NAME.match(value)
        ):
            raise PredicateError(
                "device_state(kind, index, field, value) accepts lowercase identifiers"
            )
        return
    if name in ("whole_game_clear", "levels_visited"):
        return
    if name == "norm_in":
        axis, lo_s, hi_s = args
        if axis not in _AXES:
            raise PredicateError(
                f"norm_in axis must be one of {sorted(_AXES)}, got {axis!r}"
            )
        if not _NUMBER_ARG.match(lo_s) or not _NUMBER_ARG.match(hi_s):
            raise PredicateError(
                "norm_in(axis, lo, hi) needs numeric lo and hi in [0, 1]"
            )
        lo, hi = float(lo_s), float(hi_s)
        if not (0.0 <= lo <= 1.0 and 0.0 <= hi <= 1.0):
            raise PredicateError(
                f"norm_in bounds [{lo}, {hi}] must each lie in [0, 1]; "
                "world coordinates are not a predicate"
            )
        if lo > hi:
            raise PredicateError(f"norm_in lo {lo} is greater than hi {hi}")
        return
    raise PredicateError(f"{name!r} has no argument rule")


def validate_predicate(text: str) -> PredicateCheck:


    if not isinstance(text, str) or not text.strip():
        return PredicateCheck(str(text), False, None, ["empty predicate"])
    try:
        pred = parse_predicate(text)
    except PredicateError as exc:
        return PredicateCheck(text, False, None, [str(exc)])
    warnings = [
        f"{g!r} is outside the canonical ontology {list(CANONICAL_GROUPS)}; a "
        "misspelled group evaluates to False and is indistinguishable from a "
        "submission that never got there"
        for g in pred.groups
        if g not in CANONICAL_GROUPS
    ]
    return PredicateCheck(text, True, pred, [], warnings)


@dataclass
class RouteStart:


    level: int | str = 0
    inject: dict[str, Any] = field(default_factory=dict)
    at_device: str = ""
    anchor: str = ""
    offset_norm: dict[str, float] = field(default_factory=dict)
    settle_frames: int = 30

    @property
    def has_inject(self) -> bool:
        return (
            (bool(self.inject) and any(bool(v) for v in self.inject.values()))
            or bool(self.at_device)
        )

    def to_dict(self) -> dict[str, Any]:
        out = {"level": self.level, "inject": self.inject}
        if self.at_device:
            out["at_device"] = self.at_device
        if self.anchor:
            out["anchor"] = self.anchor
        if self.offset_norm:
            out["offset_norm"] = dict(self.offset_norm)
        if self.settle_frames != 30:
            out["settle_frames"] = self.settle_frames
        return out


BASELINE_SEGMENT_START = "segment_start"
BASELINE_PREVIOUS_ROW = "previous_row"
MILESTONE_BASELINES = frozenset({BASELINE_SEGMENT_START, BASELINE_PREVIOUS_ROW})


@dataclass(frozen=True)
class Milestone:


    name: str
    predicate: str
    required_devices: tuple[str, ...] = ()


    baseline: str = BASELINE_SEGMENT_START


    trigger: str = ""

    @classmethod
    def from_value(cls, value: Any, index: int = 0) -> "Milestone":
        if isinstance(value, cls):
            return value
        if isinstance(value, dict):
            baseline = str(value.get("baseline") or BASELINE_SEGMENT_START)
            if baseline not in MILESTONE_BASELINES:
                raise ValueError(
                    f"milestone baseline must be one of {sorted(MILESTONE_BASELINES)}, "
                    f"got {baseline!r}"
                )
            return cls(
                str(value.get("name", f"m{index + 1}")),
                str(value.get("predicate", "")),
                tuple(str(d) for d in value.get("required_devices") or ()),
                baseline,
                str(value.get("trigger") or ""),
            )
        return cls(f"m{index + 1}", str(value))

    def to_dict(self) -> dict[str, Any]:
        out: dict[str, Any] = {"name": self.name, "predicate": self.predicate}
        if self.required_devices:
            out["required_devices"] = list(self.required_devices)
        if self.baseline != BASELINE_SEGMENT_START:
            out["baseline"] = self.baseline
        if self.trigger:
            out["trigger"] = self.trigger
        return out

    def player_dict(self) -> dict[str, str]:

        return {"name": self.name, "predicate": self.predicate}

    def __str__(self) -> str:
        return self.predicate

    def __contains__(self, value: str) -> bool:
        return value in self.predicate


@dataclass
class Goal:
    predicate: str


    end_predicate: str = ""


    milestones: list[Milestone] = field(default_factory=list)
    invariants: list[Milestone] = field(default_factory=list)


    observations: list[Milestone] = field(default_factory=list)

    def __post_init__(self) -> None:
        self.milestones = [Milestone.from_value(v, i) for i, v in enumerate(self.milestones)]
        self.invariants = [Milestone.from_value(v, i) for i, v in enumerate(self.invariants)]
        self.observations = [Milestone.from_value(v, i) for i, v in enumerate(self.observations)]

    @classmethod
    def from_dict(cls, raw: dict[str, Any]) -> "Goal":
        return cls(
            str(raw.get("predicate", "")),
            str(raw.get("end_predicate", "")),
            [Milestone.from_value(value, i) for i, value in enumerate(raw.get("milestones") or [])],
            [Milestone.from_value(value, i) for i, value in enumerate(raw.get("invariants") or [])],
            [Milestone.from_value(value, i) for i, value in enumerate(raw.get("observations") or [])],
        )

    def to_dict(self) -> dict[str, Any]:
        out = {"predicate": self.predicate}
        if self.end_predicate:
            out["end_predicate"] = self.end_predicate
        if self.milestones:
            out["milestones"] = [m.to_dict() for m in self.milestones]
        if self.invariants:
            out["invariants"] = [m.to_dict() for m in self.invariants]
        if self.observations:
            out["observations"] = [m.to_dict() for m in self.observations]
        return out


@dataclass
class Budget:


    steps: int = 0
    frames: int = 0

    def to_dict(self) -> dict[str, Any]:
        return {"steps": self.steps, "frames": self.frames}


@dataclass
class Provenance:


    kind: str = "H"
    source: str = ""
    drafting_model: str = ""
    date: str = ""
    reviewer: str = ""
    diff_lines: int = 0

    def __post_init__(self) -> None:
        if self.kind not in PROVENANCE_KINDS:
            raise ValueError(
                f"provenance {self.kind!r} is not one of {PROVENANCE_KINDS}"
            )

    def findings(self, route_id: str) -> list[str]:
        if self.kind != "M":
            return []
        out = []
        for attr in ("drafting_model", "date", "reviewer"):
            if not getattr(self, attr):
                out.append(
                    f"{route_id}: provenance M without {attr}; an unattributed "
                    "model-drafted route is an anonymous one"
                )
        if self.diff_lines < 0:
            out.append(f"{route_id}: negative diff_lines")
        return out

    def to_dict(self) -> dict[str, Any]:
        return {
            "kind": self.kind,
            "source": self.source,
            "drafting_model": self.drafting_model,
            "date": self.date,
            "reviewer": self.reviewer,
            "diff_lines": self.diff_lines,
        }


@dataclass
class Route:


    route_id: str
    tier: int
    start: RouteStart = field(default_factory=RouteStart)
    goal: Goal = field(default_factory=lambda: Goal(""))
    budget: Budget = field(default_factory=Budget)
    required_devices: list[str] = field(default_factory=list)
    authored_solution: dict[str, Any] = field(default_factory=dict)
    provenance: Provenance = field(default_factory=Provenance)
    notes: str = ""
    scored: bool = True


    continue_after_failure: bool = False


    continue_after_success: bool = False


    success_scope: str = "declared_ending"

    @property
    def predicate(self) -> Predicate | None:
        check = validate_predicate(self.goal.predicate)
        return check.predicate

    def validate(self) -> list[str]:

        out: list[str] = []
        if not self.route_id:
            out.append("route_id is empty")
        if self.tier not in TIERS:
            out.append(f"{self.route_id}: tier {self.tier} outside {TIERS}")
        if self.continue_after_success and self.tier not in (1, 2, 3, 4):
            out.append(
                f"{self.route_id}: continue_after_success requires a segment transaction tier 1-4"
            )
        if self.success_scope not in {"declared_ending", "all_declared_levels"}:
            out.append(f"{self.route_id}: unsupported success_scope {self.success_scope!r}")
        elif self.success_scope == "all_declared_levels" and self.tier != 5:
            out.append(f"{self.route_id}: all_declared_levels success_scope requires tier 5")
        check = validate_predicate(self.goal.predicate)
        if self.tier == 0 and not self.goal.predicate.strip() and not self.goal.end_predicate.strip():


            pass
        elif not check.ok:
            out.append(f"{self.route_id}: goal predicate rejected: {check.findings}")
        if self.goal.end_predicate.strip():
            end_check = validate_predicate(self.goal.end_predicate)
            if not end_check.ok:
                out.append(
                    f"{self.route_id}: end_predicate rejected: {end_check.findings}"
                )
        for index, milestone in enumerate(self.goal.milestones):
            milestone_check = validate_predicate(milestone.predicate)
            if not milestone_check.ok:
                out.append(
                    f"{self.route_id}: milestone[{index}] rejected: "
                    f"{milestone_check.findings}"
                )
            for dev in milestone.required_devices:
                if not DEVICE_ID.match(dev):
                    out.append(
                        f"{self.route_id}: milestone[{index}] required device "
                        f"{dev!r} is not '<subkind>#<index>'"
                    )
        for index, observation in enumerate(self.goal.observations):
            observation_check = validate_predicate(observation.predicate)
            if not observation_check.ok:
                out.append(
                    f"{self.route_id}: observation[{index}] rejected: "
                    f"{observation_check.findings}"
                )
            if observation.trigger:
                trigger_check = validate_predicate(observation.trigger)
                if not trigger_check.ok:
                    out.append(
                        f"{self.route_id}: observation[{index}] trigger rejected: "
                        f"{trigger_check.findings}"
                    )
        for index, invariant in enumerate(self.goal.invariants):
            invariant_check = validate_predicate(invariant.predicate)
            if not invariant_check.ok:
                out.append(
                    f"{self.route_id}: invariant[{index}] rejected: "
                    f"{invariant_check.findings}"
                )
        if self.start.at_device and not DEVICE_ID.match(self.start.at_device):
            out.append(
                f"{self.route_id}: start.at_device {self.start.at_device!r} is not "
                "'<subkind>#<index>'"
            )
        if self.start.anchor and not _NUMERIC_NAME.match(self.start.anchor):
            out.append(f"{self.route_id}: start.anchor must be a lowercase identifier")
        for axis, value in self.start.offset_norm.items():
            if axis not in _AXES or not -1.0 <= float(value) <= 1.0:
                out.append(
                    f"{self.route_id}: start.offset_norm {axis}={value} must use x/y/z "
                    "and a fraction in [-1, 1]"
                )
        if "state" in self.start.inject:
            entries = self.start.inject["state"]
            if not isinstance(entries, list) or not entries:
                out.append(
                    f"{self.route_id}: start.inject.state must be a non-empty list"
                )
            else:
                for index, entry in enumerate(entries):
                    prefix = f"{self.route_id}: start.inject.state[{index}]"
                    if not isinstance(entry, dict):
                        out.append(f"{prefix} must be an object")
                        continue
                    node = entry.get("node")
                    prop = entry.get("property")
                    op = entry.get("op", "set")
                    value = entry.get("value")
                    if (
                        not isinstance(node, str)
                        or not _STATE_NODE_PATH.fullmatch(node)
                        or any(part in (".", "..") for part in node.split("/"))
                    ):
                        out.append(
                            f"{prefix}.node must be a non-empty scene-relative node path"
                        )
                    if not isinstance(prop, str) or not _STATE_PROPERTY.fullmatch(prop):
                        out.append(f"{prefix}.property must be a property identifier")
                    if op not in ("set", "add"):
                        out.append(f"{prefix}.op must be 'set' or 'add'")
                    if not isinstance(value, (bool, int, float, str)):
                        out.append(f"{prefix}.value must be a scalar")
                    if op == "add" and (
                        isinstance(value, bool) or not isinstance(value, (int, float))
                    ):
                        out.append(f"{prefix}.value must be numeric for op 'add'")
        if "autoload_state" in self.start.inject:
            entries = self.start.inject["autoload_state"]
            if not isinstance(entries, list) or not entries:
                out.append(
                    f"{self.route_id}: start.inject.autoload_state must be a non-empty list"
                )
            else:
                for index, entry in enumerate(entries):
                    prefix = f"{self.route_id}: start.inject.autoload_state[{index}]"
                    if not isinstance(entry, dict):
                        out.append(f"{prefix} must be an object")
                        continue
                    node = entry.get("node")
                    prop = entry.get("property")
                    op = entry.get("op", "set")
                    value = entry.get("value")
                    if not isinstance(node, str) or not _STATE_PROPERTY.fullmatch(node):
                        out.append(f"{prefix}.node must be an autoload identifier")
                    if not isinstance(prop, str) or not _STATE_PROPERTY.fullmatch(prop):
                        out.append(f"{prefix}.property must be a property identifier")
                    if op not in ("set", "add"):
                        out.append(f"{prefix}.op must be 'set' or 'add'")
                    if not isinstance(value, (bool, int, float, str)):
                        out.append(f"{prefix}.value must be a scalar")
                    if op == "add" and (
                        isinstance(value, bool) or not isinstance(value, (int, float))
                    ):
                        out.append(f"{prefix}.value must be numeric for op 'add'")
        if not 0 <= self.start.settle_frames <= 300:
            out.append(
                f"{self.route_id}: start.settle_frames must be in [0, 300]"
            )
        if self.budget.frames <= 0:
            out.append(f"{self.route_id}: budget.frames must be positive")
        if self.budget.steps < 0:
            out.append(f"{self.route_id}: budget.steps must not be negative")
        for dev in self.required_devices:
            if not DEVICE_ID.match(dev):
                out.append(
                    f"{self.route_id}: required device {dev!r} is not "
                    "'<subkind>#<index>'"
                )
        if self.tier == 5 and self.start.has_inject:
            out.append(
                f"{self.route_id}: L5 is the whole game with zero inject, and "
                "this route injects state"
            )


        ordered_effect = bool(
            self.goal.milestones and check.predicate
            and _requires_nonzero_state_change(check.predicate.ast)
        )
        if self.tier >= 1 and not self.required_devices and not ordered_effect:
            out.append(
                f"{self.route_id}: no required_devices or ordered state-effect evidence; "
                "used_required needs real contacts, or declare milestones with a nonzero counter-delta goal"
            )
        out.extend(self.provenance.findings(self.route_id))
        return out

    def to_dict(self) -> dict[str, Any]:


        return {
            "route_id": self.route_id,
            "tier": self.tier,
            "start": self.start.to_dict(),
            "goal": self.goal.to_dict(),
            "budget": self.budget.to_dict(),
            "required_devices": list(self.required_devices),
            "authored_solution": self.authored_solution,
            "provenance": self.provenance.to_dict(),
            "notes": self.notes,
            "scored": self.scored,
            **({"continue_after_failure": True} if self.continue_after_failure else {}),
            **({"continue_after_success": True} if self.continue_after_success else {}),
            **({"success_scope": self.success_scope} if self.success_scope != "declared_ending" else {}),
        }

    @classmethod
    def from_dict(cls, raw: dict[str, Any]) -> "Route":
        start = raw.get("start") or {}
        goal = raw.get("goal") or {}
        budget = raw.get("budget") or {}
        prov = raw.get("provenance") or {}
        return cls(
            route_id=str(raw.get("route_id", "")),
            tier=int(raw.get("tier", 0)),
            start=RouteStart(
                level=start.get("level", 0),
                inject=dict(start.get("inject") or {}),
                at_device=str(start.get("at_device", "")),
                anchor=str(start.get("anchor", "")),
                offset_norm={str(k): float(v) for k, v in (start.get("offset_norm") or {}).items()},
                settle_frames=int(start.get("settle_frames", 30)),
            ),
            goal=Goal.from_dict(goal),
            budget=Budget(
                steps=int(budget.get("steps", 0)), frames=int(budget.get("frames", 0))
            ),
            required_devices=[str(d) for d in raw.get("required_devices") or []],
            authored_solution=dict(raw.get("authored_solution") or {}),
            provenance=Provenance(**prov) if prov else Provenance(),
            notes=str(raw.get("notes", "")),
            continue_after_failure=bool(raw.get("continue_after_failure", False)),
            continue_after_success=bool(raw.get("continue_after_success", False)),
            success_scope=str(raw.get("success_scope", "declared_ending")),
            scored=bool(raw.get("scored", True)),
        )


def player_view(route: Route) -> dict[str, Any]:


    return {
        "route_id": route.route_id,
        "tier": route.tier,
        "level": route.start.level,
        "inject_applied": route.start.has_inject,
        "goal": {
            "predicate": route.goal.predicate,
            **({"end_predicate": route.goal.end_predicate} if route.goal.end_predicate else {}),
            **({"milestones": [m.player_dict() for m in route.goal.milestones]} if route.goal.milestones else {}),
            **({"invariants": [m.player_dict() for m in route.goal.invariants]} if route.goal.invariants else {}),
        },
        "budget": {"steps": route.budget.steps, "frames": route.budget.frames},
    }


def walk_keys(obj: Any):

    if isinstance(obj, dict):
        for k, v in obj.items():
            yield k
            yield from walk_keys(v)
    elif isinstance(obj, (list, tuple, set)):
        for v in obj:
            yield from walk_keys(v)


def walk_values(obj: Any):

    if isinstance(obj, dict):
        for v in obj.values():
            yield from walk_values(v)
    elif isinstance(obj, (list, tuple, set)):
        for v in obj:
            yield from walk_values(v)
    else:
        yield obj


_LEAK_MIN_LEN = 4


def assert_no_leak(view: dict[str, Any], route: Route) -> list[str]:


    findings: list[str] = []

    for name in EVALUATOR_ONLY_FIELDS:
        if name in set(walk_keys(view)):
            findings.append(f"redacted field {name!r} appears as a key in the view")

    secrets = {
        s
        for s in walk_values([getattr(route, n) for n in EVALUATOR_ONLY_FIELDS])
        if isinstance(s, str) and len(s.strip()) >= _LEAK_MIN_LEN
    }
    secrets |= {
        k
        for k in walk_keys([getattr(route, n) for n in EVALUATOR_ONLY_FIELDS])
        if isinstance(k, str) and len(k) >= _LEAK_MIN_LEN
    }

    for value in walk_values(view):
        if not isinstance(value, str):
            continue
        for secret in secrets:
            if value == secret or (DEVICE_ID.match(secret) and secret in value):
                findings.append(
                    f"{value!r} is reachable from the player view and comes from "
                    "an evaluator-only field"
                )
                break
    return findings


def validate_route_set(routes: Sequence[Route]) -> list[str]:

    out: list[str] = []
    seen: dict[str, int] = {}
    for route in routes:
        out.extend(route.validate())
        if re.search(r"(?:^|/)complete_level_?\d+(?:/|$)", route.route_id):
            predicate = route.goal.predicate
            completion_evidence = (
                "whole_game_clear()" in predicate
                or "levels_visited()" in predicate
                or "numeric(level_complete)" in predicate
            )
            if not completion_evidence:
                out.append(
                    f"route_id {route.route_id!r} claims complete_level_N but its goal "
                    "contains no level-completion condition"
                )
        seen[route.route_id] = seen.get(route.route_id, 0) + 1
    out.extend(f"duplicate route_id {rid!r} ({n} times)" for rid, n in seen.items() if n > 1)
    return out


def load_route_file(path: str | Path) -> list[Route]:

    raw = json.loads(Path(path).read_text(encoding="utf-8"))
    return [Route.from_dict(r) for r in raw.get("routes") or []]
