import datetime
import os
import unittest
from unittest.mock import MagicMock, patch

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PyQt6.QtCore import QDateTime
from PyQt6.QtWidgets import (
    QApplication,
    QDialog,
    QDialogButtonBox,
    QMessageBox,
)

import apppath
from commons.dto import StickerImage
from ui.dialog_library_editing_props_edit import LibraryEditingPropsEditDialog


def make_sticker(sticker_id: int = 7) -> StickerImage:
    sticker = StickerImage()
    sticker.id = sticker_id
    sticker.original_file_name = "old name.png"
    sticker.file_size = 123
    sticker.hash = "0" * 40
    sticker.extension = ".png"
    sticker.imported_at = datetime.datetime(2026, 1, 1)
    sticker.modification_date = datetime.datetime(2025, 12, 31, 23, 59)
    sticker.size_width = 10
    sticker.size_height = 20
    sticker.vectordb_id = None
    sticker.text_in_image = None
    return sticker


class StubDB:
    def __init__(self, sticker: StickerImage):
        self.sticker = sticker
        self.calls: list[dict] = []

    def update_sticker_file_properties(self, sticker_id, **kwargs):
        self.calls.append({"sticker_id": sticker_id, **kwargs})
        updated = make_sticker(sticker_id)
        if kwargs.get("original_file_name") is not None:
            updated.original_file_name = kwargs["original_file_name"]
        if kwargs.get("modification_date") is not None:
            updated.modification_date = kwargs["modification_date"]
        self.sticker = updated
        return updated


class LibraryEditingPropsEditDialogTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = QApplication.instance() or QApplication([])
        apppath.app_path = apppath.app_path  # 占位保持一致性

    def setUp(self):
        self.sticker = make_sticker()
        self.db = StubDB(self.sticker)

    def _create_dialog(self) -> LibraryEditingPropsEditDialog:
        dialog = LibraryEditingPropsEditDialog(
            database=self.db,
            sticker=self.sticker,
        )
        self.addCleanup(dialog.deleteLater)
        return dialog

    def test_fields_are_prefilled_from_sticker(self):
        dialog = self._create_dialog()

        self.assertEqual("old name.png", dialog.lineEditFileName.text())
        self.assertEqual(
            QDateTime(self.sticker.modification_date),
            dialog.dateTimeEditModification.dateTime(),
        )

    def test_ok_saves_stripped_values_and_accepts(self):
        dialog = self._create_dialog()
        dialog.lineEditFileName.setText("  新名字.png ")
        dialog.show()

        ok_button = dialog.buttonBox.button(
            QDialogButtonBox.StandardButton.Ok
        )
        ok_button.click()

        self.assertEqual(
            [
                {
                    "sticker_id": self.sticker.id,
                    "original_file_name": "新名字.png",
                    "modification_date": QDateTime(
                        datetime.datetime(2025, 12, 31, 23, 59)
                    ).toPyDateTime(),
                }
            ],
            self.db.calls,
        )
        self.assertEqual(QDialog.DialogCode.Accepted, dialog.result())
        self.assertEqual("新名字.png", dialog.updated_sticker().original_file_name)

    def test_blank_name_warns_and_keeps_dialog_open(self):
        dialog = self._create_dialog()
        dialog.lineEditFileName.setText("   ")
        dialog.show()

        with patch.object(QMessageBox, "warning") as warning:
            ok_button = dialog.buttonBox.button(
                QDialogButtonBox.StandardButton.Ok
            )
            ok_button.click()

        warning.assert_called_once()
        self.assertEqual([], self.db.calls)
        self.assertTrue(dialog.isVisible())
        self.assertIsNone(dialog.updated_sticker())

    def test_cancel_rejects_without_saving(self):
        dialog = self._create_dialog()
        dialog.show()

        cancel_button = dialog.buttonBox.button(
            QDialogButtonBox.StandardButton.Cancel
        )
        cancel_button.click()

        self.assertEqual([], self.db.calls)
        self.assertEqual(QDialog.DialogCode.Rejected, dialog.result())

    def test_database_error_is_reported_and_dialog_stays_open(self):
        failing_db = MagicMock()
        failing_db.update_sticker_file_properties.side_effect = ValueError(
            "不存在的表情包"
        )
        dialog = LibraryEditingPropsEditDialog(
            database=failing_db,
            sticker=self.sticker,
        )
        dialog.show()
        self.addCleanup(dialog.deleteLater)

        with patch.object(QMessageBox, "critical") as critical:
            ok_button = dialog.buttonBox.button(
                QDialogButtonBox.StandardButton.Ok
            )
            ok_button.click()

        critical.assert_called_once()
        self.assertTrue(dialog.isVisible())
        self.assertIsNone(dialog.updated_sticker())


if __name__ == "__main__":
    unittest.main()
