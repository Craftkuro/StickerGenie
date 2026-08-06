"""
配置管理器异常定义
"""


class ConfigManagerError(Exception):
    """配置管理器基础异常"""
    pass


class ConfigValidationError(ConfigManagerError):
    """配置项类型验证失败异常"""
    pass


class ConfigMigrationError(ConfigManagerError):
    """配置迁移异常"""
    pass


class ConfigNotFoundError(ConfigManagerError):
    """配置文件未找到异常"""
    pass


class ConfigTypeError(ConfigManagerError):
    """配置类型错误异常"""
    pass
