# coding=utf-8
import datetime
import logging
import pathlib
import threading
from dataclasses import dataclass
from typing import Iterable, List, Optional

import boolean
from boolean.boolean import PARSE_UNKNOWN_TOKEN
from sqlalchemy import and_, case, create_engine, func, not_, or_, select, text
from sqlalchemy.orm import sessionmaker, Session, selectinload

from commons.dto import StickerImage, Tag
from .db_classes import DBStickerImage, DBTag, association_table, Base

logger = logging.getLogger(__name__)


class TagSearchExpressionError(ValueError):
    """高级标签表达式无法解析或编译时抛出的错误。"""


class _QuotedTagSearchAlgebra(boolean.BooleanAlgebra):
    """在 boolean.py 标准 tokenizer 上增加双引号标签字面量。"""

    def tokenize(self, expr):
        if not isinstance(expr, str):
            raise TypeError(f"expr must be string but it is {type(expr)}.")

        tokens = {
            "*": boolean.TOKEN_AND,
            "&": boolean.TOKEN_AND,
            "and": boolean.TOKEN_AND,
            "+": boolean.TOKEN_OR,
            "|": boolean.TOKEN_OR,
            "or": boolean.TOKEN_OR,
            "~": boolean.TOKEN_NOT,
            "!": boolean.TOKEN_NOT,
            "not": boolean.TOKEN_NOT,
            "(": boolean.TOKEN_LPAR,
            ")": boolean.TOKEN_RPAR,
            "[": boolean.TOKEN_LPAR,
            "]": boolean.TOKEN_RPAR,
            "true": boolean.TOKEN_TRUE,
            "1": boolean.TOKEN_TRUE,
            "false": boolean.TOKEN_FALSE,
            "0": boolean.TOKEN_FALSE,
            "none": boolean.TOKEN_FALSE,
        }

        position = 0
        length = len(expr)
        while position < length:
            char = expr[position]

            if char == '"':
                start = position
                position += 1
                parts = []
                while position < length:
                    char = expr[position]
                    if char != '"':
                        parts.append(char)
                        position += 1
                        continue

                    if position + 1 < length and expr[position + 1] == '"':
                        parts.append('"')
                        position += 2
                        continue

                    position += 1
                    yield boolean.TOKEN_SYMBOL, "".join(parts), start
                    break
                else:
                    raise boolean.ParseError(
                        token_string=expr[start:],
                        position=start,
                        error_code=PARSE_UNKNOWN_TOKEN,
                    )
                continue

            if char.isalnum() or char == "_":
                start = position
                position += 1
                while position < length:
                    next_char = expr[position]
                    if next_char.isalnum() or next_char in self.allowed_in_token:
                        position += 1
                        continue
                    break

                token = expr[start:position]
                token_type = tokens.get(token.lower(), boolean.TOKEN_SYMBOL)
                yield token_type, token, start
                continue

            if char in " \t\r\n":
                position += 1
                continue

            token_type = tokens.get(char)
            if token_type is None:
                raise boolean.ParseError(
                    token_string=char,
                    position=position,
                    error_code=PARSE_UNKNOWN_TOKEN,
                )
            yield token_type, char, position
            position += 1


_TAG_SEARCH_ALGEBRA = _QuotedTagSearchAlgebra()


@dataclass(frozen=True, slots=True)
class StickerMaintenanceRecord:
    id: int
    original_file_name: str
    hash: str
    extension: str
    size_width: int
    size_height: int
    vectordb_id: str | None
    text_in_image: str | None = None


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


