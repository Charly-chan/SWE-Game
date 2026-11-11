# Mode 5 Community Release 协议

安装、授权和命令见 [运行指南](MODE5_RELEASE.md)，完整计分公式与证据约束见 [评分规范](MODE5_SCORING.md)。Mode 5 只有 Community Docker 一条执行和评分链路，不使用 VLM。

## 环境与任务材料

- Linux x86_64 Docker；固定 Unity 6000.3.23f1、StandaloneLinux64、Mono。Agent 镜像包含 Editor、Linux Player 支持、模型 CLI、`gb-unity` 和开发工具。任务压缩包不含 Editor 或许可。
- 用户自行合法激活 Unity entitlement，运行时只注入私有 home；镜像和下载包不含授权。Personal 使用同一 Linux coordinator 的 Unity Hub 激活，不要求兑换 ALF/ULF。
- visible 提供 Godot 源码、GDD、素材、参考视频、Unity scaffold、SDK、接口说明及环境锁；不提供 hidden suite、控制器代码或私密凭证。
- scaffold 是 SDK 和可编译起点，不是已经移植好的游戏。Agent 必须创建原生 Unity 游戏、场景、接口、`ops.json` 和 `BUILD.md`；不得包装 Godot runtime。
- SDK 源码与依赖版本同公开 scaffold 对照，允许 Unity 生成 `.meta`、锁文件和 JSON 排版变化。不新增内容 SHA256 门槛。
- evaluator 使用固定构建器，不执行候选 `BUILD.md` shell。候选不能访问 hidden/controller 私有目录，不能提交自己的评分报告充当证据。

## 唯一运行流程

```text
setup / doctor → 任务材料与 scaffold → Agent 移植和自检
→ 原始 submission 回传 → 全新 evaluator 独立重建
→ witness / matched-null / auto-win / hidden / counterfactual
→ 运行 capture → 冻结证据评分 → report.json / report.md / 产物清单
→ 同协议任务汇总
```

静态证据在候选执行前快照。构建和 runtime 在独立离线 evaluator 中进行；结束后 coordinator 只发布其保留证据，不重新读取可变候选文件计算分数。Agent 自检和 exit 0 不等于评测通过。

Agent 默认固定预算 7200 秒（120 分钟），单次 Unity 子命令最多 1200 秒，较短 Agent 预算按比例限制。预算不包含 setup、任务生成、回传和 evaluator；`--agent-timeout 0` 被拒绝。镜像、harness、模型、provider、预算和输入设置记录到报告。完整回传的超时提交可以评测，但必须保留超时状态。

## 评分契约

Registry 为 `2026-10.mode5-evidence-five-visual1`，ranking scope 为 `mode5-community-evidence-five-visual1`。每任务有五项百分制分，按固定 35/25/15/15/10 权重得到单一 `/100` 代理总分：

| 分项 | 原始分 | 子项 |
| --- | ---: | --- |
| Mechanics | 35 | 输入映射 6；核心机制 12；交互状态 10；结束、检查点与重置 7 |
| Playability | 25 | 合法非 idle ops 5；输入消费链 5；中间进展 7；最终目标 5；暂停/重启/场景流程 3 |
| Structure | 15 | 场景和对象 5；实际引用内容 5；UI 层级与交互 5 |
| Visual | 15 | 引用素材 4；UI/状态反馈 3；渲染配置 3；动画/音频/粒子反馈 3；参考布局 2 |
| Stability | 10 | 包和构建 4；生命周期与加载/重置 3；资源和错误安全 3 |

每项的义务和分母来自 evaluator 的参考任务，在检查提交前固定。同一子项内义务等权；不能删除失败义务或靠添加文件扩大得分。运行、Editor、可信静态支持、仅文件存在、失败的系数分别为 `1.00 / 0.80 / 0.60 / 0.25 / 0`。重复证据取最强的一项，不累加；同一义务的明确反证优先。每条证据给出检查位置和覆盖率。

