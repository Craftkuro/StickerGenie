import os
import unittest
from pathlib import Path

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PyQt6.QtWidgets import QApplication, QDialog

import apppath
from ui.dialog_about import AboutDialog


class AboutDialogTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = QApplication.instance() or QApplication([])
        apppath.app_path = Path(__file__).resolve().parents[1] / "src"

    def test_ui_is_loaded_with_placeholder_content(self):
        dialog = AboutDialog()
        self.assertEqual("关于 StickerGenie", dialog.windowTitle())
        self.assertEqual("StickerGenie", dialog.labelAppName.text())
        self.assertTrue(dialog.labelVersion.text())
        self.assertTrue(dialog.labelDescription.text())

    def test_close_button_accepts_dialog(self):
        dialog = AboutDialog()
        dialog.pushButtonClose.click()
        self.assertEqual(QDialog.DialogCode.Accepted, dialog.result())


if __name__ == "__main__":
    unittest.main()
