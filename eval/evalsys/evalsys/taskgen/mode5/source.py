"""Conservative, bounded C# support inspection; never an execution verifier.

Only methods reachable from attached Unity callbacks, serialized events or
explicit AddComponent construction contribute. Comments, string contents,
known disabled branches and uncalled helpers cannot supply executable facts.
This intentionally is not a complete C# compiler: indirect calls/reflection,
complex dataflow and conditional compilation need Editor/runtime evidence.
All conclusions are discounted static support, never verified behavior.
"""
from __future__ import annotations

from dataclasses import dataclass, field
import os
from pathlib import Path
import re
from typing import Any, Mapping

from .evidence import Graph, Limits, SDK_PREFIXES, _link


CALLBACKS = frozenset({"Awake", "Start", "Update", "FixedUpdate", "LateUpdate", "OnEnable",
                      "OnDisable", "OnDestroy", "OnTriggerEnter", "OnTriggerEnter2D",
                      "OnTriggerStay", "OnTriggerStay2D", "OnCollisionEnter", "OnCollisionEnter2D"})
LEX = re.compile(r'//[^\n]*|/\*[\s\S]*?\*/|@"(?:""|[^"])*"|"(?:\\.|[^"\\])*"|\'(?:\\.|[^\'\\])*\'')
METHOD = re.compile(r"\b(?:[\w.<>\[\],?]+\s+)+(?P<name>[A-Za-z_]\w*)\s*\([^{};]*\)\s*(?:where\s+[^{}]+)?\{")
CONTROL = {"if", "while", "for", "foreach", "switch", "catch", "using", "lock"}


def strip_comments(text: str) -> str:
    def replace(match: re.Match[str]) -> str:
        value = match.group()
        return re.sub(r"[^\n]", " ", value) if value.startswith(("//", "/*")) else value
    return LEX.sub(replace, text)


def mask_literals(text: str) -> str:
    return LEX.sub(lambda match: re.sub(r"[^\n]", " ", match.group()), text)


def _closing(text: str, start: int) -> int:
    depth = 0
    for index in range(start, len(text)):
        if text[index] == "{":
            depth += 1
        elif text[index] == "}":
            depth -= 1
            if depth == 0:
                return index
    return -1


def executable_body(body: str) -> str:
    masked = mask_literals(body)
    # These branches are syntactically dead regardless of the game state.
    for match in reversed(list(re.finditer(r"\b(?:if|while)\s*\(\s*(?:false|0\s*==\s*1)\s*\)\s*", masked))):
        end = _closing(masked, match.end()) if masked[match.end():].startswith("{") else masked.find(";", match.end())
        if end >= 0:
            body = body[:match.start()] + " " * (end + 1 - match.start()) + body[end + 1:]
    masked = mask_literals(body)
    depth = 0
    for match in re.finditer(r"[{}]|\breturn\b", masked):
        if match.group() == "{":
            depth += 1
        elif match.group() == "}":
            depth -= 1
        elif depth == 0:
            # The expression before the first top-level return still executes.
            end = masked.find(";", match.end())
            if end >= 0:
                body = body[:end + 1]
            break
    return body


@dataclass
class Method:
    name: str
    owner: str
    path: str
    line: int
    body: str
    runtime_initializer: bool = False

    @property
    def reference(self) -> str:
        return f"{self.path}:{self.line}#{self.owner}.{self.name}"


@dataclass
class SourceFacts:
    methods: list[Method] = field(default_factory=list)
    numeric_changes: dict[str, list[str]] = field(default_factory=dict)
    count_changes: dict[str, list[str]] = field(default_factory=dict)
    consumed_actions: dict[str, list[str]] = field(default_factory=dict)
    outcomes: dict[str, list[str]] = field(default_factory=dict)
    flow: dict[str, list[str]] = field(default_factory=dict)
    ui: dict[str, list[str]] = field(default_factory=dict)
    dynamic_roles: dict[str, list[str]] = field(default_factory=dict)
    construction: dict[str, list[str]] = field(default_factory=dict)
    safety: dict[str, list[str]] = field(default_factory=dict)
    issues: list[str] = field(default_factory=list)
    complete: bool = True

    def to_dict(self) -> dict[str, Any]:
        return {"reachable_methods": sorted(method.reference for method in self.methods),
                **{name: {key: sorted(set(refs)) for key, refs in sorted(getattr(self, name).items())}
                   for name in ("numeric_changes", "count_changes", "consumed_actions", "outcomes",
                                "flow", "ui", "dynamic_roles", "construction", "safety")},
                "issues": self.issues, "complete": self.complete,
                "scope": "indirect static support; not control-flow or gameplay verification"}


