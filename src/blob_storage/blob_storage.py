"""
Blob Storage

用于存储大量二进制文件的类。
使用SHA1哈希进行文件去重，并将文件分散存储在256个目录中以改善查询性能。
"""

import os
import shutil
import hashlib
from pathlib import Path

from blob_storage.entities import BlobFileEntity


class BlobStorage:
    """
    Blob存储类，用于存储和管理大量的二进制文件。
    
    存储结构：
        base_path/
            00/
                <sha1_hash>.extension
            01/
                <sha1_hash>.extension
            ...
            ff/
                <sha1_hash>.extension
    
    每个文件根据其SHA1哈希值的前2个字符存储在对应的子目录中。
    """
    
    def __init__(self, blob_storage_base_path: str):
        """
        初始化BlobStorage实例。
        
        Args:
            blob_storage_base_path: Blob存储的根目录路径
        """
        self.base_path = Path(blob_storage_base_path)
        
        # 确保基础目录存在
        self.base_path.mkdir(parents=True, exist_ok=True)
    
    def _calculate_sha1(self, file_path: Path) -> str:
        """
        计算文件的SHA1哈希值。
        
        Args:
            file_path: 文件路径
            
        Returns:
            文件的SHA1哈希值（40位十六进制字符串）
        """
        sha1_hash = hashlib.sha1()
        
        with open(file_path, 'rb') as f:
            # 分块读取文件，避免大文件内存问题
            for chunk in iter(lambda: f.read(8192), b''):
                sha1_hash.update(chunk)
        
        return sha1_hash.hexdigest()
    
    def _get_extension(self, file_path: Path) -> str:
        """
        获取文件的扩展名。
        
        Args:
            file_path: 文件路径
            
        Returns:
            文件的扩展名（包含点号，例如：.jpg）
        """
        return file_path.suffix.lower()
    
    def _get_subdir_path(self, file_hash: str) -> Path:
        """
        获取文件存储的子目录路径。
        
        Args:
            file_hash: 文件的SHA1哈希值
            
        Returns:
            子目录路径（例如：base_path/00/）
        """
        # 取哈希值的前2个字符作为子目录名
        subdir_name = file_hash[:2]
        return self.base_path / subdir_name
    
    def _get_file_path(self, file_hash: str, extension: str) -> Path:
        """
        获取文件的完整存储路径。
        
        Args:
            file_hash: 文件的SHA1哈希值
            extension: 文件的扩展名
            
        Returns:
            文件的完整存储路径
        """
        subdir = self._get_subdir_path(file_hash)
        filename = f"{file_hash}{extension}"
        return subdir / filename
    
    def store_file(self, source_file_path: str, file_hash: str | None = None) -> BlobFileEntity:
        """
        存储文件到Blob存储中。
        
        如果文件已存在（相同的哈希值），则不会重复存储。
        
        Args:
            source_file_path: 源文件的路径
            file_hash：源文件的SHA1 hash（可选）
            
        Returns:
            BlobFileEntity实例，包含文件的哈希值和扩展名
            
        Raises:
            FileNotFoundError: 如果源文件不存在
        """
        source_path = Path(source_file_path)
        
        if not source_path.exists():
            raise FileNotFoundError(f"Source file not found: {source_file_path}")
        
        # 计算文件的SHA1哈希值。假如hash已提供，则使用提供的值。
        if file_hash is None:
            file_hash = self._calculate_sha1(source_path)
        elif len(file_hash) != 40:
            file_hash = self._calculate_sha1(source_path)
        
        # 获取文件扩展名
        extension = self._get_extension(source_path)
        
        # 获取目标文件路径
        target_path = self._get_file_path(file_hash, extension)
        
        # 如果文件已存在，直接返回
        if target_path.exists():
            return BlobFileEntity(file_hash, extension)
        
        # 创建子目录（如果不存在）
        target_path.parent.mkdir(parents=True, exist_ok=True)
        
        # 复制文件到目标位置
        shutil.copy2(source_path, target_path)
        
        return BlobFileEntity(file_hash, extension)
    
    def read_file(self, blob_file_entity: BlobFileEntity) -> str:
        """
        读取Blob存储中的文件，返回实际存储的文件路径。
        
        Args:
            blob_file_entity: BlobFileEntity实例
            
        Returns:
            实际存储的文件路径
            
        Raises:
            FileNotFoundError: 如果文件不存在
        """
        file_path = self._get_file_path(blob_file_entity.hash, blob_file_entity.extension)
        
        if not file_path.exists():
            raise FileNotFoundError(f"Blob file not found: {file_path}")
        
        return str(file_path)
    
    def delete_file(self, blob_file_entity: BlobFileEntity) -> None:
        """
        删除Blob存储中的文件。
        
        Args:
            blob_file_entity: BlobFileEntity实例
            
        Raises:
            FileNotFoundError: 如果文件不存在
        """
        file_path = self._get_file_path(blob_file_entity.hash, blob_file_entity.extension)
        
        if not file_path.exists():
            raise FileNotFoundError(f"Blob file not found: {file_path}")
        
        # 删除文件
        file_path.unlink()
        
        # 尝试删除空的子目录
        try:
            subdir = file_path.parent
            if subdir.exists() and not any(subdir.iterdir()):
                subdir.rmdir()
        except OSError:
            # 如果子目录不为空或删除失败，忽略错误
            pass
    
    def exists(self, blob_file_entity: BlobFileEntity) -> bool:
        """
        检查Blob存储中是否存在指定的文件。
        
        Args:
            blob_file_entity: BlobFileEntity实例
            
        Returns:
            如果文件存在返回True，否则返回False
        """
        file_path = self._get_file_path(blob_file_entity.hash, blob_file_entity.extension)
        return file_path.exists()
