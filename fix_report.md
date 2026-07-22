## ✅ 缺陷 1 已修复：传感器输入数据是假的

### 修改的文件
- `minicode/agent_loop.py:530` — 增加 `actual_response_time` 状态，保存最近一次 LLM 调用的真实耗时。
- `minicode/agent_loop.py:815-819` — 上下文控制初始化时使用真实延迟，不再使用 `step * 2.0`。
- `minicode/agent_loop.py:860-872` — 将真实延迟传入 `orch.step_start()`，并用于无 orchestrator 分支的状态观测。
- `minicode/agent_loop.py:950-1039` — 在 `_model_next()` 调用前后计时，并覆盖上下文恢复、模型切换重试路径。
- `minicode/agent_loop.py:1188-1191` — 将真实延迟传入工具调度器的延迟估计。
- `minicode/agent_loop.py:1358-1362` — 解耦控制的延迟测量改用真实耗时。
- `minicode/agent_loop.py:1373-1380` — 将当前 LLM 调用耗时传入 `orch.step_end()`。
- `minicode/agent_loop.py:1557-1560` — 稳定性快照使用真实平均延迟。
- `minicode/cybernetic_orchestrator.py:176-192` — `step_start()` 增加 `actual_response_time` 参数，并写入 `MeasurementVector.response_time`。
- `minicode/cybernetic_orchestrator.py:219-245` — `step_end()` 增加 `actual_response_time` 参数，并写入 `MetricSnapshot.avg_latency`。

### 数据流变化

修复前：
```text
agent_loop.py
  └── step * 2.0
        ├──→ StateObserver.response_time
        ├──→ StabilityMonitor.avg_latency
        └──→ DecouplingController latency
```

修复后：
```text
agent_loop.py
  ├── t0 = time.time()
  ├── _model_next(...)
  ├── actual_response_time = time.time() - t0
  │
  ├──→ orch.step_end(actual_response_time)
  │      └──→ MetricSnapshot.avg_latency
  │
  ├──→ 下一轮 orch.step_start(actual_response_time)
  │      └──→ MeasurementVector.response_time
  │             └──→ StateObserver / Kalman Filter
  │
  └──→ DecouplingController latency = actual_response_time / 60.0
```

`step_start()` 位于本轮 LLM 调用之前，因此首轮使用默认值 `0.0`，后续轮次使用上一轮已完成调用的真实耗时；`step_end()` 使用当前轮真实耗时。

### 新的数据流图
```text
LLM 调用
  │
  ├── t0 = time.time()
  ├── _model_next(...)
  └── actual_response_time = time.time() - t0
          │
          ├──→ step_end()
          │      └──→ StabilityMonitor.avg_latency
          │
          ├──→ 下一轮 step_start()
          │      └──→ StateObserver.response_time
          │             └──→ Kalman Filter
          │
          └──→ DecouplingController.token_usage_to_latency
```

### 验证方法
- `python3 -m py_compile minicode/agent_loop.py minicode/cybernetic_orchestrator.py` — 语法检查通过。
- 检查 `agent_loop.py` 和 `cybernetic_orchestrator.py`，相关 `response_time`、`avg_latency` 和解耦延迟不再使用 `step * 2.0`。

## ✅ 缺陷 2 已修复：SelfHealingEngine placebo 策略

### 修改的文件
- `minicode/self_healing_engine.py:323-332` — `_execute_reduce_concurrency()` 在 scheduler 存在时设置 `_force_max_workers = 1`，无 scheduler 时返回失败。
- `minicode/self_healing_engine.py:334-364` — `_execute_reduce_timeout()` 仅在 scheduler 暴露受支持的超时字段时减半；否则返回不支持信息。
- `minicode/self_healing_engine.py:366-389` — `_execute_safe_mode()` 设置 `_force_max_workers = 1`，并在 scheduler 暴露串行模式字段时启用该字段。
- `minicode/self_healing_engine.py:433-455` — `_execute_force_terminate()` 调用 scheduler 的取消/中断方法；没有接口时返回失败。

