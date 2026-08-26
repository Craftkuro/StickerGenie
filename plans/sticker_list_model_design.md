# 贴纸列表模型设计：自维护身份索引的 StickerListModel

## 背景与问题

列表页有两处"为了定位少数几行而扫全表"的同款问题：批量编辑标签后的 DTO 更新，以及删除广播的跨视图修剪。前一份方案（`plans/batch_tag_dto_update_design.md`）选择在调用点"捕获行号 + 三重校验 + 降级扫描"，能解决问题，但三个部件分布在页面方法里，可读性代价偏高。

根因在于：两个场景都在用 **QModelIndex 这个位置性身份**去追踪数据。行号只是坐标，任何一次增删都会让坐标全体平移，于是任何基于坐标的方案都不得不防御"坐标过期"。根本性的出路不是把防御做得更精巧，而是换成不会过期的身份。

现状梳理：

- 模型是散落各处的 plain `QStandardItemModel`：`build_sticker_model`（服务层工厂，覆盖搜索/相似/高级搜索页）+ 无限集合页两处直构；
- 每行的 DTO 以共享引用挂在 `ROLE_STICKER_IMAGE`，blob 实体挂在 `ROLE_BLOB_ENTITY`；
- `StickerListView` 内部已经私养了一套 hash→row 索引（缩略图路由用），含 connect/disconnect 四信号、全量重建、增量追加等约 60 行机械；
- 页面侧 `_update_sticker_dtos`、`_prune_deleted_rows` 各自全表扫描定位行号；
- 全项目没有任何 QSortFilterProxyModel，视图与模型是裸连接。

## 关键洞察

三个事实决定了正确的设计：

1. **结构变化必经唯一漏斗**。QAbstractItemModel 的所有增删重置都通过 `begin/endInsertRows`、`begin/endRemoveRows`、`begin/endResetModel` 发出信号。在这一个漏斗上同步维护的索引，**永远反映当前真值**——过期从"需要检测的风险"变成"结构性不可能"，校验和降级扫描因此失去存在必要。
2. **DTO 是共享引用，delegate 绘制时才读数据**。"修改一行"本质上只需要改对象 + 让视图重绘，定位行号的唯一目的是发 `dataChanged`。
3. **身份键已经天然存在**：DTO 的 `id`（业务主键）和 blob 的 `hash`（缩略图路由键）。视图私养的 hash 索引证明了这个模式可行，只是放错了地方——位置信息的权威是模型，不是某个视图。

## 目标与非目标

目标：

1. 新建专门的 `StickerListModel(QStandardItemModel)`，在模型内部同步维护 id→row 与 hash→row 双索引，读取 O(K)、永不陈旧；
2. 批量标签更新的槽函数收敛为一行调用，删除广播修剪收敛为一行调用；
3. 视图私养的 hash 索引整体删除，缩略图路由改为向模型查询；
4. 保持 `dataChanged([ROLE_STICKER_IMAGE])` 的对外契约不变（现有测试有断言，未来兼容 proxy）；
5. 无限集合页跳过删除修剪，刷新下沉到服务层（承接前方案的优化二，正交且仍然成立）。

非目标：

- 不改 DTO 共享引用设计，不改 `tags_updated` / `signal_stickers_deleted` 信号契约；
- 不处理标签模型的 `QStandardItemModel`（对话框里的 tag model、搜索补全模型等，与贴纸列表无关）；
- 不做跨模型的全局索引，每个模型实例只认识自己的行。

## 备选方案与否决理由

**A. 指针身份（持有 QStandardItem 引用或 QPersistentModelIndex）**
Qt 提供的稳定身份：兄弟行增删自动跟随，自己那行被删才失效，能把三重校验简化成一次 `isValid()`。否决原因：① 只服务"手里本来就有索引"的调用点，删除广播接收方只有 id，帮不上；② PyQt 里跨删除持有 QStandardItem 有 wrapped-object-deleted 的 RuntimeError 风险；③ `indexFromItem()` 本身是 O(n)，多行修改时反而退化。

**B. 共享引用 + 免定位重绘**
利用洞察 2：改完 DTO 直接 `viewport().update()`，零索引零扫描。作为讨论底牌保留，不落地：放弃 `dataChanged` 契约会破坏现有测试断言，未来若引入排序/过滤代理会静默失效。

**C. 本方案：模型自有身份索引**。一份基础设施同时服务批量编辑定位、删除修剪、缩略图路由三处需求，且正确性由结构保证而非协议约定。

## 方案总览

