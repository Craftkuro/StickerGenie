"""
向量数据库异常类定义

此模块定义了向量数据库操作中可能抛出的所有自定义异常。
"""


class VectorDBException(Exception):
    """
    向量数据库基础异常类
    
    所有向量数据库相关的异常都继承自此类。
    """
    pass


class VectorDimensionError(VectorDBException):
    """
    向量维度不匹配异常
    
    当输入的向量维度与配置的维度不一致时抛出。
    
    属性:
        expected: 期望的向量维度
        actual: 实际的向量维度
    """
    
    def __init__(self, expected: int, actual: int):
        """
        初始化向量维度错误
        
        参数:
            expected: 期望的向量维度
            actual: 实际的向量维度
        """
        self.expected = expected
        self.actual = actual
        super().__init__(
            f"向量维度不匹配: 期望 {expected} 维，实际 {actual} 维"
        )


class VectorNotFoundError(VectorDBException):
    """
    向量记录不存在异常
    
    当查询的向量ID不存在时抛出。
    
    属性:
        vector_id: 不存在的向量ID
    """
    
    def __init__(self, vector_id: str):
        """
        初始化向量未找到错误
        
        参数:
            vector_id: 不存在的向量ID
        """
        self.vector_id = vector_id
        super().__init__(f"向量记录不存在: {vector_id}")


class VectorDBConnectionError(VectorDBException):
    """
    数据库连接失败异常
    
    当无法连接到 ChromaDB 或初始化失败时抛出。
    
    属性:
        reason: 连接失败的原因
    """
    
    def __init__(self, reason: str):
        """
        初始化数据库连接错误
        
        参数:
            reason: 连接失败的原因描述
        """
        self.reason = reason
        super().__init__(f"数据库连接失败: {reason}")


class MetadataValidationError(VectorDBException):
    """
    元数据验证失败异常
    
    当元数据字段缺失、类型不匹配或值无效时抛出。
    
    属性:
        field: 验证失败的字段名
        reason: 验证失败的原因
    """
    
    def __init__(self, field: str, reason: str):
        """
        初始化元数据验证错误
        
        参数:
            field: 验证失败的字段名
            reason: 验证失败的原因
        """
        self.field = field
        self.reason = reason
        super().__init__(f"元数据字段 '{field}' 验证失败: {reason}")


class DuplicateVectorError(VectorDBException):
    """
    向量记录已存在异常
    
    当尝试添加已存在的向量ID时抛出。
    
    属性:
        vector_id: 重复的向量ID
    """
    
    def __init__(self, vector_id: str):
        """
        初始化重复向量错误
        
        参数:
            vector_id: 重复的向量ID
        """
        self.vector_id = vector_id
        super().__init__(f"向量记录已存在: {vector_id}")