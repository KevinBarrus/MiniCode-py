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

**一句话**：这些指标没有"魔法"，它们全部来自**传感器数据 + 数学公式**。每个控制器的代码中都有具体的计算公式

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
1.**初始化**：创建 15 个控制器实例并注入依赖
2.**编排**：在 step_start/step_end 时按正确顺序调用各控制器
3.**汇总**：收集各控制器的输出，打包返回给 agent_loop.py

### 1.2 initialize() 做了什么？

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

**15 个控制器全部在这里创建**。创建后它们之间没有任何通信——Orchestrator 是唯一的协调点

### 1.3 step_start()：调 LLM 之前的"体检"（175-217行）

```python
def step_start(self, context_manager, step, tool_error_count, saw_tool_result):
    # 步骤1：Kalman 状态估计（每步必做）
    measurement = MeasurementVector(
        response_time=step * 2.0,                # 估算值
        success_rate=1.0 - (tool_error_count / max(step, 1)),
        context_length=context_manager.get_stats().total_tokens if context_manager else 0,
        error_count=tool_error_count,
        tool_calls=0,
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

5. FeedbackController.observe(system_state)  ← 核心
   → 双 PID 计算控制信号（ControlSignal）
   → 返回给 agent_loop.py，由 _apply_control_signal() 应用到运行时

6. CyberneticSupervisor.report()
   → 聚合所有控制器快照，生成监控报告

7. AdaptivePIDTuner.tune()（每20步一次）
   → 自动调节 PID 参数（kp, ki, kd）

8. MemoryPipeline.maintain()
   → 后台记忆维护（清理、优化）
```

step_end 的执行顺序是精心设计的——先收集数据 → 再检测异常 → 再计算控制信号 → 最后做长周期优化

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

**Kalman Filter 的作用**：从可测量输出，估计不可测量的内部状态

### 2.2 实现：5 个独立的 Kalman Filter

```python
class StateObserver:
    """State observer for the agent system.

    状态观测器（黑箱方法）:
    ┌────────────────────────────────────────────────────────┐
    │  可测量输出 ─→ 观测器 ─→ 内部状态估计                   │
    │    (响应时间、成功率)       (真实负载、隐藏错误)        │
    │                                    ↓                   │
    │                              状态预测 + 预警           │
    └────────────────────────────────────────────────────────┘

    Features:
    - Multi-dimensional Kalman filtering
    - Black-box system identification
    - State prediction and trend analysis
    - Observability assessment
    """

    def __init__(self):
        
        # 真实负载
        self._internal_load_kf = KalmanFilter(
            process_noise=0.02, measurement_noise=0.15,
            initial_estimate=0.0, initial_uncertainty=0.5,
        )
        
        # 隐藏错误概率
        self._hidden_errors_kf = KalmanFilter(
            process_noise=0.01, measurement_noise=0.2,
            initial_estimate=0.0, initial_uncertainty=0.5,
        )
        
        # 上下文压力
        self._context_pressure_kf = KalmanFilter(
            process_noise=0.03, measurement_noise=0.1,
            initial_estimate=0.0, initial_uncertainty=0.5,
        )
        
        # 技能掌握度
        self._skill_mastery_kf = KalmanFilter(
            process_noise=0.05, measurement_noise=0.25,
            initial_estimate=0.5, initial_uncertainty=0.8,
        )
        
        # 系统退化程度
        self._system_degradation_kf = KalmanFilter(
            process_noise=0.005, measurement_noise=0.1,
            initial_estimate=0.0, initial_uncertainty=0.3,
        )
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

**这就是 Kalman Filter 的价值**：不会因为一两次失败就恐慌，而是**持续观测、渐进更新**，只在有足够证据时才发出预警

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

**为什么不能合并为一个 PID？**

稳定性、性能、效率三个维度经常**互相冲突**：

- 追求高稳定性 → 需要减少并发、限制步数 → **降低性能**
- 追求高性能 → 需要更多并发、更大 token budget → **降低效率**
- 追求高效率 → 用便宜模型、收紧预算 → **降低性能**

如果用一个 PID，无法对不同的矛盾做不同的取舍。三个 PID 各有不同的 kp/ki/kd，意味着对每个维度的**响应力度和速度都不同**：

| PID | kp | ki | kd | setpoint | 特点 |
|-----|----|----|----|-----------|------|
| stability | 1.5 | 0.2 | 0.1 | 0.85 | 最敏感，快速响应不稳定 |
| performance | 1.0 | 0.15 | 0.08 | 0.75 | 中等敏感 |
| efficiency | 0.8 | 0.1 | 0.05 | 0.60 | 最温和，效率波动不需要剧烈反应 |

**kp/ki/kd 的物理含义（开车类比）**：

- **P（比例）**：离目的地还有多远，就踩多大油门。偏了 10%，输出 10% × kp。**响应现在**。
- **I（积分）**：你已经偏了很久了，虽然每次偏差不大，但累积起来该加把劲了。**消除历史欠账**（比如长时间轻微偏离目标，P 项不够触发，I 项会逐渐累积直到触发）。
- **D（微分）**：你正在快速靠近目标，松油门别冲过头。**预测未来，防超调**。

**每个 PID 控制器的核心公式**：

```python
class PIDController:
    def compute(self, setpoint: float, measured: float, dt: float = 1.0) -> float:
        error = setpoint - measured    # 当前偏差

        # 比例项 P：对应当前偏差
        p = self.kp * error

        # 积分项 I：累积偏差（消除稳态误差）
        self._state.integral += error * dt                 # ∫error dt 的离散实现
        self._state.integral = max(-10.0, min(10.0, self._state.integral))  # 抗积分饱和
        i = self.ki * self._state.integral

        # 微分项 D：偏差变化率（预测趋势）
        d = self.kd * (error - self._state.previous_error) / max(dt, 0.001)  # d(error)/dt 的离散实现
        self._state.previous_error = error

        output = p + i + d
        return max(self.output_min, min(self.output_max, output))
