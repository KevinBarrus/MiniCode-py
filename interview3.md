# MiniCode 项目详解3：Agent 循环骨架 (agent_loop.py)

## 一、定位：大脑的"调用中心"

如果 MiniCode 是一个人：

```
main.py          → 五官四肢（接收用户输入、初始化感官）
tty_app.py       → 神经系统（事件驱动、实时响应）
agent_loop.py    → 大脑皮层（调用 LLM、协调工具、控制论反馈）
工具系统          → 肌肉骨骼（执行具体操作）
控制论系统        → 自主神经（自动调节、自愈修复）
```

agent_loop.py **不做的事**：
- 不处理用户输入（那是 main.py / tty_app.py 的事）
- 不渲染 UI（那是 tui/ 的事）
- 不直接调用 LLM API（那是 model adapter 的事）
- 不实现工具的具体逻辑（那是 tools/ 的事）

agent_loop.py **做的事**：**把所有这些组件编排在一起，形成一个闭环**

---

## 二、整体架构概览

agent_loop.py 的核心是一个函数：`run_agent_turn()`（1,778 行文件，约 1,200 行在这里）

它的结构是**严格的线性初始化 + 循环执行**：

```
run_agent_turn()
│
├─ Phase 1: 变量声明 + 工具调度器初始化
├─ Phase 2: 工程控制论全量初始化（仅当 enable_work_chain=True）
│   ├─ 2a: Work Chain 解析（意图 → 任务对象）
│   ├─ 2b: Orchestrator 初始化（15 个控制器门面）
│   ├─ 2c: SmartRouter 模型路由
│   ├─ 2d: Feedforward 前馈预判
│   ├─ 2e: ModelSelection 模型选择
│   ├─ 2f: Memory 注入管线
│   ├─ 2g: Context 控制论初始化（PID + 预测守卫）
│   ├─ 2h: SelfHealing 自愈引擎初始化
│   └─ 2i: CostControl 成本控制初始化
│
├─ Phase 3: 上下文检查 + 预请求压缩
│
└─ Phase 4: 主循环 while step < max_steps
    ├─ 4a: 控制论闭环 step_start（观测当前状态）
    ├─ 4b: 调用 LLM _model_next()
    ├─ 4c: 分类 LLM 响应（assistant / tool_calls / progress）
    ├─ 4d: 并发执行工具
    ├─ 4e: 控制论闭环 step_end（反馈 + 调整）
    └─ 4f: 清理 + 反思（finally 块）
```

---

## 三、Phase 1-3：初始化阶段

### 3.0 变量声明

```python
current_messages = list(messages)    # 当前对话历史（会被修改）
saw_tool_result = False              # 本轮是否收到过工具结果
empty_response_retry_count = 0       # 空响应重试次数
recoverable_thinking_retry_count = 0 # thinking 中断重试
tool_error_count = 0                 # 工具执行错误累计
step = 0                             # 当前步数
```

这些是**整个循环期间共享的状态变量**。它们不是全局变量，而是闭包变量——只在 run_agent_turn() 内部可见

### 3.1 enable_work_chain 是什么？

```python
enable_work_chain: bool = True  # 默认值
```

这是一个**功能开关**：

| enable_work_chain | 效果 |
|---|---|
| `True`（默认） | 启用 15 个控制论控制器 + 智能路由 + 记忆注入 + 上下文控制论 + 自愈 + 成本控制 |
| `False` | 只做基础的 LLM 调用 + 工具执行，无任何控制论能力 |

**面试视角**：这个开关体现了"渐进式复杂度"的设计思想——核心链路可以独立运行，高级特性按需加载。这是后端开发中常见的功能开关模式

### 3.2 控制论初始化全貌

15 个控制器通过 **Orchestrator（门面模式）** 统一管理：

