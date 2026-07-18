# MiniCode 项目详解2：主链路 + 基础流程(tty_app.py)

## 一、核心数据结构

### 1.1 TtyAppArgs vs ScreenState

tui/state.py
```python
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
```
分离关注点：

TtyAppArgs(不可变配置):
- main.py 传入的参数
- 整个会话期间不变
- 类似于依赖注入

ScreenState(可变状态):
- UI 相关的状态
- 用户交互过程中不断变化
- 需要持久化和恢复

## 二、Phase 1 : 初始化

### 2.1 会话加载
```python
    if handle_session_listing(cwd, list_sessions_only):
        return messages

    session = load_or_create_session(cwd, resume_session)
```

会话加载逻辑：
```python
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
```

会话存储位置：
```text
~/.mini-code/sessions/
    ├─ a1b2c3d4e5f6...json  # 会话1
    ├─ f7e8d9c0b1a2...json  # 会话2
    └─ ...
```

### 2.2 状态构建
```python
    # 状态构建
    args, state = build_tty_runtime_state(
        runtime,
        tools,
        model,
        messages,
        cwd,
        permissions,
        session,
        memory_manager,
        context_manager,
    )
```
build_tty_runtime_state 流程：
```python
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
```
**理解数据分类**

数据分为两类：

配置数据：
- 运行时配置
- 整个会话期间不变
- main.py 传入，无需修改
- 例如：runtime, tools, model, cwd

运行时数据：
- 会话过程中产生和变化的数据
- 需要持久化（保存到文件）
- 需要恢复（从文件加载）
- 例如：history, session, autosave

**history 为什么在 ScreenState?**
```text
history（历史命令）会动态变化：
用户输入 "ls"
    ↓
history.append("ls")   ← 添加到 history
    ↓
用户按上箭头
    ↓
history_index -= 1      ← 浏览历史
    ↓
用户选择历史命令 "ls"
    ↓
history_index = len(history)   ← 重置索引
```
关键点：
- history 在用户交互过程中不断变化
- history 需要持久化（保存到 ~/.mini-code/history.json）
- history 需要恢复（启动时加载）

如果放在 TtyAppArgs（不可变配置）：
```python
args.history.append("ls")  # ← 不能修改，违反设计原则
args.history = new_history  # ← 需要重新赋值
```
放在 ScreenState（可变状态）：
```python
state.history.append("ls")  # ← 直接修改，自然流畅
```
**autosave 为什么在 ScreenState?**
```python
# autosave 管理器会在运行时使用：
while not should_exit:
    # 定期检查是否需要保存
    if state.autosave:
        state.autosave.save_if_needed()  # ← 动态调用
```
关键点：
- autosave 是一个运行时服务（管理自动保存）
- 需要定期调用（在事件循环中）
- 需要保存状态（会话结束时）

**session 为什么在 ScreenState?**
```python
# session 数据会在运行时更新：
state.session.messages = args.messages  # ← 更新消息
state.session.history = state.history   # ← 更新历史
state.session.transcript_entries = [...] # ← 更新对话记录

# 最后保存：
save_session(state.session)  # ← 持久化到文件
```
关键点：
- session 是持久化的载体（保存会话数据）
- 运行时需要频繁更新
- 会话结束需要强制保存

**为什么 if session.messages 代表 “如果恢复会话”？**

理解 session 的来源
```python
# 场景1：新会话
session = create_new_session(workspace=workspace)
# 创建的新 session：
# {
#     "session_id": "a1b2c3...",
#     "messages": [],           ← 空列表
#     "transcript_entries": [], ← 空列表
#     "history": [],
# }

# 场景2：恢复会话
session = load_session(resume_session)
# 从文件加载的 session：
# {
#     "session_id": "a1b2c3...",
#     "messages": [              ← 有内容
#         {"role": "user", "content": "帮我分析代码"},
#         {"role": "assistant", "content": "..."},
#     ],
#     "transcript_entries": [...], ← 有内容
#     "history": ["ls", "pwd"],
# }
```

判断逻辑
```python
if session.messages:
    # session.messages 非空 → 说明是从文件加载的 → 恢复会话
    args.messages.clear()
    args.messages.extend(session.messages)  # 恢复消息
    for entry_data in session.transcript_entries:
        state.transcript.append(TranscriptEntry(**entry_data))  # 恢复对话记录
else:
    # session.messages 为空 → 说明是新创建的 → 新会话
    # 不需要恢复，直接开始
    pass
```
为什么可以这样判断？

因为新会话的 messages 一定是空的
```python
# 新会话创建流程
def create_new_session(workspace: str) -> SessionData:
    return SessionData(
        session_id=generate_id(),
        workspace=workspace,
        messages=[],           # ← 空列表
        transcript_entries=[], # ← 空列表
        history=[],           # ← 空列表
        created_at=time.time(),
    )

# 恢复会话加载流程
def load_session(session_id: str) -> SessionData | None:
    path = get_session_path(session_id)
    data = json.loads(path.read_text())
    return SessionData(
        session_id=data["session_id"],
        messages=data["messages"],  # ← 可能为空，但通常有内容
        transcript_entries=data["transcript_entries"],
        # ...
    )
```
所以：
- session.messages 非空 → 肯定是恢复会话（因为新会话是空的）
- session.messages 为空 → 新会话（或恢复了一个空会话）

### 2.3 渲染器初始化
```python
    # 节流渲染器：合并快速 rerender() 调用以减少闪烁
    throttled = _ThrottledRenderer(lambda: _render_screen(args, state), min_interval=0.016)

    def rerender() -> None:
        throttled.request()
```
节流渲染(Throttled Renderer) 的必要性：
```text
问题：
  用户输入 "h"
    ↓
  rerender()  # 渲染：input="h"

  用户输入 "e"
    ↓
  rerender()  # 渲染：input="he"

  用户输入 "l"
    ↓
  rerender()  # 渲染：input="hel"

  用户输入 "l"
    ↓
  rerender()  # 渲染：input="hell"

  用户输入 "o"
    ↓
  rerender()  # 渲染：input="hello"

结果：5 次渲染，屏幕闪烁
```
解决：
```text
  ThrottledRenderer 节流：
    min_interval=0.016  # 最小间隔 16ms (60fps)

  用户输入 "h", "e", "l", "l", "o"
    ↓
  只在最后一次渲染：
  rerender()  # 渲染：input="hello"

结果：1 次渲染，流畅
```
_ThrottledRenderer 实现：
```python
class _ThrottledRenderer:
    __slots__ = ("_render_fn", "_min_interval", "_pending", "_last_render_time", "_lock")

    def __init__(self, render_fn: Callable[[], None], min_interval: float = 0.033) -> None:
        self._render_fn = render_fn
        self._min_interval = min_interval
        self._pending = False
        self._last_render_time: float = 0.0
        self._lock = threading.Lock()

    def request(self) -> None:
        """请求渲染（异步）"""
        with self._lock:
            self._pending = True

    def flush(self) -> None:
        """执行渲染（如果需要）"""
        now = time.monotonic()
        with self._lock:
            if not self._pending: # 没有待渲染
                return
            if now - self._last_render_time < self._min_interval: # 间隔太短
                return
            self._pending = False
            self._last_render_time = now
        self._render_fn()

    def force(self) -> None:
        with self._lock:
            self._pending = False
            self._last_render_time = time.monotonic()
        self._render_fn() # 执行渲染
```

**为什么 request 是异步请求渲染？**
```python
# request() - 异步请求
def request(self) -> None:
    with self._lock:
        self._pending = True  # ← 设置标志："需要渲染"
    # ← 立即返回，不执行渲染

# flush() - 条件执行
def flush(self) -> None:
    now = time.monotonic()
    with self._lock:
        if not self._pending:  # ← 没有待渲染，跳过
            return
        if now - self._last_render_time < self._min_interval:  # ← 间隔太短，跳过
            return
        self._pending = False
        self._last_render_time = now
    self._render_fn()  # ← 真正执行渲染

# force() - 强制执行
def force(self) -> None:
    with self._lock:
        self._pending = False
        self._last_render_time = time.monotonic()
    self._render_fn()  # ← 立即执行渲染，无视间隔
```
调用流程
```python
# 事件处理中调用 request()
def rerender():
    throttled.request()  # ← 只是"请求"，不立即渲染

# 主循环空闲时调用 flush()
while not should_exit:
    # ...
    if not ready:  # 没有输入
        throttled.flush()  # ← 空闲时才真正执行渲染
    # ...
```
为什么说是"异步"：
- 请求渲染和执行渲染是分离的
- request() 只是"登记需求"
- flush() 才是"真正执行"

