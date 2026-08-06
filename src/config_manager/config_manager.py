"""
配置管理器核心实现

提供基于 TOML 格式的配置文件管理功能，支持：
- key-value 形式的配置读取和写入
- 多种数据类型支持（str, int, bool, list[str], list[int]）
- 配置文件注释保持
- 配置版本迁移
- 类型验证和默认值回退
"""

from __future__ import annotations
import os
import logging
from pathlib import Path
from typing import Any, Union, List, Optional, Dict, TYPE_CHECKING

if TYPE_CHECKING:
    from tomlkit import TOMLDocument

import tomlkit
from tomlkit import items
from tomlkit.toml_document import TOMLDocument

from .schema import ConfigField, ConfigSchema, ConfigType
from .exceptions import (
    ConfigManagerError,
    ConfigValidationError,
    ConfigMigrationError,
    ConfigNotFoundError,
    ConfigTypeError,
)

logger = logging.getLogger(__name__)

# 配置元数据键名
VERSION_KEY = "__version__"
SECTION_NAME = "config"


class ConfigManager:
    """
    TOML 配置文件管理器
    
    提供配置的读取、写入、验证和迁移功能。
    
    Attributes:
        config_path: 配置文件路径
        schema: 配置模式
        version: 当前配置版本
        
    Examples:
        >>> schema = [
        ...     ConfigField("app_name", ConfigType.STRING, "MyApp", "应用名称"),
        ...     ConfigField("debug", ConfigType.BOOL, False, "调试模式"),
        ...     ConfigField("workers", ConfigType.INT, 4, "工作线程数"),
        ...     ConfigField("extensions", ConfigType.LIST_STR, ["jpg"], "文件扩展名"),
        ...     ConfigField("sizes", ConfigType.LIST_INT, [100], "大小配置"),
        ... ]
        >>> config = ConfigManager("config.toml", schema, "1.0.0")
        >>> config.get("app_name")
        'MyApp'
        >>> config.set("workers", 8)
        >>> config.save()
    """
    
    def __init__(
        self,
        config_path: str | Path,
        schema: List[ConfigField],
        version: str,
        create_if_not_exists: bool = True,
    ):
        """
        初始化配置管理器
        
        Args:
            config_path: 配置文件路径
            schema: 配置模式定义
            version: 配置版本号
            create_if_not_exists: 如果配置文件不存在是否创建
        """
        self._config_path = Path(config_path)
        self._schema = ConfigSchema(schema)
        self._version = version
        self._create_if_not_exists = create_if_not_exists
        
        # 内部配置存储
        self._config: TOMLDocument | None = None
        self._config_values: Dict[str, Any] = {}
        
        # 加载配置
        self._load()
    
    @property
    def config_path(self) -> Path:
        """获取配置文件路径"""
        return self._config_path
    
    @property
    def version(self) -> str:
        """获取配置版本"""
        return self._version
    
    @property
    def schema(self) -> ConfigSchema:
        """获取配置模式"""
        return self._schema
    
    def _load(self) -> None:
        """加载配置文件"""
        if not self._config_path.exists():
            if self._create_if_not_exists:
                logger.info(f"配置文件不存在，将创建: {self._config_path}")
                self._create_default_config()
            else:
                raise ConfigNotFoundError(f"配置文件不存在: {self._config_path}")
        
        try:
            content = self._config_path.read_text(encoding="utf-8")
            self._config = tomlkit.loads(content)
        except Exception as e:
            logger.error(f"加载配置文件失败: {e}")
            raise ConfigManagerError(f"无法加载配置文件: {e}") from e
        
        # 应用迁移逻辑
        self._apply_migration()
        
        # 加载配置值到内存
        self._sync_config_values()
    
    def _create_default_config(self) -> None:
        """创建默认配置文件"""
        # 确保目录存在
        self._config_path.parent.mkdir(parents=True, exist_ok=True)
        
        # 创建带注释的默认配置
        doc = tomlkit.document()
        
        # 添加版本信息
        doc[VERSION_KEY] = self._version
        
        # 添加配置节
        config_section = tomlkit.table()
        for field in self._schema.fields:
            # 添加字段注释
            if field.comment:
                config_section.add(tomlkit.comment(field.comment))
            config_section[field.key] = field.default
        
        doc[SECTION_NAME] = config_section
        
        # 写入文件
        with open(self._config_path, "w", encoding="utf-8") as f:
            tomlkit.dump(doc, f)
        
        logger.info(f"已创建默认配置文件: {self._config_path}")
    
    def _apply_migration(self) -> None:
        """应用配置迁移"""
        if self._config is None:
            return
            
        stored_version = self._config.get(VERSION_KEY, "0.0.0")
        
        if stored_version == self._version:
            # 版本相同，无需迁移
            return
        
        logger.info(f"配置迁移: {stored_version} -> {self._version}")
        
        # 确保 config 节存在
        if SECTION_NAME not in self._config:
            self._config[SECTION_NAME] = tomlkit.table()
        
        config_section = self._config[SECTION_NAME]
        
        # 迁移策略：
        # 1. 新增的 key -> 使用默认值
        # 2. 已存在的 key -> 保留配置文件中的值
        # 3. 已移除的 key -> 丢弃（不处理）
        
        for field in self._schema.fields:
            if field.key not in config_section:
                # 新增配置项，使用默认值
                logger.info(f"添加新配置项: {field.key} = {field.default}")
                config_section[field.key] = field.default
        
        # 更新版本号
        self._config[VERSION_KEY] = self._version
        
        # 保存迁移后的配置
        self._save_config()
    
    def _sync_config_values(self) -> None:
        """同步配置值到内存，验证类型"""
        self._config_values.clear()
        
        if self._config is None or SECTION_NAME not in self._config:
            return
        
        config_section: Dict = self._config[SECTION_NAME]
        
        for key in self._schema.keys:
            field = self._schema.get_field(key)
            if not field:
                continue
            
            if key in config_section:
                value = config_section[key]
                
                # 验证类型
                if field.validate_value(value):
                    self._config_values[key] = value
                else:
                    # 类型验证失败，使用默认值
                    logger.warning(
                        f"配置项 '{key}' 类型验证失败，期望 {field.type.value}，"
                        f"实际 {type(value).__name__}，使用默认值: {field.default}"
                    )
                    self._config_values[key] = field.default
                    config_section[key] = field.default
            else:
                # 配置文件中没有该键，使用默认值
                self._config_values[key] = field.default
    
    def _save_config(self) -> None:
        """保存配置到文件"""
        try:
            # 确保目录存在
            self._config_path.parent.mkdir(parents=True, exist_ok=True)
            
            # 写回配置
            with open(self._config_path, "w", encoding="utf-8") as f:
                tomlkit.dump(self._config, f)
            
            logger.debug(f"配置已保存: {self._config_path}")
        except Exception as e:
            logger.error(f"保存配置文件失败: {e}")
            raise ConfigManagerError(f"无法保存配置文件: {e}") from e
    
    def get(self, key: str, default: Any = None) -> Any:
        """
        获取配置值
        
        Args:
            key: 配置键名
            default: 默认值（如果配置键不存在）
            
        Returns:
            配置值
        """
        if key in self._config_values:
            return self._config_values[key]
        
        if default is not None:
            return default
        
        # 如果没有默认值，返回 schema 中的默认值
        return self._schema.get_default(key)
    
    def set(self, key: str, value: Any) -> None:
        """
        设置配置值
        
        Args:
            key: 配置键名
            value: 配置值
            
        Raises:
            ConfigTypeError: 如果值类型不匹配
            ValueError: 如果配置键不存在于 schema 中
        """
        # 检查键是否存在于 schema
        if not self._schema.has_key(key):
            raise ValueError(f"配置键 '{key}' 不存在于 schema 中")
        
        field = self._schema.get_field(key)
        
        if field is None:
            raise ValueError(f"配置键 '{key}' 不存在于 schema 中")
        
        # 验证类型
        if not field.validate_value(value):
            raise ConfigTypeError(
                f"配置值类型错误: '{key}' 期望 {field.type.value}，"
                f"实际 {type(value).__name__}"
            )
        
        # 更新内存中的值
        self._config_values[key] = value
        
        # 更新 TOML 文档
        if self._config is not None and SECTION_NAME in self._config:
            self._config[SECTION_NAME][key] = value
    
    def save(self) -> None:
        """保存配置到文件"""
        self._save_config()
    
    def reload(self) -> None:
        """重新加载配置"""
        self._load()
    
    def validate(self) -> bool:
        """
        验证所有配置项的类型
        
        Returns:
            是否全部验证通过
        """
        all_valid = True
        
        for key, value in self._config_values.items():
            if not self._schema.validate_value(key, value):
                logger.warning(f"配置项 '{key}' 验证失败")
                all_valid = False
        
        return all_valid
    
    def get_all(self) -> Dict[str, Any]:
        """
        获取所有配置值
        
        Returns:
            配置值字典
        """
        return self._config_values.copy()
    
    def __getitem__(self, key: str) -> Any:
        """支持字典式访问"""
        return self.get(key)
    
    def __setitem__(self, key: str, value: Any) -> None:
        """支持字典式设置"""
        self.set(key, value)
    
    def __contains__(self, key: str) -> bool:
        """支持 in 操作符"""
        return key in self._config_values
    
    def __repr__(self) -> str:
        return f"ConfigManager(path={self._config_path}, version={self._version})"
