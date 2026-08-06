# coding=utf-8
import datetime
from typing import List, Optional
import logging

from sqlalchemy.orm import Mapped, relationship, mapped_column, sessionmaker, Session
from sqlalchemy import Column, Integer, String, Boolean, ForeignKey, DateTime, Table, create_engine, select
from sqlalchemy.orm import declarative_base
from sqlalchemy.testing.provision import drop_db

from commons.dto import StickerImage, Tag

logger = logging.getLogger(__name__)


Base = declarative_base()


association_table = Table('tag_assoc',
                            Base.metadata,
                            Column('sticker_id', ForeignKey('sticker_images.id')),
                            Column('tag_id', ForeignKey('tags.id')))

class DBStickerImage(Base):
    __tablename__ = 'sticker_images'
    id: Mapped[int] = mapped_column(primary_key=True, autoincrement=True)

    original_file_name: Mapped[str]
    relative_path: Mapped[str]
    file_size: Mapped[int]
    hash: Mapped[str]
    imported_at: Mapped[datetime.datetime]
    modification_date: Mapped[datetime.datetime]
    size_width: Mapped[int]
    size_height: Mapped[int]

    # 向量库中存储的ID
    vectordb_id: Mapped[Optional[int]]

    # 利用中间表存放每个sticker的多个tag
    tags: Mapped[List['DBTag']] = relationship(secondary=association_table, back_populates='stickers')

    def __repr__(self):
        return f'<DBStickerImage \"{self.file_name}\">'

    def load_from_dto(self, dto: StickerImage, session: Session):
        self.original_file_name = dto.original_file_name
        self.relative_path = dto.relative_path
        self.file_size = dto.file_size
        self.hash = dto.hash
        self.imported_at = dto.imported_at
        self.modification_date = dto.modification_date
        self.size_width = dto.size_width
        self.size_height = dto.size_height
        self.vectordb_id = dto.vectordb_id
        self.text_in_image = dto.text_in_image

        # Tags
        # 清空现有关联
        self.tags = []
        # 添加新关联
        for normal_tag in dto.tags:
            db_tag = session.query(DBTag).filter_by(name=normal_tag.name).first()
            self.tags.any(db_tag)

    def export(self) -> StickerImage:
        """
        将SQLAlchemy的实例转成普通实例
        :return:
        """
        normal_instance = StickerImage()
        normal_instance.file_name = self.file_name
        normal_instance.relative_path = self.relative_path
        normal_instance.hash = self.hash
        normal_instance.modification_date = self.modification_date
        normal_instance.main_tag = self.main_tag.name
        for db_tag in self.tags:
            normal_instance.tags.append(db_tag.export())

        return normal_instance



class DBTag(Base):
    __tablename__ = 'tags'
    id: Mapped[int] = mapped_column(primary_key=True)

    name: Mapped[str]
    description: Mapped[str] = mapped_column(nullable=True)
    enabled: Mapped[bool]

    main_tag_stickers: Mapped[DBStickerImage] = relationship(back_populates='main_tag')

    stickers: Mapped[List[DBStickerImage]] = relationship(secondary=association_table, back_populates='tags')

    def __repr__(self):
        return f'<DBTag \"{self.name}\">'

    def load_from_normal_instance(self, normal_instance: Tag, session: Session):
        self.name = normal_instance.name
        self.description = normal_instance.description
        self.enabled = normal_instance.enabled

        # 此处无需更新关联

    def export(self) -> Tag:
        normal_instance = Tag()
        normal_instance.name = self.name
        normal_instance.description = self.description
        normal_instance.enabled = self.enabled
        return normal_instance
