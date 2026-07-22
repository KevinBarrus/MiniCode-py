你的任务是修复 MiniCode 工程控制论系统中的设计缺陷。缺陷清单在 `design_defects.md` 中。

---

## 核心原则

1. **修复完一个缺陷后，必须停下来，向我汇报 3 件事并等我确认**：
   - 改了什么（具体文件、函数、行数）
   - 数据流如何改变（修复前 vs 修复后）
   - 新的数据流是什么（用 ASCII 图表示）
  并且将你修复的内容以及你给我汇报的这三个点写入 fix_report.md 中

2. **最小改动原则**：只改必须改的代码，不动无关逻辑

3. **不引入新问题**：修完之后自己检查一遍数据流是否闭环

---

## 修复清单（按优先级）

### 优先级 1：传感器假数据（缺陷 1）

**问题**：`response_time` 和 `avg_latency` 使用硬编码的 `step * 2.0`，而非真实的 LLM 调用耗时。

**修复步骤**：

1. 在 `agent_loop.py` 中找到 `_model_next()` 调用点（约 950 行），在调用前后加 `time.time()` 计时，获取真实的 `actual_response_time`

2. 修改 `cybernetic_orchestrator.py` 的 `step_start()` 方法签名，增加 `actual_response_time: float = 0.0` 参数，替换 `response_time=step * 2.0`

3. 修改 `cybernetic_orchestrator.py` 的 `step_end()` 方法签名，增加 `actual_response_time: float = 0.0` 参数，替换 `avg_latency=step * 2.0`

4. 在 `agent_loop.py` 的 `step_start()` 和 `step_end()` 调用处，传入 `actual_response_time`

**修复后的数据流**：
```
LLM 调用 (agent_loop.py:950)
   │  t0 = time.time()
   │  next_step = _model_next(...)
   │  actual_response_time = time.time() - t0
   │
   ├──→ orch.step_start(actual_response_time=actual_response_time)
   │       └──→ StateObserver.update(MeasurementVector(response_time=actual_response_time))
   │              └──→ Kalman Filter 用真实耗时估算 internal_load
   │
   └──→ orch.step_end(actual_response_time=actual_response_time)
           └──→ StabilityMonitor.record_snapshot(MetricSnapshot(avg_latency=actual_response_time))
```

---

### 优先级 2：SelfHealingEngine placebo 策略（缺陷 2）

**问题**：8 个自愈策略中 4 个只返回 dict，没有实际执行修复动作。

**需要修复的 4 个方法**（都在 `self_healing_engine.py`）：

1. **`_execute_reduce_concurrency`**（约 323 行）
   - 当前：返回 `{"success": True}` 但什么都没改
   - 修复：设置 `self._tool_scheduler._force_max_workers = 1`（降低并发）
   - 注意：先检查 `self._tool_scheduler` 是否存在

2. **`_execute_reduce_timeout`**（约 332 行）
   - 当前：返回 `{"success": True}` 但什么都没改
   - 修复：如果 tool_scheduler 有超时参数，将其减半；如果没有，在返回的 dict 中用 `action` 字段说明"需要外部应用此更改"
   - 这是一个**半修复**——tool_scheduler 可能没有暴露超时接口。如果没有，诚实标注并返回 `{"success": False, "action": "timeout reduction not supported by current scheduler"}`

3. **`_execute_safe_mode`**（约 339 行）
   - 当前：返回 `{"success": True}` 但什么都没改
   - 修复：设置 `self._tool_scheduler._force_max_workers = 1` 并将工具执行切换为串行模式（如果 scheduler 支持）

4. **`_execute_force_terminate`**（约 391 行）
   - 当前：返回 `{"success": True}` 但什么都没改
   - 修复：调用 tool_scheduler 的取消/中断方法（如果存在）；如果没有，返回 `{"success": False, "action": "force_terminate requires scheduler interrupt support"}`

**修复后的数据流**：
```
SelfHealingEngine.detect_and_heal(metrics)
   │
   ├── RESOURCE_EXHAUSTION → _execute_reduce_concurrency()
   │      └──→ tool_scheduler._force_max_workers = 1  ← 真实修改
   │
   ├── TOOL_TIMEOUT → _execute_reduce_timeout()
   │      └──→ tool_scheduler.timeout /= 2  ← 真实修改 (如果支持)
   │
   ├── ERROR_SPIKE → _execute_safe_mode()
   │      └──→ tool_scheduler._force_max_workers = 1, 串行模式  ← 真实修改
   │
   └── DEADLOCK → _execute_force_terminate()
          └──→ tool_scheduler.cancel_all()  ← 真实修改 (如果支持)
```

---

### 优先级 3：FeedforwardController 不调 PID setpoint（缺陷 5）

**问题**：FeedforwardController 根据任务意图设置了 token_budget、concurrency，但从没修改 PID 的目标值。

**修复步骤**：

1. 在 `feedforward_controller.py` 的 `PreemptiveConfig` 中增加 3 个字段：
```python
stability_setpoint: float = 0.85
performance_setpoint: float = 0.75
efficiency_setpoint: float = 0.60
```

2. 在 `_INTENT_CONFIGS` 中为每个意图类型增加 setpoint 映射。逻辑：
   - REFACTOR/DEBUG：稳定性要求高（0.90），效率可以低
   - SEARCH/DOCUMENT：稳定性要求低（0.70），效率可以高
   - CODE/TEST：中等

3. 在 `feedback_controller.py` 的 `FeedbackController` 中增加方法：
```python
def set_setpoints(self, stability: float, performance: float, efficiency: float):
    self._stability_target = stability
    self._performance_target = performance
    self._efficiency_target = efficiency
```