### 数据流变化

修复前：
```text
SelfHealingEngine.detect_and_heal(metrics)
  └──→ HealingStrategy.execute()
          └──→ 返回 success=True
                  └──→ scheduler 没有发生任何改变
```

修复后：
```text
SelfHealingEngine.detect_and_heal(metrics)
  └──→ HealingStrategy.execute()
          ├── RESOURCE_EXHAUSTION
          │     └──→ tool_scheduler._force_max_workers = 1
          ├── TOOL_TIMEOUT
          │     └──→ 已暴露超时字段减半；否则明确返回失败
          ├── ERROR_SPIKE
          │     └──→ tool_scheduler._force_max_workers = 1
          └── DEADLOCK
                └──→ scheduler.cancel_all()/interrupt()（若存在）
```

### 新的数据流图
```text
故障指标
  │
  └──→ _detect_faults()
          │
          └──→ _execute_healing()
                  │
                  ├── RESOURCE_EXHAUSTION
                  │      └──→ ToolScheduler._force_max_workers = 1
                  │
                  ├── TOOL_TIMEOUT
                  │      └──→ scheduler timeout / _force_tool_timeout /= 2
                  │
                  ├── ERROR_SPIKE
                  │      └──→ ToolScheduler._force_max_workers = 1
                  │             └──→ 后续工具执行最多一个 worker
                  │
                  └── DEADLOCK
                         └──→ scheduler cancellation / interruption
```

### 验证方法
- `python3 -m py_compile minicode/self_healing_engine.py` — 语法检查通过。
- 使用真实 `ToolScheduler` 验证并发降为 1、安全模式生效、无超时/取消接口时诚实失败。
- 使用带超时字段和 `cancel_all()` 的最小 scheduler 验证超时减半和取消调用均生效。
- 尝试运行 `pytest -q tests/test_advanced_cybernetics.py -k 'SelfHealingEngine'`，但当前环境没有安装 `pytest`。

## ✅ 缺陷 3 已修复：缺少真实 Agent A/B 对比评测

### 修改的文件
- `minicode/agent_loop.py:560-563` — 将上下文压缩器、ContextCybernetics、MemoryManager 和 CostControl 的局部变量提前初始化为 `None`，使 `enable_work_chain=False` 的基线路径可以正常运行。
- `tests/test_cybernetic_integration.py:1-139` — 新增真实 Agent 集成测试，使用 Mock LLM 和真实工具注册表。

### 数据流变化

修复前：
```text
enable_work_chain=False
  └──→ run_agent_turn()
          └──→ 访问未初始化的 context_cybernetics/context_compactor
                  └──→ A/B 基线无法完成
```

修复后：
```text
同一任务 + 同一 Mock LLM
  ├── enable_work_chain=False
  │      └──→ Agent 基线执行
  │             └──→ 任务结果、步数、工具错误数
  │
  └── enable_work_chain=True
         └──→ Agent + CyberneticOrchestrator 执行
                └──→ 任务结果、步数、工具错误数
                       └──→ A/B 可比较数据
```

### 新的数据流图
```text
Mock LLM
  │
  ├──→ baseline: enable_work_chain=False
  │      └──→ ToolRegistry → ToolScheduler → result.txt
  │
  └──→ cybernetic: enable_work_chain=True
         └──→ Orchestrator
                ├──→ ContextCybernetics
                ├──→ FeedbackController
                ├──→ SelfHealingEngine
                └──→ ToolRegistry → ToolScheduler → result.txt
                         │
                         └──→ 对比 steps/tool_errors/messages/completed
```

### 集成覆盖
- 同一写文件任务的 baseline/cybernetic 双臂执行与结果对比。
- 上下文压力 → ContextCybernetics → SystemState → FeedbackController 链路。
- StateObserver 接收错误爆发数据，并触发 SelfHealingEngine 的资源故障策略。

