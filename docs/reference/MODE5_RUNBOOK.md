# Mode 5 使用手册：环境、认证、启动与报告

本文面向第一次运行 Mode 5 的使用者。基础设施维护者如果需要重建 Unity 镜像、处理 license 或修改 sandbox，请同时阅读 [Unity 基础设施手册](../../eval/infra/unity/README.md)。

## 1. 先分清你要做哪件事

| 工作 | 是否每次评测都做 |
|---|---|
| 准备 package 和 submission | 是 |
| 用现有认证 VM 评测一个候选 | 是 |
| 启动 VLM 二阶段 | 按需 |
| 重新认证 Unity VM | 只有环境、版本、license 或 sandbox 变化时 |

固定任务包中，40 个游戏的 suite 已通过校准；`shadow_walker` 的当前 suite 尚未通过校准，不能进入正式排名。不要手工修改 `calibration_status.json`、suite digest 或 `score_eligible`。

## 2. 正式环境要求

正式 score-bearing Mode 5 必须使用认证 disposable Ubuntu VM，而不是普通 Windows Unity、普通 WSL 或普通容器。

宿主机需要 Windows x64、PowerShell、QEMU/WHPX、WSL Ubuntu（只作为磁盘/ISO 准备工具）、足够的 NTFS 空间、认证 Ubuntu 24.04 guest、Unity 6000.3.23f1 Linux Editor 和 Linux IL2CPP、隔离的 Unity license state、Xvfb/Mesa、ffmpeg，以及当前 profile/base image/license image/evidence digest。

候选 Unity 工程必须在 guest 中导入、编译、运行和采集。QEMU 使用 `-nic none`，候选不能访问网络；controller 和 evaluator 负责 hidden route、观测和报告。

## 3. 认证检查

### 3.1 平台认证

环境变化后按顺序执行：

1. Unity blank fixture certification；
2. sandbox certification；
3. controller/observer/artifact certification；
4. profile digest 和 license isolation 校验；
5. 确认 guest 无候选网络、Xvfb 和 renderer 正常。

平台认证失败是 infrastructure 问题，不能记候选 0 分。

### 3.2 任务状态

读取任务包内 `hidden/unity/behavior/calibration_status.json`。
只有 `status=calibrated` 且 `runtime_ready=true` 的固定 suite 可以进入正式评测。
不要手工修改状态或 digest；评测器会校验一致性。

## 4. 准备任务包

```powershell
$PY = 'C:\path\to\python.exe'
$env:PYTHONPATH = (Resolve-Path eval/evalsys).Path

& $PY eval/evalsys/bin/bench gen-task `
  --mode port `
  --game games/<game> `
  --out D:\runs\<game>\package
```

package 中 `visible/` 给 Agent，`hidden/` 只给 evaluator，另有 `manifest.json`、`PROMPT.md` 和 `HANDOFF.md`。候选不得看到或提交 `hidden/` 内容。

## 5. 候选提交内容

```text
submission/
├── game/
│   ├── Assets/
│   ├── Packages/
│   ├── ProjectSettings/
│   └── Assets/GameBenchmark/gb_interface.json
├── BUILD.md
└── ops.json 或 demos.json
```

提交前检查：工程可以按 `BUILD.md` 构建 Linux Player，`gb_interface.json` 未被删除或改成自定义协议，ops tape 有真实动作，不包含 Godot runtime、evaluator、hidden route、reference rubric、certificate 或最终答案，也没有修改固定 SDK/observer 协议。

## 6. 选择是否运行 VLM

### 6.1 先跑 Objective，不调用 VLM

```powershell
--visual-judge none
```

仍会保留 evaluator-owned witness 视频和帧，得到 Objective 70 的各项读数；两个 VLM 域留作未测，之后可以对已有证据做 rescore。

### 6.2 完整运行 Objective + VLM

```powershell
--visual-judge vlm
```

建议使用外部 secret 文件或环境变量：

```powershell
$env:GAMEBENCH_VLM_PROVIDER = 'responses'
$env:GAMEBENCH_VLM_MODEL = 'gpt-5.6-sol'
$env:GAMEBENCH_VLM_EFFORT = 'high'
$env:GAMEBENCH_VLM_BASE_URL = 'https://your-responses-gateway'
$env:GAMEBENCH_VLM_KEY_ENV = 'MODE5_VLM_KEY'
$env:MODE5_VLM_KEY = '<secret>'
```

不要把 key 写入 package、submission、日志或文档。

## 7. 启动正式评测

### Objective-only

```powershell
& $PY eval/evalsys/bin/bench eval-task `
  --package D:\runs\<game>\package `
  --submission D:\runs\<game>\submission `
  --out D:\runs\<game>\evaluation `
  --engine auto `
  --unity-vm on `
  --visual-judge none
