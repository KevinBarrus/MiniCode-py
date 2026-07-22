# MiniCode 模拟面试全集

## 说明

本文档包含两轮模拟面试：
- **Round 1-4**（第一部分）：工程控制论深度拷打——面试官看源码，专找代码矛盾追问
- **Round 5**（第二部分）：通用项目面试——面试官不看源码，从项目整体角度提问

---

# 第一部分：工程控制论深度拷打

---

## Round 1：整体架构理解

### Q1：画出 15 个控制器的数据流图。如果删掉 StateObserver 会怎样？删掉 FeedbackController 呢？

**核心答案**：

15 个控制器的实际数据流只有一条主线，分两个阶段执行：

```
━━━ step_start ━━━
  StateObserver       ← MeasurementVector → ObservedState → 仅日志告警（不影响其他控制器）
  PredictiveController ← context_usage, error_rate → PredictiveActions → 仅日志告警

━━━ step_end ━━━ (真正起作用的链路)
  ① StabilityMonitor.record_snapshot()           → 记录历史快照，喂给 ContextCybernetics
  ② ProgressController.decide()                  → 判断卡住 → STOP/CONTINUE
  ③ SelfHealingEngine.detect_and_heal()          → 检测故障 → 执行修复动作
  ④ ContextCybernetics.to_system_state()         → SystemState
        │
        └──→ FeedbackController.observe()         → ControlSignal
               │
               └──→ _apply_control_signal()       → 改运行时参数（max_steps/token_budget/concurrency/model）
  ⑤ CyberneticSupervisor.report()                → 聚合快照 → 持久化 JSON
  ⑥ AdaptivePIDTuner.tune() (每20步)             → 调 ContextCybernetics.pid 的 kp/ki/kd
  ⑦ MemoryPipeline.maintain()                    → 后台记忆维护
```

**关键桥接点**（`cybernetic_orchestrator.py:286-289`）：

```python
if self.context_cybernetics and self.feedback:
    system_state = self.context_cybernetics.to_system_state()
    control_signal = self.feedback.observe(system_state)  # 唯一的关键数据流
```

**如果删掉 StateObserver**：
- Kalman Filter 估计值（internal_load、hidden_errors、system_degradation）消失
- 只有日志告警受影响，不影响任何其他控制器的输入
- **结论**：StateObserver 是独立的观测器，不参与控制闭环

**如果删掉 FeedbackController**：
- ControlSignal 生成消失 → _apply_control_signal() 收不到信号
- 运行时不再自动调整并发数、token 预算、最大步数
- SelfHealingEngine 仍可独立工作（它只依赖自己的故障检测）
- ContextCybernetics 仍可独立运行（它有自己的 PID）
- **结论**：FeedbackController 是核心执行器，删掉它 → 整个外层控制闭环断裂

15 个控制器并不是平等的。真正参与闭环控制的只有一条线：ContextCybernetics 生产 SystemState → FeedbackController 消费并产出 ControlSignal → 运行时被修改。其余控制器要么是纯观测（StateObserver/StabilityMonitor/Supervisor），要么是旁路保护（SelfHealingEngine/ProgressController），要么是周期性维护（AdaptivePIDTuner）。这个架构的核心就是双层 PID，其他 13 个控制器都是支撑。

**源码出处**：
- 完整数据流：`cybernetic_orchestrator.py` 的 `step_start()` (176-217) 和 `step_end()` (219-350)
- 桥接代码：`cybernetic_orchestrator.py:286-289`
- `_apply_control_signal()`：`agent_loop.py:363-422`

---

### Q2：两个独立的振荡检测器会造成什么问题？

**问题描述**：

- **检测器 A**：`CyberneticFeedbackLoop.detect_oscillation()`（`context_cybernetics.py:578-586`）
  - 监测：压缩后 usage 的方向变化次数
  - 输出：True/False（二进制）
  - 用途：通过 `to_system_state()` 写入 `SystemState.oscillation_index`

- **检测器 B**：`FeedbackController._compute_oscillation()`（`feedback_controller.py:299-315`）
  - 监测：稳定性误差信号的方向变化次数
  - 输出：0.0-1.0（连续值）
  - 用途：设置 `ControlSignal.oscillation_index`

