# 图库审阅对话框设计与实施计划

## 文档状态

- 状态：设计定稿，等待实施
- 基线日期：2026-08-23
- 目标范围：主菜单改名、`LibraryAuditingDialog` 新对话框、StickerDBV1 两个 id 查询、相关测试
- 核心原则：id 的有效性、稀疏性、回绕等存储层知识全部收敛在数据库层；UI 只表达导航意图，不做重试和存在性探测

## 需求背景

主菜单"图库"下已有占位 action `actionStartLibraryAudit`（现文案"开始图库审计"）。本任务把它改名为"开始图库审阅"，并实现对应对话框：

- 左侧是图片查看器（参考 `ImageViewerDialog` 的业务逻辑，但独立实现，不继承）；
- 右侧是相似图片窗格（继承 `SimilarImagesPage`），默认隐藏；
- 提供基于浏览历史的"后退"、随机跳转"随机"、顺序跳转"前进"三种导航；
- 编辑属性（文件属性的编辑功能）本次不做。

用户已确认的决策：

1. 对话框打开时初始加载**随机一张**。
2. "前进"到达末尾后**回绕到最小 id**。
3. 底部标签/文本 tab 与图片查看器一致（可编辑、可保存），文件属性只读；"编辑属性"按钮为未来文件属性编辑预留，本次保留但不连接。
4. 工具栏望远镜按钮 `pushButtonStartExamine` 改名为 `pushButtonLibraryAuditing` 并接入同一对话框。

## 当前实现基线

- `src/ui/main_window.ui`：
  - `actionStartLibraryAudit` 文案为"开始图库审计"，挂在 `menu_2`（图库菜单）；
  - `pushButtonStartExamine` 是统一搜索栏上的图标按钮，toolTip"图库审计"，目前无任何代码连接。
- `src/ui/main_window.py::setup_base_slots()` 集中接线所有 action；`open_tag_manager()` 演示了"数据库未初始化则警告并拒绝打开"的模式。
- `src/ui/dialog_image_viewer.py::ImageViewerDialog`：GIF 用 `QMovie` + `PanZoomImageView.set_movie()`，其余用 `set_image(QPixmap)`；`closeEvent` 停止 movie；底部三个 tab（标签/文本/文件属性）的初始化与填充逻辑可直接参考。
- `src/ui/widgets/pan_zoom_image_view.py::PanZoomImageView`：滚轮缩放、拖拽平移、双击复位，`set_image` / `set_movie` 即插即用。
- `src/ui/page_similar_images.py::SimilarImagesPage(FiniteStickerCollectionPage)`：`set_similar_data(search_results, sticker_map)` 缓存原始向量查询结果，`apply_filter_and_refresh()` 从缓存重建模型；自带过滤弹窗和显示大小滑块。
- `src/services/sticker_library_viewer_service.py`：
  - `fetch_similar_candidates(sticker)` 返回 `(search_results, sticker_map)`，持 `vector_store_lock` 查询 Chroma；
  - 无向量的图片抛 `ValueError("该图片还没有特征向量。")`。
- `src/stickerdb/v1/sticker_db.py`：现有查询接口没有 min/max id、随机 id 或"下一个 id"；`get_stickers_by_ids` 保持输入顺序返回 DTO 列表。
- 图片文件路径由 `BlobFileEntity(sticker.hash, sticker.extension)` + `current_blob_storage.read_file()` 解析（见 `build_sticker_items`）。
- `tests/test_main_window_main_menu.py:97` 断言菜单含"开始图库审计"，改名后必须同步更新。

## 目标

- 菜单文案与 toolTip 改名为"图库审阅"系。
- 新对话框提供：左上三个导航按钮 + 信息 Label、左侧大图查看（含 GIF 动画）、右侧可开关的相似图片窗格、底部标签/文本/文件属性三 tab。
- 导航语义完全正确：任意删除造成的 id 空洞、单图库、空库都有确定行为，无重试循环。
- 相似图片懒加载：窗格不可见时不做向量查询；从不可见变可见时刷新当前内容。
- unittest 覆盖导航逻辑、惰性加载时机、按钮文案切换与菜单改名。

## 非目标

- 不实现"编辑属性"按钮的功能（文件属性编辑），按钮保留原样。
- 不实现历史的前进方向（只有后退）。
- 不做跨会话的记忆（上次浏览位置等）。
- 不修改 `SimilarImagesPage` 本身的行为。
- 不考虑导入/维护与本对话框的数据一致性（读多写少，标签/文本保存走既有原子接口）。

## 一、改名与主窗口接线

### main_window.ui

| 对象 | 改动 |
|---|---|
| `actionStartLibraryAudit` | text："开始图库审计" → "开始图库审阅" |
| `pushButtonStartExamine` | objectName 改为 `pushButtonLibraryAuditing`；toolTip "图库审计" → "图库审阅" |

objectName 改名安全：全库检索确认无 Python 代码引用 `pushButtonStartExamine`。

### main_window.py

`setup_base_slots()` 增加：

