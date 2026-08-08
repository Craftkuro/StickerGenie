import datetime
from typing import Optional


class StickerImage:
    id: int
    original_file_name: str
    relative_path: str
    file_size: int
    hash: str
    extension: str
    imported_at: datetime.datetime
    modification_date: datetime.datetime
    size_width: int
    size_height: int
    vectordb_id: Optional[int]
    text_in_image: Optional[str]

    tags: list['Tag']

    def __init__(self):
        self.tags = []

    def __repr__(self):
        return f'StickerImage<{self.relative_path}>'


class Tag:
    """
    用于表情包的标签。
    """
    id: Optional[int]
    name: str
    description: str| None
    enabled: bool
    color_rgb: str

    def __init__(self):
        self.id = None
        self.description = None
        self.enabled = True
        self.color_rgb = '#2196F3'

    def __repr__(self):
        return f'Tag<{self.name}, enabled={self.enabled}>'
