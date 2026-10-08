
from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path
import time

from ..frozen_data import ROOT, install_task, load_manifest
from .modes import Mode, parse_mode
from .package import TaskPackage, write_json
from .visual_materials import freeze_visual_rubric

PLAYTEST_KIT_CREATED_AT = '.created_at'


class GenerateError(ValueError):
    pass


def generate_task(game: str | Path, *, mode: str | Mode, out: str | Path,
                  mutations: str = 'auto', assets: str = 'copy',
                  score_against_source: bool = False, case_id: str = '',
                  playtest_kit: bool = False, reference_video: bool = True) -> TaskPackage:

    resolved = mode if isinstance(mode, Mode) else parse_mode(mode)
    if mutations != 'auto' or assets != 'copy':
        raise GenerateError('released task packages do not support custom construction options')
    if not reference_video and resolved.id != 'brief':
        raise GenerateError('--reference-video off applies only to Mode 1 (brief)')
    data = load_manifest()
    parts = Path(game).parts
    candidates = [part for part in parts if part in data['games']]
    if len(set(candidates)) != 1:
        raise GenerateError(f'unknown or ambiguous game: {game}')
    game_id = candidates[0]
    cases = data['games'][game_id]['cases']
    if resolved.id == 'bugfix':
        if not case_id and len(cases) == 1:
            case_id = cases[0]
        if case_id not in cases:
            raise GenerateError('select a released --case-id: ' + ', '.join(cases))
    elif case_id:
        raise GenerateError('--case-id applies only to bugfix tasks')
    kit = bool(playtest_kit and resolved.id in {'brief', 'gdd', 'skeleton'})
    variant = f'{game_id}--{resolved.id}--{case_id or "default"}--kit{int(kit)}--video{int(reference_video)}'
    dest = Path(out).absolute()
    try:
        install_task(game_id, variant, dest)
        if resolved.id in {"brief", "gdd", "skeleton", "port"} and reference_video:
            freeze_visual_rubric(dest / "hidden", game_id)
        package = TaskPackage.read(dest)
    except (OSError, ValueError, RuntimeError) as exc:
        raise GenerateError(str(exc)) from exc
    if score_against_source:
        package.manifest['score_against_source'] = True
        write_json(dest / 'manifest.json', package.manifest)
    return package


def _write_created_at(target: Path, now: float | None = None, *, budget_s: int | None = None) -> None:

    stamp = time.time() if now is None else now
    iso = datetime.fromtimestamp(stamp, tz=timezone.utc).strftime('%Y-%m-%dT%H:%M:%SZ')
    budget = 'unlimited' if budget_s is None else str(budget_s)
    target.write_text(f'{int(stamp)} {iso} budget_s={budget}\n', encoding='utf-8')

from typing import Any
from ..routes.runner import HARNESS_STOPS

def _surface_contract(project: Path) -> dict[str, Any]:


    scripts = [str(path.relative_to(project)).replace('\\', '/') for path in project.rglob('*.gd') if path.is_file()]
    scenes = [str(path.relative_to(project)).replace('\\', '/') for path in project.rglob('*.tscn') if path.is_file()]
    return {'script_count': len(scripts), 'scene_count': len(scenes), 'scripts': sorted(scripts), 'scenes': sorted(scenes)}

def _suite_status(replay: dict[str, Any], tiers: dict[str, int]) -> dict[str, dict[str, Any]]:
    out: dict[str, dict[str, Any]] = {}
    gold = replay.get('gold') or {}
    for row in gold.get('readings') or []:
        route_id = str(row.get('route_id') or '')
        reading = row.get('reading') or {}
        stop = str(reading.get('stop_reason') or '')
        if row.get('ok'):
            status = 'pass'
        elif not reading or stop in HARNESS_STOPS:
            status = 'infrastructure_error'
        else:
            status = 'behavioral_fail'
        out[route_id] = {'tier': int(tiers.get(route_id, row.get('tier') or 0)), 'status': status, 'stop_reason': stop, 'detail': str(row.get('detail') or '')[:500]}
    for route_id, tier in tiers.items():
        out.setdefault(route_id, {'tier': tier, 'status': 'not_run', 'stop_reason': '', 'detail': ''})
    return out
