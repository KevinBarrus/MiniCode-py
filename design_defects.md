# MiniCode 设计缺陷清单

> 本文档由模拟面试（interview6.md）拷打过程中发现。按严重程度排序。
> 用途：作为后续修复工作的任务清单。

---

## 缺陷总览

| # | 缺陷 | 严重度 | 影响 | 所在文件 |
|---|------|--------|------|---------|
| 1 | 传感器输入数据是假的 | 🔴 严重 | StateObserver/PredictiveController 基于错误数据做决策 | agent_loop.py, cybernetic_orchestrator.py |
| 2 | SelfHealingEngine 一半策略是 placebo | 🔴 严重 | 故障检测到了但修不好 | self_healing_engine.py |
| 3 | 缺少真实 Agent A/B 对比评测 | 🔴 严重 | 现有消融框架主要是合成控制器模拟，无法证明真实 Agent 是否受益 | cybernetic_ablation.py, tests/ |
| 4 | DecouplingController 的耦合矩阵没被 PID 消费 | 🟡 中等 | 耦合矩阵虽被计算，但没有回写 PID；多变量耦合无人补偿 | decoupling_controller.py, agent_loop.py, cybernetic_orchestrator.py |
| 5 | FeedforwardController 没调 PID setpoint | 🟡 中等 | 所有任务用同样的 PID 目标值 | feedforward_controller.py, feedback_controller.py |
| 6 | to_system_state() 的 oscillation_index 是死数据 | 🟡 中等 | ContextCybernetics 算的振荡检测没被消费 | context_cybernetics.py, feedback_controller.py |
| 7 | 两个独立的振荡检测器 | 🟡 中等 | 重复计算，语义不同但名字相同 | context_cybernetics.py, feedback_controller.py |
| 8 | 缺少 conditional integration（PID anti-windup 不完整） | 🟢 轻微 | 误差反转时 PID 有短暂响应延迟 | feedback_controller.py, context_cybernetics.py |
| 9 | PredictiveController 的预测建议只打日志不执行 | 🟢 轻微 | 预测到了问题但不处理（实际压缩由别的模块执行） | predictive_controller.py, cybernetic_orchestrator.py |
| 10 | 集成测试文件缺失 | 🟢 轻微 | 不存在 test_cybernetic_integration.py，缺少真实控制器链路的集成覆盖 | tests/test_cybernetic_integration.py |

---

## 详细说明

### 缺陷 1：传感器输入数据是假的

**症状**：
```python
# cybernetic_orchestrator.py:191 (step_start)
measurement = MeasurementVector(
    response_time=step * 2.0,  # ← 硬编码！假设每步刚好 2 秒
    ...
)

# cybernetic_orchestrator.py:243 (step_end)
snapshot = MetricSnapshot(
    avg_latency=step * 2.0,  # ← 同上
    ...
)
```

**影响链**：
- `response_time=step*2.0` → StateObserver 的 Kalman Filter → internal_load 估计值不准
- `avg_latency=step*2.0` → StabilityMonitor 的快照 → 后续耦合分析不准
- `agent_loop.py` 中解耦控制的 `token_usage_to_latency` 也使用 `step*2.0/60.0`
- 因此 StateObserver、StabilityMonitor 和 DecouplingController 都没有使用同一次 LLM 调用的真实延迟

**修复方案**：
在 `agent_loop.py:950` 的 LLM 调用前后加计时：
```python
t0 = time.time()
next_step = _model_next(model, current_messages, ...)
actual_response_time = time.time() - t0
```
然后将 `actual_response_time` 传给 `step_start()` 和 `step_end()`，替换 `step * 2.0`。

**需要修改的文件**：
- `agent_loop.py`：LLM 调用处加计时，传递实际耗时
- `cybernetic_orchestrator.py`：`step_start()` 和 `step_end()` 接受 `actual_response_time` 参数

---

### 缺陷 2：SelfHealingEngine 一半策略是 placebo

**症状**：
```python
# self_healing_engine.py:323-330
def _execute_reduce_concurrency(self) -> dict[str, Any]:
    if self._tool_scheduler and hasattr(self._tool_scheduler, '_controller'):
        return {"success": True, "action": "Reduced concurrency to minimum..."}
    return {"success": True, "action": "Concurrency reduction logged (no scheduler ref)"}
```
两个分支都只返回 dict，没有实际修改任何东西。

**placebo 执行器清单**：
| 执行器 | 声称做什么 | 实际做什么 |
|--------|-----------|-----------|
| `_execute_reduce_concurrency` | 降低并发 | 返回 dict |
| `_execute_reduce_timeout` | 缩短超时 | 返回 dict |
| `_execute_safe_mode` | 安全模式 | 返回 dict |
| `_execute_force_terminate` | 强制终止 | 返回 dict |

**实际起作用的执行器**：
| 执行器 | 实际效果 |
|--------|---------|
| `_execute_cybernetic_compaction` | 调用 `orchestrator.try_reactive_recover()` → 真实压缩 |
| `_execute_dampen_oscillation` | 修改 `pid.kd*=2, pid.kp*=0.5, pid.ki=0.01` |
| `_execute_force_compaction` | 调用 orchestrator 或 compactor |
| `_execute_model_upgrade` | 调整 token_budget（依赖 compactor 存在） |