def _compile_tag_search_condition(expression):
    algebra = _TAG_SEARCH_ALGEBRA

    if expression is algebra.TRUE or expression is algebra.FALSE:
        raise TagSearchExpressionError(
            "高级搜索表达式不允许使用 TRUE、FALSE、None 或数字常量，"
            "请使用双引号包裹标签名。"
        )

    if isinstance(expression, algebra.Symbol):
        return DBStickerImage.tags.any(
            and_(
                DBTag.enabled.is_(True),
                DBTag.name == expression.obj,
            )
        )

    if isinstance(expression, algebra.NOT):
        return not_(_compile_tag_search_condition(expression.args[0]))

    if isinstance(expression, algebra.AND):
        return and_(*(
            _compile_tag_search_condition(argument)
            for argument in expression.args
        ))

    if isinstance(expression, algebra.OR):
        return or_(*(
            _compile_tag_search_condition(argument)
            for argument in expression.args
        ))

    raise TagSearchExpressionError("高级搜索表达式包含不支持的节点。")


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
        # 为已存在的数据库补齐索引（新库已随 create_all 创建）
        self._ensure_indexes()
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

    @staticmethod
    def _next_tag_order(session: Session) -> int:
        next_order = session.execute(
            select(func.coalesce(func.max(DBTag.order), 0))
        ).scalar_one()
        return int(next_order)

    def _create_tag(self, session: Session, dto: Tag) -> DBTag:
        db_tag = self._import_tag(dto)
        db_tag.order = self._next_tag_order(session)
        session.add(db_tag)
        session.flush()
        dto.id = db_tag.id
        dto.order = db_tag.order
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
            
            # 构建查询；附加 id 作为次级排序，保证分页顺序稳定。
            stmt = (
                select(DBStickerImage)
                .options(selectinload(DBStickerImage.tags))
                .order_by(
                    order_column.desc() if descending else order_column.asc(),
                    DBStickerImage.id.desc() if descending else DBStickerImage.id.asc(),
                )
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
            stmt = (
                select(DBStickerImage)
                .options(selectinload(DBStickerImage.tags))
                .join(association_table)
                .where(association_table.c.tag_id == db_tag.id)
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
            stmt = (
                select(DBStickerImage)
                .options(selectinload(DBStickerImage.tags))
                .where(DBStickerImage.original_file_name.like(f'%{name}%'))
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
                select(DBStickerImage)
                .options(selectinload(DBStickerImage.tags))
                .where(DBStickerImage.id.in_(unique_ids))
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

    def random_sticker_id(self, *, excluding: Optional[int] = None) -> Optional[int]:
        """随机返回一个存在的图片 id；可指定排除某个 id。

        在真实存在的行上均匀采样（ORDER BY RANDOM()），天然无视删除造成的 id 空洞。
        空库、或排除后无剩余图片时返回 None。
        """
        stmt = select(DBStickerImage.id)
        if excluding is not None:
            stmt = stmt.where(DBStickerImage.id != excluding)
        stmt = stmt.order_by(func.random()).limit(1)

        with self._get_session() as session:
            return session.execute(stmt).scalar_one_or_none()

    def next_sticker_id(self, after_id: int) -> Optional[int]:
        """返回 after_id 之后的下一个存在 id；已是最大 id 时回绕到最小 id。空库返回 None。"""
        with self._get_session() as session:
            next_id, min_id = session.execute(
                select(
                    func.min(case((DBStickerImage.id > after_id, DBStickerImage.id))),
                    func.min(DBStickerImage.id),
                )
            ).one()
        return next_id if next_id is not None else min_id

    def get_existing_sticker_hashes(self, hashes: Iterable[str]) -> set[str]:
        """返回已存在于图库中的图片 hash。"""
        with self._get_session() as session:
            return _existing_hashes_in_session(session, hashes)

    def list_maintenance_records(self) -> list[StickerMaintenanceRecord]:
        """返回维护任务需要的轻量图片记录，不加载标签关系。"""
        with self._get_session() as session:
            rows = session.execute(
                select(
                    DBStickerImage.id,
                    DBStickerImage.original_file_name,
                    DBStickerImage.hash,
                    DBStickerImage.extension,
                    DBStickerImage.size_width,
                    DBStickerImage.size_height,
                    DBStickerImage.vectordb_id,
                    DBStickerImage.text_in_image,
                ).order_by(DBStickerImage.id.asc())
            ).all()
        return [StickerMaintenanceRecord(*row) for row in rows]

    def list_tags(self, enabled_only: bool = False) -> List[Tag]:
        """
        列出全局标签，按用户定义顺序排序。
        :param enabled_only: 为 True 时只返回启用的标签
        :return: Tag DTO 列表
        """
        with self._get_session() as session:
            stmt = select(DBTag).order_by(DBTag.order.asc(), DBTag.id.asc())
            if enabled_only:
                stmt = stmt.where(DBTag.enabled.is_(True))

            db_tags = session.execute(stmt).scalars().all()
            return [self._export_tag(tag) for tag in db_tags]

    def search_tags(
        self,
        query: str,
        *,
        limit: int = 10,
        enabled_only: bool = True,
    ) -> List[Tag]:
        """按名称子串查询标签，并按标签顺序返回。"""
        query = query.strip()
        if not query or limit <= 0:
            return []

        with self._get_session() as session:
            stmt = select(DBTag).where(
                DBTag.name.contains(query, autoescape=True)
            )
            if enabled_only:
                stmt = stmt.where(DBTag.enabled.is_(True))
            stmt = stmt.order_by(DBTag.order.asc(), DBTag.id.asc()).limit(limit)
            db_tags = session.execute(stmt).scalars().all()
            return [self._export_tag(tag) for tag in db_tags]

    def search_stickers_by_tag(self, query: str) -> List[StickerImage]:
        """查询任意启用标签名称包含指定文本的图片。"""
        query = query.strip()
        if not query:
            return []

        with self._get_session() as session:
            stmt = (
                select(DBStickerImage)
                .options(selectinload(DBStickerImage.tags))
                .join(
                    association_table,
                    association_table.c.sticker_id == DBStickerImage.id,
                )
                .join(DBTag, DBTag.id == association_table.c.tag_id)
                .where(
                    DBTag.enabled.is_(True),
                    DBTag.name.contains(query, autoescape=True),
                )
                .distinct()
                .order_by(
                    DBStickerImage.modification_date.desc(),
                    DBStickerImage.id.desc(),
                )
            )
            db_stickers = session.execute(stmt).scalars().all()
            return [self._export_sticker(sticker) for sticker in db_stickers]

    def search_stickers_by_tag_expression(
        self,
        expression: str,
    ) -> List[StickerImage]:
        """按布尔标签表达式查询图片，标签叶子节点使用严格相等匹配。"""
        if not isinstance(expression, str):
            raise TagSearchExpressionError("高级搜索表达式必须是文本。")

        expression = expression.strip()
        if not expression:
            return []

        try:
            parsed_expression = _TAG_SEARCH_ALGEBRA.parse(expression)
            condition = _compile_tag_search_condition(parsed_expression)
        except TagSearchExpressionError:
            raise
        except boolean.ParseError as exc:
            raise TagSearchExpressionError(
                f"高级搜索表达式语法错误：{exc}"
            ) from exc

        with self._get_session() as session:
            stmt = (
                select(DBStickerImage)
                .options(selectinload(DBStickerImage.tags))
                .where(condition)
                .order_by(
                    DBStickerImage.modification_date.desc(),
                    DBStickerImage.id.desc(),
                )
            )
            db_stickers = session.execute(stmt).scalars().all()
            return [self._export_sticker(sticker) for sticker in db_stickers]

    def search_stickers_by_text(self, query: str) -> List[StickerImage]:
        """查询图片识别文本中包含指定文本的图片。"""
        query = query.strip()
        if not query:
            return []

        with self._get_session() as session:
            stmt = (
                select(DBStickerImage)
                .options(selectinload(DBStickerImage.tags))
                .where(
                    DBStickerImage.text_in_image.contains(
                        query,
                        autoescape=True,
                    )
                )
                .order_by(
                    DBStickerImage.modification_date.desc(),
                    DBStickerImage.id.desc(),
                )
            )
            db_stickers = session.execute(stmt).scalars().all()
            return [self._export_sticker(sticker) for sticker in db_stickers]

    def search_stickers_by_file_name(self, query: str) -> List[StickerImage]:
        """查询原始文件名中包含指定文本的图片。"""
        query = query.strip()
        if not query:
            return []

        with self._get_session() as session:
            stmt = (
                select(DBStickerImage)
                .options(selectinload(DBStickerImage.tags))
                .where(
                    DBStickerImage.original_file_name.contains(
                        query,
                        autoescape=True,
                    )
                )
                .order_by(
                    DBStickerImage.modification_date.desc(),
                    DBStickerImage.id.desc(),
                )
            )
            db_stickers = session.execute(stmt).scalars().all()
            return [self._export_sticker(sticker) for sticker in db_stickers]
    
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
                            db_tag = self._create_tag(session, tag_dto)
                        
                        db_sticker.tags.append(db_tag)
                
                session.add(db_sticker)
                inserted_pairs.append((dto, db_sticker))

            session.flush()
            for dto, db_sticker in inserted_pairs:
                dto.id = db_sticker.id
            session.commit()
            return [dto for dto, _ in inserted_pairs]

    def add_missing_tags(self, tags: List[Tag]) -> int:
        """只插入当前不存在的同名标签，返回实际新增数量；已有标签完全不改。"""
        with self._write_lock, self._get_session() as session:
            seen_names = set(
                session.execute(select(DBTag.name)).scalars().all()
            )
            added = 0
            for dto in tags:
                if dto.name in seen_names:
                    continue
                seen_names.add(dto.name)
                session.add(self._import_tag(dto))
                added += 1
            session.commit()
            return added

    def merge_sticker_tags(
        self,
        sticker_hash: str,
        tags: List[Tag],
    ) -> int:
        """按 hash 合并图片的标签关联（并集去重），返回新增的关联数。"""
        if not tags:
            return 0

        with self._write_lock, self._get_session() as session:
            db_sticker = session.execute(
                select(DBStickerImage).where(
                    DBStickerImage.hash == sticker_hash
                )
            ).scalar_one_or_none()
            if db_sticker is None:
                return 0

            existing_tag_ids = {tag.id for tag in db_sticker.tags}
            added = 0
            for dto in tags:
                if dto.id is None or dto.id in existing_tag_ids:
                    continue
                db_tag = session.get(DBTag, dto.id)
                if db_tag is None:
                    continue
                db_sticker.tags.append(db_tag)
                existing_tag_ids.add(dto.id)
                added += 1
            session.commit()
            return added

    def set_sticker_vector_ids(self, vector_ids_by_sticker_id: dict[int, str]) -> None:
        """批量回填图片关联的 Chroma UUID。"""
        if not vector_ids_by_sticker_id:
            return

        with self._write_lock, self._get_session() as session:
            db_stickers = session.execute(
                select(DBStickerImage).where(
                    DBStickerImage.id.in_(vector_ids_by_sticker_id)
                )
            ).scalars().all()
            for db_sticker in db_stickers:
                db_sticker.vectordb_id = vector_ids_by_sticker_id[db_sticker.id]
            session.commit()

    def set_sticker_texts(
        self,
        text_by_sticker_id: dict[int, str | None],
    ) -> None:
        """批量回填图片 OCR 文本；None 表示无有效文本。"""
        if not text_by_sticker_id:
            return

        with self._write_lock, self._get_session() as session:
            db_stickers = session.execute(
                select(DBStickerImage).where(
                    DBStickerImage.id.in_(text_by_sticker_id)
                )
            ).scalars().all()
            for db_sticker in db_stickers:
                db_sticker.text_in_image = text_by_sticker_id[db_sticker.id]
            session.commit()

    def replace_sticker_vector_ids(
        self,
        vector_ids_by_sticker_id: dict[int, str],
    ) -> None:
        """严格批量替换向量 ID；任一图片不存在时不提交。"""
        if not vector_ids_by_sticker_id:
            return

        sticker_ids = set(vector_ids_by_sticker_id)
        with self._write_lock, self._get_session() as session:
            db_stickers = session.execute(
                select(DBStickerImage).where(DBStickerImage.id.in_(sticker_ids))
            ).scalars().all()
            found_ids = {db_sticker.id for db_sticker in db_stickers}
            missing_ids = sorted(sticker_ids - found_ids)
            if missing_ids:
                raise ValueError(f"图片记录不存在：{missing_ids}")

            for db_sticker in db_stickers:
                db_sticker.vectordb_id = vector_ids_by_sticker_id[db_sticker.id]
            session.commit()
    
    def modify_stickers(self, stickers: List[StickerImage]):
        """
        修改现有表情包。
        根据 StickerImage 实例中包含的 id 来确定需要更新的对象。
        :param stickers: StickerImage DTO 列表
        """
        with self._write_lock, self._get_session() as session:
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
                        db_tag = self._create_tag(session, tag_dto)

                    db_sticker.tags.append(db_tag)
            
            session.commit()

    def update_sticker_file_properties(
        self,
        sticker_id: int,
        *,
        original_file_name: Optional[str] = None,
        modification_date: Optional[datetime.datetime] = None,
    ) -> StickerImage:
        """
        更新图片的原始文件名与记录的修改时间；参数为 None 的字段保持原值。
        返回更新后的完整 DTO。图片不存在时抛出 ValueError，事务不留下部分更新。
        """
        with self._write_lock, self._get_session() as session:
            db_sticker = session.get(DBStickerImage, sticker_id)
            if db_sticker is None:
                raise ValueError(f"不存在的表情包，id={sticker_id}")

            if original_file_name is not None:
                db_sticker.original_file_name = self._normalize_original_file_name(
                    original_file_name, db_sticker.extension
                )
            if modification_date is not None:
                db_sticker.modification_date = modification_date

            session.commit()
            return self._export_sticker(db_sticker)

    @staticmethod
    def _normalize_original_file_name(raw_name: str, extension: str) -> str:
        """把用户输入规范化为安全的导出文件名。

        校验规则与图库导出的 _validate_original_file_name 对齐（空名、路径
        分隔符、相对路径片段都会让整次导出失败，必须在保存前拦截）。
        扩展名以实际文件类型为准：仅当输入恰好以真实扩展名结尾时原样保留
        （大小写归一），其余后缀视为基础名的一部分并追加真实扩展名，
        未填扩展名则自动补全——保证导出的文件名与文件内容类型一致。
        """
        name = raw_name.strip()
        if not name:
            raise ValueError("文件名不能为空。")
        if (
            name in {".", ".."}
            or "/" in name
            or "\\" in name
            or pathlib.Path(name).name != name
        ):
            raise ValueError(f"文件名包含系统不支持的字符，无法保存：{raw_name!r}")

        actual_extension = extension.lower()
        if actual_extension and name.lower().endswith(actual_extension):
            name = name[: -len(actual_extension)]
        name = name.rstrip(" .")
        if not name:
            raise ValueError("文件名不能为空。")
        return name + extension

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
        with self._write_lock, self._get_session() as session:
            if tag.id is not None:
                # 尝试根据 id 查找现有标签
                db_tag = session.get(DBTag, tag.id)
                
                if db_tag is not None:
                    # 修改现有标签
                    db_tag.load_from_dto(tag)
                else:
                    # id 不存在，创建新标签
                    db_tag = self._create_tag(session, tag)
            else:
                # 没有 id，根据名称查找
                db_tag = session.execute(
                    select(DBTag).where(DBTag.name == tag.name)
                ).scalar_one_or_none()
                
                if db_tag is not None:
                    # 修改现有标签
                    existing_order = db_tag.order
                    db_tag.load_from_dto(tag)
                    db_tag.order = existing_order
                else:
                    # 创建新标签
                    db_tag = self._create_tag(session, tag)
            
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

    def batch_edit_sticker_tags(
        self,
        sticker_ids: Iterable[int],
        tag_ids: Iterable[int],
        *,
        add: bool,
    ) -> tuple[int, list[StickerImage]]:
        """批量增加或删除图片标签，并返回修改数量和最新图片 DTO。"""
        unique_sticker_ids = list(dict.fromkeys(sticker_ids))
        unique_tag_ids = list(dict.fromkeys(tag_ids))
        if not unique_sticker_ids or not unique_tag_ids:
            return 0, []

        with self._write_lock, self._get_session() as session:
            db_tags = session.execute(
                select(DBTag).where(DBTag.id.in_(unique_tag_ids))
            ).scalars().all()
            tags_by_id = {tag.id: tag for tag in db_tags}
            missing_tag_ids = [
                tag_id
                for tag_id in unique_tag_ids
                if tag_id not in tags_by_id
            ]
            if missing_tag_ids:
                raise ValueError(f"不存在的标签，id={missing_tag_ids}")

            db_stickers = session.execute(
                select(DBStickerImage)
                .options(selectinload(DBStickerImage.tags))
                .where(DBStickerImage.id.in_(unique_sticker_ids))
            ).scalars().all()
            stickers_by_id = {
                sticker.id: sticker for sticker in db_stickers
            }
            selected_tag_ids = set(unique_tag_ids)
            modified_count = 0

            for sticker_id in unique_sticker_ids:
                db_sticker = stickers_by_id.get(sticker_id)
                if db_sticker is None:
                    continue

                current_tag_ids = {tag.id for tag in db_sticker.tags}
                if add:
                    updated_tags = list(db_sticker.tags)
                    for tag_id in unique_tag_ids:
                        if tag_id in current_tag_ids:
                            continue
                        updated_tags.append(tags_by_id[tag_id])
                        current_tag_ids.add(tag_id)
                else:
                    updated_tags = [
                        tag
                        for tag in db_sticker.tags
                        if tag.id not in selected_tag_ids
                    ]

                if len(updated_tags) == len(db_sticker.tags):
                    continue

                db_sticker.tags = updated_tags
                modified_count += 1

            session.commit()

            updated_stickers = [
                self._export_sticker(stickers_by_id[sticker_id])
                for sticker_id in unique_sticker_ids
                if sticker_id in stickers_by_id
            ]
            return modified_count, updated_stickers

    def _ensure_indexes(self) -> None:
        """为已存在的数据库补齐 ORM 中声明的索引，重复执行无副作用。"""
        with self.engine.begin() as connection:
            for table in Base.metadata.tables.values():
                for index in table.indexes:
                    columns = ", ".join(column.name for column in index.columns)
                    connection.execute(
                        text(
                            f"CREATE INDEX IF NOT EXISTS {index.name} "
                            f"ON {table.name} ({columns})"
                        )
                    )
