# Play–Watch–Fix kit

Use this kit to cold-import your game, replay `ops.json`, inspect state and
images, and compare behavior after edits. It runs the stock replay driver
against your current submission and checks the public interface rules.

Requirements: `$GODOT_BIN` (or `godot` on PATH), `python3`, and — for filming
only — a display or `xvfb-run`. Every command is capped at 110 s so it fits
under the harness's 120 s child limit. Keep `playtest/` **out of**
`submission/`.

## The loop

```
build ──▶ play ──▶ watch ──▶ diff against GDD ──▶ fix ──▶ repeat
          │          │              │
   playtest.sh    film.sh       watch.md / watch.py
   null_control.sh
```

1. **Build.** Implement or change the game. Keep `gb_levels.json` beside
   `project.godot` and `ops.json` at the submission root.
2. **Play.** `playtest/playtest.sh submission` — cold import in a scratch copy,
   then the required replay (`--headless --fixed-fps 60 -s interface/replay_ops.gd`).
   Prints the `REPLAY_SCENE` / `REPLAY_WARNING` / `REPLAY_FINAL` lines, the
   `SCRIPT ERROR` count and one `PLAYTEST_VERDICT` line ending in
   `levels_visited=k/n` (declared `gb_levels.json` scene entries the tape
   actually entered; inspect stage state when progression happens within one scene) and
   `health_decreased=yes|no` (when `numeric.health` is declared; read where
   the evaluator reads it, by `gb_playtest_health.gd` in the scratch copy).
   A flat health reading needs contact and state evidence to explain its cause.
   Open `playtest_out/trace.md` after each replay. It lists observed state
   changes, held actions and press/release edges, links the properties to source locations, and
   shows state and code differences from the preceding playtest.
   Exit 0 only when the tape ends in a declared success ending — which is
   necessary, not sufficient (see `check.sh`). Then
   `playtest/null_control.sh submission` — the same tape with every input
   removed. `NULL_CONTROL self_win=yes` means the game clears itself; the
   evaluator's matched control will see the same and the tape proves nothing.
3. **Watch.** `playtest/film.sh submission` — films the replay (under
   `xvfb-run` when there is no display) into `playtest_out/sheet.png` (24
   evenly spaced frames) and `replay.mp4`. Open the sheet.
4. **Diff against your GDD.** Go through `playtest/watch.md`
   (`playtest/watch.py --gdd submission/GDD.md` fills in your mechanics): is
   each mechanic you wrote down visible? does every action give feedback? does
   progression show? is the ending shown?
5. **Fix.** Pick a concrete discrepancy against the public task and reference
   video. Use `trace.md` to locate the relevant code, make the improvement,
   then replay and inspect the state differences and images. Work on one
   discrepancy at a time and preserve existing working behavior. When a deadline
   is present, reserve its final 20 % for preparing the submission.

Use a short replay for a specific interaction and film it when the visible
result helps the next edit. Such a diagnostic replay need not reach an ending.

Two rules that are not optional:

- **When the run has a deadline, reserve time for the final submission.**
  The runner refreshes `playtest/.created_at` at each actual agent launch with
  that run's time limit. The default is `budget_s=unlimited`; package age does
  not impose a deadline. For a finite budget, spend at most 20 % on playtesting.
  When under 15 min remain, `film.sh` switches to shots and prints
  `FILM_BUDGET_GUARD remaining=<s> mode=shots`. A standalone run can set
  `PLAYTEST_BUDGET_S` to a number of seconds or `unlimited`.
- **Run `playtest/check.sh submission` before every submission**, not just
  the first one. A fix that passes the replay can still break the layout
  or the contract the evaluator reads.

## Commands

| command | what it does | exit 0 means |
|---|---|---|
| `check.sh [PKG] SUB` | public conformance rules → `CHECK_VERDICT` | no failures (`PKG` defaults to the directory holding `playtest/`) |
| `playtest.sh [SUB] [OPS]` | cold import → replay → verdict | replay finished at a declared success ending |
| `null_control.sh [SUB] [OPS]` | matched no-input tape → verdict | control did **not** reach a success ending |
| `film.sh [SUB] [OPS]` | replay under a display → `sheet.png`, `replay.mp4` | filmed and the film reached the success ending (3 = skipped: no display) |
| `watch.py --gdd GDD.md [--vlm]` | checklist with your mechanics; `--vlm` asks a vision model when a key exists | ran |
| `python3 plan.py SUB PLAN OUT` | Run an authored state plan in a scratch copy; export normal inputs | every plan target was reached without script errors |

