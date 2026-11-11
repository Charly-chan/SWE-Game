# evalsys internal contract

Read this before writing any module in `evalsys/`. It is the API every
subsystem scores through, plus the constraints that are not negotiable.

Package: `evalsys/`. Run from the repository root with
`PYTHONPATH=eval/evalsys`.

## The scoring kernel — `evalsys/verdict.py`

```python
from evalsys.verdict import (
    Verdict, Attribution, Item, Interval, Gate, Ladder,
    passed, failed, skipped, exempt, inconclusive, unmeasurable, unobservable,
    score_items, combine, dump, LOW_COVERAGE_THRESHOLD,
)
```

`Item(id, weight, verdict, credit, detail, attribution, evidence)`.

Verdicts and where they land:

| verdict | in denominator | lo | hi | means |
|---|:---:|---|---|---|
| `passed` | yes | credit | credit | measured, earned `credit` ∈ (0,1] |
| `failed` | yes | 0 | 0 | measured, earned nothing |
| `malformed` | yes | 0 | 0 | minimum precondition absent |
| `skipped` | **yes** | **0** | **full** | not measured, **submission's fault** |
| `exempt` | **yes** | **0** | **full** | written genre exemption (`genre_inapplicable`); interval width records it |
| `inconclusive` | no | — | — | not measured, **our fault** |
| `unmeasurable` | no | — | — | bridge failed certification |
| `unobservable` | no | — | — | tier/modality cannot convey it |
| `error` | — | — | — | **must** carry `attribution`; resolves on construction |

`exempt` vs `unobservable`: Genre exemptions stay in
the denominator. `unobservable` is the CONTRACT verdict for tier/modality gaps
and correctly leaves the denominator. Do not reuse it for genre exemptions —
that shrinks the denom and inflates `score_lo`.

`score_items(items) -> Interval(lo, hi, coverage, denominator, by_verdict)`.
`combine([(weight, interval), ...]) -> Interval` rolls one level up.

An empty denominator returns an `Interval` with `denominator == 0`. That is
**not** a zero score — never coerce it to 0.0.

`Ladder(channel, [Gate(id, credit, detail), ...])` with credits summing to 1.0.
`ladder.resolve({gate_id: (Verdict, detail)})` returns items and **propagates
blockage**: everything above the first failed rung is `failed`; everything
above the first unmeasured rung is `skipped`. A gate you do not report on is
`skipped`, never `passed`.

## Weights — `evalsys/weights.py`

`CHANNELS: dict[str, Channel]` with `.weight` (already includes the 0.90
O-card share), `.mode` (`"H"`/`"X"`/`"either"`), `.needs_bridge`,
`.never_unmeasurable`. `O1_LADDER`, `O2_LADDER`, `TIERS`, `MODALITIES`,
`S_CARD_SUBWEIGHTS`, `S_CARD_VLM_CHANNELS`, `S_CARD_HUMAN_ONLY`.

Do not invent weights. If you need one that is not registered, stop and say so.

## Assertions and ceilings — `evalsys/assertions.py`

`Assertion(id, channel, layer, inferable_from, weight, ...)`,
`AssertionSet.audit()`, `.ceiling(tier, modality)`, `write_ceilings(...)`.

## Non-negotiable constraints

These came out of real defects today. Violating one is a bug even if the tests pass.

1. **A skip must leave a mark in the score.** Never merge "skipped" into
   "passed", and never let a skipped sub-check silently disable a sibling
   check. Emit `skipped` items, in the denominator.

2. **Never believe the submission's self-reported `VERDICT`.** A bot printing
   `VERDICT: PASS` is self-report. Score from a harness-side manifest
   reconciliation: `manifest_ids − reported_ids`, and every missing id is
   `failed`, never absent. The known cheat path is making a check throw so it
   disappears from the denominator while the mode still prints PASS.

3. **Screenshots are produced by the evaluator, never by the submission.**
   The subject does not fill in its own report card, and that applies to
   pixels. Repo-wide the bridges have produced 907 JSON files and 0 PNGs.

4. **Pixels require X mode.** `xvfb-run -a -s "-screen 0 WxHx24"` plus
   `--rendering-driver opengl3`, or the GPU wrapper `/usr/local/bin/godot-gpu`.
   Never `--headless` for anything visual: it installs a dummy backend where
   `get_texture()` returns non-null with no pixels behind it, and
   `frame_post_draw` never fires, so anything that `await`s it hangs forever.
   Use a timer, not `await`, when probing for it. Self-check is the PNG:JSON
   ratio; zero means the visual evidence does not exist.

5. **No downsampling in any same/different comparison.** A 32x18 perceptual
   hash judged three distinct failure frames identical because the text
   vanished. Compare at native resolution, and score difference over the
   **bounding box of the non-flat region**, not the whole frame — a whole-frame
   denominator systematically penalises games that concentrate information in
   one panel.

6. **Budgets are declared in frames; wall clock is computed** from the
   measured per-frame cost of *that game* on *that level*. Measured spread is
   400x. GPU routing is ~16 ms/frame with 1.13x load variance; llvmpipe varies
   9.3x with machine load and has no upper bound (483.7 ms/frame observed on 2
   cores). Anything over ~1500 frames goes to the GPU — a gate needs an upper
   bound, not a best case.

7. **If a check can only be satisfied by making the artifact worse, the check
   is the defect.** Do not add checks whose cheapest fix is deleting content.

8. **Never modify a game project.** Copy to scratch first. Other agents are
   recording 22 of these projects right now. No `rm -rf`, no `pkill -f godot`.

9. **Any "施加 X 后观察到 Y" judgement subtracts the no-X baseline.** Idle
   baseline for liveness, null control for the VLM judge, `null_input_no_win`
   for clear certification, same-class-twice for failure distinguishability.

## Environment facts

- Godot 4.5.1 at `/opt/godot451-bin/godot` (symlink to `/opt/godot-4.5.1/...`,
  same file). `GODOT_BIN` env var overrides.
- 8x A100. GPU rendering works as of today; 8 concurrent per card,
  154 fps aggregate.
- **File timestamps are useless** — an external event appended a newline to
  every text file. Compare content, never mtimes.
- Python 3.10.12, numpy 2.2.6, Pillow 12.2.0 available.

## Style

Match the kernel: dataclasses, type hints, docstrings that say *why* (citing
the defect or design section), no comments that narrate what the next line
does. Write intermediate artifacts to disk as you go — sessions drop.