```
CyberneticOrchestrator 
│
├── 反馈层 (Feedback Layer)
│   ├── FeedbackController       — 双 PID 控制（332行）
│   ├── FeedforwardController    — 前馈预判（173行）
│   └── StabilityMonitor         — 健康度追踪（392行）
│
├── 高级控制层 (Advanced Control)
│   ├── AdaptivePIDTuner         — PID 自动调参（423行）
│   ├── StateObserver            — Kalman 状态估计（~300行）
│   ├── DecouplingController     — 多变量解耦（272行）
│   └── PredictiveController     — 预测控制（388行）
│
├── 恢复层 (Recovery Layer)
│   └── SelfHealingEngine        — 7 种自愈策略（534行）
│
├── 上下文层 (Context Layer)
│   ├── ContextCyberneticsOrchestrator — 7 层控制（851行）
│   └── CostControlLoop          — 成本 PID 控制
│
├── 记忆层 (Memory Layer)
│   ├── MemoryInjectionController — 注入决策
│   └── MemoryInjector           — 注入执行
│
├── 决策层 (Decision Layer)
│   ├── SmartRouter              — 任务→模型路由
│   ├── ModelSelectionController — 模型推荐
│   └── ModelSwitcher            — 运行时热切换
│
└── 评估层 (Evaluation Layer)
    ├── ProgressController       — 进度/卡住检测（161行）
    ├── ReflectionEngine         — 任务反思
    └── CyberneticSupervisor     — 聚合监控（266行）
```

**关键代码**：
```python
orch = CyberneticOrchestrator()
orch.initialize(model, tools, runtime)
# 然后从 orch 中取出各个控制器的引用：
feedback_controller = orch.feedback
state_observer = orch.state_observer
predictive_controller = orch.predictive
# ... 等等
```

**为什么用门面模式？**
- agent_loop.py 只需要调用 `orch.step_start()` 和 `orch.step_end()`
- 15 个控制器的内部通信被 Orchestrator 封装
- 新增控制器只需修改 Orchestrator，不影响 agent_loop.py

### 3.3 上下文检查 + 预请求压缩

在进入主循环**之前**，先检查一次上下文状态：

```python
context_manager.get_stats()
    ↓
if context_cybernetics:
    → run_cycle()  # PID 控制论管线（Sense → Predict → Control → Act → Verify → Learn）
elif context_compactor:
    → process_request()  # 传统压缩
elif context_manager:
    → should_auto_compact() → compact_messages()
```

**为什么要在循环外做一次？**

如果上下文已经接近上限（比如之前对话很长），进入循环后第一轮调 LLM 就会报 "prompt too long" 错误。循环外的检查是**防患于未然**

---

## 四、Phase 4：主循环骨架

### 4.1 完整循环结构

```python
while max_steps is None or step < max_steps:
    step += 1

    # 1.控制论闭环 (step_start) 
    if orch:
        orch.step_start(context_manager, step, tool_error_count, saw_tool_result)
    else:
        # 手动按组件触发：
        state_observer.update(measurement)      # Kalman 状态估计
        predictive_controller.generate_actions() # 预测控制

    # 2.调用 LLM 
    try:
        next_step = _model_next(model, messages, ...)
    except ConnectionError:   → 降级处理 → return
    except TimeoutError:      → 降级处理 → return
    except Exception:         → 控制论恢复 / ModelSwitcher / fallback

    # 3.分类 LLM 响应
    if next_step.type == "assistant":
        → 文本响应 → return current_messages（结束 turn）
    elif next_step.type == "progress":
        → 进度消息 → 追加 NUDGE_CONTINUE → continue
    elif next_step.calls:
        → 工具调用 → 进入工具执行

    # 4.并发执行工具
    tool_scheduler.schedule_calls(calls, tools)
        ├─ concurrent_calls: ThreadPoolExecutor 并发执行
        └─ serial_calls: 按原始顺序串行执行

    # 工具结果处理管线
    ErrorClassifier.classify(result)  # 智能错误分类
    NudgeGenerator.generate(...)      # 生成纠错提示
    ReadDedup.should_dedup(...)       # 去重相同文件读取

    # 5.控制论闭环 (step_end)
    if orch:
        step_summary = orch.step_end(...)      # 反馈 + 自愈 + 控制信号
        _apply_control_signal(control_signal)  # 应用信号到运行时

    # 6.继续
    continue  # 回到循环开始，让 LLM 处理工具结果
```

### 4.2 LLM 响应分类——核心状态机

agent_loop.py 的核心是一个**状态机**，根据 LLM 返回的 `next_step.type` 决定下一步：

