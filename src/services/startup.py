# coding=utf-8
import logging
import logging.handlers
import pathlib
import time
import apppath
import blob_storage
import thumbnail_disk_storage
import services.global_instances
import services.thumbnail_provider
import services.settings
from stickerdb.v1.sticker_db import StickerDBV1
from stickerdb.vectordb import ChromaVectorStore


logger = logging.getLogger(__name__)

# 日志文件大小上限（5 MB），超过后轮转；保留最近 5 个历史文件。
LOG_FILE_MAX_BYTES = 5 * 1024 * 1024
LOG_FILE_BACKUP_COUNT = 5
# 日志保留天数，超过此年龄的日志文件在启动时清理。
LOG_FILE_RETENTION_DAYS = 30


def run_startup_tasks():
    set_logging_levels()
    setup_file_logging()
    init_settings_manager()
    library_path = resolve_library_path()
    open_library(library_path)


def set_logging_levels():
    logging.getLogger('PyQt6.uic.uiparser').setLevel(logging.INFO)
    logging.getLogger('PyQt6.uic.properties').setLevel(logging.INFO)
    logging.getLogger('PIL').setLevel(logging.INFO)


def setup_file_logging():
    """在配置目录下的 log 文件夹中写入与控制台相同的日志，并自动轮转与清理。

    使用 RotatingFileHandler 按大小轮转，并在启动时删除超过保留期的旧日志。
    """
    if apppath.user_data_dir_path is None:
        logger.warning("数据目录尚未初始化，跳过文件日志配置")
        return

    log_dir = pathlib.Path(apppath.user_data_dir_path) / "log"
    log_dir.mkdir(parents=True, exist_ok=True)

    log_file = log_dir / "stickergenie.log"
    file_handler = logging.handlers.RotatingFileHandler(
        log_file,
        maxBytes=LOG_FILE_MAX_BYTES,
        backupCount=LOG_FILE_BACKUP_COUNT,
        encoding="utf-8",
    )
    file_handler.setLevel(logging.DEBUG)
    file_handler.setFormatter(
        logging.Formatter(
            "%(asctime)s [%(levelname)s] %(name)s: %(message)s"
        )
    )
    logging.root.addHandler(file_handler)

    cleanup_expired_logs(log_dir, LOG_FILE_RETENTION_DAYS)


def cleanup_expired_logs(log_dir: pathlib.Path, retention_days: int) -> None:
    """删除 log_dir 中修改时间早于 retention_days 天的日志文件。"""
    if retention_days <= 0:
        return
    cutoff = time.time() - retention_days * 86400
    for entry in log_dir.iterdir():
        if not entry.is_file():
            continue
        try:
            if entry.stat().st_mtime < cutoff:
                entry.unlink()
        except OSError:
            logger.warning("无法删除过期日志文件: %s", entry)


def init_settings_manager():
    settings_manager = services.settings.create_settings_manager()
    services.global_instances.current_settings_manager = settings_manager


def resolve_library_path():
    settings_manager = services.global_instances.current_settings_manager
    if settings_manager is None:
        raise RuntimeError("配置管理器尚未初始化")

    library_base_path = settings_manager.get("library_base_path")
    if not isinstance(library_base_path, str) or not library_base_path.strip():
        raise RuntimeError("配置项 library_base_path 无效")

    if apppath.base_path is None:
        raise RuntimeError("数据根目录尚未初始化")

    library_path = pathlib.Path(library_base_path).expanduser()
    if not library_path.is_absolute():
        library_path = apppath.base_path / library_path

    library_path.mkdir(parents=True, exist_ok=True)
    return library_path


def open_library(library_path):
    library_path = pathlib.Path(library_path)
    if not library_path.is_absolute():
        raise ValueError("图库路径必须是绝对路径")

    services.global_instances.current_library_path = library_path
    open_db(library_path)
    init_blob_storage(library_path)
    init_thumbnail_cache(library_path)
    init_vector_store(library_path)


def open_db(library_path):
    # 打开数据库
    db_base_path = pathlib.Path(library_path) / 'db' / 'v1'
    db_file_path = db_base_path / 'sticker.db'

    db = StickerDBV1(str(db_file_path))
    services.global_instances.current_library_db = db
    

def init_blob_storage(library_path):
    blob_storage_path = pathlib.Path(library_path) / 'blob'
    current_blob_storage = blob_storage.BlobStorage(str(blob_storage_path))
    services.global_instances.current_blob_storage = current_blob_storage


def init_thumbnail_cache(library_path):
    thumbnail_path = pathlib.Path(library_path) / 'thumbnails'
    current_thumbnail_disk_storage = thumbnail_disk_storage.ThumbnailDiskStorage(
        str(thumbnail_path)
    )
    thumbnail_cache_size = int(
        services.global_instances.current_settings_manager.get(
            "thumbnail_memory_cache_size"
        )
    )
    services.global_instances.current_thumbnail_disk_storage = (
        current_thumbnail_disk_storage
    )
    services.global_instances.current_thumbnail_provider = (
        services.thumbnail_provider.ThumbnailProvider(
            disk_storage=current_thumbnail_disk_storage,
            max_cache_size=thumbnail_cache_size,
        )
    )


def init_vector_store(library_path):
    vector_store_path = pathlib.Path(library_path) / 'vectors'
    vector_store = ChromaVectorStore(str(vector_store_path))
    vector_store.initialize()
    services.global_instances.current_vector_store = vector_store
