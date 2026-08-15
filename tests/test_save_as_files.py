import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from utils.save_as_files import (
    has_duplicate_original_file_names,
    save_as_files,
)


class SaveAsFilesTests(unittest.TestCase):
    def test_copies_files_with_original_names_and_returns_counts(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            temp_root = Path(temp_dir)
            source_one = temp_root / "one.png"
            source_two = temp_root / "two.png"
            source_one.write_bytes(b"one")
            source_two.write_bytes(b"two")
            destination = temp_root / "export"
            destination.mkdir()

            succeeded, failed = save_as_files(
                [
                    (source_one, "原始一.png"),
                    (source_two, "原始二.png"),
                ],
                destination,
            )

            self.assertEqual((2, 0), (succeeded, failed))
            self.assertEqual(b"one", (destination / "原始一.png").read_bytes())
            self.assertEqual(b"two", (destination / "原始二.png").read_bytes())

    def test_partial_failure_returns_counts(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            temp_root = Path(temp_dir)
            source_one = temp_root / "one.png"
            source_two = temp_root / "two.png"
            destination = temp_root / "export"
            destination.mkdir()

            with patch(
                "utils.save_as_files.shutil.copy2",
                side_effect=[None, OSError("boom")],
            ), patch(
                "utils.save_as_files.logger.exception"
            ):
                succeeded, failed = save_as_files(
                    [
                        (source_one, "one.png"),
                        (source_two, "two.png"),
                    ],
                    destination,
                )

            self.assertEqual((1, 1), (succeeded, failed))

    def test_target_names_override_original_names(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            temp_root = Path(temp_dir)
            source_path = temp_root / "source.png"
            source_path.write_bytes(b"content")
            destination = temp_root / "export"
            destination.mkdir()

            succeeded, failed = save_as_files(
                [(source_path, "原始名称.png")],
                destination,
                target_names=["改名后.png"],
            )

            self.assertEqual((1, 0), (succeeded, failed))
            self.assertFalse((destination / "原始名称.png").exists())
            self.assertEqual(
                b"content",
                (destination / "改名后.png").read_bytes(),
            )

    def test_target_names_length_must_match_source_files(self):
        with self.assertRaises(ValueError):
            save_as_files(
                [(Path("source.png"), "原始名称.png")],
                Path("destination"),
                target_names=[],
            )

    def test_has_duplicate_original_file_names_ignores_case_and_normalization(self):
        self.assertTrue(
            has_duplicate_original_file_names(["Same.png", "same.png"])
        )
        self.assertTrue(
            has_duplicate_original_file_names(
                ["cafe\u0301.png", "caf\u00e9.png"]
            )
        )
        self.assertFalse(
            has_duplicate_original_file_names(["one.png", "two.png"])
        )


if __name__ == "__main__":
    unittest.main()