示例：
```text
  用户输入 "h"
      ↓
  rerender() → request() → 设置 _pending=True
      ↓
  立即返回（不渲染）
      ↓
  用户继续输入 "e", "l", "l", "o"
      ↓
  每次 request() → _pending=True（重复设置，但只渲染一次）
      ↓
  没有输入了（空闲）
      ↓
  flush() → 检查 _pending=True，间隔足够 → 执行渲染
      ↓
  渲染 "hello"（只渲染一次）
```

### 2.4 权限提示安装
```python
approval_event, approval_result, _ = install_permission_prompt(args, state, rerender)
```
权限提示机制：
```python
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
```

流程图：
```text
PermissionManager 检测到需要审批
    ↓
调用 args.permissions.prompt(request)
    ↓
_permission_prompt_handler:
    1. 设置 state.pending_approval
    2. rerender() → 显示权限提示 UI
    3. approval_event.wait() ← 阻塞，等待用户输入
    ↓
用户输入选择
    ↓
event_flow.py:
    approval_result["decision"] = "allow_once"
    approval_event.set()  ← 唤醒等待的线程
    ↓
_permission_prompt_handler 继续执行：
    4. 获取 result
    5. 清除 UI
    6. 返回 result
    ↓
PermissionManager 继续执行
```

## 三、Phase 2 : 运行时准备
```python
    enter_tty_runtime()

    # On Unix, listen for SIGWINCH so terminal resizes are picked up
    # immediately rather than waiting for the 0.5s cache TTL.
    # signal.signal() can only be called from the main thread.
    _prev_sigwinch = install_sigwinch_rerender(throttled)
```

### 3.1 entry_tty_runtime()
```python
def enter_tty_runtime() -> None:
    enter_alternate_screen() # 进入备用屏幕缓冲区
    hide_cursor() # 隐藏光标
```
Alternate Screen(备用屏幕):
```text
普通模式：
  $ minicode-py
  > 用户输入
  > Agent 响应
  > 用户输入
  > Agent 响应
  $  ← 退出后，历史记录还在

Alternate Screen 模式：
  ┌────────────────────────────┐
  │ MiniCode - 全屏 TUI         │  ← 专属屏幕
  │ ...                        │
  └────────────────────────────┘

  退出后 → 回到普通屏幕，没有历史记录
```

**Alternate Screen（备用屏幕）**

终端有两种屏幕缓冲区：

主屏幕缓冲区：
- 默认模式
- 滚动历史保留
- 退出程序后，历史记录可见
- 类似：在终端运行 ls、vim 等

备用屏幕缓冲区：
- 全屏应用专用
- 独立的"画布"
- 退出时，回到主屏幕
- 主屏幕的内容不变
- 类似：vim、htop、tmux

### 3.2 SIGWINCH 信号处理

```python
def install_sigwinch_rerender(throttled: _ThrottledRenderer) -> object | None:
    if sys.platform == "win32" or threading.current_thread() is not threading.main_thread():
        return None # Windows 不支持 SIGWINCH

    import signal as _signal

    def _on_sigwinch(_signum: int, _frame: object) -> None:
        invalidate_terminal_size_cache() # 清除终端大小缓存
        throttled.request() # 请求重新渲染

    try:
        return _signal.signal(_signal.SIGWINCH, _on_sigwinch)
    except (OSError, ValueError):
        return None
```

**SIGWINCH 是什么？**

SIGWINCH = "Signal: Window Change"（窗口大小变化信号）

完整名称：
- SIG - Signal（信号）
- WIN - Window（窗口）
- CH - Change（变化）

含义：终端窗口大小发生变化时，内核发送的信号

触发时机
```text
用户调整终端窗口大小：
  - 拖动窗口边框
  - 最大化/最小化
  - 改变字体大小（某些终端）
  ↓
内核检测到窗口大小变化
  ↓
发送 SIGWINCH 信号给前台进程组
  ↓
进程的 SIGWINCH 处理器被调用
```
MiniCode 的 SIGWINCH 处理器
```python
def _on_sigwinch(_signum: int, _frame: object) -> None:
    # 1. 清除终端大小缓存
    invalidate_terminal_size_cache()

    # 2. 请求重新渲染
    throttled.request()
```
为什么需要这个处理器？

问题：
```text
  终端窗口大小变化
  ↓
  如果不重新计算：
    - 渲染的布局可能超出窗口边界
    - 文字被截断或错位
  ↓
  UI 混乱
```
解决：
```text
  窗口大小变化 → SIGWINCH → 处理器：
    1. 清除旧的终端大小缓存
    2. 请求重新渲染
  ↓
  下一帧渲染时：
    - 重新获取新的终端大小
    - 根据新大小重新布局
    - UI 正常显示
```
为什么要恢复 SIGWINCH 处理器？
```python
def install_sigwinch_rerender(throttled):
    # 保存原来的处理器
    return _signal.signal(_signal.SIGWINCH, _on_sigwinch)
    #      ↑ 返回旧的处理器

def exit_tty_runtime(prev_sigwinch):
    # 恢复原来的处理器
    if prev_sigwinch is not None:
        _signal.signal(_signal.SIGWINCH, prev_sigwinch)
```
原因：

信号处理器是进程级别的设置：
- 修改会影响整个进程
- MiniCode 退出后，进程还在（shell）
- 如果不恢复，shell 的 SIGWINCH 处理会被破坏

示例：
```text
启动 MiniCode：
install_sigwinch_rerender() → 设置 MiniCode 的处理器
_prev_sigwinch = shell 的默认处理器

退出 MiniCode：
exit_tty_runtime(_prev_sigwinch) → 恢复 shell 的处理器

回到 shell：
shell 的 SIGWINCH 处理正常工作
```
如果不恢复会怎样？
```text
启动 MiniCode：
  设置 MiniCode 的 SIGWINCH 处理器

退出 MiniCode（不恢复）：
  shell 继续使用 MiniCode 的处理器
  ↓
  用户调整窗口大小
  ↓
  触发 MiniCode 的处理器
  ↓
  尝试调用 throttled.request()
  ↓
  但 throttled 对象已经不存在了
  ↓
  可能崩溃或异常
```
完整流程
```text
启动 MiniCode：
  ├─ 保存 shell 的 SIGWINCH 处理器（_prev_sigwinch）
  ├─ 设置 MiniCode 的 SIGWINCH 处理器
  └─ 进入事件循环

运行中：
  用户调整窗口大小
    ↓
  内核发送 SIGWINCH
    ↓
  MiniCode 处理器：
    ├─ 清除终端大小缓存
    └─ 请求重新渲染

退出 MiniCode：
  ├─ 恢复 shell 的 SIGWINCH 处理器
  ├─ 清理其他资源
  └─ 回到 shell
```

SIGWINCH 是 Unix 特有的信号机制

Unix/Linux:
- 信号是进程间通信机制
- SIGWINCH 专门用于"窗口大小变化"通知
- 内核会在窗口大小变化时发送这个信号

Windows:
- 没有同样的信号机制
- 使用不同的控制台 API
- 需要主动轮询检查窗口大小

Windows 如何支持窗口大小变化？

Windows 使用主动轮询：

```python
# tui/chrome.py 中的实现
def _cached_terminal_size() -> tuple[int, int]:
    """Get terminal size with caching."""
    global _terminal_size_cache

    # 每次渲染前都检查大小
    now = time.time()
    if _terminal_size_cache and (now - _terminal_size_cache["time"]) < 0.5:
        return _terminal_size_cache["size"]  # 使用缓存

    # 主动获取新大小
    size = shutil.get_terminal_size(fallback=(80, 24))
    _terminal_size_cache = {"size": size, "time": now}
    return size
```
流程：
```text
Windows 窗口大小调整：
    ↓
没有 SIGWINCH 信号
    ↓
依赖缓存机制（0.5 秒 TTL）
    ↓
每次渲染前检查：
    if 缓存过期 or 窗口大小变化:
        重新获取大小
        更新布局
    ↓
通过定期检查实现"响应窗口大小变化"
```
Unix vs Windows 对比
```text
Unix/Linux:
  窗口大小变化 → 内核发送 SIGWINCH → 信号处理器立即响应
  ↓
  响应速度快（毫秒级）

Windows:
  窗口大小变化 → 无通知
  ↓
  等待下次渲染检查（缓存过期，最多 0.5 秒）
  ↓
  响应速度慢（最多 500ms 延迟）
```
为什么 Windows 不实现类似机制？

