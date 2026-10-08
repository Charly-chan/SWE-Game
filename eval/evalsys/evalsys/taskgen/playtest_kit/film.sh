#!/usr/bin/env bash
# Film the replay so you can WATCH your game, not just read its log.
#
#   playtest/film.sh [SUBMISSION_DIR] [OPS_JSON]
#
# Godot cannot produce pixels without a display: under --headless
# get_viewport().get_texture().get_image() is null and --write-movie refuses to
# start.  So this script needs either an active $DISPLAY or `xvfb-run` (present
# in the matrix sandbox).  With neither it exits 3 and says so.
#
# Modes (PLAYTEST_FILM_MODE=auto|movie|shots, default auto):
#   movie  Godot Movie Maker (--write-movie film.avi at --fixed-fps 60), then
#          ffmpeg samples 24 tiles into sheet.png and encodes replay.mp4.
#          Chosen automatically when ffmpeg is installed.
#   shots  No ffmpeg needed: the kit's gb_playtest_shots.gd is registered as an
#          autoload in the SCRATCH copy only and saves 24 evenly spaced PNGs
#          plus sheet.png itself, via get_viewport().get_texture().get_image().
#
# Time: about 8-9 ms per frame at 640x360 on the reference sandbox, so a
# 10 000-frame tape films in ~90 s.  Every child command is capped at
# $PLAYTEST_STEP_TIMEOUT (110 s); a longer tape is filmed as a prefix with
# --quit-after and the script says how many frames it covered.
#
# Outputs in $PLAYTEST_OUT (default ./playtest_out): film.log, sheet.png,
# replay.mp4 (movie mode), shots/ (shots mode).
#
# Exit codes: 0 filmed; 1 filmed but the replay did not end in a success
# ending; 2 the run broke; 3 skipped (no display and no xvfb-run).
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
MODE=${PLAYTEST_FILM_MODE:-auto}
RES=${PLAYTEST_FILM_RES:-640x360}
MS_PER_FRAME=${PLAYTEST_MS_PER_FRAME:-9}
TILES=${PLAYTEST_TILES:-24}
# A failed or skipped attempt must not leave the previous film looking current.
rm -f "$PT_OUT/sheet.png" "$PT_OUT/replay.mp4" "$PT_OUT/film.avi"
rm -rf "$PT_OUT/shots"

# --- display -----------------------------------------------------------------
RUNNER=()
if [ -n "${DISPLAY:-}" ]; then
    pt_note "using the active display $DISPLAY"
elif command -v xvfb-run >/dev/null 2>&1; then
    RUNNER=(xvfb-run -a -s "-screen 0 1280x720x24")
    pt_note "no DISPLAY; running Godot under xvfb-run"
else
    printf 'FILM_SKIPPED reason=no_display_and_no_xvfb\n'
    pt_note "Godot renders no pixels without a display (headless viewport textures are null)."
    pt_note "Install xvfb or run where DISPLAY is set; the headless replay in playtest.sh still applies."
    exit 3
fi

# --- mode --------------------------------------------------------------------
if [ "$MODE" = "auto" ]; then
    if command -v ffmpeg >/dev/null 2>&1; then MODE=movie; else MODE=shots; fi
fi
case "$MODE" in movie|shots) ;; *) pt_die "PLAYTEST_FILM_MODE must be auto, movie or shots";; esac
if [ "$MODE" = "movie" ] && ! command -v ffmpeg >/dev/null 2>&1; then
    pt_warn "ffmpeg not found; falling back to shots mode"
    MODE=shots
