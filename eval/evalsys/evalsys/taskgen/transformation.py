
from __future__ import annotations
import json
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any, Iterable, Literal, Mapping, Sequence
from ..interface import load_submission_interface
from .mutations import declared_groups
from .skeleton import STUB_MARKER, scene_script_attachments
SCHEMA = 'gamebench.package_transformation.v1'
Action = Literal['copied', 'dropped', 'rewritten', 'generated']
Status = Literal['pass', 'fail', 'unverified']

@dataclass(frozen=True)
class TransformationRecord:
    path: str
    action: Action
    reason: str
    files: int = 1
    bytes: int = 0
    examples: tuple[str, ...] = ()

    def to_dict(self) -> dict[str, Any]:
        return asdict(self) | {'examples': list(self.examples)}

@dataclass(frozen=True)
class RestorationObligation:
    id: str
    kind: str
    value: str
    reason: str
    source: str

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)

@dataclass(frozen=True)
class TransformationManifest:
    mode: str
    records: tuple[TransformationRecord, ...]
    obligations: tuple[RestorationObligation, ...]
    source_label: str = 'reference_game'
    output_label: str = 'task_package'
    notes: tuple[str, ...] = ()
    schema: str = SCHEMA

    @property
    def counts(self) -> dict[str, int]:
        out = {name: 0 for name in ('copied', 'dropped', 'rewritten', 'generated')}
        for record in self.records:
            out[record.action] += record.files
        return out

    def to_dict(self) -> dict[str, Any]:
        return {'schema': self.schema, 'mode': self.mode, 'source_label': self.source_label, 'output_label': self.output_label, 'counts': self.counts, 'records': [record.to_dict() for record in self.records], 'obligations': [item.to_dict() for item in self.obligations], 'notes': list(self.notes)}

    def write(self, path: str | Path) -> Path:
        target = Path(path)
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(json.dumps(self.to_dict(), indent=2, ensure_ascii=False, sort_keys=True) + '\n', encoding='utf-8')
        return target

    @classmethod
    def from_dict(cls, raw: Mapping[str, Any]) -> 'TransformationManifest':
        if raw.get('schema') != SCHEMA:
            raise ValueError(f"unsupported transformation schema {raw.get('schema')!r}")
        records = tuple((TransformationRecord(path=str(item.get('path') or ''), action=str(item.get('action') or 'dropped'), reason=str(item.get('reason') or ''), files=int(item.get('files') or 0), bytes=int(item.get('bytes') or 0), examples=tuple((str(value) for value in item.get('examples') or ()))) for item in raw.get('records') or () if isinstance(item, Mapping)))
        obligations = tuple((RestorationObligation(id=str(item.get('id') or ''), kind=str(item.get('kind') or ''), value=str(item.get('value') or ''), reason=str(item.get('reason') or ''), source=str(item.get('source') or '')) for item in raw.get('obligations') or () if isinstance(item, Mapping)))
        return cls(mode=str(raw.get('mode') or ''), records=records, obligations=obligations, source_label=str(raw.get('source_label') or 'reference_game'), output_label=str(raw.get('output_label') or 'task_package'), notes=tuple((str(item) for item in raw.get('notes') or ())))

    @classmethod
    def read(cls, path: str | Path) -> 'TransformationManifest':
        raw = json.loads(Path(path).read_text(encoding='utf-8'))
        if not isinstance(raw, Mapping):
            raise ValueError('transformation manifest root must be an object')
        return cls.from_dict(raw)

