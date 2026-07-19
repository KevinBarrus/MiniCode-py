# MiniCode 项目详解4：工程控制论核心（上）

## 零、先回答一个问题

### urgency、confidence 这些指标是被谁改变的？

在进入正文之前，先把这个链路说清楚，因为这是理解整个控制论系统的钥匙：

```
┌─────────────────────────────────────────────────────────────────┐
│  数据来源：传感器层（agent_loop.py 每步自动采集）                  │
│                                                                  │
│  tool_error_count  → 每次工具失败时 +1（agent_loop.py:1281）     │
│  step              → 每轮循环 +1（agent_loop.py:851）             │
│  context_usage     → context_manager.get_stats() 实时查询         │
│  avg_latency       → step * 2.0（估算值，每次 LLM 调用约2秒）     │
│                                                                  │
├─────────────────────────────────────────────────────────────────┤
│  计算来源：估计器层（每步 step_start 时计算）                      │
│                                                                  │
│  StateObserver (Kalman Filter):                                  │
│    internal_load      ← 从 response_time 推算                    │
│    hidden_errors      ← 从 success_rate 推算                     │
│    context_pressure   ← 从 context_length 推算                   │
│    system_degradation ← 综合以上三个指标                          │
│    confidence         ← 5 个 Kalman Filter 的置信度加权平均       │
│                                                                  │
│  PredictiveController (指数平滑 + 移动平均):                      │
│    urgency            ← 从预测值和趋势方向计算                    │
│                                                                  │
│  FeedbackController (PID):                                       │
│    oscillation_index  ← 误差信号的方向变化频率                    │
│    control_signal                                     │
│      .confidence      ← 1.0 - |stability_pid_output| * 0.3       │
│                                                                  │
│  StabilityMonitor:                                               │
│    health_score       ← 综合多个快照的稳定性评估                  │
│                                                                  │
│  ProgressController:                                             │
│    stall_score        ← 输出变化频率 + 错误率                     │
│    health_score       ← 综合评估                                  │
└─────────────────────────────────────────────────────────────────┘
```

**一句话**：这些指标没有"魔法"，它们全部来自**传感器数据 + 数学公式**。你可以打开每个控制器的代码，找到具体的计算公式。

---

## 一、CyberneticOrchestrator：控制论的"指挥中心"

### 1.1 它在整条链路的什么位置？

```
agent_loop.py 初始化阶段:
    orch = CyberneticOrchestrator()        # 创建门面
    orch.initialize(model, tools, runtime)  # 初始化15个控制器
    orch.wire_memory(memory_mgr)            # 注入记忆管线
    orch.wire_healing(tool_scheduler, ...)  # 注入自愈引擎

agent_loop.py 主循环:
    while step < max_steps:
        orch.step_start(...)   # ← 每次调 LLM 前
        next_step = model.next(...)
        orch.step_end(...)     # ← 工具执行后
```

**Orchestrator 本身不做任何控制论计算**。它只负责三件事：
1. **初始化**：创建 15 个控制器实例并注入依赖
2. **编排**：在 step_start/step_end 时按正确顺序调用各控制器
3. **汇总**：收集各控制器的输出，打包返回给 agent_loop.py

### 1.2 initialize() 做了什么？（110-143行）

```python
def initialize(self, model, tools, runtime):
    self.feedback = FeedbackController()       # 双 PID
    self.cyber_supervisor = CyberneticSupervisor()  # 聚合监控
    self.stability = StabilityMonitor(100)     # 健康度追踪
    self.adaptive_tuner = AdaptivePIDTuner()   # PID 自动调参
    self.state_observer = StateObserver()      # Kalman 滤波×5
    self.decoupling = DecouplingController()   # 多变量解耦
    self.predictive = PredictiveController()   # 指数平滑预测
    self.progress = ProgressController()       # 卡住检测
    self.cost_control = CostControlLoop()      # 成本 PID
    self.memory_ctrl = MemoryInjectionController()  # 记忆注入决策
    self.model_ctrl = ModelSelectionController()    # 模型选择
    self.smart_router = SmartRouter()          # 任务→模型路由
    self.reflection = ReflectionEngine(...)    # 任务后反思
    self.model_switcher = ModelSwitcher(...)   # 运行时热切换
```

