# coding=utf-8
import logging
from typing import List, Optional

from sqlalchemy import create_engine, select, func
from sqlalchemy.orm import sessionmaker, Session

from commons.dto import StickerImage, Tag
from .db_classes import DBStickerImage, DBTag, association_table, Base

logger = logging.getLogger(__name__)


class StickerDBV1:
    """
    表情包数据库管理类。
    使用 SQLAlchemy ORM 管理 SQLite 数据库。
    """
    
    # order_by 参数映射
    ORDER_BY_MAP = {
        'imported_at': DBStickerImage.imported_at,
        'modification_date': DBStickerImage.modification_date,
        'original_file_name': DBStickerImage.original_file_name,
        'file_size': DBStickerImage.file_size,
        # 别名支持
        'date': DBStickerImage.modification_date,
        'name': DBStickerImage.original_file_name,
        'size': DBStickerImage.file_size,
    }
    
    def __init__(self, db_path: str):
        """
        初始化数据库连接。
        :param db_path: SQLite 数据库文件路径
        """
        self.db_path = db_path
        # 创建数据库引擎
        self.engine = create_engine(f'sqlite:///{db_path}', echo=False, future=True)
        # 创建所有表
        Base.metadata.create_all(self.engine)
        # 创建 session factory
        self.SessionLocal = sessionmaker(bind=self.engine, autoflush=False, autocommit=False, expire_on_commit=False)
    
    def _get_session(self) -> Session:
        """获取一个新的 session"""
        return self.SessionLocal()
    
    def _export_sticker(self, db_sticker: DBStickerImage) -> StickerImage:
        """
        将 ORM 对象转换为 DTO，避免 session 绑定问题。
        :param db_sticker: DBStickerImage 实例
        :return: StickerImage DTO
        """
        return db_sticker.export()
    
    def _export_tag(self, db_tag: DBTag) -> Tag:
        """
        将 ORM 对象转换为 DTO。
        :param db_tag: DBTag 实例
        :return: Tag DTO
        """
        return db_tag.export()
    
    def _import_sticker(self, dto: StickerImage) -> DBStickerImage:
        """
        将 DTO 转换为 ORM 对象用于新增/修改操作。
        :param dto: StickerImage DTO
        :return: DBStickerImage 实例
        """
        db_sticker = DBStickerImage()
        db_sticker.load_from_dto(dto)
        return db_sticker
    
    def _import_tag(self, dto: Tag) -> DBTag:
        """
        将 DTO 转换为 ORM 对象用于新增/修改操作。
        :param dto: Tag DTO
        :return: DBTag 实例
        """
        db_tag = DBTag()
        db_tag.load_from_dto(dto)
        return db_tag
    
    # ==================== 查询接口 ====================
    
    def list_stickers(self, order_by: str = 'date', descending: bool = False, 
                      offset: int = 0, count: Optional[int] = 100) -> List[StickerImage]:
        """
        按指定的条件列出表情包。
        :param order_by: 排序字段，支持 'imported_at', 'modification_date', 'original_file_name', 'file_size',
                        以及别名 'date'(modification_date), 'name'(original_file_name), 'size'(file_size)
        :param descending: 是否降序
        :param offset: 偏移量
        :param count: 返回数量，None 表示返回全部
        :return: StickerImage DTO 列表
        """
        with self._get_session() as session:
            # 获取排序列
            order_column = self.ORDER_BY_MAP.get(order_by, DBStickerImage.modification_date)
            
            # 构建查询
            stmt = select(DBStickerImage).order_by(
                order_column.desc() if descending else order_column.asc()
            )
            
            # 应用偏移和限制
            if offset > 0:
                stmt = stmt.offset(offset)
            if count is not None:
                stmt = stmt.limit(count)
            
            # 执行查询
            db_stickers = session.execute(stmt).scalars().all()
            
            # 转换为 DTO 并返回
            return [self._export_sticker(sticker) for sticker in db_stickers]
    
    def query_by_single_tag(self, tag: Tag) -> List[StickerImage]:
        """
        根据指定的标签，查找符合条件的表情包。
        目前只支持单个标签的筛选。
        返回的数据按数据库内部顺序，需要在其他模块重新排序。
        :param tag: 标签对象
        :return: StickerImage DTO 列表
        """
        with self._get_session() as session:
            # 首先查找标签
            db_tag = session.execute(
                select(DBTag).where(DBTag.name == tag.name)
            ).scalar_one_or_none()
            
            if db_tag is None:
                return []
            
            # 查找与该标签关联的所有表情包
            stmt = select(DBStickerImage).join(association_table).where(
                association_table.c.tag_id == db_tag.id
            )
            
            db_stickers = session.execute(stmt).scalars().all()
            
            # 转换为 DTO 并返回
            return [self._export_sticker(sticker) for sticker in db_stickers]
    
    def query_by_file_name(self, name: str) -> List[StickerImage]:
        """
        根据指定的文件名（或部分文件名），查找符合条件的表情包。
        使用模糊匹配 (LIKE)。
        返回的数据按数据库内部顺序，需要在其他模块中重新排序。
        :param name: 文件名或文件名片段
        :return: StickerImage DTO 列表
        """
        with self._get_session() as session:
            # 使用 LIKE 进行模糊匹配
            stmt = select(DBStickerImage).where(
                DBStickerImage.original_file_name.like(f'%{name}%')
            )
            
            db_stickers = session.execute(stmt).scalars().all()
            
            # 转换为 DTO 并返回
            return [self._export_sticker(sticker) for sticker in db_stickers]
    
    # ==================== 增删改接口 ====================
    
    def add_stickers(self, stickers: List[StickerImage]):
        """
        新增表情包。
        文件名和 hash 无冲突由其他模块保证。
        :param stickers: StickerImage DTO 列表
        """
        with self._get_session() as session:
            for dto in stickers:
                # 创建新的 ORM 对象
                db_sticker = self._import_sticker(dto)
                
                # 处理标签关联
                if dto.tags:
                    for tag_dto in dto.tags:
                        # 查找或创建标签
                        db_tag = session.execute(
                            select(DBTag).where(DBTag.name == tag_dto.name)
                        ).scalar_one_or_none()
                        
                        if db_tag is None:
                            # 创建新标签
                            db_tag = self._import_tag(tag_dto)
                            session.add(db_tag)
                            session.flush()  # 获取 id
                        
                        db_sticker.tags.append(db_tag)
                
                session.add(db_sticker)
            
            session.commit()
    
    def modify_stickers(self, stickers: List[StickerImage]):
        """
        修改现有表情包。
        根据 StickerImage 实例中包含的 id 来确定需要更新的对象。
        :param stickers: StickerImage DTO 列表
        """
        with self._get_session() as session:
            for dto in stickers:
                # 根据 id 查找现有记录
                db_sticker = session.get(DBStickerImage, dto.id)
                
                if db_sticker is None:
                    logger.warning(f"尝试修改不存在的表情包，id={dto.id}")
                    continue
                
                # 更新属性
                db_sticker.load_from_dto(dto)
                
                # 处理标签关联
                if dto.tags:
                    # 清空现有标签关联
                    db_sticker.tags = []
                    
                    for tag_dto in dto.tags:
                        # 查找或创建标签
                        db_tag = session.execute(
                            select(DBTag).where(DBTag.name == tag_dto.name)
                        ).scalar_one_or_none()
                        
                        if db_tag is None:
                            # 创建新标签
                            db_tag = self._import_tag(tag_dto)
                            session.add(db_tag)
                            session.flush()  # 获取 id
                        
                        db_sticker.tags.append(db_tag)
            
            session.commit()
    
    def delete_stickers(self, stickers: List[StickerImage]):
        """
        根据输入实例中的 id 删除表情包。
        :param stickers: StickerImage DTO 列表
        """
        with self._get_session() as session:
            for dto in stickers:
                # 根据 id 查找并删除
                db_sticker = session.get(DBStickerImage, dto.id)
                if db_sticker is not None:
                    session.delete(db_sticker)
                else:
                    logger.warning(f"尝试删除不存在的表情包，id={dto.id}")
            
            session.commit()
    
    def add_or_modify_tag(self, tag: Tag):
        """
        新增一个标签。
        如果与现有的 id 重复则覆盖其属性，可使用这种方式来实现修改。
        :param tag: Tag DTO 对象
        """
        with self._get_session() as session:
            if tag.id:
                # 尝试根据 id 查找现有标签
                db_tag = session.get(DBTag, tag.id)
                
                if db_tag is not None:
                    # 修改现有标签
                    db_tag.load_from_dto(tag)
                else:
                    # id 不存在，创建新标签
                    db_tag = self._import_tag(tag)
                    session.add(db_tag)
            else:
                # 没有 id，根据名称查找
                db_tag = session.execute(
                    select(DBTag).where(DBTag.name == tag.name)
                ).scalar_one_or_none()
                
                if db_tag is not None:
                    # 修改现有标签
                    db_tag.load_from_dto(tag)
                else:
                    # 创建新标签
                    db_tag = self._import_tag(tag)
                    session.add(db_tag)
            
            session.commit()
    
    def delete_tag(self, tag: Tag):
        """
        删除标签。
        对象选择的依据是 tag 的 id。
        也要清除所有与这个 Tag 的关联。
        :param tag: Tag DTO 对象
        """
        with self._get_session() as session:
            # 根据 id 查找标签
            db_tag = session.get(DBTag, tag.id)
            
            if db_tag is None:
                logger.warning(f"尝试删除不存在的标签，id={tag.id}")
                return
            
            # 删除标签（关联表的记录会级联删除）
            session.delete(db_tag)
            session.commit()