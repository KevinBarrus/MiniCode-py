# MiniCode 项目详解1：主链路 + 基础流程(main.py)

## 入口：main.py

### Phase 1 : 初始化准备

#### 1.1 Unicode 配置

```python
    # Unicode 配置
    _configure_stdio_for_unicode()
```

作用：
- 配置 stdout 和 stderr 支持 UTF-8 编码
- 防止中文输出乱码

具体实现：
```python
def _configure_stdio_for_unicode() -> None:
    for stream in (sys.stdout, sys.stderr):
        reconfigure = getattr(stream, "reconfigure", None)
        if reconfigure is not None:
            try:
                reconfigure(encoding="utf-8", errors="replace")
            except Exception:
                pass
```

getattr 是 get attribute 的缩写，表示动态获取对象的属性或方法。这里是获取 stream 对象的 reconfigure 属性，不存在则返回 None

Windows 默认编码可能不是 UTF-8，会导致中文输出乱码。通过 reconfigure(encoding="utf-8") 确保跨平台兼容性

stdout (标准输出):
- 程序的正常输出
- 例如：Agent 返回给用户的代码、分析结果等

stderr (标准错误):
- 程序的错误信息和诊断信息
- 例如：警告、错误日志、调试信息等


main.py
```python
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
```
print 函数签名：
```python
def print(*args, sep=' ', end='\n', file=None, flush=False):
    # file 参数：指定输出流
    if file is None:
        file = sys.stdout  # 默认输出到 stdout
    # 写入到指定的 file
    file.write(sep.join(args) + end)
```
这里 file = sys.stderr 是传参，表示把内容输出到 stderr 这个流中

设计原因：
- 管道分离：echo "query" | minicode-py | grep "result" 这种管道中，只有 stdout 会传给下游，stderr 会显示给用户
- 日志重定向：minicode-py > output.txt 2> error.log 可以将正常输出和错误分别保存
- 调试友好：在 IDE 中调试时，stderr 通常会以不同颜色显示


#### 1.2 参数解析

定义参数：
```python
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
```

参数说明：
| 参数 | 类型 | 作用 | 使用场景 |
| ---- | ---- | ---- | ---- |
| --resume | 可选 | 恢复之前的会话 | --resume 或 --resume <session-id> |
| --list-sessions | 标志 | 列出所有保存的会话 | 会话管理 |
| --session | 字符串 | 指定会话 ID 启动 | 指定特定会话 |
| --install | 标志 | 运行安装程序 | 首次安装 |
| --validate-config | 标志 | 验证配置并退出 | 配置检查 |
| --log-level | 字符串 | 设置日志级别 | DEBUG/INFO/WARNING/ERROR |

解析参数：
```python
    args, remaining_argv = parser.parse_known_args()
    if remaining_argv and not any(not arg.startswith("--") for arg in remaining_argv):
        parser.error(f"unrecognized arguments: {' '.join(remaining_argv)}")
```
为什么是 parse_known_args ?
- parse_known_args 允许未知参数，解析完已知参数后，返回剩余参数
- parse_args 不允许未知参数，遇到直接报错

使用 parse_known_args 可以支持动态管理命令：比如 minicode-py config set model xxx，'config set model xxx' 不是预定义的参数(add_argument 添加的才是预定义参数)，需要后续路由到 management command 处理器


| 返回值              | 含义                      |
| ---------------- | ----------------------- |
| `args`           | 成功解析的已知参数（Namespace 对象） |
| `remaining_argv` | **无法识别**的参数列表           |

例如命令行：
```bash
python script.py --known-opt value --unknown-flag pos_arg
```
返回结果中：
- args 包含 --known-opt 的值
- remaining_argv = ['--unknown-flag', 'pos_arg']

对于过滤检查条件：
```python
if remaining_argv and not any(not arg.startswith("--") for arg in remaining_argv):
    parser.error(f"unrecognized arguments: {' '.join(remaining_argv)}")
```
| 部分                         | 含义                      |
| -------------------------- | ----------------------- |
| `remaining_argv`           | 有未识别的参数                 |
| `not arg.startswith("--")` | 参数**不是** `--` 开头（即位置参数） |
| `any(...)`                 | 是否存在至少一个位置参数            |
| `not any(...)`             | **不存在**任何位置参数           |

即：有未识别参数，且这些未识别参数全都是 -- 开头的（没有位置参数），就报错

这段代码的意思是："如果有我没认出来的参数，而且这些参数全是 -- 开头的选项，那就报错；但如果里面有位置参数（不带 --），那可能是给子命令用的，先不报错，留给后面处理。"

这是支持子命令或传递参数给下游程序时的常见模式

子命令 是一种 CLI 设计模式，将不同功能模块化为独立命令。

示例：

子命令模式
```bash
minicode-py config set model claude-sonnet-4
             ↑       ↑   ↑          ↑
            子命令    子命令         参数

minicode-py session list
              ↑       ↑
            子命令  子命令
```

对比：传统参数模式
```bash
minicode-py --model claude-sonnet-4 --list-sessions
```

常见子命令模式：

`git <command> [options]`
- `git add <files>`
- `git commit -m "message"`
- `git push origin main`
- `git log --oneline`

`minicode-py <command> [options]`
- `minicode-py config set model xxx`
- `minicode-py session list`
- `minicode-py session resume <id>`

为什么用子命令：
- 模块化清晰
- 功能扩展方便
- 避免参数爆炸（如果都用 --xxx，会有几十个参数）

#### 1.3 日志初始化

```python
    # 日志初始化
    from minicode.logging_config import setup_logging
    setup_logging(level=args.log_level)
```

简单看一下 setup_logging:

```python
def setup_logging(
    level: str = "WARNING",
    log_to_file: bool = True,
    log_to_console: bool = True,
    structured: bool = False,
) -> logging.Logger:
    """配置 MiniCode 日志系统。
    
    Args:
        level: 日志级别（DEBUG/INFO/WARNING/ERROR）
        log_to_file: 是否输出到文件
        log_to_console: 是否输出到控制台
        structured: 是否使用 JSON 结构化日志格式
        
    Returns:
        配置好的根 logger
    """
    # 确保日志目录存在
    if log_to_file:
        MINI_CODE_DIR.mkdir(parents=True, exist_ok=True)
    
    # 创建根 logger
    root_logger = logging.getLogger("minicode")
    root_logger.setLevel(getattr(logging, level.upper(), logging.WARNING))
    
    # 清除已有的 handlers（避免重复）
    root_logger.handlers.clear()
    
    # 选择格式化器
    if structured:
        file_formatter = StructuredFormatter()
        console_formatter = StructuredFormatter()
    else:
        file_formatter = logging.Formatter(FILE_FORMAT)
        console_formatter = logging.Formatter(CONSOLE_FORMAT)
    
    # 文件 handler — 使用 RotatingFileHandler 防止日志无限增长
    if log_to_file:
        # RotatingFileHandler: 按大小轮转
        file_handler = logging.handlers.RotatingFileHandler(
            LOG_FILE,
            maxBytes=LOG_MAX_BYTES,
            backupCount=LOG_BACKUP_COUNT,
            encoding="utf-8",
        )
        file_handler.setLevel(logging.DEBUG)  # 文件记录所有级别
        file_handler.setFormatter(file_formatter)
        root_logger.addHandler(file_handler)
    
    # 控制台 handler
    if log_to_console:
        console_handler = logging.StreamHandler(sys.stderr)
        console_handler.setLevel(getattr(logging, level.upper(), logging.WARNING))
        console_handler.setFormatter(console_formatter)
        root_logger.addHandler(console_handler)
    
    # 减少第三方库的日志噪音
    for noisy_lib in ["urllib3", "httpx", "openai"]:
        logging.getLogger(noisy_lib).setLevel(logging.WARNING)
    
    root_logger.info("Logging initialized (level=%s, file=%s, console=%s, structured=%s)",
                     level, log_to_file, log_to_console, structured)
    
    return root_logger
```

作用：
- 初始化全局日志系统
- 根据 --log-level 参数设置日志级别

默认日志级别：WARNING
- DEBUG: 详细调试信息
- INFO: 一般信息
- WARNING: 警告信息（默认）
- ERROR: 错误信息

解析：
```python
def setup_logging(
    level: str = "WARNING",
    log_to_file: bool = True,
    log_to_console: bool = True,
    structured: bool = False,
) -> logging.Logger:
```

核心设计：

日志系统架构：

Root Logger("minicode")

File Handler
- 路径： ~/.mini-code/logs/app.log
- 级别：DEBUG（记录所有）
- 轮转：10MB 一个文件
- 保留：5个备份文件

Console Handler
- 输出：stderr
- 级别：WARNING（用户指定）
- 格式：简洁格式

关键点解析：

