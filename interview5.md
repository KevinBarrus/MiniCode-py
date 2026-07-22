# MiniCode 项目详解5：工程控制论核心（下）

## 零、Part 1 和 Part 2 的关系

在 Part 1（interview4.md）中我们理解了：

- **StateObserver**（Kalman）：从可测量输出估计隐藏状态
- **FeedbackController**（PID×3）：从 SystemState 计算 ControlSignal，改运行时
- **PredictiveController**（指数平滑+移动平均）：提前预测趋势

但有一个关键问题 Part 1 没回答：**`SystemState` 从哪里来？**

答案在 Part 2 的 `context_cybernetics.py` 中——它是连接"底层指标"和"外层 PID"的桥梁。

```
┌──────────────────────────────────────────────────────────┐
│  Part 1: 外层控制（已学）                                  │
│                                                          │
│  FeedbackController.observe(system_state) → ControlSignal │
│                              ↑                           │
│                         SystemState                      │
│                              ↑                           │
│  ═══════════════════════════════ 桥梁 ════════════════════ │
│                              ↑                           │
│  Part 2: 内层控制 + 辅助控制器（本节）                      │
│                                                          │
│  context_cybernetics.to_system_state() ← 核心桥梁          │
│  + stability_monitor（快照记录）                           │
│  + progress_controller（卡住检测）                         │
│  + decoupling_controller（耦合分析）                       │
│  + adaptive_pid_tuner（PID 自动调参）                      │
│  + cybernetic_supervisor（聚合报告）                       │
│  + self_healing_engine（已在 Part 1 7.5 节讲解）           │
└──────────────────────────────────────────────────────────┘
```

**本节策略**：context_cybernetics.py 深度讲解，其余 6 个控制器速通（理解职责 + 触发条件 + 具体做什么）。

---

## 一、架构总览：7 个控制器各管什么

| 控制器 | 文件 | 行数 | 一句话职责 | 难度 |
|--------|------|------|-----------|------|
| ContextCyberneticsOrchestrator | context_cybernetics.py | 851 | **双层 PID 的内层**，管上下文压缩 + 生产 SystemState | ⭐⭐⭐⭐⭐ |
| StabilityMonitor | stability_monitor.py | 392 | 记录每步指标快照，计算健康分/稳定性/鲁棒性 | ⭐⭐ |
| ProgressController | progress_controller.py | 161 | 检测任务是否卡住/完成，输出 CONTINUE/STOP/SWITCH | ⭐ |
| DecouplingController | decoupling_controller.py | 272 | 分析变量间耦合关系（如 token 增多→延迟升高） | ⭐⭐⭐ |
| AdaptivePIDTuner | adaptive_pid_tuner.py | 423 | 每 20 步自动调节 PID 的 kp/ki/kd 参数 | ⭐⭐⭐ |
| CyberneticSupervisor | cybernetic_supervisor.py | 266 | 聚合所有控制器快照，生成统一健康报告并持久化 | ⭐ |
| SelfHealingEngine | self_healing_engine.py | 534 | 故障检测+自动修复（**interview4.md 7.5 节已讲**） | ⭐⭐ |

**它们在 step_end 中的执行位置**（回忆 interview4.md 1.4 节）：

```
step_end() 执行顺序：
1. record_pattern_effectiveness()
2. StabilityMonitor.record_snapshot()        ← Part 2
3. ProgressController.decide()               ← Part 2
4. SelfHealingEngine.detect_and_heal()       ← Part 2 (已讲)
5. FeedbackController.observe(system_state)  ← Part 1
   ↑ system_state 来自 ContextCybernetics   ← Part 2
6. CyberneticSupervisor.report()             ← Part 2
7. AdaptivePIDTuner.tune()                   ← Part 2 (每20步)
8. MemoryPipeline.maintain()
```

---

## 二、ContextCybernetics：双层 PID 的"内层"与 SystemState 的生产者

### 2.1 它解决什么问题？

Part 1 的 FeedbackController 需要一个 `SystemState` 作为输入：

```python
system_state = context_cybernetics.to_system_state()
control_signal = feedback.observe(system_state)
```

**但 SystemState 里的字段（success_rate、context_usage、error_frequency 等）不能凭空产生。** ContextCybernetics 就是那个生产 SystemState 的工厂。

同时，它内部运行一个**独立的 PID 控制循环**，专门管理上下文压缩——这是"双层 PID"中的内层。

### 2.2 内部结构：7 个子组件

ContextCyberneticsOrchestrator 是一个**微型的控制论系统**，内部包含 7 个子组件：

```
ContextCyberneticsOrchestrator
│
├── ContextPressureSensor       ← 传感器：测量上下文压力
├── PredictiveOverflowGuard     ← 预测器：预测何时溢出
├── ContextPIDController        ← 控制器：内层 PID
├── AdaptiveThresholdManager    ← 适配器：动态调整压缩阈值
├── CompactionStrategySelector  ← 选择器：强度→策略
├── ContextCompactor            ← 执行器：实际压缩（外部注入）
└── CyberneticFeedbackLoop      ← 学习器：记录效果，自动调 kd/kp
```

