import os
import unittest
from pathlib import Path

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PyQt6.QtWidgets import QApplication, QLabel

import apppath
from services.import_library import LibraryImportProgress
from ui.dialog_library_import_progress import LibraryImportProgressDialog


class LibraryImportProgressDialogTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = QApplication.instance() or QApplication([])
        source_dir = Path(__file__).resolve().parents[1] / "src"
        apppath.setup_data_path(source_dir)

    def setUp(self):
        self.dialog = LibraryImportProgressDialog()
        self.dialog.show()
        self.app.processEvents()

    def tearDown(self):
        self.dialog.finish()
        self.app.processEvents()

    def test_displays_status_and_counts(self):
        self.dialog.update_progress(
            LibraryImportProgress(
                percent=51,
                status="正在导入备份图片",
                completed=3,
                total=8,
                cancellable=True,
            )
        )

        self.assertEqual(51, self.dialog.progressBar.value())
        self.assertEqual("正在导入备份图片", self.dialog.labelStatus.text())
        self.assertEqual("已处理 3/8", self.dialog.labelTaskProgress.text())
        self.assertIsNone(self.dialog.findChild(QLabel, "labelDetail"))
        self.assertTrue(self.dialog.pushButtonCancel.isEnabled())

    def test_cancel_is_enabled_only_during_the_per_image_stage(self):
        self.assertFalse(self.dialog.pushButtonCancel.isEnabled())

        self.dialog.update_progress(
            LibraryImportProgress(
                percent=0,
                status="正在读取备份",
                total=2,
                cancellable=False,
            )
        )
        self.assertFalse(self.dialog.pushButtonCancel.isEnabled())

        self.dialog.update_progress(
            LibraryImportProgress(
                percent=5,
                status="正在导入备份图片",
                completed=0,
                total=2,
                cancellable=True,
            )
        )
        self.assertTrue(self.dialog.pushButtonCancel.isEnabled())

    def test_clears_counts_when_total_is_unknown(self):
        self.dialog.update_progress(
            LibraryImportProgress(percent=10, status="正在准备")
        )

        self.assertEqual("", self.dialog.labelTaskProgress.text())

    def test_cannot_close_until_the_import_finishes(self):
        self.dialog.close()
        self.app.processEvents()
        self.assertTrue(self.dialog.isVisible())

        self.dialog.finish()
        self.app.processEvents()
        self.assertFalse(self.dialog.isVisible())

    def test_cancel_emits_once_and_keeps_waiting_status(self):
        requests = []
        self.dialog.cancel_requested.connect(lambda: requests.append(True))
        self.dialog.update_progress(
            LibraryImportProgress(
                percent=5,
                status="正在导入备份图片",
                completed=0,
                total=2,
                cancellable=True,
            )
        )

        self.dialog.pushButtonCancel.click()
        self.app.processEvents()
        self.dialog.pushButtonCancel.click()
        self.app.processEvents()

        self.assertEqual([True], requests)
        self.assertFalse(self.dialog.pushButtonCancel.isEnabled())
        self.assertEqual("正在中止", self.dialog.labelStatus.text())
        self.assertTrue(self.dialog.isVisible())

        self.dialog.update_progress(
            LibraryImportProgress(
                percent=50,
                status="正在导入备份图片",
                completed=1,
                total=2,
                cancellable=True,
            )
        )
        self.assertEqual("正在中止", self.dialog.labelStatus.text())
        self.assertEqual(50, self.dialog.progressBar.value())
        self.assertFalse(self.dialog.pushButtonCancel.isEnabled())
        self.assertIsNone(self.dialog.findChild(QLabel, "labelDetail"))


if __name__ == "__main__":
    unittest.main()
