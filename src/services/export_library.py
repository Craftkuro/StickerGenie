from __future__ import annotations

import datetime
import hashlib
import json
import logging
import math
import os
import time
import unicodedata
from dataclasses import dataclass
from functools import partial
from pathlib import Path, PurePosixPath
from typing import Callable, Sequence

from PyQt6.QtCore import QObject, QThread, pyqtSignal, pyqtSlot

from blob_storage import BlobFileEntity, BlobStorage
from commons.dto import StickerImage, Tag
import services.global_instances
from stickerdb.v1.sticker_db import StickerDBV1

logger = logging.getLogger(__name__)


MAX_FILES_PER_SET = 10_000


METADATA_SCHEMA = {
    "$schema": "https://json-schema.org/draft/2020-12/schema",
    "title": "StickerGenie gallery export metadata",
    "type": "object",
    "additionalProperties": False,
    "required": [
        "$schema",
        "format_version",
        "hash_algorithm",
        "exported_at",
        "images",
        "tags",
    ],
    "properties": {
        "$schema": {"const": "metadata.schema.json"},
        "format_version": {"const": 1},
        "hash_algorithm": {"const": "sha1"},
        "exported_at": {"type": "string", "format": "date-time"},
        "images": {
            "type": "array",
            "items": {
                "type": "object",
                "additionalProperties": False,
                "required": [
                    "path",
                    "hash",
                    "imported_at",
                    "modification_date",
                    "tags",
                    "text_in_image",
                ],
                "properties": {
                    "path": {
                        "type": "string",
                        "pattern": r"^set_[1-9][0-9]*/[^/\\]+$",
                    },
                    "hash": {
                        "type": "string",
                        "pattern": r"^[0-9a-f]{40}$",
                    },
                    "imported_at": {"type": "string", "format": "date-time"},
                    "modification_date": {
                        "type": "string",
                        "format": "date-time",
                    },
                    "tags": {
                        "type": "array",
                        "items": {"type": "string"},
                        "uniqueItems": True,
                    },
                    "text_in_image": {"type": ["string", "null"]},
                },
            },
        },
        "tags": {
            "type": "array",
            "items": {
                "type": "object",
                "additionalProperties": False,
                "required": [
                    "name",
                    "rgb",
                    "order",
                    "description",
                    "enabled",
                ],
                "properties": {
                    "name": {"type": "string", "minLength": 1},
                    "rgb": {
                        "type": "string",
                        "pattern": r"^#[0-9A-Fa-f]{6}$",
                    },
                    "order": {"type": "integer", "minimum": 0},
                    "description": {"type": ["string", "null"]},
                    "enabled": {"type": "boolean"},
                },
            },
        },
    },
}


class LibraryExportError(RuntimeError):
    """Base exception for an incomplete gallery export."""


class ExportDestinationNotEmptyError(LibraryExportError):
    """The selected export root already contains files."""


class ExportIntegrityError(LibraryExportError):
    """A blob did not match the SHA1 stored in the database."""


@dataclass(frozen=True)
class ExportLibraryProgress:
    percent: int
    status: str
    completed: int = 0
    total: int = 0

    def __post_init__(self) -> None:
        if not 0 <= self.percent <= 100:
            raise ValueError("percent must be between 0 and 100")
        if self.completed < 0 or self.total < 0 or self.completed > self.total:
            raise ValueError("invalid export progress counts")


@dataclass(frozen=True)
class ExportLibraryResult:
    destination: str
    image_count: int
    tag_count: int
    set_count: int


@dataclass(frozen=True)
class PlannedExportImage:
    sticker: StickerImage
    set_index: int
    relative_path: str


@dataclass(frozen=True)
class LibraryExportPlan:
    images: tuple[PlannedExportImage, ...]
    set_count: int


ProgressCallback = Callable[[ExportLibraryProgress], None]


def _report_progress(
    callback: ProgressCallback | None,
    percent: int,
    status: str,
    *,
    completed: int,
    total: int,
) -> None:
    if callback is not None:
        callback(
            ExportLibraryProgress(
                percent=percent,
                status=status,
                completed=completed,
                total=total,
            )
        )


