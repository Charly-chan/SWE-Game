#!/usr/bin/env bash
# Public benchmark entry point: download fixed task packages,
# schedule agent runs, evaluate submissions, and summarize the selected matrix.
# Packages, submissions, logs, and reports are written under --out.
set -euo pipefail

HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
export GB_ROOT="$HERE"
# shellcheck source=eval/tools/gb_env.sh
. "$HERE/eval/tools/gb_env.sh"

usage() {
  cat <<'EOF'
usage: ./run_benchmark.sh --game <id|all> --mode <brief|gdd|skeleton|bugfix|port>
       --harness <claude|codex> --model <id> [--provider auto|micu|openai|anthropic]
       [--budget <seconds>] [--reasoning high] [--playtest-kit on|off]
       [--visual-judge none|local|vlm] [--case-id <bugfix case id>]
       [--reference-video on|off] [--eval on|off] [--brief-design on|off]
       [--sandbox unshare|docker|none] [--docker-image <tag>]
       [--profile community-docker] [--mode5-state-dir <dir>]
       [--docker <docker-cli>]
       [--concurrency 3] [--out results/<run_id>] [--dry-run] [--resume]

Generates reusable task packages, runs cells through a detached bounded
scheduler, waits, evaluates each submission, and writes summary.csv,
summary.json, and leaderboard.md. Dry-run prints each exact harness argv and
does not launch a harness or make an API request.

Modes brief, gdd, skeleton, and port run one cell per game. Mode bugfix runs
one cell per active bugfix case: --game all covers every active case in the
catalog, --game <id> covers that game's cases, and --case-id <id> selects a
single case. Bugfix cell directories carry the case id as a fifth component.

--reference-video defaults to on (normal mode inputs). Only --mode brief
accepts off: omit the reference gameplay video from the agent's inputs.
Assets, brief, GB interface, agent output and evaluation remain unchanged.
This is not --visual-judge none. Use separate --out directories for the two arms.

Mode port (Mode 5, Godot -> Unity) delegates to gb mode5 run using the sole
Community Docker profile. Run gb mode5 setup/doctor first. Unity 6000.3.23f1
is provided in the verified Agent image and rebuilt by an independent offline
evaluator. --dry-run only
packages and needs no Unity; it is not execution or scoring evidence.

--eval defaults to on. --eval off runs the agents and keeps package/,
submission/, agent/ and films/ per cell but skips evaluation (no evaluation/
directory, no scores in summary.csv). Live Mode 5 requires --eval on; its
candidate/evaluator execution stays on the Community path. Other modes can
evaluate later on a machine that has the evaluator's requirements:
  ./evaluate.sh <cell>/package <cell>/submission --out <cell>/evaluation

--visual-judge defaults to none (engine-only evaluation). vlm reuses the
cell's own model route as the S-card judge and needs that key; local attaches
image diagnostics without a model.

--brief-design defaults to off. In Brief evaluation, on includes the authored
GDD quality and interface Design score. Off redistributes its weight across
the other objective axes, keeping objective/VLM weights at 85/15.

For Modes 1-4, --sandbox defaults to unshare, the formal condition. docker runs each agent in
a throwaway container instead; use it on hosts whose capability bounding set
lacks CAP_SYS_ADMIN, where unshare --mount and Codex's own bwrap sandbox cannot
start at all. The image follows the mode -- GB_SANDBOX_IMAGE (Godot) for Modes
1-4, overridable with
--docker-image; build local tags with ./docker/build.sh all. Only the task
workspace enters the container and the coordinator still runs on the host. The
container hides every host path, but its toolchain is not the pinned one and the
image is a mutable registry tag, so docker runs are never formally eligible and
their scores must not be pooled with unshare scores. Pair it with --eval off and
evaluate on a pinned host. Live Mode 5 uses its image-lock and independent
Community evaluator instead; it is not paper-environment certified.
none is debug-only. Orphaned containers: bash eval/tools/gb_docker_sweep.sh.

For Modes 1-4, --budget is off by default: each cell runs until the agent itself stops, and
the cell records budget null plus the measured wall time. --budget <seconds>
is an explicit opt-in hard cap on the agent process. Live Mode 5 defaults to
a fixed 7200-second budget; explicit positive --budget overrides it and the
report records the actual budget.

--provider defaults to auto: the api env file (.gb_api.env.example → copy,
fill, ./setup.sh --check-auth) decides the route. Codex: OPENAI_BASE_URL set →
that gateway via an explicit -c model_providers.gamebench_proxy.* block
(env_key OPENAI_API_KEY, wire_api responses); empty → Codex's native openai
provider (OPENAI_API_KEY, or the ChatGPT login in ~/.codex/auth.json when the
key is empty). Claude Code: ANTHROPIC_API_KEY (or ANTHROPIC_AUTH_TOKEN, Bearer,
when only that is set) + ANTHROPIC_BASE_URL (empty = api.anthropic.com) exported
into the agent environment only. anthropic uses the operator's Claude subscription
login from ~/.claude/.credentials.json in an isolated per-cell home. micu keeps the
historical MICU-gateway defaults; openai forces the native Codex provider.
EOF
}

