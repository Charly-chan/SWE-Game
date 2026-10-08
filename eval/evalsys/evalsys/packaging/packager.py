


from __future__ import annotations

import hashlib
import json
import shutil
from dataclasses import dataclass, field, asdict
from pathlib import Path
from typing import Any, Iterable, Sequence

from ..assertions import (
    AssertionSet,
    AuditFinding,
    DEFAULT_CHANNEL_OVERRIDES,
    load_assertions,
    write_ceilings,
)
from ..interface.loader import project_root
from ..weights import MODALITIES, TIERS

PACKAGER_VERSION = "0.1.0"


@dataclass
class PackagingIssue:
    level: str
    code: str
    message: str

    def __str__(self) -> str:
        return f"[{self.level}] {self.code}: {self.message}"


@dataclass
class SourceRecording:


    frames_dir: Path | None = None
    sampling: dict[str, Any] = field(default_factory=dict)
    audio_wav: Path | None = None
    video_mp4: Path | None = None
    coverage_json: Path | None = None
    anchor_frame: Path | None = None
    route_version: str = ""
    build_id: str = ""

    def issues(self) -> list[PackagingIssue]:
        out: list[PackagingIssue] = []
        if not self.frames_dir or not self.frames_dir.is_dir():
            out.append(PackagingIssue(
                "block", "IV7",
                "no frame sequence. Frames are the primary artifact, not the mp4: "
                "the model input channels in practice accept images, so the frames "
                "are what a subject actually receives.",
            ))
        if not self.sampling:
            out.append(PackagingIssue(
                "block", "IV7",
                "no sampling declaration. A frame sequence without its sampling rate "
                "and order is not a video.",
            ))
        if not self.coverage_json or not self.coverage_json.is_file():
            out.append(PackagingIssue(
                "block", "IV2",
                "no input_video_coverage.json. Without it, every 'V' label is an "
                "assertion that something is visible rather than a reading that it "
                "was seen.",
            ))
        if not self.route_version or not self.build_id:
            out.append(PackagingIssue(
                "block", "IV8",
                "route_version and build_id are required; they are how the three "
                "tiers are proven to come from one playthrough.",
            ))
        if not self.anchor_frame or not self.anchor_frame.is_file():
            out.append(PackagingIssue(
                "warn", "O9",
                "no designated anchor frame. Without one the composition channel "
                "has no shared reference and cannot be scored.",
            ))
        return out


def audio_issues(wav: Path | None) -> list[PackagingIssue]:


    out: list[PackagingIssue] = []
    if wav is None:
        out.append(PackagingIssue(
            "warn", "IV10",
            "no audio.wav; the pack is Mv-only and every V(audio) item will be "
            "unobservable. That is a legitimate pack, but it must be recorded as "
            "a modality limit rather than as the game having no audio.",
        ))
        return out
    if not wav.is_file():
        out.append(PackagingIssue("block", "IV10", f"declared audio.wav missing: {wav}"))
        return out
    try:
        from ..ocard.adapters import SILENCE_FLOOR_DBFS, read_wav_peak

        reading = read_wav_peak(wav)
        peak = getattr(reading, "peak_dbfs", None)
        if peak is not None and peak < SILENCE_FLOOR_DBFS:
            out.append(PackagingIssue(
                "block", "IV11",
                f"audio.wav peaks at {peak:.1f} dBFS, below the {SILENCE_FLOOR_DBFS} dBFS "
                "floor. This is a silent track wearing a valid container; it must not "
                "enter an input pack.",
            ))
    except Exception as exc:
        out.append(PackagingIssue(
            "warn", "IV11",
            f"could not measure audio energy ({exc}). Unmeasured is not unsilent.",
        ))
    return out


@dataclass
class TierPack:
    tier: str
    root: Path
    contents: dict[str, str]
    asset_mode: str
    ceilings: dict[str, dict[str, Any]]

    def to_dict(self) -> dict[str, Any]:
        return {
            "tier": self.tier,
            "root": str(self.root),
            "asset_mode": self.asset_mode,
            "contents": self.contents,
            "ceilings": self.ceilings,
        }


