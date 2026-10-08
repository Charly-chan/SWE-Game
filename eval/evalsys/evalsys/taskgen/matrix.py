

from __future__ import annotations

import csv
import hashlib
import json
import os
import platform
import re
import shlex
import shutil
import subprocess
import tempfile
import threading
import time
from collections import Counter, defaultdict
from contextlib import contextmanager
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable, Iterable, Mapping

from ..interface.loader import project_root
from .docker_sandbox import (
    CONTAINER_ENV_KEYS,
    CONTAINER_HOME,
    CONTAINER_WORKSPACE,
    DEFAULT_SANDBOX_IMAGE,
    DockerSandbox,
    DockerSandboxError,
)
from .evaluate import evaluate_task
from .generate import GenerateError, PLAYTEST_KIT_CREATED_AT, _write_created_at, generate_task
from .mode4.mode4_cases import active_mode4_cases
from .mode5.path_safety import reject_links


from .modes import MODES, PORT, Mode, parse_mode
from .package import TaskPackage, write_json
from .unity.unity_interface import find_unity_project


REPO_ROOT = Path(__file__).resolve().parents[4]
CATALOG_PATH = REPO_ROOT / "catalog.json"
MATRIX_SCHEMA = "gamebench.taskgen.matrix.v2"
AGENT_DEADLINE_CONTRACT_VERSION = "2026-09-11.v3"
AGENT_ARTIFACT_SCHEMA = "gamebench.taskgen.agent-artifacts.v1"
WORKSPACE_READY = ".taskgen_workspace_ready"
EVALUATOR_GODOT = Path(os.environ.get("GODOT_BIN") or "/opt/godot451-bin/godot")


PINNED_GODOT_VERSION = "4.5.1"
PINNED_GODOT_SHA256 = (
    "db07cae7de644278a1884d4552bdf2bca3f5d30131b18faf3a0c4d730080b199"
)
CODEX_PROVIDER_ID = "gamebench_proxy"
DEFAULT_CODEX_MODEL = "gpt-5.6"
DEFAULT_CODEX_BASE_URL = "https://vip.auto-code.net/v1"
DEFAULT_CODEX_KEY_ENV = "AUTO_CODE_API_KEY"
DEFAULT_CLAUDE_MODEL = "opus"
DEFAULT_CLAUDE_BASE_URL = "https://www.micuapi.ai"
DEFAULT_CLAUDE_KEY_ENV = "ANTHROPIC_API_KEY"


CODEX_PROVIDER_KINDS = ("custom", "openai")
DEFAULT_CODEX_AUTH_FILE = "~/.codex/auth.json"


AGENT_HOME_ROOT_VISIBLE = os.environ.get("GB_AGENT_HOME_ROOT") or "/var/tmp"


AGENT_SANDBOXES = ("unshare", "docker", "none")
VLM_ENV_KEYS = (
    "GAMEBENCH_VLM_PROVIDER",
    "GAMEBENCH_VLM_MODEL",
    "GAMEBENCH_VLM_BASE_URL",
    "GAMEBENCH_VLM_KEY_ENV",
    "GAMEBENCH_VLM_TRANSPORT",
)
_REAL_SUBPROCESS_RUN = subprocess.run


class MatrixError(RuntimeError):
    pass


class AgentProcessError(MatrixError):


    def __init__(self, failure_kind: str, exit_code: int, detail: str) -> None:
        super().__init__(detail)
        self.failure_kind = failure_kind
        self.exit_code = exit_code


@dataclass(frozen=True)
class SelectedCase:
    game_id: str
    game_path: Path
    mode: Mode
    case_id: str = ""

    def __iter__(self):

        yield self.game_id
        yield self.game_path
        yield self.mode

    def __len__(self) -> int:
        return 3

    def __getitem__(self, index: int):
        return (self.game_id, self.game_path, self.mode)[index]


@dataclass(frozen=True)
class AgentConfig:
    backend: str = "none"
    command: str = ""
    env_file: str = ""
    model: str = DEFAULT_CODEX_MODEL
    base_url: str = DEFAULT_CODEX_BASE_URL
    key_env: str = DEFAULT_CODEX_KEY_ENV
    effort: str = "high"


    timeout_s: int | None = None
    sandbox: str = "unshare"

    docker_image: str = ""

    provider: str = "custom"

    auth_file: str = ""

    dry_run: bool = False


AGENT_DRY_RUN_EXIT = -1


@dataclass
class CaseResult:
    game_id: str
    mode: str
    root: Path
    case_id: str = ""
    status: str = "pending"
    phase: str = "pending"
    detail: str = ""
    failure_kind: str = ""
    agent_exit_code: int | None = None
    resolved: bool | None = None
    eligibility: str = "not_measured"
    started_at: str = ""
    finished_at: str = ""
    durations_s: dict[str, float] = field(default_factory=dict)
    metrics: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return {
            "schema": MATRIX_SCHEMA,
            "game_id": self.game_id,
            "mode": self.mode,
            "case_id": self.case_id,
            "root": str(self.root),
            "status": self.status,
            "phase": self.phase,
            "detail": self.detail,
            "failure_kind": self.failure_kind,
            "agent_exit_code": self.agent_exit_code,
            "resolved": self.resolved,
            "eligibility": self.eligibility,
            "started_at": self.started_at,
            "finished_at": self.finished_at,
            "durations_s": self.durations_s,
            "metrics": self.metrics,
        }


def _utc_now() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat()


def load_catalog(path: str | Path = CATALOG_PATH) -> list[dict[str, Any]]:
    raw = json.loads(Path(path).read_text(encoding="utf-8"))
    if not isinstance(raw, list):
        raise MatrixError(f"catalog must be a list: {path}")
    rows: list[dict[str, Any]] = []
    for item in raw:
        if not isinstance(item, dict) or not item.get("id") or not item.get("path"):
            raise MatrixError(f"catalog row lacks id/path: {item!r}")
        rows.append(dict(item))
    return rows


def select_cases(
    *,
    game_ids: Iterable[str] = (),
    modes: Iterable[str] = (),
    case_ids: Iterable[str] = (),
    catalog_path: str | Path = CATALOG_PATH,
) -> list[SelectedCase]:
    requested_games = {str(item).strip() for item in game_ids if str(item).strip()}
    requested_cases = {str(item).strip() for item in case_ids if str(item).strip()}
    found_cases: set[str] = set()
    selected_modes: list[Mode] = []
    seen_modes: set[str] = set()
    for item in modes:
        parsed = parse_mode(str(item))
        if parsed.id not in seen_modes:
            selected_modes.append(parsed)
            seen_modes.add(parsed.id)
    if not selected_modes:
        selected_modes = list(MODES.values())
    rows = load_catalog(catalog_path)
    known = {str(row["id"]) for row in rows}
    missing = sorted(requested_games - known)
    if missing:
        raise MatrixError("unknown game id(s): " + ", ".join(missing))
    cases: list[SelectedCase] = []
    for row in rows:
        game_id = str(row["id"])
        if requested_games and game_id not in requested_games:
            continue
        game_path = (REPO_ROOT / str(row["path"])).resolve()
        for mode in selected_modes:
            if mode.id == "bugfix":
                bundles = active_mode4_cases(game_id)
                if requested_cases:
                    bundles = tuple(
                        case for case in bundles
                        if str(case.get("case_id") or "") in requested_cases
                    )
                if bundles:
                    found_cases.update(str(case["case_id"]) for case in bundles)
                    cases.extend(
                        SelectedCase(game_id, game_path, mode, str(case["case_id"]))
                        for case in bundles
                    )
                elif not requested_cases:


                    cases.append(SelectedCase(game_id, game_path, mode))
            else:
                cases.append(SelectedCase(game_id, game_path, mode))
    missing_cases = sorted(requested_cases - found_cases)
    if missing_cases:
        raise MatrixError(
            "requested Mode-4 case id(s) are not active in the selected games: "
            + ", ".join(missing_cases)
        )
    return cases


def run_matrix(
    out: str | Path,
    *,
    game_ids: Iterable[str] = (),
    modes: Iterable[str] = (),
    case_ids: Iterable[str] = (),
    agent: AgentConfig = AgentConfig(),
    engine: str = "auto",
    visual_judge: str = "none",
    resume: bool = False,
    generate_only: bool = False,
    catalog_path: str | Path = CATALOG_PATH,
    playtest_kit: bool = False,
    single_cell: bool = False,
    reference_video: bool = True,
    evaluate: bool = True,
    community_scaffold: bool = False,
) -> dict[str, Any]:
    """Run the selected cases.

    ``single_cell`` is the layout ``run_benchmark.sh`` uses: exactly one case
    whose ``package/ workspace/ agent/ submission/ evaluation/ state.json`` land
    directly under ``out`` (the cell directory), while the frozen configuration
    and the matrix summaries go to ``out/matrix/``.  The default layout keeps
    the historical ``out/runs/<game>/<mode>[/<case>]/`` tree.
    """
    root = Path(out).resolve()
    meta_root = root / "matrix" if single_cell else root
    if root.exists() and any(root.iterdir()) and not resume:
        raise MatrixError(f"matrix output is non-empty; pass --resume: {root}")
    root.mkdir(parents=True, exist_ok=True)
    meta_root.mkdir(parents=True, exist_ok=True)
    cases = select_cases(
        game_ids=game_ids,
        modes=modes,
        case_ids=case_ids,
        catalog_path=catalog_path,
    )
    if not reference_video and any(selected.mode.id != "brief" for selected in cases):
        raise MatrixError("--reference-video off applies only to Mode 1 (brief)")
    if single_cell and len(cases) != 1:
        raise MatrixError(
            f"single-cell layout needs exactly one game x mode x case, selected {len(cases)}"
        )
    _write_or_validate_run(
        meta_root,
        cases,
        agent=agent,
        engine=engine,
        visual_judge=visual_judge,
        resume=resume,
        playtest_kit=playtest_kit,
        reference_video=reference_video,
        community_scaffold=community_scaffold,
    )
    results: list[CaseResult] = []
    for selected in cases:
        game_id, game_path, mode = selected
        if single_cell:
            case_root = root
        else:
            case_root = root / "runs" / game_id / mode.id
            if selected.case_id:
                case_root = case_root / selected.case_id
        result = _run_case(
            game_id,
            game_path,
            mode,
            case_root,
            case_id=selected.case_id,
            agent=agent,
            engine=engine,
            visual_judge=visual_judge,
            resume=resume,
            generate_only=generate_only,
            playtest_kit=playtest_kit,
            reference_video=reference_video,
            evaluate=evaluate,
            community_scaffold=community_scaffold,
        )
        results.append(result)
        _write_matrix_summary(meta_root, results, total=len(cases))
    return _write_matrix_summary(meta_root, results, total=len(cases))


def _write_or_validate_run(
    root: Path,
    cases: list[SelectedCase],
    *,
    agent: AgentConfig,
    engine: str,
    visual_judge: str,
    resume: bool,
    playtest_kit: bool = False,
    reference_video: bool = True,
    community_scaffold: bool = False,
) -> None:
    path = root / "run.json"
    selected = [
        {
            "game_id": selected.game_id,
            "game_path": str(selected.game_path),
            "mode": selected.mode.id,
            "case_id": selected.case_id,
        }
        for selected in cases
    ]
    provider = _agent_provider(agent)
    vlm_route = _matrix_vlm_route(agent, visual_judge)
    frozen = {
        "schema": MATRIX_SCHEMA,
        "selected": selected,
        "agent": {
            "backend": agent.backend,
            "command": agent.command,
            "env_file": agent.env_file,
            "model": agent.model,
            "provider": provider,
            "effort": agent.effort,
            "deadline_contract": AGENT_DEADLINE_CONTRACT_VERSION,
            "sandbox": agent.sandbox,
            "timeout_s": agent.timeout_s,
        },
        "engine": engine,
        "visual_judge": visual_judge,
        "vlm_route": vlm_route,
        "playtest_kit": "on" if playtest_kit else "off",
        "reference_video": "on" if reference_video else "off",
    }
    if agent.sandbox == "docker":
        frozen["agent"]["docker_image"] = agent.docker_image
    if community_scaffold:
        frozen["mode5_scaffold"] = "community-builtins-v2"
    current_commit = _git_commit()
    if path.is_file():
        existing = json.loads(path.read_text(encoding="utf-8"))
        comparable = {key: existing.get(key) for key in frozen}
        if comparable != frozen:
            raise MatrixError("--resume configuration differs from frozen run.json")
        recorded_commit = str(existing.get("benchmark_commit") or "")
        drifted = bool(recorded_commit and current_commit and recorded_commit != current_commit)
        if drifted:
            # The check cannot tell a scoring-only change from one that moves the
            # prompt, the package or the harness, so by default it refuses either
            # way. An operator who has audited the diff and found nothing the
            # agent can see may name the frozen commit to proceed; the override
            # is recorded in run.json so the mixture is never silent.
            allowed = os.environ.get(RESUME_ACROSS_COMMITS_ENV, "").strip()
            if allowed != recorded_commit:
                raise MatrixError(
                    "--resume benchmark commit differs from frozen run.json: "
                    f"{recorded_commit} != {current_commit}. Audit the diff for "
                    "agent-visible changes (prompt, package, harness, task "
                    f"materials); if there are none, set {RESUME_ACROSS_COMMITS_ENV}"
                    f"={recorded_commit} to record the override and proceed"
                )
            history = list(existing.get("benchmark_commit_history") or [recorded_commit])
            if current_commit not in history:
                history.append(current_commit)
            write_json(path, {
                **existing,
                "benchmark_commit_history": history,
                "resumed_across_commits": True,
            })
        if (existing.get("benchmark_worktree_clean") is True
                and not _git_worktree_clean() and not drifted):
            raise MatrixError(
                "--resume benchmark worktree became dirty after a formal run started"
            )
        return
    if resume:
        raise MatrixError(f"--resume requested but run.json is missing: {path}")
    write_json(
        path,
        {
            **frozen,
            "created_at": _utc_now(),
            "benchmark_commit": current_commit,
            "benchmark_worktree_clean": _git_worktree_clean(),
        },
    )