1.RotatingFileHandler（日志轮转）
```python
file_handler = logging.handlers.RotatingFileHandler(
    LOG_FILE,
    maxBytes=LOG_MAX_BYTES,      # 例如 10MB
    backupCount=LOG_BACKUP_COUNT,  # 例如 5 个备份
    encoding="utf-8",
)
```

为什么需要轮转？
- 防止日志文件无限增长
- 自动备份历史日志
- 例如：app.log → app.log.1 → app.log.2 ...

2.清除已有 handlers

```python
root_logger.handlers.clear()
```

为什么清除？
- 防止重复添加 handler
- 避免日志重复输出

3.减少第三方库噪音

```python
for noisy_lib in ["urllib3", "httpx", "openai"]:
    logging.getLogger(noisy_lib).setLevel(logging.WARNING)
```

原因：
- urllib3 等库会输出大量 DEBUG 信息
- 干扰 MiniCode 自己的日志
- 提升到 WARNING 级别，过滤噪音

#### 1.4 特殊命令处理

```python
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
```

执行流程：

- 用户输入: minicode-py --validate-config
- 打印配置诊断信息
- return (退出程序)



- 用户输入: minicode-py --install
- 运行 install.main()
- return (退出程序)



- 用户输入: minicode-py config set model claude-sonnet-4
- remaining_argv = ['config', 'set', 'model', 'claude-sonnet-4']
- management_argv = ['config', 'set', 'model', 'claude-sonnet-4']
- maybe_handle_management_command() 处理
- return (退出程序)

之所以单独处理这些命令，是因为它们是独立的管理命令，不需要启动完整的 Agent 循环。验证配置、安装、管理命令都是一次性操作，处理完直接退出，节省资源

### Phase 2 : 配置加载

#### 2.1 Runtime Config 加载

```python
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
```

关键点：

1.配置来源优先级：
- 环境变量（最高优先级）
- ~/.mini-code/settings.json (全局配置)
- .mini-code/settings.json (项目配置)

2.runtime 对象内容：

```python
runtime = {
    "model": "claude-sonnet-4-20250514",
    "env": {
        "ANTHROPIC_API_KEY": "sk-ant-...",
        "ANTHROPIC_MODEL": "claude-sonnet-4-20250514",
    },
    # ... 其他配置
}
```

3.失败降级策略：
- 配置加载失败不会崩溃
- 设置 runtime = None
- 后续会使用 mock_model（模拟模型）

runtime 对象 vs 环境变量

环境变量:
- 系统级配置
- 在 shell 中设置：export ANTHROPIC_API_KEY=sk-ant-...
- 优点：不写入文件，安全
- 缺点：每次启动都要设置

runtime 对象:
- 运行时配置的统一抽象
- 合并多个来源：环境变量 + 配置文件
- 优点：一次加载，全局使用
- 缺点：需要初始化

对比：Java 中的 UserContext 和 MiniCode 中的 runtime

Java UserContext:
- 封装用户身份信息
- 权限、角色、偏好等
- 用于业务系统

MiniCode runtime:
- 封装模型和 API 配置
- 环境变量、密钥、endpoint
- 用于 Agent 运行时配置

相似点：
- 都是上下文对象
- 都包含运行时需要的信息

不同点：
- UserContext 关注"用户是谁"
- runtime 关注"怎么调用 LLM"

runtime 初始化流程：

```python
def load_runtime_config(cwd: str | Path | None = None) -> dict[str, Any]:
    # 加载配置
    effective = load_effective_settings(cwd)
    
    # 合并环境变量（最高优先级）
    env = {**dict(effective.get("env", {})), **os.environ}
    
    # 确定模型
    model = (
        os.environ.get("MINI_CODE_MODEL") # 优先级1：环境变量
        or effective.get("model") # 优先级2：配置文件
        or str(env.get("ANTHROPIC_MODEL", "")).strip() # 优先级3：兼容性
    )

    # --- 加载各 provider 的配置 ---
    # Anthropic
    base_url = str(env.get("ANTHROPIC_BASE_URL", "")).strip() or "https://api.anthropic.com"
    auth_token = str(env.get("ANTHROPIC_AUTH_TOKEN", "")).strip() or None
    api_key = str(env.get("ANTHROPIC_API_KEY", "")).strip() or None

    # OpenAI
    openai_base_url = (
        str(env.get("OPENAI_BASE_URL", "")).strip()
        or str(env.get("OPENAI_API_BASE", "")).strip()
        or effective.get("openaiBaseUrl", "")
        or "https://api.openai.com"
    )
    openai_api_key = str(env.get("OPENAI_API_KEY", "")).strip() or effective.get("openaiApiKey", "")

    # OpenRouter
    openrouter_base_url = (
        str(env.get("OPENROUTER_BASE_URL", "")).strip()
        or "https://openrouter.ai/api"
    )
    openrouter_api_key = str(env.get("OPENROUTER_API_KEY", "")).strip()

    # Custom endpoint
    custom_base_url = (
        str(env.get("CUSTOM_API_BASE_URL", "")).strip()
        or effective.get("customBaseUrl", "")
    )
    custom_api_key = (
        str(env.get("CUSTOM_API_KEY", "")).strip()
        or effective.get("customApiKey", "")
        or openai_api_key
    )

    raw_max_output_tokens = (
        os.environ.get("MINI_CODE_MAX_OUTPUT_TOKENS")
        or effective.get("maxOutputTokens")
        or env.get("MINI_CODE_MAX_OUTPUT_TOKENS")
    )
    max_output_tokens = None
    if raw_max_output_tokens is not None:
        try:
            parsed = int(raw_max_output_tokens)
            if parsed > 0:
                max_output_tokens = parsed
        except (TypeError, ValueError):
            max_output_tokens = None

    # 验证至少有一个认证方法
    has_auth = any([
        auth_token, api_key, openai_api_key, openrouter_api_key, custom_api_key,
    ])
    if not model:
        raise RuntimeError("No model configured. Set ~/.mini-code/settings.json or ANTHROPIC_MODEL.")
    if not has_auth:
        raise RuntimeError(
            "No auth configured. Set one of: ANTHROPIC_API_KEY, OPENAI_API_KEY, "
            "OPENROUTER_API_KEY, or CUSTOM_API_KEY."
        )

    # 用户个人信息
    global_user_profile = MINI_CODE_USER_PROFILE_PATH
    proj_user_profile = project_user_profile_path(cwd)

    # 用户偏好（来自设置）
    user_preferences = effective.get("userPreferences", {})
    response_language = (
        str(env.get("MINI_CODE_LANGUAGE", "")).strip()
        or user_preferences.get("language", "")
    )
    response_verbosity = (
        str(env.get("MINI_CODE_VERBOSITY", "")).strip()
        or user_preferences.get("verbosity", "")
    )

    # 返回 runtime 对象
    return {
        "model": model,
        "baseUrl": base_url,
        "authToken": auth_token,
        "apiKey": api_key,
        "openaiBaseUrl": openai_base_url,
        "openaiApiKey": openai_api_key,
        "openrouterBaseUrl": openrouter_base_url,
        "openrouterApiKey": openrouter_api_key,
        "customBaseUrl": custom_base_url,
        "customApiKey": custom_api_key,
        "maxOutputTokens": max_output_tokens,
        "mcpServers": effective.get("mcpServers", {}),
        "globalUserProfilePath": str(global_user_profile),
        "projectUserProfilePath": str(proj_user_profile),
        "responseLanguage": response_language,
        "responseVerbosity": response_verbosity,
        "toolProfile": str(
            os.environ.get("MINI_CODE_TOOL_PROFILE")
            or effective.get("toolProfile", "")
            or "core"
        ).strip().lower(),
        "sourceSummary": f"config: {MINI_CODE_SETTINGS_PATH} > {CLAUDE_SETTINGS_PATH} > process.env",
    }
```

os.environ (系统环境变量):
- 来自 shell 的 export 命令
- 例如：export ANTHROPIC_API_KEY=sk-ant-...
- 最高优先级
- 不写入文件，相对安全

effective (配置文件配置):
- 从 ~/.mini-code/settings.json 等文件加载
- 持久化配置
- 优先级低于环境变量

env (合并后的环境):
- effective.get("env", {})  ← 配置文件中的 env 字段
- os.environ               ← 系统环境变量（覆盖前者）
- 用于传递给子进程或 MCP Server

配置合并优先级：

优先级从高到低：
1.环境变量
2.~/.mini-code/settings.json (全局配置)
3..mini-code/settings.json (项目配置)
4.~/.claude/settings.json (Claude Code 兼容)

