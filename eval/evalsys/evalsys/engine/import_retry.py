


from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path
import re
from typing import Any


def configure_scratch_import(project: str | Path) -> None:


    path = Path(project) / "project.godot"
    if not path.is_file():
        return
    text = path.read_text(encoding="utf-8")
    section = re.search(r"(?m)^\[editor\][ \t]*$", text)
    setting = "import/use_multiple_threads=false"
    if section is None:
        updated = text.rstrip("\n") + "\n\n[editor]\n" + setting + "\n"
    else:
        following = re.search(r"(?m)^\[", text[section.end():])
        end = section.end() + following.start() if following else len(text)
        body = text[section.end():end]
        key = r"(?m)^import/use_multiple_threads[ \t]*=.*$"
        if re.search(key, body):
            body = re.sub(key, setting, body)
        else:
            body = "\n" + setting + "\n" + body
        updated = text[:section.end()] + body + text[end:]
    if updated != text:
        path.write_text(updated, encoding="utf-8")


@dataclass(frozen=True)
class ImportPass:


    returncode: int
    timed_out: bool = False
    errors: tuple[str, ...] = ()
    seconds: float = 0.0
    payload: Any = None


@dataclass(frozen=True)
class SettledImport:


    first: ImportPass
    scored: ImportPass
    attempts: int
    notes: tuple[str, ...]
    seconds: float
    errors_before_extra: tuple[str, ...]


def is_retryable_import_crash(returncode: int, *, timed_out: bool = False) -> bool:


    return (not timed_out) and int(returncode) != 0


def run_with_crash_retry(
    run_once: Callable[[], ImportPass],
) -> tuple[ImportPass, bool]:


    first = run_once()
    if is_retryable_import_crash(first.returncode, timed_out=first.timed_out):
        return run_once(), True
    return first, False


def settle_import(
    run_once: Callable[[], ImportPass],
    *,
    extra_error_pass: bool = False,
) -> SettledImport:


    notes: list[str] = []
    attempts = 0
    seconds = 0.0

    def once() -> ImportPass:
        nonlocal attempts, seconds
        attempts += 1
        result = run_once()
        seconds += float(result.seconds)
        return result

    first = once()
    if is_retryable_import_crash(first.returncode, timed_out=first.timed_out):
        notes.append(
            f"engine crash on the first pass, retried once: rc={first.returncode}"
        )
        first = once()
    if first.timed_out:
        return SettledImport(
            first=first,
            scored=first,
            attempts=attempts,
            notes=tuple(notes),
            seconds=seconds,
            errors_before_extra=first.errors,
        )

    scored = once()
    if is_retryable_import_crash(scored.returncode, timed_out=scored.timed_out):
        notes.append(
            f"engine crash on the settling pass, retried once: rc={scored.returncode}"
        )
        scored = once()

    errors_before_extra = scored.errors
    if (
        extra_error_pass
        and scored.returncode == 0
        and not scored.timed_out
        and scored.errors
    ):
        extra = once()
        if not extra.timed_out:
            scored = extra

    return SettledImport(
        first=first,
        scored=scored,
        attempts=attempts,
        notes=tuple(notes),
        seconds=seconds,
        errors_before_extra=errors_before_extra,
    )
