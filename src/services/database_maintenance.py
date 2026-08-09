# coding=utf-8
from __future__ import annotations

import logging
import threading
import time
from dataclasses import dataclass
from enum import Enum
from functools import partial
from typing import Callable

from PyQt6.QtCore import QObject, QThread, pyqtSignal, pyqtSlot

import apppath
import services.global_instances
from blob_storage import BlobFileEntity
from image_features_extractor import (
    ExtractionCancelledError,
    iter_features,
    normalize_image_path,
)
from services.image_vector_model import get_model_hash
from stickerdb.v1.sticker_db import StickerMaintenanceRecord
from stickerdb.vectordb import VectorMetadata, VectorRecord

logger = logging.getLogger(__name__)

VECTOR_BATCH_SIZE = 32
VECTOR_PREP_FRACTION = 0.3


class VectorMaintenanceScope(str, Enum):
    MISSING = "missing"
    ALL = "all"


@dataclass(frozen=True, slots=True)
class DatabaseMaintenanceOptions:
    delete_orphan_blobs: bool = True
    generate_vectors: bool = True
    vector_scope: VectorMaintenanceScope = VectorMaintenanceScope.MISSING
    delete_thumbnail_cache: bool = False

    def __post_init__(self) -> None:
        if not (
            self.delete_orphan_blobs
            or self.generate_vectors
            or self.delete_thumbnail_cache
        ):
            raise ValueError("至少需要选择一个维护操作")
        if not isinstance(self.vector_scope, VectorMaintenanceScope):
            raise TypeError("vector_scope must be a VectorMaintenanceScope")


@dataclass(frozen=True, slots=True)
class DatabaseMaintenanceProgress:
    percent: int
    task_name: str
    status: str
    completed: int = 0
    total: int = 0
    cancellable: bool = False

    def __post_init__(self) -> None:
        if not 0 <= self.percent <= 100:
            raise ValueError("percent must be between 0 and 100")
        if self.completed < 0 or self.total < 0 or self.completed > self.total:
            raise ValueError("invalid maintenance progress counts")


@dataclass(frozen=True, slots=True)
class DatabaseMaintenanceResult:
    deleted_blob_count: int = 0
    vectorized_count: int = 0
    relinked_vector_count: int = 0
    skipped_vector_count: int = 0
    deleted_thumbnail_count: int = 0
    blob_errors: tuple[str, ...] = ()
    vector_errors: tuple[str, ...] = ()
    thumbnail_errors: tuple[str, ...] = ()
    cancelled: bool = False


ProgressCallback = Callable[[DatabaseMaintenanceProgress], None]


def _overall_percent(task_index: int, task_count: int, fraction: float) -> int:
    normalized = min(1.0, max(0.0, fraction))
    return int(100 * (task_index + normalized) / task_count)


def _report_progress(
    callback: ProgressCallback | None,
    *,
    task_index: int,
    task_count: int,
    task_fraction: float,
    task_name: str,
    status: str,
    completed: int,
    total: int,
    cancellable: bool,
) -> None:
    if callback is None:
        return
    callback(
        DatabaseMaintenanceProgress(
            percent=_overall_percent(task_index, task_count, task_fraction),
            task_name=task_name,
            status=status,
            completed=completed,
            total=total,
            cancellable=cancellable,
        )
    )


def _existing_vector_for_sticker(
    vector_store,
    sticker: StickerMaintenanceRecord,
) -> VectorRecord | None:
    record = None
    if sticker.vectordb_id:
        record = vector_store.get(str(sticker.vectordb_id))
        if record is not None and record.metadata.sqlite_id == sticker.id:
            return record

    record = vector_store.get_by_sqlite_id(sticker.id)
    if record is not None and record.metadata.sqlite_id == sticker.id:
        return record
    return None


