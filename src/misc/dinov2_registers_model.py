# coding=utf-8
"""Self-contained DINOv2 ViT backbone with optional register tokens.

The implementation mirrors the inference path of facebookresearch/dinov2
``vision_transformer.py`` so that official ``*_reg4_pretrain.pth`` checkpoints
can be loaded offline and exported to ONNX without a xformers dependency.
"""

from __future__ import annotations

import math

import torch
from torch import nn
from torch.nn import functional as F


class _Mlp(nn.Module):
    def __init__(
        self,
        in_features: int,
        hidden_features: int,
        out_features: int,
    ) -> None:
        super().__init__()
        self.fc1 = nn.Linear(in_features, hidden_features, bias=True)
        self.act = nn.GELU()
        self.fc2 = nn.Linear(hidden_features, out_features, bias=True)

    def forward(self, x: torch.Tensor) -> torch.Tensor:
        x = self.fc1(x)
        x = self.act(x)
        return self.fc2(x)


class _PatchEmbed(nn.Module):
    def __init__(self, patch_size: int, embed_dim: int) -> None:
        super().__init__()
        self.proj = nn.Conv2d(
            3,
            embed_dim,
            kernel_size=patch_size,
            stride=patch_size,
        )

    def forward(self, x: torch.Tensor) -> torch.Tensor:
        return self.proj(x).flatten(2).transpose(1, 2)


class _Attention(nn.Module):
    def __init__(self, dim: int, num_heads: int) -> None:
        super().__init__()
        self.num_heads = num_heads
        self.head_dim = dim // num_heads
        self.qkv = nn.Linear(dim, dim * 3, bias=True)
        self.proj = nn.Linear(dim, dim, bias=True)

    def forward(self, x: torch.Tensor) -> torch.Tensor:
        batch, seq_len, dim = x.shape
        qkv = self.qkv(x).reshape(
            batch, seq_len, 3, self.num_heads, self.head_dim
        )
        q, k, v = torch.unbind(qkv, dim=2)
        q = q.transpose(1, 2)
        k = k.transpose(1, 2)
        v = v.transpose(1, 2)
        x = F.scaled_dot_product_attention(q, k, v)
        x = x.transpose(1, 2).contiguous().reshape(batch, seq_len, dim)
        return self.proj(x)


class _Block(nn.Module):
    def __init__(
        self,
        dim: int,
        num_heads: int,
        mlp_ratio: float,
        init_values: float,
    ) -> None:
        super().__init__()
        self.norm1 = nn.LayerNorm(dim, eps=1e-6)
        self.attn = _Attention(dim, num_heads)
        self.ls1 = _LayerScale(dim, init_values)
        self.norm2 = nn.LayerNorm(dim, eps=1e-6)
        self.mlp = _Mlp(dim, int(dim * mlp_ratio), dim)
        self.ls2 = _LayerScale(dim, init_values)

    def forward(self, x: torch.Tensor) -> torch.Tensor:
        x = x + self.ls1(self.attn(self.norm1(x)))
        x = x + self.ls2(self.mlp(self.norm2(x)))
        return x


class _LayerScale(nn.Module):
    def __init__(self, dim: int, init_values: float) -> None:
        super().__init__()
        self.gamma = nn.Parameter(torch.full((dim,), init_values))

    def forward(self, x: torch.Tensor) -> torch.Tensor:
        return x * self.gamma


