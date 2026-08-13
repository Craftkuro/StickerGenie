import base64
import os
import struct
import tempfile
import time
import unittest
from collections import defaultdict
from pathlib import Path
from unittest.mock import patch

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

# 真实坏图（hash 0b6c1c1c80db49f1d66db97f4a7709f4b4d41c27）中损坏的
# Apple "Display P3" ICC profile 载荷（556 字节）。Qt 读图后把它挂在
# QImage 上，保存 PNG 时 libpng 校验失败（Incorrect data in iCCP）。
CORRUPT_DISPLAY_P3_ICC_B64 = (
    "AAACJGFwcGwEAAAAbW50clJHQiBYWVogB98ACgAOAA0ACAA5YWNzcEFQUEwAAAAAdml2bw"
    "AAAAAAAAAAAAAAAAAAAAAAAAAAAPbWAAEAAAAA0y1hcHBs5bsOmGe9Rs1LvkRuvRt1mAAA"
    "AAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAKZGVzYwAAAPwAAABlY3BydAAAAWQA"
    "AAAjd3RwdAAAAYgAAAAUclhZWgAAAZwAAAAUZ1hZWgAAAbAAAAAUYlhZWgAAAcQAAAAUclRS"
    "QwAAAdgAAAAgY2hhZAAAAfgAAAAsYlRSQwAAAdgAAAAgZ1RSQwAAAdgAAAAgZGVzYwAAAAAA"
    "AAALRGlzcGxheSBQMwAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAA"
    "AAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAB0ZXh0AAAAAENvcHlyaWdo"
    "dCBBcHBsZSBJbmMuLCAyMDE1AABYWVogAAAAAAAA81EAAQAAAAEWzFhZWiAAAAAAAACD3wAA"
    "Pb////+7WFlaIAAAAAAAAEq/AACxNwAACrlYWVogAAAAAAAAKDgAABELAADIuXBhcmEAAAAA"
    "AAMAAAACZmYAAPKwAAANUAAAE7YAAAn8c2YzMgAAAAAAAQxCAAAF3v//8yYAAAeTAAD9kP//"
    "+6L///2jAAAD3AAAwG5GUFhSAAAAAA=="
)

from PIL import Image

import apppath
from PyQt6.QtCore import QCoreApplication
from PyQt6.QtGui import QImage, QPixmap
from PyQt6.QtWidgets import QApplication

from blob_storage import BlobFileEntity
from services.thumbnail_provider import ThumbnailProvider
from thumbnail_disk_storage import ThumbnailDiskStorage


class FakeBlobStorage:
    def __init__(self, files: dict[str, str]):
        self.files = files
        self.read_calls = defaultdict(int)

    def read_file(self, entity):
        self.read_calls[entity.hash] += 1
        file_path = self.files.get(entity.hash)
        if file_path is None:
            raise FileNotFoundError(entity.hash)
        return file_path


class NoopDiskStorage:
    def read_file(self, file_hash):
        raise FileNotFoundError(file_hash)

    def save_image(self, image, file_hash):
        pass

    def save_pixmap(self, pixmap, file_hash):
        pass

    def delete_file(self, file_hash):
        raise FileNotFoundError(file_hash)


class TrackingDiskStorage:
    def __init__(self):
        self.read_calls = 0

    def read_file(self, file_hash):
        self.read_calls += 1
        raise FileNotFoundError(file_hash)

    def save_image(self, image, file_hash):
        pass

    def save_pixmap(self, pixmap, file_hash):
        pass

    def delete_file(self, file_hash):
        raise FileNotFoundError(file_hash)


class ThumbnailProviderTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = QApplication.instance() or QApplication([])
        source_dir = Path(__file__).resolve().parents[1] / "src"
        apppath.setup_data_path(source_dir)

    def _wait_until(self, predicate, timeout_ms: int = 5000) -> bool:
        deadline = time.monotonic() + timeout_ms / 1000
        while time.monotonic() < deadline:
            QCoreApplication.processEvents()
            if predicate():
                return True
            time.sleep(0.01)
        return False

    def _save_image(self, directory: Path, name: str, width: int, height: int) -> str:
        image = QImage(width, height, QImage.Format.Format_RGB32)
        image.fill(0xFFFF0000)
        path = directory / name
        self.assertTrue(image.save(str(path)))
        return str(path)

    def test_wide_image_keeps_aspect_ratio_and_writes_disk_cache(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            file_path = self._save_image(root, "wide.jpg", 400, 100)
            blob_storage = FakeBlobStorage({"wide-hash": file_path})
            disk_storage = ThumbnailDiskStorage(root / "thumbnails")
            provider = ThumbnailProvider(
                blob_storage=blob_storage,
                disk_storage=disk_storage,
            )

            thumbnail = provider.get_thumbnail(
                BlobFileEntity("wide-hash", ".jpg")
            )
            self.assertTrue(disk_storage.exists("wide-hash"))

        self.assertFalse(thumbnail.isNull())
        self.assertEqual((200, 50), (thumbnail.width(), thumbnail.height()))

    def test_tall_image_keeps_aspect_ratio(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            file_path = self._save_image(root, "tall.jpg", 100, 400)
            provider = ThumbnailProvider(
                blob_storage=FakeBlobStorage({"tall-hash": file_path}),
                disk_storage=NoopDiskStorage(),
            )
            thumbnail = provider.get_thumbnail(
                BlobFileEntity("tall-hash", ".jpg")
            )

        self.assertFalse(thumbnail.isNull())
        self.assertEqual((50, 200), (thumbnail.width(), thumbnail.height()))

    def test_small_image_returns_original_without_disk_cache(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            file_path = self._save_image(root, "small.png", 20, 20)
            blob_storage = FakeBlobStorage({"small-hash": file_path})
            disk_storage = ThumbnailDiskStorage(root / "thumbnails")
            provider = ThumbnailProvider(
                blob_storage=blob_storage,
                disk_storage=disk_storage,
            )

            thumbnail = provider.get_thumbnail(
                BlobFileEntity("small-hash", ".png")
            )
            self.assertFalse(disk_storage.exists("small-hash"))

        self.assertFalse(thumbnail.isNull())
        self.assertEqual((20, 20), (thumbnail.width(), thumbnail.height()))
        self.assertEqual(1, blob_storage.read_calls["small-hash"])

    def test_memory_cache_avoids_repeated_blob_reads(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            file_path = self._save_image(root, "cached.png", 400, 100)
            blob_storage = FakeBlobStorage({"cached-hash": file_path})
            provider = ThumbnailProvider(
                blob_storage=blob_storage,
                disk_storage=NoopDiskStorage(),
            )
            entity = BlobFileEntity("cached-hash", ".png")

            provider.get_thumbnail(entity)
            provider.get_thumbnail(entity)

        self.assertEqual(1, blob_storage.read_calls["cached-hash"])

    def test_lru_evicts_least_recently_used_entry(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            blob_storage = FakeBlobStorage(
                {
                    "a" * 40: self._save_image(root, "a.png", 400, 100),
                    "b" * 40: self._save_image(root, "b.png", 300, 150),
                    "c" * 40: self._save_image(root, "c.png", 200, 100),
                }
            )
            provider = ThumbnailProvider(
                blob_storage=blob_storage,
                disk_storage=NoopDiskStorage(),
                max_cache_size=2,
            )

            provider.get_thumbnail(BlobFileEntity("a" * 40, ".png"))
            provider.get_thumbnail(BlobFileEntity("b" * 40, ".png"))
            provider.get_thumbnail(BlobFileEntity("a" * 40, ".png"))
            provider.get_thumbnail(BlobFileEntity("c" * 40, ".png"))
            provider.get_thumbnail(BlobFileEntity("a" * 40, ".png"))
            provider.get_thumbnail(BlobFileEntity("b" * 40, ".png"))

        self.assertEqual(1, blob_storage.read_calls["a" * 40])
        self.assertEqual(2, blob_storage.read_calls["b" * 40])
        self.assertEqual(1, blob_storage.read_calls["c" * 40])
        self.assertEqual({"a" * 40, "b" * 40}, set(provider._memory_cache))

    def test_disk_cache_serves_thumbnail_when_blob_is_missing(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            file_path = self._save_image(root, "source.png", 400, 100)
            blob_storage = FakeBlobStorage({"disk-hash": file_path})
            disk_storage = ThumbnailDiskStorage(root / "thumbnails")
            provider = ThumbnailProvider(
                blob_storage=blob_storage,
                disk_storage=disk_storage,
            )

            provider.get_thumbnail(BlobFileEntity("disk-hash", ".png"))
            provider.clear_memory_cache()
            blob_storage.files.clear()
            thumbnail = provider.get_thumbnail(
                BlobFileEntity("disk-hash", ".png")
            )

        self.assertFalse(thumbnail.isNull())
        self.assertEqual((200, 50), (thumbnail.width(), thumbnail.height()))

    def test_request_thumbnail_serves_disk_cache_when_blob_missing(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            file_path = self._save_image(root, "source.png", 400, 100)
            blob_storage = FakeBlobStorage({"request-disk-hash": file_path})
            disk_storage = ThumbnailDiskStorage(root / "thumbnails")
            provider = ThumbnailProvider(
                blob_storage=blob_storage,
                disk_storage=disk_storage,
            )

            provider.get_thumbnail(
                BlobFileEntity("request-disk-hash", ".png")
            )
            provider.clear_memory_cache()
            blob_storage.files.clear()

            thumbnail = provider.request_thumbnail(
                BlobFileEntity("request-disk-hash", ".png")
            )

        self.assertFalse(thumbnail.isNull())
        self.assertEqual((200, 50), (thumbnail.width(), thumbnail.height()))

    def test_corrupt_disk_cache_is_rebuilt_from_blob(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            file_path = self._save_image(root, "source.png", 400, 100)
            blob_storage = FakeBlobStorage({"corrupt-hash": file_path})
            disk_storage = ThumbnailDiskStorage(root / "thumbnails")
            disk_file = root / "thumbnails" / "co" / "corrupt-hash.png"
            disk_file.parent.mkdir(parents=True)
            disk_file.write_bytes(b"not a png")
            provider = ThumbnailProvider(
                blob_storage=blob_storage,
                disk_storage=disk_storage,
            )

            thumbnail = provider.get_thumbnail(
                BlobFileEntity("corrupt-hash", ".png")
            )
            self.assertTrue(disk_file.exists())

        self.assertFalse(thumbnail.isNull())
        self.assertEqual((200, 50), (thumbnail.width(), thumbnail.height()))
        self.assertEqual(1, blob_storage.read_calls["corrupt-hash"])

    def test_clear_memory_cache_forces_reload(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            file_path = self._save_image(root, "cleared.png", 400, 100)
            blob_storage = FakeBlobStorage({"cleared-hash": file_path})
            provider = ThumbnailProvider(
                blob_storage=blob_storage,
                disk_storage=NoopDiskStorage(),
            )
            entity = BlobFileEntity("cleared-hash", ".png")

            provider.get_thumbnail(entity)
            provider.clear_memory_cache()
            provider.get_thumbnail(entity)

        self.assertEqual(2, blob_storage.read_calls["cleared-hash"])

    def test_missing_file_returns_null_pixmap(self):
        provider = ThumbnailProvider(
            blob_storage=FakeBlobStorage({}),
            disk_storage=NoopDiskStorage(),
        )
        thumbnail = provider.get_thumbnail(
            BlobFileEntity("missing-hash", ".png")
        )

        self.assertTrue(thumbnail.isNull())

    def test_request_thumbnail_returns_placeholder_then_ready(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            file_path = self._save_image(root, "async.jpg", 400, 100)
            blob_storage = FakeBlobStorage({"async-hash": file_path})
            disk_storage = ThumbnailDiskStorage(root / "thumbnails")
            provider = ThumbnailProvider(
                blob_storage=blob_storage,
                disk_storage=disk_storage,
            )
            entity = BlobFileEntity("async-hash", ".jpg")

            placeholder = provider.request_thumbnail(entity)

            self.assertFalse(placeholder.isNull())
            self.assertEqual(
                (provider.THUMBNAIL_SIZE, provider.THUMBNAIL_SIZE),
                (placeholder.width(), placeholder.height()),
            )
            self.assertTrue(
                self._wait_until(
                    lambda: "async-hash" in provider._memory_cache
                )
            )
            self.assertTrue(disk_storage.exists("async-hash"))

            thumbnail = provider.request_thumbnail(entity)
            self.assertEqual((200, 50), (thumbnail.width(), thumbnail.height()))

    def test_request_thumbnail_small_image_returns_placeholder_then_ready(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            file_path = self._save_image(root, "small.png", 20, 20)
            blob_storage = FakeBlobStorage({"small-async-hash": file_path})
            disk_storage = ThumbnailDiskStorage(root / "thumbnails")
            provider = ThumbnailProvider(
                blob_storage=blob_storage,
                disk_storage=disk_storage,
            )
            entity = BlobFileEntity("small-async-hash", ".png")

            placeholder = provider.request_thumbnail(entity)
            self.assertFalse(placeholder.isNull())

            self.assertTrue(
                self._wait_until(
                    lambda: "small-async-hash" in provider._memory_cache
                )
            )
            # 小图异步生成后只进内存缓存，不写磁盘。
            self.assertFalse(disk_storage.exists("small-async-hash"))

            thumbnail = provider.request_thumbnail(entity)
            self.assertEqual((20, 20), (thumbnail.width(), thumbnail.height()))
            # 主线程不再读取 blob，只有后台任务读取一次。
            self.assertEqual(1, blob_storage.read_calls["small-async-hash"])

    def test_request_thumbnail_deduplicates_in_flight(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            file_path = self._save_image(root, "dedup.jpg", 400, 100)
            provider = ThumbnailProvider(
                blob_storage=FakeBlobStorage({"dedup-hash": file_path}),
                disk_storage=NoopDiskStorage(),
            )
            entity = BlobFileEntity("dedup-hash", ".jpg")

            class FakePool:
                def __init__(self):
                    self.starts = 0

                def start(self, _job):
                    self.starts += 1

            fake_pool = FakePool()
            with patch.object(provider, "_ensure_pool", return_value=fake_pool):
                provider.request_thumbnail(entity)
                provider.request_thumbnail(entity)

            self.assertEqual(1, fake_pool.starts)

    def test_request_thumbnail_skips_disk_while_in_flight(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            file_path = self._save_image(root, "inflight.jpg", 400, 100)
            disk_storage = TrackingDiskStorage()
            provider = ThumbnailProvider(
                blob_storage=FakeBlobStorage({"inflight-hash": file_path}),
                disk_storage=disk_storage,
            )
            provider._in_flight.add("inflight-hash")

            placeholder = provider.request_thumbnail(
                BlobFileEntity("inflight-hash", ".jpg")
            )

            self.assertEqual(0, disk_storage.read_calls)
            self.assertEqual(
                (provider.THUMBNAIL_SIZE, provider.THUMBNAIL_SIZE),
                (placeholder.width(), placeholder.height()),
            )

    def test_corrupt_disk_cache_locked_delete_is_tolerated(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            file_path = self._save_image(root, "source.png", 400, 100)
            blob_storage = FakeBlobStorage({"locked-hash": file_path})
            disk_storage = ThumbnailDiskStorage(root / "thumbnails")
            disk_file = root / "thumbnails" / "lo" / "locked-hash.png"
            disk_file.parent.mkdir(parents=True)
            disk_file.write_bytes(b"not a png")
            provider = ThumbnailProvider(
                blob_storage=blob_storage,
                disk_storage=disk_storage,
            )

            with patch.object(
                disk_storage,
                "delete_file",
                side_effect=PermissionError(32, "file in use"),
            ):
                thumbnail = provider.get_thumbnail(
                    BlobFileEntity("locked-hash", ".png")
                )

            self.assertFalse(thumbnail.isNull())
            self.assertEqual((200, 50), (thumbnail.width(), thumbnail.height()))
            self.assertTrue(disk_file.exists())

    def test_request_thumbnail_missing_blob_returns_placeholder_and_marks_failed(self):
        provider = ThumbnailProvider(
            blob_storage=FakeBlobStorage({}),
            disk_storage=NoopDiskStorage(),
        )

        thumbnail = provider.request_thumbnail(
            BlobFileEntity("missing-hash", ".png")
        )

        self.assertFalse(thumbnail.isNull())
        self.assertTrue(
            self._wait_until(
                lambda: "missing-hash" in provider._failed_hashes
            )
        )

    def test_clear_memory_cache_resets_async_state(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            file_path = self._save_image(root, "clear.jpg", 400, 100)
            provider = ThumbnailProvider(
                blob_storage=FakeBlobStorage({"clear-hash": file_path}),
                disk_storage=NoopDiskStorage(),
            )
            entity = BlobFileEntity("clear-hash", ".jpg")

            provider.request_thumbnail(entity)
            self.assertIn("clear-hash", provider._in_flight)

            provider.clear_memory_cache()

            self.assertEqual(set(), provider._in_flight)
            self.assertEqual({}, provider._memory_cache)
            if provider._pool is not None:
                provider._pool.waitForDone(5000)
            QCoreApplication.processEvents()

    def test_placeholder_loads_windows_icon_asset(self):
        provider = ThumbnailProvider()

        icon = provider._load_placeholder_icon()
        placeholder = provider._get_placeholder()

        self.assertFalse(icon.isNull())
        self.assertFalse(placeholder.isNull())
        self.assertEqual(
            (provider.THUMBNAIL_SIZE, provider.THUMBNAIL_SIZE),
            (placeholder.width(), placeholder.height()),
        )



    def _png_has_iccp(self, path: Path) -> bool:
        """检查 PNG 文件是否包含 iCCP 块。"""
        data = path.read_bytes()
        if data[:8] != b"\x89PNG\r\n\x1a\n":
            return False
        pos = 8
        while pos + 8 <= len(data):
            length = struct.unpack(">I", data[pos:pos + 4])[0]
            name = data[pos + 4:pos + 8]
            if name == b"IEND":
                break
            if name == b"iCCP":
                return True
            pos += 12 + length
        return False

    def _make_bad_icc_jpeg(
        self, directory: Path, name: str, width: int, height: int
    ) -> str:
        """构造带损坏 Display P3 ICC profile 的 JPEG，复现 Qt 保存 PNG 失败。"""
        clean = directory / "clean_tmp.jpg"
        Image.new("RGB", (width, height), (200, 60, 60)).save(clean, quality=85)
        raw = clean.read_bytes()
        payload = base64.b64decode(CORRUPT_DISPLAY_P3_ICC_B64)
        app2 = (
            b"\xff\xe2"
            + struct.pack(">H", 14 + len(payload))
            + b"ICC_PROFILE\x00\x01\x01"
            + payload
        )
        path = directory / name
        path.write_bytes(raw[:2] + app2 + raw[2:])
        clean.unlink()
        return str(path)

    def test_corrupt_icc_jpeg_thumbnail_generated_and_saved(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            file_path = self._make_bad_icc_jpeg(root, "bad_icc.jpg", 400, 300)
            blob_storage = FakeBlobStorage({"bad-icc-hash": file_path})
            disk_storage = ThumbnailDiskStorage(root / "thumbnails")
            provider = ThumbnailProvider(
                blob_storage=blob_storage,
                disk_storage=disk_storage,
            )
            entity = BlobFileEntity("bad-icc-hash", ".jpg")

            placeholder = provider.request_thumbnail(entity)
            self.assertFalse(placeholder.isNull())

            self.assertTrue(
                self._wait_until(
                    lambda: "bad-icc-hash" in provider._memory_cache
                )
            )
            self.assertNotIn("bad-icc-hash", provider._failed_hashes)
            self.assertTrue(disk_storage.exists("bad-icc-hash"))

            saved_path = Path(disk_storage.read_file("bad-icc-hash"))
            pixmap = QPixmap(str(saved_path))
            self.assertFalse(pixmap.isNull())
            self.assertEqual((200, 150), (pixmap.width(), pixmap.height()))
            # 兜底读取的数据不带色彩空间：保存的 PNG 不应含 iCCP 块
            self.assertFalse(self._png_has_iccp(saved_path))

    def test_thumbnail_save_failure_cleans_partial_and_marks_failed(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            file_path = self._save_image(root, "source.png", 400, 100)
            blob_storage = FakeBlobStorage({"save-fail-hash": file_path})
            disk_storage = ThumbnailDiskStorage(root / "thumbnails")
            provider = ThumbnailProvider(
                blob_storage=blob_storage,
                disk_storage=disk_storage,
            )

            def failing_save(image, file_hash):
                # 模拟 Qt 保存 PNG 失败后留下的半成品文件
                target = disk_storage._get_file_path(file_hash)
                target.parent.mkdir(parents=True, exist_ok=True)
                target.write_bytes(b"partial")
                raise RuntimeError(f"保存缩略图失败: {target}")

            disk_storage.save_image = failing_save

            provider.request_thumbnail(
                BlobFileEntity("save-fail-hash", ".png")
            )

            self.assertTrue(
                self._wait_until(
                    lambda: "save-fail-hash" in provider._failed_hashes
                )
            )
            self.assertFalse(disk_storage.exists("save-fail-hash"))
            self.assertNotIn("save-fail-hash", provider._memory_cache)

    def test_pcx_sync_get_thumbnail_uses_safe_fallback(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            file_path = root / "image.pcx"
            Image.new("RGB", (60, 40), (10, 200, 30)).save(
                file_path, format="PCX"
            )
            provider = ThumbnailProvider(
                blob_storage=FakeBlobStorage({"pcx-hash": str(file_path)}),
                disk_storage=NoopDiskStorage(),
            )

            thumbnail = provider.get_thumbnail(
                BlobFileEntity("pcx-hash", ".pcx")
            )

            self.assertFalse(thumbnail.isNull())
            self.assertEqual((60, 40), (thumbnail.width(), thumbnail.height()))

    def test_request_thumbnail_pcx_uses_safe_fallback_async(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            file_path = root / "image.pcx"
            Image.new("RGB", (60, 40), (10, 200, 30)).save(
                file_path, format="PCX"
            )
            blob_storage = FakeBlobStorage({"pcx-async-hash": str(file_path)})
            disk_storage = ThumbnailDiskStorage(root / "thumbnails")
            provider = ThumbnailProvider(
                blob_storage=blob_storage,
                disk_storage=disk_storage,
            )

            placeholder = provider.request_thumbnail(
                BlobFileEntity("pcx-async-hash", ".pcx")
            )
            self.assertFalse(placeholder.isNull())

            self.assertTrue(
                self._wait_until(
                    lambda: "pcx-async-hash" in provider._memory_cache
                )
            )
            self.assertNotIn("pcx-async-hash", provider._failed_hashes)
            self.assertTrue(disk_storage.exists("pcx-async-hash"))

if __name__ == "__main__":
    unittest.main()