**核心问题：SystemState 的 oscillation_index 是死数据**

```python
# observe() 方法中，从未读取 state.oscillation_index！
def observe(self, state: SystemState) -> ControlSignal:
    signal = ControlSignal()
    # ...
    error = 1.0 - state.stability_score()
    self._error_history.append(error)
    # 用的是 FeedbackController 自己的误差历史，不是 state.oscillation_index！
    signal.oscillation_index = self._compute_oscillation()
```

ContextCybernetics 辛辛苦苦算了个振荡检测，传给 SystemState，然后 FeedbackController 完全无视它，用自己的版本重新算了一遍。

**实际影响**：

这不是 bug（不会崩溃），但暴露了三个问题：

1.**接口设计不闭环**：`to_system_state()` 是按"应该有什么"写的，不是按"下游需要什么"写的
2.**语义不同**：A 检测的是"压缩结果的振荡"，B 检测的是"稳定性误差的振荡"——两个都叫 oscillation_index 但含义不同
3.**重复计算**：两个模块都在做方向变化计数，但监测的是不同的信号

**面试话术**：

> "我承认这两个振荡检测器没有整合好。ContextCybernetics 的反馈环检测的是上下文压缩是否振荡——compaction 之后 usage 降下去又弹回来。FeedbackController 检测的是稳定性误差的振荡——stability_score 反复波动。这两个信号本质上都反映'系统不稳定'，但检测的是不同层的振荡。理想的设计应该是：ContextCybernetics 把原始信号（方向变化次数）传给 FeedbackController，由 FeedbackController 统一计算一个 oscillation_index，而不是两边各算各的。这是一个我后续会重构的点。"

**源码出处**：
- `CyberneticFeedbackLoop.detect_oscillation()`：`context_cybernetics.py:578-586`
- `FeedbackController._compute_oscillation()`：`feedback_controller.py:299-315`
- `to_system_state()` 中设置 `oscillation_index`：`context_cybernetics.py:858`
- `observe()` 中设置 `signal.oscillation_index`：`feedback_controller.py:268`

---

## Round 2：PID 控制器深入

### Q3：你有三个地方在用 PID——FeedbackController（3个PID）、ContextPIDController（1个PID）、CostControlLoop（1个PID）。为什么是 5 个独立的 PID 而不是 1 个 MIMO 控制器？

**核心答案**：

**技术上**：5 个 SISO（单输入单输出）PID 比 1 个 MIMO（多输入多输出）控制器简单得多。MIMO 需要处理变量间的耦合（一个输入影响多个输出），需要解耦矩阵，调试难度指数级上升。

**但实际上，MiniCode 有一个 DecouplingController**（`decoupling_controller.py`），它正是用皮尔逊相关系数来检测变量间耦合的。但它的输出**没有被任何 PID 使用**——解耦矩阵算出来了，但 PID 的参数没有根据耦合强度调整。

```python
# decoupling_controller.py:176-191
def compute_decoupling_matrix(self) -> dict[str, dict[str, float]]:
    # 计算了 token_usage↔latency, context_pressure↔error_rate 等耦合关系
    # 但这个矩阵的输出只用于 get_coupling_status() 的日志展示
```

**所以 MiniCode 的实际情况是**：声明了要做解耦控制（代码存在），但解耦矩阵算出来之后没有反馈到 PID 参数中。5 个 PID 实际上是独立运行的。

**面试话术**：

> "选择 5 个独立 PID 而不是 MIMO，首先是工程上的务实——5 个独立 PID 的调试和推理比一个 MIMO 状态空间控制器简单一个数量级。但我的 DecouplingController 确实没有闭环——它计算了耦合矩阵但只用于可观测性展示。如果要做真正的 MIMO，解耦矩阵应该直接修改每个 PID 的 kp/ki/kd 来补偿耦合效应。这是我项目中的一个'声明了但没完全实现'的部分。"

**你的回答**："我认为是没有真正用上的" ✓

**面试官点评**：诚实，正确。但你在面试中不能只说"是"，要展开——说出证据（源码中的具体行）、分析后果（独立 PID 之间的耦合没人管）、提出改进方案（解耦矩阵回注到 PID 参数）。

