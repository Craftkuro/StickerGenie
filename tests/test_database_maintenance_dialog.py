import os
import unittest
from pathlib import Path

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PyQt6.QtWidgets import QApplication

import apppath
from services.database_maintenance import (
    DatabaseMaintenanceProgress,
    VectorMaintenanceScope,
)
from ui.dialog_database_maintenance import DatabaseMaintenanceDialog


class DatabaseMaintenanceDialogTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = QApplication.instance() or QApplication([])
        source_dir = Path(__file__).resolve().parents[1] / "src"
        apppath.setup_data_path(source_dir)

    def setUp(self):
        self.dialog = DatabaseMaintenanceDialog()
        self.dialog.show()
        self.app.processEvents()

    def tearDown(self):
        self.dialog.finish()
        self.app.processEvents()

    def test_defaults_to_both_tasks_and_missing_vector_scope(self):
        options = self.dialog.selected_options()

        self.assertTrue(options.delete_orphan_blobs)
        self.assertTrue(options.generate_vectors)
        self.assertFalse(options.delete_thumbnail_cache)
        self.assertIs(VectorMaintenanceScope.MISSING, options.vector_scope)

    def test_start_is_disabled_when_no_task_is_selected(self):
        self.dialog.checkBoxDeleteOrphanBlobs.setChecked(False)
        self.dialog.checkBoxGenerateVectors.setChecked(False)
        self.dialog.checkBoxDeleteThumbnailCache.setChecked(False)

        self.assertFalse(self.dialog.pushButtonStart.isEnabled())
        self.assertFalse(self.dialog.comboBoxVectorScope.isEnabled())

    def test_thumbnail_cache_task_alone_enables_start(self):
        self.dialog.checkBoxDeleteOrphanBlobs.setChecked(False)
        self.dialog.checkBoxGenerateVectors.setChecked(False)
        self.dialog.checkBoxDeleteThumbnailCache.setChecked(True)

        self.assertTrue(self.dialog.pushButtonStart.isEnabled())
        options = self.dialog.selected_options()
        self.assertFalse(options.delete_orphan_blobs)
        self.assertFalse(options.generate_vectors)
        self.assertTrue(options.delete_thumbnail_cache)

    def test_start_emits_options_and_locks_configuration(self):
        requests = []
        self.dialog.maintenance_requested.connect(requests.append)

        self.dialog.pushButtonStart.click()
        self.app.processEvents()

        self.assertEqual(1, len(requests))
        self.assertFalse(self.dialog.groupBoxOperations.isEnabled())
        self.assertFalse(self.dialog.pushButtonStart.isEnabled())
        self.dialog.close()
        self.app.processEvents()
        self.assertTrue(self.dialog.isVisible())

    def test_cancel_is_available_only_for_vector_progress(self):
        cancel_requests = []
        self.dialog.cancel_requested.connect(lambda: cancel_requests.append(True))
        self.dialog.pushButtonStart.click()

        self.dialog.update_progress(
            DatabaseMaintenanceProgress(
                25,
                "删除未引用的Blob数据",
                "正在清理Blob存储",
                1,
                2,
                False,
            )
        )
        self.assertFalse(self.dialog.pushButtonCancel.isEnabled())

        self.dialog.update_progress(
            DatabaseMaintenanceProgress(
                75,
                "生成图片特征向量",
                "正在生成图片向量",
                1,
                2,
                True,
            )
        )
        self.assertTrue(self.dialog.pushButtonCancel.isEnabled())
        self.assertIn("1/2", self.dialog.labelTaskProgress.text())

        self.dialog.pushButtonCancel.click()
        self.app.processEvents()
        self.dialog.pushButtonCancel.click()
        self.app.processEvents()
        self.assertEqual([True], cancel_requests)
        self.assertEqual("正在中止向量生成", self.dialog.labelStatus.text())


if __name__ == "__main__":
    unittest.main()
