# coding=utf-8
import tempfile
import unittest
from pathlib import Path

import apppath


class AppPathTests(unittest.TestCase):
    def setUp(self):
        self._original = (
            apppath.base_path,
            apppath.app_path,
            apppath.user_data_dir_path,
            apppath.main_config_file_path,
        )

    def tearDown(self):
        (
            apppath.base_path,
            apppath.app_path,
            apppath.user_data_dir_path,
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
            self.assertFalse((data_root / "StickerGenie Library").exists())


if __name__ == "__main__":
    unittest.main()
