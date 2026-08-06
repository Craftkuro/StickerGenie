"""
Blob Storage Module

用于存储大量二进制文件的模块。
支持文件的存储、读取和删除操作，使用SHA1哈希进行文件去重和分目录存储。
"""

from blob_storage.entities import BlobFileEntity
from blob_storage.blob_storage import BlobStorage

__all__ = ['BlobStorage', 'BlobFileEntity']
