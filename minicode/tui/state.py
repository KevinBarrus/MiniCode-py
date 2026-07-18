from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Callable

from minicode.cost_tracker import CostTracker
from minicode.permissions import PermissionManager
from minicode.session import AutosaveManager, SessionData
from minicode.state import AppState, Store
from minicode.tooling import ToolRegistry
from minicode.tui.types import TranscriptEntry
from minicode.agent_types import ChatMessage, ModelAdapter


@dataclass
class TtyAppArgs:
    """不可变的应用参数（运行时配置）"""
    runtime: dict | None # 配置对象
    tools: ToolRegistry # 工具注册表
    model: ModelAdapter # 模型适配器
    messages: list[ChatMessage] # 对话历史
    cwd: str # 工作目录
    permissions: PermissionManager # 权限管理器
    memory_manager: Any | None = None # 记忆管理器
    context_manager: Any | None = None # 上下文管理器


@dataclass
class PendingApproval:
    request: dict[str, Any]
    resolve: Callable[[dict[str, Any]], None]
    details_expanded: bool = False
    details_scroll_offset: int = 0
    selected_choice_index: int = 0
    feedback_mode: bool = False
    feedback_input: str = ""


@dataclass
class AggregatedEditProgress:
    entry_id: int
    tool_name: str
    path: str
    total: int = 1
    completed: int = 0
    errors: int = 0
    last_output: str = ""


@dataclass
class ScreenState:
    """可变的屏幕状态(UI状态)"""
    input: str = "" # 用户输入缓冲区
    cursor_offset: int = 0 # 光标位置
    transcript: list[TranscriptEntry] = field(default_factory=list) # 对话记录
    transcript_scroll_offset: int = 0 # 滚动偏移
    transcript_revision: int = 0
    selected_slash_index: int = 0 # 斜杠命令选择索引
    status: str | None = None # 状态消息
    active_tool: str | None = None # 当前活跃工具
    recent_tools: list[dict[str, str]] = field(default_factory=list) # 最近使用工具
    history: list[str] = field(default_factory=list) # 历史命令
    history_index: int = 0 # 历史索引
    history_draft: str = ""
    next_entry_id: int = 1
    pending_approval: PendingApproval | None = None # 待审批请求
    is_busy: bool = False # 是否忙碌
    session: SessionData | None = None # 会话数据
    autosave: AutosaveManager | None = None # 自动保存管理器
    app_state: Store[AppState] | None = None # 状态管理器
    cost_tracker: CostTracker | None = None # 开销追踪器
    agent_thread: Any = None # Agent 线程
    agent_result: dict | None = None # Agent 结果
    agent_lock: Any = None # Agent 锁
    tool_start_time: float | None = None # 工具开始使用时间
