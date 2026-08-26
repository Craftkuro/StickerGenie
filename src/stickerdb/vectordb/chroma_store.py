"""
ChromaDB 向量存储实现

此模块实现了基于 ChromaDB 的向量存储类，提供完整的 CRUD 和查询功能。
"""

import logging
import uuid
from pathlib import Path
from typing import Any, Dict, List, Optional

import numpy as np
import chromadb
from chromadb.api.client import Client as ChromaClient
from chromadb.api.models.Collection import Collection as ChromaCollection
from chromadb.config import Settings

from .config import ChromaDBConfig
from .models import VectorMetadata, VectorRecord, SearchResult
from .exceptions import (
    VectorDBException,
    VectorDimensionError,
    VectorNotFoundError,
    VectorDBConnectionError,
    MetadataValidationError,
)

logger = logging.getLogger(__name__)


class ChromaVectorStore:
    """
    基于 ChromaDB 的向量存储类
    
    提供高效的向量存储、检索和相似度搜索功能。
    使用嵌入式模式（SQLite后端），数据持久化到本地磁盘。
    
    主要功能:
    - 向量的增删改查
    - 基于向量的相似度搜索
    - 元数据过滤查询
    - 批量操作支持
    - 与SQLite数据库的ID映射
    
    示例:
        >>> config = ChromaDBConfig()
        >>> store = ChromaVectorStore("./chroma_data", config)
        >>> store.initialize()
        >>> 
        >>> # 添加向量
        >>> metadata = VectorMetadata(...)
        >>> vector_id = store.add(features, metadata)
        >>> 
        >>> # 搜索相似向量
        >>> results = store.search_by_vector(query_vector, top_k=10)
    """
    
    def __init__(
        self,
        persist_directory: str,
        config: Optional[ChromaDBConfig] = None
    ):
        """
        初始化向量存储
        
        参数:
            persist_directory: 数据持久化目录路径
            config: ChromaDB 配置对象（可选，默认使用默认配置）
        """
        self.persist_directory = Path(persist_directory)
        self.config = config or ChromaDBConfig()
        self.config.validate()
        
        self._client: Optional[ChromaClient] = None
        self._collection: Optional[ChromaCollection] = None
        
        logger.info(f"ChromaVectorStore 初始化: {self.config}")
    
    def initialize(self) -> None:
        """
        初始化 ChromaDB 客户端和集合
        
        创建持久化目录并初始化 ChromaDB 客户端。
        如果集合不存在则创建，如果存在则获取现有集合。
        
        抛出:
            VectorDBConnectionError: 如果初始化失败
        """
        if self._client is not None:
            return

        try:
            # 创建持久化目录
            self.persist_directory.mkdir(parents=True, exist_ok=True)
            
            settings = Settings(**self.config.get_client_settings())
            self._client = chromadb.PersistentClient(
                path=str(self.persist_directory),
                settings=settings,
            )

            self._collection = self._client.get_or_create_collection(
                name=self.config.collection_name,
                configuration=self.config.get_collection_configuration(),
                embedding_function=None,
            )
            
            logger.info(
                f"ChromaDB 初始化成功，集合: {self.config.collection_name}, "
                f"记录数: {self.count()}"
            )
            
        except Exception as e:
            client = self._client
            self._collection = None
            self._client = None
            if client is not None:
                client.close()

            error_msg = f"ChromaDB 初始化失败: {str(e)}"
            logger.error(error_msg, exc_info=True)
            raise VectorDBConnectionError(error_msg) from e
    
    def close(self) -> None:
        """
        关闭连接
        
        ChromaDB 会自动持久化。显式关闭客户端可释放 SQLite 文件锁和
        其他本地资源，尤其适用于 Windows 环境。
        """
        client = self._client
        self._collection = None
        self._client = None

        if client is not None:
            logger.info("关闭 ChromaDB 连接")
            client.close()
    
    def reset(self) -> None:
        """
        重置集合（删除所有数据）
        
        警告:
            此操作不可逆！会删除集合中的所有向量数据。
            
        抛出:
            VectorDBConnectionError: 如果重置失败
        """
        if self._client is None:
            raise VectorDBConnectionError("客户端未初始化")

        if not self.config.allow_reset:
            raise VectorDBConnectionError("当前配置不允许重置集合")

        try:
            # 删除现有集合
            self._client.delete_collection(name=self.config.collection_name)
            
            # 重新创建集合
            self._collection = self._client.create_collection(
                name=self.config.collection_name,
                configuration=self.config.get_collection_configuration(),
                embedding_function=None,
            )
            
            logger.warning(f"集合已重置: {self.config.collection_name}")
            
        except Exception as e:
            error_msg = f"集合重置失败: {str(e)}"
            logger.error(error_msg, exc_info=True)
            raise VectorDBConnectionError(error_msg) from e
    
    def count(self) -> int:
        """
        获取集合中的向量总数
        
        返回:
            向量记录总数
        """
        if self._collection is None:
            return 0
        return self._collection.count()
    
    def _validate_vector(self, vector: np.ndarray) -> None:
        """
        验证向量的维度和类型
        
        参数:
            vector: 待验证的向量
            
        抛出:
            VectorDimensionError: 如果向量维度不匹配
            ValueError: 如果向量类型不正确
        """
        if not isinstance(vector, np.ndarray):
            raise ValueError("向量必须是 numpy 数组")
        
        if vector.ndim != 1:
            raise ValueError("向量必须是一维数组")
        
        if vector.shape[0] != self.config.dimension:
            raise VectorDimensionError(self.config.dimension, vector.shape[0])
        
        # 转换为 float32（如果需要）
        if vector.dtype != np.float32:
            logger.debug(f"向量类型从 {vector.dtype} 转换为 float32")
    
    def _validate_metadata(self, metadata: VectorMetadata) -> None:
        """
        验证元数据的完整性
        
        参数:
            metadata: 待验证的元数据
            
        抛出:
            MetadataValidationError: 如果元数据验证失败
        """
        if not isinstance(metadata, VectorMetadata):
            raise MetadataValidationError(
                "metadata",
                "必须是 VectorMetadata 实例"
            )
        
        # 验证必需字段
        if not metadata.image_filename:
            raise MetadataValidationError("image_filename", "不能为空")
        
        if not metadata.model_hash:
            raise MetadataValidationError("model_hash", "不能为空")
        
        if metadata.sqlite_id <= 0:
            raise MetadataValidationError("sqlite_id", "必须为正整数")
        
        if metadata.image_width <= 0 or metadata.image_height <= 0:
            raise MetadataValidationError("image_width/height", "必须为正整数")
    
    def add(
        self,
        vector: np.ndarray,
        metadata: VectorMetadata
    ) -> str:
        """
        添加单个向量记录
        
        参数:
            vector: 特征向量（768维，float32）
            metadata: 元数据对象
            
        返回:
            记录ID（UUID格式）
            
        抛出:
            VectorDimensionError: 向量维度不匹配
            MetadataValidationError: 元数据验证失败
            VectorDBConnectionError: 数据库连接失败
        """
        if self._collection is None:
            raise VectorDBConnectionError("集合未初始化")
        
        # 验证
        self._validate_vector(vector)
        self._validate_metadata(metadata)
        
        # 生成 UUID
        vector_id = uuid.uuid4().hex
        
        try:
            # 确保向量是 float32 类型
            vector_float32 = vector.astype(np.float32)
            
            # 添加到 ChromaDB
            self._collection.add(
                ids=[vector_id],
                embeddings=[vector_float32.tolist()],
                metadatas=[metadata.to_dict()]
            )
            
            logger.debug(
                f"添加向量: ID={vector_id}, "
                f"filename={metadata.image_filename}, "
                f"sqlite_id={metadata.sqlite_id}"
            )
            
            return vector_id
            
        except Exception as e:
            error_msg = f"添加向量失败: {str(e)}"
            logger.error(error_msg, exc_info=True)
            raise VectorDBException(error_msg) from e
    
    def add_batch(
        self,
        vectors: List[np.ndarray],
        metadata_list: List[VectorMetadata]
    ) -> List[str]:
        """
        批量添加向量记录
        
        参数:
            vectors: 向量列表
            metadata_list: 元数据列表
            
        返回:
            记录ID列表
            
        抛出:
            ValueError: 如果向量和元数据列表长度不匹配
            VectorDimensionError: 向量维度不匹配
            MetadataValidationError: 元数据验证失败
        """
        if self._collection is None:
            raise VectorDBConnectionError("集合未初始化")
        
        if len(vectors) != len(metadata_list):
            raise ValueError("向量和元数据列表长度必须相同")
        
        if len(vectors) == 0:
            return []
        
        # 验证所有向量和元数据
        for i, (vector, metadata) in enumerate(zip(vectors, metadata_list)):
            try:
                self._validate_vector(vector)
                self._validate_metadata(metadata)
            except Exception as e:
                logger.error(f"批量添加第 {i} 项验证失败: {e}")
                raise
        
        # 生成 UUID 列表
        vector_ids = [uuid.uuid4().hex for _ in range(len(vectors))]
        
        try:
            # 转换向量为 float32 列表
            embeddings = [v.astype(np.float32).tolist() for v in vectors]
            metadatas = [m.to_dict() for m in metadata_list]
            
            # 批量添加
            self._collection.add(
                ids=vector_ids,
                embeddings=embeddings,
                metadatas=metadatas
            )
            
            logger.info(f"批量添加 {len(vector_ids)} 个向量")
            return vector_ids
            
        except Exception as e:
            error_msg = f"批量添加向量失败: {str(e)}"
            logger.error(error_msg, exc_info=True)
            raise VectorDBException(error_msg) from e
    
    def delete(self, vector_id: str) -> bool:
        """
        删除指定ID的向量记录
        
        参数:
            vector_id: 记录ID
            
        返回:
            True 如果删除成功，False 如果记录不存在
        """
        if self._collection is None:
            raise VectorDBConnectionError("集合未初始化")
        
        try:
            existing = self._collection.get(ids=[vector_id], include=[])
            if not existing["ids"]:
                logger.warning(f"向量不存在，无法删除: {vector_id}")
                return False

            self._collection.delete(ids=[vector_id])
            logger.debug(f"删除向量: {vector_id}")
            return True
            
        except Exception as e:
            error_msg = f"删除向量失败: {str(e)}"
            logger.error(error_msg, exc_info=True)
            raise VectorDBException(error_msg) from e
    
    def delete_batch(self, vector_ids: List[str]) -> int:
        """
        批量删除向量记录

        超过单批上限（Chroma 限制约 5461）时自动分块，
        调用方无需关心集合规模。

        参数:
            vector_ids: 记录ID列表

        返回:
            成功删除的记录数
        """
        if self._collection is None:
            raise VectorDBConnectionError("集合未初始化")

        if len(vector_ids) == 0:
            return 0

        try:
            unique_ids = list(dict.fromkeys(vector_ids))
            existing = self._collection.get(ids=unique_ids, include=[])
            existing_ids = existing["ids"]

            if not existing_ids:
                logger.warning("所有向量ID都不存在")
                return 0

            # Chroma 对单次操作的批大小有硬限制，分块提交。
            chunk_size = 5000
            for start in range(0, len(existing_ids), chunk_size):
                self._collection.delete(
                    ids=existing_ids[start:start + chunk_size]
                )
            logger.info(f"批量删除 {len(existing_ids)} 个向量")
            return len(existing_ids)

        except Exception as e:
            error_msg = f"批量删除向量失败: {str(e)}"
            logger.error(error_msg, exc_info=True)
            raise VectorDBException(error_msg) from e

    def find_ids_by_sqlite_ids(
        self, sqlite_ids: List[int]
    ) -> Dict[int, str]:
        """
        批量解析 sqlite_id -> 向量记录ID 的映射

        供批量删除前一次性定位记录，替代逐条 get_by_sqlite_id。

        参数:
            sqlite_id: SQLite 数据库中的记录ID列表

        返回:
            {sqlite_id: vector_id} 字典；查不到记录的键不出现
        """
        if self._collection is None:
            raise VectorDBConnectionError("集合未初始化")

        unique_ids = list(dict.fromkeys(sqlite_ids))
        if not unique_ids:
            return {}

        try:
            result = self._collection.get(
                where={"sqlite_id": {"$in": unique_ids}},
                include=["metadatas"],
            )

            mapping: Dict[int, str] = {}
            for i in range(len(result["ids"])):
                metadata = result["metadatas"][i]
                mapping[int(metadata["sqlite_id"])] = result["ids"][i]
            return mapping

        except Exception as e:
            error_msg = f"批量解析 sqlite_id 失败: {str(e)}"
            logger.error(error_msg, exc_info=True)
            raise VectorDBException(error_msg) from e

    def delete_by_sqlite_id(self, sqlite_id: int) -> bool:
        """
        根据 SQLite ID 删除向量记录
        
        参数:
            sqlite_id: SQLite 数据库中的记录ID
            
        返回:
            True 如果删除成功
        """
        # 先查询找到对应的向量ID
        record = self.get_by_sqlite_id(sqlite_id)
        if record is None:
            logger.warning(f"SQLite ID {sqlite_id} 对应的向量不存在")
            return False
        
        return self.delete(record.id)
    
    def update(
        self,
        vector_id: str,
        vector: np.ndarray,
        metadata: VectorMetadata
    ) -> bool:
        """
        更新向量记录
        
        参数:
            vector_id: 记录ID
            vector: 新的特征向量
            metadata: 新的元数据
            
        返回:
            True 如果更新成功
            
        抛出:
            VectorNotFoundError: 记录不存在
            VectorDimensionError: 向量维度不匹配
        """
        if self._collection is None:
            raise VectorDBConnectionError("集合未初始化")
        
        # 检查记录是否存在
        if not self.exists(vector_id):
            raise VectorNotFoundError(vector_id)
        
        # 验证
        self._validate_vector(vector)
        self._validate_metadata(metadata)
        
        try:
            # 确保向量是 float32 类型
            vector_float32 = vector.astype(np.float32)
            
            # 更新记录
            self._collection.update(
                ids=[vector_id],
                embeddings=[vector_float32.tolist()],
                metadatas=[metadata.to_dict()]
            )
            
            logger.debug(f"更新向量: {vector_id}")
            return True
            
        except Exception as e:
            error_msg = f"更新向量失败: {str(e)}"
            logger.error(error_msg, exc_info=True)
            raise VectorDBException(error_msg) from e
    
    def update_metadata(
        self,
        vector_id: str,
        metadata: VectorMetadata
    ) -> bool:
        """
        更新元数据（不修改向量）
        
        参数:
            vector_id: 记录ID
            metadata: 新的元数据
            
        返回:
            True 如果更新成功
            
        抛出:
            VectorNotFoundError: 记录不存在
        """
        if self._collection is None:
            raise VectorDBConnectionError("集合未初始化")
        
        # 检查记录是否存在
        if not self.exists(vector_id):
            raise VectorNotFoundError(vector_id)
        
        # 验证元数据
        self._validate_metadata(metadata)
        
        try:
            # 只更新元数据
            self._collection.update(
                ids=[vector_id],
                metadatas=[metadata.to_dict()]
            )
            
            logger.debug(f"更新元数据: {vector_id}")
            return True
            
        except Exception as e:
            error_msg = f"更新元数据失败: {str(e)}"
            logger.error(error_msg, exc_info=True)
            raise VectorDBException(error_msg) from e
    
    def get(self, vector_id: str) -> Optional[VectorRecord]:
        """
        根据ID获取向量记录
        
        参数:
            vector_id: 记录ID
            
        返回:
            VectorRecord 对象，如果不存在则返回 None
        """
        if self._collection is None:
            raise VectorDBConnectionError("集合未初始化")
        
        try:
            result = self._collection.get(
                ids=[vector_id],
                include=["embeddings", "metadatas"]
            )
            
            if not result["ids"]:
                return None
            
            # 构建 VectorRecord
            vector = np.array(result["embeddings"][0], dtype=np.float32)
            metadata = VectorMetadata.from_dict(result["metadatas"][0])
            
            return VectorRecord(
                id=result["ids"][0],
                vector=vector,
                metadata=metadata
            )
            
        except Exception as e:
            error_msg = f"获取向量失败: {str(e)}"
            logger.error(error_msg, exc_info=True)
            raise VectorDBException(error_msg) from e
    
    def get_by_sqlite_id(self, sqlite_id: int) -> Optional[VectorRecord]:
        """
        根据 SQLite ID 获取向量记录
        
        参数:
            sqlite_id: SQLite 数据库中的记录ID
            
        返回:
            VectorRecord 对象，如果不存在则返回 None
        """
        if self._collection is None:
            raise VectorDBConnectionError("集合未初始化")
        
        try:
            # 查询元数据
            result = self._collection.get(
                where={"sqlite_id": sqlite_id},
                include=["embeddings", "metadatas"]
            )
            
            if not result["ids"]:
                return None
            
            # 构建 VectorRecord（取第一个匹配项）
            vector = np.array(result["embeddings"][0], dtype=np.float32)
            metadata = VectorMetadata.from_dict(result["metadatas"][0])
            
            return VectorRecord(
                id=result["ids"][0],
                vector=vector,
                metadata=metadata
            )
            
        except Exception as e:
            error_msg = f"通过 SQLite ID 获取向量失败: {str(e)}"
            logger.error(error_msg, exc_info=True)
            raise VectorDBException(error_msg) from e
    
    def exists(self, vector_id: str) -> bool:
        """
        检查记录是否存在
        
        参数:
            vector_id: 记录ID
            
        返回:
            True 如果记录存在
        """
        if self._collection is None:
            return False
        
        try:
            result = self._collection.get(ids=[vector_id])
            return len(result["ids"]) > 0
        except Exception as e:
            error_msg = f"检查向量是否存在失败: {str(e)}"
            logger.error(error_msg, exc_info=True)
            raise VectorDBException(error_msg) from e
    
    def search_by_id(
        self,
        vector_id: str,
        top_k: int = 10,
        include_distances: bool = True
    ) -> List[SearchResult]:
        """
        根据向量ID查询最相似的记录
        
        参数:
            vector_id: 查询的向量ID
            top_k: 返回结果数量
            include_distances: 是否包含距离信息
            
        返回:
            SearchResult 列表，按相似度降序排列，包含查询向量自身作为参考
            
        抛出:
            VectorNotFoundError: 记录ID不存在
        """
        # 先获取向量
        record = self.get(vector_id)
        if record is None:
            raise VectorNotFoundError(vector_id)
        
        results = self.search_by_vector(
            record.vector,
            top_k=top_k,
            include_distances=include_distances,
            # 只比较同一特征模型生成的向量，避免模型切换后新旧向量混算
            model_hash=record.metadata.model_hash,
        )
        # 保留查询向量自身：第一张图作为相似度比较的参考图
        return results
    
    def search_by_vector(
        self,
        query_vector: np.ndarray,
        top_k: int = 10,
        include_distances: bool = True,
        model_hash: Optional[str] = None,
    ) -> List[SearchResult]:
        """
        根据输入向量查询最相似的记录
        
        参数:
            query_vector: 查询向量（768维）
            top_k: 返回结果数量
            include_distances: 是否包含距离信息
            model_hash: 仅返回该模型哈希的记录；None 表示不过滤
            
        返回:
            SearchResult 列表，按相似度降序排列
            
        抛出:
            VectorDimensionError: 向量维度不匹配
        """
        if self._collection is None:
            raise VectorDBConnectionError("集合未初始化")
        
        # 验证向量
        self._validate_vector(query_vector)
        
        try:
            # 确保向量是 float32 类型
            query_vector_float32 = query_vector.astype(np.float32)
            
            query_options = {}
            if model_hash is not None:
                query_options["where"] = {"model_hash": model_hash}

            # 查询
            results = self._collection.query(
                query_embeddings=[query_vector_float32.tolist()],
                n_results=top_k,
                include=["embeddings", "metadatas", "distances"],
                **query_options,
            )
            
            # 构建 SearchResult 列表
            search_results = []
            for i in range(len(results["ids"][0])):
                vector = np.array(results["embeddings"][0][i], dtype=np.float32)
                metadata = VectorMetadata.from_dict(results["metadatas"][0][i])
                distance = results["distances"][0][i] if include_distances else 0.0
                
                search_results.append(SearchResult(
                    id=results["ids"][0][i],
                    distance=distance,
                    metadata=metadata,
                    vector=vector
                ))
            
            logger.debug(f"搜索返回 {len(search_results)} 个结果")
            return search_results
            
        except Exception as e:
            error_msg = f"向量搜索失败: {str(e)}"
            logger.error(error_msg, exc_info=True)
            raise VectorDBException(error_msg) from e
    
    def query_by_metadata(
        self,
        filters: Dict[str, Any],
        limit: int = 100
    ) -> List[VectorRecord]:
        """
        根据元数据条件查询记录
        
        参数:
            filters: 元数据过滤条件
            limit: 返回结果数量限制
            
        返回:
            VectorRecord 列表
        """
        if self._collection is None:
            raise VectorDBConnectionError("集合未初始化")
        
        try:
            result = self._collection.get(
                where=filters,
                limit=limit,
                include=["embeddings", "metadatas"]
            )
            
            # 构建 VectorRecord 列表
            records = []
            for i in range(len(result["ids"])):
                vector = np.array(result["embeddings"][i], dtype=np.float32)
                metadata = VectorMetadata.from_dict(result["metadatas"][i])
                
                records.append(VectorRecord(
                    id=result["ids"][i],
                    vector=vector,
                    metadata=metadata
                ))
            
            logger.debug(f"元数据查询返回 {len(records)} 个结果")
            return records
            
        except Exception as e:
            error_msg = f"元数据查询失败: {str(e)}"
            logger.error(error_msg, exc_info=True)
            raise VectorDBException(error_msg) from e
    
    def __enter__(self):
        """上下文管理器入口"""
        self.initialize()
        return self
    
    def __exit__(self, exc_type, exc_val, exc_tb):
        """上下文管理器退出"""
        self.close()
        return False
