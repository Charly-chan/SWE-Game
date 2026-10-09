<div align="center">

# SWE-Game: Can Coding Agents Build the Games We Want?

[![arXiv](https://img.shields.io/badge/arXiv-2609.33678-b31b1b.svg)](https://arxiv.org/abs/2609.33678)
[![HF Papers](https://img.shields.io/badge/%F0%9F%A4%97%20HF%20Papers-SWE--Game-ffd21e)](https://huggingface.co/papers/2609.33678)
[![Dataset](https://img.shields.io/badge/🤗%20Dataset-SWE--Game-yellow)](https://huggingface.co/datasets/Charly-chan/SWE-Game)
[![License](https://img.shields.io/badge/License-Apache--2.0-blue.svg)](LICENSE)

**v1.0.0** · 41 reference games · 5 task types · 246 tasks

[Tasks](#tasks) · [Main Results](#main-results) · [Evaluation](#evaluation) · [Quick Start](#quick-start) · [Game Gallery](GAMES.md) · [Dataset](#reference-data) · [Repository](#repository-structure) · [Documentation](#documentation) · [Citation](#citation)

</div>

SWE-Game is a benchmark for coding agents across five game development tasks: building games from briefs or design documents, completing code skeletons, repairing bugs, and porting Godot games to Unity.

The benchmark covers **41 reference games** spanning 2D and 3D platforming, action, puzzles, driving, and strategy. Agents receive task-specific requirements and materials; the evaluator runs their submissions, replays player inputs, and assesses game mechanics, progression, playability, and visual quality.

Use this repository to **run an agent**, **evaluate a submission**, or **reproduce a benchmark run**. Task packages, reference projects, assets, and gameplay recordings are available in the [Hugging Face dataset](https://huggingface.co/datasets/Charly-chan/SWE-Game).

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
| **Bug Repair** | Repair a game while preserving its behavior | Faulty project, gameplay requirements, bug report | Repaired Godot project | 82 |
| **Godot-to-Unity Porting** | Preserve a game's behavior across engines | Godot source, design document, assets, reference video, Unity interface | Unity project and build instructions | 41 |

See the [task protocol](docs/tasks.md) for submission requirements, the [catalog](catalog.json) for game identifiers, and [version information](docs/releasing.md) for the task inventory and scoring configuration.

## Main Results

The following tables reproduce **Table 3** of the [paper](https://arxiv.org/html/2609.33678v1#S4.T3). Scores range from **0 to 100**, with higher values indicating better performance. The experiment configuration and agent frameworks are described in [Section 4.1](https://arxiv.org/html/2609.33678v1#S4.SS1); [version information](docs/releasing.md#published-results) records the result-set provenance.

### Brief-to-Game

<table width="820">
  <thead>
    <tr>
      <th align="left" width="170">Model</th>
      <th align="right" width="145">Mechanics</th>
      <th align="right" width="145">Content</th>
      <th align="right" width="145">Playability</th>
      <th align="right" width="145">VLM</th>
      <th align="right" width="70">Total</th>
    </tr>
  </thead>
  <tbody>
    <tr><td align="left">Qwen3.8&nbsp;Flash</td><td align="right">21.10</td><td align="right">23.32</td><td align="right">28.38</td><td align="right">40.40</td><td align="right">25.97</td></tr>
    <tr><td align="left">Grok4.6</td><td align="right">25.66</td><td align="right">35.34</td><td align="right">61.21</td><td align="right">48.39</td><td align="right">38.19</td></tr>
    <tr><td align="left">GPT-5.6&nbsp;Luna</td><td align="right">24.17</td><td align="right">21.29</td><td align="right">55.51</td><td align="right">60.05</td><td align="right">32.60</td></tr>
    <tr><td align="left">Opus5</td><td align="right">27.55</td><td align="right">48.26</td><td align="right">80.99</td><td align="right">69.23</td><td align="right"><strong>50.21</strong></td></tr>
    <tr><td align="left">GLM5.3&nbsp;Flash</td><td align="right">17.25</td><td align="right">33.07</td><td align="right">44.51</td><td align="right">33.03</td><td align="right">30.29</td></tr>
    <tr><td align="left">Minimax&nbsp;M3</td><td align="right">25.30</td><td align="right">27.20</td><td align="right">42.65</td><td align="right">32.09</td><td align="right">29.54</td></tr>
  </tbody>
</table>

### GDD-to-Game

<table width="820">
  <thead>
    <tr>
      <th align="left" width="170">Model</th>
      <th align="right" width="145">Mechanics</th>
      <th align="right" width="145">Content</th>
      <th align="right" width="145">Playability</th>
      <th align="right" width="145">VLM</th>
      <th align="right" width="70">Total</th>
    </tr>
  </thead>
  <tbody>
    <tr><td align="left">Qwen3.8&nbsp;Flash</td><td align="right">25.07</td><td align="right">32.25</td><td align="right">64.75</td><td align="right">49.61</td><td align="right">37.35</td></tr>
    <tr><td align="left">Grok4.6</td><td align="right">28.66</td><td align="right">53.58</td><td align="right">59.60</td><td align="right">60.20</td><td align="right">48.55</td></tr>
    <tr><td align="left">GPT-5.6&nbsp;Luna</td><td align="right">31.52</td><td align="right">53.52</td><td align="right">76.57</td><td align="right">67.06</td><td align="right">52.67</td></tr>
    <tr><td align="left">Opus5</td><td align="right"><strong>32.96</strong></td><td align="right"><strong>61.07</strong></td><td align="right"><strong>91.94</strong></td><td align="right"><strong>75.03</strong></td><td align="right"><strong>59.68</strong></td></tr>
    <tr><td align="left">GLM5.3&nbsp;Flash</td><td align="right">26.21</td><td align="right">37.83</td><td align="right">69.45</td><td align="right">45.84</td><td align="right">40.18</td></tr>
    <tr><td align="left">Minimax&nbsp;M3</td><td align="right">32.84</td><td align="right">38.05</td><td align="right">72.51</td><td align="right">46.19</td><td align="right">42.58</td></tr>
  </tbody>
</table>

### Skeleton Completion

<table width="820">
  <thead>
    <tr>
      <th align="left" width="170">Model</th>
      <th align="right" width="116">Mechanics</th>
      <th align="right" width="116">Content</th>
      <th align="right" width="116">Playability</th>
      <th align="right" width="116">Scaffold</th>
      <th align="right" width="116">VLM</th>
      <th align="right" width="70">Total</th>
    </tr>
  </thead>
  <tbody>
    <tr><td align="left">Qwen3.8&nbsp;Flash</td><td align="right">24.60</td><td align="right">20.99</td><td align="right">65.51</td><td align="right">91.15</td><td align="right">58.40</td><td align="right">39.19</td></tr>
    <tr><td align="left">Grok4.6</td><td align="right">26.92</td><td align="right">16.03</td><td align="right">80.75</td><td align="right">86.36</td><td align="right">70.08</td><td align="right">40.76</td></tr>
    <tr><td align="left">GPT-5.6&nbsp;Luna</td><td align="right">29.57</td><td align="right">17.70</td><td align="right">82.35</td><td align="right">88.44</td><td align="right">73.87</td><td align="right">43.10</td></tr>
    <tr><td align="left">Opus5</td><td align="right"><strong>36.22</strong></td><td align="right"><strong>36.93</strong></td><td align="right"><strong>93.77</strong></td><td align="right"><strong>94.62</strong></td><td align="right"><strong>78.36</strong></td><td align="right"><strong>54.06</strong></td></tr>
    <tr><td align="left">GLM5.3&nbsp;Flash</td><td align="right">22.63</td><td align="right">11.97</td><td align="right">82.70</td><td align="right">79.13</td><td align="right">49.90</td><td align="right">34.38</td></tr>
    <tr><td align="left">Minimax&nbsp;M3</td><td align="right">21.87</td><td align="right">20.03</td><td align="right">79.09</td><td align="right">74.39</td><td align="right">47.49</td><td align="right">35.69</td></tr>
  </tbody>
</table>

### Bug Repair

<table width="820">
  <thead>
    <tr>
      <th align="left" width="170">Model</th>
      <th align="right" width="145">Restoration</th>
      <th align="right" width="145">Retained<br>routes</th>
      <th align="right" width="145">Preservation<br>contracts</th>
      <th align="right" width="145">Validity<br>gates</th>
      <th align="right" width="70">Total</th>
    </tr>
  </thead>
  <tbody>
    <tr><td align="left">Qwen3.8&nbsp;Flash</td><td align="right">44.40</td><td align="right">98.61</td><td align="right">80.56</td><td align="right">98.61</td><td align="right">26.13</td></tr>
    <tr><td align="left">Grok4.6</td><td align="right">56.52</td><td align="right">96.88</td><td align="right">85.94</td><td align="right">99.72</td><td align="right">52.44</td></tr>
    <tr><td align="left">GPT-5.6&nbsp;Luna</td><td align="right">69.62</td><td align="right">98.35</td><td align="right">86.08</td><td align="right">99.72</td><td align="right">58.05</td></tr>
    <tr><td align="left">Opus5</td><td align="right"><strong>90.18</strong></td><td align="right"><strong>99.51</strong></td><td align="right"><strong>93.17</strong></td><td align="right">99.72</td><td align="right"><strong>83.46</strong></td></tr>
    <tr><td align="left">GLM5.3&nbsp;Flash</td><td align="right">44.34</td><td align="right">96.00</td><td align="right">75.51</td><td align="right"><strong>99.85</strong></td><td align="right">27.55</td></tr>
    <tr><td align="left">Minimax&nbsp;M3</td><td align="right">35.27</td><td align="right">96.05</td><td align="right">76.32</td><td align="right">94.74</td><td align="right">27.36</td></tr>
  </tbody>
</table>

### Godot-to-Unity Porting

<table width="820">
  <thead>
    <tr>
      <th align="left" width="170">Model</th>
      <th align="right" width="116">Mechanics</th>
      <th align="right" width="116">Playability</th>
      <th align="right" width="116">Structure</th>
      <th align="right" width="116">Visual</th>
      <th align="right" width="116">Stability</th>
      <th align="right" width="70">Total</th>
    </tr>
  </thead>
  <tbody>
    <tr><td align="left">Qwen3.8&nbsp;Flash</td><td align="right">57.31</td><td align="right">50.39</td><td align="right">50.27</td><td align="right">36.78</td><td align="right">55.20</td><td align="right">51.23</td></tr>
    <tr><td align="left">Grok4.6</td><td align="right">61.71</td><td align="right">50.26</td><td align="right">78.76</td><td align="right">35.40</td><td align="right">77.50</td><td align="right">59.04</td></tr>
    <tr><td align="left">GPT-5.6&nbsp;Luna</td><td align="right">63.26</td><td align="right">51.44</td><td align="right">82.87</td><td align="right">31.60</td><td align="right">76.80</td><td align="right">59.85</td></tr>
    <tr><td align="left">Opus5</td><td align="right"><strong>70.66</strong></td><td align="right"><strong>65.88</strong></td><td align="right"><strong>90.53</strong></td><td align="right"><strong>58.40</strong></td><td align="right"><strong>88.60</strong></td><td align="right"><strong>72.40</strong></td></tr>
    <tr><td align="left">GLM5.3&nbsp;Flash</td><td align="right">50.37</td><td align="right">38.32</td><td align="right">63.13</td><td align="right">12.13</td><td align="right">58.30</td><td align="right">44.33</td></tr>
    <tr><td align="left">Minimax&nbsp;M3</td><td align="right">53.44</td><td align="right">48.31</td><td align="right">42.74</td><td align="right">49.28</td><td align="right">50.32</td><td align="right">49.62</td></tr>
  </tbody>
</table>

**Reading the table.** Bold marks the best result in each column within a task. Content includes asset realization; Design assesses the authored GDD; Scaffold assesses completion and integration. For construction tasks, Mechanics includes certified reference-input routes, and Playability covers demonstration validity, feature coverage, and behavior relative to matched no-input controls. Totals follow [Section 3.4](https://arxiv.org/html/2609.33678v1#S3.SS4); Bug Repair totals average per-task products of the component factors.

## Evaluation

Evaluation combines two evidence domains:

- **Objective Behavioral Evaluation** executes the submitted project and checks input responses, observable mechanics, progression, and mode-specific requirements. Matched no-input controls help determine whether demonstrated behavior depends on player action.
- **Perceptual Quality Assessment** uses the paper’s four rubric groups: visible mechanics, design and content, functional visual communication, and art.

The three construction tasks allocate 85 objective points and 15 game-specific VLM points. Bug Repair measures behavioral restoration and preservation. Godot-to-Unity Porting scores Mechanics 35, Playability 25, Structure 15, Visual 15, and Stability 10 using independently collected runtime, Editor, and artifact evidence. Its Visual component measures implementation correspondence, and its leaderboard uses the arithmetic task mean. See the [Mode 5 scoring contract](docs/reference/MODE5_SCORING.md).

For construction tasks, enable `--visual-judge vlm` to obtain the complete score; visual judging is disabled by default. Brief Design scoring is optional: use `--brief-design on` to include authored GDD quality. Mode 5 uses the fixed `2026-10.mode5-evidence-five-visual1` protocol without a VLM. Record the evaluator commit, registry, model, harness, budget, task materials, and input settings with each run. See the [scoring specification](docs/reference/HIERARCHICAL_MULTI_EVIDENCE_SCORECARD.md).

## Quick Start

The runner targets Linux x86-64 (Ubuntu 22.04/24.04), with root or sudo and user-namespace support. Setup installs Godot **4.5.1** and the pinned agent CLIs. Porting uses the [Community Docker workflow](docs/reference/MODE5_RELEASE.md), with licensed Unity **6000.3.23f1** in independent agent and evaluator containers.

```bash
git clone --depth 1 https://github.com/Charly-chan/SWE-Game.git
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
  --out results/brief-dry-run

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

### Mode 5: Community Docker

```bash
./gb mode5 setup
./gb mode5 doctor --license-provider existing-home --unity-config-root "$HOME"
./gb mode5 run --game cat_defense --harness claude \
  --claude-settings "$HOME/.claude/settings.json" --out /absolute/results/cat-defense-port
```

Setup and doctor prepare the locally built, licensed toolchain. Run installs a fixed task,
launches the agent, rebuilds its submission in a fresh offline evaluator, and retains
runtime evidence and reports. See the [Mode 5 guide](docs/reference/MODE5_RELEASE.md).
Mode 5 fixed packages include the Community scaffold and the updated calibrated
runtime contracts for all 41 games. The default agent budget is 7200 seconds.

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
| [`.github/`](.github/AUTOMATION.md) | Automated tests, container publishing, and the pull request template |
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
| [Godot-to-Unity Porting](docs/reference/MODE5_RELEASE.md) | Cross-engine setup and evaluation |
| [Documentation index](docs/README.md) | All guides and detailed contracts |

## License

Project-owned benchmark code and documentation are licensed under [Apache-2.0](LICENSE).
Reference games, assets, recordings, and reused code retain their component licenses;
see [third-party notices](THIRD_PARTY_NOTICES.md).

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
