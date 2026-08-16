# coding=utf-8
"""图库备份导入。

把“导出图库”生成的 metadata.json 与 set_N/ 目录合并进当前图库：
同名标签不修改，图片按 hash 去重合并，不做 OCR、不生成向量。
取消只在逐张处理图片的阶段开放，单张图片先复制 blob 再写 SQLite。
"""
from __future__ import annotations

import datetime
import json
import logging
import re
import threading
from dataclasses import dataclass
from pathlib import Path, PurePosixPath
from typing import Callable

from PyQt6.QtCore import QObject, pyqtSignal

import services.global_instances
from blob_storage import BlobStorage
from commons.image_metadata import StickerImageMetadata
from commons.dto import StickerImage, Tag
from services.background_job import BackgroundJobService
from stickerdb.v1.sticker_db import StickerDBV1
from utils.image_metadata import get_image_metadata

logger = logging.getLogger(__name__)

IMAGE_PATH_PATTERN = re.compile(r"^set_[1-9][0-9]*/[^/\\]+$")
HASH_PATTERN = re.compile(r"^[0-9a-fA-F]{40}$")
RGB_PATTERN = re.compile(r"^#[0-9A-Fa-f]{6}$")

# 进度区间：0-5 读取与校验，5-100 逐张导入图片。
TAG_MERGE_END_PERCENT = 5


class LibraryImportError(RuntimeError):
    """图库备份无法导入时抛出。"""


@dataclass(frozen=True, slots=True)
class LibraryImportProgress:
    percent: int
    status: str
    completed: int = 0
    total: int = 0
    last_file_name: str | None = None
    cancellable: bool = False

    def __post_init__(self) -> None:
        if not 0 <= self.percent <= 100:
            raise ValueError("percent must be between 0 and 100")
        if self.completed < 0 or self.total < 0 or self.completed > self.total:
            raise ValueError("invalid import progress counts")


@dataclass(frozen=True, slots=True)
class LibraryImportResult:
    metadata_path: str
    added_image_count: int = 0
    merged_tag_image_count: int = 0
    added_tag_count: int = 0
    damaged_count: int = 0
    errors: tuple[str, ...] = ()
    cancelled: bool = False


@dataclass(frozen=True, slots=True)
class _BackupImage:
    relative_path: str
    hash: str
    imported_at: datetime.datetime
    modification_date: datetime.datetime
    text_in_image: str | None
    tag_names: tuple[str, ...]


ProgressCallback = Callable[[LibraryImportProgress], None]


def _is_cancelled(cancel_event: threading.Event | None) -> bool:
    return cancel_event is not None and cancel_event.is_set()


def _report_progress(
    callback: ProgressCallback | None,
    percent: int,
    status: str,
    *,
    completed: int,
    total: int,
    last_file_name: str | None = None,
    cancellable: bool,
) -> None:
    if callback is not None:
        callback(
            LibraryImportProgress(
                percent=percent,
                status=status,
                completed=completed,
                total=total,
                last_file_name=last_file_name,
                cancellable=cancellable,
            )
        )


def _read_metadata(metadata_path: str | Path) -> dict:
    path = Path(metadata_path)
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise LibraryImportError(f"无法读取备份文件：{exc}") from exc
    if not isinstance(value, dict):
        raise LibraryImportError("备份文件内容不是 JSON 对象。")
    return value


def _parse_datetime(value: object, field: str, path: str) -> datetime.datetime:
    if not isinstance(value, str):
        raise LibraryImportError(f"图片 {path} 的 {field} 无效。")
    try:
        parsed = datetime.datetime.fromisoformat(value)
    except ValueError as exc:
        raise LibraryImportError(f"图片 {path} 的 {field} 无效。") from exc
    # 导出时把本地时间转为带时区值写出，这里还原成 naive 本地时间。
    if parsed.tzinfo is not None:
        parsed = parsed.astimezone().replace(tzinfo=None)
    return parsed


def _parse_images(raw_images: object) -> list[_BackupImage]:
    if not isinstance(raw_images, list):
        raise LibraryImportError("备份文件的 images 字段必须是数组。")

    images = []
    for index, item in enumerate(raw_images, start=1):
        if not isinstance(item, dict):
            raise LibraryImportError(f"第 {index} 条图片记录无效。")

        relative_path = item.get("path")
        if (
            not isinstance(relative_path, str)
            or not IMAGE_PATH_PATTERN.fullmatch(relative_path)
            or PurePosixPath(relative_path).name in {".", ".."}
        ):
            raise LibraryImportError(f"图片路径无效：{relative_path!r}")

        hash_value = item.get("hash")
        if not isinstance(hash_value, str) or not HASH_PATTERN.fullmatch(hash_value):
            raise LibraryImportError(f"图片 {relative_path} 的 hash 无效。")

        text_in_image = item.get("text_in_image")
        if text_in_image is not None and not isinstance(text_in_image, str):
            raise LibraryImportError(
                f"图片 {relative_path} 的 text_in_image 无效。"
            )

        tag_names = item.get("tags")
        if not isinstance(tag_names, list) or not all(
            isinstance(name, str) for name in tag_names
        ):
            raise LibraryImportError(f"图片 {relative_path} 的 tags 字段无效。")

        images.append(
            _BackupImage(
                relative_path=relative_path,
                hash=hash_value.lower(),
                imported_at=_parse_datetime(
                    item.get("imported_at"),
                    "导入时间",
                    relative_path,
                ),
                modification_date=_parse_datetime(
                    item.get("modification_date"),
                    "修改时间",
                    relative_path,
                ),
                text_in_image=text_in_image,
                tag_names=tuple(dict.fromkeys(tag_names)),
            )
        )
    return images


