# 评测方法

这份文档解释当前评测方式。具体字段看[五模式协议](tasks.md)，
实际命令看[运行手册](reference/TASKGEN_HARNESS.md)。

## 1. 五种任务

| 任务 | 给模型的主要输入 | 收回什么 | 重点检查 |
| --- | --- | --- | --- |
| 自主设计与生成（brief） | 需求简报、素材、参考录像、GB 接口说明 | 自写 GDD、Godot 工程、功能演示或整局操作 | 原始需求有没有实现，不能通过缩减自写 GDD 降低目标 |
| 按 GDD 生成（gdd） | 给定 GDD、素材、录像、GB 接口说明 | Godot 工程、功能演示或整局操作 | 设计中要求的机制、内容和交互 |
| 最小框架补全（skeleton） | 最小接入框架、任务说明、素材、录像 | 补全后的工程、功能演示或整局操作 | 接入框架是否保留、玩法是否实际完成 |
| 游戏修复（bugfix） | 清理后的故障工程、玩法要求、玩家症状报告 | 修复后的工程 | 目标故障消失、原有功能不回退，不要求补丁与参考写法一致 |
| 跨引擎迁移（port） | 清理后的 Godot 工程、GDD、素材、录像、跨引擎接口 | Unity 工程、接口声明、操作轨迹和构建说明 | 目标引擎构建运行、机制和内容语义保留 |

模型读取任务包的公开部分；隐藏判据、参考答案和评测产物留给评测器。
后三种任务给出的框架或源码范围不同，不能把它们当成完全相同的输入条件。
每种模式的完整文件清单与例外由协议维护。

**自验工具包（模式 1–3 的可选实验臂，默认不附带）**：`visible/playtest/` 让模型在 agent 阶段（默认无 wall-clock 预算，跑到自己退出）自己"玩—看—修"自己的游戏：`playtest.sh` 冷导入 + 标配驱动回放 + 一行判定（最终场景 / 帧数 / `SCRIPT ERROR` / 是否到达成功结局），`null_control.sh` 同长无输入对照（游戏会不会自己赢），`film.sh` 在沙箱的 `xvfb-run` 下把回放拍成 24 格拼图和 mp4，`watch.md` 让模型对着自己的 GDD 检查每个机制是否可见、动作是否有反馈、是否有推进、结局是否展示。它只用模型本来就拿到的材料和沙箱里量到的工具，不读任何隐藏内容，也不算分——它把提示词里本来就有的三条要求（固定帧率回放、到达声明结局、同长对照不能赢）做成了可执行的命令。`bench gen-task` / `bench run-task-matrix` / `run_benchmark.sh` 的 `--playtest-kit` 默认 `off`，传 `on` 才下载带它的任务包（`manifest.json.playtest_kit` 记录）；详见 [PLAYTEST_KIT.md](reference/PLAYTEST_KIT.md)。

## 2. 从提交到报告

评测器先检查工程与接口，再运行提交、施加输入、记录状态，最后汇总功能、对照和视觉证据。
参考游戏提供任务依据和可运行的比较对象；候选游戏的证据仍要在候选工程上重新采集。

报告将证据分为以下两个域；它们不是两个新的评分维度，不替代下方六维评分：

| 英文名称 | 中文名称 | 现有内部字段 | 证据内容 |
|---|---|---|---|
| Objective Behavioral Evaluation | 客观行为评测 | `ocard` | 引擎运行、输入响应、状态变化与可观测交付要求的客观检查 |
| Perceptual Quality Assessment | 感知质量评审 | `scard` | 按可见机制、设计与内容、功能性视觉传达、美术四组评审实际采集画面 |

