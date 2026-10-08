#!/usr/bin/env bash
# Single-game launcher: download a fixed task package, run Claude Code or Codex,
# and retain the submission with optional evaluation. The matrix is detached;
# scripts/README.md and docs/running.md describe the entry points and outputs.
set -euo pipefail

HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
export GB_ROOT="$HERE"
# shellcheck source=eval/tools/gb_env.sh
. "$HERE/eval/tools/gb_env.sh"

usage() {
  cat <<'EOF'
usage: ./scripts/run_coding.sh <game> <brief|gdd|skeleton|bugfix|port> <claude|codex>
                       [--budget SECONDS] [--out DIR] [--dry-run]
                       [--sandbox unshare|docker|none] [--docker-image TAG]
                       [--eval on|off]

The process is launched detached. Its task package, workspace, agent artifacts,
submission, and evaluation are written below DIR/runs/<game>/<mode>/.
Mode port additionally needs a licensed Unity 6000.3.23f1 editor; run
./setup.sh --unity for the manual licensing steps.
--sandbox defaults to unshare (formal). Docker runs use the mode-specific
toolchain image described in docker/README.md. The published images pin their
toolchains; the current protocol marks Docker agent runs formally ineligible.
The none sandbox is debug-only.
Use --sandbox docker --eval off for Mode 5 and transfer the submission to
the certified VM for formal scoring.
EOF
}

[ $# -ge 3 ] || { usage >&2; exit 2; }
GAME="$1"; MODE="$2"; HARNESS="$3"; shift 3
BUDGET=3600
OUT=""
DRY_RUN=0
AGENT_SANDBOX=unshare DOCKER_IMAGE="" EVAL=on
while [ $# -gt 0 ]; do
  case "$1" in
    --budget) [ $# -ge 2 ] || gb_die "--budget needs seconds"; BUDGET="$2"; shift ;;
    --out) [ $# -ge 2 ] || gb_die "--out needs a directory"; OUT="$2"; shift ;;
    --sandbox) [ $# -ge 2 ] || gb_die "--sandbox needs a mode"; AGENT_SANDBOX="$2"; shift ;;
    --dry-run) DRY_RUN=1 ;;
    --docker-image) DOCKER_IMAGE="${2:-}"; shift ;;
    --eval) EVAL="${2:-}"; shift ;;
    -h|--help) usage; exit 0 ;;
    *) gb_die "unknown option $1 (try --help)" ;;
  esac
  shift
done

case "$MODE" in brief|gdd|skeleton|bugfix|port) ;; *) gb_die "mode must be brief, gdd, skeleton, bugfix, or port" ;; esac
case "$HARNESS" in claude|codex) ;; *) gb_die "harness must be claude or codex" ;; esac
case "$AGENT_SANDBOX" in unshare|docker|none) ;; *) gb_die "sandbox must be unshare, docker, or none" ;; esac
case "$EVAL" in on|off) ;; *) gb_die "--eval must be on or off" ;; esac
if [ -n "$DOCKER_IMAGE" ] && [ "$AGENT_SANDBOX" != docker ]; then
  gb_die "--docker-image requires --sandbox docker"
fi
# The image follows the mode unless --docker-image names one; see gb_env.sh.
if [ "$AGENT_SANDBOX" = docker ]; then
  DOCKER_IMAGE="$(gb_sandbox_image_for_mode "$MODE" "$DOCKER_IMAGE")"
  if [ "$MODE" = port ] && [ "$EVAL" = on ]; then
    gb_die "Docker Mode 5 requires --eval off; formal scoring uses the certified VM"
  fi
fi
case "$BUDGET" in ''|*[!0-9]*) gb_die "--budget must be a positive integer" ;; esac
[ "$BUDGET" -gt 0 ] || gb_die "--budget must be a positive integer"

if [ -d "$GAME" ]; then
  GAME_ID="$(basename "$(readlink -f "$GAME")")"
else
  GAME_ID="$GAME"
fi
if [ -z "$OUT" ]; then
  OUT="$GB_RUNS_ROOT/run_${GAME_ID}_${MODE}_${HARNESS}_$(date -u +%Y%m%dT%H%M%SZ)"
fi
OUT="$(readlink -m "$OUT")"
LOG="$OUT.launch.log"
PIDFILE="$OUT.pid"
SUBMISSION="$OUT/runs/$GAME_ID/$MODE/submission"

API_ENV="$(gb_api_env_path)"
[ -f "$API_ENV" ] || gb_die "API env file missing: $API_ENV (copy .gb_api.env.example and fill it)"
gb_load_api_env

if [ "$HARNESS" = codex ]; then
  MODEL="${GB_CODEX_MODEL:-gpt-5.6-sol}"
else
  MODEL="${GB_CLAUDE_MODEL:-claude-opus-5}"
fi
# Same route rules as run_benchmark.sh --provider auto (see gb_env.sh).
gb_resolve_agent_route "$HARNESS" auto
BASE_URL="$GB_ROUTE_BASE_URL"
KEY_ENV="$GB_ROUTE_KEY_ENV"

