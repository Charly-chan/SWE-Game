


from __future__ import annotations

import os
import re
import shutil
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Iterable

from evalsys.engine.import_retry import ImportPass, configure_scratch_import, settle_import
from evalsys.harness import face_path
from evalsys.render.modes import RenderMode, RunResult, build_command, build_env, run
from evalsys.verdict import Attribution, Item, Verdict, failed, inconclusive, passed


CAPTURE_PROBE = face_path("gb_capture_probe.gd")

def _default_scratch_root() -> Path:


    if os.name == "nt":
        drive = os.environ.get("SystemDrive", "C:").rstrip("\\/") or "C:"
        return Path(drive + "\\") / "gb_scratch" / "gb_render_scratch"
    if Path("/data2").is_dir():
        return Path("/tmp/swe-game/gb_render_scratch")
    return Path("/tmp/gb_render_scratch")


SCRATCH_ROOT = Path(os.environ.get("GB_SCRATCH_ROOT", str(_default_scratch_root())))


EXCLUDE_DIRS = frozenset(
    {
        ".godot",
        ".git",
        ".import",
        "__pycache__",
        "inputs",
        "reference",
        "staging",
        "_archives",
        "web",
        "builds",
        "export",
    }
)


EVIDENCE_DIRS = frozenset({"compare", "verify", "recording", "shots", "screenshots"})
HEAVY_EXT = frozenset(
    {".png", ".jpg", ".jpeg", ".webp", ".bmp", ".tga", ".avi", ".mp4", ".mov", ".gif"}
)


PROTECTED_PREFIXES = ("/opt/swe-game/asset-library",)

_AUTOLOAD_LINE = re.compile(r'^\s*(\w+)\s*=\s*"\*?res://', re.M)


INJECTED_AUTOLOADS: frozenset[str] = frozenset(
    {
        "GBAssetProbe",
        "GBCaptureProbe",
        "GBHarnessProbe",
        "GBInputCapture",
        "GBPlaytestPlan",
        "GBRecordDriver",
        "GBRouteDriver",
        "GBRuntimeProbe",
        "GBTruthDriver",
        "GBTruthProbe",
    }
)


class ScratchRefused(Exception):
    pass


def find_project_root(path: str | os.PathLike[str]) -> Path:


    p = Path(path)
    if (p / "project.godot").is_file():
        return p
    nested = sorted(d for d in p.iterdir() if (d / "project.godot").is_file())
    if len(nested) == 1:
        return nested[0]
    return p


@dataclass
class ScratchResult:
    source: str
    scratch: str
    ok: bool
    detail: str
    files: int = 0
    bytes: int = 0
    seconds: float = 0.0
    excluded_dirs: list[str] = field(default_factory=list)


    cold_start: bool = True

    def to_dict(self) -> dict[str, Any]:
        return {
            "source": self.source,
            "scratch": self.scratch,
            "ok": self.ok,
            "detail": self.detail,
            "files": self.files,
            "bytes": self.bytes,
            "seconds": round(self.seconds, 3),
            "cold_start": self.cold_start,
            "excluded_dirs": self.excluded_dirs,
        }


def _assert_writable(dest: Path) -> None:
    real = str(dest.resolve() if dest.exists() else dest.absolute())
    for prefix in PROTECTED_PREFIXES:
        if real == prefix or real.startswith(prefix.rstrip("/") + "/"):
            raise ScratchRefused(
                f"refusing to write a scratch copy into {real}: that is a live "
                "game project tree (§8)"
            )


def drop_scratch(path: str | os.PathLike[str], root: Path = SCRATCH_ROOT) -> bool:


    real = Path(path).absolute()
    root_real = root.absolute()
    try:
        real.relative_to(root_real)
    except ValueError:
        return False
    if real == root_real or not real.is_dir():
        return False
    shutil.rmtree(real, ignore_errors=True)
    return True


def _godot_cache_is_reusable(godot_dir: Path) -> bool:


    imported = godot_dir / "imported"
    if not imported.is_dir():
        return False
    try:
        next(imported.iterdir())
    except StopIteration:
        return False
    return True


