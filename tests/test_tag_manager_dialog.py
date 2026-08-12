import os
import tempfile
import unittest
import xml.etree.ElementTree as ElementTree
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock, patch

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PyQt6.QtGui import QColor
from PyQt6.QtWidgets import QApplication, QDialogButtonBox, QMessageBox

import apppath
import services.global_instances
from commons.dto import Tag
from services.settings import create_settings_manager
from stickerdb.v1.sticker_db import StickerDBV1
from ui.dialog_tag_manager import TAG_DATA_ROLE, TagManagerDialog
from ui.main_window import MainWindow


def make_tag(
    name: str,
    *,
    description: str | None = None,
    enabled: bool = True,
    color: str = "#2196F3",
    order: int = 0,
) -> Tag:
    tag = Tag()
    tag.name = name
    tag.description = description
    tag.enabled = enabled
    tag.color_rgb = color
    tag.order = order
    return tag


class TagManagerDialogTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = QApplication.instance() or QApplication([])
        apppath.app_path = Path(__file__).resolve().parents[1] / "src"

    def setUp(self):
        self._temp_dir = tempfile.TemporaryDirectory()
        self.db = StickerDBV1(str(Path(self._temp_dir.name) / "library.db"))
        self.first = self.db.add_or_modify_tag(
            make_tag(
                "First",
                description="First description",
                color="#112233",
            )
        )
        self.second = self.db.add_or_modify_tag(
            make_tag("Second", enabled=False, color="#445566")
        )
        self.dialog = TagManagerDialog(database=self.db)

    def tearDown(self):
        self.dialog.close()
        self.db.engine.dispose()
        self._temp_dir.cleanup()

    def test_ui_declares_tag_manager_controls(self):
        ui_path = apppath.app_path / "ui" / "dialog_tag_manager.ui"
        root = ElementTree.parse(ui_path).getroot()
        widget_names = {
            widget.attrib["name"]
            for widget in root.iter("widget")
            if "name" in widget.attrib
        }

        self.assertTrue(
            {
                "splitterTags",
                "lineEditTagFilter",
                "listWidgetTags",
                "lineEditTagName",
                "plainTextEditTagDescription",
                "pushButtonTagColor",
                "checkBoxTagEnabled",
                "spinBoxTagOrder",
                "pushButtonSaveTag",
                "buttonBox",
            }.issubset(widget_names)
        )

    def test_loads_all_tags_and_filters_by_name(self):
        self.assertEqual(2, self.dialog.listWidgetTags.count())
        self.assertEqual("First", self.dialog.lineEditTagName.text())

        self.dialog.lineEditTagFilter.setText("second")

        self.assertTrue(self.dialog.listWidgetTags.item(0).isHidden())
        self.assertFalse(self.dialog.listWidgetTags.item(1).isHidden())
        self.assertIsNone(self.dialog.listWidgetTags.currentItem())

    def test_switching_selection_discards_unsaved_changes(self):
        self.dialog.lineEditTagName.setText("Unsaved")

        self.dialog.listWidgetTags.setCurrentRow(1)
        self.dialog.listWidgetTags.setCurrentRow(0)

        self.assertEqual("First", self.dialog.lineEditTagName.text())
        self.assertEqual(
            ["First", "Second"],
            [tag.name for tag in self.db.list_tags()],
        )

    def test_save_updates_only_current_tag_immediately(self):
        self.dialog.lineEditTagName.setText("Renamed")
        self.dialog.plainTextEditTagDescription.setPlainText("Updated")
        self.dialog.checkBoxTagEnabled.setChecked(False)
        self.dialog.spinBoxTagOrder.setValue(7)
        self.dialog._set_tag_color(QColor("#ABCDEF"))
        self.dialog._update_dirty_state()

        self.dialog.pushButtonSaveTag.click()

        stored = next(tag for tag in self.db.list_tags() if tag.id == self.first.id)
        unchanged = next(tag for tag in self.db.list_tags() if tag.id == self.second.id)
        self.assertEqual("Renamed", stored.name)
        self.assertEqual("Updated", stored.description)
        self.assertFalse(stored.enabled)
        self.assertEqual("#ABCDEF", stored.color_rgb)
        self.assertEqual(7, stored.order)
        self.assertEqual("Second", unchanged.name)
        self.assertFalse(self.dialog.pushButtonSaveTag.isEnabled())

    def test_unsaved_new_tag_is_discarded_on_selection_change(self):
        self.dialog.toolButtonAddTag.click()
        self.dialog.lineEditTagName.setText("Draft")

        self.dialog.listWidgetTags.setCurrentRow(1)

        self.assertEqual("Second", self.dialog.lineEditTagName.text())
        self.assertNotIn("Draft", [tag.name for tag in self.db.list_tags()])

    def test_save_creates_new_tag(self):
        self.dialog.toolButtonAddTag.click()
        self.dialog.lineEditTagName.setText("Created")
        self.dialog.plainTextEditTagDescription.setPlainText("New description")

        self.dialog.pushButtonSaveTag.click()

        stored = next(tag for tag in self.db.list_tags() if tag.name == "Created")
        self.assertEqual("New description", stored.description)
        self.assertEqual(stored.id, self.dialog._selected_tag.id)

    def test_close_discards_unsaved_changes(self):
        self.dialog.lineEditTagName.setText("Unsaved")

        self.dialog.buttonBox.button(
            QDialogButtonBox.StandardButton.Close
        ).click()

        stored = next(tag for tag in self.db.list_tags() if tag.id == self.first.id)
        self.assertEqual("First", stored.name)

    def test_delete_removes_current_tag_after_confirmation(self):
        with patch(
            "ui.dialog_tag_manager.QMessageBox.question",
            return_value=QMessageBox.StandardButton.Yes,
        ):
            self.dialog.toolButtonDeleteTag.click()

        self.assertEqual(
            ["Second"],
            [tag.name for tag in self.db.list_tags()],
        )

    def test_save_failure_preserves_editor_values(self):
        self.dialog.lineEditTagName.setText("Still here")

        with patch.object(
            self.db,
            "add_or_modify_tag",
            side_effect=OSError("database unavailable"),
        ), patch(
            "ui.dialog_tag_manager.QMessageBox.critical"
        ) as critical, patch("ui.dialog_tag_manager.logger.exception"):
            self.dialog.pushButtonSaveTag.click()

        self.assertEqual("Still here", self.dialog.lineEditTagName.text())
        self.assertTrue(self.dialog.pushButtonSaveTag.isEnabled())
        critical.assert_called_once_with(
            self.dialog,
            "保存失败",
            "database unavailable",
        )


class MainWindowTagManagerTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = QApplication.instance() or QApplication([])

    def test_main_window_ui_declares_tag_manager_action_in_repository_menu(self):
        apppath.app_path = Path(__file__).resolve().parents[1] / "src"
        root = ElementTree.parse(apppath.app_path / "ui" / "main_window.ui").getroot()
        repository_menu = next(
            widget
            for widget in root.iter("widget")
            if widget.attrib.get("name") == "menu_2"
        )
        action_names = [
            action.attrib["name"]
            for action in repository_menu.findall("addaction")
        ]

        self.assertIn("actionOpenTagManager", action_names)

    def test_open_tag_manager_uses_current_database_and_refreshes_search(self):
        database = object()
        refresh_suggestions = Mock()
        window = SimpleNamespace(
            customSearchBox=SimpleNamespace(
                refresh_suggestions=refresh_suggestions,
            )
        )

        with patch.object(
            services.global_instances,
            "current_library_db",
            database,
        ), patch("ui.main_window.TagManagerDialog") as dialog_class:
            MainWindow.open_tag_manager(window)

        dialog_class.assert_called_once_with(window, database=database)
        dialog_class.return_value.exec.assert_called_once_with()
        refresh_suggestions.assert_called_once_with()

    def test_tag_manager_button_opens_tag_manager(self):
        apppath.app_path = Path(__file__).resolve().parents[1] / "src"
        previous_main_window = services.global_instances.main_window
        database = object()
        with tempfile.TemporaryDirectory() as temp_dir:
            settings_manager = create_settings_manager(
                Path(temp_dir) / "settings.toml"
            )
            with patch(
                "ui.main_window.services.import_images.ImageImportService"
            ), patch(
                "ui.main_window.services.export_library.LibraryExportService"
            ), patch(
                "ui.main_window.services.database_maintenance.DatabaseMaintenanceService"
            ), patch.object(MainWindow, "debug_start_test_view"):
                window = MainWindow(settings_manager=settings_manager)
            try:
                with patch.object(
                    services.global_instances,
                    "current_library_db",
                    database,
                ), patch("ui.main_window.TagManagerDialog") as dialog_class:
                    window.pushButtonTagManager.click()

                dialog_class.assert_called_once_with(window, database=database)
                dialog_class.return_value.exec.assert_called_once_with()
            finally:
                window.close()
                services.global_instances.main_window = previous_main_window


if __name__ == "__main__":
    unittest.main()