**源码出处**：
- DecouplingController 计算解耦矩阵：`decoupling_controller.py:176-191`
- 解耦矩阵的使用仅限于 `get_coupling_status()`：`decoupling_controller.py:249-265`
- DecouplingController 在 agent_loop 中的调用：`agent_loop.py:1357-1363`（仅在 `not orch` 时调用）

---

### Q4：你的 ContextPIDController 的 kp=2.0、kd=0.3，FeedbackController 的 stability_pid 的 kp=1.5、kd=0.1。这些初始值是怎么确定的？做过参数整定吗？

**核心答案**：

从代码来看，这些初始值是**手动设定的经验值**：

```python
# FeedbackController
self._stability_pid = PIDController(kp=1.5, ki=0.2, kd=0.1)
self._performance_pid = PIDController(kp=1.0, ki=0.15, kd=0.08)
self._efficiency_pid = PIDController(kp=0.8, ki=0.1, kd=0.05)

# ContextPIDController (默认值)
kp=2.0, ki=0.15, kd=0.3
```

**这些值的逻辑**：
- 内层 kp=2.0 > 外层 kp=1.5：上下文压力需要更快响应
- 内层 kd=0.3 > 外层 kd=0.1：上下文更容易振荡，需要更强的微分阻尼
- 稳定性 > 性能 > 效率的 kp 递减：稳定性最重要

**但是**，MiniCode 有 AdaptivePIDTuner，它会在运行时自动调参。三种调参方法：

| 方法 | 原理 | 何时触发 |
|------|------|---------|
| Performance Adaptive（默认） | 规则法：大误差→加 kp/kd，小误差→加 ki | 每 20 步 |
| Relay Feedback | 继电器反馈产生极限环振荡，提取临界增益 | 连续振荡 > 3 次 |
| Gradient Based | 参数扰动 + 梯度下降 | 性能变差时 |

**面试话术**：

初始值是手动设定的经验值。内层（上下文 PID）的 kp 比外层（行为 PID）大，因为上下文压力的响应延迟容忍度更低——你宁愿稍微过度压缩，也不能让上下文溢出。但手动设定的值只是一个起点，我实现了 AdaptivePIDTuner，它会在运行时根据实际性能自动调整 kp/ki/kd。不过诚实地说，AdaptivePIDTuner 的梯度优化方法需要多次 evaluate_params() 调用，在真实 Agent 运行中代价太高，默认的规则法更实用。

**源码出处**：
- PID 初始值：`feedback_controller.py:172-174`、`context_cybernetics.py:209-211`
- AdaptivePIDTuner.tune()：`adaptive_pid_tuner.py:255-293`
- 调参触发点：`cybernetic_orchestrator.py:327-344`（每 20 步）

---

### Q5：你的 PID 输出经过 clamp 到 [-1, 1] 或 [0, 1]。但 PID 的积分项可能有 windup 问题——误差持续存在导致积分项无限增长。你做了什么来防止？

**核心答案**：

两个 PID 都做了 anti-windup：

**FeedbackController 的 PIDController**（`feedback_controller.py:133-135`）：
```python
self._state.integral += error * dt
self._state.integral = max(-10.0, min(10.0, self._state.integral))  # clamp
```

**ContextPIDController**（`context_cybernetics.py:249-251`）：
```python
self._integral += error * dt
self._integral = max(-self.integral_windup_limit,
                     min(self.integral_windup_limit, self._integral))  # 默认 2.0
```

内层的 windup limit (2.0) 比外层 (10.0) 更严格——上下文变化快，积分不能积累太多。

**但还有一个问题**：clamp 只能防止积分无限增长，不能防止"积分饱和后无法快速退出"。当误差由正变负时，需要先把积分从 clamp 上限降下来，这段时间 PID 输出仍然偏高。这种现象叫 **conditional integration**——更高级的做法是在误差穿越 0 时清零积分，MiniCode 没有做这个。

**面试话术**：

> "我做了最基本的 anti-windup：clamp 积分项。内层的 windup limit 比外层严格（2.0 vs 10.0），因为上下文变化更频繁，积分更容易积累。但我没有做 conditional integration（误差过零时清零积分），这意味着在误差反转时可能有短暂的响应延迟。如果要继续优化，应该加这个逻辑。"