def make_scratch(
    project_dir: str | os.PathLike[str],
    dest: str | os.PathLike[str],
    *,
    overwrite: bool = True,
) -> Path:


    src = find_project_root(project_dir)
    out = Path(dest)
    _assert_writable(out)


    if (
        os.environ.get("GB_ROUTES_KEEP_SCRATCH") == "1"
        and out.exists()
        and (out / "project.godot").is_file()
        and _godot_cache_is_reusable(out / ".godot")
    ):
        configure_scratch_import(out)
        return out
    godot_keep: Path | None = None
    if out.exists():
        if not overwrite:
            raise ScratchRefused(f"{out} already exists and overwrite=False")
        cached = out / ".godot"
        if cached.is_dir() and _godot_cache_is_reusable(cached):


            godot_keep = out.parent / f".{out.name}.godot_keep"
            if godot_keep.exists():
                shutil.rmtree(godot_keep, ignore_errors=True)
            shutil.move(str(cached), str(godot_keep))
        if not drop_scratch(out, root=out.parent):
            raise ScratchRefused(f"refusing to overwrite {out}: not removable as scratch")
    out.parent.mkdir(parents=True, exist_ok=True)

    src_real = src.resolve()

    def ignore(dirpath: str, names: list[str]) -> set[str]:
        drop = {n for n in names if n in EXCLUDE_DIRS}
        rel = os.path.relpath(os.path.realpath(dirpath), src_real)
        if set(rel.split(os.sep)) & EVIDENCE_DIRS:
            drop |= {
                n
                for n in names
                if os.path.splitext(n)[1].lower() in HEAVY_EXT or n.endswith(".png.import")
            }
        return drop

    shutil.copytree(src, out, ignore=ignore, symlinks=True)
    configure_scratch_import(out)
    if godot_keep is not None and godot_keep.exists():
        dest_godot = out / ".godot"
        if dest_godot.exists():
            shutil.rmtree(dest_godot, ignore_errors=True)
        shutil.move(str(godot_keep), str(dest_godot))
    return out


def make_scratch_result(
    project_dir: str | os.PathLike[str],
    dest: str | os.PathLike[str],
    *,
    overwrite: bool = True,
) -> ScratchResult:

    started = time.time()
    src = find_project_root(project_dir)
    try:
        out = make_scratch(project_dir, dest, overwrite=overwrite)
    except (ScratchRefused, OSError) as exc:
        return ScratchResult(
            source=str(src),
            scratch=str(dest),
            ok=False,
            detail=f"{type(exc).__name__}: {exc}",
            seconds=time.time() - started,
        )
    files = 0
    total = 0
    for base, _dirs, names in os.walk(out):
        for n in names:
            files += 1
            try:
                total += os.path.getsize(os.path.join(base, n))
            except OSError:
                pass


    cold = not _godot_cache_is_reusable(out / ".godot")
    return ScratchResult(
        source=str(src),
        scratch=str(out),
        ok=(out / "project.godot").is_file(),
        detail=f"{'cold' if cold else 'warm'} copy of {src.name}: {files} files, "
        f"{total / 1e6:.1f} MB, "
        + (".godot excluded, this import is cold" if cold else
           ".godot inherited from the previous copy, this import is NOT cold"),
        files=files,
        bytes=total,
        seconds=time.time() - started,
        excluded_dirs=sorted(EXCLUDE_DIRS),
        cold_start=cold,
    )


@dataclass
class InjectResult:
    scratch: str
    autoload_name: str
    script_rel: str
    ok: bool
    detail: str
    created_section: bool = False
    existing_autoloads: list[str] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return {
            "scratch": self.scratch,
            "autoload_name": self.autoload_name,
            "script_rel": self.script_rel,
            "ok": self.ok,
            "detail": self.detail,
            "created_section": self.created_section,
            "existing_autoloads": self.existing_autoloads,
        }

    def as_item(self, id: str = "render/probe_injected", weight: float = 1.0) -> Item:
        if self.ok:
            return passed(id, weight=weight, detail=self.detail, evidence=self.to_dict())
        return inconclusive(
            id,
            weight=weight,
            detail=self.detail,
            attribution=Attribution.HARNESS,
            evidence=self.to_dict(),
        )


def _splice_autoload_line(text: str, line: str, where: str) -> tuple[str, bool]:


    if where not in ("front", "back"):
        raise ValueError(
            f"inject_autoload where must be 'front' or 'back', got {where!r}"
        )
    created = "[autoload]" not in text
    if created:
        return text.rstrip("\n") + "\n\n[autoload]\n\n" + line, True
    if where == "front":
        return text.replace("[autoload]\n", "[autoload]\n\n" + line, 1), False
    start = text.find("[autoload]")
    rest_from = start + len("[autoload]")
    nxt = re.search(r"\n\[", text[rest_from:])
    if nxt is None:
        body = text if text.endswith("\n") else text + "\n"
        return body + line, False
    insert_at = rest_from + nxt.start()
    prefix = text[:insert_at]
    if not prefix.endswith("\n"):
        prefix += "\n"
    return prefix + line + text[insert_at:], False