```
StickerListModel(QStandardItemModel)          ← 新组件，唯一位置真值
    ├─ 内部: _row_by_id / _row_by_hash 两张 dict + 脏标记
    ├─ 结构信号自联: 追加增量维护，其余打脏，读时懒重建
    ├─ row_for_hash()      ← 缩略图路由（替代视图私有索引）
    ├─ refresh_stickers()  ← 按 id 定位改标签（替代页面全表扫描）
    └─ remove_stickers_by_ids() ← 定位+合并区段+成段移除（替代逐行删）

StickerListView                               ← 瘦身，删掉全部索引机械
StickerListPage                               ← 槽函数退化为薄解析器
```

填充模型不需要任何专用 API：`rowsInserted` 自联钩子对 `appendRow` 天然生效，`build_sticker_items` + 循环 appendRow 的现有代码原样保留。

## 详细设计

### 1. 组件位置与骨架

位置：`src/commons/sticker_list_model.py`。选 commons 而非 ui/services：服务层已依赖 `commons.dto`、`commons.roles`，反向亦然，放这里无循环导入风险，且它与 `roles.py` 同属跨层共享词汇。

```python
class StickerListModel(QStandardItemModel):
    """自维护 id→row / hash→row 身份索引的贴纸列表模型。

    行号查询永远返回当前真值：索引在本模型的结构变化漏斗上同步维护，
    不存在陈旧行号，调用方无需校验。
    """

    def __init__(self, parent=None):
        super().__init__(parent)
        self._row_by_id: dict[int, int] = {}
        self._row_by_hash: dict[str, int] = {}
        self._dirty = True
        self.rowsInserted.connect(self._on_rows_inserted)
        self.rowsRemoved.connect(self._mark_dirty)
        self.modelReset.connect(self._on_reset)

    # ---- 对外查询 ----
    def row_for_id(self, sticker_id: int) -> int | None: ...
    def row_for_hash(self, file_hash: str) -> int | None: ...

    # ---- 对外操作 ----
    def refresh_stickers(self, updated_stickers: list) -> int: ...
    def remove_stickers_by_ids(self, deleted_ids) -> int: ...

    # ---- 内部维护 ----
    def _on_rows_inserted(self, _parent, first, last): ...
    def _mark_dirty(self, *_): ...
    def _on_reset(self, *_): ...
    def _ensure_index(self): ...
    def _rebuild_index(self): ...
```

### 2. 索引维护规则（刻意最简）

- **追加插入**（`last == rowCount()-1`）：从 `[first, last]` 逐行读 role 增量写入两张 dict；
- **其余一切结构变化**（中间插入、删除、reset/clear）：只打脏标记，不做任何逐行修正；
- **唯一读入口** `_ensure_index()`：脏则全量重建并清标记。所有公开方法第一步都走它；
- 重建即现版 `_rebuild_hash_index` 的双键版：遍历行取 `ROLE_STICKER_IMAGE.id` 与 `ROLE_BLOB_ENTITY.hash`，缺哪个跳哪个；重复键后写覆盖（与现状 hash 索引语义一致）。

不做"删除时增量平移 dict 值"的理由：离散删除会让每次平移退化成 O(n)，与懒重建同阶却多一套易错逻辑；懒重建把整批删除的成本摊成一次 O(n)。符合项目"简单优先"约定。

### 3. `refresh_stickers`：批量标签更新

```python
def refresh_stickers(self, updated_stickers: list) -> int:
    """按 id 定位行，把新标签写进行上挂着的共享 DTO 并局部重绘。

    返回实际更新的行数；id 不在本模型中的条目自然跳过。
    """
    self._ensure_index()
    updated_by_id = {
        s.id: s for s in updated_stickers if getattr(s, "id", None) is not None
    }
    changed = 0
    for sticker_id, source in updated_by_id.items():
        row = self._row_by_id.get(sticker_id)
        if row is None:
            continue
        index = self.index(row, 0)
        dto = index.data(ROLE_STICKER_IMAGE)
        if dto is None:
            continue
        dto.tags = list(source.tags)
        self.dataChanged.emit(index, index, [ROLE_STICKER_IMAGE])
        changed += 1
    return changed
```

要点：

- 模型在信号到达时现查现写，模态期间发生过什么都无所谓——即使行被删过，索引也是当前真值，查不到就是不在本页，天然跳过；无限页整体换过模型的话，槽里解析到的是新模型，同样正确。前方案防"张冠李戴"的全部校验在此结构性消失；
- 写的是"该行当前挂着的 DTO"（共享引用原地改），`tags` 浅拷贝赋值，语义与现状一致；
- `dataChanged` 显式带 `[ROLE_STICKER_IMAGE]`，沿用现状契约。

### 4. `remove_stickers_by_ids`：删除广播修剪

