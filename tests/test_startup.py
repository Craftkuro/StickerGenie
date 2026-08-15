# coding=utf-8
import unittest
import tempfile
from pathlib import Path
from unittest import mock

import apppath
import services.global_instances
import services.startup as startup
from services.settings import SETTINGS_SCHEMA, SETTINGS_VERSION, create_settings_manager


class _FakeSettings:
    def __init__(self, value):
        self.value = value

    def get(self, key):
        self.key = key
        return self.value


class StartupLibraryPathsTests(unittest.TestCase):
    def test_settings_schema_declares_library_base_path(self):
        field = next(
            config_field
            for config_field in SETTINGS_SCHEMA
            if config_field.key == "library_base_path"
        )
        self.assertEqual("str", field.type.value)
        self.assertEqual("StickerGenie Library", field.default)

    def test_existing_config_gains_library_base_path(self):
        with tempfile.TemporaryDirectory() as tmp:
            config_path = Path(tmp) / "config.toml"
            config_path.write_text(
                '__version__ = "1.3.0"\n\n[config]\nrecent_search_limit = 3\n',
                encoding="utf-8",
            )

            manager = create_settings_manager(config_path)

            self.assertEqual(
                "StickerGenie Library",
                manager.get("library_base_path"),
            )
            content = config_path.read_text(encoding="utf-8")
            self.assertIn(f'__version__ = "{SETTINGS_VERSION}"', content)
            self.assertIn(
                'library_base_path = "StickerGenie Library"',
                content,
            )

    def test_run_startup_tasks_resolves_path_then_opens_library(self):
        order = []

        def mark(name):
            return lambda *args, **kwargs: order.append(name)

        def resolve_library_path_mock(*args, **kwargs):
            order.append("resolve")
            return mock.sentinel.library_path

        with mock.patch.object(
            startup, "set_logging_levels", side_effect=mark("log")
        ), mock.patch.object(
            startup, "init_settings_manager", side_effect=mark("settings")
        ), mock.patch.object(
            startup, "resolve_library_path",
            side_effect=resolve_library_path_mock,
        ), mock.patch.object(
            startup, "open_library", side_effect=mark("open")
        ) as open_library:
            startup.run_startup_tasks()

        self.assertEqual(order, ["log", "settings", "resolve", "open"])
        open_library.assert_called_once_with(mock.sentinel.library_path)

    def test_resolve_library_path_relative_to_base_path(self):
        with tempfile.TemporaryDirectory() as tmp:
            data_root = Path(tmp) / "data"
            fake_settings = _FakeSettings("My Library")

            with mock.patch.object(
                services.global_instances,
                "current_settings_manager",
                fake_settings,
            ), mock.patch.object(apppath, "base_path", data_root):
                result = startup.resolve_library_path()

            expected = data_root / "My Library" / "Default Library"
            self.assertEqual(expected, result)
            self.assertTrue(expected.exists())
            self.assertEqual("library_base_path", fake_settings.key)

    def test_resolve_library_path_accepts_absolute_config(self):
        with tempfile.TemporaryDirectory() as tmp:
            data_root = Path(tmp) / "data"
            custom_root = Path(tmp) / "custom-lib"

            with mock.patch.object(
                services.global_instances,
                "current_settings_manager",
                _FakeSettings(str(custom_root)),
            ), mock.patch.object(apppath, "base_path", data_root):
                result = startup.resolve_library_path()

            self.assertEqual(custom_root / "Default Library", result)
            self.assertTrue(result.exists())

    def test_resolve_library_path_requires_settings_manager(self):
        with mock.patch.object(
            services.global_instances, "current_settings_manager", None
        ):
            with self.assertRaises(RuntimeError):
                startup.resolve_library_path()

    def test_resolve_library_path_rejects_blank_value(self):
        with mock.patch.object(
            services.global_instances,
            "current_settings_manager",
            _FakeSettings("   "),
        ):
            with self.assertRaises(RuntimeError):
                startup.resolve_library_path()

    def test_open_library_requires_absolute_path(self):
        with mock.patch.object(startup, "open_db") as open_db, mock.patch.object(
            startup, "init_blob_storage"
        ) as init_blob, mock.patch.object(
            startup, "init_thumbnail_cache"
        ) as init_thumb, mock.patch.object(
            startup, "init_vector_store"
        ) as init_vector:
            with self.assertRaises(ValueError):
                startup.open_library("relative/library")

        open_db.assert_not_called()
        init_blob.assert_not_called()
        init_thumb.assert_not_called()
        init_vector.assert_not_called()

    def test_open_library_calls_each_store_with_library_path(self):
        with tempfile.TemporaryDirectory() as tmp:
            library_path = Path(tmp) / "Default Library"

            with mock.patch.object(startup, "open_db") as open_db, mock.patch.object(
                startup, "init_blob_storage"
            ) as init_blob, mock.patch.object(
                startup, "init_thumbnail_cache"
            ) as init_thumb, mock.patch.object(
                startup, "init_vector_store"
            ) as init_vector:
                startup.open_library(str(library_path))

        open_db.assert_called_once_with(library_path)
        init_blob.assert_called_once_with(library_path)
        init_thumb.assert_called_once_with(library_path)
        init_vector.assert_called_once_with(library_path)


if __name__ == "__main__":
    unittest.main()
