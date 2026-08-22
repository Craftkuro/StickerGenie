# coding=utf-8
import logging
from datetime import datetime
from pathlib import Path
from typing import Optional

from PyQt6 import uic
from PyQt6.QtCore import Qt
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

        # 相似图片窗格默认隐藏；pushButtonEditProperties 为未来文件属性编辑预留，暂不连接。
        self.widgetSimilarImages.setVisible(False)
        self.pushButtonShowHideSimilarImages.setText(SIMILAR_BUTTON_SHOW_TEXT)

        self.pushButtonPrev.clicked.connect(self._go_back)
        self.pushButtonRand.clicked.connect(self._go_random)
        self.pushButtonNext.clicked.connect(self._go_next)
        self.pushButtonShowHideSimilarImages.clicked.connect(
            self._toggle_similar_images
        )

        self.imageTextEditWidget.set_database(self._database)
        self._init_tag_editor()
        self._init_file_info_table()

        initial_id = self._database.random_sticker_id()
        if initial_id is not None:
            self._navigate_to(initial_id)

    # ==================== 导航 ====================

    def _current_id(self) -> Optional[int]:
        if 0 <= self._position < len(self._history):
            return self._history[self._position]
        return None

    def _go_back(self):
        """沿浏览历史后退一步；已在起点时不做任何事。"""
        if self._position <= 0:
            return
        self._position -= 1
        stickers = self._database.get_stickers_by_ids(
            [self._history[self._position]]
        )
        if not stickers:
            logger.warning("历史记录对应的图片已不存在，id=%s", self._history[self._position])
            return
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
            splitter_width = max(self.splitterLeftRight.width(), 2)
            half = splitter_width // 2
            self.splitterLeftRight.setSizes([half, splitter_width - half])
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
