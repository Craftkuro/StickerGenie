import os
import unittest
from pathlib import Path
from tempfile import TemporaryDirectory

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PyQt6.QtWidgets import QApplication, QDialog

from commons.dto import Tag
from stickerdb.v1.sticker_db import StickerDBV1
from ui.dialog_tag_selector import TagSelectorDialog


def make_tag(name: str) -> Tag:
    tag = Tag()
    tag.name = name
    tag.enabled = True
    tag.color_rgb = "#2196F3"
    return tag


class TagSelectorDialogTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = QApplication.instance() or QApplication([])

    def setUp(self):
        self._temp_dir = TemporaryDirectory()
        self.db = StickerDBV1(str(Path(self._temp_dir.name) / "library.db"))
        self.alpha = self.db.add_or_modify_tag(make_tag("Alpha"))
        self.beta = self.db.add_or_modify_tag(make_tag("Beta"))
        self.dialog = TagSelectorDialog(database=self.db)

    def tearDown(self):
        self.dialog.close()
        self.db.engine.dispose()
        self._temp_dir.cleanup()

    def item_names(self, list_widget):
        return [
            list_widget.item(row).text()
            for row in range(list_widget.count())
        ]

    def test_default_window_title(self):
        self.assertEqual("选择标签", self.dialog.windowTitle())

    def test_custom_window_title(self):
        dialog = TagSelectorDialog(database=self.db, window_title="编辑标签")
        try:
            self.assertEqual("编辑标签", dialog.windowTitle())
        finally:
            dialog.close()

    def test_initial_selection_shows_in_selected_list(self):
        dialog = TagSelectorDialog(
            database=self.db,
            selected_tag_ids=[self.alpha.id, self.beta.id],
        )
        try:
            self.assertEqual(
                ["Alpha", "Beta"],
                self.item_names(dialog.selector.selected_list_widget),
            )
            self.assertEqual(
                [self.alpha.id, self.beta.id],
                dialog.selected_tag_ids(),
            )
        finally:
            dialog.close()

    def test_ok_button_accepts_dialog_and_returns_selection(self):
        self.dialog.selector.available_list_widget.item(0).setSelected(True)
        self.dialog.selector.add_button.click()
        self.dialog.selector.ok_button.click()

        self.assertEqual(QDialog.DialogCode.Accepted, self.dialog.result())
        self.assertEqual([self.alpha.id], self.dialog.selected_tag_ids())

    def test_selected_tags_returns_tag_objects_in_display_order(self):
        dialog = TagSelectorDialog(
            database=self.db,
            selected_tag_ids=[self.beta.id, self.alpha.id],
        )
        try:
            tags = dialog.selected_tags()
            self.assertEqual(
                [self.alpha.name, self.beta.name],
                [tag.name for tag in tags],
            )
        finally:
            dialog.close()


if __name__ == "__main__":
    unittest.main()
