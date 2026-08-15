# coding=utf-8
"""数据库维护服务。

负责孤立 Blob 清理、OCR 文本补全、向量重建/关联修复和缩略图缓存清理。
OCR 与向量部分通过 batch_job_runner 在子进程中执行，本模块只提供同步函数，
后台线程由 services.background_job 管理。
"""
from __future__ import annotations

import logging
import threading
import time
from dataclasses import dataclass
from enum import Enum
from typing import Callable

import apppath
import services.global_instances
from batch_job_runner.exceptions import JobCancelledError
from batch_job_runner.models import wrapper_input_identifier
from blob_storage import BlobFileEntity
from image_features_extractor import (
    DEFAULT_MODEL_FILENAME,
    VectorBatchJobRunner,
    normalize_image_path,
)
from image_text_extractor import OcrBatchJobRunner
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
    extract_text: bool = True
    generate_vectors: bool = True
    vector_scope: VectorMaintenanceScope = VectorMaintenanceScope.MISSING
    delete_thumbnail_cache: bool = False

    def __post_init__(self) -> None:
        if not (
            self.delete_orphan_blobs
            or self.extract_text
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
    ocr_count: int = 0
    vectorized_count: int = 0
    relinked_vector_count: int = 0
    skipped_vector_count: int = 0
    deleted_thumbnail_count: int = 0
    blob_errors: tuple[str, ...] = ()
    ocr_errors: tuple[str, ...] = ()
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


def _extract_missing_texts(
    records: list[StickerMaintenanceRecord],
    *,
    task_index: int,
    task_count: int,
    progress: ProgressCallback | None,
    cancel_event: threading.Event | None,
) -> tuple[int, tuple[str, ...], bool]:
    database = services.global_instances.current_library_db
    blob_storage = services.global_instances.current_blob_storage
    if database is None or blob_storage is None:
        raise RuntimeError("图库尚未初始化，无法识别图片文字")

    task_name = "识别图片文字"
    candidates: list[StickerMaintenanceRecord] = []
    image_paths: list[str] = []
    errors: list[str] = []

    for record in records:
        if record.text_in_image is not None:
            continue
        try:
            image_path = blob_storage.read_file(
                BlobFileEntity(record.hash, record.extension)
            )
        except Exception as exc:
            errors.append(f"{record.original_file_name}：{exc}")
            continue
        candidates.append(record)
        image_paths.append(image_path)

    candidate_total = len(image_paths)
    if not candidate_total:
        _report_progress(
            progress,
            task_index=task_index,
            task_count=task_count,
            task_fraction=1.0,
            task_name=task_name,
            status="文字识别完成",
            completed=0,
            total=0,
            cancellable=True,
        )
        return 0, tuple(errors), False

    _report_progress(
        progress,
        task_index=task_index,
        task_count=task_count,
        task_fraction=0.0,
        task_name=task_name,
        status="正在识别图片文字",
        completed=0,
        total=candidate_total,
        cancellable=True,
    )

    if cancel_event is not None and cancel_event.is_set():
        return 0, tuple(errors), True

    sticker_by_path = {
        normalize_image_path(blob_path): sticker
        for sticker, blob_path in zip(candidates, image_paths)
    }
    ocr_count = 0

    runner = OcrBatchJobRunner()
    try:
        for result_batch in runner.iter_results(
            image_paths,
            total=candidate_total,
            cancel_event=cancel_event,
        ):
            # 按子进程返回的路径把结果映射回维护记录；失败的 wrapper 只记入
            # errors，不中断整个 OCR 任务。
            text_by_sticker_id: dict[int, str | None] = {}
            for wrapper in result_batch.results:
                image_path = wrapper_input_identifier(wrapper)
                sticker = sticker_by_path.get(image_path)
                if sticker is None:
                    errors.append(f"{image_path}：缺少对应的维护记录")
                    continue
                if wrapper.hasException:
                    errors.append(
                        f"{sticker.original_file_name}：{wrapper.error}"
                    )
                    continue
                _, text = wrapper.data
                text_by_sticker_id[sticker.id] = text

            if text_by_sticker_id:
                try:
                    database.set_sticker_texts(text_by_sticker_id)
                except Exception as exc:
                    logger.exception("回填图片文字失败")
                    errors.append(f"文字回填失败：{exc}")
                else:
                    ocr_count += len(text_by_sticker_id)

            completed = min(candidate_total, result_batch.progress.completed)
            _report_progress(
                progress,
                task_index=task_index,
                task_count=task_count,
                task_fraction=completed / candidate_total,
                task_name=task_name,
                status="正在识别图片文字",
                completed=completed,
                total=candidate_total,
                cancellable=True,
            )
    except JobCancelledError:
        return ocr_count, tuple(errors), True
    except Exception as exc:
        logger.exception("图片文字识别任务失败")
        return ocr_count, tuple(errors + [str(exc)]), False

    return ocr_count, tuple(errors), False


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

    model_path = apppath.app_path / DEFAULT_MODEL_FILENAME
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
        # 全量重建：先清空向量库，后面所有记录都会重新生成向量。
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
            # 增量修复：先检查当前批次已有的向量；能复用的记录改关联，
            # 完全没有向量的才进入候选列表。
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
        # 准备阶段已完成数 = 已检查数 - 候选数（即已跳过/复用的记录数）。
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

    runner = VectorBatchJobRunner(model_path)
    try:
        for result_batch in runner.iter_results(
            image_paths,
            total=candidate_total,
            cancel_event=cancel_event,
        ):
            # 每个结果批次先按路径归集 sticker、向量和元数据，再在向量库锁内
            # 一次性写入 Chroma 与 SQLite，保证两边的关联一致。
            batch_stickers: list[StickerMaintenanceRecord] = []
            batch_vectors = []
            batch_metadata = []
            for wrapper in result_batch.results:
                image_path = wrapper_input_identifier(wrapper)
                sticker = sticker_by_path.get(image_path)
                if sticker is None:
                    errors.append(f"{image_path}：缺少对应的维护记录")
                    continue
                if wrapper.hasException:
                    errors.append(
                        f"{sticker.original_file_name}：{wrapper.error}"
                    )
                    continue
                _, vector = wrapper.data
                batch_stickers.append(sticker)
                batch_vectors.append(vector)
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
            # 总体进度 = 30% 准备阶段 + 70% 向量生成阶段；生成阶段按候选
            # 条数折算，并把已有向量造成的“已完成”基数带入界面计数。
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
    except JobCancelledError:
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
        + int(options.extract_text)
        + int(options.generate_vectors)
        + int(options.delete_thumbnail_cache)
    )
    task_index = 0
    deleted_blob_count = 0
    ocr_count = 0
    vectorized_count = 0
    relinked_vector_count = 0
    skipped_vector_count = 0
    deleted_thumbnail_count = 0
    blob_errors: tuple[str, ...] = ()
    ocr_errors: tuple[str, ...] = ()
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
    if options.extract_text:
        records = database.list_maintenance_records()
        ocr_count, ocr_errors, ocr_cancelled = _extract_missing_texts(
            records,
            task_index=task_index,
            task_count=task_count,
            progress=progress,
            cancel_event=cancel_event,
        )
        cancelled = cancelled or ocr_cancelled
        task_index += 1

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
        task_index += 1

    if options.delete_thumbnail_cache:
        deleted_thumbnail_count, thumbnail_errors = _delete_thumbnail_cache(
            task_index=task_index,
            task_count=task_count,
            progress=progress,
        )
        task_index += 1

    return DatabaseMaintenanceResult(
        deleted_blob_count=deleted_blob_count,
        ocr_count=ocr_count,
        vectorized_count=vectorized_count,
        relinked_vector_count=relinked_vector_count,
        skipped_vector_count=skipped_vector_count,
        deleted_thumbnail_count=deleted_thumbnail_count,
        blob_errors=blob_errors,
        ocr_errors=ocr_errors,
        vector_errors=vector_errors,
        thumbnail_errors=thumbnail_errors,
        cancelled=cancelled,
    )
