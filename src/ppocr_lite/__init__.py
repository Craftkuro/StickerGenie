# -*- encoding: utf-8 -*-
"""ppocr_lite：轻量 PP-OCR 推理引擎（无 cv2/scipy 依赖）。

用法：
    from ppocr_lite import OcrEngine
    engine = OcrEngine()
    results = engine.recognize("path/to/image.png")  # [(text, score), ...]
"""

import logging

from .engine import OcrEngine

__all__ = ["OcrEngine", "recognize"]

_engine = None


def recognize(image_path) -> list:
    """一次性便捷函数：内部缓存引擎实例后同步识别。"""
    global _engine
    if _engine is None:
        _engine = OcrEngine(log_level=logging.WARNING)
    return _engine.recognize(image_path)
