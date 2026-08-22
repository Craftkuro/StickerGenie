import os
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PyQt6.QtGui import QAction
from PyQt6.QtWidgets import QApplication

import apppath
import services.global_instances
from services.settings import create_settings_manager
from ui.main_window import MainWindow


class MainWindowMainMenuButtonTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = QApplication.instance() or QApplication([])
        apppath.app_path = Path(__file__).resolve().parents[1] / "src"

    def setUp(self):
        self.previous_main_window = services.global_instances.main_window
        self.temporary_directory = tempfile.TemporaryDirectory()
        self.settings_manager = create_settings_manager(
            Path(self.temporary_directory.name) / "settings.toml"
        )
        self.window = None

    def tearDown(self):
        if self.window is not None:
            self.window.close()
            self.window.deleteLater()
            self.app.processEvents()
        services.global_instances.main_window = self.previous_main_window
        self.temporary_directory.cleanup()

    def _create_window(self, *, frozen: bool):
        with patch.object(sys, "frozen", frozen, create=True), patch(
            "ui.main_window.ImageImportService"
        ), patch(
            "ui.main_window.services.export_library.LibraryExportService"
        ), patch("ui.main_window.DatabaseMaintenanceService"), patch.object(
            MainWindow, "debug_start_test_view"
        ):
            self.window = MainWindow(settings_manager=self.settings_manager)

    def test_menu_bar_is_hidden_and_button_opens_same_menus(self):
        self._create_window(frozen=False)
        self.window.show()
        self.app.processEvents()

        self.assertTrue(self.window.menuBar().isHidden())

        self.assertEqual("", self.window.pushButtonMainMenu.text())
        self.assertFalse(self.window.pushButtonMainMenu.icon().isNull())

        popup = self.window.pushButtonMainMenu.menu()
        self.assertIsNotNone(popup)

        bar_menus = [
            action.menu()
            for action in self.window.menuBar().actions()
            if action.menu() is not None
        ]
        popup_menus = [
            action.menu()
            for action in popup.actions()
            if action.menu() is not None
        ]

        self.assertEqual(
            [menu.title() for menu in bar_menus],
            [menu.title() for menu in popup_menus],
        )
        for bar_menu, popup_menu in zip(bar_menus, popup_menus):
            self.assertIs(bar_menu, popup_menu)

    def test_frozen_build_does_not_add_developer_menu_to_button(self):
        self._create_window(frozen=True)

        popup = self.window.pushButtonMainMenu.menu()
        self.assertIsNotNone(popup)
        popup_titles = [action.text() for action in popup.actions()]
        self.assertNotIn("开发工具", popup_titles)

    def test_menu_structure_matches_expected(self):
        self._create_window(frozen=False)

        expected = {
            "文件": ["导入图片", None, "退出"],
            "图库": [
                "标签管理器",
                "开始图库审阅",
                "开始数据库维护",
                None,
                "导入备份",
                "导出备份",
            ],
            "选项": ["设置"],
            "帮助": ["关于"],
            "开发工具": ["自定义调试操作"],
        }

        actual = {}
        for menu_action in self.window.menuBar().actions():
            menu = menu_action.menu()
            if menu is None:
                continue
            items = [
                None if action.isSeparator() else action.text()
                for action in menu.actions()
            ]
            actual[menu.title()] = items

        self.assertEqual(expected, actual)

        for removed_name in (
            "actionNewStickerRepo",
            "actionOpenExistingRepo",
            "actionManageLoadedRepos",
            "actionCurrentViewMenuSortMode",
            "actionMostUsedKeywords",
        ):
            self.assertIsNone(self.window.findChild(QAction, removed_name))


if __name__ == "__main__":
    unittest.main()
