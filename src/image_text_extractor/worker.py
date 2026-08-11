"""Spawn-safe worker entry point and OCR text composition."""

from __future__ import annotations

import logging
import traceback
from collections.abc import Sequence
from multiprocessing.connection import Connection
from typing import Any

from .models import (
    OCR_TEXT_MAX_LENGTH,
    OCR_TEXT_PREFIX,
    ImageTextResult,
    WorkerStartupInfo,
)


logger = logging.getLogger(__name__)

INIT_OK = "INIT_OK"
INIT_ERROR = "INIT_ERROR"
REQUEST_BATCH = "REQUEST_BATCH"
PROCESS_BATCH = "PROCESS_BATCH"
END_INPUT = "END_INPUT"
CANCEL = "CANCEL"
BATCH_RESULT = "BATCH_RESULT"
JOB_ERROR = "JOB_ERROR"
DONE = "DONE"


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


def _send_message(connection: Connection, kind: str, payload: Any = None) -> None:
    connection.send((kind, payload))


def _receive_message(connection: Connection) -> tuple[str, Any]:
    message = connection.recv()
    if (
        not isinstance(message, tuple)
        or len(message) != 2
        or not isinstance(message[0], str)
    ):
        raise RuntimeError(f"invalid IPC message: {message!r}")
    return message


def _image_error_message(error: Exception) -> str:
    detail = str(error).strip()
    return f"{type(error).__name__}: {detail}" if detail else type(error).__name__


def process_image_batch(engine, image_paths: Sequence[str]):
    """Run OCR for one ordered path batch inside the worker process."""

    results: list[ImageTextResult] = []
    for image_path in image_paths:
        try:
            text = compose_ocr_text(engine(image_path))
            results.append(ImageTextResult.succeeded(image_path, text))
        except Exception as error:
            results.append(
                ImageTextResult.failed(
                    image_path,
                    _image_error_message(error),
                )
            )
    return tuple(results)


def _initialize_engine():
    from rapidocr import RapidOCR

    return RapidOCR(params={"Global.log_level": "WARNING"})


def worker_process_entry(connection: Connection) -> None:
    """Top-level spawn target for one OCR job."""

    try:
        try:
            engine = _initialize_engine()
        except BaseException as error:
            logger.error("image text worker initialization failed: %s", error)
            _send_message(connection, INIT_ERROR, _image_error_message(error))
            return

        _send_message(
            connection,
            INIT_OK,
            WorkerStartupInfo(engine_name="rapidocr"),
        )
        _send_message(connection, REQUEST_BATCH)

        while True:
            kind, payload = _receive_message(connection)
            if kind == PROCESS_BATCH:
                if (
                    not isinstance(payload, (tuple, list))
                    or not payload
                    or not all(isinstance(path, str) and path for path in payload)
                ):
                    raise RuntimeError(
                        "PROCESS_BATCH requires non-empty string paths"
                    )
                results = process_image_batch(engine, payload)
                _send_message(connection, BATCH_RESULT, results)
                _send_message(connection, REQUEST_BATCH)
            elif kind == END_INPUT:
                _send_message(connection, DONE, False)
                return
            elif kind == CANCEL:
                _send_message(connection, DONE, True)
                return
            else:
                raise RuntimeError(f"unknown parent IPC message: {kind!r}")
    except (EOFError, BrokenPipeError):
        logger.info("image text worker connection closed")
    except BaseException as error:
        logger.error("image text worker failed:\n%s", traceback.format_exc())
        try:
            _send_message(connection, JOB_ERROR, _image_error_message(error))
        except (EOFError, BrokenPipeError, OSError):
            pass
    finally:
        connection.close()
