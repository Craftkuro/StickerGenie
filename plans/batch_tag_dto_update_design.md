# 列表页局部更新的性能设计：批量改标签与跨视图删除修剪

## 背景与问题

本文覆盖列表页两处"为了定位少数几行而扫全表"的同款问题：批量编辑标签后的 DTO 更新，以及删除广播的跨视图修剪。

批量编辑标签的现有流程是这样的：

1. 用户在列表页多选几张图，右键选"批量编辑标签"；
2. `_batch_edit_tags_for_indexes` 从选中行取出 DTO，交给 `BatchTagEditDialog`；
3. 对话框里点确认后写入数据库，然后把数据库导出的全新 DTO 列表通过 `tags_updated` 信号发出来；
4. 页面侧的 `_update_sticker_dtos` 收到信号，把这些新标签写回自己模型里的 DTO，并通知视图重绘。

问题出在第 4 步的实现方式：为了搞清楚"哪几行的数据变了"，它把整个模型从头到尾扫一遍（O(n)）。哪怕这次只改了 3 张图，也要摸全部的行。列表越大（比如无限集合页滚动加载后有几万行），关掉对话框之后的那一下卡顿就越明显。

有点讽刺的是，打开对话框之前我们就已经精确知道受影响的是哪几行了——这个信息被直接扔掉了。

另外有一个现成的巧合可以利用：模型里存的 DTO 和传给对话框的 DTO 是**同一批 Python 对象**（共享引用），所以"更新数据"本质上只是把新标签列表拷贝到这些对象上，不需要替换模型里的条目。

## 目标与非目标

目标：

1. 更新 DTO 时优先用已知的行号直接定位，复杂度从 O(n) 降到 O(K)（K = 本次编辑的图片数）；
2. 万一行号不可信（数据在别处被动过），自动回退到全表扫描重新定位，宁可慢一点也不能把标签写到错误的行上；
3. 不改变 `tags_updated` 信号契约，不改变 DTO 共享引用的设计。

非目标：

- 不建常驻的 id→行号 索引。全项目只有这一个调用点，而且入口处天然拿着精确行号，专门养一套索引去伺候它不值当；
- 不处理"其他快照页还显示着旧标签"的一致性瑕疵，那是现状行为，本方案不扩大也不缩小它的范围。

## 方案总览

分两条路：

1. **快路径（默认）**：打开对话框前，把选中行的"id → 行索引"记在一张对照表里。对话框关闭、信号到达后，逐条核对"这一行现在还是不是当初那张图"，是就直接原地改标签、发重绘通知。
2. **降级路径（兜底）**：只要有一条核对不通过，就把没通过的条目攒起来，对整个模型做**一次**扫描重新定位行号，然后照常应用。

按现在的交互方式，模态对话框开着的时候用户基本没机会改动这个页面背后的模型，所以走到降级路径的概率极小。但兜底必须存在：一旦真发生了（比如删除广播在模态期间修剪了行），盲用旧行号会把标签写到别的图片头上，造成视图错乱。正确性优先于性能，这是本方案的核心取舍。

## 详细设计

改动全部集中在 `src/ui/widgets/sticker_list_page.py`。

### 1. 打开对话框前捕获行号

```python
def _batch_edit_tags_for_indexes(self, indexes: list[QModelIndex]) -> None:
    stickers = [...]  # 现有逻辑不变
    if len(stickers) < 2:
        return

    # 记住 "id -> 行索引"。模态期间行可能被删除广播移除，
    # 所以信号到达后还要逐条校验，不能盲用。
    index_by_id: dict[int, QModelIndex] = {}
    for index in indexes:
        if not index.isValid():
            continue
        sticker = index.data(ROLE_STICKER_IMAGE)
        if sticker is not None and getattr(sticker, "id", None) is not None:
            index_by_id[sticker.id] = index

    try:
        dialog = BatchTagEditDialog(stickers, parent=self)
    except RuntimeError as exc:
        QMessageBox.warning(self, "无法打开", str(exc))
        return

    dialog.tags_updated.connect(
        lambda updated: self._update_sticker_dtos(updated, index_by_id)
    )
    dialog.exec()
```