def _git_commit() -> str:
    try:
        return subprocess.run(
            ["git", "rev-parse", "HEAD"],
            cwd=REPO_ROOT,
            capture_output=True,
            text=True,
            timeout=10,
            check=False,
        ).stdout.strip()
    except (OSError, subprocess.TimeoutExpired):
        return ""


def _git_worktree_clean() -> bool:
    try:
        shown = subprocess.run(
            ["git", "status", "--porcelain", "--untracked-files=all"],
            cwd=REPO_ROOT,
            capture_output=True,
            text=True,
            timeout=10,
            check=False,
        )
        return shown.returncode == 0 and not shown.stdout.strip()
    except (OSError, subprocess.TimeoutExpired):
        return False


@contextmanager
def _matrix_vlm_environment(agent: AgentConfig, visual_judge: str):

    backend = agent.backend.strip().lower()
    if visual_judge != "vlm" or backend not in {"codex", "claude"}:
        yield
        return
    selected = _matrix_vlm_route(agent, visual_judge)
    key_env = str(selected.get("key_env") or "")
    tracked = tuple(VLM_ENV_KEYS) + ((key_env,) if key_env else ())
    previous = {name: os.environ.get(name) for name in tracked}
    environment = {
        "GAMEBENCH_VLM_PROVIDER": str(selected.get("provider") or ""),
        "GAMEBENCH_VLM_MODEL": str(selected.get("model") or ""),
        "GAMEBENCH_VLM_BASE_URL": str(selected.get("base_url") or ""),
        "GAMEBENCH_VLM_KEY_ENV": key_env,
        "GAMEBENCH_VLM_TRANSPORT": str(selected.get("transport") or ""),
    }
    env_values = _load_env_file(agent.env_file)
    try:
        if key_env and not os.environ.get(key_env) and env_values.get(key_env):
            os.environ[key_env] = env_values[key_env]
        for name, value in environment.items():
            if value:
                os.environ[name] = value
            else:
                os.environ.pop(name, None)
        yield
    finally:
        for name, value in previous.items():
            if value is None:
                os.environ.pop(name, None)
            else:
                os.environ[name] = value


def _matrix_vlm_route(agent: AgentConfig, visual_judge: str) -> dict[str, Any]:
    backend = agent.backend.strip().lower()
    if visual_judge != "vlm" or backend not in {"codex", "claude"}:
        return {"enabled": False}
    provider = _agent_provider(agent) or {}
    return {
        "enabled": True,
        "provider": "responses" if backend == "codex" else "anthropic",
        "transport": "responses" if backend == "codex" else "claude_code",
        "model": str(provider.get("model") or agent.model),
        "base_url": str(provider.get("base_url") or ""),
        "key_env": str(provider.get("key_env") or ""),
    }


def _run_case(
    game_id: str,
    game_path: Path,
    mode: Mode,
    root: Path,
    *,
    case_id: str = "",
    agent: AgentConfig,
    engine: str,
    visual_judge: str,
    resume: bool,
    generate_only: bool,
    playtest_kit: bool = False,
    reference_video: bool = True,
    evaluate: bool = True,
    community_scaffold: bool = False,
) -> CaseResult:
    state_path = root / "state.json"
    previous: dict[str, Any] = {}
    if resume and state_path.is_file():
        previous = json.loads(state_path.read_text(encoding="utf-8"))
        if previous.get("status") in {"completed", "package_blocked"} or (
            generate_only and previous.get("phase") in {"generated", "completed"}
        ):
            return _case_from_wire(previous, root)
    prior_phase = str(previous.get("phase") or "")
    result = (
        _case_from_wire(previous, root)
        if previous else
        CaseResult(
            game_id=game_id,
            mode=mode.id,
            root=root,
            case_id=case_id,
            started_at=_utc_now(),
        )
    )
    result.status = "running"
    result.detail = ""
    result.failure_kind = ""
    result.finished_at = ""
    if not result.started_at:
        result.started_at = _utc_now()
    if not prior_phase:
        result.phase = "start"
    root.mkdir(parents=True, exist_ok=True)
    _save_case(result)
    try:
        package_dir = root / "package"
        package: TaskPackage | None = None
        if resume and (package_dir / "manifest.json").is_file():
            try:
                package = TaskPackage.read(package_dir)
            except (OSError, ValueError, json.JSONDecodeError):
                package = None
        if package is None:
            if package_dir.exists():
                shutil.rmtree(package_dir)
            started = time.monotonic()
            generate_task(
                game_path,
                mode=mode,
                out=package_dir,
                case_id=case_id,
                playtest_kit=playtest_kit,
                reference_video=reference_video,
                community_scaffold=community_scaffold,
            )
            result.durations_s["generate"] = round(time.monotonic() - started, 3)
            package = TaskPackage.read(package_dir)
        result.phase = "generated"
        _save_case(result)

        workspace = root / "workspace"
        workspace_ready = root / WORKSPACE_READY
        agent_completed = bool(
            resume
            and result.agent_exit_code == 0
            and prior_phase in {"collect", "submitted", "evaluate", "completed"}
        )
        reuse_workspace = bool(
            resume and workspace.is_dir() and workspace_ready.is_file()
        )
        if prior_phase == "agent" and not agent_completed:
            # A failed agent leaves half-written files behind, and handing them
            # back makes the next attempt a different experiment: the model now
            # starts from work it cannot account for. Discarding them is the
            # right default.
            #
            # It is the wrong default when the agent was cut off by something
            # outside the submission -- a gateway that ran out of quota mid-run
            # -- and hours of real work is sitting in the workspace. Then the
            # operator may choose to hand it back, but the choice has to be
            # explicit and it has to be visible afterwards: the cell keeps a
            # marker so nobody later mistakes it for a clean single-shot run.
            if os.environ.get(REUSE_AGENT_WORKSPACE_ENV) == "1" and reuse_workspace:
                (root / REUSED_WORKSPACE_MARKER).write_text(
                    "the agent phase failed and its workspace was handed back "
                    "under %s; this cell is not a clean single-shot run\n"
                    % REUSE_AGENT_WORKSPACE_ENV,
                    encoding="utf-8",
                )
            else:
                reuse_workspace = False
        if not reuse_workspace:
            if workspace.exists():
                shutil.rmtree(workspace)
            shutil.copytree(package.visible, workspace)
            workspace_ready.write_text("ready\n", encoding="utf-8")
        if generate_only:
            package_blockers = list(package.manifest.get("blockers") or [])
            dry_run_agent = (
                not package_blockers and agent.dry_run and agent.backend != "none"
            )
            if dry_run_agent:
                result.agent_exit_code = run_coding_agent(workspace, root / "agent", agent,
                    **({"mode": mode.id} if agent.sandbox == "docker" else {}))
            result.status = "package_blocked" if package_blockers else "generated"
            result.phase = result.status
            result.detail = (
                "; ".join(package_blockers)
                if package_blockers else
                "package, visible-only workspace and agent request generated (dry run)"
                if dry_run_agent else
                "package and visible-only workspace generated"
            )
            result.finished_at = _utc_now()
            _save_case(result)
            return result

        package_blockers = list(package.manifest.get("blockers") or [])
        if package_blockers:
            result.status = "package_blocked"
            result.phase = "package_blocked"
            result.detail = "; ".join(package_blockers)
            result.finished_at = _utc_now()
            _save_case(result)
            return result

        submission = root / "submission"
        have_submission = _submission_ready(submission, mode)
        if not (resume and have_submission):
            if agent.backend == "none":
                raise MatrixError("agent backend is none; use --generate-only or configure an agent")
            if not agent_completed:
                result.phase = "agent"
                result.agent_exit_code = None
                _save_case(result)
                started = time.monotonic()
                result.agent_exit_code = run_coding_agent(workspace, root / "agent", agent,
                    **({"mode": mode.id} if agent.sandbox == "docker" else {}))
                result.durations_s["agent"] = round(time.monotonic() - started, 3)
                result.phase = "collect"
                _save_case(result)
            if submission.exists():
                shutil.rmtree(submission)
            collect_submission(workspace, submission, mode=mode)

        if not evaluate:
            # Keep this resumable: "completed" would skip a later --eval on.
            result.status = "submitted"
            result.phase = "submitted"
            result.resolved = None
            result.eligibility = "not_measured"
            result.detail = "evaluation deferred (--eval off)"
            result.finished_at = _utc_now()
            _save_case(result)
            return result

        result.phase = "evaluate"
        _save_case(result)
        started = time.monotonic()
        with _matrix_vlm_environment(agent, visual_judge):
            evaluated = evaluate_task(
                package.root,
                submission,
                engine=engine,
                visual_judge=visual_judge,
                out=root / "evaluation",
            )
        result.durations_s["evaluate"] = round(time.monotonic() - started, 3)
        result.resolved = evaluated.resolved
        result.eligibility = evaluated.eligibility_status
        evaluation_wire = evaluated.to_dict()
        headline = dict(evaluation_wire.get("headline") or {})
        reproduction = next(
            (item for item in evaluated.fidelity_items if item.id == "reproduction"),
            None,
        )
        reproduction_evidence = dict(getattr(reproduction, "evidence", {}) or {})
        reproduction_card = reproduction_evidence.get("card")
        try:
            agent_usage = json.loads(
                (root / "agent" / "usage.json").read_text(encoding="utf-8")
            )
        except (OSError, json.JSONDecodeError):
            agent_usage = {}
        result.metrics = {
            "headline_status": headline.get("status"),
            "headline_score": headline.get("score"),
            "ranking_eligible": headline.get("ranking_eligible"),
            "behavior_score_lo": evaluation_wire["behavior"]["score"]["lo"],
            "behavior_score_hi": evaluation_wire["behavior"]["score"]["hi"],
            "behavior_coverage": evaluation_wire["behavior"]["score"]["coverage"],
            "comparable": bool(evaluation_wire.get("comparable")),
            "reproduction_credit": (
                float(reproduction.credit)
                if reproduction is not None and reproduction.credit is not None
                else None
            ),
            "reproduction": {
                "credit": (
                    float(reproduction.credit)
                    if reproduction is not None and reproduction.credit is not None
                    else None
                ),
                "package": reproduction_evidence.get("path"),
                "card_path": reproduction_evidence.get("card_path"),
                "card": reproduction_card if isinstance(reproduction_card, dict) else None,
            },
            "item_verdicts": {
                item.id: item.verdict.value for item in evaluated.items
            },
            "effective_effort": agent_usage.get("effective_reasoning_effort"),
            "agent_usage": agent_usage,
            "scorecard": evaluation_wire.get("scorecard"),
        }
        result.status = "completed"
        result.phase = "completed"
        result.detail = (
            "resolved=yes" if result.resolved is True else
            "resolved=no" if result.resolved is False else
            "resolved=not_measured"
        )
    except GenerateError as exc:
        result.status = "package_blocked"
        result.phase = "package_blocked"
        result.detail = str(exc)
    except AgentProcessError as exc:
        result.status = "failed"
        result.phase = "agent"
        result.failure_kind = exc.failure_kind
        result.agent_exit_code = exc.exit_code
        result.detail = str(exc)
    except Exception as exc:
        result.status = "failed"
        result.failure_kind = {
            "start": "package_error",
            "generated": "workspace_error",
            "agent": "agent_transport_error",
            "collect": "submission_contract_error",
            "evaluate": "verifier_system_error",
        }.get(result.phase, "pipeline_error")
        result.detail = f"{type(exc).__name__}: {exc}"
    result.finished_at = _utc_now()
    _save_case(result)
    return result


def _save_case(result: CaseResult) -> None:
    write_json(result.root / "state.json", result.to_dict())


