# 模拟轴与连续输入

`gb_levels.json.analog_axes` 声明 `look_x`、`look_y`、`steer` 等连续输入。
每条轴指定取值范围和量化档数。离散输入见
[EXTENDED_ACTIONS.md](EXTENDED_ACTIONS.md)。

## 声明

```json
{
  "analog_axes": [
    {"id": "look_x", "why": "Continuous camera yaw or horizontal aiming.", "min": -1.0, "max": 1.0, "steps": 17},
    {"id": "look_y", "why": "Continuous camera pitch or vertical aiming.", "min": -1.0, "max": 1.0, "steps": 17}
  ]
}
```

| 规则 | 要求 |
| --- | --- |
| 集合 | 可省略；声明时必须是数组，最多 4 项 |
| `id` | 匹配 `^[a-z][a-z0-9_]{1,31}$`，不重复，不与扩展动作或保留名冲突 |
| `why` | 去除两端空白后至少 8 个字符 |
| 范围 | `min`、`max` 是有限数，且 `min < max` |
| `steps` | 2–33 的整数，包含两个端点 |
| 中性点 | 当 `min < 0 < max` 时，量化网格包含 0 |
| 玩家输入 | InputMap 中有非零 `InputEventJoypadMotion` |
| 游戏实现 | 玩法源码中包含轴名的字符串字面量 |

声明进入规范化接口摘要。注册任务使用固定接口中的轴集合；提交物和输入
录像不能扩大它。未声明的空集合不加入规范化字段。

## 回放与读取

已有操作可携带 `axes`，无需新增操作类型：

```json
{"op": "hold", "action": "gb_up", "frames": 30, "axes": {"look_y": -1.0}}
{"op": "wait", "frames": 20, "axes": {"look_x": 0.5, "look_y": -0.25}}
```

未声明的轴返回 `unknown_axis`；非数值或超出范围的值返回 `bad_axis_value`。
有效值量化到声明网格。驱动在操作持续期间保持输入，结束后归零。
只改变轴、不按键时可使用 `wait` 或 `state`。

游戏可读取 `GBHarnessProbe.axis_value(id)`，或已绑定的 InputMap / JoypadMotion。
轴输入与普通玩家控制应作用于同一套游戏状态。

## 无计划输入对照

`extended_mash_no_win` 同时按住所有扩展动作，将所有轴设为 `max`，持续
1100 帧。只有轴时使用 `state` 加 `axes`；两类声明都为空时该项不适用。
这个对照不能穷尽仅在 `min` 触发、延迟边沿、位置限制或更长时间窗口的行为。

接口限制见 `eval/interface/contract.v2.json`，输入校验和量化分别位于
`eval/evalsys/evalsys/interface/loader.py` 与 `eval/evalsys/evalsys/routes/agent.py`。