**源码出处**：
- 外层 anti-windup：`feedback_controller.py:133-135`
- 内层 anti-windup：`context_cybernetics.py:249-251`

---

## Round 3：设计与权衡

### Q6：你有一个 SelfHealingEngine 专门做故障检测和修复，又有一个 PredictiveController 做预测。当一个指标的预测值超过阈值（PredictiveController）和实际值已经超过阈值（SelfHealingEngine）同时发生时，会发生什么？会不会两个系统打架？

**核心答案**：

它们不会直接冲突，因为修复的是**不同的东西**：

| | PredictiveController | SelfHealingEngine |
|---|---|---|
| 触发条件 | 预测值超阈值 + 趋势向上 | 实际值超阈值 |
| 修复方式 | 生成 PredictiveAction（日志告警为主） | 执行 HealingStrategy（修改运行时参数） |
| 真正修改运行时的动作 | `trigger_compaction`（唯一会实际执行的动作） | 调整 PID 参数、降低并发、安全模式等 |

**但有一个真实的隐患**：如果 PredictiveController 触发了 `trigger_compaction`，同时 SelfHealingEngine 也检测到 CONTEXT_OVERFLOW 触发了 `cybernetic_compaction`，那么会出现**双重压缩**——两个系统都尝试压缩上下文。ContextCybernetics 的 `run_cycle()` 可能被执行两次。

**实际上这个问题在代码中已经被防止了**：在 `step_start()` 中：

```python
# cybernetic_orchestrator.py:212-217
if step > 2:
    actions = self.predictive.generate_predictive_actions()
    if actions and actions[0].urgency > 0.7:
        action = actions[0]
        if action.recommended_action == "trigger_compaction" and self.context_cybernetics:
            logger.info("Predictive: trigger_compaction urgency=%.2f", action.urgency)
            # 只打日志，不实际执行压缩！
```

注意这里**只打了日志**，并没有调用 `context_cybernetics.run_cycle()`。实际压缩只在 `step_end` 中的 `FeedbackController.observe()` → `_apply_control_signal()` 链路触发。所以 PredictiveController 的压缩建议是**咨询性质**的，不会和 SelfHealingEngine 冲突。

**面试话术**：

> "PredictiveController 的压缩建议是纯咨询性质的——它只打日志说'我预测上下文快溢出了'，但不实际触发压缩。真正的压缩由 ContextCybernetics 的 run_cycle() 和 SelfHealingEngine 的 detect_and_heal() 执行。这两个是可能冲突的——如果 SelfHealingEngine 刚刚调整了 PID 参数，ContextCybernetics 下一轮 run_cycle() 就用新参数运行，这是一种'协作'。但如果两个系统同时尝试压缩上下文，可能会导致 double compaction。目前代码里通过执行顺序（SelfHealingEngine 在 run_cycle 之前）部分避免了这个问题。"

**源码出处**：
- PredictiveController 压缩建议只打日志：`cybernetic_orchestrator.py:212-217`
- SelfHealingEngine 实际执行修复：`self_healing_engine.py:287-297`

---

# 第二部分：通用项目面试

> 这一轮模拟的是**不看源码的面试官**。问题覆盖项目介绍、架构设计、安全、记忆、异常处理等通用维度。
> 回答者：Claude（基于 MiniCode 源码真实回答）

---

## Q1：简单介绍一下这个项目

MiniCode 是一个本地 AI Coding Agent 运行时——"从零实现的 Claude Code 迷你版"。用户输入自然语言任务 → Agent 自主规划、调用工具、管理上下文、控制成本。

**核心模块**：

| 模块 | 职责 |
|------|------|
| Agent Loop | ReAct 循环：任务 → LLM → 工具调用 → 结果回填 → 循环 |
| 工程控制论系统 | 15 个控制器的运行时治理框架——双层 PID、Kalman 状态估计、自愈引擎 |
| 工具系统 | 30+ 工具（文件读写、代码搜索、Shell 执行、Git 操作），参数校验+并发调度 |
| 权限管理 | 分层权限（只读/写入/执行），敏感操作需用户审批 |
| Memory 系统 | 向量检索 + 重排序 + 时间线记忆 + 工作记忆的多层记忆管线 |
| Context 管理 | 双层 PID 驱动的上下文压缩——不是简单截断，是按强度的渐进式 compact |
| TUI | 基于 Textual 框架的终端 UI，实时展示工具调用、思考过程、上下文水位 |

