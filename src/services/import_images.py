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
from pathlib import Path
from typing import Callable, List, Optional

import apppath
import services.global_instances
from batch_job_runner.exceptions import JobCancelledError
from batch_job_runner.models import wrapper_input_identifier
from services.image_vector_model import get_model_hash
from commons.dto import StickerImage, Tag
from commons.image_metadata import StickerImageMetadata
from image_features_extractor import DEFAULT_MODEL_FILENAME, VectorBatchJobRunner
from image_text_extractor import OcrBatchJobRunner, normalize_image_path
from stickerdb.vectordb import VectorMetadata
from utils.image_metadata import get_image_metadata

logger = logging.getLogger(__name__)

IMPORT_BATCH_SIZE = 32
# 导入进度区间：0-5 预处理，5-15 写 SQLite，15-40 OCR，40-100 向量。
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


@dataclass(frozen=True, slots=True)
class ImportCandidate:
    """准备阶段产出的单张待导入图片。"""

    sticker: StickerImage
    file_path: str
    file_hash: str


@dataclass(frozen=True)
class ImportImagesProgress:
    percent: int
    status: str
    completed: int = 0
    total: int = 0

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
) -> None:
    if callback is not None:
        callback(
            ImportImagesProgress(
                percent=percent,
                status=status,
                completed=completed,
                total=total,
            )
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
        normalize_image_path(blob_path): sticker
        for sticker, blob_path in stickers_and_blob_paths
    }
    model_hash = get_model_hash(model_path)
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

    runner = VectorBatchJobRunner(model_path)
    try:
        for result_batch in runner.iter_results(
            image_paths,
            total=total,
            cancel_event=cancel_event,
        ):
            # 子进程结果不保证顺序，先按路径映射回 StickerImage，再成批写入
            # 向量库和 SQLite；失败项只记录错误，不影响同批其他图片。
            batch_stickers: list[StickerImage] = []
            batch_vectors = []
            batch_metadata = []

            for wrapper in result_batch.results:
                image_path = wrapper_input_identifier(wrapper)
                sticker = sticker_by_path.get(image_path)
                if sticker is None:
                    errors.append(f"{image_path}：缺少对应的导入记录")
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
            )
    except JobCancelledError:
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
        normalize_image_path(blob_path): sticker
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

    runner = OcrBatchJobRunner()
    try:
        for result_batch in runner.iter_results(
            image_paths,
            total=total,
            cancel_event=cancel_event,
        ):
            # 把 OCR 结果按路径映射回 sticker，成功文本批量回填数据库；
            # wrapper.hasException 表示单条失败，任务仍继续。
            text_by_sticker_id: dict[int, str | None] = {}

            for wrapper in result_batch.results:
                image_path = wrapper_input_identifier(wrapper)
                sticker = sticker_by_path.get(image_path)
                if sticker is None:
                    errors.append(f"{image_path}：缺少对应的导入记录")
                    continue
                if wrapper.hasException:
                    errors.append(
                        f"{sticker.original_file_name}：{wrapper.error}"
                    )
                    continue

                _, text = wrapper.data
                sticker.text_in_image = text
                text_by_sticker_id[sticker.id] = text

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
            )
    except JobCancelledError:
        return ocr_count, tuple(errors)
    except Exception as exc:
        logger.exception("图片文字识别任务失败")
        return ocr_count, tuple(errors + [str(exc)])

    return ocr_count, tuple(errors)