### 验证方法
- `python3 -m py_compile minicode/agent_loop.py tests/test_cybernetic_integration.py` — 语法检查通过。
- 使用临时工作区执行两臂 Mock LLM A/B 测试，结果均完成、各执行 1 步且工具错误数为 0。
- 上下文压力和错误爆发集成检查通过。
- 当前环境没有安装 `pytest`，因此未能通过 pytest 命令运行测试文件。

## ✅ 缺陷 4 已修复：解耦矩阵没有被 PID 消费

### 修改的文件
- `minicode/decoupling_controller.py:148-155` — 增加已应用耦合记录，避免同一耦合在每个 step 重复衰减 PID 增益。
- `minicode/decoupling_controller.py:194-223` — 新增 `apply_to_pid()`，将强耦合关系映射到 Context PID、性能 PID、稳定性 PID 和效率 PID，并降低对应 `kp`。
- `minicode/decoupling_controller.py:278-279` — reset 时清理已应用的 PID 耦合记录。
- `minicode/cybernetic_orchestrator.py:301-309` — 在 `step_end()` 中调用解耦补偿，并将调整结果放入 step summary。
- `tests/test_cybernetic_integration.py:143-159` — 新增强耦合到 PID 增益调整的集成检查。

### 数据流变化

修复前：
```text
agent_loop.py
  └──→ record_measurement()
          └──→ compute_decoupling_matrix()
                  └──→ 结果仅用于状态/日志
                         └──→ PID 参数不变
```

修复后：
```text
agent_loop.py
  └──→ record_measurement()
          └──→ CyberneticOrchestrator.step_end()
                  └──→ apply_to_pid()
                          ├──→ Context PID.kp
                          ├──→ Performance PID.kp
                          ├──→ Stability PID.kp
                          └──→ Efficiency PID.kp
```

### 新的数据流图
```text
变量测量
  │
  └──→ CouplingAnalyzer
          └──→ compute_decoupling_matrix()
                  │
                  └──→ apply_to_pid(context_pid, feedback_controller)
                          │
                          ├── coupling > 0.5
                          │      └──→ kp *= (1 - coupling * 0.5)
                          │
                          └──→ step_summary["decoupling_adjustments"]
```

相同耦合关系只在首次应用时调整一次，避免每个 step 重复降低 `kp` 造成控制器失稳。

### 验证方法
- `python3 -m py_compile minicode/decoupling_controller.py minicode/cybernetic_orchestrator.py tests/test_cybernetic_integration.py` — 语法检查通过。
- 使用 6 组强相关测量验证性能 PID 的 `kp` 被降低。
- 重复调用验证同一耦合不会重复衰减 `kp`。
- 使用 `CyberneticOrchestrator.step_end()` 验证解耦调整结果进入 summary。

## ✅ 缺陷 5 已修复：FeedforwardController 没有调整 PID setpoint

### 修改的文件
- `minicode/feedforward_controller.py:16-37` — `PreemptiveConfig` 增加稳定性、性能和效率三个 setpoint 字段，并纳入默认配置合并。
- `minicode/feedforward_controller.py:51-60` — 为 CODE、DEBUG、REFACTOR、SEARCH、REVIEW、TEST、DOCUMENT 和 SYSTEM 意图增加目标值映射。
- `minicode/feedback_controller.py:192-202` — 新增 `set_setpoints()`，将前馈配置安全地写入三个外层 PID 目标值并限制在 `0.0-1.0`。
- `minicode/agent_loop.py:615-625` — 任务初始化完成前馈配置后，将三个 setpoint 应用到实际运行中的 `FeedbackController`。
- `tests/test_feedforward_controller.py` — 增加默认值、意图映射和反馈控制器目标值测试。
- `tests/test_cybernetic_integration.py` — 增加真实 Agent 初始化时 setpoint 传递测试。

### 数据流变化

修复前：
```text
任务意图
  └──→ FeedforwardController
          └──→ token_budget / concurrency / timeout
                  └──→ FeedbackController PID targets 固定为
                         stability=0.85, performance=0.75, efficiency=0.60
```

