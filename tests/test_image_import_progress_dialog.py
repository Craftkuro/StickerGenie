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

    def test_displays_status_counts_and_detail_placeholder(self):
        progress = ImportImagesProgress(
            percent=51,
            status="正在写入图库",
            completed=3,
            total=8,
        )

        self.dialog.update_progress(progress)

        self.assertEqual(51, self.dialog.progressBar.value())
        self.assertEqual("正在写入图库", self.dialog.labelStatus.text())
        self.assertEqual(
            "正在保存图片到图库",
            self.dialog.labelDetail.text(),
        )
        self.assertEqual("", self.dialog.labelDetail.toolTip())

    def test_cannot_close_until_the_import_finishes(self):
        self.dialog.close()
        self.app.processEvents()
        self.assertTrue(self.dialog.isVisible())

        self.dialog.finish()
        self.app.processEvents()
        self.assertFalse(self.dialog.isVisible())

    def test_cancel_button_emits_once_and_keeps_the_dialog_open(self):
        cancel_requests = []
        self.dialog.cancel_requested.connect(lambda: cancel_requests.append(True))

        self.dialog.pushButtonCancel.click()
        self.app.processEvents()
        self.dialog.pushButtonCancel.click()
        self.app.processEvents()

        self.assertEqual([True], cancel_requests)
        self.assertFalse(self.dialog.pushButtonCancel.isEnabled())
        self.assertEqual("正在中止", self.dialog.labelStatus.text())
        self.assertTrue(self.dialog.isVisible())

        self.dialog.update_progress(
            ImportImagesProgress(
                percent=50,
                status="正在写入图库",
                completed=1,
                total=2,
            )
        )
        self.assertEqual("正在中止", self.dialog.labelStatus.text())
        self.assertEqual(50, self.dialog.progressBar.value())
        self.assertEqual("正在等待当前操作结束", self.dialog.labelDetail.text())


if __name__ == "__main__":
    unittest.main()
