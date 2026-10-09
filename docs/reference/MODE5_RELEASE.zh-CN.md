# Mode 5：Community Docker 运行指南

Mode 5（`port`）让 coding agent 把 Godot 游戏移植为原生 Unity 游戏。公开运行使用 Community Docker 链路；评分、输入及隔离约定见 [Mode 5 协议](MODE5_RELEASE_PROTOCOL.md)。

首次安装依赖并激活自己的 Unity 许可后，一条 `./gb mode5 run` 完成任务生成、Agent 移植、提交收集、独立构建、客观评测、录像和报告。它保证执行评测流程，不保证任意模型的游戏一定编译成功或通过测试。

## 1. 安装一次

需要 Linux x86_64、Python 3.10+、以及当前用户可访问的 Linux Docker daemon。Windows 用户在自己的 WSL2 Linux 发行版内执行下面的命令；Docker daemon 必须属于同一个已激活 Unity 的 Linux 环境。

从仓库根目录执行，不需要先安装宿主机 Godot/Unity Editor，也不需要运行 Modes 1–4：

```bash
python3 -m venv .venv
.venv/bin/python -m pip install -r eval/evalsys/requirements.txt
export GB_PYTHON="$PWD/.venv/bin/python"
docker info
./gb mode5 setup
```

`setup` 下载固定的官方 Unity archives，本地构建 Agent/evaluator 镜像并记录版本。没有配置许可时返回 `built_needs_license`，并不代表已经可以运行 Agent。固定工具链为 Unity **6000.3.23f1 / StandaloneLinux64 / Mono**；镜像包括 Editor、Linux Player 支持、模型 CLI、公开 SDK 工具及 `gb-unity`。

首次安装需要网络，Editor 镜像约十几 GB，下载包、Docker 缓存和结果还需要额外空间。Windows 应检查承载 WSL 虚拟盘的物理分区，而不是只看 Linux 内的可用容量。Unity Editor 不从本仓库、HF 或容器 registry 分发，已激活镜像也不公开分发。

已有官方下载包可避免重复下载：

```bash
./gb mode5 setup --unity-archives /absolute/path/to/archives --no-download
```

## 2. 配置自己的 Unity 授权

Unity 许可不能由仓库代为分发。Personal 用户在同一个 Linux/WSL coordinator 上，通过 Unity Hub 登录并激活自己有权使用的许可；然后运行：

```bash
./gb mode5 doctor --license-provider existing-home --unity-config-root "$HOME"
```

`--unity-config-root` 是 **Hub 已激活的 Linux home 根目录**，不是 license 文件路径；其中应有 `.config/unity3d/`、`.local/share/unity3d/` 的授权状态。也可以指向专用私有 home。该根目录不得 group/world-readable；如是自己的 Linux home，可用 `chmod 700 "$HOME"` 收紧权限后重试。工具只读取对应 Unity 状态，不把整个 home 打包进镜像。

Docker 必须在激活所用的同一 Linux/WSL 环境中运行：`existing-home` 会只读映射该宿主的机器身份。只把 Windows 或其他机器的授权文件复制过来不等于合法且有效的激活。实际可用性以 doctor 为准；不能用“已装 Unity/已登录 Hub”替代检查。

其他有权使用的许可方式：

```bash
# 适用于有权使用且为当前环境签发的许可文件，不是 Personal 的通用激活方式
./gb mode5 doctor --license-provider file --license-file /absolute/private/license.ulf

# 适用于机构合法配置的浮动授权
./gb mode5 doctor --license-provider floating --floating-endpoint YOUR_LICENSE_ENDPOINT
```

file/existing-home 来源需要私有权限；不提交 ALF/ULF、授权 home、machine-id 或 provider 配置。floating 的 lease 必须支持后续离线运行；需要持续联网的授权服务不适用于此 evaluator。