修复后：
```text
任务意图
  └──→ FeedforwardController.preconfigure()
          └──→ PreemptiveConfig
                  ├──→ token_budget / concurrency / timeout
                  └──→ stability_setpoint /
                      performance_setpoint /
                      efficiency_setpoint
                          └──→ FeedbackController.set_setpoints()
                                  └──→ 三个 PID 使用任务类型目标值
```

### 新的数据流图
```text
REFACTOR / DEBUG
  └──→ 高稳定性目标
          └──→ stability PID setpoint = 0.90

SEARCH / DOCUMENT
  └──→ 高效率目标
          ├──→ stability PID setpoint = 0.70
          └──→ efficiency PID setpoint = 0.80

CODE / TEST
  └──→ 中等目标
          ├──→ stability PID setpoint = 0.85
          ├──→ performance PID setpoint = 0.75
          └──→ efficiency PID setpoint = 0.60
```

### 验证方法
- `python3 -m py_compile minicode/feedforward_controller.py minicode/feedback_controller.py minicode/agent_loop.py tests/test_feedforward_controller.py tests/test_cybernetic_integration.py` — 语法检查通过。
- 验证 REFACTOR、SEARCH 等意图生成不同 setpoint。
- 验证 `FeedbackController.get_status()` 返回前馈配置后的目标值。
- 使用真实 Mock Agent 验证初始化阶段确实调用 `set_setpoints()`。
- 当前环境没有安装 `pytest`，因此未运行 pytest 命令。

## ✅ 缺陷 10 已修复：控制论集成测试缺失

### 修改的文件
- `tests/test_cybernetic_integration.py:1-265` — 完整建立控制论集成测试文件，覆盖 Mock LLM Agent、A/B 对比、上下文压力、错误爆发、解耦、预测压缩、前馈 setpoint 和振荡故障链路。
- `fix_report.md` — 记录缺陷 10 的集成测试补齐情况。

### 数据流变化

修复前：
```text
控制器单元测试
  ├──→ 各模块独立验证
  └──→ 缺少跨模块 Agent 执行链路验证
```

修复后：
```text
Mock LLM + ToolRegistry + Agent Loop
  ├──→ baseline/cybernetic A/B
  ├──→ ContextCybernetics → FeedbackController
  ├──→ StateObserver → SelfHealingEngine
  ├──→ DecouplingController → PID
  ├──→ PredictiveController → ContextCompaction
  └──→ Oscillation feedback → outer ControlSignal
```

### 新的数据流图
```text
真实 Agent 集成测试
  │
  ├── 正常任务
  │     └──→ Mock LLM → ToolRegistry → Agent result
  │
  ├── A/B 对比
  │     └──→ enable_work_chain=False / True
  │             └──→ completion / steps / tool_errors
  │
  ├── 故障场景
  │     ├──→ 上下文压力 → Context PID → Feedback PID
  │     ├──→ 错误爆发 → StateObserver → SelfHealingEngine
  │     └──→ 振荡序列 → SystemState → ControlSignal
  │
  └── 预测与解耦
        ├──→ predictive compaction → message synchronization
        └──→ coupling matrix → PID gain adjustment
```

### 验证方法
- `wc -l tests/test_cybernetic_integration.py` — 集成测试文件已存在并包含 7 个测试场景。
- `python3 -m py_compile tests/test_cybernetic_integration.py` — 语法检查通过。
- 已手动执行振荡故障集成场景，确认 `SystemState.oscillation_index=0.4` 且外层控制信号收到振荡数据。
- 当前环境没有安装 `pytest`，因此未运行 pytest 命令。

## ✅ 缺陷 9 已修复：预测建议只打日志不执行

### 修改的文件
- `minicode/cybernetic_orchestrator.py:176-230` — `step_start()` 接收当前消息列表，在高紧急度预测到上下文压缩时实际调用 `ContextCybernetics.run_cycle()`。
- `minicode/cybernetic_orchestrator.py:216-230` — 压缩成功后通过原列表切片更新消息，并同步 `context_manager.messages`。
- `minicode/agent_loop.py:867-874` — 将 `current_messages` 传入 `orch.step_start()`。
- `tests/test_cybernetic_integration.py` — 增加预测性压缩执行与消息同步测试。
- `fix_report.md` — 记录缺陷 9 的修复过程。

