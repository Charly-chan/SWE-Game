#!/usr/bin/env bash
# gb_shard_run.sh — run one mode as N parallel per-game shards.
#
#   bash eval/tools/gb_shard_run.sh --mode bugfix --model gpt-5.6-luna \
#        --out results/bugfix_luna [--jobs 8] [--concurrency 2]
#
# Why this exists: run_benchmark.sh generates every cell's package *serially*
# before it launches the agent scheduler, and --concurrency only governs the
# scheduler. For Mode 4 that generation is the dominant cost -- each case runs a
# differential preflight that replays the clean and mutated projects under Godot
# (~11 min measured), so 130 cases is ~23 h of serial work no amount of extra
# containers can shorten. Sharding by game puts that generation in N processes.
#
# Each shard gets its own --out subdirectory: concurrent run_benchmark.sh
# invocations cannot share one, because they truncate the same queue.tsv,
# worker-status.tsv and scheduler.* paths. Merge afterwards with --merge.
#
# Peak containers is jobs x concurrency. Re-running the same command resumes:
# every shard passes --resume, so finished cells are skipped.
set -uo pipefail

HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
cd "$HERE" || exit 2

MODE="" MODEL="" HARNESS=codex OUT="" JOBS=8 CONCURRENCY=2 SANDBOX=docker EVAL=off
REASONING=high MERGE=0 GAMES=""
# Without this each shard falls back to the default (empty) api env file, and
# gb_resolve_agent_route then picks Codex's native openai provider -- which the
# docker sandbox refuses outright, so every cell would fail on a bad route.
API_ENV="${GB_API_ENV:-$HOME/.config/gamebench/gateway.env}"
while [ $# -gt 0 ]; do
  case "$1" in
    --mode) MODE="${2:-}"; shift ;;
    --model) MODEL="${2:-}"; shift ;;
    --harness) HARNESS="${2:-}"; shift ;;
    --out) OUT="${2:-}"; shift ;;
    --jobs) JOBS="${2:-}"; shift ;;
    --concurrency) CONCURRENCY="${2:-}"; shift ;;
    --sandbox) SANDBOX="${2:-}"; shift ;;
    --eval) EVAL="${2:-}"; shift ;;
    --reasoning) REASONING="${2:-}"; shift ;;
    --games) GAMES="${2:-}"; shift ;;
    --api-env) API_ENV="${2:-}"; shift ;;
    --merge) MERGE=1 ;;
    -h|--help) sed -n '2,20p' "$0"; exit 0 ;;
    *) printf 'error: unknown option %s (try --help)\n' "$1" >&2; exit 2 ;;
  esac
  shift
done
[ -n "$MODE" ] && [ -n "$OUT" ] || { printf 'error: --mode and --out are required\n' >&2; exit 2; }
OUT="$(readlink -m "$OUT")"

# ---- merge shards back into one results directory ----
if [ "$MERGE" = 1 ]; then
  merged="$OUT/merged"
  mkdir -p "$merged/cells"
  n=0
  for shard in "$OUT"/shards/*/; do
    [ -d "$shard/cells" ] || continue
    for cell in "$shard"cells/*/; do
      [ -d "$cell" ] || continue
      target="$merged/cells/$(basename "$cell")"
      [ -e "$target" ] || { mv "$cell" "$target"; n=$((n+1)); }
    done
    # run.json of the first shard describes the arm; they are identical but for --game
    [ -f "$merged/run.json" ] || cp "$shard/run.json" "$merged/run.json" 2>/dev/null || true
  done
  printf 'merged %d cell(s) -> %s\n' "$n" "$merged"
  printf 'summarize with: %s eval/evalsys/bin/bench summarize-results --out %s\n' \
    "${GB_PYTHON:-.venv/bin/python3}" "$merged"
  exit 0
fi

[ -n "$MODEL" ] || { printf 'error: --model is required\n' >&2; exit 2; }
if [ -z "$GAMES" ]; then
  # Only games that actually have a cell in this mode; run_benchmark.sh exits 2
  # on a game with no active bundle, which would look like a shard failure.
  GAMES="$(.venv/bin/python3 - "$MODE" <<'PY'
import sys
sys.path.insert(0, "eval/evalsys")
mode = sys.argv[1]
if mode == "bugfix":
    from evalsys.taskgen.mode4.mode4_cases import active_mode4_cases
    print(" ".join(sorted({c["game_id"] for c in active_mode4_cases()})))
else:
    # catalog.json is a flat list of game records, the same shape
    # run_benchmark.sh reads at its --game all expansion.
    import json
    print(" ".join(sorted(g["id"] for g in json.load(open("catalog.json")))))
PY
)" || { printf 'error: could not enumerate games\n' >&2; exit 2; }
fi
set -- $GAMES
total=$#
mkdir -p "$OUT/shards" "$OUT/logs"
printf 'sharding mode=%s model=%s games=%d jobs=%d concurrency=%d (peak containers %d)\n' \
  "$MODE" "$MODEL" "$total" "$JOBS" "$CONCURRENCY" "$((JOBS * CONCURRENCY))"
printf 'out=%s\napi-env=%s\n' "$OUT" "$API_ENV"
[ -f "$API_ENV" ] || { printf 'error: api env file %s does not exist\n' "$API_ENV" >&2; exit 2; }

running=0 done_n=0 failed=""
for game in $GAMES; do
  while [ "$running" -ge "$JOBS" ]; do wait -n 2>/dev/null || true; running=$((running-1)); done
  (
    GB_API_ENV="$API_ENV" \
    ./run_benchmark.sh --game "$game" --mode "$MODE" --harness "$HARNESS" --model "$MODEL" \
      --reasoning "$REASONING" --sandbox "$SANDBOX" --eval "$EVAL" \
      --concurrency "$CONCURRENCY" --out "$OUT/shards/$game" --resume \
      >"$OUT/logs/$game.log" 2>&1
    # exit 1 just means some cell did not resolve; only 2 is a precondition failure
    rc=$?
    [ "$rc" -le 1 ] || printf '%s\trc=%s\n' "$game" "$rc" >>"$OUT/shard-failures.tsv"
  ) &
  running=$((running+1)); done_n=$((done_n+1))
  printf '[%d/%d] launched %s\n' "$done_n" "$total" "$game"
done
wait
printf '\nall shards finished\n'
[ -f "$OUT/shard-failures.tsv" ] && { printf 'precondition failures:\n'; cat "$OUT/shard-failures.tsv"; }
cells="$(find "$OUT/shards" -maxdepth 2 -name cells -type d -exec sh -c 'ls -1 "$1" | wc -l' _ {} \; 2>/dev/null | paste -sd+ | bc 2>/dev/null || echo 0)"
printf 'cells produced: %s\n' "$cells"
printf 'merge with: bash eval/tools/gb_shard_run.sh --mode %s --out %s --merge\n' "$MODE" "$OUT"
