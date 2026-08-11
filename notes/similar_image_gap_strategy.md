# 相似图片查找：结果过滤策略

## 0. 模型切换说明

默认特征模型已切换为 `siglip_base_patch16_224`。旧策略基于 DINOv2 的
相似度分布和默认阈值；SigLIP 切换后需要重新评估这些参数。

## 1. 旧策略（已弃用）

相似图片查找最初使用"固定相似度阈值 + 数量上限"的方式（例如 `>50%`
或 `>70%`）。实际使用中发现两个问题：

- 阈值调高时，很多相关图片查不到（相关结果可能落在 35%~50% 区间）。
- 阈值调低时，大量无关图片混进结果。

因此改为观察每个查询自己的相似度排序曲线，用"最大落差"决定结果边界。
旧策略的核心实现是 `services.sticker_library_viewer_service` 中的
`_select_similar_count()`：

1. 从向量库取回相似度最高的 200 个候选（数量上限可配）。
2. 按相似度降序计算相邻名次之间的分数差。
3. 如果存在明显落差（默认 >=0.02），保留落差之前的结果，落差之后的
   候选视为另一个群体直接排除。
4. 如果曲线平缓（没有明显落差）：
   - 最高分很低（默认 <0.4）：判定没有可分的相似群体，返回空。
   - 最高分较高：保留整个高分平台，再按最低相似度过滤尾部。
5. 对保留结果应用最低绝对相似度过滤（默认 0.25）。
6. 如果只剩一个结果且分数不高（默认 <0.5），视为随机孤点，返回空。
7. 最终结果数不超过 100。

由于 SigLIP 在大型图库上的分数曲线整体偏高且平缓，该策略出现了严重的
两极分化：大量查询只返回 1 张重复图，或一次性返回 100 张低分结果。

## 2. 新策略：drop-rate knee 检测

新策略把过滤逻辑拆到了独立模块
[`src/services/similarity_result_filter.py`](../src/services/similarity_result_filter.py)。
核心思想是：对每个查询，观察其排序曲线从最高分到最低分的累计下降，
当累计下降达到总下降的某个比例（knee）时截断；同时强制保留至少
`min_keep` 个结果，避免 knee 过早导致只显示一张重复图。

算法步骤：

1. 输入必须按相似度降序排列。
2. 如果最高分低于 `min_similarity`，直接返回空。
3. 计算总下降 `total_drop = top_score - bottom_score`。
4. 从顶部开始累加相邻下降，直到 `cumulative_drop >= target_drop_ratio * total_drop`，
   记录截断位置。
5. 截断位置与 `min_keep` 取较大值，保证至少保留若干结果。
6. 只保留分数仍高于 `min_similarity` 的候选。
7. 最终受 `max_results` 限制。

重复 / 近重复群会被自然保留，因为它们通常处于同一个高分平台内；如果
某个查询只有一张近重复图而后续分数骤降，`min_keep` 仍会把紧随其后的
几张相似图带回来，避免结果变成"只有一张图"。

## 3. 实现位置

- 过滤模块：[`src/services/similarity_result_filter.py`](../src/services/similarity_result_filter.py)
- 调用位置：[`src/services/sticker_library_viewer_service.py`](../src/services/sticker_library_viewer_service.py)
  中的 `find_similar_stickers()`
- 参数 schema：[`src/services/settings.py`](../src/services/settings.py)
- 设置 UI：[`src/ui/dialog_settings.ui`](../src/ui/dialog_settings.ui)
- 单元测试：[`tests/test_similarity_result_filter.py`](../tests/test_similarity_result_filter.py)

## 4. 可调参数

这些参数已加入设置 schema，可在设置对话框中调整，保存后下一次相似图片
查询即生效。

| 配置键 | 类型 | 默认值 | 作用 | 调参方向 |
| --- | --- | --- | --- | --- |
| `similar_image_target_drop_ratio` | float/string | 0.5 | 累计下降比例阈值 | 调大更保守（保留更少），调小更宽松 |
| `similar_image_min_keep` | int | 5 | 最少保留结果数 | 调大可避免只返回 1 张，调小更依赖曲线形状 |
| `similar_image_min_similarity` | float/string | 0.50 | 最低相似度 | 调大过滤低分噪音，调小可能找回低分相关图 |
| `similar_image_max_results` | int | 100 | 最多返回结果数 | 硬上限 |

剩余常量：

| 常量 | 默认值 | 作用 |
| --- | --- | --- |
| `SIMILAR_IMAGE_CANDIDATE_COUNT` | 200 | 一次从向量库取回的候选数量上限 |
| `SIMILAR_IMAGE_MAX_RESULTS` | 100 | 最终展示的结果数量上限 |

## 5. 实际效果（2026-08-11，14,716 张 SigLIP 向量）

新策略默认参数（target_drop_ratio=0.5, min_keep=5, min_similarity=0.50,
max_results=100）下的结果数分布：

| 结果数 | 查询数 | 占比 |
| --- | ---: | ---: |
| 0 | 0 | 0.00% |
| 1 | 0 | 0.00% |
| 2 | 0 | 0.00% |
| 3-5 | 2,494 | 16.95% |
| 6-20 | 5,437 | 36.95% |
| 21-50 | 5,626 | 38.23% |
| 51-100 | 1,157 | 7.86% |
| 101-200 | 2 | 0.01% |

相比旧策略，分布连续，没有出现"只返回 1 张"或"直接撑满 100 张"的极端。

## 6. 调参建议

- 觉得"结果太少 / 有些相关图没找到"：调低 `target_drop_ratio` 或
  `min_similarity`，或提高 `min_keep`。
- 觉得"噪音太多 / 结果太杂"：调高 `min_similarity` 或 `target_drop_ratio`。
- 如果某张图有很多近重复，而你希望看到更多不同变体：提高 `min_keep`。
- 图库规模变大后，如果担心高分平台很长，可以保持 `max_results` 不变或调低。

## 7. 注意事项

- 过滤模块的输入必须是按相似度降序排列的 `SearchResult` 列表，Chroma 查询
  结果天然满足该顺序。
- 该策略只影响查询结果的截断，不影响向量库中存储的数据；模型切换后无需
  修改本策略，只需重新生成向量。
- 参数通过设置对话框保存后，下一次相似图片查询立即生效，无需重启应用。