具体看 config.py :
```python
def load_effective_settings(cwd: str | Path | None = None) -> dict[str, Any]:
    claude_settings = read_settings_file(CLAUDE_SETTINGS_PATH) # Claude Code 配置
    global_mcp = read_mcp_config_file(MINI_CODE_MCP_PATH) # 全局 MCP
    project_mcp = read_mcp_config_file(project_mcp_path(cwd)) # 项目 MCP
    mini_code_settings = read_settings_file(MINI_CODE_SETTINGS_PATH) # MiniCode 配置

    return merge_settings(
        merge_settings(
            merge_settings(claude_settings, {"mcpServers": global_mcp}),
            {"mcpServers": project_mcp},
        ),
        mini_code_settings,
    )
```

### Phase 3 : 核心组件初始化

#### 3.1 Prompt Handler

```python
    prompt_handler = _make_cli_permission_prompt() if sys.stdin.isatty() else None
```

具体实现：

```python
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
```

执行流程示例：

Agent 想执行命令: which python

系统调用: 
```python
prompt_handler({
    "summary": "Allow command execution: which python",
    "choices": [
        {"key": "y", "label": "Yes", "decision": "allow_once"},
        {"key": "Y", "label": "Yes, always for this command", "decision": "allow_always"},
        {"key": "n", "label": "No", "decision": "deny_once"},
    ]
})
```

输出到控制台:
```text
    Allow command execution: which python
      [y] Yes
      [Y] Yes, always for this command
      [n] No
    Choose: _
```

作用：
- 如果在真正的终端运行（stdin.isatty() == True），创建 CLI 权限提示处理器
- 如果在管道或 IDE 运行，不创建（返回 None）

TTY = Teletype（电传打字机）

在现代计算机中，TTY 表示：
- 真正的终端（用户交互式输入）
- 区别于管道输入（程序间通信）

#### 3.2 工具注册

```python
tools = create_default_tool_registry(cwd, runtime=runtime)
```
作用：
- 创建工具注册表
- 注册所有内置工具（read_file, write_file, grep_files, run_command 等）
- 加载 MCP 服务器工具
- 加载 Skills

ToolRegistry 结构图：
```Plaintext
ToolRegistry
    ├─ 内置工具
    │   ├─ File ops: read_file, write_file, edit_file, patch_file
    │   ├─ Search: grep_files, list_files, file_tree
    │   ├─ Execution: run_command, git_tool, test_runner
    │   ├─ Web: web_fetch, web_search
    │   └─ Task: task_tool, ask_user
    │
    ├─ MCP 工具
    │   └─ 从 MCP servers 动态加载
    │
    └─ Skills (技能)
        └─ 斜杠命令工作流
```

具体实现：

init.py
```python
def create_default_tool_registry(cwd: str, runtime: dict | None = None) -> ToolRegistry:
    skills = [asdict(skill) for skill in discover_skills(cwd)]
    mcp = create_mcp_backed_tools(cwd=cwd, mcp_servers=dict(runtime.get("mcpServers", {})) if runtime else {})
    profile = _resolve_tool_profile(runtime)
    tools = list(_CORE_TOOLS)
    if _is_full_tool_profile(profile):
        tools.extend(_load_utility_wrapper_tools())
    tools.extend(
        [
            create_load_skill_tool(cwd),
            *mcp["tools"],
        ]
    )
    return ToolRegistry(
        tools,
        skills=skills,
        mcp_servers=mcp["servers"],
        disposer=mcp["dispose"],
    )
```

Python 的 __init__.py 不会自动执行函数

tools/__init__.py 文件内容
```python
_CORE_TOOLS = [...]  # ← 这是变量定义，会执行
def create_default_tool_registry(...):  # <- 这是函数定义，会执行
    ...                                   # 但函数体不会执行
```

只有当调用时才执行：
tools = create_default_tool_registry()  # <- 这时才执行函数体

Python 执行顺序：

1.导入模块时
   import minicode.tools

2.执行 tools/__init__.py 的顶层代码
- _CORE_TOOLS = [...]  <- 变量定义（执行）
- def create_default_tool_registry(...): <- 函数定义（只定义，不执行）

3.调用函数时
   tools = create_default_tool_registry(cwd, runtime)

4.执行函数体
- skills = discover_skills(cwd)  <- 执行
- mcp = create_mcp_backed_tools(...)  <- 执行
- return ToolRegistry(...)  <- 执行

为什么这样设计？

如果不定义在 __init__.py，需要这样导入：
from minicode.tools.registry import create_default_tool_registry  

定义在 __init__.py，可以简化导入：
from minicode.tools import create_default_tool_registry  

**内置工具 vs MCP 工具**

内置工具：
- 存储在 tools/ 目录
- Python 实现，直接调用
- 性能好，无网络开销
- 缺点：只能用 MiniCode 自带的功能

MCP 工具：
- 通过 MCP 协议连接外部服务
- 可以连接任何 MCP Server（如数据库、搜索引擎、文件系统等）
- 优点：无限扩展性

架构图：

```text
ToolRegistry
    │
    ├─ 内置工具 (tools/*.py)
    │   ├─ read_file.py    → 直接调用 Python 函数
    │   ├─ write_file.py   → 无需网络
    │   └─ run_command.py  → 性能最优
    │
    └─ MCP 工具
        │
        ├─ sequential-thinking (MCP Server)
        │   └─ 远程服务，通过 JSON-RPC 通信
        │
        ├─ database-query (MCP Server)
        │   └─ 连接数据库
        │
        └─ web-search (MCP Server)
            └─ 连接搜索引擎
```

MCP 的价值

场景举例：

内置工具 - 本地文件操作
read_file(path="/home/user/project/src/main.py")  # 直接调用

MCP 工具 - 连接外部服务
sequential_thinking(prompt="分析这个问题...")  # 通过 JSON-RPC 调用远程服务

MCP 工具 - 连接数据库
database_query(sql="SELECT * FROM users")  # 通过 MCP 连接数据库

MCP 工具 - 连接搜索引擎
web_search(query="Python asyncio tutorial")  # 通过 MCP 调用搜索 API

为什么用 MCP 而不是直接 HTTP API？

MCP 协议优势：
- 统一接口：所有工具用相同方式调用
- 工具发现：自动发现 MCP Server 提供的工具
- 权限控制：统一的权限管理
- 类型安全：工具定义包含类型信息

MCP 服务器不一定需要自己部署

方式1：使用官方 MCP Server（无需部署）
```json
// ~/.mini-code/mcp.json
{
  "mcpServers": {
    "sequential-thinking": {
      "command": "npx",
      "args": ["-y", "@modelcontextprotocol/server-sequential-thinking"]
    }
  }
}
```
工作原理：
- MiniCode 启动时自动执行 npx -y @modelcontextprotocol/server-sequential-thinking
- npx 会自动下载并运行 MCP Server
- 无需手动部署，即开即用

方式2：自己部署 MCP Server（企业/定制场景）
```json
{
  "mcpServers": {
    "company-database": {
      "command": "python",
      "args": ["mcp_server.py"],
      "env": {
        "DB_URL": "postgresql://..."
      }
    }
  }
}
```
自己部署的场景：

1.企业内部数据库
- 连接公司数据库
- 需要自定义查询逻辑

2.私有 API
- 连接内部服务
- 需要认证和权限控制

3.定制工具
- 特殊的业务逻辑
- 官方 MCP Server 不满足需求

#### 3.3 Permission Manager

```python
permissions = PermissionManager(cwd, prompt=prompt_handler)
```
作用：
- 管理权限审批
- 跟踪权限作用域
- 处理 allow/deny 决策

权限管理策略：
- 用户可以允许一次
- 用户可以允许整个会话
- 用户可以永久允许（写入配置）
- 用户可以拒绝

管理权限审批类似于执行 which python 语句，agent会问你选择 Yes/Yes且在本对话中始终允许 which python 语句/No 

权限决策类型：

permisson.py
```python
# 权限决策类型
PermissionDecision = Literal[
    "allow_once",
    "allow_always",
    "allow_turn",
    "allow_all_turn",
    "deny_once",
    "deny_always",
    "deny_with_feedback",
]
```

权限审批流程：