def _parse_tags(raw_tags: object) -> list[Tag]:
    if not isinstance(raw_tags, list):
        raise LibraryImportError("备份文件的 tags 字段必须是数组。")

    tags = []
    seen_names = set()
    for index, item in enumerate(raw_tags, start=1):
        if not isinstance(item, dict):
            raise LibraryImportError(f"第 {index} 个标签记录无效。")

        name = item.get("name")
        if not isinstance(name, str) or not name:
            raise LibraryImportError(f"第 {index} 个标签的名称无效。")
        if name in seen_names:
            continue

        color = item.get("rgb")
        order = item.get("order")
        enabled = item.get("enabled")
        description = item.get("description")
        if not isinstance(color, str) or not RGB_PATTERN.fullmatch(color):
            raise LibraryImportError(f"标签 {name!r} 的颜色值无效。")
        if not isinstance(order, int) or isinstance(order, bool) or order < 0:
            raise LibraryImportError(f"标签 {name!r} 的排序值无效。")
        if not isinstance(enabled, bool):
            raise LibraryImportError(f"标签 {name!r} 的启用状态无效。")
        if description is not None and not isinstance(description, str):
            raise LibraryImportError(f"标签 {name!r} 的描述无效。")

        seen_names.add(name)
        tag = Tag()
        tag.name = name
        tag.color_rgb = color
        tag.order = order
        tag.enabled = enabled
        tag.description = description
        tags.append(tag)
    return tags


def _dedupe_images(images: list[_BackupImage]) -> list[_BackupImage]:
    """按 hash 去重，同一 hash 的多条记录合并标签集合。"""
    first_by_hash = {}
    tag_names_by_hash = {}
    for image in images:
        if image.hash not in first_by_hash:
            first_by_hash[image.hash] = image
            tag_names_by_hash[image.hash] = list(image.tag_names)
            continue
        for name in image.tag_names:
            if name not in tag_names_by_hash[image.hash]:
                tag_names_by_hash[image.hash].append(name)

    merged = []
    for hash_value, first in first_by_hash.items():
        merged.append(
            _BackupImage(
                relative_path=first.relative_path,
                hash=hash_value,
                imported_at=first.imported_at,
                modification_date=first.modification_date,
                text_in_image=first.text_in_image,
                tag_names=tuple(tag_names_by_hash[hash_value]),
            )
        )
    return merged


def _source_path(backup_root: Path, relative_path: str) -> Path:
    # 路径已被 IMAGE_PATH_PATTERN 限制为 set_N/文件名，不含分隔符与 ..。
    return backup_root.joinpath(*PurePosixPath(relative_path).parts)


def _resolve_tags(
    names: tuple[str, ...],
    tag_by_name: dict[str, Tag],
) -> list[Tag]:
    tags = []
    for name in names:
        tag = tag_by_name.get(name)
        if tag is None:
            logger.warning("备份图片引用了未知标签：%s", name)
            continue
        tags.append(tag)
    return tags


def _build_sticker(
    image: _BackupImage,
    file_metadata: StickerImageMetadata,
    source: Path,
    tags: list[Tag],
) -> StickerImage:
    sticker = StickerImage()
    sticker.original_file_name = PurePosixPath(image.relative_path).name
    sticker.relative_path = str(source)
    sticker.file_size = file_metadata.file_size
    sticker.hash = file_metadata.hash
    sticker.extension = file_metadata.extension
    sticker.imported_at = image.imported_at
    sticker.modification_date = image.modification_date
    sticker.size_width = file_metadata.size_width
    sticker.size_height = file_metadata.size_height
    sticker.vectordb_id = None
    sticker.text_in_image = image.text_in_image
    sticker.tags = list(tags)
    return sticker


def preflight(metadata_path: str | Path) -> Path:
    """轻量预检：能解析 JSON，且有图片时 set_1 目录必须存在。"""
    metadata_path = Path(metadata_path)
    if not metadata_path.is_file():
        raise LibraryImportError("选择的备份文件不存在。")

    metadata = _read_metadata(metadata_path)
    images = metadata.get("images")
    if isinstance(images, list) and images and not (
        metadata_path.parent / "set_1"
    ).is_dir():
        raise LibraryImportError("备份包含图片，但缺少 set_1 目录。")
    return metadata_path.parent


