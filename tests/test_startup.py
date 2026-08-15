# coding=utf-8
import unittest
import tempfile
from pathlib import Path
from unittest import mock

import apppath
import services.global_instances
import services.startup as startup
from services.settings import SETTINGS_SCHEMA, SETTINGS_VERSION, create_settings_manager


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

    def test_run_startup_tasks_configures_library_before_storage(self):
        order = []

        def mark(name):
            return lambda *args, **kwargs: order.append(name)

        with mock.patch.object(
            startup, "set_logging_levels", side_effect=mark("log")
        ), mock.patch.object(
            startup, "init_settings_manager", side_effect=mark("settings")
        ), mock.patch.object(
            startup, "configure_library_paths", side_effect=mark("configure")
        ), mock.patch.object(
            startup, "open_db", side_effect=mark("db")
        ), mock.patch.object(
            startup, "init_blob_storage", side_effect=mark("blob")
        ), mock.patch.object(
            startup, "init_thumbnail_cache", side_effect=mark("thumb")
        ), mock.patch.object(
            startup, "init_vector_store", side_effect=mark("vector")
        ):
            startup.run_startup_tasks()

        self.assertEqual(
            order,
            ["log", "settings", "configure", "db", "blob", "thumb", "vector"],
        )

    def test_configure_library_paths_reads_setting(self):
        class FakeSettings:
            def get(self, key):
                self.key = key
                return "My Library"

        fake_settings = FakeSettings()
        with mock.patch.object(
            services.global_instances,
            "current_settings_manager",
            fake_settings,
        ), mock.patch.object(apppath, "setup_library_paths") as setup:
            startup.configure_library_paths()

        self.assertEqual(fake_settings.key, "library_base_path")
        setup.assert_called_once_with("My Library")

    def test_configure_library_paths_requires_settings_manager(self):
        with mock.patch.object(
            services.global_instances, "current_settings_manager", None
        ):
            with self.assertRaises(RuntimeError):
                startup.configure_library_paths()

    def test_configure_library_paths_rejects_blank_value(self):
        class BlankSettings:
            def get(self, key):
                return "   "

        with mock.patch.object(
            services.global_instances,
            "current_settings_manager",
            BlankSettings(),
        ):
            with self.assertRaises(RuntimeError):
                startup.configure_library_paths()


if __name__ == "__main__":
    unittest.main()
