# GameCraft-Bench source

Repository: https://github.com/FreedomIntelligence/gamecraft-bench
Revision: `a43347534374df9a0c1a6c001aa9380862783f6d`
Paper: https://arxiv.org/abs/2606.17861
License: Apache-2.0, reproduced in `LICENSE`.

The verifier and its import dependencies retain the original package name.
Harbor's agent harness, dashboard, tasks, and assets are not included.
Upstream has no NOTICE file at this revision; our attribution is in `NOTICE`.

## Changes from upstream

The two prompt fragments saying "Godot 2D game" in `verifier/judges/_common.py` and
`verifier/judges/openai_gpt.py` now say "game", to cover our 2D, 3D and Unity tasks.
Those files carry modification notices. Other Python files are unchanged.
The score scale, prompt's judging instructions, response parser, frame sampler,
frame selection and max/mean aggregators remain upstream implementations.

Our adapter in `evalsys/scard/task_visual.py` supplies evaluator-recorded video
and task-owned requirements. The upstream score_project/replay entry point is
not used: our engine driver records our existing ops format. Provider/capture
failure remains an evaluator gap in our report, not a game-quality zero.
No GT reference images or submitter descriptions enter the judge request.

Upstream defaults: GPT-5.5, chat completions, continuous 0–1 scores, 0.5-second
sampling, a deterministic window of at most 20 seconds, at most 40 frames per
judge request, and at most 10 demos. Task rubrics declare the demo/window caps.

Upstream's published stability study uses two task families and its preliminary
human comparison uses three, not all 140 tasks. They provide prior evidence,
not validation of our tasks, our wording adaptation, or another judge model.
