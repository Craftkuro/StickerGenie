import os
import tempfile
import unittest
import xml.etree.ElementTree as ElementTree
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock, patch

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PyQt6.QtWidgets import QApplication, QDialog, QDialogButtonBox

import apppath
from ui.dialog_settings import SettingsDialog, create_settings_manager
from ui.main_window import MainWindow


class SettingsDialogTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = QApplication.instance() or QApplication([])
        apppath.app_path = Path(__file__).resolve().parents[1] / "src"

    def setUp(self):
        self.temporary_directory = tempfile.TemporaryDirectory()
        self.config_path = Path(self.temporary_directory.name) / "settings.toml"
        self.manager = create_settings_manager(self.config_path)

    def tearDown(self):
        self.temporary_directory.cleanup()

    def test_loads_saved_values_and_switches_categories(self):
        self.manager.set("recent_search_limit", 8)
        self.manager.set("tag_suggestion_limit", 12)
        self.manager.save()

        dialog = SettingsDialog(config_manager=self.manager)

        self.assertEqual(1, dialog.listWidget.count())
        self.assertEqual("搜索", dialog.listWidget.item(0).text())
        self.assertEqual(0, dialog.stackedWidget.currentIndex())
        self.assertEqual(8, dialog.spinBoxRecentSearchLimit.value())
        self.assertEqual(12, dialog.spinBoxTagSuggestionLimit.value())

        dialog.listWidget.setCurrentRow(0)

        self.assertEqual(0, dialog.stackedWidget.currentIndex())

    def test_schema_contains_only_active_settings_and_internal_history(self):
        self.assertEqual(
            [
                "recent_search_limit",
                "tag_suggestion_limit",
                "recent_searches",
            ],
            self.manager.schema.keys,
        )

    def test_user_facing_settings_are_declared_in_ui_file(self):
        ui_path = apppath.app_path / "ui" / "dialog_settings.ui"
        root = ElementTree.parse(ui_path).getroot()
        widget_names = {
            widget.attrib["name"]
            for widget in root.iter("widget")
            if "name" in widget.attrib
        }

        self.assertTrue(
            {
                "spinBoxRecentSearchLimit",
                "spinBoxTagSuggestionLimit",
            }.issubset(widget_names)
        )
        self.assertTrue(
            {
                "checkBoxRestoreLastSession",
                "checkBoxConfirmBeforeDelete",
                "comboBoxDefaultView",
                "comboBoxTheme",
                "spinBoxThumbnailSize",
                "checkBoxShowTagCounts",
            }.isdisjoint(widget_names)
        )

    def test_apply_saves_values_without_closing_dialog(self):
        dialog = SettingsDialog(config_manager=self.manager)
        dialog.show()
        dialog.spinBoxRecentSearchLimit.setValue(24)
        dialog.spinBoxTagSuggestionLimit.setValue(7)

        apply_button = dialog.buttonBox.button(
            QDialogButtonBox.StandardButton.Apply
        )
        self.assertTrue(apply_button.isEnabled())
        apply_button.click()

        saved_manager = create_settings_manager(self.config_path)
        self.assertEqual(24, saved_manager.get("recent_search_limit"))
        self.assertEqual(7, saved_manager.get("tag_suggestion_limit"))
        self.assertFalse(apply_button.isEnabled())
        self.assertTrue(dialog.isVisible())
        dialog.close()

    def test_cancel_discards_unapplied_changes(self):
        dialog = SettingsDialog(config_manager=self.manager)
        dialog.spinBoxRecentSearchLimit.setValue(20)

        dialog.buttonBox.button(
            QDialogButtonBox.StandardButton.Cancel
        ).click()

        saved_manager = create_settings_manager(self.config_path)
        self.assertEqual(3, saved_manager.get("recent_search_limit"))
        self.assertEqual(QDialog.DialogCode.Rejected, dialog.result())

    def test_ok_saves_changes_and_accepts_dialog(self):
        dialog = SettingsDialog(config_manager=self.manager)
        dialog.spinBoxTagSuggestionLimit.setValue(18)

        dialog.buttonBox.button(
            QDialogButtonBox.StandardButton.Ok
        ).click()

        saved_manager = create_settings_manager(self.config_path)
        self.assertEqual(18, saved_manager.get("tag_suggestion_limit"))
        self.assertEqual(QDialog.DialogCode.Accepted, dialog.result())

    def test_save_failure_keeps_dialog_open(self):
        dialog = SettingsDialog(config_manager=self.manager)
        dialog.show()
        dialog.spinBoxRecentSearchLimit.setValue(20)

        with patch.object(
            self.manager,
            "save",
            side_effect=OSError("磁盘不可写"),
        ), patch(
            "ui.dialog_settings.QMessageBox.critical"
        ) as critical, patch("ui.dialog_settings.logger.exception"):
            dialog.buttonBox.button(
                QDialogButtonBox.StandardButton.Ok
            ).click()

        self.assertTrue(dialog.isVisible())
        self.assertEqual(3, self.manager.get("recent_search_limit"))
        critical.assert_called_once_with(dialog, "保存设置失败", "磁盘不可写")
        dialog.close()


class MainWindowSettingsTests(unittest.TestCase):
    def test_open_settings_runs_modal_dialog(self):
        settings_manager = object()
        refresh_suggestions = Mock()
        window = SimpleNamespace(
            _settings_manager=settings_manager,
            customSearchBox=SimpleNamespace(
                refresh_suggestions=refresh_suggestions,
            ),
        )
        with patch("ui.main_window.SettingsDialog") as dialog_class:
            MainWindow.open_settings(window)

        dialog_class.assert_called_once_with(
            window,
            config_manager=settings_manager,
        )
        dialog_class.return_value.exec.assert_called_once_with()
        refresh_suggestions.assert_called_once_with()


if __name__ == "__main__":
    unittest.main()
