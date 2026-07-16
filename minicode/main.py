from __future__ import annotations

import argparse
import sys
import os
from pathlib import Path

from minicode.agent_loop import run_agent_turn
from minicode.cli_commands import try_handle_local_command
from minicode.config import load_runtime_config
from minicode.history import load_history_entries, save_history_entries
from minicode.local_tool_shortcuts import parse_local_tool_shortcut
from minicode.manage_cli import maybe_handle_management_command
from minicode.model_registry import create_model_adapter
from minicode.permissions import PermissionManager
from minicode.prompt import build_system_prompt
from minicode.tools import create_default_tool_registry
from minicode.tooling import ToolContext
from minicode.tui.transcript import format_transcript_text
from minicode.tui.types import TranscriptEntry
from minicode.tty_app import run_tty_app
from minicode.workspace import resolve_tool_path


def _handle_local_command(user_input: str, tools) -> str | None:
    if user_input == "/tools":
        return "\n".join(f"{tool.name}: {tool.description}" for tool in tools.list())
    local_result = try_handle_local_command(user_input, tools=tools, cwd=str(Path.cwd()))
    return local_result


def _render_banner(runtime: dict | None, cwd: str, permission_summary: list[str], counts: dict[str, int]) -> str:
    model = runtime["model"] if runtime else "unconfigured"
    lines = [
        "╔══════════════════════════════════════════════════════════╗",
        "║  🤖 MiniCode Python - Your Terminal Coding Assistant    ║",
        "╠══════════════════════════════════════════════════════════╣",
        f"║  Model: {model:<46} ║",
        f"║  CWD: {cwd:<50} ║",
    ]
    if permission_summary:
        for perm in permission_summary[:2]:  # 只显示前2个权限摘要
            lines.append(f"║  {perm:<60} ║")
    lines.append("╠══════════════════════════════════════════════════════════╣")
    lines.append(
        f"║  📊 Skills: {counts['skillCount']:>2} | MCP Servers: {counts['mcpCount']:>2} | "
        f"Transcript: {counts['transcriptCount']:>3} ║"
    )
    lines.append("╚══════════════════════════════════════════════════════════╝")
    return "\n".join(lines)


def _render_quick_start() -> str:
    """显示快速入门指南"""
    return """
💡 Quick Start Guide:
  📝 Edit files:     edit_file.py or patch_file.py
  🔍 Search code:    /grep <pattern> or grep_files tool
  🏃 Run commands:   /cmd <command> or run_command tool
  🧠 Think deeply:   Use sequential_thinking MCP tool
  📚 View skills:    /skills
  ❓ Get help:       /help

🚀 Try saying:
  "帮我分析这个项目的结构"
  "用 TDD 方式实现 XX 功能"
  "系统性地调试这个 bug"
  "帮我写个技术方案"
"""


def _append_transcript(transcript: list[TranscriptEntry], **kwargs) -> None:
    transcript.append(TranscriptEntry(id=len(transcript) + 1, **kwargs))


def _make_cli_permission_prompt():
    """Create a simple CLI-based permission prompt for non-TTY fallback."""
    def _prompt(request: dict) -> dict:
        # 1.打印权限请求摘要
        print(f"\n{request.get('summary', 'Permission Request')}")

        # 2.如果有多个选项，显示选项
        choices = request.get("choices", [])
        if choices:
            for choice in choices:
                print(f"  [{choice.get('key', '')}] {choice.get('label', '')}")
            answer = input("Choose: ").strip()
            for choice in choices:
                if answer == choice.get("key"):
                    return {"decision": choice.get("decision", "allow_once")}
        # 简单的 y/n 确认
        answer = input("Allow? (y/n): ").strip().lower()
        return {"decision": "allow_once" if answer in ("y", "yes") else "deny_once"}
    return _prompt


def _configure_stdio_for_unicode() -> None:
    for stream in (sys.stdout, sys.stderr):
        reconfigure = getattr(stream, "reconfigure", None)
        if reconfigure is not None:
            try:
                reconfigure(encoding="utf-8", errors="replace")
            except Exception:
                pass


def _save_transcript_file(cwd: str, permissions, transcript: list[TranscriptEntry], output_path: str) -> str:
    target = resolve_tool_path(ToolContext(cwd=cwd, permissions=permissions), output_path, "write")
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(format_transcript_text(transcript), encoding="utf-8")
    return str(target)