def _prepare_candidates(
    file_paths: List[str],
    tags: Optional[List[Tag]] = None,
    *,
    progress: ProgressCallback | None = None,
    cancel_event: threading.Event | None = None,
) -> tuple[list[ImportCandidate], int, bool]:
    """读取图片元数据并过滤同一请求内的重复 hash。"""
    candidates: list[ImportCandidate] = []
    request_hashes: set[str] = set()
    duplicate_count = 0

    _report_progress(
        progress,
        0,
        "正在预处理图片",
        total=len(file_paths),
    )
    if _is_cancelled(cancel_event):
        return candidates, duplicate_count, True

    for file_path in file_paths:
        if _is_cancelled(cancel_event):
            return candidates, duplicate_count, True

        path = Path(file_path)
        if not path.exists():
            continue

        try:
            metadata = get_image_metadata(path)
            if _is_cancelled(cancel_event):
                return candidates, duplicate_count, True

            if metadata.hash in request_hashes:
                duplicate_count += 1
                continue
            request_hashes.add(metadata.hash)

            sticker = _metadata_to_sticker_image(metadata, path)
            if tags:
                for tag in tags:
                    sticker.tags.append(tag)
            candidates.append(
                ImportCandidate(
                    sticker=sticker,
                    file_path=file_path,
                    file_hash=metadata.hash,
                )
            )
        except (OSError, ValueError) as exc:
            logger.warning("无法读取图片 %s: %s", file_path, exc)
            continue

    return candidates, duplicate_count, False


def _select_new_candidates(
    candidates: list[ImportCandidate],
    current_library_db,
    *,
    cancel_event: threading.Event | None = None,
) -> tuple[list[ImportCandidate], int, bool]:
    """过滤图库中已经存在的 hash。"""
    if _is_cancelled(cancel_event):
        return [], 0, True

    existing_hashes = current_library_db.get_existing_sticker_hashes(
        candidate.sticker.hash for candidate in candidates
    )
    if _is_cancelled(cancel_event):
        return [], 0, True

    duplicate_count = sum(
        1
        for candidate in candidates
        if candidate.sticker.hash in existing_hashes
    )
    import_candidates = [
        candidate
        for candidate in candidates
        if candidate.sticker.hash not in existing_hashes
    ]
    return import_candidates, duplicate_count, False


def _commit_candidates(
    import_candidates: list[ImportCandidate],
    *,
    current_library_db,
    current_blob_storage,
    progress: ProgressCallback | None = None,
    cancel_event: threading.Event | None = None,
    run_enrichment: bool = False,
) -> tuple[list[StickerImage], list[tuple[StickerImage, str]], int, bool]:
    """分批复制 Blob 并提交 SQLite，返回实际插入的图片。"""
    candidate_count = len(import_candidates)
    imported_stickers: list[StickerImage] = []
    all_inserted_stickers_and_blob_paths: list[tuple[StickerImage, str]] = []
    duplicate_count = 0

    _report_progress(
        progress,
        PREPROCESS_END_PERCENT,
        "正在写入图库",
        total=candidate_count,
    )

    for batch_start in range(0, candidate_count, IMPORT_BATCH_SIZE):
        if _is_cancelled(cancel_event):
            return (
                imported_stickers,
                all_inserted_stickers_and_blob_paths,
                duplicate_count,
                True,
            )

        batch_candidates = import_candidates[
            batch_start : batch_start + IMPORT_BATCH_SIZE
        ]
        batch_stickers = []
        batch_stickers_and_blob_paths = []
        for candidate in batch_candidates:
            if _is_cancelled(cancel_event):
                return (
                    imported_stickers,
                    all_inserted_stickers_and_blob_paths,
                    duplicate_count,
                    True,
                )

            blob_entity = current_blob_storage.store_file(
                candidate.file_path,
                candidate.file_hash,
                extension_override=candidate.sticker.extension,
            )
            if _is_cancelled(cancel_event):
                return (
                    imported_stickers,
                    all_inserted_stickers_and_blob_paths,
                    duplicate_count,
                    True,
                )

            blob_path = current_blob_storage.read_file(blob_entity)
            batch_stickers.append(candidate.sticker)
            batch_stickers_and_blob_paths.append(
                (candidate.sticker, blob_path)
            )

        if _is_cancelled(cancel_event):
            return (
                imported_stickers,
                all_inserted_stickers_and_blob_paths,
                duplicate_count,
                True,
            )

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
        sqlite_end = SQLITE_END_PERCENT if run_enrichment else 100
        percent = _percent_in_range(
            completed,
            candidate_count,
            PREPROCESS_END_PERCENT,
            sqlite_end,
        )
        _report_progress(
            progress,
            percent,
            "正在写入图库",
            completed=completed,
            total=candidate_count,
        )

        if _is_cancelled(cancel_event):
            return (
                imported_stickers,
                all_inserted_stickers_and_blob_paths,
                duplicate_count,
                True,
            )

    return (
        imported_stickers,
        all_inserted_stickers_and_blob_paths,
        duplicate_count,
        False,
    )


