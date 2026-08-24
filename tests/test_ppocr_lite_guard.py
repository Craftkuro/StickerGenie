# coding=utf-8
"""回归护栏：ppocr_lite 源码不得引入禁用依赖，公开 API 契约保持稳定。"""

import ast
import inspect
import unittest
from pathlib import Path

import numpy as np

import ppocr_lite
from ppocr_lite import OcrEngine
from ppocr_lite.params import OcrParams

_PACKAGE_DIR = Path(ppocr_lite.__file__).resolve().parent

FORBIDDEN_MODULES = ("cv2", "scipy", "shapely", "omegaconf", "yaml", "requests", "tqdm")


class DependencyGuardTests(unittest.TestCase):
    def test_sources_do_not_import_forbidden_modules(self):
        for source_file in _PACKAGE_DIR.glob("*.py"):
            content = source_file.read_text(encoding="utf-8")
            for module in FORBIDDEN_MODULES:
                self.assertNotRegex(
                    content,
                    rf"^\s*(import {module}\b|from {module}\b)",
                    f"{source_file.name} 不应导入 {module}",
                )

    def test_imported_third_party_modules_are_whitelisted(self):
        allowed = {"numpy", "PIL", "onnxruntime", "pyclipper", "logging", "math", "apppath"}
        imported = set()
        for source_file in _PACKAGE_DIR.glob("*.py"):
            tree = ast_parse(source_file.read_text(encoding="utf-8"))
            for node in walk_nodes(tree):
                if isinstance(node, ast.Import):
                    imported.update(alias.name.split(".")[0] for alias in node.names)
                elif isinstance(node, ast.ImportFrom) and node.level == 0 and node.module:
                    imported.add(node.module.split(".")[0])
        third_party = {
            name for name in imported if name not in STDLIB_HINT and name != "ppocr_lite"
        }
        unexpected = third_party - allowed
        self.assertEqual(set(), unexpected)


def ast_parse(source):
    return ast.parse(source)


def walk_nodes(tree):
    yield from ast.walk(tree)


STDLIB_HINT = {
    "__future__",
    "abc",
    "argparse",
    "ast",
    "collections",
    "contextlib",
    "dataclasses",
    "datetime",
    "enum",
    "functools",
    "hashlib",
    "inspect",
    "io",
    "itertools",
    "json",
    "logging",
    "math",
    "os",
    "pathlib",
    "re",
    "sys",
    "tempfile",
    "time",
    "typing",
    "unittest",
}


class PublicApiContractTests(unittest.TestCase):
    def test_module_level_recognize_exists(self):
        self.assertTrue(callable(ppocr_lite.recognize))

    def test_engine_recognize_signature(self):
        signature = list(inspect.signature(OcrEngine.recognize).parameters)
        self.assertEqual(["self", "image"], signature)

    def test_params_frozen(self):
        params = OcrParams()
        with self.assertRaises(Exception):
            params.text_score = 0.9

    def test_default_values_match_rapidocr_config(self):
        params = OcrParams()
        self.assertEqual(0.5, params.text_score)
        self.assertEqual(30, params.min_side_len)
        self.assertEqual(2000, params.max_side_len)
        self.assertEqual(736, params.det_limit_side_len)
        self.assertEqual("min", params.det_limit_type)
        self.assertEqual(0.3, params.det_thresh)
        self.assertEqual(0.5, params.det_box_thresh)
        self.assertEqual(1.6, params.det_unclip_ratio)
        self.assertTrue(params.det_use_dilation)
        self.assertEqual((3, 48, 192), params.cls_image_shape)
        self.assertEqual((3, 48, 320), params.rec_image_shape)

    def test_models_present_in_src_root(self):
        from ppocr_lite.sessions import resolve_models_dir
        from ppocr_lite.params import (
            CLS_MODEL_FILENAME,
            DET_MODEL_FILENAME,
            REC_MODEL_FILENAME,
        )

        models_dir = resolve_models_dir(None)
        for filename in (DET_MODEL_FILENAME, CLS_MODEL_FILENAME, REC_MODEL_FILENAME):
            self.assertTrue(
                (models_dir / filename).is_file(), f"缺少模型文件 {filename}"
            )


if __name__ == "__main__":
    unittest.main()
