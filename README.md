<div align="center">

# SWE-Game: Can Coding Agents Build the Games We Want?

[![arXiv](https://img.shields.io/badge/arXiv-2609.33678-b31b1b.svg)](https://arxiv.org/abs/2609.33678)

[![Dataset](https://img.shields.io/badge/🤗%20Dataset-SWE--Game-yellow)](https://huggingface.co/datasets/Charly-chan/SWE-Game)

41 reference games · 5 task types

[Tasks](#tasks) · [Evaluation](#evaluation) · [Quick Start](#quick-start) · [Game Gallery](GAMES.md) · [Dataset](#reference-data) · [Repository](#repository-structure) · [Documentation](#documentation) · [Citation](#citation)

</div>

SWE-Game evaluates how coding agents build, complete, repair, and port interactive games. Tasks are grounded in a corpus of 41 Godot games, with game assets, design documents, reference gameplay, and executable behavioral requirements.

The evaluator runs submitted projects, replays player inputs, and measures game mechanics, progression, and perceptual quality.

<table>
  <tr>
    <td width="33%"><img src="docs/assets/gallery/3d_platformer.png" alt="Beacon Relay: a colorful 3D floating-island platformer"></td>
    <td width="33%"><img src="docs/assets/gallery/pixel_platformer_v2.png" alt="Pixel Platformer v2: a colorful single-screen pixel-art platformer"></td>
    <td width="33%"><img src="docs/assets/gallery/kindle_relay.png" alt="Kindle Relay: a 3D exploration game beneath a starry sky"></td>
  </tr>
  <tr>
    <td align="center">Beacon Relay</td>
    <td align="center">Pixel Platformer v2</td>
    <td align="center">Kindle Relay</td>
  </tr>
</table>

*Example reference-game screenshots. Explore the [full game gallery](GAMES.md).*

## Tasks

Five task types vary the information available to the agent and the development work it must perform:

| Task type | Goal | Main agent inputs | Deliverable | Inventory |
| --- | --- | --- | --- | ---: |
| **Brief-to-Game** | Design and build a game | Brief, assets, reference video | Game design document and Godot project | 41 |
| **GDD-to-Game** | Implement a specified game | Game design document, assets, reference video | Godot project | 41 |
| **Skeleton Completion** | Complete a game from a minimal framework | Code skeleton, task requirements, assets, reference video | Completed Godot project | 41 |
| **Bug Repair** | Repair a game while preserving its behavior | Faulty project, gameplay requirements, bug report | Repaired Godot project | 82 active cases |
| **Godot-to-Unity Porting** | Preserve a game's behavior across engines | Godot source, design document, assets, reference video, Unity interface | Unity project and build instructions | 41 |

Counts describe this fixed release. The paper evaluates 247 tasks, including 83 Bug Repair cases; this release includes 82 repair cases. See the [task protocol](docs/tasks.md) for submission requirements and [catalog](catalog.json) for game identifiers.

## Evaluation

Evaluation combines two evidence domains:

- **Objective Behavioral Evaluation** executes the submitted project and checks input responses, observable mechanics, progression, and mode-specific requirements. Matched no-input controls help determine whether demonstrated behavior depends on player action.
- **Perceptual Quality Assessment** uses the paper’s four rubric groups: visible mechanics, design and content, functional visual communication, and art.

The three construction tasks allocate 85 objective points and 15 game-specific VLM points. Bug Repair measures behavioral restoration and preservation. Godot-to-Unity Porting allocates 70 objective points, 15 structure VLM points, and 15 visual-quality VLM points. Strict completion (`resolved`) is reported separately from the graded score.

Visual judging is disabled by default. A complete construction or porting score requires VLM evidence; otherwise the composite remains null. See the [scoring specification](docs/reference/HIERARCHICAL_MULTI_EVIDENCE_SCORECARD.md) for weights and registries, and [paper Section 4.5](https://arxiv.org/html/2609.33678v1#S4.SS5) for human agreement studies. For reproducibility, record the evaluator commit, registry, model, harness, budget, input settings, and judge configuration.

## Quick Start

The runner targets Linux x86-64 (Ubuntu 22.04/24.04), with root or sudo and user-namespace support. Setup installs Godot **4.5.1** and the pinned agent CLIs. Porting evaluation additionally requires a licensed Unity **6000.3.23f1** [certified VM](eval/infra/unity/README.md).

```bash
GIT_LFS_SKIP_SMUDGE=1 git clone --depth 1 --single-branch --branch main \
  https://github.com/Charly-chan/SWE-Game.git
cd SWE-Game
sudo ./setup.sh
./setup.sh --check
```

Reference game source, assets, and videos are hosted in the [Hugging Face dataset](https://huggingface.co/datasets/Charly-chan/SWE-Game). The runner downloads a fixed task package from a pinned dataset revision and verifies SHA-256 checksums. Configure your model provider using [.gb_api.env.example](.gb_api.env.example); see the [runner guide](docs/running.md) for setup details.

### Run an agent

The runner supports Codex and Claude Code harnesses. Choose a model identifier available through your configured provider:

```bash
MODEL_ID="your-model-id"

# Download one fixed task package and inspect the agent command without calling a model.
./run_benchmark.sh --game wizard_chase --mode brief \
  --harness codex --model "$MODEL_ID" --dry-run \
  --out results/brief-preview

# Run the agent and retain its submission for evaluation on a separate machine.
./run_benchmark.sh --game wizard_chase --mode brief \
  --harness codex --model "$MODEL_ID" --eval off \
  --out results/brief-agent
```

Batch runs, concurrency, budgets, and resume options are documented in the [runner guide](docs/running.md). [Docker](docker/README.md) and [Harbor](docs/harbor.md) provide additional execution options.

### Evaluate a submission

Pass the downloaded task package and the corresponding submission directory:

```bash
./evaluate.sh path/to/package path/to/submission \
  --out results/evaluation --visual-judge none
```

Use `--visual-judge vlm` for visual assessment. Reports and scores are written under the selected output directory; see the [output contract](docs/reference/OUTPUT_CONTRACT.md). Rescoring and adding visual evidence to saved runs are covered in [evaluation commands](docs/quickstart.md#evaluation-commands).

## Reference Data

The [Hugging Face dataset](https://huggingface.co/datasets/Charly-chan/SWE-Game) contains **246 fixed tasks**, **41 reference game projects**, and **41 reference recordings**. The 451 downloadable package variants cover the supported playtest-kit and Mode-1 video settings. GitHub hosts the evaluator, runners, fixed scoring data, documentation, and gallery screenshots. Original game and asset licenses are preserved with the data and indexed in [third-party notices](THIRD_PARTY_NOTICES.md).

The runner downloads the required data automatically. To prefetch one game and its primary recording:

```bash
python scripts/fetch_reference_data.py --game kindle_relay --with-videos
```

For the full corpus, use `--game all --with-videos`. See [data downloads](docs/reference-data.md) for pinned versions, checksums, storage, and recovery.

## Repository Structure

| Path | Purpose |
| --- | --- |
| [`.github/`](.github/README.md) | Automated tests, container publishing, and the pull request template |
| [`data/`](data/README.md) | Pinned task-data revision, download paths, and checksums |
| [`docker/`](docker/README.md) | Godot and Unity coding-agent images, build scripts, and toolchain checks |
| [`docs/`](docs/README.md) | User guides, scoring contracts, and game attribution |
| [`eval/`](eval/README.md) | Evaluator implementation, task definitions, interfaces, and reference evidence |
| [`scripts/`](scripts/README.md) | Data downloads, agent launchers, and evaluation utilities |
| `games/` | Reference projects downloaded from Hugging Face; created on demand and ignored by Git |
| `results/` | Local task packages, submissions, logs, and evaluation reports; ignored by Git |

The root entry points are [`setup.sh`](setup.sh) for installation,
[`run_benchmark.sh`](run_benchmark.sh) for agent runs, and
[`evaluate.sh`](evaluate.sh) for scoring saved submissions.
[`catalog.json`](catalog.json) lists the reference games and their metadata.

Inside `eval/`, [`taskgen/`](eval/taskgen/README.md) stores fixed visual rubrics,
[`tasks/`](eval/tasks/README.md) stores game-specific evaluation definitions and
baselines, and [`evalsys/`](eval/evalsys/README.md) contains task downloads, agent
execution, and submission evaluation.

## Documentation

| Resource | Contents |
| --- | --- |
| [Runner guide](docs/running.md) | Providers, batch execution, and reproducibility |
| [Task protocol](docs/tasks.md) | Inputs and submission requirements |
| [Evaluation overview (中文)](docs/evaluation.md) | Behavioral and perceptual evaluation |
| [Godot-to-Unity Porting](docs/reference/UNITY_MODE5.md) | Cross-engine setup and evaluation |
| [Documentation index](docs/README.md) | All guides and detailed contracts |

## Citation

If you use SWE-Game, please cite the [paper](https://arxiv.org/abs/2609.33678):

```bibtex
@misc{chen2026swegame,
  title         = {{SWE-Game}: Can Coding Agents Build the Games We Want?},
  author        = {Xiaoyu Chen and Lai Wei and Jin Wang and Xiangyu Zou and Ruochen Fan and Enze Luo and Mingzhe Yao and Jiahui Zhu and Yuhua Wen and Linghe Kong and Weiran Huang},
  year          = {2026},
  eprint        = {2609.33678},
  archivePrefix = {arXiv},
  primaryClass  = {cs.AI},
  url           = {https://arxiv.org/abs/2609.33678}
}
```
