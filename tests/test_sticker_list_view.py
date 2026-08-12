import datetime
import os
import tempfile
import time
import unittest
from pathlib import Path
from unittest.mock import patch

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PyQt6 import sip
from PyQt6.QtCore import QCoreApplication, QEvent, QPoint, QRect, QSize, Qt
from PyQt6.QtGui import (
    QIcon,
    QImage,
    QPainter,
    QPixmap,
    QStandardItem,
    QStandardItemModel,
)
from PyQt6.QtTest import QSignalSpy
from PyQt6.QtWidgets import (
    QApplication,
    QAbstractItemView,
    QListView,
    QMenu,
    QSlider,
    QStyle,
    QStyleOptionViewItem,
)

import apppath
from blob_storage import BlobFileEntity
from commons.dto import StickerImage
from commons.roles import ROLE_BLOB_ENTITY, ROLE_SIMILARITY
from services.sticker_library_viewer_service import build_sticker_model
from services.thumbnail_provider import ThumbnailProvider
from ui.page_finite_sticker_collection import FiniteStickerCollectionPage
from ui.page_infinite_sticker_collection import InfiniteStickerCollectionPage
from ui.page_search_result import SearchResultPage
from ui.page_similar_images import SimilarImagesPage
from ui.widgets.sticker_list_view_widget import (
    StickerItemDelegate,
    StickerListView,
)


class FakeBlobStorage:
    def __init__(self, file_path: Path):
        self.file_path = file_path

    def read_file(self, _entity):
        return str(self.file_path)