```

**⚠️ 常见误区：`previous_error` 存在哪里？**

 `previous_error` 来自 `SystemState` 或 `to_system_state()`？**并非**。它存在 PID 控制器内部的 `_PIDState` 对象里：

```python
@dataclass
class _PIDState:
    integral: float = 0.0
    previous_error: float = 0.0    # ← PID 的"私人账本"，外部看不见
```

`PIDController.compute()` 每次被调用时：
1.用 `self._state.previous_error`（上一次调用留下的）计算 D 项
2.把本次的 `error` 存入 `self._state.previous_error`（留给下一次调用）

**`SystemState` 和 `_PIDState` 是两层完全不同的东西：**
- `SystemState`：ContextCybernetics.to_system_state() 创建的，描述"系统现在什么样"——成功率、响应时间、上下文使用率等。喂给 `FeedbackController.observe()`。
- `_PIDState`：`PIDController` 内部维护的，是 PID 算法自己的"草稿纸"——积分累加到哪了、上次误差是多少。外部完全不可见。

#### 离散积分：`self._state.integral += error * dt` 怎么就是 ∫error dt？

数学上的积分是连续曲线下的面积，计算机用"小矩形累加"来近似：

```
         error
           │
    0.3 ─────────────────────────────────
           │░░░░░░░░░░░░░░░░░░░░░░░░░░░░░░│
    0.2 ──│░░░░░░░░░░░░░░░░░░░░░░░░░░░░░░│──
           │░░░░░░░░░░░░░░░░░░░░░░░░░░░░░░│░░│
    0.1 ──│░░░░░░░░░░░░░░░░░░░░░░░░░░░░░░│░░│──
           │░░░░░░░░░░░░░░░░░░░░░░░░░░░░░░│░░│░░│
      0 ──┴────────┬────────┬────────┬───┴──┴──┴──→ 时间
                  t=1      t=2      t=3

每次调用 compute() → integral += error × dt  (累加一个矩形面积)

t=1: integral += 0.1 × 1.0 = 0.1      ← 第一个矩形
t=2: integral += 0.2 × 1.0 = 0.3      ← 累加第二个矩形
t=3: integral += 0.3 × 1.0 = 0.6      ← 累加第三个矩形
                 ↑
           总面积 = 误差曲线下的面积 = 积分
```

`max(-10.0, min(10.0, ...))` 是**抗积分饱和**（anti-windup）：如果误差长期为正（一直超标），积分项会无限增长，所以限制在 [-10, 10] 范围内。

**为什么需要下限 -10？**

对称的逻辑：如果系统**长期表现太好**（error = setpoint - measured < 0），积分会累积成一个很大的负数。一旦系统突然出问题需要纠正，这个负积分会把 P 项的纠正力抵消掉，导致响应迟缓。下限 -10 防止"历史太好"导致"反应太慢"。

**为什么 clamp 到 10 而不是 1？**

因为 I 项还要乘以 ki（0.1~0.2），实际贡献大约是 10 × 0.2 = 2.0。而 PID 输出在 `compute()` 末尾被 clamp 到 `[-1, 1]`（`output_min=-1.0, output_max=1.0`），所以 integral=10 时 I 项的实际影响力已经被 ki 稀释到约 2.0（超限但可接受）。如果 clamp 到 1，I 项最大贡献只有 0.2，消除稳态误差的能力就太弱了。

#### 离散导数：`(error - previous_error) / dt` 怎么就是 d(error)/dt？

数学上的导数是切线斜率，计算机用"两点连线斜率"来近似：

```
         error
           │
     当前 →│     ●
           │    ╱
           │   ╱ ← 斜率 = (当前值 - 上次值) / 时间间隔
           │  ╱        = Δerror / Δt
     上次 →│ ●          = 离散版的导数
           │
           └────────────────────→ 时间
              t-1       t

d(error)/dt = (error_new - error_old) / dt
            = (0.23 - 0.15) / 1.0
            = 0.08
            → 误差以 0.08/步 的速度在恶化
```

`max(dt, 0.001)` 防止除零。

#### 完整计算示例

```
setpoint=0.85, measured=0.62, dt=1.0:
    error = 0.85 - 0.62 = 0.23

    P = 1.5 × 0.23                        = 0.345   ← "现在差 0.23"
    I = 0.2 × 1.5                         = 0.300   ← "之前累积了 1.5 的偏差"
       (integral 已通过多轮累加到 1.5)
    D = 0.1 × (0.23 - 0.15) / 1.0         = 0.008   ← "误差还在增大，趋势不妙"

    output = 0.345 + 0.300 + 0.008 = 0.653
    → clamped 到 [-1.0, 1.0] → 0.653
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
        if prev_delta * curr_delta < 0:  # 方向变了
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
1.context_cybernetics 存在（enable_work_chain=True）
2.feedback 存在（Orchestrator 初始化成功）
3.control_signal.confidence > 0.6（PID 输出可信）

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

### 3.10 `_apply_control_signal`：控制信号如何真正改变运行时

`FeedbackController.observe()` 返回的 `ControlSignal` 只是一个"建议书"。真正动手的是 `agent_loop.py` 中的 `_apply_control_signal()`。

**控制信号的完整传递链路**：

```
PIDController.compute()
  → FeedbackController.observe() 返回 ControlSignal 对象
    → orch.step_end() 返回 summary["control_signal"]
      → agent_loop.py: step_summary = orch.step_end(...)
        → _apply_control_signal(
            control_signal=step_summary["control_signal"],
            system_state=step_summary["system_state"],
            max_steps=..., tool_scheduler=..., context_compactor=...,
            model_switcher=..., feedback_controller=...
          )
```

**`_apply_control_signal` 的 6 个具体动作**：