它自己就是一个完整的 **感知→预测→控制→适配→选择→执行→学习** 闭环。

### 2.3 ContextPressureSensor：上下文压力传感器

```python
class ContextPressureSensor:
    """Continuous context pressure sensor with derivative estimation.

    Sliding-window sensor that measures not just current usage ratio,
    but also growth rate (1st derivative) and acceleration (2nd derivative)
    to enable predictive control actions.
    """

    def __init__(self, window_size: int = 10):
        self._window_size = window_size
        self._history: list[ContextPressureReading] = []
        self._last_token_count: int = 0
        self._last_usage_ratio: float = 0.0
        self._last_timestamp: float = 0.0
        self._last_growth_rate: float = 0.0

    def measure(
        self,
        token_count: int,
        message_count: int,
        context_window: int,
        *,
        turn_id: int = 0,
    ) -> ContextPressureReading:
        now = time.time()
        usage_ratio = token_count / max(context_window, 1) # 当前使用率

        dt = now - self._last_timestamp if self._last_timestamp > 0 else 1.0
        dt = max(dt, 0.001)

        # 一阶导数：增长速度
        raw_growth = (token_count - self._last_token_count) / max(context_window, 1) if self._last_timestamp > 0 else 0.0
        growth_rate = raw_growth / dt if dt > 0 else 0.0

        # 二阶导数：加速度
        acceleration = (growth_rate - self._last_growth_rate) / dt if dt > 0 else 0.0

        # 指数平滑
        alpha = 0.3
        smoothed_growth = alpha * growth_rate + (1 - alpha) * self._last_growth_rate

        # 异常检测
        anomaly = self._detect_anomaly(usage_ratio, smoothed_growth, acceleration)
```

它不只是测量"当前用了多少 token"，而是同时测量三个量：

| 测量量 | 物理含义 | 用途 |
|--------|---------|------|
| `usage_ratio` | 当前水位 | PID 的 process_variable |
| `growth_rate`（一阶导数） | 水位上涨速度 | 预测器输入 |
| `acceleration`（二阶导数） | 上涨是否在加速 | 异常检测（ACCELERATING_GROWTH） |

**三种异常类型**：

```python
def _detect_anomaly(self, usage_ratio, growth_rate, acceleration):
    # 突然飙升：当前使用率比近 3 次均值高 15% 且增长快
    if usage_ratio > avg_usage + 0.15 and growth_rate > 0.02:
        return AnomalyType.SUDDEN_SPIKE

    # 加速增长：加速度 > 0.001 且已有正增长
    if acceleration > 0.001 and growth_rate > 0.01:
        return AnomalyType.ACCELERATING_GROWTH

    # 振荡：growth_rate 反复变号但总使用率没变
    if signs 反复变化 and usage 基本不变:
        return AnomalyType.OSCILLATION
```

### 2.4 ContextPIDController：内层 PID

这是 FeedbackController 的 PID（Part 1 3.2 节）的**兄弟**。公式完全一样，但职责和参数不同：

| | FeedbackController 的 PID（外层） | ContextPIDController（内层） |
|---|---|---|
| 控制目标 | Agent 行为（并发、模型、预算） | 上下文压缩强度 |
| setpoint | stability=0.85, perf=0.75, eff=0.60 | **0.70**（目标使用率 70%） |
| process_variable | stability_score / performance_score | **上下文使用率（usage_ratio）** |
| kp, ki, kd | 1.5/1.0/0.8, 0.2/0.15/0.1, 0.1/0.08/0.05 | **2.0, 0.15, 0.3** |
| 输出范围 | [-1, 1] | **[0, 1]（压缩强度）** |
| integral_windup | 10.0 | **2.0**（上下文变化快，积分不能太大） |

**关键区别**：
- 内层 kp=2.0（比外层的 1.5 更激进）——上下文压力需要更快响应
- 内层 kd=0.3（比外层的 0.1 更大）——上下文容易振荡，需要更强的微分阻尼
- 内层 setpoint=0.70——留 30% 的余量，而不是等到 85% 才压

### 2.5 PredictiveOverflowGuard：预测何时溢出

和 Part 1 的 PredictiveController 类似，但更聚焦——只预测**上下文何时溢出**：

```python
class PredictiveOverflowGuard:
    def predict(self, horizon_turns=10) -> PredictiveOutlook:
        # 线性外推：当前使用率 + 增长速度 × 预测步数
        projected = self._smoothed_usage + effective_growth * horizon_turns

        # 计算剩余步数
        turns_to_overflow = (0.95 - usage) / growth_rate

        # urgency 查表（离散分段，不是 Part 1 的连续公式）
        if turns_to_overflow <= 0:       urgency = 1.0   # 已经爆了
        elif turns_to_overflow <= 3:     urgency = 0.8   # 3 步内溢出
        elif turns_to_overflow <= 6:     urgency = 0.5   # 6 步内
        elif turns_to_overflow <= 10:    urgency = 0.3   # 10 步内
```