permisson.py
```python
    def ensure_path_access(self, target_path: str, intent: str) -> None:
        normalized_target = _normalize_path(target_path)
        
        # 1.快速路径：在工作目录内，直接允许
        if _is_within_directory(self.workspace_root, normalized_target):
            return
        
        # 2.检查拒绝集合（失败快速）
        if normalized_target in self.session_denied_paths or _matches_directory_prefix(normalized_target, self.denied_directory_prefixes):
            raise RuntimeError(f"Access denied for path outside cwd: {normalized_target}")
        
        # 3.检查已批准集合
        if normalized_target in self.session_allowed_paths or _matches_directory_prefix(normalized_target, self.allowed_directory_prefixes):
            return # 已批准过
        
        if normalized_target in self.session_denied_paths or _matches_directory_prefix(normalized_target, self.denied_directory_prefixes):
            raise RuntimeError(f"Access denied for path outside cwd: {normalized_target}")
        if normalized_target in self.session_allowed_paths or _matches_directory_prefix(normalized_target, self.allowed_directory_prefixes):
            return
        
        # Auto mode risk assessment for path access
        assessment = self.auto_checker.assess_risk("path_access", {"path": normalized_target, "intent": intent})
        if assessment.action == "approve":
            get_mode_state().record_decision("approve")
            self.session_allowed_paths.add(normalized_target)
            return
        
        if self.prompt is None:
            raise RuntimeError(
                f"Path {normalized_target} is outside cwd {self.workspace_root}. Start minicode in TTY mode to approve it."
            )

        scope_directory = normalized_target if intent in {"list", "command_cwd"} else str(Path(normalized_target).parent)
        
        # 4.需要审批，调用 prompt
        result = self.prompt(
            {
                "kind": "path",
                "summary": f"mini-code wants {intent.replace('_', ' ')} access outside the current cwd",
                "details": [
                    f"cwd: {self.workspace_root}",
                    f"target: {normalized_target}",
                    f"scope directory: {scope_directory}",
                ],
                "scope": scope_directory,
                "choices": [
                    {"key": "y", "label": "allow once", "decision": "allow_once"},
                    {"key": "a", "label": "allow this directory", "decision": "allow_always"},
                    {"key": "n", "label": "deny once", "decision": "deny_once"},
                    {"key": "d", "label": "deny this directory", "decision": "deny_always"},
                ],
            }
        )
        
        # 5.根据用户决策处理
        decision = result.get("decision")
        if decision == "allow_once":
            self.session_allowed_paths.add(normalized_target)
            return
        if decision == "allow_always":
            self.allowed_directory_prefixes.add(scope_directory)
            self._persist() # 写入配置文件
            return
        if decision == "deny_always":
            self.denied_directory_prefixes.add(scope_directory)
            self._persist()
        else:
            self.session_denied_paths.add(normalized_target)
        raise RuntimeError(f"Access denied for path outside cwd: {normalized_target}")
```

**实际执行示例**

Agent: 我要执行 which python

系统：检测到命令执行请求

权限检查: 这不是危险命令

调用 prompt_handler:

显示给用户:
```bash
Allow command execution: which python?
                                      
[y] Yes (this time only)            
[Y] Yes, always for this command    
[s] Yes, always for this session    
[n] No                              
                                     
Choose: _     
```                     

用户输入: Y

决策: allow_always

1.添加到 allowed_command_patterns
2.写入 ~/.mini-code/permissions.json
3.下次执行 which python 不再询问

main.py
```python
        # 判断是否为交互式终端
        if not sys.stdin.isatty():
            # 不是交互式终端
            for raw_input in sys.stdin: # 循环读取 stdin
                user_input = raw_input.strip()
                if not user_input:
                    continue # 跳过空行
```
这里因为是非 TTY 模式，因此只能读取 stdin，而不能用上下箭头 + 回车的方式选择选项

TTY 模式支持上下箭头 + 回车选择选项：

tui/event_flow.py
```python
    if event.name == "up" and _move_pending_approval_selection(state, -1):
        rerender()
        return True

    if event.name == "down" and _move_pending_approval_selection(state, 1):
        rerender()
        return True
```

危险命令检测：

permisson.py
```python
def _classify_dangerous_command(command: str, args: list[str]) -> str | None:
    normalized_args = [arg.strip() for arg in args if arg.strip()]
    signature = _format_command_signature(command, normalized_args)
    
    # git reset --hard 危险
    if command == "git":
        if "reset" in normalized_args and "--hard" in normalized_args:
            return f"git reset --hard can discard local changes ({signature})"
        if "clean" in normalized_args:
            return f"git clean can delete untracked files ({signature})"
        if "checkout" in normalized_args and "--" in normalized_args:
            return f"git checkout -- can overwrite working tree files ({signature})"
        if "push" in normalized_args and any(arg in {"--force", "-f"} for arg in normalized_args):
            return f"git push --force rewrites remote history ({signature})"
        if "restore" in normalized_args and any(arg.startswith("--source") for arg in normalized_args):
            return f"git restore --source can overwrite local files ({signature})"

    if command == "npm" and "publish" in normalized_args:
        return f"npm publish affects a registry outside this machine ({signature})"

    # 灾难性删除命令检测，rm -rf 危险
    if command == "rm":
        # 组合所有标志（支持 -rf, -fr, -Rf, -r -f 等）
        combined_flags = "".join(arg for arg in normalized_args if arg.startswith("-")).lower()
        # 检查是否同时有递归和强制标志
        if "r" in combined_flags and "f" in combined_flags:
            # 检查是否针对根目录或使用 --no-preserve-root
            if any(arg in {"/", "/*"} for arg in normalized_args) or "--no-preserve-root" in normalized_args:
                return f"rm -rf can cause catastrophic data loss ({signature})"
            # 即使不是根目录，rm -rf 也是危险的
            return f"rm -rf can cause catastrophic data loss ({signature})"

    # 磁盘写入/格式化命令检测
    if command in {"dd", "mkfs", "mkfs.ext4", "mkfs.vfat", "fdisk", "format"}:
        return f"{command} can modify or destroy disk partitions ({signature})"

    # 权限全开命令检测，chmod 777 危险
    if command == "chmod":
        if "777" in normalized_args or any(arg.endswith("777") for arg in normalized_args):
            return f"chmod 777 opens permissions to all users ({signature})"
    
    # 可执行任意代码
    if command in {
        "node", "python", "python3", "pythonw",
        "bun", "bash", "sh", "zsh", "fish",
        "powershell", "pwsh",
    }:
        return f"{command} can execute arbitrary local code ({signature})"

    # macOS 特有的危险命令
    if command == "diskutil":
        return f"diskutil can erase or partition disks ({signature})"
    if command == "csrutil":
        return f"csrutil modifies System Integrity Protection ({signature})"
    if command == "defaults" and "write" in normalized_args:
        return f"defaults write modifies system preferences ({signature})"
    if command == "launchctl" and any(arg in {"unload", "bootout", "disable"} for arg in normalized_args):
        return f"launchctl can disable system services ({signature})"
    if command == "dscl":
        return f"dscl can modify directory services and user accounts ({signature})"

    return None
```

**Permisson Manager 的职责是否与 Prompt Handler 重叠？**

Permission Manager (业务逻辑层)                      
- 权限检查：ensure_path_access(), ensure_command()  
- 权限存储：session_allowed, allowed_patterns       
- 权限决策：根据配置/会话状态判断                     
- 权限持久化：写入 ~/.mini-code/permissions.json

Prompt Handler (UI交互层)                            
- 显示权限请求：print("Allow...")                    
- 显示选项：[y] Yes, [n] No                          
- 获取输入：input("Choose: ")                        
- 返回决策：{"decision": "allow_once"}

```python
# 1. Agent 想执行命令
tool_input = {"command": "which", "args": ["python"]}

# 2. agent_loop 调用工具前，检查权限
permissions.ensure_command_allowed("which", ["python"])

# 3. Permission Manager 内部逻辑
def ensure_command_allowed(self, command, args):
    # Step 1: 检查是否已批准
    if command in self.session_allowed_commands:
        return  # 已批准，无需询问

    if command in self.allowed_command_patterns:
        return  # 配置文件中已允许

    # Step 2: 检查是否已拒绝
    if command in self.session_denied_commands:
        raise PermissionDenied()

    # Step 3: 需要用户决策 - 调用 Prompt Handler
    if self.prompt:
        result = self.prompt({  # ← 这里调用 Handler
            "summary": f"Allow command: {command}",
            "choices": [
                {"key": "y", "label": "Yes", "decision": "allow_once"},
                {"key": "Y", "label": "Always", "decision": "allow_always"},
                {"key": "n", "label": "No", "decision": "deny_once"},
            ]
        })

        # Step 4: 根据用户决策更新状态
        decision = result["decision"]
        if decision == "allow_once":
            self.session_allowed_commands.add(command)
        elif decision == "allow_always":
            self.allowed_command_patterns.add(command)
            self._persist()  # 写入配置文件
```

Prompt Handler ≈ 前端表单组件
- 负责"显示"和"收集输入"
- 不做业务逻辑判断
- 只返回用户的选择

Permission Manager ≈ 后端权限服务
- 负责"检查"、"存储"、"更新"
- 做业务逻辑判断
- 需要用户输入时调用 Handler

类似于后端项目中 Controller 层和 Service 层之间的关系

