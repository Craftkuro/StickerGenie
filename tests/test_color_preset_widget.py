import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PyQt6.QtCore import Qt
from PyQt6.QtGui import QColor
from PyQt6.QtWidgets import QApplication, QDialog, QDialogButtonBox

from config_manager import ConfigField, ConfigType
from services.settings import SETTINGS_SCHEMA, SETTINGS_VERSION, create_settings_manager
from ui.widgets.color_preset_widget import ColorPresetDialog


class ColorPresetSchemaTests(unittest.TestCase):
    def test_list_table_type_validates_list_of_dicts(self):
        field = ConfigField("color_presets", ConfigType.LIST_TABLE, [])
        self.assertTrue(
            field.validate_value([{"name": "作者", "rgb": "#E91E63"}])
        )
        self.assertTrue(field.validate_value([]))
        self.assertFalse(field.validate_value("作者"))
        self.assertFalse(field.validate_value(["作者"]))
        self.assertFalse(field.validate_value(123))

    def test_settings_schema_declares_color_presets(self):
        field = next(
            config_field
            for config_field in SETTINGS_SCHEMA
            if config_field.key == "color_presets"
        )
        self.assertIs(ConfigType.LIST_TABLE, field.type)
        self.assertEqual([], field.default)


class ColorPresetConfigFileTests(unittest.TestCase):
    def setUp(self):
        self.temporary_directory = tempfile.TemporaryDirectory()
        self.config_path = Path(self.temporary_directory.name) / "config.toml"

    def tearDown(self):
        self.temporary_directory.cleanup()

    def test_presets_written_as_toml_table_array(self):
        manager = create_settings_manager(self.config_path)
        manager.set(
            "color_presets",
            [
                {"name": "作者", "rgb": "#E91E63"},
                {"name": "系列", "rgb": "#2196F3"},
            ],
        )
        manager.save()

        content = self.config_path.read_text(encoding="utf-8")
        self.assertIn("[[config.color_presets]]", content)
        self.assertIn('name = "作者"', content)

        reloaded = create_settings_manager(self.config_path)
        self.assertEqual(
            [
                {"name": "作者", "rgb": "#E91E63"},
                {"name": "系列", "rgb": "#2196F3"},
            ],
            reloaded.get("color_presets"),
        )

    def test_existing_config_gains_empty_color_presets(self):
        self.config_path.write_text(
            '__version__ = "1.2.0"\n\n'
            "[config]\n"
            "recent_search_limit = 3\n",
            encoding="utf-8",
        )

        manager = create_settings_manager(self.config_path)
        self.assertEqual([], manager.get("color_presets"))
        content = self.config_path.read_text(encoding="utf-8")
        self.assertIn(f'__version__ = "{SETTINGS_VERSION}"', content)
        self.assertIn("color_presets", content)


class ColorPresetDialogTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = QApplication.instance() or QApplication([])

    def setUp(self):
        self.temporary_directory = tempfile.TemporaryDirectory()
        self.config_path = Path(self.temporary_directory.name) / "config.toml"
        self.config_manager = create_settings_manager(self.config_path)
        self.dialog = ColorPresetDialog(config_manager=self.config_manager)

    def tearDown(self):
        self.dialog.close()
        self.temporary_directory.cleanup()

    def test_list_starts_empty_without_presets(self):
        self.assertEqual(0, self.dialog.listWidgetPresets.count())
        ok_button = self.dialog.buttonBox.button(
            QDialogButtonBox.StandardButton.Ok
        )
        self.assertFalse(ok_button.isEnabled())
        self.assertIsNone(self.dialog.selected_preset())

    def test_add_preset_appends_selects_and_persists(self):
        self.dialog.lineEditPresetName.setText("作者")
        self.dialog.pushButtonAddPreset.click()

        self.assertEqual("作者", self.dialog.selected_preset()["name"])
        self.assertEqual("#2196F3", self.dialog.selected_rgb())
        reloaded = create_settings_manager(self.config_path)
        self.assertEqual(
            [{"name": "作者", "rgb": "#2196F3"}],
            reloaded.get("color_presets"),
        )

    def test_add_rejects_empty_name(self):
        with patch(
            "ui.widgets.color_preset_widget.QMessageBox.warning"
        ) as warning:
            self.dialog.pushButtonAddPreset.click()
        warning.assert_called_once()
        self.assertEqual([], self.config_manager.get("color_presets"))

    def test_add_rejects_duplicate_name(self):
        self.dialog.lineEditPresetName.setText("作者")
        self.dialog.pushButtonAddPreset.click()
        self.dialog.lineEditPresetName.setText("作者")
        with patch(
            "ui.widgets.color_preset_widget.QMessageBox.warning"
        ) as warning:
            self.dialog.pushButtonAddPreset.click()
        warning.assert_called_once()
        reloaded = create_settings_manager(self.config_path)
        self.assertEqual(1, len(reloaded.get("color_presets")))

    def test_dialog_shows_saved_presets(self):
        self.config_manager.set(
            "color_presets",
            [
                {"name": "作者", "rgb": "#E91E63"},
                {"name": "系列", "rgb": "#2196F3"},
            ],
        )
        self.config_manager.save()

        dialog = ColorPresetDialog(config_manager=self.config_manager)
        try:
            self.assertEqual(2, dialog.listWidgetPresets.count())
            first_item = dialog.listWidgetPresets.item(0)
            self.assertEqual("作者", first_item.text())
            self.assertEqual(
                {"name": "作者", "rgb": "#E91E63"},
                first_item.data(Qt.ItemDataRole.UserRole),
            )
        finally:
            dialog.close()

    def test_ok_accepts_selected_preset(self):
        self.config_manager.set(
            "color_presets",
            [{"name": "作者", "rgb": "#E91E63"}],
        )
        self.config_manager.save()

        dialog = ColorPresetDialog(config_manager=self.config_manager)
        try:
            ok_button = dialog.buttonBox.button(
                QDialogButtonBox.StandardButton.Ok
            )
            self.assertTrue(ok_button.isEnabled())
            ok_button.click()
            self.assertEqual(QDialog.DialogCode.Accepted, dialog.result())
            self.assertEqual("#E91E63", dialog.selected_rgb())
        finally:
            dialog.close()

    def test_color_button_opens_picker(self):
        self.assertEqual("#2196F3", self.dialog.pushButtonPresetColor.text())
        with patch(
            "ui.widgets.color_preset_widget.QColorDialog.getColor",
            return_value=QColor("#112233"),
        ):
            self.dialog.pushButtonPresetColor.click()
        self.assertEqual("#112233", self.dialog.pushButtonPresetColor.text())


if __name__ == "__main__":
    unittest.main()
