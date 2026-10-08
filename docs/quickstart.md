# Quick start

These commands turn a fresh Ubuntu 22.04/24.04 clone into a complete benchmark run
or a standalone submission evaluation. Godot tasks use 4.5.1.

## 1. Set up

```bash
GIT_LFS_SKIP_SMUDGE=1 git clone --depth 1 --single-branch --branch main https://github.com/Charly-chan/SWE-Game.git
cd SWE-Game
sudo ./setup.sh
./setup.sh --check
```

A shallow clone is enough to run the benchmark. Reference projects and videos live
in the [Hugging Face dataset](https://huggingface.co/datasets/Charly-chan/SWE-Game);
`run_benchmark.sh` downloads each selected game's data when it prepares a package.
The released task manifest pins the dataset revision and checksums.

`setup.sh` is idempotent and self-verifying: it ends by printing the same
dependency table `--check` prints and exits non-zero, naming the items, if
anything required is still missing. `./setup.sh --check` likewise exits 0 only
when nothing is missing. It installs:

- apt packages (Xvfb, ffmpeg, Git LFS, jq, fonts, CA certificates, Godot's
  runtime libraries) and Node 22 if the host has no Node >= 20;
- an isolated Python venv at `.venv` with `eval/evalsys/requirements.txt`;
  `run_benchmark.sh`, `scripts/run_coding.sh`, and `evaluate.sh` refuse to run without
  it (override its location with `GB_VENV`);
- Godot 4.5.1 with a pinned SHA-256 (override `GODOT_BIN` to reuse an existing
  4.5.1 binary);
- the pinned Claude Code and Codex CLIs together with their Linux platform
  packages in a private npm prefix, `/opt/gamebench/node_modules`, plus
  proxy-neutral shims `/opt/gamebench/bin/claude` and `/opt/gamebench/bin/codex`
  that every entry script puts first on `PATH`. Nothing is installed with
  `npm -g`, so a broken host-wide install cannot affect a run;
- Git LFS filters for the remaining repository artifacts (`git lfs install --local`).
  Reference game data uses the pinned Hugging Face downloader. To prefetch a game,
  run `python scripts/fetch_reference_data.py --game kindle_relay --with-videos`.
  See [data storage and recovery](reference-data.md) for downloading all projects.

Useful flags: `--skip-apt`, `--skip-godot`, `--skip-cli`, `--skip-lfs`, `--check`.

Setup also writes the key template to `~/.config/gamebench/gb_api.env` (mode
0600) when no key file exists. API wiring is that one file: fill
`OPENAI_API_KEY` / `OPENAI_BASE_URL` for Codex (both empty = your `codex login`
against the official API) and `ANTHROPIC_API_KEY` / `ANTHROPIC_BASE_URL` for
Claude Code (empty base URL = official), then run `./setup.sh --check-auth` for
one non-billed probe per key. The `--check` table reports the file as `todo`
while every key line is empty; dry-runs need no key.

The setup shims isolate the pinned CLIs from host-specific wrappers and proxy
settings. Agent-visible tools must live outside the paths hidden by the unshare
sandbox; see [running experiments](running.md) for environment overrides.

Use `--sandbox docker` when the host cannot support the required namespace
sandbox. Each container receives its task workspace and runs the image's tools;
the host coordinates collection and evaluation. The repository Dockerfiles pin
CLI and engine versions. Under the current protocol, Docker agent runs remain
`formal_eligible=false`; retain their recorded environment deviations when
comparing results. See the [Docker guide](../docker/README.md).

## 2. Run a benchmark

Choose a model identifier available through your configured provider. Agent runs
use that provider account; the dry-run below generates inputs without model calls.

```bash
MODEL_ID="your-model-id"
./run_benchmark.sh \
  --game cat_defense --mode gdd \
  --harness codex --model "$MODEL_ID" \
  --reasoning high \
  --playtest-kit off --out results/cat-defense-gdd
```

There is no wall-clock budget by default: each cell runs until the agent exits
on its own, `summary.csv` records `budget_s` empty and the measured `wall_s`.
`--budget <seconds>` opts in to a hard cap on the agent process; when it fires
the cell has no submission.

That single command generates reusable packages, starts a detached scheduler
with the requested concurrency, waits for its workers, evaluates every
submission with the auto-selected engine, and writes `summary.csv`,
`summary.json`, and `leaderboard.md`.

Use `--game all` to run every catalog game. Allowed modes are `brief`, `gdd`,
`skeleton`, `bugfix`, and `port`; harnesses are `codex` and `claude`. Mode 5
needs the certified Unity evaluation environment; use `--eval off` to defer scoring. `--provider`
defaults to `auto`: the key file decides the route (a filled `OPENAI_BASE_URL`
/ `ANTHROPIC_BASE_URL` selects that gateway, empty selects the official
endpoint). `--provider openai` forces Codex's native OpenAI account (codex
only); `--provider micu` keeps the historical MICU-gateway defaults. Add
`--concurrency 3` to cap simultaneous cells and `--resume` to reuse generated
packages or continue an interrupted run.