完整的权限流程：
```text
┌────────────────────────────────────────────────────────────
│ Agent 想执行工具                                            
│  - read_file(path="/etc/passwd")                           
│  - run_command(command="rm -rf /")                          
└────────────────────────────────────────────────────────────
                      ↓
┌────────────────────────────────────────────────────────────
│ agent_loop.py 调用工具前检查                                
│  tools.execute(tool_name, tool_input, context)              
│      ↓                                                      
│  context.permissions.ensure_path_access(path, intent)       
└────────────────────────────────────────────────────────────
                      ↓
┌────────────────────────────────────────────────────────────
│ Permission Manager (业务逻辑层)                              
│  Step 1: 检查缓存                                           
│    if path in session_allowed_paths:                        
│        return  # 已批准                                     
│                                                             
│  Step 2: 检查配置                                           
│    if path matches allowed_directory_prefixes:              
│        return  # 配置文件允许                               
│                                                             
│  Step 3: 检查拒绝列表                                       
│    if path in session_denied_paths:                         
│        raise PermissionDenied()                             
│                                                             
│  Step 4: 需要用户决策 → 调用 Prompt Handler                 
│    result = self.prompt(request)                            
└────────────────────────────────────────────────────────────
                      ↓
┌────────────────────────────────────────────────────────────
│ Prompt Handler (UI交互层)                                   
│  显示：                                                      
│    Allow path access outside cwd: /etc/passwd?              
│    [y] Yes (this time only)                                 
│    [Y] Yes, always for this path                            
│    [n] No                                                   
│                                                             
│  获取输入：                                                  
│    answer = input("Choose: ")                               
│                                                             
│  返回决策：                                                  
│    return {"decision": "allow_once"}                        
└────────────────────────────────────────────────────────────
                      ↓
┌────────────────────────────────────────────────────────────
│ Permission Manager (继续处理)                                
│  Step 5: 根据决策更新状态                                    
│    if decision == "allow_once":                             
│        session_allowed_paths.add(path)                      
│    elif decision == "allow_always":                         
│        allowed_directory_prefixes.add(path)                 
│        self._persist()  # 写入配置文件                      
│                                                             
│  Step 6: 返回（允许执行）                                    
│    return  # 工具可以执行                                    
└────────────────────────────────────────────────────────────
                      ↓
┌────────────────────────────────────────────────────────────
│ agent_loop.py 继续执行工具                                   
│  result = tools.execute(...)                                
└────────────────────────────────────────────────────────────
```

#### 3.4 Model Adapter 模型适配器

```python
    # 创建模型适配器，统一接口，屏蔽底层 API 差异
    force_mock = runtime is None
    model = create_model_adapter(
        model=runtime.get("model", "") if runtime else "",
        tools=tools,
        runtime=runtime,
        force_mock=force_mock,
    )
```

作用：
- 创建模型适配器
- 根据 model 名称自动选择 provider（Anthropic/OpenAI/其他）
- 统一接口，屏蔽底层 API 差异

统一的内容

1.消息格式
- Anthropic: role + content (list of blocks)
- OpenAI: role + content (string)
- 统一为: {"role": "...", "content": "..."} (agent_loop 使用)

2.工具调用
- Anthropic: tool_use blocks
- OpenAI: tool_calls array
- 统一为: AgentStep.calls = [{"toolName": ..., "input": ...}]

3.流式输出
- Anthropic: content_block_delta events
- OpenAI: choices[0].delta.content
- 统一为: on_stream_chunk(text)

4.Thinking blocks
- Anthropic: thinking blocks
- OpenAI: 不支持
- 统一为: on_thinking_chunk(text) (OpenAI 会忽略)

5.错误处理
- Anthropic: error.type + error.message
- OpenAI: error.code + error.message
- 统一为: 异常 + 友好消息

Model Adapter 选择逻辑：

```text
create_model_adapter(model, tools, runtime)
    ↓
model_registry.py
    ↓
检测 provider:
    ├─ 以 "claude-" 开头 → Anthropic Adapter
    ├─ 以 "gpt-" 开头 → OpenAI Adapter
    ├─ 包含 "/" (如 "openrouter/...") → Custom Endpoint
    └─ force_mock=True → Mock Model (测试用)
```

Adapter 选择逻辑：

model_registry.py
```python
def detect_provider(model: str, runtime: dict | None = None) -> Provider:
    """Auto-detect which provider to use based on model name and config.

    Priority:
    1. OpenRouter — if OPENROUTER_API_KEY set or model starts with "openrouter/"
    2. OpenAI — if model matches OpenAI patterns or OPENAI_API_KEY set
    3. Custom — if CUSTOM_API_BASE_URL set
    4. Anthropic — default
    """
    model_lower = model.lower()

    # 1. OpenRouter 检测
    if os.environ.get("OPENROUTER_API_KEY") or model_lower.startswith("openrouter/"):
        return Provider.OPENROUTER
    # 检查 provider 前缀
    for prefix in ("anthropic/", "openai/", "google/", "meta-llama/", "deepseek/",
                   "qwen/", "minimax/", "mistralai/"):
        if model_lower.startswith(prefix):
            if os.environ.get("OPENROUTER_API_KEY"):
                return Provider.OPENROUTER
            # Could also be a custom endpoint with this naming
            if runtime and runtime.get("openaiBaseUrl"):
                return Provider.CUSTOM
            # Default to OpenRouter for vendor-prefixed models
            return Provider.OPENROUTER

    # 2. DeepSeek 直连 API
    if model_lower.startswith("deepseek") or "deepseek" in model_lower:
        if os.environ.get("DEEPSEEK_API_KEY"):
            return Provider.CUSTOM
        # If registered as CUSTOM in BUILTIN_MODELS, use that
        if model in BUILTIN_MODELS and BUILTIN_MODELS[model].provider == Provider.CUSTOM:
            return Provider.CUSTOM

    # 3. OpenAI 检测
    openai_prefixes = ("gpt-4", "gpt-3.5", "o1-", "o3-", "chatgpt-")
    openai_exact = {"gpt-4o", "gpt-4o-mini", "gpt-4-turbo", "o1", "o1-mini", "o3-mini"}
    if model_lower in openai_exact or any(model_lower.startswith(p) for p in openai_prefixes):
        return Provider.OPENAI
    if os.environ.get("OPENAI_API_KEY") and not os.environ.get("ANTHROPIC_API_KEY"):
        return Provider.OPENAI

    # 3. Custom endpoint 检测
    custom_base = (
        os.environ.get("CUSTOM_API_BASE_URL", "")
        or (runtime or {}).get("customBaseUrl", "")
    )
    if custom_base:
        return Provider.CUSTOM

    # 4. 默认：Anthropic
    return Provider.ANTHROPIC
```

检测优先级：

1.OpenRouter: model.startswith("openrouter/") 或有 OPENROUTER_API_KEY
2.DeepSeek: model.startswith("deepseek") 且有 DEEPSEEK_API_KEY
3.OpenAI: model 包含 gpt/o1/o3 关键字
4.Custom: 设置了 CUSTOM_API_BASE_URL
5.Anthropic: 默认

**Endpoint**

Endpoint = API 端点 = 服务的访问地址

Anthropic API:
  Endpoint: https://api.anthropic.com/v1/messages

OpenAI API:
  Endpoint: https://api.openai.com/v1/chat/completions

OpenRouter (代理):
  Endpoint: https://openrouter.ai/api/v1/chat/completions

Custom Endpoint (自定义):
  Endpoint: http://localhost:8000/v1/chat/completions  (例如本地部署的模型)

为什么需要 Custom Endpoint？

场景1：本地部署模型（如 Ollama, vLLM）
export CUSTOM_API_BASE_URL=http://localhost:11434/v1
export CUSTOM_API_KEY=not-needed

场景2：企业内网部署
export CUSTOM_API_BASE_URL=https://internal.company.com/api/v1
export CUSTOM_API_API_KEY=internal-key

场景3：第三方兼容 API（如 DeepSeek, Moonshot）
export CUSTOM_API_BASE_URL=https://api.deepseek.com/v1
export CUSTOM_API_KEY=sk-xxx

#### 3.5 Context Manager

```python
    # 初始化上下文管理器，管理上下文窗口
    from minicode.context_manager import ContextManager
    from minicode.logging_config import get_logger
    logger = get_logger("main")
    context_mgr = None
    if runtime:
        context_mgr = ContextManager(model=runtime.get("model", "default"))
        logger.info("Context manager initialized for model: %s", runtime.get("model", "unknown"))
```

作用：
- 管理 context window（上下文窗口）
- Token 计数和估算
- 自动压缩

LLM 有 token 限制，问题：
    对话越来越长 → tokens 超限 → API 报错

解决：
    Context Manager 监控 tokens 使用
    接近限制 → 自动压缩

