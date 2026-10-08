#!/usr/bin/env bash
# Run all five task types for one model, one mode at a time, using game shards.
# Each mode and shard has a separate output directory to keep scheduler files
# and run metadata independent.
#
# Usage: bash run_five_modes.sh <harness> <model> <out-root> [jobs] [concurrency]
# Total agent concurrency is jobs * concurrency (default: 3 * 1).
# Choose these values to fit the provider's rate limits and available resources.
set -uo pipefail
HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
cd "$HERE" || exit 2

HARNESS="${1:?harness (claude|codex)}"
MODEL="${2:?model id}"
ROOT="${3:?output root}"
JOBS="${4:-3}"
CONC="${5:-1}"

mkdir -p "$ROOT"
for mode in brief gdd skeleton bugfix port; do
  printf '\n===== %s | %s %s | %s =====\n' "$(date -u +%FT%TZ)" "$HARNESS" "$MODEL" "$mode"
  bash eval/tools/gb_shard_run.sh --mode "$mode" --harness "$HARNESS" --model "$MODEL" \
    --out "$ROOT/$mode" --jobs "$JOBS" --concurrency "$CONC"
  printf '%s done: %s\n' "$mode" "$(date -u +%FT%TZ)"
  bash eval/tools/gb_shard_run.sh --mode "$mode" --out "$ROOT/$mode" --merge
done
printf '\nall five modes finished for %s\n' "$MODEL"