Check the exact harness command without spending API credit:

```bash
./run_benchmark.sh \
  --game cat_defense --mode gdd \
  --harness codex --model "$MODEL_ID" \
  --dry-run
```

Dry-run still generates the task package and prompt, then prints the exact
Claude Code or Codex argv. It does not launch the harness or make an API
request. The model identifier is visible in that argv.

Each run follows the stable tree documented in
`docs/reference/OUTPUT_CONTRACT.md`: `run.json`, one directory per cell under
`cells/`, and the three top-level summaries. A cell contains `package/`,
`submission/`, `agent/`, `evaluation/`, and `films/`.

## 3. Evaluate or rescore

### Mode-1 input ablation: no reference video

Only `--mode brief` accepts `--reference-video off`. It omits the input reference
movie and its viewing instructions; assets, brief, GB interface, required GDD and
feature demonstrations, and the evaluator stay the same. Other modes reject it.
The default `on` preserves the normal inputs of every mode (bugfix still has no movie).

```bash
./run_benchmark.sh --game canopy_dash --mode brief --harness codex --model "$MODEL_ID" \
  --reference-video off --visual-judge none --playtest-kit off --dry-run --out results/mode1-no-video
```

Use a separate output directory for the paired `--reference-video on` run and
keep model, harness, reasoning, budget policy, tools and evaluation identical.
`--dry-run` prepares inputs without starting an agent; remove it and add `--resume`
to run generation from that prepared directory (or use a fresh output directory).
This switch does not disable evaluation videos or the VLM judge. Input conditions
are recorded in `run.json` and each package manifest. See the
[paper ablations](https://arxiv.org/html/2609.33678v1#S4.SS4).

### Evaluation commands

For Godot `brief`, `gdd`, and `skeleton` tasks, a submission may provide
`demos.json` instead of a whole-run `ops.json`. Each feature demonstration is
replayed independently; a single whole-game-clear recording is not required.
See the [feature-demonstration protocol](tasks.md#feature-demonstrations)
for the JSON format, fixed starting state, and scoring rules. Bug-fix and Unity
port tasks retain their own evaluation protocols.

Evaluate a generated package and submission with the auto-selected engine:

```bash
./evaluate.sh /path/to/package /path/to/submission --out /path/to/evaluation
```

VLM calls are disabled by default. Modes 1–3 use the corresponding
`2026-09-19.modeN-vlm1` registry: 85 objective points plus 15 VLM points.
Without a complete visual reading, `objective_total.score` remains available
and `weighted_total.score` is null. Missing required objective evidence also
keeps the composite incomplete. Mode 4 defaults to
`2026-09-15.mode4-redesign1`; Mode 5 to `2026-09-20.mode5-mdva-domain1`.

For paid visual assessment, configure the external file selected by
`GB_API_ENV` (or `~/.config/gamebench/gb_api.env`). For example, to use the
Fable rubric judge through Claude Code:

```bash
export GAMEBENCH_VLM_PROVIDER=claude_code
export GAMEBENCH_VLM_MODEL=claude-fable-5-1
export GAMEBENCH_VLM_BASE_URL=https://www.micuapi.ai
export GAMEBENCH_VLM_KEY_ENV=MICU_API_KEY
# Set MICU_API_KEY in this external file.
# GAMEBENCH_CLAUDE_CODE_BIN=/path/to/claude  # optional executable override
```

For a Responses-compatible model, set `GAMEBENCH_VLM_PROVIDER=responses`,
`GAMEBENCH_VLM_MODEL`, `GAMEBENCH_VLM_BASE_URL`, and `GAMEBENCH_VLM_KEY_ENV`
explicitly for your provider. The Responses transport also honors
`OPENAI_BASE_URL` / `AUTO_CODE_BASE_URL` when no judge-specific endpoint is set.
API credentials and the Claude executable are checked before engine evaluation;
this local check does not establish model availability or account balance.

```bash
./evaluate.sh /path/to/package /path/to/submission \
  --out /path/to/with-visual --visual-judge vlm
```

The current game rubric judge sees evaluator-recorded candidate gameplay,
frames from the supplied GT video, and asset examples. Its groups are M 10%,
D 18%, V 27%, and A 45% of the 15-point visual contribution. Each item uses
direct continuous 0–1 scores under the rubric’s full, partial, and zero-credit
criteria, with applicable deficiency caps. Independent calibration remains pending.
`--visual-judge local` attaches supported local diagnostics and cannot supply
these VLM points. `GB_VISUAL_JUDGE` changes the shell default; an explicit flag
wins.

Rejudge retained recordings or record a retained Godot submission first:

```bash
./evaluate.sh --judge-visuals /path/to/report.json --out /path/to/rejudged
./evaluate.sh --judge-visuals /path/to/report.json \
  --out /path/to/with-visual --record-missing
# Upgrade an older Mode 1 report explicitly; use mode2-vlm1 / mode3-vlm1 for those modes.
./evaluate.sh --judge-visuals /path/to/old-report.json \
  --out /path/to/upgraded --record-missing --registry 2026-09-19.mode1-vlm1
```

Output directories must be new. A saved `modeN-vlm1` report retains its registry
when visual evidence is added. Historical reports without that registry or a
game-rubric manifest keep the historical `visual1` behavior unless `--registry`
is given. Use `--package` and `--submission` if their retained paths moved.
These commands preserve objective observations and never rerun a coding agent.
Recompute objective observations with the normal `evaluate.sh` form when the
behavioral evaluator itself changed.

`--score-against-source` enables additional source-conditioned objective channels
for packages generated without them. It can change the measured score and is
not implied by `--visual-judge`.

Historical `2026-09-11.evidence1` and `2026-09-11.visual1` registries remain
available explicitly. The latter uses the older GameCraft-adapted judge,
`GAMECRAFT_BENCH_JUDGE_*` settings, and its historical weights; it is separate
from the current game-specific rubric protocol.

The summary prints `weighted_total`, `headline_ceiling`, `resolved`, failing
strict items, `ranking_eligible`, its explanatory note, `replay_visual_status`,
and both report paths. `not_measured` means no replay reading was produced;
check the report rather than treating it as a visual pass. For feature-demo
submissions, the summary also prints feature/action-caused coverage and each
segment's visual status. Their results are in `demonstrations` and
`demonstration_visuals`; an absent whole-run `replay_reading` does not mean
the independent segment recordings were not judged.
A low score or `resolved=no` is a valid measured result; the command preserves
`bench eval-task`'s nonzero status for strict failures.

To apply the current scorecard to already stored verdicts without rerunning
Godot:

```bash
./evaluate.sh --rescore /path/to/report.json
# Add --write to replace its scorecard; a .pre-rescore backup is kept.
```

Environment overrides are documented in `.gb_api.env.example` and
`eval/tools/gb_env.sh`. Evaluations can need several GB of scratch space; set
`GB_SCRATCH_ROOT` when `/tmp/swe-game` is unavailable.
