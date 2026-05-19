# MiniCode Python — Cybernetic Agent Memory

> **Closed-Loop Cybernetic Memory for LLM Agents**
>
> 钱学森工程控制论 × 记忆检索优化 × DDD 领域驱动
>
> [![Tests](https://img.shields.io/badge/tests-718%20passed-brightgreen)]()
> [![Python](https://img.shields.io/badge/python-3.12-blue)]()

---

## 核心贡献

传统 Agent 记忆系统使用**静态检索**（固定 top-K BM25 或向量相似度）。我们首次将**工程控制论**（PID 闭环 + Kalman 滤波 + Lyapunov 稳定性）形式化地应用于 Agent 记忆管理，实现了一个**自适应、可证明稳定、具备多层语义的检索管线**。

| 指标 | 纯 BM25 | 我们的完整管线 | 提升 |
|------|---------|--------------|------|
| P@3 | 0.350 | **0.717** | **2.05×** |
| R@5 | 0.362 | **0.704** | **1.94×** |
| MRR | 0.713 | **1.000** | **+40%** |
| 跨域噪音 | 65.0% | **6.7%** | **-58.3%** |

> 消融实验：80 条记忆 × 20 条查询 × 5 领域（frontend/backend/database/devops/testing）

---

## 理论框架

### 记忆价值函数

$$V(m, t, c) = \text{relevance}(m, t) \times \text{freshness}(m) \times \text{utility}(m, c)$$

| 分量 | 定义 | 实现 |
|------|------|------|
| relevance(m,t) | BM25 评分 + 领域 Jaccard 软混合 | `final_score = bm25 × 0.7 + domain_jaccard × 0.3` |
| freshness(m) | exp(-age_days / 30) | 指数衰减，τ = 30 天 |
| utility(m,c) | 1 + α·ln(1 + usage_count) | 使用次数的对数边际收益 |

### PID 稳定性 —— Lyapunov 证明

考虑上下文 PID 控制器：e(t) = usage(t) - 0.70（设定点）

构造 Lyapunov 函数：VL(e, ∫e) = ½e² + (ki/2)(∫e)²

则 V̇L = -(kp/m)·e² < 0（当 kp > 0），系统**渐近稳定**：e(t) → 0 as t → ∞。

### 自适应冷却

$$\tau_{\text{cool}}(c) = \tau_{\text{base}} \times (1 - \text{context\_pressure})$$

上下文压力高 → 冷却短 → 注入更积极。钳位在 [5s, 120s]。

### 扩散激活

$$a_j = \sum_i a_i \times 0.5 \times \text{Jaccard}(m_i, m_j)$$

通过 `related_to` 图传播，depth=1。实现 Hebb 式联想记忆。

### 信息保持界

跨层级记忆压缩：I(m_arch) ≈ I(m) - ε，其中 ε = -log₂(len(original)/len(summarized))

---

## 系统架构

```
                         ┌──────────────────────────────┐
                         │   CyberneticOrchestrator     │
                         │   (15+ controllers)          │
                         └──────────┬───────────────────┘
                                    │
         ┌──────────────────────────┼──────────────────────────┐
         │                          │                          │
    ┌────▼─────┐            ┌──────▼──────┐           ┌───────▼──────┐
    │  PID ×4  │            │  Kalman ×5  │           │  Feedforward │
    │ context  │            │  state      │           │  predictive  │
    │ cost     │            │  observer   │           │  decoupling  │
    │ feedback │            │             │           │              │
    │ adaptive │            │             │           │              │
    └──────────┘            └─────────────┘           └──────────────┘
                                    │
                         ┌──────────▼───────────┐
                         │   MemoryPipeline     │
                         │   (unified facade)   │
                         └──────────┬───────────┘
                                    │
              ┌─────────────────────┼─────────────────────┐
              │                     │                     │
    ┌─────────▼────────┐  ┌────────▼────────┐  ┌─────────▼────────┐
    │   READ           │  │   WRITE         │  │   MAINTAIN       │
    │ Domain→BM25      │  │ ReflectionEngine│  │ CuratorAgent     │
    │ +Vector(RRF)     │  │ →TaskContext    │  │ consolidate      │
    │ →Reranker→Inject │  │ →MemoryManager  │  │ validate/promote │
    └──────────────────┘  └─────────────────┘  └──────────────────┘
```

### 记忆检索管线（3 层）

```
Task + Files
    │
    ▼
Layer 1 ─ 语义理解
    DomainClassifier (9 领域, 60+ 文件后缀映射)
    BM25 + Domain Weight (final = bm25×0.7 + jaccard×0.3)
    Query Reformulation (低分时自动改写)
    Vector Search + RRF Fusion (可选)
    Memory Value Scoring (V = rel × fresh × util)
    │
    ▼
Layer 2 ─ 策展精选
    LLM Reranker (Haiku-level, LRU cached, 60s TTL)
    top-15 → curated top-3
    矛盾检测 + 上下文摘要
    │
    ▼
Layer 3 ─ 联想注入
    Spreading Activation (related_to graph, depth=1)
    Adaptive Cooldown (context-pressure aware)
    PID-controlled injection rate
    │
    ▼
System Prompt
```

### 多层存储架构

| 层级 | 保留期 | 压缩 | 晋升规则 |
|------|--------|------|---------|
| WORKING | 当前会话 | 无 | 访问时自动 |
| SHORT_TERM | < 7 天 | 无 | usage ≥ 5 ∧ age > 7d |
| LONG_TERM | < 30 天 | 无 | — |
| ARCHIVAL | 永久 | 首句摘要 | age > 30d 未访问 |

---

## Memory Pipeline API

**设计原则：一个类，四个方法，完整生命周期。**

```python
from minicode.memory_pipeline import MemoryPipeline

pipeline = MemoryPipeline(memory_manager)
pipeline.initialize(model_adapter, enable_vector=True)

# 任务开始 —— 检索 + 注入
memories = pipeline.read("Add login form", ["src/Login.tsx"])
messages  = pipeline.inject("Add login form", ["src/Login.tsx"], messages)

# 任务结束 —— 持久化 + 反馈
pipeline.write("Add login form", execution_trace)
pipeline.feedback(success=True, injected_memory_ids=[...])

# 后台维护 —— 每 ~10 个任务
report = pipeline.maintain()
```

### 消融实验 —— 逐组件贡献

```
Config                      P@3      R@5      MRR      Noise
────────────────────────────────────────────────────────────
C0: BM25 (baseline)        0.350    0.362    0.713    65.0%
C1: + Domain Weight        0.383    0.446    0.844    42.0%
C2: + Query Expansion      0.450    0.496    0.858    38.0%
C3: + Reranker (Full)      0.717    0.704    1.000     6.7%
```

**结论**：Reranker 贡献 73% 的精度提升（+0.267 P@3）。Domain + Expansion 在零 LLM 成本下削减 27% 噪音。完整管线精度 2.05× 基准。

---

## 控制论控制器矩阵

| 控制器 | 类型 | 作用 |
|--------|------|------|
| ContextPIDController | PID | 上下文压力 → 压缩强度 |
| CostControlLoop (BudgetPID) | PID | 成本速率 → 预算乘数 |
| FeedbackController (双 PID) | PID ×2 | 系统状态 → 13 维 ControlSignal |
| AdaptivePIDTuner | 自适应 | 每 20 轮自动调参 |
| StateObserver | Kalman ×5 | 隐藏状态估计（负载/错误/压力/掌握度/退化） |
| FeedforwardController | 前馈 | Intent → 预配置 |
| PredictiveController | 预测 | 时序预测 → 主动动作 |
| DecouplingController | 解耦 | 多变量 RGA 耦合分析 |
| SelfHealingEngine | 自愈 | 8 种故障类型的自动恢复 |
| StabilityMonitor | 监测 | 6 维健康评分 + 异常检测 |
| CyberneticSupervisor | 监督 | 全局风险聚合 |
| ProgressController | 进度 | 停滞检测 + 策略建议 |
| MemoryInjectionController | 记忆 | PID 控制注入模式 |
| ModelSelectionController | 模型 | 风险/成本自适应选模 |
| DomainClassifier | 分类 | 9 领域 60+ 后缀自动推导 |

---

## 目录结构

```
minicode/
├── memory.py                # 核心：BM25, MemoryTier, MemoryEntry, 索引
├── memory_pipeline.py       # 统一管线：read/write/inject/maintain
├── memory_reranker.py       # LLM 策展：top-15 → top-3 + 摘要
├── memory_curator_agent.py  # 后台策展：合并/校验/晋升/关联
├── memory_injector.py       # PID 控制的记忆注入
├── domain_classifier.py     # 领域分类：60+ 后缀映射
├── vector_memory.py         # 向量检索（可选）
├── agent_reflection.py      # 自省引擎 → TaskContext
├── cybernetic_orchestrator.py # 15+ 控制器外观
├── feedback_controller.py   # 双 PID 外环 + ControlSignal
├── context_cybernetics.py   # 7 层上下文控制论
├── cost_control.py          # 预算 PID
├── self_healing_engine.py   # 8 种故障自愈
├── agent_loop.py            # Agent 主循环
└── ...

tests/
├── test_domain_memory.py    # 领域分类 + 查询扩展
├── test_memory_reranker.py  # Reranker 全场景
├── test_memory_curator.py   # Curator 全场景
├── test_feedback_controller.py
├── test_feedforward_controller.py
├── test_cybernetics_concurrency.py  # 并发压测
├── test_cybernetics_e2e.py          # E2E 控制链
└── ...

docs/
└── memory_theory.md         # 形式化理论：V(m,t,c) + Lyapunov + 信息保持

py-src/scripts/
├── ablation_study.py        # 消融实验（LaTeX 表格输出）
├── benchmark_memory.py      # 全量 benchmark
└── demo_memory_reranker.py  # 效果对比 demo
```

---

## 快速开始

```bash
git clone https://github.com/QUSETIONS/MiniCode-Python.git
cd MiniCode-Python

# 安装
pip install -e .

# 运行
python -m minicode.main

# 测试
pytest  # 718 passed, 2 skipped

# Mock 模式（无需 API key）
MINI_CODE_MODEL_MODE=mock python -m minicode.main
```

## 配置

`~/.mini-code/settings.json`:
```json
{
  "model": "claude-sonnet-4-20250514",
  "env": {
    "ANTHROPIC_AUTH_TOKEN": "your-token"
  }
}
```

---

## MiniCode 生态

| 仓库 | 角色 |
|------|------|
| [MiniCode](https://github.com/LiuMengxuan04/MiniCode) | 主项目入口 |
| [MiniCode-Python](https://github.com/QUSETIONS/MiniCode-Python) | Python 实现（本仓库） |
| [MiniCode-rs](https://github.com/harkerhand/MiniCode-rs) | Rust 实现 |

---

## 致谢

- 钱学森《工程控制论》(Engineering Cybernetics, 1954)
- Wiener, N. *Cybernetics: or Control and Communication in the Animal and the Machine* (1948)
- Mem0 / Letta (MemGPT) / True Memory 等记忆系统的开创性工作
- SCL (Structured Cognitive Loop) R-CCAM 架构
