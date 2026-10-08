from __future__ import annotations
import importlib.util
import json
import sys
from pathlib import Path
from types import ModuleType

def eval_root() -> Path:
    return Path(__file__).resolve().parents[2]

def tasks_root() -> Path:
    return eval_root() / 'tasks'

def task_dir(task_id: str) -> Path:
    return tasks_root() / task_id

def route_path(task_id: str) -> Path:
    return task_dir(task_id) / 'route.json'

def certificate_path(task_id: str) -> Path:
    return task_dir(task_id) / 'certificate.rt1.json'

def expectations_path(task_id: str) -> Path:
    return task_dir(task_id) / 'expectations.json'

def snapshot_path(task_id: str) -> Path:
    return task_dir(task_id) / 'snapshot.json'

def task_metadata(task_id: str) -> dict:
    path = task_dir(task_id) / 'task.json'
    if not path.is_file():
        return {}
    raw = json.loads(path.read_text(encoding='utf-8'))
    if not isinstance(raw, dict):
        raise ValueError(f'task metadata must be a JSON object: {path}')
    return raw

def all_task_metadata() -> dict[str, dict]:
    out: dict[str, dict] = {}
    if not tasks_root().is_dir():
        return out
    for path in tasks_root().glob('*/task.json'):
        out[path.parent.name] = task_metadata(path.parent.name)
    return out

def registry_path() -> Path:
    return eval_root() / 'routes' / 'registry.json'

def artifacts_dir(task_id: str) -> Path:
    return eval_root() / 'artifacts' / task_id

def runs_dir(task_id: str) -> Path:
    return artifacts_dir(task_id) / 'runs'

def resolve_eval_path(path: str | Path) -> Path:
    candidate = Path(path)
    return candidate if candidate.is_absolute() else eval_root() / candidate

def registered_task_ids() -> tuple[str, ...]:
    path = registry_path()
    if not path.is_file():
        return ()
    raw = json.loads(path.read_text(encoding='utf-8'))
    return tuple(sorted((str(key) for key in raw.get('tasks') or {})))

def default_registered_task_id() -> str:
    ids = registered_task_ids()
    if len(ids) != 1:
        raise RuntimeError('a task id is required when the registry does not contain exactly one task')
    return ids[0]
