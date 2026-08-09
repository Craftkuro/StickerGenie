# coding=utf-8
"""基于输入 Blob hash 分桶的缩略图磁盘缓存。"""

import logging
from pathlib import Path

from PyQt6.QtGui import QPixmap

logger = logging.getLogger(__name__)


class ThumbnailDiskStorage:
    """按 BlobFileEntity 的 hash 分桶存储缩略图。

    存储结构与 BlobStorage 类似：
        base_path/
            00/
                <blob_hash>.png
            01/
                <blob_hash>.png
            ...
            ff/
                <blob_hash>.png

    这里的分桶 hash 是原始图片（BlobFileEntity）的 hash，而不是缩略图自身的 hash。
    缩略图统一保存为 PNG，删除缓存时由本模块提供 delete_all() 接口。
    """

    THUMBNAIL_EXTENSION = ".png"

    def __init__(self, base_path: str | Path):
        """初始化缩略图缓存目录。"""
        self.base_path = Path(base_path)
        self.base_path.mkdir(parents=True, exist_ok=True)

    def _get_subdir_path(self, file_hash: str) -> Path:
        """返回分桶子目录（取 hash 前 2 个字符）。"""
        return self.base_path / file_hash[:2]

    def _get_file_path(self, file_hash: str) -> Path:
        """返回缩略图文件的完整路径。"""
        subdir = self._get_subdir_path(file_hash)
        filename = f"{file_hash}{self.THUMBNAIL_EXTENSION}"
        return subdir / filename

    def exists(self, file_hash: str) -> bool:
        """检查指定 hash 的缩略图是否存在。"""
        return self._get_file_path(file_hash).exists()

    def read_file(self, file_hash: str) -> str:
        """返回缩略图文件的路径。

        Raises:
            FileNotFoundError: 缩略图不存在时抛出。
        """
        file_path = self._get_file_path(file_hash)
        if not file_path.exists():
            raise FileNotFoundError(f"Thumbnail not found: {file_path}")
        return str(file_path)

    def save_pixmap(self, pixmap: QPixmap, file_hash: str) -> None:
        """将缩略图保存为 PNG。

        Args:
            pixmap: 待保存的缩略图。
            file_hash: 原始图片（BlobFileEntity）的 hash。

        Raises:
            ValueError: pixmap 为空时抛出。
            RuntimeError: 保存失败时抛出。
        """
        if pixmap.isNull():
            raise ValueError("不能保存空的缩略图")
        target_path = self._get_file_path(file_hash)
        target_path.parent.mkdir(parents=True, exist_ok=True)
        if not pixmap.save(str(target_path), "PNG"):
            raise RuntimeError(f"保存缩略图失败: {target_path}")

    def delete_file(self, file_hash: str) -> None:
        """删除指定 hash 的缩略图。

        Raises:
            FileNotFoundError: 缩略图不存在时抛出。
        """
        file_path = self._get_file_path(file_hash)
        if not file_path.exists():
            raise FileNotFoundError(f"Thumbnail not found: {file_path}")
        file_path.unlink()
        try:
            subdir = file_path.parent
            if subdir.exists() and not any(subdir.iterdir()):
                subdir.rmdir()
        except OSError:
            pass

    def delete_all(self) -> tuple[int, tuple[str, ...]]:
        """删除缓存目录下的全部内容，删除失败的项跳过并收集错误。

        Returns:
            (成功删除的文件数, 错误信息元组)
        """
        if not self.base_path.exists():
            return 0, ()

        deleted_count = 0
        errors: list[str] = []
        for child in list(self.base_path.iterdir()):
            try:
                if child.is_file():
                    child.unlink()
                    deleted_count += 1
                elif child.is_dir():
                    removed, child_errors = self._delete_directory(child)
                    deleted_count += removed
                    errors.extend(child_errors)
            except Exception as exc:
                logger.exception("删除缩略图缓存失败：%s", child)
                errors.append(f"{child}：{exc}")
        return deleted_count, tuple(errors)

    def _delete_directory(self, directory: Path) -> tuple[int, list[str]]:
        """递归删除一个子目录中的文件并尝试移除空目录。"""
        deleted_count = 0
        errors: list[str] = []
        for child in list(directory.iterdir()):
            try:
                if child.is_file():
                    child.unlink()
                    deleted_count += 1
                elif child.is_dir():
                    removed, child_errors = self._delete_directory(child)
                    deleted_count += removed
                    errors.extend(child_errors)
            except Exception as exc:
                logger.exception("删除缩略图缓存失败：%s", child)
                errors.append(f"{child}：{exc}")
        try:
            directory.rmdir()
        except OSError:
            pass
        return deleted_count, errors
