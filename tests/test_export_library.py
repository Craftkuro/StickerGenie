import datetime
import hashlib
import json
import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PyQt6.QtCore import QCoreApplication, QEventLoop, QThread, QTimer
from jsonschema import Draft202012Validator

import services.global_instances
from blob_storage import BlobFileEntity, BlobStorage
from commons.dto import StickerImage, Tag
from services.export_library import (
    ExportDestinationNotEmptyError,
    ExportIntegrityError,
    ExportLibraryResult,
    LibraryExportError,
    LibraryExportService,
    build_export_plan,
    export_library,
)
from stickerdb.v1.sticker_db import StickerDBV1


def make_tag(
    name: str,
    *,
    color: str = "#2196F3",
    description: str | None = None,
    enabled: bool = True,
) -> Tag:
    tag = Tag()
    tag.name = name
    tag.color_rgb = color
    tag.description = description
    tag.enabled = enabled
    return tag


def make_sticker(
    file_name: str,
    file_hash: str,
    *,
    extension: str = ".png",
    tags: list[Tag] | None = None,
    modification_date: datetime.datetime | None = None,
) -> StickerImage:
    sticker = StickerImage()
    sticker.original_file_name = file_name
    sticker.relative_path = file_name
    sticker.file_size = 1
    sticker.hash = file_hash
    sticker.extension = extension
    sticker.imported_at = datetime.datetime(2026, 1, 2, 3, 4, 5)
    sticker.modification_date = modification_date or datetime.datetime(
        2025, 6, 7, 8, 9, 10
    )
    sticker.size_width = 1
    sticker.size_height = 1
    sticker.vectordb_id = None
    sticker.text_in_image = None
    sticker.tags = list(tags or [])
    return sticker


