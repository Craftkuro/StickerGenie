#coding=utf-8
import logging
import pathlib
from datetime import datetime
from typing import Iterable

from PyQt6.QtCore import pyqtSignal, pyqtSlot, QObject
from PyQt6.QtGui import QStandardItemModel, QIcon, QStandardItem

import services.global_instances
import commons.constants
from blob_storage import BlobFileEntity
from commons.dto import StickerImage
from commons.roles import (
    ROLE_BLOB_ENTITY,
    ROLE_FILE_PATH,
    ROLE_SIMILARITY,
    ROLE_STICKER_IMAGE,
)
from commons.signal_objects import MainWindowNewTabRequest

from ui.page_finite_sticker_collection import FiniteStickerCollectionPage
from ui.page_infinite_sticker_collection import InfiniteStickerCollectionPage

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

        icon = QIcon(pathlib.Path(file_path).as_posix())
        item = QStandardItem(icon, "")
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


def load_library_page(offset: int, count: int) -> list[StickerImage]:
    """分页读取全库图片，供无限集合标签页滚动加载使用。"""
    db = services.global_instances.current_library_db
    if db is None:
        return []
    return db.list_stickers(offset=offset, count=count)


def _select_similar_count(scores: list[float]) -> int:
    """根据相似度曲线上的最大落差决定保留的相似图片数量。

    scores 是向量库返回的候选相似度，必须按降序排列（Chroma 查询天然
    按相似度从高到低返回）。返回值表示最终保留几个候选，调用方用
    ``search_results[:返回值]`` 截断即可。

    为什么不用固定阈值：真实图库中“相关图片”和“无关图片”的相似度区间
    会重叠（例如相关结果可能落在 0.35~0.50，而无关结果也能到 0.40），
    单一阈值要么漏掉相关结果，要么放进大量噪音。改为观察排序曲线上
    的“最大落差”后，截断位置由每个查询自己的分数分布决定，不再依赖
    一个全局固定的相似度数值。
    """
    if not scores:
        return 0

    # 相邻排名之间的相似度差，即“名次每前进一位，分数跳了多少”。
    # gaps[i] 表示第 i 名与第 i+1 名之间的落差。
    gaps = [
        scores[index] - scores[index + 1]
        for index in range(len(scores) - 1)
    ]
    max_gap = max(gaps, default=0.0)

    if max_gap >= commons.constants.SIMILAR_IMAGE_MIN_GAP:
        # 存在明显分群：最大落差之前的候选属于一个“相似群体”，
        # 落差之后的结果分数骤降，视为另一个（不相关的）群体，直接排除。
        # gaps.index(max_gap) 是最大落差出现的位置，+1 是因为要保留
        # 该位置本身（例如 gaps[1] 是第 1 名和第 2 名之间的落差，
        # 最大落差在 gaps[1] 时保留前 2 名）。
        keep_count = gaps.index(max_gap) + 1
    elif (
        scores[0] < commons.constants.SIMILAR_IMAGE_NO_GAP_MIN_TOP_SIMILARITY
    ):
        # 分数曲线非常平缓，找不到明显的“分群边界”，同时最高分也很低：
        # 说明没有任何候选与查询图片拉开差距，此时返回空比返回一批
        # 似是而非的结果更符合直觉。
        return 0
    else:
        # 分数曲线平缓但整体分数较高：例如一整组相近图片的分数都挤在
        # 0.50 附近，内部落差很小。这种情况不应该因为“没有大落差”就
        # 全部丢掉，而是保留整个高分平台，后续再用最低相似度过滤尾部。
        keep_count = len(scores)

    # 绝对相似度下限：即使落差策略把某个候选划进了“相似群体”，
    # 分数低于该值的仍然不展示，避免把低分噪音带进结果。
    kept_scores = [
        score for score in scores[:keep_count]
        if score >= commons.constants.SIMILAR_IMAGE_MIN_SIMILARITY
    ]
    if (
        len(kept_scores) == 1
        and kept_scores[0]
        < commons.constants.SIMILAR_IMAGE_LONE_RESULT_MIN_SIMILARITY
    ):
        # 只有一个候选活过前面的过滤，且它的分数并不高：这种“孤点”
        # 多半是库里碰巧最像的一张无关图片，而不是真正的相似图片，
        # 直接返回空。只有单个候选分数足够高时才值得展示。
        return 0
    # 防止图库很大或某个查询确实有大量相近图片时结果页被撑爆，
    # 最多展示 SIMILAR_IMAGE_MAX_RESULTS 个。
    return min(len(kept_scores), commons.constants.SIMILAR_IMAGE_MAX_RESULTS)


def find_similar_stickers(
    sticker: StickerImage,
    *,
    top_k: int = commons.constants.SIMILAR_IMAGE_CANDIDATE_COUNT,
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

    keep_count = _select_similar_count(
        [result.similarity for result in search_results]
    )
    search_results = search_results[:keep_count]

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


def open_similar_stickers_tab(
    sticker: StickerImage,
    *,
    top_k: int = commons.constants.SIMILAR_IMAGE_CANDIDATE_COUNT,
) -> None:
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
    page = FiniteStickerCollectionPage(auto_refresh=False)
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

    page = InfiniteStickerCollectionPage()
    main_window = services.global_instances.main_window

    request = MainWindowNewTabRequest(
        widget=page,
        title="图库浏览",
        closable=False,
    )
    main_window.signal_add_new_tab.emit(request)

