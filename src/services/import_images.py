# coding=utf-8
"""
图片导入服务。
提供将图片文件导入数据库的功能。
"""

import datetime
import logging
import threading
import time
from dataclasses import dataclass
from functools import partial
from pathlib import Path
from typing import Callable, List, Optional

from PyQt6.QtCore import QObject, QThread, pyqtSignal, pyqtSlot

import apppath
import services.global_instances
from services.image_vector_model import get_model_hash as _get_shared_model_hash
from commons.dto import StickerImage, Tag
from commons.image_metadata import StickerImageMetadata
from commons.signal_objects import ImportImagesRequest
from image_features_extractor import (
    DEFAULT_MODEL_FILENAME,
    ExtractionCancelledError,
    iter_features,
)
from image_text_extractor import TextExtractionCancelledError, iter_texts
from stickerdb.vectordb import VectorMetadata
from utils.image_metadata import get_image_metadata

logger = logging.getLogger(__name__)

IMPORT_BATCH_SIZE = 32
OCR_BATCH_SIZE = 8
PREPROCESS_END_PERCENT = 5
SQLITE_END_PERCENT = 15
OCR_START_PERCENT = 15
OCR_END_PERCENT = 40
VECTOR_START_PERCENT = 40


def _percent_in_range(value: int, total: int, start: int, end: int) -> int:
    """把已完成的 value/total 映射到 [start, end] 进度区间。"""
    if total <= 0:
        return end
    ratio = min(1.0, max(0.0, value / total))
    return start + int((end - start) * ratio)


@dataclass(frozen=True)
class ImportImagesResult:
    imported_stickers: tuple[StickerImage, ...]
    duplicate_count: int = 0
    vectorized_count: int = 0
    vector_errors: tuple[str, ...] = ()
    ocr_count: int = 0
    ocr_errors: tuple[str, ...] = ()
    cancelled: bool = False


@dataclass(frozen=True)
class ImportImagesProgress:
    percent: int
    status: str
    completed: int = 0
    total: int = 0
    last_file_name: str | None = None

    def __post_init__(self):
        if not 0 <= self.percent <= 100:
            raise ValueError("percent must be between 0 and 100")
        if self.completed < 0 or self.total < 0 or self.completed > self.total:
            raise ValueError("invalid import progress counts")


ProgressCallback = Callable[[ImportImagesProgress], None]


def _is_cancelled(cancel_event: threading.Event | None) -> bool:
    return cancel_event is not None and cancel_event.is_set()


def _report_progress(
    callback: ProgressCallback | None,
    percent: int,
    status: str,
    *,
    completed: int = 0,
    total: int = 0,
    last_file_name: str | None = None,
) -> None:
    if callback is not None:
        callback(
            ImportImagesProgress(
                percent=percent,
                status=status,
                completed=completed,
                total=total,
                last_file_name=last_file_name,
            )
        )


def _get_model_hash(model_path: Path) -> str:
    return _get_shared_model_hash(model_path)

def _metadata_to_sticker_image(metadata: StickerImageMetadata, file_path: Path) -> StickerImage:
    """
    将图片元数据转换为 StickerImage DTO。
    :param metadata: 图片元数据
    :param file_path: 图片文件路径
    :return: StickerImage DTO
    """
    now = datetime.datetime.now()
    
    sticker = StickerImage()
    sticker.original_file_name = metadata.original_file_name
    sticker.relative_path = str(file_path)
    sticker.file_size = metadata.file_size
    sticker.hash = metadata.hash
    sticker.extension = metadata.extension
    sticker.imported_at = now
    sticker.modification_date = now
    sticker.size_width = metadata.size_width
    sticker.size_height = metadata.size_height
    sticker.vectordb_id = None
    sticker.text_in_image = None
    
    return sticker