**和 Claude Code 的关系**：不是逆向工程，是理解其设计原理后的独立实现。重点在于"Agent 运行时"的工程架构。

---

## Q2：Agent 的执行链路如何设计？如何确保连续任务的正确性？

**执行链路**（`agent_loop.py` 的 `run_agent_turn()` 函数）：

```
用户输入
  → 意图解析（IntentParser：CODE/DEBUG/REFACTOR/SEARCH 等）
  → 前馈预配置（FeedforwardController：根据任务类型设置参数）
  → 记忆注入（MemoryPipeline：检索相关历史记忆注入 prompt）
  → 上下文初始化（ContextCybernetics：PID 控制器就绪）
  
  → 主循环 while step < max_steps:
      step_start:
        ① StateObserver: Kalman 估计系统隐藏状态
        ② PredictiveController: 预测趋势，提前预警
        
      LLM 调用:
        发送 messages → 模型返回 assistant / tool_calls
        
      工具执行:
        ③ ToolScheduler: 并发调度工具，冲突检测，超时控制
        
      step_end:
        ④ StabilityMonitor: 记录指标快照
        ⑤ ProgressController: 检测任务是否卡住
        ⑥ SelfHealingEngine: 检测故障并自愈
        ⑦ ContextCybernetics → FeedbackController → ControlSignal
           → 调整运行时参数（并发/模型/预算）
```

**如何确保连续任务的正确性**：

1. **Work Chain**：复杂任务拆成 `TaskGraph`，子任务串行/并行执行，有明确状态机（PENDING→RUNNING→COMPLETED/FAILED）
2. **ProgressController**：6 个信号综合打分，`stall_score >= 0.75` 触达用户
3. **SelfHealingEngine**：8 种故障类型各自有修复策略
4. **FeedbackController 闭环**：每步结束时 PID 计算控制信号，自动调整运行时参数

---

## Q3：如何处理工具调用的安全问题？

四层防护：

**第一层：权限模型**（`permissions.py`）

每个工具有权限级别标记。用户可配置三种模式：`default`（敏感操作弹窗确认）、`accept_all`（信任模式）、`deny_all`（只读模式）。

**第二层：安全执行**（`safe_execution.py`）

Shell 命令执行前审查——危险命令检测（`rm -rf`、`curl | bash` 等模式匹配）、工作区外路径拦截。

**第三层：参数校验**（`capability_registry.py`）

每个注册的工具声明参数 schema。ToolScheduler 在调用前校验参数，不合规的调用被拒绝并返回错误给 LLM。

**第四层：API Key 保护**

模型 API key 通过环境变量或 `.env` 注入，`config.py` 统一管理，key 不进入 prompt 上下文。

**源码出处**：`permissions.py`、`safe_execution.py`、`capability_registry.py`、`config.py`

---

## Q4：能力复用与 Skill 管理如何设计？

**三层能力体系**：

```
Layer 1: 原子工具（tools/ 目录，30+ 个）
  - read_file, write_file, edit_file, run_command 等
  - 每个工具继承 BaseTool，声明式注册：name + description + parameter_schema

Layer 2: 能力注册表（capability_registry.py）
  - 统一注册/发现所有工具
  - LLM 通过 function calling 的 tools 列表看到所有可用工具
  - 工具按类别分组（文件操作、代码搜索、Shell、Git 等）

Layer 3: Skills 系统（skills.py）
  - 将多个工具调用组合成可复用的工作流
  - LLM 可以通过 load_skill 工具动态加载
  - 用户通过 /<skill-name> 斜杠命令手动触发
```

**可扩展性**：新增工具只需在 `tools/` 下创建文件并注册；新增 Skill 定义工作流 + 触发条件；新增模型后端实现 `ModelAdapter` 接口。

---

## Q5：模型如何主动发起工具调用？

使用标准的 **Function Calling 机制**：

