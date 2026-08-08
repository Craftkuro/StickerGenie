# coding=utf-8
import apppath
import blob_storage
import services.global_instances
from stickerdb.v1.sticker_db import StickerDBV1
from stickerdb.vectordb import ChromaVectorStore



def run_startup_tasks():
    open_db()
    init_blob_storage()
    init_vector_store()

def open_db():
    # 打开数据库
    db_base_path = apppath.default_library_path / 'db' / 'v1'
    db_file_path = db_base_path / 'sticker.db'

    db = StickerDBV1(str(db_file_path))
    services.global_instances.current_library_db = db
    

def init_blob_storage():
    blob_storage_path = apppath.default_library_path / 'blob'
    current_blob_storage = blob_storage.BlobStorage(str(blob_storage_path))
    services.global_instances.current_blob_storage = current_blob_storage


def init_vector_store():
    vector_store_path = apppath.default_library_path / 'vectors'
    vector_store = ChromaVectorStore(str(vector_store_path))
    vector_store.initialize()
    services.global_instances.current_vector_store = vector_store
