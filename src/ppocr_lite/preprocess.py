# -*- encoding: utf-8 -*-
"""预处理：整图限界缩放、垂直补边、det/cls/rec 三种输入张量构造。

resize 一律走 Pillow 双线性（替代 cv2.resize INTER_LINEAR，插值差异已接受）。
本项目不消费 boxes，坐标回映链整体省略。
"""

import math

import numpy as np
from PIL import Image

from .det_postprocess import DetResizeError


def _resize_bilinear(img: np.ndarray, width: int, height: int) -> np.ndarray:
    pil_img = Image.fromarray(img)
    resized = pil_img.resize((width, height), Image.Resampling.BILINEAR)
    return np.asarray(resized)


def _round_to_multiple_of_32(value: int) -> int:
    return int(round(value / 32) * 32)


def limit_image_size(
    img: np.ndarray, min_side_len: float = 30, max_side_len: float = 2000
) -> np.ndarray:
    """最长边 >max 等比缩小、最短边 <min 等比放大，尺寸取 32 倍数；合规则零拷贝直通。"""
    h, w = img.shape[:2]
    if max(h, w) > max_side_len:
        img = _reduce_max_side(img, max_side_len)
        h, w = img.shape[:2]
    if min(h, w) < min_side_len:
        img = _increase_min_side(img, min_side_len)
    return img


def _reduce_max_side(img: np.ndarray, max_side_len: float) -> np.ndarray:
    """复刻 rapidocr reduce_max_side：等比缩小到最长边不超限，取整到 32 倍数。"""
    h, w = img.shape[:2]

    ratio = 1.0
    if max(h, w) > max_side_len:
        ratio = float(max_side_len) / h if h > w else float(max_side_len) / w

    resize_h = _round_to_multiple_of_32(int(h * ratio))
    resize_w = _round_to_multiple_of_32(int(w * ratio))
    if resize_w <= 0 or resize_h <= 0:
        raise DetResizeError("缩放后的宽高非正值")
    return _resize_bilinear(img, resize_w, resize_h)


def _increase_min_side(img: np.ndarray, min_side_len: float) -> np.ndarray:
    """复刻 rapidocr increase_min_side：等比放大到最短边不越限，取整到 32 倍数。"""
    h, w = img.shape[:2]

    ratio = 1.0
    if min(h, w) < min_side_len:
        ratio = float(min_side_len) / h if h < w else float(min_side_len) / w

    resize_h = _round_to_multiple_of_32(int(h * ratio))
    resize_w = _round_to_multiple_of_32(int(w * ratio))
    if resize_w <= 0 or resize_h <= 0:
        raise DetResizeError("缩放后的宽高非正值")
    return _resize_bilinear(img, resize_w, resize_h)


def vertical_padding(
    img: np.ndarray, width_height_ratio: float = 8, min_height: float = 30
) -> np.ndarray:
    """h<=min_height 或 w/h>ratio 时上下对称补黑边至 max(w/ratio, min_height)*2 高。"""
    h, w = img.shape[:2]
    use_limit_ratio = False
    if width_height_ratio != -1:
        use_limit_ratio = w / h > width_height_ratio

    if h <= min_height or use_limit_ratio:
        new_h = max(int(w / width_height_ratio), int(min_height)) * 2
        padding_h = int(abs(new_h - h) / 2)
        return np.pad(img, ((padding_h, padding_h), (0, 0), (0, 0)))

    return img


def build_det_input(
    img: np.ndarray,
    *,
    limit_side_len: int = 736,
    limit_type: str = "min",
    mean=(0.5, 0.5, 0.5),
    std=(0.5, 0.5, 0.5),
):
    """构造 det 输入 [1,3,H,W] float32；无法缩放时返回 None（语义=无文本）。"""
    h, w = img.shape[:2]

    ratio = 1.0
    if limit_type == "min":
        if min(h, w) < limit_side_len:
            ratio = float(limit_side_len) / h if h < w else float(limit_side_len) / w
    else:
        if max(h, w) > limit_side_len:
            ratio = float(limit_side_len) / h if h > w else float(limit_side_len) / w

    resize_h = _round_to_multiple_of_32(int(h * ratio))
    resize_w = _round_to_multiple_of_32(int(w * ratio))
    if resize_w <= 0 or resize_h <= 0:
        return None

    resized = _resize_bilinear(img, resize_w, resize_h).astype("float32")
    normalized = (resized * (1.0 / 255.0) - np.array(mean, dtype=np.float32)) / np.array(
        std, dtype=np.float32
    )
    tensor = normalized.transpose((2, 0, 1))
    return np.expand_dims(tensor, axis=0).astype(np.float32)


def resize_norm_cls(img: np.ndarray, image_shape=(3, 48, 192)) -> np.ndarray:
    """方向分类输入：[3,48,192] 固定宽，等比缩放右零 pad，(x/255-0.5)/0.5。"""
    channels, img_height, img_width = image_shape
    h, w = img.shape[:2]
    ratio = w / float(h)
    if math.ceil(img_height * ratio) > img_width:
        resized_w = img_width
    else:
        resized_w = int(math.ceil(img_height * ratio))

    resized = _resize_bilinear(img, resized_w, img_height).astype("float32")
    resized = resized.transpose((2, 0, 1)) / 255.0
    resized -= 0.5
    resized /= 0.5

    padded = np.zeros((channels, img_height, img_width), dtype=np.float32)
    padded[:, :, :resized_w] = resized
    return padded


def resize_norm_rec(
    img: np.ndarray, max_wh_ratio: float, image_shape=(3, 48, 320)
) -> np.ndarray:
    """识别输入：批内动态宽 [3,48,W]，等比缩到高 48、超宽截断、右零 pad。"""
    channels, img_height, _ = image_shape
    img_width = int(img_height * max_wh_ratio)

    h, w = img.shape[:2]
    ratio = w / float(h)
    if math.ceil(img_height * ratio) > img_width:
        resized_w = img_width
    else:
        resized_w = int(math.ceil(img_height * ratio))

    resized = _resize_bilinear(img, resized_w, img_height).astype("float32")
    resized = resized.transpose((2, 0, 1)) / 255.0
    resized -= 0.5
    resized /= 0.5

    padded = np.zeros((channels, img_height, img_width), dtype=np.float32)
    padded[:, :, :resized_w] = resized
    return padded