### 2. 校验规则：为什么不能只看 isValid()

Qt 的普通 `QModelIndex` 有个反直觉的坑：它指向的行被删掉之后，`isValid()` **依然返回 True**——这个标记只在创建那一刻赋值，之后不跟踪模型的任何变化。项目里已经有注释踩过这个坑（`_open_image_viewer_for_index` 里的"陈旧索引 isValid() 仍为真"）。

所以光检查 `isValid()` 远远不够，完整的校验要做三件事：

1. 对照表里有这个 id 对应的索引，且索引对象本身有效；
2. `index.row()` 小于当前模型行数（防删除后越界）；
3. 该位置当前的 DTO 的 id 等于期望的 id（防行平移后张冠李戴）。

第 3 条是灵魂所在。如果模态期间有行被修剪，后面的行号会整体前移，旧行号就可能指向另一张完全无关的图；不做 id 比对就把标签写上去，等于替别人改了资料——这正是要防住的视图不一致问题。

```python
def _index_still_holds(self, index, sticker_id: int, model) -> bool:
    """校验捕获的行索引现在是否仍指向同一张图。"""
    if index is None or not index.isValid():
        return False
    if index.row() >= model.rowCount():
        return False
    sticker = index.data(ROLE_STICKER_IMAGE)
    return sticker is not None and getattr(sticker, "id", None) == sticker_id
```

### 3. 应用与降级

```python
def _update_sticker_dtos(
    self,
    updated_stickers: list,
    index_by_id: dict[int, QModelIndex] | None = None,
) -> None:
    """更新当前模型中 DTO 的标签，同时保留 DTO 对象引用。

    带 index_by_id 时走快路径：按捕获的行号定位，逐条校验后再写；
    校验不过的条目攒起来，最后对全表做一次扫描补定位。
    """
    updated_by_id = {
        sticker.id: sticker
        for sticker in updated_stickers
        if getattr(sticker, "id", None) is not None
    }
    model = self.listViewStickerList.model()
    if model is None or not updated_by_id:
        return

    pending = updated_by_id
    if index_by_id:
        pending = {}
        for sticker_id, updated in updated_by_id.items():
            index = index_by_id.get(sticker_id)
            if self._index_still_holds(index, sticker_id, model):
                self._apply_updated_tags(model, index, updated)
            else:
                pending[sticker_id] = updated

    if not pending:
        return
    # 降级路径：全表只扫一遍，给没通过校验的条目找回准确行号。
    for row in range(model.rowCount()):
        index = model.index(row, 0)
        sticker = index.data(ROLE_STICKER_IMAGE)
        if sticker is None:
            continue
        updated = pending.get(getattr(sticker, "id", None))
        if updated is not None:
            self._apply_updated_tags(model, index, updated)


def _apply_updated_tags(self, model, index: QModelIndex, updated) -> None:
    """把新标签写进该行的共享 DTO，并通知视图只重绘这一格。"""
    sticker = index.data(ROLE_STICKER_IMAGE)
    if sticker is None:
        return
    sticker.tags = list(updated.tags)
    model.dataChanged.emit(index, index, [ROLE_STICKER_IMAGE])
```

几个实现要点：

- **降级扫描只跑一次**，不管有几条没通过校验。最坏情况也就是回到今天的 O(n)，不会更糟；
- 写标签时读的是"该行当前挂着的 DTO"而不是捕获时留存的那个引用。两者在校验通过时本来就是同一个对象（共享引用），但从模型现读一份更稳妥，也让降级路径天然正确；
- `tags` 用浅拷贝赋值（`list(updated.tags)`），保持现有的共享语义不变；
- `dataChanged` 显式带上 `[ROLE_STICKER_IMAGE]` roles。这一点沿用现状：视图侧的 hash 索引处理器看到这个 roles 会直接早退，不会触发缩略图索引重建，两套机制互不打扰；
- 逐行发 `dataChanged` 看着多，实际开销很小：Qt 会把多次局部重绘请求合并成一次绘制，而且 K 通常就是个位数。