```python
def _apply_control_signal(*, control_signal, system_state, max_steps,
                          tool_scheduler, context_compactor, model_switcher,
                          feedback_controller):
    # 门控：信心不足时，什么都不做
    if not control_signal or control_signal.confidence <= 0.6:
        return max_steps    # ← 系统越乱越不调参，保守策略

    # 1.限制最大步数
    if control_signal.limit_max_steps and control_signal.limit_max_steps < max_steps:
        max_steps = control_signal.limit_max_steps
        logger.info("FeedbackController: limiting max_steps %d -> %d", ...)

    # 2.调整 token 预算
    if control_signal.adjust_token_budget != 1.0:
        new_budget = max(1000, int(current_budget * control_signal.adjust_token_budget))
        context_compactor._tool_budget.budget_per_message = new_budget

    # 3.强制降低并发（稳定性差时）
    if control_signal.reduce_parallelism:
        tool_scheduler._force_max_workers = min(current, 2)

    # 4.微调并发数（稳定性好时加大并发）
    if control_signal.adjust_concurrency != 0:
        tool_scheduler._force_max_workers = max(1, 4 + control_signal.adjust_concurrency)

    # 5.升级模型
    if control_signal.increase_model_level:
        model_switcher._pending_upgrade = True

    # 6.降级模型（只打日志，不自动执行）
    if control_signal.decrease_model_level:
        logger.info("FeedbackController: model downgrade recommended ...")

    # 7.持久化记忆（只打日志，不自动执行）
    if control_signal.suggest_memory_persistence:
        logger.info("FeedbackController: persisting working memory")

    return max_steps    # ← 返回可能被修改后的 max_steps
```

**修改了什么？一张表看清楚**：

| 条件 | 动作 | 修改的运行时参数 | 影响范围 |
|------|------|-----------------|---------|
| `confidence <= 0.6` | **直接返回，什么都不做** | — | 全局门控 |
| `limit_max_steps < max_steps` | 缩短最大步数 | `max_steps` | 本轮 agent 循环 |
| `adjust_token_budget ≠ 1.0` | 调整 token 预算 | `context_compactor._tool_budget.budget_per_message` | 上下文压缩策略 |
| `reduce_parallelism = True` | 强制降并发 | `tool_scheduler._force_max_workers → 2` | 工具并发执行 |
| `adjust_concurrency ≠ 0` | 微调并发数 | `tool_scheduler._force_max_workers → max(1, 4+adj)` | 工具并发执行 |
| `increase_model_level = True` | 标记升级模型 | `model_switcher._pending_upgrade = True` | 下次 LLM 调用 |
| `decrease_model_level = True` | **只打日志** | —（不自动降级，需人工确认） | — |
| `suggest_memory_persistence = True` | **只打日志** | — | — |

**关键设计：confidence 门控**

```python
signal.confidence = min(1.0, max(0.3, 1.0 - abs(stability_output) * 0.3))
```

`confidence <= 0.6` 时直接 return，意味着**系统越不稳定，控制信号越被抑制**。stability_output 很大时 confidence 反而低——"系统已经很乱了，别再乱调参数了"。

---

## 四、PredictiveController：预测控制——"危险还没来，但我看见了"

### 4.1 它解决什么问题？

FeedbackController 是**事后调整**（已经出错了才响应），PredictiveController 是**事前预防**（还没出错就提前行动）。

### 4.2 实现：指数平滑 + 移动平均

```python
class PredictiveController:
    """Predictive controller for proactive system management.

    预测控制器（超前控制）:
    ┌─────────────────────────────────────────────────────────────┐
    │  历史数据 ─→ 预测模型 ─→ 未来状态预测 ─→ 预防性控制动作     │
    │                       ↓                                      │
    │                 风险预警 + 优化建议                          │
    └─────────────────────────────────────────────────────────────┘

    Features:
    - Multi-metric prediction
    - Trend detection and extrapolation
    - Predictive action recommendation
    - Confidence-based filtering
    """

    def __init__(self, max_history: int = 100):
        self._predictors: dict[str, dict[str, Any]] = {}
        self._prediction_history: list[PredictionResult] = []
        self._max_history = max_history
        self._max_pred_history = 200

        self._init_metrics()

    def _init_metrics(self) -> None:
        metrics = [
            "response_time",
            "error_rate",
            "context_usage",
            "cpu_usage",
            "memory_usage",
            "throughput",
            "stability_score",
            "performance_score",
        ]
        for metric in metrics:
            self._predictors[metric] = {
                "exp_smoother": ExponentialSmoother(alpha=0.3), # 指数平滑
                "ma_predictor": MovingAveragePredictor(), # 移动平均
                "last_prediction": None,
                "prediction_count": 0,
            }
```

**指数平滑**（ExponentialSmoother）：
```python
def update(self, actual):
    # 新预测 = 0.3 * 实际值 + 0.7 * 旧预测（近期数据权重更高）
    self._forecast = self.alpha * actual + (1-self.alpha) * self._forecast
```

**移动平均趋势检测**（MovingAveragePredictor）：
```python
    def predict_trend(self) -> str:
        if len(self._all_values) < 3:
            return "stable"

        short_avg = self._get_average(5)
        medium_avg = self._get_average(10)
        long_avg = self._get_average(20)

        if short_avg is None or medium_avg is None or long_avg is None:
            return "stable"

        if short_avg > medium_avg > long_avg: # 加速上升
            return "up"
        elif short_avg < medium_avg < long_avg: # 加速下降
            return "down"
        return "stable"
```

**数据从哪里来？ExponentialSmoother.update() 在哪被调用？**

调用链有**两层包装**：

```python
# 第一层：step_start 中调用 PredictiveController.update()
self.predictive.update("error_rate", tool_error_count / max(step, 1))

# 第二层：PredictiveController.update() 内部调用子预测器
def update(self, metric_name: str, value: float) -> None:
    predictor = self._predictors[metric_name]
    predictor["exp_smoother"].update(value)   # ← ExponentialSmoother.update() 在这里
    predictor["ma_predictor"].add_value(value) # ← MovingAveragePredictor 也同时更新
```

**重要事实：实际只有 2 个指标在接收数据**。在 orch 路径的 step_start 中：

```python
self.predictive.update("context_usage", stats.usage_percentage / 100.0)
self.predictive.update("error_rate", tool_error_count / max(step, 1))
```

