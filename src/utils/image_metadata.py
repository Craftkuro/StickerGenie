import hashlib
import os
import sys
from pathlib import Path

from PIL import Image

# 添加项目根目录到 Python 路径，以便导入 commons 模块
project_root = Path(__file__).resolve().parent.parent.parent
if str(project_root) not in sys.path:
    sys.path.insert(0, str(project_root))

from commons.image_metadata import StickerImageMetadata


def get_image_metadata(file_path: str | Path) -> StickerImageMetadata:
    """
    获取指定图片文件的元数据。
    
    Args:
        file_path: 图片文件的路径
        
    Returns:
        StickerImageMetadata: 包含图片元数据的对象
        
    Raises:
        FileNotFoundError: 当文件不存在时
        ValueError: 当文件不是有效的图片时
    """
    file_path = Path(file_path)
    
    if not file_path.exists():
        raise FileNotFoundError(f"文件不存在：{file_path}")
    
    # 获取原始文件名
    original_file_name = file_path.name
    
    # 获取文件大小
    file_size = file_path.stat().st_size
    
    # 获取文件扩展名
    extension = file_path.suffix.lower()
    
    # 计算 SHA1 哈希值
    sha1_hash = hashlib.sha1()
    with open(file_path, 'rb') as f:
        for chunk in iter(lambda: f.read(8192), b''):
            sha1_hash.update(chunk)
    hash_value = sha1_hash.hexdigest()
    
    # 获取图片尺寸
    try:
        with Image.open(file_path) as img:
            size_width, size_height = img.size
    except Exception as e:
        raise ValueError(f"无法读取图片尺寸：{e}")
    
    return StickerImageMetadata(
        original_file_name=original_file_name,
        file_size=file_size,
        hash=hash_value,
        extension=extension,
        size_width=size_width,
        size_height=size_height
    )


def get_directory_images_metadata(directory_path: str | Path) -> list[StickerImageMetadata]:
    """
    获取指定目录下所有图片文件的元数据。
    
    Args:
        directory_path: 目录路径
        
    Returns:
        list[StickerImageMetadata]: 包含所有图片元数据的列表
        
    Raises:
        NotADirectoryError: 当路径不是目录时
    """
    directory_path = Path(directory_path)
    
    if not directory_path.exists():
        raise FileNotFoundError(f"目录不存在：{directory_path}")
    
    if not directory_path.is_dir():
        raise NotADirectoryError(f"路径不是目录：{directory_path}")
    
    # 支持的图片格式
    supported_extensions = {'.jpg', '.jpeg', '.png', '.gif', '.bmp', '.webp', '.tiff', '.tif'}
    
    metadata_list = []
    
    for file_path in directory_path.iterdir():
        if file_path.is_file() and file_path.suffix.lower() in supported_extensions:
            try:
                metadata = get_image_metadata(file_path)
                metadata_list.append(metadata)
            except (ValueError, IOError) as e:
                # 跳过无法读取的图片文件
                print(f"警告：无法读取文件 {file_path}: {e}")
                continue
    
    return metadata_list