**和 Part 1 PredictiveController 的区别**：

| | PredictiveController（Part 1） | PredictiveOverflowGuard（Part 2） |
|---|---|---|
| 预测范围 | 8 个指标（但只有 2 个有数据） | 1 个指标：上下文使用率 |
| 预测方法 | 指数平滑 + 移动平均 加权 | 指数平滑 + 线性外推 |
| urgency 公式 | 连续：`(pred - thresh) / thresh` | 离散：查表分段 |
| 触发动作 | 生成 PredictiveAction（大部分只打日志） | 影响 compaction strategy 选择 |

### 2.6 AdaptiveThresholdManager：动态阈值

普通的阈值是固定的（如 context_usage > 0.85 触发压缩）。但这个管理器**根据耦合关系动态调整阈值**：

```python
def get_effective_threshold(self) -> float:
    threshold = self._base_threshold  # 0.85

    # 根据任务意图调整（REFACTOR 任务比 SEARCH 任务需要更多上下文余量）
    if intent_type == "REFACTOR":  threshold → 0.72*0.4 + 0.85*0.6 = 0.80
    if intent_type == "SEARCH":    threshold → 0.88*0.4 + 0.85*0.6 = 0.86

    # 根据耦合关系收紧（上下文压力大 + 错误率高 → 提前压缩）
    if context_pressure 与 error_rate 强耦合:    threshold -= (coupling - 0.4) * 0.15
    if context_pressure 与 latency 强耦合:       threshold -= (coupling - 0.5) * 0.10

    return clamp(0.55, 0.95, threshold)
```

**逻辑**：如果上下文压力已经导致了错误率上升（耦合度高），就**提前**（降低阈值）触发压缩，避免上下文问题扩散到其他维度。

**意图类型映射**（`INTENT_THRESHOLD_MAP`）：

| 意图 | 阈值 | 理由 |
|------|------|------|
| REFACTOR | 0.72 | 重构涉及多文件，需要更多上下文余量 |
| DEBUG | 0.75 | 调试需要来回查看，上下文消耗快 |
| CODE | 0.78 | 一般编码 |
| TEST | 0.80 | 测试通常涉及较少上下文 |
| REVIEW | 0.85 | 审查通常读多写少 |
| SEARCH | 0.88 | 搜索操作上下文压力低 |
| DOCUMENT | 0.88 | 文档操作上下文压力低 |
| SYSTEM | 0.82 | 系统级操作 |

### 2.7 CompactionStrategySelector：强度→策略

PID 输出的是连续值 `intensity ∈ [0, 1]`，但压缩策略是离散的。Selector 做映射：

```python
STRATEGY_MAP = [
    (0.00, CompactStrategy.MICROCOMPACT),     # 0.00-0.30: 只清理旧工具结果
    (0.30, CompactStrategy.SESSION_MEMORY),   # 0.30-0.55: 用记忆系统做摘要压缩
    (0.55, CompactStrategy.FULL),             # 0.55-1.00: 完整压缩（结构化摘要+保留尾部）
]
```

**urgency 可以覆盖 intensity 决策**：

```
if urgency > 0.9:  FULL + force=True     ← 紧急：不管 intensity 多少，强制全量压缩
if urgency > 0.7:  SESSION_MEMORY        ← 高紧急：至少做记忆压缩
if SUDDEN_SPIKE:   SESSION_MEMORY        ← 异常飙升：立即响应
if ACCELERATING:   FULL                  ← 加速增长：提前全量压缩
```

### 2.8 CyberneticFeedbackLoop：压缩后的学习

每次压缩后，记录效果（是否有效、释放了多少 token、使用率变化）：

```python
def record(self, action, result, usage_before, usage_after):
    # 检测振荡：使用率反复上下 → PID 参数可能需要调整
    if now_direction != prev_direction:
        self._direction_changes += 1

def detect_oscillation(self) -> bool:
    # 近 6 次压缩后使用率方向变化 ≥ 3 次 → 振荡！
    return direction_changes >= 3

def recommend_pid_adjustment(self):
    if self.detect_oscillation():
        return {"kd_boost": 0.2, "kp_reduce": 0.1}
        # kd 增大 → 更强微分阻尼，抑制振荡
        # kp 减小 → 不那么激进
```

**这是真正的"控制论自愈"——通过观察自己的控制效果，自动调整 PID 参数。**

### 2.9 run_cycle()：一次完整的感知-思考-行动循环

把 7 个子组件串联起来的是 `run_cycle()`：