其余 6 个指标（response_time、cpu_usage、memory_usage、throughput、stability_score、performance_score）虽然初始化了预测器，但从未被喂数据，预测器永远处于初始状态，不会产生有效预测。

**`generate_predictive_actions()` 如何遍历所有指标**：

 `_assess_prediction` 并不只评估一个指标。实际上它在循环里被调用了 8 次：

```python
def generate_predictive_actions(self) -> list[PredictiveAction]:
    actions = []
    for metric_name, predictor in self._predictors.items():  # ← 遍历全部 8 个指标
        if predictor["last_prediction"] is None:
            continue                    # 还没预测过的跳过
        prediction = predictor["last_prediction"]
        if prediction.confidence < 0.5:
            continue                    # 预测不够可信的跳过
        action = self._assess_prediction(metric_name, prediction)  # 每个指标评估一次
        if action:
            actions.append(action)

    actions.sort(key=lambda a: a.urgency, reverse=True)  # 按紧急度排序
    return actions                                      # 返回 urgency 最高的在前
```

所以 `_assess_prediction` 会被调用最多 8 次，每次的 `metric_name` 不同，内部的 `thresholds` 字典根据 metric_name 查表获取对应的危险阈值。

### 4.3 urgency 是怎么算出来的？

实际的 `generate_predictive_actions()` 非常简洁——它只做两件事：过滤低置信度预测，然后把每个预测委托给 `_assess_prediction()`：

```python
def generate_predictive_actions(self) -> list[PredictiveAction]:
    actions = []

    for metric_name, predictor in self._predictors.items():
        if predictor["last_prediction"] is None:
            continue

        prediction = predictor["last_prediction"]
        if prediction.confidence < 0.5:    # ← 预测不够可信，跳过
            continue

        action = self._assess_prediction(metric_name, prediction)  # ← 核心在这
        if action:
            actions.append(action)

    actions.sort(key=lambda a: a.urgency, reverse=True)  # 按紧急度降序
    return actions
```

真正计算 urgency 的是 `_assess_prediction()`：

```python
def _assess_prediction(self, metric_name: str, prediction: PredictionResult):
    # 每个指标定义了自己的"危险阈值"
    thresholds = {
        "response_time":     {"high": 45.0, "low": 5.0},
        "error_rate":        {"high": 3.0,  "low": 0.0},
        "context_usage":     {"high": 0.85, "low": 0.0},
        "stability_score":   {"high": 1.0,  "low": 0.5},
        "performance_score": {"high": 1.0,  "low": 0.5},
        ...
    }

    thresh = thresholds[metric_name]

    # ── 条件1：向上突破的指标（越高越危险）──
    if prediction.predicted_value > thresh["high"] and prediction.trend_direction == "up":
        # urgency = 超出阈值的比例
        urgency = min(1.0, (prediction.predicted_value - thresh["high"]) / thresh["high"])

        # 例如 context_usage: predicted=0.95, threshold=0.85
        #   urgency = (0.95 - 0.85) / 0.85 = 0.118
        # 例如 error_rate: predicted=6.0, threshold=3.0
        #   urgency = (6.0 - 3.0) / 3.0 = 1.0  ← 封顶

        return PredictiveAction(
            urgency=urgency,
            recommended_action="trigger_compaction",  # 根据指标名查表
            deadline_steps=2,                          # 还有多少步可以缓冲
        )

    # ── 条件2：向下突破的指标（越低越危险，如稳定性）──
    if prediction.predicted_value < thresh["low"] and prediction.trend_direction == "down":
        urgency = min(1.0, (thresh["low"] - prediction.predicted_value) / thresh["low"])

        # 例如 stability_score: predicted=0.3, threshold=0.5
        #   urgency = (0.5 - 0.3) / 0.5 = 0.4

        return PredictiveAction(
            urgency=urgency,
            recommended_action="启动干预机制",
            deadline_steps=3,
        )

    return None  # 没有超过阈值，或者趋势方向不对
```

**urgency 公式的本质**：

```
urgency = |预测值 - 阈值| / 阈值
        = 超出阈值的百分比
```

| 指标 | 阈值 | 预测值 = | urgency = | 含义 |
|------|------|----------|-----------|------|
| error_rate | high=3.0 | 6.0 | (6-3)/3 = **1.0** | 预测错误率是阈值的2倍，紧急 |
| error_rate | high=3.0 | 4.5 | (4.5-3)/3 = **0.5** | 预测超标50%，中等 |
| context_usage | high=0.85 | 0.95 | (0.95-0.85)/0.85 = **0.12** | 预测超标12%，低紧急 |
| stability_score | low=0.5 | 0.3 | (0.5-0.3)/0.5 = **0.4** | 预测跌破阈值40%，中等 |

**关键发现**：context_usage 的 urgency 在满负载时也只有 0.18，永远达不到 0.7 的触发门槛。这意味着 **PredictiveController 对上下文使用率的"预压缩"实际上很难被触发**。真正管上下文压缩的是 FeedbackController 的 PID 和 ContextCybernetics——PredictiveController 只是辅助。

**触发条件必须同时满足两个**：
1. 预测值超过阈值（`predicted_value > threshold_high` 或 `< threshold_low`）
2. 趋势方向一致（`trend_direction == "up"` 或 `"down"`）

只超阈值但趋势在回落？不触发。趋势在上行但还没到阈值？也不触发。

### 4.4 在什么条件下发挥作用？

**⚠️ 重要：PredictiveController 有两条执行路径，行为不同。**

**路径一：orch 路径（正常运行时，cybernetic_orchestrator.py line 206-217）**

```python
# orch.step_start() 中：
if self.predictive:
    if context_manager:
        self.predictive.update("context_usage", stats.usage_percentage / 100.0)
    self.predictive.update("error_rate", tool_error_count / max(step, 1))
    if step > 2:
        actions = self.predictive.generate_predictive_actions()
        if actions and actions[0].urgency > 0.7:
            action = actions[0]
            # 只对 trigger_compaction 做了处理，且只打日志
            if action.recommended_action == "trigger_compaction" and self.context_cybernetics:
                logger.info("Predictive: trigger_compaction urgency=%.2f", action.urgency)
            # enable_safe_mode、reduce_concurrency 等：不处理
```

