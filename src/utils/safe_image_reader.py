# coding=utf-8
"""安全读取图片：正常读取失败时用自定义参数兜底，返回合法可用的图像数据。

背景（notes/library_scroll_performance_report.md 6.1）：
- 部分图片的文件头/辅助块损坏（例如带非法 ICC profile 的 JPEG、格式与扩展名
  不符的文件），按普通方式读取可能失败，或读出的数据无法正常保存为 PNG。
- 本模块在普通读取失败时，改用自定义初始参数重试：
  * 从文件数据（magic bytes）识别真实格式，而不是依赖扩展名；
  * 丢弃 ICC profile，按 sRGB 语义转换为 RGB/RGBA——结果不一定在色彩管理上
    “绝对正确”，但一定是合法、可显示、可保存的图像数据。
- 所有策略都失败时抛出 SafeImageReadError，由调用方决定如何处理。
"""

from __future__ import annotations

import io
import logging
from dataclasses import dataclass
from pathlib import Path
from typing import BinaryIO, Sequence

from PIL import Image

logger = logging.getLogger(__name__)

_ALPHA_MODES = frozenset({"RGBA", "LA", "PA"})


class SafeImageReadError(RuntimeError):
    """所有读取策略都失败时抛出，供调用方决定如何处理。"""

    def __init__(self, path: str | Path, errors: Sequence[Exception]) -> None:
        self.path = str(path)
        self.errors = tuple(errors)
        details = "; ".join(
            f"{type(error).__name__}: {error}" for error in self.errors
        )
        super().__init__(
            f"无法安全读取图片 {self.path}: {details or '未知错误'}"
        )


@dataclass(frozen=True)
class SafeImageReadResult:
    """安全读取的结果。

    Attributes:
        image: 读取到的 PIL Image。
        format_name: 实际识别的图片格式（PIL 格式名，如 JPEG/PNG）。
        used_fallback: 是否使用了兜底参数（正常读取失败后重试）。
        warnings: 读取过程中的警告信息，供日志与排查使用。
    """

    image: Image.Image
    format_name: str | None
    used_fallback: bool
    warnings: tuple[str, ...]


def detect_image_format(data: bytes) -> str | None:
    """根据文件头（magic bytes）识别真实图片格式，不依赖扩展名。

    Returns:
        PIL 格式名（如 "JPEG"），无法识别时返回 None。
    """
    if data.startswith(b"\x89PNG\r\n\x1a\n"):
        return "PNG"
    if data.startswith(b"\xff\xd8\xff"):
        return "JPEG"
    if data.startswith((b"GIF87a", b"GIF89a")):
        return "GIF"
    if data.startswith(b"BM"):
        return "BMP"
    if data.startswith((b"II*\x00", b"MM\x00*")):
        return "TIFF"
    if data.startswith(b"RIFF") and len(data) >= 12 and data[8:12] == b"WEBP":
        return "WEBP"
    return None


def _to_srgb(image: Image.Image) -> Image.Image:
    """转换为 sRGB 语义的 RGB/RGBA，丢弃可能损坏的 ICC profile。"""
    if image.mode in _ALPHA_MODES or (
        image.mode == "P" and "transparency" in image.info
    ):
        return image.convert("RGBA")
    return image.convert("RGB")


def _open_and_load(
    source: str | Path | BinaryIO,
    formats: Sequence[str] | None,
) -> tuple[Image.Image, str | None]:
    """打开图片并强制解码，返回 (image, format_name)。"""
    with Image.open(source, formats=formats) as image:
        image.load()
        # copy 一份：避免延迟加载依赖源文件/句柄的生命周期
        return image.copy(), image.format


def open_image_safe(
    path: str | Path,
    *,
    formats: Sequence[str] | None = None,
) -> SafeImageReadResult:
    """安全打开一张图片。

    策略（按顺序）：
    1. 正常方式：PIL Image.open（本身按文件内容识别格式）并强制解码；
    2. 兜底：读取文件字节，用 magic bytes 识别真实格式，显式指定格式重试，
       并把结果转换为 sRGB 语义的 RGB/RGBA（丢弃可能损坏的 ICC profile）；
    3. 全部失败：抛出 SafeImageReadError。

    Args:
        path: 图片文件路径。
        formats: 限制尝试的 PIL 格式名；默认由 PIL 自动识别。

    Raises:
        SafeImageReadError: 所有读取策略都失败时抛出。
    """
    file_path = Path(path)
    errors: list[Exception] = []

    try:
        image, detected_format = _open_and_load(file_path, formats=formats)
    except Exception as error:
        errors.append(error)
        logger.debug("正常读取图片失败 %s: %s", file_path, error)
    else:
        return SafeImageReadResult(
            image=image,
            format_name=detected_format,
            used_fallback=False,
            warnings=(),
        )

    warnings = [f"正常读取失败（{errors[-1]}），尝试兜底参数"]
    try:
        data = file_path.read_bytes()
    except Exception as error:
        errors.append(error)
        raise SafeImageReadError(file_path, errors) from error

    detected_format = detect_image_format(data)
    if detected_format is not None:
        warnings.append(
            f"按文件内容识别为 {detected_format}"
            f"（扩展名：{file_path.suffix or '无'}）"
        )
        try:
            image, opened_format = _open_and_load(
                io.BytesIO(data), formats=[detected_format]
            )
        except Exception as error:
            errors.append(error)
            logger.debug("兜底读取图片失败 %s: %s", file_path, error)
        else:
            return SafeImageReadResult(
                image=_to_srgb(image),
                format_name=opened_format or detected_format,
                used_fallback=True,
                warnings=tuple(warnings),
            )

    raise SafeImageReadError(file_path, errors)


def generate_thumbnail_safe(
    path: str | Path,
    max_size: int,
) -> SafeImageReadResult:
    """安全读取图片并生成最长边不超过 max_size 的 sRGB 缩略图。

    生成的图像统一转换为 RGB/RGBA（丢弃 ICC profile），可以直接保存为 PNG。

    Raises:
        SafeImageReadError: 所有读取策略都失败时抛出。
    """
    result = open_image_safe(path)
    image = _to_srgb(result.image)
    if max_size > 0 and (image.width > max_size or image.height > max_size):
        image.thumbnail((max_size, max_size), Image.Resampling.LANCZOS)
    return SafeImageReadResult(
        image=image,
        format_name=result.format_name,
        used_fallback=result.used_fallback,
        warnings=result.warnings,
    )


def pil_to_qimage(image: Image.Image):
    """把 PIL Image 转换为 QImage（RGB/RGBA），返回独立副本。

    仅在需要与 PyQt6 交互时调用；本模块其余部分不依赖 Qt。
    """
    from PyQt6.QtGui import QImage

    mode = image.mode
    if mode == "RGBA":
        qimage_format = QImage.Format.Format_RGBA8888
        raw = image.tobytes("raw", "RGBA")
        bytes_per_line = image.width * 4
    elif mode == "RGB":
        qimage_format = QImage.Format.Format_RGB888
        raw = image.tobytes("raw", "RGB")
        bytes_per_line = image.width * 3
    else:
        return pil_to_qimage(_to_srgb(image))

    qimage = QImage(raw, image.width, image.height, bytes_per_line, qimage_format)
    # copy 一份，避免与传入的 bytes 缓冲区共享内存
    return qimage.copy()