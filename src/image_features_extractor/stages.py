"""Image preprocessing and ONNX inference stage functions.

These functions run inside the ``batch_job_runner`` worker process. The ONNX
session is initialized once by :func:`load_session` (the pipeline setup_func)
before any stage worker starts.
"""

from __future__ import annotations

import logging
from collections.abc import Mapping, Sequence
from pathlib import Path
from typing import Any

import numpy as np
from PIL import Image, ImageOps

from .model_specs import ImageFeatureModelSpec, get_model_spec


logger = logging.getLogger(__name__)

ProviderSpec = str | tuple[str, Mapping[str, Any]]

_session: Any = None
_input_name: str | None = None
_spec: ImageFeatureModelSpec | None = None


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


def _transform_image(
    image_path: str,
    spec: ImageFeatureModelSpec,
) -> np.ndarray:
    """Load and transform one image into a contiguous normalized NCHW tensor."""

    with Image.open(image_path) as source:
        transposed = ImageOps.exif_transpose(source)
        rgb_image = _convert_to_rgb(transposed)
        if spec.resize_mode == "resize":
            resized = rgb_image.resize(
                (spec.input_size, spec.input_size),
                resample=Image.Resampling.BICUBIC,
            )
            cropped = resized
        else:
            resized = _resize_shorter_side(rgb_image, spec.resize_size)
            cropped = _center_crop(resized, spec.input_size)
        pixels = np.asarray(cropped, dtype=np.float32)

    chw = np.transpose(pixels, (2, 0, 1)) / np.float32(255.0)
    mean = np.asarray(spec.normalize_mean, dtype=np.float32)[:, None, None]
    std = np.asarray(spec.normalize_std, dtype=np.float32)[:, None, None]
    normalized = (chw - mean) / std
    return np.ascontiguousarray(normalized, dtype=np.float32)


def preprocess_image(
    image_path: str,
    spec: ImageFeatureModelSpec | None = None,
):
    """Stage function: transform one image into ``(image_path, tensor)``."""

    if spec is None:
        spec = _get_spec()
    tensor = _transform_image(image_path, spec)
    return image_path, tensor


def _get_spec() -> ImageFeatureModelSpec:
    if _spec is None:
        raise RuntimeError("feature extraction session is not initialized")
    return _spec


def _get_session() -> Any:
    if _session is None:
        raise RuntimeError("feature extraction session is not initialized")
    return _session


def load_session(
    model_path: str,
    providers: Sequence[ProviderSpec] | None = None,
) -> dict[str, Any]:
    """Initialize the ONNX session once per worker process."""

    global _session, _input_name, _spec
    if _session is not None:
        return _startup_info_dict()

    model_file = Path(model_path)
    if not model_file.is_file():
        raise FileNotFoundError(f"ONNX model does not exist: {model_path}")
    _spec = get_model_spec(model_path)

    import onnxruntime as ort

    selected_providers = select_execution_providers(
        ort.get_available_providers(), providers
    )
    session_options = ort.SessionOptions()
    session_options.intra_op_num_threads = 0
    session_options.inter_op_num_threads = 0
    session_options.graph_optimization_level = ort.GraphOptimizationLevel.ORT_ENABLE_ALL

    _session = ort.InferenceSession(
        str(model_file),
        sess_options=session_options,
        providers=selected_providers,
    )
    inputs = _session.get_inputs()
    if len(inputs) != 1:
        raise RuntimeError(f"expected exactly one model input, got {len(inputs)}")
    if not _session.get_outputs():
        raise RuntimeError("the ONNX model has no outputs")
    if len(_session.get_outputs()) <= _spec.output_index:
        raise RuntimeError(
            f"model has {len(_session.get_outputs())} outputs but spec "
            f"requires output index {_spec.output_index}"
        )
    _input_name = inputs[0].name
    return _startup_info_dict()


def _startup_info_dict() -> dict[str, Any]:
    return {
        "providers": tuple(_session.get_providers()),
        "input_name": _input_name,
        "model_name": _spec.name,
        "feature_vector_size": _spec.feature_vector_size,
    }


def run_batch_inference(items):
    """Infer one batch of ``(image_path, tensor)`` items.

    Returns a list of ``(image_path, vector)`` tuples. Any exception raised
    here marks the whole batch failed (decision 5).
    """

    session = _get_session()
    if _input_name is None:
        raise RuntimeError("feature extraction session is not initialized")
    spec = _get_spec()

    image_paths = [item[0] for item in items]
    tensors = [item[1] for item in items]
    model_input = np.stack(tensors, axis=0)
    outputs = session.run(None, {_input_name: model_input})
    if len(outputs) <= spec.output_index:
        raise RuntimeError(
            f"ONNX Runtime returned {len(outputs)} outputs but spec "
            f"requires output index {spec.output_index}"
        )

    features = np.asarray(outputs[spec.output_index])
    expected_shape = (len(tensors), spec.feature_vector_size)
    if features.shape != expected_shape:
        raise RuntimeError(
            f"unexpected ONNX output shape {features.shape!r}; "
            f"expected {expected_shape!r}"
        )
    if features.dtype != np.float32:
        raise RuntimeError(
            f"unexpected ONNX output dtype {features.dtype}; expected float32"
        )

    results = []
    for index, image_path in enumerate(image_paths):
        vector = np.array(
            features[index], dtype=np.float32, order="C", copy=True
        )
        results.append((image_path, vector))
    return results