**15 个控制器全部在这里创建**。创建后它们之间没有任何通信——Orchestrator 是唯一的协调点。

### 1.3 step_start()：调 LLM 之前的"体检"（175-217行）

```python
def step_start(self, context_manager, step, tool_error_count, saw_tool_result):
    # 步骤1：Kalman 状态估计（每步必做）
    measurement = MeasurementVector(
        response_time=step * 2.0,                # 估算值
        success_rate=1.0 - (tool_error_count / max(step, 1)),
        context_length=context_manager.get_stats().total_tokens,
        error_count=tool_error_count,
    )
    observed = self.state_observer.update(measurement)
    # 如果估计的置信度 > 40% 且系统退化 > 40%，发出警告

    # 步骤2：预测控制（每步必做，但只在 urgency > 0.7 时干预）
    self.predictive.update("context_usage", usage_pct)
    self.predictive.update("error_rate", error_rate)
    if step > 2:
        actions = self.predictive.generate_predictive_actions()
        if actions[0].urgency > 0.7:
            # 只有高紧急度的预测才触发动作
```

**关键理解**：step_start 的两个控制器（StateObserver 和 PredictiveController）**每步都运行**，但只在指标超过阈值时才"出声"：

| 控制器 | 每步都算？ | 干预条件 | 不满足条件时 |
|--------|:--------:|---------|------------|
| StateObserver | ✅ | confidence > 0.4 **且** degradation > 0.4 | 静默运行，只更新内部状态 |
| PredictiveController | ✅ | urgency > 0.7 | 静默运行，只更新预测模型 |

### 1.4 step_end()：工具执行后的"反馈调整"（218-349行）

这是 Orchestrator 最复杂的方法。**按顺序**执行以下步骤：

```
step_end() 执行顺序：

1. FeedbackController.record_pattern_effectiveness()
   → 记录本步的模式是否有效（为后续正反馈准备数据）

2. StabilityMonitor.record_snapshot()
   → 记录本步的快照（错误率、延迟、上下文使用率）

3. ProgressController.decide()
   → 检测任务是否卡住（stall_score 高？health_score 低？）

4. SelfHealingEngine.detect_and_heal()
   → 检测是否需要自愈（输入：error_rate, context_usage, oscillation_index）

5. FeedbackController.observe(system_state)  ← 核心！
   → 双 PID 计算控制信号（ControlSignal）
   → 返回给 agent_loop.py，由 _apply_control_signal() 应用到运行时

6. CyberneticSupervisor.report()
   → 聚合所有控制器快照，生成监控报告

7. AdaptivePIDTuner.tune()（每20步一次）
   → 自动调节 PID 参数（kp, ki, kd）

8. MemoryPipeline.maintain()
   → 后台记忆维护（清理、优化）
```

**面试要点**：step_end 的执行顺序是精心设计的——先收集数据 → 再检测异常 → 再计算控制信号 → 最后做长周期优化。

---

## 二、StateObserver：Kalman 滤波器——"看不见的状态，我来估计"

### 2.1 它解决什么问题？

Agent 运行中，有些状态是**直接可测量**的，有些是**只能推断**的：

| 可直接测量（传感器） | 只能推断（估计器） |
|---|---|
| 工具成功/失败 | 系统是否正在退化？ |
| 上下文用了多少 token | 存在多少隐藏错误？ |
| LLM 响应花了多长时间 | 内部负载有多高？ |
| 工具调用了多少次 | 上下文压力有多大？ |

**Kalman Filter 的作用**：从可测量输出，估计不可测量的内部状态。

### 2.2 实现：5 个独立的 Kalman Filter

