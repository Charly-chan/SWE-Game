# Mode 5 评测规范：测什么、怎么测、如何计分

本文是 Mode 5 的评分和测量规范。它回答三个问题：评测器实际运行什么、每一部分测什么、为什么一个结果是分数、0 分或 `null`。

## 1. 一句话理解

候选需要把完整 Godot 游戏重建为原生 Unity 项目。Mode 5 不比较 Unity 源码是否像 Godot，也不要求复制 Godot 节点树；它检查候选能否在认证 Unity 环境中完成同样的游戏体验和关键行为。

正式总分为 100 分：

```text
Objective 70 分
  ├─ 核心机制与语义一致性       35
  ├─ 可玩性与流程完成           25
  └─ 稳定性与生命周期           10

VLM 30 分
  ├─ 跨引擎结构                 15
  └─ 视觉质量              15
```

Objective 和两个 VLM 域彼此独立记账，不做动态重归一化。

对外排行榜不再单独增加“素材”列，而是固定显示四列：

| 公开列 | 权重 | 机器计分来源 |
|---|---:|---|
| 机制与需求 | 35 | `unity_mechanic_trace` |
| 内容与素材 | 15 | `unity_structure_fidelity`（包含素材身份/内容对应） |
| 可玩性与演示 | 35 | `causal_witness` + `unity_hidden_behavior` + `unity_runtime_stability` |
| Mode 专属能力 | 15 | `unity_vlm`（Mode 5 视觉质量） |

这是展示层聚合，不改变机器证据叶：稳定性仍单独保存为 10 分诊断项，素材仍在
内容与素材列内，不会被误读成额外加分项。

## 2. 整体流程

```text
提交包 → 基础设施 gate → 静态/提交检查 → Unity VM 构建 Linux Player
       → witness + matched-null → Objective runtime suite → Objective 70
       →（请求 VLM 时）结构 VLM 15 + 视觉质量 15 → 最终报告
```

基础设施失败只能产生 `infrastructure_inconclusive`，不能冒充模型 0 分。候选真实失败可以在对应域记 0 分；evaluator 没有录到、接口失败或 VLM 响应坏，则是 `inconclusive`/retry。

## 3. 三层 gate

### 3.1 基础设施 gate

只判断评测器是否有资格测：certified Unity VM、Unity Editor、Linux IL2CPP、license、Xvfb/Mesa、controller、sandbox、hidden suite、环境 profile、证据 digest 和 artifact 通道。

失败结果：

```yaml
outcome_status: infrastructure_inconclusive
weighted_total.score: null
```

### 3.2 提交/静态域

检查工程布局、公开接口、SDK 完整性、build 配方、ops 格式、反作弊和是否夹带 evaluator 文件。

- 完全无法构建是候选失败：它阻止的 runtime/VLM 读数转为候选域 0 分并留在分母；
- 其他静态失败仍记入 Objective，但如果 Unity 还能生成合法画面，不能阻止 VLM 独立评测；
- 静态失败不自动扩散成所有 VLM 项的 0 分。

### 3.3 VLM 域 gate

- candidate 画面真实错误：对应 VLM 域可记 0；
- evaluator 没录到视频、参考素材缺失、API 失败、响应格式坏：对应 VLM 域 `null`/retry；
- Objective 失败但仍有可评画面：VLM 继续独立测。

## 4. Objective 70 分

### 4.1 核心机制与语义一致性：35 分

检查输入派发、核心交互、状态/空间/生命周期证据、观测结果交叉验证，以及是否存在 auto-win、预置胜利或伪造 telemetry。

```text
mechanics_score = 35 × 已满足机制义务 / 机制义务总数
```

### 4.2 可玩性与流程完成：25 分

| 子项 | 分值 | 主要证据 |
|---|---:|---|
| causal witness | 10 | 候选完整输入、matched-null、auto-win 对照 |
| hidden behavior | 15 | evaluator-owned 隐藏场景及 counterfactual |

causal witness 要证明候选自己的输入能完成游戏，而且 matched-null 不会自动完成。没有有效 ops tape、没有完整通关路径或通关不是由输入导致时，causal witness 是候选域失败。

