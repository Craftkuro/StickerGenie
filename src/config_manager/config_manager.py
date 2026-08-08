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

import logging
import os
import tempfile
from collections.abc import MutableMapping
from copy import deepcopy
from pathlib import Path
from typing import Any, Dict, List

import tomlkit
from tomlkit import items
from tomlkit.toml_document import TOMLDocument

from .schema import ConfigField, ConfigSchema
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
        if not isinstance(version, str) or not version.strip():
            raise ValueError("version must be a non-empty string")

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
                return
            else:
                raise ConfigNotFoundError(f"配置文件不存在: {self._config_path}")

        previous_config = self._config
        previous_values = self._config_values

        try:
            content = self._config_path.read_text(encoding="utf-8")
            self._config = tomlkit.loads(content)
        except Exception as e:
            logger.error(f"加载配置文件失败: {e}")
            raise ConfigManagerError(f"无法加载配置文件: {e}") from e

        try:
            migrated = self._apply_migration()
            repaired = self._sync_config_values()
            if migrated or repaired:
                self._save_config()
        except ConfigManagerError:
            self._config = previous_config
            self._config_values = previous_values
            raise
        except Exception as e:
            self._config = previous_config
            self._config_values = previous_values
            logger.error(f"验证配置文件失败: {e}")
            raise ConfigValidationError(f"无法验证配置文件: {e}") from e
    
    def _create_default_config(self) -> None:
        """创建默认配置文件"""
        doc = tomlkit.document()
        doc[VERSION_KEY] = self._version

        config_section = tomlkit.table()
        for field in self._schema.fields:
            if field.comment:
                config_section.add(tomlkit.comment(field.comment))
            config_section[field.key] = field.get_default()

        doc[SECTION_NAME] = config_section

        previous_config = self._config
        previous_values = self._config_values
        self._config = doc
        try:
            self._sync_config_values()
            self._save_config()
        except Exception:
            self._config = previous_config
            self._config_values = previous_values
            raise

        logger.info(f"已创建默认配置文件: {self._config_path}")

    @staticmethod
    def _parse_numeric_version(version: str) -> tuple[int, ...] | None:
        """解析点分数字版本；无法安全比较时返回 None。"""
        parts = version.split(".")
        if not parts or any(not part.isdigit() for part in parts):
            return None

        parsed = [int(part) for part in parts]
        while parsed and parsed[-1] == 0:
            parsed.pop()
        return tuple(parsed)

    def _compare_versions(self, stored_version: str) -> int:
        """比较磁盘版本和当前版本，返回 -1、0 或 1。"""
        if stored_version == self._version:
            return 0

        stored = self._parse_numeric_version(stored_version)
        current = self._parse_numeric_version(self._version)
        if stored is None or current is None:
            raise ConfigMigrationError(
                f"无法安全比较配置版本: {stored_version!r} -> {self._version!r}"
            )
        if stored == current:
            return 0
        return -1 if stored < current else 1

    def _apply_migration(self) -> bool:
        """应用配置迁移"""
        if self._config is None:
            raise ConfigValidationError("配置文档尚未加载")

        stored_version = self._config.get(VERSION_KEY)
        if stored_version is None:
            stored_version = "0.0.0"
        elif not isinstance(stored_version, str):
            raise ConfigMigrationError(
                f"配置版本必须是字符串，实际为 {type(stored_version).__name__}"
            )

        stored_version_text = str(stored_version)
        comparison = self._compare_versions(stored_version_text)
        if comparison == 0:
            if stored_version_text != self._version:
                self._config[VERSION_KEY] = self._version
                return True
            return False
        if comparison > 0:
            raise ConfigMigrationError(
                f"配置文件版本 {stored_version} 高于当前支持版本 {self._version}"
            )

        logger.info(f"配置迁移: {stored_version} -> {self._version}")
        self._config[VERSION_KEY] = self._version
        return True

    @staticmethod
    def _plain_value(value: Any) -> Any:
        """将 TOMLKit 值转换成与调用方隔离的 Python 值。"""
        if isinstance(value, items.Item):
            value = value.unwrap()
        return deepcopy(value)

    def _config_section(self, create_if_missing: bool = False) -> MutableMapping:
        """获取并验证 config 节。"""
        if self._config is None:
            raise ConfigValidationError("配置文档尚未加载")

        if SECTION_NAME not in self._config:
            if not create_if_missing:
                raise ConfigValidationError(f"配置文件缺少 [{SECTION_NAME}] 节")
            self._config[SECTION_NAME] = tomlkit.table()

        config_section = self._config[SECTION_NAME]
        if not isinstance(config_section, MutableMapping):
            raise ConfigValidationError(
                f"配置节 [{SECTION_NAME}] 必须是 TOML 表，"
                f"实际为 {type(config_section).__name__}"
            )
        return config_section

    def _sync_config_values(self) -> bool:
        """同步配置值到内存，验证类型"""
        section_was_missing = (
            self._config is not None and SECTION_NAME not in self._config
        )
        config_section = self._config_section(create_if_missing=True)
        config_values: Dict[str, Any] = {}
        repaired = section_was_missing

        for key in self._schema.keys:
            field = self._schema.get_field(key)
            if not field:
                continue

            if key in config_section:
                value = self._plain_value(config_section[key])
                if field.validate_value(value):
                    config_values[key] = value
                else:
                    default = field.get_default()
                    logger.warning(
                        f"配置项 '{key}' 类型验证失败，期望 {field.type.value}，"
                        f"实际 {type(value).__name__}，使用默认值: {default}"
                    )
                    config_values[key] = deepcopy(default)
                    config_section[key] = default
                    repaired = True
            else:
                default = field.get_default()
                logger.info(f"添加缺失配置项: {key} = {default}")
                config_values[key] = deepcopy(default)
                config_section[key] = default
                repaired = True

        self._config_values = config_values
        return repaired

    def _save_config(self) -> None:
        """保存配置到文件"""
        if self._config is None:
            raise ConfigManagerError("配置文档尚未加载，无法保存")

        temporary_path: Path | None = None
        try:
            self._config_path.parent.mkdir(parents=True, exist_ok=True)
            content = tomlkit.dumps(self._config)
            with tempfile.NamedTemporaryFile(
                mode="w",
                encoding="utf-8",
                newline="",
                prefix=f".{self._config_path.name}.",
                suffix=".tmp",
                dir=self._config_path.parent,
                delete=False,
            ) as temporary_file:
                temporary_path = Path(temporary_file.name)
                temporary_file.write(content)
                temporary_file.flush()
                os.fsync(temporary_file.fileno())

            os.replace(temporary_path, self._config_path)
            temporary_path = None
            logger.debug(f"配置已保存: {self._config_path}")
        except Exception as e:
            logger.error(f"保存配置文件失败: {e}")
            raise ConfigManagerError(f"无法保存配置文件: {e}") from e
        finally:
            if temporary_path is not None:
                try:
                    temporary_path.unlink(missing_ok=True)
                except OSError as cleanup_error:
                    logger.warning(
                        f"清理配置临时文件失败 {temporary_path}: {cleanup_error}"
                    )

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
            return deepcopy(self._config_values[key])

        if default is not None:
            return deepcopy(default)

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
        
        config_section = self._config_section()
        stored_value = deepcopy(value)
        self._config_values[key] = stored_value
        config_section[key] = deepcopy(stored_value)
    
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
        return deepcopy(self._config_values)
    
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