GAME="" MODE="" HARNESS="" MODEL="" PROVIDER="auto" BUDGET="" REASONING=high
KIT=off VISUAL_JUDGE=none CASE_ID="" CONCURRENCY=3 OUT="" DRY_RUN=0 RESUME=0
REFERENCE_VIDEO=on EVAL=on
BRIEF_DESIGN=off
SANDBOX=unshare DOCKER_IMAGE=""
PROFILE="" MODE5_STATE_DIR="" DOCKER_BIN="${GB_DOCKER_BIN:-docker}"
while [ $# -gt 0 ]; do
  case "$1" in
    --game) GAME="${2:-}"; shift ;;
    --mode) MODE="${2:-}"; shift ;;
    --harness) HARNESS="${2:-}"; shift ;;
    --model) MODEL="${2:-}"; shift ;;
    --provider) PROVIDER="${2:-}"; shift ;;
    --budget) BUDGET="${2:-}"; shift ;;
    --reasoning) REASONING="${2:-}"; shift ;;
    --playtest-kit) KIT="${2:-}"; shift ;;
    --visual-judge) VISUAL_JUDGE="${2:-}"; shift ;;
    --reference-video) REFERENCE_VIDEO="${2:-}"; shift ;;
    --eval) EVAL="${2:-}"; shift ;;
    --brief-design) BRIEF_DESIGN="${2:-}"; shift ;;
    --sandbox) SANDBOX="${2:-}"; shift ;;
    --docker-image) DOCKER_IMAGE="${2:-}"; shift ;;
    --profile) PROFILE="${2:-}"; shift ;;
    --mode5-state-dir) MODE5_STATE_DIR="${2:-}"; shift ;;
    --docker) DOCKER_BIN="${2:-}"; shift ;;
    --case-id) CASE_ID="${2:-}"; shift ;;
    --concurrency) CONCURRENCY="${2:-}"; shift ;;
    --out) OUT="${2:-}"; shift ;;
    --dry-run) DRY_RUN=1 ;;
    --resume) RESUME=1 ;;
    -h|--help) usage; exit 0 ;;
    *) printf 'error: unknown option %s (try --help)\n' "$1" >&2; exit 2 ;;
  esac
  shift
done

[ -n "$GAME" ] && [ -n "$MODE" ] && [ -n "$HARNESS" ] && [ -n "$MODEL" ] ||
  { usage >&2; exit 2; }
case "$MODE" in brief|gdd|skeleton|bugfix|port) ;; *) printf 'error: invalid --mode\n' >&2; exit 2 ;; esac
case "$HARNESS" in claude|codex) ;; *) printf 'error: invalid --harness\n' >&2; exit 2 ;; esac
case "$PROVIDER" in auto|micu|openai|anthropic) ;; *) printf 'error: invalid --provider (auto|micu|openai|anthropic)\n' >&2; exit 2 ;; esac
case "$KIT" in on|off) ;; *) printf 'error: invalid --playtest-kit\n' >&2; exit 2 ;; esac
case "$VISUAL_JUDGE" in none|local|vlm) ;; *) printf 'error: invalid --visual-judge (none|local|vlm)\n' >&2; exit 2 ;; esac
case "$REFERENCE_VIDEO" in on|off) ;; *) printf 'error: invalid --reference-video (on|off)\n' >&2; exit 2 ;; esac
case "$EVAL" in on|off) ;; *) printf 'error: invalid --eval (on|off)\n' >&2; exit 2 ;; esac
case "$BRIEF_DESIGN" in on|off) ;; *) printf 'error: invalid --brief-design (on|off)\n' >&2; exit 2 ;; esac
case "$SANDBOX" in unshare|docker|none) ;; *) printf 'error: invalid --sandbox (unshare|docker|none)\n' >&2; exit 2 ;; esac
if [ -n "$PROFILE" ] && [ "$PROFILE" != community-docker ]; then
  printf 'error: invalid --profile\n' >&2; exit 2
fi
if [ "$MODE" = port ] && [ -z "$PROFILE" ]; then PROFILE=community-docker; fi
if [ "$PROFILE" = community-docker ] && [ "$MODE" != port ]; then
  printf 'error: --profile community-docker applies only to --mode port\n' >&2; exit 2
fi
if [ -n "$DOCKER_IMAGE" ] && [ "$SANDBOX" != docker ]; then
  printf 'error: --docker-image requires --sandbox docker\n' >&2; exit 2
fi
if [ "$SANDBOX" = docker ]; then
  # Settle on one effective tag here so the preflight below probes the very image
  # the cells run in.
  DOCKER_IMAGE="$(gb_sandbox_image_for_mode "$MODE" "$DOCKER_IMAGE")"
fi
if [ "$REFERENCE_VIDEO" = off ] && [ "$MODE" != brief ]; then
  printf 'error: --reference-video off applies only to --mode brief (Mode 1)\n' >&2
  exit 2
fi
if [ -n "$CASE_ID" ] && [ "$MODE" != bugfix ]; then
  printf 'error: --case-id applies only to --mode bugfix\n' >&2
  exit 2
