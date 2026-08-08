# coding=utf-8
"""
图片导入服务。
提供将图片文件导入数据库的功能。
"""

import datetime
import hashlib
import logging
import time
from dataclasses import dataclass
from functools import lru_cache, partial
from pathlib import Path
from typing import Callable, List, Optional

from PyQt6.QtCore import QObject, QThread, pyqtSignal, pyqtSlot

import apppath
import services.global_instances
from commons.dto import StickerImage, Tag
from commons.image_metadata import StickerImageMetadata
from commons.signal_objects import ImportImagesRequest
from image_features_extractor import extract_features
from stickerdb.vectordb import VectorMetadata
from utils.image_metadata import get_image_metadata

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class ImportImagesResult:
    imported_stickers: tuple[StickerImage, ...]
    duplicate_count: int = 0
    vectorized_count: int = 0
    vector_errors: tuple[str, ...] = ()


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


@lru_cache(maxsize=4)
def _calculate_model_hash(
    model_path: str,
    file_size: int,
    modification_time_ns: int,
) -> str:
    del file_size, modification_time_ns
    digest = hashlib.sha256()
    with open(model_path, "rb") as model_file:
        for chunk in iter(lambda: model_file.read(1024 * 1024), b""):
            digest.update(chunk)
    return f"vit_b_16_{digest.hexdigest()[:16]}"


def _get_model_hash(model_path: Path) -> str:
    stat = model_path.stat()
    return _calculate_model_hash(
        str(model_path.resolve()),
        stat.st_size,
        stat.st_mtime_ns,
    )

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
) -> tuple[int, tuple[str, ...]]:
    vector_store = services.global_instances.current_vector_store
    if vector_store is None:
        return 0, ("向量数据库未初始化。",)

    model_path = apppath.app_path / "vit_b_16_features.onnx"
    if not model_path.is_file():
        return 0, (f"特征提取模型不存在：{model_path}",)

    image_paths = [blob_path for _, blob_path in stickers_and_blob_paths]

    def report_extraction_progress(extraction_progress):
        completed = min(extraction_progress.completed, len(image_paths))
        last_file_name = None
        if completed:
            last_file_name = stickers_and_blob_paths[
                completed - 1
            ][0].original_file_name
        percent = min(
            99,
            1 + int(98 * (len(image_paths) + completed) / (2 * len(image_paths))),
        )
        _report_progress(
            progress_callback,
            percent,
            "正在导入图片",
            completed=completed,
            total=len(image_paths),
            last_file_name=last_file_name,
        )

    try:
        feature_results = extract_features(
            image_paths,
            model_path=model_path,
            total=len(image_paths),
            progress=report_extraction_progress,
        )
    except Exception as exc:
        logger.exception("图片特征提取任务失败")
        return 0, (str(exc),)

    model_hash = _get_model_hash(model_path)
    successful_stickers = []
    vectors = []
    metadata_list = []
    errors = []

    for (sticker, _), feature_result in zip(
        stickers_and_blob_paths,
        feature_results,
    ):
        if not feature_result.success:
            errors.append(
                f"{sticker.original_file_name}：{feature_result.error}"
            )
            continue

        successful_stickers.append(sticker)
        vectors.append(feature_result.vector)
        metadata_list.append(
            VectorMetadata(
                image_filename=sticker.original_file_name,
                model_hash=model_hash,
                sqlite_id=sticker.id,
                extraction_timestamp=time.time(),
                image_width=sticker.size_width,
                image_height=sticker.size_height,
            )
        )

    if not vectors:
        return 0, tuple(errors)

    with services.global_instances.vector_store_lock:
        vector_ids = vector_store.add_batch(vectors, metadata_list)

    vector_ids_by_sticker_id = {
        sticker.id: vector_id
        for sticker, vector_id in zip(successful_stickers, vector_ids)
    }
    try:
        services.global_instances.current_library_db.set_sticker_vector_ids(
            vector_ids_by_sticker_id
        )
    except Exception:
        with services.global_instances.vector_store_lock:
            vector_store.delete_batch(vector_ids)
        raise

    for sticker, vector_id in zip(successful_stickers, vector_ids):
        sticker.vectordb_id = vector_id

    return len(vector_ids), tuple(errors)


