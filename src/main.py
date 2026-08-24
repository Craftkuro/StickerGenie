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

    import commons.constants
    import services.single_instance
    import services.startup
    import ui.main_window
    from PyQt6.QtCore import QLibraryInfo, QLocale, QTranslator
    from PyQt6.QtWidgets import QApplication
    from PyQt6.QtGui import QIcon, QPixmapCache
    from utils.resource_path import resolve_resource_path

    # 方便使用IDE快捷终止进程
    signal.signal(signal.SIGINT, signal.SIG_DFL)

    application = QApplication(sys.argv)
    qt_translator = QTranslator(application)
    qt_translator.load(
        QLocale("zh_CN"),
        "qtbase",
        "_",
        QLibraryInfo.path(QLibraryInfo.LibraryPath.TranslationsPath),
    )
    application.installTranslator(qt_translator)

    # 当同一目录的程序运行多个实例时，第二个及后续实例将退出，
    # 因为目前配置文件是绑定在exe路径的，同一份配置多实例可能导致数据损坏
    if not services.single_instance.ensure_single_instance(application):
        return 0

    services.startup.run_startup_tasks()

    # Qt 全局 QPixmapCache 默认容量太小，容纳不下 1000 张缩略图；与应用
    # 缩略图内存缓存规模对齐，避免 QIcon/Qt 内部绘制缓存过早淘汰。
    QPixmapCache.setCacheLimit(commons.constants.QPIXMAP_CACHE_LIMIT_KB)
    application.setQuitOnLastWindowClosed(True)
    application.setStyle("Fusion")
    application.setWindowIcon(
        QIcon(str(resolve_resource_path("app_icon.ico")))
    )

    main_window = ui.main_window.MainWindow()
    services.single_instance.activationRequested.connect(
        main_window.raise_and_activate
    )
    main_window.show()
    return application.exec()


if __name__ == "__main__":
    multiprocessing.freeze_support()
    raise SystemExit(main())