Hidden 当前规则：

- 全量执行每个游戏的非整局 L5 功能场景；
- 排除真正的 `whole_game/whole_run_clear` L5 root，因为它已由 witness 覆盖；
- 保留 L5 派生的短 milestone；
- 当前共 130 条 score-bearing hidden 场景，41 个游戏每个 2–5 条，平均约 3.17 条；
- 每条场景附带的 counterfactual 都执行；
- counterfactual 回放到干预动作，再加最多 600 个游戏帧；
- 先做实时/8× 语义哨兵比较，不一致自动退回实时。

```text
hidden_score = 15 × 通过场景数 / 应测场景数
```

### 4.3 稳定性与生命周期：10 分

检查独立冷启动、Player 存活、semantic observation 连续性、异常退出、正常收尾和 artifact 生成。
对外表格把这 10 分并入“可玩性与演示”35 分，报告内部仍单列，便于区分
“路线不会通”和“运行过程中崩溃”。

## 5. VLM 30 分

### 5.1 跨引擎结构：15 分

比较 canonical Godot 和 evaluator-owned Unity witness 的可见证据，检查 asset identity、recognizable content、scene progression 和关卡/状态顺序。

两边优先使用整局视频，在相同归一化整局进度取帧；hidden 场景截图不混入整局结构比较。旧结果没有完整候选视频时，才回退到明确标注的 witness 截图。

不从该域判断源码结构、hidden state、输入因果或是否真正通关。

完整 witness 已执行但没有有效通关路径时，结构域是候选真实失败，直接记 0 且不调用 API；evaluator 没录到或 VLM provider 失败时，则是 `null`/retry。

### 5.2 视觉质量：15 分

视觉质量按论文中的四个分组加权：

| 维度 | VLM 内部权重 | 折算总分 |
|---|---:|---:|
| 可见机制（Visible mechanics） | 10% | 1.50 |
| 设计与内容（Design and content） | 18% | 2.70 |
| 功能性视觉传达（Functional visual communication） | 27% | 4.05 |
| 美术（Art） | 45% | 6.75 |
| 合计 | 100% | 15.00 |

视觉质量评审可以使用候选录像、参考素材、冻结 rubric、task context 和 evaluator runtime facts，但不能从像素猜测 hidden predicate 或输入因果。

## 6. 0 分、null 和最终分

| 情况 | 对应结果 |
|---|---|
| VM、license、sandbox、suite digest 失败 | 全局 `infrastructure_inconclusive`，分数为 `null` |
| 候选 Unity 编译/构建失败 | 候选导致的 runtime/VLM 叶记 0；可独立读取的静态项照常计分 |
| 候选 Player 启动后崩溃/无法回放 | 静态项保留；机制、可玩性、稳定性和候选视觉读数记 0 |
| 候选真实机制/流程失败 | 对应 Objective 项记 0 或按比例扣分 |
| 候选真实视觉错误 | 对应 VLM 域记 0 或部分分 |
| evaluator 录制失败 | VLM 域 `null`，Objective 保留 |
| VLM API/格式/证据契约失败 | VLM 域 `null`，Objective 保留 |
| 所有 Objective 和 VLM 读数完成 | 才能得到完整 0–100 总分 |

绝不因为 VLM 缺失而把 Objective 70 放大到 100。

## 7. 报告字段

```yaml
objective_total: {score: 0..70 | null}
structure_vlm_total: {score: 0..15 | null}
mdva_vlm_total: {score: 0..15 | null}
vlm_total: {score: 0..30 | null}
weighted_total: {score: 0..100 | null}
outcome_status: scored | evaluation_incomplete | infrastructure_inconclusive
ranking_eligible: true | false
leaderboard:
  mechanism_and_requirements: {weight: 35, score: 0..35}
  content_and_assets: {weight: 15, score: 0..15}
  playability_and_demo: {weight: 35, score: 0..35}
  mode_specific: {weight: 15, score: 0..15}
```

`objective_total.score` 可以在 VLM retry 期间正常读取；`weighted_total.score=null` 表示总体尚未完整，不表示 Objective 没测到。`measured_subset_rate` 只是诊断字段，不是正式分数。
