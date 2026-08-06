import pathlib

from PyQt6.QtGui import QStandardItemModel, QStandardItem, QIcon

import services.global_instances
from blob_storage import BlobFileEntity


def start_debug_view():
    db = services.global_instances.current_library_db
    images = db.list_stickers()
    model = QStandardItemModel()
    current_blob_storage = services.global_instances.current_blob_storage


    for image in images:
        file_path = current_blob_storage.read_file(BlobFileEntity(image.hash, image.extension))
        icon = QIcon(pathlib.Path(file_path).as_posix())
        model.insertRow(model.rowCount(), QStandardItem(icon, image.original_file_name))

    return model

