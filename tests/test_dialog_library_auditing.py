import datetime
import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import MagicMock, patch

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PyQt6.QtCore import QRect
from PyQt6.QtWidgets import QApplication

import apppath
import services.global_instances
from blob_storage import BlobStorage
from commons.dto import StickerImage
from services.settings import create_settings_manager
from ui.dialog_library_auditing import (
    SIMILAR_BUTTON_HIDE_TEXT,
    SIMILAR_BUTTON_SHOW_TEXT,
    LibraryAuditingDialog,
)

PNG_BYTES = (
    b"\x89PNG\r\n\x1a\n\x00\x00\x00\rIHDR\x00\x00\x00\x01\x00\x00\x00\x01"
    b"\x08\x06\x00\x00\x00\x1f\x15\xc4\x89\x00\x00\x00\nIDATx\x9cc\x00\x01"
    b"\x00\x00\x05\x00\x01\r\n-\xb4\x00\x00\x00\x00IEND\xaeB`\x82"
)
GIF_BYTES = (
    b"GIF89a\x01\x00\x01\x00\x80\x00\x00\x00\x00\x00\xff\xff\xff!\xf9\x04"
    b"\x01\x00\x00\x00\x00,\x00\x00\x00\x00\x01\x00\x01\x00\x00\x02\x02D"
    b"\x01\x00;"
)


def make_sticker(
    sticker_id: int,
    file_name: str,
    *,
    extension: str = ".png",
):
    sticker = StickerImage()
    sticker.id = sticker_id
    sticker.original_file_name = file_name
    sticker.relative_path = file_name
    sticker.file_size = 1
    sticker.hash = f"{sticker_id:040d}"
    sticker.extension = extension
    sticker.imported_at = datetime.datetime(2026, 1, 1)
    sticker.modification_date = datetime.datetime(2026, 1, 1)
    sticker.size_width = 1
    sticker.size_height = 1
    sticker.vectordb_id = None
    sticker.text_in_image = None
    return sticker


class NavigationStubDB:
    """确定性导航序列的假数据库。

    random_sticker_id 依次弹出 random_queue 中与 excluding 不同的候选；
    next_sticker_id 查 next_results 映射，缺失时返回 None。
    """

    def __init__(self, stickers):
        self.stickers = {sticker.id: sticker for sticker in stickers}
        self.random_queue: list[int] = []
        self.random_calls: list[int | None] = []
        self.next_results: dict[int, int] = {}

    def get_stickers_by_ids(self, sticker_ids):
        return [
            self.stickers[sticker_id]
            for sticker_id in sticker_ids
            if sticker_id in self.stickers
        ]

    def random_sticker_id(self, *, excluding=None):
        self.random_calls.append(excluding)
        while self.random_queue:
            candidate = self.random_queue.pop(0)
            if candidate != excluding:
                return candidate
        return None

    def next_sticker_id(self, after_id):
        return self.next_results.get(after_id)


class LibraryAuditingDialogTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = QApplication.instance() or QApplication([])
        apppath.app_path = Path(__file__).resolve().parents[1] / "src"

    def setUp(self):
        self._old_blob = services.global_instances.current_blob_storage
        self._old_settings = (
            services.global_instances.current_settings_manager
        )
        self._old_main_window = services.global_instances.main_window

        self._blob_dir = tempfile.TemporaryDirectory()
        self.addCleanup(self._blob_dir.cleanup)
        self.blob_storage = BlobStorage(self._blob_dir.name)

        self._settings_dir = tempfile.TemporaryDirectory()
        self.addCleanup(self._settings_dir.cleanup)
        services.global_instances.current_settings_manager = (
            create_settings_manager(
                Path(self._settings_dir.name) / "settings.toml"
            )
        )
        services.global_instances.main_window = None

        # 三张图：2 张静态 png、1 张 gif。
        self.png_a = make_sticker(11, "a.png")
        self.gif = make_sticker(22, "b.gif", extension=".gif")
        self.png_b = make_sticker(33, "c.png")
        self._store_blob_file(self.png_a, PNG_BYTES)
        self._store_blob_file(self.gif, GIF_BYTES)
        self._store_blob_file(self.png_b, PNG_BYTES)

        self.db = NavigationStubDB([self.png_a, self.gif, self.png_b])
        services.global_instances.current_blob_storage = self.blob_storage

    def tearDown(self):
        services.global_instances.current_blob_storage = self._old_blob
        services.global_instances.current_settings_manager = (
            self._old_settings
        )
        services.global_instances.main_window = self._old_main_window
        self.app.processEvents()

    def _store_blob_file(self, sticker: StickerImage, content: bytes):
        source = (
            Path(self._blob_dir.name)
            / f"source-{sticker.id}{sticker.extension}"
        )
        source.write_bytes(content)
        self.blob_storage.store_file(str(source), sticker.hash)

    def _patch_dialog_screen(self, dialog, rect: QRect):
        fake_screen = MagicMock()
        fake_screen.availableGeometry.return_value = rect
        return patch.object(dialog, "screen", return_value=fake_screen)

    def _create_dialog(
        self,
        *,
        database=None,
        initial_random_queue=(),
    ) -> LibraryAuditingDialog:
        db = database if database is not None else self.db
        db.random_queue.extend(initial_random_queue)
        dialog = LibraryAuditingDialog(database=db)
        self.addCleanup(dialog.deleteLater)
        return dialog

    def test_initial_load_uses_random_without_exclusion(self):
        dialog = self._create_dialog(initial_random_queue=[self.gif.id])

        self.assertEqual([self.gif.id], dialog._history)
        self.assertEqual(0, dialog._position)
        self.assertIs(dialog._sticker, self.gif)
        self.assertEqual(
            f"#{self.gif.id} {self.gif.original_file_name}",
            dialog.label.text(),
        )
        self.assertEqual([None], self.db.random_calls)

    def test_navigation_records_history_and_updates_label(self):
        self.db.random_queue.extend([self.png_a.id, self.gif.id])
        self.db.next_results = {
            self.png_a.id: self.gif.id,
            self.gif.id: self.png_b.id,
        }
        dialog = self._create_dialog()

        self.assertEqual([self.png_a.id], dialog._history)

        dialog.pushButtonRand.click()
        self.assertEqual(
            [self.png_a.id, self.gif.id],
            dialog._history,
        )
        self.assertEqual(1, dialog._position)
        self.assertIsNotNone(dialog._movie)  # gif 走 QMovie 路径

        dialog.pushButtonNext.click()
        self.assertEqual(
            [self.png_a.id, self.gif.id, self.png_b.id],
            dialog._history,
        )
        self.assertEqual(2, dialog._position)
        self.assertIsNone(dialog._movie)  # 离开 gif 后 movie 已回收
        self.assertEqual(
            f"#{self.png_b.id} {self.png_b.original_file_name}",
            dialog.label.text(),
        )

    def test_navigation_buttons_show_icons_with_tooltips(self):
        dialog = self._create_dialog(initial_random_queue=[self.png_a.id])

        expectations = {
            "pushButtonPrev": "上一个",
            "pushButtonRand": "随机选择",
            "pushButtonNext": "下一个",
        }
        for name, tooltip in expectations.items():
            button = getattr(dialog, name)
            self.assertEqual(tooltip, button.toolTip())
            self.assertEqual("", button.text())
            self.assertFalse(button.icon().isNull())
            self.assertEqual(24, button.iconSize().width())

    def test_back_walks_history_reverse_without_pushing(self):
        self.db.random_queue.extend([self.png_a.id])
        self.db.next_results = {
            self.png_a.id: self.gif.id,
            self.gif.id: self.png_b.id,
        }
        dialog = self._create_dialog()
        dialog.show()
        self.app.processEvents()
        dialog.pushButtonNext.click()
        dialog.pushButtonNext.click()
        self.assertEqual(2, dialog._position)

        dialog.pushButtonPrev.click()
        self.assertEqual(
            [self.png_a.id, self.gif.id, self.png_b.id], dialog._history
        )
        self.assertEqual(1, dialog._position)
        self.assertEqual(f"#{self.gif.id} b.gif", dialog.label.text())

        dialog.pushButtonPrev.click()
        self.assertEqual(0, dialog._position)
        self.assertEqual(f"#{self.png_a.id} a.png", dialog.label.text())

        # 已在历史起点：继续后退是 no-op。
        dialog.pushButtonPrev.click()
        self.assertEqual(0, dialog._position)
        self.assertEqual(f"#{self.png_a.id} a.png", dialog.label.text())
        self.assertEqual(
            [self.png_a.id, self.gif.id, self.png_b.id], dialog._history
        )

    def test_next_wrapping_to_self_keeps_history_single_entry(self):
        single_db = NavigationStubDB([self.png_a])
        dialog = self._create_dialog(
            database=single_db,
            initial_random_queue=[self.png_a.id],
        )

        self.assertEqual([self.png_a.id], dialog._history)

        dialog.pushButtonNext.click()
        self.assertEqual([self.png_a.id], dialog._history)
        self.assertEqual(0, dialog._position)
        self.assertEqual(f"#{self.png_a.id} a.png", dialog.label.text())

    def test_random_none_is_silent_noop(self):
        dialog = self._create_dialog(initial_random_queue=[self.png_a.id])

        dialog.pushButtonRand.click()  # 排除当前 id 后没有其他图片

        self.assertEqual([self.png_a.id], dialog._history)
        self.assertEqual(0, dialog._position)
        self.assertEqual(f"#{self.png_a.id} a.png", dialog.label.text())
        self.assertEqual([None, self.png_a.id], self.db.random_calls)

    def test_similar_pane_fetches_lazily(self):
        png_d = make_sticker(44, "d.png")
        self._store_blob_file(png_d, PNG_BYTES)
        self.db.stickers[png_d.id] = png_d
        self.db.next_results = {
            self.png_a.id: self.gif.id,
            self.gif.id: self.png_b.id,
            self.png_b.id: png_d.id,
            png_d.id: self.png_a.id,
        }
        dialog = self._create_dialog(initial_random_queue=[self.png_a.id])
        dialog.show()
        self.app.processEvents()
        self.assertFalse(dialog.widgetSimilarImages.isVisible())
        self.assertIsNone(dialog._similar_page)

        fetch = MagicMock(return_value=([], {}))
        with patch(
            "services.sticker_library_viewer_service.fetch_similar_candidates",
            fetch,
        ):
            # 窗格隐藏：连续导航不触发向量查询。
            dialog.pushButtonNext.click()
            dialog.pushButtonNext.click()
            self.assertEqual(0, fetch.call_count)

            # 首次展开：恰好刷新 1 次。
            dialog.pushButtonShowHideSimilarImages.click()
            self.assertEqual(1, fetch.call_count)
            self.assertIsNotNone(dialog._similar_page)

            # 可见状态下每次导航都刷新。
            dialog.pushButtonNext.click()
            dialog.pushButtonNext.click()
            self.assertEqual(3, fetch.call_count)

            # 关闭后导航零查询；重新展开补一次刷新。
            dialog.pushButtonShowHideSimilarImages.click()
            dialog.pushButtonNext.click()
            self.assertEqual(3, fetch.call_count)
            dialog.pushButtonShowHideSimilarImages.click()
            self.assertEqual(4, fetch.call_count)

        self.assertTrue(
            dialog.widgetSimilarImages.isVisibleTo(dialog.widgetImageViewer)
        )

    def test_similar_button_text_follows_visibility(self):
        dialog = self._create_dialog(initial_random_queue=[self.png_a.id])
        dialog.show()
        self.app.processEvents()

        self.assertEqual(
            SIMILAR_BUTTON_SHOW_TEXT,
            dialog.pushButtonShowHideSimilarImages.text(),
        )

        dialog.pushButtonShowHideSimilarImages.click()
        self.assertTrue(dialog.widgetSimilarImages.isVisible())
        self.assertEqual(
            SIMILAR_BUTTON_HIDE_TEXT,
            dialog.pushButtonShowHideSimilarImages.text(),
        )

        dialog.pushButtonShowHideSimilarImages.click()
        self.assertFalse(dialog.widgetSimilarImages.isVisible())
        self.assertEqual(
            SIMILAR_BUTTON_SHOW_TEXT,
            dialog.pushButtonShowHideSimilarImages.text(),
        )

    def test_showing_similar_pane_doubles_window_and_splits_space(self):
        dialog = self._create_dialog(initial_random_queue=[self.png_a.id])
        dialog.show()
        self.app.processEvents()
        original_width = dialog.width()

        with self._patch_dialog_screen(dialog, QRect(0, 0, 4096, 2160)):
            dialog.pushButtonShowHideSimilarImages.click()

            self.assertEqual(original_width * 2, dialog.width())
            left, right = dialog.splitterLeftRight.sizes()
            self.assertGreater(right, 0)
            self.assertAlmostEqual(0.5, right / (left + right), delta=0.05)

            dialog.pushButtonShowHideSimilarImages.click()
            self.assertEqual(original_width, dialog.width())

    def test_fit_only_moves_window_back_onto_positive_origin_screen(self):
        dialog = self._create_dialog(initial_random_queue=[self.png_a.id])
        dialog.setGeometry(700, 500, 300, 200)

        with self._patch_dialog_screen(dialog, QRect(0, 0, 800, 600)):
            dialog._fit_geometry_into_screen()

        self.assertEqual((500, 400), (dialog.x(), dialog.y()))
        # 只移动不缩放：尺寸保持原样。
        self.assertEqual((300, 200), (dialog.width(), dialog.height()))

    def test_fit_handles_negative_multi_monitor_coordinates(self):
        dialog = self._create_dialog(initial_random_queue=[self.png_a.id])
        # 左侧副屏可用区域 x ∈ [-1920, -1]；窗口右缘 -300+708-1 == 407 超出。
        dialog.setGeometry(-300, 100, 708, 584)

        with self._patch_dialog_screen(dialog, QRect(-1920, 0, 1920, 1080)):
            dialog._fit_geometry_into_screen()

        self.assertEqual(-708, dialog.x())  # 右缘平移回 -1 恰好贴边
        self.assertEqual(100, dialog.y())  # 纵向本来就放得下，保持不动
        self.assertEqual((708, 584), (dialog.width(), dialog.height()))

    def test_show_defers_screen_fit_until_after_placement(self):
        dialog = self._create_dialog(initial_random_queue=[self.png_a.id])
        dialog.move(5000, 5000)

        with self._patch_dialog_screen(dialog, QRect(0, 0, 1600, 1200)):
            dialog.show()
            # 平台摆放窗口发生在 show 之后；showEvent 里安排的零延时平移
            # 会在事件循环中把它拉回屏幕内。
            self.app.processEvents()
            self.app.processEvents()

        self.assertEqual(892, dialog.x())  # 1599 - 708 + 1，右缘贴住屏幕
        self.assertEqual(372, dialog.y())  # 1199 - 828 + 1
        self.assertLessEqual(dialog.x() + dialog.width() - 1, 1599)
        self.assertLessEqual(dialog.y() + dialog.height() - 1, 1199)

    def test_expanding_similar_pane_shifts_window_back_on_screen(self):
        dialog = self._create_dialog(initial_random_queue=[self.png_a.id])
        dialog.show()
        self.app.processEvents()
        original_width = dialog.width()

        with self._patch_dialog_screen(dialog, QRect(0, 0, 1600, 600)):
            dialog.move(1000, 0)
            dialog.pushButtonShowHideSimilarImages.click()

            doubled_width = dialog.width()
            available = QRect(0, 0, 1600, 600)
            self.assertEqual(original_width * 2, doubled_width)
            # 窗口被平移回屏幕内，右缘恰好贴住可用区域右边界。
            self.assertEqual(
                available.right() - doubled_width + 1, dialog.x()
            )
            self.assertGreaterEqual(dialog.x(), available.left())
            _, right = dialog.splitterLeftRight.sizes()
            self.assertGreater(right, 0)

    def test_missing_vector_clears_list_without_crashing(self):
        dialog = self._create_dialog(initial_random_queue=[self.png_a.id])
        dialog.show()
        self.app.processEvents()

        with patch(
            "services.sticker_library_viewer_service.fetch_similar_candidates",
            MagicMock(side_effect=ValueError("该图片还没有特征向量。")),
        ):
            dialog.pushButtonShowHideSimilarImages.click()

        page = dialog._similar_page
        self.assertIsNotNone(page)
        self.assertEqual(0, page.listViewStickerList.model().rowCount())

        # 对话框仍可正常导航。
        dialog.pushButtonRand.click()
        self.assertIn("#", dialog.label.text())


if __name__ == "__main__":
    unittest.main()
