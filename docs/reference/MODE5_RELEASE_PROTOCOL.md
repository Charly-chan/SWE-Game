# Mode 5 release protocol

Mode 5 has one execution and scoring path: Community Docker. Installation and commands are in the [release guide](MODE5_RELEASE.md); the complete metric definition is in [Mode 5 scoring](MODE5_SCORING.md). VLM is not used by this release protocol.

## Environment and task materials

The fixed environment is Unity 6000.3.23f1, StandaloneLinux64, Mono, in independent Agent and evaluator containers. The user supplies a valid Unity entitlement. Visible task materials include the source game, GDD, assets, reference video, Unity scaffold, SDK, interface contract and environment lock. Hidden suites, controller code, credentials and candidate scoring reports are evaluator-owned.

The evaluator rebuilds the submitted project with its fixed builder. It does not execute candidate `BUILD.md` shell commands. A candidate must provide a native Unity project, declared scenes and entities, the required interface, `ops.json` and build metadata. A bundled Godot runtime is disallowed.

## Single workflow

```text
setup / doctor -> package and scaffold -> Agent port -> original submission
-> fresh offline evaluator -> witness and controls -> hidden runtime suite
-> retained controller evidence -> evidence-adjusted score -> report and summary
```

Static evidence is snapshotted before execution. The evaluator retains its own build, runtime and capture evidence. It never recomputes a score from mutable candidate files after execution.

The default Agent budget is 7200 seconds. Each Unity child command is capped at 1200 seconds. The budget excludes setup, package generation, submission transfer and evaluator startup. Reports record the image lock, harness, model, provider, budget and input settings.

## Scoring registry

Every release report identifies registry `2026-10.mode5-evidence-five-visual1`, ranking scope `mode5-community-evidence-five-visual1`, environment `community-docker`. Serialized score fields are defined in the [output contract](OUTPUT_CONTRACT.md).

| Component | Weight | Evidence sources |
| --- | ---: | --- |
| Mechanics | 35 | Input mapping, core mechanics, interactions/state, outcomes/checkpoints/reset |
| Playability | 25 | Valid non-idle operations, input chain, progression, final goal, pause/restart/scene flow |
| Structure | 15 | Scenes/entities, referenced content, UI hierarchy/interactions |
| Visual | 15 | Referenced assets, UI/feedback, render configuration, animation/audio, reference layout |
| Stability | 10 | Package/build configuration, lifecycle/load/reset, resource and exception safety |

Each criterion has evaluator-owned frozen obligations. Evidence coefficients are runtime verified 1.00, editor verified 0.80, static supported 0.60 and file presence only 0.25. Failed or missing evidence receives zero for that obligation. Denominators never come from candidate claims or file counts.

The fixed score total is the weighted sum of the five component percentages. Mechanics and Playability have non-runtime ceilings. Visual uses evaluator-owned `visual_implementation_correspondence` measurements: runtime captures can verify implementation correspondence for the frozen visual obligations, while Editor/static/presence evidence remains discounted. Visual measures implementation correspondence without VLM calls; perceptual and aesthetic similarity are outside this metric. Visual can receive its full 15-point weight when its runtime obligations are independently verified; the fixed denominator remains in force.

## Outcome and ranking

Component points and the strict runtime outcome are separate fields. A task is ranking eligible only when the evaluator environment and integrity gates pass and the complete release evidence is present. A candidate build failure preserves independent static facts while making runtime obligations fail. An evaluator or transport failure withholds the headline rather than assigning a model zero.

A model summary uses the arithmetic mean over the fixed 41-game task set. Low-tail diagnostics are reported beside the mean and do not replace it. Reports from another registry, environment profile, harness or budget cannot be silently pooled.

## Retained evidence

`gb mode5 rejudge` reads only retained evaluator evidence, verifies package identity and registry, and writes to a new output directory. It does not rerun the Agent or Unity and does not call a VLM. Historical reports without a pre-execution controller snapshot remain explicit incomplete reports when rescored.