### 数据流变化

修复前：
```text
PredictiveController.generate_predictive_actions()
  └──→ trigger_compaction
          └──→ logger.info()
                  └──→ 不执行压缩
```

修复后：
```text
PredictiveController.generate_predictive_actions()
  └──→ trigger_compaction, urgency > 0.7
          └──→ ContextCybernetics.run_cycle(messages, ...)
                  ├──→ 压缩消息
                  ├──→ 更新 current_messages
                  └──→ 同步 context_manager.messages
```

### 新的数据流图
```text
step_start()
  │
  └──→ PredictiveController
          └──→ PredictiveAction(
                  recommended_action="trigger_compaction",
                  urgency=0.9
              )
                  │
                  └──→ context_cybernetics.run_cycle()
                          │
                          ├──→ compacted_messages
                          ├──→ current_messages[:] = compacted_messages
                          └──→ context_manager.messages = current_messages
                                  └──→ 后续 LLM 调用使用已压缩上下文
```

如果消息列表不存在、上下文控制未启用或执行压缩失败，系统会保持原消息并记录告警，不影响主 Agent 循环。

### 验证方法
- `python3 -m py_compile minicode/cybernetic_orchestrator.py minicode/agent_loop.py tests/test_cybernetic_integration.py` — 语法检查通过。
- 高紧急度预测测试确认 `run_cycle()` 被调用。
- 验证压缩后的消息同步回 Agent 消息列表和 `ContextManager.messages`。
- 当前环境没有安装 `pytest`，因此未运行 pytest 命令。

## ✅ 缺陷 8 已修复：PID 缺少 conditional integration

### 修改的文件
- `minicode/feedback_controller.py:133-138` — 外层 `PIDController` 在误差方向穿越 0 时清零积分项，再累积当前误差。
- `minicode/context_cybernetics.py:249-253` — `ContextPIDController` 使用相同的条件积分逻辑。
- `tests/test_feedback_controller.py:119-127` — 增加外层 PID 误差反转测试。
- `tests/test_context_cybernetics.py:157-167` — 增加上下文 PID 误差反转测试。
- `fix_report.md` — 记录缺陷 8 的修改与验证结果。

### 数据流变化

修复前：
```text
PID.compute()
  └──→ error * dt 累积到 integral
          └──→ 即使误差穿越 0，旧方向积分仍然保留
                  └──→ 输出短暂滞后或过冲
```

修复后：
```text
PID.compute()
  ├──→ 计算当前 error
  ├──→ 检查 error * previous_error < 0
  │      └──→ 清零 integral
  ├──→ 累积当前方向误差
  └──→ clamp anti-windup → PID output
```

### 新的数据流图
```text
历史误差 ───────┐
                ▼
当前误差 ───→ 方向穿越检测
                │
                ├── 未穿越 0 → 继续累积积分
                │
                └── 穿越 0 → 清零旧积分
                                └──→ 累积新方向误差
                                        └──→ P + I + D
```

### 验证方法
- `python3 -m py_compile minicode/feedback_controller.py minicode/context_cybernetics.py tests/test_feedback_controller.py tests/test_context_cybernetics.py` — 语法检查通过。
- 外层 PID 从正误差切换到负误差后，积分项正确重置为当前负方向误差。
- Context PID 从正误差切换到负误差后，积分项正确重置为当前负方向误差。
- 当前环境没有安装 `pytest`，因此未运行 pytest 命令。

## ✅ 缺陷 6 已修复：`SystemState.oscillation_index` 是死数据

### 修改的文件
- `minicode/feedback_controller.py:277-280` — `FeedbackController.observe()` 读取并限制 `state.oscillation_index`，与内部振荡指数融合后写入 `ControlSignal.oscillation_index`。
- `tests/test_feedback_controller.py:158-164` — 新增外部振荡指数被消费的测试。
- `fix_report.md` — 记录本次振荡数据闭环。

