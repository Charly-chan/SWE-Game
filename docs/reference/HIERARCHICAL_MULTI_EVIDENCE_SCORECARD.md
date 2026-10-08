# Scoring specification

SWE-Game reports graded capability separately from strict task completion
(`resolved`). The implementation is
[`evalsys.taskgen.scorecard`](../../eval/evalsys/evalsys/taskgen/scorecard.py).
Record the evaluator commit, task package, registry, rubric, and judge configuration
with each result. Scores from different modes or versions have different meanings.

## Current registries

| Mode | Default registry | Composite |
|---|---|---|
| 1 · Brief | `2026-09-19.mode1-vlm1` | 85 objective + 15 game-specific VLM |
| 2 · GDD | `2026-09-19.mode2-vlm1` | 85 objective + 15 game-specific VLM |
| 3 · Skeleton | `2026-09-19.mode3-vlm1` | 85 objective + 15 game-specific VLM |
| 4 · Bugfix | `2026-09-15.mode4-redesign1` | Differential repair with integrity and regression factors |
| 5 · Port | `2026-09-20.mode5-mdva-domain1` | 70 objective + 15 structure VLM + 15 visual quality |

Visual judging is disabled by default. Modes 1–3 and 5 retain measured objective
scores when VLM evidence is missing; their complete composite remains `null`.
Mode 4 does not require a visual judge.

## Modes 1–3

Objective evidence covers execution, mechanisms, player actions, content, and
mode-specific obligations. Whole-game inputs and segmented demonstrations use
the same required-feature coverage denominator. Repeated demonstrations of one
feature do not add coverage. Requirements that were not demonstrated remain in
the denominator; evaluator failures are recorded as missing measurements.

The VLM uses evaluator-captured candidate gameplay, task-provided reference video,
asset examples, and the game's rubric. Its groups follow the paper: visible mechanics
(10%), design and content (18%), functional visual communication (27%),
and art (45%). Items receive direct continuous 0–1 scores
under the rubric’s full, partial, and zero-credit criteria and applicable caps.
The frozen scoring policy distinguishes missing core relationships from local
finishing deficiencies using their importance, scope, and severity. The
`2026-10-09.demonstrated-quality-v5` policy gives more credit to substantive
partial completion while retaining the declared deficiency ceilings. Every applicable item receives a
numeric score: entirely unshown achievement receives 0, partial demonstrations
earn their evidenced credit, and 1 requires every applicable full-credit condition
to be demonstrated. Only evidenced conditional inapplicability uses null. See the
[rubric scoring references](../../eval/taskgen/vlm_rubrics/README.md#demonstrated-visual-quality).
Item scores are averaged within each applicable group, including valid unshown
zeros; group means use the weights above, renormalized over applicable groups.
Missing recordings, provider failures and invalid judge responses withhold the
visual score. For construction tasks, the
objective points are normalized by 85 to obtain `O`; the rubric aggregate is
`V`, both in `[0, 1]`. The paper's composite is `0.85 O + 0.15 V`, reported
on a 0–100 scale.

Mode-specific rules account for the authored design in Brief tasks, specified
requirements in GDD tasks, and the supplied starting framework in Skeleton tasks.
The [evaluation guide](../evaluation.md) describes evidence collection and
[task protocol](../tasks.md) defines submission requirements.

## Mode 4

The current repair score is a product:

```text
100 × repair_credit × gates × regression_factor × regression_mean
```

The factors represent restored target behavior, required integrity gates, the
fraction of preserved routes that still pass, and graded regression evidence.
The same repair is counted once. An unchanged faulty project earns no repair
credit; regressions reduce the product. Missing required readings withhold the
score. Strict repair completion remains a separate verdict.

Repair inputs and submission requirements are described in the
[task protocol](../tasks.md).

## Mode 5

Objective evidence allocates 35 points to mechanisms and semantic consistency,
25 to playability and completion, and 10 to stability and lifecycle behavior.
Structure fidelity and visual quality each contribute 15 VLM points. The domains have
fixed denominators and report missing evidence independently.

An infrastructure failure yields an inconclusive result. A candidate failure
can yield a measured zero in the affected domain. Objective failure does not
prevent visual assessment when valid candidate frames are available. See the
[Mode 5 evaluation specification](MODE5_EVALUATION.md) and
[v2 domain contract](MODE5_V2_MDVA_DOMAIN.md) for gates, weights, and report fields.

## Reading reports

1. Check `resolved` and the failed or unmeasured required conditions.
2. Inspect the component scores and their underlying evidence.
3. Check measurement coverage, ranking eligibility, and composite status.
4. Compare scores only under the same task and evaluation configuration.

`weighted_total.score` is a point score when required evidence is complete.
Diagnostic intervals retain uncertainty for analysis; they are not composite
scores. A measured failure, missing evaluator evidence, and a task-inapplicable
criterion are distinct states. Their treatment follows the selected registry.
Ranking eligibility does not imply strict completion.

The [output contract](OUTPUT_CONTRACT.md) documents serialized fields.
`evaluate.sh --rescore` recomputes saved readings; it does not rerun probes or
collect missing visual evidence. Specify `--registry` to reproduce a historical
version. Evaluator behavior changes require a new evaluation of the retained
package and submission.

## Validation and historical protocols

The paper reports human agreement studies for behavioral and perceptual
evaluation in [Section 4.5](https://arxiv.org/html/2609.33678v1#S4.SS5).
Reproducing a score requires the recorded registry, rubric, judge model, and
video sampling configuration. Godot-to-Unity Porting also requires the certified
Unity environment. 