fi
# Movie mode costs a 90 s film plus two ffmpeg passes; with under 15 min of
# wall budget left that is time the submission itself needs.
FILM_MIN_MOVIE_REMAINING_S=${PLAYTEST_MIN_MOVIE_REMAINING_S:-900}
REMAINING_S=$(pt_remaining_s)
if [ "$REMAINING_S" != "unlimited" ] && [ "$REMAINING_S" -lt "$FILM_MIN_MOVIE_REMAINING_S" ]; then
    printf 'FILM_BUDGET_GUARD remaining=%s mode=shots\n' "$REMAINING_S"
    if [ "$MODE" = "movie" ]; then
        pt_warn "under $FILM_MIN_MOVIE_REMAINING_S s of wall budget remain; movie mode refused, using shots"
    fi
    MODE=shots
fi

# --- budget ------------------------------------------------------------------
TAPE_FRAMES=$("$PY" "$PT_TAPE" frames "$OPS") || pt_die "could not read $OPS"
EST_FRAMES=$((TAPE_FRAMES + 60))   # boot + settle + the driver's 30-frame tail
BUDGET_S=$((PT_STEP_TIMEOUT - 10))
MAX_FRAMES=$((BUDGET_S * 1000 / MS_PER_FRAME))
QUIT=()
FILM_FRAMES=$EST_FRAMES
if [ -n "${PLAYTEST_FILM_FRAMES:-}" ]; then
    FILM_FRAMES=$PLAYTEST_FILM_FRAMES
    QUIT=(--quit-after "$FILM_FRAMES")
    pt_note "filming the first $FILM_FRAMES frames (PLAYTEST_FILM_FRAMES)"
elif [ "$EST_FRAMES" -gt "$MAX_FRAMES" ]; then
    FILM_FRAMES=$MAX_FRAMES
    QUIT=(--quit-after "$FILM_FRAMES")
    pt_warn "tape is ~$EST_FRAMES frames; at ~$MS_PER_FRAME ms/frame that exceeds the $BUDGET_S s budget, so only the first $FILM_FRAMES frames are filmed (set PLAYTEST_FILM_FRAMES to choose, or film a shorter tape)"
else
    pt_note "estimated $EST_FRAMES frames (~$((EST_FRAMES * MS_PER_FRAME / 1000)) s to film)"
fi

# --- fresh candidate copy ----------------------------------------------------
# An agent may edit after playtest or film twice. Always film the current
# submission, including new assets and removals, rather than its last replay.
pt_scratch_copy "$PROJECT" "$SCRATCH"
pt_cold_import "$GODOT" "$SCRATCH" "$PT_OUT/import.log" || true

COMMON=(--path "$SCRATCH" --rendering-driver opengl3 --rendering-method gl_compatibility
        --audio-driver Dummy --resolution "$RES" --fixed-fps 60 "${QUIT[@]}")
RC=0
if [ "$MODE" = "movie" ]; then
    AVI="$PT_OUT/film.avi"
    rm -f "$AVI"
    pt_note "mode=movie: --write-movie $AVI"
    pt_timeout "${RUNNER[@]}" "$GODOT" "${COMMON[@]}" --write-movie "$AVI" \
        -s "$PT_DRIVER" -- "$OPS" >"$PT_OUT/film.log" 2>&1 || RC=$?
else
    SHOTS="$PT_OUT/shots"
    rm -rf "$SHOTS"
    # Sample frame 3 and then every EVERY frames so the last tile lands inside
    # the driver's 30-frame tail (or just before a --quit-after cut).
    if [ "${#QUIT[@]}" -gt 0 ]; then SPAN=$((FILM_FRAMES - 10)); else SPAN=$((TAPE_FRAMES + 25)); fi
    EVERY=$(( SPAN / (TILES - 1) )); [ "$EVERY" -lt 1 ] && EVERY=1
    printf '{"every": %d, "max_shots": %d, "out_dir": "%s", "tile_width": 320, "columns": 6}\n' \
        "$EVERY" "$TILES" "$SHOTS" >"$SCRATCH/gb_playtest_shots.json"
    pt_add_autoload "$SCRATCH" GBPlaytestShots gb_playtest_shots.gd
    pt_note "mode=shots: sampling every $EVERY frames into $SHOTS (autoload added to the scratch copy only)"
    pt_timeout "${RUNNER[@]}" "$GODOT" "${COMMON[@]}" \
        -s "$PT_DRIVER" -- "$OPS" >"$PT_OUT/film.log" 2>&1 || RC=$?