```python
class StateObserver:
    def __init__(self):
        # 每个 Kalman Filter 估计一个"隐藏状态"
        self._internal_load_kf = KalmanFilter(      # → 真实负载
            process_noise=0.02, measurement_noise=0.15)
        self._hidden_errors_kf = KalmanFilter(      # → 隐藏错误概率
            process_noise=0.01, measurement_noise=0.2)
        self._context_pressure_kf = KalmanFilter(   # → 上下文压力
            process_noise=0.03, measurement_noise=0.1)
        self._skill_mastery_kf = KalmanFilter(      # → 技能掌握度
            process_noise=0.05, measurement_noise=0.25)
        self._system_degradation_kf = KalmanFilter( # → 系统退化程度
            process_noise=0.005, measurement_noise=0.1)
```

**每个 Kalman Filter 的核心公式**（KalmanFilter.update）：

```python
def update(self, measurement: float) -> float:
    # 步骤1：预测不确定性（这次估计有多不准？）
    prediction_uncertainty = self.uncertainty + self.process_noise

    # 步骤2：计算卡尔曼增益（应该信任测量值多少？）
    kalman_gain = prediction_uncertainty / (prediction_uncertainty + measurement_noise)

    # 步骤3：更新估计值（测量值和之前估计值的加权平均）
    self.estimate = self.estimate + kalman_gain * (measurement - self.estimate)

    # 步骤4：更新不确定性（越更新越确定）
    self.uncertainty = (1.0 - kalman_gain) * prediction_uncertainty
```

### 2.3 从测量值到估计值的映射

```python
def update(self, measurement: MeasurementVector) -> ObservedState:
    # 每个可测量量 → 对应一个 Kalman Filter 的输入

    internal_load = self._estimate_internal_load(measurement)
    # 输入：response_time（LLM 响应时间）
    # 隐藏状态的物理意义：
    #   - 如果 response_time 突然变大 → 内部负载可能很高
    #   - Kalman 会平滑这个推断，不会因一次慢响应就断定高负载

    hidden_errors = self._estimate_hidden_errors(measurement)
    # 输入：success_rate（工具成功率）
    # 隐藏状态的物理意义：
    #   - 成功率高不代表没有隐藏问题（可能只是还没触发）
    #   - Kalman 根据成功率的趋势推断隐藏错误的概率

    context_pressure = self._estimate_context_pressure(measurement)
    # 输入：context_length（上下文长度）
    # 隐藏状态的物理意义：
    #   - 上下文快到上限时，即使还没报错，压力已经存在
    #   - Kalman 提前感知这种压力

    system_degradation = self._estimate_system_degradation(measurement)
    # 输入：综合 internal_load + hidden_errors + context_pressure
    # 隐藏状态的物理意义：
    #   - 系统整体是否在退化（多个指标同时恶化）
```

### 2.4 在什么条件下发挥作用？

```
每步 step_start 时：

    StateObserver.update(measurement)
        ↓
    ObservedState {
        internal_load: 0.65,      ← Kalman 估计值
        hidden_errors: 0.30,      ← Kalman 估计值
        context_pressure: 0.72,   ← Kalman 估计值
        system_degradation: 0.45, ← 综合指标
        confidence: 0.78,         ← 5个KF置信度加权平均
    }
        ↓
    if confidence > 0.4 and system_degradation > 0.4:
        → logger.warning("系统可能正在退化")   ← 预警
        → 后续 SelfHealing 可能会被触发
```

### 2.5 具体场景

**场景：Agent 在执行复杂重构，前 5 步一切正常**

