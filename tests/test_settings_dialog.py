import os
import tempfile
import unittest
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
        self.manager.set("theme", "dark")
        self.manager.set("thumbnail_size", 208)
        self.manager.save()

        dialog = SettingsDialog(config_manager=self.manager)

        self.assertEqual(2, dialog.listWidget.count())
        self.assertEqual(0, dialog.stackedWidget.currentIndex())
        self.assertEqual("dark", dialog.comboBoxTheme.currentData())
        self.assertEqual(208, dialog.spinBoxThumbnailSize.value())

        dialog.listWidget.setCurrentRow(1)

        self.assertEqual(1, dialog.stackedWidget.currentIndex())

    def test_apply_saves_values_without_closing_dialog(self):
        dialog = SettingsDialog(config_manager=self.manager)
        dialog.show()
        dialog.checkBoxRestoreLastSession.setChecked(False)
        dialog.spinBoxRecentSearchLimit.setValue(24)
        dialog.spinBoxTagSuggestionLimit.setValue(7)
        dialog.comboBoxDefaultView.setCurrentIndex(
            dialog.comboBoxDefaultView.findData("list")
        )

        apply_button = dialog.buttonBox.button(
            QDialogButtonBox.StandardButton.Apply
        )
        self.assertTrue(apply_button.isEnabled())
        apply_button.click()

        saved_manager = create_settings_manager(self.config_path)
        self.assertFalse(saved_manager.get("restore_last_session"))
        self.assertEqual(24, saved_manager.get("recent_search_limit"))
        self.assertEqual(7, saved_manager.get("tag_suggestion_limit"))
        self.assertEqual("list", saved_manager.get("default_view"))
        self.assertFalse(apply_button.isEnabled())
        self.assertTrue(dialog.isVisible())
        dialog.close()

    def test_cancel_discards_unapplied_changes(self):
        dialog = SettingsDialog(config_manager=self.manager)
        dialog.comboBoxTheme.setCurrentIndex(
            dialog.comboBoxTheme.findData("dark")
        )

        dialog.buttonBox.button(
            QDialogButtonBox.StandardButton.Cancel
        ).click()

        saved_manager = create_settings_manager(self.config_path)
        self.assertEqual("system", saved_manager.get("theme"))
        self.assertEqual(QDialog.DialogCode.Rejected, dialog.result())

    def test_ok_saves_changes_and_accepts_dialog(self):
        dialog = SettingsDialog(config_manager=self.manager)
        dialog.spinBoxThumbnailSize.setValue(192)

        dialog.buttonBox.button(
            QDialogButtonBox.StandardButton.Ok
        ).click()

        saved_manager = create_settings_manager(self.config_path)
        self.assertEqual(192, saved_manager.get("thumbnail_size"))
        self.assertEqual(QDialog.DialogCode.Accepted, dialog.result())

    def test_save_failure_keeps_dialog_open(self):
        dialog = SettingsDialog(config_manager=self.manager)
        dialog.show()
        dialog.spinBoxThumbnailSize.setValue(192)

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
        self.assertEqual(144, self.manager.get("thumbnail_size"))
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
