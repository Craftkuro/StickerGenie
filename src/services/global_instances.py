#coding=utf-8
import threading
from pathlib import Path

from blob_storage import BlobStorage
from config_manager import ConfigManager
from stickerdb.v1.sticker_db import StickerDBV1
from stickerdb.vectordb import ChromaVectorStore
from PyQt6.QtWidgets import QMainWindow

# 当前图库根目录（db、blob、recycler 等均位于其下）
current_library_path: Path | None = None

#已打开的数据库实例
current_library_db: StickerDBV1 | None = None

# 已打开的blob存储实例
current_blob_storage: BlobStorage | None = None

# 缩略图磁盘缓存实例
current_thumbnail_disk_storage = None

# 全局共享的缩略图服务实例（含内存 LRU 缓存）
current_thumbnail_provider = None

# 已打开的向量数据库实例
current_vector_store: ChromaVectorStore | None = None

# 应用程序设置管理器实例
current_settings_manager: ConfigManager | None = None

# 导入、检索和删除可能从不同线程访问 Chroma。
vector_store_lock = threading.RLock()

# 主窗体实例
main_window: QMainWindow | None = None
