# Run SWE-Game through Harbor

Harbor 0.23.0 runs the agents, manages Docker environments and timeouts, and
retains native agent logs and trajectories. SWE-Game still generates the task
packages, collects submissions, and computes every game score with `evalsys`.
The existing matrix runner remains available.

## Install

Use Linux with Docker Engine, Docker Compose, Buildx, and Python 3.12 or later.
The evaluator host also needs the normal SWE-Game Godot 4.5.1 setup and corpus
files. Harbor is an optional dependency and does not change the normal Python
environment:

```bash
uv venv --python 3.13 .venv-harbor
uv pip install --python .venv-harbor/bin/python \
  -r eval/evalsys/requirements-harbor.txt
export HARBOR_PYTHON="$PWD/.venv-harbor/bin/python"
docker compose version
docker buildx version

# Reuse the SWE-Game toolchain image.
./docker/build.sh godot
```

See [Docker setup](../docker/README.md) for the Unity image and image publishing.
The exporter defaults to the versioned GHCR toolchain tags. Use `--image` with
the local image, as below, when the registry image is unavailable.

## Export existing task packages

Use `bench gen-task` or the existing runner's dry run to generate a package
first. Export the **whole package**, which contains `manifest.json`, `visible/`,
and `hidden/`:

```bash
./scripts/run_harbor.sh export \
  --package /absolute/path/to/package \
  --out /absolute/path/to/harbor-dataset/shadow-walker-gdd \
  --image gamebench-agent:godot-4.5.1 \
  --agent-timeout 1800 --verifier-timeout 3600
```

Repeat into another child directory for each task or repair case. Blocked
packages are rejected before scheduling. The exporter never overwrites a task.

The exported task has this layout:

```text
instruction.md                 Public task prompt and workspace instructions
task.toml                      Timeouts, resources, and artifact collection
environment/Dockerfile         Toolchain image plus public materials only
environment/visible/           Agent inputs copied to /workspace
evaluator/package/             Full frozen package, on the evaluator host
tests/test.sh                  Fails if the SWE-Game host verifier is omitted
```

Harbor builds from `environment/`. The agent receives neither `hidden/` nor
the reference corpus. Package-local oracle paths are rebased to the exported
copy; references into the host corpus remain unchanged. Keep that corpus at its
recorded location. Re-export after moving a task directory, since oracle paths
inside the frozen package are absolute.

## Run Codex or Claude Code