class FakeThumbnailProvider:
    def __init__(self):
        self.pixmap = QPixmap(64, 64)
        self.pixmap.fill(Qt.GlobalColor.white)

    def request_thumbnail(self, _blob_entity):
        return self.pixmap


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

    def _wait_until(self, predicate, timeout_ms: int = 5000) -> bool:
        deadline = time.monotonic() + timeout_ms / 1000
        while time.monotonic() < deadline:
            QCoreApplication.processEvents()
            if predicate():
                return True
            time.sleep(0.01)
        return False

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
        self.assertEqual(QSize(200, 200), view.iconSize())
        self.assertEqual(QSize(160, 160), view.gridSize())
        self.assertTrue(view.uniformItemSizes())
        self.assertFalse(view.wordWrap())
        self.assertIsInstance(view.itemDelegate(), StickerItemDelegate)

    def test_set_display_size_updates_grid_and_delegate(self):
        view = StickerListView()
        model = QStandardItemModel()
        model.appendRow(QStandardItem(""))
        view.setModel(model)

        view.set_display_size(120)

        self.assertEqual(QSize(120, 120), view.gridSize())
        self.assertEqual(120, view.item_size())
        delegate = view.itemDelegate()
        self.assertEqual(
            QSize(120, 120),
            delegate.sizeHint(
                QStyleOptionViewItem(),
                model.index(0, 0),
            ),
        )
        view.close()

    def test_view_emits_load_more_when_scrolled_near_bottom(self):
        view = StickerListView()
        model = QStandardItemModel()
        for _ in range(200):
            model.appendRow(QStandardItem(""))
        view.setModel(model)
        view.resize(320, 240)
        view.show()
        QApplication.processEvents()

        spy = QSignalSpy(view.load_more_requested)
        scrollbar = view.verticalScrollBar()
        self.assertGreater(scrollbar.maximum(), 0)
        scrollbar.setValue(scrollbar.maximum())
        QApplication.processEvents()
        self.assertGreater(len(spy), 0)
        view.close()

    def test_finite_page_ignores_load_more_request(self):
        page = FiniteStickerCollectionPage(auto_refresh=False)
        model = QStandardItemModel()
        for _ in range(3):
            model.appendRow(QStandardItem(""))
        page.refresh_content(model)

        page.listViewStickerList.load_more_requested.emit()

        self.assertEqual(3, page.listViewStickerList.model().rowCount())
        page.close()

    def test_search_result_page_inherits_finite_page(self):
        page = SearchResultPage(auto_refresh=False)

        self.assertIsInstance(page, FiniteStickerCollectionPage)
        self.assertEqual(
            "page_finite_sticker_collection.ui",
            page.UI_FILE_NAME,
        )
        self.assertIsInstance(page.display_size_slider, QSlider)
        page.close()

    def test_context_menu_delete_lives_in_more_submenu(self):
        page = SearchResultPage(auto_refresh=False)
        model = QStandardItemModel()
        model.appendRow(QStandardItem(""))
        page.refresh_content(model)
        index = page.listViewStickerList.model().index(0, 0)

        def fake_exec(menu, position):
            more_actions = [
                action for action in menu.actions()
                if action.text() == "更多"
            ]
            self.assertEqual(1, len(more_actions))
            more_menu = more_actions[0].menu()
            self.assertIsNotNone(more_menu)
            delete_action = more_menu.actions()[0]
            self.assertEqual("删除图片", delete_action.text())
            delete_action.trigger()
            return None

        with patch.object(
            page.listViewStickerList,
            "indexAt",
            return_value=index,
        ):
            with patch.object(QMenu, "exec", fake_exec):
                with patch.object(
                    page,
                    "_delete_sticker_for_index",
                ) as delete_mock:
                    page._show_sticker_context_menu(QPoint(0, 0))

        delete_mock.assert_called_once_with(index)
        page.close()

    def test_similar_images_page_inherits_finite_page(self):
        page = SimilarImagesPage(auto_refresh=False)

        self.assertIsInstance(page, FiniteStickerCollectionPage)
        self.assertEqual(
            "page_finite_sticker_collection.ui",
            page.UI_FILE_NAME,
        )
        self.assertIsInstance(page.display_size_slider, QSlider)
        page.close()

    def test_infinite_page_loads_more_on_request(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            image_path = Path(temp_dir) / "stored-hash.png"
            image = QImage(2, 2, QImage.Format.Format_ARGB32)
            image.fill(0xFFFFFFFF)
            self.assertTrue(image.save(str(image_path)))

            class FakePagedDB:
                def __init__(self, rows):
                    self.rows = rows

                def list_stickers(self, offset=0, count=100):
                    return self.rows[offset:offset + count]

            page_size = InfiniteStickerCollectionPage.PAGE_SIZE
            stickers = [make_sticker() for _ in range(page_size * 2 + 10)]
            with patch(
                "services.global_instances.current_library_db",
                FakePagedDB(stickers),
            ), patch(
                "services.global_instances.current_blob_storage",
                FakeBlobStorage(image_path),
            ):
                page = InfiniteStickerCollectionPage(auto_refresh=False)
                page.resize(400, 300)
                page.show()
                QApplication.processEvents()
                model = page.listViewStickerList.model()
                self.assertEqual(page_size, model.rowCount())
                self.assertTrue(page._has_more)

                page.listViewStickerList.load_more_requested.emit()
                QApplication.processEvents()

                self.assertEqual(page_size * 2, model.rowCount())
                self.assertTrue(page._has_more)

                page.listViewStickerList.load_more_requested.emit()
                QApplication.processEvents()

                self.assertEqual(len(stickers), model.rowCount())
                self.assertFalse(page._has_more)
                self.assertEqual(len(stickers), page._offset)
                page.close()

    def test_toolbar_accepts_custom_widget(self):
        page = FiniteStickerCollectionPage(auto_refresh=False)
        slider = QSlider()

        action = page.add_toolbar_widget(slider)

        self.assertIs(
            slider,
            page.toolbarStickerList.widgetForAction(action),
        )
        page.close()

    def test_infinite_page_toolbar_has_refresh_button_leftmost(self):
        page = InfiniteStickerCollectionPage(auto_refresh=False)
        actions = page.toolbarStickerList.actions()

        self.assertTrue(actions)
        self.assertIs(page.refresh_action, actions[0])
        self.assertEqual("刷新", page.refresh_action.text())
        self.assertIs(
            page.display_size_slider,
            page.toolbarStickerList.widgetForAction(actions[-1]),
        )

        spy = QSignalSpy(page.signal_refresh_content)
        page.refresh_action.trigger()
        self.assertEqual(1, len(spy))
        page.close()

    def test_display_size_slider_adjusts_view_grid(self):
        page = FiniteStickerCollectionPage(auto_refresh=False)
        slider = page.display_size_slider

        self.assertIsInstance(slider, QSlider)
        self.assertEqual(160, slider.value())
        self.assertEqual(QSize(160, 160), page.listViewStickerList.gridSize())

        slider.setValue(64)
        self.assertEqual(QSize(64, 64), page.listViewStickerList.gridSize())
        self.assertEqual(64, page.listViewStickerList.item_size())

        slider.setValue(200)
        self.assertEqual(QSize(200, 200), page.listViewStickerList.gridSize())
        self.assertEqual(200, page.listViewStickerList.item_size())
        page.close()

    def test_reset_returns_to_top_after_scrolling_to_bottom(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            image_path = Path(temp_dir) / "stored-hash.png"
            image = QImage(2, 2, QImage.Format.Format_ARGB32)
            image.fill(0xFFFFFFFF)
            self.assertTrue(image.save(str(image_path)))

            class FakePagedDB:
                def __init__(self, rows):
                    self.rows = rows

                def list_stickers(self, offset=0, count=100):
                    return self.rows[offset:offset + count]

            stickers = [make_sticker() for _ in range(150)]
            with patch(
                "services.global_instances.current_library_db",
                FakePagedDB(stickers),
            ), patch(
                "services.global_instances.current_blob_storage",
                FakeBlobStorage(image_path),
            ):
                page = InfiniteStickerCollectionPage(auto_refresh=False)
                page.resize(400, 300)
                page.show()
                QApplication.processEvents()

                scrollbar = page.listViewStickerList.verticalScrollBar()
                scrollbar.setValue(scrollbar.maximum())
                QApplication.processEvents()
                self.assertEqual(
                    150,
                    page.listViewStickerList.model().rowCount(),
                )

                page._reset_and_load_first_page()
                QApplication.processEvents()

                self.assertEqual(
                    100,
                    page.listViewStickerList.model().rowCount(),
                )
                self.assertEqual(0, scrollbar.value())
                page.close()

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

            canvas = QImage(160, 160, QImage.Format.Format_ARGB32)
            canvas.fill(0xFF00FF00)
            painter = QPainter(canvas)
            try:
                option = QStyleOptionViewItem()
                option.rect = QRect(0, 0, 160, 160)
                option.state = QStyle.StateFlag.State_Enabled
                provider = ThumbnailProvider()
                delegate = StickerItemDelegate(thumbnail_provider=provider)
                with patch(
                    "services.global_instances.current_blob_storage",
                    FakeBlobStorage(image_path),
                ), patch(
                    "services.global_instances.current_thumbnail_disk_storage",
                    None,
                ), patch(
                    "services.global_instances.current_thumbnail_provider",
                    None,
                ):
                    delegate.paint(painter, option, model.index(0, 0))
                self.assertTrue(
                    self._wait_until(
                        lambda: "wide-hash" in provider._memory_cache
                    )
                )
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

    def _make_icon_item(
        self,
        similarity: float | None = None,
    ) -> QStandardItem:
        pixmap = QPixmap(64, 64)
        pixmap.fill(Qt.GlobalColor.white)
        item = QStandardItem(QIcon(pixmap), "")
        if similarity is not None:
            item.setData(similarity, ROLE_SIMILARITY)
        return item

    def _make_blob_icon_item(self, extension: str) -> QStandardItem:
        pixmap = QPixmap(64, 64)
        pixmap.fill(Qt.GlobalColor.white)
        item = QStandardItem(QIcon(pixmap), "")
        item.setData(
            BlobFileEntity("gif-hash", extension),
            ROLE_BLOB_ENTITY,
        )
        return item

    def _paint_item(
        self,
        item: QStandardItem,
        item_size: int,
        thumbnail_provider=None,
    ) -> QImage:
        model = QStandardItemModel()
        model.appendRow(item)
        canvas = QImage(item_size, item_size, QImage.Format.Format_ARGB32)
        canvas.fill(0xFF00FF00)
        painter = QPainter(canvas)
        try:
            option = QStyleOptionViewItem()
            option.rect = QRect(0, 0, item_size, item_size)
            option.state = QStyle.StateFlag.State_Enabled
            delegate = StickerItemDelegate(
                thumbnail_provider=thumbnail_provider
            )
            delegate.set_item_size(item_size)
            delegate.paint(painter, option, model.index(0, 0))
        finally:
            painter.end()
        return canvas

    @staticmethod
    def _yellow_pixel_bounds(image: QImage):
        min_x = min_y = max_x = max_y = None
        for y in range(image.height()):
            for x in range(image.width()):
                color = image.pixelColor(x, y)
                if (
                    color.red() > 230
                    and color.green() > 190
                    and color.blue() < 110
                ):
                    if min_x is None or x < min_x:
                        min_x = x
                    if min_y is None or y < min_y:
                        min_y = y
                    if max_x is None or x > max_x:
                        max_x = x
                    if max_y is None or y > max_y:
                        max_y = y
        if min_x is None:
            return None
        return (min_x, min_y, max_x, max_y)

    @staticmethod
    def _black_pixel_count(image: QImage, bounds) -> int:
        min_x, min_y, max_x, max_y = bounds
        count = 0
        for y in range(min_y, max_y + 1):
            for x in range(min_x, max_x + 1):
                color = image.pixelColor(x, y)
                if (
                    color.red() < 90
                    and color.green() < 90
                    and color.blue() < 90
                    ):
                    count += 1
        return count

    @staticmethod
    def _pink_pixel_bounds(image: QImage):
        min_x = min_y = max_x = max_y = None
        for y in range(image.height()):
            for x in range(image.width()):
                color = image.pixelColor(x, y)
                if (
                    color.red() > 235
                    and 70 < color.green() < 140
                    and 120 < color.blue() < 190
                ):
                    if min_x is None or x < min_x:
                        min_x = x
                    if min_y is None or y < min_y:
                        min_y = y
                    if max_x is None or x > max_x:
                        max_x = x
                    if max_y is None or y > max_y:
                        max_y = y
        if min_x is None:
            return None
        return (min_x, min_y, max_x, max_y)

    @staticmethod
    def _white_pixel_count(image: QImage, bounds) -> int:
        min_x, min_y, max_x, max_y = bounds
        count = 0
        for y in range(min_y, max_y + 1):
            for x in range(min_x, max_x + 1):
                color = image.pixelColor(x, y)
                if (
                    color.red() > 230
                    and color.green() > 230
                    and color.blue() > 230
                ):
                    count += 1
        return count

    def test_similarity_badge_is_drawn_at_thumbnail_top_right(self):
        canvas = self._paint_item(self._make_icon_item(0.92), 160)

        bounds = self._yellow_pixel_bounds(canvas)
        self.assertIsNotNone(bounds)
        min_x, min_y, max_x, max_y = bounds
        self.assertGreater(min_x, 80)
        self.assertLess(max_y, 80)
        self.assertGreater(self._black_pixel_count(canvas, bounds), 0)

    def test_similarity_badge_size_is_fixed_across_item_sizes(self):
        small_canvas = self._paint_item(self._make_icon_item(0.92), 96)
        large_canvas = self._paint_item(self._make_icon_item(0.92), 200)

        small_bounds = self._yellow_pixel_bounds(small_canvas)
        large_bounds = self._yellow_pixel_bounds(large_canvas)
        self.assertIsNotNone(small_bounds)
        self.assertIsNotNone(large_bounds)
        self.assertEqual(
            small_bounds[2] - small_bounds[0] + 1,
            large_bounds[2] - large_bounds[0] + 1,
        )
        self.assertEqual(
            small_bounds[3] - small_bounds[1] + 1,
            large_bounds[3] - large_bounds[1] + 1,
        )

    def test_no_similarity_means_no_badge(self):
        canvas = self._paint_item(self._make_icon_item(), 160)
        self.assertIsNone(self._yellow_pixel_bounds(canvas))

    def test_gif_badge_is_drawn_at_thumbnail_top_left(self):
        provider = FakeThumbnailProvider()
        canvas = self._paint_item(
            self._make_blob_icon_item(".gif"),
            160,
            thumbnail_provider=provider,
        )

        bounds = self._pink_pixel_bounds(canvas)
        self.assertIsNotNone(bounds)
        min_x, min_y, max_x, max_y = bounds
        self.assertLess(max_x, 80)
        self.assertLess(max_y, 80)
        self.assertLess(min_x, 30)
        self.assertLess(min_y, 30)
        self.assertGreater(self._white_pixel_count(canvas, bounds), 0)

    def test_gif_badge_size_is_fixed_across_item_sizes(self):
        provider = FakeThumbnailProvider()
        small_canvas = self._paint_item(
            self._make_blob_icon_item(".gif"),
            96,
            thumbnail_provider=provider,
        )
        large_canvas = self._paint_item(
            self._make_blob_icon_item(".gif"),
            200,
            thumbnail_provider=provider,
        )

        small_bounds = self._pink_pixel_bounds(small_canvas)
        large_bounds = self._pink_pixel_bounds(large_canvas)
        self.assertIsNotNone(small_bounds)
        self.assertIsNotNone(large_bounds)
        self.assertEqual(
            small_bounds[2] - small_bounds[0] + 1,
            large_bounds[2] - large_bounds[0] + 1,
        )
        self.assertEqual(
            small_bounds[3] - small_bounds[1] + 1,
            large_bounds[3] - large_bounds[1] + 1,
        )

    def test_gif_badge_supports_uppercase_extension(self):
        provider = FakeThumbnailProvider()
        canvas = self._paint_item(
            self._make_blob_icon_item(".GIF"),
            160,
            thumbnail_provider=provider,
        )

        self.assertIsNotNone(self._pink_pixel_bounds(canvas))

    def test_non_gif_has_no_gif_badge(self):
        provider = FakeThumbnailProvider()
        canvas = self._paint_item(
            self._make_blob_icon_item(".png"),
            160,
            thumbnail_provider=provider,
        )

        self.assertIsNone(self._pink_pixel_bounds(canvas))

    def test_gif_badge_and_similarity_badge_can_coexist(self):
        provider = FakeThumbnailProvider()
        item = self._make_blob_icon_item(".gif")
        item.setData(0.92, ROLE_SIMILARITY)
        canvas = self._paint_item(
            item,
            160,
            thumbnail_provider=provider,
        )

        self.assertIsNotNone(self._pink_pixel_bounds(canvas))
        self.assertIsNotNone(self._yellow_pixel_bounds(canvas))

    def test_library_page_uses_sticker_list_view(self):
        page = InfiniteStickerCollectionPage(auto_refresh=False)
        self.assertIsInstance(page.listViewStickerList, StickerListView)
        self.assertFalse(page.listViewStickerList.dragEnabled())
        self.assertFalse(page.listViewStickerList.acceptDrops())
        self.assertEqual(
            QAbstractItemView.DragDropMode.NoDragDrop,
            page.listViewStickerList.dragDropMode(),
        )
        page.close()

    def test_page_owns_models_and_disposes_replaced_model(self):
        page = FiniteStickerCollectionPage(auto_refresh=False)
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

    def test_view_repaints_matching_item_when_thumbnail_ready(self):
        provider = ThumbnailProvider()
        view = StickerListView(thumbnail_provider=provider)
        model = QStandardItemModel()
        item = QStandardItem("")
        item.setData(BlobFileEntity("ready-hash", ".png"), ROLE_BLOB_ENTITY)
        model.appendRow(item)
        view.setModel(model)

        with patch.object(view, "_update_item") as update_item:
            provider.thumbnail_ready.emit(
                "ready-hash",
                QImage(1, 1, QImage.Format.Format_RGB32),
            )

        update_item.assert_called_once()
        updated_index = update_item.call_args.args[0]
        self.assertEqual(
            "ready-hash",
            updated_index.data(ROLE_BLOB_ENTITY).hash,
        )
        view.close()

    def test_view_ignores_thumbnail_ready_for_absent_hash(self):
        provider = ThumbnailProvider()
        view = StickerListView(thumbnail_provider=provider)
        model = QStandardItemModel()
        item = QStandardItem("")
        item.setData(BlobFileEntity("other-hash", ".png"), ROLE_BLOB_ENTITY)
        model.appendRow(item)
        view.setModel(model)

        with patch.object(view, "_update_item") as update_item:
            provider.thumbnail_ready.emit(
                "absent-hash",
                QImage(1, 1, QImage.Format.Format_RGB32),
            )

        update_item.assert_not_called()
        view.close()

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

            page = FiniteStickerCollectionPage(auto_refresh=False)
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
