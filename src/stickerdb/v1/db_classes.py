# coding=utf-8
import datetime
from typing import List, Optional

from sqlalchemy.orm import Mapped, relationship, mapped_column, Session
from sqlalchemy import Column, Integer, String, Boolean, ForeignKey, DateTime, Table, Text
from sqlalchemy.orm import declarative_base

from commons.dto import StickerImage, Tag

Base = declarative_base()


# 关联表，用于处理 sticker 和 tag 的多对多关系
association_table = Table('tag_assoc',
                          Base.metadata,
                          Column('sticker_id', ForeignKey('sticker_images.id')),
                          Column('tag_id', ForeignKey('tags.id')))


class DBStickerImage(Base):
    """表情包图片的 ORM 映射类"""
    __tablename__ = 'sticker_images'
    
    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    
    original_file_name: Mapped[str] = mapped_column(String, nullable=False)
    relative_path: Mapped[str] = mapped_column(String, nullable=False)
    file_size: Mapped[int] = mapped_column(Integer, nullable=False)
    hash: Mapped[str] = mapped_column(String, nullable=False, unique=True)
    extension: Mapped[str] = mapped_column(String, nullable=False)
    imported_at: Mapped[datetime.datetime] = mapped_column(DateTime, nullable=False)
    modification_date: Mapped[datetime.datetime] = mapped_column(DateTime, nullable=False)
    size_width: Mapped[int] = mapped_column(Integer, nullable=False)
    size_height: Mapped[int] = mapped_column(Integer, nullable=False)
    
    # 向量库中存储的 ID
    vectordb_id: Mapped[Optional[str]] = mapped_column(String, nullable=True)
    
    # 图片中的文字
    text_in_image: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    
    # 与 Tag 的多对多关系
    tags: Mapped[List['DBTag']] = relationship(secondary=association_table, back_populates='stickers')
    
    def __repr__(self):
        return f'<DBStickerImage "{self.original_file_name}">'
    
    def load_from_dto(self, dto: StickerImage):
        """
        从 DTO 对象加载数据到 ORM 对象。
        :param dto: StickerImage DTO 实例
        """
        self.original_file_name = dto.original_file_name
        self.relative_path = dto.relative_path
        self.file_size = dto.file_size
        self.hash = dto.hash
        self.extension = dto.extension
        self.imported_at = dto.imported_at
        self.modification_date = dto.modification_date
        self.size_width = dto.size_width
        self.size_height = dto.size_height
        self.vectordb_id = dto.vectordb_id
        self.text_in_image = dto.text_in_image
    
    def export(self) -> StickerImage:
        """
        将 ORM 对象导出为 DTO 对象。
        :return: StickerImage DTO 实例
        """
        dto = StickerImage()
        dto.id = self.id
        dto.original_file_name = self.original_file_name
        dto.relative_path = self.relative_path
        dto.file_size = self.file_size
        dto.hash = self.hash
        dto.extension = self.extension
        dto.imported_at = self.imported_at
        dto.modification_date = self.modification_date
        dto.size_width = self.size_width
        dto.size_height = self.size_height
        dto.vectordb_id = self.vectordb_id
        dto.text_in_image = self.text_in_image
        
        # 导出关联的 tags
        dto.tags = [tag.export() for tag in self.tags]
        
        return dto


class DBTag(Base):
    """标签的 ORM 映射类"""
    __tablename__ = 'tags'
    
    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    
    name: Mapped[str] = mapped_column(String, nullable=False, unique=True)
    description: Mapped[Optional[str]] = mapped_column(String, nullable=True)
    enabled: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)
    color_rgb: Mapped[str] = mapped_column(String, nullable=False, default='#FFFFFF')
    order: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    
    # 与 StickerImage 的多对多关系
    stickers: Mapped[List[DBStickerImage]] = relationship(secondary=association_table, back_populates='tags')
    
    def __repr__(self):
        return f'<DBTag "{self.name}", enabled={self.enabled}>'
    
    def load_from_dto(self, dto: Tag):
        """
        从 DTO 对象加载数据到 ORM 对象。
        :param dto: Tag DTO 实例
        """
        self.name = dto.name
        self.description = dto.description
        self.enabled = dto.enabled
        self.color_rgb = dto.color_rgb
        self.order = dto.order
    
    def export(self) -> Tag:
        """
        将 ORM 对象导出为 DTO 对象。
        :return: Tag DTO 实例
        """
        dto = Tag()
        dto.id = self.id
        dto.name = self.name
        dto.description = self.description
        dto.enabled = self.enabled
        dto.color_rgb = self.color_rgb
        dto.order = self.order
        return dto
