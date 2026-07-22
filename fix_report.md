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