fi
case "$REASONING" in low|medium|high) ;; *) printf 'error: invalid --reasoning\n' >&2; exit 2 ;; esac
case "$CONCURRENCY" in ''|*[!0-9]*) printf 'error: concurrency must be a positive integer\n' >&2; exit 2 ;; esac
[ "$CONCURRENCY" -gt 0 ] || { printf 'error: concurrency must be positive\n' >&2; exit 2; }
if [ -n "$BUDGET" ]; then
  case "$BUDGET" in *[!0-9]*) printf 'error: --budget must be a positive integer (seconds)\n' >&2; exit 2 ;; esac
  [ "$BUDGET" -gt 0 ] || { printf 'error: --budget must be positive\n' >&2; exit 2; }
fi
if [ "$PROVIDER" = openai ] && [ "$HARNESS" != codex ]; then
  printf 'error: --provider openai is supported only by the codex harness\n' >&2
  exit 2
fi
if [ "$PROVIDER" = anthropic ] && [ "$HARNESS" != claude ]; then
  printf 'error: --provider anthropic is supported only by the claude harness\n' >&2
  exit 2
fi

if [ -z "$OUT" ]; then
  OUT="$HERE/results/run_$(date -u +%Y%m%dT%H%M%SZ)"
fi
OUT="$(readlink -m "$OUT")"
CELLS="$OUT/cells"
mkdir -p "$CELLS"

gb_require_venv
gb_python_env
export PATH="$GB_TOOLS_BIN:$PATH"
export PYTHONPATH="$GB_EVALSYS${PYTHONPATH:+:$PYTHONPATH}"
API_ENV="$(gb_api_env_path)"
# The env file is read here only to decide the route (base URL + key variable
# NAME); values stay in this process's environment and never reach argv or logs.
gb_load_api_env || true
gb_resolve_agent_route "$HARNESS" "$PROVIDER"
AGENT_PROVIDER="$GB_ROUTE_KIND"
BASE_URL="$GB_ROUTE_BASE_URL"
KEY_ENV="$GB_ROUTE_KEY_ENV"
AUTH_ARGS=()
AUTH_FILE=""
ENV_FILE_ARGS=()
[ ! -f "$API_ENV" ] || ENV_FILE_ARGS=(--agent-env-file "$API_ENV")
if [ "$AGENT_PROVIDER" = openai ] && [ -z "${OPENAI_API_KEY:-}" ]; then
  AUTH_FILE="${CODEX_AUTH_FILE:-$HOME/.codex/auth.json}"
  AUTH_ARGS=(--agent-auth-file "$AUTH_FILE")
fi
if [ "$AGENT_PROVIDER" = claude ]; then
  AUTH_FILE="${CLAUDE_AUTH_FILE:-$HOME/.claude/.credentials.json}"
  AUTH_ARGS=(--agent-auth-file "$AUTH_FILE")
fi
if [ "$DRY_RUN" = 1 ]; then
  # Dry-run needs no credential: the key variable is replaced by a sentinel.
  if [ -n "$KEY_ENV" ]; then
    printf -v "$KEY_ENV" '%s' "GAMEBENCH_DRY_RUN_SENTINEL"
    export "$KEY_ENV"
  fi
  AUTH_FILE=""
  AUTH_ARGS=()
fi
route_summary() {
  if [ "$AGENT_PROVIDER" = openai ]; then
    printf 'route: codex native openai provider (%s)\n' \
      "$([ -n "$AUTH_FILE" ] && printf 'ChatGPT login %s' "$AUTH_FILE" || printf 'OPENAI_API_KEY')"
  elif [ "$AGENT_PROVIDER" = claude ]; then
    printf 'route: Claude subscription login %s\n' "$AUTH_FILE"
  else
    printf 'route: %s -> %s, key from $%s (%s)\n' "$HARNESS" "$BASE_URL" "$KEY_ENV" "$API_ENV"
  fi
}

# The release Mode-5 profile is deliberately a thin delegation.  Package
# generation, the licensed Unity Agent container, the fresh offline evaluator,
# resume validation, and reporting all stay in the independently tested Python
# implementation behind `gb mode5`; this entry point only expands --game all.
if [ "$MODE" = port ] && [ "$PROFILE" = community-docker ]; then
  [ "$EVAL" = on ] || [ "$DRY_RUN" = 1 ] || {
    printf 'error: --profile community-docker requires --eval on\n' >&2; exit 2; }
  [ "$VISUAL_JUDGE" = none ] || {
    printf 'error: Mode 5 uses fixed non-VLM evidence; --visual-judge must be none\n' >&2; exit 2; }
  route_summary
  mkdir -p "$OUT/cells"
  if [ "$GAME" = all ]; then
    mapfile -t MODE5_GAMES < <("$GB_PYTHON" - "$HERE/catalog.json" <<'PY'
import json, sys
for row in json.load(open(sys.argv[1], encoding="utf-8")):
    print(row["id"])
PY
)
  else
    MODE5_GAMES=("$GAME")
  fi
  "$GB_PYTHON" - "$OUT/run.json" "$MODEL" "$HARNESS" "$PROVIDER" <<'PY'
