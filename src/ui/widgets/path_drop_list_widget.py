# coding=utf-8
import os

from PyQt6.QtCore import QFileInfo, QUrl, pyqtSignal
from PyQt6.QtGui import QDragEnterEvent, QDragMoveEvent, QDropEvent
from PyQt6.QtWidgets import QAbstractItemView, QListWidget


class PathDropListWidget(QListWidget):
    """QListWidget that accepts local files and folders dropped from Explorer."""

    paths_dropped = pyqtSignal(list)

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setAcceptDrops(True)
        self.setDragDropMode(QAbstractItemView.DragDropMode.DropOnly)

    def dragEnterEvent(self, event: QDragEnterEvent) -> None:
        self._accept_if_has_local_urls(event)

    def dragMoveEvent(self, event: QDragMoveEvent) -> None:
        self._accept_if_has_local_urls(event)

    def dropEvent(self, event: QDropEvent) -> None:
        dropped_paths = [
            (path, QFileInfo(path).isDir())
            for path in self._local_paths(event)
        ]
        if not dropped_paths:
            event.ignore()
            return
        event.acceptProposedAction()
        self.paths_dropped.emit(dropped_paths)

    @staticmethod
    def _accept_if_has_local_urls(event) -> None:
        if any(
            url.isLocalFile() and os.path.isabs(url.toLocalFile())
            for url in event.mimeData().urls()
        ):
            event.acceptProposedAction()
        else:
            event.ignore()

    @staticmethod
    def _local_paths(event: QDropEvent) -> list[str]:
        paths = []
        for url in event.mimeData().urls():
            if not url.isLocalFile():
                continue
            path = url.toLocalFile()
            if path and os.path.isabs(path):
                paths.append(path)
        return paths