def main() -> None:
    # Unicode 配置，Windows 默认编码可能不是 utf-8，会导致中文输出乱码
    _configure_stdio_for_unicode()

    # 定义参数
    parser = argparse.ArgumentParser(
        description="MiniCode Python - A lightweight terminal coding assistant",
        add_help=True,
    )
    parser.add_argument(
        "--resume",
        nargs="?",
        const="latest",
        default=None,
        metavar="SESSION_ID",
        help="Resume a previous session (use 'latest' or session ID)",
    )
    parser.add_argument(
        "--list-sessions",
        action="store_true",
        help="List all saved sessions and exit",
    )
    parser.add_argument(
        "--session",
        default=None,
        metavar="SESSION_ID",
        help="Start with a specific session ID",
    )
    parser.add_argument(
        "--install",
        action="store_true",
        help="Run the interactive installer",
    )
    parser.add_argument(
        "--validate-config",
        "--valid-config",
        action="store_true",
        help="Validate configuration and exit",
    )
    parser.add_argument(
        "--log-level",
        default="WARNING",
        choices=["DEBUG", "INFO", "WARNING", "ERROR"],
        help="Set logging level (default: WARNING)",
    )

    # 解析参数：如果有非预定义参数，且全都以 -- 开头，就报错，否则可能是子命令的参数，不报错，后续路由到 management command 处理器
    args, remaining_argv = parser.parse_known_args()
    if remaining_argv and not any(not arg.startswith("--") for arg in remaining_argv):
        parser.error(f"unrecognized arguments: {' '.join(remaining_argv)}")

    # 日志初始化
    from minicode.logging_config import setup_logging
    setup_logging(level=args.log_level)

    # 特殊命令处理
    # 验证配置
    if args.validate_config:
        from minicode.config import format_config_diagnostic
        print(format_config_diagnostic())
        return
    
    # 运行安装程序
    if args.install:
        from minicode.install import main as install_main
        install_main()
        return

    # 处理管理命令
    cwd = str(Path.cwd())
    argv = remaining_argv

    # 在交由 management commands 之前过滤出不以 -- 开头的预定义参数
    management_argv = [a for a in argv if not a.startswith("--")]
    if maybe_handle_management_command(cwd, management_argv):
        return

    runtime = None
    try:
        runtime = load_runtime_config(cwd)
    except Exception as e:  # noqa: BLE001
        runtime = None
        print(
            f"⚠️  Warning: Failed to load runtime config: {e}\n",
            file=sys.stderr,
        )
        print(
            "🔧 How to fix this:\n"
            "  1. Set your model name: export ANTHROPIC_MODEL=claude-sonnet-4-20250514\n"
            "  2. Set your API key: export ANTHROPIC_API_KEY=sk-ant-...\n"
            "  3. Or edit ~/.mini-code/settings.json:\n"
            '     {"model": "claude-sonnet-4-20250514", "env": {"ANTHROPIC_API_KEY": "sk-ant-..."}}\n'
            "  4. Restart MiniCode\n\n"
            "📖 For more info: https://github.com/QUSETIONS/MiniCode-Python\n"
            "   Falling back to mock model for now...\n",
            file=sys.stderr,
        )

    prompt_handler = _make_cli_permission_prompt() if sys.stdin.isatty() else None
    tools = create_default_tool_registry(cwd, runtime=runtime)
    permissions = PermissionManager(cwd, prompt=prompt_handler)
    
    # 创建模型适配器，统一接口，屏蔽底层 API 差异
    force_mock = runtime is None
    model = create_model_adapter(
        model=runtime.get("model", "") if runtime else "",
        tools=tools,
        runtime=runtime,
        force_mock=force_mock,
    )
    
    # 初始化上下文管理器，管理上下文窗口
    from minicode.context_manager import ContextManager
    from minicode.logging_config import get_logger
    logger = get_logger("main")
    context_mgr = None
    if runtime:
        context_mgr = ContextManager(model=runtime.get("model", "default"))
        logger.info("Context manager initialized for model: %s", runtime.get("model", "unknown"))
    
    # 初始化记忆管理器，用于跨会话知识保留
    from minicode.memory import MemoryManager
    memory_mgr = MemoryManager(project_root=Path(cwd))
    logger.info("Memory manager initialized")
    
    # 初始化用户个人资料管理器
    from minicode.user_profile import UserProfileManager
    profile_manager = UserProfileManager(cwd=cwd)
    profile_manager.load_merged() # 合并全局和项目配置
    logger.info("User profile manager initialized (global=%s, project=%s)",
                profile_manager.global_path.exists(),
                profile_manager.project_path.exists())
    
    # 初始化用于全局状态管理的存储（灵感来自 Claude Code 的 Zustand 存储）
    from minicode.state import create_app_store
    app_store = create_app_store(
        initial={
            "session_id": args.session or "new",
            "workspace": cwd,
            "model": runtime.get("model", "mock") if runtime else "mock",
        }
    )
    logger.info("Store initialized with session: %s", app_store.get_state().session_id)
    
    messages = [
        {
            "role": "system",
            "content": build_system_prompt(
                cwd,
                permissions.get_summary(),
                {
                    "skills": tools.get_skills(),
                    "mcpServers": tools.get_mcp_servers(),
                    "memory_context": memory_mgr.get_relevant_context(),  # 注入记忆
                },
            ),
        }
    ]
    history = load_history_entries()
    transcript: list[TranscriptEntry] = []

    print(
        _render_banner(
            runtime,
            cwd,
            permissions.get_summary(),
            {
                "transcriptCount": 0,
                "messageCount": len(messages),
                "skillCount": len(tools.get_skills()),
                "mcpCount": len(tools.get_mcp_servers()),
            },
        )
    )
    
    # 显示快速入门指南
    if not sys.stdin.isatty() or os.environ.get("MINI_CODE_SHOW_GUIDE", "1") == "1":
        print(_render_quick_start())
    else:
        print("")

    try:
        # 判断是否为交互式终端
        if not sys.stdin.isatty():
            # 不是交互式终端
            for raw_input in sys.stdin: # 循环读取 stdin
                user_input = raw_input.strip()
                if not user_input:
                    continue # 跳过空行

                # 1.处理 /exit 命令
                if user_input == "/exit":
                    break # 退出循环

                # 2.处理 /transcript-save 命令
                if user_input.startswith("/transcript-save "):
                    output_path = user_input[len("/transcript-save ") :].strip()
                    if not output_path:
                        print("Usage: /transcript-save <path>")
                        continue
                    saved_path = _save_transcript_file(cwd, permissions, transcript, output_path)
                    print(f"Saved transcript to {saved_path}")
                    continue

                # 3.处理 Memory 命令
                memory_result = memory_mgr.handle_user_memory_input(user_input)
                if memory_result is not None:
                    _append_transcript(transcript, kind="user", body=user_input)
                    _append_transcript(transcript, kind="assistant", body=memory_result)
                    print(memory_result)
                    continue

                # 4.处理本地命令
                local_result = _handle_local_command(user_input, tools)
                if local_result is not None:
                    _append_transcript(transcript, kind="user", body=user_input)
                    _append_transcript(transcript, kind="assistant", body=local_result)
                    print(local_result)
                    continue

                # 5.处理工具快捷方式
                shortcut = parse_local_tool_shortcut(user_input)
                if shortcut is not None:
                    _append_transcript(transcript, kind="user", body=user_input)
                    result = tools.execute(
                        shortcut["toolName"],
                        shortcut["input"],
                        context=ToolContext(cwd=cwd, permissions=permissions),
                    )
                    _append_transcript(
                        transcript,
                        kind="tool",
                        body=result.output,
                        toolName=shortcut["toolName"],
                        status="success" if result.ok else "error",
                    )
                    print(result.output)
                    continue

                # 6.调用 Agent Turn
                _append_transcript(transcript, kind="user", body=user_input)
                messages.append({"role": "user", "content": user_input})
                history.append(user_input)
                save_history_entries(history)

                # 重构 system prompt, 注入 Memory
                messages[0] = {
                    "role": "system",
                    "content": build_system_prompt(
                        cwd,
                        permissions.get_summary(),
                        {
                            "skills": tools.get_skills(),
                            "mcpServers": tools.get_mcp_servers(),
                            "memory_context": memory_mgr.get_relevant_context(query=user_input),
                        },
                    ),
                }
                permissions.begin_turn() # 开始权限追踪

                # 核心调用！
                messages = run_agent_turn(
                    model=model,
                    tools=tools,
                    messages=messages,
                    cwd=cwd,
                    permissions=permissions,
                    store=app_store,
                    context_manager=context_mgr,
                    runtime=runtime,
                )
                permissions.end_turn() # 结束权限追踪
                
                # 一轮对话结束后，将上下文使用量记录到日志中
                if context_mgr:
                    stats = context_mgr.get_stats()
                    logger.debug("After turn: %d tokens (%.0f%%)", stats.total_tokens, stats.usage_percentage)

                # 获取 assistant 响应并显示
                last_assistant = next((message for message in reversed(messages) if message["role"] == "assistant"), None)
                if last_assistant:
                    _append_transcript(transcript, kind="assistant", body=last_assistant["content"])
                    print(last_assistant["content"])
            return # 非 TTY 模式结束

        run_tty_app(
            runtime=runtime,
            tools=tools,
            model=model,
            messages=messages,
            cwd=cwd,
            permissions=permissions,
            resume_session=args.resume,
            list_sessions_only=args.list_sessions,
            memory_manager=memory_mgr,
            context_manager=context_mgr,
        )
    except KeyboardInterrupt:
        print("\n\nInterrupted by user. Shutting down gracefully...")
    finally:
        # 优雅关闭：释放所有资源
        from minicode.logging_config import get_logger
        logger = get_logger("main")
        logger.info("Shutting down...")
        
        # 处理工具（关闭 MCP 连接）
        try:
            tools.dispose() # 清理工具
            logger.info("Tools disposed successfully")
        except Exception as e:
            logger.warning("Error disposing tools: %s", e)
        
        logger.info("Shutdown complete")


if __name__ == "__main__":
    main()