历史原因：
- Windows 控制台 API 设计不同
- 没有 Unix 的信号机制
- 微软推荐使用轮询方式

现代方案：
- Windows 10+ 支持 VT（Virtual Terminal）序列
- 可以通过 API 查询窗口大小
- 但仍需要主动轮询


## 四、Phase 3 : 事件循环（核心）

### 4.1 事件循环骨架
```python
try:
    _render_screen(args, state)  # 初始渲染

    with _RawModeContext():       # 进入 Raw Mode
        while not should_exit:    # 主循环
            # 1. Autosave 检查
            # 2. Agent 线程状态检查
            # 3. 键盘输入读取
            # 4. 输入解析
            # 5. 事件分发

finally:
    exit_tty_runtime(_prev_sigwinch)
    finalize_tty_session(args, state)
```

### 4.2 Raw Mode

Canonical Mode（规范模式，默认）：
- 用户输入 "hello"
- 终端缓冲，直到按下回车
- 程序收到 "hello\n"
- 支持退格、删除等编辑

Raw Mode（原始模式）：
- 用户输入 "h"
- 程序立即收到 "h"（无需等待回车）
- 用户输入 "e"
- 程序立即收到 "e"
- 所有按键都原样传递

_RawModeContext 实现：
```python
class _RawModeContext:
    """Context manager for raw terminal mode.

    On Unix: switches stdin to raw mode via termios/tty and restores on exit.
    On Windows: msvcrt provides character-at-a-time input natively, but we
    need to ensure the console code page is set for UTF-8 and VT processing
    is enabled.
    """

    def __init__(self) -> None:
        self._old_settings: Any = None
        self._old_cp: int | None = None
        self._old_sigwinch: Any = None

    def __enter__(self) -> _RawModeContext:
        if sys.platform == "win32":
            # Ensure VT processing is active (idempotent)
            from minicode.tui.screen import _enable_windows_vt_processing
            # Windows: 启用 VT 处理和 UTF-8
            _enable_windows_vt_processing()
            # Switch console to UTF-8 code page for proper Unicode handling
            try:
                import ctypes
                kernel32 = ctypes.windll.kernel32  # type: ignore[attr-defined]
                self._old_cp = kernel32.GetConsoleOutputCP()
                kernel32.SetConsoleOutputCP(65001)  # UTF-8
            except Exception:
                pass
        else:
            # Unix: 使用 termios 设置 raw mode
            import termios
            import signal

            fd = sys.stdin.fileno()
            self._old_settings = termios.tcgetattr(fd) # 保存原始设置
            new = termios.tcgetattr(fd)

            # Wire SIGWINCH to invalidate terminal size cache on resize
            try:
                import signal

                def _on_resize(signum, frame):
                    from minicode.tui.chrome import invalidate_terminal_size_cache
                    invalidate_terminal_size_cache()
                self._old_sigwinch = signal.signal(signal.SIGWINCH, _on_resize)
            except (ImportError, AttributeError):
                pass  # Windows or no SIGWINCH support
            # Input flags: disable CR→NL translation and XON/XOFF flow control,
            # strip high bit, and break signal generation.
            new[0] &= ~(
                termios.BRKINT | termios.ICRNL | termios.INPCK
                | termios.ISTRIP | termios.IXON
            )
            # Output flags: KEEP OPOST so that \n → \r\n translation still
            # works.  tty.setraw() clears OPOST which causes "staircase"
            # output on Linux/macOS — every newline only moves down without
            # returning the cursor to column 0.
            # new[1] is intentionally left untouched.
            # Control flags: set 8-bit chars
            new[2] &= ~(termios.CSIZE | termios.PARENB)
            new[2] |= termios.CS8
            # Local flags: disable echo, canonical mode, extended processing,
            # and signal generation from keys (Ctrl-C, Ctrl-Z).
            # 禁用回显、规范模式、信号生成
            new[3] &= ~(
                termios.ECHO | # 禁用回显，用户输入不显示在屏幕上
                termios.ICANON |  # 禁用规范模式，按键立即传递，不等待回车
                termios.IEXTEN |  # 禁用扩展处理
                termios.ISIG # 禁用信号生成，Ctrl+C 不发送信号，原样传递
            )
            # Special characters: read returns after 1 byte, no timeout.
            # 设置立即返回
            new[6][termios.VMIN] = 1 # 至少读取1字节
            new[6][termios.VTIME] = 0 # 无超时
            termios.tcsetattr(fd, termios.TCSAFLUSH, new) # 应用新设置
        return self

    def __exit__(self, *_: Any) -> None:
        if sys.platform == "win32":
            if self._old_cp is not None:
                try:
                    import ctypes
                    ctypes.windll.kernel32.SetConsoleOutputCP(self._old_cp)  # type: ignore[attr-defined]
                except Exception:
                    pass
        # 恢复原始设置
        elif self._old_settings is not None:
            import termios
            import signal

            termios.tcsetattr(sys.stdin.fileno(), termios.TCSADRAIN, self._old_settings)
            if getattr(self, '_old_sigwinch', None) is not None:
                try:
                    import signal
                    signal.signal(signal.SIGWINCH, self._old_sigwinch)
                except Exception:
                    pass
```

_RawModeContext 的核心作用：将终端从"规范模式"切换到"原始模式"

规范模式vs 原始模式

Canonical Mode（规范模式，默认）：
- 缓冲输入：用户输入 → 终端缓冲 → 按回车 → 程序收到
- 行编辑：支持退格、删除、左右箭头
- 回显：输入的字符立即显示在屏幕上
- 特殊字符处理：Ctrl+C → 发送 SIGINT 信号

示例：
- 用户输入：h-e-l-l-o-退格-回车
- 程序收到："hell\n"（退格删除了 'o'）

Raw Mode（原始模式）：
特点：
- 无缓冲：每次按键立即传递给程序
- 无行编辑：退格、删除等原样传递
- 无回显：输入的字符不显示（程序自己控制显示）
- 无特殊处理：Ctrl+C 原样传递（'\x03'）

示例：
- 用户输入：h-e-l-l-o-退格-回车
- 程序收到：'h', 'e', 'l', 'l', 'o', '\x7f', '\r'（每个字符单独接收）

**禁用回显**

有回显：用户输入 'h' → 终端显示 'h' → 程序收到 'h'

无回显：用户输入 'h' → 终端不显示 → 程序收到 'h'

为什么禁用？
- TUI 自己控制显示（光标位置、颜色等）
- 避免重复显示

**禁用规范模式**

规范模式：用户输入 'h', 'e', 'l', 'l', 'o' → 缓冲 → 按回车 → 程序收到 "hello\n"

原始模式：用户输入 'h' → 程序立即收到 'h', 用户输入 'e' → 程序立即收到 'e'

为什么禁用？
- TUI 需要实时响应（上下箭头、快捷键等）
- 不能等待回车

**禁用信号生成**

有信号生成：用户按 Ctrl+C → 内核发送 SIGINT → 程序终止

无信号生成：用户按 Ctrl+C → 程序收到 '\x03' → 程序自己决定如何处理

为什么禁用？
- TUI 可能需要自定义 Ctrl+C 行为（如取消当前操作）
- 或者传递给 Agent（如中断 LLM 响应）

为什么需要 Raw Mode？

需求：实现全屏 TUI，支持：
- 实时响应用户输入（无需等待回车）
- 自定义显示（光标位置、颜色）
- 捕获特殊按键（上下箭头、Ctrl+C等）

方案：
- 使用 Raw Mode → 完全控制输入输出
- 程序自己处理所有按键逻辑

### 4.3 Autosave 检查
```python
# Autosave 检查
_autosave_counter += 1
if state.autosave and _autosave_counter >= _AUTOSAVE_CHECK_INTERVAL:
    _autosave_counter = 0
    state.autosave.save_if_needed()
```
节流策略：

_AUTOSAVE_CHECK_INTERVAL = 100  # 100 次迭代

每次迭代 ≈ 30ms （具体取决于实际迭代时间）

100 次迭代 ≈ 3 秒

所以：每 3 秒检查一次是否需要保存

为什么不在每次改变时都保存？
- 每次按键都保存 → 磁盘 IO 频繁 → 性能下降
- 每 3 秒检查一次 → 平衡性能和数据安全

