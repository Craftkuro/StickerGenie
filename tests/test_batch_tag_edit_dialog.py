import datetime
import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PyQt6.QtCore import QItemSelectionModel
from PyQt6.QtWidgets import QApplication, QDialog, QMessageBox

import apppath
from commons.dto import StickerImage, Tag
from stickerdb.v1.sticker_db import StickerDBV1
from ui.dialog_batch_tag_edit import BatchTagEditDialog


def make_tag(name: str) -> Tag:
    tag = Tag()
    tag.name = name
    tag.enabled = True
    tag.color_rgb = "#2196F3"
    return tag


def make_sticker(hash_value: str, tags=()) -> StickerImage:
    sticker = StickerImage()
    sticker.original_file_name = f"{hash_value}.png"
    sticker.relative_path = sticker.original_file_name
    sticker.file_size = 1
    sticker.hash = hash_value
    sticker.extension = ".png"
    sticker.imported_at = datetime.datetime(2026, 1, 1)
    sticker.modification_date = datetime.datetime(2026, 1, 1)
    sticker.size_width = 1
    sticker.size_height = 1
    sticker.vectordb_id = None
    sticker.text_in_image = None
    sticker.tags = list(tags)
    return sticker


class StubTagSelectorDialog:
    def __init__(self, *, result, tags, **_kwargs):
        self._result = result
        self._tags = list(tags)

    def exec(self):
        return self._result

    def selected_tags(self):
        return list(self._tags)


class BatchTagEditDialogTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = QApplication.instance() or QApplication([])
        source_dir = Path(__file__).resolve().parents[1] / "src"
        apppath.setup_data_path(source_dir)

    def setUp(self):
        self._temp_dir = tempfile.TemporaryDirectory()
        self.db = StickerDBV1(str(Path(self._temp_dir.name) / "library.db"))
        self.first = self.db.add_or_modify_tag(make_tag("First"))
        self.second = self.db.add_or_modify_tag(make_tag("Second"))
        self.stickers = self.db.add_stickers(
            [
                make_sticker("dialog-one", [self.first]),
                make_sticker("dialog-two"),
            ]
        )
        self.dialog = BatchTagEditDialog(self.stickers, database=self.db)

    def tearDown(self):
        self.dialog.close()
        self.db.engine.dispose()
        self._temp_dir.cleanup()

    def patch_selector(self, *, result, tags):
        return patch(
            "ui.dialog_batch_tag_edit.TagSelectorDialog",
            return_value=StubTagSelectorDialog(result=result, tags=tags),
        )

    def test_action_label_follows_selected_radio_button(self):
        self.assertEqual("将要增加的标签列表：", self.dialog.labelTagList.text())

        self.dialog.radioButtonRemoveTags.click()

        self.assertEqual("将要删除的标签列表：", self.dialog.labelTagList.text())

    def test_add_action_uses_tag_selector_and_deduplicates(self):
        with self.patch_selector(
            result=QDialog.DialogCode.Accepted,
            tags=[self.second, self.second],
        ):
            self.dialog._add_tags()

        self.assertEqual([self.second.id], self.dialog.selected_tag_ids())
        self.assertEqual(["Second"], [tag.name for tag in self.dialog.selected_tags()])

    def test_delete_action_removes_selected_pending_tags(self):
        with self.patch_selector(
            result=QDialog.DialogCode.Accepted,
            tags=[self.first, self.second],
        ):
            self.dialog._add_tags()

        index = self.dialog._tag_model.index(0, 0)
        self.dialog._tag_widget._list_view.selectionModel().select(
            index,
            QItemSelectionModel.SelectionFlag.Select,
        )
        self.dialog._delete_selected_tags()

        self.assertEqual([self.second.id], self.dialog.selected_tag_ids())

    def test_confirm_updates_database_emits_dtos_and_closes_after_message(self):
        with self.patch_selector(
            result=QDialog.DialogCode.Accepted,
            tags=[self.second],
        ):
            self.dialog._add_tags()

        updated_batches = []
        self.dialog.tags_updated.connect(updated_batches.append)
        with patch(
            "ui.dialog_batch_tag_edit.QMessageBox.information"
        ) as information:
            self.dialog._confirm()

        self.assertEqual(QDialog.DialogCode.Accepted, self.dialog.result())
        information.assert_called_once_with(
            self.dialog,
            "批量编辑标签",
            "已完成操作，共修改2张图片。",
        )
        self.assertEqual(1, len(updated_batches))
        self.assertEqual(2, len(updated_batches[0]))
        self.assertEqual(
            [{self.first.id, self.second.id}, {self.second.id}],
            [
                {tag.id for tag in sticker.tags}
                for sticker in self.db.list_stickers()
            ],
        )

    def test_confirm_without_tags_keeps_dialog_open(self):
        with patch(
            "ui.dialog_batch_tag_edit.QMessageBox.warning"
        ) as warning:
            self.dialog._confirm()

        warning.assert_called_once_with(
            self.dialog,
            "无法编辑标签",
            "请至少选择一个标签。",
        )
        self.assertEqual(QDialog.DialogCode.Rejected, self.dialog.result())


if __name__ == "__main__":
    unittest.main()