```python
def run_cycle(self, messages, error_rate, avg_latency, turn_id):
    # 1. SENSE: 测量当前上下文压力
    reading = self.sensor.measure(token_count, message_count, context_window)

    # 2. PREDICT: 更新预测模型 + 预测未来
    self.predictor.update(reading.usage_ratio, reading.growth_rate)
    outlook = self.predictor.predict(horizon_turns=10)

    # 3. ADAPT: 更新耦合关系 + 计算有效阈值
    self.update_coupling_metrics(error_rate, avg_latency)
    effective_threshold = self.threshold_mgr.get_effective_threshold()

    # 4. DECIDE: 需要行动吗？（三个条件任一满足）
    should_act = (
        reading.usage_ratio >= effective_threshold  # 超过动态阈值
        or outlook.urgency > 0.5                     # 预测高紧急
        or reading.anomaly is not None               # 检测到异常
    )

    # 5. CONTROL: PID 计算 + 融合预测强度
    pid_output = self.pid.compute(reading.usage_ratio)
    combined_intensity = max(pid_output, outlook.urgency * 0.8)

    # 6. SELECT: 强度→策略
    action = self.selector.select(combined_intensity, outlook.urgency,
                                   reading.anomaly, reading.usage_ratio)

    # 7. ACT: 执行压缩
    if should_act or action.force_execution:
        result = self.compactor.process_request(messages, ...)

    # 8. LEARN: 记录效果 + 自调 PID 参数
    self.feedback.record(action, result, usage_before, usage_after)
    if pid_adjustment := self.feedback.recommend_pid_adjustment():
        self.pid.kd += pid_adjustment["kd_boost"]
        self.pid.kp -= pid_adjustment["kp_reduce"]
```

### 2.10 to_system_state()：连接两层 PID 的桥梁

这是整个控制论系统中最关键的桥接方法：

```python
def to_system_state(self) -> SystemState:
    return SystemState(
        success_rate=fb_stats.get("effectiveness_rate", 1.0),    # 上下文压缩有效率
        avg_response_time=self._last_avg_latency,                # 从外部注入
        token_efficiency=1.0 - max(0, usage_ratio - 0.5),        # 使用率越低效率越高
        context_usage=reading.usage_ratio,                       # 来自传感器
        error_frequency=self._last_error_rate,                   # 从外部注入
        oscillation_index=1.0 if oscillation_detected else 0.0,  # 来自反馈环
        skill_effectiveness=fb_stats.get("effectiveness_rate"),  # = 压缩有效率
        pattern_reuse_rate=compactions / cycles,                 # 压缩频率
        knowledge_accumulation=min(1.0, cycles / 50.0),          # 经验积累
    )
```

**这张表告诉你 SystemState 的 9 个字段分别来自哪里**：

| SystemState 字段 | 来源 | 含义 |
|-----------------|------|------|
| `success_rate` | CyberneticFeedbackLoop.effectiveness_rate | 上下文压缩的有效率 |
| `avg_response_time` | 外部注入（agent_loop 估算） | 每步平均耗时 |
| `token_efficiency` | 传感器 reading.usage_ratio | 使用率越低效率越高 |
| `context_usage` | 传感器 reading.usage_ratio | 当前上下文使用率 |
| `error_frequency` | 外部注入（tool_error_count） | 工具错误率 |
| `oscillation_index` | CyberneticFeedbackLoop | 是否检测到振荡（0 或 1） |
| `skill_effectiveness` | feedback.effectiveness_rate | 压缩的有效率 |
| `pattern_reuse_rate` | 压缩总次数 / 周期数 | 压缩频率 |
| `knowledge_accumulation` | 周期数 / 50（封顶 1.0） | 经验积累程度 |

**关键理解**：SystemState 不是简单的传感器读数，它是从 ContextCybernetics 的内部状态（PID 输出、反馈环统计、传感器历史）**聚合**出来的综合画像。

### 2.11 控制器间的实际数据流（谁会调用谁）

上面的全景图（第九节）是简化的。**精确的数据流**如下——每一行都对应源码中的一个具体调用点：