### 4.4 Agent 线程状态检查
```python
# Agent 线程状态检查
agent_result_data = state.agent_result
lock = getattr(state, "agent_lock", None)
if agent_result_data is not None and lock is not None and agent_result_data.get("done"):
    with lock:
        if agent_result_data.get("messages"):
            args.messages = agent_result_data["messages"]
        agent_result_data["done"] = False  # 重设标志位
```
线程模型：
```text
┌──────────────────────────────────────────────────────
│ 主线程                                     
│  - 运行事件循环                                        
│  - 处理用户输入                                        
│  - 渲染 UI                                             
│                                                        
│  定期检查：                                            
│    if agent_result["done"]:                           
│        args.messages = agent_result["messages"]       
└──────────────────────────────────────────────────────
        ↑ 检查
        │
┌──────────────────────────────────────────────────────
│ 后台线程                                   
│  - 运行 run_agent_turn()                              
│  - 调用 LLM API                                       
│  - 执行工具                                           
│                                                        
│  完成后：                                              
│    agent_result["messages"] = new_messages            
│    agent_result["done"] = True                        
└──────────────────────────────────────────────────────
```

为什么用后台线程？
```text
同步执行：
  用户: "帮我分析代码"
      ↓
  Agent 开始执行（调用 LLM、执行工具...）
      ↓
  主线程阻塞 → UI 冻结 → 无法响应用户输入
      ↓
  5 秒后完成 → UI 恢复

异步执行（后台线程）：
  用户: "帮我分析代码"
      ↓
  启动后台线程 → Agent 执行
      ↓
  主线程继续 → UI 响应 → 用户可以输入
      ↓
  Agent 完成 → 主线程检查到结果 → 更新 UI
```

主线程在每次事件循环迭代时都检查
```python
while not should_exit:
    # 每次循环开始时检查 Agent 线程状态
    agent_result_data = state.agent_result
    lock = getattr(state, "agent_lock", None)
    if agent_result_data is not None and lock is not None and agent_result_data.get("done"):
        with lock:
            if agent_result_data.get("messages"):
                args.messages = agent_result_data["messages"]
            agent_result_data["done"] = False

    # 然后等待输入（最多 50ms）
    ready, _, _ = select.select([_fd], [], [], 0.05)  # 50ms 超时

    # 如果没有输入，继续下一轮循环（再次检查 Agent 状态）
    if not ready:
        continue

    # 有输入，处理输入...
```
检查频率取决于两个因素：

1.事件循环速度：
- 最快：立即（无输入时，select 立即返回）
- 最慢：50ms（有输入时，等待处理完成）

2.实际场景：
- 用户不输入：每 50ms 检查一次
- 用户快速输入：每次输入后检查
- 用户慢速输入：输入间隙检查（取决于处理时间）

所以：
- 最快检查间隔：< 1ms（无输入，select 立即返回）
- 最慢检查间隔：约 50ms（等待输入超时）
- 典型检查间隔：50ms 左右

为什么是 50ms ?
- 太短：CPU 占用高，频繁空转
- 太长：响应慢，用户感觉卡顿
- 50ms: 平衡性能和响应速度

完整流程图
```text
事件循环迭代：
    ↓
检查 Agent 状态（agent_result["done"]）
    ↓
等待输入（select.select，最多 50ms）
    ↓
有输入？
    ├─ 是 → 处理输入 → 渲染 → 下一轮迭代
    └─ 否 → 继续下一轮迭代（再次检查 Agent 状态）
```

即select.select() 的行为：
```python
ready, _, _ = select.select([_fd], [], [], 0.05)
#           监听的文件描述符    ↑    ↑
#                                   │    超时时间（秒）
#                                   写入文件描述符（不关心）
```
```text
情况1：有输入（就绪）
  select.select 立即返回（不管是否超时）
  ready = [stdin]
  ↓
  程序读取输入并处理

情况2：开始时无输入，最终超时
  select.select 等待 50ms
  仍然没有输入，超时返回
  ready = []
  ↓
  程序跳过处理，继续下一轮循环

情况3：开始时无输入，但中途有数据到达（在 50ms 内）
  select.select 等待最多 50ms
  在等待期间，有输入到达
  立即返回（不等待完整的 50ms）
  ready = [stdin]
  ↓
  程序读取输入并处理
```

**怎么判断是否需要保存？**

AutosaveManager 使用 “脏标记” 机制：

```python
class AutosaveManager:
    """Manages automatic session saving with rate limiting and delta support.
    
    Uses incremental saves for autosave (fast) and full saves for
    explicit save commands (consistent).
    """

    def __init__(self, session: SessionData, interval: int = AUTOSAVE_INTERVAL_SECONDS):
        self.session = session
        self.interval = interval
        self._last_save_time = time.time()  # 用当前时间初始化
        self._dirty = False # 脏标记：是否有未保存的修改
        self._full_save_counter = 0

    def mark_dirty(self) -> None:
        """标记会话需要保存"""
        self._dirty = True

    def should_save(self) -> bool:
        """检查是否需要保存"""
        # 判断条件1：是否有未保存的修改
        if not self._dirty:
            return False
        elapsed = time.time() - self._last_save_time
        # 判断条件2：距离上次保存是否超过最小间隔
        return elapsed >= self.interval

    def save_if_needed(self) -> bool:
        """Save if dirty and interval elapsed. Uses delta saves for speed.
        
        Returns True if saved.
        """
        if self.should_save():
            # Use incremental delta save for autosave (fast)
            save_session(self.session, force_full=False)
            self._last_save_time = time.time()
            self._dirty = False
            self._full_save_counter += 1
            return True
        return False

    def force_save(self) -> None:
        """Force immediate full save regardless of interval."""
        save_session(self.session, force_full=True)
        self._last_save_time = time.time()
        self._dirty = False
        self._full_save_counter = 0
```

会话数据发生变化时，调用 mark_dirty()
```text
用户输入：
    ↓
_handle_input():
    state.history.append(user_input)  # 修改历史
    state.autosave.mark_dirty()         # ← 标记脏

Agent 响应：
    ↓
args.messages.append(response)  # 修改消息
state.autosave.mark_dirty()      # ← 标记脏

工具执行：
    ↓
state.transcript.append(entry)  # 修改对话记录
state.autosave.mark_dirty()      # ← 标记脏
```

保存 SessionData 的所有字段，包括基础信息、对话历史（LLM API 格式）、对话记录（UI显示格式）、历史命令、权限摘要、技能、MCP Servers 等

完整流程：
```text
用户输入 "ls"
    ↓
state.history.append("ls")
state.autosave.mark_dirty()  # ← 标记脏（_dirty = True）

用户继续输入 "pwd"
    ↓
state.history.append("pwd")
state.autosave.mark_dirty()  # ← 再次标记（_dirty 已经是 True，重复标记）

...

事件循环迭代 100 次（约 5 秒）后：
    ↓
state.autosave.save_if_needed():
    ├─ 检查 _dirty == True（有修改）
    ├─ 检查距离上次保存 > 5 秒
    ├─ 执行保存：
    │   ├─ 构建 JSON 数据（messages, history, transcript 等）
    │   ├─ 写入临时文件
    │   └─ 原子替换
    ├─ _dirty = False（重置标记）
    └─ 记录保存时间
```

### 4.5 键盘输入读取

#### Unix 平台
```python
import select

_fd = sys.stdin.fileno()
ready, _, _ = select.select([_fd], [], [], 0.05)  # 50ms 超时
if not ready:
    throttled.flush()  # 空闲时刷新渲染
    continue

# 读取原始字节
_raw = os.read(_fd, 4096)
if not _raw:
    should_exit = True
    continue

# Drain 剩余字节
while True:
    ready2, _, _ = select.select([_fd], [], [], 0)
    if not ready2:
        break
    _more = os.read(_fd, 4096)
    if not _more:
        break
    _raw += _more
```
为什么用 os.read() 而不是 input()？

input():
- 会等待回车
- 会处理退格、删除等
- 会回显到屏幕
- 不适合 Raw Mode

os.read():
- 直接读取原始字节
- 不等待回车
- 不处理特殊字符
- 适合 Raw Mode

**为什么要 drain?**
```text
用户快速输入多个字符：
  用户按 'h', 'e', 'l', 'l', 'o'（很快，< 1ms）
  ↓
  这些字符进入内核缓冲区
  ↓
  select.select 检测到有输入（就绪）
  ↓
  os.read(_fd, 4096) 读取第一个字节块（可能只读到 'h'）
  ↓
  但缓冲区还有 'e', 'l', 'l', 'o'
  ↓
  需要把剩余的字节都读出来
  ↓
  drain 循环：继续读取，直到没有更多数据
  ↓
  最终 _raw = 'hello'（完整的输入）
```
如果不 drain :
```text
用户输入 "hello"
  ↓
  os.read 读到 'h'
  ↓
  程序处理 'h'（以为只有这一个字符）
  ↓
  下一轮循环
  ↓
  select.select 检测到还有输入
  ↓
  os.read 读到 'e'
  ↓
  程序处理 'e'
  ↓
  ...（分5次处理）
```
问题：
- 逻辑混乱（应该一起处理 "hello"）
- 效率低下（多次循环）
- 可能出现竞态条件