```
step=6 时，LLM 返回了一个语法错误（工具失败）
    → success_rate 从 1.0 降到 0.83

StateObserver 的反应：
    hidden_errors_kf.update(0.83)
    → 之前的 estimate 是 0.05（一直很健康）
    → kalman_gain ≈ 0.15（测量噪声大，不太信任单次失败）
    → 新 estimate = 0.05 + 0.15 * (0.83 - 0.05) = 0.17
    → 只是略升到 0.17，没有恐慌

step=7 时，又一个工具失败
    → success_rate = 0.71

StateObserver 的反应：
    hidden_errors_kf.update(0.71)
    → 之前的 estimate 是 0.17
    → kalman_gain 略有上升（不确定性降低了）
    → 新 estimate = 0.17 + 0.18 * (0.71 - 0.17) = 0.27
    → 继续上升，但仍然是渐进的

step=10 时，连续失败了 4 次
    → success_rate = 0.60

StateObserver 的反应：
    → estimate 已经稳步上升到 0.55
    → confidence > 0.6（经过多次观测，很确定）
    → system_degradation > 0.4（综合退化明显）
    → 触发警告："系统可能正在退化！"
```

**这就是 Kalman Filter 的价值**：不会因为一两次失败就恐慌，而是**持续观测、渐进更新**，只在有足够证据时才发出预警。

---

## 三、FeedbackController：双 PID——"系统偏了，我来校正"

### 3.1 它解决什么问题？

Agent 运行中，很多指标会偏离理想值：

- 工具错误率太高 → 需要**减速/降并发**
- 上下文太满 → 需要**强制压缩**
- 性能太好 → 可以**降级模型省钱**
- Token 效率低 → 需要**调整预算**

**PID 控制器的作用**：计算"应该调整多少"，输出一个精确的控制信号。

### 3.2 三个 PID 控制器，各管一摊

```python
class FeedbackController:
    def __init__(self):
        # 三个独立 PID，分别控制三个维度
        self._stability_pid = PIDController(kp=1.5, ki=0.2, kd=0.1)
        self._performance_pid = PIDController(kp=1.0, ki=0.15, kd=0.08)
        self._efficiency_pid = PIDController(kp=0.8, ki=0.1, kd=0.05)

        # 各自的"理想值"（setpoint）
        self._stability_target = 0.85      # 希望稳定性 > 85%
        self._performance_target = 0.75    # 希望性能 > 75%
        self._efficiency_target = 0.60     # 希望效率 > 60%
```

**每个 PID 控制器的核心公式**：

```python
class PIDController:
    def compute(self, setpoint, measured, dt):
        error = setpoint - measured    # 当前偏差

        P = kp * error                 # 比例项：对应当前偏差
        I = ki * ∫error dt            # 积分项：累积偏差（消除稳态误差）
        D = kd * d(error)/dt          # 微分项：偏差变化率（预测趋势）

        return P + I + D              # PID 输出
```

### 3.3 P、I、D 三项各自的作用

用一个具体场景解释。假设 **上下文使用率持续上升**：

```
时间点    实际使用率    偏差(error)    P项        I项        D项       PID输出
─────────────────────────────────────────────────────────────────────────────
t=1       65%          0.05          +0.075     +0.01      +0.007    +0.092
t=2       72%          0.12          +0.180     +0.03      +0.010    +0.220
t=3       80%          0.20          +0.300     +0.07      +0.012    +0.382
t=4       89%          0.29          +0.435     +0.13      +0.013    +0.578
                                                          ↑
                                                    变化率在减小
                                                    （增速放缓）
```

| 项 | 含义 | 在这个例子中 |
|----|------|------------|
| **P（比例）** | "现在差多少？" | 使用率越高，P 越大，压缩强度越大 |
| **I（积分）** | "已经差了多久？" | 持续超标的累积惩罚，防止"一直差一点但就是不触发" |
| **D（微分）** | "趋势是变好还是变坏？" | t=4 时增速放缓，D 项减小 → 防止过度压缩 |

**三句话理解 PID**：
- **P**：现在错了，现在就改
- **I**：一直错，就加大力度
- **D**：趋势在变好，就收一点力（防止超调）

### 3.4 observe()：从系统状态到控制信号（191-272行）

这是 FeedbackController 的核心方法。整个流程：

