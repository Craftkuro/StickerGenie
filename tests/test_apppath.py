# coding=utf-8
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

import apppath


class AppPathTests(unittest.TestCase):
    def setUp(self):
        self._original = (
            apppath.base_path,
            apppath.app_path,
            apppath.user_data_dir_path,
            apppath.library_base_path,
            apppath.default_library_path,
            apppath.main_config_file_path,
        )

    def tearDown(self):
        (
            apppath.base_path,
            apppath.app_path,
            apppath.user_data_dir_path,
            apppath.library_base_path,
            apppath.default_library_path,
            apppath.main_config_file_path,
        ) = self._original

    def test_setup_data_path_does_not_create_library_dirs(self):
        with tempfile.TemporaryDirectory() as tmp:
            data_root = Path(tmp) / "data"
            apppath.setup_data_path(Path(tmp) / "app", data_root)

            self.assertEqual(apppath.base_path, data_root)
            self.assertEqual(
                apppath.user_data_dir_path,
                data_root / "StickerGenie Settings",
            )
            self.assertEqual(
                apppath.main_config_file_path,
                data_root / "StickerGenie Settings" / "config.toml",
            )
            self.assertTrue(apppath.user_data_dir_path.exists())
            self.assertIsNone(apppath.library_base_path)
            self.assertIsNone(apppath.default_library_path)
            self.assertFalse((data_root / "StickerGenie Library").exists())

    def test_setup_library_paths_relative_to_base_path(self):
        with tempfile.TemporaryDirectory() as tmp:
            data_root = Path(tmp) / "data"
            apppath.setup_data_path(Path(tmp) / "app", data_root)

            apppath.setup_library_paths("StickerGenie Library")

            expected_root = data_root / "StickerGenie Library"
            self.assertEqual(apppath.library_base_path, expected_root)
            self.assertEqual(
                apppath.default_library_path,
                expected_root / "Default Library",
            )
            self.assertTrue(expected_root.exists())
            self.assertTrue(apppath.default_library_path.exists())

    def test_setup_library_paths_accepts_absolute_path(self):
        with tempfile.TemporaryDirectory() as tmp:
            data_root = Path(tmp) / "data"
            custom_root = Path(tmp) / "custom-lib"
            apppath.setup_data_path(Path(tmp) / "app", data_root)

            apppath.setup_library_paths(custom_root)

            self.assertEqual(apppath.library_base_path, custom_root)
            self.assertEqual(
                apppath.default_library_path,
                custom_root / "Default Library",
            )
            self.assertTrue(custom_root.exists())

    def test_setup_library_paths_requires_base_path(self):
        with patch.object(apppath, "base_path", None):
            with self.assertRaises(RuntimeError):
                apppath.setup_library_paths("StickerGenie Library")


if __name__ == "__main__":
    unittest.main()