### 4. 兼容性

`index_by_id` 是可选参数。将来如果出现新的调用方没有现成行号可给（直接连信号的老式用法），函数会跳过快路径、直接走全表扫描，行为与今天完全一致。

## 复杂度对比

| 场景 | 现状 | 新方案 |
| --- | --- | --- |
| 正常批量编辑（绝大多数情况） | O(n)，n 为模型总行数 | O(K)，与列表大小无关 |
| 极少数：模态期间模型被动过 | O(n)，且结果碰巧正确 | 一次 O(n) 扫描兜底，结果确定正确 |
| 无行号提示的新调用方 | O(n) | O(n)（同现状） |

## 扩展：删除广播的跨视图修剪（第二处同款问题）

### 现状回顾

删除图片的跨页同步机制（详见 `plans/sticker_deletion_sync_design.md`）：任一页面删除图片后，服务层广播 `signal_stickers_deleted(list[int])`，每个打开的列表页用 `_prune_deleted_rows` 全表扫描找出命中行、从大到小逐行移除；图库审阅对话框则用同一信号修剪浏览历史。

一次删除的真实开销由三笔叠加，比表面看到的更贵：

| 开销 | 复杂度（每接收页） | 说明 |
| --- | --- | --- |
| 找行扫描 | O(n) | 全表逐行读 DTO 比对 id |
| 隐藏的索引重建级联 | K × O(n) | 每次 `removeRow` 都同步触发视图的 `rowsRemoved` → hash 索引**全量重建**。删 20 张、列表 5 万行，就是上百万次字典写入级别的隐藏开销，比可见的扫描更贵 |
| 无限集合页的白做功 | 上述全部 + 紧跟的全量重置 | 唯一的删除入口 `_delete_stickers_for_indexes` 删完后固定调用 `slot_refresh_content()`，无限集合页随即整体换模型——刚做完的扫描和逐行移除全部作废 |

而真正需要修剪的快照页其实都不大：相似图片页封顶 `SIMILAR_IMAGE_MAX_RESULTS = 100` 条，搜索结果规模可控。

### 为什么"捕获已知行号"不能直接照搬

批量编辑场景里位置信息是免费的——页面自己打开对话框，手里就攥着选中行的索引。删除场景不一样：

1. 发起方确实有精确索引，但**行号跨模型无意义**：每页模型的内容和顺序都不同，A 页的第 42 行和 B 页的第 42 行毫无关系，广播过去谁也用不上；
2. 接收方拿到的只有 id。想不扫描就定位行号，只能养一套常驻 id→行号 索引——正是批量编辑方案里明确不做的那个东西；
3. 唯一的大模型（无限集合页）扫完就整体重置，纯浪费；其余小页面扫描本来就便宜。

结论：不为删除场景建常驻索引，但值得消掉两笔纯浪费。

### 优化一（推荐）：hash 索引改脏标记懒重建

`StickerListView` 的 hash 索引目前是"结构一变就立即全量重建"。改成打脏标记、下次真正要用时再重建：

```python
# 结构性变化处（rowsRemoved / 中间插入 / modelReset / 空 roles 的 dataChanged）：
self._hash_index_dirty = True

# 唯一读入口：
def _row_for_hash(self, file_hash: str):
    if self._hash_index_dirty:
        self._rebuild_hash_index()
        self._hash_index_dirty = False
    return self._hash_to_rows.get(file_hash)
```

效果：

- 删除 K 行的隐藏级联从 K×O(n) 变成约等于零——整批移除期间没人查缩略图路由，脏标记先攒着，等下一次 `thumbnail_ready` 才补一次重建；
- 追加行（load more）仍走现有增量写入，不打脏；
- 顺带把批量编辑章节提到的"空 roles dataChanged 触发全量重建"一并化解——打个脏标记即可，连局部重索引都省了；
- 注意所有读方必须走 `_row_for_hash` 这一个口子（当前读方只有 `_on_thumbnail_ready` 一处），绕过它直接摸 `_hash_to_rows` 会读到过期数据。

