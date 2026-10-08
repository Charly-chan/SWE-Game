# Task and submission protocol

SWE-Game provides five task types over 41 reference games. Fixed packages
separate agent-visible materials from hidden checks and reference evidence.
The [runner guide](running.md) covers package downloads and execution;
[scoring specifications](reference/HIERARCHICAL_MULTI_EVIDENCE_SCORECARD.md)
define the current registries and score aggregation.

## Inputs and deliverables

| Task | Model-visible input | Required output | Hidden evaluator view |
|---|---|---|---|
| Brief-to-Game (`brief`) | asset palette, canonical reference video, short game-kind brief, GB contract | authored `GDD.md`, complete Godot project, feature `demos.json` or self-clear `ops.json` | reference mechanics/content/visual criteria projected from GT |
| GDD-to-Game (`gdd`) | asset palette, canonical reference video, task GDD, `DEMONSTRATIONS.md` obligations list, GB contract | complete Godot project, feature `demos.json` or self-clear `ops.json` | GDD requirements plus the same reference evidence graph |
| Skeleton Completion (`skeleton`) | asset palette, canonical reference video, minimal importable GB scaffold, task requirements, `DEMONSTRATIONS.md` obligations list | completed Godot project, feature `demos.json` or self-clear `ops.json` | scaffold integrity, GDD/mechanics, and reference evidence graph |
| Bug Repair (`bugfix`) | one runnable faulty project, bug report, product requirements | repaired Godot project | target restoration, preserved behavior, integrity gates |
| Godot-to-Unity Porting (`port`) | Godot source, asset palette, canonical reference video, GDD, engine-neutral GB port contract | Unity project, Unity GB adapter, self-clear `ops.json`, reproducible Linux build recipe | the same semantics, routes, content, visual and failure/reset obligations through a Unity probe |

Only `visible/` is supplied to the agent. The full package, including `hidden/`,
stays with the evaluator. Each package freezes the task requirements and evidence
used to evaluate its submission. Use the matching dataset revision for each run; existing packages retain their
frozen requirements. Newly installed video-conditioned construction and porting
packages freeze the current evaluator visual-scoring policy under `hidden/vlm/`;
agent-visible task materials and objective checks keep their released content.

Agents may choose their own files, classes, scenes, and architecture. Observable
mechanics, progression, content, and success/failure behavior follow the task
requirements. Godot submissions implement the
[submission interface](../eval/interface/SUBMISSION_INTERFACE.md). The task-owned
functional checks determine what must be demonstrated; candidate descriptions do
not redefine those checks.

## Bug Repair

The released case inventory is [`data/task-data.json`](../data/task-data.json).
Each repair task has a stable `case_id`, a player-facing bug report, and fixed
target, regression, and negative-control checks. A repair must restore the
target behavior and preserve the remaining gameplay and interface. One game can
contribute multiple cases; batch runs count cases individually.

## Godot-to-Unity Porting

Start from the supplied `target_unity/` project and preserve its locked SDK,
package dependencies, and editor version. Porting submissions provide `ops.json`,
a Linux `BUILD.md`, and an engine-neutral interface at `Assets/GameBenchmark/gb_interface.json`. The
interface maps task actions, levels, semantic roles, numeric state, and endings
to the Unity implementation. Godot node paths and source-file organization are
not required.

The evaluator builds the project in the licensed Unity **6000.3.23f1** environment
and runs the submitted inputs, matched no-input controls, and hidden scenarios.
Its injected probe captures raw state and visual evidence; the evaluator applies
the task predicates and computes scores. Build, interface, runtime, and evidence
requirements are specified in the [porting contract](../eval/interface/UNITY_PORT_CONTRACT.md),
[evaluation specification](reference/MODE5_EVALUATION.md), and
[Unity runtime guide](reference/UNITY_MODE5.md).

<a id="feature-demonstrations"></a>

## Feature demonstrations / 分段功能演示

协议：`gamebench.feature-demos.v1`。适用于 Godot 的自由设计、给定 GDD、最小框架三种生成任务。修复任务仍使用隐藏修复与回归测试；Unity 迁移任务仍使用其专用运行协议，不能把 Godot 分段执行的结果当作 Unity 验证。

### 提交

在 `submission/` 或 `project.godot` 同级提交 `demos.json`：

```json
{
  "schema_version": 1,
  "demos": [
    {
      "id": "jump",
      "description": "演示起跳、越过障碍并落地",
      "ops": [
        {"op": "hold", "action": "gb_right", "frames": 20},
        {"op": "tap", "action": "gb_jump", "frames": 8}
      ]
    },
    {
      "id": "pickup",
      "description": "演示取得房间中的道具",
      "ops": [
        {"op": "hold", "action": "gb_right", "frames": 80}
      ]
    }
  ]
}
```

