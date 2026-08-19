#coding=utf-8
import logging
import pathlib
from datetime import datetime
from typing import Iterable, Sequence

from PyQt6.QtCore import pyqtSignal, pyqtSlot, QObject
from PyQt6.QtGui import QStandardItemModel, QStandardItem

import services.global_instances
import commons.constants
import services.similarity_result_filter as similarity_filter
from blob_storage import BlobFileEntity
from commons.dto import StickerImage
from commons.roles import (
    ROLE_BLOB_ENTITY,
    ROLE_FILE_PATH,
    ROLE_SIMILARITY,
    ROLE_STICKER_IMAGE,
)
from commons.signal_objects import MainWindowNewTabRequest
from stickerdb.vectordb.models import SearchResult

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
    signal_refresh_library_content = pyqtSignal()
    def __init__(self):
        super().__init__()

    @pyqtSlot()
    def slot_refresh_content(self):
        self.signal_refresh_library_content.emit()


wiring = Wiring()

######################################

def build_sticker_items(
    images: Iterable[StickerImage],
    similarities: dict[int, float] | None = None,
) -> list[QStandardItem]:
    items: list[QStandardItem] = []
    current_blob_storage = services.global_instances.current_blob_storage

    for image in images:
        blob_entity = BlobFileEntity(image.hash, image.extension)
        try:
            file_path = current_blob_storage.read_file(
                blob_entity
            )
        except FileNotFoundError:
            logger.warning("跳过 Blob 文件不存在的图片，id=%s", image.id)
            continue

        # 不再为每张图创建 QIcon：delegate 绘制时通过 ROLE_BLOB_ENTITY 走缩略图提供器，
        # 该 QIcon 从不用于绘制（见性能报告），纯属浪费。
        item = QStandardItem("")
        item.setAccessibleText(image.original_file_name)
        item.setData(file_path, ROLE_FILE_PATH)
        item.setData(blob_entity, ROLE_BLOB_ENTITY)
        item.setData(image, ROLE_STICKER_IMAGE)
        similarity = similarities.get(image.id) if similarities else None
        item.setData(similarity, ROLE_SIMILARITY)
        item.setToolTip(_build_sticker_tooltip(image, file_path, similarity))
        items.append(item)

    return items


def build_sticker_model(
    images: Iterable[StickerImage],
    similarities: dict[int, float] | None = None,
) -> QStandardItemModel:
    model = QStandardItemModel()
    for item in build_sticker_items(images, similarities):
        model.appendRow(item)
    return model


def load_library_page(
    offset: int,
    count: int,
    *,
    order_by: str = "imported_at",
    descending: bool = True,
) -> list[StickerImage]:
    """分页读取全库图片，供无限集合标签页滚动加载使用。"""
    db = services.global_instances.current_library_db
    if db is None:
        return []
    return db.list_stickers(
        offset=offset,
        count=count,
        order_by=order_by,
        descending=descending,
    )


def find_similar_stickers(
    sticker: StickerImage,
    *,
    top_k: int = commons.constants.SIMILAR_IMAGE_CANDIDATE_COUNT,
    result_filter: similarity_filter.SimilarityResultFilter | None = None,
) -> list[tuple[StickerImage, float]]:
    search_results, sticker_map = fetch_similar_candidates(
        sticker, top_k=top_k
    )
    if result_filter is None:
        settings_manager = (
            services.global_instances.current_settings_manager
        )
        if settings_manager is None:
            result_filter = similarity_filter.SimilarityResultFilter()
        else:
            result_filter = (
                similarity_filter.create_filter_from_settings(
                    settings_manager
                )
            )
    return build_similar_matches(
        search_results, sticker_map, result_filter=result_filter
    )