def _run_enrichment(
    stickers_and_blob_paths: list[tuple[StickerImage, str]],
    *,
    generate_vectors: bool,
    extract_text: bool,
    progress: ProgressCallback | None = None,
    cancel_event: threading.Event | None = None,
) -> tuple[int, tuple[str, ...], int, tuple[str, ...]]:
    """对已入库图片执行可选的 OCR 与向量生成。"""
    ocr_count = 0
    ocr_errors: tuple[str, ...] = ()
    vectorized_count = 0
    vector_errors: tuple[str, ...] = ()

    if extract_text and stickers_and_blob_paths:
        try:
            ocr_count, ocr_errors = _extract_texts(
                stickers_and_blob_paths,
                progress,
                cancel_event=cancel_event,
                start_percent=OCR_START_PERCENT,
                end_percent=OCR_END_PERCENT if generate_vectors else 100,
            )
        except Exception as exc:
            logger.exception("图片文字识别失败")
            ocr_errors = (*ocr_errors, f"文字识别失败：{exc}")

    if generate_vectors and stickers_and_blob_paths:
        try:
            vectorized_count, vector_errors = _generate_vectors(
                stickers_and_blob_paths,
                progress,
                cancel_event=cancel_event,
                start_percent=VECTOR_START_PERCENT,
                end_percent=100,
            )
        except Exception as exc:
            logger.exception("写入图片向量失败")
            vector_errors = (*vector_errors, f"向量写入失败：{exc}")

    return ocr_count, ocr_errors, vectorized_count, vector_errors


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
    imported_stickers: list[StickerImage] = []
    all_inserted_stickers_and_blob_paths: list[tuple[StickerImage, str]] = []
    duplicate_count = 0
    vectorized_count = 0
    vector_errors: tuple[str, ...] = ()
    ocr_count = 0
    ocr_errors: tuple[str, ...] = ()

    def make_result(*, cancelled: bool = False) -> ImportImagesResult:
        return ImportImagesResult(
            imported_stickers=tuple(imported_stickers),
            duplicate_count=duplicate_count,
            vectorized_count=vectorized_count,
            vector_errors=vector_errors,
            ocr_count=ocr_count,
            ocr_errors=ocr_errors,
            cancelled=cancelled,
        )

    candidates, request_duplicate_count, cancelled = _prepare_candidates(
        file_paths,
        tags,
        progress=progress,
        cancel_event=cancel_event,
    )
    if cancelled:
        return make_result(cancelled=True)
    duplicate_count += request_duplicate_count

    import_candidates, existing_duplicate_count, cancelled = _select_new_candidates(
        candidates,
        current_library_db,
        cancel_event=cancel_event,
    )
    if cancelled:
        return make_result(cancelled=True)
    duplicate_count += existing_duplicate_count

    (
        imported_stickers,
        all_inserted_stickers_and_blob_paths,
        batch_duplicate_count,
        cancelled,
    ) = _commit_candidates(
        import_candidates,
        current_library_db=current_library_db,
        current_blob_storage=current_blob_storage,
        progress=progress,
        cancel_event=cancel_event,
        run_enrichment=generate_vectors or extract_text,
    )
    if cancelled:
        return make_result(cancelled=True)
    duplicate_count += batch_duplicate_count

    if generate_vectors or extract_text:
        ocr_count, ocr_errors, vectorized_count, vector_errors = _run_enrichment(
            all_inserted_stickers_and_blob_paths,
            generate_vectors=generate_vectors,
            extract_text=extract_text,
            progress=progress,
            cancel_event=cancel_event,
        )

    if _is_cancelled(cancel_event):
        return make_result(cancelled=True)

    _report_progress(
        progress,
        100,
        "导入完成",
        completed=len(imported_stickers),
        total=len(import_candidates),
    )

    return make_result()