**orch 路径下 PredictiveController 只打日志，不执行任何动作。**

**路径二：fallback 路径（orch=None 时，agent_loop.py line 897-942）**

```python
# agent_loop.py run_agent_turn() 中：
if predictive_controller:
    # ... 同样的 update 和 generate_predictive_actions ...
    if actions and actions[0].urgency > 0.7:
        action = actions[0]

        # 通过 dispatch 表真正执行动作
        dispatch = {
            "trigger_compaction": lambda: (
                context_cybernetics.try_reactive_recover(current_messages, "predictive")
                if context_cybernetics else None
            ),
            "enable_safe_mode": lambda: logger.info("Predictive: safe_mode recommended"),
            "reduce_concurrency": lambda: logger.info("Predictive: reduce_concurrency recommended"),
        }
        handler = dispatch.get(action.recommended_action)
        if handler:
            handler()    # ← 真正执行！

        # 同时触发自愈引擎做交叉验证
        if self_healing_engine:
            self_healing_engine.detect_and_heal({
                "context_usage": ...,
                "error_rate": ...,
            })
```

**fallback 路径下 trigger_compaction 真的调用了 try_reactive_recover，且触发了自愈交叉验证。**

**设计意图**：PredictiveController 的职责是"趋势监控 + 数据积累"，真正的控制动作留给 step_end 中的 FeedbackController PID + SelfHealingEngine。这是控制论中"预测-反馈分离"的原则——预测器不应直接控制系统，否则噪声会导致误触发。

**但 orch 路径的 gap 是真实存在的**：连 `trigger_compaction` 都只打日志，这是一个可以强化的点。修复方案很简单——把 fallback 路径的 dispatch 逻辑移植到 orch.step_start 即可。

### 4.5 具体场景

**场景：Agent 在调试一个顽固 bug，错误率持续上升**

```
step=3: error_rate = 0.50  → ExponentialSmoother: forecast ≈ 0.50
step=4: error_rate = 1.20  → forecast = 0.3*1.2 + 0.7*0.5 = 0.71
step=5: error_rate = 2.10  → forecast = 0.3*2.1 + 0.7*0.71 = 1.13
step=6: error_rate = 3.20  → forecast = 0.3*3.2 + 0.7*1.13 = 1.75

MovingAveragePredictor:
    short_avg(5) = 1.60  >  medium_avg(10) = 0.80  >  long_avg(20) = 0.35
    → trend = "up"（加速上升趋势）

predict() 计算 future 3 步预测值:
    exp_prediction = 1.75（指数平滑的 forecast）
    ma_prediction = 3.2 + (3.2-0.5)/4 * 3 * 1.2 = 5.63（移动平均外推）
    predicted_value = 1.75*0.4 + 5.63*0.6 = 4.08
    → 预测 3 步后错误率会达到 4.08

_assess_prediction("error_rate", prediction):
    threshold_high = 3.0
    predicted_value = 4.08 > 3.0 ✅  AND  trend = "up" ✅
    
    urgency = (4.08 - 3.0) / 3.0 = 0.36
    → urgency = 0.36，未达到 0.7 的触发门槛，不干预
```

**如果错误继续恶化**：

```
step=10: 错误率飙升，预测值达到 6.0

_assess_prediction("error_rate", prediction):
    urgency = (6.0 - 3.0) / 3.0 = 1.0  ← 封顶！
    → urgency = 1.0 > 0.7 ✅ 触发！

    action = PredictiveAction(
        urgency=1.0,
        recommended_action="enable_safe_mode",
        predicted_issue="error_rate 预测值 6.00 将超过阈值 3.00",
        expected_benefit="防止错误扩散",
        deadline_steps=1,  ← 只有 1 步缓冲时间
    )
    → 启用安全模式：降低并发、延长超时
```

**关键理解**：PredictiveController 的错误率预测，在 `predicted_value=4.08` 时 urgency 只有 0.36，不会触发——它在等待更多证据。直到预测值达到 `3.0 * 1.7 = 5.1` 时 urgency 才会超过 0.7。

**对比 context_usage：为什么 context_usage 几乎不会被 PredictiveController 触发？**