def _filename_collision_key(file_name: str) -> str:
    return unicodedata.normalize("NFC", file_name).casefold()


def _validate_original_file_name(file_name: str) -> None:
    if not isinstance(file_name, str) or not file_name:
        raise LibraryExportError("图片的原始文件名为空，无法导出。")
    if (
        file_name in {".", ".."}
        or "/" in file_name
        or "\\" in file_name
        or Path(file_name).name != file_name
    ):
        raise LibraryExportError(f"图片文件名不安全，无法导出：{file_name}")


def _sticker_sort_key(sticker: StickerImage) -> tuple[str, str, str, int]:
    file_name = sticker.original_file_name
    return (
        _filename_collision_key(file_name),
        unicodedata.normalize("NFC", file_name),
        str(sticker.hash).lower(),
        int(getattr(sticker, "id", 0) or 0),
    )


def build_export_plan(
    stickers: Sequence[StickerImage],
    *,
    max_files_per_set: int = MAX_FILES_PER_SET,
) -> LibraryExportPlan:
    """Assign images to the minimum number of bounded, collision-free sets."""
    if max_files_per_set <= 0:
        raise ValueError("max_files_per_set must be positive")

    groups: dict[str, list[StickerImage]] = {}
    for sticker in sorted(stickers, key=_sticker_sort_key):
        _validate_original_file_name(sticker.original_file_name)
        key = _filename_collision_key(sticker.original_file_name)
        groups.setdefault(key, []).append(sticker)

    image_count = len(stickers)
    largest_collision_group = max((len(group) for group in groups.values()), default=0)
    set_count = max(
        1,
        math.ceil(image_count / max_files_per_set),
        largest_collision_group,
    )
    set_loads = [0] * set_count
    planned_images: list[PlannedExportImage] = []

    ordered_groups = sorted(groups.items(), key=lambda item: (-len(item[1]), item[0]))
    for _, group in ordered_groups:
        target_sets = sorted(
            range(set_count),
            key=lambda set_index: (set_loads[set_index], set_index),
        )[:len(group)]
        for sticker, set_index in zip(group, target_sets):
            if set_loads[set_index] >= max_files_per_set:
                raise RuntimeError("export set allocation exceeded its capacity")
            set_loads[set_index] += 1
            relative_path = str(
                PurePosixPath(f"set_{set_index + 1}") / sticker.original_file_name
            )
            planned_images.append(
                PlannedExportImage(
                    sticker=sticker,
                    set_index=set_index,
                    relative_path=relative_path,
                )
            )

    planned_images.sort(key=lambda item: (item.set_index, _sticker_sort_key(item.sticker)))
    return LibraryExportPlan(images=tuple(planned_images), set_count=set_count)


def _as_rfc3339(value: datetime.datetime) -> str:
    if value.tzinfo is None:
        value = value.astimezone()
    return value.isoformat()


def _exported_at_value(value: datetime.datetime | None) -> str:
    if value is None:
        value = datetime.datetime.now(datetime.timezone.utc)
    elif value.tzinfo is None:
        value = value.astimezone()
    rendered = value.isoformat()
    return rendered[:-6] + "Z" if rendered.endswith("+00:00") else rendered


def _tag_sort_key(tag: Tag) -> tuple[int, int]:
    return tag.order, int(tag.id or 0)


def _tag_metadata(tags: Sequence[Tag]) -> list[dict[str, object]]:
    return [
        {
            "name": tag.name,
            "rgb": tag.color_rgb,
            "order": tag.order,
            "description": tag.description,
            "enabled": tag.enabled,
        }
        for tag in sorted(tags, key=_tag_sort_key)
    ]


def _image_tag_names(
    sticker: StickerImage,
    global_tag_order: dict[str, tuple[int, int]],
) -> list[str]:
    def sort_key(name: str) -> tuple[bool, int, int, str]:
        order_and_id = global_tag_order.get(name)
        if order_and_id is None:
            return True, 0, 0, _filename_collision_key(name)
        return False, *order_and_id, _filename_collision_key(name)

    unique_names = {tag.name for tag in sticker.tags}
    return sorted(unique_names, key=sort_key)


