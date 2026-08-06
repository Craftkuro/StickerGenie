"""
向量数据库数据模型定义

此模块定义了向量数据库中使用的所有数据类：
- VectorMetadata: 向量元数据
- VectorRecord: 完整的向量记录
- SearchResult: 相似度搜索结果
"""

from dataclasses import dataclass, field
from typing import Any, Dict, Optional
import numpy as np
import time


@dataclass
class VectorMetadata:
    """
    向量记录的元数据
    
    属性:
        image_filename: 原始图像文件名
        model_hash: 特征提取模型的哈希值（用于判断是否需要重新生成）
        sqlite_id: SQLite数据库中对应的记录ID
        extraction_timestamp: 特征提取时间戳（Unix时间戳）
        image_width: 图像宽度（像素）
        image_height: 图像高度（像素）
        custom_fields: 自定义扩展字段（JSON可序列化字典）
    """
    
    image_filename: str
    model_hash: str
    sqlite_id: int
    extraction_timestamp: float
    image_width: int
    image_height: int
    custom_fields: Optional[Dict[str, Any]] = None
    
    def to_dict(self) -> Dict[str, Any]:
        """
        转换为字典格式（用于ChromaDB存储）
        
        注意: ChromaDB 要求元数据值必须是基本类型（str, int, float, bool）
        因此 sqlite_id 会被转换为字符串
        
        返回:
            元数据字典
        """
        metadata = {
            "image_filename": self.image_filename,
            "model_hash": self.model_hash,
            "sqlite_id": str(self.sqlite_id),  # ChromaDB 限制：转为字符串
            "extraction_timestamp": self.extraction_timestamp,
            "image_width": self.image_width,
            "image_height": self.image_height,
        }
        
        # 添加自定义字段
        if self.custom_fields:
            for key, value in self.custom_fields.items():
                # 确保值是 ChromaDB 支持的类型
                if isinstance(value, (str, int, float, bool)):
                    metadata[f"custom_{key}"] = value
        
        return metadata
    
    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> 'VectorMetadata':
        """
        从字典创建 VectorMetadata 对象
        
        参数:
            data: 元数据字典（来自ChromaDB）
            
        返回:
            VectorMetadata 实例
        """
        # 提取自定义字段
        custom_fields = {}
        for key, value in data.items():
            if key.startswith("custom_"):
                custom_fields[key[7:]] = value  # 移除 "custom_" 前缀
        
        return cls(
            image_filename=data["image_filename"],
            model_hash=data["model_hash"],
            sqlite_id=int(data["sqlite_id"]),  # 转回整数
            extraction_timestamp=float(data["extraction_timestamp"]),
            image_width=int(data["image_width"]),
            image_height=int(data["image_height"]),
            custom_fields=custom_fields if custom_fields else None
        )


@dataclass
class VectorRecord:
    """
    向量数据库记录
    
    属性:
        id: 向量记录的唯一标识符（UUID格式）
        vector: 特征向量（768维 float32 数组）
        metadata: 元数据对象
    """
    
    id: str
    vector: np.ndarray
    metadata: VectorMetadata
    
    def validate(self) -> bool:
        """
        验证记录的有效性
        
        返回:
            True 如果记录有效
            
        抛出:
            ValueError: 如果验证失败
        """
        # 验证 ID
        if not self.id or not isinstance(self.id, str):
            raise ValueError("向量ID必须是非空字符串")
        
        # 验证向量
        if not isinstance(self.vector, np.ndarray):
            raise ValueError("向量必须是 numpy 数组")
        
        if self.vector.ndim != 1:
            raise ValueError("向量必须是一维数组")
        
        if self.vector.dtype != np.float32:
            raise ValueError("向量数据类型必须是 float32")
        
        # 验证元数据
        if not isinstance(self.metadata, VectorMetadata):
            raise ValueError("元数据必须是 VectorMetadata 实例")
        
        return True


@dataclass
class SearchResult:
    """
    相似度搜索结果
    
    属性:
        id: 向量记录ID
        distance: 距离值（余弦距离，越小越相似）
        similarity: 相似度分数（0-1，越大越相似，计算为 1 - distance）
        metadata: 元数据对象
        vector: 特征向量（可选，节省内存）
    """
    
    id: str
    distance: float
    metadata: VectorMetadata
    vector: Optional[np.ndarray] = None
    
    @property
    def similarity(self) -> float:
        """
        相似度分数（0-1范围）
        
        对于余弦距离，相似度 = 1 - distance
        距离范围是 [0, 2]，所以相似度范围是 [-1, 1]
        但通常会归一化到 [0, 1]
        
        返回:
            相似度分数
        """
        return max(0.0, 1.0 - self.distance)
    
    @property
    def image_filename(self) -> str:
        """
        便捷访问图像文件名
        
        返回:
            图像文件名
        """
        return self.metadata.image_filename
    
    @property
    def sqlite_id(self) -> int:
        """
        便捷访问 SQLite ID
        
        返回:
            SQLite 数据库中的记录ID
        """
        return self.metadata.sqlite_id