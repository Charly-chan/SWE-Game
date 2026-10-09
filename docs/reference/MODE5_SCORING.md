# Mode 5: evidence-adjusted scoring

Mode 5 has one execution profile: Community Docker. The agent receives a Unity
Editor/toolchain and a protected Unity scaffold. The independent evaluator
rebuilds the submitted project, executes the witness and hidden runtime suite,
collects controller-owned evidence, and computes the five-component report.
VLM calls are not required or used by this scoring protocol.

## Metric contract

The release metric is an **evidence-adjusted proxy**, not a claim of identical
execution conditions or measurements to the paper. Every report identifies
`2026-10.mode5-evidence-five-visual1`, `environment_class = community-docker`, and
`paper_compatible = false`. `official_total` remains `null`: the visual component
is an artifact proxy, not verified rendered similarity. A proxy leaderboard
must say so explicitly.

| Component | Raw points | Published subcriteria |
| --- | ---: | --- |
| Mechanics | 35 | Input mapping 6; core mechanics 12; interactions/state 10; outcomes/checkpoints/reset 7 |
| Playability | 25 | Valid non-idle ops 5; input chain 5; progression 7; final goal 5; pause/restart/scene flow 3 |
| Structure | 15 | Scenes/entities 5; referenced content 5; UI hierarchy/interactions 5 |
| Visual | 15 | Referenced assets 4; UI/feedback 3; render configuration 3; animation/audio 3; reference layout 2 |
| Stability | 10 | Packages/build configuration 4; lifecycle/load/reset 3; resource/exception safety 3 |

The task rubric is evaluator-owned and frozen before inspecting a submission.
It lists explicit obligations for **all twenty criteria**. Within a criterion,
each obligation has equal weight. Denominators cannot come from a submission's
claimed features, number of files, or number of passing observations.

For a criterion with weight `w` and `n` obligations:

```text
criterion_points = w × sum(evidence_coefficient × coverage) / n
component_points = sum(component's criterion_points), subject to caps
component_percent = 100 × component_points / component_weight
task_total = sum(component_points)
```

Equivalently, `task_total = .35 M + .25 P + .15 S + .15 V + .10 T`, where all
five component scores are percentages. The weights are applied **once**.

## Evidence and admissibility

| Level | Coefficient | Minimum evidence |
| --- | ---: | --- |
| `runtime_verified` | 1.00 | The independent runtime observer verifies the specific obligation; a causal claim requires its control |
| `editor_verified` | 0.80 | An independent Editor check verifies the specific property |
| `static_supported` | 0.60 | Traceable, reachable source/serialized/configuration evidence supports the specific property |
| `file_presence_only` | 0.25 | A required artifact exists, but use is not established |
| `failed` | 0.00 | Explicit contradiction, missing required evidence, or absent implementation |

All positive evidence must include controller audit references. These references
are pointers for inspection, not security signatures. The evaluator must keep
the rubric, verifier records and score output outside the candidate's writable
sandbox. Candidate-produced reports are never trusted verification inputs.

Select the strongest admissible observation for each obligation; repeated
copies do not add points. Explicit failure of the **same obligation** overrides
static support. Define separate artifact and behavioral obligations where they
are genuinely different: a valid input asset is not the same fact as input
reaching and affecting a player.

Compilation success alone does not verify gameplay, input causality, a goal,
scene flow, rendered appearance or runtime stability. A failed compile does not
erase independent static artifact facts. Missing runtime evidence never becomes
runtime credit and never vanishes from a denominator. A final goal only receives
credit with independent runtime evidence; otherwise that five-point subcriterion
is zero, not a global Playability zero.

Without admissible runtime verification, Mechanics is capped at `24.5/35` and
Playability at `15/25`. These are ceilings, not automatic awards. For example,
all static-supported evidence yields 21 Mechanics points, not 24.5.

### Structure and Visual

The static collector starts from validated entry/level scenes, follows active
serialized object/component and GUID references, and records attached authored
scripts and reachable assets. It excludes inactive/disabled components, orphan
scripts, disconnected assets, protected SDK files and baseline-identical files
from candidate-authored evidence. Duplicate GUIDs and inspection limits are
reported explicitly rather than silently accepted.

Reachability is necessary evidence of use, not proof of semantic fidelity.
Repeated resources, extra scenes, keyword-filled comments and unused assets
cannot increase a rubric denominator's coverage. The graph is deliberately
conservative about dynamically loaded resources and prefab overrides; use
independent Editor/runtime checks for those cases rather than declaring their
absence a candidate defect.

The Visual criteria use evaluator-owned `visual_implementation_correspondence`
measurements. Runtime captures are checked against frozen reference roles and
causal observations for assets, UI feedback, renderer/material output,
animation/audio and layout. Editor, static-supported and file-presence evidence
remain available with their stated coefficients, but they do not become runtime
credit merely because a build succeeds. This is an implementation-correspondence
measure, not a VLM or perceptual/aesthetic similarity score.