import json, pathlib, sys
path = pathlib.Path(sys.argv[1])
path.write_text(json.dumps({
    "schema": "gamebench.mode5-community-run.v1",
    "profile": "community-docker", "paper_compatible": False,
    "params": {"mode": "port"},
    "model": sys.argv[2], "harness": sys.argv[3], "provider": sys.argv[4],
}, indent=2, sort_keys=True) + "\n", encoding="utf-8")
PY
  for game in "${MODE5_GAMES[@]}"; do
    safe_model="${MODEL//\//_}"
    cell="$OUT/cells/${game}__port__${HARNESS}__${safe_model}"
    if [ "$DRY_RUN" = 1 ]; then
      "$HERE/gb" mode5 generate --game "$game" --out "$cell/package"
      printf 'Mode 5 package preview: %s (no harness launched)\n' "$cell"
      continue
    fi
    mode5_args=(mode5 run --game "$game" --out "$cell" --harness "$HARNESS"
      --model "$MODEL" --agent-provider "$AGENT_PROVIDER" --agent-effort "$REASONING"
      --docker "$DOCKER_BIN" --fidelity-judge "$VISUAL_JUDGE")
    [ -z "$MODE5_STATE_DIR" ] || mode5_args+=(--state-dir "$MODE5_STATE_DIR")
    [ -z "$BASE_URL" ] || mode5_args+=(--agent-base-url "$BASE_URL")
    [ -z "$KEY_ENV" ] || mode5_args+=(--agent-key-env "$KEY_ENV")
    [ ! -f "$API_ENV" ] || mode5_args+=(--agent-env-file "$API_ENV")
    [ -z "$AUTH_FILE" ] || mode5_args+=(--agent-auth-file "$AUTH_FILE")
    [ -z "$BUDGET" ] || mode5_args+=(--agent-timeout "$BUDGET")
    # The outer run may be resumed while this game has never started.
    [ "$RESUME" = 0 ] || [ ! -f "$cell/run.json" ] || mode5_args+=(--resume)
    "$HERE/gb" "${mode5_args[@]}"
  done
  if [ "$DRY_RUN" = 1 ]; then
    printf 'Mode 5 Community package preview complete; no Docker, license probe, or model call.\n'
    exit 0
  fi
  "$HERE/gb" mode5 summarize "$OUT"
  printf 'Mode 5 Community Docker results: %s\n' "$OUT"
  exit 0
fi

# Docker sandbox preflight. Every cell needs the daemon and the image, so fail
# once here rather than once per cell after paying container startup. The pull is
# serialized on purpose: concurrent cells racing a cold pull all wait anyway.
SANDBOX_GODOT="" SANDBOX_CLAUDE="" SANDBOX_CODEX=""
if [ "$SANDBOX" = docker ] && [ "$DRY_RUN" = 0 ]; then
  command -v docker >/dev/null 2>&1 || {
    printf 'error: --sandbox docker: no docker client on PATH\n' >&2; exit 2; }
  timeout 60 docker version --format '{{.Server.Version}}' >/dev/null 2>&1 || {
    printf 'error: --sandbox docker: the docker daemon did not respond (DOCKER_HOST=%s)\n' \
      "${DOCKER_HOST:-<unset>}" >&2; exit 2; }
  SANDBOX_PREFLIGHT="$OUT/sandbox_preflight"
  mkdir -p "$SANDBOX_PREFLIGHT"
  bash "$HERE/eval/tools/gb_docker_sweep.sh" --report > "$SANDBOX_PREFLIGHT/orphans.txt" 2>&1 || true
  if timeout 60 docker image inspect "$DOCKER_IMAGE" >/dev/null 2>&1; then
    printf 'sandbox: using available image %s\n' "$DOCKER_IMAGE"
  else
    printf 'sandbox: pulling %s (first run may take several minutes)\n' "$DOCKER_IMAGE"
    timeout 1800 docker pull "$DOCKER_IMAGE" > "$SANDBOX_PREFLIGHT/pull.log" 2>&1 || {
      printf 'error: sandbox image unavailable: %s; build it with ./docker/build.sh or pass an accessible --docker-image (see %s)\n' \
        "$DOCKER_IMAGE" "$SANDBOX_PREFLIGHT/pull.log" >&2
      exit 2
    }
  fi
  # Record the container's own toolchain: the host versions below are absent or
  # irrelevant under docker, and reading host numbers as the agent's would
  # misreport what produced the submissions.
  if sandbox_versions="$(timeout 600 docker run --rm --label gamebench.sandbox=preflight \
      "$DOCKER_IMAGE" sh -lc 'printf "%s\t%s\t%s\n" \
        "$(godot --version 2>/dev/null | head -1)" \
        "$(claude --version 2>/dev/null | head -1)" \
        "$(codex --version 2>/dev/null | head -1)"' 2>"$SANDBOX_PREFLIGHT/probe.err")"; then
    printf '%s\n' "$sandbox_versions" > "$SANDBOX_PREFLIGHT/versions.txt"
    IFS=$'\t' read -r SANDBOX_GODOT SANDBOX_CLAUDE SANDBOX_CODEX <<<"$sandbox_versions"
    printf 'sandbox: %s  godot=%s claude=%s codex=%s\n' \
      "$DOCKER_IMAGE" "$SANDBOX_GODOT" "$SANDBOX_CLAUDE" "$SANDBOX_CODEX"
    printf 'sandbox: NOT formally eligible -- unpinned toolchain in a mutable registry tag\n'
  else
    printf 'sandbox: could not probe image versions (see %s); continuing\n' \
      "$SANDBOX_PREFLIGHT/probe.err"
  fi
