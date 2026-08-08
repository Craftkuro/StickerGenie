"""
向量数据库配置参数

此模块定义了 ChromaDB 向量数据库的所有配置参数。
"""

from dataclasses import dataclass
from typing import Any, Dict

import numpy as np
from chromadb.api.collection_configuration import CreateCollectionConfiguration


# ============================================================================
# 默认配置常量
# ============================================================================

# 默认集合名称
DEFAULT_COLLECTION_NAME = "sticker_features_v1"

# 向量维度（ViT-B/16 标准输出）
VECTOR_DIMENSION = 768

# 向量数据类型
VECTOR_DTYPE = np.float32

# 距离度量方式
# 可选: "cosine"(余弦距离), "l2"(欧氏距离), "ip"(内积)
DISTANCE_METRIC = "cosine"

# 批量操作的批次大小
BATCH_SIZE = 100

# ============================================================================
# HNSW 索引配置
# ============================================================================

# HNSW 索引参数（ChromaDB 默认使用 HNSW）
HNSW_CONSTRUCTION_EF = 100   # 构建时的 ef 参数
HNSW_SEARCH_EF = 100         # 搜索时的 ef 参数
HNSW_MAX_NEIGHBORS = 16      # 每个节点的最大连接数

# ============================================================================
# 元数据模式
# ============================================================================

# 必需的元数据字段
REQUIRED_METADATA_FIELDS = [
    "image_filename",
    "model_hash",
    "sqlite_id",
    "extraction_timestamp",
    "image_width",
    "image_height",
]

# 元数据字段类型映射
METADATA_FIELD_TYPES = {
    "image_filename": str,
    "model_hash": str,
    "sqlite_id": int,
    "extraction_timestamp": float,
    "image_width": int,
    "image_height": int,
}


@dataclass
class ChromaDBConfig:
    """
    ChromaDB 配置类
    
    封装所有 ChromaDB 相关的配置参数。
    
    属性:
        collection_name: 集合名称
        dimension: 向量维度
        distance_metric: 距离度量方式
        batch_size: 批量操作的批次大小
        hnsw_construction_ef: HNSW 构建时的 ef 参数
        hnsw_search_ef: HNSW 搜索时的 ef 参数
        hnsw_max_neighbors: HNSW 每个节点的最大连接数
        anonymized_telemetry: 是否启用匿名遥测
        allow_reset: 是否允许重置数据库
    """
    
    collection_name: str = DEFAULT_COLLECTION_NAME
    dimension: int = VECTOR_DIMENSION
    distance_metric: str = DISTANCE_METRIC
    batch_size: int = BATCH_SIZE
    hnsw_construction_ef: int = HNSW_CONSTRUCTION_EF
    hnsw_search_ef: int = HNSW_SEARCH_EF
    hnsw_max_neighbors: int = HNSW_MAX_NEIGHBORS
    anonymized_telemetry: bool = False
    allow_reset: bool = True
    
    def get_collection_configuration(self) -> CreateCollectionConfiguration:
        """
        获取集合索引配置
        
        返回 ChromaDB 1.x 集合创建时使用的 configuration 字典。
        
        返回:
            ChromaDB 集合 configuration
        """
        return {
            "hnsw": {
                "space": self.distance_metric,
                "ef_construction": self.hnsw_construction_ef,
                "ef_search": self.hnsw_search_ef,
                "max_neighbors": self.hnsw_max_neighbors,
            }
        }
    
    def get_client_settings(self) -> Dict[str, Any]:
        """
        获取 ChromaDB 客户端设置
        
        返回:
            ChromaDB 客户端设置字典
        """
        return {
            "anonymized_telemetry": self.anonymized_telemetry,
            "allow_reset": self.allow_reset,
        }
    
    def validate(self) -> bool:
        """
        验证配置的有效性
        
        返回:
            True 如果配置有效
            
        抛出:
            ValueError: 如果配置无效
        """
        if self.dimension <= 0:
            raise ValueError(f"向量维度必须为正数，当前值: {self.dimension}")
        
        if self.distance_metric not in ["cosine", "l2", "ip"]:
            raise ValueError(
                f"不支持的距离度量方式: {self.distance_metric}，"
                f"支持的选项: cosine, l2, ip"
            )
        
        if self.batch_size <= 0:
            raise ValueError(f"批次大小必须为正数，当前值: {self.batch_size}")
        
        if self.hnsw_construction_ef <= 0:
            raise ValueError(
                f"HNSW construction_ef 必须为正数，当前值: {self.hnsw_construction_ef}"
            )
        
        if self.hnsw_search_ef <= 0:
            raise ValueError(
                f"HNSW search_ef 必须为正数，当前值: {self.hnsw_search_ef}"
            )
        
        if self.hnsw_max_neighbors <= 0:
            raise ValueError(
                "HNSW max_neighbors 必须为正数，"
                f"当前值: {self.hnsw_max_neighbors}"
            )
        
        return True
    
    def __repr__(self) -> str:
        """返回配置的字符串表示"""
        return (
            f"ChromaDBConfig("
            f"collection='{self.collection_name}', "
            f"dim={self.dimension}, "
            f"metric='{self.distance_metric}', "
            f"batch_size={self.batch_size})"
        )
