# Mode 5-v2: objective 70 + structure VLM 15 + MDVA VLM 15

普通使用者请先看 [Mode 5 评测规范](MODE5_EVALUATION.md) 和
[Mode 5 使用手册](MODE5_RUNBOOK.md)；本文保留 v2 域隔离的设计细节。

Registry: `2026-09-20.mode5-mdva-domain1`

## Purpose

Mode 5 used to mix evaluator capture and the VLM reading inside the 15-point
visual category, while static hard gates could prevent the visual reading from
running at all. This revision keeps two isolated VLM leaves: 15 points for
cross-engine structure and 15 points for game-rubric MDVA. The MDVA leaf keeps
the four M/D/V/A groups underneath it.

## Domains and gates

### Infrastructure gate

The certified Unity VM, Unity version/license, hidden-suite calibration and
evaluator integrity are infrastructure-owned. Failure produces
`infrastructure_inconclusive` and `score=null`; it is never a model zero.

### Objective domain (70 points)

The objective domain contains engineering and runtime evidence:

- mechanics and semantic fidelity: 35;
- playability and progression: 25;
- runtime stability and lifecycle: 10.

Static contract failures are reported and scored in this domain. They do not
silently suppress an independently valid visual capture. Only an unaddressable
Unity layout may prevent construction of a player; other static failures remain
candidate-owned objective failures while the evaluator may still attempt visual
capture.

### Cross-engine structure VLM domain (15 points)

This domain compares evaluator-owned Unity evidence with the canonical reference
sequence. It scores asset/content identity and scene progression. A complete
candidate witness that fails to produce a whole-game completion path is a
candidate-owned gate failure and scores this domain as zero without an API call.
Capture/provider/alignment failures remain retryable and never become zero.
When both full recordings exist, the evaluator samples the Godot reference and
Unity witness at the same normalized whole-run positions. Hidden-scenario frames
are not mixed into this progression comparison. Legacy results without a full
candidate film may fall back to explicitly labelled witness screenshots.

### MDVA VLM domain (15 points)

The VLM domain is one score leaf, `unity_vlm`, and is internally grouped as:

| Group | Meaning | Weight inside VLM |
|---|---|---:|
| M | mechanic presentation and readable action | 10% |
| D | design/content/layout presentation | 18% |
| V | visual fidelity, UI, feedback and atmosphere | 27% |
| A | audiovisual polish and overall finish | 45% |

The evaluator owns the capture. The judge receives candidate frames/video,
reference frames/video, frozen rubric clauses, task context and evaluator-owned
runtime coverage facts. Mechanics and success are never inferred from pixels;
an objective completion result may only resolve whether an exhausted scope is a
measured shortfall or missing evidence.

Both VLM leaves honor the same explicit provider environment. `responses`
(the default) uses `GAMEBENCH_VLM_MODEL`, `GAMEBENCH_VLM_EFFORT`,
`GAMEBENCH_VLM_BASE_URL` and `GAMEBENCH_VLM_KEY_ENV`; `anthropic` remains an
explicit alternative. A `VisualJudge none` run records reusable evidence but
does not contact either provider and leaves both VLM leaves unmeasured.

## Missing evidence rules

- Candidate has no usable visual output or visibly fails the visual rubric:
  VLM domain is a measured zero.
- Evaluator cannot capture, reference frames are unavailable, the provider fails,
  or the response contract is malformed: VLM is `inconclusive` / retryable.
- Objective evidence remains readable while a retryable VLM result is pending.
- VLM evidence does not change objective weights; objective evidence does not
  change the VLM denominator. No dynamic renormalisation is allowed.

## Report contract

Mode 5-v2 reports:

```yaml
objective_total:
  score: 0..70 | null
structure_vlm_total:
  score: 0..15 | null
mdva_vlm_total:
  score: 0..15 | null
vlm_total:
  score: 0..30 | null
  groups: {M: ..., D: ..., V: ..., A: ...}
weighted_total:
  score: 0..100 | null
```

`weighted_total.score` is present only when infrastructure, objective and VLM
evidence are complete. `objective_total.score` is retained during a retryable
VLM failure. A candidate-owned VLM zero is a real zero; an evaluator-owned VLM
failure is not.

## Implementation notes

- The default Mode 5 registry is now `2026-09-20.mode5-mdva-domain1`.
- The prior delivery-zero registry remains loadable for historical reports.
- Capture is evidence/gating, never a score by itself. `unity_structure_fidelity`
  and `unity_vlm` remain separate score leaves and failure domains.
- Provider calls are serialized with a cross-platform evaluator lock so the
  Windows host and Linux host use the same retry/checkpoint semantics.
- Static hard-gate orchestration only blocks an unaddressable Unity layout;
  non-layout static failures are retained as objective-domain failures while a
  legal evaluator capture may still be judged.
