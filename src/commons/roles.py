# coding=utf-8
"""QStandardItem 模型角色常量，供 UI 和 service 层共享。"""

from PyQt6.QtCore import Qt


# 图片在 blob 存储中的实际文件路径
ROLE_FILE_PATH = Qt.ItemDataRole.UserRole
# 图片对应的 StickerImage DTO（含 id，便于后续按 id 查询）
ROLE_STICKER_IMAGE = Qt.ItemDataRole.UserRole + 1
# 相似图片结果的相似度；普通图库项为 None。
ROLE_SIMILARITY = Qt.ItemDataRole.UserRole + 2
