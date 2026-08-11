import datetime
import os
import tempfile
import unittest
from pathlib import Path

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PyQt6.QtGui import QImage
from PyQt6.QtWidgets import QApplication

import apppath
from commons.dto import StickerImage
from stickerdb.v1.sticker_db import StickerDBV1
from ui.dialog_image_viewer import ImageViewerDialog
from ui.widgets.image_text_edit_widget import ImageTextEditWidget


def make_sticker() -> StickerImage:
    sticker = StickerImage()
    sticker.original_file_name = "viewer.png"
    sticker.relative_path = "viewer.png"
    sticker.file_size = 1
    sticker.hash = "viewer-text-test-hash"
    sticker.extension = ".png"
    sticker.imported_at = datetime.datetime(2026, 1, 1)
    sticker.modification_date = datetime.datetime(2026, 1, 1)
    sticker.size_width = 1
    sticker.size_height = 1
    sticker.vectordb_id = None
    sticker.text_in_image = None
    return sticker


class ImageViewerTextEditorTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = QApplication.instance() or QApplication([])
        source_dir = Path(__file__).resolve().parents[1] / "src"
        apppath.setup_data_path(source_dir)

    def setUp(self):
        self._temp_dir = tempfile.TemporaryDirectory()
        self.db = StickerDBV1(str(Path(self._temp_dir.name) / "library.db"))
        self.db.add_stickers([make_sticker()])
        self.sticker = self.db.list_stickers()[0]

        self.image_path = str(Path(self._temp_dir.name) / "viewer.png")
        image = QImage(2, 2, QImage.Format.Format_ARGB32)
        image.fill(0xFFFFFFFF)
        self.assertTrue(image.save(self.image_path))

        self.dialog = ImageViewerDialog(database=self.db)
        self.dialog.load_image(self.image_path, "viewer.png", self.sticker)

    def tearDown(self):
        self.dialog.close()
        self.db.engine.dispose()
        self._temp_dir.cleanup()

    def test_text_tab_is_after_tags_and_before_file_info(self):
        tabs = self.dialog.tabWidgetBottom
        self.assertIsInstance(
            self.dialog.imageTextEditWidget,
            ImageTextEditWidget,
        )
        self.assertEqual(
            tabs.indexOf(self.dialog.tabTags) + 1,
            tabs.indexOf(self.dialog.tabText),
        )
        self.assertEqual(
            tabs.indexOf(self.dialog.tabText) + 1,
            tabs.indexOf(self.dialog.tabFileInfo),
        )
        self.assertEqual("文本", tabs.tabText(tabs.indexOf(self.dialog.tabText)))

    def test_loads_text_in_image_from_database(self):
        self.db.set_sticker_texts({self.sticker.id: "database text"})
        self.sticker = self.db.list_stickers()[0]

        self.dialog.load_image(self.image_path, "viewer.png", self.sticker)

        self.assertEqual(
            "database text",
            self.dialog.imageTextEditWidget.text_edit.toPlainText(),
        )

    def test_save_button_writes_text_to_database(self):
        self.dialog.imageTextEditWidget.text_edit.setPlainText("edited text")

        self.dialog.imageTextEditWidget.save_text()

        stored = self.db.list_stickers()[0]
        self.assertEqual("edited text", stored.text_in_image)
        self.assertEqual("edited text", self.sticker.text_in_image)

    def test_close_does_not_save_unsaved_text(self):
        self.dialog.imageTextEditWidget.text_edit.setPlainText("unsaved text")

        self.dialog.close()

        stored = self.db.list_stickers()[0]
        self.assertIsNone(stored.text_in_image)


if __name__ == "__main__":
    unittest.main()
