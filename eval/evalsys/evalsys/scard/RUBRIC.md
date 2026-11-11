# S-card rubric: anchored levels for S1–S4

Rubric version: see `RUBRIC_VERSION` in `rubric.py` (this file and the
`Band` tables there are one rubric; `tests/test_scard_rubric_md.py` fails
when they drift). The replay reading `S4_replay` (`replay.py`) is judged
under the same rules with its own criteria; it carries weight 0.

The S-card is the perceptual part of the score: what is visible in an
evaluator-captured frame and invisible to every static check. It is read by a
judge (a VLM for S1–S3, a human for S4) against the anchored levels below, and
it enters the headline only once the judge has passed the calibration fixtures
(`CALIBRATION_FIXTURES`); until then the scorecard reports it as unearned
weight.

## Level scale

Every channel is answered on five anchored levels. The level is the answer;
the credit is derived from it and never asked for separately.

| level | credit | meaning |
|---|---:|---|
| 4 | 1.00 | as good as the reference this task was cut from would look on this axis |
| 3 | 0.75 | one visible shortfall against the reference; a viewer notices it only when looking |
| 2 | 0.50 | the default: mixed, or the frames do not decide it either way |
| 1 | 0.25 | the failure is routine across the sampled frames |
| 0 | 0.00 | the property is absent, or the frames show it broke |

Arithmetic: `credit = level / 4`. A card that reads level 2 on every channel
earns 0.50 of the S-card weight (0.50 × S1 0.40 + 0.50 × S2 0.25 + 0.50 × S3
0.20 + 0.50 × S4 0.15 = 0.50), so the default is the middle of the scale, not a
pass.

## Judging rules

These bullets are sent to the judge verbatim (`rubric.judging_rules()`), and
their sha256 is part of the prompt hash. Change them here, not in the prompt
builder.

- Start every channel at level 2 and move it only for something you can point
  at in a named frame. A level you cannot anchor to a frame is level 2.
- Cite frames: every rationale names the frame indices (or contact-sheet tile
  indices / times) that decided the level, and `frames_cited` lists them. A
  rationale with no cited frame is recorded as low confidence.
- Do not reward polish the reference lacks. Level 4 means "as complete as the
  reference game this task was cut from", not "as good as a shipped title".
  When reference frames are shown they are the ceiling: post-processing,
  particles, decorative UI or extra detail the reference does not have earns
  no level above what the reference would earn, and their absence costs
  nothing when the reference lacks them too.
- Judge only what is visible. When the frames cannot show a channel (one level
  only for S3, HUD not populated for S2) answer `null` with the reason; a guess
  is indistinguishable from a measurement in the artifact.
- Do not score resemblance to the reference or to any other game, do not score
  fun, and do not score S4 (feel) from still frames; feel is human-only.

## S1 surface_completeness (weight 0.40)

Are the major visible surfaces -- walls, floors, ceilings, large background
planes -- actually textured, or are they flat colour with the art budget spent
on props and weapons?

| level | band | anchor |
|---|---|---|
| 4 | textured throughout | Every large surface in every sampled frame carries visible material detail: grain, tiling, panel lines, wear, or a gradient that is not a single lighting ramp. No wall, floor or ceiling region reads as one uniform fill. |
| 3 | one flat surface class | One class of large surface (typically ceilings) is untextured flat colour; walls and floors carry detail. A viewer notices it only when looking for it. |
| 2 | environment flat, props detailed | Walls and floors are flat fills while props, weapons and characters are fully textured -- the corpus_reference signature. The frame reads as a detailed object set floating in an untextured box. |
| 1 | near-total flat fill | Nearly every surface is a flat colour; a small number of textured elements remain. The scene reads as greybox with colours assigned. |
| 0 | untextured or absent | Greybox, missing-texture magenta, single-colour void, or a surface set that did not load at all. |

Reference rule applied: a flat-shaded reference (many of the corpus games are
untextured low-poly by design) caps a flat-shaded submission at level 4, not
level 2; "textured" here means "carries the material detail the reference
carries".

## S2 ui_hygiene (weight 0.25)

Does the rendered UI hold together: text inside its container, nothing
important occluded, and readable text sitting on something rather than
directly on the sky?

| level | band | anchor |
|---|---|---|
| 4 | clean | All text fits its container with margin. Nothing overlaps anything it should not. Every text run has a backing plate, an outline, or a contrast ratio that survives the busiest background it appears over. |
| 3 | one cosmetic defect | One instance of tight-but-legible text, or one element that clips a decorative edge. No information is lost. |
| 2 | information sometimes lost | Text overflows its container or leaves the frame edge, or an unbacked label sits on a background it partly matches, in some of the sampled frames. A player would misread it at least once. |
| 1 | information routinely lost | Overflow, occlusion or unbacked-on-sky text in most sampled frames. Numbers are truncated; labels run under other panels. |
| 0 | UI unreadable or absent | No HUD where the game needs one, or a HUD whose text cannot be read at native resolution. |

Reference rule applied: a HUD as sparse as the reference's is level 4 when it
is legible; a decorative HUD the reference does not have earns nothing extra.

## S3 atmosphere_consistency (weight 0.20)

Do level 1 and level 2 look like they belong to the same game?

| level | band | anchor |
|---|---|---|
| 4 | one coherent world | Palette, lighting temperature, contrast range and material vocabulary read as one art direction across levels. Differences between levels look intentional -- a different time of day, a different biome -- not accidental. |
| 3 | coherent with one outlier | One level departs in a single axis (much brighter, one alien hue) while still reading as the same production. |
| 2 | two art directions | The levels look like two different games' assets in one executable. Neither is broken; they do not belong together. |
| 1 | incoherent within a level | Inconsistency inside a single level: adjacent surfaces from unrelated art sets, mismatched lighting on neighbouring props. |
| 0 | no discernible art direction | Placeholder colours, or every element from a different source with no unifying treatment. |

Frames from one level only: `null`, not level 2 (the channel leaves the
denominator).

## S4 feel (weight 0.15, human only)

Does the game feel good to operate: input response, weight, animation
follow-through, hit confirmation, camera behaviour?

| level | band | anchor |
|---|---|---|
| 4 | responsive and weighted | Input registers on the frame it is given, motion has acceleration and settle, and every consequential action confirms itself to the player. |
| 3 | responsive, unremarkable | Input registers promptly and motion is legible, but nothing reinforces the player's actions: hits land without weight, landings do not settle, the camera merely follows. Nothing fights the player and nothing is memorable. |
| 2 | workable with friction | Noticeable input lag, floaty or sticky movement, or actions that resolve without confirmation. |
| 1 | actively unpleasant | The player fights the controls to do ordinary things: overshoot on every stop, inputs dropped during animations, a camera that has to be corrected before each action. |
| 0 | not operable as a game | Input and outcome are effectively unrelated: the rater cannot form a model of what any control does, or the game stops responding partway through an ordinary session. |

A judge handed still frames does not answer S4; `score_s4` refuses a VLM
number here. The `S4_replay` reading (motion legible, action feedback
visible, progression visible, ending shown) is what a sampled film can show
and is reported beside the S-card at weight 0.
