import datetime
import hashlib
import io
import json
import os
import tempfile
import threading
import time
import unittest
from pathlib import Path
from unittest.mock import patch

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PIL import Image
from PyQt6.QtCore import QCoreApplication, QEventLoop, QThread, QTimer

import services.global_instances
from blob_storage import BlobFileEntity, BlobStorage
from commons.dto import StickerImage, Tag
from services.import_library import (
    LibraryImportError,
    LibraryImportProgress,
    LibraryImportResult,
    LibraryImportService,
    import_library,
    preflight,
)
from stickerdb.v1.sticker_db import StickerDBV1


def make_tag(
    name: str,
    *,
    color: str = "#2196F3",
    description: str | None = None,
    enabled: bool = True,
    order: int = 0,
) -> Tag:
    tag = Tag()
    tag.name = name
    tag.color_rgb = color
    tag.description = description
    tag.enabled = enabled
    tag.order = order
    return tag


def make_sticker(file_name: str, content: bytes) -> StickerImage:
    sticker = StickerImage()
    sticker.original_file_name = file_name
    sticker.file_size = len(content)
    sticker.hash = hashlib.sha1(content).hexdigest()
    sticker.extension = ".png"
    sticker.imported_at = datetime.datetime(2026, 1, 2, 3, 4, 5)
    sticker.modification_date = datetime.datetime(2025, 6, 7, 8, 9, 10)
    sticker.size_width = 4
    sticker.size_height = 3
    sticker.vectordb_id = None
    sticker.text_in_image = None
    sticker.tags = []
    return sticker


def png_bytes(color: str) -> bytes:
    buffer = io.BytesIO()
    Image.new("RGB", (4, 3), color).save(buffer, format="PNG")
    return buffer.getvalue()


