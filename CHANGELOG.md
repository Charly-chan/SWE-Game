# Release notes

## Unreleased

- Make Brief Design scoring optional, defaulting to off for new evaluations. Keep the objective/VLM split at 85/15, record the choice, and preserve historical settings when rescoring saved reports.

- Restore `2026-10-08.demonstrated-quality-v4` as the default visual scoring policy across all 41 game rubrics.

- Score demonstrated visual achievement directly under `2026-10-08.demonstrated-quality-v4`: entirely unshown applicable items receive 0, partial demonstrations earn their evidenced credit, and full credit requires every applicable condition to be demonstrated. Keep technical evaluation failures distinct from valid zero judgments.
- Apply lower direct partial-credit references and declared deficiency ceilings across all 41 game rubrics; retain game-specific requirements and group weights.
- Align visual group labels with the paper: visible mechanics, design and content, functional visual communication, and art.

## Fixed task release

This release contains 246 tasks over 41 games, with 451 fixed package variants
for the supported input settings. The evaluator reports objective evidence,
perceptual assessment, and strict completion separately. Record the evaluator
commit and pinned dataset revision with benchmark results.