```
━━━ step_start (cybernetic_orchestrator.py:176-217) ━━━
  StateObserver.update(MeasurementVector)
    ← response_time, success_rate, token_count, error_count
    → ObservedState (仅用于日志告警，不参与后续控制)

  PredictiveController.update("context_usage", value)
  PredictiveController.update("error_rate", value)
  PredictiveController.generate_predictive_actions()
    → PredictiveAction[] (仅 urgency>0.7 时打日志，不实际执行)

━━━ step_end (cybernetic_orchestrator.py:219-350) ━━━
  ① FeedbackController.record_pattern_effectiveness(pattern_id, success)
     → 更新 _pattern_scores (为正反馈准备数据)

  ② StabilityMonitor.record_snapshot(MetricSnapshot)
     → StabilityMonitor.feed_orchestrator(context_cybernetics)
       → ContextCybernetics.update_coupling_metrics()

  ③ ProgressController.decide(ProgressSignal)
     → ProgressDecision (STOP/CONTINUE/REQUEST_CONFIRMATION)
     (结果仅打日志，不直接中断循环)

  ④ SelfHealingEngine.detect_and_heal(metrics)
     ← error_rate, context_usage, oscillation_index (来自 FeedbackController)
     → HealingAction[] (实际执行修复：调 PID/降并发/安全模式)

  ⑤ ContextCybernetics.to_system_state()
     → SystemState
     → FeedbackController.observe(system_state)  ← 唯一核心数据流
       → ControlSignal
       → _apply_control_signal() (agent_loop.py:363-422)
         ├─ 修改 max_steps
         ├─ 修改 token_budget
         ├─ 修改 tool_scheduler._force_max_workers
         └─ 触发模型切换

  ⑥ CyberneticSupervisor.report(snapshots)
     ← 来自 context_cybernetics + cost_control + tool_scheduler
     → SupervisorReport → save_supervisor_report() → JSON 文件

  ⑦ AdaptivePIDTuner.tune(error, dt, performance_score)  [每20步]
     → PIDParameters → context_cybernetics.pid.kp/ki/kd = tuned

  ⑧ MemoryPipeline.maintain()
```

**哪些控制器的输出被真正消费了？**

| 控制器 | 输出 | 被谁消费 | 消费方式 |
|--------|------|---------|---------|
| **ContextCybernetics** | SystemState | FeedbackController | `observe(system_state)` |
| **FeedbackController** | ControlSignal | agent_loop.py | `_apply_control_signal()` |
| **SelfHealingEngine** | HealingAction | 自身执行器 | 调 PID/降并发/安全模式 |
| **AdaptivePIDTuner** | PIDParameters | ContextCybernetics.pid | `pid.kp/ki/kd = tuned` |
| **StabilityMonitor** | MetricSnapshot | ContextCybernetics | `feed_orchestrator()` |
| StateObserver | ObservedState | **无人消费** | 仅日志 |
| PredictiveController | PredictiveAction | **无人消费** | 仅日志 |
| ProgressController | ProgressDecision | **无人消费** | 仅日志 |
| DecouplingController | CouplingMatrix | **无人消费** | 仅日志 |
| CyberneticSupervisor | SupervisorReport | JSON 文件 | 持久化 |

**一半以上的控制器输出没有进入控制闭环。** 这是一个重要的事实——面试官可能会追问"为什么不删掉它们"。

**为什么不删**：它们提供可观测性（Observability）。在真实面试中，你可以这样回答：

> "StateObserver 的 Kalman 估计、PredictiveController 的趋势预测、DecouplingController 的耦合矩阵——它们不直接参与控制，但它们让我能**解释**系统在做什么。就像汽车的仪表盘不会直接控制发动机，但你不能没有它。这是工程控制论中'可观测性'和'可控制性'的区分：不是所有状态都需要被闭环控制，但它们需要被观测。"

### 2.12 to_system_state() 的 oscillation_index 是死数据

`to_system_state()` 中把 `CyberneticFeedbackLoop.detect_oscillation()` 的结果（True/False → 1.0/0.0）写入了 `SystemState.oscillation_index`。

**但 FeedbackController.observe() 从不读取 `state.oscillation_index`。**

```python
# feedback_controller.py:192-274
def observe(self, state: SystemState) -> ControlSignal:
    signal = ControlSignal()
    # observe() 使用的是 state.stability_score()、state.performance_score()、
    # state.error_frequency、state.context_usage、state.success_rate、
    # state.avg_response_time、state.token_efficiency、state.pattern_reuse_rate
    # 但从未读取 state.oscillation_index  ← 死数据！

    # signal.oscillation_index 来自 FeedbackController 自己的误差历史：
    error = 1.0 - state.stability_score()
    self._error_history.append(error)
    signal.oscillation_index = self._compute_oscillation()  # 自己的版本
```

**两个振荡检测器的对比**：

| | CyberneticFeedbackLoop.detect_oscillation | FeedbackController._compute_oscillation |
|---|---|---|
| 检测对象 | 压缩后 usage 的方向变化 | 稳定性误差的方向变化 |
| 输出类型 | bool (0 或 1) | float (0.0-1.0) |
| 被谁消费 | 写入 SystemState → **无人消费** | 写入 ControlSignal → 被 SelfHealingEngine 消费 |
| 代码位置 | `context_cybernetics.py:578-586` | `feedback_controller.py:299-315` |

**这说明什么**：`to_system_state()` 的实现是"我能提供什么"而不是"下游需要什么"——接口设计没有从消费者角度出发。这是一个典型的**过度设计接口**问题。

---

## 三、StabilityMonitor：系统健康度追踪

### 3.1 职责

每步记录指标快照 → 计算三个分数 → 检测异常。

### 3.2 核心逻辑

