"""
配置项定义和类型系统

支持的配置类型:
- STRING: 字符串 (str)
- INT: 整数 (int)
- BOOL: 布尔值 (bool)
- LIST_STR: 字符串列表 (List[str])
- LIST_INT: 整数列表 (List[int])
- LIST_TABLE: 表数组列表 (List[Dict[str, Any]])，TOML 中写为 [[key]] 表数组
"""

from __future__ import annotations
from copy import deepcopy
from enum import Enum
from dataclasses import dataclass
from typing import Union, List, Any, Dict


class ConfigType(Enum):
    """配置类型枚举"""
    STRING = "str"
    INT = "int"
    BOOL = "bool"
    LIST_STR = "list[str]"
    LIST_INT = "list[int]"
    LIST_TABLE = "list[table]"


class WidgetKind(Enum):
    """设置界面控件类型。

    SPIN_BOX_2P（spinbox_2_digit_fractions）：两位小数的数值微调框，
    底层为 QDoubleSpinBox(decimals=2)；用于以字符串形式存储的
    数值配置（如 "0.50"），读取时转 float，写回时格式化为
    两位小数字符串。
    """
    HIDDEN = "hidden"                       # 不在界面展示
    SPIN_BOX = "spin_box"                   # QSpinBox，整数
    SPIN_BOX_2P = "spinbox_2_digit_fractions"  # 两位小数数值框，见类注释
    COMBO_BOX = "combo_box"                 # QComboBox，choices 提供 (文本, 存储值)
    CHECK_BOX = "check_box"                 # QCheckBox，布尔


@dataclass(frozen=True)
class FieldUI:
    """
    配置项的界面展示描述；全部字段可选，缺省即最简外观。

    Attributes:
        kind: 控件类型；缺省按整数微调框处理
        page: 页面常量；None 表示不在任何页面展示
        label: 表单行标签；空则回退使用 key
        group: GroupBox 标题；空则不加组，行直接进页级表单
        suffix: 数值控件的后缀（如 “ 张”）
        minimum: 数值下限；None 用 Qt 默认
        maximum: 数值上限；None 用 Qt 默认
        step: singleStep；None 用 Qt 默认
        choices: COMBO_BOX 必填，元素为 (显示文本, 存储值)
    """
    kind: WidgetKind = WidgetKind.SPIN_BOX
    page: str | None = None
    label: str = ""
    group: str = ""
    suffix: str = ""
    minimum: float | None = None
    maximum: float | None = None
    step: float | None = None
    choices: tuple[tuple[str, Any], ...] = ()


@dataclass(frozen=True)
class ConfigField:
    """
    配置字段定义
    
    Attributes:
        key: 配置键名
        type: 配置类型
        default: 默认值
        comment: 注释说明（TOML 注释，同时作为界面 tooltip）
        ui: 界面展示描述；None 表示仅在配置文件中存在，不出现在界面
    """
    key: str
    type: ConfigType
    default: Union[str, int, bool, List[str], List[int], List[Dict[str, Any]]]
    comment: str = ""
    ui: FieldUI | None = None
    
    def __post_init__(self):
        """验证配置字段定义的合法性"""
        if not isinstance(self.key, str) or not self.key:
            raise ValueError("ConfigField key must be a non-empty string")

        if not isinstance(self.type, ConfigType):
            raise TypeError(f"ConfigField type must be ConfigType, got {type(self.type)}")

        if not isinstance(self.comment, str):
            raise TypeError(f"ConfigField comment must be str, got {type(self.comment)}")

        if self.ui is not None and not isinstance(self.ui, FieldUI):
            raise TypeError(f"ConfigField ui must be FieldUI or None, got {type(self.ui)}")
        
        # 验证默认值类型与声明类型一致
        self._validate_default_type()
    
    def _validate_default_type(self):
        """验证默认值类型"""
        if not self.validate_value(self.default):
            raise TypeError(
                f"Default value for {self.type.name} must be {self.type.value}, "
                f"got {type(self.default)}"
            )
    
    def validate_value(self, value: Any) -> bool:
        """
        验证值是否符合配置类型
        
        Args:
            value: 要验证的值
            
        Returns:
            是否通过验证
        """
        if self.type == ConfigType.STRING:
            return isinstance(value, str)
        elif self.type == ConfigType.INT:
            return isinstance(value, int) and not isinstance(value, bool)
        elif self.type == ConfigType.BOOL:
            return isinstance(value, bool)
        elif self.type == ConfigType.LIST_STR:
            if not isinstance(value, list):
                return False
            return all(isinstance(item, str) for item in value)
        elif self.type == ConfigType.LIST_INT:
            if not isinstance(value, list):
                return False
            return all(isinstance(item, int) and not isinstance(item, bool) for item in value)
        elif self.type == ConfigType.LIST_TABLE:
            if not isinstance(value, list):
                return False
            return all(isinstance(item, dict) for item in value)
        return False
    
    def get_default(self) -> Union[str, int, bool, List[str], List[int], List[Dict[str, Any]]]:
        """获取默认值"""
        return deepcopy(self.default)


class ConfigSchema:
    """
    配置模式管理
    
    管理一组配置字段定义，提供查找和验证功能。
    """
    
    def __init__(self, fields: List[ConfigField]):
        """
        初始化配置模式
        
        Args:
            fields: 配置字段列表
        """
        fields_copy = deepcopy(list(fields))
        seen_keys = set()
        duplicate_keys = set()
        for config_field in fields_copy:
            if not isinstance(config_field, ConfigField):
                raise TypeError(
                    "ConfigSchema fields must contain only ConfigField instances"
                )
            if config_field.key in seen_keys:
                duplicate_keys.add(config_field.key)
            seen_keys.add(config_field.key)

        if duplicate_keys:
            keys = ", ".join(sorted(duplicate_keys))
            raise ValueError(f"Duplicate config keys: {keys}")

        self._fields: Dict[str, ConfigField] = {
            field.key: field for field in fields_copy
        }
        self._fields_list: List[ConfigField] = fields_copy
    
    def get_field(self, key: str) -> ConfigField | None:
        """
        获取配置字段定义
        
        Args:
            key: 配置键名
            
        Returns:
            配置字段定义，如果不存在返回 None
        """
        field = self._fields.get(key)
        return deepcopy(field) if field else None
    
    def get_default(self, key: str) -> Any:
        """
        获取配置的默认值
        
        Args:
            key: 配置键名
            
        Returns:
            默认值，如果不存在返回 None
        """
        field = self._fields.get(key)
        return field.get_default() if field else None
    
    def validate_value(self, key: str, value: Any) -> bool:
        """
        验证配置值
        
        Args:
            key: 配置键名
            value: 要验证的值
            
        Returns:
            是否通过验证
        """
        field = self._fields.get(key)
        if not field:
            return False
        return field.validate_value(value)
    
    @property
    def fields(self) -> List[ConfigField]:
        """获取所有配置字段"""
        return deepcopy(self._fields_list)
    
    @property
    def keys(self) -> List[str]:
        """获取所有配置键名"""
        return list(self._fields.keys())
    
    def has_key(self, key: str) -> bool:
        """检查是否存在指定的配置键"""
        return key in self._fields
