

from __future__ import annotations

import re
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any


_LIFECYCLE = re.compile(
    r"\b(?:void|IEnumerator)\s+(Awake|Start|OnEnable)\s*\([^)]*\)\s*\{",
    re.MULTILINE,
)
_SUCCESS = re.compile(r"\b(?:GBOutcome\s*\.\s*)?ReportSuccess\s*\(\s*\)\s*;")
_EVALUATOR_ENV = re.compile(
    r"Environment\s*\.\s*GetEnvironmentVariable\s*\(\s*[\"']GB_EVALUATOR(?:_RUN)?[\"']\s*\)",
    re.IGNORECASE,
)
_EVALUATOR_ARGS = re.compile(
    r"Environment\s*\.\s*GetCommandLineArgs\s*\(\s*\)", re.IGNORECASE
)
_EVALUATOR_ARG_LITERAL = re.compile(r"[\"']--gb-eval(?:-[a-z0-9_-]+)?[\"']", re.IGNORECASE)
_CONDITIONAL = re.compile(r"\b(?:if|switch|for|foreach|while|when)\b|[?&|]")
_IGNORED_DIRS = frozenset({"Library", "Temp", "Logs", "obj", "Build", "Builds"})


@dataclass(frozen=True)
class UnityAntiGrantFinding:
    code: str
    path: str
    line: int
    detail: str
    blocking: bool

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass(frozen=True)
class UnityAntiGrantReport:
    root: str
    files_scanned: int
    findings: tuple[UnityAntiGrantFinding, ...]

    @property
    def ok(self) -> bool:
        return not any(item.blocking for item in self.findings)

    def to_dict(self) -> dict[str, Any]:
        return {
            "root": self.root,
            "files_scanned": self.files_scanned,
            "ok": self.ok,
            "findings": [item.to_dict() for item in self.findings],
            "policy": "only high-confidence findings block; dynamic controls remain authoritative",
        }


def _method_body(text: str, open_brace: int) -> str | None:
    depth = 0
    for index in range(open_brace, len(text)):
        if text[index] == "{":
            depth += 1
        elif text[index] == "}":
            depth -= 1
            if depth == 0:
                return text[open_brace + 1 : index]
    return None


def _line(text: str, offset: int) -> int:
    return text.count("\n", 0, offset) + 1


def _without_comments(text: str) -> str:
    def blank(match: re.Match[str]) -> str:
        return "\n" * match.group(0).count("\n")

    return re.sub(r"//[^\n]*|/\*.*?\*/", blank, text, flags=re.S)


def scan_unity_antigrant(project: str | Path) -> UnityAntiGrantReport:


    root = Path(project).resolve()
    findings: list[UnityAntiGrantFinding] = []
    files = 0
    for path in sorted(root.rglob("*.cs")):
        relative_parts = path.relative_to(root).parts
        if any(part in _IGNORED_DIRS for part in relative_parts):
            continue
        files += 1
        rel = path.relative_to(root).as_posix()
        text = path.read_text(encoding="utf-8", errors="replace")
        code = _without_comments(text)
        if rel.startswith("Assets/GameBenchmarkEvaluator/"):
            findings.append(UnityAntiGrantFinding(
                "candidate_evaluator_override", rel, 1,
                "candidate project contains evaluator-owned probe namespace/path", True,
            ))
        for match in _LIFECYCLE.finditer(code):
            body = _method_body(code, match.end() - 1)
            if body is None or not _SUCCESS.search(body):
                continue
            stripped = re.sub(r"Debug\s*\.\s*Log\s*\([^;]*;", "", body, flags=re.S)
            remainder = _SUCCESS.sub("", stripped).strip()
            blocking = not remainder and not _CONDITIONAL.search(stripped)
            findings.append(UnityAntiGrantFinding(
                "unconditional_lifecycle_success" if blocking else "ambiguous_lifecycle_success",
                rel, _line(code, match.start()),
                f"{match.group(1)} contains ReportSuccess; "
                + ("body is visibly unconditional" if blocking else "control flow requires dynamic verification"),
                blocking,
            ))
        for success in _SUCCESS.finditer(code):
            window = code[max(0, success.start() - 500) : success.end() + 100]
            evaluator_env = _EVALUATOR_ENV.search(window)
            evaluator_arg = _EVALUATOR_ARGS.search(window) and _EVALUATOR_ARG_LITERAL.search(window)
            if evaluator_env or evaluator_arg:
                findings.append(UnityAntiGrantFinding(
                    "evaluator_aware_success", rel, _line(code, success.start()),
                    "ReportSuccess is adjacent to an explicit evaluator environment/argument check", True,
                ))
    for path in sorted(root.rglob("*")):
        relative_parts = path.relative_to(root).parts
        if not path.is_file() or any(part in _IGNORED_DIRS for part in relative_parts):
            continue
        rel = path.relative_to(root).as_posix()
        if any(token in rel.lower() for token in (
            "evaluator_certificate", "result_certificate", "unity-probe-result",
        )):
            findings.append(UnityAntiGrantFinding(
                "bundled_evaluator_artifact", rel, 1,
                "candidate bundles a certificate/result artifact reserved for the evaluator", True,
            ))
    unique = {(item.code, item.path, item.line, item.detail): item for item in findings}
    return UnityAntiGrantReport(str(root), files, tuple(unique.values()))


__all__ = ["UnityAntiGrantFinding", "UnityAntiGrantReport", "scan_unity_antigrant"]
