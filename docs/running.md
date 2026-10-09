# 运行指南：环境、模型配置与批量评测

本指南介绍依赖安装、模型连接、批量运行和结果保存。本版支持五种模式：
`brief`、`gdd`、`skeleton`、`bugfix`，以及 Mode 5 `port`（Godot→Unity）。`port` 另需 Unity
6000.3.23f1 工具链与有效许可（§9）。运行前核对固定任务包内的校准状态；未校准任务不能进入正式排名。

报告中的**客观行为评测（Objective Behavioral Evaluation）**对应运行与状态检查，
**感知质量评审（Perceptual Quality Assessment）**对应视觉质量读数。
内部 `ocard` / `scard` 字段、通道编号、权重和结果目录不变；评审是否实际运行、
能否计分仍由本次运行配置和 registry 决定。字段映射见
[输出契约](reference/OUTPUT_CONTRACT.md#paper-terminology-and-serialized-fields)。

## 1. 机器

- 任务下载和评测使用 Linux x86_64（Ubuntu 22.04/24.04）。Mode 5 使用已授权的
  Community Docker 工具链，Windows 用户在 WSL2 内运行，详见 §9。
- root 或 sudo（`setup.sh` 装 apt 包、`/opt/godot-4.5.1`、`/opt/gamebench`）。
- 内核允许非特权 user namespaces：`unshare --user --map-root-user --mount --pid --fork --mount-proc true`
  要成功；`setup.sh --check` 的 `user namespaces` 行会告诉你。
- Xvfb + ffmpeg（setup 装）。每个 cell 的评测阶段会起 Godot 进程做冷启动、路线回放和
  `xvfb-run` 拍片；agent 阶段模型自己也可能在沙箱里跑 Godot。
- 磁盘：代码仓库之外，参考工程、素材和录像从 [Hugging Face](https://huggingface.co/datasets/Charly-chan/SWE-Game)
  按游戏下载；完整数据的体积及缓存说明见 [数据下载](reference-data.md)。每个 cell
  的 package/workspace/submission/evaluation（几十到几百 MB）+ scratch（`GB_SCRATCH_ROOT`，
  默认 `/tmp/swe-game/gb_taskgen_scratch` 或仓库内 `.scratch/`）。一组
  模型/harness 的 Modes 1–4 共 205 个 cell（含 Mode 5 则 246 个）；预留 100 GB 以上。
- 并发：`run_benchmark.sh --concurrency N` 同时最多 N 个 cell；调度器在 1 分钟负载 > 60
  时暂停派发。一个 cell 高峰约 2–4 个核（Godot 导入/回放 + Xvfb + ffmpeg）加模型 CLI；
  参照机 96 核用 3–8。每个 cell 的 agent 时间默认**不设上限**（跑到 agent 自己退出；实测
  wall 时间记在 `summary.csv` 的 `wall_s`），只有显式 `--budget <秒>` 才加硬上限；之后评测 5–15 分钟。

## 2. 安装与自检

```bash
GIT_LFS_SKIP_SMUDGE=1 git clone --depth 1 --single-branch --branch main https://github.com/Charly-chan/SWE-Game.git
cd SWE-Game
sudo ./setup.sh           # 末尾打依赖表，0 required missing 才算成功（exit 0）
./setup.sh --check
```

使用 `GIT_LFS_SKIP_SMUDGE=1` 可推迟下载仓库中剩余的 LFS 附件；参考工程和录像由任务生成入口从固定的 Hugging Face 数据版本按需获取。
数据版本由 `data/task-data.json` 固定。

`setup.sh` 固定装：Godot 4.5.1（官方二进制，SHA-256 校验）、Claude Code `2.1.222`、
Codex CLI `0.153.4`（都装进私有 npm 前缀 `/opt/gamebench/node_modules`，shim 在
`/opt/gamebench/bin/{claude,codex}`；不碰 `npm -g`）、Python venv `.venv`、Git LFS filters。
版本由 `eval/tools/gb_env.sh` 里 `GB_CLAUDE_CODE_VERSION` / `GB_CODEX_VERSION` 决定。
模型对比应固定 CLI 版本，并在运行记录中保留版本信息。

## 3. 模型连接配置

模板：仓库根 `.gb_api.env.example`。复制到仓库外，查找顺序：`$GB_API_ENV` →
`~/.config/gamebench/gb_api.env` → `/tmp/swe-game/.gb_api.env`。`setup.sh`
在没有文件时把模板写到 `~/.config/gamebench/gb_api.env`（0600）。

```bash
cp .gb_api.env.example ~/.config/gamebench/gb_api.env
$EDITOR ~/.config/gamebench/gb_api.env
./setup.sh --check-auth        # 每个填了的 key 发一次不计费探针
```

配置字段（bash `set -a` source，KEY=VALUE / export / unset 行都行）：

| 变量 | 谁用 | 空着 = | 填了 = |
|---|---|---|---|
| `OPENAI_API_KEY` | Codex | 用 Codex 自己的 ChatGPT 登录（跑之前 `codex login` 一次；harness 把 `~/.codex/auth.json` 复制进每格私有 `CODEX_HOME`） | API key 认证（`env_key=OPENAI_API_KEY`） |
| `OPENAI_BASE_URL` | Codex | 官方 api.openai.com | 该网关；harness 用 `-c model_provider="gamebench_proxy" -c model_providers.gamebench_proxy.{base_url,env_key,wire_api="responses"}` 整块传参，**不碰 `~/.codex/config.toml`**。网关必须支持 Responses API（`POST {base}/responses`），使用其他协议的网关时需自行配置兼容代理 |
| `ANTHROPIC_API_KEY` | Claude Code | — | 只导出进 agent 环境（每格私有 `CLAUDE_CONFIG_DIR`，不读写 `~/.claude`）；使用 Claude Pro/Max 时改传 `--provider anthropic`，runner 将 `~/.claude/.credentials.json` 复制到该临时目录 |
| `ANTHROPIC_AUTH_TOKEN` | Claude Code | 用 `ANTHROPIC_API_KEY` | 端点要 `Authorization: Bearer` 时（适用于要求 Bearer 认证的端点）代替 `ANTHROPIC_API_KEY` 填这个；`ANTHROPIC_API_KEY` 空时路线与 `--check-auth` 探针都用它（Bearer），同样只导出进 agent 环境。模板里默认注释掉 |
| `ANTHROPIC_BASE_URL` | Claude Code | 官方 api.anthropic.com | 任意 Anthropic-compatible 端点 |

Claude Pro/Max 跨宿主运行（例如 Windows 登录、WSL 执行 evaluator）不要直接复制
`.credentials.json`：订阅登录可能受设备或平台约束。应在账号所在宿主执行 `claude setup-token`，
把生成的长期 token 写入仓库外的 env 文件：

```bash
GB_CLAUDE_AUTH_MODE=oauth
CLAUDE_CODE_OAUTH_TOKEN=<setup-token 输出；不要提交到仓库>
```

随后让 `bench run-task-matrix` 读取该 env 文件。OAuth 模式会清除 child 中的
`ANTHROPIC_API_KEY` 与 `ANTHROPIC_BASE_URL`，不加 `--bare`，token 会按其他密钥同样脱敏。
同一 WSL 用户已经能够直接 `claude auth status` 且目标模型有权限时，也可以用
`run_benchmark.sh --provider anthropic`；它把凭据复制进每格私有临时目录，结束后删除。

可选 VLM 判官（`--visual-judge vlm` / `evaluate.sh`）沿用 evaluator 原有变量 `GAMEBENCH_VLM_PROVIDER` /
`GAMEBENCH_VLM_MODEL` / `GAMEBENCH_VLM_BASE_URL` / `GAMEBENCH_VLM_KEY_ENV`。
`run_benchmark.sh` 复用该格的 coding-agent 路线；独立 `evaluate.sh` 使用外部文件里的判官配置，
建议显式填写模型、地址及 key 变量名。Claude Code 判官使用 `GAMEBENCH_VLM_PROVIDER=claude_code`；
完整配置见 [quickstart](quickstart.md#evaluation-commands)。
旧名字继续有效：`MICU_API_KEY`（`OPENAI_API_KEY` 空时它意味着 MICU 网关）、`GB_CODEX_BASE_URL` /
`GB_CODEX_KEY_ENV` / `GB_CLAUDE_BASE_URL` / `GB_CLAUDE_KEY_ENV`（分别是上表 URL / key 变量**名**的别名）。

`./setup.sh --check-auth` 每个 harness 一行：`ok` / `401 unauthorized` / `no Responses API`（网关不接
`POST /responses`，Codex 跑不了）/ `unreachable`；`OPENAI_API_KEY` 空时报 `codex login status`。探针是
`GET {base}/models`、Codex 网关再加一个空 body 的 `POST {base}/responses`（4xx = 端点存在）、Claude 是
`GET {base}/v1/models`（没有则退到空 body `POST /v1/messages`，400 = key 被接受），15 s 超时，不打印任何值。

runner 对凭据进行脱敏：`bench run-task-matrix` 只接收**变量名**（`--agent-key-env`），
`agent/request.json` / `env.json` / `stdout|stderr.log` 里的值经 `_redact` 换成 `<redacted>`；
Codex 侧还用 `shell_environment_policy.filters` 把 key 从模型能起的 shell 里剔掉；`run.json`
不含凭据。`--dry-run` 读这个文件只为决定路线（打印的 `harness argv` 里能看到 provider 块），不需要 key。

## 4. 比较条件与沙箱

固定任务包、evaluator commit、harness 和 CLI 版本、reasoning、预算、输入设置及判官配置。
默认 agent 无 wall-clock 上限，直到自行退出；`--budget <秒>` 可设置上限。
`--reasoning` 接受 `low|medium|high`，不同模型对这些设置的支持需在运行记录中确认。
`--playtest-kit on` 和 Mode 1 的 `--reference-video off` 都是独立输入条件。

默认 `--visual-judge none` 只完成客观评测。矩阵内启用 `vlm` 会复用该格的模型路线；
统一模型对比可保留原始提交，再用固定判官执行独立 `evaluate.sh`。
修改 harness 或评测器后应重新生成或验证受影响证据，保留旧报告。

`unshare` 是当前正式运行策略使用的 agent 沙箱。若宿主不支持所需 user/mount
namespaces，可使用 `--sandbox docker`。容器挂载该格工作区，并运行镜像内的 agent
工具；宿主负责调度、收集及独立评测。仓库的 Dockerfile 固定 CLI 和引擎版本。
当前协议仍将 Docker agent 运行记录为 `formal_eligible=false`；每格偏差记录在
`agent/request.json`，实际工具和镜像信息记录在 `agent/env.json`。
比较结果时应保留此环境区别，不能仅凭镜像安装成功宣称满足正式条件。
完整配置见 [Docker 指南](../docker/README.md)。

运行后保留 `submission/` 原样。清理异常中断留下的容器可使用
`bash eval/tools/gb_docker_sweep.sh --remove`，该工具按 benchmark 标签与 owner 状态筛选。

## 5. 小规模运行验证

另有一组仅针对 Mode 1 的输入消融：在 `--mode brief` 时加 `--reference-video off`，
不给 agent 参考录像，其余输入和评测不变。默认 `on` 是原条件。两组必须使用不同 `--out`
目录并保持模型/harness、reasoning、预算政策、playtest-kit 和 visual-judge 相同；不能用
`--visual-judge none` 代替删除输入视频。先加 `--dry-run` 核对任务包；完整对照命令见
[快速上手](quickstart.md#mode-1-input-ablation-no-reference-video)。

```bash
MODEL_ID="your-model-id"
./run_benchmark.sh --game canopy_dash --mode gdd --harness codex --model "$MODEL_ID" --dry-run
./run_benchmark.sh --game canopy_dash --mode gdd --harness codex --model "$MODEL_ID" --out results/smoke_gdd
./run_benchmark.sh --game canopy_dash --mode bugfix --harness codex --model "$MODEL_ID" \
  --case-id canopy-dash-plank-hold-clock-v1 --out results/smoke_bugfix
```

检查 `agent/events.jsonl` 中的工具调用、收集到的工程、`agent/usage.json` 的用量，以及
`evaluation/report.json` 的完成状态。模型提交未通过任务也会生成报告；这与调用链路失败
不同。默认 VLM 关闭时，Modes 1–3 的综合分为空是预期行为，应查看客观分及证据缺口。

## 6. 批量运行与基线

模型标识由已配置的 provider 决定；不同模型分别使用独立输出目录。

全量：`--game all`。每种模式各起一个 run（`--mode` 一次只接一个值）：

```bash
for m in brief gdd skeleton bugfix; do
  ./run_benchmark.sh --game all --mode $m --harness codex --model "$MODEL_ID" --provider openai \
    --concurrency 4 --out results/codex_model_$m
done
```

**Mode 4 = 82 个 active case**：`--mode bugfix --game all` 一格一个 active case（41 款，每款 1–3 个），
`--game <id>` 跑该游戏的全部 case，`--case-id <id>` 只跑一个。格目录多一段 case id：
`cells/<game>__bugfix__<harness>__<model>__<case_id>/`，`summary.csv` 多一列 `case_id`。
每个 bugfix 包生成时评测器都用 Godot 对源版和缺陷版做一遍差分复核（耗时取决于路线长度），且所选包在调度器启动前**顺序**生成。
要压时间就按 `--game` 拆成几个 shell 并行，各用不同 `--out`：

```bash
for g in canopy_dash wizard_chase shadow_walker; do
  ./run_benchmark.sh --game $g --mode bugfix --harness codex --model "$MODEL_ID" --provider openai \
    --concurrency 2 --out results/codex_model_bugfix_$g &
done; wait
```

**Tier 3 无模型对照**：`run_benchmark.sh` 没有 null harness（`--harness` 只有 `claude|codex`）。
对照用 `bench run-task-matrix` 的 `command` 后端跑一个什么都不做的命令，评测器收走未改动的
工作区（skeleton = 原样骨架，bugfix = 未修的缺陷工程）：

```bash
cd eval/evalsys
PYTHONPATH=. ../../.venv/bin/python3 bin/bench run-task-matrix \
  --out ../../results/null_control_skeleton --mode skeleton \
  --agent-backend command --agent-command true --agent-timeout 60 \
  --agent-sandbox unshare --engine auto --visual-judge none
PYTHONPATH=. ../../.venv/bin/python3 bin/bench run-task-matrix \
  --out ../../results/null_control_bugfix --mode bugfix \
  --agent-backend command --agent-command true --agent-timeout 60 \
  --agent-sandbox unshare --engine auto --visual-judge none
# 先试一格：加 --game canopy_dash（bugfix 再加 --case-id canopy-dash-plank-hold-clock-v1）
```

brief/gdd 没有起始工程——工作区里只有材料，没有任何 Godot 项目可交——`command` 后端会报
"agent returned no unambiguous Godot project"，所以这两种模式没有有意义的无模型对照。
已实测 canopy_dash/skeleton 的未修改骨架和
canopy_dash/bugfix/canopy-dash-plank-hold-clock-v1 的未修缺陷工程，均为 `resolved=false`；
后者在当前 `2026-09-15.mode4-redesign1` 下为 0/100。有 cell 未 resolved 时矩阵命令退出码为 1，对照跑法这是预期。
（`visible/playtest/null_control.sh` 是打包给模型自用的同长空输入回放工具，与此无关。）

第三方模型需通过相应 harness 支持的接口调用。Codex 路线使用 Responses API；
Claude Code 路线使用 Anthropic-compatible API。若通过协议代理接入，先验证文件读写、
shell 工具调用与返回结果，并记录代理版本及被忽略的参数。参数被接受不代表模型执行了
相同 reasoning 条件。

## 7. 规模与费用

每组模型/harness：Mode 1–3 各 41 格 + Mode 4 82 格 = 205 格；Mode 5 再加 41 格，共 246 格（需 Unity，§9）。
费用取决于模型、provider、任务时长、工具调用和评测设置。
先根据小规模运行的 `agent/usage.json` 估算，再设置并发与预算；未记录费用时不得按零计。
引擎运行与 VLM 判官的资源消耗也应单独记录。

## 8. 保存与复现

整个 `results/<run_id>/` 目录，按 [`docs/reference/OUTPUT_CONTRACT.md`](reference/OUTPUT_CONTRACT.md)：

- `run.json`、`summary.csv`、`summary.json`、`leaderboard.md`
- 每格 `cells/<game>__<mode>__<harness>__<model>[__<case_id>]/`：
  - `submission/`（保留原样：`game/`、`ops.json`/`demos.json`、`GDD.md`、`collection.json`。
    重新评测需要原始提交）
  - `agent/`（`events.jsonl` 轨迹、`usage.json`、`prompt.md`、`env.json`、`request.json`、`stdout.log`、`stderr.log`）
  - `evaluation/`（`report.json`、`report.md`、`card.json`、`reproduction/` 含回放帧/视频）
  - `package/`（生成的任务包，便于复现）
  - `films/`（如已生成）
- 无模型对照（`bench run-task-matrix` 直跑）保存完整目录，`runs/<game>/<mode>/<case>/` 同样含 `submission/ agent/ evaluation/`。

不要压缩前删除任何 `submission/`。打包：`tar -I zstd -cf results_<run_id>.tar.zst results/<run_id>`。

## 9. Mode 5 (port): Community Docker

Mode 5 使用 [独立 Community 工作流](reference/MODE5_RELEASE.md)。在 Linux/WSL 上运行
`./gb mode5 setup` 和 `./gb mode5 doctor`，通过授权与真实构建检查后，
`./gb mode5 run` 完成移植、独立构建、客观评测与证据保留。
`run_benchmark.sh --mode port` 转发到相同流程；`--dry-run` 只预览固定任务包。

五项权重为 35/25/15/15/10，registry 为 `2026-10.mode5-evidence-five-visual1`。
评测按独立运行、Editor、静态支持和文件存在证据计分；Visual 使用 evaluator
自有的 `visual_implementation_correspondence` 测量，不调用 VLM，也不测感知或美学相似度。
运行时实现对应证据可取得完整 15 分；任务总分使用固定分母，由 `weighted_total.score` 给出。
保留证据可用 `gb mode5 rejudge` 按相同 registry 重评。
预算、恢复、许可及运行环境边界见用户指南和 [评分协议](reference/MODE5_RELEASE_PROTOCOL.md)。

## 运行与结果配置

- Unity 授权与 Docker preflight 必须通过；环境失败保留为缺测状态。
- Visual 按实现对应证据计分，运行时义务全部验证时可取得 15/15。
- 完整模型均分要求固定 41 游戏全部完成测量，并记录 `community-docker` 环境及评分 registry。
- Modes 1–3 默认 `2026-09-19.modeN-vlm1`，Mode 4 默认 `2026-09-15.mode4-redesign1`。
  原始提交、固定任务包与评分配置应随结果保留。

## Harbor orchestration

For Harbor 0.23.0 task export, native agents, Docker lifecycle management, and
canonical SWE-Game score summaries, see [the Harbor guide](harbor.md).