```
                    ┌─────────────────┐
                    │  调用 LLM API    │
                    │  _model_next()   │
                    └────────┬────────┘
                             │
                  ┌──────────┴──────────┐
                  │   next_step.type?    │
                  └─────────────────────┘
                             │
         ┌───────────────────┼───────────────────┐
         ▼                   ▼                   ▼
    "assistant"         "progress"          tool_calls
    (文本响应)          (进度消息)          (工具调用)
         │                   │                   │
    ┌────┴────┐         ┌────┴────┐         ┌────┴────┐
    │空响应?   │         │追加     │         │并发执行  │
    │→重试/fall│        │NUDGE    │         │工具      │
    │back      │        │CONTINUE │         │→结果注入│
    └─────────┘         └─────────┘         │messages  │
                                            │→continue │
                                            └──────────┘
```

**为什么 LLM 返回文本就 return，返回工具调用就 continue？**

因为 agent_loop.py **只管一个 turn**。一个 turn 的定义是：
- 用户发一个请求 → Agent 理解 → 执行工具 → 理解结果 → ... → **最终给出文本答案**

文本答案就是这个 turn 的终点。工具调用只是中间步骤

### 4.3 NUDGE 消息机制

NUDGE 是**系统级提示**，作为 `role: "user"` 消息注入到对话中，**防止 LLM 卡住**：

| NUDGE 类型 | 触发条件 | 内容要点 |
|------------|---------|---------|
| `NUDGE_CONTINUE` | 收到 progress 消息后 | "立即继续具体的工具调用或代码修改" |
| `NUDGE_AFTER_TOOL_RESULT` | 收到 progress 且之前有工具结果 | "审查结果，然后采取下一步具体行动" |
| `NUDGE_AFTER_EMPTY_RESPONSE` | LLM 返回空响应（有过工具结果） | "选最可能的下一步并尝试" |
| `NUDGE_AFTER_EMPTY_NO_TOOLS` | LLM 返回空响应（还没用过工具） | "先检查相关文件了解代码库" |
| `RESUME_AFTER_MAX_TOKENS` | thinking 因 max_tokens 中断 | "立即从断点继续" |
| `RESUME_AFTER_PAUSE` | thinking 因 pause_turn 中断 | "继续下一步" |

**面试视角**：NUDGE 是**提示工程在生产环境中的应用**——不是写一个完美的 system prompt 就完事了，而是在运行时动态注入上下文相关的引导语

### 4.4 LLM 错误恢复——三级递进

```
                    调用 LLM 失败
                         │
            ┌────────────┼────────────┐
            ▼            ▼            ▼
      ConnectionError  TimeoutError  其他异常
      → fallback消息   → fallback消息     │
      → return         → return      ┌───┴───┐
                                     ▼       ▼
                              可恢复?    不可恢复
                          (prompt too   (rate limit/
                           long等)      auth等)
                               │           │
                    ┌──────────┴──┐        ▼
                    ▼             ▼    ModelSwitcher
              context_       context_  切换模型重试
              cybernetics    compactor      │
              .try_reactive  .reactive  ┌──┴──┐
              _recover()     _recover() │成功  │失败
                    │             │    │→cont│→fallback
                    └─────┬───────┘    └─────┘
                          │
                    恢复成功?
                    ├─ 是 → continue（重试 LLM 调用）
                    └─ 否 → ModelSwitcher → fallback
```

**关键点**：
- `ConnectionError` 和 `TimeoutError` **直接放弃**——网络问题重试可能无意义
- 其他异常先尝试**控制论恢复**（压缩上下文再重试）——因为最常见的是 "prompt too long"
- 恢复失败再尝试**切换模型**（从 Claude 切换到备用模型）
- 全部失败才返回 **fallback 消息**

### 4.5 工具并发执行——读写分离

```python
if len(calls) <= 1:
    # 单工具：直接执行
    result = _execute_single_tool(call, tools, ...)
else:
    # 多工具：ToolScheduler 智能分区
    concurrent_calls, serial_calls = tool_scheduler.schedule_calls(calls, tools)

    # Phase 1: 并发执行读操作（ThreadPoolExecutor）
    for call in concurrent_calls:
        pool.submit(_execute_single_tool, ...)

    # Phase 2: 串行执行写操作（保持原始顺序）
    for call in serial_calls:
        _execute_single_tool(call, ...)
```

