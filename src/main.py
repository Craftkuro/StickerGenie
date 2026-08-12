# coding=utf-8
"""StickerGenie application entry point."""

from __future__ import annotations

import multiprocessing


def main() -> int:
    import logging
    import os.path
    import signal
    import sys

    import apppath


    logging.basicConfig()
    logging.root.setLevel(level=logging.DEBUG)

    # 尽早找到程序所在路径。需要使用此路径来初始化配置文件路径等信息。
    if getattr(sys, "frozen", False):
        application_path = sys._MEIPASS
        base_data_path = os.path.dirname(sys.executable)
    else:
        application_path = os.path.dirname(os.path.abspath(__file__))
        base_data_path = os.path.dirname(application_path)
    apppath.setup_data_path(application_path, base_data_path)

    # 设置appid, 用于在任务栏上拥有专门的图标
    import utils.win32
    utils.win32._set_windows_app_user_model_id()

    import services.startup
    import ui.main_window
    from PyQt6.QtWidgets import QApplication
    from PyQt6.QtGui import QIcon
    from utils.resource_path import resolve_resource_path

    services.startup.run_startup_tasks()

    # 方便使用IDE快捷终止进程
    signal.signal(signal.SIGINT, signal.SIG_DFL)

    application = QApplication(sys.argv)
    application.setQuitOnLastWindowClosed(True)
    application.setStyle("Fusion")
    application.setWindowIcon(
        QIcon(str(resolve_resource_path("app_icon.ico")))
    )

    main_window = ui.main_window.MainWindow()
    main_window.show()
    return application.exec()


if __name__ == "__main__":
    multiprocessing.freeze_support()
    raise SystemExit(main())