**input() 的三个特点**

1.会等待回车
```text
input() 的行为
user_input = input("Enter: ")  # ← 程序阻塞在这里

用户输入：
'h' → 不返回
'e' → 不返回
'l' → 不返回
'l' → 不返回
'o' → 不返回
'\n' (回车) → 返回 "hello"
```
为什么？
- Canonical Mode（规范模式）下，终端缓冲输入
- 只有回车才会把缓冲区内容传递给程序

2.会处理退格、删除
```text
用户输入：
'h', 'e', 'l', 'l', 'o', 退格, '\n'
↓
input() 处理退格（删除 'o'）
↓
返回 "hell"（不是 "hello\x7f"）
```
为什么？
- 规范模式下，终端提供行编辑功能
- 退格、删除等由终端处理，不传递给程序

3.会回显到屏幕
```text
用户输入 'h'
↓
终端立即显示 'h'（回显）
↓
用户输入 'e'
↓
终端立即显示 'e'（回显）
↓
最终屏幕显示：hell（随着输入实时显示）
```
为什么？
- 规范模式下，终端默认回显输入
- 用户可以看到自己输入了什么

为什么这些行为不适合 TUI？

TUI 需求：
- 实时响应每个按键（不等待回车）
- 自己控制显示（不依赖终端回显）
- 原样接收所有按键（包括退格、Ctrl+C 等）

input() 的问题：
- 等待回车 → 不能实时响应
- 自动回显 → 和 TUI 自己的显示冲突
- 处理特殊字符 → TUI 需要自己处理

#### Windows 平台
```python
import msvcrt

if not msvcrt.kbhit():  # 检查是否有按键
    throttled.flush()
    time.sleep(0.05)
    continue

# 读取一个逻辑按键
chunk = ""
while True:
    ch = _win_read_one_key()
    if not ch:
        break
    chunk += ch
```

_win_read_one_key() 处理特殊键：
```python
def _win_read_one_key() -> str:
    """Read one logical key from Windows msvcrt, translating special keys
    into ANSI escape sequences.

    Returns an empty string if no key is available.
    """
    import msvcrt

    if not msvcrt.kbhit():
        return ""

    ch = msvcrt.getwch()

    # 特殊键：两字节序列
    if ch in ("\x00", "\xe0"):
        if msvcrt.kbhit():
            scan = ord(msvcrt.getwch())
        else:
            # Prefix arrived alone (rare) — treat as Escape
            return "\x1b"
        # 转换为 ANSI 序列
        return _WIN_SCANCODE_TO_ANSI.get(scan, "")

    # Ctrl+C → keep as '\x03' so parse_input_chunk handles it
    return ch
```

扫描码 → ANSI映射：
```python
# Windows msvcrt scan-code → ANSI escape sequence mapping.
# msvcrt.getwch() returns a two-char sequence for special keys:
#   prefix ('\x00' or '\xe0') + scan-code byte.
# We translate these to the ANSI sequences that input_parser.py already
# understands.
_WIN_SCANCODE_TO_ANSI: dict[int, str] = {
    72: "\x1b[A",    # Up
    80: "\x1b[B",    # Down
    77: "\x1b[C",    # Right
    75: "\x1b[D",    # Left
    71: "\x1b[H",    # Home
    79: "\x1b[F",    # End
    73: "\x1b[5~",   # Page Up
    81: "\x1b[6~",   # Page Down
    83: "\x1b[3~",   # Delete
    82: "\x1b[2~",   # Insert
    # Alt+Arrow (returned with \x00 prefix on some terminals)
    152: "\x1b[1;3A",  # Alt+Up
    160: "\x1b[1;3B",  # Alt+Down
    157: "\x1b[1;3C",  # Alt+Right
    155: "\x1b[1;3D",  # Alt+Left
    # Ctrl+Arrow
    141: "\x1b[1;5A",  # Ctrl+Up
    145: "\x1b[1;5B",  # Ctrl+Down
    116: "\x1b[1;5C",  # Ctrl+Right
    115: "\x1b[1;5D",  # Ctrl+Left
}
```
如果读取到的不是特殊键，如何处理？
```python
def _win_read_one_key() -> str:
    if not msvcrt.kbhit():
        return ""

    ch = msvcrt.getwch()  # 读取一个字符

    # 判断是否是特殊键
    if ch in ("\x00", "\xe0"):  # ← 特殊键的前缀
        # 处理特殊键...
        scan = ord(msvcrt.getwch())
        return _WIN_SCANCODE_TO_ANSI.get(scan, "")

    # 不是特殊键，直接返回字符
    return ch  # ← 例如：'a', 'b', 'c', '\r' 等
```
示例：

用户按普通键 'a'：
```text
  msvcrt.getwch() → 'a'
  ch = 'a'
  ch in ("\x00", "\xe0")? → 否
  return 'a'  # ← 直接返回
```
用户按回车：
```text
  msvcrt.getwch() → '\r'
  ch = '\r'
  ch in ("\x00", "\xe0")? → 否
  return '\r'  # ← 直接返回
```
两字节序列是 Windows 特殊键的编码方式

普通键：
- 'a' → 一个字节：'a' (0x61)
- 'b' → 一个字节：'b' (0x62)

特殊键（上箭头、下箭头等）：
  上箭头 → 两个字节：'\x00' + 扫描码 (0x48)
  下箭头 → 两个字节：'\xe0' + 扫描码 (0x50)

格式：
  第一个字节：前缀（'\x00' 或 '\xe0'）
  第二个字节：扫描码

为什么是两字节？

历史原因：
- Windows 控制台 API 设计如此
- 第一个字节标识"这是特殊键"
- 第二个字节标识"具体是哪个特殊键"

常见前缀：
- '\x00' → 特殊键（如功能键、箭头键）
- '\xe0' → 扩展特殊键（如 Alt+箭头）

**扫描码**

扫描码是键盘硬件层面的编码

键盘工作原理：
```text
  用户按键 → 键盘硬件检测 → 发送扫描码给操作系统
  ↓
  操作系统将扫描码转换为字符或虚拟键码
  ↓
  应用程序接收
```

例如：
- 用户按 'A' 键 → 扫描码 0x1E
- 用户按上箭头 → 扫描码 0x48
- 用户按 F1 → 扫描码 0x3B

在 Windows 中，用户按上箭头：
```text
  键盘发送扫描码 0x48
  ↓
  msvcrt.getwch() 读取：
    第一个字节：'\x00' (特殊键前缀)
    第二个字节：0x48 (扫描码)
  ↓
  程序收到：'\x00\x48' (两字节序列)
```
**ANSI**

ANSI 转义序列是终端的标准控制序列

ANSI 序列格式：
```text
  ESC + '[' + 命令字符
  ↓
  '\x1b' + '[' + 'A'  # 上箭头
  '\x1b' + '[' + 'B'  # 下箭头
```
示例：
```text
  上箭头 → "\x1b[A"
  下箭头 → "\x1b[B"
  右箭头 → "\x1b[C"
  左箭头 → "\x1b[D"
```
为什么需要转换为 ANSI 序列？

MiniCode 的设计：
- Unix 平台：终端直接发送 ANSI 序列
- Windows 平台：需要手动转换为 ANSI 序列

原因：
- 统一输入格式，方便后续处理
- parse_input_chunk() 只需处理 ANSI 序列
- 不需要区分平台

流程：
```text
  Unix：
    用户按上箭头 → 终端发送 "\x1b[A" → parse_input_chunk() 解析

  Windows：
    用户按上箭头 → msvcrt 收到 '\x00' + 0x48
    ↓
    _win_read_one_key() 转换为 "\x1b[A"
    ↓
    parse_input_chunk() 解析（和 Unix 相同）
```
完整流程示例
```text
用户按上箭头（Windows）：
  ↓
键盘硬件：扫描码 0x48
  ↓
msvcrt.getwch()：'\x00' + 0x48（两字节序列）
  ↓
_win_read_one_key()：
  ch = '\x00'  # 前缀
  ch in ("\x00", "\xe0")? → 是
  scan = 0x48  # 扫描码
  return _WIN_SCANCODE_TO_ANSI[72] = "\x1b[A"  # ANSI 序列
  ↓
parse_input_chunk()：
  解析 "\x1b[A" → KeyEvent(name="up", ...)
  ↓
event_flow.py：
  处理 KeyEvent(name="up") → _history_up(state)
```