fi

# Mode 5 setup/doctor and Unity execution are owned solely by gb mode5.

# One row per cell: "<game>\t<case_id>". case_id is empty outside bugfix.
CELL_ROWS_TEXT="$("$GB_PYTHON" - "$HERE/catalog.json" "$GAME" "$MODE" "$CASE_ID" <<'PY'
import json, sys
catalog, game, mode, case_id = sys.argv[1:]
rows = json.load(open(catalog, encoding="utf-8"))
known = [row["id"] for row in rows]
wanted = known if game == "all" else [game]
missing = sorted(set(wanted) - set(known))
if missing:
    raise SystemExit("unknown game(s): " + ", ".join(missing))
if mode != "bugfix":
    for g in wanted:
        print(f"{g}\t")
    raise SystemExit(0)
from evalsys.taskgen.mode4.mode4_cases import active_mode4_cases
cells = [(g, c["case_id"]) for g in wanted for c in active_mode4_cases(g)]
if case_id:
    cells = [cell for cell in cells if cell[1] == case_id]
    if not cells:
        raise SystemExit(f"no active bugfix case {case_id!r} for --game {game}")
elif not cells:
    raise SystemExit(f"no active bugfix bundle for {game}")
for g, c in cells:
    print(f"{g}\t{c}")
PY
)" || exit 2
mapfile -t CELL_ROWS <<<"$CELL_ROWS_TEXT"
route_summary

STARTED_AT="$(date -u +%FT%TZ)"
git_sha="$(git -C "$HERE" rev-parse HEAD)"
godot_version="$("$GODOT_BIN" --version 2>/dev/null | sed -n '1p' || true)"
claude_version="" codex_version=""
if [ "$SANDBOX" = docker ]; then
  # Under docker the agent never touches these host binaries, so the preflight
  # probe of the sandbox image is the only honest source. A dry run has no
  # probe, and recording host numbers there would let a reader take them for the
  # agent's.
  godot_version="${SANDBOX_GODOT:-}"
  claude_version="${SANDBOX_CLAUDE:-}"
  codex_version="${SANDBOX_CODEX:-}"
else
  claude_version="$(timeout 10 claude --version 2>/dev/null | sed -n '1p' || true)"
  codex_version="$(timeout 10 codex --version 2>/dev/null | sed -n '1p' || true)"
fi
"$GB_PYTHON" - "$OUT/run.json" "$GAME" "$MODE" "$HARNESS" "$MODEL" "$PROVIDER" \
  "$BUDGET" "$REASONING" "$KIT" "$VISUAL_JUDGE" "$CASE_ID" "$CONCURRENCY" \
  "$git_sha" "$godot_version" "$claude_version" "$codex_version" "$STARTED_AT" "$REFERENCE_VIDEO" "$EVAL" \
  "$SANDBOX" "$DOCKER_IMAGE" "$BRIEF_DESIGN" <<'PY'
import json, pathlib, sys
(path, game, mode, harness, model, provider, budget, reasoning, kit, visual_judge,
 case_id, concurrency, sha, godot, claude, codex, started, reference_video, evaluation,
 sandbox, sandbox_image, brief_design) = sys.argv[1:]
wire = {
  "schema": "gamebench.turnkey.run.v1",
  "params": {"game": game, "mode": mode, "harness": harness, "model": model,
             "provider": provider, "budget": int(budget) if budget else None,
             "reasoning": reasoning,
             "playtest_kit": kit, "visual_judge": visual_judge,
             "reference_video": reference_video,
             "evaluation": evaluation,
             "agent_sandbox": sandbox,
             "agent_docker_image": sandbox_image or None,
             "case_id": case_id or None, "concurrency": int(concurrency)},
  "evaluator_git_sha": sha, "registry_version": None, "godot_version": godot,
  "harness_cli_versions": {"claude": claude or None, "codex": codex or None},
  "harness_versions_source": "per-cell agent/env.json" if sandbox == "docker" else "host",
  "started_at": started, "ended_at": None,
}
if mode == "brief":
    wire["params"]["brief_design"] = brief_design
p = pathlib.Path(path)
if not p.exists():
    p.write_text(json.dumps(wire, indent=2) + "\n", encoding="utf-8")