def _generate_vectors(
    stickers_and_blob_paths: list[tuple[StickerImage, str]],
    progress_callback: ProgressCallback | None = None,
    *,
    cancel_event: threading.Event | None = None,
    start_percent: int = VECTOR_START_PERCENT,
    end_percent: int = 100,
) -> tuple[int, tuple[str, ...]]:
    vector_store = services.global_instances.current_vector_store
    if vector_store is None:
        return 0, ("向量数据库未初始化。",)

    model_path = apppath.app_path / DEFAULT_MODEL_FILENAME
    if not model_path.is_file():
        return 0, (f"特征提取模型不存在：{model_path}",)

    if not stickers_and_blob_paths:
        return 0, ()

    image_paths = [blob_path for _, blob_path in stickers_and_blob_paths]
    sticker_by_path = {
        blob_path: sticker
        for sticker, blob_path in stickers_and_blob_paths
    }
    model_hash = _get_model_hash(model_path)
    total = len(image_paths)
    vectorized_count = 0
    errors: list[str] = []

    if _is_cancelled(cancel_event):
        return 0, tuple(errors)
    _report_progress(
        progress_callback,
        start_percent,
        "正在生成图片向量",
        completed=0,
        total=total,
    )

    try:
        for result_batch in iter_features(
            image_paths,
            model_path=model_path,
            total=total,
            cancel_event=cancel_event,
        ):
            batch_stickers: list[StickerImage] = []
            batch_vectors = []
            batch_metadata = []
            batch_last_file_name = None

            for feature_result in result_batch.results:
                sticker = sticker_by_path.get(feature_result.image_path)
                if sticker is None:
                    errors.append(
                        f"{feature_result.image_path}：缺少对应的导入记录"
                    )
                    continue
                if not feature_result.success:
                    errors.append(
                        f"{sticker.original_file_name}：{feature_result.error}"
                    )
                    continue

                batch_stickers.append(sticker)
                batch_vectors.append(feature_result.vector)
                batch_metadata.append(
                    VectorMetadata(
                        image_filename=sticker.original_file_name,
                        model_hash=model_hash,
                        sqlite_id=sticker.id,
                        extraction_timestamp=time.time(),
                        image_width=sticker.size_width,
                        image_height=sticker.size_height,
                    )
                )
                batch_last_file_name = sticker.original_file_name

            if batch_stickers:
                try:
                    with services.global_instances.vector_store_lock:
                        vector_ids = vector_store.add_batch(
                            batch_vectors,
                            batch_metadata,
                        )
                    services.global_instances.current_library_db.set_sticker_vector_ids(
                        {
                            sticker.id: vector_id
                            for sticker, vector_id in zip(
                                batch_stickers,
                                vector_ids,
                            )
                        }
                    )
                except Exception as exc:
                    logger.exception("写入图片向量失败")
                    errors.append(f"向量写入失败：{exc}")
                else:
                    for sticker, vector_id in zip(batch_stickers, vector_ids):
                        sticker.vectordb_id = vector_id
                    vectorized_count += len(vector_ids)

            completed = min(total, result_batch.progress.completed)
            _report_progress(
                progress_callback,
                _percent_in_range(
                    completed,
                    total,
                    start_percent,
                    end_percent,
                ),
                "正在生成图片向量",
                completed=completed,
                total=total,
                last_file_name=batch_last_file_name,
            )
    except ExtractionCancelledError:
        return vectorized_count, tuple(errors)
    except Exception as exc:
        logger.exception("图片特征提取任务失败")
        return vectorized_count, tuple(errors + [str(exc)])

    return vectorized_count, tuple(errors)


