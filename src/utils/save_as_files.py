"""另存为图片文件的后端工具。"""

from __future__ import annotations

import logging
import shutil
import unicodedata
from collections.abc import Sequence
from pathlib import Path

logger = logging.getLogger(__name__)


def has_duplicate_original_file_names(file_names: Sequence[str]) -> bool:
    """判断原始文件名集合中是否存在重名，忽略大小写和 Unicode 规范化差异。"""
    seen_names = set()
    for file_name in file_names:
        key = unicodedata.normalize("NFC", file_name).casefold()
        if key in seen_names:
            return True
        seen_names.add(key)
    return False


def save_as_files(
    source_files: Sequence[tuple[str | Path, str]],
    destination_directory: str | Path,
    *,
    target_names: Sequence[str] | None = None,
) -> tuple[int, int]:
    """复制图片到目标目录，返回 (成功数量, 失败数量)。

    source_files 的每一项是 (图片路径, 原始文件名)。默认使用原始文件名作为
    目标文件名；target_names 可用于覆盖目标文件名，例如单选“另存为”改名场景。
    调用方应先通过 has_duplicate_original_file_names 确认原始文件名不重复。
    """
    destination_path = Path(destination_directory)
    source_list = list(source_files)
    if target_names is None:
        target_names = [
            original_file_name
            for _, original_file_name in source_list
        ]
    if len(target_names) != len(source_list):
        raise ValueError("target_names 的长度必须与 source_files 一致")

    succeeded = 0
    failed = 0
    for (source_path, original_file_name), target_name in zip(
        source_list,
        target_names,
    ):
        try:
            shutil.copy2(source_path, destination_path / target_name)
            succeeded += 1
        except Exception:
            logger.exception("另存为图片失败：%s", original_file_name)
            failed += 1
    return succeeded, failed