Configure provider credentials using Harbor's agent configuration. Native agent
options, provider endpoints, and CLI versions belong to the selected Harbor
agent; `.gb_api.env` is not loaded automatically by this entry point. See the
[Harbor agent documentation](https://docs.harborframework.com/core-concepts/agents/pre-integrated-agents).
Do not put credentials in task packages or Docker build arguments.

```bash
./scripts/run_harbor.sh run \
  --path /absolute/path/to/harbor-dataset \
  --jobs-dir /absolute/path/to/harbor-jobs \
  --job-name codex-pilot --n-concurrent 1 \
  --engine on \
  -- --agent codex --model "$MODEL_ID" --agent-kwarg version=0.153.4

# To select the other installed harness, use:
# -- --agent claude-code --model "$MODEL_ID" --agent-kwarg version=2.1.222
```

Options before `--` belong to SWE-Game. Options after it are Harbor options.
Harbor's agent installation, model routing, and logs remain native. Consult
`harbor agent schema codex` or `harbor agent schema claude-code` for available
options. Pin the CLI version for comparable runs.

The default concurrency is **one**: collection and evaluation finish before
the next task starts. Increase `--n-concurrent` to run multiple complete trials
at once. An unsuccessful trial still finishes and releases its slot; success
is not required to advance. Harbor retries are disabled by default; set its
`--max-retries` and retry filters explicitly when needed.

The agent writes to `/workspace/submission/`, following the existing submission
contract. Harbor snapshots `/workspace`, stops the agent environment, and
restores that snapshot into a fresh verifier environment. The host verifier
then uses `collect_submission` and `evaluate_task` on the collected copy. The
evaluator runs in a separate process group so its timeout also stops child
Godot processes.

## Results and scoring

Each trial retains:

- `agent/`: Harbor's agent logs and available trajectories.
- `artifacts/`: Harbor's collected workspace snapshot.
- `verifier/submission/`: submission in the normal SWE-Game layout.
- `verifier/report.json` and `report.md`: unmodified `evalsys` reports.
- `verifier/eval.stdout.log` and `eval.stderr.log`: evaluator process output.
- `verifier/swe-game.json`: score eligibility or collection/deferred/error state.

`swe-game-summary.json` is generated in the job directory when the command
returns, including on failure. Rebuild it after interrupted runs with:

```bash
./scripts/run_harbor.sh summarize /absolute/path/to/harbor-jobs/codex-pilot
```

The Harbor reward is `headline.score` only when `headline.ranking_eligible`
is true. A ranking-eligible zero remains zero. Missing or incomplete evidence,
missing projects, and evaluator failures receive no numeric reward. Strict
`resolved` is retained independently. The legacy top-level diagnostic `score`
is never used as the benchmark reward.

The summary groups results by agent and CLI version, provider, model, task mode, registry,
scale, and score scope. Every mean includes its scored/total coverage and the
unscored count; do not compare conditional means without those counts. No
criterion weights are changed. For Modes 1–4, `mean_score` averages eligible attempts within
each repair case, then cases within each game, then games equally, so games
with more repair cases do not get extra weight. `mean_trial_score` is a separate
diagnostic. Failed setup/collection attempts stay in the same group's coverage
when the registry's protocol is known; otherwise they appear in an unknown
protocol group and remain in the job-wide totals. `--registry-version` selects an existing
registry; omission keeps `evalsys` defaults. `--visual-judge` supports `none`
(default), `local`, and `vlm`, as in the existing evaluator.

**Harbor's generic `Mean` is not the SWE-Game score.** Harbor can insert zero
for missing rewards. Use `swe-game-summary.json` and the canonical reports for
benchmark reporting. The SWE-Game wrapper exits nonzero when Harbor records a
trial exception. Exit zero still does not imply a valid or successful submission;
inspect the recorded statuses, eligibility, and strict completion results.

## Mode 5: Community Docker

Harbor uses the same independent Community evaluator and five-component registry.
Export requires a passing doctor state and a **floating** license provider; Harbor
does not inject private file or existing-home activation into its agent container.
Those providers use the supported `gb mode5 run` workflow. The default Mode 5 agent
budget is 7200 seconds (120 minutes) and can be overridden explicitly.

```bash
python -m evalsys.harbor export --package /absolute/package --out /absolute/task \
  --mode5-profile community-docker --mode5-state-dir /absolute/private/state
```

The exporter reads the local lock and writes the immutable `sha256:...` Agent image ID into
Harbor's native `[environment].docker_image`. It intentionally omits a Dockerfile because
BuildKit interprets `FROM sha256:...` as a registry name; Harbor starts the already-built local
image ID directly and uploads only `environment/` public task files into its workdir.
The export is rejected if the saved doctor result is missing, failed, belongs to
a different Agent image, or does not use the floating provider. Mode 5 Agent
budget defaults to 7200 seconds; explicitly set `--agent-timeout` to override it.
Then run:

```bash
./scripts/run_harbor.sh run \
  --path /absolute/path/to/mode5-dataset \
  --jobs-dir /absolute/path/to/harbor-jobs \
  --job-name mode5-community \
  --mode5-profile community-docker \
  --mode5-state-dir "$HOME/.cache/gamebench/mode5" \
  --docker "${GB_DOCKER_BIN:-docker}" \
  -- --agent codex --model "$MODEL_ID"
```

The host verifier loads the digest-pinned image lock and passing doctor result,
collects the submission, and calls the same fresh, offline evaluator-container API
used by `./gb mode5 evaluate`. It uses the single Community scoring registry.
The resulting record carries `environment_class=community-docker`,
`paper_compatible=false`, and `ranking_scope=mode5-community-evidence-five-visual1`.
The registry is `2026-10.mode5-evidence-five-visual1`, with fixed 35/25/15/15/10 weights.
The five-component evidence proxy uses no VLM. Visual uses evaluator-owned
implementation-correspondence measurements from runtime captures, with discounted
Editor/static/presence fallback evidence; it does not measure perceptual or aesthetic
similarity. `official_total=null` and no exact paper reproduction is asserted.
Model summaries use the full-catalog arithmetic task mean, with separate
low-tail and 70/30 reliability diagnostics. Partial-catalog means are diagnostic
only. Repeated attempts are averaged within each game only when every attempt
has a valid proxy reading; an unscored attempt withholds that game's complete
reading rather than being dropped. Missing games withhold the main mean.

Harbor itself owns the Agent container. For a saved `floating` provider, export adds
only `${GB_UNITY_FLOATING_ENDPOINT}` and a pre-Agent Unity healthcheck to the task;
the wrapper reads the endpoint from private Mode 5 state and supplies it only in the
Harbor child process environment. The endpoint is not written into the dataset or
command line. A failed entitlement probe stops the trial before the model starts.

Never bake a license into an image or task. Harbor 0.23's portable task schema has no
private host-file mount for a `.ulf` or dedicated Unity home. Therefore the standalone
`./gb mode5 run` path is the supported one-command workflow for `file` and
`existing-home`; this adapter rejects their export rather than assuming that an
external secret-mount extension exists. Do not copy licensing material into
`environment/` or encode them as environment variables.

## Validation and limitations

Run the focused adapter tests in the Harbor environment:

```bash
PYTHONPATH=eval/evalsys "$HARBOR_PYTHON" -m pytest -q \
  eval/evalsys/tests/test_harbor_adapter.py
```

Use Harbor's `oracle` agent with a private `solution/solve.sh` only for controlled
transport/parity fixtures. Normal exported tasks contain no solution. Such a
fixture tests orchestration and evaluator parity, not a model's ability to
solve the game.

Harbor changes orchestration; it does not repair a model endpoint that loops,
returns malformed tool calls, or terminates before producing a project. Keep
the native trace when diagnosing those failures. This integration targets
local Docker jobs; distributed hosts require the same evaluator dependencies
and corpus paths. Mode 5 runtime grading uses the independently provisioned Community evaluator.
