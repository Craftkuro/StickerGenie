# coding=utf-8
"""Export a local SigLIP vision encoder to the app's ONNX feature format.

The exported model keeps the same interface as the DINOv2 feature model:
input float32[batch, 3, 224, 224] and output float32[batch, 768].  The
output is SigLIP's multihead-attention pooled vision embedding.  The text
tower is not included and the vector is not L2 normalized; the vector store
uses cosine distance, so normalization does not affect ranking.
"""

from __future__ import annotations

import argparse
from pathlib import Path

import numpy as np
import onnxruntime as ort
import torch
from transformers import AutoConfig, SiglipVisionModel


DEFAULT_OUTPUT = (
    Path(__file__).resolve().parents[1] / "siglip_base_patch16_224_features.onnx"
)


class _SiglipVisionEncoder(torch.nn.Module):
    """Thin wrapper that returns only SigLIP's pooled vision embedding."""

    def __init__(self, vision_model: SiglipVisionModel) -> None:
        super().__init__()
        self.vision_model = vision_model

    def forward(self, pixel_values: torch.Tensor) -> torch.Tensor:
        return self.vision_model(pixel_values).pooler_output


def _validate_onnx(
    output: Path,
    encoder: torch.nn.Module,
    sample: torch.Tensor,
) -> None:
    session = ort.InferenceSession(
        str(output),
        providers=["CPUExecutionProvider"],
    )
    with torch.no_grad():
        expected = encoder(sample).numpy()
    actual = session.run(None, {"pixel_values": sample.numpy()})[0]

    if actual.shape != (sample.shape[0], expected.shape[1]):
        raise RuntimeError(
            f"unexpected ONNX output shape {actual.shape}; "
            f"expected {(sample.shape[0], expected.shape[1])}"
        )
    np.testing.assert_allclose(actual, expected, rtol=1e-4, atol=1e-4)

    single_sample = sample[:1]
    with torch.no_grad():
        single_expected = encoder(single_sample).numpy()
    single_actual = session.run(
        None,
        {"pixel_values": single_sample.numpy()},
    )[0]
    np.testing.assert_allclose(
        single_actual,
        single_expected,
        rtol=1e-4,
        atol=1e-4,
    )


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Export SigLIP base vision encoder to ONNX."
    )
    parser.add_argument(
        "--model-dir",
        type=Path,
        required=True,
        help="Local HuggingFace model directory for siglip-base-patch16-224.",
    )
    parser.add_argument(
        "--output",
        type=Path,
        default=DEFAULT_OUTPUT,
        help="Output ONNX path.",
    )
    args = parser.parse_args()

    config = AutoConfig.from_pretrained(args.model_dir, local_files_only=True)
    vision_model = SiglipVisionModel.from_pretrained(
        args.model_dir,
        config=config.vision_config,
        local_files_only=True,
        torch_dtype=torch.float32,
    )
    vision_model.eval()
    encoder = _SiglipVisionEncoder(vision_model)
    encoder.eval()

    sample = torch.randn(2, 3, 224, 224, dtype=torch.float32)
    with torch.no_grad():
        torch.onnx.export(
            encoder,
            (sample,),
            str(args.output),
            input_names=["pixel_values"],
            output_names=["image_embeds"],
            dynamic_shapes={
                "pixel_values": {0: torch.export.Dim("batch")},
            },
            external_data=False,
            opset_version=18,
            do_constant_folding=True,
        )

    _validate_onnx(args.output, encoder, sample)
    print(f"exported and validated: {args.output}")


if __name__ == "__main__":
    main()
