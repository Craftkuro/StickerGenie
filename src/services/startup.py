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
    configure_library_paths()
    open_db()
    init_blob_storage()
    init_thumbnail_cache()
    init_vector_store()


def set_logging_levels():
    logging.getLogger('PyQt6.uic.uiparser').setLevel(logging.INFO)
    logging.getLogger('PyQt6.uic.properties').setLevel(logging.INFO)


def init_settings_manager():
    settings_manager = services.settings.create_settings_manager()
    services.global_instances.current_settings_manager = settings_manager


def configure_library_paths():
    settings_manager = services.global_instances.current_settings_manager
    if settings_manager is None:
        raise RuntimeError("配置管理器尚未初始化")

    library_base_path = settings_manager.get("library_base_path")
    if not isinstance(library_base_path, str) or not library_base_path.strip():
        raise RuntimeError("配置项 library_base_path 无效")

    apppath.setup_library_paths(library_base_path)


def _resolve_active_library_path(library_path=None):
    if library_path is not None:
        return pathlib.Path(library_path)

    if apppath.default_library_path is None:
        raise RuntimeError("图库路径尚未初始化")

    return apppath.default_library_path


def open_db(library_path=None):
    # 打开数据库
    default_library_path = _resolve_active_library_path(library_path)
    db_base_path = default_library_path / 'db' / 'v1'
    db_file_path = db_base_path / 'sticker.db'

    db = StickerDBV1(str(db_file_path))
    services.global_instances.current_library_db = db
    

def init_blob_storage(library_path=None):
    default_library_path = _resolve_active_library_path(library_path)
    blob_storage_path = default_library_path / 'blob'
    current_blob_storage = blob_storage.BlobStorage(str(blob_storage_path))
    services.global_instances.current_blob_storage = current_blob_storage


def init_thumbnail_cache(library_path=None):
    default_library_path = _resolve_active_library_path(library_path)
    thumbnail_path = default_library_path / 'thumbnails'
    current_thumbnail_disk_storage = thumbnail_disk_storage.ThumbnailDiskStorage(
        str(thumbnail_path)
    )
    services.global_instances.current_thumbnail_disk_storage = (
        current_thumbnail_disk_storage
    )
    services.global_instances.current_thumbnail_provider = (
        services.thumbnail_provider.ThumbnailProvider(
            disk_storage=current_thumbnail_disk_storage,
        )
    )


def init_vector_store(library_path=None):
    default_library_path = _resolve_active_library_path(library_path)
    vector_store_path = default_library_path / 'vectors'
    vector_store = ChromaVectorStore(str(vector_store_path))
    vector_store.initialize()
    services.global_instances.current_vector_store = vector_store