```python
def remove_stickers_by_ids(self, deleted_ids) -> int:
    """按 id 定位并移除命中行，返回移除数。payload 含陌生 id 时为无害 no-op。"""
    self._ensure_index()
    deleted = set(deleted_ids)
    rows = sorted(
        row for sticker_id, row in self._row_by_id.items()
        if sticker_id in deleted
    )
    if not rows:
        return 0
    # 合并为最大连续区段，从大区段到小区段移除，避免行号平移。
    for first, count in _group_runs(rows):
        self.removeRows(first, count)
    return len(rows)
```

`_group_runs` 把排序后的行号聚成 `(first, length)` 列表；区段按起点降序处理。K 次 `removeRow` 变成 ≤K 次 `removeRows`，通常远少于 K（多选删除往往是连续或近似连续区段）。每次 `removeRows` 只发一次 `rowsRemoved`，而视图侧不再监听这个信号做重建——原来 K×O(n) 的隐藏级联就此消失，只剩一次延迟的懒重建。

### 5. 视图瘦身（StickerListView）

整体删除：`setModel` 重载里的索引相关段、`_connect/_disconnect_model_signals`、`_on_rows_inserted/_on_rows_removed/_on_model_reset/_on_model_data_changed` 四个处理器、`_rebuild_hash_index`、`_update_hash_index_for_inserted_rows`、`_hash_to_rows` 字段（约 60 行）。

`_on_thumbnail_ready` 改为：

```python
def _on_thumbnail_ready(self, file_hash, _image) -> None:
    row_for_hash = getattr(self.model(), "row_for_hash", None)
    if row_for_hash is None:      # plain model（debug 服务等）：无法路由，跳过
        return
    row = row_for_hash(file_hash)
    ...
```

附带收益：前方案提到的"空 roles 的 dataChanged 意外触发全量重建"这一类隐患连根拔掉——视图不再监听 dataChanged。

### 6. 页面侧薄化（StickerListPage）

两个槽退化为"解析当前模型 + 一行委托"：

```python
def _update_sticker_dtos(self, updated_stickers: list) -> None:
    model = self.listViewStickerList.model()
    if isinstance(model, StickerListModel):
        model.refresh_stickers(updated_stickers)

def _prune_deleted_rows(self, deleted_ids: list) -> None:
    model = self.listViewStickerList.model()
    if isinstance(model, StickerListModel):
        model.remove_stickers_by_ids(deleted_ids)
```

`_batch_edit_tags_for_indexes` 完全恢复原状：`tags_updated` 直连槽，无捕获表、无 lambda、无校验助手。`_delete_stickers_for_indexes` 中手动的 `slot_refresh_content()` 调用随第 7 节一并移除。

### 7. 无限集合页跳过修剪 + 刷新契约下沉

承接前方案优化二，理由不变：无限页是唯一可能几万行的接收页，但它的行移除注定被紧随其后的全量重置覆盖。

- `page_infinite_sticker_collection.py` 取消订阅 `signal_stickers_deleted`；
- `services.sticker_library_viewer_service.delete_stickers` 在 SQLite 提交成功后、发射删除广播的同时调用 `wiring.slot_refresh_content()`，把"删完必刷"从调用方自觉变成结构保证。

残留缺口明示：若未来出现"只删不刷"的新入口，快照页仍有修剪兜底不受影响，只有无限页会短暂显示幽灵条目直到下次刷新。符合项目非强一致的定位。

### 8. 采用面收口

喂给贴纸列表视图的模型创建点全项目仅三处，全部换用 `StickerListModel` 后采用即告完备：

| 创建点 | 位置 | 改动 |
| --- | --- | --- |
| `build_sticker_model` | `sticker_library_viewer_service.py:101` | 构造类名替换 |
| `_reset_and_load_first_page` | `page_infinite_sticker_collection.py:122` | 构造类名替换 |
| `_load_more` 兜底分支 | `page_infinite_sticker_collection.py:150` | 构造类名替换 |

`sticker_view_service_debug.py` 的演示模型可不换：视图经 `getattr` 优雅退化，行为是缩略图就绪不触发定向重绘，符合该服务的演示定位。漏网的新创建点会在第一次删除/更新时因 `isinstance` 不匹配静默跳过——开发期靠第 10 节的回归测试暴露，运行期无害（弱一致容忍）。

### 9. 线程模型

与现状相同：全部操作在主线程，信号直连同步派发，模型索引不存在并发访问。将来若删除挪入工作线程，PyQt 自动切队列连接，槽仍在主线程执行，本设计无需预改。

### 10. 边界情况

