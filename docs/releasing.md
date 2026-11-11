# Benchmark version 1.0.0

Released October 9, 2026. This version provides the fixed task catalog, automated
downloads, coding-agent runners, submission evaluator, and score reports.

## Release inventory

| Item | v1.0.0 |
| --- | --- |
| Reference games | 41 |
| Paper task inventory | 247: 41 Brief, 41 GDD, 41 Skeleton, 83 Bug Repair, 41 Porting |
| Downloadable task inventory | 246: the current manifest contains 82 of the paper's 83 Bug Repair cases |
| Fixed package variants | 451, covering the supported input settings |
| Reference recordings | 41 |
| Godot | 4.5.1, Linux x86-64 |
| Unity | 6000.3.23f1, StandaloneLinux64, Mono, Community Docker |
| Project-owned code and documentation | [Apache-2.0](../LICENSE) |

The fixed catalog, artifact paths, and SHA-256 checksums are in
[`data/task-data.json`](../data/task-data.json). The runner fetches the required
packages automatically. See [data downloads](reference-data.md) and
[third-party notices](../THIRD_PARTY_NOTICES.md) for component attribution.

## Scoring configuration

- Brief, GDD, and Skeleton use `2026-09-19.modeN-vlm1`, with 85 objective points
  and 15 VLM points. Enable `--visual-judge vlm` for a complete score.
- Brief Design scoring defaults to on, matching the paper. It includes GDD
  quality and interface declarations; the GDD submission is required in both
  settings. `--brief-design off` is an ablation; objective weights sum to 85 in
  either configuration. Rescoring preserves the saved setting unless overridden.
- Visual rubrics use `2026-10-08.demonstrated-quality-v4`. Applicable items earn
  credit according to what the supplied gameplay evidence demonstrates.
- Bug Repair uses `2026-09-15.mode4-redesign1` for restoration and preservation.
- Porting uses `2026-10.mode5-evidence-five-visual1`, with fixed
  Mechanics/Playability/Structure/Visual/Stability weights of 35/25/15/15/10.
  The Visual component measures implementation correspondence using runtime
  captures and discounted Editor/static evidence. This non-VLM measure assesses
  implementation, rather than perceptual or aesthetic similarity. The complete
  model score is the arithmetic mean over all 41 games.

Full definitions are in the [scorecard specification](reference/HIERARCHICAL_MULTI_EVIDENCE_SCORECARD.md)
and [Mode 5 scoring contract](reference/MODE5_SCORING.md).

## Published results

The README reproduces Table 3 of the accompanying manuscript, including the
Brief-to-Game Design component and its contribution to Total. For Brief runs,
use `--brief-design on --visual-judge vlm` to select the paper's scoring components.
The component weights are transcribed from Table 9 in the
[scoring specification](reference/HIERARCHICAL_MULTI_EVIDENCE_SCORECARD.md#modes-13).
The result set and downloadable task inventory are versioned as follows:

| Result set or inventory | Total tasks | Bug Repair cases | Scoring configuration |
| --- | ---: | ---: | --- |
| Paper Table 3 | 247 | 83 | Paper §3.4 and §4.1 |
| Downloadable v1.0.0 task catalog | 246 | 82 | Current branch defaults include Brief Design and the Community porting protocol |

The benchmark description follows the paper's 247 tasks and 83 repair cases.
The pinned downloadable manifest currently provides 82 repair cases. The remaining
paper case has not been identified in this snapshot, so its ID, archive, checksums,
and experimental outputs cannot be supplied here. Do not treat a run over the 82
available cases as an exact reproduction of the paper's 83-case repair result.

Each paper result pairs a model with its own development framework and tools
(§4.1). Godot 4.5.1 and Unity Editor 6000.3.23f1 match Appendix E. The paper's
construction score includes 85 objective points and 15 VLM points; repair multiplies
the four factors within each case before averaging. Porting uses the five
35/25/15/15/10 components and an arithmetic task mean. Turning Design or the
reference video off changes the configuration relative to the main paper table.

New runs produce results under their recorded configuration. Compare model
scores using the same task inventory, evaluator, scoring registry, rubric,
judge model, environment, input settings, and budget. The published paper table
remains an attributed experimental result; it is not a new measurement of this
release's defaults.

## Certified replay correction

Earlier evaluator versions could reject long certified segment replays
before gameplay starts. Publication replaced route provenance with `withheld`,
but replay preflight still depended on the original `E` label to bypass the
online proposal budget. At the fixed 21.6 ms/frame setting, that budget permits
40 operations. This rejected 101 of the 192 certified scored routes across 36
of 41 games; none of the 41 L5 whole-game routes hit this operation-limit bug.
The registry and certificate hashes matched because the release process updated
them after the metadata projection. Hash agreement did not test replay behavior.

The correction makes fixed segment replay use its declared operation limit
regardless of provenance. Whole-game replay keeps its existing frame-bounded
behavior. Frame limits, action validation, wall deadlines, and online proposal
budgets remain in force. The existing public task files and dataset revision
remain usable with the corrected evaluator.

For affected runs, retain the original submissions and reports, update the
evaluator, and rerun objective evaluation into a new output directory. Inspect
route readings for `stop_reason: plan_refused` with `over_op_cap`. Recomputing a
scorecard from those readings cannot recover gameplay that never ran, and the
coding agent does not need to regenerate the submission.

Such refused replays are evaluator gaps. The default construction scorecards
mark the affected axes as incomplete, withhold the composite, and set
`ranking_eligible: false`. Their zero-valued diagnostic contributions are not
measured submission failures. Keep incomplete reports out of model comparisons
until the missing measurements have been collected.

## Run provenance and verification

Preserve the evaluator commit or release tag, pinned dataset revision,
agent/model version, scoring and judge configuration, input settings, budgets,
original submissions, and evaluation reports with each benchmark run.
Build the Godot image locally and record its image ID. Unity setup records the
local agent and evaluator image IDs in its private `image-lock.json`. Preserve
the lock and selected image IDs with the run record, excluding license material.

`python scripts/check_release.py --require-license` checks repository structure,
documentation links, task inventory, and license presence. Downloads verify
the pinned SHA-256 checksums. The local container smoke test checks installed tools
and renders a Godot frame. Unity `doctor` checks the user's licensed environment,
build, and runtime before a porting run.