```python
def record_snapshot(self, snapshot: MetricSnapshot):
    self._metrics.append(snapshot)   # 滑动窗口存储
    self._update_baseline(snapshot)  # 指数平滑更新基线
    self._detect_anomalies(snapshot) # 阈值检测

def _compute_health_score(self) -> float:
    # 6 个指标的加权平均
    error_score   * 0.30  # 错误率最重要
    context_score * 0.20  # 上下文压力
    latency_score * 0.15
    cpu_score     * 0.15
    memory_score  * 0.10
    throughput    * 0.10

def _compute_stability_index(self) -> float:
    # 变异系数（CV）= 标准差/均值。CV 越小越稳定
    stability = 1.0 / (1.0 + latency_CV * 0.5 + error_CV * 0.5)

def _compute_robustness_score(self) -> float:
    # 比较高负载下的错误率和正常负载下的错误率
    # 鲁棒 = 高负载时错误率不飙升
```

### 3.3 在什么时候发挥作用？

```python
# orch.step_end() 中每步调用：
snapshot = MetricSnapshot(
    error_rate=tool_error_count / max(step, 1),
    avg_latency=step * 2.0,
    context_usage=context_manager.get_stats().usage_percentage,
)
self.stability.record_snapshot(snapshot)

# 同时把快照数据喂给 ContextCybernetics 做耦合分析
self.stability.feed_orchestrator(self.context_cybernetics)
```

**面试要点**：StabilityMonitor 不主动触发动作。它只**记录和评估**，结果供其他控制器（如 CyberneticSupervisor）使用。它的检测阈值和 SelfHealingEngine 的阈值是**独立的**——前者是"监控看板"，后者是"自动修复"。

---

## 四、ProgressController：任务卡住检测

### 4.1 职责

判断 Agent 是否在"健康推进"还是"已经卡住"。

### 4.2 核心逻辑

```python
def decide(self, signal: ProgressSignal) -> ProgressDecision:
    # 计算两个分数
    health = 0.35 + completion_ratio * 0.45 - failure_ratio * 0.35 - tool_error_ratio * 0.25
    if output_changed:  health += 0.15
    if tests_passed:    health += 0.15

    stall = 0.0
    if 完成步数为 0 但已跑 3 步:      stall += 0.30  # 一直在跑却没产出
    if 工具调用 ≥ 5 但输出没变:        stall += 0.25  # 做了很多事但没效果
    if 工具错误率 ≥ 50%:              stall += 0.30  # 大部分工具调用失败
    if 接近步数预算(>85%):            stall += 0.20  # 快超时了
    if 验证失败:                      stall += 0.20
    if 超 10 分钟且完成率 < 30%:       stall += 0.20  # 又慢又没有进展

    # 决策树
    if tests_passed and completion >= 95%:
        return STOP                    # 任务完成！
    if stall >= 0.75:
        return REQUEST_CONFIRMATION    # 快卡死了，请用户介入
    if stall >= 0.50:
        return SWITCH_STRATEGY         # 换策略
    if step_pressure >= 0.80:
        return NARROW_SCOPE            # 快超时了，缩小范围
    if output_changed and not verified:
        return VERIFY                  # 有产出但没验证，建议验证
    return CONTINUE
```

### 4.3 决策的六种动作

| 动作 | 触发条件 | 含义 |
|------|---------|------|
| `CONTINUE` | 一切正常 | 继续执行 |
| `VERIFY` | 有产出但未验证 | 建议暂停验证一下 |
| `SWITCH_STRATEGY` | stall ≥ 0.50 | 换个方法 |
| `NARROW_SCOPE` | 接近步数预算 | 缩小任务范围 |
| `REQUEST_CONFIRMATION` | stall ≥ 0.75 | 需要用户介入 |
| `STOP` | 任务完成且验证通过 | 停止执行 |

**面试要点**：ProgressController 只有 161 行，但它的决策直接影响 Agent 是否继续运行。它的设计是"宁可错判也不漏判"——stall 的阈值很保守，因为让一个卡住的 Agent 继续跑比让它暂停重来更浪费。

---

## 五、DecouplingController：多变量解耦

### 5.1 它解决什么问题？

系统中多个变量会互相影响。例如：
- token 使用量增大 → 响应延迟升高（token_usage_to_latency）
- 上下文压力增大 → 错误率升高（context_pressure_to_errors）
- 并发数增加 → 稳定性下降（concurrency_to_stability）

如果对这些耦合视而不见，一个 PID 的调整可能会恶化另一个 PID 的状态。

### 5.2 核心逻辑

```python
def record_measurement(self, variable_pairs):
    # 记录变量对：(token_usage, latency) 等
    for key, (val_a, val_b) in variable_pairs.items():
        self._coupling_analyzers[key].add_sample(val_a, val_b)

def compute_decoupling_matrix(self):
    # 对每对变量计算皮尔逊相关系数
    for key, analyzer in self._coupling_analyzers.items():
        coupling_strength = analyzer.compute_coupling()  # = |pearson_correlation|
        # 存储为矩阵
```

**皮尔逊相关系数**：衡量两个变量的线性相关程度。0 = 无关，1 = 完全正相关，-1 = 完全负相关。