静态支持不等于行为验证。评论、孤立脚本、未引用素材和死代码不能拿实现分。源码证据要求可追踪的使用路径与具体操作；运行时生成的对象可由独立角色与非空 native renderer census 证明其结构。动画、音频、粒子是否适用由参考游戏决定，不要求所有游戏具备全部效果。

机制、交互和进展是不同义务：只有相符的具体机制检查才能填相应子项，掉血不等于进展。最终目标只接受实际运行及输入因果、matched-null 和 auto-win 对照；无通关证据只使对应 5 分未获得，不整体清零 Playability。

没有对应 runtime 证据时，Mechanics 上限为 `24.5/35`，Playability 为 `15/25`。Visual 使用 evaluator 自有的 `visual_implementation_correspondence` 测量：运行时 capture 会针对冻结的素材角色、UI 反馈、渲染输出、动画/音频和布局做实现对应检查，Editor、静态支持和仅文件存在仍按各自系数折算。不使用 VLM，也不宣称感知或美学相似度。Visual 的运行时义务全部独立验证时可取得完整的 15 分；固定分母总分可达到 100/100。使用 `weighted_total.score` 读取任务总分，字段定义见 [输出契约](OUTPUT_CONTRACT.md)。

源码解析是保守的有限检查，不是完整 C# 编译器。未能证明的反射、复杂数据流或布局保持未获证据状态。场景索引变化不自动证明重置，声明成功不自动证明通关，也没有按模型身份猜测的动态 credit。

## 失败、缺测与隔离

- 候选编译失败保留已经独立确认的静态分，依赖运行的义务无动态分。资源错误、异常、崩溃按实际运行和对应义务计分；失败运行保留在稳定性覆盖率分母中。
- SDK 篡改、夹带 evaluator、Godot runtime 或静态作弊使 integrity 失败，禁止执行并不可排名。
- 授权、工具链、传输或检查预算不足属于 evaluator 缺测：`total=null`，保留 `earned_proxy_points` 供诊断，不能伪造候选零分。
- 参考要求以外的可选几何观测缺口，不抹去已有已验证证据分，也不能获得依赖该观测的运行分。严格通关 `resolved` 独立报告，代理分不能覆盖严格失败。
- 没有通过当前 hidden suite 校准的开发任务只作 unscored 诊断，不能通过重新汇总或保留证据重评进入正式榜单。

## 发布产物、重评与主榜

权威产物为 evaluator 的报告、runtime 证据及 `artifact-manifest.json`。清单检查路径与文件大小，拒绝逃逸、链接和设备文件；允许额外笔记，不提供密码学防篡改保证。公开运行目录前须审阅模型对话及原始提交，授权文件和模型凭证不得发布。

`rejudge` 验证保留产物与 game/mode/schema，按同一 registry 发布保留证据到新目录，不重跑 Agent 或 Unity、不调用 VLM、不覆盖原始结果；不能跨 registry 静默改分。

同一 model/harness/provider、registry、环境、镜像、预算、输入协议内逐任务汇总，拒绝重复游戏。正式主榜为全部 41 个任务的算术均值，候选有效零分也计入；缺失或不可排名任务使完整均值缺测。部分运行仅展示 coverage 和明确标注的 `partial_mean_score`。

旁边展示最低四分之一任务均值，以及 `0.70 × 全任务均值 + 0.30 × 低尾均值` 的可靠性诊断，不能替代主榜。低尾任务数为 `ceil(N/4)`，41 任务取 11 个；同分按 game_id 排序，所有分项诊断使用同一低尾任务集合。这些是同一结果的辅助读数，不是第二种评分或执行路径。

正式模型对比应固定 evaluator commit、registry、任务材料、环境、harness 和预算，并保留原始提交及评测证据。论文表格与发布任务目录的版本信息集中列于 [版本说明](../releasing.md#published-results)。