def _image_metadata(
    planned_image: PlannedExportImage,
    global_tag_order: dict[str, tuple[int, int]],
) -> dict[str, object]:
    sticker = planned_image.sticker
    return {
        "path": planned_image.relative_path,
        "hash": sticker.hash.lower(),
        "imported_at": _as_rfc3339(sticker.imported_at),
        "modification_date": _as_rfc3339(sticker.modification_date),
        "tags": _image_tag_names(sticker, global_tag_order),
        "text_in_image": sticker.text_in_image,
    }


def _target_path(destination: Path, relative_path: str) -> Path:
    return destination.joinpath(*PurePosixPath(relative_path).parts)


def _copy_and_verify(
    source: Path,
    target: Path,
    expected_hash: str,
    modification_date: datetime.datetime,
) -> None:
    digest = hashlib.sha1()
    try:
        with source.open("rb") as source_file, target.open("xb") as target_file:
            for chunk in iter(lambda: source_file.read(1024 * 1024), b""):
                digest.update(chunk)
                target_file.write(chunk)

        actual_hash = digest.hexdigest()
        if actual_hash != expected_hash.lower():
            raise ExportIntegrityError(
                f"图片完整性校验失败：{target.name}（数据库 hash 与文件不一致）"
            )

        modification_timestamp = modification_date.timestamp()
        os.utime(target, (time.time(), modification_timestamp))
    except Exception:
        target.unlink(missing_ok=True)
        raise


def _write_json_atomic(path: Path, value: object) -> None:
    temporary_path = path.with_name(f".{path.name}.tmp")
    with temporary_path.open("x", encoding="utf-8", newline="\n") as output_file:
        json.dump(value, output_file, ensure_ascii=False, indent=2)
        output_file.write("\n")
    os.replace(temporary_path, path)


def _cleanup_failed_export(
    destination: Path,
    *,
    copied_files: Sequence[Path],
    set_directories: Sequence[Path],
    created_destination: bool,
) -> None:
    for name in (
        "metadata.json",
        "metadata.schema.json",
        ".metadata.json.tmp",
        ".metadata.schema.json.tmp",
    ):
        try:
            (destination / name).unlink(missing_ok=True)
        except OSError:
            logger.warning("无法清理导出文件：%s", destination / name)

    for file_path in reversed(copied_files):
        try:
            file_path.unlink(missing_ok=True)
        except OSError:
            logger.warning("无法清理导出图片：%s", file_path)

    for directory in reversed(set_directories):
        try:
            directory.rmdir()
        except OSError:
            logger.warning("无法清理导出目录：%s", directory)

    if created_destination:
        try:
            destination.rmdir()
        except OSError:
            logger.warning("无法清理导出根目录：%s", destination)


def export_library(
    database: StickerDBV1,
    blob_storage: BlobStorage,
    destination: str | Path,
    *,
    max_files_per_set: int = MAX_FILES_PER_SET,
    progress: ProgressCallback | None = None,
    exported_at: datetime.datetime | None = None,
) -> ExportLibraryResult:
    destination_path = Path(destination)
    created_destination = False

    if destination_path.exists():
        if not destination_path.is_dir():
            raise LibraryExportError("选择的导出路径不是目录。")
        if any(destination_path.iterdir()):
            raise ExportDestinationNotEmptyError("导出目录必须为空。")
    else:
        destination_path.mkdir(parents=True)
        created_destination = True

    copied_files: list[Path] = []
    set_directories: list[Path] = []
    try:
        stickers = database.list_stickers(count=None)
        tags = database.list_tags()
        plan = build_export_plan(
            stickers,
            max_files_per_set=max_files_per_set,
        )
        total = len(plan.images)
        _report_progress(
            progress,
            0,
            "正在准备导出",
            completed=0,
            total=total,
        )

        for set_index in range(plan.set_count):
            set_directory = destination_path / f"set_{set_index + 1}"
            set_directory.mkdir()
            set_directories.append(set_directory)

        for completed, planned_image in enumerate(plan.images, start=1):
            sticker = planned_image.sticker
            try:
                source_path = Path(
                    blob_storage.read_file(
                        BlobFileEntity(sticker.hash, sticker.extension)
                    )
                )
            except FileNotFoundError as exc:
                raise LibraryExportError(
                    f"图库中的图片文件不存在：{sticker.original_file_name}"
                ) from exc

            target_path = _target_path(destination_path, planned_image.relative_path)
            _copy_and_verify(
                source_path,
                target_path,
                sticker.hash,
                sticker.modification_date,
            )
            copied_files.append(target_path)
            percent = 99 if total == 0 else min(99, 1 + int(98 * completed / total))
            _report_progress(
                progress,
                percent,
                "正在导出图片",
                completed=completed,
                total=total,
            )

        tag_order = {tag.name: _tag_sort_key(tag) for tag in tags}
        metadata = {
            "$schema": "metadata.schema.json",
            "format_version": 1,
            "hash_algorithm": "sha1",
            "exported_at": _exported_at_value(exported_at),
            "images": [
                _image_metadata(planned_image, tag_order)
                for planned_image in plan.images
            ],
            "tags": _tag_metadata(tags),
        }
        _write_json_atomic(destination_path / "metadata.schema.json", METADATA_SCHEMA)
        _write_json_atomic(destination_path / "metadata.json", metadata)

        _report_progress(
            progress,
            100,
            "导出完成",
            completed=total,
            total=total,
        )
        return ExportLibraryResult(
            destination=str(destination_path),
            image_count=total,
            tag_count=len(tags),
            set_count=plan.set_count,
        )
    except Exception:
        _cleanup_failed_export(
            destination_path,
            copied_files=copied_files,
            set_directories=set_directories,
            created_destination=created_destination,
        )
        raise