| 情况 | 行为 |
| --- | --- |
| payload 含本页没有的 id | 定位不到，no-op |
| 同一 id/hash 在模型中重复出现 | 后写覆盖，定位到最后出现的行（与现状 hash 索引一致；id 是主键理论上不重复）|
| 行缺 `ROLE_STICKER_IMAGE` 或 `ROLE_BLOB_ENTITY` | 该行不进对应索引，各键独立 |
| 模态期间模型被整体替换 | 槽解析到新模型，旧 id 查不到则跳过，无需感知 |
| `clear()` / `setRowCount(0)` | 走 modelReset → 打脏，下次读时重建为空 |

## 与既有方案文档的关系

本文档**取代** `plans/batch_tag_dto_update_design.md`，对应关系：

| 前方案内容 | 本文档处置 |
| --- | --- |
| §详细设计 1–3（捕获表 / 三重校验 / 快路径+降级扫描） | 被 `StickerListModel.refresh_stickers` 整体替代，正确性由结构保证 |
| 优化一（hash 索引脏标记懒重建） | 吸收进模型，升级为 id+hash 双键统一维护 |
| 优化二（无限页跳过修剪 + 刷新下沉服务层） | 原样采纳（§7） |
| "完整版同款快路径"备案 | 即本方案本身 |

前文档保留不动，仅作历史参考。

## 复杂度对比

| 场景 | 现状 | 本方案 |
| --- | --- | --- |
| 批量标签更新 | O(n) 全表扫描 | O(K) 索引定位 + 局部重绘 |
| 删除广播修剪（每接收页） | O(n) 扫描 + K×O(n) 视图级联 | O(K) 定位 + #segments 次区段移除 + 一次延迟 O(n) 重建 |
| 无限页删除 | 上述全部 + 白做功 | 零（跳过订阅），重置流程照旧 |
| 缩略图就绪路由 | O(1)，但视图私养索引随结构变化反复重建 | O(1)，重建次数大幅减少（仅真脏才重建） |
| 索引维护常开销 | 视图端已有 hash 单键重建 | 同阶（双键 ×2 字典写入，摊薄后可忽略） |

## 改动清单

| 文件 | 改动 |
| --- | --- |
| `src/commons/sticker_list_model.py` | 新增 `StickerListModel` 与 `_group_runs` 助手，约 110 行 |
| `src/services/sticker_library_viewer_service.py` | `build_sticker_model` 换构造类；`delete_stickers` 提交成功后调用 `wiring.slot_refresh_content()` |
| `src/ui/widgets/sticker_list_view_widget.py` | 删除整套 hash 索引机械（约 −60 行）；`_on_thumbnail_ready` 改查模型 |
| `src/ui/widgets/sticker_list_page.py` | `_update_sticker_dtos` / `_prune_deleted_rows` 薄化为委托；删除 `_batch_edit_tags_for_indexes` 相关捕获逻辑（若曾实现）；`_delete_stickers_for_indexes` 移除手动 `slot_refresh_content()` |
| `src/ui/page_infinite_sticker_collection.py` | 两处直构换类；取消订阅 `signal_stickers_deleted` |

预计净增约 30–60 行（不含测试）；复杂度的净变化是把分散在视图与页面里的定位知识集中为一个可单测的组件。

## 测试

新增 `tests/test_sticker_list_model.py`（标准库 unittest）：

1. **索引基本性**：构建→追加→中间插入→删除若干离散行→reset，每步后 `row_for_id` / `row_for_hash` 返回当前真值（重点：删除后行号前移的正确性）；
2. **懒重建**：连续多次删除期间无重建发生（可用重建计数桩验证），下一次读触发恰一次重建；
3. **refresh_stickers**：命中行 DTO 标签更新、发出带 `[ROLE_STICKER_IMAGE]` 的 dataChanged、陌生 id 跳过、返回计数正确；
4. **remove_stickers_by_ids**：离散行合并为最少区段（如 [1,2,3,7,9,10] → [1,3]+[7,1]+[9,2]）、行数收缩正确、空集/陌生集 no-op；
5. **模态竞争模拟**：捕获 id 后先删别的行再调 `refresh_stickers`，标签落在正确行（结构性正确，无降级分支可测）。

改造 `tests/test_sticker_list_view.py`：

6. 原针对视图 hash 索引的用例迁移到 `test_sticker_list_model.py`；视图侧保留"plain model 时 thumbnail_ready 不炸"的退化用例；
7. 页面级：批量编辑后对应行标签可见更新（沿用现有 dataChanged 断言路径）；删除广播后快照页行收缩。

改造 `tests/test_batch_tag_edit_dialog.py` 相关联动用例：确认无捕获参数、直连信号的现状交互不变。

## 测试注意

项目使用 Python 标准库 unittest（部分测试以 pytest 风格编写），依赖全部位于 `.venv` 虚拟环境，运行测试请使用 `.venv` 解释器。