**修复方案**：
4 个 placebo 执行器需要接入真实的运行时修改逻辑：

1. `_execute_reduce_concurrency`：设置 `tool_scheduler._force_max_workers = 1`
2. `_execute_reduce_timeout`：设置 `tool_scheduler` 的超时参数（如果存在）
3. `_execute_safe_mode`：设置 `tool_scheduler` 为串行模式
4. `_execute_force_terminate`：调用 `tool_scheduler` 的取消/终止方法

**需要修改的文件**：
- `self_healing_engine.py`：4 个执行器补充真实逻辑

---

### 缺陷 3：缺少真实环境 A/B 对比评测

**症状**：
- `cybernetic_ablation.py` 有消融框架，但它跑的是**确定性合成数据**，不调用真实 LLM
- `tests/test_cybernetic_integration.py` 当前**不存在**，不是空文件
- 没有 `enable_work_chain=True` vs `False` 的真实对比数据

**修复方案**：
1. 选取 5-10 个真实编码任务（如"在 xxx 文件中添加一个函数"）
2. 对每个任务跑两遍：`enable_work_chain=True` vs `enable_work_chain=False`
3. 对比指标：任务完成率、总 token 消耗、工具错误数、总步数
4. 将对比数据写入 `cybernetic_ablation` 的报告

**需要做的事情**：
- 编写缺失的 `tests/test_cybernetic_integration.py`
- 准备真实任务集
- 跑对比实验并记录结果

---

### 缺陷 4：DecouplingController 的耦合矩阵没被 PID 消费

**症状**：
```python
# decoupling_controller.py:176-191
def compute_decoupling_matrix(self) -> dict[str, dict[str, float]]:
    # 计算了 token_usage↔latency, context_pressure↔error_rate 等耦合关系
    # 结果没有被任何 PID 参数或控制指令消费
```

当前调用点在 `agent_loop.py:1352-1363`，会记录测量并计算矩阵，但随后没有应用矩阵结果。该调用位于 `orch.step_end()` 之前，并不是只在 `not orch` 分支执行；实际问题是矩阵计算与 PID 控制之间没有闭环。

**影响**：5 个独立 PID（3 个 FeedbackController + 1 个 ContextPID + 1 个 CostControl）各自独立运行，一个 PID 的输出变化会影响另一个 PID 的输入，但没有补偿机制。

**修复方案**：
将解耦矩阵的计算结果回注到 PID 参数中。例如，如果 `token_usage_to_latency` 耦合度 > 0.5，则降低性能 PID 的 kp（避免过度反应）。

**需要修改的文件**：
- `decoupling_controller.py`：增加 `apply_to_pid()` 方法
- `cybernetic_orchestrator.py`：在 `step_end` 中调用解耦 → PID 参数调整

---

### 缺陷 5：FeedforwardController 没调 PID setpoint

**症状**：
PID 的 setpoint 对所有任务都一样：
```python
# feedback_controller.py:177-179
self._stability_target = 0.85
self._performance_target = 0.75
self._efficiency_target = 0.60
```

FeedforwardController 能根据任务意图（SEARCH/REFACTOR/CODE）设置不同的 token_budget、concurrency、timeout，但**从不修改 PID setpoint**。

**修复方案**：
1. 在 `PreemptiveConfig` 中增加三个字段：
```python
stability_setpoint: float = 0.85
performance_setpoint: float = 0.75
efficiency_setpoint: float = 0.60
```

2. 在 `FeedforwardController._INTENT_CONFIGS` 中为不同意图设置不同目标：
```python
# SEARCH 任务：稳定性要求低，效率要求高
IntentType.SEARCH: {..., "stability_setpoint": 0.70, "performance_setpoint": 0.60, "efficiency_setpoint": 0.80}
# REFACTOR 任务：稳定性要求高，效率可以低
IntentType.REFACTOR: {..., "stability_setpoint": 0.90, "performance_setpoint": 0.80, "efficiency_setpoint": 0.50}
```

3. 在 `agent_loop.py` 初始化阶段，将 setpoint 应用到 `FeedbackController`

**需要修改的文件**：
- `feedforward_controller.py`：`PreemptiveConfig` 增加字段，`_INTENT_CONFIGS` 增加映射
- `feedback_controller.py`：增加 `set_setpoints()` 方法
- `cybernetic_orchestrator.py`：`step_end` 中应用前馈 setpoint

---

### 缺陷 6：to_system_state() 的 oscillation_index 是死数据

**症状**：
```python
# context_cybernetics.py:858
oscillation_index=1.0 if fb_stats.get("oscillation_detected") else 0.0
```

这个值被写入 `SystemState.oscillation_index`，但 `FeedbackController.observe()` **从未读取** `state.oscillation_index`。它使用的是自己的 `_compute_oscillation()`。

**修复方案**（二选一）：
- **方案 A**：删除 `to_system_state()` 中的 `oscillation_index` 字段
- **方案 B**：让 `observe()` 使用 `state.oscillation_index` 替代自己的 `_compute_oscillation()`

