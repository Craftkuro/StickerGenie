# coding=utf-8
import datetime
import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PIL import Image
from PyQt6.QtCore import QPoint, Qt
from PyQt6.QtGui import QContextMenuEvent, QImage
from PyQt6.QtWidgets import QApplication, QFileDialog, QMenu, QMessageBox

import apppath
from commons.dto import StickerImage
from ui.dialog_image_viewer import ImageViewerDialog


def make_sticker(name="viewer.png", extension=".png") -> StickerImage:
    sticker = StickerImage()
    sticker.original_file_name = name
    sticker.file_size = 1
    sticker.hash = "viewer-context-menu-hash"
    sticker.extension = extension
    sticker.imported_at = datetime.datetime(2026, 1, 1)
    sticker.modification_date = datetime.datetime(2026, 1, 1)
    sticker.size_width = 1
    sticker.size_height = 1
    sticker.vectordb_id = None
    sticker.text_in_image = None
    return sticker


def make_animated_gif(path: Path) -> None:
    first = Image.new("RGBA", (2, 2), "red")
    second = Image.new("RGBA", (2, 2), "blue")
    first.save(
        path,
        save_all=True,
        append_images=[second],
        duration=50,
        loop=0,
    )


class ImageViewerContextMenuTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = QApplication.instance() or QApplication([])
        source_dir = Path(__file__).resolve().parents[1] / "src"
        apppath.setup_data_path(source_dir)

    def setUp(self):
        self._temp_dir = tempfile.TemporaryDirectory()
        self.image_path = Path(self._temp_dir.name) / "viewer.png"
        image = QImage(2, 2, QImage.Format.Format_ARGB32)
        image.fill(0xFFFFFFFF)
        self.assertTrue(image.save(str(self.image_path)))
        self.sticker = make_sticker()
        self.dialog = ImageViewerDialog(database=None)
        self.dialog.load_image(str(self.image_path), "", self.sticker)

    def tearDown(self):
        self.dialog.close()
        self._temp_dir.cleanup()

    def _trigger_menu_action(self, action_text: str) -> list[str]:
        actions = []

        def fake_exec(menu, _position):
            actions[:] = [action.text() for action in menu.actions()]
            action = next(
                action for action in menu.actions() if action.text() == action_text
            )
            action.trigger()
            return None

        with patch.object(QMenu, "exec", fake_exec):
            self.dialog._show_image_context_menu(QPoint(0, 0))
        return actions

    def test_context_menu_offers_copy_and_save_as_for_static_image(self):
        with patch(
            "services.image_clipboard_service.copy_image_to_clipboard"
        ) as copy_mock:
            actions = self._trigger_menu_action("复制到剪贴板")

        self.assertEqual(["复制到剪贴板", "另存为"], actions)
        copy_mock.assert_called_once_with(
            str(self.image_path),
            "viewer.png",
            anim_as_static_image=False,
        )

    def test_context_menu_offers_first_frame_copy_for_gif(self):
        gif_path = Path(self._temp_dir.name) / "animated.gif"
        make_animated_gif(gif_path)
        self.sticker.original_file_name = "animated.gif"
        self.sticker.extension = ".gif"
        self.dialog.load_image(str(gif_path), "", self.sticker)

        with patch(
            "services.image_clipboard_service.copy_image_to_clipboard"
        ) as copy_mock:
            actions = self._trigger_menu_action("复制首帧到剪贴板")

        self.assertEqual(
            ["复制到剪贴板", "复制首帧到剪贴板", "另存为"],
            actions,
        )
        copy_mock.assert_called_once_with(
            str(gif_path),
            "animated.gif",
            anim_as_static_image=True,
        )

    def test_save_as_writes_selected_destination(self):
        destination = Path(self._temp_dir.name) / "saved.png"
        with patch.object(
            QFileDialog,
            "getSaveFileName",
            return_value=(str(destination), ""),
        ):
            with patch.object(QMessageBox, "information") as info_mock:
                self.dialog._save_current_image_as()

        self.assertTrue(destination.is_file())
        info_mock.assert_called_once_with(
            self.dialog,
            "另存为成功",
            "图片已保存。",
        )

    def test_save_as_cancel_does_not_copy(self):
        with patch.object(
            QFileDialog,
            "getSaveFileName",
            return_value=("", ""),
        ):
            with patch("ui.dialog_image_viewer.save_as_files") as save_mock:
                self.dialog._save_current_image_as()

        save_mock.assert_not_called()

    def test_context_menu_uses_custom_context_menu_policy(self):
        self.assertEqual(
            Qt.ContextMenuPolicy.CustomContextMenu,
            self.dialog.widgetImageViewer.contextMenuPolicy(),
        )

    def test_context_menu_without_image_does_not_exec(self):
        empty_dialog = ImageViewerDialog(database=None)
        self.addCleanup(empty_dialog.close)
        with patch.object(QMenu, "exec") as exec_mock:
            empty_dialog._show_image_context_menu(QPoint(0, 0))

        exec_mock.assert_not_called()

    def test_right_click_on_viewport_opens_context_menu(self):
        with patch.object(QMenu, "exec") as exec_mock:
            event = QContextMenuEvent(
                QContextMenuEvent.Reason.Mouse,
                QPoint(10, 10),
                self.dialog.widgetImageViewer.viewport().mapToGlobal(
                    QPoint(10, 10)
                ),
            )
            QApplication.sendEvent(
                self.dialog.widgetImageViewer.viewport(),
                event,
            )
            QApplication.processEvents()

        exec_mock.assert_called_once()


if __name__ == "__main__":
    unittest.main()
