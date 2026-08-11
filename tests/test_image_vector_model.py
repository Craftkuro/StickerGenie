import hashlib
import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from image_features_extractor import DEFAULT_MODEL_FILENAME
from services.image_vector_model import (
    calculate_model_hash,
    get_model_hash,
)


class ImageVectorModelTests(unittest.TestCase):
    def setUp(self):
        self._temp_dir = tempfile.TemporaryDirectory()
        self.root = Path(self._temp_dir.name)
        self.model_path = self.root / DEFAULT_MODEL_FILENAME
        self.model_path.write_bytes(b"model-bytes")

    def tearDown(self):
        self._temp_dir.cleanup()

    def _sidecar_path(self) -> Path:
        return self.model_path.with_suffix(".sha1")

    def _expected_hash(self) -> str:
        digest = hashlib.sha1(self.model_path.read_bytes()).hexdigest()
        return digest[:16]

    def test_hash_depends_only_on_file_content(self):
        expected = calculate_model_hash(str(self.model_path))

        os.utime(self.model_path, (1_000_000_000, 1_000_000_000))

        self.assertEqual(expected, calculate_model_hash(str(self.model_path)))

    def test_reads_full_sha1_sidecar_without_hashing_model(self):
        digest = "ab" * 20
        self._sidecar_path().write_text(f"{digest}\n", encoding="ascii")

        with patch("services.image_vector_model._calculate_sha1") as calculate:
            result = get_model_hash(self.model_path)

        self.assertEqual(digest[:16], result)
        calculate.assert_not_called()

    def test_reads_short_hex_sidecar(self):
        self._sidecar_path().write_text("cdef0123456789ab\n", encoding="ascii")

        with patch("services.image_vector_model._calculate_sha1") as calculate:
            result = get_model_hash(self.model_path)

        self.assertEqual("cdef0123456789ab", result)
        calculate.assert_not_called()

    def test_reads_sha1sum_style_sidecar(self):
        digest = "cd" * 20
        self._sidecar_path().write_text(
            f"{digest}  {DEFAULT_MODEL_FILENAME}\n",
            encoding="ascii",
        )

        with patch("services.image_vector_model._calculate_sha1") as calculate:
            result = get_model_hash(self.model_path)

        self.assertEqual(digest[:16], result)
        calculate.assert_not_called()

    def test_missing_sidecar_computes_without_writing(self):
        expected_hash = self._expected_hash()

        result = get_model_hash(self.model_path)

        self.assertEqual(expected_hash, result)
        self.assertFalse(self._sidecar_path().exists())

    def test_invalid_sidecar_falls_back_without_writing(self):
        self._sidecar_path().write_text("not-a-hash\n", encoding="ascii")
        expected_hash = self._expected_hash()

        result = get_model_hash(self.model_path)

        self.assertEqual(expected_hash, result)
        self.assertEqual(
            "not-a-hash",
            self._sidecar_path().read_text(encoding="ascii").strip(),
        )


if __name__ == "__main__":
    unittest.main()