### 数据流变化

修复前：
```text
ContextCybernetics.to_system_state()
  └──→ SystemState.oscillation_index
          └──→ FeedbackController.observe() 不读取
                  └──→ ControlSignal 只使用内部振荡历史
```

修复后：
```text
ContextCybernetics.to_system_state()
  └──→ SystemState.oscillation_index
          └──→ FeedbackController.observe()
                  └──→ 内部振荡指数 60% + 外部振荡指数 40%
                          └──→ ControlSignal.oscillation_index
```

### 新的数据流图
```text
ContextCybernetics
  └──→ SystemState.oscillation_index ───────┐
                                            │ 40%
FeedbackController._compute_oscillation() ─┤
                                            │ 60%
                                            ▼
                              ControlSignal.oscillation_index
                                            │
                                            └──→ 下游自愈/控制逻辑
```

外部指数会先限制在 `0.0-1.0`，避免异常状态值污染控制信号。本次只解决缺陷 6 的死数据问题，未修改缺陷 7 的两个检测器统一逻辑。

### 验证方法
- `python3 -m py_compile minicode/feedback_controller.py tests/test_feedback_controller.py` — 语法检查通过。
- 使用 `SystemState(oscillation_index=0.8)` 验证首轮输出为 `0.32`，证明外部振荡指数已被消费。
- 当前环境没有安装 `pytest`，因此未运行 pytest 命令。

## ✅ 缺陷 7 已修复：存在两个独立且语义不一致的振荡检测器

### 修改的文件
- `minicode/context_cybernetics.py:578-592` — 新增 `get_direction_changes()`，输出最近压缩使用率的原始方向变化次数；`detect_oscillation()` 改为基于该原始值进行布尔阈值判断。
- `minicode/context_cybernetics.py:607-608` — `get_stats()` 输出统一的原始 `direction_changes`。
- `minicode/context_cybernetics.py:861` — `to_system_state()` 将方向变化次数归一化为 `0.0-1.0` 的 `SystemState.oscillation_index`。
- `tests/test_context_cybernetics.py:397-409` — 新增原始方向变化次数测试，并保留布尔检测兼容性测试。
- `fix_report.md` — 记录缺陷 7 的数据流统一过程。

### 数据流变化

修复前：
```text
CyberneticFeedbackLoop.detect_oscillation()
  └──→ bool
          └──→ SystemState.oscillation_index = 0.0 或 1.0

FeedbackController._compute_oscillation()
  └──→ float
          └──→ ControlSignal.oscillation_index

两个检测器输出语义不同，ContextCybernetics 的原始变化量没有向上游传递。
```

修复后：
```text
CyberneticFeedbackLoop
  └──→ get_direction_changes() → 原始次数
          ├──→ detect_oscillation() → bool 阈值兼容接口
          └──→ get_stats() → direction_changes
                  └──→ to_system_state()
                          └──→ 归一化 oscillation_index
                                  └──→ FeedbackController 融合
```

### 新的数据流图
```text
压缩使用率序列
  │
  └──→ get_direction_changes()
          └──→ direction_changes = N
                  └──→ min(1.0, N / 10.0)
                          └──→ SystemState.oscillation_index
                                  ├──→ 内部误差振荡指数 60%
                                  └──→ ContextCybernetics 振荡指数 40%
                                          └──→ ControlSignal.oscillation_index
```

旧的 `detect_oscillation()` 仍返回布尔值，保证现有调用方兼容；统一后的原始信号由 `get_direction_changes()` 提供。

### 验证方法
- `python3 -m py_compile minicode/context_cybernetics.py minicode/feedback_controller.py tests/test_context_cybernetics.py tests/test_feedback_controller.py` — 语法检查通过。
- 交替使用率序列验证 `get_direction_changes()` 返回原始次数 `4`。
- 验证 `detect_oscillation()` 仍按阈值返回 `True`。
- 当前环境没有安装 `pytest`，因此未运行 pytest 命令。
