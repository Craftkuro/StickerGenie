# 图片删除跨页同步设计

## 背景与问题

删除图片只影响发起删除的那个标签页：该页从自己的模型里移除行，其他标签页（搜索结果、相似图片、高级搜索）和对话框（图库审阅）对此毫不知情，继续展示已删除的图片。用户可能对这些"幽灵条目"执行复制、另存为、编辑标签、后退导航等操作。

实测程序不会崩溃，但会出现空白图片、报错提示或卡死的导航状态，属于一致性缺陷而非稳定性缺陷。

现状梳理：

- UI 层删除只有一个入口：`StickerListPage._delete_stickers_for_indexes` → `services.sticker_library_viewer_service.delete_stickers` → `db.delete_stickers`。
- 服务层删除完成后调用 `wiring.slot_refresh_content()` 广播无参信号 `signal_refresh_library_content`，但全项目只有全库浏览页 `InfiniteStickerCollectionPage` 订阅它。
- 三个结果页（搜索/高级搜索/相似图片）以 `auto_refresh=False` 创建，是快照语义，从不刷新。
- 图库审阅对话框维护内存浏览历史 `_history: list[int]` 与 `_position` 指针；命中已删 id 时 `_go_back` 先减指针再发现图片不存在，记 warning 后 return，导航卡死在死条目上。
- 审阅对话框虽然是应用级模态，但它内嵌了 `SimilarImagesPage`（继承 `StickerListPage`），在窗格里右键删除完全可行——"审阅开着时发生删除"是主要发生路径之一。

## 目标与非目标

目标：

1. 任一页面删除图片后，所有打开的标签页、审阅对话框在无需用户手动刷新的情况下移除对应条目；
2. 审阅历史的顺序语义保持不变（摘除被删元素，剩余元素按原序拼接），位置指针正确调整；
3. 大数据量下不引入可感知的 UI 卡顿；
4. 不改变 SQLite 主记录 / 向量库与 Blob 可重建的项目完整性约定。

非目标：

- 不处理"恢复备份覆盖图库""维护清理孤儿文件"等非删除入口造成的数据变化（继续由现有无参 refresh 信号与读取时兜底覆盖）;
- 不做跨进程同步；
- 不重构结果页的快照语义为实时查询。

## 方案总览

采用**携带删除 id 的广播信号 + 各端本地修剪**：

1. `Wiring` 单例新增 `signal_stickers_deleted = pyqtSignal(list)`（payload 为 `list[int]`）；
2. 服务层 `delete_stickers` 在 SQLite 提交成功后立即发射，向量/Blob 清理之前；
3. 基类 `StickerListPage` 统一订阅，按 payload 从自身模型移除命中行——一处实现，现有与未来的列表页自动受益；
4. `LibraryAuditingDialog` 额外订阅，对 `_history` 做"过滤拼接 + 指针调整"；
5. 读取路径保留轻量兜底（pixmap 加载失败给出明确提示）。

不选"修剪前反查数据库"方案的原因：被删 id 在删除点本来就在手上，反查纯属绕路；且真实成本不在 SQLite 主键 IN 查询本身，而在 SQLAlchemy 编译上万绑定参数（超过 SQLite 变量数上限还要分批），全程同步跑在 UI 线程，万行级模型的页会卡。

## 详细设计

### 1. 信号定义（Wiring）

位置：`src/services/sticker_library_viewer_service.py` 的 `Wiring` 类。

```python
signal_stickers_deleted = pyqtSignal(list)   # payload: list[int] 已删除的 sticker id
```

放在 `Wiring` 而不是 `StickerListPage` 上：Qt 信号定义在类上是实例级的，页面实例之间无法互相广播；`Wiring` 是模块级 QObject 单例，正是为跨页通知而生，现有 `signal_refresh_library_content` 即先例。

### 2. 发射时机与服务层改动

位置：`sticker_library_viewer_service.delete_stickers`。

- 时序约束：`db.delete_stickers()` 返回（事务已提交）后**立刻** emit，然后才做向量库与 Blob 清理。理由：按项目约定 SQLite 是主记录，即使向量/Blob 清理报错（函数以 `cleanup_errors` 形式返回），UI 行也必须消失；反过来，若清理失败导致不发信号，会出现数据库已删而界面仍在的更糟状态。
- payload 直接取入参 DTO 列表的 `[s.id for s in stickers]`，包括数据库层因"id 不存在"跳过的项也无妨（下游按集合匹配，天然幂等）。
- 发射代码放在服务层而非 DB 层，保持 `stickerdb` 包不依赖 Qt。

### 3. 基类订阅与修剪（StickerListPage）

`__init__` 中无条件连接（不受 `auto_refresh` 影响——修剪是"去掉库里已没有的"，对快照页也是安全语义，不同于全量刷新）：

