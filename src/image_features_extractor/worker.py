"""Spawn-safe worker entry point and image preprocessing implementation."""

from __future__ import annotations

import logging
import traceback
from collections.abc import Mapping, Sequence
from multiprocessing.connection import Connection
from pathlib import Path
from typing import Any

import numpy as np
from PIL import Image, ImageOps

from .models import (
    FEATURE_VECTOR_SIZE,
    ImageFeatureResult,
    ProviderSpec,
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

_RESIZE_SIZE = 256
_CROP_SIZE = 224
_NORMALIZE_MEAN = np.asarray((0.485, 0.456, 0.406), dtype=np.float32)[:, None, None]
_NORMALIZE_STD = np.asarray((0.229, 0.224, 0.225), dtype=np.float32)[:, None, None]


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


def select_execution_providers(
    available_providers: Sequence[str],
    requested_providers: Sequence[ProviderSpec] | None = None,
) -> list[ProviderSpec]:
    """Select the default CUDA/CPU provider chain or validate an explicit chain."""

    available = set(available_providers)
    if requested_providers is None:
        selected: list[ProviderSpec] = []
        if "CUDAExecutionProvider" in available:
            selected.append("CUDAExecutionProvider")
        if "CPUExecutionProvider" in available:
            selected.append("CPUExecutionProvider")
        if not selected:
            raise RuntimeError(
                "neither CUDAExecutionProvider nor CPUExecutionProvider is available"
            )
        return selected

    if not requested_providers:
        raise ValueError("providers cannot be empty")

    selected = []
    for provider in requested_providers:
        if isinstance(provider, str):
            name = provider
            normalized: ProviderSpec = provider
        elif (
            isinstance(provider, tuple)
            and len(provider) == 2
            and isinstance(provider[0], str)
            and isinstance(provider[1], Mapping)
        ):
            name = provider[0]
            normalized = (name, dict(provider[1]))
        else:
            raise ValueError(f"invalid provider configuration: {provider!r}")

        if name not in available:
            raise RuntimeError(f"requested ONNX provider is unavailable: {name}")
        selected.append(normalized)

    return selected


def _convert_to_rgb(image: Image.Image) -> Image.Image:
    has_transparency = image.mode in {"RGBA", "LA"} or "transparency" in image.info
    if not has_transparency:
        return image.convert("RGB")

    foreground = image.convert("RGBA")
    background = Image.new("RGBA", foreground.size, (255, 255, 255, 255))
    return Image.alpha_composite(background, foreground).convert("RGB")


def _resize_shorter_side(image: Image.Image, size: int) -> Image.Image:
    width, height = image.size
    if width <= 0 or height <= 0:
        raise ValueError(f"image has invalid dimensions: {image.size!r}")
    if width <= height:
        resized = (size, int(size * height / width))
    else:
        resized = (int(size * width / height), size)
    return image.resize(resized, resample=Image.Resampling.BILINEAR)


def _center_crop(image: Image.Image, size: int) -> Image.Image:
    width, height = image.size
    left = int(round((width - size) / 2.0))
    top = int(round((height - size) / 2.0))
    return image.crop((left, top, left + size, top + size))


def preprocess_image(image_path: str) -> np.ndarray:
    """Load and transform one image into a contiguous normalized NCHW tensor."""

    with Image.open(image_path) as source:
        transposed = ImageOps.exif_transpose(source)
        rgb_image = _convert_to_rgb(transposed)
        resized = _resize_shorter_side(rgb_image, _RESIZE_SIZE)
        cropped = _center_crop(resized, _CROP_SIZE)
        pixels = np.asarray(cropped, dtype=np.float32)

    chw = np.transpose(pixels, (2, 0, 1)) / np.float32(255.0)
    normalized = (chw - _NORMALIZE_MEAN) / _NORMALIZE_STD
    return np.ascontiguousarray(normalized, dtype=np.float32)


def _initialize_session(
    model_path: str,
    providers: Sequence[ProviderSpec] | None,
):
    model_file = Path(model_path)
    if not model_file.is_file():
        raise FileNotFoundError(f"ONNX model does not exist: {model_path}")

    import onnxruntime as ort

    selected_providers = select_execution_providers(
        ort.get_available_providers(), providers
    )
    session_options = ort.SessionOptions()
    session_options.intra_op_num_threads = 0
    session_options.inter_op_num_threads = 0
    session_options.graph_optimization_level = ort.GraphOptimizationLevel.ORT_ENABLE_ALL

    session = ort.InferenceSession(
        str(model_file),
        sess_options=session_options,
        providers=selected_providers,
    )
    inputs = session.get_inputs()
    if len(inputs) != 1:
        raise RuntimeError(f"expected exactly one model input, got {len(inputs)}")
    if not session.get_outputs():
        raise RuntimeError("the ONNX model has no outputs")

    input_name = inputs[0].name
    startup_info = WorkerStartupInfo(
        providers=tuple(session.get_providers()),
        input_name=input_name,
    )
    return session, input_name, startup_info


def _image_error_message(error: Exception) -> str:
    detail = str(error).strip()
    return f"{type(error).__name__}: {detail}" if detail else type(error).__name__


def process_image_batch(session, input_name: str, image_paths: Sequence[str]):
    """Preprocess and infer one ordered path batch inside the worker process."""

    results: list[ImageFeatureResult | None] = [None] * len(image_paths)
    tensors: list[np.ndarray] = []
    successful_indexes: list[int] = []

    for index, image_path in enumerate(image_paths):
        try:
            tensors.append(preprocess_image(image_path))
            successful_indexes.append(index)
        except Exception as error:
            results[index] = ImageFeatureResult.failed(
                image_path, _image_error_message(error)
            )

    if tensors:
        model_input = np.stack(tensors, axis=0)
        outputs = session.run(None, {input_name: model_input})
        if not outputs:
            raise RuntimeError("ONNX Runtime returned no outputs")

        features = np.asarray(outputs[0])
        expected_shape = (len(tensors), FEATURE_VECTOR_SIZE)
        if features.shape != expected_shape:
            raise RuntimeError(
                f"unexpected ONNX output shape {features.shape!r}; "
                f"expected {expected_shape!r}"
            )
        if features.dtype != np.float32:
            raise RuntimeError(
                f"unexpected ONNX output dtype {features.dtype}; expected float32"
            )

        for output_index, result_index in enumerate(successful_indexes):
            vector = np.array(
                features[output_index], dtype=np.float32, order="C", copy=True
            )
            results[result_index] = ImageFeatureResult.succeeded(
                image_paths[result_index], vector
            )

    if any(result is None for result in results):
        raise RuntimeError("worker failed to produce a result for every input path")
    return tuple(results)


def worker_process_entry(
    connection: Connection,
    model_path: str,
    providers: Sequence[ProviderSpec] | None,
) -> None:
    """Top-level spawn target for one extraction job."""

    try:
        try:
            session, input_name, startup_info = _initialize_session(
                model_path, providers
            )
        except BaseException as error:
            logger.error("image feature worker initialization failed: %s", error)
            _send_message(connection, INIT_ERROR, _image_error_message(error))
            return

        _send_message(connection, INIT_OK, startup_info)
        _send_message(connection, REQUEST_BATCH)

        while True:
            kind, payload = _receive_message(connection)
            if kind == PROCESS_BATCH:
                if (
                    not isinstance(payload, (tuple, list))
                    or not payload
                    or not all(isinstance(path, str) and path for path in payload)
                ):
                    raise RuntimeError("PROCESS_BATCH requires non-empty string paths")
                results = process_image_batch(session, input_name, payload)
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
        logger.info("image feature worker connection closed")
    except BaseException as error:
        logger.error(
            "image feature worker failed:\n%s", traceback.format_exc()
        )
        try:
            _send_message(connection, JOB_ERROR, _image_error_message(error))
        except (EOFError, BrokenPipeError, OSError):
            pass
    finally:
        connection.close()
