#coding=utf-8
import logging
import os.path
import signal
import sys

import utils.instance_tracker
from utils.instance_tracker import application

logging.basicConfig()
logging.root.setLevel(level=logging.DEBUG)
logger = logging.getLogger(__name__)

logging.getLogger('PyQt6.uic').setLevel(logging.ERROR)

import apppath

# 尽早找到程序所在路径。需要使用此路径来初始化配置文件路径等信息。
if getattr(sys, 'frozen', False):
    app_path = sys._MEIPASS  # by pyinstaller
    is_packaged_build = True
else:
    app_path = os.path.dirname(os.path.abspath(__file__))

# 配置apppath包中的各项路径
apppath.setup_data_path(app_path)



#import ui.main_window

from PyQt6.QtWidgets import QApplication

if __name__ == "__main__":
    signal.signal(signal.SIGINT, signal.SIG_DFL)  # Handle ctrl+c


    app = QApplication(sys.argv)
    app.setQuitOnLastWindowClosed(True)
    app.setStyle('Fusion')   # Temporarily used in development
    utils.instance_tracker.application = app

    #main_window = ui.main_window.MainWindow()
    #main_window.show()


    sys.exit(app.exec())
