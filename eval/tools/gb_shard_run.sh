#!/usr/bin/env bash
# gb_shard_run.sh — run one mode as N parallel per-game shards.
#
#   bash eval/tools/gb_shard_run.sh --mode bugfix --model MODEL \
#        --out results/bugfix [--jobs 8] [--concurrency 2]
#
# The benchmark runner generates packages serially before scheduling agents.
# Sharding by game allows those package-generation steps to run in parallel,
# which is especially useful for the 82 active Bug Repair cases.
#
# Each shard gets its own --out subdirectory: concurrent run_benchmark.sh
# invocations cannot share one, because they truncate the same queue.tsv,
# worker-status.tsv and scheduler.* paths. Merge afterwards with --merge.
#
# Peak Godot containers is jobs x concurrency. Mode 5 defaults to one job.
# Re-running the same command resumes shards with an existing run manifest.
set -uo pipefail

HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
cd "$HERE" || exit 2

MODE="" MODEL="" HARNESS=codex OUT="" JOBS="" CONCURRENCY="" SANDBOX="" EVAL=on
REASONING=high MERGE=0 ALLOW_PARTIAL=0 GAMES="" GAMES_SET=0
# An explicit env file overrides the standard provider configuration.
API_ENV="${GB_API_ENV:-}"
while [ $# -gt 0 ]; do
  case "$1" in
    --mode|--model|--harness|--out|--jobs|--concurrency|--sandbox|--eval|--reasoning|--games|--api-env)
      [ $# -ge 2 ] && [ -n "$2" ] || {
        printf 'error: %s requires a value\n' "$1" >&2; exit 2; } ;;
  esac
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
    --games) GAMES="${2:-}"; GAMES_SET=1; shift ;;
    --api-env) API_ENV="${2:-}"; shift ;;
    --merge) MERGE=1 ;;
    --allow-partial) ALLOW_PARTIAL=1 ;;
    -h|--help) sed -n '2,21p' "$0"; exit 0 ;;
    *) printf 'error: unknown option %s (try --help)\n' "$1" >&2; exit 2 ;;
  esac
  shift
done
[ -n "$MODE" ] && [ -n "$OUT" ] || { printf 'error: --mode and --out are required\n' >&2; exit 2; }
OUT="$(readlink -m "$OUT")"
PYTHON="${GB_PYTHON:-$HERE/.venv/bin/python3}"
[ -x "$PYTHON" ] || PYTHON=python3
case "$MODE" in brief|gdd|skeleton|bugfix|port) ;; *) printf 'error: invalid --mode\n' >&2; exit 2 ;; esac
case "$EVAL" in on|off) ;; *) printf 'error: invalid --eval\n' >&2; exit 2 ;; esac
if [ "$MODE" = port ] && [ "$EVAL" = off ]; then
  printf 'error: Mode 5 requires --eval on\n' >&2
  exit 2
fi
if [ -z "$JOBS" ]; then
  if [ "$MODE" = port ]; then JOBS=1; else JOBS=8; fi
fi
if [ -z "$CONCURRENCY" ]; then
  if [ "$MODE" = port ]; then CONCURRENCY=1; else CONCURRENCY=2; fi
fi
for value in "$JOBS" "$CONCURRENCY"; do
  [[ "$value" =~ ^[1-9][0-9]*$ ]] || {
    printf 'error: --jobs and --concurrency must be positive integers\n' >&2; exit 2; }
done
if [ -z "$SANDBOX" ]; then
  if [ "$MODE" = port ]; then SANDBOX=docker; else SANDBOX=unshare; fi
fi
case "$SANDBOX" in unshare|docker|none) ;; *) printf 'error: invalid --sandbox\n' >&2; exit 2 ;; esac
if [ "$MODE" = port ] && [ "$SANDBOX" != docker ]; then
  printf 'error: Mode 5 requires Community Docker\n' >&2; exit 2
fi
if [ "$MODE" = port ] && [ "$CONCURRENCY" != 1 ]; then
  printf 'error: Mode 5 runs one cell per game; --concurrency must be 1\n' >&2; exit 2
fi
if [ "$ALLOW_PARTIAL" = 1 ] && [ "$MERGE" != 1 ]; then
  printf 'error: --allow-partial applies only to --merge\n' >&2; exit 2
fi
if [ "$GAMES_SET" = 1 ] && [ -z "$GAMES" ]; then
  printf 'error: --games cannot be empty\n' >&2; exit 2
fi

