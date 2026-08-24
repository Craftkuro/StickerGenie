# -*- encoding: utf-8 -*-
"""ppocr_lite 的全部默认参数（替代 rapidocr config.yaml）。

默认值与 rapidocr 3.9.2 默认 config.yaml 的生效值一一对应，
见 plans/lightweight_ocr_engine_design.md 第二节。
"""

from dataclasses import dataclass

DET_MODEL_FILENAME = "PP-OCRv6_det_small.onnx"
CLS_MODEL_FILENAME = "ch_ppocr_mobile_v2.0_cls_mobile.onnx"
REC_MODEL_FILENAME = "PP-OCRv6_rec_small.onnx"


@dataclass(frozen=True)
class OcrParams:
    """全流水线默认参数，实例不可变。"""

    # Global
    text_score: float = 0.5
    min_side_len: float = 30
    max_side_len: float = 2000
    use_vertical_padding: bool = True
    min_height: float = 30
    width_height_ratio: float = 8

    # Det（DBNet）
    det_limit_side_len: int = 736
    det_limit_type: str = "min"
    det_mean: tuple = (0.5, 0.5, 0.5)
    det_std: tuple = (0.5, 0.5, 0.5)
    det_thresh: float = 0.3
    det_box_thresh: float = 0.5
    det_max_candidates: int = 1000
    det_unclip_ratio: float = 1.6
    det_use_dilation: bool = True

    # Cls（方向分类）
    cls_image_shape: tuple = (3, 48, 192)
    cls_batch_num: int = 6
    cls_thresh: float = 0.9

    # Rec（文本识别）
    rec_image_shape: tuple = (3, 48, 320)
    rec_batch_num: int = 6
