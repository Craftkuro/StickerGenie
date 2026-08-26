import os
import unittest
from pathlib import Path

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PyQt6.QtCore import Qt
from PyQt6.QtWidgets import QApplication, QLabel, QPushButton

import apppath
from services.export_library import ExportLibraryProgress
from ui.dialog_library_export_progress import LibraryExportProgressDialog


class LibraryExportProgressDialogTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = QApplication.instance() or QApplication([])
        source_dir = Path(__file__).resolve().parents[1] / "src"
        apppath.setup_data_path(source_dir)

    def setUp(self):
        self.dialog = LibraryExportProgressDialog()
        self.dialog.show()
        self.app.processEvents()

    def tearDown(self):
        self.dialog.finish()
        self.app.processEvents()

    def test_displays_status_and_counts(self):
        self.dialog.update_progress(
            ExportLibraryProgress(
                percent=51,
                status="正在导出图片",
                completed=3,
                total=8,
            )
        )

        self.assertEqual(51, self.dialog.progressBar.value())
        self.assertEqual("正在导出图片", self.dialog.labelStatus.text())
        self.assertEqual("已处理 3/8", self.dialog.labelTaskProgress.text())
        self.assertIsNone(self.dialog.findChild(QLabel, "labelDetail"))

    def test_clears_counts_when_total_is_unknown(self):
        self.dialog.update_progress(
            ExportLibraryProgress(percent=0, status="正在准备导出")
        )

        self.assertEqual("", self.dialog.labelTaskProgress.text())

    def test_is_application_modal_and_has_no_cancel_button(self):
        self.assertTrue(self.dialog.isModal())
        self.assertEqual(
            Qt.WindowModality.ApplicationModal,
            self.dialog.windowModality(),
        )
        self.assertIsNone(self.dialog.findChild(QPushButton, "pushButtonCancel"))

    def test_cannot_close_until_the_export_finishes(self):
        self.dialog.close()
        self.app.processEvents()
        self.assertTrue(self.dialog.isVisible())

        self.dialog.finish()
        self.app.processEvents()
        self.assertFalse(self.dialog.isVisible())


if __name__ == "__main__":
    unittest.main()