def import_images_with_result(
    file_paths: List[str],
    tags: Optional[List[Tag]] = None,
    *,
    generate_vectors: bool = False,
    progress: ProgressCallback | None = None,
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

    _report_progress(
        progress,
        0,
        "正在预处理",
        total=len(file_paths),
    )
    
    for file_path in file_paths:
        path = Path(file_path)
        
        if not path.exists():
            continue
        
        try:
            # 使用工具函数获取图片元数据
            metadata = get_image_metadata(path)

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

    existing_hashes = current_library_db.get_existing_sticker_hashes(
        sticker.hash for sticker, _, _ in candidates
    )
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
        1,
        "正在导入图片",
        total=len(import_candidates),
    )

    imported_stickers = []
    stickers_and_blob_paths = []
    work_units_per_image = 2 if generate_vectors else 1
    total_work_units = max(1, len(import_candidates) * work_units_per_image)
    for completed, (sticker, file_path, file_hash) in enumerate(
        import_candidates,
        start=1,
    ):
        # 只有新图片才需要进入 Blob 存储。
        blob_entity = current_blob_storage.store_file(file_path, file_hash)
        blob_path = current_blob_storage.read_file(blob_entity)
        imported_stickers.append(sticker)
        stickers_and_blob_paths.append((sticker, blob_path))
        percent = min(99, 1 + int(98 * completed / total_work_units))
        _report_progress(
            progress,
            percent,
            "正在导入图片",
            completed=completed,
            total=len(import_candidates),
            last_file_name=(
                None if generate_vectors else sticker.original_file_name
            ),
        )

    if imported_stickers:
        attempted_count = len(imported_stickers)
        inserted_stickers = current_library_db.add_stickers(imported_stickers)
        duplicate_count += attempted_count - len(inserted_stickers)
        inserted_object_ids = {id(sticker) for sticker in inserted_stickers}
        imported_stickers = inserted_stickers
        stickers_and_blob_paths = [
            (sticker, blob_path)
            for sticker, blob_path in stickers_and_blob_paths
            if id(sticker) in inserted_object_ids
        ]

    vectorized_count = 0
    vector_errors = ()
    if generate_vectors and stickers_and_blob_paths:
        try:
            vectorized_count, vector_errors = _generate_vectors(
                stickers_and_blob_paths,
                progress,
            )
        except Exception as exc:
            logger.exception("写入图片向量失败")
            vector_errors = (f"向量写入失败：{exc}",)

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
        total=len(imported_stickers),
        last_file_name=last_file_name,
    )

    return ImportImagesResult(
        imported_stickers=tuple(imported_stickers),
        duplicate_count=duplicate_count,
        vectorized_count=vectorized_count,
        vector_errors=vector_errors,
    )


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
    failed = pyqtSignal(str)
    progress_changed = pyqtSignal(object)

    def __init__(self, request: ImportImagesRequest):
        super().__init__()
        self._request = request

    @pyqtSlot()
    def run(self):
        try:
            result = import_images_with_result(
                list(self._request.file_paths),
                generate_vectors=self._request.generate_vectors,
                progress=self.progress_changed.emit,
            )
        except Exception as exc:
            logger.exception("导入图片失败")
            self.failed.emit(str(exc))
            return
        self.succeeded.emit(result)


class ImageImportService(QObject):
    """在独立 QThread 中执行每个导入请求。"""

    import_finished = pyqtSignal(object)
    import_failed = pyqtSignal(str)
    import_progress_changed = pyqtSignal(object)

    def __init__(self, parent: QObject | None = None):
        super().__init__(parent)
        self._jobs: dict[QThread, _ImportImagesWorker] = {}

    @property
    def active_job_count(self) -> int:
        return len(self._jobs)

    def start_import(self, request: ImportImagesRequest) -> None:
        if not isinstance(request, ImportImagesRequest):
            raise TypeError("request must be an ImportImagesRequest")

        thread = QThread(self)
        worker = _ImportImagesWorker(request)
        worker.moveToThread(thread)
        self._jobs[thread] = worker

        thread.started.connect(worker.run)
        worker.succeeded.connect(self.import_finished)
        worker.failed.connect(self.import_failed)
        worker.progress_changed.connect(self.import_progress_changed)
        worker.succeeded.connect(thread.quit)
        worker.failed.connect(thread.quit)
        worker.succeeded.connect(worker.deleteLater)
        worker.failed.connect(worker.deleteLater)
        thread.finished.connect(partial(self._release_job, thread))
        thread.finished.connect(thread.deleteLater)
        thread.start()

    def _release_job(self, thread: QThread) -> None:
        self._jobs.pop(thread, None)
