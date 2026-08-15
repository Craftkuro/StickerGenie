# coding=utf-8
"""Application settings schema and factory.

This module is kept separate from the UI so that services can read settings
without importing any Qt or dialog code at startup.
"""

from __future__ import annotations

from pathlib import Path

import apppath
from config_manager import ConfigField, ConfigManager, ConfigType


SETTINGS_VERSION = "1.4.0"

SETTINGS_SCHEMA = [
    ConfigField(
        "library_base_path",
        ConfigType.STRING,
        "StickerGenie Library/Default Library",
        "图库路径；可以是相对数据目录的路径，也可以是绝对路径",
    ),
    ConfigField(
        "recent_search_limit",
        ConfigType.INT,
        3,
        "显示的最近搜索候选数量",
    ),
    ConfigField(
        "tag_suggestion_limit",
        ConfigType.INT,
        10,
        "显示的标签搜索候选数量",
    ),
    ConfigField(
        "recent_searches",
        ConfigType.LIST_STR,
        [],
        "最近搜索，最新的项目在前",
    ),
    ConfigField(
        "similar_image_target_drop_ratio",
        ConfigType.STRING,
        "0.5",
        "相似图片：累计下降比例阈值（0-1之间）",
    ),
    ConfigField(
        "similar_image_min_keep",
        ConfigType.INT,
        5,
        "相似图片：最少保留结果数",
    ),
    ConfigField(
        "similar_image_min_similarity",
        ConfigType.STRING,
        "0.50",
        "相似图片：最低相似度阈值（0-1之间）",
    ),
    ConfigField(
        "similar_image_max_results",
        ConfigType.INT,
        100,
        "相似图片：最多返回结果数",
    ),
    ConfigField(
        "color_presets",
        ConfigType.LIST_TABLE,
        [],
        "颜色预设（名称 + RGB），存储于 [[config.color_presets]]",
    ),
]


def create_settings_manager(
    config_path: str | Path | None = None,
) -> ConfigManager:
    """Create the application settings manager for the configured data path."""
    if config_path is None:
        config_path = apppath.main_config_file_path
    if config_path is None:
        raise RuntimeError("应用程序数据路径尚未初始化")

    return ConfigManager(config_path, SETTINGS_SCHEMA, SETTINGS_VERSION)
