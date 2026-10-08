# Turnkey output contract

`run_benchmark.sh --out results/<run_id>` writes a self-contained run tree:

```text
results/<run_id>/
├── run.json
├── cells/
│   └── <game>__<mode>__<harness>__<model>/
│       ├── package/
│       ├── submission/
│       ├── agent/
│       │   ├── events.jsonl
│       │   ├── prompt.md
│       │   ├── usage.json
│       │   └── env.json
│       ├── evaluation/
│       │   ├── report.json
│       │   ├── report.md
│       │   └── card.json
│       └── films/
├── summary.csv
├── summary.json
└── leaderboard.md
```

The cell name is the four requested dimensions joined by `__`; `/` in a model
identifier is encoded as `_` so it remains one directory component. Mode
`bugfix` runs one cell per active case, so its cell names carry the case id as
a fifth component: `<game>__bugfix__<harness>__<model>__<case_id>`. Missing
optional artifacts do not make the tree invalid. In particular, `films/` is
created only when film capture is enabled, and agent artifacts can be absent
when a harness fails before it starts.

## Mode `port` cells

Mode `port` (Mode 5, Godot to Unity) runs one cell per game with the four-part
cell name and the same `summary.csv` columns (`case_id` empty). Its cells differ
in content, not layout:

- `submission/` is a Unity project, not a Godot one: `Assets/`, `Packages/`,
  `ProjectSettings/` (with `ProjectVersion.txt` = `6000.3.23f1`),
  `Assets/GameBenchmark/gb_interface.json`, `ops.json`, `BUILD.md`, and
  `collection.json`.
- `evaluation/unity_runtime/` is the evaluator-owned copy of the project that
  the Unity editor built, with `unity_build.log`, the Linux player, and the
  probe frames and MP4 from the witness, null, and hidden-route replays. It is
  present only when an editor was found; with no editor, wrong version, or no
  licence, `report.json` records `unity_build` as `inconclusive` and every
  runtime item as `inconclusive`, and `weighted_total` is `null`
  (`status=evaluation_incomplete`).
- `results/<run_id>/unity_preflight/editor.log` is the log of the licence probe
  `run_benchmark.sh` runs before launching agents; a live port run does not
  proceed without it.

## Paper terminology and serialized fields

| Paper / report term | Chinese term | Existing serialized field |
|---|---|---|
| Objective Behavioral Evaluation | 客观行为评测 | `ocard` |
| Perceptual Quality Assessment | 感知质量评审 | `scard` |

The field `ocard` denotes **Objective Behavioral Evaluation** in the paper;
`scard` denotes **Perceptual Quality Assessment**. Current serializers spell
these keys without underscores. No `o_card` / `s_card` aliases are introduced.

These fields belong to the source-conditioned evidence record: standalone
`bench evaluate` writes them in its `card.json`; `bench eval-task` embeds that
record in the `reproduction` item's `evidence.card` in `report.json` when the
reproduction stage runs. Its full record is in `reproduction/card.json`.
The top-level `evaluation/card.json` is instead the hierarchical `scorecard`
copied from `report.json`, as described below.

The names label two evidence domains, not new scoring categories. All existing
JSON keys, `ocard` / `scard` source kinds, `O1`–`O9` / `S1`–`S4` identifiers,
registry versions and weight fields remain unchanged. Stored
`*.scorecard.json` rescore artifacts also retain their schema. In particular,
`ranking_basis=objective_and_calibrated_perceptual` keeps its original spelling
and meaning; a display-name change does not make an uncalibrated reading eligible.

## `run.json`

`run.json` is `gamebench.turnkey.run.v1`. It records:

- `params`: game selection, mode, harness, model, provider, wall `budget`
  (seconds, or `null` when the default uncapped run was used), reasoning effort, playtest-kit arm, visual judge (`none`, `local`, or
  `vlm`), the requested bugfix `case_id` (`null` unless `--case-id` was
  given), and maximum concurrency.
- `evaluator_git_sha` and the evaluation `registry_version`.
- `params.reference_video`: input switch, `on` by default; `off` is permitted
  only for the Mode-1 (`brief`) no-reference-video ablation. It does not change
  the visual judge or submission demonstrations. Each cell's `matrix/run.json`
  and `package/manifest.json` also record `reference_video`; the existing
  `manifest.video.copied` records whether a movie was actually delivered
  (Mode 4 normally has no movie regardless of the default switch).
- `godot_version` and `harness_cli_versions` for Claude Code and Codex.
- UTC `started_at` and `ended_at` timestamps.

Credentials, credential values, proxy values, and operator home paths are not
part of this file.

Keep the two Mode-1 input arms in separate run directories and retain `run.json`
with their summaries. Existing CSV fields and cell names do not change; compare
the two runs by game, not by pooling them into one model leaderboard.

## Summary files

`summary.json` is `gamebench.turnkey.summary.v1` with a `rows` array.
`summary.csv` contains the same rows and columns:

`game`, `mode`, `case_id`, `harness`, `model`, `kit`, `budget_s`, `wall_s`, `turns`, `cost_usd`,
`tokens_in`, `tokens_out`, `weighted_total`, `headline_ceiling`, `resolved`,
`failed_required_items`, `ranking_eligible`, `ranking_note`, `O1` through
`O9`, and `registry_version`.

`case_id` is the bugfix case a cell ran (empty outside mode `bugfix`).
Score fields retain the names and values from `evaluation/report.json`.
`O1`–`O9` are the scorecard source `point` values whose `kind` is `ocard`;
an unreported or unobservable point is JSON `null` and an empty CSV field.
`failed_required_items` is a JSON array in JSON output and compact JSON text in
CSV. Token totals and cost come only from `agent/usage.json`; unavailable
usage is not estimated. `budget_s` is the hard wall cap the cell's agent ran
under (`agent/request.json` `timeout_s`): JSON `null` and an empty CSV field
when the run had no `--budget`, the default. `wall_s` is the measured agent
duration in seconds (`state.json` `durations_s.agent`), the number to compare
across harnesses now that cells are uncapped by default.

A cell whose package generation was refused (`state.json`
`status=package_blocked`) does not stop the run: it gets a row with the
identity columns only, `ranking_eligible=false`, and the refusal reason in
`ranking_note` as `package_blocked: <reason>`; every other field is empty.
`run_benchmark.sh` also lists such cells in `blocked.tsv`
(`game`, `case_id`, cell path, reason) and exits non-zero when any exist.

`leaderboard.md` groups by mode, then harness and model. It reports mean
weighted total and resolved rate. Means never mix modes. Only rows with both
`resolved=true` and `ranking_eligible=true` enter the ranked mean; excluded
rows remain visible in `summary.csv` and `summary.json`.

## Standalone evaluation

`evaluate.sh PACKAGE SUBMISSION --out CELL/evaluation` writes the identical
`evaluation/{report.json,report.md,card.json}` layout. `card.json` is the
scorecard object copied from `report.json`, not a separately scored result.
