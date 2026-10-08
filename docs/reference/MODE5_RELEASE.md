# Mode 5: Community Docker release guide

Mode 5 (`port`) asks a coding agent to port a Godot game to a native Unity game. The public release uses one Community Docker workflow. The [release protocol](MODE5_RELEASE_PROTOCOL.md) defines the input, isolation, evidence and score contract.

## Requirements

Use Linux x86-64, Python 3.10 or newer, Docker, and a Unity entitlement activated by the same Linux or WSL coordinator. The repository does not distribute Unity Editor files, license files, private homes, model credentials, or hidden controller code.

```bash
python3 -m venv .venv
.venv/bin/python -m pip install -r eval/evalsys/requirements.txt
export GB_PYTHON="$PWD/.venv/bin/python"
./gb mode5 setup
./gb mode5 doctor --license-provider existing-home --unity-config-root "$HOME"
```

The fixed toolchain is Unity 6000.3.23f1, StandaloneLinux64, Mono. `setup` builds the local Agent and evaluator images from official Unity archives. `doctor` must pass before a run. The default Agent budget is 7200 seconds; one Unity child command is limited to 1200 seconds.

## Run and resume

```bash
./gb mode5 run --game cat_defense --harness claude --model YOUR_MODEL \
  --agent-provider codex --out /absolute/results/cat_defense
./gb mode5 summarize /absolute/results
./gb mode5 rejudge --evaluation /absolute/results/cat_defense/evaluation \
  --package /absolute/results/cat_defense/package \
  --out /absolute/results/cat_defense/rejudged
```

The evaluator receives the original submission, rebuilds it in a fresh offline container, runs the witness, matched-null, auto-win, hidden behavior and counterfactual checks, and retains controller-owned evidence. Resume state records the phase, image lock, budget, harness, model and input settings.

Static evidence is captured before candidate execution. The evaluator never trusts a candidate-authored report as verification input. Agent self-checks and exit code zero do not prove that the port passed.

## Fixed package boundary

Visible materials include the Godot source, GDD, assets, reference video, Unity scaffold, SDK, interface contract and environment lock. Hidden suites, controller code and private credentials remain evaluator-owned. The candidate must create a native Unity project and `ops.json`; bundling a Godot runtime is forbidden.

The generated package contains the calibrated Mode 5 suite contract from `data/mode5`. It does not contain Unity Editor archives or license state. The matching HF bundles must be published before end users can generate the final fixed packages.

## Release score

The only release registry is `2026-10.mode5-evidence-five-visual1`. It publishes a five-component evidence-adjusted proxy with weights Mechanics 35, Playability 25, Structure 15, Visual 15 and Stability 10. Visual uses evaluator-owned `visual_implementation_correspondence` measurements from runtime captures, with discounted Editor/static/presence fallback evidence. No VLM is called, and the metric does not measure perceptual or aesthetic similarity. Reports set `paper_compatible=false` and `official_total=null`.

A strict runtime outcome is reported separately from proxy points. Missing runtime evidence remains missing; static evidence is discounted and never silently promoted to runtime credit. See [Mode 5 scoring](MODE5_SCORING.md) and the [English protocol](MODE5_RELEASE_PROTOCOL.md).

## Harbor

Harbor export is supported only when the active Mode 5 image lock, passing preflight and floating license provider are present. The validated Personal or existing-home workflow is `gb mode5 run`, not a Harbor container export.

## Privacy and failure handling

License material stays in the private coordinator or floating endpoint. Logs are filtered before publication. Environment failures are reported as infrastructure or inconclusive states and are not converted into model zeroes. Candidate build and runtime failures remain candidate evidence and do not erase independent static facts.
