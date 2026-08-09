#coding=utf-8
import logging
import pathlib
from datetime import datetime
from typing import Iterable

from PyQt6.QtCore import pyqtSignal, pyqtSlot, QObject
from PyQt6.QtGui import QStandardItemModel, QIcon, QStandardItem

import services.global_instances
from blob_storage import BlobFileEntity
from commons.dto import StickerImage
from commons.roles import ROLE_FILE_PATH, ROLE_SIMILARITY, ROLE_STICKER_IMAGE
from commons.signal_objects import MainWindowNewTabRequest

from ui.page_sticker_library_view import StickerLibraryViewPage

logger = logging.getLogger(__name__)


def _format_datetime(value: datetime | None) -> str:
    if value is None:
        return "不可用"
    return value.strftime("%Y-%m-%d %H:%M:%S")


def _build_sticker_tooltip(
    image: StickerImage,
    file_path: str,
    similarity: float | None,
) -> str:
    lines = [
        f"文件名：{pathlib.Path(file_path).name}",
        f"原始文件名：{image.original_file_name}",
        f"修改日期：{_format_datetime(image.modification_date)}",
    ]
    if similarity is not None:
        lines.append(f"相似度：{similarity:.1%}")
    return "\n".join(lines)


class Wiring(QObject):
    signal_refresh_library_content_result = pyqtSignal(QStandardItemModel)
    def __init__(self):
        super().__init__()

    @pyqtSlot()
    def slot_refresh_content(self):
        ret = refresh_content()
        self.signal_refresh_library_content_result.emit(ret)


wiring = Wiring()

######################################

def build_sticker_model(
    images: Iterable[StickerImage],
    similarities: dict[int, float] | None = None,
) -> QStandardItemModel:
    model = QStandardItemModel()
    current_blob_storage = services.global_instances.current_blob_storage

    for image in images:
        try:
            file_path = current_blob_storage.read_file(
                BlobFileEntity(image.hash, image.extension)
            )
        except FileNotFoundError:
            logger.warning("跳过 Blob 文件不存在的图片，id=%s", image.id)
            continue

        icon = QIcon(pathlib.Path(file_path).as_posix())
        item = QStandardItem(icon, "")
        item.setAccessibleText(image.original_file_name)
        item.setData(file_path, ROLE_FILE_PATH)
        item.setData(image, ROLE_STICKER_IMAGE)
        similarity = similarities.get(image.id) if similarities else None
        item.setData(similarity, ROLE_SIMILARITY)
        item.setToolTip(_build_sticker_tooltip(image, file_path, similarity))
        model.insertRow(model.rowCount(), item)

    return model


def refresh_content() -> QStandardItemModel:
    db = services.global_instances.current_library_db
    return build_sticker_model(db.list_stickers())


def find_similar_stickers(
    sticker: StickerImage,
    *,
    top_k: int = 50,
) -> list[tuple[StickerImage, float]]:
    db = services.global_instances.current_library_db
    vector_store = services.global_instances.current_vector_store
    if db is None or vector_store is None:
        raise RuntimeError("图库或向量数据库未初始化。")

    with services.global_instances.vector_store_lock:
        record = None
        if sticker.vectordb_id:
            record = vector_store.get(str(sticker.vectordb_id))
        if record is None:
            record = vector_store.get_by_sqlite_id(sticker.id)
        if record is None:
            raise ValueError("该图片还没有特征向量。")
        search_results = vector_store.search_by_id(record.id, top_k=top_k)

    similarity_by_sticker_id = {
        result.sqlite_id: result.similarity
        for result in search_results
    }
    stickers = db.get_stickers_by_ids(
        [result.sqlite_id for result in search_results]
    )
    return [
        (similar_sticker, similarity_by_sticker_id[similar_sticker.id])
        for similar_sticker in stickers
    ]


def open_similar_stickers_tab(sticker: StickerImage, *, top_k: int = 50) -> None:
    matches = find_similar_stickers(sticker, top_k=top_k)
    similarities = {
        similar_sticker.id: similarity
        for similar_sticker, similarity in matches
    }
    open_sticker_results_tab(
        (similar_sticker for similar_sticker, _ in matches),
        f"相似图片[{sticker.original_file_name}]",
        similarities=similarities,
    )


def open_sticker_results_tab(
    images: Iterable[StickerImage],
    title: str,
    *,
    similarities: dict[int, float] | None = None,
) -> None:
    """在独立标签页中展示给定的图片结果。"""
    page = StickerLibraryViewPage(auto_refresh=False)
    page.refresh_content(build_sticker_model(images, similarities))

    main_window = services.global_instances.main_window
    if main_window is None:
        raise RuntimeError("主窗口尚未初始化。")
    request = MainWindowNewTabRequest(
        widget=page,
        title=title,
        closable=True,
    )
    main_window.signal_add_new_tab.emit(request)


def delete_sticker(sticker: StickerImage) -> tuple[str, ...]:
    """以 SQLite 为主记录删除图片，再清理可重建的向量和 Blob。"""
    db = services.global_instances.current_library_db
    blob_storage = services.global_instances.current_blob_storage
    vector_store = services.global_instances.current_vector_store
    if db is None or blob_storage is None:
        raise RuntimeError("图库未初始化。")

    db.delete_stickers([sticker])
    cleanup_errors = []

    if vector_store is not None:
        try:
            with services.global_instances.vector_store_lock:
                if sticker.vectordb_id:
                    deleted = vector_store.delete(str(sticker.vectordb_id))
                    if not deleted:
                        vector_store.delete_by_sqlite_id(sticker.id)
                else:
                    vector_store.delete_by_sqlite_id(sticker.id)
        except Exception as exc:
            logger.exception("删除图片向量失败，id=%s", sticker.id)
            cleanup_errors.append(f"向量清理失败：{exc}")

    blob_entity = BlobFileEntity(sticker.hash, sticker.extension)
    try:
        if blob_storage.exists(blob_entity):
            blob_storage.delete_file(blob_entity)
    except Exception as exc:
        logger.exception("删除 Blob 文件失败，id=%s", sticker.id)
        cleanup_errors.append(f"文件清理失败：{exc}")

    return tuple(cleanup_errors)

def open_sticker_library_view_tab():

    page = StickerLibraryViewPage()
    main_window = services.global_instances.main_window

    request = MainWindowNewTabRequest(
        widget=page,
        title="图库浏览",
        closable=False,
    )
    main_window.signal_add_new_tab.emit(request)