logger.info 的日志会记录到哪里？是打在控制台上，还是记录到日志文件中？

实际上两个地方都会记录，看 logging_config.py
```python
    # 文件 handler — 使用 RotatingFileHandler 防止日志无限增长，记录所有级别
    if log_to_file:
        # RotatingFileHandler: 按大小轮转
        file_handler = logging.handlers.RotatingFileHandler(
            LOG_FILE,
            maxBytes=LOG_MAX_BYTES,
            backupCount=LOG_BACKUP_COUNT,
            encoding="utf-8",
        )
        file_handler.setLevel(logging.DEBUG)  # 文件记录所有级别
        file_handler.setFormatter(file_formatter)
        root_logger.addHandler(file_handler)
    
    # 控制台 handler，记录用户指定级别
    if log_to_console:
        console_handler = logging.StreamHandler(sys.stderr)
        console_handler.setLevel(getattr(logging, level.upper(), logging.WARNING)) # 默认只显示 WARNING
        console_handler.setFormatter(console_formatter)
        root_logger.addHandler(console_handler)
```

日志级别配置：

默认：WARNING
```bash
minicode-py
```

显示 INFO
```bash
minicode-py --log-level INFO
```

显示 DEBUG
```bash
minicode-py --log-level DEBUG
```

日志文件位置：
```bash
~/.mini-code/logs/app.log        # 主日志
~/.mini-code/logs/app.log.1      # 备份1
~/.mini-code/logs/app.log.2      # 备份2
```

**用户第一个请求进来后，系统第一个写日志的地方在哪？**

实际上，第一个日志写在多处：

用户输入 "帮我分析代码"

1.main.py: 保存历史（可能写日志）
```python
history.append(user_input)
save_history_entries(history)
```

2.main.py : 开始权限跟踪（可能写日志）
```python
    permissions.begin_turn()
```

3.agent_loop.py : Hook 日志（如果有配置）
```python
    fire_hook_sync(HookEvent.AGENT_START, step=step, cwd=cwd)
```
4.agent_loop.py : Orchestrator step_start
```python
orch.step_start(...)  # ← 内部会写 INFO 级别日志
  
cybernetic_orchestrator.py:
    logger.info("Step %d started", step)
```

如果设置 --log-level INFO, 会看到类似：
```bash
2026-07-16 10:00:00,123 - minicode.cybernetic_orchestrator - INFO - Step 1 started
2026-07-16 10:00:00,125 - minicode.state_observer - INFO - StateObserver: internal_load=0.25
2026-07-16 10:00:00,130 - minicode.agent_loop - INFO - Calling model API
```
#### 3.6 Memory Manager

```python
    # 初始化记忆管理器，用于跨会话知识保留
    from minicode.memory import MemoryManager
    memory_mgr = MemoryManager(project_root=Path(cwd))
    logger.info("Memory manager initialized")
```

作用：
- 跨会话知识保持
- 三层 Memory：user, project, local
- TF-IDF 检索相关记忆

Memory 架构：
```text
MemoryManager
    ├─ User Memory (~/.mini-code/memory/)
    │   └─ 用户级别的通用知识
    │
    ├─ Project Memory (.mini-code/memory/)
    │   └─ 项目相关的知识
    │
    └─ Local Memory (.mini-code/memory/local/)
        └─ 当前会话的临时知识
```

三种知识会放在不同的文件中，见 memory.py 开头的注释：
```python
"""Layered memory system for cross-session knowledge retention.

Provides three-tier memory hierarchy:
- User memory (~/.mini-code/memory/) - cross-project, persistent
- Project memory (.mini-code-memory/) - shared across sessions, can be versioned
- Local memory (.mini-code-memory-local/) - project-specific, not checked in

Memory is automatically injected into system prompts to give the agent
context about past decisions, codebase patterns, and project conventions.

Search uses TF-IDF relevance scoring for intelligent retrieval.
"""
```
**TF-IDF**

TF-IDF = Term Frequency - Inverse Document Frequency（词频-逆文档频率）

这是信息检索中经典的相关性算法

公式：

TF-IDF(term, doc, corpus) = TF(term, doc) × IDF(term, corpus)

TF (词频) = 词在文档中出现的次数 / 文档总词数
IDF (逆文档频率) = log(文档总数 / 包含该词的文档数)

直观理解：

TF (Term Frequency):
- "项目" 在某个记忆中出现 10 次，其他词只出现 1 次
- "项目" 很重要，TF 高

IDF (Inverse Document Frequency):
- ∵ "的" 在所有记忆中都出现
- ∴ "的" 是常用词，IDF 低
- ∵ "微服务" 只在少数记忆中出现
- ∴ "微服务" 是专业词，IDF 高

TF-IDF = TF × IDF:
高频 + 罕见 = 高分（这个记忆很相关）
高频 + 常见 = 低分（可能是常用词）
低频 + 常见 = 低分（不重要）

查询扩展应用：memory.py

```python
def _expand_query_terms(terms: list[str], active_domains: list[str] | None = None) -> list[str]:
    """Expand query terms using code terminology + domain-specific dictionaries."""
    expanded = list(terms)
    for term in terms:
        if term in _CODE_TERM_EXPANSIONS:
            expanded.extend(_CODE_TERM_EXPANSIONS[term])
    # Domain-specific expansions
    if active_domains:
        for domain in active_domains:
            domain_dict = _DOMAIN_TERM_EXPANSIONS.get(domain, {})
            for term in terms:
                if term in domain_dict:
                    expanded.extend(domain_dict[term])
    return expanded
```

对查询进行扩展：
- 用户查询: "组件"
- 扩展为: ["组件", "component", "widget", "control", "element"]
- 用这些词计算 TF-IDF
- 返回最相关的记忆

TF-IDF 在 search 函数中：

memory.py
```python
    def search(
        self,
        query: str,
        scope: MemoryScope | None = None,
        limit: int = 20,
        min_relevance: float = 0.1,
        active_domains: list[str] | None = None,
    ) -> list[MemoryEntry]:
        """Search across memory scopes with TF-IDF + domain relevance.

        Args:
            query: Search query string
            scope: Optional scope to limit search to
            limit: Maximum results to return
            min_relevance: Minimum relevance score threshold (0.0-1.0)
            active_domains: Current domain context for soft boosting

        Returns:
            Entries ranked by relevance (TF-IDF + domain + usage + recency)
        """
        results = []

        scopes_to_search = [scope] if scope else list(MemoryScope)

        for s in scopes_to_search:
            # 对每个记忆文件调用 search
            results.extend(self.memories[s].search(query, active_domains=active_domains))

        # Apply minimum relevance threshold
        # (entries are already scored by MemoryFile.search)
        if min_relevance > 0:
            # Normalize scores to 0-1 range for threshold comparison
            if results:
                max_score = max(
                    self._score_entry(e, _tokenize(query)) for e in results
                )
                if max_score > 0:
                    results = [
                        e for e in results
                        if self._score_entry(e, _tokenize(query)) / max_score >= min_relevance
                    ]

        # Results are already ranked by MemoryFile.search()
        # Deduplicate by content (keep highest-scored)
        seen_content: set[str] = set()
        deduped = []
        for entry in results:
            content_key = entry.content[:100].strip().lower()
            if content_key not in seen_content:
                seen_content.add(content_key)
                deduped.append(entry)

        return deduped[:limit]
```

memory.py 的 _score_entry:
```python
    def _score_entry(self, entry: MemoryEntry, query_tokens: list[str]) -> float:
        """计算记忆条目的相关性分数"""
        if not query_tokens:
            return 0.0
        
        # 1.查询词扩展
        query_tokens_expanded = _expand_query_terms(query_tokens)
        
        # 2.记忆条目分词
        entry_tokens = _tokenize(
            f"{entry.content} {entry.category} {' '.join(entry.tags)}"
        )
        
        # 3.计算 IDF
        idf = _compute_idf([entry_tokens])
        
        # 4.BM25 分数(TF-IDF 改进版)
        avgdl = len(entry_tokens)
        bm25 = _bm25_score(query_tokens_expanded, entry_tokens, idf, avgdl)
        query_lower = " ".join(query_tokens).lower()
        content_lower = entry.content.lower()
        
        # 5.子串匹配加分
        substring_score = 0.0
        if query_lower in content_lower:
            substring_score = 2.0
        elif any(q in content_lower for q in query_tokens):
            substring_score = 1.0
        
        # 6.标签匹配加分
        tag_score = 0.0
        exact_tag_match = any(tag.lower() == query_lower for tag in entry.tags)
        partial_tag_match = any(query_lower in tag.lower() for tag in entry.tags)
        if exact_tag_match:
            tag_score = 5.0
        elif partial_tag_match:
            tag_score = 1.5
        if query_lower in entry.category.lower():
            tag_score += 1.0
            
        # 7.使用频率加分
        usage_bonus = math.log1p(entry.usage_count) * 0.3
        
        # 8.新近度加分
        age_hours = (time.time() - entry.updated_at) / 3600
        recency_bonus = 1.0 / (1.0 + age_hours / 24.0) * 0.5
        
        # 总分
        return bm25 + substring_score + tag_score + usage_bonus + recency_bonus
```

