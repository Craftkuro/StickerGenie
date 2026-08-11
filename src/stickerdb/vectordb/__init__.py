"""
向量数据库模块

此模块提供基于 ChromaDB 的向量存储和检索功能，用于存储和查询图像特征向量。

主要组件:
- ChromaVectorStore: 核心向量存储类
- VectorMetadata: 向量元数据模型
- VectorRecord: 向量记录模型
- SearchResult: 搜索结果模型
- ChromaDBConfig: 配置类

示例用法:
    >>> from stickerdb.vectordb import (
    ...     ChromaVectorStore,
    ...     VectorMetadata,
    ...     ChromaDBConfig
    ... )
    >>> 
    >>> # 初始化向量存储
    >>> config = ChromaDBConfig()
    >>> store = ChromaVectorStore("./chroma_data", config)
    >>> store.initialize()
    >>> 
    >>> # 添加向量
    >>> import numpy as np
    >>> vector = np.random.rand(768).astype(np.float32)
    >>> metadata = VectorMetadata(
    ...     image_filename="test.jpg",
    ...     model_hash="dinov2_vitb14_v1",
    ...     sqlite_id=1,
    ...     extraction_timestamp=1234567890.0,
    ...     image_width=800,
    ...     image_height=600
    ... )
    >>> vector_id = store.add(vector, metadata)
    >>> 
    >>> # 搜索相似向量
    >>> results = store.search_by_vector(vector, top_k=10)
    >>> for result in results:
    ...     print(f"{result.image_filename}: {result.similarity:.3f}")
    >>> 
    >>> # 关闭
    >>> store.close()
"""

from .chroma_store import ChromaVectorStore
from .models import VectorMetadata, VectorRecord, SearchResult
from .config import ChromaDBConfig
from .exceptions import (
    VectorDBException,
    VectorDimensionError,
    VectorNotFoundError,
    VectorDBConnectionError,
    MetadataValidationError,
    DuplicateVectorError,
)

__all__ = [
    # 核心类
    'ChromaVectorStore',
    
    # 数据模型
    'VectorMetadata',
    'VectorRecord',
    'SearchResult',
    
    # 配置
    'ChromaDBConfig',
    
    # 异常类
    'VectorDBException',
    'VectorDimensionError',
    'VectorNotFoundError',
    'VectorDBConnectionError',
    'MetadataValidationError',
    'DuplicateVectorError',
]

__version__ = '1.0.0'
__author__ = 'StickerGenie Team'