```
即使上下文满了（predicted_value = 1.0）:
    urgency = (1.0 - 0.85) / 0.85 = 0.176  ← 永远达不到 0.7

结论：PredictiveController 不管上下文压缩这件事。
真正管理上下文的是 ContextCybernetics（PID控制管线）和 FeedbackController。
PredictiveController 的强项是检测 error_rate 和 stability/performance 的恶化趋势。
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

控制论系统就像一个优秀的运维工程师——大部分时间只是静静地看着监控面板，只有指标出现异常趋势时才出手干预。而且它会先用温和的手段（降压、减速），只在温和手段无效时才升级到更强的措施。

### 5.3 完整端到端示例：从检测到修改运行时

以下是一次 step 的完整控制论闭环。场景：Agent 在调一个顽固 bug，已经跑了 15 步，错误频发。

```
step_start ──────────────────────────────────────────────────
│
│ StateObserver(Kalman): 从 response_time/error_count
│   估计 hidden_errors=0.7 → 只打 warning, 不阻止执行
│ PredictiveController: error_rate 预测值 5.2 > 3.0
│   urgency=0.73 > 0.7 → orch 路径只打日志, 不执行
│
↓
LLM 调用 + 工具执行 ─────────────────────────────────────────
│
│ LLM 调用读写工具，又有 2 个工具返回错误
│ → tool_error_count = 5 (累计 15 步中 5 次失败)
│ → 工具结果管线: ErrorClassifier 分类 → NudgeGenerator
│   生成纠错提示注入消息 → ReadDedup 去重
│
↓
step_end (控制论闭环) ───────────────────────────────────────
│
│ ① StabilityMonitor.record_snapshot():
│    记录 error_rate=0.33, context_usage=0.82
│
│ ② ProgressController.decide():
│    跑了 15 步，失败 5 次 → 还没卡住，不触发 STOP
│
│ ③ SelfHealingEngine.detect_and_heal():
│    error_rate=0.33 < 3.0 → 不触发 ERROR_SPIKE
│    context_usage=0.82 < 0.85 → 不触发 CONTEXT_OVERFLOW
│    → 本轮不自愈
│
│ ④ FeedbackController.observe(system_state): ★核心★
│    system_state ← context_cybernetics.to_system_state()
│    ├─ stability_score  = 0.58  (远低于目标 0.85)
│    └─ performance_score = 0.41 (远低于目标 0.75)
│
│    stability_pid.compute(0.85, 0.58):
│      error=0.27 → P=0.405 + I=0.26(累积) + D=0.01
│      → stability_output = 0.675 > 0.3 触发!
│
│    performance_pid.compute(0.75, 0.41):
│      error=0.34 → P=0.34 + I=0.31 + D=0.02
│      → performance_output = 0.67 > 0.3 触发!
│      → avg_response_time=45s > 30s → increase_model_level
│
│    → ControlSignal {
│        reduce_parallelism: True
│        increase_model_level: True
│        confidence: 1.0 - |0.675|*0.3 = 0.798
│      }
│
│ ⑤ Supervisor.report() + AdaptivePIDTuner(每20步)
│ ⑥ MemoryPipeline.maintain()
│
↓
_apply_control_signal ───────────────────────────────────────
│
│ confidence=0.798 > 0.6 ✅ → 应用信号
│
│ ① reduce_parallelism=True:
│    → tool_scheduler._force_max_workers = 2
│    工具从并行 4 个变成最多并行 2 个
│
│ ② increase_model_level=True:
│    → model_switcher._pending_upgrade = True
│    下次 LLM 调用可能切换到更强模型
│
↓
下一步 (step=16) ───────────────────────────────────────────
│
│ 运行时参数已被修改:
│ - 工具最大并发: 4 → 2
│ - 下次 LLM 调用: 可能用更强模型
│ - 错误率有望下降，稳定性有望回升
│
│ 如果改善: PID 的积分项逐渐减小，控制信号减弱
│ 如果继续恶化: PID 积分项继续累积，触发 force_compaction
│                + SelfHealing 也可能介入
```

**这条链路的本质**：工具执行结果 → 汇聚成系统状态指标 → PID 计算偏差 → 生成控制信号 → 修改运行时参数 → 影响下一步行为

**关键桥梁**：`context_cybernetics.to_system_state()` 是连接"工具执行结果"和"PID 输入"的环节。它把原始指标（token 数、压缩次数、上下文使用率等）转换成 FeedbackController 能理解的 SystemState。这部分在控制论 Part 2 展开。

---

## 六、要点

| 问题 | 答案要点 |
|------|---------|
| **Orchestrator 门面解决了什么问题** | 15个控制器的生命周期管理。agent_loop 只调用 step_start/step_end，不关心内部协调 |
| **kp/ki/kd 分别是什么** | P（比例）响应现在的偏差，踩多大油门看差多远；I（积分）消除历史累积偏差，长时间微偏也会触发；D（微分）预测趋势防超调，快到了就松油门 |
| **为什么是三个 PID？** | 稳定性、性能、效率三者互相冲突（稳→降并发→损性能），需要独立取舍 |
| **`previous_error` 存在哪** | 在 `_PIDState` 里，是 PID 的内部草稿纸，不在 `SystemState` 中。`SystemState` 描述系统状态，`_PIDState` 是 PID 算法的记忆 |
| **I 项为什么有下限 -10** | 防止"历史表现太好"导致积分累积成很大的负数，系统一旦出问题时 P 项的纠正力被负积分抵消 |
| **confidence 门控是什么意思** | `_apply_control_signal` 在 `confidence <= 0.6` 时直接返回，系统越不稳定越不调参 |
| **ControlSignal 怎么改变运行时** | 6 个动作：限制 max_steps、调 token budget、降/升并发、标记模型升级。降级模型和持久化记忆只打日志不自动执行 |
| **Kalman Filter 在这里能做什么** | 从可观测输出（响应时间、成功率）估计隐藏状态（内部负载、系统退化） |
| **为什么 Kalman 不会因一次失败就恐慌** | Kalman Gain 机制：测量噪声大时信任历史估计，测量噪声小时信任新测量 |
| **PredictiveController 和 FeedbackController 的区别** | Predictive 是**事前预防**（还没发生就行动），Feedback 是**事后校正**（发生了再调整） |
| **为什么 control_signal.confidence 在偏差大时反而降低** | 保守策略：系统越不稳定，控制信号越应该谨慎使用 |
| **oscillation_index 怎么算出来的** | 统计误差序列中方向变化的频率。高频振荡 = 系统不稳定 |
| **控制论和普通 if/else 的区别** | if/else 是离散的（到阈值就触发），PID 是连续的（平滑调节力度） |
| **自愈引擎是怎么判断和修复的** | 检测：简单阈值（context>0.85, error>3.0, oscillation>0.6...）→ 分类：7种FaultType → 策略：按empirical_success_rate排序 → 执行：调用具体修复函数 → 记录成功/失败 |
| **Kalman.uncertainty 没返回，怎么发挥作用** | 两条路径：①决定下一轮的 Kalman Gain（内部记忆）②通过 get_confidence() 影响 ObservedState.confidence→门控决策 |
| **为什么叫"双 PID"而不是三 PID** | "双"指两层架构：Layer1 ContextCybernetics PID（管上下文压缩）+ Layer2 FeedbackController 3个PID（管行为调优），不是指 FeedbackController 内部数量 |
| **kp/ki/kd 和 P/I/D 的关系** | P/I/D 是"项"，kp/ki/kd 是各项目的"增益系数"（放大倍数）。PID输出 = kp×error + ki×integral + kd×derivative |
| **什么是稳态误差** | 系统稳定后实际值与目标值之间持续存在的微小差距。P 项消除不了（error 小时 P 也小），I 项能消除（只要还有误差就持续累积） |

---

## 七、补充：常见误区与深入理解

### 7.1 PredictiveController 的两条路径（重要）

**PredictiveController 在 orch 路径和 fallback 路径下行为不同：**

| action_type | orch.step_start | fallback 路径（agent_loop.py） |
|---|---|---|
| `trigger_compaction` | `logger.info(...)` **只打日志** | `dispatch.get()` → 真的调了 `context_cybernetics.try_reactive_recover()` |
| `enable_safe_mode` | **不处理** | `logger.info(...)` 只打日志 |
| `reduce_concurrency` | **不处理** | `logger.info(...)` 只打日志 |
| 自愈交叉验证 | **不触发** | `self_healing_engine.detect_and_heal(...)` |

**这不是设计失误，而是"预测-反馈分离"原则的体现**：PredictiveController 的职责是趋势监控和数据积累（step_start），真正的控制动作留给 FeedbackController PID + SelfHealingEngine（step_end）。预测器不直接控制系统，避免噪声误触发。

**但 orch 路径确实存在 gap**：正常运行时走 orch 路径，`trigger_compaction` 只打日志不执行。修复方案：把 fallback 路径的 dispatch 逻辑移植到 orch.step_start 即可，这是一行代码的改动，不是架构问题。

### 7.2 KalmanFilter 的两层调用链（常见误解）

容易只看到 `StateObserver.update(MeasurementVector)`，没追踪到下一层。实际上有**两层 update**：

```
第一层：StateObserver.update(measurement: MeasurementVector) -> ObservedState
  ├── 把 7 个可测量字段映射成 5 个标量
  └── 调用 5 个 _estimate_*() 方法，每个内部调用 KalmanFilter.update(float)

