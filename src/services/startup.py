# coding=utf-8
import pathlib

import blob_storage
import services.global_instances
from stickerdb.v1.sticker_db import StickerDBV1



def run_startup_tasks():
    open_db()
    init_blob_storage()

def open_db():
    # 打开数据库
    db_base_path = r"D:\GitRepos\StickerGenie\StickerGenie Library\Default Library\db\v1"
    db_file_path = pathlib.Path(db_base_path, "sticker.db")

    db = StickerDBV1(str(db_file_path))
    services.global_instances.current_library_db = db
    

def init_blob_storage():
    current_blob_storage = blob_storage.BlobStorage(r"D:\GitRepos\StickerGenie\StickerGenie Library\Default Library\blob")
    services.global_instances.current_blob_storage = current_blob_storage