doctor 必须显示 `status: pass`，真实检查授权、依赖、C# 编译、Linux Player 构建、离线 runtime/capture 和清理。授权失败时停止在 Agent 之前，不花模型调用费尝试绕过它。配置保存在当前用户的 `~/.cache/gamebench/mode5`；可通过 `GB_MODE5_HOME` 或命令的 `--state-dir` 使用其他私有位置。

如果许可已经激活，可把本地构建和 doctor 合并为一条准备命令：

```bash
./gb mode5 setup --license-provider existing-home --unity-config-root "$HOME"
```

## 3. 一条命令运行一个游戏

使用已有 Claude Code 的 API provider 配置（包括 Anthropic-compatible 模型）：

```bash
export GB_PYTHON="$PWD/.venv/bin/python"
./gb mode5 run --game cat_defense --harness claude \
  --claude-settings "$HOME/.claude/settings.json" \
  --out "$HOME/gamebench-results/cat-defense-port"
```

settings 必须含 API credential 和模型配置；纯 OAuth 登录不等于该选项所需的 API settings。工具只读取必要 model/provider/credential，不复制个人 history/hooks/plugins。若文件没有模型，另加 `--model YOUR_MODEL_ID`。Agent 使用镜像中固定的 Claude Code，不依赖宿主机 CLI 版本。

使用自有 OpenAI API key 的 Codex：

```bash
# 事先在当前 shell 的私有环境中配置 OPENAI_API_KEY，不把 key 写入仓库
./gb mode5 run --game cat_defense --harness codex \
  --agent-provider openai --model YOUR_MODEL_ID \
  --out "$HOME/gamebench-results/cat-defense-port"
```

自定义 API provider 可显式设置 `--agent-base-url`、`--agent-key-env`，或通过 `--agent-env-file /absolute/private/provider.env` 注入私有环境。不要把凭证直接写入命令行或公开文件。

默认 Agent 固定预算 **7200 秒 / 120 分钟**，用 `--agent-timeout SECONDS` 设置正整数预算；0 不表示无限。Unity 子命令上限 1200 秒，evaluator 有独立超时。预算只限制 Agent，不是整条链路的总耗时；不同预算的结果不混排，也不代表论文原实验预算。

Mode 5 使用 `codex/mode5-main-integration` 提交 `01f4a2c1` 中的新版校准合同，41 个游戏均为 `calibrated/runtime_ready`。校准合同和 Community scaffold 在发布时写入固定包；运行时下载、校验并安装，不临时重编译 suite 或改造 scaffold。

Windows 用户在 WSL 内使用上述同样的命令。推荐把仓库、虚拟环境和输出放在 Linux 原生目录；`/mnt/c`、`/mnt/d` 的大量 Unity 缓存 I/O 可能明显更慢。每次新 shell 重新设置 `GB_PYTHON`，或直接使用已激活的虚拟环境。

运行顺序：

```text
HF 安装并校验固定任务包（源工程/素材/主录像/已校准隐藏 suite/Community scaffold）→ 已授权 Unity Agent
→ 阅读源代码/GDD/素材/视频 → 编写并自检 Unity 项目
→ 收集原始 submission → 全新离线 evaluator 独立重建
→ witness/no-input/hidden/counterfactual → capture → report/manifest → 汇总
```

scaffold 是可编译的起点和 SDK，不是已移植好的游戏。Agent 修改后仍可能有 C# 错误、资源路径或场景绑定问题。`gb-unity smoke` 只检查短窗口内的启动/异常并停止 Player/Xvfb，不替代完整 gameplay/interface 测试。

## 4. 输出、恢复与独立评测

`--out` 使用新目录，保留整个结果目录：

```text
RUN/
  package/                       任务包；只把 visible 提供给 Agent
  agent/                         Agent transcript、版本、预算和状态
  submission/                    原始 Unity 提交
  evaluation/report.json         权威评测报告
  evaluation/report.md           可读报告
  evaluation/runtime-evidence/   evaluator 捕获的运行证据
  evaluation/artifact-manifest.json
  mode5-state.json
  summary.json / summary.csv / leaderboard.md
```