fi

pt_print_replay_lines "$PT_OUT/film.log"
grep -E '^PLAYTEST_SHOTS' "$PT_OUT/film.log" || true
if [ "$RC" = "124" ]; then
    pt_warn "Godot hit the $PT_STEP_TIMEOUT s cap; lower PLAYTEST_FILM_FRAMES or PLAYTEST_MS_PER_FRAME was optimistic"
fi

# --- post-process --------------------------------------------------------------
if [ "$MODE" = "movie" ]; then
    if [ ! -s "$AVI" ]; then
        pt_warn "no movie written (rc=$RC); read $PT_OUT/film.log"
        exit 2
    fi
    # Pick TILES frames spread from the first to the last recorded frame, so
    # the final tile shows how the run ended.
    RECORDED=$(grep -oE '^REPLAY_FINAL frame=[0-9]+' "$PT_OUT/film.log" | grep -oE '[0-9]+$' || true)
    [ -n "$RECORDED" ] || RECORDED=$FILM_FRAMES
    STRIDE=$(( (RECORDED - 1) / (TILES - 1) )); [ "$STRIDE" -lt 1 ] && STRIDE=1
    ffmpeg -hide_banner -loglevel error -y -i "$AVI" \
        -vf "select='not(mod(n\,$STRIDE))',scale=320:-1,tile=6x4" -vsync vfr \
        -frames:v 1 "$PT_OUT/sheet.png" \
        || pt_warn "contact sheet failed"
    # Preserve recorded detail when the caller requests a larger viewport.
    # yuv420p needs even dimensions; only round an odd edge down by one pixel.
    if ! ffmpeg -hide_banner -loglevel error -y -i "$AVI" -vf 'scale=trunc(iw/2)*2:trunc(ih/2)*2' -r 30 \
            -c:v libx264 -crf 28 -pix_fmt yuv420p -an "$PT_OUT/replay.mp4" 2>/dev/null; then
        ffmpeg -hide_banner -loglevel error -y -i "$AVI" -vf 'scale=trunc(iw/2)*2:trunc(ih/2)*2' -r 30 \
            -c:v mpeg4 -q:v 5 -an "$PT_OUT/replay.mp4" || pt_warn "mp4 encode failed; keeping the AVI"
    fi
    if [ -s "$PT_OUT/replay.mp4" ] && [ "${PLAYTEST_KEEP_AVI:-0}" != "1" ]; then
        rm -f "$AVI"
    fi
else
    if [ -s "$SHOTS/sheet.png" ]; then
        cp "$SHOTS/sheet.png" "$PT_OUT/sheet.png"
    else
        pt_warn "no shots were saved (rc=$RC); read $PT_OUT/film.log"
        exit 2
    fi
fi

VERDICT=$("$PY" "$PT_TAPE" summary "$PT_OUT/film.log" "$SCRATCH" FILM_VERDICT)
printf '%s\n' "$VERDICT"
printf 'FILM_OUTPUT mode=%s frames=%s sheet=%s' "$MODE" "$FILM_FRAMES" "$PT_OUT/sheet.png"
[ "$MODE" = "movie" ] && [ -s "$PT_OUT/replay.mp4" ] && printf ' mp4=%s' "$PT_OUT/replay.mp4"
[ "$MODE" = "shots" ] && printf ' shots=%s' "$SHOTS"
printf '\n'
pt_note "now open sheet.png and go through playtest/watch.md against your GDD"
case "$VERDICT" in
    *"reached_success_ending=yes"*) exit 0 ;;
    *) [ "${#QUIT[@]}" -gt 0 ] && pt_note "a prefix film does not reach the ending by construction"; exit 1 ;;
esac
