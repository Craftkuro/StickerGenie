"""
Blob File Entity

存储在BlobStorage中的文件实体类。
"""


class BlobFileEntity:
    """
    Blob存储文件实体类，包含文件的哈希值和扩展名。
    
    Attributes:
        hash: 文件的SHA1哈希值
        extension: 文件的扩展名（包含点号，例如：.jpg）
    """
    
    def __init__(self, file_hash: str, extension: str):
        """
        初始化BlobFileEntity实例。
        
        Args:
            file_hash: 文件的SHA1哈希值（40位十六进制字符串）
            extension: 文件的扩展名（包含点号，例如：.jpg）
        """
        self.hash = file_hash
        self.extension = extension
    
    def __repr__(self):
        return f'BlobFileEntity(hash={self.hash}, extension={self.extension})'
    
    def __eq__(self, other):
        if not isinstance(other, BlobFileEntity):
            return False
        return self.hash == other.hash and self.extension == other.extension
    
    def __hash__(self):
        return hash((self.hash, self.extension))