```
SystemState（来自 ContextCybernetics）
    │
    ├─ stability_score()  → stability_pid.compute(0.85, stability) → stability_output
    ├─ performance_score() → performance_pid.compute(0.75, perf)   → performance_output
    └─ token_efficiency   → efficiency_pid.compute(0.60, eff)     → efficiency_output
                                │
                                ▼
                         ControlSignal {
                            reduce_parallelism: True/False
                            force_compaction: True/False
                            increase_model_level: True/False
                            adjust_token_budget: 0.7
                            adjust_concurrency: +2
                            oscillation_index: 0.3
                            confidence: 0.85
                            reason: "低稳定性 (0.62)，启动负反馈调节"
                         }
```

### 3.5 PID 输出如何映射到具体动作？

```python
def observe(self, state: SystemState) -> ControlSignal:

    # ── 稳定性 PID ──
    stability_output = self._stability_pid.compute(0.85, stability_score, dt)

    if stability_output > 0.3:               # 稳定性低于目标
        signal.reduce_parallelism = True      #   → 减少并发
        signal.increase_nudge_frequency = True # → 更频繁引导 LLM

        if state.error_frequency > 3.0:       # 错误率非常高
            signal.reduce_tool_timeout = 15.0  #   → 缩短超时（快速失败）
            signal.limit_max_steps = 20        #   → 限制步数

        if state.context_usage > 0.85:         # 上下文快满了
            signal.force_compaction = True      #   → 强制压缩

    elif stability_output < -0.3:            # 稳定性高于目标
        signal.adjust_concurrency = +2        #   → 可以更大胆，增加并发

    # ── 性能 PID ──
    performance_output = self._performance_pid.compute(0.75, perf_score, dt)

    if performance_output > 0.3:              # 性能不足
        if state.avg_response_time > 30.0:     # 响应太慢
            signal.increase_model_level = True  #   → 升级模型

    elif performance_output < -0.3:           # 性能超过目标
        if state.success_rate > 0.9:           # 成功率很高
            signal.decrease_model_level = True  #   → 降级模型省钱

    # ── 效率 PID ──
    efficiency_output = self._efficiency_pid.compute(0.60, efficiency, dt)

    if efficiency_output > 0.3:               # 效率不足
        signal.adjust_token_budget = 0.7       #   → 收紧 token 预算

    # ── 正反馈（强化有效模式）──
    if perf_score > 0.85 and pattern_reuse_rate > 0.3:
        signal.recommend_skill_update = True    #   → 推荐技能更新
        signal.suggest_memory_persistence = True # → 持久化记忆
```

### 3.6 振荡检测：_compute_oscillation()

```python
def _compute_oscillation(self) -> float:
    """检测误差信号是否在振荡（方向反复变化）"""
    # 统计最近 N 个误差值中，方向变化的次数
    direction_changes = 0
    for i in range(2, len(self._error_history)):
        prev_delta = error[i-1] - error[i-2]
        curr_delta = error[i] - error[i-1]
        if prev_delta * curr_delta < 0:  # 方向变了！
            direction_changes += 1

    return direction_changes / (len(error_history) - 2)
```

**物理意义**：如果误差一会大一会小（振荡），说明系统不稳定。oscillation_index 高 → 需要更保守的控制策略。

### 3.7 control_signal.confidence 怎么来的？

```python
signal.confidence = min(1.0, max(0.3, 1.0 - abs(stability_output) * 0.3))
```

| stability_output | confidence | 含义 |
|-----------------|-----------|------|
| 0.0（刚好在目标） | 1.0 | "系统状态很好，控制信号非常可信" |
| 0.5（中等偏差） | 0.85 | "有一定偏差，信号比较可信" |
| 1.0（严重偏差） | 0.70 | "偏差很大，信号中等可信" |
| 2.0（极度偏差） | 0.40 | "偏差极大，信号不太可信" |

**设计逻辑**：偏差越大，说明系统越不稳定，此时的控制信号反而应该谨慎使用。这是一种**保守策略**。

