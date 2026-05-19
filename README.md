# MiniCode — Cybernetic AI Coding Agent

> **Terminal-first AI coding assistant with closed-loop self-regulation**
>
> 钱学森工程控制论驱动 · 15+ 自适应控制器 · 718 tests
>
> [![Tests](https://img.shields.io/badge/tests-718%20passed-brightgreen)]()
> [![Python](https://img.shields.io/badge/python-3.12-blue)]()

MiniCode is a terminal AI coding agent. It reads your codebase, executes tools, and writes code — like Claude Code or Aider. What makes it different: **it regulates itself**.

Every coding agent hits the same walls — context overflow, runaway costs, tool errors, irrelevant memory. MiniCode uses classic **engineering cybernetics** (PID loops, Kalman filters, feedback control) to detect these problems and auto-correct in real time. No human intervention needed.

---

## Quick Start

```bash
git clone https://github.com/QUSETIONS/MiniCode-Python.git
cd MiniCode-Python
pip install -e .
python -m minicode.main
```

Mock mode (no API key):
```bash
MINI_CODE_MODEL_MODE=mock python -m minicode.main
```

---

## Architecture: The Cybernetic Loop

MiniCode wraps the LLM in a **Sense → Predict → Control → Act** feedback loop:

```
                    ┌──────────────────────────┐
   User Input ───→  │      Agent Loop          │  ───→ Response
                    │  (run_agent_turn)         │
                    └──────────┬───────────────┘
                               │
         ┌─────────────────────┼─────────────────────┐
         │                     │                     │
    ┌────▼─────┐         ┌────▼─────┐          ┌────▼─────┐
    │  SENSE   │         │ CONTROL  │          │   ACT    │
    │ sensors  │────────→│ PID ×4   │─────────→│ tool     │
    │ Kalman×5 │         │ feedback │          │ budget   │
    │ metrics  │         │ adaptive │          │ compact  │
    └──────────┘         └──────────┘          └──────────┘
```

### What gets auto-regulated

| Problem | Controller | Action |
|---------|-----------|--------|
| Context near limit | ContextPIDController | Auto-compaction, strategy selection |
| Tool errors spiking | SelfHealingEngine | Safe mode, reduce concurrency |
| Cost exceeding budget | BudgetPIDController | Tighten token budget |
| Agent oscillating | FeedbackController | Reduce parallelism, dampen PID |
| Task stalling | ProgressController | Switch strategy, narrow scope |
| Memory irrelevant | DomainClassifier + Reranker | Domain-filter, LLM-curate top-3 |

---

## Key Features

### Smart Agents
- **15 cybernetic controllers** — your agent doesn't just run; it watches itself
- **Dual-PID loop** — context PID (inner) + feedback PID (outer), closed-loop stability
- **Kalman state estimation** — 5 hidden variables estimated from observable outputs
- **Self-healing** — 8 fault types auto-detected and recovered
- **Feedforward pre-configuration** — intent detection → pre-emptive tool/timeout/token tuning

### Memory That Learns
- Remembers your project conventions across sessions
- **Domain-aware retrieval**: auto-detects frontend/backend/database/devops context
- **3-layer retrieval pipeline**: BM25 → LLM Reranker → Spreading Activation
- **Auto-curation**: background agent consolidates, de-duplicates, validates, and links memories
- **Multi-tier storage**: WORKING → SHORT_TERM → LONG_TERM → ARCHIVAL

### Terminal Experience
- TUI-based interactive mode
- Tool calling with concurrent execution
- Session persistence and recovery
- Permission-gated local operations
- MCP server integration

---

## How It Works

```
$ python -m minicode.main

> Add a login form component with email validation

[MiniCode senses: frontend task, React codebase]
[Feedforward: pre-configures token budget=6000, timeout=45s]
[Context PID: usage at 45%, no compaction needed]
[Memory: domain classifier → frontend memories → LLM reranker → 3 curated memories injected]
[Agent: generates Login.tsx with react-hook-form + zod]
[Verification: runs tests, all pass]
[Feedback: pattern reinforced, memory utility boosted]
[Cost PID: $0.03 spent, well within budget]
```

**The agent doesn't just respond — it manages itself.**

---

## Memory Retrieval Pipeline

One of MiniCode's most advanced subsystems. Traditional agents inject memories by simple keyword search. MiniCode runs a full adaptive pipeline:

```
Task + Files
    │
    ▼
Domain Classification (9 domains, 60+ file extension mappings)
    │
    ▼
BM25 + Domain Weight (bm25×0.7 + jaccard×0.3)
    + Query Reformulation fallback
    + Vector Search fusion (optional, RRF)
    │
    ▼
LLM Reranker (curates top-15 → top-3 with reasoning)
    │
    ▼
Spreading Activation (related_to graph, depth=1)
    │
    ▼
Adaptive Injection (context-pressure-aware cooldown)
    │
    ▼
System Prompt
```

### Ablation Study: 80 memories × 20 queries × 5 domains

| Configuration | P@3 | R@5 | Noise |
|-------------|-----|-----|-------|
| BM25 (baseline) | 0.350 | 0.362 | 65.0% |
| + Domain Weight | 0.383 | 0.446 | 42.0% |
| + Query Expansion | 0.450 | 0.496 | 38.0% |
| + Reranker (Full) | **0.717** | **0.704** | **6.7%** |

**2.05× precision improvement, 58% noise reduction.**

---

## Controller Matrix

| Controller | Type | What it does |
|-----------|------|-------------|
| ContextPIDController | PID | Usage → compaction strength |
| BudgetPIDController | PID | Cost rate → token budget |
| FeedbackController | Dual-PID | System state → 13-dim control signal |
| AdaptivePIDTuner | Self-tuning | Auto-tunes PID every 20 turns |
| StateObserver | Kalman ×5 | Hidden state from observables |
| FeedforwardController | Preemptive | Intent → tool config |
| PredictiveController | Forecast | Time-series → proactive actions |
| DecouplingController | RGA | Multi-variable coupling analysis |
| SelfHealingEngine | Recovery | 8 fault types auto-heal |
| StabilityMonitor | Health | 6-dim health scoring |
| CyberneticSupervisor | Aggregate | Global risk view |
| ProgressController | Stall detect | Strategy suggestions |
| MemoryInjectionController | PID | Controls injection rate |
| ModelSelectionController | Router | Risk/cost model selection |
| DomainClassifier | Classifier | 9 domains from file extensions |

---

## Project Structure

```
minicode/
├── agent_loop.py              # Main agent loop
├── cybernetic_orchestrator.py # 15-controller facade
├── feedback_controller.py     # Dual-PID outer loop
├── context_cybernetics.py     # 7-layer context control
├── cost_control.py            # Budget PID
├── self_healing_engine.py     # Fault detection + recovery
├── memory.py                  # BM25 search, multi-tier storage
├── memory_pipeline.py         # Unified read/write/maintain
├── memory_reranker.py         # LLM curation
├── memory_injector.py         # PID-controlled injection
├── memory_curator_agent.py    # Background optimization
├── domain_classifier.py       # File ext → domain
├── agent_reflection.py        # Post-task TaskContext
├── progress_controller.py     # Stall detection
└── ...

tests/                         # 718 tests across 30+ files
docs/
├── memory_theory.md           # V(m,t,c) + Lyapunov + info bounds
└── CODE_WIKI.md
```

---

## Configuration

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

## MiniCode Ecosystem

| Repo | Role |
|------|------|
| [MiniCode](https://github.com/LiuMengxuan04/MiniCode) | Main project |
| [MiniCode-Python](https://github.com/QUSETIONS/MiniCode-Python) | Python (this repo) |
| [MiniCode-rs](https://github.com/harkerhand/MiniCode-rs) | Rust |

---

## Theory

MiniCode's control loop is not heuristic — it's mathematically grounded:

- **Lyapunov stability**: V̇ = -(kp/m)·e² < 0, proving PID convergence
- **Memory value function**: V(m,t,c) = relevance × freshness × utility
- **Kalman optimality**: minimum-variance unbiased state estimation
- **RRF fusion**: reciprocal rank fusion of BM25 + vector results

See [`docs/memory_theory.md`](docs/memory_theory.md) for the full formal treatment.

---

## Acknowledgments

- 钱学森《工程控制论》(Engineering Cybernetics, 1954)
- Wiener, *Cybernetics* (1948)
- Mem0, Letta/MemGPT, True Memory