第二层：KalmanFilter.update(measurement: float) -> float
  └── 执行 4 步卡尔曼滤波公式（预测不确定性 → 卡尔曼增益 → 更新估计 → 更新不确定性）
```

**"measurement 从哪里来"的答案**：来自 `_estimate_*` 方法的映射逻辑。例如：

```python
# state_observer.py line 267-274
def _estimate_internal_load(self, measurement: MeasurementVector) -> float:
    latency_ratio = measurement.response_time / max(self._response_time_baseline, 0.001)
    latency_score = min(1.0, latency_ratio / 3.0)       # 响应慢 → 负载可能高
    tool_intensity = min(1.0, measurement.tool_calls / 10.0)  # 工具调用多 → 负载高
    estimated_load = latency_score * 0.6 + tool_intensity * 0.4  # 加权得到标量
    return self._internal_load_kf.update(estimated_load)  # ← 喂给 Kalman
```

MeasurementVector 是"原始数据"，`_estimate_*` 方法是"翻译器"，KalmanFilter 是"滤波器"。

### 7.3 prediction_uncertainty 越大 → kalman_gain 越大？这很合理

这是 Kalman Filter 最反直觉的一点。公式：

```
kalman_gain = prediction_uncertainty / (prediction_uncertainty + measurement_noise)
```

- prediction_uncertainty → ∞：kalman_gain → 1（**完全信任测量值**）
- prediction_uncertainty → 0：kalman_gain → 0（**完全信任预测值**）

**直觉**：我对自己的预测越不确定，就越应该多听听新的测量数据。反之，如果我的预测非常准，新来的测量值很可能只是噪声。

这是**贝叶斯更新**的核心思想：先验（prediction）越不确定，后验（estimate）越依赖新证据（measurement）。

### 7.4 ObservedState 是 5 个状态，不是 4 个

```python
@dataclass
class ObservedState:
    internal_load: float = 0.0       # 内部负载 - 从 response_time 估计
    hidden_errors: float = 0.0       # 隐藏错误概率 - 从 success_rate 估计
    context_pressure: float = 0.0    # 上下文压力 - 从 context_length 估计
    skill_mastery: float = 0.0       # 技能掌握度 - 从 success_rate + retry_count 估计
    system_degradation: float = 0.0  # 系统退化 - 综合以上所有指标
```

每个都有独立的 KalmanFilter 实例，各自有不同的 process_noise 和 measurement_noise 参数。

### 7.5 自愈引擎速查

自愈引擎的逻辑是**检测→分类→策略→执行→记录**五步：

| 故障类型 | 检测条件 | 修复策略 | 实际做什么 |
|---------|---------|---------|-----------|
| `CONTEXT_OVERFLOW` | context_usage > 0.85 | cybernetic_compaction | 调 `context_cybernetics.try_reactive_recover()` |
| `ERROR_SPIKE` | error_rate > 3.0 | enable_safe_mode | 串行化工具执行 |
| `OSCILLATION` | oscillation_index > 0.6 | dampen_control_signals | kd×2, kp×0.5, ki→0.01 |
| `RESOURCE_EXHAUSTION` | cpu > 0.9 或 memory > 0.9 | reduce_concurrency | 降低并发数 |
| `PERFORMANCE_DEGRADATION` | latency > 45s 或 throughput < 0.5 | upgrade_model | token budget ×1.5 |
| `DEADLOCK` | 外部检测 | force_terminate_stuck_tools | 终止卡住的工具 |
| `MEMORY_LEAK` | 外部检测 | trigger_memory_cleanup | 强制压缩上下文 |

这不是什么高深的机器学习，就是工程上对已知故障模式的快速响应。它的"智能"体现在：
1.按 `empirical_success_rate` 排序策略（历史成功率高的优先）
2.记录了所有 healing 的成功/失败，形成经验积累
3.对振荡的自愈直接修改 PID 参数（kd×2, kp×0.5），是真正的"控制论自愈"

### 7.6 KalmanFilter.uncertainty 没有被返回，那它怎么发挥作用？

`KalmanFilter.update()` 只返回 `self.estimate`，但 `self.uncertainty` 通过两条路径影响系统：

**路径一：影响下一次 update() 的 Kalman Gain（内部记忆）**

```python
def update(self, measurement: float) -> float:
    # 读取上一轮的 uncertainty
    prediction_uncertainty = self.uncertainty + self.process_noise  # ← 读
    kalman_gain = prediction_uncertainty / (prediction_uncertainty + measurement_noise)
    self.estimate = self.estimate + kalman_gain * (measurement - self.estimate)
    # 更新 uncertainty，留给下一轮
    self.uncertainty = (1.0 - kalman_gain) * prediction_uncertainty  # ← 写
    return self.estimate  # 只返回 estimate，uncertainty 留在内部
