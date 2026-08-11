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
THUMBNAIL_CACHE_MAX_COUNT = 1000

# 相似图片查找：一次从向量库取回的候选数量上限
SIMILAR_IMAGE_CANDIDATE_COUNT = 200
# 相似图片查找：最终展示的结果数量上限
SIMILAR_IMAGE_MAX_RESULTS = 100
# 相似图片查找：相邻排名之间多大的相似度落差才算明显分群
SIMILAR_IMAGE_MIN_GAP = 0.02
# 相似图片查找：结果的最低绝对相似度
SIMILAR_IMAGE_MIN_SIMILARITY = 0.25
# 相似图片查找：只有孤零零一个结果时，要求的最低相似度
SIMILAR_IMAGE_LONE_RESULT_MIN_SIMILARITY = 0.5
# 相似图片查找：分数曲线没有明显落差时，最高分必须达到该值才返回结果
SIMILAR_IMAGE_NO_GAP_MIN_TOP_SIMILARITY = 0.4
