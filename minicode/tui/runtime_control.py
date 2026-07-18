from __future__ import annotations

import sys
import threading
import time
from typing import Callable

from minicode.tui.chrome import invalidate_terminal_size_cache
from minicode.tui.screen import enter_alternate_screen, exit_alternate_screen, hide_cursor, show_cursor


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
        self._render_fn()


def enter_tty_runtime() -> None:
    enter_alternate_screen() # 进入备用屏幕缓冲区
    hide_cursor() # 隐藏光标


def exit_tty_runtime(prev_sigwinch: object | None) -> None:
    # 恢复 SIGWINCH 处理器
    if prev_sigwinch is not None and sys.platform != "win32":
        import signal as _signal

        _signal.signal(_signal.SIGWINCH, prev_sigwinch)  # type: ignore[arg-type]
    show_cursor() # 显示光标
    exit_alternate_screen() # 退出备用屏幕


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
