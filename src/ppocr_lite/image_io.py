# -*- encoding: utf-8 -*-
"""读图：本地路径 → BGR ndarray（uint8 HWC）。

对照 rapidocr utils/load_image.py 的 str 路径分支逐公式复刻：
EXIF 转正、灰度/LA/RGBA 通道合成。与 rapidocr 的唯一有意差异：
调色板(P)模式图先转 RGBA，避免把索引值当灰度值。
"""

import logging
from pathlib import Path

import numpy as np
from PIL import Image, ImageOps

logger = logging.getLogger(__name__)


def load_bgr(image_path) -> np.ndarray:
    path = Path(image_path)
    if not path.exists():
        raise FileNotFoundError(f"{path} 不存在")

    with Image.open(path) as img:
        if getattr(img, "format", "") == "JPEG":
            _try_jpeg_draft(img)
        img = _exif_transpose(img)
        if img.mode == "P":
            img = img.convert("RGBA")
        elif img.mode == "1":
            img = img.convert("L")
        array = np.array(img)

    return convert_to_bgr(array)


def _try_jpeg_draft(img):
    """大图先降采样解码，仅对 JPEG 生效；失败不影响正常解码。"""
    try:
        if max(img.size) > 2000:
            img.draft(img.mode, (2000, 2000))
    except Exception as exc:
        logger.debug("JPEG draft 解码跳过：%s", exc)


def _exif_transpose(img: Image.Image) -> Image.Image:
    try:
        corrected = ImageOps.exif_transpose(img)
        return img if corrected is None else corrected
    except Exception:
        return img


def convert_to_bgr(array: np.ndarray) -> np.ndarray:
    """任意通道形态 → BGR uint8，语义对齐 rapidocr LoadImage.convert_img。"""
    if array.ndim == 2:
        return gray_to_bgr(array)

    channel = array.shape[2]
    if channel == 1:
        return gray_to_bgr(array[..., 0])
    if channel == 2:
        return blend_la_to_bgr(array)
    if channel == 3:
        return np.ascontiguousarray(array[:, :, ::-1])
    if channel == 4:
        return blend_rgba_to_bgr(array)
    raise ValueError(f"不支持的通道数：{channel}")


def gray_to_bgr(gray: np.ndarray) -> np.ndarray:
    return np.stack([gray, gray, gray], axis=-1)


def blend_la_to_bgr(array: np.ndarray) -> np.ndarray:
    """gray + alpha → BGR，复刻 rapidocr cvt_two_to_three（含饱和加法）。"""
    gray = array[..., 0].astype(np.uint16)
    alpha = array[..., 1]

    bgr = np.stack([gray, gray, gray], axis=-1)
    masked = np.where((alpha != 0)[..., None], bgr, 0)

    not_alpha = (255 - alpha).astype(np.uint16)[..., None]
    blended = np.clip(masked + not_alpha, 0, 255)
    return blended.astype(np.uint8)


def blend_rgba_to_bgr(array: np.ndarray) -> np.ndarray:
    """RGBA → BGR，完整保留 rapidocr cvt_four_to_three 公式：
    按非透明区平均亮度选黑/白底做 alpha 混合（透明贴纸的关键路径）。"""
    rgb = array[:, :, :3]
    alpha = array[:, :, 3]

    non_transparent_rgb = rgb[alpha > 0]
    if non_transparent_rgb.size == 0:
        bg_color = (255, 255, 255)
    else:
        r, g, b = (
            non_transparent_rgb[:, 0],
            non_transparent_rgb[:, 1],
            non_transparent_rgb[:, 2],
        )
        luminance = 0.299 * r + 0.587 * g + 0.114 * b
        avg_luminance = np.mean(luminance)
        bg_color = (255, 255, 255) if avg_luminance < 128 else (0, 0, 0)

    background = np.full_like(rgb, bg_color, dtype=np.uint8)
    alpha_norm = alpha.astype(np.float32) / 255.0
    foreground_blend = rgb.astype(np.float32) * alpha_norm[..., None]
    background_blend = background.astype(np.float32) * (1.0 - alpha_norm)[..., None]

    blended = (foreground_blend + background_blend).astype(np.uint8)
    return np.ascontiguousarray(blended[:, :, ::-1])