else:
    existing = json.loads(p.read_text(encoding="utf-8"))
    old = existing["params"]
    # Each cell's own run.json would reject a sandbox change, but only for cells
    # that already started; cells that never ran would happily proceed under the
    # new mode and summarize_results would blend two protocol arms into one table
    # with a single formal-eligibility verdict.  Refuse at second zero instead.
    for key, default in (("reference_video", "on"), ("agent_sandbox", "unshare"), ("agent_docker_image", None)):
        if old.get(key, default) != wire["params"][key]:
            raise SystemExit(f"error: {key} differs from run.json; use a separate --out directory")
    if mode == "brief" and old.get("brief_design", "on") != brief_design:
        raise SystemExit("error: brief_design differs from run.json; use a separate --out directory")
    existing["params"]["evaluation"] = evaluation
    p.write_text(json.dumps(existing, indent=2) + "\n", encoding="utf-8")
PY

common_args() {
  COMMON=(
    --game "$1" --mode "$MODE" --agent-backend "$HARNESS" --model "$MODEL"
    --agent-provider "$AGENT_PROVIDER" --agent-effort "$REASONING"
    --agent-sandbox "$SANDBOX" --engine auto
    --visual-judge "$VISUAL_JUDGE" --playtest-kit "$KIT" --single-cell
    --eval "$EVAL"
    --brief-design "$BRIEF_DESIGN"
    --reference-video "$REFERENCE_VIDEO"
  )
  [ -z "$BUDGET" ] || COMMON+=(--agent-timeout "$BUDGET")
  [ -z "$DOCKER_IMAGE" ] || COMMON+=(--agent-docker-image "$DOCKER_IMAGE")
  if [ "$AGENT_PROVIDER" = custom ]; then
    COMMON+=(--agent-base-url "$BASE_URL" --agent-key-env "$KEY_ENV" "${ENV_FILE_ARGS[@]}")
  else
    COMMON+=("${AUTH_ARGS[@]}")
  fi
  [ -z "$2" ] || COMMON+=(--case-id "$2")
}

cell_dir() {
  local safe_model="${MODEL//\//_}"
  printf '%s/%s__%s__%s__%s%s' "$CELLS" "$1" "$MODE" "$HARNESS" "$safe_model" "${2:+__$2}"
}

# A cell whose package generation refused (state.json status=package_blocked)
# prints its reason; any other cell prints nothing.
blocked_reason() {
  [ -f "$1/state.json" ] || return 0
  "$GB_PYTHON" - "$1/state.json" <<'PY'
import json, sys
s = json.load(open(sys.argv[1], encoding="utf-8"))
if s.get("status") == "package_blocked":
    print(s.get("detail") or "package blocked")
PY
}

QUEUE="$OUT/queue.tsv"
BLOCKED="$OUT/blocked.tsv"
: >"$QUEUE"
: >"$BLOCKED"
blocked_cells=0
for row in "${CELL_ROWS[@]}"; do
  IFS=$'\t' read -r game case_id <<<"$row"
  cell="$(cell_dir "$game" "$case_id")"
  common_args "$game" "$case_id"
  # run-task-matrix exits 1 when the package was refused; that cell is recorded
  # as blocked below and the run goes on. Any other failure still stops the run.
  gen_rc=0
  if [ ! -f "$cell/package/manifest.json" ]; then
    if [ "$REFERENCE_VIDEO" = on ] && [ "$MODE" != bugfix ]; then
      gb_ensure_reference_film "$game"
    else
      gb_ensure_reference_project "$game"
    fi
    GEN_RESUME=()
    [ "$RESUME" = 1 ] && [ -f "$cell/run.json" ] && GEN_RESUME+=(--resume)
    "$GB_PYTHON" "$GB_BENCH" run-task-matrix --out "$cell" "${COMMON[@]}" \
      "${GEN_RESUME[@]}" --generate-only $([ "$DRY_RUN" = 1 ] && printf '%s' --agent-dry-run) || gen_rc=$?
  elif [ "$RESUME" = 1 ]; then
    if [ "$DRY_RUN" = 1 ] && [ ! -f "$cell/agent/request.json" ]; then
      "$GB_PYTHON" "$GB_BENCH" run-task-matrix --out "$cell" "${COMMON[@]}" \
        --resume --generate-only --agent-dry-run || gen_rc=$?
    fi
  else
    printf 'error: cell exists; pass --resume: %s\n' "$cell" >&2
    exit 2
  fi
  reason="$(blocked_reason "$cell")"
  if [ -n "$reason" ]; then
    printf 'blocked: %s%s: %s\n' "$game" "${case_id:+ $case_id}" "$reason" >&2
    printf '%s\t%s\t%s\t%s\n' "$game" "$case_id" "$cell" "$reason" >>"$BLOCKED"
    blocked_cells=$((blocked_cells+1))
    continue
  fi
  [ "$gen_rc" -eq 0 ] || exit "$gen_rc"
  mkdir -p "$cell/films"
  if [ "$DRY_RUN" = 1 ]; then
    "$GB_PYTHON" - "$cell/agent/request.json" <<'PY'
import json, shlex, sys
r = json.load(open(sys.argv[1], encoding="utf-8"))
print("harness argv:", shlex.join(str(x) for x in r["command"]))
PY
  else
    printf '%s\t%s\t%s\n' "$game" "$cell" "$case_id" >>"$QUEUE"
  fi
done