```python
self.actionStartLibraryAudit.triggered.connect(self.open_library_auditing)
self.pushButtonLibraryAuditing.clicked.connect(self.open_library_auditing)
```

```python
def open_library_auditing(self):
    database = services.global_instances.current_library_db
    if database is None:
        QMessageBox.warning(self, "无法打开", "仓库数据库尚未初始化。")
        return
    if database.random_sticker_id() is None:
        QMessageBox.warning(self, "无法打开", "图库中没有图片。")
        return
    LibraryAuditingDialog(self, database=database).exec()
```

### tests/test_main_window_main_menu.py

第 97 行期望值 "开始图库审计" → "开始图库审阅"。其余断言不受影响（objectName 不在检查之列）。

## 二、数据库层：两个语义完整的查询

`src/stickerdb/v1/sticker_db.py` 查询接口区新增：

```python
def random_sticker_id(self, *, excluding: int | None = None) -> int | None:
    """随机返回一个存在的图片 id；可指定排除某个 id。

    在真实存在的行上均匀采样（ORDER BY RANDOM()），天然无视删除造成的 id 空洞。
    空库、或排除后无剩余图片时返回 None。
    """
    # SELECT id FROM sticker_image
    #   [WHERE id != :excluding]
    #   ORDER BY RANDOM() LIMIT 1

def next_sticker_id(self, after_id: int) -> int | None:
    """返回 after_id 之后的下一个存在 id；已是最大 id 时回绕到最小 id。空库返回 None。"""
    # 单条往返同时取两个候选：
    # SELECT MIN(CASE WHEN id > :after THEN id END), MIN(id) FROM sticker_image
    # 取第一个值，为 NULL 时取第二个值（回绕）
```

设计要点：

- **随机**：`ORDER BY RANDOM() LIMIT 1` 只扫 id 一列，人工点击频率下毫秒级，彻底消除 randint 命中空洞重摇、单图库死循环等问题。"排除当前张"用 SQL 表达，返回 None 即"没有别的图"。
- **前进**：`MIN(id) WHERE id > ?` 就是"+1 找不到就 +2，以此类推"的集合论表达；回绕行为按用户决策收敛进本方法，UI 不感知。
- 不再需要 `get_sticker_id_range()` 之类的范围查询。

边界语义：

| 场景 | `random_sticker_id(excluding=c)` | `next_sticker_id(c)` |
|---|---|---|
| 空库 | None（打开前已拦截） | None |
| 仅剩当前这一张 | None → UI 静默 no-op | 回绕到自己 → UI 不追加历史 |
| 正常 | 与当前不同的有效 id | 下一个有效 id 或回绕首 id |

## 三、新对话框 `src/ui/dialog_library_auditing.py`

类名 `LibraryAuditingDialog(QDialog)`，加载用户已绘制的 `ui/dialog_library_auditing.ui`。独立实现，不继承 `ImageViewerDialog`。

### 状态

```python
self._database            # 注入或取 global_instances.current_library_db
self._sticker: StickerImage | None
self._movie: QMovie | None
self._file_path: str | None
self._history: list[int]  # 浏览过的 id 序列
self._position: int       # 当前在 _history 中的下标
self._similar_page: SimilarImagesPage | None   # 惰性创建
self._similar_stale: bool = True               # 相似列表是否落后于当前图片
```

### 左侧查看器（复刻 ImageViewerDialog 核心）

- `_show_sticker(sticker)`：
  1. `BlobFileEntity(sticker.hash, sticker.extension)` + `current_blob_storage.read_file()` 得到路径；FileNotFoundError 时警告并保持现状；
  2. GIF → 新建 `QMovie` 接 `graphicsView.set_movie(movie)`（无效 movie 回退 `set_image`）；其余 → `set_image(QPixmap(path))`；
  3. 更新 Label：`#{id} {original_file_name}`；
  4. 刷新底部三个 tab（见下）；
  5. 相似窗格可见则刷新相似列表，否则置 `_similar_stale = True`。
- `closeEvent` 停止并回收 movie（照搬 `_stop_movie` 逻辑）。
- `.ui` 中 `widgetImageViewer` 名下的 `graphicsView` 即 `PanZoomImageView`，缩放/平移无需额外代码。

### 导航

| 控件 | 行为 |
|---|---|
| 后退 `pushButtonPrev` | `_position > 0` 时 `_position -= 1` 并载入 `_history[_position]`（不入栈）；否则 no-op |
| 随机 `pushButtonRand` | `db.random_sticker_id(excluding=current_id)`；None 时静默 no-op |
| 前进 `pushButtonNext` | `db.next_sticker_id(current_id)`；None 时静默 no-op |
| Label `label` | `#{id} {original_file_name}` |

`_navigate_to(new_id)` 统一入口：

```python
def _navigate_to(self, new_id: int):
    if new_id == self._current_id():
        return                      # 单图库前进回绕到自己：不重复入栈
    stickers = self._database.get_stickers_by_ids([new_id])
    if not stickers:
        return                      # 理论不可达：id 来自数据库层，保证有效
    del self._history[self._position + 1:]
    self._history.append(new_id)
    self._position += 1
    self._show_sticker(stickers[0])
```