```python
wiring.signal_stickers_deleted.connect(self._prune_deleted_rows)
```

槽的实现要点：

1. `deleted = set(ids)`；空集直接返回；
2. 单遍扫描当前模型所有行，取 `ROLE_STICKER_IMAGE.id`，命中则收集行号；
3. 无命中直接返回（大多数页面的大多数删除是无害 no-op）;
4. 收集到的行号**从大到小**逐个 `model.removeRow(row)`，避免行号失效。

复杂度：O(模型行数) 的纯 Python 扫描 + O(实际删掉的行数) 的行移除，无任何 IO。万行级模型的扫描在毫秒量级，远低于一次巨型 IN 查询的编译开销。

### 4. 删除入口去重

`_delete_stickers_for_indexes` 里现有的手动逐行 `removeRow` 逻辑成为冗余——发射信号后基类槽会把本页的行一并剪掉。删除该段，让信号成为唯一的行移除机制，避免同一批行被两套逻辑各自处理。

### 5. 审阅历史修剪（LibraryAuditingDialog）

新增槽 `_prune_history(deleted_ids)` 并在 `__init__` 连接：

1. 快速 no-op：`set(ids) & set(self._history)` 为空则返回；
2. 拼接：`kept = [i for i in self._history if i not in deleted]`，剩余元素天然保序；
3. 指针调整，分两种情况：
   - **当前条目幸存**：`_position -= (被删且原下标 < _position 的元素个数)`；
   - **当前条目被删**（正在显示的图被删）：落到最近的幸存前驱，即新列表下标 `min(_position - 移除数, len(kept) - 1)` 处，用 `get_stickers_by_ids([target_id])` 取单条并直接调 `_show_sticker` 刷新画面。这是唯一需要查库的场景：单个主键查询、仅发生在罕见情况，性能可忽略。若历史被删空，重置 `_position = -1` 并跳一张随机图（`random_sticker_id()`），空库则停在空白态。
4. 注意落向前驱时**不要**走 `_navigate_to`——它会截断前进分支再入栈；应先设好 `_position` 再直接 `_show_sticker`。

顺带修复既有隐患：`_go_back` 目前先减指针再做存在性检查，失败时指针停在死条目上。修剪机制上线后该分支基本不可达，但仍建议把检查提前到移动指针之前，防御未来新的失效来源。

### 6. 线程模型

当前删除全程在主线程（右键菜单处理器内），信号以直连方式同步派发，各订阅者顺序执行，无并发问题。若将来删除挪进工作线程，PyQt 对跨线程信号自动改用队列连接，槽仍会在主线程执行，设计不需要预先改动。

### 7. 读取路径兜底

信号机制只保证"经删除入口消失"的数据一致。作为最后防线：

- `ImageViewerDialog.load_image` 与 `_open_image_viewer_for_index` 在 `QPixmap.isNull()` 或 blob 文件缺失时，弹"图片已被删除或文件丢失"的明确提示替代静默空白，并顺手把对应行从模型移除；
- 操作类动作（复制/另存为/编辑标签）已有 try/except 报错提示，保持不动。

### 8. 残留缺口（明示）

| 场景 | 覆盖方式 |
| --- | --- |
| 经删除入口消失 | 本方案的信号 + 修剪 |
| 恢复备份覆盖图库 | 现有无参 refresh 信号重置全库页；结果页靠兜底提示 |
| 维护清理孤儿 Blob 文件 | DB 记录未动，列表本就有效；查看器兜底提示 |

符合项目"数据完整性要求不高、优先简单设计"的约定。

## 改动清单

| 文件 | 改动 |
| --- | --- |
| `src/services/sticker_library_viewer_service.py` | `Wiring` 新增信号；`delete_stickers` 提交后发射 |
| `src/ui/widgets/sticker_list_page.py` | 基类订阅 + `_prune_deleted_rows`；移除删除入口的手动 removeRow |
| `src/ui/dialog_library_auditing.py` | 订阅 + `_prune_history`；调整 `_go_back` 检查次序 |
| `src/ui/dialog_image_viewer.py` | pixmap 失败时的明确提示（可选） |

预计总量约 60–80 行，不含测试。

## 测试

挂进现有 `tests/test_sticker_list_view.py`、`tests/test_dialog_library_auditing.py` 等：

- 两页同开，A 页删除后 B 页模型收缩、A 页恰好移除对应行；
- 批量删除后所有存活页行数一致；payload 含各页没有的 id 时为无害 no-op;
- 审阅历史：删中段元素保序、指针左移正确；删当前元素自动落前驱并刷新画面；删光历史进入空态/随机；
- 打开已删图片出提示而非静默空白。

## 测试注意

项目使用 Python 标准库 unittest（部分测试以 pytest 风格编写），依赖全部位于 `.venv` 虚拟环境，运行测试请使用 `.venv` 解释器。