1. `agent_loop.py` 构建 messages 时，将 `CapabilityRegistry` 中的所有工具以 `tools` 参数传给 LLM API
2. LLM 返回的响应中若包含 `tool_calls` 字段，说明模型要调用工具
3. `agent_loop.py` 解析 `tool_calls`，提取 `tool_name` + `arguments`
4. 交给 `ToolScheduler` 并发执行
5. 每个工具的结果以 `tool_result` 角色追加到 messages
6. 下一次 LLM 调用时，模型看到工具结果，决定继续调工具还是输出最终回答

```
messages 结构：
  user: "修复 bug"
  assistant: tool_calls: [{"name": "read_file", "args": {...}}]
  tool: "文件内容..."
  assistant: tool_calls: [{"name": "edit_file", "args": {...}}]
  tool: "编辑成功"
  assistant: "bug 已修复，修改了 xxx"
```

---

## Q6：工具调用是否有参数校验和权限审批？具体怎么做？

**参数校验**在 `ToolScheduler` 中：

1. `CapabilityRegistry` 中每个工具注册了 `parameter_schema`（JSON Schema 格式）
2. 调用前，`ToolScheduler` 用 schema 校验 LLM 传来的 arguments
3. 校验失败 → 返回错误信息给 LLM（含期望的参数格式），LLM 可纠正后重试
4. 校验通过 → 进入权限审批

**权限审批**在 `permissions.py` 中：

```python
if tool.permission_level == "read":
    直接执行  # 只读操作不审批
elif tool.permission_level == "write":
    if user_mode == "accept_all": 直接执行
    elif user_mode == "deny_all": 拒绝
    else: 弹 TUI 确认框 → 用户 approve/deny
elif tool.permission_level == "execute":
    弹 TUI 确认框（显示完整命令）→ 用户审批
```

权限审批通过 TUI 事件流推到前端——用户看到确认卡片，按键 approve 或 deny。

---

## Q7：执行过程可以通过事件流实时展示到 TUI 吗？具体怎么做？

可以。整个执行过程通过**事件流**外化，TUI 订阅并实时渲染。

**事件类型**（`tui/event_flow.py`）：
- `thinking`：模型的思考过程
- `assistant_text`：模型的文本输出（流式 token）
- `tool_call_start/end`：工具调用的开始和结束
- `tool_permission_request`：权限审批请求
- `compaction_event`：上下文压缩事件
- `error_event`：错误事件

**数据流**：
```
agent_loop.py
  → on_assistant_stream_chunk (流式 token)
  → on_thinking_chunk (思考内容)
  → HookEvent 系统 (工具调用、错误、压缩)
  → TUI renderer 订阅 → 实时更新界面
```

**TUI 布局**（`tui/renderer.py`）：
- 左侧：对话区域（流式渲染 LLM 输出）
- 右侧：状态面板（上下文水位、步骤计数、活跃工具）
- 底部：输入栏（支持斜杠命令、多行输入）
- 工具调用以可折叠卡片展示；权限审批以模态弹窗展示

---

## Q8：每一次 run 过后会留下什么记录？

**五类记录**：

1. **会话记录**（`session.py`）：完整的 messages 历史 + session ID + 时间戳 + 元数据
2. **事件日志**（`tui/transcript.py`）：完整事件流，可回放
3. **控制论报告**（`cybernetic_supervisor.py`）：持久化到 `.minicode/cybernetic_supervisor.json`，包含各控制器健康度、风险等级、推荐动作
4. **Timeline Memory**（`timeline_memory.py`）：按时间线记录关键事件，用于后续记忆检索
5. **Cost Tracking**（`cost_tracker.py`）：Token 消耗、API 调用次数、预估费用

---

## Q9：多轮会话之间的会话历史如何管理？

**三层记忆体系**：

| 层级 | 存储 | 生命周期 | 检索方式 |
|------|------|---------|---------|
| Thread | session 对象 | 单次会话 | 直接引用 |
| Notes | MemoryCuratorAgent 提取 | 中期 | memory 系统查询 |
| Memory | 向量存储+时间线索引 | 持久化 | 语义相似度+时间范围 |

**续接流程**：

```
新一轮 run 启动:
  ① MemoryPipeline.inject(task_description) → 检索相关历史记忆
  ② 从 session store 加载上一轮 thread 摘要
  ③ 将记忆 + 摘要注入当前 messages
  ④ 模型获得完整上下文
```

---

## Q10：Subagents 具体如何实现？通信如何解决？什么时候调用？

