# Playtest kit

The optional kit lets a coding agent run its Godot game, inspect state changes
and images, and compare behavior after an edit. It is packaged as
`visible/playtest/` for Mode 1–3 (`brief`, `gdd`, `skeleton`). It is off by
default and uses the supported whole-game `ops.json` workflow.

Enable it with `--playtest-kit on` on `bench gen-task`,
`bench run-task-matrix`, or `run_benchmark.sh`.
`manifest.json.playtest_kit` records whether it was actually shipped.

Source: [playtest_kit](../../eval/evalsys/evalsys/taskgen/playtest_kit/).
The [packaged README](../../eval/evalsys/evalsys/taskgen/playtest_kit/README.md)
is the agent-facing command reference.

## Use

Run from the workspace containing `submission/`, `interface/`, and `playtest/`:

```sh
playtest/check.sh submission
playtest/playtest.sh submission
# Read playtest_out/trace.md and inspect the corresponding source.
playtest/null_control.sh submission
playtest/film.sh submission
# Open playtest_out/sheet.png or frames from playtest_out/replay.mp4.
```

Choose one concrete discrepancy with the task, edit the relevant code, then
replay and inspect the result. Short diagnostic tapes can target an interaction:

```sh
PLAYTEST_OUT="$PWD/diagnostics/gather" \
  playtest/playtest.sh submission diagnostics/gather.json
PLAYTEST_OUT="$PWD/diagnostics/gather" \
  playtest/film.sh submission diagnostics/gather.json
```

A diagnostic tape can finish in a level. The final clearing tape should reach
a declared success ending, and its matched no-input control should not.

## Commands and outputs

| Command | Output | Exit status |
|---|---|---|
| `check.sh SUB` | Public interface and `ops.json` checks; `CHECK_VERDICT` | 0 checks pass; 1 violations |
| `playtest.sh SUB [OPS]` | Cold import, stock replay at 60 fps, `PLAYTEST_VERDICT`, `trace.md`, `trace.json` | 0 success ending; 1 finished elsewhere; 2 replay did not finish |
| `null_control.sh SUB [OPS]` | Same operation count and frame budget with every operation changed to `wait` | 0 no self-win; 1 self-win; 2 broken |
| `film.sh SUB [OPS]` | Fresh recording, contact sheet, MP4 or PNG shots | 0 filmed to success; 1 no success ending; 2 broken; 3 no display |
| `watch.py --gdd SUB/GDD.md` | A checklist built from the authored mechanics | 0 |

Outputs go to `PLAYTEST_OUT`, default `./playtest_out`. Relative paths are
resolved before Godot changes its working directory. `playtest.sh` and
`film.sh` work on scratch copies, leaving the submitted project unchanged.
Every film starts from the current candidate and clears previous visual
outputs. The MP4 keeps the recorded resolution, rounded to even dimensions for
video encoding. `PLAYTEST_FILM_RES=1280x720` requests a larger capture.

## Runtime feedback

The report also separates the replay into operation windows. Each `ops[index]`
retains its before/after values, ranges and change counts over all observed
ticks, including late changes after the per-property event sample fills.
Boot and the final settling tail are separate windows. Across consecutive
playtests in the same output directory, `trace.md` identifies the first changed
window in the unchanged input prefix. It stops comparing at a changed input
and skips partial operation windows. This localizes an observed difference;
the task requirements and interaction evidence determine whether to edit the
game or its route.

Repeated execution errors are grouped by message and stack, with occurrence
counts and the corresponding source lines.

The observer reads common scalar gameplay fields and the numeric properties
declared in `gb_levels.json`. Its default targets are scene roots, autoloads,
and `gb_player` nodes, including their `run`, `state`, `run_state`, and `game`
holders. It runs at physics priority 1000 in the scratch project.

- `trace.md` shows first/last values, numeric ranges, change counts, held
  actions, press/release edges, and references to the observed properties in
  their scripts. One-frame taps can produce a press edge after release;
  these are recorded separately from the held state.
- `trace.json` keeps the events, state summaries, operation tape, watch
  configuration, and source snapshots.
- Reusing the same output directory moves the preceding report to
  `trace.previous.json`. The new report compares matching scene/node/property
  summaries and observed scripts, and states whether the input tape changed.
- A replay missing either the observer's end marker or the driver's
  `REPLAY_FINAL` is marked incomplete; available events and errors are retained.

For task-specific state, create a watch file outside `submission/`:

```json
[
  {"script": "res://main.gd", "properties": ["cooldown", "run.score", "enemies.0.hp"]},
  {"node": "/root/World/Player", "properties": ["velocity.x", "grounded"]}
]
```

```sh
PLAYTEST_WATCH="$PWD/watch.json" playtest/playtest.sh submission
```

Paths can traverse objects, dictionaries, array indices, and vector components.
Numbers, booleans and strings are observed; missing values remain unobserved.
The feedback requires no model API call. The existing `watch.py --vlm` option
is separate and requires explicitly configured provider credentials.

## State-driven input generation

`python3 playtest/plan.py submission plan.json plan_out` runs an authored plan
on a scratch copy and writes `plan_out/generated-ops.json`. The plan can approach
live 2D objects and issue normal inputs until a named state is reached. It also
handles targets achieved by an earlier action: a `vanished` condition requires
prior observation in the current scene, so an unobserved object is not counted
as successfully removed. The packaged README describes selectors and conditions.

The controller queues inputs through the stock driver and does not edit game
state. Replaying the exported tape with `playtest.sh` against the submission is
part of the workflow. The first movement controller supports top-down 2D motion;
goals, bindings and strategy come from the agent's public task and source.

## Time limits and environment

Python 3 and Godot are required. Filming additionally needs a display or
`xvfb-run`; movie encoding uses `ffmpeg`, with PNG shots as the fallback.

The runner refreshes `playtest/.created_at` at each actual Docker or
native/unshare agent launch. It records the real run timeout; an unlimited run
uses `budget_s=unlimited`. Package age does not impose a deadline.
`PLAYTEST_BUDGET_S` can override this in standalone use. With a finite deadline
and under 15 minutes remaining, filming switches from movies to shots.

`PLAYTEST_STEP_TIMEOUT` defaults to 110 seconds per command, independently of
the overall agent deadline. Long recordings may be prefixes; inspect the film
output for the captured frame count. The packaged README lists the remaining
capture settings.

## Limits and interpretation

- Source references locate property uses; they are not a statement-level
  execution history. Source and state differences help inspection but do not
  establish causality by themselves.
- For each object/property in a scene, individual events contain the initial
  value and first 47 changes. First/last values, numeric ranges and change
  counts continue across all observed ticks. Fast-changing positions can
  exhaust the event sample early; use focused tapes for such interactions.
- `levels_visited` counts scene entries. Games can have multiple stages in
  one scene. A flat health value needs interaction evidence to explain it.
  Import errors must be resolved before interpreting missing textures or state.
- The static checker expects whole-game `ops.json`. It does not validate a
  `demos.json` manifest; explicit tapes can still be replayed individually.
- The kit provides debugging evidence, not a benchmark score. This update has
  no completed before/after score comparison establishing a coding benefit.