```

连续多轮的效果：随着测量数据增多，`uncertainty` 逐渐减小 → Kalman Gain 变小 → 滤波器越来越信任自己的估计，越来越不依赖新测量。这是 Kalman 的"学习"过程。

**路径二：get_confidence() → ObservedState.confidence → 门控决策**

```python
# KalmanFilter
def get_confidence(self) -> float:
    return 1.0 - self.uncertainty

# StateObserver.update()
overall_confidence = (
    self._internal_load_kf.get_confidence() * 0.25    # 1.0 - uncertainty
    + self._hidden_errors_kf.get_confidence() * 0.25
    + self._context_pressure_kf.get_confidence() * 0.2
    + self._skill_mastery_kf.get_confidence() * 0.15
    + self._system_degradation_kf.get_confidence() * 0.15
)

state = ObservedState(..., confidence=overall_confidence)
```

`overall_confidence` 决定了 StateObserver 的估计是否被采信——`confidence > 0.4` 才触发退化警告。

### 7.7 为什么叫"双 PID"？FeedbackController 不是有 3 个 PID 吗？

**"双"不是指 FeedbackController 内部的数量，而是指两层 PID 架构。**

```python
# context_cybernetics.py to_system_state() 注释:
"""Forms the upper layer of a dual-PID control architecture:
  Layer 1 (this module): ContextPIDController → ContextCompactor
  Layer 2 (feedback_controller): FeedbackController → agent behavior tuning
"""
```

```
┌──────────────────────────────────────────────┐
│  Layer 2 (外层): FeedbackController          │
│  3 个 PID: stability / performance / efficiency │
│  输入: SystemState                           │
│  输出: ControlSignal → 改并发、改模型、改预算  │
│                   ↑                          │
│              SystemState                     │
│                   ↑                          │
│  Layer 1 (内层): ContextCybernetics          │
│  1 个 PID: ContextPIDController              │
│  输入: context_usage, token 变化率            │
│  输出: 直接控制 ContextCompactor 压缩上下文    │
└──────────────────────────────────────────────┘
```

- **内层**（ContextCybernetics 的 PID）：只管上下文压缩，响应快，直接操作 compactor
- **外层**（FeedbackController 的 3 个 PID）：管整个 Agent 的行为调优，通过 ControlSignal 间接影响运行时

所以 "dual-PID" = 双层 PID，每层可以有一个或多个 PID 实例。

### 7.8 kp、ki、kd 是什么？和 P、I、D 的关系

P/I/D 是**项**（term），kp/ki/kd 是**系数**（gain/增益）。

```
PID 输出 = kp × error   +   ki × integral   +   kd × derivative
           ↑                  ↑                   ↑
        比例增益            积分增益            微分增益
         (系数)             (系数)             (系数)
```

| 参数 | 含义 | kp=1.5（stability PID）的意思 |
|------|------|------|
| **kp** | 偏差的放大倍数 | 偏差 0.1 → P 项贡献 0.15。kp 越大纠正越猛，但越容易振荡 |
| **ki** | 累积误差的放大倍数 | integral=5 → I 项贡献 1.0。ki 越大，历史欠账被追得越快 |
| **kd** | 变化率的放大倍数 | 误差变化率 0.2/s → D 项贡献 0.02。kd 越大，越能抑制超调 |

**为什么三个 PID 的 kp 不同？** 因为不同维度的重要性不同：

```
stability:    kp=1.5, ki=0.2, kd=0.1  ← 最敏感，稳定性出问题要最快响应
performance:  kp=1.0, ki=0.15, kd=0.08 ← 中等
efficiency:   kp=0.8, ki=0.1, kd=0.05  ← 最温和，效率波动不值得剧烈反应
```

### 7.9 什么是稳态误差？为什么 I 项能消除它？

**定义**：系统稳定后，实际值和目标值之间持续存在的微小差距。

```
目标 (setpoint = 0.85)
  │     ┌─────────────────────────
0.85 ─────────────────-----------─────
  │         ↗ 系统趋近目标
  │       ↗
  │     ↗
0.82 ───────────────────────────────── ← 最终停在 0.82，差 0.03 = 稳态误差
  │
  └──────────────────────────────────→ 时间
```

**为什么 P 项消除不了？** P 项 = kp × error。当 error 越来越小（比如 0.03），P 项也越来越小（0.03 × 1.5 = 0.045）。小到某个程度时，不足以克服系统的"摩擦力"（惯性、噪声），系统就停住了。

**为什么 I 项能消除？** I 项 = ki × ∫error dt。只要 error > 0（系统还在目标下方），积分就**一直累加**。哪怕 error 只有 0.03：

```
step 1: integral = 0.03,  I = 0.2 × 0.03 = 0.006
step 2: integral = 0.06,  I = 0.2 × 0.06 = 0.012
step 5: integral = 0.15,  I = 0.2 × 0.15 = 0.03  ← 追上 P 项的量级了
step 10: integral = 0.30, I = 0.2 × 0.30 = 0.06  ← 足够推动系统到达目标
```

**一旦系统到达目标**（error=0），积分停止增长，I 项维持在当前值，恰好抵消"摩擦力"。

**一句话**：P 项是"差多少纠正多少"，误差小了它就弱了；I 项是"差多久就累积多久"，只要还在差着，它就越长越大，直到把偏差彻底消除。
