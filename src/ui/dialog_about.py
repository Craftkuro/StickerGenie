# coding=utf-8
"""关于对话框。"""
from PyQt6 import uic
from PyQt6.QtCore import Qt
from PyQt6.QtWidgets import QDialog

import apppath


class AboutDialog(QDialog):
    """展示应用名称、版本等占位信息的关于对话框。"""

    def __init__(self, parent=None):
        super().__init__(parent)

        ui_file_path = apppath.app_path / "ui" / "dialog_about.ui"
        uic.loadUi(ui_file_path, self)

        self.setModal(True)
        self.setWindowModality(Qt.WindowModality.ApplicationModal)
        self.pushButtonClose.clicked.connect(self.accept)
