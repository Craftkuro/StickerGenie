"""OCR stage functions running inside the ``batch_job_runner`` worker."""

from __future__ import annotations

import logging
from typing import Any


logger = logging.getLogger(__name__)

OCR_TEXT_PREFIX = "[OCR]"
OCR_TEXT_MAX_LENGTH = 4000

_engine: Any = None


def _is_cjk_like_last_char(char: str) -> bool:
    codepoint = ord(char)
    return (
        0x3400 <= codepoint <= 0x4DBF
        or 0x4E00 <= codepoint <= 0x9FFF
        or 0xF900 <= codepoint <= 0xFAFF
        or 0x3040 <= codepoint <= 0x30FF
        or 0xAC00 <= codepoint <= 0xD7AF
    )


def _normalize_ocr_items(items: Any) -> list[tuple[str, float]]:
    """Return (text, score) pairs from RapidOCR-like outputs."""

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
    """Compose filtered OCR blocks into the final database string."""

    parts: list[str] = []
    last_char = ""
    for text, score in _normalize_ocr_items(items):
        text = text.strip()
        if not text or score <= 0.9:
            continue
        if parts and not _is_cjk_like_last_char(last_char):
            parts.append(" ")
        parts.append(text)
        last_char = text[-1]

    body = "".join(parts).strip()
    if not body:
        return None
    return OCR_TEXT_PREFIX + body[:OCR_TEXT_MAX_LENGTH]


def load_ocr_engine():
    """Initialize the RapidOCR engine once per worker process."""

    global _engine
    if _engine is None:
        from rapidocr import RapidOCR

        _engine = RapidOCR(params={"Global.log_level": "WARNING"})
    return {"engine_name": "rapidocr"}


def _get_engine() -> Any:
    if _engine is None:
        raise RuntimeError("OCR engine is not initialized")
    return _engine


def ocr_image(image_path: str):
    """Run OCR for one image path; returns ``(image_path, text)``."""

    text = compose_ocr_text(_get_engine()(image_path))
    return image_path, text