### 优化二（推荐）：无限集合页跳过修剪

无限集合页是唯一可能几万行的接收页，但它的行移除注定被紧随其后的全量重置覆盖。让它不再订阅 `signal_stickers_deleted` 即可，行的消失由重置完成。两次操作之间没有事件循环重入，不会出现闪烁或幽灵条目。

前提是"删除必刷新"这个契约可靠。现状它靠调用方自觉：唯一的删除入口在删完后手动调 `slot_refresh_content()`。建议顺手把这次调用下沉到服务层 `delete_stickers` 提交成功之后，与 `signal_stickers_deleted` 并排发出——契约从"约定"变成"结构保证"，将来新增删除入口也不会漏。

残留缺口明示：若未来出现"只删不刷"的新入口，快照页仍有修剪兜底不受影响，只有无限页会短暂显示幽灵条目直到下次刷新。符合项目非强一致的定位。

### 如果将来要做完整版"同款"快路径（仅备案，不实施）

设计备查：视图在现有重建循环里同时维护 id→row 双键索引并暴露 `rows_for_ids(ids)`；`_prune_deleted_rows` 改成"查索引 → 校验该行 DTO.id 确实属于删除集合 → 从大到小移除；任何校验不过 → 一次全表扫描兜底"，与批量编辑方案完全同构。代价是每次重建多一倍字典写入、信号消费面变大。在相似图片页封顶 100 条的现状下预期收益接近零，故只留档备查。

## 改动清单

| 文件 | 改动 |
| --- | --- |
| `src/ui/widgets/sticker_list_page.py` | `_batch_edit_tags_for_indexes` 捕获 id→索引映射并传入槽；`_update_sticker_dtos` 重写为"快路径 + 一次性降级扫描"；新增 `_index_still_holds`、`_apply_updated_tags` 两个小助手；`_delete_stickers_for_indexes` 移除手动的 `slot_refresh_content()` 调用（下沉后由服务层保证） |
| `src/ui/widgets/sticker_list_view_widget.py` | hash 索引改脏标记懒重建，统一读入口 `_row_for_hash`；结构性变化的处理从立即重建改为打脏 |
| `src/ui/page_infinite_sticker_collection.py` | 取消订阅 `signal_stickers_deleted` |
| `src/services/sticker_library_viewer_service.py` | `delete_stickers` 提交成功后调用 `slot_refresh_content()`，与删除广播并排发出 |

预计净增约 70 行，不含测试。

## 测试

挂进现有的 `tests/test_batch_tag_edit_dialog.py` 和 `tests/test_sticker_list_view.py`：

1. **快路径行为不变**：多选编辑后，对应行的 DTO 标签更新、发出带 `[ROLE_STICKER_IMAGE]` roles 的 dataChanged；
2. **降级路径正确性**：构造"捕获索引之后删掉一行"的场景（模拟模态期间的删除广播），验证受影响的条目最终写到**正确**的行上，且没有误伤别的行；其余条目仍走快路径；
3. **张冠李戴防护**：让捕获的行号指向另一张图，验证那条标签不会被写错位置，而是经全表扫描落到正确行；
4. **无提示兼容**：不带 `index_by_id` 直接触发 `tags_updated`，行为与现状一致（全表扫描）；
5. **懒重建正确性**：删除若干行后（索引处于脏状态），下一次 `thumbnail_ready` 仍能路由到正确行并完成重建；
6. **无限页跳过修剪**：删除后无限页不执行扫描，模型由重置流程整体更换、内容正确；
7. **刷新契约下沉**：调用服务层 `delete_stickers` 后，`signal_refresh_library_content` 必然发出（无论从哪个页面发起）。

## 测试注意

项目使用 Python 标准库 unittest（部分测试以 pytest 风格编写），依赖全部位于 `.venv` 虚拟环境，运行测试请使用 `.venv` 解释器。
