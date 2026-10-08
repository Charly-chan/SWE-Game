#!/usr/bin/env bash
# Matched no-input control: the same tape with every input removed.
#
#   playtest/null_control.sh [SUBMISSION_DIR] [OPS_JSON]
#
# pt_tape.py rewrites each op of ops.json into a `wait` with the same frame
# budget (same op count, same horizon), so the control lasts exactly as long
# as your tape did.  The evaluator runs the same kind of matched control; if a
# game reaches its success ending with no input, the submitted tape did not
# cause the clear.
#
# Exit codes: 0 the control did NOT reach a success ending (good);
#             1 the game "won" by itself (fix this before submitting);
#             2 the replay itself broke.
set -u
. "$(dirname "$0")/lib.sh"

SUB=${1:-submission}
[ -d "$SUB" ] || pt_die "submission directory not found: $SUB"
PROJECT=$(pt_project_root "$SUB")
OPS=$(pt_ops_file "$SUB" "${2:-}")
GODOT=$(pt_godot)
PY=$(pt_python)
pt_require_driver
mkdir -p "$PT_OUT"
SCRATCH="$PT_OUT/scratch"
CONTROL="$PT_OUT/null_ops.json"

FRAMES=$("$PY" "$PT_TAPE" null "$OPS" "$CONTROL") || pt_die "could not read $OPS"
pt_note "control tape written to $CONTROL ($FRAMES frames, no actions)"

if [ ! -f "$SCRATCH/project.godot" ]; then
    pt_scratch_copy "$PROJECT" "$SCRATCH"
    pt_cold_import "$GODOT" "$SCRATCH" "$PT_OUT/import.log" || true
fi

RC=0
pt_replay "$GODOT" "$SCRATCH" "$CONTROL" "$PT_OUT/null_control.log" || RC=$?
pt_print_replay_lines "$PT_OUT/null_control.log"
VERDICT=$("$PY" "$PT_TAPE" summary "$PT_OUT/null_control.log" "$SCRATCH" NULL_CONTROL)
printf '%s\n' "$VERDICT"

case "$VERDICT" in
    *"finished=no"*)
        pt_warn "control did not reach REPLAY_FINAL (godot rc=$RC); read $PT_OUT/null_control.log"
        exit 2
        ;;
    *"reached_success_ending=yes"*)
        printf 'NULL_CONTROL self_win=yes\n'
        pt_warn "the game reaches a success ending with no input at all; the evaluator's matched control will too, and the tape proves nothing"
        exit 1
        ;;
    *)
        printf 'NULL_CONTROL self_win=no\n'
        exit 0
        ;;
esac