BM25 计算：

```python
def _bm25_score(
    query_tokens: list[str],
    doc_tokens: list[str],
    idf: dict[str, float],
    avgdl: float,
    *,
    k1: float = _BM25_K1, # 饱和参数
    b: float = _BM25_B, # 长度归一化参数
) -> float:
    """Compute Okapi BM25 score between query and document.

    Formula:
        score(q,d) = sum(IDF(qi) * (tf(qi,d) * (k1 + 1)) /
                         (tf(qi,d) + k1 * (1 - b + b * |d|/avgdl)))
    """
    if not query_tokens or not doc_tokens or avgdl == 0:
        return 0.0

    doc_len = len(doc_tokens)
    tf_doc = _compute_tf(doc_tokens)
    total_tokens = doc_len

    score = 0.0
    for term in set(query_tokens):
        if term not in idf:
            continue
        tf = tf_doc.get(term, 0.0) # 词频
        if tf == 0:
            continue
        # BM25 公式
        numerator = tf * (k1 + 1)
        denominator = tf + k1 * (1 - b + b * (total_tokens / avgdl))
        score += idf[term] * (numerator / denominator)

    return score
```

总结：
- _expand_query_terms 是查询扩展（增加同义词）
- _bm25_score 才是真正的 TF-IDF 计算
- _score_entry 综合多个因素计算最终分数

#### 3.7 User Profile Manager

```python
    # 初始化用户个人资料管理器
    from minicode.user_profile import UserProfileManager
    profile_manager = UserProfileManager(cwd=cwd)
    profile_manager.load_merged()
    logger.info("User profile manager initialized (global=%s, project=%s)",
                profile_manager.global_path.exists(),
                profile_manager.project_path.exists())
```

作用：
- 加载用户偏好设置
- 合并全局和项目配置
- 存储用户习惯（如常用命令、编码风格等）

User Profile Manager 某种程度上类似于 Claude Code 中专门读取 CLAUDE.md 的控制器

Claude Code 的 CLAUDE.md:
- 项目级配置
- 包含项目规则、偏好等
- 写在项目根目录
- 提示词的一部分

MiniCode 的 UserProfileManager:
- 用户级 + 项目级配置
- 存储在 USER.md 文件中
- 包含用户偏好、习惯等
- 不直接注入到 prompt，而是作为配置

存储位置：

全局 USER.md:
  ~/.mini-code/USER.md

项目 USER.md:
  .mini-code/USER.md

合并逻辑在 main.py 中：
```python
    # 初始化用户个人资料管理器
    from minicode.user_profile import UserProfileManager
    profile_manager = UserProfileManager(cwd=cwd)
    profile_manager.load_merged() # 合并全局和项目配置
    logger.info("User profile manager initialized (global=%s, project=%s)",
                profile_manager.global_path.exists(),
                profile_manager.project_path.exists())
```

#### 3.8 App State Store

```python
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
```

作用：
- 全局状态管理（类似 React 的 Zustand/Redux）
- 存储 session_id, workspace, model, tool_calls 等
- 提供 reducer 模式更新状态

设计模式：
```text
create_app_store() → Store[AppState]
    ├─ get_state() → AppState
    ├─ set_state(reducer) → 更新状态
    └─ subscribe(listener) → 监听状态变化
```

AppState:
- session_id: str
- workspace: str
- model: str
- tool_calls: int
- busy: bool
- ...

使用 store 模式，便于统一管理全局状态，避免分散的状态变量。类似前端的 Redux/Zustand，提供可预测的状态更新和监听机制。TUI 可以订阅状态变化自动重渲染

具体应用场景：

场景1：工具执行时更新状态

```python
# agent_loop.py
if store:
    store.set_state(set_busy("read_file"))  # 设置忙碌状态

# ↓ 内部调用

# state.py 
def set_busy(tool_name: str | None = None) -> Callable[[AppState], AppState]:
    def updater(state: AppState) -> AppState:
        state.is_busy = True
        state.active_tool = tool_name
        state.status_message = f"Running {tool_name}..."
        state.update_timestamp()
        return state
    return updater
```

触发订阅：

```python
# tty_app.py 中订阅状态变化
unsubscribe = store.subscribe(lambda: rerender())

# 状态变化 → 自动触发 rerender()
store.set_state(set_busy("read_file"))
    ↓
# state.py
for listener in self._listeners:
    try:
        listener()  # ← 调用 rerender()
    except Exception:
        pass
```

完整流程：
```text
用户: "读取 main.py"
    ↓
Agent 决定调用 read_file 工具
    ↓
agent_loop.py:
    store.set_state(set_busy("read_file"))
        ↓
    state.py:
        state.is_busy = True
        state.active_tool = "read_file"
            ↓
        通知所有订阅者: listener()  ← rerender()
            ↓
    tty_app.py:
        TUI 重新渲染，显示：
        ┌────────────────────────────┐
        │ Running read_file...       │  ← status_message
        │ [=====              ] 50%  │  ← 进度条
        └────────────────────────────┘
    ↓
工具执行完成
    ↓
agent_loop.py:
    store.set_state(increment_tool_calls())
    store.set_state(set_idle())
        ↓
    state.py:
        state.tool_call_count += 1
        state.is_busy = False
            ↓
        再次通知订阅者 → rerender()
            ↓
    tty_app.py:
        TUI 更新为：
        ┌────────────────────────────┐
        │ Ready                      │
        │ Tool calls: 1              │
        └────────────────────────────┘
```

**Zustand**

Zustand 是一个前端状态管理库，类似于 Redux，但更简单

特点：
- 极简 API
- 无需 Provider
- 基于 Hooks

MiniCode 中的实现：state.py

```python
class Store(Generic[T]):
    """Zustand-style state management.
    
    Provides predictable state updates with subscriber notifications.
    Inspired by Claude Code's Zustand store implementation.
    """
    
    def __init__(
        self,
        initial_state: T,
        on_change: Callable[[T, T], None] | None = None,
    ):
        """Initialize store with initial state.
        
        Args:
            initial_state: Initial state value
            on_change: Optional callback invoked on state changes
        """
        self._state = initial_state
        self._listeners: list[Callable[[], None]] = []
        self._on_change = on_change
        self._update_count = 0
    
    def get_state(self) -> T:
        """Get current state."""
        return self._state
    
    def set_state(self, updater: Callable[[T], T]) -> None:
        """Update state using an updater function.
        
        Args:
            updater: Function that takes current state and returns new state
        """
        prev = self._state
        next_state = updater(prev)
        
        # Skip no-op updates
        if next_state is prev:
            return
        
        # Invoke change callback
        if self._on_change:
            self._on_change(next_state, prev)
        
        self._state = next_state
        self._update_count += 1
        
        # 通知订阅者
        for listener in self._listeners:
            try:
                listener()
            except Exception:
                # Don't let listener errors break state updates
                pass
    
    def subscribe(self, listener: Callable[[], None]) -> Callable[[], None]:
        """Subscribe to state changes.
        
        Args:
            listener: Callback invoked on state changes
        
        Returns:
            Unsubscribe function
        """
        self._listeners.append(listener)
        
        def unsubscribe():
            if listener in self._listeners:
                self._listeners.remove(listener)
        
        return unsubscribe
    
    @property
    def update_count(self) -> int:
        """Number of state updates."""
        return self._update_count
    
    @property
    def subscriber_count(self) -> int:
        """Number of active subscribers."""
        return len(self._listeners)
```

**Reducer 模式**

Reducer = 一个纯函数，接收旧状态，返回新状态

传统方式（直接修改）
```python
state.tool_calls += 1  # 不安全，难以追踪
```

Reducer 模式（函数式更新）
```python
def increment_tool_calls() -> Callable[[AppState], AppState]:
    """返回一个 reducer 函数"""
    def updater(state: AppState) -> AppState:
        state.tool_calls += 1
        state.update_timestamp()
        return state
    return updater
```

使用
```python
store.set_state(increment_tool_calls())
```

优势：
- 可预测：每次更新都是函数调用
- 可追踪：可以记录每次更新
- 可订阅：状态变化时自动通知
- 线程安全：避免并发修改问题

**订阅机制**

TUI 订阅状态变化
```python
unsubscribe = store.subscribe(lambda: rerender())
```

状态变化 → 自动触发 rerender()
```python
store.set_state(set_busy("read_file"))
```
listener() 被调用 → rerender() → UI 更新

