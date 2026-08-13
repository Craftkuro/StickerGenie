# 按 hash 定位视口中图片：设计文档

- 日期：2026-08-13
- 状态：已实现（2026-08-13）
- 关联：[library_scroll_performance_report.md](./library_scroll_performance_report.md) 第 7.2-5 条
- 改动范围：只改 `src/ui/widgets/sticker_list_view_widget.py`；不动缩略图服务、缓存、模型构建与各页面。

## 1. 背景与目标

性能报告中，`_on_thumbnail_ready` 在每次缩略图就绪信号到达时，都要从视口首行扫描到末行（高分屏下约 2000 行）来找匹配的 `BlobFileEntity.hash`，累计约 1931 万次 `index.data` 调用。

目标：改为 **hash -> 行号** 的直接定位，让 `_on_thumbnail_ready` 变成 O(1) 查询，消除逐行扫描。

## 2. 方案概览

在 `StickerListView` 内部维护一张 `dict[str, int]`（hash -> row），跟随模型的增删改保持同步：

- `thumbnail_ready` 到达时，先查表拿到行号，再只重绘该行。
- 模型是唯一的，索引放在视图内部即可，不需要改模型、provider 或 service。

## 3. 数据结构

```python
# 在 StickerListView.__init__ 中初始化
self._hash_to_rows: dict[str, int] = {}
```

使用 `int` 行号而不是 `QPersistentModelIndex` 的理由：

- 列表是扁平模型（无树形结构），行号足够。
- 行号比 `QPersistentModelIndex` 更轻量、更易调试。
- DB 中 `hash` 有唯一约束（`sticker_images.hash unique=True`），同一模型内一个 hash 最多对应一行。
  - 若未来允许重复，只需把值类型改成 `list[int]`，查询处循环一次即可。

## 4. 索引维护时机

在 `setModel` 中统一连接/断开模型信号，保证索引永远跟随当前模型：

| 事件 | 处理 | 说明 |
| --- | --- | --- |
| `setModel` | 断开旧模型信号，连接新模型信号，全量重建 | 一次 O(n) |
| `rowsInserted`（追加） | 只为新插入的行补索引 | 每页 100 行，O(100) |
| `rowsInserted`（中间插入） | 全量重建 | 当前业务不会发生，兜底 |
| `rowsRemoved` | 全量重建 | 删除图片场景，发生频率低 |
| `modelReset` | 全量重建 | 兜底 |
| `dataChanged` | 若影响 `ROLE_BLOB_ENTITY` 则全量重建 | 当前业务不会发生，兜底 |

核心方法：

```python
def _rebuild_hash_index(self) -> None:
    """全量重建 hash -> row 映射。"""
    self._hash_to_rows.clear()
    model = self.model()
    if model is None:
        return
    for row in range(model.rowCount()):
        blob_entity = model.index(row, 0).data(ROLE_BLOB_ENTITY)
        if blob_entity is not None:
            self._hash_to_rows[blob_entity.hash] = row

def _update_hash_index_for_inserted_rows(self, first: int, last: int) -> None:
    """rowsInserted 后增量更新；非追加场景直接全量重建。"""
    model = self.model()
    if model is None:
        return
    if last != model.rowCount() - 1:
        # 中间插入会让后续行号整体 +1，重建比逐行修正更不易出错。
        self._rebuild_hash_index()
        return
    for row in range(first, last + 1):
        blob_entity = model.index(row, 0).data(ROLE_BLOB_ENTITY)
        if blob_entity is not None:
            self._hash_to_rows[blob_entity.hash] = row
```

## 5. `_on_thumbnail_ready` 改造

保持“只重绘可见匹配行”的现有语义，但查找改为查表：

```python
def _on_thumbnail_ready(self, file_hash, _image) -> None:
    row = self._hash_to_rows.get(file_hash)
    if row is None:
        return

    start_row, end_row = self._visible_row_range()
    if start_row <= row <= end_row:
        self._update_item(self.model().index(row, 0))
```

`_visible_row_range` 就是把现在 `_on_thumbnail_ready` 里“用 `indexAt` 求首尾可见行”的逻辑原样抽出来，行为不变：

```python
def _visible_row_range(self) -> tuple[int, int]:
    """返回当前视口覆盖的行区间 [start, end]；视口未布局时返回全表区间。"""
    model = self.model()
    if model is None or model.rowCount() <= 0:
        return 0, -1
    row_count = model.rowCount()
    start_row = 0
    end_row = row_count - 1
    first_index = self.indexAt(self.viewport().rect().topLeft())
    last_index = self.indexAt(self.viewport().rect().bottomRight())
    if first_index.isValid():
        start_row = first_index.row()
    if last_index.isValid():
        end_row = min(end_row, last_index.row())
    return start_row, end_row
```

## 6. 边界情况

- 视口尚未布局（例如测试中 view 未 show）：`indexAt` 返回无效索引，`_visible_row_range` 回退到全表区间，仍会更新匹配行，与现状一致。
- 缩略图就绪时模型尚未设置：`_hash_to_rows` 为空，直接返回。
- hash 对应的行已滚出视口：查表有值但不在区间内，不更新；滚回视口时 paint 会从缓存取图。
- 删除行后行号变化：`rowsRemoved` 后全量重建，索引不会残留旧行号。
- 多标签页：每个 `StickerListView` 各自维护自己的映射，互不影响；缩略图 provider 仍是全局共享。

## 7. 复杂度与内存

- 就绪信号处理：`dict` 查询 O(1) + 区间判断 O(1)，不再依赖视口行数。
- 增量追加：每页 O(page_size)，约 100 次 `index.data`。
- 全量重建：O(n)，只在打开标签页、删除、reset 等低频场景发生。
- 内存：约 n 个 hash -> int 条目（14k 行时远小于 1MB），可忽略。

## 8. 为什么不用其他方案

| 方案 | 不采用原因 |
| --- | --- |
| 每次信号全量重建索引 | 每个就绪信号 O(n)，退化回线性扫描，只是把扫描提前 |
| 只维护“可见区”映射，滚动时增删 | 需要在 paint/滚动回调里维护集合，边界多、易漏，复杂度反而更高 |
| 就绪后整视口重绘 | 仍会对约 2000 个可见 item 走一遍 paint/index.data，性能问题依旧 |
| 用 `QPersistentModelIndex` 存映射 | 比行号重，且插入/删除时仍需自己维护集合，收益不大 |

## 9. 验证与回归

实现记录（2026-08-13）：已按本设计修改 src/ui/widgets/sticker_list_view_widget.py，并在 	ests/test_sticker_list_view.py 补充 3 个测试；	ests.test_sticker_list_view 共 37 个测试全部通过。

- 现有测试 `tests/test_sticker_list_view.py` 中 `test_view_repaints_matching_item_when_thumbnail_ready` 与 `test_view_ignores_thumbnail_ready_for_absent_hash` 应保持通过。
- 新增测试建议：
  1. 模型插入多行后，对某可见行触发 ready，只更新该行；
  2. 对已滚出视口的行触发 ready，不更新；
  3. 追加行后触发 ready，能通过新行 hash 更新；
  4. 删除行后触发 ready，不会引用已删除行号。
- 性能回归按报告第 8 节命令复测：
  `.venv\Scripts\python.exe experiments\profile_library_scroll.py --pages 60 --profile --profile-jobs --thumbnails build\profile_thumbnails`
  重点看 `index.data` 调用次数与 `_on_thumbnail_ready` 耗时是否下降。