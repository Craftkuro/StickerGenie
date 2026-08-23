#coding=utf-8
"""删除图片的回收站。

删除图片时，Blob 文件与元数据以 ``<hash><扩展名>`` + ``<hash>.json``
sidecar 的形式暂存到 ``<图库>/recycler`` 目录，人工恢复时可直接重新导入。
回收站完全在数据库与 Blob 存储之外：SQLite 主记录照常删除，
维护功能的孤儿 Blob 清理只扫描 ``blob/`` 目录，永远不会触及 recycler；
任何不一致的最坏结果只是目录里多一个废文件。
"""

import datetime
import json
import logging
import shutil
from pathlib import Path

import services.global_instances
from blob_storage import BlobFileEntity

logger = logging.getLogger(__name__)

RECYCLER_DIR_NAME = "recycler"
SIDECAR_SCHEMA_VERSION = 1


def recycler_dir() -> Path | None:
    """返回回收站目录；图库尚未初始化时返回 None。"""
    library_path = services.global_instances.current_library_path
    if library_path is None:
        return None
    return Path(library_path) / RECYCLER_DIR_NAME


def _format_dt(value: datetime.datetime | None) -> str | None:
    return value.isoformat() if value else None


def stash_sticker(sticker) -> None:
    """把已删除图片的 Blob 文件与元数据移入回收站。

    Blob 文件缺失视为没有可暂存的内容（可能已被维护清理），静默返回；
    其他失败向上抛出，由调用方汇总为清理错误提示。
    """
    directory = recycler_dir()
    if directory is None:
        raise RuntimeError("图库尚未初始化，无法移入回收站。")

    entity = BlobFileEntity(sticker.hash, sticker.extension)
    try:
        source = Path(
            services.global_instances.current_blob_storage.read_file(entity)
        )
    except FileNotFoundError:
        logger.info("Blob 文件不存在，跳过回收站暂存：%s", sticker.hash)
        return

    directory.mkdir(parents=True, exist_ok=True)

    target = directory / f"{sticker.hash}{sticker.extension}"
    if target.exists():
        # 同一 hash 内容必然相同；sidecar 以最近一次删除为准。
        target.unlink()
    shutil.move(str(source), str(target))

    payload = {
        "schema_version": SIDECAR_SCHEMA_VERSION,
        "original_file_name": sticker.original_file_name,
        "extension": sticker.extension,
        "text_in_image": sticker.text_in_image,
        "tags": [tag.name for tag in sticker.tags],
        "imported_at": _format_dt(sticker.imported_at),
        "modification_date": _format_dt(sticker.modification_date),
        "deleted_at": datetime.datetime.now().isoformat(),
    }
    sidecar = directory / f"{sticker.hash}.json"
    sidecar.write_text(
        json.dumps(payload, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