```python
def _pearson_correlation(self, x, y):
    # cov(x,y) / (std(x) * std(y))
    numerator = sum((x[i] - x_mean) * (y[i] - y_mean))
    denominator = sqrt(sum((x[i]-x_mean)^2) * sum((y[i]-y_mean)^2))
    return numerator / denominator
```

### 5.3 什么时候被调用？

```python
# agent_loop.py 工具执行完成后、step_end 之前：
if decoupling_controller:
    decoupling_controller.record_measurement({
        "token_usage_to_latency": (context_usage, step * 2.0 / 60.0),
        "context_pressure_to_errors": (context_usage, error_rate),
    })
    decoupling_controller.compute_decoupling_matrix()
```

**面试要点**：DecouplingController 目前的数据输入比较粗糙（只有 2 对变量有实际数据），但它的架构是为更复杂的耦合分析预留的。它是一个"准备好了但还没充分利用"的控制器。

---

## 六、AdaptivePIDTuner：PID 自动调参

### 6.1 职责

每 20 步自动调节 ContextCybernetics 内部 PID 的 kp/ki/kd 参数。

### 6.2 三种调参方法

| 方法 | 适用场景 | 原理 |
|------|---------|------|
| PERFORMANCE_ADAPTIVE | 默认 | 根据误差大小和趋势微调参数（大误差→加 kp/kd，小误差→加 ki） |
| RELAY_FEEDBACK | 系统振荡时 | 施加继电器信号使系统产生极限环振荡，从振荡周期和幅值反推最优参数 |
| GRADIENT_BASED | 有足够历史数据 | 对每个参数加微小扰动，观察性能变化方向，梯度下降优化 |

### 6.3 核心逻辑

```python
def tune(self, error, dt, performance_score):
    if self._tuning_cooldown > 0:   # 冷却期，不调
        return

    # 判断是否需要切换调参方法
    if self._should_switch_method():
        self._switch_tuning_method()

    # 根据当前方法调参
    if method == RELAY_FEEDBACK:
        params = self._tune_relay(error, dt)
    elif method == GRADIENT_BASED:
        params = self._tune_gradient(performance_score)
    else:  # PERFORMANCE_ADAPTIVE（默认）
        params = self._tune_adaptive(error, dt)
```

**PERFORMANCE_ADAPTIVE 的简单规则**：

```python
def _tune_adaptive(self, error, dt):
    if error > 0.5:           # 偏差很大
        kp *= 1.1             # 加强比例响应
        kd *= 1.05            # 加强微分阻尼
    elif error < 0.1:         # 偏差很小
        ki *= 1.05            # 加强积分消除微小稳态误差
        kd *= 0.95            # 放松微分

    if error_trend > 0.3:     # 误差在快速增大
        kd *= 1.2             # 大幅加强微分阻尼
        kp *= 0.9             # 降低激进程度
    elif error_trend < -0.3:  # 误差在快速减小
        ki *= 1.1             # 加强积分巩固成果
```

### 6.4 什么时候被调用？

```python
# orch.step_end() 中，每 20 步一次：
if step > 0 and step % 20 == 0:
    tuned = self.adaptive_tuner.tune(stability_error, dt=1.0, performance_score=perf)
    if tuned:
        self.context_cybernetics.pid.kp = tuned.kp
        self.context_cybernetics.pid.ki = tuned.ki
        self.context_cybernetics.pid.kd = tuned.kd
```

**面试要点**：AdaptivePIDTuner 调的是**内层 PID**（ContextPIDController），不是外层（FeedbackController 的 3 个 PID）。外层 PID 的参数目前是写死的。

---

## 七、CyberneticSupervisor：监控报告聚合器

### 7.1 职责

收集各控制器的快照 → 聚合成一份健康报告 → 持久化到磁盘。

### 7.2 核心逻辑

```python
def report(self, snapshots: list[ControlSnapshot]) -> SupervisorReport:
    health = avg(snapshots)           # 所有快照健康分的均值
    max_risk = max(snapshots)         # 最高风险值
    risk_level = classify(max_risk)   # <0.4 LOW, <0.7 MEDIUM, <0.9 HIGH, ≥0.9 CRITICAL
    actions = collect_actions()       # 收集所有非"继续"的动作建议
    return SupervisorReport(health, risk_level, snapshots, actions)
```

数据来源（在 orch.step_end 中）：

```python
snapshots = []
snapshots.append(supervisor.snapshot_from_context(context_cybernetics.get_stats()))
snapshots.append(supervisor.snapshot_from_cost(cost_control.get_stats()))
snapshots.append(supervisor.snapshot_from_tool_decision(tool_scheduler.last_decision))
report = supervisor.report(snapshots)
save_supervisor_report(report)  # 持久化到 .minicode/cybernetic_supervisor.json
```

### 7.3 面试要点

