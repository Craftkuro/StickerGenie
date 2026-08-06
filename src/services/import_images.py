# coding=utf-8
"""
图片导入服务。
提供将图片文件导入数据库的功能。
"""

import datetime
from pathlib import Path
from typing import List, Optional


import services.global_instances
from commons.dto import StickerImage, Tag
from commons.image_metadata import StickerImageMetadata
from services.global_instances import current_library_db, current_blob_storage
from utils.image_metadata import get_image_metadata


def _metadata_to_sticker_image(metadata: StickerImageMetadata, file_path: Path) -> StickerImage:
    """
    将图片元数据转换为 StickerImage DTO。
    :param metadata: 图片元数据
    :param file_path: 图片文件路径
    :return: StickerImage DTO
    """
    now = datetime.datetime.now()
    
    sticker = StickerImage()
    sticker.original_file_name = metadata.original_file_name
    sticker.relative_path = str(file_path)
    sticker.file_size = metadata.file_size
    sticker.hash = metadata.hash
    sticker.extension = metadata.extension
    sticker.imported_at = now
    sticker.modification_date = now
    sticker.size_width = metadata.size_width
    sticker.size_height = metadata.size_height
    sticker.vectordb_id = None
    sticker.text_in_image = None
    
    return sticker


def import_images(file_paths: List[str], tags: Optional[List[Tag]] = None) -> List[StickerImage]:
    """
    将多个图片文件导入数据库。
    :param file_paths: 图片文件路径列表
    :param tags: 可选的标签列表，将应用于所有导入的图片
    :return: 成功导入的 StickerImage 对象列表
    :raises RuntimeError: 当数据库未初始化时
    """
    if services.global_instances.current_library_db is None:
        raise RuntimeError("数据库未初始化，无法导入图片")

    if services.global_instances.current_blob_storage is None:
        raise RuntimeError("blob存储未初始化，无法导入图片")

    current_library_db = services.global_instances.current_library_db
    current_blob_storage = services.global_instances.current_blob_storage
    imported_stickers = []
    
    for file_path in file_paths:
        path = Path(file_path)
        
        if not path.exists():
            continue
        
        try:
            # 使用工具函数获取图片元数据
            metadata = get_image_metadata(path)
            
            # 转换为 StickerImage DTO
            sticker = _metadata_to_sticker_image(metadata, path)
            
            # 添加标签
            if tags:
                for tag in tags:
                    sticker.tags.append(tag)
            
            imported_stickers.append(sticker)

            # 将图片复制到blob存储中
            current_blob_storage.store_file(file_path, metadata.hash)
            
        except (FileNotFoundError, ValueError) as e:
            # 跳过无法读取的图片文件
            print(f"警告：无法读取文件 {file_path}: {e}")
            continue
    
    # 批量插入数据库
    if imported_stickers:
        current_library_db.add_stickers(imported_stickers)
    
    return imported_stickers