if [ "$DRY_RUN" = 1 ]; then
  "$GB_PYTHON" "$HERE/eval/tools/summarize_results.py" "$OUT"
  printf 'dry-run: downloaded packages and exact requests; no harness launched and no API request made (blocked cells: %s)\n' "$blocked_cells"
  exit "$([ "$blocked_cells" -eq 0 ] && echo 0 || echo 1)"
fi

key_value=""
[ -z "$KEY_ENV" ] || key_value="${!KEY_ENV:-}"
if [ "$AGENT_PROVIDER" = custom ]; then
  [ -n "$key_value" ] && [[ "$key_value" != *REPLACE_ME* ]] ||
    { printf 'error: %s is empty in %s (copy .gb_api.env.example, fill it, then ./setup.sh --check-auth)\n' "$KEY_ENV" "$API_ENV" >&2; exit 2; }
elif [ "$AGENT_PROVIDER" = openai ] && [ -z "$key_value" ] && [ ! -f "$AUTH_FILE" ]; then
  printf 'error: codex native provider has no credential: OPENAI_API_KEY is empty in %s and %s does not exist (run `codex login`, or fill OPENAI_API_KEY)\n' "$API_ENV" "$AUTH_FILE" >&2
  exit 2
elif [ "$AGENT_PROVIDER" = claude ] \
  && { [ "${GB_CLAUDE_AUTH_MODE:-}" != oauth ] || [ -z "${CLAUDE_CODE_OAUTH_TOKEN:-}" ]; } \
  && [ ! -f "$AUTH_FILE" ]; then
  printf 'error: Claude subscription provider has no credential at %s (run `claude login`)\n' "$AUTH_FILE" >&2
  exit 2
fi
# The balance guard below is a MICU dashboard feature; only that gateway gets it.
MICU_GATE=0
BILLING_KEY=""
case "$BASE_URL" in
  *micuapi.ai*) MICU_GATE=1; BILLING_KEY="${MICU_API_KEY:-$key_value}" ;;
esac

# Internal scheduler mode. It is detached by the parent, caps concurrency,
# waits under high load, and applies the MICU balance guard before launches.
if [ "${GB_TURNKEY_SCHEDULER:-0}" = 1 ]; then
  exit 99
fi
SCHEDULER="$OUT/scheduler.sh"
cat >"$SCHEDULER" <<'SCHED'
#!/usr/bin/env bash
set -u
running=0
pids=()
cells=()
reap_one() {
  local pid="${pids[0]}" cell="${cells[0]}" rc=0
  wait "$pid" || rc=$?
  printf '%s\t%s\n' "$rc" "$cell" >>"$OUT/worker-status.tsv"
  pids=("${pids[@]:1}"); cells=("${cells[@]:1}"); running=$((running-1))
}
remaining_usd() {
  local sub use
  sub="$(env -u http_proxy -u https_proxy -u HTTP_PROXY -u HTTPS_PROXY -u all_proxy -u ALL_PROXY curl -fsS -m 25 -H "Authorization: Bearer $BILLING_KEY" https://www.micuapi.ai/v1/dashboard/billing/subscription 2>/dev/null)" || { echo unknown; return; }
  use="$(env -u http_proxy -u https_proxy -u HTTP_PROXY -u HTTPS_PROXY -u all_proxy -u ALL_PROXY curl -fsS -m 25 -H "Authorization: Bearer $BILLING_KEY" 'https://www.micuapi.ai/v1/dashboard/billing/usage?start_date=2026-01-01&end_date=2026-12-31' 2>/dev/null)" || { echo unknown; return; }
  SUB="$sub" USE="$use" "$GB_PYTHON" - <<'PY'
import json, os
print(float(json.loads(os.environ["SUB"])["hard_limit_usd"]) - float(json.loads(os.environ["USE"])["total_usage"]) / 100)
PY
}
: >"$OUT/worker-status.tsv"
while IFS=$'\t' read -r game cell case_id; do
  while [ "$running" -ge "$CONCURRENCY" ]; do reap_one; done
  while awk '{exit !($1 > 60)}' /proc/loadavg; do sleep 30; done
  if [ "$MICU_GATE" = 1 ]; then
    while :; do
      balance="$(remaining_usd)"
      need="$("$GB_PYTHON" -c "print(15 + 10 * $running)")"
      if [ "$balance" != unknown ] && "$GB_PYTHON" -c "raise SystemExit(0 if float('$balance') >= float('$need') else 1)"; then break; fi
      printf '%s waiting for MICU balance (remaining=%s need=%s)\n' "$(date -u +%FT%TZ)" "$balance" "$need"
      sleep 60
    done
  fi
  common=(--game "$game" --mode "$MODE" --agent-backend "$HARNESS" --model "$MODEL"
    --agent-provider "$AGENT_PROVIDER" --agent-effort "$REASONING"
    --agent-sandbox "$SANDBOX" --engine auto --visual-judge "$VISUAL_JUDGE" --playtest-kit "$KIT" --single-cell
    --eval "$EVAL" --brief-design "$BRIEF_DESIGN"
    --reference-video "$REFERENCE_VIDEO")
  [ -z "$BUDGET" ] || common+=(--agent-timeout "$BUDGET")
  [ -z "$DOCKER_IMAGE" ] || common+=(--agent-docker-image "$DOCKER_IMAGE")
  if [ "$AGENT_PROVIDER" = custom ]; then
    common+=(--agent-base-url "$BASE_URL" --agent-key-env "$KEY_ENV")
    [ -z "$API_ENV_ARG" ] || common+=(--agent-env-file "$API_ENV_ARG")
  elif [ -n "$AUTH_FILE" ]; then
    common+=(--agent-auth-file "$AUTH_FILE")
  fi
  [ -z "$case_id" ] || common+=(--case-id "$case_id")
  env -u http_proxy -u https_proxy -u HTTP_PROXY -u HTTPS_PROXY -u all_proxy -u ALL_PROXY \
    "$GB_PYTHON" "$GB_BENCH" run-task-matrix --out "$cell" "${common[@]}" --resume \
    >"$cell/worker.log" 2>&1 &
  pids+=("$!"); cells+=("$cell"); running=$((running+1))
