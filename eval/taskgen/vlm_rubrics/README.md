# Visual rubrics

Each game directory contains fixed rubric criteria and reference-video frame
indices used by perceptual assessment. Criteria and weights are evaluated using
the task video and evaluator-captured candidate gameplay.

The response schema, missing-evidence handling, and weights are documented in the
[scoring specification](../../../docs/reference/HIERARCHICAL_MULTI_EVIDENCE_SCORECARD.md).

## Demonstrated visual quality

The `2026-10-09.demonstrated-quality-v5` policy assigns a direct continuous score
from 0 to 1 to every applicable item. Entirely unshown achievement receives 0;
partial demonstrations earn credit for the required achievements they show.
Full credit requires positive evidence for every applicable full-credit condition.
Only explicitly conditional, evidenced not-applicable items use null.

Record unshown requirements in `missing_evidence` and condition-level coverage
checks. Record only observed shortcomings in `deficiencies`; missing evidence
does not prove that a feature is absent. The policy takes precedence over older
item-level wording that treated missing evidence as an unscored item. Missing
recordings, provider failures and invalid judge responses remain incomplete
assessments.

The v5 policy gives more credit to substantive partial completion. Its continuous
judgment references are 0.01–0.05 for placeholder presence, 0.10–0.30 for substantive
parts with an important relationship missing, 0.40–0.60 for an established core
result with substantial gaps, 0.65–0.80 for complete core relationships with
limited finishing defects, and 0.85–0.95 for only very slight isolated defects.
These are guidance, not fixed bins or automatic awards. Intermediate values and
equal scores remain valid. Every item retains its declared deficiency ceilings. Apply a ceiling only when its triggering
condition is observed. The host averages applicable items, including valid zero
scores, without an exponent or alternative-score conversion. Group weights are
visible mechanics 10%, design and content 18%, functional visual communication
27%, and art 45%.

New task packages freeze the policy and rubric version. Rejudging retained
recordings under this policy produces a new assessment; previously saved
judgments keep their original evidence and scores.
