# coding=utf-8
"""把后台任务进度映射到主窗口的任务栏按钮。

仅做薄封装：解析一次窗口句柄后转发给 utils.win32 的任务栏接口；
任何失败（非 Windows、句柄不可用、COM 出错）都静默降级为无进度显示，
绝不向 Qt 槽内抛异常。
"""

from __future__ import annotations

import logging

from utils import win32

logger = logging.getLogger(__name__)


class TaskbarProgressBridge:
    """begin/update/clear 三段式驱动主窗口的任务栏进度。"""

    def __init__(self, window):
        self._window = window
        self._hwnd: int | None = None

    def begin(self) -> None:
        """任务开始：以 0% 进入正常进度状态。"""
        self.update(0)

    def update(self, percent: int) -> None:
        hwnd = self._resolve_hwnd()
        if hwnd:
            win32.taskbar_set_progress(hwnd, percent, 100)

    def clear(self) -> None:
        hwnd = self._resolve_hwnd()
        if hwnd:
            win32.taskbar_clear_progress(hwnd)

    def _resolve_hwnd(self) -> int | None:
        if self._hwnd is not None:
            return self._hwnd
        try:
            win_id = self._window.winId()
        except Exception:
            return None
        if not win_id:
            return None
        try:
            self._hwnd = int(win_id)
        except Exception:
            return None
        return self._hwnd