4. 在 `cybernetic_orchestrator.py` 的 `step_end()` 中（或初始化时），从 `PreemptiveConfig` 读取 setpoint 并调用 `set_setpoints()`。注意：FeedforwardController 只在初始化时运行一次，所以 setpoint 只需要设置一次。

**修复后的数据流**：
```
agent_loop.py 初始化:
  FeedforwardController.preconfigure(parsed_intent)
    → PreemptiveConfig {
        token_budget, concurrency, model,  // 原有的
        stability_setpoint,                // 新增
        performance_setpoint,              // 新增
        efficiency_setpoint                // 新增
      }
    → FeedbackController.set_setpoints(...)  ← 新增的数据流
       └──→ PID 目标值随任务类型变化
```

---

### 优先级 4：解耦矩阵没闭环（缺陷 4）

**问题**：`DecouplingController.compute_decoupling_matrix()` 算出了耦合矩阵，但结果只用于日志展示。5 个独立 PID 之间的耦合没有被补偿。

**修复步骤**：

1. 在 `cybernetic_orchestrator.py` 的 `step_end()` 中（约 1363 行附近），确保 DecouplingController 在 orch=True 时也被调用（当前只在 `not orch` 时运行）

2. 在 `decoupling_controller.py` 中增加方法 `apply_to_pid()`：
```python
def apply_to_pid(self, context_pid, feedback_controller) -> dict[str, float]:
    """根据耦合强度调整 PID 参数。强耦合 → 降低 kp 避免过度反应。"""
    matrix = self.compute_decoupling_matrix()
    adjustments = {}
    for key, coupling in matrix.items():
        if coupling > 0.5:
            # 强耦合：降低 context_pid 的 kp 避免连锁反应
            adjustments[key] = coupling
    return adjustments
```

3. 在 `step_end()` 中调用 `apply_to_pid()`，根据耦合强度微调 PID 参数

**修复后的数据流**：
```
step_end():
  decoupling_controller.record_measurement(...)     ← 喂数据
  decoupling_controller.compute_decoupling_matrix()  ← 算耦合
  decoupling_controller.apply_to_pid(...)            ← 调 PID ← 新增
    └──→ context_cybernetics.pid.kp *= (1 - coupling * 0.5)  ← 强耦合降 kp
```

---

### 优先级 5：振荡检测整合（缺陷 6 + 7）

**问题**：两个独立的振荡检测器，一个输出 bool，一个输出 float。SystemState 的 oscillation_index 是死数据。

**修复步骤**：

1. 在 `context_cybernetics.py` 的 `CyberneticFeedbackLoop` 中，将 `detect_oscillation()` 改为返回**原始方向变化次数**（当前返回 bool），增加新方法 `get_direction_changes()`:

```python
def get_direction_changes(self) -> int:
    return self._direction_changes
```

2. 在 `context_cybernetics.py` 的 `to_system_state()` 中，将 `oscillation_index` 改为传入 `direction_changes`（原始值），而非 bool：

```python
oscillation_index=float(fb_stats.get("direction_changes", 0)) / 10.0  # 归一化到 0-1
```

3. 在 `feedback_controller.py` 的 `observe()` 中，**消费** `state.oscillation_index` 并和自己的 `_compute_oscillation()` 取加权平均：

```python
internal_osc = self._compute_oscillation()
external_osc = state.oscillation_index
signal.oscillation_index = internal_osc * 0.6 + external_osc * 0.4  # 加权融合
```

**修复后的数据流**：
```
ContextCybernetics:
  CyberneticFeedbackLoop.direction_changes  ← 原始信号
    → to_system_state() → SystemState.oscillation_index (归一化)

FeedbackController:
  state.oscillation_index (来自 ContextCybernetics)  ─┐
  _compute_oscillation() (自己的误差历史)            ─┤
                                                       ↓
                        加权融合 → ControlSignal.oscillation_index
                                       │
                                       └──→ SelfHealingEngine 消费
```

---

### 优先级 6：PredictiveController 预测→执行闭环（缺陷 9）

**问题**：预测到了问题只打日志，不执行。

**修复步骤**：

在 `cybernetic_orchestrator.py` 的 `step_start()` 中（约 213-217 行），将日志改为实际调用：

```python
# 修复前：
if action.recommended_action == "trigger_compaction" and self.context_cybernetics:
    logger.info("Predictive: trigger_compaction urgency=%.2f", action.urgency)
    # 什么都不做

# 修复后：
if action.recommended_action == "trigger_compaction" and self.context_cybernetics:
    logger.info("Predictive: trigger_compaction urgency=%.2f", action.urgency)
    if action.urgency > 0.8 and self.context_cybernetics.enabled:
        # 真正的预测性压缩：提前释放上下文空间
        self.context_cybernetics.run_cycle(messages, ...)
```

**修复后的数据流**：
```
step_start():
  PredictiveController.generate_predictive_actions()
    → PredictiveAction(urgency=0.85, recommended_action="trigger_compaction")
    → context_cybernetics.run_cycle()  ← 新增：提前压缩，不等 PID 反应
```

---

## 汇报格式模板

每修复完一个缺陷后，使用以下格式汇报：

```
## ✅ 缺陷 X 已修复：[缺陷名称]

### 修改的文件
- `xxx.py:123` — 做了什么改动
- `yyy.py:456` — 做了什么改动

### 数据流变化

修复前：
  A → B → (断裂) → C 收不到数据

修复后：
  A → B → C（数据完整传递）

### 新的数据流图
  [ASCII 图]

### 验证方法
  - 如何确认修复生效
```

---

## 不要做的事

- ❌ 不改动不相关的代码（"顺手优化"）
- ❌ 不引入新的参数/配置（除非修复必须）
- ❌ 不删除现有功能（即使看起来是死代码，除非明确标记为"可删除"）
- ❌ 修复完了不汇报