def _delete_orphan_blobs(
    records: list[StickerMaintenanceRecord],
    *,
    task_index: int,
    task_count: int,
    progress: ProgressCallback | None,
) -> tuple[int, tuple[str, ...]]:
    blob_storage = services.global_instances.current_blob_storage
    if blob_storage is None:
        raise RuntimeError("Blob存储未初始化，无法执行维护")

    task_name = "删除未引用的Blob数据"
    referenced = {
        (record.hash.lower(), record.extension.lower())
        for record in records
    }
    stored_entities = list(blob_storage.iter_file_entities())
    total = len(stored_entities)
    deleted_count = 0
    errors: list[str] = []

    _report_progress(
        progress,
        task_index=task_index,
        task_count=task_count,
        task_fraction=0.0,
        task_name=task_name,
        status="正在扫描Blob存储",
        completed=0,
        total=total,
        cancellable=False,
    )

    for completed, entity in enumerate(stored_entities, start=1):
        if (entity.hash.lower(), entity.extension.lower()) not in referenced:
            try:
                blob_storage.delete_file(entity)
                deleted_count += 1
            except Exception as exc:
                logger.exception("删除孤立Blob失败：%s", entity)
                errors.append(f"{entity.hash}{entity.extension}：{exc}")

        _report_progress(
            progress,
            task_index=task_index,
            task_count=task_count,
            task_fraction=completed / total if total else 1.0,
            task_name=task_name,
            status="正在清理Blob存储",
            completed=completed,
            total=total,
            cancellable=False,
        )

    if not stored_entities:
        _report_progress(
            progress,
            task_index=task_index,
            task_count=task_count,
            task_fraction=1.0,
            task_name=task_name,
            status="Blob存储清理完成",
            completed=0,
            total=0,
            cancellable=False,
        )

    return deleted_count, tuple(errors)


def _delete_thumbnail_cache(
    *,
    task_index: int,
    task_count: int,
    progress: ProgressCallback | None,
) -> tuple[int, tuple[str, ...]]:
    storage = services.global_instances.current_thumbnail_disk_storage
    if storage is None:
        raise RuntimeError("缩略图缓存存储未初始化，无法执行维护")

    task_name = "删除缩略图缓存"
    _report_progress(
        progress,
        task_index=task_index,
        task_count=task_count,
        task_fraction=0.0,
        task_name=task_name,
        status="正在删除缩略图缓存",
        completed=0,
        total=0,
        cancellable=False,
    )

    deleted_count, errors = storage.delete_all()

    provider = services.global_instances.current_thumbnail_provider
    if provider is not None:
        try:
            provider.clear_memory_cache()
        except Exception as exc:
            logger.exception("清空缩略图内存缓存失败")
            errors = [*errors, f"内存缓存清空失败：{exc}"]

    _report_progress(
        progress,
        task_index=task_index,
        task_count=task_count,
        task_fraction=1.0,
        task_name=task_name,
        status="缩略图缓存删除完成",
        completed=deleted_count,
        total=deleted_count,
        cancellable=False,
    )
    return deleted_count, tuple(errors)