shard_complete() {
  local shard="$OUT/shards/$1" cell count=0
  [ -f "$shard/run.json" ] && [ -d "$shard/cells" ] || return 1
  for cell in "$shard"/cells/*/; do
    [ -d "$cell" ] || continue
    [ -d "$cell/submission" ] || return 1
    if [ "$(cat "$OUT/shard-eval")" = on ]; then
      [ -f "$cell/evaluation/report.json" ] || return 1
    fi
    count=$((count+1))
  done
  [ "$count" -gt 0 ]
}

# ---- merge shards back into one results directory ----
if [ "$MERGE" = 1 ]; then
  [ -s "$OUT/expected-games.txt" ] && [ -f "$OUT/shard-status.tsv" ] &&
    [ -f "$OUT/shard-mode" ] && [ -f "$OUT/shard-eval" ] || {
      printf 'error: no shard-run record in %s\n' "$OUT" >&2; exit 2; }
  [ "$(cat "$OUT/shard-mode")" = "$MODE" ] || {
    printf 'error: recorded mode differs from %s\n' "$MODE" >&2; exit 2; }
  missing=0
  while IFS= read -r game; do
    rc="$(awk -F '\t' -v game="$game" '$1 == game { print $2 }' "$OUT/shard-status.tsv")"
    shard="$OUT/shards/$game"
    if [ "$rc" != 0 ] || ! shard_complete "$game"; then
      printf 'incomplete shard: %s (exit=%s)\n' "$game" "${rc:-missing}" >&2
      missing=$((missing+1))
    fi
  done < "$OUT/expected-games.txt"
  if [ "$missing" -gt 0 ] && [ "$ALLOW_PARTIAL" != 1 ]; then
    printf 'error: %d incomplete shard(s); use --allow-partial to merge available cells\n' "$missing" >&2
    exit 1
  fi
  merged="$OUT/merged"
  mkdir -p "$merged/cells" || exit 2
  # Refresh the merged view so an earlier successful run cannot leak stale cells.
  for target in "$merged"/cells/*; do
    if [ -L "$target" ]; then rm -- "$target" || exit 2
    elif [ -e "$target" ]; then
      printf 'error: merged cell is not a managed link: %s\n' "$target" >&2; exit 2
    fi
  done
  n=0
  included_games=()
  while IFS= read -r game; do
    rc="$(awk -F '\t' -v game="$game" '$1 == game { print $2 }' "$OUT/shard-status.tsv")"
    shard="$OUT/shards/$game"
    if [ "$rc" != 0 ] || ! shard_complete "$game"; then
      continue
    fi
    for cell in "$shard"/cells/*/; do
      [ -d "$cell" ] || continue
      target="$merged/cells/$(basename "$cell")"
      [ ! -e "$target" ] && [ ! -L "$target" ] || {
        printf 'error: merged cell name collision: %s\n' "$target" >&2; exit 2; }
      ln -s "$cell" "$target" || exit 2
      n=$((n+1))
    done
    included_games+=("$game")
  done < "$OUT/expected-games.txt"
  [ "$n" -gt 0 ] || { printf 'error: no complete cells to merge\n' >&2; exit 1; }
  # A merged result has several source runs; copying one shard's run.json
  # would falsely describe the entire result as a single-game run.
  "$PYTHON" - "$OUT" "$MODE" "$n" "${included_games[@]}" <<'PY' || exit 2
import json
import pathlib
import sys

root = pathlib.Path(sys.argv[1])
mode = sys.argv[2]
cell_count = int(sys.argv[3])
included = sys.argv[4:]
expected = (root / "expected-games.txt").read_text(encoding="utf-8").splitlines()
manifest = {
    "schema": "gamebench.shard-merge.v1",
    "mode": mode,
    "evaluation": (root / "shard-eval").read_text(encoding="utf-8").strip(),
    "expected_games": expected,
    "included_games": included,
    "incomplete_games": [game for game in expected if game not in included],
    "partial": len(included) != len(expected),
    "cell_count": cell_count,
    "shard_runs": {game: f"../shards/{game}/run.json" for game in included},
}
path = root / "merged/run.json"
temporary = path.with_suffix(".json.tmp")
temporary.write_text(json.dumps(manifest, indent=2) + "\n", encoding="utf-8")
temporary.replace(path)
PY
  printf 'merged %d cell(s), incomplete shards: %d -> %s\n' "$n" "$missing" "$merged"
  printf 'summarize with: %s eval/evalsys/bin/bench summarize-results --out %s\n' \
    "${GB_PYTHON:-.venv/bin/python3}" "$merged"
  exit 0
fi

[ -n "$MODEL" ] || { printf 'error: --model is required\n' >&2; exit 2; }
[ -z "$API_ENV" ] || [ -f "$API_ENV" ] || {
  printf 'error: api env file %s does not exist\n' "$API_ENV" >&2; exit 2; }
# Validate explicit selections against the active mode inventory as well.
available="$("$PYTHON" - "$MODE" <<'PY'
import sys
sys.path.insert(0, "eval/evalsys")
mode = sys.argv[1]
if mode == "bugfix":
    from evalsys.taskgen.mode4.mode4_cases import active_mode4_cases
    games = {c["game_id"] for c in active_mode4_cases()}
else:
    import json
    games = {g["id"] for g in json.load(open("catalog.json", encoding="utf-8"))}
print("\n".join(sorted(games)))
PY
)" || { printf 'error: could not enumerate games\n' >&2; exit 2; }
[ -n "$available" ] || { printf 'error: no active games in mode %s\n' "$MODE" >&2; exit 2; }
[ -n "$GAMES" ] || GAMES="$available"
set -f
set -- $GAMES
set +f
total=$#
[ "$total" -gt 0 ] || { printf 'error: empty game list\n' >&2; exit 2; }
declare -A seen=()
for game in "$@"; do
  [[ "$game" =~ ^[a-z0-9_]+$ ]] && [[ $'\n'"$available"$'\n' == *$'\n'"$game"$'\n'* ]] || {
    printf 'error: unknown game for %s: %s\n' "$MODE" "$game" >&2; exit 2; }
  [ -z "${seen[$game]:-}" ] || {
    printf 'error: duplicate game: %s\n' "$game" >&2; exit 2; }
  seen[$game]=1
