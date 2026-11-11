# 扩展动作

`gb_levels.json.extended_actions` 声明规范动作之外的离散输入，例如换弹、
选工具或训练单位。完整提交接口见
[SUBMISSION_INTERFACE.md](../../eval/interface/SUBMISSION_INTERFACE.md)。
连续输入见 [ANALOG_AXES.md](ANALOG_AXES.md)。

## 声明与校验

```json
{"extended_actions": [{"id": "reload", "why": "Reload the equipped weapon independently of firing."}]}
```

| 字段或规则 | 要求 |
| --- | --- |
| 集合 | 可省略；声明时必须是数组，最多 16 项 |
| `id` | 匹配 `^[a-z][a-z0-9_]{1,31}$`，且不重复 |
| 保留名 | 不得使用规范动作、`gb_*`、`ui_*` 或 pause/reset/quit/exit |
| `why` | 去除两端空白后至少 8 个字符，说明额外输入的用途 |
| 玩家输入 | InputMap 中有非零 keycode、physical_keycode 或 button_index |
| 游戏实现 | 玩法源码中包含该动作名的字符串字面量 |

声明进入规范化接口摘要。注册任务使用固定接口中的动作集合；提交物和输入
录像不能扩大它。模式 1–3 的游戏可在任务允许的范围内声明额外动作，并接受
相同校验。静态字面量检查不能单独证明游戏运行时实际使用了该输入。

## 回放

输入录像只能使用公开操作表中的操作和已声明的动作。未声明动作返回
`unknown_action`；`gb_pause` 和 `gb_reset` 不允许用于提交录像。
具体任务还可规定必须提供的扩展动作，以任务包为准。

## 无计划输入对照

`extended_mash_no_win` 从第 0 帧同时按住全部扩展动作，并将声明的模拟轴
设为最大值，持续 1100 帧。这个输入不应使游戏自动获胜。没有扩展动作和
模拟轴时，该项不适用。`null_no_win` 另行检查无输入时是否自动获胜。

对照识别直接达成目标和切换到成功结局。它不能穷尽延迟按下边沿、必须先
走到指定位置、特定动作顺序或超过 1100 帧的触发条件。

## 实现

- `eval/interface/contract.v2.json`：接口限制。
- `eval/evalsys/evalsys/interface/loader.py`：声明校验。
- `eval/evalsys/evalsys/routes/agent.py`：操作表与对照输入。
- `eval/harness/gb_probe.gd`、`gb_route_driver.gd`：输入派发与运行观测。

引擎使用 Godot 4.5.1，由 `GODOT_BIN` 指定。
