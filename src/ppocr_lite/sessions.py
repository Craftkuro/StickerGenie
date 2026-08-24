# -*- encoding: utf-8 -*-
"""ONNX 会话创建与 rec 字符表读取。"""

import logging
from pathlib import Path

import numpy as np
import onnxruntime as ort

from .params import CLS_MODEL_FILENAME, DET_MODEL_FILENAME, REC_MODEL_FILENAME

logger = logging.getLogger(__name__)

_CHARACTER_KEY = "character"


def resolve_models_dir(models_dir=None) -> Path:
    """定位模型目录：显式传入优先，其次 apppath.app_path（开发态=src/，打包后=_MEIPASS）。"""
    if models_dir is not None:
        return Path(models_dir)

    try:
        import apppath
    except ImportError:
        apppath = None
    if apppath is not None and getattr(apppath, "app_path", None) is not None:
        return Path(apppath.app_path)

    return Path(__file__).resolve().parent.parent


class ModelSessions:
    """三个模型的常驻会话；providers 交给 onnxruntime 默认选择（GPU 由其自动提供）。"""

    def __init__(self, models_dir=None):
        models_dir = resolve_models_dir(models_dir)
        self.det = self._create_session(models_dir / DET_MODEL_FILENAME)
        self.cls = self._create_session(models_dir / CLS_MODEL_FILENAME)
        self.rec = self._create_session(models_dir / REC_MODEL_FILENAME)
        self.characters = read_rec_character_list(self.rec)

    @staticmethod
    def _create_session(model_path: Path) -> ort.InferenceSession:
        if not model_path.is_file():
            raise FileNotFoundError(f"OCR 模型不存在：{model_path}")

        sess_opt = ort.SessionOptions()
        sess_opt.log_severity_level = 4
        sess_opt.graph_optimization_level = ort.GraphOptimizationLevel.ORT_ENABLE_ALL
        # enable_cpu_mem_arena 保持 ORT 默认开启；rapidocr 关它的做法不适用本项目。
        # intra/inter_op 线程数不显式设置，交由 ORT 自动决策；
        # 流水线 stage 已是单线程，避免超订。
        logger.info("加载 OCR 模型 %s", model_path.name)
        return ort.InferenceSession(str(model_path), sess_options=sess_opt)


def run_session(session: ort.InferenceSession, input_content: np.ndarray) -> np.ndarray:
    input_dict = {v.name: input_content for v in session.get_inputs()}
    output_names = [v.name for v in session.get_outputs()]
    try:
        return session.run(output_names, input_dict)[0]
    except Exception as exc:
        raise RuntimeError(f"onnxruntime 推理失败：{exc}") from exc


def read_rec_character_list(rec_session: ort.InferenceSession) -> list:
    """从 rec 模型元数据读字符表：尾部补空格、头部补 CTC blank（索引 0）。"""
    meta = rec_session.get_modelmeta().custom_metadata_map
    if _CHARACTER_KEY not in meta:
        raise KeyError(f"rec 模型元数据缺少 {_CHARACTER_KEY} 键")
    character_list = meta[_CHARACTER_KEY].splitlines()
    character_list.append(" ")
    character_list.insert(0, "blank")
    return character_list