@dataclass
class PackagedTask:
    task_id: str
    root: Path
    packs: dict[str, TierPack]
    ceilings: dict[str, Any]
    issues: list[PackagingIssue]
    audit: list[AuditFinding]
    blind_readability: float | None

    @property
    def admissible(self) -> bool:


        return not any(i.level == "block" for i in self.issues) and not any(
            f.level == "block" for f in self.audit
        )

    def to_dict(self) -> dict[str, Any]:
        return {
            "task_id": self.task_id,
            "packager_version": PACKAGER_VERSION,
            "root": str(self.root),
            "admissible": self.admissible,
            "blind_readability": self.blind_readability,
            "packs": {k: v.to_dict() for k, v in self.packs.items()},
            "issues": [asdict(i) for i in self.issues],
            "audit": [asdict(f) for f in self.audit],
        }


def sha256_file(p: Path) -> str:
    h = hashlib.sha256()
    with p.open("rb") as fh:
        for chunk in iter(lambda: fh.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def _link_or_copy(src: Path, dst: Path) -> None:


    dst.parent.mkdir(parents=True, exist_ok=True)
    if dst.exists():
        dst.unlink()
    try:
        dst.hardlink_to(src)
    except (OSError, AttributeError):
        shutil.copy2(src, dst)


def _copy_tree(src: Path, dst: Path, *, only: set[str] | None = None) -> int:
    n = 0
    for f in sorted(src.rglob("*")):
        if not f.is_file():
            continue
        if only is not None and f.name not in only:
            continue
        _link_or_copy(f, dst / f.relative_to(src))
        n += 1
    return n


class TaskPackager:


    def __init__(
        self,
        task_id: str,
        reference_project: Path,
        recording: SourceRecording,
        assertions: AssertionSet,
        *,
        brief_md: Path | None = None,
        assets_full: Path | None = None,
        assets_seen: Path | None = None,
        gdd_md: Path | None = None,
    ) -> None:
        self.task_id = task_id
        self.reference_project = Path(reference_project)
        self.recording = recording
        self.assertions = assertions
        self.brief_md = brief_md
        self.assets_full = assets_full
        self.assets_seen = assets_seen
        self.gdd_md = gdd_md


    def preflight(self) -> list[PackagingIssue]:
        issues = list(self.recording.issues())
        issues.extend(audio_issues(self.recording.audio_wav))


        engine_root = project_root(self.reference_project)
        if not (engine_root / "project.godot").is_file():
            issues.append(PackagingIssue(
                "block", "REF",
                f"{self.reference_project} is not a Godot project.",
            ))
        levels = engine_root / "gb_levels.json"
        if not levels.is_file():
            issues.append(PackagingIssue(
                "block", "L2.5",
                "reference has no gb_levels.json. Without it nothing can be driven "
                "to a playable level, so the runnable channel measures the title "
                "menu -- which is how a project with 246 in-game errors in its own "
                "logs was passed.",
            ))
        if self.brief_md is None or not Path(self.brief_md).is_file():
            issues.append(PackagingIssue(
                "block", "S",
                "no brief. Everything labelled 'S' is delivered through it verbatim "
                "at every tier; without it those items are inferable from nowhere.",
            ))

        if self.assets_full is None:
            issues.append(PackagingIssue(
                "warn", "D2", "no full asset pack: D2 and D3 cannot be built."))
        if self.assets_seen is None:
            issues.append(PackagingIssue(
                "warn", "D1.5",
                "no on-screen asset subset: D1.5 cannot be built, so the D1->D2 gap "
                "cannot be split into 'assets supplied art' and 'assets leaked "
                "mechanics'.",
            ))
        if self.gdd_md is None:
            issues.append(PackagingIssue("warn", "D3", "no GDD: D3 cannot be built."))
        elif Path(self.gdd_md).is_file():
            issues.extend(self._gdd_coverage_issues())
        return issues

    def _gdd_coverage_issues(self) -> list[PackagingIssue]:


        out: list[PackagingIssue] = []
        text = Path(self.gdd_md).read_text(encoding="utf-8", errors="replace").lower()
        kinds = self._reference_gb_kinds()
        missing = sorted(k for k in kinds if k.lower() not in text)
        if missing:
            out.append(PackagingIssue(
                "block", "M2",
                "GDD does not mention these classes present in the reference: "
                + ", ".join(missing)
                + ". D3 must be a superset of D2, or the difficulty axis inverts.",
            ))
        return out

    def _reference_gb_kinds(self) -> set[str]:

        import re

        pat = re.compile(r"\bgb_[a-z0-9_]+\b")
        found: set[str] = set()
        for f in self.reference_project.rglob("*"):
            if f.suffix not in {".gd", ".tscn", ".tres", ".json"} or not f.is_file():
                continue
            try:
                found.update(pat.findall(f.read_text(encoding="utf-8", errors="replace")))
            except OSError:
                continue
        found.discard("gb_levels")
        return found


    def build(self, out_root: Path, *, tiers: Sequence[str] | None = None) -> PackagedTask:
        out_root = Path(out_root)
        task_root = out_root / self.task_id
        (task_root / "bench").mkdir(parents=True, exist_ok=True)
        (task_root / "inputs").mkdir(parents=True, exist_ok=True)

        issues = self.preflight()
        audit = self.assertions.audit()

        ceilings = write_ceilings(
            self.assertions,
            task_root / "bench" / "ceiling.json",
            channel_overrides_by_tier=DEFAULT_CHANNEL_OVERRIDES,
        )
        (task_root / "bench" / "assertions.json").write_text(
            json.dumps(
                {
                    "task_id": self.task_id,
                    "assertions": [a.to_dict() for a in self.assertions.assertions],
                },
                indent=2,
                sort_keys=True,
                ensure_ascii=False,
            ),
            encoding="utf-8",
        )
        self._write_ia_audit(task_root / "bench" / "ia_audit.json", audit)

        wanted = list(tiers) if tiers else list(TIERS)
        packs: dict[str, TierPack] = {}
        for tid in wanted:
            pack = self._build_tier(task_root, tid, ceilings)
            if pack is not None:
                packs[tid] = pack

        task = PackagedTask(
            task_id=self.task_id,
            root=task_root,
            packs=packs,
            ceilings=ceilings,
            issues=issues,
            audit=audit,
            blind_readability=self.assertions.blind_readability(),
        )
        (task_root / "TASK.json").write_text(
            json.dumps(task.to_dict(), indent=2, sort_keys=True, ensure_ascii=False),
            encoding="utf-8",
        )
        return task

    def _write_ia_audit(self, path: Path, findings: list[AuditFinding]) -> None:
        by_label: dict[str, int] = {}
        for a in self.assertions.assertions:
            for lab in a.inferable_from:
                by_label[lab] = by_label.get(lab, 0) + 1
        annotated = [a for a in self.assertions.assertions if len(a.annotators) >= 2]
        payload = {
            "task_id": self.task_id,
            "assertions": len(self.assertions.assertions),
            "label_counts": by_label,
            "two_annotator_coverage": (
                len(annotated) / len(self.assertions.assertions)
                if self.assertions.assertions else 0.0
            ),
            "blind_readability": self.assertions.blind_readability(),
            "blind_readability_note": (
                "Hit rate of a blind annotator on V-class items. This is a reading "
                "on the recording, not on the labels: a low value means this game "
                "was recorded badly."
            ),
            "findings": [asdict(f) for f in findings],
        }
        path.write_text(
            json.dumps(payload, indent=2, sort_keys=True, ensure_ascii=False),
            encoding="utf-8",
        )

    def _build_tier(
        self, task_root: Path, tier_id: str, ceilings: dict[str, Any]
    ) -> TierPack | None:
        tier = TIERS[tier_id]
        root = task_root / "inputs" / tier_id.replace(".", "_")
        contents: dict[str, str] = {}

        rec = self.recording
        if rec.frames_dir and rec.frames_dir.is_dir():
            n = _copy_tree(rec.frames_dir, root / "frames")
            contents["frames"] = f"{n} files"
        (root / "sampling.json").parent.mkdir(parents=True, exist_ok=True)
        (root / "sampling.json").write_text(
            json.dumps(
                {
                    **rec.sampling,
                    "route_version": rec.route_version,
                    "build_id": rec.build_id,
                },
                indent=2,
                sort_keys=True,
            ),
            encoding="utf-8",
        )
        contents["sampling.json"] = "declared"

        if self.brief_md and Path(self.brief_md).is_file():
            _link_or_copy(Path(self.brief_md), root / "task.md")
            contents["task.md"] = "brief (S-class, verbatim at every tier)"


        if rec.audio_wav and Path(rec.audio_wav).is_file():
            _link_or_copy(Path(rec.audio_wav), root / "audio.wav")
            contents["audio.wav"] = "Mva only; ignored under Mv"
        if rec.video_mp4 and Path(rec.video_mp4).is_file():
            _link_or_copy(Path(rec.video_mp4), root / "video.mp4")
            contents["video.mp4"] = "derived; frames are authoritative"
        if rec.anchor_frame and Path(rec.anchor_frame).is_file():
            _link_or_copy(Path(rec.anchor_frame), root / "anchor_frame.png")
            contents["anchor_frame.png"] = "designated anchor for the composition channel"

        if tier_id == "D1.5":
            if not self.assets_seen:
                return None
            n = _copy_tree(Path(self.assets_seen), root / "assets_subset")
            contents["assets_subset"] = f"{n} files (only assets seen on screen)"
        elif tier_id in ("D2", "D3"):
            if not self.assets_full:
                return None
            n = _copy_tree(Path(self.assets_full), root / "assets_full")
            contents["assets_full"] = f"{n} files"
        if tier_id == "D3":
            if not self.gdd_md:
                return None
            _link_or_copy(Path(self.gdd_md), root / "gdd.md")
            contents["gdd.md"] = "GDD"

        tier_ceilings = {
            mid: ceilings["ceilings"][f"{tier_id}/{mid}"] for mid in MODALITIES
        }
        (root / "MANIFEST.json").write_text(
            json.dumps(
                {
                    "tier": tier_id,
                    "tier_name": tier.name,
                    "asset_mode": tier.asset_mode,
                    "provides": sorted(tier.provides),
                    "contents": contents,
                    "ceilings": tier_ceilings,
                    "ceiling_note": (
                        "Cross-tier comparison uses score/ceiling and nothing else. "
                        "The raw score at this tier cannot reach 1.0 by construction."
                    ),
                    "file_hashes": {
                        str(p.relative_to(root)): sha256_file(p)
                        for p in sorted(root.rglob("*"))
                        if p.is_file() and p.name != "MANIFEST.json"
                    },
                },
                indent=2,
                sort_keys=True,
                ensure_ascii=False,
            ),
            encoding="utf-8",
        )
        return TierPack(
            tier=tier_id,
            root=root,
            contents=contents,
            asset_mode=tier.asset_mode,
            ceilings=tier_ceilings,
        )


def asset_leak_audit(assets_full: Path, assets_seen: Path) -> dict[str, Any]:


    full = {p.name for p in Path(assets_full).rglob("*") if p.is_file()}
    seen = {p.name for p in Path(assets_seen).rglob("*") if p.is_file()}
    only_in_pack = sorted(full - seen)
    return {
        "assets_full": len(full),
        "assets_on_screen": len(seen),
        "pack_only": len(only_in_pack),
        "pack_only_sample": only_in_pack[:50],
        "leak_ratio": (len(only_in_pack) / len(full)) if full else 0.0,
        "note": (
            "pack_only entities are supplied but never shown. D2 - D1.5 measures "
            "their value; a large pack_only set also means the pack should be "
            "trimmed for this task."
        ),
    }
