#!/usr/bin/env bash
# Run all five task types for one model, one mode at a time, using game shards.
# Each mode and shard has a separate output directory to keep scheduler files
# and run metadata independent.
#
# Usage: bash run_five_modes.sh <harness> <model> <out-root> [jobs] [concurrency]
# Godot modes default to 3 jobs x 1 worker; Mode 5 defaults to one Unity job.
# Set GB_MODE5_JOBS explicitly to increase licensed Unity concurrency.
set -uo pipefail
HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
cd "$HERE" || exit 2

[ $# -ge 3 ] && [ $# -le 5 ] || {
  printf 'usage: bash run_five_modes.sh <codex|claude> <model> <out-root> [jobs] [concurrency]\n' >&2
  exit 2
}
HARNESS="$1"
MODEL="$2"
ROOT="$3"
[ -n "$MODEL" ] && [ -n "$ROOT" ] || {
  printf 'error: model and output root are required\n' >&2; exit 2; }
JOBS="${4:-3}"
CONC="${5:-1}"
MODE5_JOBS="${GB_MODE5_JOBS:-1}"
GODOT_SANDBOX="${GB_GODOT_SANDBOX:-unshare}"
case "$HARNESS" in codex|claude) ;; *)
  printf 'error: harness must be codex or claude\n' >&2; exit 2 ;;
esac
for value in "$JOBS" "$CONC" "$MODE5_JOBS"; do
  [[ "$value" =~ ^[1-9][0-9]*$ ]] || {
    printf 'error: jobs and concurrency must be positive integers\n' >&2; exit 2; }
done
case "$GODOT_SANDBOX" in unshare|docker|none) ;; *)
  printf 'error: GB_GODOT_SANDBOX must be unshare, docker, or none\n' >&2; exit 2 ;;
esac

mkdir -p "$ROOT" || exit 2
for mode in brief gdd skeleton bugfix port; do
  mode_jobs="$JOBS"
  mode_concurrency="$CONC"
  sandbox="$GODOT_SANDBOX"
  if [ "$mode" = port ]; then
    mode_jobs="$MODE5_JOBS"
    mode_concurrency=1
    sandbox=docker
  fi
  printf '\n===== %s | %s %s | %s =====\n' "$(date -u +%FT%TZ)" "$HARNESS" "$MODEL" "$mode"
  if bash eval/tools/gb_shard_run.sh --mode "$mode" --harness "$HARNESS" --model "$MODEL" \
    --out "$ROOT/$mode" --jobs "$mode_jobs" --concurrency "$mode_concurrency" --sandbox "$sandbox" --eval on; then
    :
  else
    rc=$?
    printf 'error: %s failed (exit %d); see %s/logs\n' "$mode" "$rc" "$ROOT/$mode" >&2
    exit "$rc"
  fi
  printf '%s done: %s\n' "$mode" "$(date -u +%FT%TZ)"
  if bash eval/tools/gb_shard_run.sh --mode "$mode" --out "$ROOT/$mode" --merge; then
    :
  else
    rc=$?
    printf 'error: %s merge failed (exit %d)\n' "$mode" "$rc" >&2
    exit "$rc"
  fi
done
printf '\nall five modes finished for %s\n' "$MODEL"
