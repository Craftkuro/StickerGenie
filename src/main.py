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

    if getattr(sys, "frozen", False):
        application_path = sys._MEIPASS
    else:
        application_path = os.path.dirname(os.path.abspath(__file__))
    apppath.setup_data_path(application_path)

    import services.startup
    import ui.main_window
    from PyQt6.QtWidgets import QApplication

    services.startup.run_startup_tasks()
    signal.signal(signal.SIGINT, signal.SIG_DFL)

    application = QApplication(sys.argv)
    application.setQuitOnLastWindowClosed(True)
    application.setStyle("Fusion")

    main_window = ui.main_window.MainWindow()
    main_window.show()
    return application.exec()


if __name__ == "__main__":
    multiprocessing.freeze_support()
    raise SystemExit(main())
