from dataclasses import dataclass


@dataclass
class StickerImageMetadata:
    """
    用于存储贴纸图片元数据的数据类。
    
    Attributes:
        original_file_name: 原始文件名
        file_size: 文件大小（字节）
        hash: SHA1 哈希值
        extension: 文件扩展名（包含点号，例如 '.png'）
        size_width: 图片宽度（像素）
        size_height: 图片高度（像素）
    """
    original_file_name: str
    file_size: int
    hash: str
    extension: str
    size_width: int
    size_height: int