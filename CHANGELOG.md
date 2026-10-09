# Release notes

## Unreleased

- Make the sharded five-mode runner evaluate by default, propagate shard failures,
  and require an explicit choice to merge partial results. Mode 5 uses one Unity
  shard by default; construction-mode runs without VLM evidence are labeled as
  objective-only.
- Remove unused experimental navigation files and correct public Mode 5 command
  examples. Clarify the README result-table corrections made after `v1.0.0`.

## v1.0.0 — 2026-10-09

- Release 246 tasks over 41 games, with 451 fixed package variants for the supported input settings and automatic checksum-verified downloads.
- Provide Godot execution, Harbor orchestration, and the Unity Community Docker workflow with independent submission evaluation and retained evidence.
- Publish a Godot 4.5.1 image with Codex in `ghcr.io/charly-chan/swe-game-public`; local builds also support Claude Code and licensed Unity.
- License project-owned code and documentation under Apache-2.0; preserve third-party game, asset, and code notices.

- Make Brief Design scoring optional, defaulting to off for new evaluations. Keep the objective/VLM split at 85/15, record the choice, and preserve historical settings when rescoring saved reports.

- Restore `2026-10-08.demonstrated-quality-v4` as the default visual scoring policy across all 41 game rubrics.

- Score demonstrated visual achievement directly under `2026-10-08.demonstrated-quality-v4`: entirely unshown applicable items receive 0, partial demonstrations earn their evidenced credit, and full credit requires every applicable condition to be demonstrated. Keep technical evaluation failures distinct from valid zero judgments.
- Apply lower direct partial-credit references and declared deficiency ceilings across all 41 game rubrics; retain game-specific requirements and group weights.
- Align visual group labels with the paper: visible mechanics, design and content, functional visual communication, and art.

### Evaluation configuration

The evaluator reports objective evidence, perceptual assessment, and strict
completion separately. Record the evaluator commit and pinned dataset revision
with benchmark results. See [version information](docs/releasing.md) for the
release inventory, scoring defaults, and published result provenance.
