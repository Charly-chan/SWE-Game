# SWE-Game v1.0.0

Released October 9, 2026. This version provides the fixed task catalog, automated
downloads, coding-agent runners, submission evaluator, and score reports.

## Release inventory

| Item | v1.0.0 |
| --- | --- |
| Reference games | 41 |
| Tasks | 246: 41 Brief, 41 GDD, 41 Skeleton, 82 Bug Repair, 41 Porting |
| Fixed package variants | 451, covering the supported input settings |
| Reference recordings | 41 |
| Dataset | [Charly-chan/SWE-Game](https://huggingface.co/datasets/Charly-chan/SWE-Game) |
| Pinned dataset revision | `651906f7d922474b8e4754bacd6f7fba38cc0397` |
| Godot | 4.5.1, Linux x86-64 |
| Unity | 6000.3.23f1, StandaloneLinux64, Mono, Community Docker |
| Project-owned code and documentation | [Apache-2.0](../LICENSE) |

The fixed catalog, download locations, and SHA-256 checksums are in
[`data/task-data.json`](../data/task-data.json). The runner fetches the required
packages automatically. See [data downloads](reference-data.md) and
[third-party notices](../THIRD_PARTY_NOTICES.md) for component attribution.

## Scoring configuration

- Brief, GDD, and Skeleton use `2026-09-19.modeN-vlm1`, with 85 objective points
  and 15 VLM points. Enable `--visual-judge vlm` for a complete score.
- Brief Design scoring defaults to off. `--brief-design on` includes GDD
  quality and interface declarations; the GDD submission is required in both
  settings. Objective weights sum to 85 in either configuration.
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

The README preserves the published values from
[paper Table 3](https://arxiv.org/html/2609.33678v1#S4.T3).
The result set and downloadable task inventory are versioned as follows:

| Result set or inventory | Total tasks | Bug Repair cases | Scoring configuration |
| --- | ---: | ---: | --- |
| Paper Table 3 | 247 | 83 | [Paper §3.4](https://arxiv.org/html/2609.33678v1#S3.SS4) and [§4.1](https://arxiv.org/html/2609.33678v1#S4.SS1) |
| v1.0.0 task catalog | 246 | 82 | Release defaults above, including optional Brief Design and the Community porting protocol |

New runs produce results under their recorded configuration. Compare model
scores using the same task inventory, evaluator, scoring registry, rubric,
judge model, environment, input settings, and budget. The published paper table
remains an attributed experimental result; it is not a new measurement of this
release's defaults.

## Run provenance and verification

Preserve the evaluator commit or release tag, pinned dataset revision,
agent/model version, scoring and judge configuration, input settings, budgets,
original submissions, and evaluation reports with each benchmark run.
Container tags with a source revision support repeatable image selection;
Unity setup records the local image digests.

`python scripts/check_release.py --require-license` checks repository structure,
documentation links, task inventory, and license presence. Downloads verify
the pinned SHA-256 checksums. The container workflow checks installed tools
and renders a Godot frame. Unity `doctor` checks the user's licensed environment,
build, and runtime before a porting run.