**读写分离策略**：
- **读操作**（read_file, grep_files, list_files）：并发，无冲突风险
- **写操作**（write_file, edit_file, run_command）：串行，保证顺序（防止后一个写操作基于前一个的结果）

**并发度由谁控制？**

```
ToolScheduler.get_recommended_max_workers()
    ↑ 输入：错误率、平均延迟、近期失败次数
    ↓ 输出：推荐并发数

控制论信号覆盖：
    FeedbackController 如果检测到振荡 → _force_max_workers = 2
    FeedbackController 如果检测到稳定 → 恢复正常并发度
```

### 4.6 工具结果处理管线

```
ToolResult
    ├─ result.ok == False
    │   ├─ ErrorClassifier.classify(output, tool_name)
    │   │   → 智能分类：权限问题？网络问题？语法错误？
    │   │
    │   └─ NudgeGenerator.generate(classified, retry_count)
    │       → "[System note: 这个错误是因为...建议...]"
    │
    ├─ result.ok == True + 稳定性压力
    │   └→ "[System note: 系统处于稳定性压力下，倾向于小步增量]"
    │
    └─ ReadDedup（仅 read_file）
        └─ 如果重复读取同一文件 → "[content unchanged from read #1]"
```

**ReadDedup 的作用**：Agent 经常会多次读取同一个文件。如果每次完整返回，会浪费大量上下文空间。ReadDedup 检测到重复读取时，用一个简短的 stub 替换内容

---

## 五、控制论在什么条件下发挥作用？

这是面试中最需要讲清楚的问题。控制论不是每一步都"大动干戈"，大多数时候它只是**默默观测**

### 场景演示：用户说"帮我重构数据库层"

```
step=1: orch.step_start()
    ├─ StateObserver: internal_load=0.2（低负载，正常）
    ├─ Predictive: 无风险预测
    └─ → 不做任何干预

    LLM: [read_file("db.py"), grep_files("SELECT"), list_files("models/")]
    → 3 个读操作，并发执行 ✅

────────────────────────────────────────────────

step=3: orch.step_start()
    ├─ StateObserver: internal_load=0.6（中等）
    │   → hidden_errors=0.7（基于可观测输出推断）
    │   → 触发 self_healing_engine.detect_and_heal()
    │       → 诊断: CONTEXT_CONFUSION
    │       → 策略: 建议压缩上下文
    │
    └─ Predictive: 预测 error_rate 继续上升
        → urgency=0.8（高紧急度）
        → 触发 trigger_compaction

    → 上下文被压缩后 LLM 调用正常 ✅

────────────────────────────────────────────────

step=5: orch.step_end()
    ├─ FeedbackController.compute_control_signal()
    │   → error_frequency=0.4（工具失败率 40%）
    │   → 控制信号: reduce_tool_timeout=10s（缩短超时）
    │
    ├─ _apply_control_signal()
    │   → tool_scheduler._force_tool_timeout = 10.0
    │
    └─ DecouplingController.compute_decoupling_matrix()
        → token_usage 和 latency 正相关（耦合度 0.7）
        → 建议: 减少并发工具调用
```

### 各控制器的触发条件总结

| 控制器 | 每步都运行？ | 触发条件 | 做什么 |
|--------|:----------:|---------|--------|
| StateObserver | ✅ 是 | 总是运行（卡尔曼滤波需要持续更新） | 估计系统内部状态 |
| Predictive | ✅ 是 | 总是预测，但只在 urgency>0.7 时干预 | 预测未来风险 |
| Feedback | ✅ 是 | 总是计算，但只在 confidence>0.6 时应用 | 输出控制信号 |
| SelfHealing | 条件触发 | error_rate 高 / context_usage 高 / oscillation 高 | 诊断+修复 |
| Progress | ✅ 是 | 总是检测，但只在 health_score 低时警告 | 检测任务卡住 |
| Decoupling | ✅ 是 | 总是计算解耦矩阵 | 发现隐藏耦合关系 |
| ModelSwitcher | LLM 错误时 | API 异常且非 rate limit | 热切换模型 |
| ContextCybernetics | 条件触发 | 上下文压力超过 setpoint（默认 70%） | PID 控制压缩 |

**一句话总结**：控制论让 MiniCode 从"被动响应错误"变成"主动预防错误"