done
if [ -s "$OUT/expected-games.txt" ] &&
   ! printf '%s\n' "$@" | cmp -s "$OUT/expected-games.txt" -; then
  printf 'error: game selection differs from the existing run; use a new --out\n' >&2
  exit 2
fi
# A new attempt invalidates the old merged manifest until its own merge passes.
if [ -e "$OUT/merged/run.json" ] || [ -L "$OUT/merged/run.json" ]; then
  rm -- "$OUT/merged/run.json" || exit 2
fi
mkdir -p "$OUT/shards" "$OUT/logs" || exit 2
printf '%s\n' "$MODE" > "$OUT/shard-mode" || exit 2
printf '%s\n' "$EVAL" > "$OUT/shard-eval" || exit 2
printf '%s\n' "$@" > "$OUT/expected-games.txt" || exit 2
: > "$OUT/shard-status.tsv" || exit 2
printf 'sharding mode=%s model=%s games=%d jobs=%d concurrency=%d eval=%s sandbox=%s\n' \
  "$MODE" "$MODEL" "$total" "$JOBS" "$CONCURRENCY" "$EVAL" "$SANDBOX"
if [ "$MODE" != port ] && [ "$EVAL" = on ]; then
  printf 'construction modes without VLM judging report objective readings only, not a complete weighted score\n'
fi
[ -z "$API_ENV" ] || export GB_API_ENV="$API_ENV"

declare -A pid_game=()
result=0 completed=0
finish_one() {
  local pid rc game
  if wait -n -p pid "${!pid_game[@]}"; then rc=0; else rc=$?; fi
  [ -n "${pid:-}" ] || { printf 'error: could not identify finished shard\n' >&2; return 1; }
  game="${pid_game[$pid]}"
  unset "pid_game[$pid]"
  if [ "$rc" -eq 0 ] && ! shard_complete "$game"; then
    printf 'warning: %s returned success without complete cells\n' "$game" >&2
    rc=1
  fi
  printf '%s\t%s\n' "$game" "$rc" >> "$OUT/shard-status.tsv"
  completed=$((completed+1))
  printf '[%d/%d] %s exit=%d\n' "$completed" "$total" "$game" "$rc"
  if [ "$rc" -eq 2 ]; then result=2
  elif [ "$rc" -ne 0 ] && [ "$result" -eq 0 ]; then result=1
  fi
}
for game in "$@"; do
  while [ "${#pid_game[@]}" -ge "$JOBS" ]; do finish_one || exit 1; done
  (
    resume_args=()
    [ ! -f "$OUT/shards/$game/run.json" ] || resume_args=(--resume)
    ./run_benchmark.sh --game "$game" --mode "$MODE" --harness "$HARNESS" --model "$MODEL" \
      --reasoning "$REASONING" --sandbox "$SANDBOX" --eval "$EVAL" \
      --concurrency "$CONCURRENCY" --out "$OUT/shards/$game" "${resume_args[@]}" \
      >"$OUT/logs/$game.log" 2>&1
  ) &
  pid_game[$!]="$game"
done
while [ "${#pid_game[@]}" -gt 0 ]; do finish_one || exit 1; done
printf 'all %d shards finished; status=%d\n' "$total" "$result"
printf 'merge with: bash eval/tools/gb_shard_run.sh --mode %s --out %s --merge\n' "$MODE" "$OUT"
exit "$result"
