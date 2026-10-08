# GameBenchmark submission interface

Version: **2** (registered 2026-08-23). The machine authority is
[`contract.v2.json`](contract.v2.json); this document and the JSON Schema are generated.

<!-- BEGIN GENERATED SUBMISSION INTERFACE v2 -->
## Fixed vocabulary

- Required group: `gb_player`.
- Optional fixed groups: `gb_collectible`, `gb_goal`, `gb_hazard`, `gb_enemy`, `gb_checkpoint`, `gb_door`, `gb_interactive`.
- Required bound-and-read actions: `gb_left`, `gb_right`, `gb_up`, `gb_down`, `gb_jump`, `gb_action`, `gb_pause`, `gb_reset`.
- Success endings: `victory`, `success`, `win`, `complete`, `run_complete`.
- Failure endings: `defeat`, `failure`, `failed`, `loss`, `lose`.
- Numeric slots: `progress`, `health`, `timer`, `score`.
- Device kinds: `collectible`, `goal`, `hazard`, `enemy`, `checkpoint`, `door`, `interactive`, `pad`, `tile`, `movplat`, `falling_platform`, `spring`.

## Manifest fields

| field | shape | obligation | consumers |
|---|---|---|---|
| `levels` | array | global | O1, O3, O4, O5, O7, O8 |
| `endings` | object | global | O2, O3, O7 |
| `numeric` | object | optional | O2, O3 |
| `device_ids` | object | optional | O2, O3, O7 |
| `anchor_camera` | string | task-conditional: O9 | O2, O9 |
| `audio_buses` | array | optional | O2 |
| `level_clear` | object | optional | O2, O3, O7 |
| `level_entry` | object | optional | O2, O7 |
| `extended_actions` | array | optional | O7, O8 |
| `analog_axes` | array | optional | O7, O8 |

A success ending must resolve to a shipped scene. If a failure ending is declared, it must also
resolve to a shipped scene and remain distinguishable from success. Optional fields may be omitted
unless frozen task requirements make them conditional obligations; once declared, they must be valid.

`extended_actions` is how a game adds verbs beyond the eight canonical actions. A task may require
a minimum number of them, or specific ids when its design document names them; the requirement is
stated in the task's visible materials (statement, GDD, or skeleton task) and enforced by that
task's `rubric_interface` check. A mash that holds every declared extra from frame 0 must not win.

## `extended_actions` and `analog_axes` entries

Each `extended_actions` entry is exactly `{"id": ..., "why": ...}` and each `analog_axes` entry
exactly `{"id", "why", "min", "max", "steps"}`; no other keys (`label`, `doc`, `key`, ...) are
accepted, and an entry without `why` is not a declaration. The conformance loader applies these rules
to every entry, and a single rejected entry rejects the whole list, so every op in the tape that
names one of your extras is then refused as `unknown_action`:

- `id` matches `^[a-z][a-z0-9_]{1,31}$` and is unique across extras and axes.
- `id` is **not reserved**: not a canonical action name (`gb_left`, `gb_right`, `gb_up`, `gb_down`, `gb_jump`, `gb_action`, `gb_pause`, `gb_reset`), not `pause`, `reset`, `quit`, `exit`,
  and not under the prefixes `gb_*`, `ui_*` — `gb_*` is the evaluator's namespace and `ui_*` is
  Godot's. Declaring `gb_dash` or `gb_confirm` as an extra rejects the list.
- `why` is at least 8 characters and says why the six canonical verbs cannot express the verb.
- `id` is bound in `project.godot` InputMap to a real key or button (`keycode` / `physical_keycode`
  / `button_index`), not an empty `Object()` stub; an axis is bound to a real `InputEventJoypadMotion`.
- `id` is read as a string literal (`"id"` or `'id'`) in gameplay source (`*.gd` / `*.tscn`).

## `numeric` values

Each `numeric` value is a **bare property name** (`^[A-Za-z_]\w*$`), for example
`{"health": "health", "progress": "stage_index"}`. The route driver reads it verbatim with
`Object.get(<name>)` on every autoload, the current scene root, every `gb_*` group node, and any
`run` / `state` / `run_state` / `game` object one of those holds; an `int` or `float` found there
is the slot's reading. Not accepted, because the driver does not parse them: an owner prefix
(`gb_player.health`), a NodePath (`/root/GB:health`, `$Player:health`), or a scene address
(`res://main.tscn::Player:health`). `bench conformance` reports the recognised `form` and
`driver_resolves` per slot under `checks.numeric.values`; a witness reading reports per slot whether
it was read as `declared`, only through the driver's same-named `guess`, or `none`.

`level_clear.predicate` uses parsed evaluator syntax, not natural-language prose, node paths, or
GDScript. Legal function/arity pairs are `overlap(2)`, `collected_all(1)`, `count(1)`, `alive(1)`, `norm_delta(1)`, `norm_in(3)`, `numeric(1)`, `numeric_delta(1)`, `count_delta(1)`, `whole_game_clear(0)`, `levels_visited(0)`, `contact(2)`, `device_present(2)`, `standing_on(2)`, `device_norm_delta(3)`, `device_state(4)`, `device_numeric(3)`, `device_numeric_delta(3)`. Valid examples are
`overlap(gb_player, gb_goal)`, `collected_all(gb_collectible) and overlap(gb_player, gb_goal)`, `numeric(progress) >= 2`, `count_delta(gb_enemy) <= -8`. Omit the optional field when the success scene or ordinary `gb_goal`
transition already identifies clearing honestly.

## Information boundary

Addresses come from the submission; answers remain evaluator-owned. A submission must never
declare expected counts, expected composition, supplied-asset usage, route success, or gold values.
Excluded default obligations: `observe()`, `observe().kind`, `Agent Bridge`.
<!-- END GENERATED SUBMISSION INTERFACE v2 -->

## Evaluator-side self-check

These commands are available in the benchmark repository. They are not
guaranteed to be present inside an isolated agent task workspace.

```bash
bench conformance <project> --json
bench conformance <project> --task-id <id> --json
bench conformance <project> --task-id <id> --explain
bench conformance --schema
```