done <"$QUEUE"
while [ "$running" -gt 0 ]; do reap_one; done
SCHED
chmod +x "$SCHEDULER"

# QUEUE too: the detached scheduler reads it (`done <"$QUEUE"`) and runs under
# `set -u`, so without this it dies with "QUEUE: unbound variable" before
# launching a single cell.
export OUT QUEUE CONCURRENCY PROVIDER MODE HARNESS MODEL AGENT_PROVIDER REASONING BUDGET KIT VISUAL_JUDGE REFERENCE_VIDEO EVAL BRIEF_DESIGN
export SANDBOX DOCKER_IMAGE
export GB_PYTHON GB_BENCH API_ENV BASE_URL="${BASE_URL:-}" KEY_ENV="${KEY_ENV:-}"
API_ENV_ARG=""
[ ! -f "$API_ENV" ] || API_ENV_ARG="$API_ENV"
export API_ENV_ARG MICU_GATE BILLING_KEY="${BILLING_KEY:-}" AUTH_FILE
setsid nohup bash "$SCHEDULER" >"$OUT/scheduler.log" 2>&1 < /dev/null &
SCHEDULER_PID=$!
printf '%s\n' "$SCHEDULER_PID" >"$OUT/scheduler.pid"
printf 'scheduler: pid=%s log=%s concurrency=%s\n' "$SCHEDULER_PID" "$OUT/scheduler.log" "$CONCURRENCY"
wait "$SCHEDULER_PID"

evaluation_failures=0
while IFS=$'\t' read -r worker_rc cell; do
  # Matrix owns evaluation and resume. Preserve its report and exit status.
  if [ "$EVAL" = on ] && [ -f "$cell/evaluation/report.json" ]; then
    "$GB_PYTHON" - "$cell/evaluation/report.json" "$cell/evaluation/card.json" <<'PY'
import json, pathlib, sys
report = json.loads(pathlib.Path(sys.argv[1]).read_text(encoding="utf-8"))
pathlib.Path(sys.argv[2]).write_text(
    json.dumps(report.get("scorecard") or {}, indent=2, ensure_ascii=False) + "\n",
    encoding="utf-8",
)
PY
  fi
  if [ "$worker_rc" -ne 0 ] || [ ! -d "$cell/submission" ] ||
     { [ "$EVAL" = on ] && [ ! -f "$cell/evaluation/report.json" ]; }; then
    printf 'warning: failed, unresolved or incomplete cell (worker exit %s): %s\n' "$worker_rc" "$cell" >&2
    evaluation_failures=$((evaluation_failures+1))
  fi
done <"$OUT/worker-status.tsv"

ENDED_AT="$(date -u +%FT%TZ)"
"$GB_PYTHON" "$HERE/eval/tools/summarize_results.py" "$OUT"
"$GB_PYTHON" - "$OUT/run.json" "$ENDED_AT" <<'PY'
import json, pathlib, sys
p = pathlib.Path(sys.argv[1]); wire = json.loads(p.read_text(encoding="utf-8"))
wire["ended_at"] = sys.argv[2]
versions = []
for report in p.parent.glob("cells/*/evaluation/report.json"):
    card = json.loads(report.read_text(encoding="utf-8")).get("scorecard") or {}
    if card.get("registry_version"):
        versions.append(card["registry_version"])
wire["registry_version"] = versions[0] if versions and len(set(versions)) == 1 else (sorted(set(versions)) or None)
p.write_text(json.dumps(wire, indent=2) + "\n", encoding="utf-8")
PY
if [ "$EVAL" = off ]; then
  printf 'evaluation deferred (--eval off): no evaluation/ per cell; run ./evaluate.sh <cell>/package <cell>/submission --out <cell>/evaluation later\n'
fi
printf 'results: %s (evaluation failures or unresolved cells: %s; blocked cells: %s)\n' "$OUT" "$evaluation_failures" "$blocked_cells"
exit "$([ "$evaluation_failures" -eq 0 ] && [ "$blocked_cells" -eq 0 ] && echo 0 || echo 1)"
