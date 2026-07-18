from __future__ import annotations

import threading
from pathlib import Path
from typing import Any

from minicode.cost_tracker import CostTracker
from minicode.history import load_history_entries
from minicode.permissions import PermissionManager
from minicode.session import (
    AutosaveManager,
    SessionData,
    create_new_session,
    format_session_list,
    format_session_resume,
    get_latest_session,
    list_sessions,
    load_session,
    save_session,
)
from minicode.state import create_app_store
from minicode.tui.state import PendingApproval, ScreenState, TtyAppArgs
from minicode.tui.tool_lifecycle import _bump_transcript_revision
from minicode.tui.types import TranscriptEntry


def handle_session_listing(cwd: str, list_sessions_only: bool) -> bool:
    if not list_sessions_only:
        return False
    sessions = list_sessions()
    print(format_session_list(sessions))
    return True


def load_or_create_session(cwd: str, resume_session: str | None) -> SessionData:
    workspace = str(Path(cwd).resolve())
    if resume_session:
        # 场景1：--resume latest
        if resume_session == "latest":
            session = get_latest_session(workspace=workspace)
            if session:
                print(format_session_resume(session))
                return session # 恢复最新会话
            print("No previous session found for this workspace.")
            return create_new_session(workspace=workspace) # 如果没有，就创建新会话

        # 场景2：--resume <session-id>
        session = load_session(resume_session)
        if not session:
            raise FileNotFoundError(f"Session '{resume_session}' not found.")
        print(format_session_resume(session))
        return session

    # 场景3：全新启动
    session = get_latest_session(workspace=workspace)
    if session:
        print(f"Previous session found: {session.session_id[:8]}")
        print("Use --resume to continue, or starting fresh session.")
        return create_new_session(workspace=workspace)

    return create_new_session(workspace=workspace)


def build_tty_runtime_state(
    runtime: dict | None,
    tools: Any,
    model: Any,
    messages: list[Any],
    cwd: str,
    permissions: PermissionManager,
    session: SessionData,
    memory_manager: Any | None = None,
    context_manager: Any | None = None,
) -> tuple[TtyAppArgs, ScreenState]:
    # 1.创建 TtyAppArgs
    args = TtyAppArgs(
        runtime=runtime,
        tools=tools,
        model=model,
        messages=messages,
        cwd=cwd,
        permissions=permissions,
        memory_manager=memory_manager,
        context_manager=context_manager,
    )

    # 2.创建 ScreenState
    state = ScreenState(
        history=load_history_entries(), # 加载历史命令
        session=session,
        autosave=AutosaveManager(session), # 自动保存管理器
        app_state=create_app_store({ # 全局状态
            "session_id": session.session_id,
            "workspace": cwd,
            "model": runtime.get("model", "unknown") if runtime else "unknown",
        }),
        cost_tracker=CostTracker(), # 成本跟踪
    )
    state.history_index = len(state.history)

    # 3.如果恢复会话，加载之前的 messages 和 transcript
    if session.messages:
        args.messages.clear()
        args.messages.extend(session.messages)
        for entry_data in session.transcript_entries:
            state.transcript.append(TranscriptEntry(**entry_data))
        _bump_transcript_revision(state)
        print(f"Restored {len(session.messages)} messages, {len(state.transcript)} transcript entries.")

    return args, state


def install_permission_prompt(
    args: TtyAppArgs,
    state: ScreenState,
    rerender: Any,
) -> tuple[threading.Event, dict[str, Any], Any]:
    approval_event = threading.Event() # 线程同步事件
    approval_result: dict[str, Any] = {} # 存储用户决策

    def _permission_prompt_handler(request: dict[str, Any]) -> dict[str, Any]:
        """权限提示处理器，会被 PermissionManager 调用"""
        nonlocal approval_result
        # 1.创建待审批 UI 状态
        state.pending_approval = PendingApproval(
            request=request,
            resolve=lambda r: None,
        )
        # 2.触发渲染，显示权限提示
        rerender()

        # 3.等待用户决策（阻塞当前线程）
        approval_event.clear()
        approval_event.wait() # 阻塞，直到用户做出选择

        # 4.获取结果并清除 UI
        result = approval_result.copy()
        state.pending_approval = None
        return result

    # 5.注入到 PermissionManager
    args.permissions.prompt = _permission_prompt_handler
    return approval_event, approval_result, _permission_prompt_handler


def finalize_tty_session(args: TtyAppArgs, state: ScreenState) -> None:
    if not state.session:
        return

    # 保存 messages（对话历史）
    state.session.messages = list(args.messages)

    # 保存 transcript（对话记录）
    state.session.transcript_entries = [
        {
            "id": e.id,
            "kind": e.kind,
            "toolName": e.toolName,
            "status": e.status,
            "body": e.body,
            "collapsed": e.collapsed,
            "collapsedSummary": e.collapsedSummary,
            "collapsePhase": e.collapsePhase,
        }
        for e in state.transcript
    ]

    # 保存历史
    state.session.history = state.history

    # 保存其它元数据
    state.session.permissions_summary = args.permissions.get_summary()
    state.session.skills = args.tools.get_skills()
    state.session.mcp_servers = args.tools.get_mcp_servers()

    # 持久化到磁盘
    if state.autosave:
        state.autosave.force_save() # 真正写入文件
    else:
        save_session(state.session) # 或者直接保存

    print(f"\nSession saved: {state.session.session_id[:8]}")