---

## 六、核心数据流

```
                    ┌──────────────────────────────────┐
                    │         run_agent_turn()          │
                    │                                   │
messages (输入) ────→│  current_messages                │
                    │                                   │
                    │  ┌─────────────────────────────┐  │
                    │  │   while step < max_steps:   │  │
                    │  │                              │  │
                    │  │  orch.step_start() ← 观测   │  │
                    │  │       ↓                      │  │
                    │  │  _model_next() ← 调 LLM     │  │
                    │  │       ↓                      │  │
                    │  │  ┌──────────────────────┐    │  │
                    │  │  │ "assistant" → return  │    │  │
                    │  │  │ "progress" → continue │    │  │
                    │  │  │ tool_calls → 执行     │    │  │
                    │  │  └──────────────────────┘    │  │
                    │  │       ↓                      │  │
                    │  │  tools.execute(并发/串行)    │  │
                    │  │       ↓                      │  │
                    │  │  结果注入 messages            │  │
                    │  │       ↓                      │  │
                    │  │  orch.step_end() ← 反馈     │  │
                    │  │       ↓                      │  │
                    │  │  _apply_control_signal()     │  │
                    │  │       ↓                      │  │
                    │  │  continue ─────────────────→ │  │
                    │  │                              │  │
                    │  └─────────────────────────────┘  │
                    │                                   │
messages (输出) ←───│  返回更新后的对话历史              │
                    └──────────────────────────────────┘
```

---

## 七、关键设计决策

### 7.1 为什么 LLM 调用和工具执行在同一个循环里？

这体现了 **ReAct 模式**（Reasoning + Acting）：

```
传统方案（非Agent）：调 LLM → 返回 → (程序决定) → 调工具 → 返回给用户
MiniCode 方案（Agent）：调 LLM → 工具调用 → 执行工具 → 追加结果 → 再调 LLM → ...
```

Agent 需要在**工具结果的基础上继续推理**。让 LLM 在同一个上下文中看到工具的返回结果，才能做出下一步决策

### 7.2 为什么用 `continue` 而不是 `return`？

```python
if next_step.calls:
    # 执行工具...
    continue  # ← 回到循环开始，让 LLM 处理工具结果

if next_step.type == "assistant":
    return current_messages  # ← 只有文本答案才结束 turn
```

工具只是**中间步骤**。执行完工具后，必须再调一次 LLM 让模型理解结果并决定下一步

### 7.3 控制论为什么在 step_start 和 step_end 两个位置？

```
step_start:  观测 → 决策 → 调整参数（在调 LLM 前）
step_end:    反馈 → 计算控制信号（在工具执行后）
```

这是**闭环控制**的标准结构：
- step_start = **传感器读数**：现在状态怎么样？
- step_end = **控制器输出**：需要调整什么？

### 7.4 agent_loop.py 的边界

agent_loop.py **只管一个 turn**：

```
用户输入 1 → run_agent_turn() → 返回 → main.py / tty_app.py 处理
用户输入 2 → run_agent_turn() → 返回 → ...
```

每次调用 `run_agent_turn()` 是独立的——虽然 messages 在调用之间被保留（由调用方管理），但控制论状态（step, tool_error_count 等）只在单个 turn 内有效

---

## 八、面试要点速查

| 问题 | 答案要点 |
|------|---------|
| **agent_loop.py 的职责** | 编排 LLM 调用 + 工具执行 + 控制论反馈的闭环 |
| **为什么用 Orchestrator 门面** | 15 个控制器统一管理，agent_loop 不需要知道内部通信细节 |
| **工具为什么并发执行** | 读写分离（读并发、写串行），ToolScheduler 动态调整并发度 |
| **错误恢复有几级** | 三级：控制论恢复 → Compactor 恢复 → ModelSwitcher 切换模型 |
| **NUDGE 消息的作用** | 防止 LLM 卡住，在模型不确定下一步时给温和的动态引导 |
| **enable_work_chain 开关的意义** | 控制论特性可插拔，核心链路独立运行 |
| **为什么工具执行后 continue** | ReAct 模式：让 LLM 看到工具结果后继续推理 |
| **控制论什么时候介入** | 不是每步都干预，只在指标异常时才"出手" |