def inject_autoload(
    scratch: str | os.PathLike[str],
    script_src: str | os.PathLike[str] = CAPTURE_PROBE,
    autoload_name: str = "GBCaptureProbe",
    *,
    where: str = "front",
) -> InjectResult:


    root = Path(scratch)
    _assert_writable(root)
    pg = root / "project.godot"
    if not pg.is_file():
        return InjectResult(
            str(root), autoload_name, "", False, f"no project.godot under {root}"
        )
    src = Path(script_src)
    if not src.is_file():
        return InjectResult(
            str(root), autoload_name, "", False, f"probe source missing: {src}"
        )

    dest_name = src.name
    shutil.copy2(src, root / dest_name)

    text = pg.read_text(encoding="utf-8", errors="replace")
    existing = _AUTOLOAD_LINE.findall(text)
    if f"{autoload_name}=" in text:
        assignment = text.split(f"{autoload_name}=", 1)[1].split("\n", 1)[0]
        if f"res://{dest_name}" in assignment:


            return InjectResult(
                str(root),
                autoload_name,
                dest_name,
                True,
                f"{autoload_name} already registered; script refreshed",
                existing_autoloads=existing,
            )
        return InjectResult(
            str(root),
            autoload_name,
            dest_name,
            False,
            f"{autoload_name} is already an autoload in this project; refusing to "
            "shadow it",
            existing_autoloads=existing,
        )

    line = f'{autoload_name}="*res://{dest_name}"\n'
    text, created = _splice_autoload_line(text, line, where)
    pg.write_text(text, encoding="utf-8")

    placed = " (created the [autoload] section)" if created else f" ({where} of [autoload])"
    return InjectResult(
        str(root),
        autoload_name,
        dest_name,
        True,
        f"injected {autoload_name} -> res://{dest_name}" + placed,
        created_section=created,
        existing_autoloads=existing,
    )


@dataclass
class ImportResult:
    scratch: str
    ok: bool
    detail: str
    returncode: int = 0
    seconds: float = 0.0
    timed_out: bool = False
    errors: list[str] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return {
            "scratch": self.scratch,
            "ok": self.ok,
            "detail": self.detail,
            "returncode": self.returncode,
            "seconds": round(self.seconds, 3),
            "timed_out": self.timed_out,
            "errors": self.errors[:20],
            "error_count": len(self.errors),
        }

    def as_item(self, id: str = "render/cold_import", weight: float = 1.0) -> Item:
        if self.timed_out:
            return inconclusive(
                id,
                weight=weight,
                detail=self.detail,
                attribution=Attribution.HARNESS,
                evidence=self.to_dict(),
            )
        if self.ok:
            return passed(id, weight=weight, detail=self.detail, evidence=self.to_dict())


        return failed(
            id,
            weight=weight,
            detail=self.detail,
            attribution=Attribution.SUBMISSION,
            evidence=self.to_dict(),
        )


_IMPORT_ERROR = re.compile(r"^\s*(ERROR|SCRIPT ERROR|Failed|Parse Error)", re.M)
_IMPORT_IGNORE = re.compile(
    r"Started the engine as `root`|GODOT_SILENCE_ROOT_WARNING|audio|ALSA|PulseAudio",
    re.I,
)


def _import_once(root: Path, timeout: float) -> tuple[RunResult, list[str]]:
    cmd = build_command(RenderMode.H, root, extra_args=["--import"])
    res = run(cmd, cwd=root, timeout=timeout, env=build_env(RenderMode.H))
    errors = [
        ln.strip()[:200]
        for ln in res.log.splitlines()
        if _IMPORT_ERROR.search(ln) and not _IMPORT_IGNORE.search(ln)
    ]
    return res, errors


def cold_import(
    scratch: str | os.PathLike[str], timeout: float = 600.0
) -> ImportResult:


    root = Path(scratch)
    from ..render.modes import scaled_timeout

    timeout = scaled_timeout(timeout)

    def _once() -> ImportPass:
        res, errors = _import_once(root, timeout)
        return ImportPass(
            returncode=res.returncode,
            timed_out=res.timed_out,
            errors=tuple(errors),
            seconds=res.seconds,
            payload=res,
        )

    settled = settle_import(_once, extra_error_pass=True)
    first = settled.first
    second = settled.scored
    first_errors = list(first.errors)
    errors = list(second.errors)

    crash_notes = [note for note in settled.notes if "engine crash" in note]
    if first.timed_out:
        return ImportResult(
            str(root),
            False,
            f"cold import did not finish within {timeout:.0f}s",
            first.returncode,
            first.seconds,
            True,
            first_errors,
        )
    if second.timed_out:
        return ImportResult(
            str(root),
            False,
            f"the settling import did not finish within {timeout:.0f}s",
            second.returncode,
            settled.seconds,
            True,
            errors,
        )

    seconds = settled.seconds


    cached = (root / ".godot").is_dir()
    ok = second.returncode == 0 and not errors and cached


    settled_lines = [
        e for e in (first_errors + list(settled.errors_before_extra)) if e not in errors
    ]
    if ok:
        detail = (
            f"cold import clean in {seconds:.1f}s over up to three settling passes"
            + (f" ({len(settled_lines)} first-pass lines settled)" if settled_lines else "")
        )
    elif not cached:


        detail = f"cold import wrote no .godot cache into {root}"
    else:
        detail = (
            f"cold import: rc={second.returncode}, {len(errors)} error lines "
            "still present after a settling reimport"
        )
    return ImportResult(
        str(root),
        ok,
        detail,
        second.returncode,
        seconds,
        False,
        errors
        + [f"settled on reimport: {s}" for s in settled_lines]
        + crash_notes,
    )


