"""运行在 batch_job_runner 子进程内的 OCR stage 函数。

OCR 引擎由 load_ocr_engine()（流水线 setup_func）在 worker 内一次性加载，
ocr_image() 是唯一 stage：单线程逐图识别并把结果拼成数据库文本。
"""

from __future__ import annotations

import logging
from typing import Any


logger = logging.getLogger(__name__)

# 数据库中的 OCR 文本统一带此前缀，便于与用户手写标签区分。
OCR_TEXT_PREFIX = "[OCR]"
OCR_TEXT_MAX_LENGTH = 4000

_engine: Any = None


def _is_cjk_like_last_char(char: str) -> bool:
    """判断字符是否属于中/日/韩等不需要加空格分隔的书写体系。"""
    codepoint = ord(char)
    return (
        0x3400 <= codepoint <= 0x4DBF
        or 0x4E00 <= codepoint <= 0x9FFF
        or 0xF900 <= codepoint <= 0xFAFF
        or 0x3040 <= codepoint <= 0x30FF
        or 0xAC00 <= codepoint <= 0xD7AF
    )


def _normalize_ocr_items(items: Any) -> list[tuple[str, float]]:
    """把 RapidOCR 风格的输出归一化为 ``(text, score)`` 列表。

    RapidOCR 不同版本会返回 result 对象或不同嵌套的列表结构，这里兼容
    ``[box, text, score]``、``[text, score]`` 以及带 txts/scores 属性的对象。
    """

    if items is None:
        return []

    if hasattr(items, "txts") and hasattr(items, "scores"):
        if items.txts is None or items.scores is None:
            return []
        return list(zip(items.txts, items.scores))

    if not isinstance(items, (list, tuple)):
        raise ValueError(f"unexpected OCR output type: {type(items).__name__}")

    if (
        len(items) == 2
        and isinstance(items[1], (int, float))
        and not isinstance(items[1], bool)
        and not isinstance(items[0], (int, float))
    ):
        items = items[0]
        if items is None:
            return []
        return _normalize_ocr_items(items)

    normalized: list[tuple[str, float]] = []
    for item in items:
        if (
            isinstance(item, (list, tuple))
            and len(item) >= 3
            and isinstance(item[1], str)
            and isinstance(item[2], (int, float))
            and not isinstance(item[2], bool)
        ):
            normalized.append((item[1], float(item[2])))
            continue
        if (
            isinstance(item, (list, tuple))
            and len(item) == 2
            and isinstance(item[0], str)
            and isinstance(item[1], (int, float))
            and not isinstance(item[1], bool)
        ):
            normalized.append((item[0], float(item[1])))
            continue
        raise ValueError(f"invalid OCR item: {item!r}")
    return normalized


def compose_ocr_text(items) -> str | None:
    """把过滤后的 OCR 文本块拼成最终入库字符串。"""

    parts: list[str] = []
    last_char = ""
    for text, score in _normalize_ocr_items(items):
        text = text.strip()
        # 只保留高置信度文本，避免模糊图片产生大量噪声。
        if not text or score <= 0.9:
            continue
        # CJK 字符之间不加空格；其他书写体系之间用空格分隔，提升可读性。
        if parts and not _is_cjk_like_last_char(last_char):
            parts.append(" ")
        parts.append(text)
        last_char = text[-1]

    body = "".join(parts).strip()
    if not body:
        return None
    return OCR_TEXT_PREFIX + body[:OCR_TEXT_MAX_LENGTH]


def load_ocr_engine():
    """在 worker 进程内一次性初始化 RapidOCR 引擎。"""

    global _engine
    if _engine is None:
        from rapidocr import RapidOCR

        _engine = RapidOCR(params={"Global.log_level": "WARNING"})
    return {"engine_name": "rapidocr"}


def _get_engine() -> Any:
    """返回已初始化的 OCR 引擎；未初始化时视为流水线契约错误。"""
    if _engine is None:
        raise RuntimeError("OCR engine is not initialized")
    return _engine


def ocr_image(image_path: str):
    """OCR stage：识别一张图片，返回 ``(image_path, text)``。"""

    text = compose_ocr_text(_get_engine()(image_path))
    return image_path, text