初次打开：`random_sticker_id()`（不排除）→ `_navigate_to(id)`。

### 相似图片窗格（性能策略的核心）

- 惰性创建：首次切换为可见时才实例化 `SimilarImagesPage(auto_refresh=False)`，加入 `verticalLayout_6`；此前右侧完全不占用查询资源。
- 开关按钮 `pushButtonShowHideSimilarImages`：

```python
def _toggle_similar_images(self):
    visible = not self.widgetSimilarImages.isVisible()
    self.widgetSimilarImages.setVisible(visible)
    self.pushButtonShowHideSimilarImages.setText(
        "隐藏相似图片" if visible else "查看相似图片>>"
    )
    if visible and self._similar_stale:
        self._refresh_similar_images()
```

- 导航时（`_show_sticker` 内）：窗格可见 → `_refresh_similar_images()`；不可见 → 仅 `_similar_stale = True`，**零向量查询**。
- `_refresh_similar_images()`：

```python
try:
    search_results, sticker_map = fetch_similar_candidates(self._sticker)
except Exception as exc:            # ValueError(无向量)/RuntimeError(未初始化) 等
    logger.warning(...)
    page.refresh_content(build_sticker_model([]))   # 清空列表
    # 状态栏或列表位置提示"该图片还没有特征向量"
    return
page.set_similar_data(search_results, sticker_map)
page.apply_filter_and_refresh()
self._similar_stale = False
```

- 双击相似项沿用 StickerListPage 既有逻辑弹出独立 `ImageViewerDialog`，无需额外处理。

### 底部属性区（与图片查看器一致）

| Tab | 实现 |
|---|---|
| 标签 `tabTags` | 复刻 `_init_tag_editor`：CustomTagWidget + 添加/删除，`_save_tags` 走 `database.set_sticker_tags()`，成功后同步本地 DTO 并重载模型 |
| 文本 `tabText` | `ImageTextEditWidget.set_database(db)` + `set_sticker(sticker)`，编辑保存由组件自理 |
| 文件属性 `tabFileInfo` | 复刻 `_init_file_info_table` + `_reload_file_info`，只读展示 |
| 强制激活 | 构造时 `tabWidgetBottom.setCurrentIndex(0)`（防 .ui 编辑残留状态） |

`pushButtonEditProperties` 本次不连接任何槽，保持可见。

## 四、测试计划

### tests/test_main_window_main_menu.py（改）

- 第 97 行期望文本改为 "开始图库审阅"。

### tests/test_dialog_library_auditing.py（新增）

offscreen Qt（参照 `test_main_window_main_menu.py` 环境），stub 数据库对象（参照 `test_similar_images_service.py` 提供 `get_stickers_by_ids` 的假库写法）：

1. **导航**：stub `random_sticker_id` / `next_sticker_id` 为确定性序列，验证随机/前进各入一条历史、Label 更新、GIF 与静态图两条加载路径。
2. **后退**：多次导航后连点后退，验证按历史逆序回退且不再入栈；`_position == 0` 时 no-op。
3. **前进回绕**：stub `next_sticker_id` 返回当前 id 自身（单图库），验证历史不追加重复项。
4. **随机 no-op**：stub 返回 None，验证界面与历史不变。
5. **相似窗格惰性**：计数器 stub `fetch_similar_candidates`；窗格隐藏时连续导航 N 次 → 计数为 0；点开窗格 → 恰好刷新 1 次；可见状态下再导航 → 每次 +1；关掉再导航再打开 → 又 +1。
6. **文案切换**：按钮文字随可见性在"查看相似图片>>"/"隐藏相似图片"间切换。
7. **无向量容错**：stub 抛 ValueError，列表清空且对话框不崩溃。

### 回归

`.venv` 解释器运行：

```
python -m unittest tests.test_main_window_main_menu tests.test_dialog_library_auditing tests.test_sticker_list_view tests.test_pan_zoom_image_view
```

## 五、实施步骤清单

1. `main_window.ui`：改两处文案 + 按钮 objectName 改名。
2. `sticker_db.py`：新增 `random_sticker_id` / `next_sticker_id` 及单元测试（可并入现有 db 测试风格）。
3. 新建 `dialog_library_auditing.py`（状态、导航、查看器、底部 tabs、相似窗格）。
4. `main_window.py`：`open_library_auditing()` + 两处接线。
5. 更新 `test_main_window_main_menu.py`，新建 `test_dialog_library_auditing.py`。
6. 全量回归上述测试套件。

## 六、风险与备注

- `ORDER BY RANDOM()` 为全表扫描排序；仅取 id 列、人工触发频率，几十万行内均为毫秒级。将来若成瓶颈，换 `LIMIT 1 OFFSET random*count` 变体即可，方法签名不变。
- `SimilarImagesPage` 构造依赖 `global_instances.current_settings_manager`，其内部已处理 None；对话框内使用无需额外兜底。
- 标签/文本保存直接复用既有原子写接口（`set_sticker_tags` / `set_sticker_texts`），符合项目"SQLite 部分保证完整"的一致性目标。