def inspect_sources(project: Path, graph: Graph, *, limits: Limits = Limits()) -> SourceFacts:
    facts = SourceFacts()
    methods: list[Method] = []
    files: dict[str, str] = {}
    total = 0
    assets = project / "Assets"
    if not assets.is_dir():
        return facts
    if any(_link(parent) for parent in (assets, *assets.parents)):
        facts.complete = False
        facts.issues.append("linked source root refused")
        return facts
    for directory, dirs, names in os.walk(project / "Assets", followlinks=False):
        dirs[:] = sorted(name for name in dirs if name != "Editor" and not _link(Path(directory) / name))
        for name in sorted(names):
            path = Path(directory) / name
            rel = path.relative_to(project).as_posix()
            if path.suffix != ".cs" or rel.startswith(SDK_PREFIXES) or _link(path):
                continue
            size = path.stat().st_size
            total += size
            if size > limits.file_bytes or total > limits.total_bytes or len(files) >= limits.files:
                facts.complete = False
                facts.issues.append("source inspection budget exceeded")
                return facts
            try:
                text = strip_comments(path.read_text(encoding="utf-8-sig"))
            except (OSError, UnicodeError):
                facts.complete = False
                facts.issues.append(f"unreadable C# source: {rel}")
                continue
            # Do not interpret compile-disabled source as executable support.
            # Conservatively exclude all conditional regions: this inspector
            # does not know the compiler's active symbols. Preserve line refs.
            text = re.sub(r"^[ \t]*#if\b[^\n]*[\s\S]*?^[ \t]*#endif[^\n]*", lambda m: re.sub(r"[^\n]", " ", m.group()), text, flags=re.M)
            files[rel] = text
            masked = mask_literals(text)
            classes = []
            for value in re.finditer(r"\bclass\s+(\w+)[^{;]*\{", masked):
                close = _closing(masked, value.end() - 1)
                if close >= 0:
                    classes.append((value.start(), close, value[1]))
            declaration_masked = re.sub(
                r"\[\s*(?:UnityEngine\.)?RuntimeInitializeOnLoadMethod(?:Attribute)?(?:\([^\]]*\))?\s*\]",
                lambda value: re.sub(r"[^\n]", " ", value.group()), masked,
            )
            for match in METHOD.finditer(declaration_masked):
                if match["name"] in CONTROL:
                    continue
                owners = [value for value in classes if value[0] < match.start() < value[1]]
                if not owners:
                    continue
                end = _closing(masked, match.end() - 1)
                if end < 0:
                    facts.complete = False
                    facts.issues.append(f"unbalanced method body: {rel}")
                    continue
                methods.append(Method(match["name"], owners[-1][2], rel,
                                      text.count("\n", 0, match.start()) + 1,
                                      executable_body(text[match.end():end]),
                                      bool(re.search(r"\bstatic\b", masked[match.start():match.end()])
                                           and re.search(r"\[\s*(?:UnityEngine\.)?RuntimeInitializeOnLoadMethod(?:Attribute)?(?:\([^\]]*\))?\s*\]\s*$",
                                                         masked[max(owners[-1][0], match.start() - 512):match.start()]))))
    active_classes = {(method.path, method.owner) for method in methods if method.path in graph.attached_scripts
                      and method.owner == Path(method.path).stem}
    pending = [method for method in methods if ((method.path, method.owner) in active_classes
                                               and method.name in CALLBACKS) or method.runtime_initializer]
    selected: set[tuple[str, str, int]] = set()
    # A method bound by a serialized UnityEvent is an explicit execution root.
    event_methods = {name for rows in graph.documents.values() for row in rows
                     for name in row.get("event_methods", [])}
    pending.extend(method for method in methods if (method.path, method.owner) in active_classes and method.name in event_methods)
    while pending:
        method = pending.pop()
        ident = (method.path, method.name, method.line)
        if ident in selected:
            continue
        selected.add(ident)
        facts.methods.append(method)
        masked = mask_literals(method.body)
        called = set(re.findall(r"(?<![\w.])(?:this\s*\.\s*)?([A-Za-z_]\w*)\s*\(", masked)) - CONTROL
        called.update(re.findall(r"\.\s*onClick\s*\.\s*AddListener\s*\(\s*(\w+)\s*\)", masked))
        for name in called:
            same_owner = [value for value in methods if value.name == name and value.owner == method.owner
                          and value.path == method.path]
            if len(same_owner) == 1:
                pending.extend(same_owner)
        # Resolve explicitly constructed component callbacks, including code-
        # built projects whose initial Scene is only a bootstrap component.
        created = set(re.findall(r"\bAddComponent\s*<\s*(\w+)\s*>\s*\(", masked))
        for owner in created:
            definitions = {(value.path, value.owner) for value in methods if value.owner == owner}
            if len(definitions) == 1 and not definitions <= active_classes:
                active_classes.update(definitions)
                pending.extend(value for value in methods if (value.path, value.owner) in definitions and value.name in CALLBACKS)
    combined = "\n".join(method.body for method in facts.methods)
    aliases: dict[tuple[str, str, str], str] = {}
    for method in facts.methods:
        masked = mask_literals(method.body)
        for match in re.finditer(r'\b(\w+)\s*=\s*[^;\n]*?FindAction\s*\(\s*"([^"\n]+)"', method.body):
            if re.search(r"\bFindAction\b", masked[match.start():match.end()]):
                aliases[(method.path, method.owner, match[1])] = match[2].split("/")[-1]
    static_roles = _script_roles(graph)
    for method in facts.methods:
        body, code, ref = method.body, mask_literals(method.body), method.reference
        def add(group: str, name: str) -> None:
            getattr(facts, group).setdefault(name, []).append(ref)
        for (path, owner, alias), action in aliases.items():
            if (path, owner) != (method.path, method.owner):
                continue
            if re.search(r"\b" + re.escape(alias) + r"\s*\.\s*(?:ReadValue|IsPressed|WasPressedThisFrame|WasReleasedThisFrame)\b", code):
                add("consumed_actions", action)
        for match in re.finditer(r'(?:ReportNumeric|SetNumeric)\s*\(\s*"(\w+)"\s*,\s*(\w+)\s*\)', body):
            if not code[match.start():match.end()].lstrip().startswith(("ReportNumeric", "SetNumeric")):
                continue
            slot, variable = match[1], match[2]
            if code[match.start():].startswith("SetNumeric") and slot in {"health", "score", "progress", "timer"}:
                # Per-entity telemetry is not the task-wide/player reading.
                if "gb_player" not in static_roles.get(method.path, set()):
                    continue
            owners = "\n".join(value.body for value in facts.methods
                               if value.owner == method.owner and value.path == method.path)
            if re.search(r"\b" + re.escape(variable) + r"\s*(?:\+\+|--|\+=|-=|=\s*" + re.escape(variable) + r"\s*[+-])", mask_literals(owners)):
                add("numeric_changes", slot)
                positive = r"\s*(?=\d*\.?\d*[1-9])(?:\d+(?:\.\d+)?[fFdD]?)(?![\w.])(?=\s*;)"
                for sign, pattern in (("increase", r"\+\+|\+=" + positive + r"|=\s*" + re.escape(variable) + r"\s*\+" + positive),
                                      ("decrease", r"--|-=" + positive + r"|=\s*" + re.escape(variable) + r"\s*-" + positive)):
                    if re.search(r"\b" + re.escape(variable) + r"\s*(?:" + pattern + r")", mask_literals(owners)):
                        add("numeric_changes", slot + ":" + sign)
        for role in static_roles.get(method.path, []):
            if re.search(r"\bDestroy\s*\(\s*(?:gameObject|this\.gameObject)\s*\)", code):
                add("count_changes", role)
        if re.search(r"\bDestroy\s*\(", code):
            for match in re.finditer(r'(?:\.Role\s*==\s*|CompareTag\s*\(\s*)"(gb_\w+)"', body):
                if code[match.start():match.end()].strip():
                    add("count_changes", match[1])
        for kind, name in (("ReportSuccess", "success"), ("ReportFailure", "failure"), ("ReportCheckpoint", "checkpoint")):
            if re.search(r"\bGBOutcome\s*\.\s*" + kind + r"\s*\(", code):
                add("outcomes", name)
        if re.search(r"\bSceneManager\s*\.\s*LoadScene\s*\(", code):
            add("flow", "scene_transition")
            if re.search(r"\bGetActiveScene\s*\(\s*\)", code):
                add("flow", "reset")
                add("outcomes", "reset")
        if re.search(r"\bTime\s*\.\s*timeScale\s*=\s*0(?:f)?\s*;", code):
            add("flow", "pause")
        if (re.search(r"(?:restart|reset)", method.name, re.I)
                and re.search(r"\b(?:progress|score)\s*=\s*0\s*;", code)
                and re.search(r"\b(?:health)\s*=\s*[1-9]\d*(?:\.\d+)?[fF]?\s*;", code)
                and re.search(r"\.\s*(?:position|localPosition)\s*=", code)):
            add("flow", "reset")
            add("outcomes", "reset")
        for match in re.finditer(r'\b(\w+)\.Configure\s*\(\s*"(gb_\w+)"', body):
            if not code[match.start():match.end()].lstrip().startswith(match[1] + ".Configure"):
                continue
            # Only configure an actual GBEntity variable, not an arbitrary
            # method named Configure or a string that mentions a role.
            owner_code = mask_literals("\n".join(value.body for value in facts.methods
                                                if value.owner == method.owner and value.path == method.path))
            if re.search(r"\b" + re.escape(match[1]) + r"\s*=\s*[^;\n]*AddComponent\s*<\s*GBEntity\s*>", owner_code):
                add("dynamic_roles", match[2])
        for match in re.finditer(r"\bAddComponent\s*<\s*(\w+)", code):
            add("construction", match[1])
        # UI assignments must be executable, and UI types must be constructed
        # or serialized independently before these facts are admitted.
        for match in re.finditer(r"\b\w+\s*\.\s*(?:text|value|fillAmount)\s*=\s*([^;]+);", body):
            if not re.match(r"\w+\s*\.", code[match.start():match.end()].lstrip()):
                continue
            expression = match[1]
            for slot in ("health", "score", "progress", "timer"):
                if re.search(r"\b" + slot + r"\b", mask_literals(expression)):
                    add("ui", slot)
            if re.search(r'"[^"\n]*(?:victory|complete|game over|defeat|success|failure|胜利|失败)[^"\n]*"', expression, re.I):
                add("ui", "outcome_ui")
        if re.search(r"\.\s*onClick\s*\.\s*AddListener\s*\(", code):
            add("ui", "interactive_ui")
        # Risk support requires a real guard + protected dereference/lookup,
        # not the appearance of 'null' or an empty catch block.
        if re.search(r"\bif\s*\(\s*(\w+)\s*(?:==\s*null|is\s+null)\s*\)\s*(?:\{\s*)?return\b", code):
            add("safety", "null_guard")
    return facts


