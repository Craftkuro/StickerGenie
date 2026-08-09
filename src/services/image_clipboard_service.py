# coding=utf-8
from __future__ import annotations

import html
import logging
import re
import shutil
import tempfile
import time
import uuid
from pathlib import Path

from PyQt6.QtCore import QByteArray, QMimeData, QMimeDatabase, QUrl
from PyQt6.QtGui import QGuiApplication, QImage

logger = logging.getLogger(__name__)

STAGING_TTL_SECONDS = 24 * 60 * 60
_INVALID_FILENAME_CHARACTERS = re.compile(r'[<>:"/\\|?*\x00-\x1f]')
_WINDOWS_RESERVED_NAMES = {
    "CON",
    "PRN",
    "AUX",
    "NUL",
    *(f"COM{number}" for number in range(1, 10)),
    *(f"LPT{number}" for number in range(1, 10)),
}


def _default_staging_root() -> Path:
    return Path(tempfile.gettempdir()) / "StickerGenie" / "clipboard"


def _safe_display_name(display_name: str, source_path: Path) -> str:
    # Treat both slash styles as separators even when tests run off Windows.
    candidate = re.split(r"[/\\]", display_name.strip())[-1]
    candidate = _INVALID_FILENAME_CHARACTERS.sub("_", candidate).rstrip(" .")
    if not candidate or candidate in {".", ".."}:
        candidate = source_path.name

    source_suffix = source_path.suffix
    candidate_path = Path(candidate)
    if source_suffix and candidate_path.suffix.casefold() != source_suffix.casefold():
        candidate = f"{candidate_path.stem or 'image'}{source_suffix}"

    if Path(candidate).stem.upper() in _WINDOWS_RESERVED_NAMES:
        candidate = f"_{candidate}"
    return candidate


def _cleanup_expired_staging_directories(
    staging_root: Path,
    *,
    now: float | None = None,
) -> None:
    if not staging_root.exists():
        return

    cutoff = (time.time() if now is None else now) - STAGING_TTL_SECONDS
    for child in staging_root.iterdir():
        if not child.is_dir() or child.is_symlink():
            continue
        try:
            if child.stat().st_mtime < cutoff:
                shutil.rmtree(child)
        except OSError:
            logger.warning("无法清理剪贴板暂存目录：%s", child, exc_info=True)


def _stage_image_file(
    source_path: Path,
    display_name: str,
    staging_root: Path,
) -> Path:
    staging_root.mkdir(parents=True, exist_ok=True)
    _cleanup_expired_staging_directories(staging_root)

    staging_directory = staging_root / uuid.uuid4().hex
    staging_directory.mkdir()
    staged_path = staging_directory / _safe_display_name(display_name, source_path)
    shutil.copy2(source_path, staged_path)
    return staged_path


def _detect_mime_type(source_path: Path) -> str:
    database = QMimeDatabase()
    mime_type = database.mimeTypeForFile(
        str(source_path),
        QMimeDatabase.MatchMode.MatchContent,
    ).name()
    if not mime_type.startswith("image/"):
        mime_type = database.mimeTypeForFile(
            str(source_path),
            QMimeDatabase.MatchMode.MatchExtension,
        ).name()
    if not mime_type.startswith("image/"):
        raise ValueError(f"无法识别图片格式：{source_path.name}")
    return mime_type


def _local_file_url_for_html(path: Path) -> str:
    """Return a file:/// URL for QQ/WeChat HTML paste.

    Keep non-ASCII path segments raw instead of percent-encoding them:
    QQ/WeChat's HTML parser follows the article's ``file:///path`` form,
    while Qt's QUrl.toEncoded() output may not be resolved by them.
    """
    return f"file:///{path.as_posix()}"


def create_image_mime_data(
    source_path: str | Path,
    display_name: str,
    *,
    staging_root: str | Path | None = None,
) -> tuple[QMimeData, Path]:
    """Build clipboard data and a staged file with a readable name.

    GIFs are exposed as an HTML fragment with a local file URL so QQ/WeChat
    paste them as animated images (plain bitmap copies only carry the first
    frame). Other images use file + raw bytes + bitmap formats so they paste
    everywhere.
    """
    source = Path(source_path)
    if not source.is_file():
        raise FileNotFoundError(f"图片文件不存在：{source}")

    mime_type = _detect_mime_type(source)
    is_gif = mime_type == "image/gif" or source.suffix.casefold() == ".gif"
    root = Path(staging_root) if staging_root is not None else _default_staging_root()
    staged_path = _stage_image_file(source, display_name, root)

    mime_data = QMimeData()
    if is_gif:
        local_url = _local_file_url_for_html(staged_path.resolve())
        mime_data.setHtml(
            '<html><body><!--StartFragment-->'
            '<meta charset="utf-8">'
            f'<img src="{html.escape(local_url, quote=True)}" />'
            '<!--EndFragment--></body></html>'
        )
        return mime_data, staged_path

    encoded_image = source.read_bytes()
    staged_url = QUrl.fromLocalFile(str(staged_path.resolve()))
    mime_data.setUrls([staged_url])
    mime_data.setData(mime_type, QByteArray(encoded_image))

    image = QImage.fromData(encoded_image)
    if image.isNull():
        raise ValueError(f"无法读取图片数据：{source.name}")
    mime_data.setImageData(image)

    return mime_data, staged_path


def copy_image_to_clipboard(
    source_path: str | Path,
    display_name: str,
) -> Path:
    """Copy an image to the clipboard (GIFs as an HTML fragment)."""
    if QGuiApplication.instance() is None:
        raise RuntimeError("应用程序尚未初始化。")

    mime_data, staged_path = create_image_mime_data(source_path, display_name)
    QGuiApplication.clipboard().setMimeData(mime_data)
    return staged_path
