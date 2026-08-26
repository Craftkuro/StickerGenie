import datetime
import os
import tempfile
import time
import unittest
from pathlib import Path
from unittest.mock import patch

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PyQt6 import sip
from PyQt6.QtCore import (
    QCoreApplication,
    QEvent,
    QItemSelectionModel,
    QPoint,
    QPointF,
    QRect,
    QSize,
    QStandardPaths,
    Qt,
)
from PyQt6.QtGui import (
    QAction,
    QFontMetrics,
    QIcon,
    QImage,
    QPainter,
    QPixmap,
    QStandardItem,
    QStandardItemModel,
    QWheelEvent,
)
from PyQt6.QtTest import QSignalSpy, QTest
from PyQt6.QtWidgets import (
    QApplication,
    QAbstractItemView,
    QListView,
    QMenu,
    QMessageBox,
    QSlider,
    QStyle,
    QStyleOptionViewItem,
    QToolButton,
)

import apppath
import commons.constants
from blob_storage import BlobFileEntity
from commons.dto import StickerImage, Tag
from commons.roles import (
    ROLE_BLOB_ENTITY,
    ROLE_FILE_PATH,
    ROLE_SIMILARITY,
    ROLE_STICKER_IMAGE,
)
from commons.sticker_list_model import StickerListModel
import services.global_instances
from services.settings import create_settings_manager
from services.sticker_library_viewer_service import (
    build_sticker_model,
    load_library_page,
    wiring,
)
from services.thumbnail_provider import ThumbnailProvider
from ui.page_finite_sticker_collection import FiniteStickerCollectionPage
from ui.page_infinite_sticker_collection import InfiniteStickerCollectionPage
from ui.page_search_result import SearchResultPage
from ui.page_similar_images import SimilarImagesPage
from ui.widgets.sticker_list_view_widget import (
    MORE_BADGE_PAD_X,
    StickerItemDelegate,
    StickerListView,
    TAG_CHIP_GAP,
    layout_tag_chips,
)
from ui.widgets.toolbar_spacer import ToolbarSpacer


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

    def _send_wheel(self, view, delta, modifiers=Qt.KeyboardModifier.NoModifier):
        position = view.viewport().rect().center()
        event = QWheelEvent(
            QPointF(position),
            QPointF(view.viewport().mapToGlobal(position)),
            QPoint(0, delta),
            QPoint(0, delta),
            Qt.MouseButton.NoButton,
            modifiers,
            Qt.ScrollPhase.NoScrollPhase,
            False,
        )
        QApplication.sendEvent(view.viewport(), event)

    def _focus_list_view(self, page):
        page.show()
        page.listViewStickerList.setFocus()
        QApplication.processEvents()

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
        self.assertEqual(
            QAbstractItemView.SelectionMode.ExtendedSelection,
            view.selectionMode(),
        )
        self.assertEqual(QSize(200, 200), view.iconSize())
        self.assertEqual(QSize(120, 120), view.gridSize())
        self.assertTrue(view.uniformItemSizes())
        self.assertFalse(view.wordWrap())
        self.assertIsInstance(view.itemDelegate(), StickerItemDelegate)

    def test_new_view_uses_configured_default_icon_size(self):
        with tempfile.TemporaryDirectory() as temporary_directory:
            manager = create_settings_manager(
                Path(temporary_directory) / "settings.toml"
            )
            manager.set("default_icon_size", 140)
            with patch.object(
                services.global_instances,
                "current_settings_manager",
                manager,
            ):
                view = StickerListView()

        self.assertEqual(QSize(140, 140), view.gridSize())
        self.assertEqual(140, view.item_size())
        view.close()

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

    def test_switching_back_to_icon_mode_restores_wheel_scroll_step(self):
        # Qt 只在列表布局路径里重算滚动条 singleStep；图标模式切过去再
        # 切回来后若残留列表模式的值，滚轮每格只会滚动几像素。
        view = StickerListView()
        model = QStandardItemModel()
        for _ in range(200):
            model.appendRow(QStandardItem(""))
        view.setModel(model)
        view.resize(800, 600)
        view.show()
        QApplication.processEvents()

        initial_single_step = view.verticalScrollBar().singleStep()
        self.assertGreater(initial_single_step, 1)

        view.set_display_mode(
            commons.constants.LIST_DISPLAY_MODE_LIST
        )
        QApplication.processEvents()
        self.assertEqual(1, view.verticalScrollBar().singleStep())

        view.set_display_mode(
            commons.constants.LIST_DISPLAY_MODE_ICON
        )
        QApplication.processEvents()

        self.assertEqual(
            initial_single_step,
            view.verticalScrollBar().singleStep(),
        )

        # 归还控制权后，图标模式下调整尺寸应像启动时一样自动跟随，
        # 无需再次切换模式。
        view.set_display_size(240)
        view.doItemsLayout()
        self.assertEqual(
            240 + view.spacing(),
            view.verticalScrollBar().singleStep(),
        )

        # 调整图标尺寸后再次往返，步长应跟随新的行距。
        view.set_display_size(160)
        expected_step = 160 + view.spacing()
        view.set_display_mode(
            commons.constants.LIST_DISPLAY_MODE_LIST
        )
        view.set_display_mode(
            commons.constants.LIST_DISPLAY_MODE_ICON
        )
        QApplication.processEvents()
        self.assertEqual(expected_step, view.verticalScrollBar().singleStep())
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
            self.assertEqual("移动到图库回收站", delete_action.text())
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

    def test_context_menu_multi_selection_offers_copy_save_as_and_delete(self):
        page = SearchResultPage(auto_refresh=False)
        model = QStandardItemModel()
        model.appendRow(QStandardItem(""))
        model.appendRow(QStandardItem(""))
        page.refresh_content(model)
        view = page.listViewStickerList
        selection_model = view.selectionModel()
        selection_model.select(
            model.index(0, 0),
            QItemSelectionModel.SelectionFlag.ClearAndSelect,
        )
        selection_model.select(
            model.index(1, 0),
            QItemSelectionModel.SelectionFlag.Select,
        )

        def fake_exec(menu, position):
            action_texts = [action.text() for action in menu.actions()]
            self.assertEqual(
                ["复制到剪贴板", "", "另存为", "批量编辑标签", "", "更多"],
                action_texts,
            )
            copy_action = menu.actions()[0]
            copy_action.trigger()
            batch_action = menu.actions()[3]
            batch_action.trigger()
            more_menu = menu.actions()[5].menu()
            delete_action = more_menu.actions()[0]
            self.assertEqual("移动到图库回收站", delete_action.text())
            delete_action.trigger()
            return None

        with patch.object(
            view,
            "indexAt",
            return_value=model.index(1, 0),
        ):
            with patch.object(QMenu, "exec", fake_exec):
                with patch.object(
                    page,
                    "_delete_stickers_for_indexes",
                ) as delete_mock:
                    with patch.object(
                        page,
                        "_batch_edit_tags_for_indexes",
                    ) as batch_mock:
                        with patch.object(
                            page,
                            "_copy_stickers_for_indexes",
                        ) as copy_mock:
                            page._show_sticker_context_menu(QPoint(0, 0))

        self.assertEqual(
            [0, 1],
            [index.row() for index in delete_mock.call_args.args[0]],
        )
        self.assertEqual(
            [0, 1],
            [index.row() for index in batch_mock.call_args.args[0]],
        )
        self.assertEqual(
            [0, 1],
            [index.row() for index in copy_mock.call_args.args[0]],
        )
        copy_mock.assert_called_once()
        page.close()

    def test_batch_tag_update_preserves_model_dto_reference(self):
        page = SearchResultPage(auto_refresh=False)
        model = StickerListModel()
        current_sticker = make_sticker()
        item = QStandardItem("")
        item.setData(current_sticker, ROLE_STICKER_IMAGE)
        model.appendRow(item)
        page.refresh_content(model)

        updated_sticker = make_sticker()
        updated_sticker.tags = []
        updated_tag = Tag()
        updated_tag.id = 123
        updated_tag.name = "Updated"
        updated_sticker.tags = [updated_tag]

        page._update_sticker_dtos([updated_sticker])

        stored_sticker = model.index(0, 0).data(ROLE_STICKER_IMAGE)
        self.assertIs(current_sticker, stored_sticker)
        self.assertEqual(["Updated"], [tag.name for tag in stored_sticker.tags])
        page.close()

    def test_context_menu_image_properties_opens_viewer_for_single_selection(self):
        page = SearchResultPage(auto_refresh=False)
        model = QStandardItemModel()
        model.appendRow(QStandardItem(""))
        page.refresh_content(model)
        index = page.listViewStickerList.model().index(0, 0)

        def fake_exec(menu, position):
            action_texts = [action.text() for action in menu.actions()]
            self.assertEqual(
                [
                    "复制到剪贴板",
                    "",
                    "查找相似图片",
                    "另存为",
                    "图片属性",
                    "",
                    "更多",
                ],
                action_texts,
            )
            image_properties_action = next(
                action
                for action in menu.actions()
                if action.text() == "图片属性"
            )
            image_properties_action.trigger()
            return None

        with patch.object(
            page.listViewStickerList,
            "indexAt",
            return_value=index,
        ):
            with patch.object(QMenu, "exec", fake_exec):
                with patch.object(
                    page,
                    "_open_image_viewer_for_index",
                ) as open_mock:
                    page._show_sticker_context_menu(QPoint(0, 0))

        open_mock.assert_called_once_with(index)
        page.close()

    def test_context_menu_gif_offers_copy_first_frame_after_copy(self):
        page = SearchResultPage(auto_refresh=False)
        model = QStandardItemModel()
        item = QStandardItem("")
        item.setData(BlobFileEntity("gif-hash", ".gif"), ROLE_BLOB_ENTITY)
        model.appendRow(item)
        page.refresh_content(model)
        index = page.listViewStickerList.model().index(0, 0)

        def fake_exec(menu, position):
            action_texts = [action.text() for action in menu.actions()]
            self.assertEqual(
                [
                    "复制到剪贴板",
                    "复制首帧到剪贴板",
                    "",
                    "查找相似图片",
                    "另存为",
                    "图片属性",
                    "",
                    "更多",
                ],
                action_texts,
            )
            copy_first_frame_action = next(
                action
                for action in menu.actions()
                if action.text() == "复制首帧到剪贴板"
            )
            copy_first_frame_action.trigger()
            return None

        with patch.object(
            page.listViewStickerList,
            "indexAt",
            return_value=index,
        ):
            with patch.object(QMenu, "exec", fake_exec):
                with patch.object(
                    page,
                    "_copy_sticker_for_index",
                ) as copy_sticker_mock:
                    page._show_sticker_context_menu(QPoint(0, 0))

        copy_sticker_mock.assert_called_once_with(
            index,
            anim_as_static_image=True,
        )
        page.close()

    def test_save_as_single_uses_file_dialog_and_copies_image(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            temp_root = Path(temp_dir)
            source_path = temp_root / "source.png"
            source_path.write_bytes(b"single-image")
            target_path = temp_root / "saved.png"

            model = QStandardItemModel()
            item = QStandardItem("")
            item.setData(make_sticker(), ROLE_STICKER_IMAGE)
            item.setData(str(source_path), ROLE_FILE_PATH)
            model.appendRow(item)

            page = SearchResultPage(auto_refresh=False)
            page.refresh_content(model)
            with patch(
                "ui.widgets.sticker_list_page.QFileDialog.getSaveFileName",
                return_value=(str(target_path), ""),
            ) as dialog_mock, patch(
                "ui.widgets.sticker_list_page.QMessageBox.information"
            ) as information_mock:
                page._save_as_for_indexes([model.index(0, 0)])

            dialog_mock.assert_called_once_with(
                page,
                "另存为",
                str(
                    Path(
                        QStandardPaths.writableLocation(
                            QStandardPaths.StandardLocation.DesktopLocation
                        )
                    )
                    / "原始名称.png"
                ),
            )
            self.assertEqual(b"single-image", target_path.read_bytes())
            information_mock.assert_called_once_with(
                page,
                "导出完成",
                "已导出1张图片。",
            )
            page.close()

    def test_save_as_multi_uses_directory_dialog_and_copies_all(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            temp_root = Path(temp_dir)
            source_paths = {
                "one.png": b"one",
                "two.png": b"two",
            }
            model = QStandardItemModel()
            for file_name, content in source_paths.items():
                source_path = temp_root / f"source-{file_name}"
                source_path.write_bytes(content)
                sticker = make_sticker()
                sticker.original_file_name = file_name
                item = QStandardItem("")
                item.setData(sticker, ROLE_STICKER_IMAGE)
                item.setData(str(source_path), ROLE_FILE_PATH)
                model.appendRow(item)

            destination = temp_root / "export"
            destination.mkdir()
            page = SearchResultPage(auto_refresh=False)
            page.refresh_content(model)
            indexes = [model.index(0, 0), model.index(1, 0)]
            with patch(
                "ui.widgets.sticker_list_page.QFileDialog.getExistingDirectory",
                return_value=str(destination),
            ) as dialog_mock, patch(
                "ui.widgets.sticker_list_page.QMessageBox.information"
            ) as information_mock:
                page._save_as_for_indexes(indexes)

            dialog_mock.assert_called_once_with(
                page,
                "选择保存目录",
                QStandardPaths.writableLocation(
                    QStandardPaths.StandardLocation.DesktopLocation
                ),
            )
            self.assertEqual(b"one", (destination / "one.png").read_bytes())
            self.assertEqual(b"two", (destination / "two.png").read_bytes())
            information_mock.assert_called_once_with(
                page,
                "导出完成",
                "已导出2张图片。",
            )
            page.close()

    def test_save_as_multi_rejects_duplicate_original_file_names(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            temp_root = Path(temp_dir)
            model = QStandardItemModel()
            for index in range(2):
                sticker = make_sticker()
                sticker.original_file_name = "same.png"
                item = QStandardItem("")
                item.setData(sticker, ROLE_STICKER_IMAGE)
                item.setData(str(temp_root / f"source-{index}.png"), ROLE_FILE_PATH)
                model.appendRow(item)

            page = SearchResultPage(auto_refresh=False)
            page.refresh_content(model)
            with patch(
                "ui.widgets.sticker_list_page.QFileDialog.getExistingDirectory"
            ) as dialog_mock, patch(
                "ui.widgets.sticker_list_page.QMessageBox.warning"
            ) as warning_mock, patch(
                "utils.save_as_files.shutil.copy2"
            ) as copy_mock:
                page._save_as_for_indexes(
                    [model.index(0, 0), model.index(1, 0)]
                )

            dialog_mock.assert_not_called()
            copy_mock.assert_not_called()
            warning_mock.assert_called_once_with(
                page,
                "无法另存为",
                "您所选的文件名的原始文件名有重复，请少选一些或使用图库导出的功能。",
            )
            page.close()

    def test_save_as_multi_reports_partial_failure(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            temp_root = Path(temp_dir)
            model = QStandardItemModel()
            for file_name in ("one.png", "two.png"):
                source_path = temp_root / f"source-{file_name}"
                source_path.write_bytes(file_name.encode("ascii"))
                sticker = make_sticker()
                sticker.original_file_name = file_name
                item = QStandardItem("")
                item.setData(sticker, ROLE_STICKER_IMAGE)
                item.setData(str(source_path), ROLE_FILE_PATH)
                model.appendRow(item)

            destination = temp_root / "export"
            destination.mkdir()
            page = SearchResultPage(auto_refresh=False)
            page.refresh_content(model)
            with patch(
                "ui.widgets.sticker_list_page.QFileDialog.getExistingDirectory",
                return_value=str(destination),
            ), patch(
                "utils.save_as_files.shutil.copy2",
                side_effect=[None, OSError("boom")],
            ), patch(
                "utils.save_as_files.logger.exception"
            ), patch(
                "ui.widgets.sticker_list_page.QMessageBox.information"
            ) as information_mock:
                page._save_as_for_indexes(
                    [model.index(0, 0), model.index(1, 0)]
                )

            information_mock.assert_called_once_with(
                page,
                "导出完成",
                "已导出1张图片，1张导出失败。",
            )
            page.close()

    def test_delete_stickers_prunes_rows_via_broadcast_signal(self):
        page = SearchResultPage(auto_refresh=False)
        model = StickerListModel()
        stickers = []
        for sticker_id in (1, 2, 3):
            sticker = make_sticker()
            sticker.id = sticker_id
            stickers.append(sticker)
            item = QStandardItem("")
            item.setData(sticker, ROLE_STICKER_IMAGE)
            model.appendRow(item)
        page.refresh_content(model)

        # 模拟服务层行为：删除提交后立即广播被删 id 列表。
        def fake_delete(deleted):
            wiring.signal_stickers_deleted.emit([s.id for s in deleted])
            return ()

        with patch(
            "services.sticker_library_viewer_service.delete_stickers",
            side_effect=fake_delete,
        ) as delete_mock, patch(
            "ui.widgets.sticker_list_page.QMessageBox.question",
            return_value=QMessageBox.StandardButton.Yes,
        ) as question_mock:
            page._delete_stickers_for_indexes(
                [model.index(0, 0), model.index(2, 0)]
            )

        delete_mock.assert_called_once()
        # 多选确认框：标题与正文都体现“移动到图库回收站”。
        self.assertEqual(
            "移动到图库回收站", question_mock.call_args[0][1]
        )
        self.assertEqual(
            "确定将选中的 2 张图片移动到图库内的回收站吗？\n"
            "回收站在recycler目录，请在有空时手动清理。",
            question_mock.call_args[0][2],
        )
        self.assertEqual(1, model.rowCount())
        self.assertEqual(
            2, model.index(0, 0).data(ROLE_STICKER_IMAGE).id
        )
        page.close()

    def test_delete_broadcast_with_unknown_ids_is_noop(self):
        page = SearchResultPage(auto_refresh=False)
        model = StickerListModel()
        for sticker_id in (1, 2):
            sticker = make_sticker()
            sticker.id = sticker_id
            item = QStandardItem("")
            item.setData(sticker, ROLE_STICKER_IMAGE)
            model.appendRow(item)
        page.refresh_content(model)

        wiring.signal_stickers_deleted.emit([999])

        self.assertEqual(2, model.rowCount())
        page.close()

    def test_delete_broadcast_prunes_every_open_page(self):
        first_page = SearchResultPage(auto_refresh=False)
        second_page = SearchResultPage(auto_refresh=False)
        first_model = StickerListModel()
        second_model = StickerListModel()
        for sticker_id in (1, 2, 3):
            item = QStandardItem("")
            sticker = make_sticker()
            sticker.id = sticker_id
            item.setData(sticker, ROLE_STICKER_IMAGE)
            first_model.appendRow(item)
        for sticker_id in (2, 3):
            item = QStandardItem("")
            sticker = make_sticker()
            sticker.id = sticker_id
            item.setData(sticker, ROLE_STICKER_IMAGE)
            second_model.appendRow(item)
        first_page.refresh_content(first_model)
        second_page.refresh_content(second_model)

        wiring.signal_stickers_deleted.emit([2, 3])

        self.assertEqual(1, first_model.rowCount())
        self.assertEqual(
            1, first_model.index(0, 0).data(ROLE_STICKER_IMAGE).id
        )
        self.assertEqual(0, second_model.rowCount())
        first_page.close()
        second_page.close()

    def test_similar_images_page_inherits_finite_page(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            settings_manager = create_settings_manager(
                Path(temp_dir) / "settings.toml"
            )
            with patch.object(
                services.global_instances,
                "current_settings_manager",
                settings_manager,
            ):
                page = SimilarImagesPage(auto_refresh=False)

        self.assertIsInstance(page, FiniteStickerCollectionPage)
        self.assertEqual(
            "page_finite_sticker_collection.ui",
            page.UI_FILE_NAME,
        )
        self.assertIsInstance(page.display_size_slider, QSlider)
        page.close()

    def test_similar_images_page_sets_custom_empty_text(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            settings_manager = create_settings_manager(
                Path(temp_dir) / "settings.toml"
            )
            with patch.object(
                services.global_instances,
                "current_settings_manager",
                settings_manager,
            ):
                page = SimilarImagesPage(auto_refresh=False)

        self.assertEqual(
            SimilarImagesPage.EMPTY_STATE_TEXT,
            page.listViewStickerList._empty_text,
        )
        self.assertNotEqual(
            StickerListView.DEFAULT_EMPTY_TEXT,
            SimilarImagesPage.EMPTY_STATE_TEXT,
        )
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

                def list_stickers(self, offset=0, count=100, **kwargs):
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

    def test_insert_toolbar_widgets_around_spacer(self):
        page = FiniteStickerCollectionPage(auto_refresh=False)
        left_button = QToolButton()
        right_button = QToolButton()

        page.insert_toolbar_widget_left_of_spacer(left_button)
        page.insert_toolbar_widget_right_of_spacer(right_button)

        widgets = [
            page.toolbarStickerList.widgetForAction(action)
            for action in page.toolbarStickerList.actions()
        ]
        self.assertEqual(
            [
                page.display_mode_button,
                left_button,
                page.toolbar_spacer,
                right_button,
                page.display_size_slider,
            ],
            widgets,
        )
        self.assertIsInstance(page.toolbar_spacer, ToolbarSpacer)
        self.assertEqual("toolbarSpacer", page.toolbar_spacer.objectName())
        page.close()

    def test_list_shortcuts_dispatch_to_selected_item_handlers(self):
        page = SearchResultPage(auto_refresh=False)
        model = QStandardItemModel()
        for _ in range(2):
            item = QStandardItem("")
            sticker = make_sticker()
            item.setData(sticker, ROLE_STICKER_IMAGE)
            model.appendRow(item)
        page.refresh_content(model)
        view = page.listViewStickerList
        selection_model = view.selectionModel()
        index = model.index(0, 0)
        selection_model.select(
            index,
            QItemSelectionModel.SelectionFlag.ClearAndSelect,
        )
        view.setCurrentIndex(index)
        self._focus_list_view(page)

        with patch.object(page, "_copy_sticker_for_index") as copy_mock, patch.object(
            page, "_save_as_for_indexes"
        ) as save_mock, patch.object(
            page, "_open_image_viewer_for_index"
        ) as open_mock, patch.object(
            page, "_delete_stickers_for_indexes"
        ) as delete_mock:
            QTest.keyClick(view, Qt.Key.Key_C, Qt.KeyboardModifier.ControlModifier)
            QTest.keyClick(view, Qt.Key.Key_S, Qt.KeyboardModifier.ControlModifier)
            QTest.keyClick(view, Qt.Key.Key_Return)
            QTest.keyClick(view, Qt.Key.Key_Delete)

        copy_mock.assert_called_once_with(index)
        save_mock.assert_called_once()
        self.assertEqual([0], [item.row() for item in save_mock.call_args.args[0]])
        open_mock.assert_called_once_with(index)
        delete_mock.assert_called_once()
        self.assertEqual(
            [0], [item.row() for item in delete_mock.call_args.args[0]]
        )
        page.close()

    def test_ctrl_a_selects_all_items(self):
        page = SearchResultPage(auto_refresh=False)
        model = QStandardItemModel()
        for _ in range(3):
            model.appendRow(QStandardItem(""))
        page.refresh_content(model)
        self._focus_list_view(page)

        QTest.keyClick(
            page.listViewStickerList,
            Qt.Key.Key_A,
            Qt.KeyboardModifier.ControlModifier,
        )

        self.assertEqual(3, len(page.listViewStickerList.selectionModel().selectedRows()))
        page.close()

    def test_ctrl_c_uses_file_paths_for_multiple_selection(self):
        page = SearchResultPage(auto_refresh=False)
        model = QStandardItemModel()
        paths = ["C:/library/first.png", "C:/library/second.gif"]
        for path in paths:
            item = QStandardItem("")
            sticker = make_sticker()
            item.setData(sticker, ROLE_STICKER_IMAGE)
            item.setData(path, ROLE_FILE_PATH)
            model.appendRow(item)
        page.refresh_content(model)
        selection_model = page.listViewStickerList.selectionModel()
        selection_model.select(
            model.index(0, 0),
            QItemSelectionModel.SelectionFlag.ClearAndSelect,
        )
        selection_model.select(
            model.index(1, 0),
            QItemSelectionModel.SelectionFlag.Select,
        )
        self._focus_list_view(page)

        with patch(
            "services.image_clipboard_service.copy_file_paths_to_clipboard"
        ) as copy_files:
            QTest.keyClick(
                page.listViewStickerList,
                Qt.Key.Key_C,
                Qt.KeyboardModifier.ControlModifier,
            )

        copy_files.assert_called_once_with(paths)
        page.close()

    def test_ctrl_wheel_changes_size_and_syncs_slider(self):
        page = FiniteStickerCollectionPage(auto_refresh=False)
        view = page.listViewStickerList
        signal_spy = QSignalSpy(view.display_size_changed)
        initial_size = view.item_size()

        self._send_wheel(
            view,
            120,
            Qt.KeyboardModifier.ControlModifier,
        )

        self.assertEqual(initial_size + 8, view.item_size())
        self.assertEqual(view.item_size(), page.display_size_slider.value())
        self.assertEqual(1, len(signal_spy))

        self._send_wheel(view, -120)

        self.assertEqual(initial_size + 8, view.item_size())
        page.close()

    def test_f5_refreshes_only_infinite_page(self):
        infinite_page = InfiniteStickerCollectionPage(auto_refresh=False)
        finite_page = FiniteStickerCollectionPage(auto_refresh=False)
        infinite_spy = QSignalSpy(infinite_page.signal_refresh_content)
        finite_spy = QSignalSpy(finite_page.signal_refresh_content)
        self._focus_list_view(infinite_page)

        QTest.keyClick(infinite_page.listViewStickerList, Qt.Key.Key_F5)

        self.assertEqual(1, len(infinite_spy))

        self._focus_list_view(finite_page)
        QTest.keyClick(finite_page.listViewStickerList, Qt.Key.Key_F5)
        self.assertEqual(0, len(finite_spy))
        infinite_page.close()
        finite_page.close()

    def test_insert_toolbar_actions_around_spacer(self):
        page = FiniteStickerCollectionPage(auto_refresh=False)
        left_action = QAction("left", page)
        right_action = QAction("right", page)

        page.insert_toolbar_action_left_of_spacer(left_action)
        page.insert_toolbar_action_right_of_spacer(right_action)

        actions = page.toolbarStickerList.actions()
        widgets = [
            page.toolbarStickerList.widgetForAction(action)
            for action in actions
        ]
        self.assertIs(left_action, actions[1])
        self.assertIs(page.toolbar_spacer, widgets[2])
        self.assertIs(right_action, actions[3])
        page.close()

    def test_infinite_page_toolbar_control_order(self):
        page = InfiniteStickerCollectionPage(auto_refresh=False)
        actions = page.toolbarStickerList.actions()
        widgets = [
            page.toolbarStickerList.widgetForAction(action) for action in actions
        ]

        # [显示模式][刷新][排序] | spacer | [滑块]
        self.assertEqual(
            [
                page.display_mode_button,
                page.sort_button,
                page.toolbar_spacer,
                page.display_size_slider,
            ],
            [widgets[0], widgets[2], widgets[3], widgets[4]],
        )
        self.assertIs(page.refresh_action, actions[1])
        self.assertFalse(page.refresh_action.icon().isNull())
        self.assertEqual("刷新图库", page.refresh_action.toolTip())

        spy = QSignalSpy(page.signal_refresh_content)
        page.refresh_action.trigger()
        self.assertEqual(1, len(spy))
        page.close()

    def test_load_library_page_defaults_to_newest_import_date(self):
        class FakeDB:
            def __init__(self):
                self.calls = []

            def list_stickers(self, **kwargs):
                self.calls.append(kwargs)
                return []

        fake_db = FakeDB()
        with patch("services.global_instances.current_library_db", fake_db):
            load_library_page(12, 34)

        self.assertEqual(
            [
                {
                    "offset": 12,
                    "count": 34,
                    "order_by": "imported_at",
                    "descending": True,
                }
            ],
            fake_db.calls,
        )

    def test_infinite_page_sort_button_defaults_to_newest_import_date(self):
        page = InfiniteStickerCollectionPage(auto_refresh=False)
        button = page.sort_button
        menu = button.menu()

        self.assertIsInstance(button, QToolButton)
        self.assertIsNotNone(menu)
        self.assertEqual(
            [
                ("imported_at", True),
                ("imported_at", False),
                ("file_size", False),
                ("file_size", True),
            ],
            [action.data() for action in menu.actions()],
        )
        checked = [action for action in menu.actions() if action.isChecked()]
        self.assertEqual(1, len(checked))
        self.assertEqual("导入日期（新到旧）", checked[0].text())
        self.assertEqual(("imported_at", True), checked[0].data())
        self.assertEqual("图片排序方式", button.toolTip())
        page.close()

    def test_infinite_page_reloads_first_page_when_sort_changes(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            image_path = Path(temp_dir) / "stored-hash.png"
            image = QImage(2, 2, QImage.Format.Format_ARGB32)
            image.fill(0xFFFFFFFF)
            self.assertTrue(image.save(str(image_path)))

            class FakePagedDB:
                def __init__(self, rows):
                    self.rows = rows
                    self.calls = []

                def list_stickers(
                    self,
                    offset=0,
                    count=100,
                    order_by="imported_at",
                    descending=True,
                ):
                    self.calls.append((offset, order_by, descending))
                    return self.rows[offset:offset + count]

            page_size = InfiniteStickerCollectionPage.PAGE_SIZE
            stickers = [make_sticker() for _ in range(page_size + 5)]
            fake_db = FakePagedDB(stickers)
            with patch(
                "services.global_instances.current_library_db",
                fake_db,
            ), patch(
                "services.global_instances.current_blob_storage",
                FakeBlobStorage(image_path),
            ):
                page = InfiniteStickerCollectionPage(auto_refresh=False)
                page.resize(400, 300)
                page.show()
                QApplication.processEvents()
                self.assertEqual(
                    [(0, "imported_at", True)],
                    fake_db.calls,
                )

                page.sort_button.menu().actions()[2].trigger()
                QApplication.processEvents()

                self.assertEqual(
                    (0, "file_size", False),
                    fake_db.calls[-1],
                )
                self.assertEqual(
                    page_size,
                    page.listViewStickerList.model().rowCount(),
                )
                self.assertEqual(page_size, page._offset)
                page.close()

    def test_display_size_slider_adjusts_view_grid(self):
        page = FiniteStickerCollectionPage(auto_refresh=False)
        slider = page.display_size_slider

        self.assertIsInstance(slider, QSlider)
        self.assertEqual(120, slider.value())
        self.assertEqual(QSize(120, 120), page.listViewStickerList.gridSize())

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

                def list_stickers(self, offset=0, count=100, **kwargs):
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
        model = StickerListModel()
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
        model = StickerListModel()
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

    def test_thumbnail_ready_with_plain_model_is_ignored(self):
        # plain model（debug 服务等）没有 row_for_hash：路由优雅退化，不炸。
        provider = ThumbnailProvider()
        view = StickerListView(thumbnail_provider=provider)
        model = QStandardItemModel()
        item = QStandardItem("")
        item.setData(BlobFileEntity("plain-hash", ".png"), ROLE_BLOB_ENTITY)
        model.appendRow(item)
        view.setModel(model)

        with patch.object(view, "_update_item") as update_item:
            provider.thumbnail_ready.emit(
                "plain-hash",
                QImage(1, 1, QImage.Format.Format_RGB32),
            )

        update_item.assert_not_called()
        view.close()

    def test_thumbnail_ready_updates_row_appended_after_set_model(self):
        provider = ThumbnailProvider()
        view = StickerListView(thumbnail_provider=provider)
        model = StickerListModel()
        first = QStandardItem("")
        first.setData(BlobFileEntity("first-hash", ".png"), ROLE_BLOB_ENTITY)
        model.appendRow(first)
        view.setModel(model)

        second = QStandardItem("")
        second.setData(BlobFileEntity("second-hash", ".png"), ROLE_BLOB_ENTITY)
        model.appendRow(second)

        with patch.object(view, "_update_item") as update_item:
            provider.thumbnail_ready.emit(
                "second-hash",
                QImage(1, 1, QImage.Format.Format_RGB32),
            )

        update_item.assert_called_once()
        updated_index = update_item.call_args.args[0]
        self.assertEqual(
            "second-hash",
            updated_index.data(ROLE_BLOB_ENTITY).hash,
        )
        view.close()

    def test_thumbnail_ready_ignores_row_outside_viewport(self):
        provider = ThumbnailProvider()
        view = StickerListView(thumbnail_provider=provider)
        model = StickerListModel()
        for row in range(20):
            item = QStandardItem("")
            item.setData(
                BlobFileEntity(f"hash-{row}", ".png"),
                ROLE_BLOB_ENTITY,
            )
            model.appendRow(item)
        view.setModel(model)
        view.resize(100, 80)
        view.show()
        QApplication.processEvents()

        with patch.object(view, "_update_item") as update_item:
            provider.thumbnail_ready.emit(
                "hash-15",
                QImage(1, 1, QImage.Format.Format_RGB32),
            )

        update_item.assert_not_called()
        view.close()

    def test_thumbnail_ready_uses_shifted_row_after_removal(self):
        provider = ThumbnailProvider()
        view = StickerListView(thumbnail_provider=provider)
        model = StickerListModel()
        for file_hash in ("hash-a", "hash-b", "hash-c"):
            item = QStandardItem("")
            item.setData(
                BlobFileEntity(file_hash, ".png"),
                ROLE_BLOB_ENTITY,
            )
            model.appendRow(item)
        view.setModel(model)

        model.removeRow(1)

        with patch.object(view, "_update_item") as update_item:
            provider.thumbnail_ready.emit(
                "hash-b",
                QImage(1, 1, QImage.Format.Format_RGB32),
            )
            provider.thumbnail_ready.emit(
                "hash-c",
                QImage(1, 1, QImage.Format.Format_RGB32),
            )

        update_item.assert_called_once()
        updated_index = update_item.call_args.args[0]
        self.assertEqual("hash-c", updated_index.data(ROLE_BLOB_ENTITY).hash)
        self.assertEqual(1, updated_index.row())
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
                anim_as_static_image=False,
            )
            page.close()

    def test_copy_static_uses_current_item_file_and_original_name(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            image_path = Path(temp_dir) / "stored-hash.png"
            image = QImage(2, 2, QImage.Format.Format_ARGB32)
            image.fill(0xFFFFFFFF)
            self.assertTrue(image.save(str(image_path)))

            sticker = make_sticker()
            sticker.original_file_name = "动态表情.gif"
            sticker.extension = ".gif"
            with patch(
                "services.global_instances.current_blob_storage",
                FakeBlobStorage(image_path),
            ):
                model = build_sticker_model([sticker])

            page = FiniteStickerCollectionPage(auto_refresh=False)
            page.refresh_content(model)
            with patch(
                "services.image_clipboard_service.copy_image_to_clipboard"
            ) as copy_image:
                page._copy_sticker_for_index(
                    model.index(0, 0),
                    anim_as_static_image=True,
                )

            copy_image.assert_called_once_with(
                str(image_path),
                "动态表情.gif",
                anim_as_static_image=True,
            )
            page.close()

    # ==================== 显示模式切换（视图层） ====================

    def test_set_display_mode_switches_view_and_grid(self):
        view = StickerListView()
        model = QStandardItemModel()
        model.appendRow(QStandardItem(""))
        view.setModel(model)
        view.resize(400, 240)
        view.show()
        QApplication.processEvents()
        expected_width = view.viewport().width()

        view.set_display_mode(commons.constants.LIST_DISPLAY_MODE_LIST)

        self.assertEqual(QListView.ViewMode.ListMode, view.viewMode())
        self.assertEqual(
            QSize(expected_width, StickerListView.DETAIL_ROW_HEIGHT_DEFAULT),
            view.gridSize(),
        )
        delegate = view.itemDelegate()
        self.assertEqual(
            QSize(StickerListView.DETAIL_ROW_HEIGHT_DEFAULT * 4,
                  StickerListView.DETAIL_ROW_HEIGHT_DEFAULT),
            delegate.sizeHint(QStyleOptionViewItem(), model.index(0, 0)),
        )

        view.set_display_mode(commons.constants.LIST_DISPLAY_MODE_ICON)

        self.assertEqual(QListView.ViewMode.IconMode, view.viewMode())
        self.assertEqual(QSize(120, 120), view.gridSize())
        self.assertEqual(
            QSize(120, 120),
            delegate.sizeHint(QStyleOptionViewItem(), model.index(0, 0)),
        )
        view.close()

    def test_display_sizes_are_remembered_per_mode(self):
        view = StickerListView()
        view.resize(400, 240)
        view.show()
        QApplication.processEvents()

        view.set_display_size(96)
        view.set_display_mode(commons.constants.LIST_DISPLAY_MODE_LIST)
        view.set_display_size(100)

        self.assertEqual(100, view.item_size())
        self.assertEqual(
            QSize(view.viewport().width(), 100),
            view.gridSize(),
        )

        view.set_display_mode(commons.constants.LIST_DISPLAY_MODE_ICON)

        self.assertEqual(96, view.item_size())
        self.assertEqual(QSize(96, 96), view.gridSize())

        view.set_display_mode(commons.constants.LIST_DISPLAY_MODE_LIST)

        self.assertEqual(100, view.item_size())
        view.close()

    def test_detail_row_height_is_clamped_to_slider_range(self):
        view = StickerListView()
        view.set_display_mode(commons.constants.LIST_DISPLAY_MODE_LIST)

        view.set_display_size(10)
        self.assertEqual(StickerListView.DETAIL_ROW_HEIGHT_MIN, view.item_size())
        view.set_display_size(5000)
        self.assertEqual(StickerListView.DETAIL_ROW_HEIGHT_MAX, view.item_size())
        view.close()

    def test_resize_updates_detail_grid_width(self):
        view = StickerListView()
        model = QStandardItemModel()
        for _ in range(30):
            model.appendRow(QStandardItem(""))
        view.setModel(model)
        view.set_display_mode(commons.constants.LIST_DISPLAY_MODE_LIST)
        view.resize(400, 240)
        view.show()
        QApplication.processEvents()
        first_width = view.gridSize().width()

        view.resize(650, 240)
        QApplication.processEvents()

        self.assertNotEqual(first_width, view.gridSize().width())
        self.assertEqual(view.viewport().width(), view.gridSize().width())
        view.close()

    def test_item_size_reflects_current_mode(self):
        view = StickerListView()

        self.assertEqual(120, view.item_size())
        view.set_display_mode(commons.constants.LIST_DISPLAY_MODE_LIST)
        self.assertEqual(
            StickerListView.DETAIL_ROW_HEIGHT_DEFAULT,
            view.item_size(),
        )
        view.set_display_mode(commons.constants.LIST_DISPLAY_MODE_ICON)
        self.assertEqual(120, view.item_size())
        view.close()

    def test_rebuilt_delegate_inherits_current_mode_and_size(self):
        view = StickerListView(thumbnail_provider=ThumbnailProvider())
        view.set_display_size(120)
        view.set_display_mode(commons.constants.LIST_DISPLAY_MODE_LIST)
        view.set_thumbnail_provider(ThumbnailProvider())

        delegate = view.itemDelegate()
        self.assertIsInstance(delegate, StickerItemDelegate)
        self.assertEqual(
            commons.constants.LIST_DISPLAY_MODE_LIST,
            delegate._display_mode,
        )
        self.assertEqual(72, delegate._item_size)
        view.close()

    def test_toolbar_has_display_mode_menu_button(self):
        page = FiniteStickerCollectionPage(auto_refresh=False)
        toggle = page.display_mode_button
        widgets = [
            page.toolbarStickerList.widgetForAction(action)
            for action in page.toolbarStickerList.actions()
        ]

        self.assertIsInstance(toggle, QToolButton)
        self.assertIn(toggle, widgets)
        menu = toggle.menu()
        self.assertIsNotNone(menu)
        self.assertFalse(toggle.icon().isNull())
        # 位于工具栏左侧：在弹性 spacer 之前。
        self.assertLess(
            widgets.index(toggle),
            widgets.index(page.toolbar_spacer),
        )
        self.assertLess(
            widgets.index(toggle),
            widgets.index(page.display_size_slider),
        )

        self.assertEqual(
            [
                commons.constants.LIST_DISPLAY_MODE_ICON,
                commons.constants.LIST_DISPLAY_MODE_LIST,
            ],
            [action.data() for action in menu.actions()],
        )
        checked = [action for action in menu.actions() if action.isChecked()]
        self.assertEqual(1, len(checked))
        self.assertEqual("图标", checked[0].text())
        self.assertEqual("切换图标/详细信息显示", toggle.toolTip())
        page.close()

    def test_display_mode_menu_updates_view_and_slider(self):
        page = FiniteStickerCollectionPage(auto_refresh=False)
        view = page.listViewStickerList
        toggle = page.display_mode_button
        slider = page.display_size_slider
        actions_by_text = {
            action.text(): action
            for action in toggle.menu().actions()
        }

        actions_by_text["详细信息"].trigger()

        self.assertEqual(
            commons.constants.LIST_DISPLAY_MODE_LIST,
            view._display_mode,
        )
        self.assertEqual(QListView.ViewMode.ListMode, view.viewMode())
        self.assertEqual((48, StickerListView.DETAIL_ROW_HEIGHT_MAX), (slider.minimum(), slider.maximum()))
        self.assertEqual(StickerListView.DETAIL_ROW_HEIGHT_DEFAULT, slider.value())

        actions_by_text["图标"].trigger()

        self.assertEqual(QListView.ViewMode.IconMode, view.viewMode())
        self.assertEqual(
            (StickerListView.DISPLAY_SIZE_MIN, StickerListView.ICON_DISPLAY_SIZE_MAX),
            (slider.minimum(), slider.maximum()),
        )
        self.assertEqual(120, slider.value())
        self.assertEqual(QSize(120, 120), view.gridSize())

        checked = [
            action for action in toggle.menu().actions() if action.isChecked()
        ]
        self.assertEqual(["图标"], [action.text() for action in checked])
        page.close()

    # ==================== 详细模式绘制（像素采样） ====================

    @staticmethod
    def _make_tag(name, *, tag_id=1, order=0, color="#2196F3"):
        tag = Tag()
        tag.id = tag_id
        tag.name = name
        tag.order = order
        tag.color_rgb = color
        return tag

    def _paint_item_in_rect(
        self,
        item: QStandardItem,
        rect: QRect,
        thumbnail_provider=None,
        display_mode: int | None = None,
    ) -> QImage:
        model = QStandardItemModel()
        model.appendRow(item)
        canvas = QImage(rect.width(), rect.height(), QImage.Format.Format_ARGB32)
        canvas.fill(0xFF00FF00)
        painter = QPainter(canvas)
        try:
            option = QStyleOptionViewItem()
            option.rect = rect
            option.state = QStyle.StateFlag.State_Enabled
            delegate = StickerItemDelegate(
                thumbnail_provider=thumbnail_provider
            )
            if display_mode is not None:
                delegate.set_display_mode(display_mode)
            delegate.set_item_size(rect.height())
            delegate.paint(painter, option, model.index(0, 0))
        finally:
            painter.end()
        return canvas

    def _paint_detail_item(
        self,
        item: QStandardItem,
        *,
        width: int = 400,
        height: int = 72,
        thumbnail_provider=None,
    ) -> QImage:
        return self._paint_item_in_rect(
            item,
            QRect(0, 0, width, height),
            thumbnail_provider=thumbnail_provider,
            display_mode=commons.constants.LIST_DISPLAY_MODE_LIST,
        )

    @staticmethod
    def _white_pixel_bounds(image: QImage):
        min_x = min_y = max_x = max_y = None
        for y in range(image.height()):
            for x in range(image.width()):
                color = image.pixelColor(x, y)
                if (
                    color.red() > 230
                    and color.green() > 230
                    and color.blue() > 230
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
    def _gray_pixel_count(image: QImage, bounds) -> int:
        min_x, min_y, max_x, max_y = bounds
        count = 0
        for y in range(min_y, max_y + 1):
            for x in range(min_x, max_x + 1):
                color = image.pixelColor(x, y)
                if (
                    130 < color.red() < 190
                    and abs(color.red() - color.green()) < 12
                    and abs(color.green() - color.blue()) < 12
                ):
                    count += 1
        return count

    def test_detail_paint_draws_thumbnail_on_left(self):
        provider = FakeThumbnailProvider()
        item = QStandardItem("")
        item.setData(BlobFileEntity("detail-hash", ".png"), ROLE_BLOB_ENTITY)
        canvas = self._paint_detail_item(item, thumbnail_provider=provider)

        bounds = self._white_pixel_bounds(canvas)
        self.assertIsNotNone(bounds)
        min_x, _, max_x, _ = bounds
        self.assertLessEqual(max_x, canvas.width() // 3)
        self.assertGreater(min_x, 0)

    def test_detail_paint_draws_filename_and_tags_text(self):
        sticker = make_sticker()
        sticker.original_file_name = "detail_name.png"
        sticker.tags = [
            self._make_tag("城市", tag_id=1, order=1),
            self._make_tag("建筑", tag_id=2, order=2),
        ]
        item = QStandardItem("")
        item.setData(sticker, ROLE_STICKER_IMAGE)
        canvas = self._paint_detail_item(item)

        text_zone = (90, 0, canvas.width() - 1, canvas.height() - 1)
        self.assertGreater(self._black_pixel_count(canvas, text_zone), 0)

    def test_detail_paint_draws_colored_tag_chips(self):
        sticker = make_sticker()
        sticker.tags = [self._make_tag("红色标签", color="#FF0000")]
        item = QStandardItem("")
        item.setData(sticker, ROLE_STICKER_IMAGE)
        canvas = self._paint_detail_item(item)

        red_count = 0
        for y in range(canvas.height()):
            for x in range(canvas.width() // 2, canvas.width()):
                color = canvas.pixelColor(x, y)
                if color.red() > 200 and color.green() < 80 and color.blue() < 80:
                    red_count += 1
        self.assertGreater(red_count, 0)

    def test_detail_paint_keeps_gif_badge_on_small_thumbnail(self):
        provider = FakeThumbnailProvider()
        item = QStandardItem("")
        item.setData(BlobFileEntity("detail-gif-hash", ".gif"), ROLE_BLOB_ENTITY)
        canvas = self._paint_detail_item(item, thumbnail_provider=provider)

        bounds = self._pink_pixel_bounds(canvas)
        self.assertIsNotNone(bounds)
        min_x, min_y, max_x, max_y = bounds
        self.assertLess(max_x, canvas.width() // 3)
        self.assertLess(max_y, canvas.height() // 2)
        self.assertLess(min_x, 40)
        self.assertLess(min_y, 20)

    def test_detail_paint_without_dto_falls_back_to_display_role(self):
        item = QStandardItem("fallback_name.png")
        canvas = self._paint_detail_item(item)

        text_zone = (100, 0, canvas.width() - 1, canvas.height() - 1)
        self.assertGreater(self._black_pixel_count(canvas, text_zone), 0)

    def test_empty_tags_leave_blank_region(self):
        sticker = make_sticker()
        sticker.original_file_name = "a.png"
        sticker.tags = []
        item = QStandardItem("")
        item.setData(sticker, ROLE_STICKER_IMAGE)
        canvas = self._paint_detail_item(item)

        blank_zone = (int(canvas.width() * 0.55), 0, canvas.width() - 1,
                      canvas.height() - 1)
        self.assertEqual(0, self._black_pixel_count(canvas, blank_zone))

    def test_detail_paint_overflow_tags_show_plus_n_badge(self):
        sticker = make_sticker()
        sticker.tags = [
            self._make_tag(f"很长很长的标签名{i}", tag_id=i, order=i)
            for i in range(12)
        ]
        item = QStandardItem("")
        item.setData(sticker, ROLE_STICKER_IMAGE)
        canvas = self._paint_detail_item(item, width=360)

        right_edge = (
            canvas.width() - 60, 0, canvas.width() - 1, canvas.height() - 1
        )
        gray_count = self._gray_pixel_count(canvas, right_edge)
        self.assertGreater(gray_count, 0)
        badge_rows = {
            y
            for y in range(canvas.height())
            if self._gray_pixel_count(
                canvas,
                (canvas.width() - 60, y, canvas.width() - 1, y),
            ) > 0
        }
        # 徽标垂直居中，不贴行顶。
        self.assertTrue(all(row > canvas.height() * 0.2 for row in badge_rows))

    def test_detail_filename_font_not_polluted_by_badges(self):
        # GIF 角标会改写画笔字体；文件名必须始终按 option.font 渲染。
        name = "font_check.png"
        counts = []
        for extension in (".png", ".gif"):
            provider = FakeThumbnailProvider()
            item = QStandardItem("")
            item.setData(
                BlobFileEntity(f"hash{extension}", extension),
                ROLE_BLOB_ENTITY,
            )
            sticker = make_sticker()
            sticker.original_file_name = name
            item.setData(sticker, ROLE_STICKER_IMAGE)
            canvas = self._paint_detail_item(
                item, thumbnail_provider=provider
            )
            zone = (90, 0, int(canvas.width() * 0.45), canvas.height() - 1)
            counts.append(self._black_pixel_count(canvas, zone))

        self.assertGreater(counts[0], 0)
        self.assertEqual(counts[0], counts[1])

    def test_detail_chip_border_renders_symmetric_band(self):
        sticker = make_sticker()
        sticker.tags = [self._make_tag("城市"), self._make_tag("建筑")]
        item = QStandardItem("")
        item.setData(sticker, ROLE_STICKER_IMAGE)
        width, height = 400, 72
        canvas = self._paint_detail_item(item, width=width, height=height)

        # 与 _paint_detail 的布局推导保持一致，得到标签区与圆角片矩形。
        text_left = 8 + (height - 16 - 1) + 1 + 12
        text_width = (width - 1) - 8 - text_left + 1
        name_limit = int(text_width * 0.35)
        tags_left = text_left + name_limit - 1 + 1 + 16
        tags_rect = QRect(tags_left, 0, width - 8 - tags_left + 1, height)
        metrics = QFontMetrics(QApplication.instance().font())
        layout = layout_tag_chips(
            tags_rect, sticker.tags, metrics
        )
        self.assertEqual(2, len(layout.chips))

        def is_border_tinted(color):
            # 开启抗锯齿后描边与绿底/淡蓝填充混合，纯色判定失效；
            # 背景绿、内部填充与黑色文字的蓝分量都很低，
            # 只有描边混色能带来高蓝通道值。
            return color.blue() >= 100

        runs = []
        start = None
        for x in range(canvas.width()):
            has_border = any(
                is_border_tinted(canvas.pixelColor(x, y))
                for y in range(canvas.height())
            )
            if has_border:
                if start is None:
                    start = x
            elif start is not None:
                runs.append((start, x - 1))
                start = None
        if start is not None:
            runs.append((start, canvas.width() - 1))

        self.assertEqual(len(layout.chips), len(runs))
        for (chip_rect, _), (run_start, run_end) in zip(layout.chips, runs):
            # 抗锯齿下描边以路径为中心渲染，
            # 可见带相对逻辑矩形允许 ±1px 的混合渗出。
            self.assertLessEqual(chip_rect.left() - 1, run_start)
            self.assertGreaterEqual(chip_rect.left(), run_start)
            self.assertLessEqual(chip_rect.right(), run_end)
            self.assertGreaterEqual(chip_rect.right() + 1, run_end)
        widths = {run_end - run_start + 1 for run_start, run_end in runs}
        # 各片可见带宽度一致（对称渲染），不允许某一片被裁剪或加粗。
        self.assertEqual(1, len(widths))
        for (_, prev_end), (next_start, _) in zip(runs, runs[1:]):
            gap = next_start - prev_end - 1
            # 混合渗出使相邻可见带的间距相对 TAG_CHIP_GAP 有 ±2px 容差。
            self.assertGreaterEqual(gap, TAG_CHIP_GAP - 2)
            self.assertLessEqual(gap, TAG_CHIP_GAP)

    def test_rounded_corner_paint_enables_antialiasing(self):
        """角标与标签片的圆角对称性依赖抗锯齿：绘制期间必须开启。"""
        hints_seen = []
        original = QPainter.drawRoundedRect

        def spy(painter, *args, **kwargs):
            hints_seen.append(painter.renderHints())
            return original(painter, *args, **kwargs)

        icon_item = self._make_icon_item(0.92)
        sticker = make_sticker()
        sticker.tags = [self._make_tag("城市"), self._make_tag("建筑")]
        detail_item = QStandardItem("")
        detail_item.setData(sticker, ROLE_STICKER_IMAGE)

        with patch.object(QPainter, "drawRoundedRect", spy):
            self._paint_item(icon_item, 160)
            self._paint_detail_item(detail_item)

        self.assertTrue(hints_seen)
        self.assertTrue(
            all(
                hint & QPainter.RenderHint.Antialiasing
                for hint in hints_seen
            )
        )

    # ==================== 标签布局纯函数 ====================

    def test_layout_tag_chips_preserves_input_order(self):
        metrics = QFontMetrics(QApplication.instance().font())
        tags = [
            self._make_tag("甲", tag_id=5, order=1),
            self._make_tag("乙", tag_id=9, order=1),
            self._make_tag("丙", tag_id=1, order=2),
        ]

        layout = layout_tag_chips(QRect(0, 0, 600, 72), tags, metrics)

        self.assertEqual(["甲", "乙", "丙"], [label for _, label in layout.chips])
        lefts = [chip.left() for chip, _ in layout.chips]
        self.assertEqual(sorted(lefts), lefts)
        self.assertEqual(0, layout.hidden_count)

    def test_layout_tag_chips_folds_overflow_with_plus_n(self):
        metrics = QFontMetrics(QApplication.instance().font())
        tags = [
            self._make_tag(f"长标签名称{i}", tag_id=i, order=i)
            for i in range(10)
        ]
        rect = QRect(0, 0, 260, 72)

        layout = layout_tag_chips(rect, tags, metrics)

        self.assertGreater(layout.hidden_count, 0)
        self.assertLess(len(layout.chips), len(tags))
        badge_width = (
            metrics.horizontalAdvance(f"+{layout.hidden_count}")
            + 2 * MORE_BADGE_PAD_X
        )
        for chip_rect, _ in layout.chips:
            self.assertTrue(rect.contains(chip_rect))
            self.assertLessEqual(
                chip_rect.right() + 1 + TAG_CHIP_GAP,
                rect.right() + 1 - badge_width,
            )

    def test_layout_tag_chips_single_oversized_tag_is_folded(self):
        metrics = QFontMetrics(QApplication.instance().font())
        tags = [self._make_tag("一个特别特别特别特别长的标签名")]

        layout = layout_tag_chips(QRect(0, 0, 60, 72), tags, metrics)

        self.assertEqual([], layout.chips)
        self.assertEqual(1, layout.hidden_count)

    # ==================== 链路回归 ====================

    def test_batch_tag_update_repaints_detail_rows(self):
        page = SearchResultPage(auto_refresh=False)
        model = StickerListModel()
        current_sticker = make_sticker()
        item = QStandardItem("")
        item.setData(current_sticker, ROLE_STICKER_IMAGE)
        model.appendRow(item)
        page.refresh_content(model)
        detail_action = next(
            action
            for action in page.display_mode_button.menu().actions()
            if action.text() == "详细信息"
        )
        detail_action.trigger()

        spy = QSignalSpy(model.dataChanged)
        updated_sticker = make_sticker()
        updated_sticker.tags = [self._make_tag("批量新标签", tag_id=77)]
        page._update_sticker_dtos([updated_sticker])

        self.assertGreaterEqual(len(spy), 1)
        self.assertEqual([ROLE_STICKER_IMAGE], list(spy[0][2]))
        stored_sticker = model.index(0, 0).data(ROLE_STICKER_IMAGE)
        self.assertEqual(["批量新标签"], [tag.name for tag in stored_sticker.tags])
        page.close()

    def test_mode_switch_preserves_load_more_and_thumbnail_routing(self):
        provider = ThumbnailProvider()
        view = StickerListView(thumbnail_provider=provider)
        model = StickerListModel()
        for row in range(200):
            item = QStandardItem("")
            item.setData(BlobFileEntity(f"hash-{row}", ".png"), ROLE_BLOB_ENTITY)
            model.appendRow(item)
        view.setModel(model)
        view.resize(320, 240)
        view.show()
        QApplication.processEvents()

        for mode in (
            commons.constants.LIST_DISPLAY_MODE_LIST,
            commons.constants.LIST_DISPLAY_MODE_ICON,
            commons.constants.LIST_DISPLAY_MODE_LIST,
        ):
            view.set_display_mode(mode)

        spy = QSignalSpy(view.load_more_requested)
        scrollbar = view.verticalScrollBar()
        self.assertGreater(scrollbar.maximum(), 0)
        scrollbar.setValue(scrollbar.maximum())
        QApplication.processEvents()
        self.assertGreater(len(spy), 0)

        scrollbar.setValue(0)
        QApplication.processEvents()
        with patch.object(view, "_update_item") as update_item:
            provider.thumbnail_ready.emit(
                "hash-1",
                QImage(1, 1, QImage.Format.Format_RGB32),
            )
        update_item.assert_called_once()
        self.assertEqual("hash-1",
                         update_item.call_args.args[0].data(ROLE_BLOB_ENTITY).hash)
        view.close()

    def test_viewer_close_emits_data_changed(self):
        page = SearchResultPage(auto_refresh=False)
        model = QStandardItemModel()
        item = QStandardItem("")
        item.setData(make_sticker(), ROLE_STICKER_IMAGE)
        item.setData("stored.png", ROLE_FILE_PATH)
        model.appendRow(item)
        page.refresh_content(model)
        index = model.index(0, 0)

        class FakeDialog:
            def __init__(self, parent=None):
                pass

            def load_image(self, *_args, **_kwargs):
                pass

            def exec(self):
                return 0

        with patch(
            "ui.widgets.sticker_list_page.ImageViewerDialog", FakeDialog
        ):
            spy = QSignalSpy(model.dataChanged)
            page._open_image_viewer_for_index(index)

        self.assertEqual(1, len(spy))
        self.assertEqual(index.row(), spy[0][0].row())
        self.assertEqual([ROLE_STICKER_IMAGE], list(spy[0][2]))
        page.close()

    def test_viewer_close_after_row_deletion_skips_data_changed(self):
        page = SearchResultPage(auto_refresh=False)
        model = QStandardItemModel()
        item = QStandardItem("")
        item.setData(make_sticker(), ROLE_STICKER_IMAGE)
        item.setData("stored.png", ROLE_FILE_PATH)
        model.appendRow(item)
        page.refresh_content(model)
        index = model.index(0, 0)

        class DeletingDialog:
            def __init__(self, parent=None):
                pass

            def load_image(self, *_args, **_kwargs):
                pass

            def exec(self):
                model.removeRow(0)
                return 0

        with patch(
            "ui.widgets.sticker_list_page.ImageViewerDialog", DeletingDialog
        ):
            spy = QSignalSpy(model.dataChanged)
            page._open_image_viewer_for_index(index)

        self.assertEqual(0, len(spy))
        page.close()


class StickerListViewEmptyStateTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = QApplication.instance() or QApplication([])

    @staticmethod
    def _grab_image(view):
        return view.grab().toImage()

    def test_empty_state_flag_follows_model_row_count(self):
        view = StickerListView()
        self.assertTrue(view._empty_state_active)

        model = QStandardItemModel()
        model.appendRow(QStandardItem(""))
        view.setModel(model)
        self.assertFalse(view._empty_state_active)

        model.removeRow(0)
        self.assertTrue(view._empty_state_active)
        view.close()

    def test_empty_view_paints_placeholder_and_populated_does_not(self):
        view = StickerListView()
        view.resize(320, 240)
        view.show()
        QApplication.processEvents()
        empty_image = self._grab_image(view)

        model = QStandardItemModel()
        model.appendRow(QStandardItem("x"))
        view.setModel(model)
        QApplication.processEvents()
        populated_image = self._grab_image(view)

        self.assertEqual(StickerListView.DEFAULT_EMPTY_TEXT, view._empty_text)
        self.assertNotEqual(empty_image, populated_image)
        view.close()

    def test_placeholder_is_restored_after_row_removal(self):
        view = StickerListView()
        view.resize(320, 240)
        view.show()
        QApplication.processEvents()
        baseline = self._grab_image(view)

        model = QStandardItemModel()
        model.appendRow(QStandardItem("x"))
        view.setModel(model)
        model.removeRow(0)
        QApplication.processEvents()

        self.assertTrue(view._empty_state_active)
        self.assertEqual(baseline, self._grab_image(view))
        view.close()

    def test_set_empty_text_customizes_placeholder(self):
        view = StickerListView()
        view.resize(320, 240)
        view.show()
        QApplication.processEvents()
        default_image = self._grab_image(view)

        view.set_empty_text("这里什么都没有")
        QApplication.processEvents()

        self.assertEqual("这里什么都没有", view._empty_text)
        self.assertNotEqual(default_image, self._grab_image(view))
        view.close()


if __name__ == "__main__":
    unittest.main()