def _script_roles(graph: Graph) -> dict[str, set[str]]:
    result: dict[str, set[str]] = {}
    for rows in graph.documents.values():
        for marker in rows:
            role = marker.get("scalars", {}).get("role")
            if not role or not str(marker.get("script_path") or "").endswith("/GBEntity.cs"):
                continue
            for row in rows:
                if row.get("owner_file_id") == marker.get("owner_file_id") and row.get("script_path") in graph.attached_scripts:
                    result.setdefault(row["script_path"], set()).add(role)
    return result


def supports_predicate(predicate: str, facts: SourceFacts) -> list[str]:
    if re.search(r"\bAND\b|&&", predicate, re.I):
        return []  # Do not treat one part of a conjunction as the whole claim.
    numeric = re.search(r"numeric(?:_delta)?\((\w+)\)\s*([<>])", predicate)
    count = re.search(r"count_delta\((gb_\w+)\)\s*<", predicate)
    if numeric:
        sign = "increase" if numeric[2] == ">" else "decrease"
        return facts.numeric_changes.get(numeric[1] + ":" + sign, [])
    if count:
        return facts.count_changes.get(count[1], [])
    if "levels_visited()" in predicate:
        return facts.flow.get("scene_transition", [])
    if "whole_game_clear()" in predicate:
        return facts.outcomes.get("success", [])
    return []