def _extract_texts(
    stickers_and_blob_paths: list[tuple[StickerImage, str]],
    progress_callback: ProgressCallback | None = None,
    *,
    cancel_event: threading.Event | None = None,
    start_percent: int = OCR_START_PERCENT,
    end_percent: int = OCR_END_PERCENT,
) -> tuple[int, tuple[str, ...]]:
    current_library_db = services.global_instances.current_library_db
    if current_library_db is None:
        return 0, ("图库尚未初始化，无法回填图片文字。",)

    if not stickers_and_blob_paths:
        return 0, ()

    image_paths = [blob_path for _, blob_path in stickers_and_blob_paths]
    sticker_by_path = {
        blob_path: sticker
        for sticker, blob_path in stickers_and_blob_paths
    }
    total = len(image_paths)
    ocr_count = 0
    errors: list[str] = []

    if _is_cancelled(cancel_event):
        return 0, tuple(errors)
    _report_progress(
        progress_callback,
        start_percent,
        "正在识别图片文字",
        completed=0,
        total=total,
    )

    try:
        for result_batch in iter_texts(
            image_paths,
            batch_size=OCR_BATCH_SIZE,
            total=total,
            cancel_event=cancel_event,
        ):
            text_by_sticker_id: dict[int, str | None] = {}
            batch_last_file_name = None

            for text_result in result_batch.results:
                sticker = sticker_by_path.get(text_result.image_path)
                if sticker is None:
                    errors.append(
                        f"{text_result.image_path}：缺少对应的导入记录"
                    )
                    continue
                if not text_result.success:
                    errors.append(
                        f"{sticker.original_file_name}：{text_result.error}"
                    )
                    continue

                sticker.text_in_image = text_result.text
                text_by_sticker_id[sticker.id] = text_result.text
                batch_last_file_name = sticker.original_file_name

            if text_by_sticker_id:
                try:
                    current_library_db.set_sticker_texts(text_by_sticker_id)
                except Exception as exc:
                    logger.exception("回填图片文字失败")
                    errors.append(f"文字回填失败：{exc}")
                else:
                    ocr_count += len(text_by_sticker_id)

            completed = min(total, result_batch.progress.completed)
            _report_progress(
                progress_callback,
                _percent_in_range(
                    completed,
                    total,
                    start_percent,
                    end_percent,
                ),
                "正在识别图片文字",
                completed=completed,
                total=total,
                last_file_name=batch_last_file_name,
            )
    except TextExtractionCancelledError:
        return ocr_count, tuple(errors)
    except Exception as exc:
        logger.exception("图片文字识别任务失败")
        return ocr_count, tuple(errors + [str(exc)])

    return ocr_count, tuple(errors)