字段拼写、通道编号及 registry 不变；[输出契约](reference/OUTPUT_CONTRACT.md#paper-terminology-and-serialized-fields)
列明两类读数在文件中的位置。是否测得、是否纳入总分仍服从对应版本的规则。

报告保留六类维度：

| 维度 | 回答什么 |
| --- | --- |
| 工程可运行与可观测性 | 能否导入、启动、渲染，并通过公共接口接受输入、读取状态 |
| 机制与任务完成度 | 跳跃、拾取、战斗、生产、失败重试等任务要求是否发生 |
| 操作有效性与可玩性 | 功能是否由有效操作触发，演示是否形成要求的进程 |
| 结构、内容与素材实现 | 要求的关卡或进度、内容关系和素材使用是否落实 |
| 视觉质量与体验证据 | 画面、动作反馈与可见功能是否清楚、符合要求 |
| 模式专项义务 | 自写设计、框架接入、修复回归或跨引擎迁移是否完成 |

权重、适用项和聚合细则统一放在[评分细则](reference/HIERARCHICAL_MULTI_EVIDENCE_SCORECARD.md)，
不在这里再维护一套数字。不同模式适用项可能不同，不能只拿一个总分横向比较。

## 3. 输入对照与行为归因

运行中发生变化，不一定是玩家操作产生的。对于需要操作触发的功能，评测器比较：

- 提交的正常输入。
- 相同起点、相同运行时长的无输入对照。
- 协议要求的其他控制，例如检查评测标志是否改变结果。

如果不操作也会获得同样进度，就不能把它计为操作带来的贡献。
合法的自动生产、计时或自动前进仍可作为观察到的功能，不能统一判错。

这种对照给出受当前起点、时长和观测条件约束的证据，不等于证明了所有场景下的因果关系。

## 4. 分段功能演示

Godot 的前三种生成任务可以交 `demos.json`，例如分别演示跳跃、拾取、失败后重试。
也可以继续单独交整局 `ops.json`。

每段从第一个声明关卡独立冷启动，包含走到演示位置的操作，不继承上一段进度。
评测器重放输入、做对照；开启视觉时再拍摄该段。
提交者不需要另外制作一份录像。

每段单独报告观察到的功能、操作产生的功能和覆盖贡献，整个任务取功能覆盖的并集。
重复演示同一功能不重复加分；未展示的必需功能仍留在分母里。

整局和分段使用相同的功能覆盖口径：已观察到的必需检查数 / 全部必需检查数。
无伤通关不判“受伤机制错误”，但没有触发受伤就不能获得该功能的验证分。
触发了条件却没有正确结果，仍是行为失败；引擎超时等采集失败记为未完成评价。
严格行为结论与部分覆盖分分别报告。

操作因果覆盖的分母只包含任务声明需要玩家操作的功能。
自动跑酷、自然倒计时通过隐藏 rubric 的 `requires_player_action: false` 和
`automatic_reason` 声明；它们仍必须出现在功能证据中，不会从功能分母删除。
当前声明覆盖 canopy_dash 的前进、harvest_ledger 的日间时钟、hive_flight 的关卡时钟。

部分功能可以得分，但不等于完整任务通过。
任务明确要求整局通关或完整内容时，这些要求仍然保留。
JSON 示例、字段与边界见[分段提交协议](tasks.md#feature-demonstrations)。

### Brief 的可选 Design 评分

当前 Brief 评测默认使用 `--brief-design off`。Design 指作者 GDD 的质量和接口声明一致性
（`gdd_quality`、`gdd_interface`），与 VLM 的设计与内容 D 组分别计量。
开启 `--brief-design on` 时沿用原有约 2.428 分的 Design 权重；关闭时移除这两项，
其余客观项按原比例归一到 85 分，VLM 保持 15 分。关闭的 Design 检查只保留诊断，
不影响得分、评测完整性或严格通过状态。GDD 文件及其对 brief 的覆盖仍是任务要求。

`evaluate.sh`、`bench eval-task`、`bench run-task-matrix` 和 `run_benchmark.sh`
都接受该开关。报告的 `brief_design` 字段记录实际设置；批量运行的 `run.json` 固定该设置，
续跑不能中途切换。对照实验应使用相同的 Design 设置。

已有报告可以直接重算，无需再次运行引擎或 VLM：

```bash
./evaluate.sh --rescore /path/to/report.json --brief-design off --out-dir /path/to/scores
```

省略重算开关时保留报告原设置；尚无该字段的旧报告按 Design 开启处理。
该可选项适用于 Mode-1 redesign/VLM 注册版本，较早的历史注册版本保持原有规则。

## 5. VLM 看什么

Modes 1–3 默认使用各自的 `2026-09-19.modeN-vlm1`：客观行为占 85 分，
游戏专属 VLM rubric 占 15 分。VLM 默认关闭；开启后，判官会看到评测器录制的候选游戏，
以及任务提供的 GT 视频参考帧、素材示例和本游戏 rubric。

| VLM 分组 | 视觉分内部权重 |
|---|---:|
| 可见机制（Visible mechanics） | 10% |
| 设计与内容（Design and content） | 18% |
| 功能性视觉传达（Functional visual communication） | 27% |
| 美术（Art） | 45% |

每项依据 rubric 中的完整、部分和零分标准，直接给出连续 0–1 attainment，
再应用该项实际触发的缺陷上限。`2026-10-08.demonstrated-quality-v4` 沿用较低的直接部分分参照：
仅有占位/原型表现约 0.01–0.05，重要组成缺失约 0.05–0.20，基本完成但有明显缺陷约 0.20–0.40，
核心完整且仅有小范围收尾缺陷约 0.45–0.65；充分完整、至多存在极轻微孤立缺陷约 0.70–0.90。
这些参照允许连续取值，并非固定档位；无缺陷且全部适用条件有逐项实证为 1。
适用条目的成果完全未展示时给 0，部分展示按已展示成果给部分分；未展示条件记入 `missing_evidence`，不虚构为可见缺陷。
只有有明确条件和候选证据支持的不适用条目使用 `null`。
组件存在、布局可读或没有明显错误，不能代替条目所要求的每一项视觉关系。
已声明严重缺陷的上限分别从 0.50/0.40/0.30/0.25 下调至 0.15/0.10/0.08/0.05，触发条件不变。
按论文定义，先在每组内平均适用条目的得分，再按上表权重汇总视觉分。
完全不适用的组退出分母，其余组的权重重新归一化；有效的未展示零分留在分母。
缺少录像、服务失败或无效判官响应仍使评测不完整，视觉分为 `null`。
核心关系未实现与局部收尾缺陷按实际影响区分；缺失的判官读数不是质量零分。
未完成 VLM 时保留 `objective_total.score`，`weighted_total.score=null`，不能进入综合排名。
客观必需项缺失也会保持整张卡未完成。独立人工校准仍未完成。

从仓库根目录执行，使用尚不存在的输出目录：

```bash
# 执行客观评测，默认不调用 VLM
./evaluate.sh /path/to/package /path/to/submission --out /path/to/objective
# 同时采集视觉证据并调用配置好的判官
./evaluate.sh /path/to/package /path/to/submission --out /path/to/visual --visual-judge vlm
# 在新版报告上后补视觉，保留其 modeN-vlm1 规则
./evaluate.sh --judge-visuals /path/to/objective/report.json \
  --out /path/to/with-visual --record-missing
# 旧版报告升级需明确指定对应模式；此处为 Mode 1
./evaluate.sh --judge-visuals /path/to/old-report.json \
  --out /path/to/upgraded --record-missing --registry 2026-09-19.mode1-vlm1
```

外部 API 文件通过 `GB_API_ENV` 选择。Responses 判官使用
`GAMEBENCH_VLM_PROVIDER=responses`；Fable/Claude Code 判官使用 `claude_code`。
同时配置 `GAMEBENCH_VLM_MODEL`、`GAMEBENCH_VLM_BASE_URL` 和 `GAMEBENCH_VLM_KEY_ENV`。
Claude Code 可通过 `GAMEBENCH_CLAUDE_CODE_BIN` 指定可执行文件。
配置示例见 [quickstart](quickstart.md#evaluation-commands) 和根目录 `.gb_api.env.example`。
独立评测使用判官配置；`run_benchmark.sh --visual-judge vlm` 复用该格的模型路线，
因此正式模型对比应固定后续判官，避免混用不同评审条件。

`--rescore` 只按当前模式规则重算已保存读数，不执行引擎或调用判官。
`--judge-visuals` 会调用判官；`--record-missing` 会先重新录制保留的 Godot 项目。
它保留行为证据和 `resolved`，路径移动时可传 `--package`、`--submission`。
仅存有 JSON 分数而没有工程或录像时，无法补出视觉证据。
如果行为测试实现也变了，应对原 package/submission 重新运行普通 `evaluate.sh`。

`--score-against-source` 为生成时没有开启该项的任务包增加参考条件化的客观测量，
不会因开启 VLM 而自动打开。当前 VLM 可以录制独立 demos，或在没有 demos 时录制整段 ops。
录像采样与 rubric 版本保存在视觉证据 manifest 中。

### 历史 visual1 协议

显式选择 `2026-09-11.visual1` 时仍使用旧的 GameCraft-adapted 判官：只看候选录像和
任务文字，不看 GT，使用 `GAMECRAFT_BENCH_JUDGE_*`、Chat Completions 与历史聚合权重。
没有新版 registry 或 game-rubric manifest 的旧报告，在后补视觉时也保留该行为，除非
显式传入当前模式的 `--registry`。它与上面的 M/D/V/A 游戏专属 rubric 分数不能混用。
原始实现、许可证与归属见 [`gamecraft_bench/`](../eval/evalsys/gamecraft_bench/UPSTREAM.md)。
历史 `evidence1` 的未校准视觉诊断不计入总分。

## 6. 报告解读

### 参考素材与行为证据

素材使用项（JSON 通道 `O6`）以参考游戏的**运行时已加载素材**为期望集合，
冻结在 `eval/tasks/<game>/expectations.json` 的 `runtime_assets` 中。
未用到的素材包库存、压缩包本身、脚本和场景文件不进入这个分母。
新任务包把这些参考素材补入公开的 `visible/assets/reference_media/`；
相同素材改名后仍按内容识别，重名但内容不同的素材分别计算。
参考采集失败会拒绝冻结；没有文件型素材的参考则明确记“不适用”，不是满分。

任务包包含固定的素材期望与接口要求，评测时按该版本执行。

单次传送没有建立拾取／目标的前置条件时，该探针记“不可观测”，而不是通过，
也不当作评测器崩溃来扣留整张总分。完整触发后没有效果仍判失败。
同一提交的认证整局回放通过，可提供一条整体目标成功证据；
不能据此声称拾取等单项机制都通过。真正的探针崩溃或超时仍是未完成评测。

整局路线的通过标志和进度得分分开：没有完成终点条件就判失败；
干净且满足相应机制约束的路线仍按已完成里程碑比例获得部分分。
即使全部中间里程碑已完成，也不能代替终点条件。

历史结果重新测量需保留原报告并记录评测代码版本；只运行 `--rescore`
不会重新执行这些探针。旧输入包未提供参考素材时，不能把补齐素材后的新任务
当作同一实验条件，也不能把旧提交的素材缺失归因于模型能力。

先看三件事，再看总分：

1. **严格完成判定**：本模式要求的条件是否全部满足，哪些失败、哪些证据不足。
2. **六类分数与覆盖率**：具体完成了哪些功能，有多少适用要求真正被测量。
3. **未完成原因**：游戏实现错误、未演示、任务不适用、引擎或采集失败分别是什么。

`resolved` 是严格完成结论，`weighted_total` 是部分质量总分。
有分数不等于通过，能参与排名也不等于严格完成。
`report.md` 适合阅读，`report.json` 保留细项、协议与版本信息；
分段结果在 `demonstrations`，分段视觉在 `demonstration_visuals`。

历史整局通关分数与新的分段覆盖分数含义不同。
正式模型对比要固定任务、协议、评分版本与预算，不能把它们直接混排。

## 7. 评分版本：registry 行与感知质量评审 rubric

权重表见[评分细则](reference/HIERARCHICAL_MULTI_EVIDENCE_SCORECARD.md)；这里只记录哪一行是默认、哪一行可选，以及它们各改了什么。

当前默认 registry：

| 模式 | 默认版本 |
|---|---|
| 1 brief | `2026-09-19.mode1-vlm1` |
| 2 gdd | `2026-09-19.mode2-vlm1` |
| 3 skeleton | `2026-09-19.mode3-vlm1` |
| 4 bugfix | `2026-09-15.mode4-redesign1` |
| 5 port | `2026-10.mode5-evidence-five-visual1` |

前三种模式使用 85 分客观能力和 15 分真实 VLM 读数，视觉证据协议为
`2026-09-19.game-rubric-v1`。Mode 4 按修复、门槛与回归保护计分，不获得视觉分。
Mode 5 使用 35/25/15/15/10 五项固定权重，基于独立运行、Editor、静态支持和
文件存在证据发布非 VLM 代理分。Visual 使用 evaluator 自有的
`visual_implementation_correspondence` 测量；运行时实现对应证据可取得完整 15/15，
静态与文件证据仍按系数折算，不测感知或美学相似度。`official_total` 为 null。见
[Mode 5 评分规范](reference/MODE5_SCORING.md)。
较早的 `modeN-redesign1`、`evidence1` 和 `visual1` 仍能用 `--registry` 显式复算；
它们保留自己的权重和占位规则。比较分数时必须同时固定任务模式、registry、rubric 和判官模型。

`evaluate.sh --rescore` 默认使用当前模式版本，输出会列出旧版本到新版本的变化。
要复现历史版本，显式指定它。旧行为证据缺少新版要求的测量时，重算仍会报告缺口，
需要重新执行评测才能补齐。

## 8. 当前限制

- 跨实现评测应按公开行为判断，不要求相同脚本、节点路径或场景组织。收集不再一律要求对象数减一，推进不再一律要求换场景；依赖参考布局的旧路线读数仍需单列，不能直接当成新实现的机制错误。
- 分段入口目前固定在第一关，不支持任意中途存档起点；修复与 Unity 迁移没有自动继承 Godot 分段协议。
- 可测功能取决于任务的激活条件和可执行判据；观测缺口不能由 VLM 印象补成通过。
- modeN-vlm1 的 VLM 读数参与计分，判官会收到 GT 和素材帧。论文的人工一致性验证见
  [§4.5](https://arxiv.org/html/2609.33678v1#S4.SS5)。报告中的
  `objective_and_game_vlm_not_independently_calibrated` 是沿用的协议状态字段；
  它不概括论文的验证结论。有限录像与稀疏采样仍可能漏掉关键事件，没出镜不能证明功能不存在。
- Mode 2 的需求归属与实际计分分母必须一致。若 `objective_total.unmeasured` 报告
  `unscored_requirement`，需要补齐评测映射；不能把已有客观小分当成完整成绩。
- 历史 visual1 不接收 GT，使用 20 秒观察窗口；它的上游解析器会把漏答填 0，
  max 聚合也存在多段机会偏差。这些历史规则不适用于当前游戏专属 rubric。
- Mode 5 按[Release 协议](reference/MODE5_RELEASE_PROTOCOL.md)报告，正式评测需要
  Community Docker、固定 Unity 版本与有效许可。默认客观分满分 70；Structure/Visual 缺测时完整总分为 null。`paper_compatible=false`，Community 结果与论文历史结果分开比较。
