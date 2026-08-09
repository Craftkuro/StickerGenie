import datetime
import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PyQt6 import sip
from PyQt6.QtCore import QCoreApplication, QEvent, QRect, QSize
from PyQt6.QtGui import QIcon, QImage, QPainter, QStandardItem, QStandardItemModel
from PyQt6.QtWidgets import (
    QApplication,
    QAbstractItemView,
    QListView,
    QStyle,
    QStyleOptionViewItem,
)

import apppath
from blob_storage import BlobFileEntity
from commons.dto import StickerImage
from commons.roles import ROLE_BLOB_ENTITY
from services.sticker_library_viewer_service import build_sticker_model
from ui.page_sticker_library_view import StickerLibraryViewPage
from ui.sticker_list_view_widget import (
    StickerItemDelegate,
    StickerListView,
)


class FakeBlobStorage:
    def __init__(self, file_path: Path):
        self.file_path = file_path

    def read_file(self, _entity):
        return str(self.file_path)


def make_sticker() -> StickerImage:
    sticker = StickerImage()
    sticker.id = 7
    sticker.original_file_name = "原始名称.png"
    sticker.relative_path = "原始名称.png"
    sticker.file_size = 1
    sticker.hash = "stored-hash"
    sticker.extension = ".png"
    sticker.imported_at = datetime.datetime(2026, 8, 9, 9, 0, 0)
    sticker.modification_date = datetime.datetime(2026, 8, 8, 12, 34, 56)
    sticker.size_width = 1
    sticker.size_height = 1
    sticker.vectordb_id = None
    sticker.text_in_image = None
    return sticker


class StickerListViewTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = QApplication.instance() or QApplication([])
        source_dir = Path(__file__).resolve().parents[1] / "src"
        apppath.setup_data_path(source_dir)

    def test_uses_large_image_only_grid(self):
        view = StickerListView()

        self.assertEqual(QListView.ViewMode.IconMode, view.viewMode())
        self.assertEqual(QListView.ResizeMode.Adjust, view.resizeMode())
        self.assertEqual(QListView.Movement.Static, view.movement())
        self.assertFalse(view.dragEnabled())
        self.assertFalse(view.acceptDrops())
        self.assertEqual(
            QAbstractItemView.DragDropMode.NoDragDrop,
            view.dragDropMode(),
        )
        self.assertEqual(QSize(144, 144), view.iconSize())
        self.assertEqual(QSize(160, 160), view.gridSize())
        self.assertTrue(view.uniformItemSizes())
        self.assertFalse(view.wordWrap())
        self.assertIsInstance(view.itemDelegate(), StickerItemDelegate)

    def test_paint_preserves_aspect_ratio_for_jpeg_thumbnails(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            image_path = Path(temp_dir) / "wide.jpg"
            image = QImage(400, 100, QImage.Format.Format_RGB32)
            image.fill(0xFFFF0000)
            self.assertTrue(image.save(str(image_path)))

            item = QStandardItem(QIcon(str(image_path)), "")
            item.setData(
                BlobFileEntity("wide-hash", ".jpg"),
                ROLE_BLOB_ENTITY,
            )
            model = QStandardItemModel()
            model.appendRow(item)

            delegate = StickerItemDelegate()
            canvas = QImage(160, 160, QImage.Format.Format_ARGB32)
            canvas.fill(0xFF00FF00)
            painter = QPainter(canvas)
            try:
                option = QStyleOptionViewItem()
                option.rect = QRect(0, 0, 160, 160)
                option.state = QStyle.StateFlag.State_Enabled
                with patch(
                    "services.global_instances.current_blob_storage",
                    FakeBlobStorage(image_path),
                ):
                    delegate.paint(painter, option, model.index(0, 0))
            finally:
                painter.end()

            min_x, min_y = 160, 160
            max_x, max_y = -1, -1
            for y in range(canvas.height()):
                for x in range(canvas.width()):
                    color = canvas.pixelColor(x, y)
                    if color.red() > 200 and color.green() < 100:
                        min_x = min(min_x, x)
                        min_y = min(min_y, y)
                        max_x = max(max_x, x)
                        max_y = max(max_y, y)

            width = max_x - min_x + 1
            height = max_y - min_y + 1
            self.assertEqual((144, 36), (width, height))

    def test_library_page_uses_sticker_list_view(self):
        page = StickerLibraryViewPage(auto_refresh=False)
        self.assertIsInstance(page.listViewStickerList, StickerListView)
        self.assertFalse(page.listViewStickerList.dragEnabled())
        self.assertFalse(page.listViewStickerList.acceptDrops())
        self.assertEqual(
            QAbstractItemView.DragDropMode.NoDragDrop,
            page.listViewStickerList.dragDropMode(),
        )
        page.close()

    def test_page_owns_models_and_disposes_replaced_model(self):
        page = StickerLibraryViewPage(auto_refresh=False)
        first_model = QStandardItemModel()
        second_model = QStandardItemModel()

        page.refresh_content(first_model)
        self.assertIs(page.listViewStickerList, first_model.parent())

        page.refresh_content(second_model)
        self.assertIs(page.listViewStickerList, second_model.parent())
        self.assertIs(second_model, page.listViewStickerList.model())

        QCoreApplication.sendPostedEvents(None, QEvent.Type.DeferredDelete)
        self.assertTrue(sip.isdeleted(first_model))
        self.assertFalse(sip.isdeleted(second_model))

        page.deleteLater()
        QCoreApplication.sendPostedEvents(None, QEvent.Type.DeferredDelete)
        self.assertTrue(sip.isdeleted(second_model))

    def test_model_hides_text_and_exposes_metadata_in_tooltip(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            image_path = Path(temp_dir) / "stored-hash.png"
            image = QImage(2, 2, QImage.Format.Format_ARGB32)
            image.fill(0xFFFFFFFF)
            self.assertTrue(image.save(str(image_path)))

            with patch(
                "services.global_instances.current_blob_storage",
                FakeBlobStorage(image_path),
            ):
                model = build_sticker_model([make_sticker()])

        item = model.item(0)
        self.assertEqual("", item.text())
        self.assertEqual(
            "文件名：stored-hash.png\n"
            "原始文件名：原始名称.png\n"
            "修改日期：2026-08-08 12:34:56",
            item.toolTip(),
        )

    def test_similarity_is_appended_to_metadata_tooltip(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            image_path = Path(temp_dir) / "stored-hash.png"
            with patch(
                "services.global_instances.current_blob_storage",
                FakeBlobStorage(image_path),
            ):
                model = build_sticker_model([make_sticker()], {7: 0.875})

        self.assertTrue(model.item(0).toolTip().endswith("相似度：87.5%"))

    def test_copy_uses_current_item_file_and_original_name(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            image_path = Path(temp_dir) / "stored-hash.png"
            image = QImage(2, 2, QImage.Format.Format_ARGB32)
            image.fill(0xFFFFFFFF)
            self.assertTrue(image.save(str(image_path)))

            with patch(
                "services.global_instances.current_blob_storage",
                FakeBlobStorage(image_path),
            ):
                model = build_sticker_model([make_sticker()])

            page = StickerLibraryViewPage(auto_refresh=False)
            page.refresh_content(model)
            with patch(
                "services.image_clipboard_service.copy_image_to_clipboard"
            ) as copy_image:
                page._copy_sticker_for_index(model.index(0, 0))

            copy_image.assert_called_once_with(
                str(image_path),
                "原始名称.png",
            )
            page.close()


if __name__ == "__main__":
    unittest.main()