The implementation imposes no separate Visual ceiling. When the corresponding
runtime obligations are independently verified, Visual can reach its full 15-point
weight and the fixed-denominator proxy can reach 100/100. `official_total` remains
null and `paper_compatible` remains false.

### Stability and failures

Inspectable package/build settings can receive artifact credit. Editor builds
verify only the corresponding build obligations. Actual load/reset behavior
and absence of runtime errors require independently observed runs. Merely
finding `try`, `catch`, `null`, `Start` or `Update` in source earns no safety or
lifecycle credit.

Candidate compilation/runtime failure is local to dependent obligations.
Evaluator infrastructure failure makes the task unrankable (`total = null`),
while retaining earned proxy points as diagnostics. Integrity failure is a hard
gate, also unrankable; static scores never authorize executing an unsafe project.

## Model aggregation

Group reports by identical registry, task catalog, environment, harness, model,
provider, budget and input protocol. Reject duplicate game reports and mixed
registries. Missing/unrankable tasks do not disappear into an apparently
complete model average: a complete leaderboard score requires the full catalog.

```text
tail_count = ceil(0.25 × number_of_tasks)
tail_tasks = tasks with lowest task_total, ties ordered by game_id
model_score = all_task_mean
reliability_diagnostic = 0.70 × all_task_mean + 0.30 × tail_task_mean
```

For 41 tasks the tail contains 11. The headline and component scores use the
arithmetic task mean. Low-tail mean and 70/30 reliability are adjacent diagnostic
columns only: they never replace the ranking metric or select another pipeline.
The same low-tail task set is used for component diagnostics. Missing tasks
withhold the complete model mean; `partial_mean_score` is explicitly diagnostic.
No reproduction of historical paper numbers is asserted without rescoring the
original submissions under this frozen protocol.

## Implementation and verification

The Community evaluator, CLI, shared scorecard and Harbor adapter publish this
single registry. There is no selectable older Mode 5 scoring fallback.

The controller-facing scoring API lives in
`eval/evalsys/evalsys/taskgen/mode5/score.py`; conservative serialized
inspection lives in `evidence.py`. Observations must be generated by trusted
task-specific verifier adapters, not by candidate-authored dictionaries. These
modules are building blocks of the Community evaluator, not an alternative
execution command or a separately selectable scoring profile.

`adapter.py` takes a private pre-execution snapshot and subsequently combines
it with controller build/runtime verifier items. `report.py` exposes the
five-component shared scorecard, Markdown view and protocol-grouped model
summary. The adapter does not recover missing evidence by rereading mutable
candidate files after executing Unity.

Each observation also carries explicit `coverage` in `[0, 1]`. Ordinarily it is
one. For cold starts and error checks it is the fraction of **all scheduled
positive runs** independently verified, with failed runs retained. Its effective
credit is `coefficient × coverage`; adding more cold runs does not dilute the
separate reset/scene-flow obligations. A final goal remains binary. Runtime
evidence in Stability does not remove a non-runtime Mechanics/Playability cap.

Static source support follows attached scripts, Unity callbacks, explicit
static runtime-initialization attributes, serialized
events and explicit component construction. It excludes comments, string-only
claims, known disabled branches and uncalled helpers. Concrete numeric mutations,
role-conditioned destruction, Input Actions consumers, outcome calls and UI
assignments support only the matching obligations, at coefficient 0.60. This is
a bounded conservative inspector, not a complete C# compiler or proof of gameplay.
Indirect calls, complex dataflow, conditional compilation and procedural layout
without reference correspondence remain unsupported unless independently observed.

Core mechanics use the task's registered checks. Interaction credit uses only
state/overlap checks; progression uses progress/score, level or completion checks.
A health decrease does not become progression. Each admitted record identifies
the property it supports; a generic passed scenario cannot fill all three axes.
Animation/audio/particle and interactive UI requirements are frozen from trusted
reference material before candidate inspection. Candidate omissions do not shrink
the denominator. Procedural roles and nonempty native renderer components can be
confirmed by the independent runtime census; this is structure, not appearance.

Layout support compares nondegenerate semantic-role relative ordering, with at
least two roles and no reused reference layout. It is not pixel fidelity. A mere
scene index change does not verify pause/reset. Flow source support is discounted;
full runtime credit requires a dedicated causal verifier. Missing optional geometry
observations leave the dependent obligations unearned; actual transport/licensing
or inspection-budget failures withhold ranking, not pretend to be candidate zeros.

The dedicated tests cover evidence coefficients, caps, fixed denominators,
failure precedence, goal/visual admissibility, local failures, full-catalog
coverage, low-tail selection, component reconciliation, non-finite scores,
unattached/inactive components, fake GUID strings, scaffold exclusion,
duplicate GUIDs, path traversal and explicit collector exhaustion.