TOOL="$(command -v "$HARNESS" 2>/dev/null || true)"
# --sandbox docker runs the CLI from the sandbox image, so a missing or
# wrapper-shadowed host CLI is irrelevant there.  sandbox=none still runs on the
# host, so it keeps both checks.
if [ "$AGENT_SANDBOX" != docker ]; then
  [ -n "$TOOL" ] || gb_die "$HARNESS CLI is missing; run ./setup.sh"
fi
if [ "$AGENT_SANDBOX" != docker ] && [ "$HARNESS" = claude ] && [ "$TOOL" = /usr/local/bin/claude ]; then
  gb_die "/usr/local/bin/claude is the reference host's broken proxy wrapper; set GB_TOOLS_BIN=/opt/gb_live0904/bin or run ./setup.sh to install /opt/gamebench/bin/claude"
fi
KEY_VALUE="${!KEY_ENV:-}"
ROUTE_ARGS=()
if [ "$GB_ROUTE_KIND" = openai ]; then
  # Codex native provider: OPENAI_API_KEY, else the ChatGPT login copied per cell.
  AUTH_FILE="${CODEX_AUTH_FILE:-$HOME/.codex/auth.json}"
  [ -n "$KEY_VALUE" ] || [ -f "$AUTH_FILE" ] ||
    gb_die "OPENAI_API_KEY is empty in $API_ENV and $AUTH_FILE does not exist (run \`codex login\` or fill OPENAI_API_KEY)"
  ROUTE_ARGS=(--agent-provider openai)
  [ -n "$KEY_VALUE" ] || ROUTE_ARGS+=(--agent-auth-file "$AUTH_FILE")
else
  [ -n "$KEY_VALUE" ] || gb_die "$KEY_ENV is unset or empty in $API_ENV (see .gb_api.env.example; ./setup.sh --check-auth)"
  case "$KEY_VALUE" in *REPLACE_ME*) gb_die "$KEY_ENV still contains REPLACE_ME in $API_ENV" ;; esac
  ROUTE_ARGS=(--agent-key-env "$KEY_ENV" --agent-base-url "$BASE_URL")
fi
[ -x "$GB_BENCH" ] || gb_die "bench is not executable: $GB_BENCH"
gb_require_venv
if [ "$MODE" = bugfix ]; then
  gb_ensure_reference_project "$GAME_ID"
else
  gb_ensure_reference_film "$GAME_ID"
fi

if [ "$MODE" = port ] && [ "$EVAL" = on ] && { [ -z "${UNITY_BIN:-}" ] || [ ! -x "${UNITY_BIN:-}" ]; }; then
  gb_note "warning: port evaluation is Unity-gated; UNITY_BIN is not a licensed executable"
fi

gb_python_env
CMD=(
  "$GB_PYTHON" "$GB_BENCH" run-task-matrix
  --out "$OUT" --game "$GAME_ID" --mode "$MODE"
  --agent-backend "$HARNESS" --model "$MODEL" "${ROUTE_ARGS[@]}"
  --agent-env-file "$API_ENV" --agent-effort high
  --agent-sandbox "$AGENT_SANDBOX" --eval "$EVAL"
  --engine auto --visual-judge none
)
[ -z "$BUDGET" ] || CMD+=(--agent-timeout "$BUDGET")
[ -z "$DOCKER_IMAGE" ] || CMD+=(--agent-docker-image "$DOCKER_IMAGE")

print_command() {
  printf 'command: setsid nohup'
  printf ' %q' "${CMD[@]}"
  printf ' >%q 2>&1 </dev/null\n' "$LOG"
}

if [ "$AGENT_SANDBOX" = docker ]; then
  printf 'agent environment: docker image %s (tools checked inside each live container)\n' "$DOCKER_IMAGE"
else
  printf 'validated: %s=%s, %s at %s\n' "$HARNESS" "$("$TOOL" --version 2>&1 | sed -n '1p')" "$KEY_ENV" "$API_ENV"
fi
if [ "$GB_ROUTE_KIND" = openai ]; then printf 'route: codex native openai provider\n'; else printf 'route: %s\n' "$BASE_URL"; fi
printf 'submission: %s\n' "$SUBMISSION"
print_command
if [ "$DRY_RUN" = 1 ]; then
  printf 'dry-run: nothing launched and no API request made\n'
  exit 0
fi

[ ! -e "$OUT" ] || gb_die "output already exists: $OUT"
mkdir -p "$(dirname "$OUT")"
setsid nohup "${CMD[@]}" >"$LOG" 2>&1 < /dev/null &
PID=$!
printf '%s\n' "$PID" >"$PIDFILE"
printf 'launched: pid=%s log=%s\n' "$PID" "$LOG"
printf 'artifacts (after agent exit): %s/{events.jsonl,prompt.md,usage.json,env.json}\n' "$OUT/runs/$GAME_ID/$MODE/agent"