def import_images_with_result(
    file_paths: List[str],
    tags: Optional[List[Tag]] = None,
    *,
    generate_vectors: bool = False,
    extract_text: bool = False,
    progress: ProgressCallback | None = None,
    cancel_event: threading.Event | None = None,
) -> ImportImagesResult:
    """
    将多个图片文件导入数据库。
    :param file_paths: 图片文件路径列表
    :param tags: 可选的标签列表，将应用于所有导入的图片
    :return: 成功导入的 StickerImage 对象列表
    :raises RuntimeError: 当数据库未初始化时
    """
    if services.global_instances.current_library_db is None:
        raise RuntimeError("数据库未初始化，无法导入图片")

    if services.global_instances.current_blob_storage is None:
        raise RuntimeError("blob存储未初始化，无法导入图片")

    current_library_db = services.global_instances.current_library_db
    current_blob_storage = services.global_instances.current_blob_storage
    candidates = []
    request_hashes = set()
    duplicate_count = 0
    imported_stickers = []
    vectorized_count = 0
    vector_errors: list[str] = []
    ocr_count = 0
    ocr_errors: list[str] = []

    def make_result(*, cancelled: bool = False) -> ImportImagesResult:
        return ImportImagesResult(
            imported_stickers=tuple(imported_stickers),
            duplicate_count=duplicate_count,
            vectorized_count=vectorized_count,
            vector_errors=tuple(vector_errors),
            ocr_count=ocr_count,
            ocr_errors=tuple(ocr_errors),
            cancelled=cancelled,
        )

    _report_progress(
        progress,
        0,
        "正在预处理图片",
        total=len(file_paths),
    )

    if _is_cancelled(cancel_event):
        return make_result(cancelled=True)
    
    for file_path in file_paths:
        if _is_cancelled(cancel_event):
            return make_result(cancelled=True)

        path = Path(file_path)
        
        if not path.exists():
            continue
        
        try:
            # 使用工具函数获取图片元数据
            metadata = get_image_metadata(path)

            if _is_cancelled(cancel_event):
                return make_result(cancelled=True)

            if metadata.hash in request_hashes:
                duplicate_count += 1
                continue
            request_hashes.add(metadata.hash)
            
            # 转换为 StickerImage DTO
            sticker = _metadata_to_sticker_image(metadata, path)
            
            # 添加标签
            if tags:
                for tag in tags:
                    sticker.tags.append(tag)

            candidates.append((sticker, file_path, metadata.hash))
            
        except (OSError, ValueError) as e:
            # 跳过无法读取的图片文件
            logger.warning("无法读取图片 %s: %s", file_path, e)
            continue

    if _is_cancelled(cancel_event):
        return make_result(cancelled=True)

    existing_hashes = current_library_db.get_existing_sticker_hashes(
        sticker.hash for sticker, _, _ in candidates
    )

    if _is_cancelled(cancel_event):
        return make_result(cancelled=True)

    duplicate_count += sum(
        1 for sticker, _, _ in candidates if sticker.hash in existing_hashes
    )
    import_candidates = [
        candidate
        for candidate in candidates
        if candidate[0].hash not in existing_hashes
    ]

    _report_progress(
        progress,
        PREPROCESS_END_PERCENT,
        "正在写入图库",
        total=len(import_candidates),
    )

    candidate_count = len(import_candidates)
    all_inserted_stickers_and_blob_paths: list[tuple[StickerImage, str]] = []
    for batch_start in range(0, candidate_count, IMPORT_BATCH_SIZE):
        if _is_cancelled(cancel_event):
            return make_result(cancelled=True)

        batch_candidates = import_candidates[
            batch_start : batch_start + IMPORT_BATCH_SIZE
        ]
        batch_stickers = []
        batch_stickers_and_blob_paths = []
        for sticker, file_path, file_hash in batch_candidates:
            if _is_cancelled(cancel_event):
                return make_result(cancelled=True)

            blob_entity = current_blob_storage.store_file(file_path, file_hash)
            if _is_cancelled(cancel_event):
                return make_result(cancelled=True)

            blob_path = current_blob_storage.read_file(blob_entity)
            batch_stickers.append(sticker)
            batch_stickers_and_blob_paths.append((sticker, blob_path))

        if _is_cancelled(cancel_event):
            return make_result(cancelled=True)

        inserted_stickers = current_library_db.add_stickers(batch_stickers)
        duplicate_count += len(batch_stickers) - len(inserted_stickers)
        inserted_object_ids = {id(sticker) for sticker in inserted_stickers}
        inserted_stickers_and_blob_paths = [
            (sticker, blob_path)
            for sticker, blob_path in batch_stickers_and_blob_paths
            if id(sticker) in inserted_object_ids
        ]
        imported_stickers.extend(inserted_stickers)
        all_inserted_stickers_and_blob_paths.extend(
            inserted_stickers_and_blob_paths
        )

        completed = len(imported_stickers)
        sqlite_end = (
            SQLITE_END_PERCENT
            if (generate_vectors or extract_text)
            else 100
        )
        percent = _percent_in_range(
            completed,
            candidate_count,
            PREPROCESS_END_PERCENT,
            sqlite_end,
        )
        last_file_name = (
            imported_stickers[-1].original_file_name
            if imported_stickers
            else None
        )
        _report_progress(
            progress,
            percent,
            "正在写入图库",
            completed=completed,
            total=candidate_count,
            last_file_name=last_file_name,
        )

        if _is_cancelled(cancel_event):
            return make_result(cancelled=True)

    if extract_text and all_inserted_stickers_and_blob_paths:
        try:
            ocr_count, ocr_errors = _extract_texts(
                all_inserted_stickers_and_blob_paths,
                progress,
                cancel_event=cancel_event,
                start_percent=OCR_START_PERCENT,
                end_percent=OCR_END_PERCENT if generate_vectors else 100,
            )
        except Exception as exc:
            logger.exception("图片文字识别失败")
            ocr_errors.append(f"文字识别失败：{exc}")

    if generate_vectors and all_inserted_stickers_and_blob_paths:
        try:
            vectorized_count, vector_errors = _generate_vectors(
                all_inserted_stickers_and_blob_paths,
                progress,
                cancel_event=cancel_event,
                start_percent=VECTOR_START_PERCENT,
                end_percent=100,
            )
        except Exception as exc:
            logger.exception("写入图片向量失败")
            vector_errors.append(f"向量写入失败：{exc}")

    if _is_cancelled(cancel_event):
        return make_result(cancelled=True)

    last_file_name = (
        imported_stickers[-1].original_file_name
        if imported_stickers
        else None
    )
    _report_progress(
        progress,
        100,
        "导入完成",
        completed=len(imported_stickers),
        total=candidate_count,
        last_file_name=last_file_name,
    )

    return make_result()