这是最"薄"的控制器——纯数据聚合，没有反馈逻辑。它的价值在于**可观测性**：你可以随时查看 `.minicode/cybernetic_supervisor.json` 了解系统当前健康状态。

---

## 八、Part 2 各控制器速查表

| 控制器 | 输入 | 输出 | 触发频率 | 修改了什么 |
|--------|------|------|---------|-----------|
| ContextCybernetics | token 数、消息数、error_rate、latency | 压缩后的 messages + SystemState | 每步 | messages 内容 + SystemState |
| StabilityMonitor | error_rate, context_usage, latency | MetricSnapshot 入队 + 异常记录 | 每步 | 内部历史队列 |
| ProgressController | step, errors, output_changed | CONTINUE/STOP/SWITCH 等 | 每步 | **仅返回建议，不直接修改运行时** |
| DecouplingController | (token, latency), (pressure, error) 等 | 耦合矩阵 | 每步 | 内部矩阵 |
| AdaptivePIDTuner | stability_error, perf_score | 新 kp/ki/kd | 每 20 步 | **context_cybernetics.pid 的参数** |
| CyberneticSupervisor | 各控制器快照 | SupervisorReport（持久化） | 每步 | JSON 文件 |
| SelfHealingEngine | error_rate, context_usage, oscillation | HealingAction | 每步 | 调 compactor/pid/并发（详见 Part 1 7.5） |

---

## 九、完整系统全景图

```
                         ┌─── agent_loop.py ───┐
                         │                      │
step_start ──────────────┤  StateObserver       │
                         │  PredictiveController│
                         │                      │
LLM + 工具执行 ──────────┤  ErrorClassifier      │
                         │  NudgeGenerator      │
                         │  ReadDedup           │
                         │                      │
                         │  DecouplingController│ ← Part 2
                         │                      │
step_end ────────────────┤  StabilityMonitor    │ ← Part 2
                         │  ProgressController  │ ← Part 2
                         │  SelfHealingEngine   │ ← Part 2 (已讲)
                         │  FeedbackController  │ ← Part 1
                         │    ↑                 │
                         │    │ SystemState     │
                         │    │                 │
                         │  ContextCybernetics  │ ← Part 2 ★核心桥梁
                         │  CyberneticSupervisor│ ← Part 2
                         │  AdaptivePIDTuner    │ ← Part 2 (每20步)
                         │  MemoryPipeline      │
                         └──────────────────────┘
```

**两条控制线路**：

1. **上下文线路**（内层）：ContextPressureSensor → ContextPIDController → StrategySelector → ContextCompactor → 压缩 messages → CyberneticFeedbackLoop 学习 → 自调 PID
2. **行为线路**（外层）：ContextCybernetics.to_system_state() → FeedbackController.observe() → ControlSignal → _apply_control_signal → 改并发/改模型/改预算

---

## 十、面试要点速查

| 问题 | 答案要点 |
|------|---------|
| **SystemState 从哪里来** | context_cybernetics.to_system_state()。9 个字段来自传感器、反馈环统计、外部注入 |
| **为什么叫"双层 PID"** | 内层 ContextPIDController 管上下文压缩强度，外层 FeedbackController 管 Agent 行为调优 |
| **内外层 PID 参数为什么不同** | 内层 kp=2.0/kd=0.3（更激进，上下文压力响应要快），外层 kp=1.0-1.5（更稳健） |
| **ContextPressureSensor 测什么** | 不只是使用率，还有 growth_rate（一阶导数）和 acceleration（二阶导数）+ 三种异常检测 |
| **PredictionOverflowGuard 和 PredictiveController 的区别** | OverflowGuard 只预测上下文溢出（线性外推+离散urgency），PredictiveController 预测 8 个指标（指数平滑+移动平均+连续urgency） |
| **动态阈值有什么用** | 根据任务意图（REFACTOR→0.72, SEARCH→0.88）和耦合关系动态调整压缩触发点 |
| **ProgressController 怎么判断卡住** | 6 个信号加分：无完成步、无输出变化、高错误率、接近预算、验证失败、慢进度。stall≥0.75→请求用户介入 |
| **DecouplingController 怎么计算耦合** | 皮尔逊相关系数：cov(x,y)/(std(x)*std(y))。衡量两个变量的线性相关程度 |
| **AdaptivePIDTuner 什么时候调参** | 每 20 步。默认用简单规则（大误差→加kp/kd，小误差→加ki），振荡时切到继电器反馈法 |
| **CyberneticSupervisor 的价值** | 可观测性。报告持久化到 .minicode/cybernetic_supervisor.json，可随时查看系统健康状态 |
| **哪些控制器实际修改了运行时** | ContextCybernetics（改 messages）、FeedbackController（改 tool_scheduler/model_switcher）、SelfHealingEngine（改 PID/compactor）、AdaptivePIDTuner（改内层 PID 参数） |
| **哪些控制器只观察不修改** | StabilityMonitor、ProgressController、DecouplingController、CyberneticSupervisor |