### 3.8 在什么条件下发挥作用？

```
每步 step_end 时：

    if context_cybernetics and feedback:
        system_state = context_cybernetics.to_system_state()  ← 获取系统状态
        control_signal = feedback.observe(system_state)       ← 计算控制信号
        summary["control_signal"] = control_signal            ← 打包返回

返回给 agent_loop.py 后：
    _apply_control_signal(control_signal, ...)
    → 只有 control_signal.confidence > 0.6 时才应用
```

**条件链**：
1. context_cybernetics 存在（enable_work_chain=True）
2. feedback 存在（Orchestrator 初始化成功）
3. control_signal.confidence > 0.6（PID 输出可信）

三个条件都满足，控制信号才会真正改变运行时参数。

### 3.9 具体场景

**场景：Agent 在调试一个顽固的 bug，已经试了 10 步**

```
step=10, 状态快照:
    error_rate = 0.50（每2步就失败1次）
    context_usage = 0.88（上下文快满了）
    avg_response_time = 45.0（响应越来越慢）

SystemState:
    stability_score = 1.0 - (0.50*0.3 + 0.20*0.2 + 0.15*0.2 + 0.08*0.3)
                    = 1.0 - 0.254
                    = 0.746  ← 低于目标 0.85
    performance_score = 0.50*0.3 + 0.30*0.2 + 0.25*0.2 + ...
                      = 0.31  ← 严重低于目标 0.75

FeedbackController.observe():
    stability_pid.compute(0.85, 0.746):
        error = 0.85 - 0.746 = 0.104
        P = 1.5 * 0.104 = 0.156
        I = 0.2 * 0.45 = 0.090（累积了一段时间）
        D = 0.1 * 0.02 = 0.002
        stability_output = 0.248  ← 没超过 0.3，暂时不触发

    performance_pid.compute(0.75, 0.31):
        error = 0.75 - 0.31 = 0.44
        P = 1.0 * 0.44 = 0.44
        I = 0.15 * 0.98 = 0.147
        D = 0.08 * 0.05 = 0.004
        performance_output = 0.591  ← 超过 0.3！

    → signal.increase_model_level = True
      （因为 avg_response_time = 45.0 > 30.0）
    → signal.reason = "性能不足 (0.31)，建议升级模型"

_apply_control_signal():
    control_signal.confidence = 1.0 - |0.248| * 0.3 = 0.926 > 0.6 ✅
    → signal.increase_model_level = True
    → model_switcher._pending_upgrade = True
    → 下一步可能切换到更强的模型
```

**这个场景展示的核心逻辑**：当系统持续在低性能状态挣扎时，PID 不会在第一秒就建议升级模型（P 项可能不够），但当积分项（I）累积到一定程度后，就会触发升级建议。

---

## 四、PredictiveController：预测控制——"危险还没来，但我看见了"

### 4.1 它解决什么问题？

FeedbackController 是**事后调整**（已经出错了才响应），PredictiveController 是**事前预防**（还没出错就提前行动）。

### 4.2 实现：指数平滑 + 移动平均

```python
class PredictiveController:
    def __init__(self):
        # 每个指标维护两个预测器
        self._predictors[metric] = {
            "exp_smoother": ExponentialSmoother(alpha=0.3),  # 指数平滑
            "ma_predictor": MovingAveragePredictor(),         # 移动平均
        }
```

**指数平滑**（ExponentialSmoother）：
```python
def update(self, actual):
    # 新预测 = 0.3 * 实际值 + 0.7 * 旧预测（近期数据权重更高）
    self._forecast = 0.3 * actual + 0.7 * self._forecast
```

**移动平均趋势检测**（MovingAveragePredictor）：
```python
def predict_trend(self):
    short_avg = average of last 5 values
    medium_avg = average of last 10 values
    long_avg = average of last 20 values

    if short_avg > medium_avg > long_avg: return "up"     # 加速上升
    if short_avg < medium_avg < long_avg: return "down"   # 加速下降
    return "stable"
```

