# coding=utf-8
import logging
import pathlib
import threading
from typing import Iterable, List, Optional

from sqlalchemy import create_engine, select, func
from sqlalchemy.orm import sessionmaker, Session

from commons.dto import StickerImage, Tag
from .db_classes import DBStickerImage, DBTag, association_table, Base

logger = logging.getLogger(__name__)


def _existing_hashes_in_session(
    session: Session,
    hashes: Iterable[str],
) -> set[str]:
    unique_hashes = list(dict.fromkeys(hashes))
    existing_hashes = set()
    for offset in range(0, len(unique_hashes), 500):
        chunk = unique_hashes[offset:offset + 500]
        existing_hashes.update(
            session.execute(
                select(DBStickerImage.hash).where(DBStickerImage.hash.in_(chunk))
            ).scalars().all()
        )
    return existing_hashes


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
        pathlib.Path(db_path).parent.mkdir(parents=True, exist_ok=True)
        # 创建数据库引擎
        self.engine = create_engine(f'sqlite:///{db_path}', echo=False, future=True)
        # 创建所有表
        Base.metadata.create_all(self.engine)
        # 创建 session factory
        self.SessionLocal = sessionmaker(bind=self.engine, autoflush=False, autocommit=False, expire_on_commit=False)
        self._write_lock = threading.RLock()
    
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

    def get_stickers_by_ids(self, sticker_ids: List[int]) -> List[StickerImage]:
        """批量按 ID 查询图片，并保持输入 ID 的顺序。"""
        unique_ids = list(dict.fromkeys(sticker_ids))
        if not unique_ids:
            return []

        with self._get_session() as session:
            db_stickers = session.execute(
                select(DBStickerImage).where(DBStickerImage.id.in_(unique_ids))
            ).scalars().all()
            stickers_by_id = {
                sticker.id: self._export_sticker(sticker)
                for sticker in db_stickers
            }
            return [
                stickers_by_id[sticker_id]
                for sticker_id in unique_ids
                if sticker_id in stickers_by_id
            ]

    def get_existing_sticker_hashes(self, hashes: Iterable[str]) -> set[str]:
        """返回已存在于图库中的图片 hash。"""
        with self._get_session() as session:
            return _existing_hashes_in_session(session, hashes)

    def list_tags(self, enabled_only: bool = False) -> List[Tag]:
        """
        列出全局标签，按名称排序。
        :param enabled_only: 为 True 时只返回启用的标签
        :return: Tag DTO 列表
        """
        with self._get_session() as session:
            stmt = select(DBTag).order_by(DBTag.name.asc())
            if enabled_only:
                stmt = stmt.where(DBTag.enabled.is_(True))

            db_tags = session.execute(stmt).scalars().all()
            return [self._export_tag(tag) for tag in db_tags]
    
    # ==================== 增删改接口 ====================
    
    def add_stickers(self, stickers: List[StickerImage]) -> List[StickerImage]:
        """新增图片，忽略重复 hash，并返回实际插入的 DTO。"""
        if not stickers:
            return []

        with self._write_lock, self._get_session() as session:
            existing_hashes = _existing_hashes_in_session(
                session,
                (sticker.hash for sticker in stickers),
            )
            seen_hashes = set(existing_hashes)
            inserted_pairs = []
            for dto in stickers:
                if dto.hash in seen_hashes:
                    continue
                seen_hashes.add(dto.hash)

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
                inserted_pairs.append((dto, db_sticker))

            session.flush()
            for dto, db_sticker in inserted_pairs:
                dto.id = db_sticker.id
            session.commit()
            return [dto for dto, _ in inserted_pairs]

    def set_sticker_vector_ids(self, vector_ids_by_sticker_id: dict[int, str]) -> None:
        """批量回填图片关联的 Chroma UUID。"""
        if not vector_ids_by_sticker_id:
            return

        with self._get_session() as session:
            db_stickers = session.execute(
                select(DBStickerImage).where(
                    DBStickerImage.id.in_(vector_ids_by_sticker_id)
                )
            ).scalars().all()
            for db_sticker in db_stickers:
                db_sticker.vectordb_id = vector_ids_by_sticker_id[db_sticker.id]
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
                
                # DTO 表示图片的完整状态，空列表也应清除现有标签关联。
                db_sticker.tags = []

                for tag_dto in dto.tags:
                    db_tag = session.execute(
                        select(DBTag).where(DBTag.name == tag_dto.name)
                    ).scalar_one_or_none()

                    if db_tag is None:
                        db_tag = self._import_tag(tag_dto)
                        session.add(db_tag)
                        session.flush()

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
    
    def add_or_modify_tag(self, tag: Tag) -> Tag:
        """
        新增一个标签。
        如果与现有的 id 重复则覆盖其属性，可使用这种方式来实现修改。
        :param tag: Tag DTO 对象
        """
        with self._get_session() as session:
            if tag.id is not None:
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
            return self._export_tag(db_tag)
    
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

    def set_sticker_tags(self, sticker_id: int, tag_ids: List[int]) -> StickerImage:
        """
        使用给定的全局标签 ID 替换一张图片的全部标签关联。

        空列表会清除全部关联；不存在的图片或标签会抛出 ValueError，
        事务不会留下部分更新。
        """
        unique_tag_ids = list(dict.fromkeys(tag_ids))

        with self._get_session() as session:
            db_sticker = session.get(DBStickerImage, sticker_id)
            if db_sticker is None:
                raise ValueError(f"不存在的表情包，id={sticker_id}")

            db_tags = []
            if unique_tag_ids:
                found_tags = session.execute(
                    select(DBTag).where(DBTag.id.in_(unique_tag_ids))
                ).scalars().all()
                tags_by_id = {tag.id: tag for tag in found_tags}
                missing_ids = [tag_id for tag_id in unique_tag_ids if tag_id not in tags_by_id]
                if missing_ids:
                    raise ValueError(f"不存在的标签，id={missing_ids}")
                db_tags = [tags_by_id[tag_id] for tag_id in unique_tag_ids]

            db_sticker.tags = db_tags
            session.commit()
            return self._export_sticker(db_sticker)
