# coding=utf-8

# Enums
LIST_DISPLAY_MODE_LIST = 0
LIST_DISPLAY_MODE_ICON = 1

SORT_BY_NAME = 0
SORT_BY_DATE = 1

# 缩略图：最长边尺寸（像素）
THUMBNAIL_SIZE = 200
# 原图长宽均不超过该值时，不生成缩略图，直接使用原图
THUMBNAIL_SKIP_THRESHOLD = 300
# 缩略图内存缓存的最大条目数（LRU）
THUMBNAIL_CACHE_MAX_COUNT = 2000
# Qt 全局 QPixmapCache 容量（KB）：默认约 10MB，容纳不下 1000 张 200x200
# ARGB32 缩略图（约 160KB/张）。按应用缩略图缓存规模放大，至少保证 1000 张。
QPIXMAP_CACHE_LIMIT_KB = (
    max(1000, THUMBNAIL_CACHE_MAX_COUNT)
    * THUMBNAIL_SIZE
    * THUMBNAIL_SIZE
    * 4
    // 1024
)

# 相似图片查找：一次从向量库取回的候选数量上限
SIMILAR_IMAGE_CANDIDATE_COUNT = 200
# 相似图片查找：最终展示的结果数量上限
SIMILAR_IMAGE_MAX_RESULTS = 100