### 4.6 输入解析
```python
parsed = parse_input_chunk(input_remainder + chunk)
input_remainder = parsed.rest
```
parse_input_chunk 的作用：
```text
# 原始输入："\x1b[A"  # 上箭头
# ↓ 解析为：
KeyEvent(name="up", text="", ctrl=False, meta=False, shift=False)

# 原始输入："hello"
# ↓ 解析为：
TextEvent(text="h")
TextEvent(text="e")
TextEvent(text="l")
TextEvent(text="l")
TextEvent(text="o")

# 原始输入："\x03"  # Ctrl-C
# ↓ 解析为：
KeyEvent(name="c", text="c", ctrl=True, ...)
```

### 4.7 事件分发
```python
for event in parsed.events:
    try:
        _handle_tty_event(args, state, event, rerender, approval_event, approval_result, _handle_input)

        # 检查退出条件
        if state.input == "/exit" or (
            isinstance(event, KeyEvent)
            and event.name == "c"
            and event.ctrl
        ):
            raise SystemExit(0)

    except SystemExit:
        should_exit = True
        break

    except Exception as e:
        # 记录事件处理错误，但不中断主循环
        logging.debug("Event handling error: %s", e, exc_info=True)

# 确保最终状态可见
throttled.flush()
```
事件分发流程：
```text
用户按下 "上箭头"
    ↓
原始输入："\x1b[A"
    ↓
parse_input_chunk() → KeyEvent(name="up", ...)
    ↓
_handle_tty_event():
    ↓
event_flow.py:
    if event.name == "up":
        _history_up(state)  # 历史命令上翻
        rerender()          # 重新渲染
```

## 五、Phase 4 : 清理
```python
finally:
    # Restore previous SIGWINCH handler on Unix
    exit_tty_runtime(_prev_sigwinch)
    
    finalize_tty_session(args, state)
return args.messages
```

### 5.1 exit_tty_runtime()
```python
def exit_tty_runtime(prev_sigwinch: object | None) -> None:
    # 恢复 SIGWINCH 处理器
    if prev_sigwinch is not None and sys.platform != "win32":
        import signal as _signal

        _signal.signal(_signal.SIGWINCH, prev_sigwinch)  # type: ignore[arg-type]
    show_cursor() # 显示光标
    exit_alternate_screen() # 退出备用屏幕
```

### 5.2 finalize_tty_session()
```python
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
```
完整的保存流程：
```text
用户退出 MiniCode（Ctrl+D 或 /exit）
    ↓
finally 块执行：
    ↓
finalize_tty_session(args, state):
    ↓
1. 更新 state.session 对象（内存）
    ├─ session.messages = args.messages
    ├─ session.transcript_entries = state.transcript
    ├─ session.history = state.history
    └─ session.permissions_summary = ...
    ↓
2. 持久化到磁盘
    ├─ state.autosave.force_save()
    │   ↓
    │   save_session(session)
    │       ↓
    │       构建 JSON 数据
    │       ↓
    │       写入临时文件
    │       ↓
    │       原子替换为目标文件
    └─ 或者直接 save_session(state.session)
    ↓
3. 打印确认消息
    print(f"\nSession saved: {session.session_id[:8]}")
```


## 六、完整流程

```text
┌──────────────────────────────────────────────────────────────
│              用户启动: minicode-py                            
└──────────────────────────────────────────────────────────────
                              ↓
┌──────────────────────────────────────────────────────────────
│ main.py: 初始化配置、工具、模型、Memory等                      
└──────────────────────────────────────────────────────────────
                              ↓
┌──────────────────────────────────────────────────────────────
│ run_tty_app() 入口                                            
├──────────────────────────────────────────────────────────────
│ Phase 1: 初始化                                               
│  ├─ 加载会话                                   
│  ├─ 构建状态 (args, state)                                   
│  ├─ 创建节流渲染器                               
│  └─ 安装权限提示                                   
├──────────────────────────────────────────────────────────────
│ Phase 2: 运行时准备                                           
│  ├─ 进入备用屏幕                                 
│  ├─ 隐藏光标                                    
│  └─ 注册 SIGWINCH 处理器                                      
├──────────────────────────────────────────────────────────────
│ Phase 3: 事件循环 ⭐                                          
│                                                                
│  ┌────────────────────────────────────────────────────────── 
│  │ while not should_exit:                                    
│  │                                                            
│  │   1. Autosave 检查（每 2 秒）                             
│  │      state.autosave.save_if_needed()                      
│  │                                                            
│  │   2. Agent 线程状态检查                                   
│  │      if agent_result["done"]:                             
│  │          args.messages = agent_result["messages"]         
│  │                                                            
│  │   3. 等待键盘输入                            
│  │      Unix: select.select(stdin, timeout=0.05)             
│  │      Win: msvcrt.kbhit()                                   
│  │                                                            
│  │   4. 读取原始输入                               
│  │      Unix: os.read(stdin, 4096)                           
│  │      Win: msvcrt.getwch()                                  
│  │                                                            
│  │   5. 解析输入                                             
│  │      "\x1b[A" → KeyEvent(name="up")                       
│  │      "hello" → [TextEvent("h"), TextEvent("e"), ...]      
│  │                                                            
│  │   6. 分发事件                                             
│  │      _handle_tty_event(args, state, event, ...)           
│  │      ├─ KeyEvent("up") → _history_up()                    
│  │      ├─ KeyEvent("return") → _handle_input()              
│  │      ├─ TextEvent("h") → state.input += "h"              
│  │      └─ ...                                                
│  │                                                           
│  │   7. 渲染节流                                             
│  │      throttled.flush()                                     
│  │                                                           
│  │   8. 检查退出                                             
│  │      if state.input == "/exit" or Ctrl-C:                 
│  │          should_exit = True                               
│  └──────────────────────────────────────────────────────────
├─────────────────────────────────────────────────────────────
│ Phase 4: 清理                                                 
│  ├─ 退出备用屏幕                                              
│  ├─ 显示光标                                                  
│  ├─ 保存会话                                                  
│  └─ 打印保存确认                                              
└──────────────────────────────────────────────────────────────
                              ↓
                         返回 main.py
```

## 七、关键设计模式

### 7.1 事件驱动架构
```text
传统同步模式：
  用户输入 → 程序处理 → 阻塞等待 → 用户输入 → ...

事件驱动模式：
  事件队列 ← [键盘事件, 网络事件, 定时器事件, ...]
      ↓
  事件循环：从队列取事件 → 分发给处理器 → 继续循环
```

### 7.2 线程模型
主线程：
- UI 渲染
- 用户输入处理
- 事件分发
- 非阻塞操作

后台线程：
- LLM API 调用（耗时）
- 工具执行（可能耗时）
- 不阻塞 UI

通信机制：
- state.agent_result：共享数据
- threading.Lock：线程同步
- threading.Event：线程通知

后台线程在 _handle_input() 函数中启动：

tui/input_handler.py
```python
def _handle_input(args, state, rerender, submit_text=None):
    """处理用户输入（提交命令）"""

    # 获取用户输入
    user_input = submit_text or state.input
    if not user_input.strip():
        return False

    # 检查是否是本地命令
    local_result = _handle_local_command(user_input, args.tools)
    if local_result is not None:
        # 本地命令，直接处理
        return True

    # 需要调用 Agent - 启动后台线程
    state.is_busy = True
    rerender()

    # 准备线程参数
    state.agent_result = {"done": False, "messages": None}
    state.agent_lock = threading.Lock()

    # 启动后台线程
    state.agent_thread = threading.Thread(
        target=_run_agent_in_thread,
        args=(args, state, user_input),
        daemon=True,
    )
    state.agent_thread.start()

    return True


def _run_agent_in_thread(args, state, user_input):
    """后台线程：运行 Agent"""
    try:
        # 构建消息
        messages = list(args.messages)
        messages.append({"role": "user", "content": user_input})

        # ⭐ 调用 run_agent_turn（包含 LLM API 调用和工具执行）
        messages = run_agent_turn(
            model=args.model,
            tools=args.tools,
            messages=messages,
            cwd=args.cwd,
            permissions=args.permissions,
            store=state.app_state,
            context_manager=args.context_manager,
            runtime=args.runtime,
        )

        # 保存结果
        with state.agent_lock:
            state.agent_result["messages"] = messages
            state.agent_result["done"] = True  # ← 标记完成

    except Exception as e:
        # 错误处理
        logging.error("Agent thread error: %s", e, exc_info=True)
        with state.agent_lock:
            state.agent_result["error"] = str(e)
            state.agent_result["done"] = True
```

