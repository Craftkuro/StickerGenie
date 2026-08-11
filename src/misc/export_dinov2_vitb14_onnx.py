# coding=utf-8
"""把 DINOv2 ViT-B/14 导出为应用使用的 ONNX 特征模型。

导出的 ONNX 与旧的 ViT-B/16 ONNX 保持相同的外部接口，现有提取管线无需改动：

- 输入：float32[batch, 3, 224, 224]（NCHW，RGB，ImageNet 均值/方差归一化）
- 输出：float32[batch, 768]（CLS token，已通过模型最后的 LayerNorm）

用法（第一次需要联网下载权重）：
    python src/misc/export_dinov2_vitb14_onnx.py

如果已经手动下载了官方权重 dinov2_vitb14_pretrain.pth：
    python src/misc/export_dinov2_vitb14_onnx.py --checkpoint dinov2_vitb14_pretrain.pth

给出 --checkpoint 后完全离线工作（用 transformers 的 Dinov2Model 加载
官方 state dict，不再需要 GitHub 或 HuggingFace）。

默认先从 torch.hub（GitHub）加载；GitHub 不可达时会自动回退到
HuggingFace（可通过 HF_ENDPOINT 指定镜像，例如
HF_ENDPOINT=https://hf-mirror.com python src/misc/export_dinov2_vitb14_onnx.py）。
也可以强制指定来源：
    python src/misc/export_dinov2_vitb14_onnx.py --source transformers

默认导出到 src/dinov2_vitb14_features.onnx，导出后会立刻用 ONNX Runtime
做形状和数值一致性自检。

导出环境依赖（仅生成模型时需要，应用运行时仍然只依赖 onnxruntime）：
    pip install torch torchvision onnx onnxscript onnxruntime
    # 使用 transformers 来源时还需要：
    pip install transformers
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[1]
DEFAULT_OUTPUT = PROJECT_ROOT / "dinov2_vitb14_features.onnx"
DEFAULT_ARCH = "dinov2_vitb14"
DEFAULT_OPSET = 18
INPUT_SIZE = 224
# 与 image_features_extractor.models.FEATURE_VECTOR_SIZE 保持一致
FEATURE_DIM = 768

# 与 facebookresearch/dinov2 的 dinov2_vitb14（ViT-B/14）一致
DINOV2_BASE_CONFIG = dict(
    hidden_size=768,
    num_hidden_layers=12,
    num_attention_heads=12,
    intermediate_size=3072,
    patch_size=14,
    num_channels=3,
    layer_norm_eps=1e-6,
    # 官方预训练权重在 518x518 下训练，pos_embed 为 (1, 1370, 768)；
    # 推理输入仍用 224x224，forward 时会自动插值位置编码
    image_size=518,
    use_mask_token=True,
)


def load_backbone_torchhub(arch: str, checkpoint: Path | None):
    """从 torch.hub 加载 DINOv2 骨干网络。"""
    if checkpoint is not None:
        return load_backbone_from_checkpoint(checkpoint)

    import torch.hub as hub
    return hub.load("facebookresearch/dinov2", arch, verbose=False)


def _convert_official_checkpoint(state_dict):
    """把官方 dinov2_vitb14_pretrain.pth 映射为 transformers.Dinov2Model 键名。"""
    import torch

    from collections import OrderedDict

    used_sources = set()

    def pick(key):
        used_sources.add(key)
        return state_dict[key]

    def pick_or(key, default):
        if key in state_dict:
            used_sources.add(key)
            return state_dict[key]
        return default

    out = OrderedDict()
    out["embeddings.cls_token"] = pick("cls_token")
    out["embeddings.position_embeddings"] = pick("pos_embed")
    out["embeddings.patch_embeddings.projection.weight"] = pick(
        "patch_embed.proj.weight"
    )
    out["embeddings.patch_embeddings.projection.bias"] = pick(
        "patch_embed.proj.bias"
    )
    out["embeddings.mask_token"] = pick("mask_token")
    out["layernorm.weight"] = pick("norm.weight")
    out["layernorm.bias"] = pick("norm.bias")

    for block_index in range(DINOV2_BASE_CONFIG["num_hidden_layers"]):
        src = f"blocks.{block_index}"
        dst = f"encoder.layer.{block_index}"
        out[f"{dst}.norm1.weight"] = pick(f"{src}.norm1.weight")
        out[f"{dst}.norm1.bias"] = pick(f"{src}.norm1.bias")

        qkv_weight = pick(f"{src}.attn.qkv.weight")
        qkv_bias = pick(f"{src}.attn.qkv.bias")
        dim = qkv_weight.shape[0] // 3
        for name, part in (
            ("query", slice(0, dim)),
            ("key", slice(dim, 2 * dim)),
            ("value", slice(2 * dim, 3 * dim)),
        ):
            out[f"{dst}.attention.attention.{name}.weight"] = qkv_weight[part]
            out[f"{dst}.attention.attention.{name}.bias"] = qkv_bias[part]

        out[f"{dst}.attention.output.dense.weight"] = pick(
            f"{src}.attn.proj.weight"
        )
        out[f"{dst}.attention.output.dense.bias"] = pick(
            f"{src}.attn.proj.bias"
        )
        # 官方 ViT-B/14 权重的 LayerScale 参数名为 ls1.gamma / ls2.gamma；
        # 某些变体可能没有该参数，transformers 默认 layerscale_value=1.0
        # 即恒等变换，缺少时补 1.0 保持一致。
        out[f"{dst}.layer_scale1.lambda1"] = pick_or(
            f"{src}.ls1.gamma",
            torch.ones(state_dict[f"{src}.norm1.weight"].shape),
        )
        out[f"{dst}.norm2.weight"] = pick(f"{src}.norm2.weight")
        out[f"{dst}.norm2.bias"] = pick(f"{src}.norm2.bias")
        out[f"{dst}.mlp.fc1.weight"] = pick(f"{src}.mlp.fc1.weight")
        out[f"{dst}.mlp.fc1.bias"] = pick(f"{src}.mlp.fc1.bias")
        out[f"{dst}.layer_scale2.lambda1"] = pick_or(
            f"{src}.ls2.gamma",
            torch.ones(state_dict[f"{src}.norm1.weight"].shape),
        )
        out[f"{dst}.mlp.fc2.weight"] = pick(f"{src}.mlp.fc2.weight")
        out[f"{dst}.mlp.fc2.bias"] = pick(f"{src}.mlp.fc2.bias")

    unused = set(state_dict) - used_sources
    if unused:
        raise RuntimeError(
            "checkpoint contains unrecognized keys: "
            + ", ".join(sorted(unused)[:5])
        )
    return out


def load_backbone_from_checkpoint(checkpoint: Path):
    """离线加载官方 dinov2_vitb14_pretrain.pth（不访问 GitHub/HuggingFace）。"""
    import torch
    from transformers import Dinov2Config, Dinov2Model

    config = Dinov2Config(**DINOV2_BASE_CONFIG)
    model = Dinov2Model(config)
    state_dict = torch.load(checkpoint, map_location="cpu")
    model.load_state_dict(_convert_official_checkpoint(state_dict))
    return model.eval()


def load_backbone_transformers(hf_repo: str):
    """从 HuggingFace 加载 DINOv2（facebook/dinov2-base 即 ViT-B/14）。"""
    from transformers import Dinov2Model

    return Dinov2Model.from_pretrained(hf_repo).eval()


def _make_cls_token_extractor(backbone):
    """包装 DINOv2，使其输出 [batch, 768] 的 CLS token 特征。"""
    import torch

    class ClsTokenExtractor(torch.nn.Module):
        def __init__(self) -> None:
            super().__init__()
            self.backbone = backbone

        def forward(self, images):
            if hasattr(self.backbone, "forward_features"):
                features = self.backbone.forward_features(images)
            else:
                # transformers 的 Dinov2Model 使用 forward()
                features = self.backbone(images)
            if isinstance(features, dict) and "x_norm" in features:
                x_norm = features["x_norm"]
            elif hasattr(features, "last_hidden_state"):
                # transformers 的 Dinov2ModelOutput（ModelOutput 也实现了
                # __getitem__，所以必须先于普通 dict 分支判断）
                x_norm = features.last_hidden_state
            elif isinstance(features, dict) and "last_hidden_state" in features:
                x_norm = features["last_hidden_state"]
            elif isinstance(features, (tuple, list)):
                # 旧版 DINOv2 返回 (x_norm, x_norm_patchtokens)
                x_norm = features[0]
            else:
                raise TypeError(
                    "unexpected DINOv2 forward_features output: "
                    f"{type(features).__name__}"
                )
            return x_norm[:, 0]

    return ClsTokenExtractor().eval()


def export_onnx(backbone, output: Path, opset: int):
    """导出 ONNX 并返回 PyTorch 的参考输出，用于导出后自检。"""
    import numpy as np
    import torch

    # 清掉旧产物，避免上次导出的外部权重文件残留
    output.unlink(missing_ok=True)
    Path(str(output) + ".data").unlink(missing_ok=True)

    model = _make_cls_token_extractor(backbone)
    dummy = torch.zeros(1, 3, INPUT_SIZE, INPUT_SIZE, dtype=torch.float32)
    with torch.no_grad():
        expected = model(dummy).numpy()
    if expected.shape != (1, FEATURE_DIM):
        raise RuntimeError(
            f"unexpected CLS output shape {expected.shape}; "
            f"expected (1, {FEATURE_DIM})"
        )

    output.parent.mkdir(parents=True, exist_ok=True)
    torch.onnx.export(
        model,
        dummy,
        str(output),
        input_names=["images"],
        output_names=["features"],
        dynamic_axes={
            "images": {0: "batch"},
            "features": {0: "batch"},
        },
        opset_version=opset,
        do_constant_folding=True,
        # 单文件 ONNX：权重内嵌，方便打包和拷贝
        external_data=False,
    )
    return expected


def verify_onnx(output: Path, expected):
    """用 ONNX Runtime 检查输出形状，并和 PyTorch 输出对比数值。"""
    import numpy as np
    import onnxruntime as ort

    session = ort.InferenceSession(
        str(output),
        providers=["CPUExecutionProvider"],
    )
    inputs = session.get_inputs()
    outputs = session.get_outputs()
    if len(inputs) != 1 or len(outputs) != 1:
        raise RuntimeError("unexpected ONNX input/output count")

    input_name = inputs[0].name
    output_name = outputs[0].name
    batch = np.zeros((2, 3, INPUT_SIZE, INPUT_SIZE), dtype=np.float32)
    actual = session.run([output_name], {input_name: batch})[0]
    if actual.shape != (2, FEATURE_DIM):
        raise RuntimeError(
            f"unexpected ONNX batch=2 output shape {actual.shape}; "
            f"expected (2, {FEATURE_DIM})"
        )
    np.testing.assert_allclose(
        actual[0],
        expected[0],
        rtol=1e-3,
        atol=1e-4,
    )


def main(argv=None) -> None:
    # Windows 控制台默认 GBK 时，torch.onnx 打印的 emoji 会让导出崩溃
    for stream in (sys.stdout, sys.stderr):
        try:
            stream.reconfigure(encoding="utf-8", errors="backslashreplace")
        except (AttributeError, ValueError):
            pass

    parser = argparse.ArgumentParser(
        description="Export DINOv2 ViT-B/14 CLS-token features to ONNX.",
    )
    parser.add_argument(
        "--arch",
        default=DEFAULT_ARCH,
        help=f"DINOv2 torch.hub 架构名（默认 {DEFAULT_ARCH}）",
    )
    parser.add_argument(
        "--source",
        choices=("auto", "torchhub", "transformers"),
        default="auto",
        help="权重来源；auto 先尝试 torch.hub，失败后回退 transformers",
    )
    parser.add_argument(
        "--hf-repo",
        default="facebook/dinov2-base",
        help="HuggingFace 模型仓库（默认 facebook/dinov2-base）",
    )
    parser.add_argument(
        "--checkpoint",
        type=Path,
        default=None,
        help="本地 DINOv2 官方权重（.pth state dict）；缺省时从 torch.hub 下载",
    )
    parser.add_argument(
        "--output",
        type=Path,
        default=DEFAULT_OUTPUT,
        help=f"输出 ONNX 路径（默认 {DEFAULT_OUTPUT}）",
    )
    parser.add_argument(
        "--opset",
        type=int,
        default=DEFAULT_OPSET,
        help=f"ONNX opset（默认 {DEFAULT_OPSET}）",
    )
    args = parser.parse_args(argv)

    try:
        import torch  # noqa: F401
    except ImportError as exc:
        raise SystemExit(
            "需要安装 torch 才能导出模型："
            "pip install torch torchvision onnx onnxruntime"
        ) from exc

    print(f"加载 DINOv2（{args.arch}）...")
    if args.source == "transformers":
        backbone = load_backbone_transformers(args.hf_repo)
    elif args.source == "torchhub":
        backbone = load_backbone_torchhub(args.arch, args.checkpoint)
    else:
        if args.checkpoint is not None:
            # 显式给了本地权重时不再回退，避免掩盖文件/映射错误
            backbone = load_backbone_torchhub(args.arch, args.checkpoint)
        else:
            try:
                backbone = load_backbone_torchhub(args.arch, args.checkpoint)
            except Exception as exc:
                print(f"torch.hub 加载失败（{exc}），回退到 HuggingFace ...")
                backbone = load_backbone_transformers(args.hf_repo)
    print(f"导出 ONNX 到 {args.output} ...")
    expected = export_onnx(backbone, args.output, args.opset)
    print("用 ONNX Runtime 自检...")
    verify_onnx(args.output, expected)
    print(f"完成：{args.output}（{args.output.stat().st_size / 1024 / 1024:.1f} MB）")


if __name__ == "__main__":
    main()
