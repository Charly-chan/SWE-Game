# Reading failures and missing evidence

Read a report at the item or scoring-axis level before interpreting its total.
`resolved`, `evaluation_incomplete`, `ranking_eligible`, and the numeric score
answer different questions. A numerical zero alone does not identify its cause.

| Evidence | Attribution | Interpretation |
| --- | --- | --- |
| Completed cold import fails on the submitted project | Candidate failure | Affected runtime axes receive zero; unrelated measurements keep their own status |
| A completed probe confirms every declared scene failed to load | Candidate failure | Dependent runtime measurements cannot succeed for this submission |
| Only one scene fails while other scenes run | Local candidate failure | Do not infer failure of every scene or unrelated probe |
| Required input tape is absent or invalid | Candidate contract failure | The affected input-dependent requirement fails; this does not prove every game mechanic is absent |
| Valid VLM judgment finds required achievement entirely unshown | Demonstrated-achievement score | Receives 0 with a concrete coverage explanation; this does not assert the implementation lacks the feature |
| Timeout, missing engine, failed recorder, or missing judge response | Unmeasured evidence | Inspect the recorded cause; do not automatically convert it into a model failure |
| Generation process exits abnormally before delivery | Generation failure, cause unresolved unless separately evidenced | Do not infer a game capability score from the exit code alone |
| Original submission cannot be located | Unknown source state | Absence from an archive is not proof the model produced nothing |

The evaluator recognizes scene-load errors only for the declared scene after a
completed probe. A matching log line during a timeout or launch failure is not
enough. The first scene's failure may explain a failed asset probe without
explaining other missing readings.

## Demonstrations and GDD coverage

Feature-demo manifests retain the frozen parser and acceptance rules. A malformed
segment can reject the manifest, including otherwise valid segments. This is a
submission-contract failure plus unexecuted evidence; it does not establish that
every feature in the project fails. Demo validity and accepted-demo coverage
receive candidate-failure zeros when required input is missing, invalid, or idle.
Valid inputs whose replay produced no measurement remain unmeasured, including
legacy tapes. A valid legacy input tape may still yield a
recording without satisfying all feature-demo obligations.

When `demo_coverage.expected` already includes a frozen GDD mechanic, that axis
owns the requirement. The GDD coverage table names this owner and does not award
the requirement a second time. Only accepted observations in the demonstration
summary establish coverage; raw observations from rejected segments do not.
Requirements absent from every declared denominator remain evaluator gaps.

## Mode 4 repair and Mode 5 port evidence

Mode 4 attributes a blocked replay to a broken candidate only when the task's
publication preflight passed, O1 independently confirmed a failed candidate cold
import, and the route scratch copied and injected successfully before a completed
import failed. Each affected reading must record that launch failure. Missing
routes, missing control pairs, import timeouts, and evaluator crashes remain
unmeasured. Target-restoration credit and regression weights are unchanged.

Mode 5 preserves explicitly measured partial credit for failed aggregate hidden
scenarios. For example, one successful scenario out of two earns half of that
leaf's weight. Stability coverage includes every scheduled cold run: an explicit
candidate launch failure earns zero, while an unmeasured evaluator run withholds
the leaf and headline until it can be retried.

A candidate build or probe failure does not override an independently recorded
judge, capture, or infrastructure failure. The host finalizer treats absent media
as a candidate zero only when a candidate build/probe failure accounts for it.
Failing the auto-win control alone does not explain missing video; neither does a
lost recording after capture was reported successful.

## Scores and reproducibility

Some historical fixed-weight registries serialize unmeasured axes as named zeros
and list them in `weighted_total.unmeasured`. Those zeros are not measured
candidate failures. The current VLM registries leave the composite score null
when required evidence is incomplete; inspect `objective_total` and the detailed
axis statuses separately. Policy-imputed zeros in an exported table must remain
distinguishable from observations.

An evaluator-owned rubric that failed its publication audit cannot establish
interface coverage. Its score remains unmeasured. When objective probes and the
visual judge both lack evidence, the retry plan lists both domains.

Report both the evaluator commit and registry version. Attribution bug fixes may
change results without changing weights. `evaluate.sh --rescore` recomputes a
score from saved observations; it cannot recover a missing probe, video, or judge
response. Preserve the original report before writing an updated scorecard.

See the [evaluation overview](evaluation.md) and
[output contract](reference/OUTPUT_CONTRACT.md) for the complete schemas.