@dataclass(frozen=True)
class TransformationDiagnostic:
    id: str
    status: Status
    detail: str
    evidence: Mapping[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return {'id': self.id, 'status': self.status, 'detail': self.detail, 'evidence': dict(self.evidence)}

@dataclass(frozen=True)
class TransformationAudit:
    mode: str
    diagnostics: tuple[TransformationDiagnostic, ...]

    @property
    def status(self) -> Status:
        if any((item.status == 'fail' for item in self.diagnostics)):
            return 'fail'
        if any((item.status == 'unverified' for item in self.diagnostics)):
            return 'unverified'
        return 'pass'

    def by_id(self, diagnostic_id: str) -> TransformationDiagnostic:
        for item in self.diagnostics:
            if item.id == diagnostic_id:
                return item
        raise KeyError(diagnostic_id)

    def to_dict(self) -> dict[str, Any]:
        return {'schema': 'gamebench.package_transformation_audit.v1', 'mode': self.mode, 'status': self.status, 'diagnostics': [item.to_dict() for item in self.diagnostics]}

def _norm(path: Path) -> str:
    return str(path).replace('\\', '/')

def _diagnostic(diagnostic_id: str, status: Status, detail: str, **evidence: Any) -> TransformationDiagnostic:
    return TransformationDiagnostic(diagnostic_id, status, detail, evidence)

def _runtime_status(runtime: Mapping[str, Any] | None, gate: str) -> TransformationDiagnostic:
    if not runtime or gate not in runtime:
        return _diagnostic(f'runtime/{gate}', 'unverified', 'no evaluator runtime reading was supplied')
    raw = runtime[gate]
    if isinstance(raw, Mapping):
        value = str(raw.get('status') or raw.get('verdict') or '').lower()
        detail = str(raw.get('detail') or '')
    else:
        value = str(raw).lower()
        detail = ''
    if value in {'pass', 'passed', 'true'}:
        return _diagnostic(f'runtime/{gate}', 'pass', detail or f'{gate} passed')
    if value in {'fail', 'failed', 'false'}:
        return _diagnostic(f'runtime/{gate}', 'fail', detail or f'{gate} failed')
    return _diagnostic(f'runtime/{gate}', 'unverified', detail or f'{gate} did not produce a decisive reading')

def _check_obligations(project: Path, manifest: TransformationManifest) -> list[TransformationDiagnostic]:
    diagnostics: list[TransformationDiagnostic] = []
    try:
        interface = load_submission_interface(project)
        bound = set(interface.actions.bound) - set(interface.actions.unread)
        numeric = set(interface.numeric)
    except (OSError, ValueError):
        bound = set()
        numeric = set()
    groups = declared_groups(project)
    for item in manifest.obligations:
        if item.kind == 'path':
            ok = (project / item.value).is_file()
        elif item.kind == 'action':
            ok = item.value in bound
        elif item.kind == 'group':
            ok = item.value in groups
        elif item.kind == 'numeric':
            ok = item.value in numeric
        else:
            continue
        diagnostics.append(_diagnostic(f'static/{item.id}', 'pass' if ok else 'fail', item.reason if ok else f'missing {item.kind} obligation {item.value}: {item.reason}', kind=item.kind, value=item.value, source=item.source))
    return diagnostics

def audit_mode3_submission(project: Path, manifest: TransformationManifest, *, runtime: Mapping[str, Any] | None=None) -> TransformationAudit:
    diagnostics = _check_obligations(project, manifest)
    marker_path = project / 'gb_player_adapter.gd'
    marker_present = marker_path.is_file() and STUB_MARKER in marker_path.read_text(encoding='utf-8', errors='replace')
    diagnostics.append(_diagnostic('static/scaffold_completion', 'fail' if marker_present else 'pass', 'supplied scaffold marker is still present' if marker_present else 'supplied scaffold marker was replaced'))
    attachments = {_norm(path.relative_to(project)): set(scene_script_attachments(path)) for path in project.rglob('*.tscn') if path.is_file()}
    attached = any(('res://gb_player_adapter.gd' in values for values in attachments.values()))
    diagnostics.append(_diagnostic('static/scaffold_attachment', 'pass' if attached else 'fail', 'GB adapter remains attached to a supplied scene' if attached else 'no supplied scene remains attached to res://gb_player_adapter.gd'))
    for gate in ('causal_witness', 'mechanic_trace'):
        diagnostics.append(_runtime_status(runtime, gate))
    for item in manifest.obligations:
        if item.kind != 'runtime_check':
            continue
        diagnostics.append(_runtime_status(runtime, item.value))
    return TransformationAudit('skeleton', tuple(diagnostics))

def audit_mode4_submission(project: Path, manifest: TransformationManifest, *, runtime: Mapping[str, Any] | None=None) -> TransformationAudit:
    diagnostics = _check_obligations(project, manifest)
    unexpected = [record for record in manifest.records if record.path.startswith('unexpected_')]
    diagnostics.append(_diagnostic('static/package_transform', 'fail' if unexpected else 'pass', f'sanitization unexpectedly removed {sum((item.files for item in unexpected))} file(s)' if unexpected else 'every source omission has a registered packaging reason', unexpected=[item.to_dict() for item in unexpected]))
    for gate in ('repair_differential', 'gold_replay'):
        diagnostics.append(_runtime_status(runtime, gate))
    return TransformationAudit('bugfix', tuple(diagnostics))
