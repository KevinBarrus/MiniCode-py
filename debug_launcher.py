"""PyCharm 调试入口 — 跳过 TUI/stdin，直接调用核心逻辑。

用法：在 PyCharm 中右键这个文件 → Debug。
在 agent_loop.py 等核心文件中点红点打断点即可。
"""

import sys
from pathlib import Path

# 强制 CLI 模式（防止任何地方意外进入 TUI）
sys.stdin.isatty = lambda: False

from minicode.agent_loop import run_agent_turn
from minicode.config import load_runtime_config
from minicode.model_registry import create_model_adapter
from minicode.permissions import PermissionManager
from minicode.prompt import build_system_prompt
from minicode.tools import create_default_tool_registry
from minicode.context_manager import ContextManager
from minicode.memory import MemoryManager

cwd = str(Path.cwd())

# 加载配置
runtime = load_runtime_config(cwd)
tools = create_default_tool_registry(cwd, runtime=runtime)
permissions = PermissionManager(cwd)
model = create_model_adapter(
    model=runtime.get("model", ""),
    tools=tools,
    runtime=runtime,
)
context_mgr = ContextManager(model=runtime.get("model", "default"))
memory_mgr = MemoryManager(project_root=Path(cwd))

# 构建初始消息
messages = [
    {
        "role": "system",
        "content": build_system_prompt(
            cwd,
            permissions.get_summary(),
            {
                "skills": tools.get_skills(),
                "mcpServers": tools.get_mcp_servers(),
                "memory_context": memory_mgr.get_relevant_context(),
            },
        ),
    },
    {
        "role": "user",
        "content": "你好，请用一句话介绍你自己",  # ← 改这行来测试不同的用户输入
    },
]

# === 在这里打断点，然后 F7 进入 run_agent_turn ===
result = run_agent_turn(
    model=model,
    tools=tools,
    messages=messages,
    cwd=cwd,
    permissions=permissions,
    context_manager=context_mgr,
    runtime=runtime,
)

print("\n=== 最终响应 ===")
last = result[-1] if result else {}
print(last.get("content", "(无响应)"))
print(f"\n总计 {len(result)} 条消息")