LLM 调用和工具执行都在 run_agent_turn() 中：
```python
# agent_loop.py

def run_agent_turn(model, tools, messages, ...):
    """Agent 主循环"""

    while step < max_steps:
        # ⭐ 1. 调用 LLM API
        next_step = model.next(messages)

        # 处理响应
        if next_step.type == "assistant":
            # 文本响应，返回
            return messages

        # ⭐ 2. 执行工具
        for call in next_step.calls:
            tool_name = call["toolName"]
            tool_input = call["input"]

            # 执行工具（如 read_file, run_command 等）
            result = tools.execute(tool_name, tool_input, context)

            # 添加结果到消息
            messages.append({
                "role": "tool_result",
                "toolName": tool_name,
                "content": result.output,
            })
```

线程同步：同步的是 state.agent_result 这个共享数据
```python
# 主线程和后台线程都会访问 agent_result

# 后台线程（写入）：
with state.agent_lock:
    state.agent_result["messages"] = messages  # ← 写入
    state.agent_result["done"] = True

# 主线程（读取）：
agent_result_data = state.agent_result
lock = getattr(state, "agent_lock", None)
if agent_result_data is not None and lock is not None and agent_result_data.get("done"):
    with lock:
        if agent_result_data.get("messages"):
            args.messages = agent_result_data["messages"]  # ← 读取
        agent_result_data["done"] = False
```

为了防止竞态条件，所以需要锁

线程模型流程：
```text
┌─────────────────────────────────────────────────────
│ 主线程（事件循环）                                    
│                                                       
│ 1. 用户输入 "帮我分析代码"                            
│    ↓                                                  
│ 2. _handle_input() 决定启动后台线程                  
│    ├─ state.agent_result = {"done": False}           
│    ├─ state.agent_lock = threading.Lock()            
│    └─ 启动后台线程                                    
│                                                       
│ 3. 继续事件循环                                      
│    while not should_exit:                             
│        ├─ 检查 agent_result["done"]                  
│        ├─ 等待用户输入（最多 50ms）                  
│        └─ 渲染 UI                                     
│                                                       
│ 4. 检测到 agent_result["done"] = True                
│    ├─ 获取锁                                          
│    ├─ args.messages = agent_result["messages"]       
│    ├─ agent_result["done"] = False                   
│    └─ 释放锁                                          
│                                                       
│ 5. 渲染新消息                                         
│    rerender()                                         
└─────────────────────────────────────────────────────
        ↑ 启动                          ↑ 检查结果
        │                               │
        │    ┌──────────────────────────┘
        │    │
        │    │
┌─────────────────────────────────────────────────────
│ 后台线程                                             
│                                                       
│ _run_agent_in_thread(args, state, user_input):       
│                                                       
│ 1. 构建消息                                           
│    messages.append({"role": "user", "content": ...}) 
│                                                       
│ 2. 调用 run_agent_turn()                             
│    ├─ model.next() → LLM API 调用                   
│    │   └─ 等待响应（可能几秒到几十秒）               
│    │                                                   
│    └─ tools.execute() → 工具执行                     
│        ├─ read_file("main.py")                       
│        ├─ grep_files("function")                     
│        └─ ...                                         
│                                                       
│ 3. 完成后保存结果                                     
│    with agent_lock:  # ← 获取锁                      
│        agent_result["messages"] = messages            
│        agent_result["done"] = True  # ← 通知主线程   
│    # ← 释放锁                                          
│                                                       
│ 4. 线程结束                                           
└─────────────────────────────────────────────────────
```



### 7.3 节流与防抖
节流：
- 控制执行频率
- "最多每 16ms 执行一次"
- 用于：渲染控制

防抖：
- 延迟执行，直到停止触发
- "停止输入 500ms 后执行"
- 用于：搜索建议

**节流与防抖**

这是两个不同的优化策略，虽然目的相似（减少执行次数），但实现不同

节流：控制执行频率

核心思想：无论触发多少次，最多每 N 毫秒执行一次

图示：
```text
时间轴（毫秒）：   0   16   32   48   64   80   96  112
                    │    │    │    │    │    │    │    │

触发请求：         ●●●●●●●●●●●●●●●●●●●●●●●●●●●●●●●●●●●●
                  （用户快速输入，触发大量 rerender()）

实际执行：         ▼                   ▼                ▼
                  渲染               渲染            渲染
```
结果：
- 触发：36 次
- 执行：3 次
- 每次间隔至少 16ms

MiniCode 的实现：
```python
class _ThrottledRenderer:
    def __init__(self, render_fn, min_interval=0.016):
        self._min_interval = min_interval  # 最小间隔 16ms

    def request(self):
        # 只是标记"需要渲染"
        self._pending = True

    def flush(self):
        now = time.monotonic()
        # 检查：是否有待渲染 + 间隔是否足够
        if self._pending and (now - self._last_render_time >= self._min_interval):
            self._render_fn()  # 执行渲染
```
使用场景：
- 渲染控制（MiniCode 正在用）
- 滚动事件处理
- 窗口大小调整
- 鼠标移动跟踪

防抖：延迟执行

核心思想：停止触发 N 毫秒后，才执行一次

图示：
```text
时间轴（毫秒）：   0   50  100  150  200  250  300  350  400
                    │    │    │    │    │    │    │    │

触发请求：         ●●●●●●●●●●●●●●●●●●●●●●●●●●●●●●●●●●●●
                  （用户输入，每次按键触发 search()）

等待计时：         ←等待→←重置→←等待→←重置→←等待→←重置→←等...
                  （每次触发都重置计时器）

实际执行：                                                 ▼
                                                          搜索
```
结果：
- 触发：36 次
- 执行：1 次
- 停止输入 300ms 后执行

伪代码实现：
```python
import threading

def debounce(func, delay=0.3):
    """防抖装饰器"""
    timer = None

    def wrapper(*args, **kwargs):
        nonlocal timer

        # 取消之前的计时器
        if timer:
            timer.cancel()

        # 设置新的计时器
        timer = threading.Timer(delay, lambda: func(*args, **kwargs))
        timer.start()

    return wrapper

# 使用示例
@debounce(delay=0.3)
def search(query):
    """搜索建议"""
    print(f"搜索：{query}")
```
实验：防抖16ms vs 节流16ms

假设用户快速输入 "hello"（每个字符间隔 10ms）：
```text
时间线：
  0ms    10ms   20ms   30ms   40ms   50ms   60ms   70ms
  │      │      │      │      │      │      │      │

用户输入：
  h      e      l      l      o     (停)
```
防抖（16ms）

| 时间 | 用户输入 | 计时器状态 | 渲染 |
| ---- | -------- | ---------- | ---- |
| 0ms | h | 启动计时器16ms | — |
| 10ms | e | 取消旧计时器，启动新的 | — |
| 20ms | l | 取消旧计时器，启动新的 | — |
| 30ms | l | 取消旧计时器，启动新的 | — |
| 40ms | o | 取消旧计时器，启动新的 | — |
| 56ms | (超时) | 计时器完成 | ▼ 渲染 "hello" |

问题：
- 用户在 0-40ms 期间：什么都看不到（输入被缓冲）
- 用户在 40-56ms 期间：仍然什么都看不到（等待超时）
- 用户在 56ms：突然看到 "hello"（延迟 56ms）
- 体验：有明显的"卡顿感"

节流（16ms）

| 时间 | 用户输入 | state.input | 渲染状态 | 渲染结果 |
| ---- | -------- | ----------- | -------- | -------- |
| 0ms | h | "h" | request() | 等待 |
| 10ms | e | "he" | request() | 等待 |
| 16ms | (空闲) | "he" | flush() → 渲染 | ▼ "he" |
| 20ms | l | "hel" | request() | 等待 |
| 30ms | l | "hell" | request() | 等待 |
| 32ms | (空闲) | "hell" | flush() → 渲染 | ▼ "hell" |
| 40ms | o | "hello" | request() | 等待 |
| 48ms | (空闲) | "hello" | flush() → 渲染 | ▼ "hello" |

效果：
- 用户在 16ms：看到 "he"（部分输入）
- 用户在 32ms：看到 "hell"（部分输入）
- 用户在 48ms：看到 "hello"（最终输入）
- 体验：实时反馈，看到输入过程