def import_images(
    file_paths: List[str],
    tags: Optional[List[Tag]] = None,
    *,
    generate_vectors: bool = False,
) -> List[StickerImage]:
    """导入图片并返回 SQLite DTO；详细结果由后台服务消费。"""
    result = import_images_with_result(
        file_paths,
        tags,
        generate_vectors=generate_vectors,
    )
    for error in result.vector_errors:
        logger.warning("生成图片向量失败：%s", error)
    return list(result.imported_stickers)


class _ImportImagesWorker(QObject):
    succeeded = pyqtSignal(object)
    cancelled = pyqtSignal(object)
    failed = pyqtSignal(str)
    progress_changed = pyqtSignal(object)

    def __init__(self, request: ImportImagesRequest, cancel_event: threading.Event):
        super().__init__()
        self._request = request
        self._cancel_event = cancel_event

    @pyqtSlot()
    def run(self):
        try:
            result = import_images_with_result(
                list(self._request.file_paths),
                generate_vectors=self._request.generate_vectors,
                extract_text=self._request.extract_text,
                progress=self.progress_changed.emit,
                cancel_event=self._cancel_event,
            )
        except Exception as exc:
            logger.exception("导入图片失败")
            self.failed.emit(str(exc))
            return
        if result.cancelled:
            self.cancelled.emit(result)
        else:
            self.succeeded.emit(result)


class ImageImportService(QObject):
    """在独立 QThread 中执行每个导入请求。"""

    import_finished = pyqtSignal(object)
    import_cancelled = pyqtSignal(object)
    import_failed = pyqtSignal(str)
    import_progress_changed = pyqtSignal(object)

    def __init__(self, parent: QObject | None = None):
        super().__init__(parent)
        self._jobs: dict[QThread, _ImportImagesWorker] = {}
        self._cancel_events: dict[QThread, threading.Event] = {}

    @property
    def active_job_count(self) -> int:
        return len(self._jobs)

    def start_import(self, request: ImportImagesRequest) -> None:
        if not isinstance(request, ImportImagesRequest):
            raise TypeError("request must be an ImportImagesRequest")
        if self._jobs:
            raise RuntimeError("已有图片导入任务正在进行")

        thread = QThread(self)
        cancel_event = threading.Event()
        worker = _ImportImagesWorker(request, cancel_event)
        worker.moveToThread(thread)
        self._jobs[thread] = worker
        self._cancel_events[thread] = cancel_event

        thread.started.connect(worker.run)
        worker.succeeded.connect(self.import_finished)
        worker.cancelled.connect(self.import_cancelled)
        worker.failed.connect(self.import_failed)
        worker.progress_changed.connect(self.import_progress_changed)
        worker.succeeded.connect(thread.quit)
        worker.cancelled.connect(thread.quit)
        worker.failed.connect(thread.quit)
        worker.succeeded.connect(worker.deleteLater)
        worker.cancelled.connect(worker.deleteLater)
        worker.failed.connect(worker.deleteLater)
        thread.finished.connect(partial(self._release_job, thread))
        thread.finished.connect(thread.deleteLater)
        thread.start()

    def cancel_import(self) -> bool:
        if not self._cancel_events:
            return False

        cancel_event = next(iter(self._cancel_events.values()))
        if cancel_event.is_set():
            return False

        cancel_event.set()
        return True

    def _release_job(self, thread: QThread) -> None:
        self._jobs.pop(thread, None)
        self._cancel_events.pop(thread, None)
