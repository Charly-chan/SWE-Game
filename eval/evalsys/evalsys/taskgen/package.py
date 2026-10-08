from __future__ import annotations
import hashlib
import json
import re
import shutil
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Iterable
SCHEMA_VERSION = 1
HANDOFF_TEXT = '# Task package\n\nGive the coding agent only `visible/`.\nThe evaluator requires the complete package, including `hidden/`.\nKeep evaluator files outside the agent workspace.\n'
_BOT_AUTOLOAD_RE = re.compile('^\\s*[A-Za-z_][A-Za-z0-9_]*\\s*=\\s*"?\\*?res://(?:[^"\\n]*/)?bot/', re.I)
_AUTOLOAD_PATH_RE = re.compile('=\\s*"?\\*?res://([^"\\n]+)"?\\s*$')

def write_json(path: Path, payload: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, indent=2, ensure_ascii=False, sort_keys=True) + '\n', encoding='utf-8')

def read_json(path: Path) -> Any:
    return json.loads(path.read_text(encoding='utf-8'))

def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open('rb') as handle:
        for chunk in iter(lambda: handle.read(1 << 20), b''):
            digest.update(chunk)
    return digest.hexdigest()

@dataclass
class TaskPackage:
    root: Path
    manifest: dict[str, Any] = field(default_factory=dict)

    @property
    def visible(self) -> Path:
        return self.root / 'visible'

    @property
    def hidden(self) -> Path:
        return self.root / 'hidden'

    @classmethod
    def read(cls, root: str | Path) -> 'TaskPackage':
        base = Path(root).resolve()
        manifest_path = base / 'manifest.json'
        if not manifest_path.is_file():
            raise FileNotFoundError(f'not a task package (missing manifest.json): {base}')
        manifest = read_json(manifest_path)
        if not isinstance(manifest, dict):
            raise ValueError(f'manifest.json is not an object: {manifest_path}')
        version = int(manifest.get('schema_version') or 0)
        if version != SCHEMA_VERSION:
            raise ValueError(f'unsupported taskgen schema_version {version}; expected {SCHEMA_VERSION}')
        if not (base / 'visible').is_dir() or not (base / 'hidden').is_dir():
            raise ValueError(f'package is missing visible/ or hidden/: {base}')
        required = (base / 'HANDOFF.md', base / 'PROMPT.md', base / 'visible' / 'PROMPT.md')
        missing = [path.name for path in required if not path.is_file()]
        if missing:
            raise ValueError('package readiness files are missing: ' + ', '.join(missing))
        return cls(base, manifest)

    def write_manifest(self) -> None:
        (self.root / 'HANDOFF.md').write_text(HANDOFF_TEXT, encoding='utf-8')
        prompt = self.visible / 'PROMPT.md'
        if prompt.is_file():
            shutil.copy2(prompt, self.root / 'PROMPT.md')
        write_json(self.root / 'manifest.json', self.manifest)
