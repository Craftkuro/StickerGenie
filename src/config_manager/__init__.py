"""
配置管理器模块

提供基于 TOML 格式的 key-value 配置文件管理功能。

支持的配置类型:
- STRING: 字符串
- INT: 整数
- BOOL: 布尔值
- LIST_STR: 字符串列表
- LIST_INT: 整数列表
- LIST_TABLE: 表数组列表（TOML 中写为 [[key]] 表数组）

示例:
    >>> from config_manager import ConfigManager, ConfigField, ConfigType
    >>> schema = [
    ...     ConfigField("app_name", ConfigType.STRING, "MyApp", "应用名称"),
    ...     ConfigField("debug", ConfigType.BOOL, False, "调试模式"),
    ... ]
    >>> config = ConfigManager("config.toml", schema, "1.0.0")
    >>> config.get("app_name")
    'MyApp'
"""

from .schema import ConfigType, ConfigField, ConfigSchema, FieldUI, WidgetKind
from .config_manager import ConfigManager
from .exceptions import (
    ConfigManagerError,
    ConfigValidationError,
    ConfigMigrationError,
    ConfigNotFoundError,
    ConfigTypeError,
)

__version__ = "1.0.0"

__all__ = [
    "ConfigType",
    "ConfigField", 
    "ConfigSchema",
    "FieldUI",
    "WidgetKind",
    "ConfigManager",
    "ConfigManagerError",
    "ConfigValidationError",
    "ConfigMigrationError",
    "ConfigNotFoundError",
    "ConfigTypeError",
]