### 4.3 urgency 是怎么算出来的？

```python
def generate_predictive_actions(self) -> list[PredictiveAction]:
    actions = []

    for metric_name in self._predictors:
        prediction = self.predict(metric_name, SHORT)  # 预测未来3步
        if prediction is None:
            continue

        # urgency = 预测风险 × 置信度
        if metric_name == "context_usage":
            if prediction.predicted_value > 0.90:       # 预测3步后上下文会用满
                actions.append(PredictiveAction(
                    urgency=min(0.95, prediction.predicted_value),
                    recommended_action="trigger_compaction",
                ))

        elif metric_name == "error_rate":
            if prediction.trend_direction == "up":       # 错误率在上升
                actions.append(PredictiveAction(
                    urgency=min(0.80, prediction.predicted_value * 1.5),
                    recommended_action="enable_safe_mode",
                ))
    ...
```

| urgency 值 | 含义 | 例子 |
|-----------|------|------|
| 0.0 - 0.3 | 低风险 | 预测指标平稳 |
| 0.3 - 0.7 | 中等风险 | 预测某项指标轻微恶化 |
| 0.7 - 0.9 | 高风险 | 预测上下文即将爆满 |
| 0.9 - 1.0 | 紧急 | 预测错误率将飙升 |

### 4.4 在什么条件下发挥作用？

```
每步 step_start 时（step > 2）：

    PredictiveController.update("context_usage", 0.72)
    PredictiveController.update("error_rate", 0.15)

    actions = PredictiveController.generate_predictive_actions()

    if actions and actions[0].urgency > 0.7:  ← 只在高紧急度时干预
        if action.recommended_action == "trigger_compaction":
            context_cybernetics.try_reactive_recover(...)  ← 预压缩

        elif action.recommended_action == "enable_safe_mode":
            logger.info("建议降低并发、延长超时")

        # 同时触发自愈引擎做交叉验证
        self_healing_engine.detect_and_heal(...)
```

### 4.5 具体场景

**场景：Agent 在执行大规模代码迁移，上下文使用率快速上升**

```
step=3: context_usage = 55%  → ExponentialSmoother: forecast ≈ 55%
step=4: context_usage = 62%  → forecast = 0.3*62 + 0.7*55 = 57%
step=5: context_usage = 70%  → forecast = 0.3*70 + 0.7*57 = 61%

MovingAveragePredictor:
    short_avg(5) = 62%   >   medium_avg(10) = 48%   >   long_avg(20) = 35%
    → trend = "up"（加速上升趋势）

PredictiveController.generate_predictive_actions():
    预测 context_usage 未来3步:
        predicted_value = 61% + 7% * 3 * 1.2 = 86%
        → 预测 3 步后上下文会达到 86%

    action = PredictiveAction(
        urgency=0.86,           ← > 0.7，触发！
        recommended_action="trigger_compaction",
        predicted_issue="上下文使用率将在3步内达到 86%",
        expected_benefit="预压缩可释放 20-30% 的上下文空间",
    )
    → 在上下文真正爆满之前，就触发了压缩
```

**对比有无预测控制**：

```
没有预测控制：
    step=5: usage=70%，正常
    step=6: usage=78%，正常
    step=7: usage=87%，超过阈值 → 触发压缩（但此时已经接近极限）
    step=8: LLM 调用，如果压缩不够 → prompt too long 错误

有预测控制：
    step=5: usage=70%，预测3步后达86%
    → urgency=0.86 > 0.7 → 预压缩！
    → step=6 时上下文已经回到 55%
    → 后续稳定运行 ✅
```

---

## 五、控制论完整闭环：一次完整的 step 中发生了什么

### 5.1 时序图

