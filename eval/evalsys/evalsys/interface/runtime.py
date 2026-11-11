

from __future__ import annotations

import json
from pathlib import Path

from .model import SubmissionInterface


def write_runtime_interface(interface: SubmissionInterface, directory: str | Path) -> Path:
    root = Path(directory).resolve()
    root.mkdir(parents=True, exist_ok=True)
    path = root / f"gb_interface_{interface.normalized_sha256[:16]}.json"
    expected = json.dumps(
        interface.runtime_dict(), indent=2, sort_keys=True, ensure_ascii=True
    ) + "\n"
    if not path.is_file() or path.read_text(encoding="utf-8") != expected:
        path.write_text(expected, encoding="utf-8")
    return path


def runtime_args(interface: SubmissionInterface, path: str | Path) -> list[str]:
    return [
        f"--gb-interface-file={Path(path).resolve()}",
        f"--gb-interface-version={interface.version}",
        f"--gb-interface-source-sha={interface.source_sha256}",
        f"--gb-interface-normalized-sha={interface.normalized_sha256}",
    ]