def import_library(
    database: StickerDBV1,
    blob_storage: BlobStorage,
    metadata_path: str | Path,
    *,
    progress: ProgressCallback | None = None,
    cancel_event: threading.Event | None = None,
) -> LibraryImportResult:
    metadata_path = Path(metadata_path)
    metadata = _read_metadata(metadata_path)

    if metadata.get("format_version") != 1:
        raise LibraryImportError("不支持的备份格式版本。")
    if metadata.get("hash_algorithm") != "sha1":
        raise LibraryImportError("不支持的 hash 算法。")

    backup_root = metadata_path.parent
    images = _parse_images(metadata.get("images"))
    tags = _parse_tags(metadata.get("tags"))
    if images and not (backup_root / "set_1").is_dir():
        raise LibraryImportError("备份包含图片，但缺少 set_1 目录。")

    planned_images = _dedupe_images(images)
    total = len(planned_images)
    _report_progress(
        progress,
        0,
        "正在读取备份",
        completed=0,
        total=total,
        cancellable=False,
    )

    added_tag_count = database.add_missing_tags(tags)
    tag_by_name = {tag.name: tag for tag in database.list_tags()}
    _report_progress(
        progress,
        TAG_MERGE_END_PERCENT,
        "正在合并标签",
        completed=0,
        total=total,
        cancellable=False,
    )

    existing_hashes = database.get_existing_sticker_hashes(
        image.hash for image in planned_images
    )

    def percent(completed: int) -> int:
        return TAG_MERGE_END_PERCENT + int(
            (100 - TAG_MERGE_END_PERCENT) * completed / total
        )

    added_image_count = 0
    merged_tag_image_count = 0
    damaged_count = 0
    errors = []
    completed = 0

    def make_result(*, cancelled: bool) -> LibraryImportResult:
        return LibraryImportResult(
            metadata_path=str(metadata_path),
            added_image_count=added_image_count,
            merged_tag_image_count=merged_tag_image_count,
            added_tag_count=added_tag_count,
            damaged_count=damaged_count,
            errors=tuple(errors),
            cancelled=cancelled,
        )

    for image in planned_images:
        if _is_cancelled(cancel_event):
            return make_result(cancelled=True)

        file_name = PurePosixPath(image.relative_path).name
        _report_progress(
            progress,
            percent(completed),
            "正在导入备份图片",
            completed=completed,
            total=total,
            last_file_name=file_name,
            cancellable=True,
        )

        source = _source_path(backup_root, image.relative_path)
        try:
            file_metadata = get_image_metadata(source)
        except (OSError, ValueError) as exc:
            damaged_count += 1
            errors.append(f"{image.relative_path}：{exc}")
            completed += 1
            continue

        if file_metadata.hash.lower() != image.hash:
            damaged_count += 1
            errors.append(
                f"{image.relative_path}：图片 hash 与备份记录不一致"
            )
            completed += 1
            continue

        # 先复制 blob、后写 SQLite：任意时刻中断最多留下未引用 blob。
        blob_storage.store_file(str(source), file_metadata.hash)
        if _is_cancelled(cancel_event):
            return make_result(cancelled=True)

        sticker = _build_sticker(
            image,
            file_metadata,
            source,
            _resolve_tags(image.tag_names, tag_by_name),
        )
        if image.hash in existing_hashes:
            if database.merge_sticker_tags(sticker.hash, sticker.tags):
                merged_tag_image_count += 1
        elif database.add_stickers([sticker]):
            added_image_count += 1
        elif database.merge_sticker_tags(sticker.hash, sticker.tags):
            merged_tag_image_count += 1

        completed += 1
        _report_progress(
            progress,
            percent(completed),
            "正在导入备份图片",
            completed=completed,
            total=total,
            cancellable=True,
        )

    _report_progress(
        progress,
        100,
        "导入完成",
        completed=completed,
        total=total,
        cancellable=False,
    )
    return make_result(cancelled=False)


class LibraryImportService(BackgroundJobService):
    """在独立 QThread 中执行备份导入并通过专用信号回传结果。"""

    import_finished = pyqtSignal(object)
    import_cancelled = pyqtSignal(object)
    import_failed = pyqtSignal(str)
    import_progress_changed = pyqtSignal(object)

    def __init__(self, parent: QObject | None = None):
        super().__init__(parent)
        self.succeeded.connect(self.import_finished)
        self.cancelled.connect(self.import_cancelled)
        self.failed.connect(self.import_failed)
        self.progress_changed.connect(self.import_progress_changed)

    def start_import(self, metadata_path: str | Path) -> None:
        if self.active_job_count:
            raise RuntimeError("已有图库备份导入任务正在运行。")

        database = services.global_instances.current_library_db
        blob_storage = services.global_instances.current_blob_storage
        if database is None or blob_storage is None:
            raise RuntimeError("图库尚未初始化。")

        path = str(metadata_path)

        def run(progress, cancel_event):
            return import_library(
                database,
                blob_storage,
                path,
                progress=progress,
                cancel_event=cancel_event,
            )

        self.start(
            run,
            cancel_allowed=lambda progress: bool(
                getattr(progress, "cancellable", False)
            ),
        )

    def cancel_import(self) -> bool:
        return self.cancel()