def _case_from_wire(raw: dict[str, Any], root: Path) -> CaseResult:
    return CaseResult(
        game_id=str(raw.get("game_id") or ""),
        mode=str(raw.get("mode") or ""),
        root=root,
        case_id=str(raw.get("case_id") or ""),
        status=str(raw.get("status") or "pending"),
        phase=str(raw.get("phase") or "pending"),
        detail=str(raw.get("detail") or ""),
        failure_kind=str(raw.get("failure_kind") or ""),
        agent_exit_code=raw.get("agent_exit_code"),
        resolved=raw.get("resolved"),
        eligibility=str(raw.get("eligibility") or "not_measured"),
        started_at=str(raw.get("started_at") or ""),
        finished_at=str(raw.get("finished_at") or ""),
        durations_s=dict(raw.get("durations_s") or {}),
        metrics=dict(raw.get("metrics") or {}),
    )


def _load_env_file(path: str) -> dict[str, str]:
    values: dict[str, str] = {}
    if not path:
        return values
    source = Path(path)
    if not source.is_file():
        raise MatrixError(f"agent env file does not exist: {source}")
    for raw in source.read_text(encoding="utf-8").splitlines():
        line = raw.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        values[key.strip()] = value.strip().strip("'\"")
    return values


def _toml_string(value: str) -> str:

    return json.dumps(str(value), ensure_ascii=False)


def _codex_provider_kind(config: AgentConfig) -> str:
    kind = (config.provider or "custom").strip().lower() or "custom"
    if kind not in CODEX_PROVIDER_KINDS:
        raise MatrixError(
            f"unknown codex provider kind {config.provider!r}; expected one of {CODEX_PROVIDER_KINDS}"
        )
    return kind


def _agent_provider(config: AgentConfig) -> dict[str, str] | None:
    backend = config.backend.strip().lower()
    if backend == "codex":
        if _codex_provider_kind(config) == "openai":
            return {
                "kind": "openai",
                "model": config.model.strip() or DEFAULT_CODEX_MODEL,
                "base_url": "",
                "key_env": "OPENAI_API_KEY",
                "auth_file": str(config.auth_file or DEFAULT_CODEX_AUTH_FILE),
                "wire_api": "responses",
            }
        return {
            "kind": "custom",
            "model": config.model.strip() or DEFAULT_CODEX_MODEL,
            "base_url": config.base_url.strip() or DEFAULT_CODEX_BASE_URL,
            "key_env": config.key_env.strip() or DEFAULT_CODEX_KEY_ENV,
            "wire_api": "responses",
        }
    if backend == "claude":
        model = config.model.strip()
        base_url = config.base_url.strip()
        key_env = config.key_env.strip()


        if not model or model == DEFAULT_CODEX_MODEL:
            model = DEFAULT_CLAUDE_MODEL
        if not base_url or base_url == DEFAULT_CODEX_BASE_URL:
            base_url = DEFAULT_CLAUDE_BASE_URL
        if not key_env or key_env == DEFAULT_CODEX_KEY_ENV:
            key_env = DEFAULT_CLAUDE_KEY_ENV
        return {
            "model": model,
            "base_url": base_url,
            "key_env": key_env,
            "wire_api": "anthropic_messages",
        }
    return None


def _redact(text: str | bytes | None, secrets: Iterable[str]) -> str:
    if text is None:
        return ""
    if isinstance(text, bytes):
        rendered = text.decode("utf-8", errors="replace")
    else:
        rendered = text
    for secret in secrets:
        if secret:
            rendered = rendered.replace(secret, "<redacted>")
    return rendered


CHILD_TIMEOUT_CAP_MAX_S = 120


CHILD_TIMEOUT_SHIM_PY = '''#!/usr/bin/env python3
import os
import re
import sys

args = sys.argv[1:]
index = 0
while index < len(args):
    value = args[index]
    if value == "--":
        index += 1
        break
    if value in {"-k", "--kill-after", "-s", "--signal"}:
        index += 2
        continue
    if value.startswith("-"):
        index += 1
        continue
    break

if index < len(args):
    match = re.fullmatch(r"([0-9]+(?:[.][0-9]+)?)([smhd]?)", args[index])
    if match:
        scale = {"": 1.0, "s": 1.0, "m": 60.0, "h": 3600.0, "d": 86400.0}
        requested_s = float(match.group(1)) * scale[match.group(2)]
        cap_s = int(os.environ["GB_CHILD_TIMEOUT_CAP_S"])
        if requested_s > cap_s:
            args[index] = f"{cap_s}s"

real_timeout = os.environ["GB_REAL_TIMEOUT"]
os.execv(real_timeout, [real_timeout, *args])
'''