```
时间 →

agent_loop.py                         控制器们
    │                                     │
    ├─ step_start() ───────────────────→  │
    │                                     ├─ StateObserver.update()
    │                                     │   → ObservedState{
    │                                     │       internal_load: 0.45,
    │                                     │       confidence: 0.72
    │                                     │     }
    │                                     │
    │                                     ├─ PredictiveController.update()
    │                                     │   → urgency 不够高，不干预
    │                                     │
    ├─ model.next() ───────────────────→  │  [LLM 调用中...]
    │                                     │
    ├─ 收到 next_step（工具调用）          │
    ├─ 执行工具...                         │
    │                                     │
    ├─ step_end() ──────────────────────→ │
    │                                     ├─ record_pattern_effectiveness()
    │                                     ├─ StabilityMonitor.record_snapshot()
    │                                     ├─ ProgressController.decide()
    │                                     ├─ SelfHealingEngine.detect_and_heal()
    │                                     │
    │                                     ├─ FeedbackController.observe()
    │                                     │   → ControlSignal{
    │                                     │       force_compaction: True,
    │                                     │       reduce_parallelism: True,
    │                                     │       confidence: 0.85,
    │                                     │       reason: "低稳定性 (0.62)"
    │                                     │     }
    │                                     │
    │                                     ├─ CyberneticSupervisor.report()
    │                                     └─ MemoryPipeline.maintain()
    │                                     │
    ├─ _apply_control_signal() ←─────────┘
    │   → tool_scheduler._force_max_workers = 2
    │   → context_compactor.compact_messages()
    │
    ├─ continue → 下一轮
```

### 5.2 "正常时静默，异常时介入"的设计哲学

大部分时候，控制论系统是这样的：

```
step=1..5: 正常
    StateObserver:      [静默] confidence > 0.4, 但 degradation < 0.4
    PredictiveController: [静默] urgency < 0.7
    FeedbackController: [静默] control_signal.confidence < 0.6
    自愈引擎:            [静默] 没检测到异常

step=6: 出现第一个错误
    StateObserver:      [静默] 仍然在累积证据
    PredictiveController: [静默] 错误率预测上升，但 urgency 还不够
    FeedbackController: [计算中] stability_output 开始上升
    自愈引擎:            [静默]

step=10: 连续多个错误
    StateObserver:      [预警] degradation=0.52，confidence=0.68
    PredictiveController: [触发] urgency=0.82 → trigger_compaction
    FeedbackController: [应用] control_signal → reduce_parallelism
    自愈引擎:           [修复] ERROR_RECOVERY 策略
```

**面试时可以这样总结**：

> "控制论系统就像一个优秀的运维工程师——大部分时间只是静静地看着监控面板，只有指标出现异常趋势时才出手干预。而且它会先用温和的手段（降压、减速），只在温和手段无效时才升级到更强的措施。"

---

## 六、面试要点速查

| 问题 | 答案要点 |
|------|---------|
| **Orchestrator 门面解决了什么问题** | 15个控制器的生命周期管理。agent_loop 只调用 step_start/step_end，不关心内部协调 |
| **PID 三个参数的作用** | P 响应现在，I 消除累积偏差，D 预测趋势防振荡 |
| **为什么是三个 PID？** | 稳定性、性能、效率是三个独立维度，需要独立调节 |
| **Kalman Filter 在这里能做什么** | 从可观测输出（响应时间、成功率）估计隐藏状态（内部负载、系统退化） |
| **为什么 Kalman 不会因一次失败就恐慌** | Kalman Gain 机制：测量噪声大时信任历史估计，测量噪声小时信任新测量 |
| **PredictiveController 和 FeedbackController 的区别** | Predictive 是**事前预防**（还没发生就行动），Feedback 是**事后校正**（发生了再调整） |
| **为什么 control_signal.confidence 在偏差大时反而降低** | 保守策略：系统越不稳定，控制信号越应该谨慎使用 |
| **oscillation_index 怎么算出来的** | 统计误差序列中方向变化的频率。高频振荡 = 系统不稳定 |
| **控制论和普通 if/else 的区别** | if/else 是离散的（到阈值就触发），PID 是连续的（平滑调节力度） |
