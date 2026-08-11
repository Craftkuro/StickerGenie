# coding=utf-8
"""Model hash helpers backed by a precomputed SHA-1 sidecar file."""

import hashlib
import re
from pathlib import Path


MODEL_HASH_FILE_SUFFIX = ".sha1"
MODEL_HASH_PREFIX = "dinov2_vitb14_"
MODEL_HASH_DIGEST_LENGTH = 16

_SHA1_DIGEST_PATTERN = re.compile(r"\b[0-9a-f]{40}\b")
_HEX_DIGITS = frozenset("0123456789abcdef")


def _model_hash_file(model_path: Path) -> Path:
    return model_path.with_suffix(MODEL_HASH_FILE_SUFFIX)


def _format_model_hash(hex_digest: str) -> str:
    return f"{MODEL_HASH_PREFIX}{hex_digest[:MODEL_HASH_DIGEST_LENGTH]}"


def _is_hex_digest(value: str, length: int) -> bool:
    return len(value) == length and all(char in _HEX_DIGITS for char in value)


def _read_static_model_hash(hash_file: Path) -> str | None:
    try:
        content = hash_file.read_text(encoding="utf-8-sig").strip()
    except (OSError, UnicodeError):
        return None
    if not content:
        return None

    content = content.lower()
    digest_match = _SHA1_DIGEST_PATTERN.search(content)
    if digest_match is not None:
        return _format_model_hash(digest_match.group(0))

    if content.startswith(MODEL_HASH_PREFIX):
        rest = content[len(MODEL_HASH_PREFIX):]
        suffix = rest.split(maxsplit=1)[0] if rest else ""
        if _is_hex_digest(suffix, MODEL_HASH_DIGEST_LENGTH):
            return _format_model_hash(suffix)
    return None


def _calculate_sha1(
    model_path: str,
) -> str:
    digest = hashlib.sha1()
    with open(model_path, "rb") as model_file:
        for chunk in iter(lambda: model_file.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def calculate_model_hash(
    model_path: str,
) -> str:
    return _format_model_hash(_calculate_sha1(model_path))


def get_model_hash(model_path: Path) -> str:
    hash_file = _model_hash_file(model_path)
    static_hash = _read_static_model_hash(hash_file)
    if static_hash is not None:
        # Trust the sidecar as-is; model changes require updating the file.
        return static_hash

    return _format_model_hash(
        _calculate_sha1(str(model_path.resolve()))
    )