```

### Objective + VLM

```powershell
& $PY eval/evalsys/bin/bench eval-task `
  --package D:\runs\<game>\package `
  --submission D:\runs\<game>\submission `
  --out D:\runs\<game>\evaluation `
  --engine auto `
  --unity-vm on `
  --visual-judge vlm
```

约定：`--out` 使用新目录；不要手工覆盖半截旧结果；中断后只使用 runner 支持的 resume；diagnostic smoke 只能验证环境，不能进入正式分数。

## 8. 评测阶段和产物

| 阶段 | 典型产物 |
|---|---|
| 静态检查 | gate items、静态诊断 |
| Unity build | Player、build log |
| witness | `result.json`、PNG、MP4 |
| matched-null | null result |
| hidden | behavior result、checkpoint evidence |
| counterfactual | intervention result |
| Objective 汇总 | `objective_total`、各 item |
| structure VLM | cross-engine fidelity evidence |
| MDVA | `mdva_judgments/`、VLM response evidence |
| 最终输出 | `report.json`、`report.md`、artifact manifest |

## 9. 如何读报告

先看：

```text
evaluation/report.md
evaluation/report.json
evaluation/card.json
evaluation/artifact-manifest.json
```

推荐顺序：`outcome_status` → `gates.infrastructure/submission/validity` → `objective_total` → `structure_vlm_total` → `mdva_vlm_total` → `weighted_total` → `ranking_eligible` → runtime suite 和 artifact manifest。

### Objective 已有分数，VLM 还没测

```yaml
objective_total.score: 56.5
structure_vlm_total.score: null
mdva_vlm_total.score: null
weighted_total.score: null
outcome_status: evaluation_incomplete
```

这表示 Objective 已经可读，VLM 需要补测；不能把 56.5 当成最终排行榜分数，也不能把它放大到 100。

### 候选真实失败

```yaml
objective_total.score: 31.0
structure_vlm_total.score: 0
mdva_vlm_total.score: 7.4
```

结构域 0 是候选通关路径失败造成的真实 0；MDVA 仍可独立评价画面。

### 认证环境失败

```yaml
outcome_status: infrastructure_inconclusive
weighted_total.score: null
```

先修 VM、Unity、license、sandbox 或 artifact 通道，不要把它解释成 Agent 能力为 0。

## 10. 常见故障

| 现象 | 解释 | 下一步 |
|---|---|---|
| `infrastructure_inconclusive` | VM/Unity/license/sandbox 未通过 | 查看 preflight、profile 和 guest log |
| Unity build failed | 候选工程/build 配方问题 | 查看 build log、`BUILD.md` 和 Player 产物 |
| no ops tape | 候选没有有效演示输入 | 检查 `ops.json`/`demos.json` |
| no evaluator recording | evaluator 没有得到合法录像 | 保留 Objective，重试 VLM/采集 |
| VLM response malformed | provider 或响应契约问题 | 查看 group 的 prompt、raw response、normalized.json |
| hidden scenario failed | 候选机制/流程未满足场景 | 查看 scenario id、goal、behavior result |
| weighted total null | 至少一个固定域尚未测完 | 先看 objective/VLM 两个总字段 |
| ranking eligible false | 结果不能进入排行榜 | 查看 unmeasured、inconclusive、gate 状态 |