def _child_timeout_cap(timeout_s: int | None, *, mode: str | None = None) -> int:
    if mode == "port":
        # Unity cold import/build is substantially slower than a Godot check.
        # Keep it bounded without imposing the shared two-minute ceiling.
        return min(1200, max(120, timeout_s // 4)) if timeout_s is not None else 1200
    if timeout_s is None:
        return CHILD_TIMEOUT_CAP_MAX_S
    return min(CHILD_TIMEOUT_CAP_MAX_S, max(30, timeout_s // 10))


def _deadline_prompt(timeout_s: int | None, *, mode: str | None = None) -> str:
    command_cap = _child_timeout_cap(timeout_s, mode=mode)
    if timeout_s is None:
        deadline = (
            "The coding-agent process has no external wall-clock timeout: the harness "
            "waits until you exit on your own, and records a submission only after a "
            "normal process exit. Finish by writing every required contract file, "
            "running a bounded final check, and exiting normally. "
        )
    else:
        deadline = (
            f"The coding-agent process has a hard external timeout of {timeout_s} seconds. "
            "If that timeout fires, the harness records no submission even when files exist. "
            "Reserve the final 20% of the budget for writing every required contract file, "
            "a bounded final check, and a normal process exit. "
        )
    return (
        "\n\n## Harness execution budget (mandatory)\n\n"
        + deadline
        + f"Do not start any child command with a timeout above {command_cap} seconds. "
        "When a bounded check times out, record that uncertainty and finish the required "
        "submission instead of repeating or extending the same check. Optional polish and "
        "self-tests must never prevent a normal exit.\n"
        f"Deadline contract version: {AGENT_DEADLINE_CONTRACT_VERSION}.\n"
    )


def _tool_version(argv: list[str]) -> str | None:
    try:
        proc = subprocess.run(
            argv, capture_output=True, text=True, timeout=5, check=False
        )
    except (OSError, subprocess.SubprocessError):
        return None
    text = (proc.stdout or proc.stderr or "").strip()
    return text.splitlines()[0] if text else None


AGENT_ENV_TOOLS = ("jq", "ffmpeg", "ffprobe", "xvfb-run", "node", "ruby", "python3")


@dataclass(frozen=True)
class EnvironmentProbe:


    location: str
    tool_version: Callable[[list[str]], str | None]
    has_tool: Callable[[str, str | None], bool]
    dir_writable: Callable[[str], bool]
    uname: Callable[[], str]
    agent_cli: Callable[[], dict[str, str | None]]


HOST_PROBE = EnvironmentProbe(
    location="host",
    tool_version=_tool_version,
    has_tool=lambda name, path: shutil.which(name, path=path) is not None,
    dir_writable=lambda path: bool(
        Path(path).is_dir() and os.access(path, os.W_OK)
    ),
    uname=lambda: " ".join(platform.uname()),
    agent_cli=lambda: {"codex": None, "claude": None},
)


HARNESS_ENV_PREFIXES = ("ANTHROPIC_", "CLAUDE_", "CODEX_", "OPENAI_", "GB_")


HARNESS_ENV_SECRET_MARKERS = ("KEY", "TOKEN", "SECRET", "PASSWORD", "CREDENTIAL")


HARNESS_ENV_PUBLIC_NAMES = ("CLAUDE_CODE_MAX_CONTEXT_TOKENS",)
HARNESS_ENV_PUBLIC_SUFFIXES = ("_KEY_ENV",)


def _harness_env_is_secret(name: str) -> bool:
    upper = name.upper()
    if upper in HARNESS_ENV_PUBLIC_NAMES or upper.endswith(HARNESS_ENV_PUBLIC_SUFFIXES):
        return False
    return any(marker in upper for marker in HARNESS_ENV_SECRET_MARKERS)


def _harness_env_snapshot(env: Mapping[str, str]) -> dict[str, str]:


    snapshot: dict[str, str] = {}
    for name in sorted(env):
        if not name.startswith(HARNESS_ENV_PREFIXES):
            continue
        value = str(env[name] or "")
        if _harness_env_is_secret(name):
            snapshot[name] = f"<set:{len(value)}>" if value else "<empty>"
        else:
            snapshot[name] = value
    return snapshot


def _agent_environment(
    env: dict[str, str],
    *,
    timeout_s: int | None,
    child_timeout_cap_s: int | None,
    probe: EnvironmentProbe | None = None,
) -> dict[str, Any]:
    probe = probe or HOST_PROBE
    home = str(env.get("HOME") or "")
    return {
        "schema": AGENT_ARTIFACT_SCHEMA,
        "godot": {
            "path": env.get("GODOT_BIN"),
            "version": (
                probe.tool_version([env["GODOT_BIN"], "--version"])
                if env.get("GODOT_BIN")
                else None
            ),
        },
        "unity": {
            "path": env.get("UNITY_BIN"),
            "version": (
                probe.tool_version([env["UNITY_BIN"], "-version"])
                if env.get("UNITY_BIN")
                else None
            ),
        },
        "unity_license": {
            "provider": env.get("GB_UNITY_LICENSE_PROVIDER") or None,
            "configured": bool(env.get("GB_UNITY_LICENSE_PROVIDER")),
            "probe_passed": env.get("GB_UNITY_LICENSE_PROBE_PASSED") == "1",
        },
        "home": home or None,
        "home_writable": bool(home and probe.dir_writable(home)),
        "display": env.get("DISPLAY") or None,
        "tools": {
            name: probe.has_tool(name, env.get("PATH")) for name in AGENT_ENV_TOOLS
        },
        "timeout_s": timeout_s,
        "child_timeout_cap_s": child_timeout_cap_s,
        "uname": probe.uname(),
        "image_id": env.get("GB_SANDBOX_IMAGE_ID"),
        #: Where the facts above were measured, so a reader can tell a container
        #: snapshot from a host one instead of assuming the harness's own rootfs.
        "measured_in": probe.location,
        #: The Godot the *evaluator* will score with, always measured on the host.
        #: Under sandbox=docker this differs from ``godot`` above.
        "evaluator_godot": (
            _tool_version([str(EVALUATOR_GODOT), "--version"])
            if EVALUATOR_GODOT.is_file()
            else None
        ),
        "agent_cli": probe.agent_cli(),
        "harness_env": _harness_env_snapshot(env),
    }


def _environment_prompt(snapshot: Mapping[str, Any]) -> str:
    godot = snapshot.get("godot") or {}
    unity = snapshot.get("unity") or {}
    unity_note = ""
    if unity.get("path"):
        license_state = snapshot.get("unity_license") or {}
        license_configured = license_state.get("configured", False)
        license_probed = license_state.get("probe_passed", False)
        unity_note = (
            f"Unity: {unity.get('version') or 'unknown'} at `{unity['path']}`. "
            f"Unity entitlement {'is configured' if license_configured else 'is not configured'} "
            f"and its batchmode probe {'passed' if license_probed else 'has not passed'}. "
            "Run the bounded import/compile command in `ENVIRONMENT.md` before submission. "
        )
    tools = snapshot.get("tools") or {}
    absent = ", ".join(sorted(name for name, present in tools.items() if not present))
    display = snapshot.get("display")
    # Fired by the data, not by the sandbox mode: whenever the Godot the agent
    # develops against is not the one the evaluator scores with, say so.  Under
    # sandbox=docker that is the image's Godot vs the host's; on a formal host it
    # catches a GODOT_BIN that has drifted from the pinned evaluator binary.
    evaluator = snapshot.get("evaluator_godot")
    mismatch = ""
    if evaluator and godot.get("version") and evaluator != godot.get("version"):
        mismatch = (
            f"The evaluator scores your submission with Godot `{evaluator}`, which "
            f"differs from the `{godot.get('version')}` in this environment. Do not "
            f"rely on APIs introduced after `{evaluator}`. "
        )
    return (
        "\n\n## Harness environment (measured)\n\n"
        f"Godot: {godot.get('version') or 'unknown'} at "
        f"`{godot.get('path') or 'not found'}`. "
        f"`HOME` is writable at `{snapshot.get('home')}`. "
        + unity_note
        + (
            f"`DISPLAY` is `{display}`. "
            if display
            else (
                "There is no active display, so direct `--write-movie` is unavailable; "
                f"`xvfb-run` is {'installed' if tools.get('xvfb-run') else 'not installed'}. "
            )
        )
        + (f"Tools not installed: {absent}. " if absent else "All listed helper tools are installed. ")
        + mismatch
        + "These facts were measured by the harness before launch.\n"
    )


def _json_line_objects(text: str) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    for line in text.splitlines():
        try:
            value = json.loads(line)
        except json.JSONDecodeError:
            continue
        if isinstance(value, dict):
            rows.append(value)
    return rows


def _walk_mappings(value: Any):
    if isinstance(value, Mapping):
        yield value
        for nested in value.values():
            yield from _walk_mappings(nested)
    elif isinstance(value, list):
        for nested in value:
            yield from _walk_mappings(nested)


def _normalised_events(backend: str, stdout: str, stderr: str) -> list[dict[str, Any]]:
    events: list[dict[str, Any]] = []
    turn = 0
    for stream, text in (("stdout", stdout), ("stderr", stderr)):
        parsed_lines = set()
        for line_no, line in enumerate(text.splitlines()):
            try:
                payload = json.loads(line)
            except json.JSONDecodeError:
                continue
            if not isinstance(payload, dict):
                continue
            parsed_lines.add(line_no)
            components: list[dict[str, Any]] = []
            item = payload.get("item")
            if isinstance(item, dict):
                components.append(item)
            message = payload.get("message")
            if isinstance(message, dict) and isinstance(message.get("content"), list):
                components.extend(
                    block for block in message["content"] if isinstance(block, dict)
                )
            if not components:
                components = [payload]
            outer_type = str(payload.get("type") or payload.get("event") or "")
            for component in components:
                event_type = str(
                    component.get("type")
                    or component.get("event")
                    or outer_type
                    or "message"
                )
                lowered = event_type.lower()
                if "error" in lowered:
                    kind = "error"
                elif "tool_result" in lowered:
                    kind = "tool_result"
                elif "tool" in lowered or "command" in lowered:
                    kind = (
                        "tool_result"
                        if "completed" in outer_type.lower()
                        else "tool_call"
                    )
                elif "reason" in lowered:
                    kind = "reasoning_summary"
                else:
                    kind = "message"
                tool = (
                    component.get("tool")
                    or component.get("name")
                    or (
                        event_type
                        if "tool" in lowered or "command" in lowered
                        else None
                    )
                )
                command = (
                    component.get("command")
                    or component.get("argv")
                    or component.get("input")
                )
                output = (
                    component.get("stdout")
                    or component.get("aggregated_output")
                    or component.get("content")
                    or component.get("text")
                    or ""
                )
                events.append({
                    "ts": payload.get("ts") or payload.get("timestamp"),
                    "turn": turn,
                    "kind": kind,
                    "tool": tool,
                    "argv": str(command)[:2048] if command is not None else None,
                    "exit_code": component.get("exit_code"),
                    "wall_ms": component.get("wall_ms"),
                    "stdout_head": str(output)[:4096],
                    "stderr_head": str(component.get("stderr") or "")[:4096],
                    "cwd": component.get("cwd") or payload.get("cwd"),
                    "backend": backend,
                    "backend_event_type": f"{outer_type}:{event_type}".strip(":"),
                    "stream": stream,
                })
                turn += 1
        for line_no, line in enumerate(text.splitlines()):
            if line_no in parsed_lines or not line.strip():
                continue
            events.append({
                "ts": None,
                "turn": turn,
                "kind": "error" if stream == "stderr" else "message",
                "tool": None,
                "argv": None,
                "exit_code": None,
                "wall_ms": None,
                "stdout_head": line[:4096] if stream == "stdout" else "",
                "stderr_head": line[:4096] if stream == "stderr" else "",
                "cwd": None,
                "backend": backend,
                "backend_event_type": "text",
                "stream": stream,
            })
            turn += 1
    return events


def _usage_artifact(
    backend: str,
    config: AgentConfig,
    stdout: str,
    stderr: str,
) -> dict[str, Any]:
    rows = _json_line_objects(stdout) + _json_line_objects(stderr)
    usage_rows = [
        nested["usage"]
        for row in rows
        for nested in _walk_mappings(row)
        if isinstance(nested.get("usage"), dict)
    ]
    effort_match = re.search(
        r"reasoning effort:\s*([A-Za-z0-9_-]+)",
        stdout + "\n" + stderr,
        flags=re.I,
    )
    if effort_match:
        effective_effort: str | None = effort_match.group(1).lower()
        effort_source = "backend_output"
    elif backend == "claude":
        effective_effort = config.effort
        effort_source = "command_argument"
    else:
        effective_effort = None
        effort_source = "not_reported"
    return {
        "schema": AGENT_ARTIFACT_SCHEMA,
        "backend": backend,
        "requested_reasoning_effort": config.effort,
        "effective_reasoning_effort": effective_effort,
        "effective_effort_source": effort_source,
        "per_turn": usage_rows,
        "totals": usage_rows[-1] if usage_rows else {},
        "cost": next(
            (
                row.get("total_cost_usd")
                for row in reversed(rows)
                if row.get("total_cost_usd") is not None
            ),
            None,
        ),
    }


def _claude_init_event(stdout: str) -> dict[str, Any] | None:

    for line in stdout.splitlines():
        line = line.strip()
        if not line.startswith("{"):
            continue
        try:
            payload = json.loads(line)
        except json.JSONDecodeError:
            continue
        if payload.get("type") == "system" and payload.get("subtype") == "init":
            return payload
    return None


def _claude_runtime_fingerprint(stdout: str) -> dict[str, Any]:


    init = _claude_init_event(stdout) or {}
    return {
        "init_seen": bool(init),
        "model": init.get("model"),
        "tools": sorted(init.get("tools") or []),
        "skills": sorted(init.get("skills") or []),
        "agents": sorted(init.get("agents") or []),
        "mcp_servers": init.get("mcp_servers") or [],
        "slash_commands": init.get("slash_commands") or [],
        "permission_mode": init.get("permissionMode"),
        "api_key_source": init.get("apiKeySource"),
        "output_style": init.get("output_style"),


        "claude_code_version": init.get("claude_code_version"),


        "context_window": init.get("contextWindow"),
        "session_id": init.get("session_id"),
    }


def _write_agent_artifacts(
    log_dir: Path,
    *,
    backend: str,
    config: AgentConfig,
    stdout: str,
    stderr: str,
) -> None:
    if backend == "claude":
        fingerprint = _claude_runtime_fingerprint(stdout)
        write_json(log_dir / "harness.json", fingerprint)
        environment_path = log_dir / "env.json"
        if environment_path.is_file():
            try:
                environment = json.loads(environment_path.read_text(encoding="utf-8"))
            except (OSError, json.JSONDecodeError):
                environment = None
            if isinstance(environment, dict):


                environment["harness_runtime"] = fingerprint
                write_json(environment_path, environment)
    events = _normalised_events(backend, stdout, stderr)
    (log_dir / "events.jsonl").write_text(
        "".join(json.dumps(row, sort_keys=True) + "\n" for row in events),
        encoding="utf-8",
    )
    write_json(
        log_dir / "usage.json",
        _usage_artifact(backend, config, stdout, stderr),
    )


def _codex_auth_file(config: AgentConfig) -> Path:
    return Path(os.path.expanduser(config.auth_file or DEFAULT_CODEX_AUTH_FILE))


def _host_which(name: str, env: Mapping[str, str]) -> str | None:
    return shutil.which(name, path=env.get("PATH"))


def _codex_command(
    workspace: Path,
    config: AgentConfig,
    env: dict[str, str],
    which: Callable[[str, Mapping[str, str]], str | None] = _host_which,
) -> list[str]:
    kind = _codex_provider_kind(config)
    codex = which("codex", env)
    if not codex:
        raise AgentProcessError(
            "agent_transport_error", 127, "Codex CLI executable was not found on PATH"
        )
    model = config.model.strip() or DEFAULT_CODEX_MODEL
    head = [
        codex,
        "exec",
        "--ephemeral",
        "--ignore-user-config",
        "--ignore-rules",
        "--skip-git-repo-check",
        "-C",
        str(workspace),
        "--sandbox",


        "danger-full-access" if config.sandbox == "docker" else "workspace-write",
        "--json",
        "-c",
        'approval_policy="never"',
        "-c",
        f"model_reasoning_effort={_toml_string(config.effort)}",
        "--model",
        model,
    ]
    if kind == "openai":


        if not env.get("OPENAI_API_KEY") and not _codex_auth_file(config).is_file():
            raise AgentProcessError(
                "agent_transport_error",
                78,
                "Codex openai provider credential is unavailable: OPENAI_API_KEY is not set "
                f"and {_codex_auth_file(config)} does not exist (run `codex login` or point "
                "--agent-auth-file at an auth.json)",
            )
        return [
            *head,
            "-c",
            'model_provider="openai"',
            "-c",
            'shell_environment_policy.inherit="core"',
            "-c",
            "shell_environment_policy.ignore_default_excludes=false",
            "-c",
            'shell_environment_policy.filters={"OPENAI_API_KEY"="exclude"}',
            "-",
        ]
    key_env = config.key_env.strip()
    if not key_env:
        raise AgentProcessError(
            "agent_transport_error", 78, "Codex provider key environment name is empty"
        )
    if not env.get(key_env):
        raise AgentProcessError(
            "agent_transport_error",
            78,
            f"Codex provider credential is unavailable: environment variable {key_env} is not set",
        )
    base_url = config.base_url.strip()
    if not base_url:
        raise AgentProcessError(
            "agent_transport_error", 78, "Codex provider base URL is empty"
        )
    return [
        *head,
        "-c",
        f'model_provider={_toml_string(CODEX_PROVIDER_ID)}',
        "-c",
        f'model_providers.{CODEX_PROVIDER_ID}.name={_toml_string("GameBenchmark proxy")}',
        "-c",
        f'model_providers.{CODEX_PROVIDER_ID}.base_url={_toml_string(base_url)}',
        "-c",
        f'model_providers.{CODEX_PROVIDER_ID}.env_key={_toml_string(key_env)}',
        "-c",
        f'model_providers.{CODEX_PROVIDER_ID}.wire_api="responses"',
        "-c",
        'shell_environment_policy.inherit="core"',
        "-c",
        "shell_environment_policy.ignore_default_excludes=false",
        "-c",
        (
            "shell_environment_policy.filters={"
            f"{_toml_string(key_env)}=\"exclude\""
            "}"
        ),
        "-",
    ]


CLAUDE_EXPECTED_TOOLS = frozenset({"Bash", "Edit", "Read"})


CLAUDE_VERSION_ENV = "GB_CLAUDE_CODE_VERSION"


UNFROZEN_HARNESS_ENV = "GB_ALLOW_UNFROZEN_CLAUDE_HARNESS"


RESUME_ACROSS_COMMITS_ENV = "GB_RESUME_ACROSS_COMMITS"


REUSE_AGENT_WORKSPACE_ENV = "GB_REUSE_AGENT_WORKSPACE"


REUSED_WORKSPACE_MARKER = "workspace_reused_from_failed_agent"


CLAUDE_BOOTSTRAP_CONFIG_ENV = "GB_CLAUDE_BOOTSTRAP_CONFIG"


CLAUDE_OAUTH_BARE_ENV = "GB_CLAUDE_OAUTH_BARE"


CLAUDE_BOOTSTRAP_AUTH_ENV = "GB_CLAUDE_BOOTSTRAP_AUTH"


CLAUDE_PERSISTENT_HOME_ENV = "GB_CLAUDE_PERSISTENT_HOME"


def _harness_is_frozen(env: Mapping[str, str]) -> bool:
    return str(env.get(UNFROZEN_HARNESS_ENV, "")).strip() not in {"1", "true", "TRUE"}


def _assert_claude_harness(
    fingerprint: Mapping[str, Any], env: Mapping[str, str] | None = None
) -> None:


    if not fingerprint.get("init_seen"):
        raise AgentProcessError(
            "agent_harness_error",
            79,
            "Claude Code emitted no system/init event, so this cell's runtime "
            "tool surface cannot be verified",
        )
    actual = frozenset(fingerprint.get("tools") or ())
    if actual != CLAUDE_EXPECTED_TOOLS:
        missing = sorted(CLAUDE_EXPECTED_TOOLS - actual)
        unexpected = sorted(actual - CLAUDE_EXPECTED_TOOLS)
        raise AgentProcessError(
            "agent_harness_error",
            79,
            "Claude Code tool surface does not match the frozen harness "
            f"(expected {sorted(CLAUDE_EXPECTED_TOOLS)}): "
            f"missing={missing} unexpected={unexpected}",
        )
    expected_version = str((env or {}).get(CLAUDE_VERSION_ENV, "")).strip()
    actual_version = str(fingerprint.get("claude_code_version") or "")
    if expected_version and actual_version and actual_version != expected_version:
        raise AgentProcessError(
            "agent_harness_error",
            79,
            f"Claude Code version does not match the pinned build: ran "
            f"{actual_version}, {CLAUDE_VERSION_ENV} names {expected_version}",
        )


def _claude_command(
    workspace: Path,
    config: AgentConfig,
    env: dict[str, str],
    which: Callable[[str, Mapping[str, str]], str | None] = _host_which,
) -> list[str]:
    provider = _agent_provider(config) or {}
    key_env = str(provider.get("key_env") or "")
    auth_mode = env.get("GB_CLAUDE_AUTH_MODE", "").strip().lower()
    oauth_token = env.get("CLAUDE_CODE_OAUTH_TOKEN", "")
    use_oauth = auth_mode == "oauth"
    use_file_auth = auth_mode == "file"
    if use_oauth and not oauth_token:
        raise AgentProcessError(
            "agent_transport_error",
            78,
            "Claude OAuth credential is unavailable: environment variable CLAUDE_CODE_OAUTH_TOKEN is not set",
        )
    if use_file_auth and not Path(config.auth_file).expanduser().is_file():
        raise AgentProcessError(
            "agent_transport_error",
            78,
            f"Claude credential file is unavailable: {config.auth_file}",
        )
    if not use_oauth and not use_file_auth and (not key_env or not env.get(key_env)):
        raise AgentProcessError(
            "agent_transport_error",
            78,
            f"Claude provider credential is unavailable: environment variable {key_env or '<empty>'} is not set",
        )
    claude_override = str(env.get("GB_CLAUDE_BIN") or "").strip()
    claude = claude_override or which("claude", env)
    if not claude:
        raise AgentProcessError(
            "agent_transport_error", 127, "Claude Code executable was not found on PATH"
        )
    if use_oauth or use_file_auth:


        env.pop("ANTHROPIC_API_KEY", None)
        env.pop("ANTHROPIC_AUTH_TOKEN", None)
        env.pop("ANTHROPIC_BASE_URL", None)
        if use_file_auth:
            env.pop("CLAUDE_CODE_OAUTH_TOKEN", None)
    elif key_env == "ANTHROPIC_AUTH_TOKEN":


        env["ANTHROPIC_AUTH_TOKEN"] = env[key_env]
        if not env.get("ANTHROPIC_API_KEY"):
            env.pop("ANTHROPIC_API_KEY", None)
    else:
        env["ANTHROPIC_API_KEY"] = env[key_env]
    base_url = str(provider.get("base_url") or "")
    if base_url and not (use_oauth or use_file_auth):
        env["ANTHROPIC_BASE_URL"] = base_url
    elif not (use_oauth or use_file_auth):
        env.pop("ANTHROPIC_BASE_URL", None)
    model_id = str(provider.get("model") or DEFAULT_CLAUDE_MODEL)


    env["CLAUDE_CODE_SIMPLE_SYSTEM_PROMPT"] = "0"


    for alias in (
        "ANTHROPIC_MODEL",
        "ANTHROPIC_DEFAULT_OPUS_MODEL",
        "ANTHROPIC_DEFAULT_SONNET_MODEL",
        "ANTHROPIC_DEFAULT_HAIKU_MODEL",
        "CLAUDE_CODE_SUBAGENT_MODEL",
    ):
        env[alias] = model_id
    frozen = _harness_is_frozen(env)
    oauth_bare = str(env.get(CLAUDE_OAUTH_BARE_ENV, "")).strip() == "1"
    if use_oauth and frozen and not oauth_bare:


        raise AgentProcessError(
            "agent_harness_error",
            79,
            "Claude OAuth routing cannot produce the frozen tool surface: "
            "--bare is required for a reproducible surface and Claude Code "
            "2.1.x ignores its OAuth credential under --bare.  Run benchmark "
            f"cells through ANTHROPIC_API_KEY, or set {UNFROZEN_HARNESS_ENV}=1 "
            "for a harness self-test whose cells are not comparable with "
            "frozen ones.",
        )
    command = [
        claude,
        "--print",
        "--model",
        model_id,
        "--effort",
        config.effort,
        "--permission-mode",
        "acceptEdits",


        "--allowedTools",
        ",".join(sorted(CLAUDE_EXPECTED_TOOLS)),
        "--no-session-persistence",
        "--verbose",
        "--output-format",
        "stream-json",
    ]
    if frozen or oauth_bare:

        command.insert(2, "--bare")
    if use_oauth and oauth_bare and str(env.get(CLAUDE_PERSISTENT_HOME_ENV, "")).strip():


        env.pop("CLAUDE_CODE_OAUTH_TOKEN", None)
        env.pop("GB_CLAUDE_AUTH_MODE", None)
    return command


def _sandbox_deviations(
    config: AgentConfig, environment: Mapping[str, Any]
) -> list[str]:


    if config.sandbox != "docker":
        return []
    notes = [
        "agent ran in the docker sandbox image, not the pinned host toolchain; "
        "not formally eligible"
    ]
    godot = (environment.get("godot") or {}).get("version")
    evaluator = environment.get("evaluator_godot")
    if godot and evaluator and godot != evaluator:
        notes.append(f"agent Godot {godot} vs evaluator Godot {evaluator}")
    elif godot and not evaluator:
        notes.append(f"agent Godot {godot}; evaluator Godot not measurable on this host")
    for name, version in (environment.get("agent_cli") or {}).items():
        if version:
            notes.append(f"{name} CLI in image: {version}")
    return notes


def _docker_probe(sandbox: DockerSandbox) -> EnvironmentProbe:

    facts = sandbox.facts
    assert facts is not None
    return EnvironmentProbe(
        location=f"container:{sandbox.name}",


        tool_version=lambda argv: facts.versions.get(
            next((n for n, p in facts.paths.items() if p == argv[0]), ""), None
        ),


        has_tool=lambda name, _path: (
            facts.tools[name] if name in facts.tools else bool(sandbox.which(name))
        ),
        dir_writable=lambda path: (
            facts.home_writable if path == CONTAINER_HOME else bool(sandbox.which("test"))
        ),
        uname=lambda: facts.uname,
        agent_cli=lambda: {
            "codex": facts.versions.get("codex"),
            "claude": facts.versions.get("claude"),
        },
    )


def _pinned_evaluator_godot() -> Path | None:


    want = os.environ.get("GB_GODOT_SHA256") or PINNED_GODOT_SHA256
    if not (want and EVALUATOR_GODOT.is_file()):
        return None
    resolved = EVALUATOR_GODOT.resolve()
    digest = hashlib.sha256()
    try:
        with resolved.open("rb") as handle:
            for chunk in iter(lambda: handle.read(1 << 20), b""):
                digest.update(chunk)
    except OSError:
        return None
    return resolved if digest.hexdigest() == want else None


DOCKER_UNITY_IMAGE = "gamebench-agent:unity-6000.3.23f1"


def _docker_image_for_mode(config: AgentConfig, mode: str | None) -> str:


    explicit = config.docker_image.strip()
    if explicit:
        return explicit
    return DOCKER_UNITY_IMAGE if mode == "port" else DEFAULT_SANDBOX_IMAGE


def _start_docker_sandbox(
    workspace: Path, log_dir: Path, config: AgentConfig, *, mode: str | None = None
) -> DockerSandbox:
    """Bring up the container and load the workspace into it."""
    if config.backend == "codex" and _codex_provider_kind(config) == "openai":
        # The openai provider carries ~/.codex/auth.json into the sandbox.
        # Copying a personal credential into a container on a shared daemon is
        # not something to do implicitly, and silently ignoring auth_file would
        # surface later as an opaque 401.
        raise MatrixError(
            "--agent-sandbox docker supports only --agent-provider custom; the "
            "openai provider needs ~/.codex/auth.json inside the sandbox, which "
            "the docker driver does not implement"
        )
    provider = os.environ.get("GB_UNITY_LICENSE_PROVIDER", "").strip() if mode == "port" else ""
    license_file = os.environ.get("GB_UNITY_LICENSE_FILE", "").strip() if mode == "port" else ""
    config_root = os.environ.get("GB_UNITY_CONFIG_ROOT", "").strip() if mode == "port" else ""
    endpoint = os.environ.get("GB_UNITY_FLOATING_ENDPOINT", "").strip() if mode == "port" else ""
    provider = provider or ("file" if license_file else "existing-home" if config_root
                            else "floating" if endpoint else "")
    sandbox = DockerSandbox(
        image=_docker_image_for_mode(config, mode),
        log_path=log_dir / "sandbox.log",
        cell=log_dir.parent.name,
        host_machine_identity=mode == "port" and provider == "existing-home",
    )
    sandbox.unity_workspace = mode == "port"
    try:
        sandbox.start()
        sandbox.copy_in(workspace)
        probe_request = {
            "tools": AGENT_ENV_TOOLS,
            "versioned": {
                "godot": ["--version"],
                **({"unity": ["-version"]} if mode == "port" else {}),
                "codex": ["--version"],
                "claude": ["--version"],
            },
        }
        facts = sandbox.probe_facts(**probe_request)
        if mode == "port":
            unity = facts.path_of("unity")
            version = facts.versions.get("unity") or ""
            if not unity:
                raise DockerSandboxError(
                    "Mode 5 agent image does not provide the `unity` executable"
                )
            if "6000.3.23f1" not in version:
                raise DockerSandboxError(
                    "Mode 5 agent image Unity version mismatch: expected "
                    f"6000.3.23f1, observed {version or 'no version output'}"
                )
            if not provider:
                raise AgentProcessError(
                    "agent_environment_invalid", 78,
                    "Mode 5 requires a configured Unity license provider before the Agent starts",
                )
            try:
                sandbox.configure_unity_license(
                    provider=provider,
                    source=Path(license_file or config_root) if (license_file or config_root) else None,
                    endpoint=endpoint,
                )
            except DockerSandboxError as exc:
                raise AgentProcessError("agent_environment_invalid", 78, str(exc)) from exc
        # The sandbox image is expected to already carry the evaluator's pinned
        # Godot.  When it does not -- an older image, or an override -- fall back
        # to injecting the host's copy, so the agent never develops against a
        # different engine from the one that scores it.  A version-only mismatch
        # would otherwise read as a broken submission.
        installed = facts.versions.get("godot") or ""
        if not installed.startswith(PINNED_GODOT_VERSION):
            pinned = _pinned_evaluator_godot()
            if pinned is not None:
                sandbox.install_godot(pinned, want_prefix=PINNED_GODOT_VERSION)
                sandbox.probe_facts(**probe_request)
    except DockerSandboxError as exc:
        sandbox.close()
        raise AgentProcessError("agent_transport_error", 125, str(exc)) from exc
    return sandbox


def run_coding_agent(
    workspace: Path, log_dir: Path, config: AgentConfig, *, mode: str | None = None
) -> int:
    """Run the coding agent, optionally inside a sandbox container.

    The sandbox is owned here rather than inside the body so that every exit
    path -- clean return, typed AgentProcessError, wall-clock timeout, an
    unexpected exception, KeyboardInterrupt -- tears the container down.
    """
    if config.sandbox not in AGENT_SANDBOXES:
        # Fail before anything is created or written.
        raise MatrixError(f"unknown agent sandbox {config.sandbox!r}")
    sandbox: DockerSandbox | None = None
    if config.sandbox == "docker" and not config.dry_run:
        log_dir.mkdir(parents=True, exist_ok=True)
        sandbox = _start_docker_sandbox(workspace, log_dir, config, mode=mode)
    agent_exit: int | None = None
    try:
        agent_exit = _run_coding_agent(
            workspace, log_dir, config, sandbox, mode=mode,
        )
        return agent_exit
    finally:
        if sandbox is not None:
            copy_failure = sandbox.close(copy_out_to=workspace)
            if sandbox.transfer:
                write_json(log_dir / "transfer.json", sandbox.transfer)
            if copy_failure is not None and agent_exit == 0:
                # The agent finished cleanly, so this transport failure is the
                # only thing that went wrong.  Reporting it as such matters:
                # staying silent would let collect_submission raise "agent
                # returned no project.godot" and blame the model for a broken
                # copy.  When the agent had already failed, the original error
                # is the real cause and is left to propagate.
                raise AgentProcessError(
                    "agent_transport_error",
                    125,
                    f"docker sandbox could not copy {CONTAINER_WORKSPACE} back to "
                    f"{workspace}: {copy_failure}",
                )


def _pump_stream(stream, sink, secrets, collected: list[str]) -> None:


    try:
        for line in stream:
            text = _redact(line, secrets)
            collected.append(text)
            sink.write(text)
            sink.flush()
    finally:
        try:
            stream.close()
        except Exception:
            pass


def _run_agent_streamed(command: list[str], *, cwd, env, input: str | None,
                        timeout: float | None, log_dir, secrets):
    """Run the agent, writing its transcript to disk as it is produced.

    Raises ``subprocess.TimeoutExpired`` carrying whatever was captured, so the
    callers' existing timeout handling keeps working unchanged.
    """

    if subprocess.run is not _REAL_SUBPROCESS_RUN:
        result = subprocess.run(
            command, cwd=cwd, env=env, input=input, timeout=timeout,
            capture_output=True, text=True, check=False,
        )
        stdout = _redact(result.stdout or "", secrets)
        stderr = _redact(result.stderr or "", secrets)
        (log_dir / "stdout.log").write_text(stdout, encoding="utf-8")
        (log_dir / "stderr.log").write_text(stderr, encoding="utf-8")
        return result.returncode, stdout, stderr
    out_parts: list[str] = []
    err_parts: list[str] = []
    with (
        open(log_dir / "stdout.log", "w", encoding="utf-8") as out_sink,
        open(log_dir / "stderr.log", "w", encoding="utf-8") as err_sink,
    ):
        proc = subprocess.Popen(
            command, cwd=cwd, env=env,
            stdin=subprocess.PIPE if input is not None else subprocess.DEVNULL,
            stdout=subprocess.PIPE, stderr=subprocess.PIPE,
            text=True, bufsize=1,
        )
        pumps = [
            threading.Thread(target=_pump_stream, args=(proc.stdout, out_sink, secrets, out_parts), daemon=True),
            threading.Thread(target=_pump_stream, args=(proc.stderr, err_sink, secrets, err_parts), daemon=True),
        ]
        for pump in pumps:
            pump.start()
        if input is not None and proc.stdin is not None:
            try:
                proc.stdin.write(input)
                proc.stdin.close()
            except (BrokenPipeError, OSError):
                pass
        try:
            proc.wait(timeout=timeout)
        except subprocess.TimeoutExpired:
            proc.kill()
            proc.wait()
            for pump in pumps:
                pump.join(timeout=10)
            raise subprocess.TimeoutExpired(
                command, timeout, output="".join(out_parts), stderr="".join(err_parts),
            ) from None
        except BaseException:
            # Operator cancellation must not close transcript sinks while the
            # pump threads are still writing, or leave the local CLI running.
            proc.kill()
            proc.wait()
            for pump in pumps:
                pump.join(timeout=10)
            raise
        for pump in pumps:
            pump.join(timeout=30)
    return proc.returncode, "".join(out_parts), "".join(err_parts)


def _unity_selfcheck_snapshot(sandbox: DockerSandbox) -> dict[str, str]:
    expected = ("scaffold_import", "script_compile", "linux_player_build", "player_smoke")
    result = {name: "not_run" for name in expected}
    try:
        text = sandbox.read_text(f"{CONTAINER_WORKSPACE}/.selfcheck/status.json")
        raw = json.loads(text) if text else {}
    except (AttributeError, TypeError, ValueError):
        raw = {}
    if isinstance(raw, Mapping) and raw.get("schema") == "gamebench.mode5-agent-selfcheck.v1":
        for name in expected:
            value = str(raw.get(name) or "not_run")
            result[name] = value if value in {"pass", "fail", "not_run"} else "invalid"
    return result


def _agent_process_environment() -> dict[str, str]:
    env = dict(os.environ)
    # These coordinator-only values may contain private host paths or the
    # unredacted floating endpoint. The sandbox setup consumes them before the
    # Agent starts. A floating Agent receives only Unity's required
    # UNITY_LICENSE_SERVER variable later.
    for private_name in (
        "GB_UNITY_LICENSE_FILE", "GB_UNITY_CONFIG_ROOT",
        "GB_UNITY_FLOATING_ENDPOINT",
    ):
        env.pop(private_name, None)
    return env


def _run_coding_agent(
    workspace: Path,
    log_dir: Path,
    config: AgentConfig,
    sandbox: DockerSandbox | None = None,
    *,
    mode: str | None = None,
) -> int:
    log_dir.mkdir(parents=True, exist_ok=True)
    prompt = workspace / "PROMPT.md"
    if not prompt.is_file():
        raise MatrixError(f"workspace lacks PROMPT.md: {workspace}")
    env = _agent_process_environment()
    env_values = _load_env_file(config.env_file)
    for key, value in env_values.items():
        env.setdefault(key, value)
    if EVALUATOR_GODOT.is_file():
        env["GODOT_BIN"] = str(EVALUATOR_GODOT)
        env["PATH"] = (
            f"{EVALUATOR_GODOT.parent}{os.pathsep}{env.get('PATH', '')}"
        )
    sandbox_target = Path(CONTAINER_WORKSPACE)
    if config.sandbox == "unshare":
        sandbox_target.mkdir(parents=True, exist_ok=True)
    visible_workspace = (
        sandbox_target if config.sandbox in {"unshare", "docker"} else workspace
    )
    visible_prompt = visible_workspace / "PROMPT.md"
    env["GB_TASK_WORKSPACE"] = str(visible_workspace)
    env["GB_TASK_PROMPT"] = str(visible_prompt)
    env["GB_TASK_SUBMISSION"] = str(visible_workspace / "submission")
    env["LLM_MODEL"] = config.model.strip() or DEFAULT_CODEX_MODEL
    probe: EnvironmentProbe | None = None
    which = _host_which
    if sandbox is not None:
        # The host assignments above name host paths that do not exist in the
        # container: undo them from the measured container facts.
        facts = sandbox.facts
        assert facts is not None
        godot = facts.path_of("godot")
        if godot:
            env["GODOT_BIN"] = godot
        else:
            env.pop("GODOT_BIN", None)
        unity = facts.path_of("unity")
        if unity:
            env["UNITY_BIN"] = unity
        else:
            env.pop("UNITY_BIN", None)
        if mode == "port":
            env["GB_UNITY_LICENSE_PROVIDER"] = str(
                sandbox.unity_license.get("provider") or ""
            )
            env["GB_UNITY_LICENSE_PROBE_PASSED"] = (
                "1" if sandbox.unity_license.get("probe_passed") else "0"
            )
            if sandbox.unity_license.get("provider") == "floating":
                env["UNITY_LICENSE_SERVER"] = os.environ.get(
                    "GB_UNITY_FLOATING_ENDPOINT", ""
                )
        env["PATH"] = facts.login_path
        env["GB_SANDBOX_IMAGE_ID"] = sandbox.image_id or ""
        probe = _docker_probe(sandbox)
        which = sandbox.which
    prompt_text = prompt.read_text(encoding="utf-8")

    backend = config.backend.strip().lower()
    child_timeout_cap_s: int | None = None
    prompt_input: str | None = None
    codex_home: Path | None = None
    claude_home: Path | None = None
    claude_home_is_temporary = False
    if backend == "command":
        if not config.command.strip():
            raise MatrixError("--agent-command is required for backend=command")
        replacements = {
            "workspace": str(visible_workspace),
            "prompt": str(visible_prompt),
            "submission": str(visible_workspace / "submission"),
        }
        command = [part.format(**replacements) for part in shlex.split(config.command)]
    elif backend == "codex":
        command = _codex_command(visible_workspace, config, env, which)
        # The provider credential remains available to Codex itself, while the
        # shell_environment_policy above removes it from model-spawned commands.
        # Each attempt gets its own authentication/cache root.  In the unshare
        # runner the private tmpfs hides the host directory; in the debug
        # sandbox=none path the finally block below removes it; under docker the
        # home lives in the container and dies with it.
        carry_auth = (
            _codex_provider_kind(config) == "openai"
            and not env.get("OPENAI_API_KEY")
        )
        if sandbox is not None:
            env["CODEX_HOME"] = f"{CONTAINER_HOME}/codex"
            env["HOME"] = CONTAINER_HOME
        elif carry_auth:
            # The sandbox overmounts /tmp; a home that must carry auth.json
            # into the sandbox has to live somewhere the sandbox can see.
            home_root = Path(AGENT_HOME_ROOT_VISIBLE)
            home_root.mkdir(parents=True, exist_ok=True)
            codex_home = Path(
                tempfile.mkdtemp(prefix="gamebench-codex-home-", dir=str(home_root))
            )
            shutil.copyfile(_codex_auth_file(config), codex_home / "auth.json")
            (codex_home / "auth.json").chmod(0o600)
        else:
            codex_home = Path(
                tempfile.mkdtemp(prefix="gamebench-codex-home-", dir="/tmp")
            )
        if codex_home is not None:
            env["CODEX_HOME"] = str(codex_home)
            env["HOME"] = str(codex_home)
        child_timeout_cap_s = _child_timeout_cap(config.timeout_s, mode=mode)
    elif backend == "claude":
        command = _claude_command(visible_workspace, config, env, which)
        if sandbox is not None:
            env["CLAUDE_CONFIG_DIR"] = f"{CONTAINER_HOME}/claude"
            env["HOME"] = CONTAINER_HOME
        else:
            bootstrap_config = str(env.get(CLAUDE_BOOTSTRAP_CONFIG_ENV, "")).strip()
            bootstrap_auth = str(env.get(CLAUDE_BOOTSTRAP_AUTH_ENV, "")).strip() == "1"
            persistent_home = str(env.get(CLAUDE_PERSISTENT_HOME_ENV, "")).strip()
            if persistent_home:
                claude_home = Path(persistent_home).expanduser()
                if not claude_home.is_dir():
                    raise AgentProcessError(
                        "agent_transport_error", 78,
                        f"Claude persistent home does not exist: {claude_home}",
                    )
            else:
                claude_home_root = AGENT_HOME_ROOT_VISIBLE if (bootstrap_config or bootstrap_auth) else "/tmp"
                Path(claude_home_root).mkdir(parents=True, exist_ok=True)
                claude_home = Path(
                    tempfile.mkdtemp(prefix="gamebench-claude-home-", dir=claude_home_root)
                )
                claude_home_is_temporary = True
            if bootstrap_config:
                bootstrap_path = Path(bootstrap_config).expanduser()
                if not bootstrap_path.is_file():
                    raise AgentProcessError(
                        "agent_transport_error",
                        78,
                        f"Claude bootstrap config does not exist: {bootstrap_path}",
                    )
                shutil.copyfile(bootstrap_path, claude_home / ".claude.json")
                (claude_home / ".claude.json").chmod(0o600)
            if bootstrap_auth:
                auth_path = Path(config.auth_file).expanduser()
                if not auth_path.is_file():
                    raise AgentProcessError(
                        "agent_transport_error",
                        78,
                        f"Claude auth file does not exist: {auth_path}",
                    )
                shutil.copyfile(auth_path, claude_home / ".credentials.json")
                (claude_home / ".credentials.json").chmod(0o600)
            env["CLAUDE_CONFIG_DIR"] = str(claude_home)
            env["HOME"] = str(claude_home)
        child_timeout_cap_s = _child_timeout_cap(config.timeout_s, mode=mode)
    else:
        raise MatrixError(f"unknown agent backend {config.backend!r}")

    if sandbox is not None:
        # Prepared before env.json is written so the recorded snapshot is exactly
        # what the container receives -- including the shim on PATH -- rather
        # than a host environment that merely resembles it.
        try:
            if child_timeout_cap_s is not None:
                shim_dir, real_timeout = sandbox.install_child_timeout_shim(
                    source=CHILD_TIMEOUT_SHIM_PY, cap_s=child_timeout_cap_s
                )
                env["GB_REAL_TIMEOUT"] = real_timeout
                env["GB_CHILD_TIMEOUT_CAP_S"] = str(child_timeout_cap_s)
                env["PATH"] = f"{shim_dir}{os.pathsep}{env.get('PATH', '')}"
        except DockerSandboxError as exc:
            raise AgentProcessError("agent_transport_error", 125, str(exc)) from exc
        # An allowlist, not a filter: host-only values (proxies, DISPLAY, host
        # PATH entries) must not leak into the container, which reaches the model
        # gateway directly.  `env` is rebound so env.json, the redaction set and
        # subprocess all agree on one dict.
        env = {
            key: str(value)
            for key, value in env.items()
            if key in CONTAINER_ENV_KEYS
            or key in env_values
            or key.startswith(("ANTHROPIC_", "OPENAI_"))
        }

    environment = _agent_environment(
        env,
        timeout_s=config.timeout_s,
        child_timeout_cap_s=child_timeout_cap_s,
        probe=probe,
    )
    write_json(log_dir / "env.json", environment)
    if backend in {"codex", "claude"}:
        prompt_input = (
            prompt_text
            + _deadline_prompt(config.timeout_s, mode=mode)
            + _environment_prompt(environment)
        )
    (log_dir / "prompt.md").write_text(
        prompt_input if prompt_input is not None else prompt_text,
        encoding="utf-8",
    )

    logged_command = list(command)

    if config.sandbox == "unshare":
        if child_timeout_cap_s is not None:
            real_timeout = shutil.which("timeout", path=env.get("PATH"))
            if not real_timeout:
                raise AgentProcessError(
                    "agent_transport_error",
                    127,
                    "formal coding-agent sandbox requires GNU timeout on PATH",
                )
            env["GB_REAL_TIMEOUT"] = real_timeout
            env["GB_CHILD_TIMEOUT_CAP_S"] = str(child_timeout_cap_s)
        wrapper = """set -eu
source_workspace=$1
shift 1
mount --bind "$source_workspace" /workspace
mount -t tmpfs -o mode=755 tmpfs /data2
mount -t tmpfs -o mode=1777 tmpfs /tmp
if [ -n "${GB_CHILD_TIMEOUT_CAP_S:-}" ]; then
    mkdir -p /tmp/gamebench-agent-bin
    cat > /tmp/gamebench-agent-bin/timeout <<'PY'
""" + CHILD_TIMEOUT_SHIM_PY + """PY
    chmod 755 /tmp/gamebench-agent-bin/timeout
    PATH="/tmp/gamebench-agent-bin:$PATH"
    export PATH
fi
for private_home in "${CODEX_HOME:-}" "${CLAUDE_CONFIG_DIR:-}"; do
    if [ -n "$private_home" ]; then
        mkdir -p "$private_home"
    fi
done
cd /workspace
exec "$@"
"""
        command = [
            "unshare", "--user", "--map-root-user", "--mount", "--pid", "--fork",
            "--mount-proc", "sh", "-c", wrapper, "taskgen-sandbox",
            str(workspace), *command,
        ]
    elif config.sandbox == "docker" and sandbox is not None:
        try:
            command = sandbox.exec_argv(
                command,
                interactive=prompt_input is not None,
                env_file=sandbox.write_env_file(env),
            )
        except DockerSandboxError as exc:
            raise AgentProcessError("agent_transport_error", 125, str(exc)) from exc
    elif config.sandbox not in AGENT_SANDBOXES:
        # Defence in depth: run_coding_agent already rejects unknown modes before
        # anything is created.  Reached with sandbox="docker" only on a dry run,
        # where there is no container and the inner argv is what should be shown.
        raise MatrixError(f"unknown agent sandbox {config.sandbox!r}")

    secret_values = {
        value for key, value in env_values.items()
        if value and any(token in key.upper() for token in ("KEY", "SECRET", "TOKEN"))
    }
    provider = _agent_provider(config) or {}
    provider_key_env = str(provider.get("key_env") or "")
    if backend in {"codex", "claude"} and env.get(provider_key_env):
        secret_values.add(env[provider_key_env])
    safe_logged_command = [_redact(part, secret_values) for part in logged_command]
    write_json(
        log_dir / "request.json",
        {
            "backend": backend,
            "sandbox": config.sandbox,
            "command": safe_logged_command,
            "prompt": "stdin" if backend in {"codex", "claude"} else str(visible_prompt),
            "provider": provider,
            "requested_reasoning_effort": config.effort,
            "timeout_s": config.timeout_s,
            "deadline_contract": AGENT_DEADLINE_CONTRACT_VERSION,
            "child_timeout_cap_s": child_timeout_cap_s,
            "artifacts": {
                "events": "events.jsonl",
                "prompt": "prompt.md",
                "usage": "usage.json",
                "environment": "env.json",
            },
            "cwd": str(workspace),
            "dry_run": bool(config.dry_run),
            #: Sandbox provenance.  Recorded here rather than on AgentConfig or
            #: the frozen run.json agent block, because those are compared key by
            #: key on --resume and a new key would invalidate every in-flight run.
            "sandbox_image": (
                sandbox.image
                if sandbox is not None
                else (config.docker_image.strip() or DEFAULT_SANDBOX_IMAGE)
                if config.sandbox == "docker"
                else None
            ),
            "sandbox_image_id": sandbox.image_id if sandbox is not None else None,
            "sandbox_container": sandbox.name if sandbox is not None else None,
            "deviations": _sandbox_deviations(config, environment),
        },
    )
    try:
        if config.dry_run:
            return AGENT_DRY_RUN_EXIT
        kit = workspace / "playtest"
        if kit.is_dir():
            _write_created_at(kit / PLAYTEST_KIT_CREATED_AT, budget_s=config.timeout_s)
        returncode, out_text, err_text = _run_agent_streamed(
            command,
            cwd=workspace,
            # The docker client needs the *host* environment: `env` now names
            # container paths for HOME and PATH, which would make the CLI look
            # for a nonexistent host config dir and could hide `docker` itself.
            env=sandbox.client_env if sandbox is not None else env,
            input=prompt_input,
            timeout=config.timeout_s,
            log_dir=log_dir,
            secrets=secret_values,
        )
    except subprocess.TimeoutExpired as exc:
        stdout = _redact(exc.stdout, secret_values)
        stderr = (
            _redact(exc.stderr, secret_values)
            + f"\nagent timeout after {config.timeout_s}s\n"
        )
        (log_dir / "stdout.log").write_text(stdout, encoding="utf-8")
        (log_dir / "stderr.log").write_text(stderr, encoding="utf-8")
        _write_agent_artifacts(
            log_dir,
            backend=backend,
            config=config,
            stdout=stdout,
            stderr=stderr,
        )
        raise AgentProcessError(
            "agent_timeout", 124, f"coding agent timed out after {config.timeout_s}s"
        )
    finally:
        if mode == "port" and sandbox is not None:
            environment["unity_selfcheck"] = _unity_selfcheck_snapshot(sandbox)
            write_json(log_dir / "env.json", environment)
        if codex_home is not None:
            shutil.rmtree(codex_home, ignore_errors=True)
        if claude_home is not None and claude_home_is_temporary:
            shutil.rmtree(claude_home, ignore_errors=True)
    # stdout.log / stderr.log were written line by line while the agent ran.
    _write_agent_artifacts(
        log_dir,
        backend=backend,
        config=config,
        stdout=out_text,
        stderr=err_text,
    )
    if returncode != 0:
        raise AgentProcessError(
            "agent_transport_error" if backend in {"codex", "claude"} else "agent_execution_error",
            int(returncode),
            f"coding agent exited with status {returncode}",
        )
    if backend == "claude" and not config.dry_run and _harness_is_frozen(env):
        # A clean exit under the wrong harness is the failure this catches:
        # it produces a submission that looks scoreable but answers a
        # different question than its neighbours.
        _assert_claude_harness(_claude_runtime_fingerprint(out_text), env)
    return 0


def _unity_project_ready(path: Path) -> bool:
    return (
        (path / "Assets").is_dir()
        and (path / "Packages").is_dir()
        and (path / "ProjectSettings" / "ProjectVersion.txt").is_file()
    )


def _submission_ready(path: Path, mode: Mode | str) -> bool:
    resolved = mode if isinstance(mode, Mode) else parse_mode(mode)
    if resolved.id == PORT.id:
        return _unity_project_ready(path)
    return (path / "game" / "project.godot").is_file()


ROOT_RESCUED_INTERFACE_FILES = ("gb_levels.json",)


def collect_submission(
    workspace: Path,
    out: Path,
    *,
    mode: Mode | str = "brief",
) -> Path:
    resolved = mode if isinstance(mode, Mode) else parse_mode(mode)
    candidate = workspace / "submission"
    source_root = candidate if candidate.is_dir() else workspace
    if resolved.id == PORT.id:
        try:
            reject_links(source_root, label="Mode 5 workspace")
        except ValueError as exc:
            raise MatrixError(str(exc)) from exc
        project = find_unity_project(source_root)
        if not _unity_project_ready(project):
            raise MatrixError(
                "agent returned no unambiguous Unity project; expected Assets/, "
                "Packages/, and ProjectSettings/ProjectVersion.txt at the root or "
                "in one nested directory"
            )
        if out.exists():
            raise MatrixError(f"submission destination already exists: {out}")
        out.mkdir(parents=True)
        for name in ("Assets", "Packages", "ProjectSettings"):
            shutil.copytree(project / name, out / name)
        for name in ("ops.json", "BUILD.md"):
            source = next(
                (path for path in (source_root / name, project / name) if path.is_file()),
                None,
            )
            if source is not None:
                shutil.copy2(source, out / name)
        write_json(
            out / "collection.json",
            {
                "source_root": str(source_root),
                "project": str(project),
                "engine": "unity",
                "collected": _utc_now(),
            },
        )
        return out

    try:
        project = project_root(source_root)
    except (OSError, ValueError) as exc:
        raise MatrixError(f"agent returned no unambiguous Godot project: {exc}") from exc
    if not (project / "project.godot").is_file():
        raise MatrixError(f"agent returned no project.godot under {source_root}")
    if out.exists():
        raise MatrixError(f"submission destination already exists: {out}")
    shutil.copytree(project, out / "game")
    for name in ("GDD.md", "ops.json", "demos.json"):
        for source in (source_root / name, project / name):
            if source.is_file():
                shutil.copy2(source, out / name)
                break
    # The evaluator and the route driver read `res://gb_levels.json`, i.e. the
    # manifest beside project.godot.  The prompt's layout diagram also allows it
    # at the submission root, so a root-level manifest is moved into the project
    # when the project itself ships none (a project-level file always wins).
    rescued_interface_files: list[str] = []
    if project.resolve() != source_root.resolve():
        for name in ROOT_RESCUED_INTERFACE_FILES:
            root_copy = source_root / name
            if root_copy.is_file() and not (out / "game" / name).is_file():
                shutil.copy2(root_copy, out / "game" / name)
                rescued_interface_files.append(name)
    write_json(
        out / "collection.json",
        {
            "source_root": str(source_root),
            "project": str(project),
            "engine": "godot",
            "collected": _utc_now(),
            "rescued_interface_files": rescued_interface_files,
        },
    )
    return out


def _submission_produced(item: CaseResult) -> bool:
    return _submission_ready(item.root / "submission", item.mode)


def _mean(values: list[float]) -> float | None:
    return sum(values) / len(values) if values else None


def _aggregate_verdict(values: list[str]) -> str:

    unique = set(values)
    if len(unique) == 1:
        return values[0]
    if unique & {"failed", "malformed"}:
        return "failed"
    if unique & {"inconclusive", "unmeasurable", "skipped", "exempt"}:
        return "inconclusive"
    if unique <= {"passed", "unobservable"}:
        return "passed"
    return "mixed"


def _planned_case_rows(
    run_wire: dict[str, Any], results: list[CaseResult]
) -> list[tuple[str, str, str]]:
    selected = run_wire.get("selected") or []
    rows = [
        (
            str(item.get("game_id") or ""),
            str(item.get("mode") or ""),
            str(item.get("case_id") or ""),
        )
        for item in selected
        if isinstance(item, dict) and item.get("game_id") and item.get("mode")
    ]
    if rows:
        return rows
    return [(item.game_id, item.mode, item.case_id) for item in results]


def _agent_attempted(item: CaseResult) -> bool:


    return item.agent_exit_code is not None and not (
        item.status == "generated" and item.agent_exit_code == AGENT_DRY_RUN_EXIT
    )


def _case_denominators(
    cells: list[CaseResult],
    ready_cases: set[tuple[str, str, str]],
    *,
    planned: int,
) -> dict[str, int]:
    measured = [item for item in cells if item.resolved is not None]
    return {
        "planned_cells": planned,
        "reported_cells": len(cells),
        "material_ready_cells": sum(
            (item.game_id, item.mode, item.case_id) in ready_cases for item in cells
        ),
        "agent_attempted_cells": sum(_agent_attempted(item) for item in cells),
        "submission_produced_cells": sum(_submission_produced(item) for item in cells),
        "verifier_measured_cells": len(measured),
        "resolved_yes": sum(item.resolved is True for item in cells),
        "resolved_no": sum(item.resolved is False for item in cells),
        "resolved_not_measured": sum(
            item.status == "completed" and item.resolved is None for item in cells
        ),
    }


def _game_mode_aggregates(
    results: list[CaseResult],
    ready_cases: set[tuple[str, str, str]],
    planned_rows: list[tuple[str, str, str]],
) -> dict[tuple[str, str], dict[str, Any]]:
    expected = Counter((game_id, mode) for game_id, mode, _case_id in planned_rows)
    grouped: dict[tuple[str, str], list[CaseResult]] = defaultdict(list)
    for item in results:
        grouped[(item.game_id, item.mode)].append(item)
    aggregates: dict[tuple[str, str], dict[str, Any]] = {}
    for key in sorted(set(expected) | set(grouped)):
        cells = grouped.get(key, [])
        expected_cases = int(expected.get(key) or len(cells))
        complete = expected_cases > 0 and len(cells) == expected_cases
        measured = complete and all(item.resolved is not None for item in cells)
        resolved = (
            all(item.resolved is True for item in cells) if measured else None
        )
        behavior_values = [
            float(item.metrics["behavior_score_lo"])
            for item in cells
            if item.metrics.get("comparable") is True
            and item.metrics.get("behavior_score_lo") is not None
        ] if complete else []
        headline_values = [
            float(item.metrics["headline_score"])
            for item in cells
            if item.metrics.get("ranking_eligible") is True
            and item.metrics.get("headline_score") is not None
        ] if complete else []
        reproduction_values = [
            float(item.metrics["reproduction_credit"])
            for item in cells
            if item.metrics.get("reproduction_credit") is not None
        ] if complete else []
        verdicts: dict[str, list[str]] = defaultdict(list)
        if complete:
            for item in cells:
                for check_id, verdict in (item.metrics.get("item_verdicts") or {}).items():
                    verdicts[str(check_id)].append(str(verdict))
        aggregates[key] = {
            "game_id": key[0],
            "mode": key[1],
            "expected_cases": expected_cases,
            "reported_cases": len(cells),
            "complete": complete,
            "material_ready": complete and all(
                (item.game_id, item.mode, item.case_id) in ready_cases for item in cells
            ),
            "agent_attempted": complete and all(
                _agent_attempted(item) for item in cells
            ),
            "submission_produced": complete and all(
                _submission_produced(item) for item in cells
            ),
            "verifier_measured": measured,
            "resolved": resolved,
            "resolved_not_measured": (
                complete
                and all(item.status == "completed" for item in cells)
                and not measured
            ),
            "behavior_score_lo": _mean(behavior_values),
            "headline_score": _mean(headline_values),
            "ranking_eligible": bool(cells) and complete and all(
                item.metrics.get("ranking_eligible") is True for item in cells
            ),
            "reproduction_credit": _mean(reproduction_values),
            "item_verdicts": {
                check_id: _aggregate_verdict(values)
                for check_id, values in verdicts.items()
            },
        }
    return aggregates


def _write_matrix_summary(root: Path, results: list[CaseResult], *, total: int) -> dict[str, Any]:
    counts: dict[str, int] = {}
    for item in results:
        counts[item.status] = counts.get(item.status, 0) + 1
    ready_cases: set[tuple[str, str, str]] = set()
    for item in results:
        manifest = item.root / "package" / "manifest.json"
        if not manifest.is_file():
            continue
        try:
            package_wire = json.loads(manifest.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            continue
        if not list(package_wire.get("blockers") or []):
            ready_cases.add((item.game_id, item.mode, item.case_id))
    run_wire: dict[str, Any] = {}
    try:
        run_wire = json.loads((root / "run.json").read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        pass
    planned_rows = _planned_case_rows(run_wire, results)
    planned_case_count = len(planned_rows) if planned_rows else total
    case_denominators = _case_denominators(
        results, ready_cases, planned=planned_case_count
    )
    aggregates = _game_mode_aggregates(results, ready_cases, planned_rows)
    planned_game_modes = set((game_id, mode) for game_id, mode, _case_id in planned_rows)
    game_mode_denominators = {
        "planned_game_modes": len(planned_game_modes),
        "reported_game_modes": sum(bool(item["reported_cases"]) for item in aggregates.values()),
        "material_ready_game_modes": sum(item["material_ready"] for item in aggregates.values()),
        "agent_attempted_game_modes": sum(item["agent_attempted"] for item in aggregates.values()),
        "submission_produced_game_modes": sum(
            item["submission_produced"] for item in aggregates.values()
        ),
        "verifier_measured_game_modes": sum(
            item["verifier_measured"] for item in aggregates.values()
        ),
        "resolved_yes": sum(item["resolved"] is True for item in aggregates.values()),
        "resolved_no": sum(item["resolved"] is False for item in aggregates.values()),
        "resolved_not_measured": sum(
            item["resolved_not_measured"] for item in aggregates.values()
        ),
    }
    mode4_cells = [item for item in results if item.mode == "bugfix"]
    mode4_planned = sum(mode == "bugfix" for _game, mode, _case in planned_rows)
    mode4_raw = _case_denominators(
        mode4_cells, ready_cases, planned=mode4_planned
    )
    mode4_case_attempts = {
        "planned_cases": mode4_raw["planned_cells"],
        "reported_cases": mode4_raw["reported_cells"],
        "material_ready_cases": mode4_raw["material_ready_cells"],
        "agent_attempted_cases": mode4_raw["agent_attempted_cells"],
        "submission_produced_cases": mode4_raw["submission_produced_cells"],
        "verifier_measured_cases": mode4_raw["verifier_measured_cells"],
        "resolved_yes_cases": mode4_raw["resolved_yes"],
        "resolved_no_cases": mode4_raw["resolved_no"],
        "resolved_not_measured_cases": mode4_raw["resolved_not_measured"],
    }
    sandbox = str((run_wire.get("agent") or {}).get("sandbox") or "")
    worktree_clean = (
        run_wire.get("benchmark_worktree_clean") is True and _git_worktree_clean()
    )
    formal_eligible = sandbox == "unshare" and worktree_clean
    modes = sorted({mode for _game_id, mode in planned_game_modes} | {item.mode for item in results})
    per_mode: dict[str, dict[str, Any]] = {}
    for mode in modes:
        cells = [item for item in results if item.mode == mode]
        game_rows = [item for key, item in aggregates.items() if key[1] == mode]
        measured_rows = [item for item in game_rows if item["verifier_measured"]]
        behavior_values = [
            float(item["behavior_score_lo"])
            for item in game_rows
            if item["behavior_score_lo"] is not None
        ]
        headline_values = [
            float(item["headline_score"])
            for item in game_rows
            if item["ranking_eligible"] is True and item["headline_score"] is not None
        ]
        reproduction_values = [
            float(item["reproduction_credit"])
            for item in game_rows
            if item["reproduction_credit"] is not None
        ]
        verdict_counts: dict[str, dict[str, int]] = {}
        for game_row in game_rows:
            for check_id, verdict in game_row["item_verdicts"].items():
                bucket = verdict_counts.setdefault(str(check_id), {})
                bucket[verdict] = bucket.get(verdict, 0) + 1
        planned_for_mode = sum(selected_mode == mode for _game, selected_mode in planned_game_modes)
        planned_cases_for_mode = sum(
            selected_mode == mode for _game, selected_mode, _case in planned_rows
        )
        case_level = _case_denominators(
            cells, ready_cases, planned=planned_cases_for_mode
        )
        per_mode[mode] = {


            "planned_cells": planned_for_mode,
            "planned_game_modes": planned_for_mode,
            "reported_game_modes": sum(bool(item["reported_cases"]) for item in game_rows),
            "case_attempts": planned_cases_for_mode,
            "reported_case_attempts": len(cells),
            "material_ready_cells": sum(item["material_ready"] for item in game_rows),
            "material_ready_case_attempts": case_level["material_ready_cells"],
            "agent_attempted_cells": sum(item["agent_attempted"] for item in game_rows),
            "agent_attempted_case_attempts": case_level["agent_attempted_cells"],
            "submission_produced_cells": sum(
                item["submission_produced"] for item in game_rows
            ),
            "submission_produced_case_attempts": case_level["submission_produced_cells"],
            "verifier_measured_cells": len(measured_rows),
            "verifier_measured_case_attempts": case_level["verifier_measured_cells"],
            "resolved_yes": sum(item["resolved"] is True for item in game_rows),
            "resolved_no": sum(item["resolved"] is False for item in game_rows),
            "resolved_not_measured": sum(
                item["resolved_not_measured"] for item in game_rows
            ),
            "resolved_success_rate": (
                sum(item["resolved"] is True for item in game_rows) / len(measured_rows)
                if measured_rows else None
            ),
            "mean_behavior_score_lo": _mean(behavior_values),
            "mean_headline_score": _mean(headline_values) if formal_eligible else None,
            "ranking_eligible_game_modes": (
                sum(item["ranking_eligible"] for item in game_rows)
                if formal_eligible else 0
            ),
            "mean_reproduction_credit": _mean(reproduction_values),
            "item_verdict_counts": verdict_counts,
        }
    game_mode_measured = game_mode_denominators["verifier_measured_game_modes"]
    game_mode_yes = game_mode_denominators["resolved_yes"]
    payload = {
        "schema": MATRIX_SCHEMA,
        "root": str(root),
        "total_selected": total,
        "total_game_modes": len(planned_game_modes),
        "reported": len(results),
        "reported_game_modes": game_mode_denominators["reported_game_modes"],
        "counts": counts,


        "denominators": case_denominators,
        "game_mode_denominators": game_mode_denominators,
        "mode4_case_attempts": mode4_case_attempts,
        "resolved_success_rate": (
            game_mode_yes / game_mode_measured if game_mode_measured else None
        ),
        "measurement_coverage": (
            game_mode_measured / len(planned_game_modes) if planned_game_modes else None
        ),
        "case_attempt_resolved_success_rate": (
            case_denominators["resolved_yes"] / case_denominators["verifier_measured_cells"]
            if case_denominators["verifier_measured_cells"] else None
        ),
        "aggregation": {
            "cross_mode_unit": "game_mode",
            "mode4_within_game": (
                "resolved requires every planned case to be measured and pass; "
                "continuous metrics are averaged within game before averaging games"
            ),
            "case_attempts_reported_separately": True,
        },
        "per_mode": per_mode,
        "protocol": {
            "sandbox": sandbox,
            "benchmark_worktree_clean": worktree_clean,
            "formal_eligible": formal_eligible,
            "note": (
                "scoped benchmark-material isolation: /data2 is hidden except the "
                "visible workspace; other host paths and network are not isolated"
                if formal_eligible else


                "container-isolated run: no host path is visible to the agent, but "
                "the sandbox image ships an unpinned toolchain and is a mutable "
                "registry tag, so the run is not reproducible from this repository"
                if sandbox == "docker" else
                "development run: formal eligibility requires unshare and a clean, "
                "committed benchmark definition"
            ),
        },
        "cases": [item.to_dict() for item in results],
        "updated_at": _utc_now(),
    }
    write_json(root / "summary.json", payload)
    fields = [
        "game_id", "mode", "case_id", "status", "phase", "agent_exit_code", "resolved",
        "eligibility", "headline_status", "headline_score", "ranking_eligible",
        "behavior_score_lo", "behavior_coverage",
        "reproduction_credit", "failure_kind", "detail", "started_at", "finished_at",
    ]
    with (root / "summary.csv").open("w", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=fields)
        writer.writeheader()
        for item in results:
            row = item.to_dict()
            row.update({
                "behavior_score_lo": item.metrics.get("behavior_score_lo"),
                "behavior_coverage": item.metrics.get("behavior_coverage"),
                "headline_status": item.metrics.get("headline_status"),
                "headline_score": item.metrics.get("headline_score"),
                "ranking_eligible": item.metrics.get("ranking_eligible"),
                "reproduction_credit": item.metrics.get("reproduction_credit"),
            })
            writer.writerow({key: row.get(key) for key in fields})
    denominator_fields = [
        "planned_cells", "reported_cells", "material_ready_cells", "agent_attempted_cells",
        "submission_produced_cells", "verifier_measured_cells", "resolved_yes",
        "resolved_no", "resolved_not_measured", "resolved_success_rate",
        "measurement_coverage",
    ]
    denominator_row = {
        **payload["denominators"],
        "resolved_success_rate": payload["case_attempt_resolved_success_rate"],
        "measurement_coverage": (
            case_denominators["verifier_measured_cells"] / case_denominators["planned_cells"]
            if case_denominators["planned_cells"] else None
        ),
    }
    with (root / "denominators.csv").open("w", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=denominator_fields)
        writer.writeheader()
        writer.writerow(denominator_row)
    game_mode_fields = list(game_mode_denominators) + [
        "resolved_success_rate", "measurement_coverage"
    ]
    with (root / "game_mode_denominators.csv").open(
        "w", encoding="utf-8", newline=""
    ) as handle:
        writer = csv.DictWriter(handle, fieldnames=game_mode_fields)
        writer.writeheader()
        writer.writerow({
            **game_mode_denominators,
            "resolved_success_rate": payload["resolved_success_rate"],
            "measurement_coverage": payload["measurement_coverage"],
        })
    with (root / "mode4_case_attempts.csv").open(
        "w", encoding="utf-8", newline=""
    ) as handle:
        writer = csv.DictWriter(handle, fieldnames=list(mode4_case_attempts))
        writer.writeheader()
        writer.writerow(mode4_case_attempts)
    mode_fields = [
        "mode", "planned_cells", "planned_game_modes", "reported_game_modes",
        "case_attempts", "reported_case_attempts", "material_ready_cells",
        "material_ready_case_attempts", "agent_attempted_cells",
        "submission_produced_cells", "verifier_measured_cells", "resolved_yes",
        "resolved_no", "resolved_not_measured", "resolved_success_rate",
        "mean_headline_score", "ranking_eligible_game_modes",
        "mean_behavior_score_lo", "mean_reproduction_credit",
    ]
    with (root / "per_mode.csv").open("w", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=mode_fields)
        writer.writeheader()
        for mode, values in sorted(per_mode.items()):
            writer.writerow({"mode": mode, **{key: values.get(key) for key in mode_fields[1:]}})
    return payload