class ExportLibraryTests(unittest.TestCase):
    def setUp(self):
        self._temp_dir = tempfile.TemporaryDirectory()
        self.root = Path(self._temp_dir.name)
        self.db = StickerDBV1(str(self.root / "library.db"))
        self.blob_storage = BlobStorage(str(self.root / "blob"))

    def tearDown(self):
        self.db.engine.dispose()
        self._temp_dir.cleanup()

    def _add_sticker(
        self,
        original_file_name: str,
        content: bytes,
        *,
        tags: list[Tag] | None = None,
        text_in_image: str | None = None,
        modification_date: datetime.datetime | None = None,
    ) -> StickerImage:
        file_hash = hashlib.sha1(content).hexdigest()
        extension = Path(original_file_name).suffix.lower()
        source_path = self.root / f"source-{file_hash}{extension}"
        source_path.write_bytes(content)
        self.blob_storage.store_file(str(source_path), file_hash)

        sticker = make_sticker(
            original_file_name,
            file_hash,
            extension=extension,
            tags=tags,
            modification_date=modification_date,
        )
        sticker.file_size = len(content)
        sticker.relative_path = str(source_path)
        sticker.text_in_image = text_in_image
        return self.db.add_stickers([sticker])[0]

    def test_exports_human_readable_metadata_schema_and_database_mtime(self):
        zulu = self.db.add_or_modify_tag(
            make_tag(
                "Zulu",
                color="#112233",
                description="末尾标签",
                enabled=False,
            )
        )
        alpha = self.db.add_or_modify_tag(
            make_tag("Alpha", color="#AABBCC", description="首个标签")
        )
        modification_date = datetime.datetime(2024, 5, 6, 7, 8, 9)
        content = "中文图片数据".encode("utf-8")
        sticker = self._add_sticker(
            "示例.png",
            content,
            tags=[zulu, alpha],
            text_in_image="图片文字",
            modification_date=modification_date,
        )
        destination = self.root / "export"
        destination.mkdir()

        progress_events = []
        result = export_library(
            self.db,
            self.blob_storage,
            destination,
            progress=progress_events.append,
            exported_at=datetime.datetime(
                2026, 8, 9, 4, 0, tzinfo=datetime.timezone.utc
            ),
        )

        self.assertEqual(1, result.image_count)
        self.assertEqual(2, result.tag_count)
        self.assertEqual(1, result.set_count)
        self.assertEqual(0, progress_events[0].percent)
        self.assertEqual(100, progress_events[-1].percent)

        metadata_bytes = (destination / "metadata.json").read_bytes()
        self.assertFalse(metadata_bytes.startswith(b"\xef\xbb\xbf"))
        metadata = json.loads(metadata_bytes.decode("utf-8"))
        self.assertEqual("metadata.schema.json", metadata["$schema"])
        self.assertEqual(1, metadata["format_version"])
        self.assertEqual("sha1", metadata["hash_algorithm"])
        self.assertEqual("2026-08-09T04:00:00Z", metadata["exported_at"])
        self.assertEqual(
            [
                {
                    "name": "Zulu",
                    "rgb": "#112233",
                    "order": 0,
                    "description": "末尾标签",
                    "enabled": False,
                },
                {
                    "name": "Alpha",
                    "rgb": "#AABBCC",
                    "order": 1,
                    "description": "首个标签",
                    "enabled": True,
                },
            ],
            metadata["tags"],
        )

        image_metadata = metadata["images"][0]
        self.assertEqual("set_1/示例.png", image_metadata["path"])
        self.assertEqual(sticker.hash, image_metadata["hash"])
        self.assertEqual(["Zulu", "Alpha"], image_metadata["tags"])
        self.assertEqual("图片文字", image_metadata["text_in_image"])

        exported_file = destination / "set_1" / "示例.png"
        self.assertEqual(content, exported_file.read_bytes())
        self.assertAlmostEqual(
            modification_date.timestamp(),
            exported_file.stat().st_mtime,
            delta=1.0,
        )

        schema = json.loads(
            (destination / "metadata.schema.json").read_text(encoding="utf-8")
        )
        Draft202012Validator.check_schema(schema)
        Draft202012Validator(schema).validate(metadata)
        self.assertEqual(
            "https://json-schema.org/draft/2020-12/schema",
            schema["$schema"],
        )
        self.assertEqual(
            ["string", "null"],
            schema["properties"]["images"]["items"]["properties"][
                "text_in_image"
            ]["type"],
        )

    def test_plan_uses_the_minimum_sets_for_capacity_and_name_collisions(self):
        stickers = [
            make_sticker("Same.png", f"{index:040x}")
            for index in range(1, 4)
        ]
        stickers.extend(
            [
                make_sticker("other-a.png", f"{4:040x}"),
                make_sticker("other-b.png", f"{5:040x}"),
            ]
        )

        plan = build_export_plan(stickers, max_files_per_set=2)

        self.assertEqual(3, plan.set_count)
        loads = [0] * plan.set_count
        names_by_set = [set() for _ in range(plan.set_count)]
        for planned_image in plan.images:
            loads[planned_image.set_index] += 1
            collision_name = planned_image.sticker.original_file_name.casefold()
            self.assertNotIn(
                collision_name,
                names_by_set[planned_image.set_index],
            )
            names_by_set[planned_image.set_index].add(collision_name)
        self.assertLessEqual(max(loads), 2)
        self.assertEqual(3, len({
            planned_image.set_index
            for planned_image in plan.images
            if planned_image.sticker.original_file_name == "Same.png"
        }))

    def test_case_only_file_names_are_treated_as_collisions(self):
        plan = build_export_plan(
            [
                make_sticker("Cat.png", "1" * 40),
                make_sticker("cat.png", "2" * 40),
            ]
        )

        self.assertEqual(2, plan.set_count)
        self.assertEqual({0, 1}, {image.set_index for image in plan.images})

    def test_plan_rejects_file_names_with_path_separators(self):
        for file_name in ("folder/image.png", "folder\\image.png", ".."):
            with self.subTest(file_name=file_name), self.assertRaises(
                LibraryExportError
            ):
                build_export_plan(
                    [make_sticker(file_name, "1" * 40)]
                )

    def test_empty_gallery_still_creates_set_one(self):
        destination = self.root / "empty-export"
        destination.mkdir()

        result = export_library(self.db, self.blob_storage, destination)

        self.assertEqual(0, result.image_count)
        self.assertEqual(1, result.set_count)
        self.assertTrue((destination / "set_1").is_dir())
        metadata = json.loads(
            (destination / "metadata.json").read_text(encoding="utf-8")
        )
        self.assertEqual([], metadata["images"])
        self.assertEqual([], metadata["tags"])

    def test_rejects_non_empty_destination_without_modifying_it(self):
        destination = self.root / "non-empty"
        destination.mkdir()
        sentinel = destination / "keep.txt"
        sentinel.write_text("keep", encoding="utf-8")

        with self.assertRaises(ExportDestinationNotEmptyError):
            export_library(self.db, self.blob_storage, destination)

        self.assertEqual("keep", sentinel.read_text(encoding="utf-8"))
        self.assertEqual([sentinel], list(destination.iterdir()))

    def test_missing_blob_aborts_and_removes_created_artifacts(self):
        sticker = make_sticker("missing.png", "a" * 40)
        self.db.add_stickers([sticker])
        destination = self.root / "missing-export"
        destination.mkdir()

        with self.assertRaises(LibraryExportError):
            export_library(self.db, self.blob_storage, destination)

        self.assertEqual([], list(destination.iterdir()))

    def test_hash_mismatch_aborts_and_removes_created_artifacts(self):
        sticker = self._add_sticker("changed.png", b"original")
        blob_path = Path(
            self.blob_storage.read_file(
                BlobFileEntity(sticker.hash, sticker.extension)
            )
        )
        blob_path.write_bytes(b"changed")
        destination = self.root / "invalid-export"
        destination.mkdir()

        with self.assertRaises(ExportIntegrityError):
            export_library(self.db, self.blob_storage, destination)

        self.assertEqual([], list(destination.iterdir()))

    def test_service_executes_export_outside_the_main_thread(self):
        app = QCoreApplication.instance() or QCoreApplication([])
        service = LibraryExportService()
        loop = QEventLoop()
        execution_threads = []
        received_results = []
        expected_result = ExportLibraryResult("destination", 0, 0, 1)
        old_db = services.global_instances.current_library_db
        old_blob = services.global_instances.current_blob_storage
        services.global_instances.current_library_db = self.db
        services.global_instances.current_blob_storage = self.blob_storage

        def execute_export(*args, **kwargs):
            execution_threads.append(QThread.currentThread())
            return expected_result

        def finish(result):
            received_results.append(result)
            loop.quit()

        service.export_finished.connect(finish)
        try:
            with patch(
                "services.export_library.export_library",
                side_effect=execute_export,
            ):
                service.start_export(self.root / "thread-export")
                thread = next(iter(service._jobs))
                QTimer.singleShot(3000, loop.quit)
                loop.exec()
                thread.wait(3000)
                app.processEvents()
        finally:
            services.global_instances.current_library_db = old_db
            services.global_instances.current_blob_storage = old_blob

        self.assertEqual([expected_result], received_results)
        self.assertEqual(1, len(execution_threads))
        self.assertIsNot(execution_threads[0], app.thread())


if __name__ == "__main__":
    unittest.main()
