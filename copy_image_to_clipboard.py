# coding=utf-8
"""Copy one image to the clipboard using compound image and file formats."""

from __future__ import annotations

import argparse
import ctypes
import html
import sys
from pathlib import Path
from typing import Sequence

from PyQt6.QtCore import QByteArray, QMimeData, QMimeDatabase, QUrl
from PyQt6.QtGui import QGuiApplication, QImage


def detect_mime_type(image_path: Path) -> str:
    database = QMimeDatabase()
    mime_type = database.mimeTypeForFile(
        str(image_path),
        QMimeDatabase.MatchMode.MatchContent,
    ).name()
    if not mime_type.startswith("image/"):
        mime_type = database.mimeTypeForFile(
            str(image_path),
            QMimeDatabase.MatchMode.MatchExtension,
        ).name()
    if not mime_type.startswith("image/"):
        raise ValueError(f"无法识别图片格式：{image_path.name}")
    return mime_type


def build_compound_mime_data(
    image_path: str | Path,
    *,
    include_static_gif_fallback: bool = True,
) -> tuple[QMimeData, str, bool]:
    path = Path(image_path).expanduser().resolve()
    if not path.is_file():
        raise FileNotFoundError(f"图片文件不存在：{path}")

    encoded_image = path.read_bytes()
    mime_type = detect_mime_type(path)
    is_gif = mime_type == "image/gif" or path.suffix.casefold() == ".gif"
    file_url = QUrl.fromLocalFile(str(path))

    mime_data = QMimeData()
    mime_data.setUrls([file_url])
    mime_data.setData(mime_type, QByteArray(encoded_image))

    if is_gif:
        encoded_url = bytes(file_url.toEncoded()).decode("ascii")
        mime_data.setHtml(
            f'<meta charset="utf-8"><img src="{html.escape(encoded_url, quote=True)}">'
        )

    if not is_gif or include_static_gif_fallback:
        image = QImage.fromData(encoded_image)
        if image.isNull():
            raise ValueError(f"无法读取图片数据：{path.name}")
        mime_data.setImageData(image)

    return mime_data, mime_type, is_gif


def flush_windows_clipboard() -> None:
    """Render delayed Qt clipboard formats so they survive process exit."""
    if sys.platform != "win32":
        return

    ole_flush_clipboard = ctypes.windll.ole32.OleFlushClipboard
    ole_flush_clipboard.argtypes = []
    ole_flush_clipboard.restype = ctypes.c_long
    result = ole_flush_clipboard()
    if result < 0:
        raise OSError(f"OleFlushClipboard 失败：HRESULT 0x{result & 0xFFFFFFFF:08X}")


def copy_image(
    image_path: str | Path,
    *,
    include_static_gif_fallback: bool = True,
) -> tuple[str, bool, list[str]]:
    if QGuiApplication.instance() is None:
        raise RuntimeError("QGuiApplication 尚未初始化。")

    mime_data, mime_type, is_gif = build_compound_mime_data(
        image_path,
        include_static_gif_fallback=include_static_gif_fallback,
    )
    formats = mime_data.formats()
    QGuiApplication.clipboard().setMimeData(mime_data)
    QGuiApplication.processEvents()
    flush_windows_clipboard()
    return mime_type, is_gif, formats


def parse_arguments(arguments: Sequence[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="将一张图片以图片、原始数据和文件形式写入系统剪贴板。",
    )
    parser.add_argument("image_path", type=Path, help="要复制的图片文件路径")
    parser.add_argument(
        "--no-static-gif-fallback",
        action="store_true",
        help="不写入 GIF 首帧位图；可能提高动图粘贴兼容性，但画图将无法粘贴",
    )
    return parser.parse_args(arguments)


def main(arguments: Sequence[str] | None = None) -> int:
    options = parse_arguments(arguments)
    image_path = options.image_path.expanduser().resolve()
    if not image_path.is_file():
        print(f"错误：图片文件不存在：{image_path}", file=sys.stderr)
        return 2

    application = QGuiApplication.instance()
    if application is None:
        application = QGuiApplication([sys.argv[0]])
    application.setApplicationName("StickerGenie Clipboard Tester")

    try:
        mime_type, is_gif, formats = copy_image(
            image_path,
            include_static_gif_fallback=not options.no_static_gif_fallback,
        )
    except Exception as exc:
        print(f"复制失败：{exc}", file=sys.stderr)
        return 1

    print(f"已复制：{image_path}")
    print(f"图片类型：{mime_type}")
    if is_gif:
        fallback_status = "关闭" if options.no_static_gif_fallback else "开启"
        print(f"GIF 首帧兜底：{fallback_status}")
    print(f"MIME 格式：{', '.join(formats)}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