**实现方式**：通过 `Agent` 工具，复用底层模型平台的 Subagent 能力。LLM 可以调用 `Agent` 工具，传入子任务描述，平台启动独立 Agent 实例执行。

**通信方式**：父子 Agent 通过**结构化返回值**通信。父 Agent 传入任务描述 → 子 Agent 返回结果（文本+结构化数据）→ 以 `tool_result` 形式回到父 Agent 的消息历史。

**调用时机**（由 LLM 自主决策）：
- 任务可并行分解（如"同时审查 A 和 B 文件"）
- 子任务独立且需要上下文隔离
- 需要独立工作空间（子 Agent 可获得独立的 git worktree）

**并发控制**（`ToolScheduler`）：子 Agent 可并发运行，有冲突检测（不能同时写同一文件），并发数上限由 FeedbackController 的 PID 动态调整。

---

## Q11：记忆管理是怎么做的？

**完整记忆管线**（`memory_pipeline.py` 编排）：

```
任务启动:
  ① MemoryInjector: 向量相似度检索
  ② MemoryReranker: LLM 重排序（相关度+时效性+重要性）
  ③ 注入 Prompt

任务执行中:
  ④ WorkingMemory: 本轮内的临时记忆（关键决策、模式、发现）

任务执行后:
  ⑤ MemoryCuratorAgent: 提取关键经验、模式、教训
  ⑥ TimelineMemory: 按时间线记录事件
```

**分层存储**：

| 层级 | 存储 | 检索方式 |
|------|------|---------|
| Working | 内存 dict | 直接读取 |
| Session | session 对象 | 直接引用 |
| Vector | 向量数据库 | 语义相似度 |
| Timeline | 时间戳索引 | 时间范围查询 |

---

## Q12：说说你对工程控制论的理解？是否相当于后台进程监控主进程？

**不是后台进程。** 工程控制论系统运行在 **同一个进程、同一个线程**中——是嵌入在 Agent Loop 主循环 `step_start()` 和 `step_end()` 中的**同步调用**。

**更好的类比**：不是"监控摄像头"，是**汽车的 ECU（电子控制单元）**——和发动机一起工作，实时感知、实时调节。

**五层架构**：

```
Layer 1 - 传感器层: StateObserver (Kalman×5), StabilityMonitor, ContextPressureSensor
Layer 2 - 预测层:    PredictiveController, PredictiveOverflowGuard, FeedforwardController
Layer 3 - 控制层:    FeedbackController (PID×3), ContextPIDController, CostControlLoop, DecouplingController
Layer 4 - 执行层:    SelfHealingEngine, ProgressController, AdaptivePIDTuner
Layer 5 - 聚合层:    CyberneticSupervisor
```

**核心闭环**：
```python
while step < max_steps:
    orch.step_start()              # 同步：感知→预测→预判
    next_step = model.next(msgs)   # LLM 调用
    execute_tools(next_step.tool_calls)
    orch.step_end()                # 同步：反馈→自愈→聚合
```

**面试关键句**："工程控制论的核心思想是——把 LLM 当成一个不可靠的黑箱，不去修改它，而是在它外面包一层反馈控制框架。正常时静默，异常时按比例介入。"

---

## Q13：说说你做了哪些异常处理？

**四层异常处理**：

**第一层：API 层面重试**
- `api_retry.py`：指数退避重试（rate limit、网络错误、5xx）
- `ModelSwitcher`：连续失败时自动切换备用模型
- Thinking 中断重试

**第二层：工具执行容错**
- `ToolScheduler`：超时检测 + 失败隔离（一个工具失败不影响其他并发工具）
- 错误信息结构化回传给 LLM，LLM 可调整策略重试
- 空响应重试（最多 3 次）

**第三层：控制论自愈**（`SelfHealingEngine`）

| 故障类型 | 检测条件 | 修复动作 |
|---------|---------|---------|
| RESOURCE_EXHAUSTION | CPU > 90% | 降低并发 |
| CONTEXT_OVERFLOW | context > 85% | 触发压缩 |
| TOOL_TIMEOUT | 超时频繁 | 串行模式 |
| ERROR_SPIKE | error_rate > 3.0 | 安全模式 |
| PERFORMANCE_DEGRADATION | latency > 45s | 升级模型 |
| OSCILLATION | osc_index > 0.6 | PID 阻尼 |
| DEADLOCK | 无响应 | 强制终止 |
| MEMORY_LEAK | 内存持续增长 | 强制回收 |