class _ExportLibraryWorker(QObject):
    succeeded = pyqtSignal(object)
    failed = pyqtSignal(str)
    progress_changed = pyqtSignal(object)

    def __init__(
        self,
        database: StickerDBV1,
        blob_storage: BlobStorage,
        destination: str,
        max_files_per_set: int,
    ):
        super().__init__()
        self._database = database
        self._blob_storage = blob_storage
        self._destination = destination
        self._max_files_per_set = max_files_per_set

    @pyqtSlot()
    def run(self) -> None:
        try:
            result = export_library(
                self._database,
                self._blob_storage,
                self._destination,
                max_files_per_set=self._max_files_per_set,
                progress=self.progress_changed.emit,
            )
        except Exception as exc:
            logger.exception("导出图库失败")
            self.failed.emit(str(exc))
            return
        self.succeeded.emit(result)


class LibraryExportService(QObject):
    """Run each gallery export in a dedicated QThread."""

    export_finished = pyqtSignal(object)
    export_failed = pyqtSignal(str)
    export_progress_changed = pyqtSignal(object)

    def __init__(self, parent: QObject | None = None):
        super().__init__(parent)
        self._jobs: dict[QThread, _ExportLibraryWorker] = {}

    @property
    def active_job_count(self) -> int:
        return len(self._jobs)

    def start_export(
        self,
        destination: str | Path,
        *,
        max_files_per_set: int = MAX_FILES_PER_SET,
    ) -> None:
        if self._jobs:
            raise RuntimeError("已有图库导出任务正在运行。")
        if max_files_per_set <= 0:
            raise ValueError("max_files_per_set must be positive")

        database = services.global_instances.current_library_db
        blob_storage = services.global_instances.current_blob_storage
        if database is None or blob_storage is None:
            raise RuntimeError("图库尚未初始化。")

        thread = QThread(self)
        worker = _ExportLibraryWorker(
            database,
            blob_storage,
            str(destination),
            max_files_per_set,
        )
        worker.moveToThread(thread)
        self._jobs[thread] = worker

        thread.started.connect(worker.run)
        worker.succeeded.connect(self.export_finished)
        worker.failed.connect(self.export_failed)
        worker.progress_changed.connect(self.export_progress_changed)
        worker.succeeded.connect(thread.quit)
        worker.failed.connect(thread.quit)
        worker.succeeded.connect(worker.deleteLater)
        worker.failed.connect(worker.deleteLater)
        thread.finished.connect(partial(self._release_job, thread))
        thread.finished.connect(thread.deleteLater)
        thread.start()

    def _release_job(self, thread: QThread) -> None:
        self._jobs.pop(thread, None)