class ImportLibraryTests(unittest.TestCase):
    def setUp(self):
        self._temp_dir = tempfile.TemporaryDirectory()
        self.root = Path(self._temp_dir.name)
        self.db = StickerDBV1(str(self.root / "library.db"))
        self.blob_storage = BlobStorage(str(self.root / "blob"))
        self.backup_root = self.root / "backup"
        self.backup_root.mkdir()

    def tearDown(self):
        self.db.engine.dispose()
        self._temp_dir.cleanup()

    def _write_image(self, relative_path: str, content: bytes) -> str:
        target = self.backup_root / Path(relative_path)
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes(content)
        return str(target)

    def _write_backup(
        self,
        images: list[dict],
        tags: list[dict],
        *,
        format_version: int = 1,
        hash_algorithm: str = "sha1",
    ) -> Path:
        metadata_path = self.backup_root / "metadata.json"
        metadata_path.write_text(
            json.dumps(
                {
                    "$schema": "metadata.schema.json",
                    "format_version": format_version,
                    "hash_algorithm": hash_algorithm,
                    "exported_at": "2026-08-16T00:00:00Z",
                    "images": images,
                    "tags": tags,
                },
                ensure_ascii=False,
            ),
            encoding="utf-8",
        )
        return metadata_path

    def _image_record(
        self,
        file_name: str,
        content: bytes,
        *,
        tags: list[str] | None = None,
        text_in_image: str | None = None,
        imported_at: str = "2026-01-02T03:04:05",
        modification_date: str = "2025-06-07T08:09:10",
        hash_value: str | None = None,
        set_name: str = "set_1",
    ) -> dict:
        relative_path = f"{set_name}/{file_name}"
        self._write_image(relative_path, content)
        return {
            "path": relative_path,
            "hash": hash_value or hashlib.sha1(content).hexdigest(),
            "imported_at": imported_at,
            "modification_date": modification_date,
            "tags": list(tags or []),
            "text_in_image": text_in_image,
        }

    def _tag_record(self, name: str, **overrides) -> dict:
        record = {
            "name": name,
            "rgb": "#2196F3",
            "order": 0,
            "description": None,
            "enabled": True,
        }
        record.update(overrides)
        return record

    def test_imports_standard_backup_into_empty_library(self):
        content = png_bytes("red")
        file_hash = hashlib.sha1(content).hexdigest()
        metadata_path = self._write_backup(
            [
                self._image_record(
                    "示例.png",
                    content,
                    tags=["Alpha"],
                    text_in_image="图片文字",
                )
            ],
            [
                self._tag_record(
                    "Alpha",
                    rgb="#AABBCC",
                    order=3,
                    description="首个标签",
                    enabled=False,
                )
            ],
        )

        result = import_library(self.db, self.blob_storage, metadata_path)

        self.assertEqual(1, result.added_image_count)
        self.assertEqual(0, result.merged_tag_image_count)
        self.assertEqual(1, result.added_tag_count)
        self.assertEqual(0, result.damaged_count)
        self.assertEqual((), result.errors)
        self.assertFalse(result.cancelled)

        stickers = self.db.list_stickers(count=None)
        self.assertEqual(1, len(stickers))
        sticker = stickers[0]
        self.assertEqual("示例.png", sticker.original_file_name)
        self.assertEqual(len(content), sticker.file_size)
        self.assertEqual(file_hash, sticker.hash)
        self.assertEqual(".png", sticker.extension)
        self.assertEqual(datetime.datetime(2026, 1, 2, 3, 4, 5), sticker.imported_at)
        self.assertEqual(
            datetime.datetime(2025, 6, 7, 8, 9, 10),
            sticker.modification_date,
        )
        self.assertEqual((4, 3), (sticker.size_width, sticker.size_height))
        self.assertIsNone(sticker.vectordb_id)
        self.assertEqual("图片文字", sticker.text_in_image)
        self.assertEqual(["Alpha"], [tag.name for tag in sticker.tags])

        tag = self.db.list_tags()[0]
        self.assertEqual("#AABBCC", tag.color_rgb)
        self.assertEqual(3, tag.order)
        self.assertEqual("首个标签", tag.description)
        self.assertFalse(tag.enabled)
        self.assertTrue(
            self.blob_storage.exists(BlobFileEntity(file_hash, ".png"))
        )

    def test_imports_mismatched_backup_extension_using_detected_format(self):
        content = png_bytes("red")
        file_hash = hashlib.sha1(content).hexdigest()
        metadata_path = self._write_backup(
            [self._image_record("示例.jpg", content)],
            [],
        )

        result = import_library(self.db, self.blob_storage, metadata_path)

        self.assertEqual(1, result.added_image_count)
        sticker = self.db.list_stickers(count=None)[0]
        self.assertEqual(".png", sticker.extension)
        self.assertTrue(
            self.blob_storage.exists(BlobFileEntity(file_hash, ".png"))
        )
        self.assertFalse(
            self.blob_storage.exists(BlobFileEntity(file_hash, ".jpg"))
        )

    def test_existing_hash_merges_tags_without_changing_the_image(self):
        content = png_bytes("green")
        existing = make_sticker("原文件.png", content)
        old_tag = self.db.add_or_modify_tag(make_tag("Old", color="#111111"))
        existing.tags = [old_tag]
        existing.text_in_image = "原有文字"
        self.db.add_stickers([existing])

        metadata_path = self._write_backup(
            [
                self._image_record(
                    "备份名.png",
                    content,
                    tags=["New"],
                    text_in_image="备份文字",
                )
            ],
            [self._tag_record("New")],
        )

        result = import_library(self.db, self.blob_storage, metadata_path)

        self.assertEqual(0, result.added_image_count)
        self.assertEqual(1, result.merged_tag_image_count)
        self.assertEqual(1, result.added_tag_count)

        stickers = self.db.list_stickers(count=None)
        self.assertEqual(1, len(stickers))
        sticker = stickers[0]
        self.assertEqual("原文件.png", sticker.original_file_name)
        self.assertEqual("原有文字", sticker.text_in_image)
        self.assertEqual(
            {"Old", "New"},
            {tag.name for tag in sticker.tags},
        )

    def test_existing_tag_attributes_are_not_overwritten(self):
        existing = self.db.add_or_modify_tag(
            make_tag(
                "Alpha",
                color="#111111",
                description="原有描述",
                enabled=True,
            )
        )
        existing.order = 7
        self.db.add_or_modify_tag(existing)
        metadata_path = self._write_backup(
            [],
            [
                self._tag_record(
                    "Alpha",
                    rgb="#AABBCC",
                    order=0,
                    description="备份描述",
                    enabled=False,
                )
            ],
        )

        result = import_library(self.db, self.blob_storage, metadata_path)

        self.assertEqual(0, result.added_tag_count)
        tag = self.db.list_tags()[0]
        self.assertEqual("#111111", tag.color_rgb)
        self.assertEqual("原有描述", tag.description)
        self.assertTrue(tag.enabled)
        self.assertEqual(7, tag.order)

    def test_missing_and_hash_mismatched_files_are_damaged(self):
        good_content = png_bytes("blue")
        metadata_path = self._write_backup(
            [
                self._image_record("good.png", good_content),
                {
                    "path": "set_1/missing.png",
                    "hash": "a" * 40,
                    "imported_at": "2026-01-02T03:04:05",
                    "modification_date": "2025-06-07T08:09:10",
                    "tags": [],
                    "text_in_image": None,
                },
                self._image_record(
                    "bad.png",
                    png_bytes("black"),
                    hash_value="b" * 40,
                ),
            ],
            [],
        )

        result = import_library(self.db, self.blob_storage, metadata_path)

        self.assertEqual(1, result.added_image_count)
        self.assertEqual(2, result.damaged_count)
        self.assertEqual(2, len(result.errors))
        self.assertEqual(
            ["good.png"],
            [sticker.original_file_name for sticker in self.db.list_stickers(count=None)],
        )

    def test_tags_only_backup_is_imported(self):
        metadata_path = self._write_backup(
            [],
            [self._tag_record("Alpha", rgb="#AABBCC")],
        )

        result = import_library(self.db, self.blob_storage, metadata_path)

        self.assertEqual(0, result.added_image_count)
        self.assertEqual(1, result.added_tag_count)
        self.assertEqual([], self.db.list_stickers(count=None))
        self.assertEqual("Alpha", self.db.list_tags()[0].name)

    def test_images_without_set_one_are_rejected_by_core_and_preflight(self):
        metadata_path = self._write_backup(
            [
                {
                    "path": "set_1/missing.png",
                    "hash": "a" * 40,
                    "imported_at": "2026-01-02T03:04:05",
                    "modification_date": "2025-06-07T08:09:10",
                    "tags": [],
                    "text_in_image": None,
                }
            ],
            [],
        )

        with self.assertRaisesRegex(LibraryImportError, "缺少 set_1"):
            import_library(self.db, self.blob_storage, metadata_path)
        with self.assertRaisesRegex(LibraryImportError, "缺少 set_1"):
            preflight(metadata_path)

    def test_unsupported_format_version_and_hash_algorithm_are_rejected(self):
        for key, value in (
            ("format_version", 2),
            ("hash_algorithm", "md5"),
        ):
            with self.subTest(key=key):
                metadata_path = self._write_backup(
                    [],
                    [],
                    **{key: value},
                )
                with self.assertRaises(LibraryImportError):
                    import_library(self.db, self.blob_storage, metadata_path)

    def test_duplicate_hashes_and_tag_names_are_deduplicated(self):
        content = png_bytes("white")
        metadata_path = self._write_backup(
            [
                self._image_record("a.png", content, tags=["X"]),
                self._image_record("b.png", content, tags=["Y"]),
            ],
            [
                self._tag_record("X", rgb="#AAAAAA", order=1),
                self._tag_record("X", rgb="#BBBBBB", order=2),
                self._tag_record("Y", rgb="#CCCCCC", order=3),
            ],
        )

        progress_events = []
        result = import_library(
            self.db,
            self.blob_storage,
            metadata_path,
            progress=progress_events.append,
        )

        self.assertEqual(1, result.added_image_count)
        self.assertEqual(2, result.added_tag_count)
        sticker = self.db.list_stickers(count=None)[0]
        self.assertEqual(["X", "Y"], [tag.name for tag in sticker.tags])
        tags_by_name = {tag.name: tag for tag in self.db.list_tags()}
        self.assertEqual("#AAAAAA", tags_by_name["X"].color_rgb)
        self.assertEqual(1, tags_by_name["X"].order)

        per_image_events = [
            event
            for event in progress_events
            if event.status == "正在导入备份图片"
        ]
        self.assertTrue(per_image_events)
        self.assertTrue(all(event.total == 1 for event in per_image_events))
        self.assertEqual(1, progress_events[-1].completed)
        self.assertEqual(1, progress_events[-1].total)
        self.assertEqual(100, progress_events[-1].percent)

    def test_timezone_and_z_suffix_values_round_trip_to_naive_local_time(self):
        content = png_bytes("yellow")
        imported_at = "2026-01-02T03:04:05+08:00"
        modification_date = "2025-06-07T08:09:10Z"
        metadata_path = self._write_backup(
            [
                self._image_record(
                    "time.png",
                    content,
                    imported_at=imported_at,
                    modification_date=modification_date,
                )
            ],
            [],
        )

        import_library(self.db, self.blob_storage, metadata_path)

        sticker = self.db.list_stickers(count=None)[0]
        expected_imported = (
            datetime.datetime.fromisoformat(imported_at)
            .astimezone()
            .replace(tzinfo=None)
        )
        expected_modified = (
            datetime.datetime.fromisoformat(modification_date)
            .astimezone()
            .replace(tzinfo=None)
        )
        self.assertEqual(expected_imported, sticker.imported_at)
        self.assertEqual(expected_modified, sticker.modification_date)
        self.assertIsNone(sticker.imported_at.tzinfo)

    def test_cancel_keeps_processed_images_and_skips_the_rest(self):
        first_content = png_bytes("red")
        second_content = png_bytes("blue")
        metadata_path = self._write_backup(
            [
                self._image_record("first.png", first_content),
                self._image_record("second.png", second_content),
            ],
            [],
        )
        cancel_event = threading.Event()
        original_add_stickers = self.db.add_stickers

        def add_stickers_and_cancel(stickers):
            inserted = original_add_stickers(stickers)
            cancel_event.set()
            return inserted

        with patch.object(
            self.db,
            "add_stickers",
            side_effect=add_stickers_and_cancel,
        ):
            result = import_library(
                self.db,
                self.blob_storage,
                metadata_path,
                cancel_event=cancel_event,
            )

        self.assertTrue(result.cancelled)
        self.assertEqual(1, result.added_image_count)
        self.assertEqual(
            ["first.png"],
            [sticker.original_file_name for sticker in self.db.list_stickers(count=None)],
        )

    def test_cancel_after_blob_copy_leaves_only_an_unreferenced_blob(self):
        content = png_bytes("green")
        file_hash = hashlib.sha1(content).hexdigest()
        metadata_path = self._write_backup(
            [self._image_record("only.png", content)],
            [],
        )
        cancel_event = threading.Event()
        original_store_file = self.blob_storage.store_file

        def store_file_and_cancel(file_path, file_hash, **kwargs):
            entity = original_store_file(file_path, file_hash, **kwargs)
            cancel_event.set()
            return entity

        with patch.object(
            self.blob_storage,
            "store_file",
            side_effect=store_file_and_cancel,
        ):
            result = import_library(
                self.db,
                self.blob_storage,
                metadata_path,
                cancel_event=cancel_event,
            )

        self.assertTrue(result.cancelled)
        self.assertEqual(0, result.added_image_count)
        self.assertEqual([], self.db.list_stickers(count=None))
        blob_path = self.blob_storage.read_file(
            BlobFileEntity(file_hash, ".png")
        )
        self.assertEqual(content, Path(blob_path).read_bytes())

    def test_add_missing_tags_only_inserts_new_names(self):
        existing = self.db.add_or_modify_tag(
            make_tag("Existing", color="#111111")
        )
        existing.order = 7
        self.db.add_or_modify_tag(existing)

        added = self.db.add_missing_tags(
            [
                make_tag("Existing", color="#222222", order=0),
                make_tag("New", color="#333333", order=1),
            ]
        )

        self.assertEqual(1, added)
        tags_by_name = {tag.name: tag for tag in self.db.list_tags()}
        self.assertEqual("#111111", tags_by_name["Existing"].color_rgb)
        self.assertEqual(7, tags_by_name["Existing"].order)
        self.assertEqual("#333333", tags_by_name["New"].color_rgb)

    def test_merge_sticker_tags_is_a_deduplicated_union(self):
        content = png_bytes("purple")
        sticker = make_sticker("sticker.png", content)
        first = self.db.add_or_modify_tag(make_tag("First"))
        second = self.db.add_or_modify_tag(make_tag("Second"))
        sticker.tags = [first]
        self.db.add_stickers([sticker])

        added = self.db.merge_sticker_tags(sticker.hash, [first, second])
        again = self.db.merge_sticker_tags(sticker.hash, [first, second])

        self.assertEqual(1, added)
        self.assertEqual(0, again)
        merged = self.db.list_stickers(count=None)[0]
        self.assertEqual(
            ["First", "Second"],
            [tag.name for tag in merged.tags],
        )
        self.assertEqual("sticker.png", merged.original_file_name)