**第四层：兜底**
- `ProgressController`：stall_score > 0.75 → 用户介入
- `max_steps` 硬限制
- `finally` 块确保审计日志和任务状态一定写入

---

## Q14：斜杠命令如何识别？谁负责做这个工作？

**识别链**：

```
用户输入 "/compact"
  → tui/input_handler.py: 检测到 / 前缀
  → cli_commands.py: 命令路由表查找 "compact"
  → 找到匹配 → 执行对应 handler
```

**命令注册表**（`cli_commands.py`）：`/help`、`/compact`、`/clear`、`/model`、`/memory`、`/skills`、`/tools`、`/status` 等。

**与 Skill 的区别**：斜杠命令由用户手动触发、直接执行（不走 LLM）；Skill 由 LLM 在任务中自动调用 `load_skill`，也可由用户 `/skill-name` 手动触发。

---

## Q15：做这个项目中遇见的最大的问题是什么？怎么定位和解决的？

**（面试话术模板——可根据自己的理解调整）**

> "最大的问题是**上下文压缩的'抖颤'问题**。最初用简单阈值触发——usage > 85% 就压缩。但压缩完降到 60%，LLM 继续回答又涨到 86%，又触发压缩……长任务中可能压缩 5-6 次，严重影响效率。
>
> **定位过程**：在日志中加了上下文使用率的时序记录，画成折线图，发现是锯齿形——每次压缩后短暂下降然后快速反弹。这是控制系统中典型的**振荡**问题。
>
> **解决过程**：意识到简单的 if-else 阈值触发不够——需要用连续控制代替离散决策。引入了 PID 控制器：P（比例）响应当前偏差，I（积分）消除稳态误差，D（微分）预测趋势抑制超调。设置 setpoint=0.70（目标水位 70%）而不是 85%，让系统**提前、渐进地**调节。
>
> 改完之后，上下文水位从锯齿形变成了围绕 70% 的平滑曲线。压缩频率从每 5-6 步一次降到每 15-20 步一次。
>
> 这个经历让我深刻理解了：**工程问题不只是功能实现，更在于系统行为的平滑性。** 也是因为这个问题，我后续引入了完整的工程控制论体系——Kalman 状态估计、双层 PID、自愈引擎，都是从这个初始问题生长出来的。"

**这个回答的亮点**：有具体场景 → 有定位过程（日志+可视化）→ 有解决方案（PID）→ 有量化结果 → 拔高到设计思想。

---

## 面试话术速查表

| 问题 | 关键词/口诀 |
|------|-----------|
| 项目介绍 | "从零实现的 Claude Code 迷你版，重点在 Agent 运行时架构" |
| 执行链路 | "ReAct 循环 + 15 个控制器两层嵌入（step_start/step_end）" |
| 安全 | "四层防护：权限模型 + 安全执行 + 参数校验 + Key 保护" |
| Skill 管理 | "三层能力体系：原子工具 → 注册表 → Skills 工作流" |
| 工具调用 | "标准 Function Calling + tool_result 回填 messages" |
| 参数校验+权限 | "JSON Schema 校验 → 三级权限审批 → TUI 弹窗" |
| 事件流 | "HookEvent 系统 → TUI renderer 订阅 → 流式渲染" |
| 运行记录 | "五类：session + events + 控制论报告 + timeline + cost" |
| 会话管理 | "三层记忆：Thread → Notes → Memory" |
| Subagents | "Agent 工具 + 结构化返回值 + 并发冲突检测" |
| 记忆管理 | "六步管线：注入→重排→注入→工作记忆→策展→时间线" |
| 工程控制论 | "不是后台进程，是 ECU。五层架构，嵌入主循环同步执行" |
| 异常处理 | "四层：API 重试 → 工具容错 → 控制论自愈 → 兜底" |
| 斜杠命令 | "TUI 检测 / 前缀 → cli_commands.py 路由表 → handler" |
| 最大问题 | "上下文抖颤 → 日志可视化定位 → PID 替代阈值 → 量化改进" |