这里的动作仅说明格式，不是任何游戏的保证成功解法。每段都从第一个声明关卡冷启动，采用固定 60 FPS 和一个准备 tick；到功能发生地点所需的操作也属于这段。段与段不继承状态，不允许提交任意场景入口、状态注入或自定义通过谓词。失败重试演示需使用游戏正常的重试交互；评测器保留的 `gb_reset` 不是游戏重试操作。

Linux 执行后端为每次分段、对照和拍摄分配独立的 `XDG_DATA_HOME`，避免 `user://` 进度、音量或解锁存档在各次运行间继承；不改操作者的 HOME 和原存档。分段驱动进入声明的失败画面后继续消费正常操作，允许在同段展示重试；旧整局通关路线仍在失败画面停止。

不必自行拍录像，也不必先整局通关。评测器会重放每段、采集状态，并在启用 VLM 时拍摄及评价每段。原来的完整通关 `ops.json` 仍可单独提交；同时存在两者时，功能评分使用分段协议，`ops.json` 不再被重放（评测器在分段路径结束后不运行整局重放）。

### 如何计分

任务方的功能要求决定分母。M2/M3 新生成的包通过 `visible/DEMONSTRATIONS.md` 公布文字要求，不公开隐藏谓词；M1（brief）不生成该文件——brief 给予创作自由，其参考机制判据按上表的 Brief-to-Game 行保持隐藏，分母仍是隐藏评分表的功能集。提交者的 `description` 不是评分标准，也不能删掉要求。

- 每段得到逐功能的“观察到效果”和“动作引起效果”两个读数，以及该段对完整任务功能集的覆盖贡献。
- 每段独立运行正常输入、等时长无输入、带评测标志的同一输入；原有空输入和额外动作控制也保留。
- 所有片段取已验证功能的并集。重复一百次相同演示不会增加总分。
- 没展示的功能仍未覆盖，条件未触发也不会自动视为已实现。采集超时或引擎驱动故障则标记未测量，不伪装成游戏得零分。
- 被动计时、自动前进由任务的隐藏检查声明 `requires_player_action: false` 并说明 `automatic_reason`。它们保留在功能覆盖分母，但不进入操作因果覆盖分母；没有声明的功能默认需要操作因果证据。严格完成要求功能全部覆盖且存在操作因果证据（纯自动任务无此项），并通过其他既有控制。
- 功能覆盖比例和因果覆盖比例进入原有相应评分项；`demonstrations_complete` 独立保留严格完成判断。因此部分功能可以得分，但不能被报告成整个任务完成。
- 若任务本身明确要求完整通关或全部关卡，这个要求仍保留。分段协议取消的是“所有功能必须挤进同一条通关录像”，不是删除任务要求。

### Evaluation and visual evidence

```bash
./evaluate.sh /path/to/package /path/to/submission --out /path/to/new-report
```

The evaluator selects the current registry for the task type. Visual judging is
disabled by default; add `--visual-judge vlm` with a configured judge to collect
perceptual evidence. The current construction-task VLM uses evaluator-captured
candidate gameplay, reference video, asset examples, and the game's rubric.
The [scoring specification](reference/HIERARCHICAL_MULTI_EVIDENCE_SCORECARD.md)
records the weights, missing-evidence rules, and historical registry links.

`report.json` and `report.md` retain the measured scores, completion verdict,
feature coverage, and evidence gaps. A partial score does not imply strict task
completion. Missing required visual evidence leaves the complete composite null.
Use the [failure-attribution guide](evaluation-status.md) to distinguish candidate
failures from missing evaluator evidence.

To assess saved evaluator recordings, use
`./evaluate.sh --judge-visuals REPORT --out NEW_DIR`; add `--record-missing` to
capture a retained Godot submission without rerunning its coding agent. Full
commands and judge configuration are in [quick start](quickstart.md#evaluation-commands).

Reports record `witness_protocol` so comparisons can hold the submission protocol
fixed. Historical whole-game causal scores and feature-demonstration coverage
have different meanings and should be compared within their own protocols.

### 已知边界

当前冷启动起点固定在第一关。尚未提供直接选择任意房间或自动恢复中途存档的分段入口。能评分哪些具体功能取决于该游戏的可执行功能判据；不能用“有录像”代替缺失的跳跃、重试或道具效果判据。抽帧视觉也不能代替真实输入手感测试。
