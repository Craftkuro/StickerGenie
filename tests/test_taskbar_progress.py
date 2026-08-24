import os
import sys
import unittest
from unittest.mock import Mock, patch

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from ui.operations.taskbar_progress import TaskbarProgressBridge
from utils import win32


class _FakeWindow:
    def __init__(self, win_id=1234567890, error=None):
        self.win_id_calls = 0
        self._win_id = win_id
        self._error = error

    def winId(self):
        self.win_id_calls += 1
        if self._error is not None:
            raise self._error
        return self._win_id


class TaskbarProgressBridgeTests(unittest.TestCase):
    def test_begin_update_and_clear_forward_percent_to_the_window(self):
        window = _FakeWindow()
        recorded = []

        def fake_set(hwnd, completed, total):
            recorded.append(("set", hwnd, completed, total))
            return True

        def fake_clear(hwnd):
            recorded.append(("clear", hwnd))
            return True

        bridge = TaskbarProgressBridge(window)
        with patch.object(win32, "taskbar_set_progress", fake_set), patch.object(
            win32, "taskbar_clear_progress", fake_clear
        ):
            bridge.begin()
            bridge.update(42)
            bridge.clear()

        self.assertEqual(
            [
                ("set", 1234567890, 0, 100),
                ("set", 1234567890, 42, 100),
                ("clear", 1234567890),
            ],
            recorded,
        )
        self.assertEqual(1, window.win_id_calls)

    def test_window_without_winid_is_silently_ignored(self):
        class _WindowWithoutWinId:
            pass

        recorded = []
        bridge = TaskbarProgressBridge(_WindowWithoutWinId())
        with patch.object(
            win32,
            "taskbar_set_progress",
            lambda *args: recorded.append(args),
        ), patch.object(
            win32,
            "taskbar_clear_progress",
            lambda *args: recorded.append(args),
        ):
            bridge.begin()
            bridge.update(10)
            bridge.clear()

        self.assertEqual([], recorded)

    def test_failing_winid_does_not_raise_and_skips_the_call(self):
        window = _FakeWindow(error=RuntimeError("window not created"))
        bridge = TaskbarProgressBridge(window)
        fake_set = Mock(return_value=True)
        with patch.object(win32, "taskbar_set_progress", fake_set):
            bridge.update(5)
            bridge.clear()

        fake_set.assert_not_called()


@unittest.skipUnless(sys.platform == "win32", "Windows-only COM integration")
class Win32TaskbarListTests(unittest.TestCase):
    def test_invalid_hwnd_is_rejected_without_raising(self):
        self.assertFalse(win32.taskbar_set_progress(0, 30, 100))
        self.assertFalse(win32.taskbar_set_progress(-1, 30, 100))
        self.assertFalse(win32.taskbar_clear_progress(0))
        self.assertFalse(win32.taskbar_clear_progress(-1))

    def test_real_widget_round_trip_returns_bool_without_raising(self):
        from PyQt6.QtWidgets import QApplication, QWidget

        app = QApplication.instance() or QApplication([])
        widget = QWidget()
        widget.winId()
        hwnd = int(widget.winId())

        # 只验证调用链可用且返回布尔值；offscreen 环境下 shell 调用的具体结果不保证。
        self.assertIsInstance(win32.taskbar_set_progress(hwnd, 30, 100), bool)
        self.assertIsInstance(win32.taskbar_clear_progress(hwnd), bool)


if __name__ == "__main__":
    unittest.main()