def _generate_vectors(
    records: list[StickerMaintenanceRecord],
    scope: VectorMaintenanceScope,
    *,
    task_index: int,
    task_count: int,
    progress: ProgressCallback | None,
    cancel_event: threading.Event | None,
) -> tuple[int, int, int, tuple[str, ...], bool]:
    database = services.global_instances.current_library_db
    blob_storage = services.global_instances.current_blob_storage
    vector_store = services.global_instances.current_vector_store
    if database is None or blob_storage is None:
        raise RuntimeError("图库尚未初始化，无法生成向量")
    if vector_store is None:
        raise RuntimeError("向量数据库未初始化，无法生成向量")

    model_path = apppath.app_path / "vit_b_16_features.onnx"
    if not model_path.is_file():
        raise RuntimeError(f"特征提取模型不存在：{model_path}")
    model_hash = get_model_hash(model_path)

    task_name = "生成图片特征向量"
    total = len(records)
    vectorized_count = 0
    relinked_count = 0
    skipped_count = 0
    errors: list[str] = []

    if not records:
        _report_progress(
            progress,
            task_index=task_index,
            task_count=task_count,
            task_fraction=1.0,
            task_name=task_name,
            status="向量生成完成",
            completed=0,
            total=0,
            cancellable=True,
        )
        return vectorized_count, relinked_count, skipped_count, tuple(errors), False

    if scope is VectorMaintenanceScope.ALL:
        prep_status = "正在准备图片向量"
    else:
        prep_status = "正在检查图片向量"

    _report_progress(
        progress,
        task_index=task_index,
        task_count=task_count,
        task_fraction=0.0,
        task_name=task_name,
        status=prep_status,
        completed=0,
        total=total,
        cancellable=True,
    )

    if cancel_event is not None and cancel_event.is_set():
        return vectorized_count, relinked_count, skipped_count, tuple(errors), True

    if scope is VectorMaintenanceScope.ALL:
        with services.global_instances.vector_store_lock:
            vector_store.reset()

    relink_ids: dict[int, str] = {}
    candidates: list[StickerMaintenanceRecord] = []
    image_paths: list[str] = []

    def add_candidate(sticker: StickerMaintenanceRecord) -> None:
        try:
            image_path = blob_storage.read_file(
                BlobFileEntity(sticker.hash, sticker.extension)
            )
        except FileNotFoundError as exc:
            errors.append(f"{sticker.original_file_name}：{exc}")
            return
        candidates.append(sticker)
        image_paths.append(image_path)

    for batch_start in range(0, total, VECTOR_BATCH_SIZE):
        if cancel_event is not None and cancel_event.is_set():
            return vectorized_count, relinked_count, skipped_count, tuple(errors), True

        batch = records[batch_start:batch_start + VECTOR_BATCH_SIZE]

        if scope is VectorMaintenanceScope.MISSING:
            existing_by_sticker_id: dict[int, VectorRecord | None] = {}
            with services.global_instances.vector_store_lock:
                for sticker in batch:
                    existing_by_sticker_id[sticker.id] = _existing_vector_for_sticker(
                        vector_store,
                        sticker,
                    )

            for sticker in batch:
                existing = existing_by_sticker_id[sticker.id]
                if existing is not None:
                    if sticker.vectordb_id != existing.id:
                        relink_ids[sticker.id] = existing.id
                    else:
                        skipped_count += 1
                    continue
                add_candidate(sticker)
        else:
            for sticker in batch:
                add_candidate(sticker)

        checked = min(total, batch_start + len(batch))
        completed = min(total, checked - len(candidates))
        _report_progress(
            progress,
            task_index=task_index,
            task_count=task_count,
            task_fraction=VECTOR_PREP_FRACTION * checked / total,
            task_name=task_name,
            status=prep_status,
            completed=completed,
            total=total,
            cancellable=True,
        )

    if cancel_event is not None and cancel_event.is_set():
        return vectorized_count, relinked_count, skipped_count, tuple(errors), True

    if relink_ids:
        try:
            database.replace_sticker_vector_ids(relink_ids)
        except Exception as exc:
            logger.exception("SQLite回填向量关联失败")
            errors.append(f"向量关联修复失败：{exc}")
        else:
            relinked_count += len(relink_ids)

    candidate_total = len(image_paths)
    if not candidate_total:
        _report_progress(
            progress,
            task_index=task_index,
            task_count=task_count,
            task_fraction=1.0,
            task_name=task_name,
            status="向量生成完成",
            completed=total,
            total=total,
            cancellable=True,
        )
        return vectorized_count, relinked_count, skipped_count, tuple(errors), False

    sticker_by_path = {
        normalize_image_path(blob_path): sticker
        for sticker, blob_path in zip(candidates, image_paths)
    }

    try:
        for result_batch in iter_features(
            image_paths,
            model_path=model_path,
            batch_size=VECTOR_BATCH_SIZE,
            total=candidate_total,
            cancel_event=cancel_event,
        ):
            batch_stickers: list[StickerMaintenanceRecord] = []
            batch_vectors = []
            batch_metadata = []
            for feature_result in result_batch.results:
                sticker = sticker_by_path.get(feature_result.image_path)
                if sticker is None:
                    errors.append(
                        f"{feature_result.image_path}：缺少对应的维护记录"
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

            if batch_stickers:
                try:
                    with services.global_instances.vector_store_lock:
                        new_vector_ids = vector_store.add_batch(
                            batch_vectors,
                            batch_metadata,
                        )
                    if len(new_vector_ids) != len(batch_stickers):
                        raise RuntimeError(
                            "向量数据库返回的ID数量与图片数量不一致"
                        )
                    database.replace_sticker_vector_ids(
                        {
                            sticker.id: vector_id
                            for sticker, vector_id in zip(
                                batch_stickers,
                                new_vector_ids,
                            )
                        }
                    )
                except Exception as exc:
                    logger.exception("写入图片向量失败")
                    errors.append(f"向量写入失败：{exc}")
                else:
                    vectorized_count += len(new_vector_ids)

            completed = min(
                total,
                total - candidate_total + result_batch.progress.completed,
            )
            extraction_ratio = result_batch.progress.completed / candidate_total
            if extraction_ratio >= 1.0:
                task_fraction = 1.0
            else:
                task_fraction = (
                    VECTOR_PREP_FRACTION
                    + (1 - VECTOR_PREP_FRACTION) * extraction_ratio
                )
            _report_progress(
                progress,
                task_index=task_index,
                task_count=task_count,
                task_fraction=task_fraction,
                task_name=task_name,
                status="正在生成图片向量",
                completed=completed,
                total=total,
                cancellable=True,
            )
    except ExtractionCancelledError:
        return vectorized_count, relinked_count, skipped_count, tuple(errors), True

    return vectorized_count, relinked_count, skipped_count, tuple(errors), False


def run_database_maintenance(
    options: DatabaseMaintenanceOptions,
    *,
    progress: ProgressCallback | None = None,
    cancel_event: threading.Event | None = None,
) -> DatabaseMaintenanceResult:
    if not isinstance(options, DatabaseMaintenanceOptions):
        raise TypeError("options must be a DatabaseMaintenanceOptions")

    database = services.global_instances.current_library_db
    if database is None:
        raise RuntimeError("仓库数据库尚未初始化")

    task_count = (
        int(options.delete_orphan_blobs)
        + int(options.generate_vectors)
        + int(options.delete_thumbnail_cache)
    )
    task_index = 0
    deleted_blob_count = 0
    vectorized_count = 0
    relinked_vector_count = 0
    skipped_vector_count = 0
    deleted_thumbnail_count = 0
    blob_errors: tuple[str, ...] = ()
    vector_errors: tuple[str, ...] = ()
    thumbnail_errors: tuple[str, ...] = ()

    if options.delete_orphan_blobs:
        records = database.list_maintenance_records()
        deleted_blob_count, blob_errors = _delete_orphan_blobs(
            records,
            task_index=task_index,
            task_count=task_count,
            progress=progress,
        )
        task_index += 1

    cancelled = False
    if options.generate_vectors:
        records = database.list_maintenance_records()
        (
            vectorized_count,
            relinked_vector_count,
            skipped_vector_count,
            vector_errors,
            cancelled,
        ) = _generate_vectors(
            records,
            options.vector_scope,
            task_index=task_index,
            task_count=task_count,
            progress=progress,
            cancel_event=cancel_event,
        )

    if options.delete_thumbnail_cache:
        deleted_thumbnail_count, thumbnail_errors = _delete_thumbnail_cache(
            task_index=task_index,
            task_count=task_count,
            progress=progress,
        )
        task_index += 1

    return DatabaseMaintenanceResult(
        deleted_blob_count=deleted_blob_count,
        vectorized_count=vectorized_count,
        relinked_vector_count=relinked_vector_count,
        skipped_vector_count=skipped_vector_count,
        deleted_thumbnail_count=deleted_thumbnail_count,
        blob_errors=blob_errors,
        vector_errors=vector_errors,
        thumbnail_errors=thumbnail_errors,
        cancelled=cancelled,
    )


class _DatabaseMaintenanceWorker(QObject):
    succeeded = pyqtSignal(object)
    cancelled = pyqtSignal(object)
    failed = pyqtSignal(str)
    progress_changed = pyqtSignal(object)

    def __init__(
        self,
        options: DatabaseMaintenanceOptions,
        cancel_event: threading.Event,
    ) -> None:
        super().__init__()
        self._options = options
        self._cancel_event = cancel_event

    @pyqtSlot()
    def run(self) -> None:
        try:
            result = run_database_maintenance(
                self._options,
                progress=self.progress_changed.emit,
                cancel_event=self._cancel_event,
            )
        except Exception as exc:
            logger.exception("数据库维护失败")
            self.failed.emit(str(exc))
            return

        if result.cancelled:
            self.cancelled.emit(result)
        else:
            self.succeeded.emit(result)


class DatabaseMaintenanceService(QObject):
    maintenance_finished = pyqtSignal(object)
    maintenance_cancelled = pyqtSignal(object)
    maintenance_failed = pyqtSignal(str)
    maintenance_progress_changed = pyqtSignal(object)

    def __init__(self, parent: QObject | None = None) -> None:
        super().__init__(parent)
        self._jobs: dict[QThread, _DatabaseMaintenanceWorker] = {}
        self._cancel_events: dict[QThread, threading.Event] = {}
        self._can_cancel = False

    @property
    def active_job_count(self) -> int:
        return len(self._jobs)

    def start_maintenance(self, options: DatabaseMaintenanceOptions) -> None:
        if not isinstance(options, DatabaseMaintenanceOptions):
            raise TypeError("options must be a DatabaseMaintenanceOptions")
        if self._jobs:
            raise RuntimeError("已有数据库维护任务正在运行")

        thread = QThread(self)
        cancel_event = threading.Event()
        worker = _DatabaseMaintenanceWorker(options, cancel_event)
        worker.moveToThread(thread)
        self._jobs[thread] = worker
        self._cancel_events[thread] = cancel_event
        self._can_cancel = False

        thread.started.connect(worker.run)
        worker.succeeded.connect(self.maintenance_finished)
        worker.cancelled.connect(self.maintenance_cancelled)
        worker.failed.connect(self.maintenance_failed)
        worker.progress_changed.connect(self._forward_progress)
        worker.succeeded.connect(thread.quit)
        worker.cancelled.connect(thread.quit)
        worker.failed.connect(thread.quit)
        worker.succeeded.connect(worker.deleteLater)
        worker.cancelled.connect(worker.deleteLater)
        worker.failed.connect(worker.deleteLater)
        thread.finished.connect(partial(self._release_job, thread))
        thread.finished.connect(thread.deleteLater)
        thread.start()

    @pyqtSlot(object)
    def _forward_progress(self, progress: DatabaseMaintenanceProgress) -> None:
        self._can_cancel = progress.cancellable
        self.maintenance_progress_changed.emit(progress)

    def cancel_maintenance(self) -> bool:
        if not self._can_cancel or not self._cancel_events:
            return False

        cancel_event = next(iter(self._cancel_events.values()))
        if cancel_event.is_set():
            return False
        cancel_event.set()
        return True

    def _release_job(self, thread: QThread) -> None:
        self._jobs.pop(thread, None)
        self._cancel_events.pop(thread, None)
        self._can_cancel = False