@dataclass
class ScratchPrep:


    scratch: ScratchResult
    inject: InjectResult | None
    imported: ImportResult | None

    @property
    def ok(self) -> bool:
        return (
            self.scratch.ok
            and self.inject is not None
            and self.inject.ok
            and (self.imported is None or self.imported.ok)
        )

    @property
    def path(self) -> Path:
        return Path(self.scratch.scratch)

    def items(self, prefix: str = "render") -> list[Item]:
        out: list[Item] = []
        if self.scratch.ok:
            out.append(
                passed(
                    f"{prefix}/scratch_copy",
                    detail=self.scratch.detail,
                    evidence=self.scratch.to_dict(),
                )
            )
        else:
            out.append(
                inconclusive(
                    f"{prefix}/scratch_copy",
                    detail=self.scratch.detail,
                    attribution=Attribution.HARNESS,
                    evidence=self.scratch.to_dict(),
                )
            )
        if self.inject is not None:
            out.append(self.inject.as_item(f"{prefix}/probe_injected"))
        if self.imported is not None:
            out.append(self.imported.as_item(f"{prefix}/cold_import"))
        return out

    def to_dict(self) -> dict[str, Any]:
        return {
            "scratch": self.scratch.to_dict(),
            "inject": self.inject.to_dict() if self.inject else None,
            "import": self.imported.to_dict() if self.imported else None,
            "ok": self.ok,
        }

    def failure_summary(self, limit: int = 1500) -> str:


        if self.ok:
            return ""
        if not self.scratch.ok:
            stage, detail, errors = "scratch_copy", self.scratch.detail, []
        elif self.inject is None:
            stage, detail, errors = "probe_inject", "no injection was attempted", []
        elif not self.inject.ok:
            stage, detail, errors = "probe_inject", self.inject.detail, []
        else:
            imported = self.imported
            stage = "cold_import"
            detail = imported.detail if imported is not None else "no import record"
            errors = list(getattr(imported, "errors", []) or [])
            if imported is not None and getattr(imported, "timed_out", False):
                detail = f"timed out after {imported.seconds:.0f}s; {detail}"
            elif imported is not None:
                detail = f"rc={imported.returncode}; {detail}"
        text = f"scratch preparation failed at {stage}: {detail}"
        if errors:
            text += " | errors: " + " || ".join(str(item) for item in errors[:6])
        text += f" | scratch={self.scratch.scratch}"
        return text[:limit]


def prepare_scratch(
    project_dir: str | os.PathLike[str],
    dest: str | os.PathLike[str] | None = None,
    *,
    script_src: str | os.PathLike[str] = CAPTURE_PROBE,
    autoload_name: str = "GBCaptureProbe",
    do_import: bool = True,
    import_timeout: float = 600.0,
) -> ScratchPrep:

    src = find_project_root(project_dir)
    out = Path(dest) if dest is not None else SCRATCH_ROOT / src.name
    scratch = make_scratch_result(src, out)
    if not scratch.ok:
        return ScratchPrep(scratch, None, None)
    inject = inject_autoload(scratch.scratch, script_src, autoload_name)
    if inject.ok and Path(script_src).resolve() == CAPTURE_PROBE.resolve():
        shared = inject_autoload(scratch.scratch, face_path("gb_probe.gd"), "GBHarnessProbe")
        if not shared.ok:
            return ScratchPrep(scratch, shared, None)
    reused_cache = _godot_cache_is_reusable(Path(scratch.scratch) / ".godot")


    imported = cold_import(scratch.scratch, import_timeout) if do_import else None
    if imported is not None and reused_cache and imported.ok:
        imported.detail = "reused cache verified by settling import"
    return ScratchPrep(scratch, inject, imported)