关键区别

防抖（16ms）：
```text
  输入期间：完全看不到（等待超时）
  停止输入后：等待 16ms 才显示
  ↓
  用户感受："我输入了，但屏幕没反应，等一下才突然出现"
```
节流（16ms）：
```text
  输入期间：每 16ms 更新一次显示
  用户看到："h" → "he" → "hel" → "hell" → "hello"
  ↓
  用户感受："实时响应，我输入什么屏幕就显示什么"
```
为什么MiniCode用节流？

MiniCode 的需求：
- 用户输入命令（如 "/help"）
- 需要实时看到输入内容
- 确认输入正确后再提交

如果用防抖（即使 16ms）：
```text
  用户输入：/h-e-l-p
  ↓
  期间什么都看不到
  ↓
  停止输入 16ms 后突然显示 "/help"
  ↓
  用户体验差："我输入了吗？屏幕怎么没反应？"
```
如果用节流：
```text
  用户输入：/h
  ↓
  16ms 后显示："/h"
  ↓
  用户继续输入：e-l-p
  ↓
  每 16ms 更新显示："/he" → "/hel" → "/help"
  ↓
  用户体验好："实时看到输入，确认正确后回车提交"
```
| 特性 | 节流 | 防抖 |
| ---- | ---- | ---- |
| 核心思想 | 控制频率 | 延迟执行 |
| 执行时机 | 间隔足够就执行 | 停止触发后才执行 |
| 执行次数 | 多次（有规律） | 一次（最后） |
| 首次执行 | 立即（可能） | 延迟 |
| 典型应用 | 渲染、滚动、鼠标移动 | 搜索、验证、自动保存 |

但是，搜索建议场景用防抖是对的：
```text
用户输入：j-a-v-a（快速搜索 "java"）
    ↓
每次按键都触发 search()
    ↓
search("j") → 等待
search("ja") → 取消旧的，等待
search("jav") → 取消旧的，等待
search("java") → 取消旧的，等待
    ↓
停止输入 300ms 后
    ↓
search("java") → 发送请求
```
好处：
- 不会发送 4 次请求
- 只发送 1 次请求
- 节省资源

## 八、总结

### 8.1 tty_app.py 的核心职责

1.会话管理
- 加载/保存会话
- 自动保存

2.事件循环
- 键盘输入捕获
- 事件解析和分发
- UI 响应

3.线程管理
- Agent 后台执行
- 结果同步

4.渲染控制
- 节流渲染
- 终端大小变化处理

5.权限集成
- 权限提示 UI
- 用户决策收集

### 8.2 关键文件关系
```text
tty_app.py (主控制器)
    ├─ tui/state.py (数据结构)
    ├─ tui/event_flow.py (事件处理)
    ├─ tui/input_handler.py (输入处理)
    ├─ tui/input_parser.py (输入解析)
    ├─ tui/renderer.py (渲染)
    ├─ tui/session_flow.py (会话流程)
    └─ tui/runtime_control.py (运行时控制)
```

### 8.3 流程

在 interview1.md 中，解析的主要是 main.py 是如何处理非 TTY 模式的交互。在 main.py 中处理 TTY 模式的代码为
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
可以看到，如果在“判断是否为交互式终端”那一步的结果为“是”，那么会直接调用最后的 run_tty_app 函数。在 interview2.md 中，详细解析了 run_tty_app 函数做了什么。首先明确一点：对于 Coding Agent 而言，交互式终端绝对是更为常见的使用模式，几乎所有的 Coding Agent 的 CLI 都是交互式终端

首先搞懂两个关键数据结构：
- TtyAppArgs: 记录不可变的应用参数，包括runtime、工具注册表、模型适配器、对话历史、工作目录、权限管理器、记忆管理器、上下文管理器。这几个参数在 main.py 进入 TTY 模式前已完成初始化，所以直接作为参数传入 run_tty_app，再作为参数传给 TtyAppArgs 的构造函数
- ScreenState: 记录可变的屏幕状态，包括但不限于：用户输入缓冲区、光标位置、对话记录、最近使用工具、待审批请求、是否忙碌、状态管理器、开销追踪器、Agent结果/线程/锁 等

#### Phase 1 : 初始化

加载会话、构建状态、创建节流渲染器、安装权限提示

- 加载会话：根据传入的 resume_session 判断是恢复最新会话、还是恢复指定id会话、还是创建新会话

- 状态构建：初始化 TtyAppArgs 和 ScreenState，但不是每个字段都通过传入的参数直接初始化，有的是通过调函数被初始化，有的字段一开始不会被初始化

- 初始化节流渲染器，节流指的是减少渲染频率

- 安装权限提示，权限提示处理器会被 PermissionManager 调用

#### Phase 2 : 运行时准备

进入备用屏幕、隐藏光标、注册 SIGWINCH 处理器

- 进入备用屏幕：类似于 ClaudeCode 的全屏终端，退出后会回到系统终端。“没有历史记录”指的是回退到系统终端后，往上滑不会看见在 MiniCode 中的对话记录，并不意味着在 Agent 中的交互记录（例如工具调用、对话历史）没有被保存起来

- 隐藏光标：hide_cursor() 函数

- 注册 SIGWINCH 处理器：SIGWINCH 表示窗口大小变化信号，如果是 windows 系统就不使用 SIGWINCH 信号进行窗口大小变化的通知，如果是 Unix/Linux 就注册 SIGWINCH 处理器，当内核检测到窗口大小变化，就发送 SIGWINCH 信号给前台进程组，进程的 SIGWINCH 处理器被调用，清除终端大小缓存并请求重新渲染。同时需要记录上一个 SIGWINCH 处理器，这样 MiniCode 退出后，就由 Shell 的 SIGWINCH 处理器接管。Windows 则是定期检查窗口大小是否发生变化或者缓存是否过期，即轮询

#### Phase 3 : 事件循环

进入RawMode、自动保存检查、Agent 线程状态检查、等待键盘输入（50ms）、读取原始输入、解析输入、分发事件、渲染节流、检查退出

- RawMode : 无缓冲：用户输入什么程序就立即收到什么；无行编辑：退格、删除等原样传递；无回显：输入的字符不显示（不代表用户看不到自己输入了什么，而是程序接收到用户的输入后，再自己控制显示出来）；无特殊处理：Ctrl+C原样传递。禁用回显的目的：TUI 自己控制显示。禁用规范模式的目的：TUI需要实时响应（例如上下箭头、快捷键）所以不能等待回车。禁用信号生成的目的：对于 Ctrl + C，程序可以自己决定如何处理（例如中断 LLM 响应，而不是内核发送 SIGINT 导致程序终止）。RawMode的优势在于自主权高，只不过需要自己处理所有按键逻辑

- 自动保存检查：每 _AUTOSAVE_CHECK_INTERVAL * 迭代时间 秒检查一次是否需要保存。如果有脏标记就保存，如果没有脏标记但距离上次保存已经超过了最小间隔也保存

- Agent 线程状态检查与等待键盘输入：主线程负责运行事件循环、处理用户输入、渲染UI，后台线程负责执行任务。主线程在每次事件循环迭代时，先检查 agent.result，如果为 true 代表后台线程完成了任务，就获取 agent_result 中的对话历史，重设标志位为 false，然后等待输入（50ms超时）。如果有输入就处理并渲染，没有就回到循环开头

- 读取原始输入：用 os.read() 读取原始字节（即不等待回车、不处理特殊字符）

- 解析输入：ANSI 转义序列是终端的标准控制序列，就是用字符串表示具体的按键操作。Unix 平台中，终端直接发送 ANSI 序列，为了统一管理，对于 Windows 平台，先手动转换为 ANSI 序列，再统一调用 parse_input_chunk() 处理。对于 Windows ，先通过msvcrt.kbhit() 获取按键，然后在 _win_read_one_key() 中处理。如果字符在两字节序列中（即为特殊键，特殊键由前缀+扫描码两个字节构成，普通键则是一个字节），就转换为 ANSI 序列，否则直接返回字符（普通键）。无论是 Windows 还是 Unix/Linux，得到 ANSI 序列后，统一用 parse_input_chunk() 处理。在该函数中，将ANSI 原始序列解析为 KeyEvent 或者 TextEvent

- 分发事件：解析为 Event 后，调用 _handle_tty_event() 处理，该函数通过 if 分支确定 Event 要执行什么函数。例如 event.name == "up" 就执行历史命令上翻 + 重新渲染的函数

#### Phase 4 : 清理

退出备用屏幕、显示光标、保存会话、打印保存确认、返回 main.py