def fetch_similar_candidates(
    sticker: StickerImage,
    *,
    top_k: int = commons.constants.SIMILAR_IMAGE_CANDIDATE_COUNT,
) -> tuple[list, dict[int, StickerImage]]:
    """查询向量库并取回完整候选集，不过滤。

    Returns:
        (search_results, sticker_map) — search_results 是原始 SearchResult
        列表（按相似度降序），sticker_map 以 sqlite_id 为键。
    """
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

    sticker_ids = [result.sqlite_id for result in search_results]
    stickers = db.get_stickers_by_ids(sticker_ids)
    sticker_map = {sticker.id: sticker for sticker in stickers}
    return search_results, sticker_map


def build_similar_matches(
    search_results: Sequence,
    sticker_map: dict[int, StickerImage],
    *,
    result_filter: similarity_filter.SimilarityResultFilter | None = None,
) -> list[tuple[StickerImage, float]]:
    """把向量查询结果映射为 (StickerImage, similarity) 列表，可选用过滤。

    result_filter 为 None 时不做任何过滤，直接返回全部候选。
    """
    if result_filter is not None:
        search_results = result_filter.filter(search_results)
    return [
        (sticker_map[result.sqlite_id], result.similarity)
        for result in search_results
        if result.sqlite_id in sticker_map
    ]


def open_search_results_tab(
    images: Iterable[StickerImage],
    title: str,
) -> None:
    """在独立标签页中展示标签/文本/文件名搜索结果。"""
    from ui.page_search_result import SearchResultPage

    page = SearchResultPage(auto_refresh=False)
    page.refresh_content(build_sticker_model(images))
    _open_result_tab(page, title)


def open_advanced_search_results_tab(
    expression: str,
    images: Iterable[StickerImage],
) -> None:
    """打开高级标签表达式结果页。"""
    from ui.page_advanced_search_result import AdvancedSearchResultPage

    page = AdvancedSearchResultPage(
        expression,
        images,
        auto_refresh=False,
    )
    _open_result_tab(page, "高级搜索[表达式]")


def open_similar_stickers_tab(
    sticker: StickerImage,
    *,
    top_k: int = commons.constants.SIMILAR_IMAGE_CANDIDATE_COUNT,
    result_filter: similarity_filter.SimilarityResultFilter | None = None,
) -> None:
    from ui.page_similar_images import SimilarImagesPage

    search_results, sticker_map = fetch_similar_candidates(
        sticker, top_k=top_k
    )
    page = SimilarImagesPage(auto_refresh=False)
    page.set_similar_data(search_results, sticker_map)
    if result_filter is not None:
        page.set_filter_config(True, result_filter.config)
    page.apply_filter_and_refresh()
    _open_result_tab(page, f"相似图片[{sticker.original_file_name}]")


def _open_result_tab(page, title: str) -> None:
    """把有限结果页作为可关闭标签页加入主窗口。"""
    main_window = services.global_instances.main_window
    if main_window is None:
        raise RuntimeError("主窗口尚未初始化。")
    request = MainWindowNewTabRequest(
        widget=page,
        title=title,
        closable=True,
    )
    main_window.signal_add_new_tab.emit(request)


def delete_stickers(stickers: Sequence[StickerImage]) -> tuple[str, ...]:
    """以 SQLite 为主记录批量删除图片，再清理可重建的向量和 Blob。"""
    db = services.global_instances.current_library_db
    blob_storage = services.global_instances.current_blob_storage
    vector_store = services.global_instances.current_vector_store
    if db is None or blob_storage is None:
        raise RuntimeError("图库未初始化。")

    sticker_list = list(stickers)
    db.delete_stickers(sticker_list)
    cleanup_errors = []

    for sticker in sticker_list:
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


def delete_sticker(sticker: StickerImage) -> tuple[str, ...]:
    """删除单张图片，复用批量删除的清理逻辑。"""
    return delete_stickers([sticker])

def open_sticker_library_view_tab():
    from ui.page_infinite_sticker_collection import InfiniteStickerCollectionPage

    page = InfiniteStickerCollectionPage()
    main_window = services.global_instances.main_window

    request = MainWindowNewTabRequest(
        widget=page,
        title="图库浏览",
        closable=False,
    )
    main_window.signal_add_new_tab.emit(request)

