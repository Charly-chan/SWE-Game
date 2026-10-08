#!/usr/bin/env bash
# Play your own game the way the evaluator will: cold import, then replay
# ops.json with the stock driver at --fixed-fps 60, then one verdict line.
#
#   playtest/playtest.sh [SUBMISSION_DIR] [OPS_JSON]
#
# Defaults: SUBMISSION_DIR=submission, OPS_JSON=<submission>/ops.json.
# Output goes to $PLAYTEST_OUT (default ./playtest_out): import.log, replay.log
# and a scratch copy of the project (the submission is never written to).
#
# The verdict line also reports `levels_visited=k/n` (declared gb_levels.json
# levels the replay entered) and `health_decreased=yes|no` when gb_levels.json
# declares `numeric.health` (gb_playtest_health.gd is registered as an autoload
# in the scratch copy only and reads that property where the evaluator does).
#
# Reaching a success ending is NOT the evaluator's only criterion: run
# check.sh for the public conformance rules, null_control.sh for the matched
# control, film.sh + watch.md for what is actually on screen.
#
# Exit codes: 0 the replay finished at a declared success ending;
#             1 it finished elsewhere (not a clearing tape yet);
#             2 the import or replay itself broke (REPLAY_ERROR, timeout, crash).
set -u
. "$(dirname "$0")/lib.sh"

SUB=${1:-submission}
[ -d "$SUB" ] || pt_die "submission directory not found: $SUB"
PROJECT=$(pt_project_root "$SUB")
OPS=$(pt_ops_file "$SUB" "${2:-}")
OPS=$(cd "$(dirname "$OPS")" && pwd)/$(basename "$OPS")
GODOT=$(pt_godot)
PY=$(pt_python)
pt_require_driver
mkdir -p "$PT_OUT"
SCRATCH="$PT_OUT/scratch"

pt_note "project=$PROJECT ops=$OPS godot=$GODOT out=$PT_OUT"
TAPE_FRAMES=$("$PY" "$PT_TAPE" frames "$OPS") || pt_die "could not read $OPS"
pt_note "tape declares $TAPE_FRAMES frames (~$((TAPE_FRAMES / 60)) s of play at 60 fps)"

pt_scratch_copy "$PROJECT" "$SCRATCH"
TRACE_ARGS=()
[ -z "${PLAYTEST_WATCH:-}" ] || TRACE_ARGS+=(--watch "$PLAYTEST_WATCH")
"$PY" "$PT_KIT_DIR/trace_feedback.py" prepare "$SCRATCH" "${TRACE_ARGS[@]}" || pt_die "could not prepare runtime observations"
pt_add_autoload "$SCRATCH" GBPlaytestTrace gb_playtest_trace.gd
if ! pt_cold_import "$GODOT" "$SCRATCH" "$PT_OUT/import.log"; then
    pt_warn "cold import exited non-zero; see $PT_OUT/import.log (continuing to the replay)"
fi

RC=0
pt_replay "$GODOT" "$SCRATCH" "$OPS" "$PT_OUT/replay.log" || RC=$?
pt_print_replay_lines "$PT_OUT/replay.log"
"$PY" "$PT_KIT_DIR/trace_feedback.py" report "$SCRATCH" "$OPS" "$PT_OUT/replay.log" "$PT_OUT" || pt_warn "runtime feedback could not be written; inspect replay.log"
"$PY" "$PT_TAPE" details "$PT_OUT/replay.log" "$SCRATCH"
VERDICT=$("$PY" "$PT_TAPE" summary "$PT_OUT/replay.log" "$SCRATCH" PLAYTEST_VERDICT)
printf '%s\n' "$VERDICT"

case "$VERDICT" in
    *"levels_visited=-"*)
        pt_warn "gb_levels.json declares no res:// levels; the evaluator's witness has nothing to launch" ;;
    *"levels_visited="*/*)
        LV=${VERDICT##*levels_visited=}; LV=${LV%% *}
        if [ "${LV%%/*}" != "${LV##*/}" ]; then
            pt_warn "the tape entered ${LV%%/*} of the ${LV##*/} declared scene entries (see PLAYTEST_LEVELS missed=); check task progression separately when multiple stages run within one scene"
        fi
        ;;
esac
case "$VERDICT" in
    *"health_decreased=unresolved"*)
        pt_warn "numeric.health is declared but no int/float property of that name was readable on an autoload, the current scene root, a gb_* node or its run/state/run_state/game holder; the evaluator reads it the same way and will find nothing" ;;
    *"health_decreased=no"*)
        pt_note "health never decreased during the tape; replay a relevant interaction and inspect contact events and health state to determine why" ;;
esac

case "$VERDICT" in
    *"finished=no"*)
        if [ "$RC" = "124" ]; then
            pt_warn "replay hit the $PT_STEP_TIMEOUT s cap before REPLAY_FINAL; shorten the tape or raise PLAYTEST_STEP_TIMEOUT"
        else
            pt_warn "no REPLAY_FINAL line (godot rc=$RC); read $PT_OUT/replay.log"
        fi
        exit 2
        ;;
    *"reached_success_ending=yes"*)
        pt_warn "reaching an ending is NOT the evaluator's only criterion: it also applies the public conformance rules (run playtest/check.sh), a matched no-input control (null_control.sh), harness-detection scans and mechanic traces; a green verdict here is necessary, not sufficient"
        pt_note "next: playtest/check.sh, then null_control.sh and film.sh"
        exit 0
        ;;
    *)
        pt_note "the tape does not end in a declared success ending; this is not a clearing route yet"
        exit 1
        ;;
esac
