# coding=utf-8
"""Application settings schema and factory.

This module is kept separate from the UI so that services can read settings
without importing any Qt or dialog code at startup.

SETTINGS_SCHEMA 是设置界面的单一事实来源：带 `ui` 描述的字段会由设置
对话框自动装配到对应页面；无 `ui`（或 page=None）的字段只存在于配置
文件中。字段的出现顺序决定页面、分组与行的显示顺序。
"""

from __future__ import annotations

from pathlib import Path

import apppath
import commons.constants
from config_manager import ConfigField, ConfigManager, ConfigType, FieldUI, WidgetKind


SETTINGS_VERSION = "1.5.0"

PAGE_GENERAL = "general"
PAGE_SEARCH = "search"

PAGE_TITLES = {
    PAGE_GENERAL: "常规",
    PAGE_SEARCH: "搜索",
}

SETTINGS_SCHEMA = [
    ConfigField(
        "library_base_path",
        ConfigType.STRING,
        "StickerGenie Library/Default Library",
        "图库路径；可以是相对数据目录的路径，也可以是绝对路径",
    ),
    ConfigField(
        "thumbnail_memory_cache_size",
        ConfigType.INT,
        2000,
        "应用程序内部缓存的缩略图数量，默认2000，调小可节约内存但可能显著影响性能。重启后应用。",
        ui=FieldUI(
            kind=WidgetKind.SPIN_BOX,
            page=PAGE_GENERAL,
            label="缩略图内存缓存大小",
            group="缩略图缓存",
            suffix=" 张",
            minimum=100,
            maximum=100000,
            step=100,
        ),
    ),
    ConfigField(
        "recent_search_limit",
        ConfigType.INT,
        3,
        "搜索框中最多显示的最近搜索数量；设为 0 可关闭。",
        ui=FieldUI(
            kind=WidgetKind.SPIN_BOX,
            page=PAGE_SEARCH,
            label="最近搜索候选",
            group="搜索候选",
            suffix=" 项",
            maximum=100,
        ),
    ),
    ConfigField(
        "tag_suggestion_limit",
        ConfigType.INT,
        10,
        "标签搜索时最多显示的匹配标签数量；设为 0 可关闭。",
        ui=FieldUI(
            kind=WidgetKind.SPIN_BOX,
            page=PAGE_SEARCH,
            label="标签搜索候选",
            group="搜索候选",
            suffix=" 项",
            maximum=100,
        ),
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
        "相似图片：累计下降比例阈值（0-1之间）。保留累计相似度下降达到总下降的该比例之前的候选，值越小结果越保守。",
        ui=FieldUI(
            kind=WidgetKind.SPIN_BOX_2P,
            page=PAGE_SEARCH,
            label="累计下降比例",
            group="相似图片",
            minimum=0.01,
            maximum=0.99,
            step=0.05,
        ),
    ),
    ConfigField(
        "similar_image_min_keep",
        ConfigType.INT,
        5,
        "相似图片：最少保留结果数，即使曲线下降很快也至少保留这么多结果，避免只显示一张重复图。",
        ui=FieldUI(
            kind=WidgetKind.SPIN_BOX,
            page=PAGE_SEARCH,
            label="最少保留数",
            group="相似图片",
            maximum=50,
        ),
    ),
    ConfigField(
        "similar_image_min_similarity",
        ConfigType.STRING,
        "0.50",
        "相似图片：最低相似度阈值（0-1之间），低于该相似度的候选不会进入结果。",
        ui=FieldUI(
            kind=WidgetKind.SPIN_BOX_2P,
            page=PAGE_SEARCH,
            label="最低相似度",
            group="相似图片",
            minimum=0.0,
            maximum=1.0,
            step=0.05,
        ),
    ),
    ConfigField(
        "similar_image_max_results",
        ConfigType.INT,
        commons.constants.SIMILAR_IMAGE_MAX_RESULTS,
        "相似图片：最多返回结果数（硬上限）。",
        ui=FieldUI(
            kind=WidgetKind.SPIN_BOX,
            page=PAGE_SEARCH,
            label="最多返回数",
            group="相似图片",
            minimum=1,
            maximum=200,
        ),
    ),
    ConfigField(
        "similar_image_candidate_count",
        ConfigType.INT,
        commons.constants.SIMILAR_IMAGE_CANDIDATE_COUNT,
        "执行相似图片查找时，从向量数据库查询获得的图片总数",
        ui=FieldUI(
            kind=WidgetKind.SPIN_BOX,
            page=PAGE_SEARCH,
            label="候选总数",
            group="相似图片",
            suffix=" 张",
            minimum=1,
            maximum=10000,
            step=50,
        ),
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
