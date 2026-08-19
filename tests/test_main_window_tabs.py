import os
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock, patch

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PyQt6 import sip
from PyQt6.QtCore import QCoreApplication, QEvent
from PyQt6.QtWidgets import QApplication, QTabBar, QWidget

import apppath
import services.global_instances
import services.sticker_library_viewer_service as library_viewer_service
from commons.signal_objects import MainWindowNewTabRequest
from services.settings import create_settings_manager
import ui.page_advanced_search_result as advanced_search_result_page
import ui.page_infinite_sticker_collection as infinite_sticker_collection_page
import ui.page_search_result as search_result_page
import ui.page_similar_images as similar_images_page
from ui.main_window import MainWindow


class MainWindowTabTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = QApplication.instance() or QApplication([])
        apppath.app_path = Path(__file__).resolve().parents[1] / "src"

    def setUp(self):
        self.previous_main_window = services.global_instances.main_window
        self.temporary_directory = tempfile.TemporaryDirectory()
        settings_manager = create_settings_manager(
            Path(self.temporary_directory.name) / "settings.toml"
        )
        with patch("ui.main_window.ImageImportService"), patch(
            "ui.main_window.services.export_library.LibraryExportService"
        ), patch.object(MainWindow, "debug_start_test_view"):
            self.window = MainWindow(settings_manager=settings_manager)

    def tearDown(self):
        self.window.close()
        self.window.deleteLater()
        services.global_instances.main_window = self.previous_main_window
        QCoreApplication.sendPostedEvents(None, QEvent.Type.DeferredDelete)
        self.temporary_directory.cleanup()

    def _add_tab(self, title: str, *, closable: bool) -> tuple[QWidget, int]:
        page = QWidget()
        self.window.add_new_tab(
            MainWindowNewTabRequest(
                widget=page,
                title=title,
                closable=closable,
            )
        )
        return page, self.window.tabWidget.indexOf(page)

    def _has_close_button(self, index: int) -> bool:
        tab_bar = self.window.tabWidget.tabBar()
        return any(
            tab_bar.tabButton(index, position) is not None
            for position in (
                QTabBar.ButtonPosition.LeftSide,
                QTabBar.ButtonPosition.RightSide,
            )
        )

    def test_non_closable_tab_rejects_close_request(self):
        page, index = self._add_tab("图库浏览", closable=False)

        self.assertFalse(self._has_close_button(index))
        self.window.tabWidget.tabCloseRequested.emit(index)
        QCoreApplication.sendPostedEvents(None, QEvent.Type.DeferredDelete)

        self.assertEqual(1, self.window.tabWidget.count())
        self.assertIs(page, self.window.tabWidget.widget(0))
        self.assertFalse(sip.isdeleted(page))

    def test_closable_tab_is_removed_and_deleted_later(self):
        library_page, _ = self._add_tab("图库浏览", closable=False)
        search_page, search_index = self._add_tab(
            "文本搜索[test]",
            closable=True,
        )

        self.assertTrue(self._has_close_button(search_index))
        self.window.tabWidget.tabCloseRequested.emit(search_index)

        self.assertEqual(1, self.window.tabWidget.count())
        self.assertIs(library_page, self.window.tabWidget.widget(0))
        self.assertFalse(sip.isdeleted(search_page))

        QCoreApplication.sendPostedEvents(None, QEvent.Type.DeferredDelete)
        self.assertTrue(sip.isdeleted(search_page))

    def test_closable_policy_follows_tab_after_index_changes(self):
        library_page, _ = self._add_tab("图库浏览", closable=False)
        first_page, first_index = self._add_tab("标签搜索[first]", closable=True)
        second_page, _ = self._add_tab("标签搜索[second]", closable=True)

        self.window.tabWidget.tabCloseRequested.emit(first_index)
        QCoreApplication.sendPostedEvents(None, QEvent.Type.DeferredDelete)

        second_index = self.window.tabWidget.indexOf(second_page)
        self.assertEqual(1, second_index)
        self.assertTrue(self._has_close_button(second_index))
        self.window.tabWidget.tabCloseRequested.emit(second_index)
        QCoreApplication.sendPostedEvents(None, QEvent.Type.DeferredDelete)

        self.assertTrue(sip.isdeleted(first_page))
        self.assertTrue(sip.isdeleted(second_page))
        self.assertEqual(1, self.window.tabWidget.count())
        self.assertIs(library_page, self.window.tabWidget.widget(0))

    def test_raise_and_activate_uses_qt_window_operations(self):
        with patch.object(self.window, "show") as show, patch.object(
            self.window, "raise_"
        ) as raise_window, patch.object(
            self.window, "activateWindow"
        ) as activate_window:
            self.window.raise_and_activate()

        show.assert_called_once_with()
        raise_window.assert_called_once_with()
        activate_window.assert_called_once_with()

class TabRequestPolicyTests(unittest.TestCase):
    def test_search_result_tabs_are_closable(self):
        emit = Mock()
        page = Mock()
        main_window = SimpleNamespace(
            signal_add_new_tab=SimpleNamespace(emit=emit),
        )

        with patch.object(
            services.global_instances,
            "main_window",
            main_window,
        ), patch.object(
            search_result_page,
            "SearchResultPage",
            return_value=page,
        ), patch.object(
            library_viewer_service,
            "build_sticker_model",
            return_value=object(),
        ):
            library_viewer_service.open_search_results_tab([], "搜索结果")

        request = emit.call_args.args[0]
        self.assertTrue(request.closable)
        self.assertIs(page, request.widget)

    def test_advanced_search_result_tab_uses_fixed_title(self):
        emit = Mock()
        page = Mock()
        main_window = SimpleNamespace(
            signal_add_new_tab=SimpleNamespace(emit=emit),
        )

        with patch.object(
            services.global_instances,
            "main_window",
            main_window,
        ), patch.object(
            advanced_search_result_page,
            "AdvancedSearchResultPage",
            return_value=page,
        ):
            library_viewer_service.open_advanced_search_results_tab(
                "A AND B",
                [],
            )

        request = emit.call_args.args[0]
        self.assertTrue(request.closable)
        self.assertEqual("高级搜索[表达式]", request.title)
        self.assertIs(page, request.widget)

    def test_similar_images_tabs_are_closable(self):
        emit = Mock()
        page = Mock()
        main_window = SimpleNamespace(
            signal_add_new_tab=SimpleNamespace(emit=emit),
        )
        source = SimpleNamespace(original_file_name="source.png")

        with patch.object(
            services.global_instances,
            "main_window",
            main_window,
        ), patch.object(
            library_viewer_service,
            "fetch_similar_candidates",
            return_value=([], {}),
        ), patch.object(
            similar_images_page,
            "SimilarImagesPage",
            return_value=page,
        ):
            library_viewer_service.open_similar_stickers_tab(source)

        request = emit.call_args.args[0]
        self.assertTrue(request.closable)
        self.assertIs(page, request.widget)

    def test_library_tab_is_not_closable(self):
        emit = Mock()
        page = Mock()
        main_window = SimpleNamespace(
            signal_add_new_tab=SimpleNamespace(emit=emit),
        )

        with patch.object(
            services.global_instances,
            "main_window",
            main_window,
        ), patch.object(
            infinite_sticker_collection_page,
            "InfiniteStickerCollectionPage",
            return_value=page,
        ):
            library_viewer_service.open_sticker_library_view_tab()

        request = emit.call_args.args[0]
        self.assertFalse(request.closable)
        self.assertIs(page, request.widget)


if __name__ == "__main__":
    unittest.main()
