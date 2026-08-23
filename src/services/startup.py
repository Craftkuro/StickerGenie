# coding=utf-8
import logging
import pathlib
import apppath
import blob_storage
import thumbnail_disk_storage
import services.global_instances
import services.thumbnail_provider
import services.settings
from stickerdb.v1.sticker_db import StickerDBV1
from stickerdb.vectordb import ChromaVectorStore


logger = logging.getLogger(__name__)


def run_startup_tasks():
    set_logging_levels()
    init_settings_manager()
    library_path = resolve_library_path()
    open_library(library_path)


def set_logging_levels():
    logging.getLogger('PyQt6.uic.uiparser').setLevel(logging.INFO)
    logging.getLogger('PyQt6.uic.properties').setLevel(logging.INFO)


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
