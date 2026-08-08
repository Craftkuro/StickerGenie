import os
import unittest
from pathlib import Path

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PyQt6.QtWidgets import QApplication

import apppath
from services.import_images import ImportImagesProgress
from ui.dialog_image_import_progress import ImageImportProgressDialog


class ImageImportProgressDialogTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = QApplication.instance() or QApplication([])
        source_dir = Path(__file__).resolve().parents[1] / "src"
        apppath.setup_data_path(source_dir)

    def setUp(self):
        self.dialog = ImageImportProgressDialog()
        self.dialog.show()
        self.app.processEvents()

    def tearDown(self):
        self.dialog.finish()
        self.app.processEvents()

    def test_displays_status_counts_progress_and_last_file(self):
        progress = ImportImagesProgress(
            percent=51,
            status="正在导入图片",
            completed=3,
            total=8,
            last_file_name="a-very-long-image-file-name.png",
        )

        self.dialog.update_progress(progress)

        self.assertEqual(51, self.dialog.progressBar.value())
        self.assertEqual("正在导入图片", self.dialog.labelStatus.text())
        self.assertIn(
            "a-very-long-image-file-name.png",
            self.dialog.labelDetail.toolTip(),
        )

    def test_cannot_close_until_the_import_finishes(self):
        self.dialog.close()
        self.app.processEvents()
        self.assertTrue(self.dialog.isVisible())

        self.dialog.finish()
        self.app.processEvents()
        self.assertFalse(self.dialog.isVisible())


if __name__ == "__main__":
    unittest.main()