class LibraryImportServiceTests(unittest.TestCase):
    def setUp(self):
        self._old_db = services.global_instances.current_library_db
        self._old_blob = services.global_instances.current_blob_storage
        self._temp_dir = tempfile.TemporaryDirectory()
        self.root = Path(self._temp_dir.name)
        self.db = StickerDBV1(str(self.root / "library.db"))
        self.blob_storage = BlobStorage(str(self.root / "blob"))
        services.global_instances.current_library_db = self.db
        services.global_instances.current_blob_storage = self.blob_storage

    def tearDown(self):
        services.global_instances.current_library_db = self._old_db
        services.global_instances.current_blob_storage = self._old_blob
        self.db.engine.dispose()
        self._temp_dir.cleanup()

    def test_start_requires_an_initialized_library(self):
        services.global_instances.current_library_db = None
        service = LibraryImportService()
        with self.assertRaisesRegex(RuntimeError, "图库尚未初始化"):
            service.start_import("backup/metadata.json")

    def test_runs_import_outside_the_main_thread(self):
        app = QCoreApplication.instance() or QCoreApplication([])
        service = LibraryImportService()
        loop = QEventLoop()
        execution_threads = []
        received_results = []
        expected_result = LibraryImportResult("backup/metadata.json")

        def execute_import(*args, **kwargs):
            execution_threads.append(QThread.currentThread())
            return expected_result

        service.import_finished.connect(
            lambda result: (received_results.append(result), loop.quit())
        )
        with patch(
            "services.import_library.import_library",
            side_effect=execute_import,
        ):
            service.start_import(self.root / "metadata.json")
            thread = next(iter(service._jobs))
            QTimer.singleShot(3000, loop.quit)
            loop.exec()
            thread.wait(3000)
            app.processEvents()

        self.assertEqual([expected_result], received_results)
        self.assertEqual(1, len(execution_threads))
        self.assertIsNot(execution_threads[0], app.thread())

    def test_cancel_requires_cancellable_latest_progress(self):
        app = QCoreApplication.instance() or QCoreApplication([])
        service = LibraryImportService()
        loop = QEventLoop()
        started = threading.Event()
        captured = {}

        def run_job(*args, progress, cancel_event):
            captured["progress"] = progress
            progress(
                LibraryImportProgress(
                    0,
                    "正在读取备份",
                    total=1,
                    cancellable=False,
                )
            )
            started.set()
            while not cancel_event.wait(0.01):
                time.sleep(0)
            return LibraryImportResult("backup", cancelled=True)

        service.import_cancelled.connect(lambda _result: loop.quit())
        with patch(
            "services.import_library.import_library",
            side_effect=run_job,
        ):
            service.start_import("backup/metadata.json")
            thread = next(iter(service._jobs))
            self.assertTrue(started.wait(3))
            app.processEvents()
            self.assertFalse(service.cancel_import())

            captured["progress"](
                LibraryImportProgress(
                    5,
                    "正在导入备份图片",
                    completed=0,
                    total=1,
                    cancellable=True,
                )
            )
            app.processEvents()
            self.assertTrue(service.cancel_import())
            self.assertFalse(service.cancel_import())
            QTimer.singleShot(3000, loop.quit)
            loop.exec()
            thread.wait(3000)
            app.processEvents()

        self.assertEqual(0, service.active_job_count)


if __name__ == "__main__":
    unittest.main()
