# -*- encoding: utf-8 -*-
"""同步门面 OcrEngine：串起读图→预处理→检测→裁剪→分类→识别→组装全部环节。"""

import logging
import os

import numpy as np

from . import det_postprocess as db
from . import geometry, image_io, preprocess, recognition
from .params import OcrParams
from .sessions import ModelSessions, run_session

logger = logging.getLogger(__name__)


class OcrEngine:
    """轻量 PP-OCR 同步引擎。实例非线程安全，由调用方保证串行使用。

    recognize 返回阅读顺序的 [(text, score)] 列表；已完成 strip 空行过滤与
    text_score(<0.5) 过滤，无文本时返回 []。异常正常抛出，不在引擎内吞掉。
    """

    def __init__(
        self,
        *,
        models_dir: str | os.PathLike[str] | None = None,
        log_level: int = logging.WARNING,
    ):
        logging.getLogger(__package__).setLevel(log_level)
        self.params = OcrParams()
        self.sessions = ModelSessions(models_dir)

    def recognize(self, image: str | os.PathLike[str]) -> list:
        """同步识别一张图，返回阅读顺序的 (text, score) 列表；无文本返回 []。"""
        params = self.params

        img = image_io.load_bgr(image)
        img = preprocess.limit_image_size(img, params.min_side_len, params.max_side_len)
        if params.use_vertical_padding:
            img = preprocess.vertical_padding(
                img, params.width_height_ratio, params.min_height
            )

        boxes = self._detect(img, params)
        if len(boxes) == 0:
            return []

        crops = [geometry.crop_text_region(img, box) for box in boxes]
        crops = recognition.rotate_crops(crops, self.sessions.cls, params)
        results = recognition.recognize_crops(
            self.sessions.rec, self.sessions.characters, crops, params
        )
        return [
            (text, score)
            for text, score in results
            if text.strip() and score >= params.text_score
        ]

    def _detect(self, img: np.ndarray, params: OcrParams) -> np.ndarray:
        """det 预处理 + 推理 + DB 后处理，返回 int32 [N,4,2] 检测框（可为空数组）。"""
        det_input = preprocess.build_det_input(
            img,
            limit_side_len=params.det_limit_side_len,
            limit_type=params.det_limit_type,
            mean=params.det_mean,
            std=params.det_std,
        )
        if det_input is None:
            return np.empty((0, 4, 2), dtype=np.int32)

        preds = run_session(self.sessions.det, det_input)
        prob_map = preds[0, 0, :, :]
        boxes, _scores = db.boxes_from_prob_map(
            prob_map,
            (img.shape[0], img.shape[1]),
            thresh=params.det_thresh,
            box_thresh=params.det_box_thresh,
            max_candidates=params.det_max_candidates,
            unclip_ratio=params.det_unclip_ratio,
            use_dilation=params.det_use_dilation,
        )
        if len(boxes) == 0:
            return np.empty((0, 4, 2), dtype=np.int32)
        return boxes