#### 3.9 System Prompt 构建

```python
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
```

作用：
- 构建 system prompt（系统提示）
- 注入权限信息、技能、MCP 服务器、Memory 上下文

System Prompt 包含：
```text
System Prompt:
    ├─ 工作目录信息
    ├─ 权限摘要
    ├─ 可用技能
    ├─ MCP 服务器
    ├─ Memory 上下文
    └─ Governance Rules (治理规则)
```
memory_mgr.get_relevant_context() 根据当前任务检索相关记忆，作为上下文注入到 system prompt 中。这实现了跨会话的知识保持

**走到 get_relevant_context 的时候不是还没接收用户的输入，那是如何知道“当前任务”的？**

```text
用户输入: "帮我优化数据库查询性能"
    ↓
get_relevant_context(query="帮我优化数据库查询性能")
    ↓
1. 分词: ["优化", "数据库", "查询", "性能"]
    ↓
2. 扩展: ["优化", "数据库", "database", "查询", "query", "性能", "performance"]
    ↓
3. TF-IDF 计算
    ↓
4. 返回最相关的记忆:
    - "之前用索引优化了用户表的查询"
    - "数据库连接池配置"
    - "PostgreSQL 查询优化最佳实践"
```


#### 3.10 History 和 Transcript

```python
    history = load_history_entries()
    transcript: list[TranscriptEntry] = []
```

作用：
- history: 加载历史输入记录（用于上下箭头浏览历史）
- transcript: 当前会话的对话记录（用于保存和显示）

### Phase 4 : Banner 显示

```python
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
```

banner 内容：
```python
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
```

### Phase 5 : 主执行循环

两条路径：

路径 A : 非 TTY 模式
```python 
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
```

执行流程：

1.stdin 输入：

2.判断输入类型：
- /exit -> break 退出
- `/transcript-save <path>` -> 保存 transcript
- Memory 命令 -> memory_mgr.handle_user_memory_input()
- 本地命令 -> _handle_local_command()
例如 /tools /skills /help 等本地命令
- 工具快捷方式 -> tools.execute()
例如 `/grep <pattern>` `/cmd <command>`
- 普通用户输入 -> run_agent_turn

这是命令路由模式，不同命令需要不同的处理器：
- /exit 等控制命令：直接处理
- Memory 命令：需要 MemoryManager 处理持久化
- 本地命令：不调用 LLM，节省成本和延迟
- 工具快捷方式：直接执行工具，不经过 LLM
- 普通输入：需要 LLM 理解和执行"

Transcript = 对话记录

Transcript vs History vs Messages

History (历史命令):
- 用户输入的命令历史
- 用上下箭头浏览
- 存储在 ~/.mini-code/history.json
- 例如：["ls", "pwd", "help"]

Transcript (对话记录):
- 完整的对话记录
- 包含 user, assistant, tool 的所有交互
- 用于保存和回看
- 例如：
    [
      {kind: "user", body: "帮我分析代码"},
      {kind: "assistant", body: "好的，我来..."},
      {kind: "tool", body: "读取文件...", toolName: "read_file"},
    ]

Messages (消息历史):
- LLM API 的消息格式
- 只包含 role + content
- 用于传递给 LLM
- 例如：
    [
      {role: "system", content: "你是..."},
      {role: "user", content: "帮我分析代码"},
      {role: "assistant", content: "好的，我来..."},
    ]

Transcript 的用途：

```python
# 保存对话记录：
/transcript-save conversation.txt

# 输出
# User: 帮我分析代码
# Assistant: 好的，我来...
# Tool: read_file → 读取文件...
```

/transcript-save 的使用场景：

Session vs Transcript

Session (会话):
  - 自动保存到 ~/.mini-code/sessions/<id>.json
  - 包含 messages 历史
  - 用于恢复会话（--resume）
  - 格式：LLM API 格式

Transcript (对话记录):
  - 需要手动保存
  - 包含用户、助手、工具的完整交互
  - 用于分享、记录、回看
  - 格式：人类可读的文本

使用场景

场景1：分享解决方案
```text
用户解决了一个复杂的 bug
用户: "帮我调试这个连接池泄露问题"
Agent: "好的，我来分析..." (经过多轮工具调用)
Agent: "问题找到了，是连接未关闭..."

用户想分享给同事
/transcript-save debugging-session.md

输出文件：
debugging-session.md:
  User: 帮我调试这个连接池泄露问题
  Assistant: 好的，我来分析...
  Tool: grep_files → 查找连接池相关代码
  Tool: read_file → 查看 ConnectionPool.java
  Assistant: 问题找到了...
```
场景2：记录技术决策
```text
技术方案讨论
用户: "帮我设计用户认证系统"
Agent: "我建议使用 JWT + Refresh Token..."

/transcript-save auth-design-2024-07.md

作为技术文档归档
```
场景3：教学和演示
```text
演示如何使用 MiniCode
用户: "教我如何用 TDD 方式实现登录功能"

/transcript-save tdd-login-demo.md

分享给学生或同事
```
场景4：问题复现
```text
# 遇到奇怪的问题，想记录下来
用户: "为什么这个查询这么慢？"
Agent: "我发现缺少索引..."

/transcript-save performance-issue-2024-07.md
```

为什么 Claude Code 不需要？因为 Claude Code 有不同的设计：

Claude Code:
- Web UI 有完整的对话历史
- 可以随时在 UI 中查看和导出
- 有 Projects 功能，自动保存

MiniCode:
- 纯终端运行
- 没有 Web UI
- 需要手动导出为可读格式

MiniCode 的自动保存：

tty_app.py 
```python
if state.autosave and _autosave_counter >= _AUTOSAVE_CHECK_INTERVAL:
    _autosave_counter = 0
    state.autosave.save_if_needed()  # ← 自动保存 session
```
自动保存的是 Session（JSON 格式），不是 Transcript（文本格式）

路径 B : TTY 模式
```python
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
```
作用：
- 启动全屏 TUI（Terminal User Interface）
- 事件驱动架构
- 实时渲染和交互

### Phase 6 : 清理和退出

```python
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
```

清理流程：

1.用户 Ctrl + C

2.KeyBoardInterrupt 异常

3.打印中断消息

4.finally 块：
- logger.info("Shutting down...")
- tools.dispose() -> 关闭 MCP 连接
- logger.info("Shutdown complete")

使用 finally 块，确保资源清理代码一定会执行，即使发生异常或用户中断。MCP 连接如果不正确关闭，可能导致服务器端资源泄露

MCP 调用流程

```text
Agent: 我需要深度思考
    ↓
LLM 返回工具调用: {"toolName": "sequential_thinking", "input": {...}}
    ↓
MiniCode 发现这是 MCP 工具
    ↓
通过 JSON-RPC 发送到 MCP Server:
    {
      "jsonrpc": "2.0",
      "method": "tools/call",
      "params": {
        "name": "sequential_thinking",
        "arguments": {...}
      }
    }
    ↓
MCP Server 处理并返回结果
    ↓
MiniCode 将结果返回给 Agent
```

## 总结

Phase 1 : 初始化准备
- 字符集配置
- 准备好可能的参数
- 日志初始化
- 做好处理特殊命令的准备

Phase 2 : 加载Runtime配置：环境变量/配置文件，包括：
- provider
- 用户偏好
- 认证方式
- 用户个人信息
- 模型
- 最大输出token
- 端点

Phase 3 : 核心组件初始化
- Prompt Handler: 显示权限请求、获取用户决策，负责 UI 交互
- 工具注册
- Permission Manager: 权限核心管理组件，负责检查权限（路径/命令是否允许）、存储权限状态（session/persistent）、调用 Prompt Handler 获取用户决策、根据决策更新权限配置
- Model Adapter: 屏蔽各provider的底层API差异
- Context Manager: 管理上下文窗口、估算token、达到阈值进行压缩
- Memory Manager: 跨会话记忆保持、支持查询扩展与TF-IDF检索
- User Profile Manager: 管理用户个人资料与喜好
- App State Store: 统一的状态管理，管理事件触发与监听
- 构建 System Prompt: 注入权限信息、Skills、mcp-servers、记忆
- 加载历史记录
- 声明好transcript数组存储会话的对话记录

Phase 4 : Banner 显示

Phase 5 : 主执行循环
- 如果是非 TTY 模式，就接收用户的 input（这里也是用户输入文本、按下回车后第一步经过的代码，前面是MiniCode跑起来时做的准备工作），根据输入类型调用不同的函数进行处理
- TTY 模式的话，见 interview2.md

Phase 6 : 清理与退出
- 用户 Ctrl + C
- 抛 KeyBoardInterrupt 异常
- finally 块中打印日志+关闭MCP连接