`SUB` defaults to `submission`; `OPS` to `SUB/ops.json`. The project may be
`SUB/project.godot` or `SUB/game/project.godot`. Outputs go to
`$PLAYTEST_OUT` (default `./playtest_out`): `scratch/` (imported copy),
`import.log`, `replay.log`, `null_ops.json`, `null_control.log`, `film.log`,
`sheet.png`, `replay.mp4` or `shots/`, `watch_checklist.md`, `trace.md`,
`trace.json`, and (after a repeated playtest) `trace.previous.json`.

`film.sh` imports a fresh copy of the current submission on every call. It
clears its previous images/video before recording, so a failed capture cannot
be mistaken for evidence of the latest code. The per-command 110-second limit
still applies even when the agent has no overall deadline.

The static checker follows the whole-game `ops.json` return option. It does
not validate a `demos.json` manifest; the replay and film commands can still
accept an individual operation tape as their second argument.

Environment knobs: `PLAYTEST_OUT`, `PLAYTEST_STEP_TIMEOUT` (110),
`PLAYTEST_FILM_MODE` (`auto|movie|shots`), `PLAYTEST_FILM_FRAMES` (film only a
prefix), `PLAYTEST_FILM_RES` (`640x360`; the MP4 retains the recorded resolution),
`PLAYTEST_MS_PER_FRAME` (9, budget estimate), `PLAYTEST_KEEP_AVI`,
`PLAYTEST_DRIVER`, `PLAYTEST_WATCH` (optional watch JSON).

## Runtime feedback and source references

Every `playtest.sh` replay observes declared numeric slots and common gameplay
state on scene roots, autoloads and player nodes. Observations run in the scratch
copy, at physics priority 1000. The report contains:

- `trace.md`: first/last values, ranges, change counts, held actions, press/release edges and
  property references in the corresponding scripts. References locate code
  to inspect; they are not a statement-level execution history.
  Runtime errors are grouped by message and call stack, with occurrence
  counts and the actual source lines. A recurring error therefore keeps its
  location visible instead of filling the summary with duplicate messages.
- `trace.json`: recorded changes, complete state summaries and observed source
  snapshots. After the next playtest, `trace.previous.json` holds the previous
  run so the report can show the code and state differences.
- Each `ops[index]` has its own before/after values, numeric ranges and change
  counts in `trace.json` → `operations`, including late operations after the
  individual-event sample fills. `trace.md` shows an operation table. Boot and
  the driver's final settling frames have separate windows. A newly observed
  object has no assumed before value.
- With a preceding trace in the same output directory, the report locates the
  first changed operation under the same input prefix. If an input changes at
  `ops[12]`, only operations before it are compared. Partial operation windows
  are excluded from that comparison. The first difference is a place to
  inspect, not a failure verdict; check the public requirement, input edges
  and game images before changing code or repairing the route.

To inspect other properties, create `watch.json` beside `submission/`:

```json
[
  {"script": "res://main.gd", "properties": ["cooldown", "run.score", "enemies.0.hp"]},
  {"node": "/root/World/Player", "properties": ["velocity.x", "grounded"]}
]
```

```sh
PLAYTEST_WATCH="$PWD/watch.json" playtest/playtest.sh submission
```

Paths traverse objects, dictionaries, array indices and vector components.
Scalar numbers, booleans and strings are recorded; ranges apply to numbers.
Add public task-specific values as needed. For each object/property in a scene,
the report preserves the initial value and the first 47 changes, plus the
first/last/range/change-count summary over every observed tick.
Missing values remain unobserved. A held action or a flat counter alone does
not establish whether the interaction actually reached its target.
Each event separates held `actions` from `just_pressed` and `just_released`.
Godot can report a one-frame tap's press edge after the action is no longer held.

For values that change every frame, inspect the corresponding operation window
in `trace.json`; use a short replay when you need individual change samples.
A run that stops before both
the observer and stock replay driver finish is marked incomplete; its partial
events and execution errors remain available.

Compare both the state feedback and the game images after editing. Keep the
same replay for a useful first comparison; when gameplay changes require a
new route, the report explicitly notes the different input tape.

## Generate inputs from a state plan

For a top-down 2D game, `plan.py` can approach live objects and issue inputs until
a declared state is reached. Write the plan from the public task and your own
source. The controller queues actions through the same stock driver; it never
assigns gameplay state or calls gameplay methods. Its output is ordinary input
data. Always replay that output against the unmodified submission:

