"""Model-specific configuration for image feature extraction.

Everything that changes when the ONNX feature model changes lives here:
model filename, preprocessing, output shape, and which ONNX output is used.
Every supported model has a registered spec here. The app does not switch
models at runtime; changing the active model means updating
``DEFAULT_MODEL_SPEC`` and regenerating the feature vectors.
"""

from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path


@dataclass(frozen=True, slots=True)
class ImageFeatureModelSpec:
    """Declarative adapter for one image feature extraction model."""

    name: str
    model_filename: str
    feature_vector_size: int
    input_size: int
    resize_size: int
    normalize_mean: tuple[float, float, float]
    normalize_std: tuple[float, float, float]
    resize_mode: str = "shorter_side_crop"
    # "shorter_side_crop": DINOv2-style short-side resize then center crop;
    # "resize": SigLIP-style direct resize to (input_size, input_size).
    output_index: int = 0

    def __post_init__(self) -> None:
        if not isinstance(self.name, str) or not self.name:
            raise ValueError("name must be a non-empty string")
        if not isinstance(self.model_filename, str) or not self.model_filename:
            raise ValueError("model_filename must be a non-empty string")
        if (
            isinstance(self.feature_vector_size, bool)
            or not isinstance(self.feature_vector_size, int)
            or self.feature_vector_size <= 0
        ):
            raise ValueError("feature_vector_size must be a positive integer")
        if (
            isinstance(self.input_size, bool)
            or not isinstance(self.input_size, int)
            or self.input_size <= 0
        ):
            raise ValueError("input_size must be a positive integer")
        if (
            isinstance(self.resize_size, bool)
            or not isinstance(self.resize_size, int)
            or self.resize_size <= 0
        ):
            raise ValueError("resize_size must be a positive integer")
        if (
            isinstance(self.output_index, bool)
            or not isinstance(self.output_index, int)
            or self.output_index < 0
        ):
            raise ValueError("output_index must be a non-negative integer")
        if (
            len(self.normalize_mean) != 3
            or len(self.normalize_std) != 3
        ):
            raise ValueError(
                "normalize_mean and normalize_std must contain three values"
            )
        if any(value <= 0 for value in self.normalize_std):
            raise ValueError("normalize_std values must be positive")
        if self.resize_mode not in {"shorter_side_crop", "resize"}:
            raise ValueError(
                "resize_mode must be 'shorter_side_crop' or 'resize'"
            )


def _dinov2_vitb14_spec(
    name: str,
    model_filename: str,
) -> ImageFeatureModelSpec:
    return ImageFeatureModelSpec(
        name=name,
        model_filename=model_filename,
        feature_vector_size=768,
        input_size=224,
        resize_size=256,
        normalize_mean=(0.485, 0.456, 0.406),
        normalize_std=(0.229, 0.224, 0.225),
    )


def _siglip_base_patch16_224_spec() -> ImageFeatureModelSpec:
    return ImageFeatureModelSpec(
        name="siglip_base_patch16_224",
        model_filename="siglip_base_patch16_224_features.onnx",
        feature_vector_size=768,
        input_size=224,
        resize_size=224,
        normalize_mean=(0.5, 0.5, 0.5),
        normalize_std=(0.5, 0.5, 0.5),
        resize_mode="resize",
    )


DINOV2_VITB14_SPEC = _dinov2_vitb14_spec(
    "dinov2_vitb14",
    "dinov2_vitb14_features.onnx",
)
DINOV2_VITB14_REG4_SPEC = _dinov2_vitb14_spec(
    "dinov2_vitb14_reg4",
    "dinov2_vitb14_reg4_features.onnx",
)
SIGLIP_BASE_PATCH16_224_SPEC = _siglip_base_patch16_224_spec()

MODEL_SPECS = (
    DINOV2_VITB14_SPEC,
    DINOV2_VITB14_REG4_SPEC,
    SIGLIP_BASE_PATCH16_224_SPEC,
)
DEFAULT_MODEL_SPEC = SIGLIP_BASE_PATCH16_224_SPEC
DEFAULT_MODEL_FILENAME = DEFAULT_MODEL_SPEC.model_filename

_MODEL_SPECS_BY_FILENAME = {
    spec.model_filename: spec
    for spec in MODEL_SPECS
}
_MODEL_SPECS_BY_NAME = {
    spec.name: spec
    for spec in MODEL_SPECS
}


def get_model_spec(
    model_path: str | os.PathLike[str],
) -> ImageFeatureModelSpec:
    """Return the registered spec for an ONNX model path."""

    path = Path(model_path)
    spec = _MODEL_SPECS_BY_FILENAME.get(path.name)
    if spec is None:
        spec = _MODEL_SPECS_BY_NAME.get(path.stem)
    if spec is None:
        supported = ", ".join(
            spec.model_filename
            for spec in MODEL_SPECS
        )
        raise ValueError(
            f"unsupported image feature model {path.name!r}; "
            f"supported models: {supported}"
        )
    return spec
