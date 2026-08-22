import os
import tempfile
import unittest
from contextlib import contextmanager
from pathlib import Path
from unittest.mock import patch

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PyQt6.QtCore import Qt
from PyQt6.QtGui import QColor
from PyQt6.QtWidgets import (
    QApplication,
    QDialog,
    QDialogButtonBox,
    QMessageBox,
)

import apppath
from services.settings import create_settings_manager
from ui.dialog_settings import SettingsDialog
from ui.settings_page_color_preset_manager import (
    ColorPresetManagerWidget,
    PresetInputDialog,
)


class PresetInputDialogTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = QApplication.instance() or QApplication([])

    def test_accepts_name_and_color(self):
        dialog = PresetInputDialog()
        try:
            dialog.lineEditPresetName.setText("作者")
            with patch(
                "ui.settings_page_color_preset_manager.QColorDialog.getColor",
                return_value=QColor("#112233"),
            ):
                dialog.pushButtonColor.click()
            self.assertEqual("#112233", dialog.preset_rgb())
            dialog.buttonBox.button(
                QDialogButtonBox.StandardButton.Ok
            ).click()
            self.assertEqual(
                QDialog.DialogCode.Accepted, dialog.result()
            )
            self.assertEqual("作者", dialog.preset_name())
        finally:
            dialog.close()

    def test_rejects_empty_name(self):
        dialog = PresetInputDialog()
        try:
            with patch(
                "ui.settings_page_color_preset_manager.QMessageBox.warning"
            ) as warning:
                dialog.buttonBox.button(
                    QDialogButtonBox.StandardButton.Ok
                ).click()
            warning.assert_called_once()
            self.assertEqual(
                QDialog.DialogCode.Rejected, dialog.result()
            )
        finally:
            dialog.close()

    def test_rejects_duplicate_name(self):
        dialog = PresetInputDialog(existing_names=["作者"])
        try:
            dialog.lineEditPresetName.setText("作者")
            with patch(
                "ui.settings_page_color_preset_manager.QMessageBox.warning"
            ) as warning:
                dialog.buttonBox.button(
                    QDialogButtonBox.StandardButton.Ok
                ).click()
            warning.assert_called_once()
            self.assertEqual(
                QDialog.DialogCode.Rejected, dialog.result()
            )
        finally:
            dialog.close()

    def test_new_mode_uses_new_preset_title(self):
        dialog = PresetInputDialog()
        try:
            self.assertEqual("新建颜色预设", dialog.windowTitle())
            self.assertEqual("", dialog.lineEditPresetName.text())
        finally:
            dialog.close()

    def test_edit_mode_prefills_name_and_color(self):
        dialog = PresetInputDialog(
            initial_name="作者",
            initial_color="#112233",
            existing_names=["系列"],
        )
        try:
            self.assertEqual("编辑颜色预设", dialog.windowTitle())
            self.assertEqual("作者", dialog.lineEditPresetName.text())
            self.assertEqual("#112233", dialog.pushButtonColor.text())
            dialog.buttonBox.button(
                QDialogButtonBox.StandardButton.Ok
            ).click()
            self.assertEqual(
                QDialog.DialogCode.Accepted, dialog.result()
            )
            self.assertEqual("作者", dialog.preset_name())
            self.assertEqual("#112233", dialog.preset_rgb())
        finally:
            dialog.close()

    def test_edit_allows_keeping_original_name(self):
        dialog = PresetInputDialog(
            initial_name="作者",
            existing_names=["系列"],
        )
        try:
            dialog.buttonBox.button(
                QDialogButtonBox.StandardButton.Ok
            ).click()
            self.assertEqual(
                QDialog.DialogCode.Accepted, dialog.result()
            )
        finally:
            dialog.close()


class ColorPresetManagerWidgetTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = QApplication.instance() or QApplication([])

    def setUp(self):
        self.temporary_directory = tempfile.TemporaryDirectory()
        self.config_path = (
            Path(self.temporary_directory.name) / "config.toml"
        )
        self.config_manager = create_settings_manager(self.config_path)
        self.widget = ColorPresetManagerWidget(
            config_manager=self.config_manager
        )

    def tearDown(self):
        self.widget.close()
        self.temporary_directory.cleanup()

    @staticmethod
    @contextmanager
    def _stub_input_dialog(name="作者", rgb="#E91E63"):
        with patch(
            "ui.settings_page_color_preset_manager.PresetInputDialog"
        ) as dialog_class:
            dialog_class.return_value.exec.return_value = (
                QDialog.DialogCode.Accepted
            )
            dialog_class.return_value.preset_name.return_value = name
            dialog_class.return_value.preset_rgb.return_value = rgb
            yield

    def test_loads_presets_from_config(self):
        self.config_manager.set(
            "color_presets",
            [{"name": "作者", "rgb": "#E91E63"}],
        )
        self.config_manager.save()

        widget = ColorPresetManagerWidget(config_manager=self.config_manager)
        try:
            self.assertEqual(1, widget.listWidgetPresets.count())
            item = widget.listWidgetPresets.item(0)
            self.assertEqual("作者", item.text())
            self.assertEqual(
                {"name": "作者", "rgb": "#E91E63"},
                item.data(Qt.ItemDataRole.UserRole),
            )
        finally:
            widget.close()

    def test_add_appends_pending_without_saving(self):
        received = []
        self.widget.changed.connect(lambda: received.append(True))
        with self._stub_input_dialog():
            self.widget.toolButtonAddPreset.click()

        self.assertEqual(1, self.widget.listWidgetPresets.count())
        self.assertEqual("作者", self.widget.listWidgetPresets.item(0).text())
        self.assertEqual([True], received)
        self.assertEqual([], self.config_manager.get("color_presets"))

    def test_delete_removes_pending_after_confirmation(self):
        with self._stub_input_dialog(name="作者"):
            self.widget.toolButtonAddPreset.click()
        with self._stub_input_dialog(name="系列", rgb="#2196F3"):
            self.widget.toolButtonAddPreset.click()
        self.assertEqual(2, self.widget.listWidgetPresets.count())

        self.widget.listWidgetPresets.setCurrentRow(0)
        with patch(
            "ui.settings_page_color_preset_manager.QMessageBox.question",
            return_value=QMessageBox.StandardButton.Yes,
        ) as question:
            self.widget.toolButtonDeletePreset.click()
        question.assert_called_once()

        self.assertEqual(1, self.widget.listWidgetPresets.count())
        self.assertEqual("系列", self.widget.listWidgetPresets.item(0).text())

    def test_delete_without_confirmation_keeps_preset(self):
        with self._stub_input_dialog():
            self.widget.toolButtonAddPreset.click()
        with patch(
            "ui.settings_page_color_preset_manager.QMessageBox.question",
            return_value=QMessageBox.StandardButton.No,
        ):
            self.widget.toolButtonDeletePreset.click()
        self.assertEqual(1, self.widget.listWidgetPresets.count())

    def test_save_settings_stages_into_config_manager_without_disk_write(self):
        with self._stub_input_dialog():
            self.widget.toolButtonAddPreset.click()

        self.widget.save_settings()
        self.assertEqual(
            [{"name": "作者", "rgb": "#E91E63"}],
            self.config_manager.get("color_presets"),
        )
        reloaded = create_settings_manager(self.config_path)
        self.assertEqual([], reloaded.get("color_presets"))

    def test_reload_settings_discards_pending_changes(self):
        with self._stub_input_dialog():
            self.widget.toolButtonAddPreset.click()
        self.assertEqual(1, self.widget.listWidgetPresets.count())

        self.widget.reload_settings()
        self.assertEqual(0, self.widget.listWidgetPresets.count())

    def test_toolbar_buttons_have_icons(self):
        self.assertFalse(self.widget.toolButtonAddPreset.icon().isNull())
        self.assertFalse(self.widget.toolButtonEditPreset.icon().isNull())
        self.assertFalse(self.widget.toolButtonDeletePreset.icon().isNull())

    def test_edit_button_enabled_only_with_selection(self):
        self.assertFalse(self.widget.toolButtonEditPreset.isEnabled())
        self.assertFalse(self.widget.toolButtonDeletePreset.isEnabled())
        with self._stub_input_dialog():
            self.widget.toolButtonAddPreset.click()
        self.assertTrue(self.widget.toolButtonEditPreset.isEnabled())
        self.assertTrue(self.widget.toolButtonDeletePreset.isEnabled())

    def test_edit_updates_pending_and_emits_changed(self):
        with self._stub_input_dialog():
            self.widget.toolButtonAddPreset.click()
        self.assertEqual(1, self.widget.listWidgetPresets.count())

        received = []
        self.widget.changed.connect(lambda: received.append(True))
        with self._stub_input_dialog(name="系列", rgb="#2196F3"):
            self.widget.toolButtonEditPreset.click()

        self.assertEqual(1, self.widget.listWidgetPresets.count())
        item = self.widget.listWidgetPresets.item(0)
        self.assertEqual("系列", item.text())
        self.assertEqual(
            {"name": "系列", "rgb": "#2196F3"},
            item.data(Qt.ItemDataRole.UserRole),
        )
        self.assertEqual([True], received)
        self.assertEqual([], self.config_manager.get("color_presets"))


class SettingsDialogColorPresetTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = QApplication.instance() or QApplication([])
        apppath.app_path = Path(__file__).resolve().parents[1] / "src"

    def setUp(self):
        self.temporary_directory = tempfile.TemporaryDirectory()
        self.config_path = (
            Path(self.temporary_directory.name) / "settings.toml"
        )
        self.manager = create_settings_manager(self.config_path)

    def tearDown(self):
        self.temporary_directory.cleanup()

    def _add_preset_in_dialog(self, dialog, name="作者", rgb="#E91E63"):
        with patch(
            "ui.settings_page_color_preset_manager.PresetInputDialog"
        ) as dialog_class:
            dialog_class.return_value.exec.return_value = (
                QDialog.DialogCode.Accepted
            )
            dialog_class.return_value.preset_name.return_value = name
            dialog_class.return_value.preset_rgb.return_value = rgb
            dialog.colorPresetManager.toolButtonAddPreset.click()

    def test_apply_saves_presets_and_disables_button(self):
        dialog = SettingsDialog(config_manager=self.manager)
        dialog.show()
        try:
            self._add_preset_in_dialog(dialog)

            apply_button = dialog.buttonBox.button(
                QDialogButtonBox.StandardButton.Apply
            )
            self.assertTrue(apply_button.isEnabled())
            apply_button.click()

            saved = create_settings_manager(self.config_path)
            self.assertEqual(
                [{"name": "作者", "rgb": "#E91E63"}],
                saved.get("color_presets"),
            )
            self.assertFalse(apply_button.isEnabled())
        finally:
            dialog.close()

    def test_cancel_discards_pending_presets(self):
        dialog = SettingsDialog(config_manager=self.manager)
        try:
            self._add_preset_in_dialog(dialog)
            dialog.buttonBox.button(
                QDialogButtonBox.StandardButton.Cancel
            ).click()

            saved = create_settings_manager(self.config_path)
            self.assertEqual([], saved.get("color_presets"))
            self.assertEqual(
                QDialog.DialogCode.Rejected, dialog.result()
            )
        finally:
            dialog.close()

    def test_ok_saves_presets_and_accepts(self):
        dialog = SettingsDialog(config_manager=self.manager)
        try:
            self._add_preset_in_dialog(dialog)
            dialog.buttonBox.button(
                QDialogButtonBox.StandardButton.Ok
            ).click()

            saved = create_settings_manager(self.config_path)
            self.assertEqual(
                [{"name": "作者", "rgb": "#E91E63"}],
                saved.get("color_presets"),
            )
            self.assertEqual(
                QDialog.DialogCode.Accepted, dialog.result()
            )
        finally:
            dialog.close()

    def test_save_failure_reloads_preset_page(self):
        dialog = SettingsDialog(config_manager=self.manager)
        dialog.show()
        try:
            self._add_preset_in_dialog(dialog)
            self.assertEqual(
                1, dialog.colorPresetManager.listWidgetPresets.count()
            )

            with patch.object(
                self.manager,
                "save",
                side_effect=OSError("磁盘不可写"),
            ), patch(
                "ui.dialog_settings.QMessageBox.critical"
            ), patch(
                "ui.dialog_settings.logger.exception"
            ):
                dialog.buttonBox.button(
                    QDialogButtonBox.StandardButton.Ok
                ).click()

            self.assertTrue(dialog.isVisible())
            self.assertEqual(
                0, dialog.colorPresetManager.listWidgetPresets.count()
            )
            self.assertEqual(
                [], self.manager.get("color_presets")
            )
        finally:
            dialog.close()


if __name__ == "__main__":
    unittest.main()
