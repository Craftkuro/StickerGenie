# coding=utf-8
import logging
from datetime import datetime
from pathlib import Path
from typing import Optional

from PyQt6 import uic
from PyQt6.QtCore import QTimer, Qt
from PyQt6.QtGui import QMovie, QPixmap, QStandardItem, QStandardItemModel
from PyQt6.QtWidgets import (
    QAbstractItemView,
    QDialog,
    QHeaderView,
    QMessageBox,
    QTableWidgetItem,
)
from sqlalchemy.exc import SQLAlchemyError

import apppath
import services.global_instances
import services.sticker_library_viewer_service
from blob_storage import BlobFileEntity
from commons.dto import StickerImage, Tag

from .dialog_library_editing_props_edit import LibraryEditingPropsEditDialog
from .page_similar_images import SimilarImagesPage
from .widgets.custom_tag_widget import CustomTagWidget, TAG_ACCENT_COLOR_ROLE

logger = logging.getLogger(__name__)

WINDOW_TITLE = "图库审阅"
TAG_DATA_ROLE = Qt.ItemDataRole.UserRole
SIMILAR_BUTTON_SHOW_TEXT = "查看相似图片>>"
SIMILAR_BUTTON_HIDE_TEXT = "<<隐藏相似图片"


class LibraryAuditingDialog(QDialog):
    """
    图库审阅对话框：左侧浏览大图，右侧按需展开相似图片窗格。

    导航语义（随机/前进）由数据库层保证有效，本对话框只表达导航意图，
    不做存在性探测和重试。
    """

    def __init__(self, parent=None, database=None):
        super().__init__(parent)

        self._database = (
            database
            if database is not None
            else services.global_instances.current_library_db
        )
        self._sticker: Optional[StickerImage] = None
        self._movie: Optional[QMovie] = None
        self._file_path: Optional[str] = None
        self._history: list[int] = []   # 浏览过的 id 序列
        self._position: int = -1        # 当前在 _history 中的下标
        self._similar_page: Optional[SimilarImagesPage] = None
        self._similar_stale: bool = True

        ui_file_path = apppath.app_path / 'ui' / 'dialog_library_auditing.ui'
        uic.loadUi(ui_file_path, self)

        self.setWindowTitle(WINDOW_TITLE)
        self.setWindowFlags(
            self.windowFlags() | Qt.WindowType.WindowMaximizeButtonHint
        )

        # 强制激活第1个标签，以免编辑ui文件的时候存入其他激活的标签影响程序运行时行为
        self.tabWidgetBottom.setCurrentIndex(0)

        # 相似图片窗格默认展开，窗口按双倍宽度打开。
        self.widgetSimilarImages.setVisible(True)
        self.pushButtonShowHideSimilarImages.setText(SIMILAR_BUTTON_HIDE_TEXT)
        self.resize(self.width() * 2, self.height())

        self.pushButtonPrev.clicked.connect(self._go_back)
        self.pushButtonRand.clicked.connect(self._go_random)
        self.pushButtonNext.clicked.connect(self._go_next)
        self.pushButtonShowHideSimilarImages.clicked.connect(
            self._toggle_similar_images
        )
        self.pushButtonEditProperties.clicked.connect(
            self._open_property_editor
        )

        # 图片在别处被删除时修剪浏览历史，避免导航卡死在死条目上。
        services.sticker_library_viewer_service.wiring.signal_stickers_deleted.connect(
            self._prune_history
        )

        self.imageTextEditWidget.set_database(self._database)
        self._init_tag_editor()
        self._init_file_info_table()

        initial_id = self._database.random_sticker_id()
        if initial_id is not None:
            self._navigate_to(initial_id)
            # 构造期对话框尚未显示，_show_sticker 只会标记 stale；
            # 窗格默认展开，这里显式补上首次相似图片加载。
            self._refresh_similar_images()

    # ==================== 导航 ====================

    def _current_id(self) -> Optional[int]:
        if 0 <= self._position < len(self._history):
            return self._history[self._position]
        return None

    def _go_back(self):
        """回退到上一张看过的图。

        已经退到最早一条历史、或者上一张图已被删除时，停在原地不动。
        """
        if self._position <= 0:
            return
        # 先确认上一张还在，再去移动指针。顺序反过来的话，一旦上一张
        # 恰好已被删除，指针就会停在一张不存在的图上，之后每次点
        # “上一个”都会卡在同一处。
        target_id = self._history[self._position - 1]
        stickers = self._database.get_stickers_by_ids([target_id])
        if not stickers:
            logger.warning("历史里的上一张图片已不存在，id=%s", target_id)
            return
        self._position -= 1
        self._show_sticker(stickers[0])

    def _go_random(self):
        current_id = self._current_id()
        next_id = self._database.random_sticker_id(excluding=current_id)
        if next_id is None:
            return
        self._navigate_to(next_id)

    def _go_next(self):
        current_id = self._current_id()
        if current_id is None:
            return
        next_id = self._database.next_sticker_id(current_id)
        if next_id is None:
            return
        self._navigate_to(next_id)

    def _navigate_to(self, new_id: int):
        if new_id == self._current_id():
            return                      # 单图库前进回绕到自己：不重复入栈
        stickers = self._database.get_stickers_by_ids([new_id])
        if not stickers:
            # 理论不可达：id 来自数据库层，保证有效。
            logger.warning("目标图片不存在，id=%s", new_id)
            return
        del self._history[self._position + 1:]
        self._history.append(new_id)
        self._position += 1
        self._show_sticker(stickers[0])

    def _prune_history(self, deleted_ids: list) -> None:
        """收到删除广播后，把刚删掉的图片从浏览历史里清除。

        这是 Qt 槽函数：内部出错时只记日志、不往外抛。PyQt6 的规矩是
        槽函数里冒出未捕获的异常会直接把整个程序带崩。
        """
        try:
            self._apply_history_prune(set(deleted_ids))
        except Exception:
            logger.exception("修剪审阅历史失败")

    def _apply_history_prune(self, deleted: set) -> None:
        """从 _history 里去掉被删的 id，并让画面指向一张仍然存在的图。

        _history 按先后顺序记录看过的图片 id，_position 指向当前显示
        的那张。删掉其中一些 id 之后要保证两件事：

        1. 剩下的 id 保持原来的先后顺序（相当于从列表里抠掉几项）；
        2. _position 要么继续指着原来那张图，要么在原来那张恰好被删
           时改指到别处。

        :param deleted: 本次广播中所有被删图片的 id 集合
        """
        # 如果广播里的 id 一张都没看过，不用动。
        if not deleted.intersection(self._history):
            return

        current_id = self._current_id()

        # 数一数当前这张图的左边有几张会被删掉。
        # 左边每少一张，当前这张在新列表里的位置就往前挪一格，
        # 所以这个数字也是稍后指针需要左移的格数。
        removed_on_left = sum(
            1 for i in self._history[: self._position] if i in deleted
        )

        # 从列表里抠掉所有被删的 id。列表推导式按原顺序遍历，
        # 剩下的元素顺序自然不变。
        self._history = [i for i in self._history if i not in deleted]

        # 情况一：正在看的图没被删。
        # 只需把指针左移 removed_on_left 格，它仍然指着原来那张图。
        #
        # 例：历史 [A, B, C]，正在看 C（下标 2），删掉 B。
        # 新历史 [A, C]，指针从 2 挪到 1，指的还是 C。
        if current_id is not None and current_id not in deleted:
            self._position -= removed_on_left
            return

        # 情况二：正在看的图被删了，得换一张来显示。
        # 优先显示历史上离它最近的前一张；如果前几张连着都被删了，
        # 就再往前找。位置这样算：
        #   self._position - removed_on_left 是当前这张在新列表里
        #     “本应待”的位置；
        #   再减 1 就是它前面那格。
        #   new_position 小于 0 说明它本来就是最早的一张、前面没有
        #     更早的了，退而求其次看新列表的第一张；
        #   最后 min 保证不超出列表末尾。
        #
        # 例：历史 [A, B, C]，正在看 B（下标 1），删掉 B。
        # 新历史 [A, C]，1 - 0 - 1 = 0，改看 A。
        new_position = self._position - removed_on_left - 1
        if new_position < 0:
            new_position = 0
        if self._history:
            self._position = min(new_position, len(self._history) - 1)
            stickers = self._database.get_stickers_by_ids(
                [self._history[self._position]]
            )
            if stickers:
                self._show_sticker(stickers[0])
                return

        # 情况三：历史已经被删空（或者极端情况下取不到图）。
        # 从库里随机跳一张当作新的浏览起点；整个图库都没有图可看时，
        # 清空画面进入空白态。
        self._position = -1
        random_id = self._database.random_sticker_id()
        if random_id is not None:
            # 此时 _history 为空且 _position 为 -1，
            # _navigate_to 正好会把这张随机图作为第一条历史压入。
            self._navigate_to(random_id)
            return
        self._blank_view()

    def _blank_view(self) -> None:
        """清空左侧大图区域，显示空白。"""
        self._stop_movie()
        self.graphicsView.set_image(QPixmap())
        self._sticker = None
        self._file_path = None
        self.label.setText("")
        self._reload_tag_model()

    # ==================== 左侧查看器 ====================

    def _show_sticker(self, sticker: StickerImage):
        current_blob_storage = services.global_instances.current_blob_storage
        try:
            file_path = current_blob_storage.read_file(
                BlobFileEntity(sticker.hash, sticker.extension)
            )
        except FileNotFoundError:
            logger.warning("图片文件不存在，id=%s", sticker.id)
            QMessageBox.warning(self, "无法打开", "图片文件不存在或已被移动。")
            return

        self._stop_movie()
        pixmap = QPixmap(file_path)
        if Path(file_path).suffix.lower() == ".gif":
            movie = QMovie(file_path)
            self._movie = movie
            self.graphicsView.set_movie(movie)
            if not movie.isValid():
                self._movie = None
                self.graphicsView.set_image(pixmap)
        else:
            self.graphicsView.set_image(pixmap)
        if pixmap.isNull():
            logger.warning("无法加载图片: %s", file_path)

        self._sticker = sticker
        self._file_path = file_path
        self.label.setText(f"#{sticker.id} {sticker.original_file_name}")

        self.imageTextEditWidget.set_sticker(sticker)
        self._reload_tag_model()
        self._reload_file_info(file_path, pixmap)

        if (
            self._similar_page is not None
            and self.widgetSimilarImages.isVisible()
        ):
            self._refresh_similar_images()
        else:
            self._similar_stale = True

    def closeEvent(self, event):
        self._stop_movie()
        super().closeEvent(event)

    def showEvent(self, event):
        super().showEvent(event)
        # 平台会在显示之后才自行摆放窗口，因此延迟到事件循环里再调整。
        QTimer.singleShot(0, self._after_shown)

    def _after_shown(self):
        self._fit_geometry_into_screen()
        if self.widgetSimilarImages.isVisible():
            self._split_similar_pane_evenly()

    def _split_similar_pane_evenly(self):
        splitter_width = max(self.splitterLeftRight.width(), 2)
        half = splitter_width // 2
        self.splitterLeftRight.setSizes([half, splitter_width - half])

    def _fit_geometry_into_screen(self):
        """把窗口平移回当前屏幕的可用区域，避免右半边超出屏幕。

        只移动不缩放：Qt 已把顶层窗口最大尺寸限制到屏幕尺寸。
        多显示器下屏幕原点可能是负值，因此一律以屏幕可用几何计算边界。
        """
        if self.isMaximized():
            return
        screen = self.screen()
        if screen is None:
            return
        available = screen.availableGeometry()
        x = min(self.x(), available.right() - self.width() + 1)
        y = min(self.y(), available.bottom() - self.height() + 1)
        self.move(max(x, available.left()), max(y, available.top()))

    def _stop_movie(self) -> None:
        movie = self._movie
        if movie is not None:
            movie.stop()
            movie.setFileName("")
            movie.deleteLater()
            self._movie = None

    # ==================== 相似图片窗格 ====================

    def _toggle_similar_images(self):
        visible = not self.widgetSimilarImages.isVisible()
        self.widgetSimilarImages.setVisible(visible)
        self.pushButtonShowHideSimilarImages.setText(
            SIMILAR_BUTTON_HIDE_TEXT if visible else SIMILAR_BUTTON_SHOW_TEXT
        )
        if visible:
            # 向右扩展一倍窗口，并让相似窗格占据右半边，避免它只分到窄窄一条。
            self.resize(self.width() * 2, self.height())
            self._fit_geometry_into_screen()
            self._split_similar_pane_evenly()
            if self._similar_stale:
                self._refresh_similar_images()
        else:
            self.resize(max(self.width() // 2, 1), self.height())

    def _ensure_similar_page(self) -> SimilarImagesPage:
        if self._similar_page is None:
            self._similar_page = SimilarImagesPage(auto_refresh=False)
            self.widgetSimilarImages.layout().addWidget(self._similar_page)
        return self._similar_page

    def _refresh_similar_images(self):
        page = self._ensure_similar_page()
        if self._sticker is None:
            return

        try:
            search_results, sticker_map = (
                services.sticker_library_viewer_service.fetch_similar_candidates(
                    self._sticker
                )
            )
        except Exception as exc:
            # ValueError(无向量)/RuntimeError(未初始化) 等：清空列表并提示原因。
            logger.warning("获取相似图片失败：%s", exc)
            page.refresh_content(
                services.sticker_library_viewer_service.build_sticker_model([])
            )
            main_window = services.global_instances.main_window
            if main_window is not None:
                main_window.statusBar().showMessage(str(exc), 8000)
            return

        page.set_similar_data(search_results, sticker_map)
        page.apply_filter_and_refresh()
        self._similar_stale = False

    # ==================== 标签编辑 ====================

    def _init_tag_editor(self):
        self._tag_model = QStandardItemModel(self)
        self._tag_widget = CustomTagWidget(self._tag_model, self.widgetTagEditor)
        self._tag_widget.add_action.triggered.connect(self._add_tag)
        self._tag_widget.delete_action.triggered.connect(self._delete_selected_tags)
        self.widgetTagEditor.layout().addWidget(self._tag_widget)

    def _reload_tag_model(self):
        self._tag_model.clear()
        if self._sticker is None:
            return

        for tag in self._sticker.tags:
            item = QStandardItem(tag.name)
            item.setEditable(False)
            item.setData(tag, TAG_DATA_ROLE)
            item.setData(tag.color_rgb, TAG_ACCENT_COLOR_ROLE)
            self._tag_model.appendRow(item)

    def _add_tag(self):
        """打开标签选择对话框；确认后把所选标签追加到当前图片。"""
        if self._sticker is None:
            return

        from ui.dialog_tag_selector import TagSelectorDialog

        dialog = TagSelectorDialog(
            database=self._database,
            parent=self,
        )
        if dialog.exec() == QDialog.DialogCode.Accepted:
            self._save_tags(self._merge_selected_tags(dialog.selected_tags()))

    def _merge_selected_tags(self, selected_tags: list[Tag]) -> list[Tag]:
        """把所选标签追加到当前标签集合，按 id 去重并保持顺序。"""
        known_ids = {tag.id for tag in self._sticker.tags}
        merged = list(self._sticker.tags)
        for tag in selected_tags:
            if tag.id not in known_ids:
                known_ids.add(tag.id)
                merged.append(tag)
        return merged

    def _delete_selected_tags(self):
        if self._sticker is None:
            return

        selected_indexes = [
            index
            for index in self._tag_widget.selectedIndexes()
            if index.data(TAG_DATA_ROLE) is not None
        ]
        if not selected_indexes:
            QMessageBox.information(self, "删除标签", "请先选择要从当前图片移除的标签。")
            return

        selected_ids = {
            index.data(TAG_DATA_ROLE).id for index in selected_indexes
        }
        tag_names = "、".join(
            index.data(TAG_DATA_ROLE).name for index in selected_indexes
        )
        answer = QMessageBox.question(
            self,
            "删除标签",
            f'确实要取消关联标签"{tag_names}"吗？',
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
            QMessageBox.StandardButton.No,
        )
        if answer != QMessageBox.StandardButton.Yes:
            return

        remaining_tags = [
            tag for tag in self._sticker.tags if tag.id not in selected_ids
        ]
        self._save_tags(remaining_tags)

    def _save_tags(self, tags: list[Tag]):
        try:
            updated_sticker = self._database.set_sticker_tags(
                self._sticker.id,
                [tag.id for tag in tags],
            )
        except (ValueError, OSError, SQLAlchemyError) as exc:
            logger.exception("保存图片标签失败")
            QMessageBox.critical(self, "保存失败", str(exc))
            return

        self._sticker.tags = updated_sticker.tags
        self._reload_tag_model()

    # ==================== 文件属性（只读） ====================

    def _init_file_info_table(self):
        table = self.tableWidgetFileInfo
        table.setColumnCount(2)
        table.setHorizontalHeaderLabels(["属性", "值"])
        table.setEditTriggers(QAbstractItemView.EditTrigger.NoEditTriggers)
        table.setSelectionBehavior(QAbstractItemView.SelectionBehavior.SelectRows)
        table.setAlternatingRowColors(True)
        table.verticalHeader().setVisible(False)
        table.horizontalHeader().setSectionResizeMode(
            0, QHeaderView.ResizeMode.ResizeToContents
        )
        table.horizontalHeader().setSectionResizeMode(1, QHeaderView.ResizeMode.Stretch)

    def _reload_file_info(self, file_path: str, pixmap: QPixmap):
        path = Path(file_path)
        try:
            display_path = str(path.resolve(strict=False))
        except OSError:
            display_path = str(path)

        try:
            stat = path.stat()
        except OSError:
            stat = None

        original_name = getattr(self._sticker, "original_file_name", None)
        file_name = original_name or path.name or "不可用"

        extension = path.suffix or getattr(self._sticker, "extension", "")
        file_format = extension.lstrip(".").upper() or "不可用"

        if not pixmap.isNull():
            dimensions = f"{pixmap.width()} x {pixmap.height()} 像素"
        else:
            width = getattr(self._sticker, "size_width", None)
            height = getattr(self._sticker, "size_height", None)
            dimensions = f"{width} x {height} 像素" if width and height else "不可用"

        file_size = stat.st_size if stat is not None else getattr(
            self._sticker, "file_size", None
        )
        modified_at = (
            datetime.fromtimestamp(stat.st_mtime)
            if stat is not None
            else getattr(self._sticker, "modification_date", None)
        )

        rows = [
            ("文件名", file_name),
            ("文件路径", display_path),
            ("文件格式", file_format),
            ("图片尺寸", dimensions),
            ("文件大小", self._format_file_size(file_size)),
            ("修改时间", self._format_datetime(modified_at)),
        ]

        imported_at = getattr(self._sticker, "imported_at", None)
        if imported_at is not None:
            rows.append(("导入时间", self._format_datetime(imported_at)))

        file_hash = getattr(self._sticker, "hash", None)
        if file_hash:
            rows.append(("SHA1", str(file_hash)))

        self.tableWidgetFileInfo.setRowCount(len(rows))
        for row, (label, value) in enumerate(rows):
            label_item = QTableWidgetItem(label)
            value_item = QTableWidgetItem(value)
            value_item.setToolTip(value)
            self.tableWidgetFileInfo.setItem(row, 0, label_item)
            self.tableWidgetFileInfo.setItem(row, 1, value_item)

            if label == "文件路径":
                line_height = self.tableWidgetFileInfo.fontMetrics().lineSpacing()
                self.tableWidgetFileInfo.setRowHeight(row, 3 * line_height)

    @staticmethod
    def _format_file_size(size: Optional[int]) -> str:
        if size is None or size < 0:
            return "不可用"
        if size < 1024:
            return f"{size:,} 字节"

        value = float(size)
        for unit in ("KB", "MB", "GB", "TB"):
            value /= 1024
            if value < 1024 or unit == "TB":
                return f"{value:.2f} {unit} ({size:,} 字节)"

        return f"{size:,} 字节"

    @staticmethod
    def _format_datetime(value: Optional[datetime]) -> str:
        if value is None:
            return "不可用"
        return value.strftime("%Y-%m-%d %H:%M:%S")

    # ==================== 文件属性编辑 ====================

    def _open_property_editor(self):
        if self._sticker is None:
            return

        dialog = LibraryEditingPropsEditDialog(
            parent=self,
            database=self._database,
            sticker=self._sticker,
        )
        if dialog.exec() != QDialog.DialogCode.Accepted:
            return

        updated = dialog.updated_sticker()
        if updated is None:
            return

        self._sticker = updated
        self.label.setText(f"#{updated.id} {updated.original_file_name}")
        self.imageTextEditWidget.set_sticker(updated)
        self._reload_tag_model()
        self._reload_file_info(self._file_path, QPixmap(self._file_path))