报告中的候选失败与环境失败分开；Agent exit 0 或 helper pass 不是游戏通过。结果目录可能含模型对话和原始提交，公开前自行审阅，不把运行目录当作可直接发布的安全包。

中断后使用原命令、相同配置和 `--resume` 恢复。已完成 cell 不重复跑 Agent。若评测输出不完整，工具会保留旧目录再重试；传输失败不会把原始 scaffold 冒充模型提交。

已有提交可用一条命令独立评测，不再调用 Agent：

```bash
./gb mode5 evaluate --package /absolute/results/RUN/package \
  --submission /absolute/results/RUN/submission --out /absolute/results/new-evaluation
```

重新汇总：

```bash
./gb mode5 summarize /absolute/results/RUN
```

主入口 `run_benchmark.sh --mode port` 也转发到同一 Community evaluator。
`--dry-run` 只通过同一 Community 生成器预览任务包，不要求宿主机安装模型 CLI，
不启动 Docker/Agent，不验证授权，也不生成评测成绩。

## 5. 当前评分边界

评分 registry：`2026-10.mode5-evidence-five-visual1`。五项固定权重为 **Mechanics 35 + Playability 25 + Structure 15 + Visual 15 + Stability 10**，二十个子项依据运行、Editor、静态支持及文件存在证据计分。不调用 VLM；Visual 使用 evaluator 自有的 `visual_implementation_correspondence` 测量，可由运行时实现对应证据取得完整 15 分，不是感知或美学相似度评分。

每任务输出单一 `/100` 代理分及完整证据明细。编译失败保留独立静态分，通关 5 分仅接受可信因果运行证据。环境/传输故障不可排名，不能伪造候选零分。严格通关 `resolved` 与代理分分开。

可将 controller 保留证据重新发布到新目录，不重跑 Agent/Unity、不调用 judge：

```bash
./gb mode5 rejudge --evaluation /absolute/results/RUN/evaluation \
  --package /absolute/results/RUN/package --out /absolute/results/republished
```

manifest 只检查路径和大小，允许额外笔记，不提供密码学防篡改保证。主榜为固定 41 游戏的算术均值，旁列展示低尾均值和 70/30 可靠性诊断；部分运行仅展示 coverage 和 partial mean。

使用 `weighted_total.score` 读取 0–100 任务总分，并随结果保存 registry 和 `environment_class=community-docker`。字段定义见 [输出契约](OUTPUT_CONTRACT.md)，计算方法见 [完整评分规范](MODE5_SCORING.md)。

## 6. Harbor 与常见问题

Harbor grader 使用同一 Docker evaluator，不另造评分逻辑；但 Harbor 自己管理 Agent 容器。`export` 仅接受 doctor 已通过的 **floating** provider，默认 Mode 5 Agent 预算也是 7200 秒。Personal/file/existing-home 不支持 Harbor 私密注入，export 会明确拒绝；这些用户使用已支持的 `gb mode5 run`。详见 [Harbor 指南](../harbor.md)。

- Docker daemon 不可达：在当前 Linux/WSL shell 先确认 `docker info`；不要对外暴露无认证 Docker TCP 端口。
- doctor 授权失败：检查 same-host Linux Hub 激活、目录私有权限和 provider；不重新跑模型。
- 镜像版本不匹配：重跑 setup，再 doctor；不能拿旧 preflight 证明新镜像有效。
- 参考工程/素材/主录像缺失：任务生成会从固定 HF 版本按需下载；可用 `python scripts/fetch_reference_data.py --game GAME_ID --with-videos` 预取，见[数据指南](../reference-data.md)。任务与校准合同均来自 `data/task-data.json` 固定的校验数据包。
- Unity compile/runtime 失败：看 Agent selfcheck 与 evaluator report/log；起始 scaffold 可用不保证模型改动正确。
- 总分 null：看未测/基础设施归因；代理总分缺测表示存在环境/传输/完整性问题，不是等待 VLM。