```sh
python3 playtest/plan.py submission plan.json plan_out
playtest/playtest.sh submission plan_out/generated-ops.json
```

`OUT` must be a new directory. It retains the plan, generated inputs, full
observations, import/execution logs and scratch project. Keep a clearing tape
as `submission/ops.json` after inspecting the replay. `--godot` and `--driver`
override the usual `GODOT_BIN`/PATH and packaged driver locations.

`result.json` records the actual reading when each step finishes. For an input
condition, compare `actual` with `expected`; unreadable values have
`observed: false`. Movement readings include matching object counts, the actor
and destination positions, and the remaining distance. A failed step has the
same fields under `pending_observation`, so the next edit can use the observed
state instead of guessing from a timeout alone. Movement targets are observed
from scene entry: `target_history` distinguishes a never-observed target from
one that was present and later disappeared, even when no `skip_if` was authored.

An example for an interactive object removed when activated:

```json
{"steps": [
  {"name": "approach object", "kind": "move",
   "target": {"group": "gb_interactive"}, "distance": 40, "max_frames": 900},
  {"name": "activate object", "kind": "input", "actions": ["gb_action"],
   "repeat_every": 15, "until": {"vanished": {"group": "gb_interactive"}}}
]}
```

- `move` reads an actor (default `gb_player`) and the nearest matching target,
  then issues `gb_left/right/up/down` until within `distance`. Select objects
  by `group`, `node` (`@scene` means the current scene), or `script`; `where`
  filters scalar properties. A target's `property` can name a Vector2 path,
  such as `plots.0.pos`; `offset: [x, y]` changes the approach point.
- `input` holds `actions` until its predicate is true, or for a fixed `frames`.
  `repeat_every` instead emits a one-frame tap followed by released frames.
  A step's `max_frames` bounds input search (default 1800), not the game clock.
- Predicates support `read: {node/group/script, property}`, `count: {selector}`,
  and `sum: [read/count terms]`, with `cmp: eq/ge/le/gt/lt` and `value`.
  `all: [predicates]` combines conditions; `scene: "res://..."` checks a scene.
- `vanished: {selector}` requires an object to have been observed in the current
  scene before all matching objects disappear. An initially absent object does
  not satisfy it. `skip_if` applies a predicate before a step, useful when another
  action already achieved that target. Observations reset on scene changes.

The plan's goals and bindings are authored inputs, not inferred requirements.
Direct movement has no obstacle planner or platforming controller. A completed
plan establishes its own targets; inspect public requirements, replay outcomes
and images separately. Use explicit scene conditions for scene transitions.

## What works where

| environment | `playtest.sh` / `null_control.sh` | `film.sh` |
|---|---|---|
| no display, no `xvfb-run` | yes (headless) | **no** — exits 3. Godot renders nothing headless: `get_viewport().get_texture().get_image()` is null and `--write-movie` needs a window. |
| no display, `xvfb-run` installed (the matrix sandbox) | yes | yes, via `xvfb-run -a`; `movie` mode when `ffmpeg` exists, otherwise `shots` mode |
| `DISPLAY` set | yes | yes, directly |

Filming costs about 8–9 ms per frame at 640×360 (a 10 600-frame tape took
80 s); the MJPEG AVI is ~35 KB per frame and is deleted after `replay.mp4` is
encoded. Tapes longer than the budget are filmed as a `--quit-after` prefix and
the script says so; set `PLAYTEST_FILM_FRAMES` to choose the prefix, or film a
shorter tape that reaches an interesting point.

`shots` mode registers `gb_playtest_shots.gd` as an autoload **in the scratch
copy only**; it reads its settings from a JSON file, never from the command
line or environment, and saves 24 PNGs plus `sheet.png` itself. The health
tracker `gb_playtest_health.gd` is registered the same way by every script and
reads only `gb_levels.json`. Do not copy either into your project: a shipped
sampler is scanned like any other script.

## What this kit is not

It is not the evaluator. It carries no scoring rules, no hidden routes, and no
idea what the source game looked like. It replays the tape you give it with the stock
driver at the mandatory fixed rate and tells you what happened. A green
`PLAYTEST_VERDICT` is necessary for a clear, not sufficient: the evaluator also
checks the interface contract, scans for harness detection, traces mechanics,
and runs its own matched control.