**需要修改的文件**：
- `context_cybernetics.py`：方案 A 删除字段；或方案 B 传递原始信号
- `feedback_controller.py`：方案 B 消费 `state.oscillation_index`

---

### 缺陷 7：两个独立的振荡检测器

**症状**：

| | 检测器 A | 检测器 B |
|---|---|---|
| 位置 | `CyberneticFeedbackLoop.detect_oscillation()` | `FeedbackController._compute_oscillation()` |
| 监测对象 | 压缩结果的方向变化 | 稳定性误差的方向变化 |
| 输出类型 | bool (0 或 1) | float (0.0-1.0) |
| 被谁消费 | SystemState → **无人消费** | ControlSignal → SelfHealingEngine |

两个检测器做类似的事（统计方向变化），但检测不同信号，输出的名字都叫 "oscillation" 但含义不同。

**修复方案**：
统一为一个振荡检测器，放在 `FeedbackController` 中。ContextCybernetics 的反馈环只负责提供**原始方向变化次数**，由 FeedbackController 统一计算振荡指数。

**需要修改的文件**：
- `context_cybernetics.py`：移除 `CyberneticFeedbackLoop.detect_oscillation()`，暴露 `direction_changes` 原始值
- `feedback_controller.py`：`_compute_oscillation()` 接受来自 ContextCybernetics 的额外信号

---

### 缺陷 8：PID anti-windup 不完整

**症状**：
两个 PID 都做了 clamp anti-windup（很好），但没有 **conditional integration**——当误差穿越 0 时不清零积分项。

```python
# feedback_controller.py:133-135 (外层)
self._state.integral += error * dt
self._state.integral = max(-10.0, min(10.0, self._state.integral))

# context_cybernetics.py:249-251 (内层)
self._integral += error * dt
self._integral = max(-2.0, min(2.0, self._integral))
```

**影响**：当误差由正变负时，积分项需要从 clamp 上限降到 0，这段时间 PID 输出仍然偏高。在上下文压力突然变化时（如大文件读取完成），可能有 1-3 步的响应延迟。

**修复方案**：
在 `compute()` 方法中加条件积分逻辑：
```python
# 当误差穿越 0 时，清零积分项
if error * self._prev_error < 0:
    self._integral = 0.0
```

**需要修改的文件**：
- `feedback_controller.py`：`PIDController.compute()` 方法
- `context_cybernetics.py`：`ContextPIDController.compute()` 方法

---

### 缺陷 9：PredictiveController 预测建议只打日志

**症状**：
```python
# cybernetic_orchestrator.py:212-217
actions = self.predictive.generate_predictive_actions()
if actions and actions[0].urgency > 0.7:
    action = actions[0]
    if action.recommended_action == "trigger_compaction" and self.context_cybernetics:
        logger.info("Predictive: trigger_compaction urgency=%.2f", action.urgency)
        # 注意：只打日志，不调用 context_cybernetics.run_cycle()！
```

**影响**：预测到了上下文即将溢出，但只记录不行动。真正的压缩由 ContextCybernetics 的 PID 在执行 step_end 时触发，但预测的提前量被浪费了。

**修复方案**：
当预测 `urgency > 0.7` 且建议是 `trigger_compaction` 时，实际调用 `context_cybernetics.run_cycle()`，实现**预测性压缩**（不等 PID 反应过来）。

**需要修改的文件**：
- `cybernetic_orchestrator.py`：`step_start()` 中增加实际的压缩调用

---

### 缺陷 10：集成测试文件缺失

**症状**：
`tests/test_cybernetic_integration.py` 当前不存在。

虽然有：
- `test_advanced_cybernetics.py`（658 行）：控制器的单元测试
- `cybernetic_ablation.py`（853 行）：合成数据的消融框架

但缺少使用 Mock LLM、真实控制器组合和 Agent 执行链路进行端到端验证的集成测试。

**修复方案**：
编写 `test_cybernetic_integration.py`，包含：
1. Mock LLM + 真实控制器的集成测试
2. 模拟故障场景（上下文溢出、错误爆发、振荡）验证自愈引擎
3. enable_work_chain=True vs False 的对比测试

**需要做的事情**：
- 编写集成测试用例
- 准备 mock 场景

---

## 修复优先级建议

### 第一优先级（面试前必须修，影响演示效果）
1. **缺陷 1**（传感器假数据）— 3 行代码，极大提升可信度
2. **缺陷 2**（自愈 placebo）— 4 个执行器各加 1-2 行

### 第二优先级（面试时会被追问的）
3. **缺陷 5**（前馈不调 PID setpoint）— 展示"我知道控制论中前馈+反馈的经典组合"
4. **缺陷 4**（解耦矩阵没闭环）— 至少让 DecouplingController 在 orch=True 时也运行

### 第三优先级（锦上添花）
5. **缺陷 6+7**（振荡检测整合）
6. **缺陷 8**（条件积分）
7. **缺陷 9**（预测性压缩）

### 长期
8. **缺陷 3**（真实 A/B 对比）
9. **缺陷 10**（集成测试）
