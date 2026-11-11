# Shared helpers for playtest.sh / null_control.sh / film.sh.  Sourced, not run.
#
# Everything here uses only what the package and the sandbox provide: the stock
# driver in ../interface/replay_ops.gd, $GODOT_BIN (or `godot` on PATH),
# python3, and GNU timeout when present.  Nothing talks to the evaluator.

pt_note() { printf 'PLAYTEST_NOTE %s\n' "$*"; }
pt_warn() { printf 'PLAYTEST_WARNING %s\n' "$*"; }
pt_die() { printf 'PLAYTEST_ERROR %s\n' "$*" >&2; exit 2; }

PT_KIT_DIR=$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)
PT_TAPE="$PT_KIT_DIR/pt_tape.py"
PT_DRIVER=${PLAYTEST_DRIVER:-"$PT_KIT_DIR/../interface/replay_ops.gd"}
PT_OUT=${PLAYTEST_OUT:-"$PWD/playtest_out"}
# Godot changes its working directory to the scratch project. Generated tapes
# and captures must keep referring to the caller's output directory.
case "$PT_OUT" in
    /*) ;;
    *) PT_OUT="$PWD/$PT_OUT" ;;
esac
# The harness caps every child command at 120 s; leave headroom.
PT_STEP_TIMEOUT=${PLAYTEST_STEP_TIMEOUT:-110}

pt_godot() {
    if [ -n "${GODOT_BIN:-}" ] && [ -x "$GODOT_BIN" ]; then
        printf '%s\n' "$GODOT_BIN"
    elif command -v godot >/dev/null 2>&1; then
        command -v godot
    else
        pt_die "no Godot binary: set GODOT_BIN or put godot on PATH"
    fi
}

pt_python() {
    if command -v python3 >/dev/null 2>&1; then
        printf 'python3\n'
    else
        pt_die "python3 is required (tape arithmetic and log summary)"
    fi
}

# Resolve the Godot project root inside a submission directory.
pt_project_root() {
    local sub=$1
    if [ -f "$sub/project.godot" ]; then
        printf '%s\n' "$sub"
    elif [ -f "$sub/game/project.godot" ]; then
        printf '%s\n' "$sub/game"
    else
        pt_die "no project.godot under $sub or $sub/game"
    fi
}

# Resolve ops.json: explicit argument, else beside project.godot, else at the
# submission root (the layout the evaluator accepts).
pt_ops_file() {
    local sub=$1 explicit=${2:-}
    if [ -n "$explicit" ]; then
        [ -f "$explicit" ] || pt_die "ops file not found: $explicit"
        printf '%s\n' "$explicit"
    elif [ -f "$sub/ops.json" ]; then
        printf '%s\n' "$sub/ops.json"
    elif [ -f "$sub/game/ops.json" ]; then
        printf '%s\n' "$sub/game/ops.json"
    else
        pt_die "no ops.json under $sub (pass it as the second argument)"
    fi
}

pt_require_driver() {
    [ -f "$PT_DRIVER" ] || pt_die "stock driver not found at $PT_DRIVER (set PLAYTEST_DRIVER)"
}

# --- wall-clock budget --------------------------------------------------------
# The runner stamps playtest/.created_at immediately before the agent starts.
# budget_s=unlimited means there is no overall deadline. A standalone kit also
# defaults to unlimited; directory/package age does not impose a deadline.
PT_CREATED_AT="$PT_KIT_DIR/.created_at"

pt_budget_s() {
    if [ -n "${PLAYTEST_BUDGET_S:-}" ]; then
        printf '%s\n' "$PLAYTEST_BUDGET_S"
        return
    fi
    local stamped=""
    if [ -f "$PT_CREATED_AT" ]; then
        stamped=$(head -n 1 "$PT_CREATED_AT" | grep -oE 'budget_s=([0-9]+|unlimited)' | cut -d= -f2 || true)
    fi
    printf '%s\n' "${stamped:-unlimited}"
}

pt_started_at() {
    local start=""
    if [ -f "$PT_CREATED_AT" ]; then
        start=$(head -n 1 "$PT_CREATED_AT" | grep -oE '^[0-9]+' || true)
    fi
    if [ -z "$start" ]; then
        start=$(stat -c %Y "$PT_KIT_DIR" 2>/dev/null || stat -f %m "$PT_KIT_DIR" 2>/dev/null || date +%s)
    fi
    printf '%s\n' "$start"
}

pt_elapsed_s() {
    local now start
    now=$(date +%s)
    start=$(pt_started_at)
    local elapsed=$((now - start))
    [ "$elapsed" -lt 0 ] && elapsed=0
    printf '%s\n' "$elapsed"
}

pt_remaining_s() {
    local budget
    budget=$(pt_budget_s)
    if [ "$budget" = "unlimited" ]; then
        printf 'unlimited\n'
        return
    fi
    local remaining=$(( budget - $(pt_elapsed_s) ))
    [ "$remaining" -lt 0 ] && remaining=0
    printf '%s\n' "$remaining"
}

# Run a command under a wall-clock cap when `timeout` exists.
pt_timeout() {
    if command -v timeout >/dev/null 2>&1; then
        timeout "$PT_STEP_TIMEOUT" "$@"
    else
        "$@"
    fi
}

# Fresh scratch copy of the project without .godot/, so the import is cold and
# the submission itself is never written to.
pt_scratch_copy() {
    local project=$1 scratch=$2
    rm -rf "$scratch"
    mkdir -p "$(dirname "$scratch")"
    cp -a "$project" "$scratch"
    rm -rf "$scratch/.godot"
    if [ ! -f "$scratch/gb_levels.json" ]; then
        # Collection moves a root-level manifest beside project.godot when the
        # project ships none of its own; mirror that so the replay sees it.
        local parent
        parent=$(dirname "$project")
        if [ -f "$parent/ops.json" ] && [ -f "$parent/gb_levels.json" ]; then
            cp "$parent/gb_levels.json" "$scratch/gb_levels.json"
            pt_note "copied root-level gb_levels.json beside project.godot (as collection does)"
        else
            pt_warn "no gb_levels.json beside project.godot: the evaluator cannot run its witness"
        fi
    fi
    pt_add_health_probe "$scratch"
}

# Register a kit script as an autoload in the SCRATCH copy's project.godot
# (never in the submission).  pt_add_autoload SCRATCH NAME FILE  copies
# $PT_KIT_DIR/FILE beside project.godot and adds NAME="*res://FILE".
pt_add_autoload() {
    local scratch=$1 name=$2 file=$3
    cp "$PT_KIT_DIR/$file" "$scratch/$file"
    "$(pt_python)" - "$scratch/project.godot" "$name" "$file" <<'PY'
import re, sys
path, name, file = sys.argv[1:4]
text = open(path, encoding="utf-8").read()
row = '%s="*res://%s"\n' % (name, file)
if file not in text:
    if re.search(r"^\[autoload\]\s*$", text, re.M):
        text = re.sub(r"^\[autoload\]\s*\n", "[autoload]\n\n" + row, text, count=1, flags=re.M)
    else:
        text = text.rstrip("\n") + "\n\n[autoload]\n\n" + row
    open(path, "w", encoding="utf-8").write(text)
PY
}

# The health tracker (gb_playtest_health.gd) prints PLAYTEST_HEALTH lines that
# pt_tape.py turns into health_decreased=; it needs the scratch copy only.
pt_add_health_probe() {
    pt_add_autoload "$1" GBPlaytestHealth gb_playtest_health.gd
}

# Cold import; prints the SCRIPT ERROR count, returns Godot's exit code.
pt_cold_import() {
    local godot=$1 scratch=$2 log=$3 rc=0
    pt_timeout "$godot" --headless --path "$scratch" --import >"$log" 2>&1 || rc=$?
    local errors
    errors=$(grep -c '^SCRIPT ERROR' "$log" || true)
    printf 'PLAYTEST_IMPORT rc=%s script_errors=%s log=%s\n' "$rc" "$errors" "$log"
    if [ "$errors" != "0" ]; then
        grep -A2 '^SCRIPT ERROR' "$log" | head -n 30
    fi
    return $rc
}

# Headless replay through the stock driver at the mandatory fixed rate.
pt_replay() {
    local godot=$1 scratch=$2 ops=$3 log=$4 rc=0
    shift 4
    pt_timeout "$godot" --headless --path "$scratch" --fixed-fps 60 "$@" \
        -s "$PT_DRIVER" -- "$ops" >"$log" 2>&1 || rc=$?
    return $rc
}

pt_print_replay_lines() {
    local log=$1
    grep -E '^(REPLAY_SCENE|REPLAY_WARNING|REPLAY_ERROR|REPLAY_FINAL)' "$log" | head -n 60 || true
    # First and last health readings only; the full series is in the log.
    local health
    health=$(grep -E '^PLAYTEST_HEALTH frame=' "$log" || true)
    if [ -n "$health" ]; then
        printf '%s\n' "$health" | head -n 1
        if [ "$(printf '%s\n' "$health" | wc -l)" -gt 1 ]; then
            printf '%s\n' "$health" | tail -n 1
        fi
    fi
    grep -E '^PLAYTEST_HEALTH (not declared|further)' "$log" || true
    local errors
    errors=$(grep -c '^SCRIPT ERROR' "$log" || true)
    if [ "$errors" != "0" ]; then
        printf 'PLAYTEST_SCRIPT_ERRORS %s (first shown below)\n' "$errors"
        grep -A2 '^SCRIPT ERROR' "$log" | head -n 30
    fi
}
