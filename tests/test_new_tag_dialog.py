import os
import unittest
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest.mock import patch

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PyQt6.QtGui import QColor
from PyQt6.QtWidgets import QApplication, QDialog, QDialogButtonBox

from commons.dto import Tag
from stickerdb.v1.sticker_db import StickerDBV1
from ui.dialog_tag_selector import (
    TAG_ID_ROLE,
    NewTagDialog,
    TagSelectorWidget,
)


def make_tag(
    name: str,
    *,
    enabled: bool = True,
    color: str = "#2196F3",
    order: int = 0,
) -> Tag:
    tag = Tag()
    tag.name = name
    tag.enabled = enabled
    tag.color_rgb = color
    tag.order = order
    return tag


class NewTagDialogTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = QApplication.instance() or QApplication([])

    def setUp(self):
        self._temp_dir = TemporaryDirectory()
        self.db = StickerDBV1(str(Path(self._temp_dir.name) / "library.db"))
        self.db.add_or_modify_tag(make_tag("Existing"))
        self.dialog = NewTagDialog(database=self.db)

    def tearDown(self):
        self.dialog.close()
        self.db.engine.dispose()
        self._temp_dir.cleanup()

    def test_defaults_match_tag_properties(self):
        self.assertEqual("", self.dialog.lineEditTagName.text())
        self.assertEqual("", self.dialog.plainTextEditTagDescription.toPlainText())
        self.assertTrue(self.dialog.checkBoxTagEnabled.isChecked())
        self.assertEqual("#2196F3", self.dialog.pushButtonTagColor.text())
        self.assertEqual(0, self.dialog.spinBoxTagOrder.value())
        self.assertIsNone(self.dialog.new_tag_id)

    def test_save_creates_tag_and_returns_id(self):
        created_ids = []
        self.dialog.tag_created.connect(created_ids.append)
        self.dialog.lineEditTagName.setText("New Tag")
        self.dialog.plainTextEditTagDescription.setPlainText("Some description")
        self.dialog.checkBoxTagEnabled.setChecked(False)
        self.dialog.spinBoxTagOrder.setValue(5)
        self.dialog._set_tag_color(QColor("#ABCDEF"))

        self.dialog.buttonBox.button(
            QDialogButtonBox.StandardButton.Save
        ).click()

        self.assertEqual(QDialog.DialogCode.Accepted, self.dialog.result())
        stored = next(
            tag for tag in self.db.list_tags() if tag.name == "New Tag"
        )
        self.assertEqual(stored.id, self.dialog.new_tag_id)
        self.assertEqual([stored.id], created_ids)
        self.assertEqual("Some description", stored.description)
        self.assertFalse(stored.enabled)
        self.assertEqual("#ABCDEF", stored.color_rgb)

    def test_save_requires_non_empty_name(self):
        with patch(
            "ui.dialog_tag_selector.dialog_new_tag.QMessageBox.warning"
        ) as warning:
            self.dialog.lineEditTagName.setText("   ")
            self.dialog.buttonBox.button(
                QDialogButtonBox.StandardButton.Save
            ).click()

        warning.assert_called_once_with(
            self.dialog,
            "无法保存",
            "标签名称不能为空。",
        )
        self.assertEqual(QDialog.DialogCode.Rejected, self.dialog.result())
        self.assertIsNone(self.dialog.new_tag_id)
        self.assertEqual(["Existing"], [tag.name for tag in self.db.list_tags()])

    def test_save_rejects_duplicate_name(self):
        with patch(
            "ui.dialog_tag_selector.dialog_new_tag.QMessageBox.warning"
        ) as warning:
            self.dialog.lineEditTagName.setText("Existing")
            self.dialog.buttonBox.button(
                QDialogButtonBox.StandardButton.Save
            ).click()

        warning.assert_called_once_with(
            self.dialog,
            "无法保存",
            "已经存在同名标签。",
        )
        self.assertEqual(QDialog.DialogCode.Rejected, self.dialog.result())
        self.assertIsNone(self.dialog.new_tag_id)
        self.assertEqual(["Existing"], [tag.name for tag in self.db.list_tags()])

    def test_save_failure_keeps_dialog_open(self):
        self.dialog.lineEditTagName.setText("Will fail")
        with patch.object(
            self.db,
            "add_or_modify_tag",
            side_effect=OSError("database unavailable"),
        ), patch(
            "ui.dialog_tag_selector.dialog_new_tag.QMessageBox.critical"
        ) as critical, patch(
            "ui.dialog_tag_selector.dialog_new_tag.logger.exception"
        ):
            self.dialog.buttonBox.button(
                QDialogButtonBox.StandardButton.Save
            ).click()

        critical.assert_called_once()
        self.assertEqual(QDialog.DialogCode.Rejected, self.dialog.result())
        self.assertIsNone(self.dialog.new_tag_id)
        self.assertEqual(["Existing"], [tag.name for tag in self.db.list_tags()])

    def test_cancel_rejects_without_creating(self):
        self.dialog.lineEditTagName.setText("Draft")

        self.dialog.buttonBox.button(
            QDialogButtonBox.StandardButton.Cancel
        ).click()

        self.assertEqual(QDialog.DialogCode.Rejected, self.dialog.result())
        self.assertIsNone(self.dialog.new_tag_id)
        self.assertEqual(["Existing"], [tag.name for tag in self.db.list_tags()])

    def test_set_tag_color_updates_button(self):
        self.dialog._set_tag_color(QColor("#ABCDEF"))
        self.assertEqual("#ABCDEF", self.dialog.pushButtonTagColor.text())
        self.assertEqual("#ABCDEF", self.dialog._tag_color.name().upper())

    def test_choose_tag_color_uses_preset_dialog(self):
        with patch(
            "ui.dialog_tag_selector.dialog_new_tag.ColorPresetDialog"
        ) as dialog_class:
            dialog_class.return_value.exec.return_value = (
                QDialog.DialogCode.Accepted
            )
            dialog_class.return_value.selected_rgb.return_value = "#ABCDEF"
            self.dialog.pushButtonTagColor.click()

        dialog_class.assert_called_once_with(self.dialog)
        self.assertEqual("#ABCDEF", self.dialog.pushButtonTagColor.text())
        self.assertEqual("#ABCDEF", self.dialog._tag_color.name().upper())

    def test_choose_tag_color_cancel_keeps_current_color(self):
        with patch(
            "ui.dialog_tag_selector.dialog_new_tag.ColorPresetDialog"
        ) as dialog_class:
            dialog_class.return_value.exec.return_value = (
                QDialog.DialogCode.Rejected
            )
            self.dialog.pushButtonTagColor.click()

        dialog_class.assert_called_once_with(self.dialog)
        self.assertEqual("#2196F3", self.dialog.pushButtonTagColor.text())

    def test_missing_database_raises(self):
        with patch(
            "services.global_instances.current_library_db",
            None,
        ):
            with self.assertRaises(RuntimeError):
                NewTagDialog(database=None)


class NewTagDialogSelectorIntegrationTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = QApplication.instance() or QApplication([])

    def setUp(self):
        self._temp_dir = TemporaryDirectory()
        self.db = StickerDBV1(str(Path(self._temp_dir.name) / "library.db"))
        self.widget = TagSelectorWidget(database=self.db)

    def tearDown(self):
        self.widget.close()
        self.db.engine.dispose()
        self._temp_dir.cleanup()

    def test_new_tag_button_refreshes_list_and_selects_new_tag(self):
        new_tag = self.db.add_or_modify_tag(make_tag("Created"))

        with patch(
            "ui.dialog_tag_selector.widget.NewTagDialog"
        ) as dialog_class:
            dialog_class.return_value.exec.return_value = (
                QDialog.DialogCode.Accepted
            )
            dialog_class.return_value.new_tag_id = new_tag.id
            self.widget.new_tag_button.click()

        dialog_class.assert_called_once_with(self.widget, database=self.db)
        self.assertEqual(
            ["Created"],
            [
                self.widget.available_list_widget.item(row).text()
                for row in range(self.widget.available_list_widget.count())
            ],
        )
        current = self.widget.available_list_widget.currentItem()
        self.assertIsNotNone(current)
        self.assertEqual(new_tag.id, current.data(TAG_ID_ROLE))

    def test_new_tag_button_rejected_keeps_list_unchanged(self):
        with patch(
            "ui.dialog_tag_selector.widget.NewTagDialog"
        ) as dialog_class:
            dialog_class.return_value.exec.return_value = (
                QDialog.DialogCode.Rejected
            )
            dialog_class.return_value.new_tag_id = None
            self.widget.new_tag_button.click()

        self.assertEqual(0, self.widget.available_list_widget.count())
        self.assertIsNone(self.widget.available_list_widget.currentItem())


if __name__ == "__main__":
    unittest.main()