class DinoV2RegistersBackbone(nn.Module):
    """DINOv2 ViT-B/14 backbone with the same keys as official checkpoints."""

    def __init__(
        self,
        *,
        img_size: int = 518,
        patch_size: int = 14,
        embed_dim: int = 768,
        depth: int = 12,
        num_heads: int = 12,
        mlp_ratio: float = 4.0,
        num_register_tokens: int = 0,
        init_values: float = 1e-5,
        interpolate_antialias: bool = False,
        interpolate_offset: float = 0.1,
    ) -> None:
        super().__init__()
        self.patch_size = patch_size
        self.num_register_tokens = num_register_tokens
        self.interpolate_antialias = interpolate_antialias
        self.interpolate_offset = interpolate_offset

        num_patches = (img_size // patch_size) ** 2
        self.patch_embed = _PatchEmbed(patch_size, embed_dim)
        self.cls_token = nn.Parameter(torch.zeros(1, 1, embed_dim))
        self.pos_embed = nn.Parameter(torch.zeros(1, num_patches + 1, embed_dim))
        self.register_tokens = (
            nn.Parameter(torch.zeros(1, num_register_tokens, embed_dim))
            if num_register_tokens
            else None
        )
        self.blocks = nn.ModuleList(
            [
                _Block(embed_dim, num_heads, mlp_ratio, init_values)
                for _ in range(depth)
            ]
        )
        self.norm = nn.LayerNorm(embed_dim, eps=1e-6)
        self.mask_token = nn.Parameter(torch.zeros(1, embed_dim))

    def interpolate_pos_encoding(
        self,
        x: torch.Tensor,
        width: int,
        height: int,
    ) -> torch.Tensor:
        previous_dtype = x.dtype
        npatch = x.shape[1] - 1
        num_pos = self.pos_embed.shape[1] - 1
        if npatch == num_pos and width == height:
            return self.pos_embed

        pos_embed = self.pos_embed.float()
        class_pos_embed = pos_embed[:, 0]
        patch_pos_embed = pos_embed[:, 1:]
        grid = int(math.sqrt(num_pos))
        w0 = width // self.patch_size
        h0 = height // self.patch_size
        kwargs: dict[str, object]
        if self.interpolate_offset:
            kwargs = {
                "scale_factor": (
                    (w0 + self.interpolate_offset) / grid,
                    (h0 + self.interpolate_offset) / grid,
                )
            }
        else:
            kwargs = {"size": (w0, h0)}

        patch_pos_embed = F.interpolate(
            patch_pos_embed.reshape(1, grid, grid, -1).permute(0, 3, 1, 2),
            mode="bicubic",
            antialias=self.interpolate_antialias,
            **kwargs,
        )
        patch_pos_embed = (
            patch_pos_embed.permute(0, 2, 3, 1).reshape(1, -1, pos_embed.shape[-1])
        )
        return torch.cat(
            (class_pos_embed.unsqueeze(0), patch_pos_embed),
            dim=1,
        ).to(previous_dtype)

    def prepare_tokens(self, x: torch.Tensor) -> torch.Tensor:
        batch, _, width, height = x.shape
        x = self.patch_embed(x)
        x = torch.cat((self.cls_token.expand(batch, -1, -1), x), dim=1)
        x = x + self.interpolate_pos_encoding(x, width, height)
        if self.register_tokens is not None:
            x = torch.cat(
                (
                    x[:, :1],
                    self.register_tokens.expand(batch, -1, -1),
                    x[:, 1:],
                ),
                dim=1,
            )
        return x

    def forward_features(self, x: torch.Tensor) -> dict[str, torch.Tensor]:
        x = self.prepare_tokens(x)
        for block in self.blocks:
            x = block(x)
        x_norm = self.norm(x)
        return {
            "x_norm_clstoken": x_norm[:, 0],
            "x_norm_regtokens": x_norm[
                :, 1 : self.num_register_tokens + 1
            ],
            "x_norm_patchtokens": x_norm[:, self.num_register_tokens + 1 :],
            "x_prenorm": x,
        }


def build_backbone_from_checkpoint(
    checkpoint: dict,
) -> DinoV2RegistersBackbone:
    """Build the backbone matching an official DINOv2 checkpoint."""

    if isinstance(checkpoint, dict) and "state_dict" in checkpoint:
        checkpoint = checkpoint["state_dict"]

    if not isinstance(checkpoint, dict):
        raise TypeError("checkpoint must be a PyTorch state dict")

    register_tokens = checkpoint.get("register_tokens")
    if register_tokens is None:
        num_register_tokens = 0
    elif (
        isinstance(register_tokens, torch.Tensor)
        and register_tokens.ndim == 3
    ):
        num_register_tokens = register_tokens.shape[1]
    else:
        raise ValueError("unexpected register_tokens shape in checkpoint")

    model = DinoV2RegistersBackbone(
        num_register_tokens=num_register_tokens,
        interpolate_antialias=num_register_tokens > 0,
        interpolate_offset=0.0 if num_register_tokens > 0 else 0.1,
    )
    missing, unexpected = model.load_state_dict(checkpoint, strict=False)
    if missing or unexpected:
        raise RuntimeError(
            "checkpoint does not match the DINOv2 ViT-B/14 backbone: "
            f"missing={missing[:5]}, unexpected={unexpected[:5]}"
        )
    return model.eval()
