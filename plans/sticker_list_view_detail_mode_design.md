# 表情包列表视图"详细信息模式"设计（缩略图 + 文件名 + 标签）

## 背景与需求

`StickerListView`（继承 QListView）目前只有一种展示形态：图标网格，每个格子只画一张居中缩略图（外加 GIF / 相似度角标），不显示任何文字信息。

需求是新增一种"详细信息"形态，每行展示：

```
=========
缩略图  文件名          标签
[图片]  my_image.jpg   城市,建筑,人文
=========
```

硬性约束：

1. **标签展示顺序必须与图片查看器标签编辑框完全一致**：按 `order` 升序，同 `order` 按 `id` 从小到大；
2. 标签优先渲染成带颜色的样式；若在 delegate 内渲染代价过高，退化为纯文字也可接受；
3. 采用路线 A（双模式自绘 delegate），不换 QTreeView/QTableView，不用 `setIndexWidget`。

## 现状梳理（与本方案相关的关键事实）

调研结论中有四个非常有利的既有条件，本方案的低价正是建立在它们之上：

### 事实 1：标签数据已经在模型里，且排序已满足要求

- 每个 item 通过 `ROLE_STICKER_IMAGE` 携带完整的 `StickerImage` DTO（src/services/sticker_library_viewer_service.py:88）；
- DTO 的 `tags: list[Tag]` 在数据库导出时已经按 `(order, id)` 排序（src/stickerdb/v1/db_classes.py:105 `sorted(self.tags, key=lambda tag: (tag.order, tag.id))`）；
- 图片查看器的标签编辑框读取的是**同一个列表**（src/ui/dialog_image_viewer.py `_reload_tag_model` 直接遍历 `sticker.tags`）。

因此"与编辑框顺序一致"这一约束**自动满足**：两个界面消费同一份已排序数据，不存在二次排序的需求。`Tag` DTO 自带 `color_rgb`，彩色渲染所需的数据也是现成的。

注意：导出不过滤 `enabled`，编辑框也不过滤，两者一致。详细信息模式同样**显示全部关联标签**（含 disabled），保持三方一致。

### 事实 2：delegate 已完全自绘，加分支即可

`StickerItemDelegate.paint()` 自己画选中态、悬停态、缩略图（走 `ThumbnailProvider.request_thumbnail(ROLE_BLOB_ENTITY)` 异步管线）、GIF/相似度角标，完全不依赖 Qt 默认项渲染。新增一种绘制布局只是在这个类里加一个分支，不会与 Qt 的内置行为打架。

### 事实 3：模式常量早已预留

- src/commons/constants.py 定义了 `LIST_DISPLAY_MODE_LIST = 0` 和 `LIST_DISPLAY_MODE_ICON = 1`；
- `StickerListView.__init__` 里已有 `self.display_mode = commons.constants.LIST_DISPLAY_MODE_ICON` 属性，只是从未被消费。

本方案直接复用这对常量：`LIST_DISPLAY_MODE_LIST` 即"详细信息模式"（历史命名，不再改名，避免波及）。

### 事实 4：标签变更后的刷新链路已存在

批量编辑标签后，`StickerListPage._update_sticker_dtos`（src/ui/widgets/sticker_list_page.py:282）原地更新 DTO 的 `tags` 并 `model.dataChanged.emit(index, index, [ROLE_STICKER_IMAGE])`。delegate 只要从该角色读标签，标签列就能随批量编辑自动重绘，零额外工作。

### 其他相关现状

- 视图配置：`IconMode` + `ResizeMode.Adjust` + `gridSize=(item_size, item_size)` + `UniformItemSizes(True)` + `Spacing(8)` + `WordWrap(False)`；
- `set_display_size(size)`（滑块驱动）同时改 `self._item_size`、delegate 的 `_item_size` 和 gridSize；
- hash->row 索引、无限滚动加载（load_more）、缩略图就绪定点重绘（`_on_thumbnail_ready`）全部基于角色数据与行号，与绘制方式无关；
- 工具栏基类提供 `add_toolbar_widget` / `insert_toolbar_widget_right_of_spacer`，滑块位于最右端，左侧紧邻 spacer 的位置可用于放置切换按钮；
- `ui/page_finite_sticker_collection.ui` 等通过 promoted widget 声明 `StickerListView`，`.ui` 文件无需改动；
- debug 服务（sticker_view_service_debug.py）构造的模型把文件名放在 `DisplayRole`、没有 `ROLE_STICKER_IMAGE`，属于降级路径的输入。

## 目标与非目标

### 目标

1. 所有基于 `StickerListPage` 的标签页（全库浏览、搜索结果、高级搜索、相似图片）都能在"图标 / 详细信息"两种形态间一键切换；
2. 详细信息模式下每行展示：左侧小缩略图（含 GIF/相似度角标）、文件名（过长省略）、按编辑框顺序排列的标签；
3. 批量编辑标签后标签列即时更新（复用现有 dataChanged 链路）；
4. 图标模式的行为、外观、性能与现状完全一致（回归红线）；
5. hash 索引、无限加载、异步缩略图重绘等基础设施不受模式切换影响。

### 非目标

- 不做模式持久化（每次新建标签页默认图标模式；后续可作为设置项扩展）；
- 不做列头、列宽拖拽（那才是换 QTreeView 的理由，本次没有此需求）;
- 不做多列排序、右键列选择等资源管理器完整能力；
- 不改动数据库层、服务层的任何查询与 DTO 结构；
- 不处理 `.ui` 文件（promoted widget 机制下无需改动）。

## 总体方案

在 `StickerItemDelegate` 内部实现**双绘制分支**，在 `StickerListView` 上实现**模式切换与布局参数管理**：

| 关注点 | 图标模式（现状） | 详细信息模式（新增） |
| --- | --- | --- |
| QListView.ViewMode | IconMode | ListMode |
| gridSize | `(s, s)` 正方形 | `(viewport宽, h)` 整行 |
| delegate.sizeHint | `(s, s)` | `(兜底宽, h)`（cell 尺寸由 gridSize 决定）|
| 绘制内容 | 居中大缩略图 + 角标 | 小缩略图 + 角标 + 文件名 + 标签 |
| 滑块语义 | 格子边长（32–512） | 行高（48–128） |

核心思想：**两种模式的尺寸互相独立记忆**（类似 Windows 资源管理器：图标大小与详细信息行宽互不干扰），滑块永远作用于当前模式。

## 详细设计

### 1. 模式状态与尺寸记忆（StickerListView）

```python
class StickerListView(QListView):
    DETAIL_ROW_HEIGHT_DEFAULT = 72
    DETAIL_ROW_HEIGHT_MIN = 48      # 与滑块下限一致
    DETAIL_ROW_HEIGHT_MAX = 128     # 详细信息行高上限，避免行高失控

    def __init__(...):
        ...
        self._display_mode = commons.constants.LIST_DISPLAY_MODE_ICON
        self._icon_item_size = self.ITEM_SIZE           # 图标模式记忆值
        self._detail_row_height = self.DETAIL_ROW_HEIGHT_DEFAULT
```

- `item_size()` 保持对外语义不变：返回**当前模式**的尺寸（图标模式返回 `_icon_item_size`，详细模式返回 `_detail_row_height`）。滑块初始化代码 `slider.setValue(view.item_size())` 无需修改。
- 原 `_item_size` 成员拆分为上述两个记忆值；`set_thumbnail_provider` 中重建 delegate 后需把当前模式的尺寸传给新 delegate。

### 2. 模式切换（StickerListView.set_display_mode）

```python
def set_display_mode(self, mode: int) -> None:
    self._display_mode = mode
    delegate = self.itemDelegate()
    if isinstance(delegate, StickerItemDelegate):
        delegate.set_display_mode(mode)

    if mode == commons.constants.LIST_DISPLAY_MODE_LIST:
        self.setViewMode(QListView.ViewMode.ListMode)
        self._apply_row_height(self._detail_row_height)
        self._sync_detail_grid_width()
    else:
        self.setViewMode(QListView.ViewMode.IconMode)
        self._apply_item_size(self._icon_item_size)
    self.doItemsLayout()   # 立即重排，不等下一次视口事件
```

辅助方法：

```python
def _apply_item_size(self, size):          # 图标模式：正方形格
    self.setGridSize(QSize(size, size))

def _apply_row_height(self, height):       # 详细模式：整行格
    delegate.set_item_size(height)         # delegate 用它画缩略图区
    self._sync_detail_grid_width()

def _sync_detail_grid_width(self):
    width = max(1, self.viewport().width())
    self.setGridSize(QSize(width, self._detail_row_height))
```

关键技术点与权衡：

- **ListMode + gridSize 是可行的组合**。QListView 在两种 ViewMode 下都尊重 gridSize；不设 gridSize 时 ListMode 会退回 delegate sizeHint 宽度且不会拉伸到视口宽，所以必须显式设置整行宽度。
- **宽度同步时机**：`resizeEvent` 覆写中调用 `_sync_detail_grid_width()`。滚动条出现/消失会造成 viewport 宽度滞后一帧的问题由 `ResizeMode.Adjust` 的自动重排消化，实践中表现为最多一次额外的重排，不做预判滚动条宽度的复杂计算（简单优先）。
- `setUniformItemSizes(True)` 可以保留：sizeHint 与 index 无关（只依赖模式），所有行等高，满足均匀性前提。
- `setWordWrap(False)` 已有，配合文本省略正好。
- Spacing(8) 在 ListMode 下成为行间距，视觉可接受，不为模式单独调整。

### 3. 滑块语义（set_display_size 改造）

```python
def set_display_size(self, size: int) -> None:
    if self._display_mode == commons.constants.LIST_DISPLAY_MODE_LIST:
        self._detail_row_height = max(self.DETAIL_ROW_HEIGHT_MIN,
                                      min(int(size), self.DETAIL_ROW_HEIGHT_MAX))
        self._apply_row_height(self._detail_row_height)
    else:
        self._icon_item_size = max(32, min(int(size), 512))
        delegate.set_item_size(self._icon_item_size)
        self._apply_item_size(self._icon_item_size)
```

滑块的 range 由页面层在切换模式时同步（见 §6），view 内部再做一次 clamp 兜底。两个模式各记各的值，来回切换互不污染。

### 4. 委托层（StickerItemDelegate）

#### 4.1 结构调整

把现有 `paint()` 的图标绘制主体原样抽成 `_paint_icon(painter, option, index)`（纯移动，不改逻辑），`paint()` 变为：

```python
def paint(self, painter, option, index):
    painter.save()
    try:
        # 现有的选中/悬停背景绘制保留在这里（两种模式通用，
        # 都作用在整个 option.rect 上）
        ...
        if self._display_mode == commons.constants.LIST_DISPLAY_MODE_LIST:
            self._paint_detail(painter, option, index)
        else:
            self._paint_icon(painter, option, index)
    finally:
        painter.restore()

def sizeHint(self, option, index):
    if self._display_mode == commons.constants.LIST_DISPLAY_MODE_LIST:
        # 宽度仅作兜底；实际 cell 尺寸由 gridSize 决定
        return QSize(self._item_size * 4, self._item_size)
    return QSize(self._item_size, self._item_size)
```

`_item_size` 在详细模式下含义变为"行高"，`set_item_size()` 本身无需改动。

#### 4.2 详细模式布局（_paint_detail）

```
option.rect（整行）
┌──────────────────────────────────────────────────────────────┐
│ ┌────┐                                                       │
│ │thumb│  ← 正方形区，边长 = 行高 − 2×THUMB_PAD              │
│ │ +  │                                          (文字垂直居中)│
│ │角标│  文件名elide   标签文本/chips…（溢出 +N）             │
│ └────┘                                                       │
└──────────────────────────────────────────────────────────────┘
```

绘制步骤：

1. **缩略图**：复用现有 `_pixmap_for_index(index, thumb_rect.size(), mode)`（ROLE_BLOB_ENTITY → ThumbnailProvider 异步管线，未就绪时返回共享占位图），`scaled(KeepAspectRatio, Smooth)` 后 `moveCenter` 到左侧正方形区中心绘制。缩略图就绪信号触发的定点重绘（`_on_thumbnail_ready`）照常工作。
2. **角标**：GIF / 相似度角标复用现有 `_draw_badge` / `_draw_similarity_badge`，传入的基准 rect 从大图 `pixmap_rect` 换成小图 `pixmap_rect`，函数内部逻辑（贴角、字号、内边距）完全不用改。
3. **文件名**：
   ```python
   sticker = index.data(ROLE_STICKER_IMAGE)
   if sticker is not None:
       filename = sticker.original_file_name
   else:
       filename = index.data(Qt.ItemDataRole.DisplayRole) or ""   # debug 模型降级
   ```
   使用 `QFontMetrics.elidedText(filename, Qt.TextElideMode.ElideRight, 可用宽)` 绘制，pen 取 `option.palette.text()`。
4. **文件名宽度分配**：文件名区起点 = 缩略图区右缘 + `DETAIL_TEXT_GAP`(12px)；文件名最大宽度 = `min(实际文本宽, 剩余宽 × DETAIL_FILENAME_RATIO(0.35))`。文件名普遍较短，按需占用、上限封顶，把更多横向空间留给标签。
5. **标签区**：从文件名末尾 + 16px 间隔开始，到行尾减 PADDING 结束。

#### 4.3 标签渲染——第一档：纯文字（Phase 1）

```python
tags_text = ", ".join(tag.name for tag in sticker.tags)   # 顺序即编辑框顺序
drawn = metrics.elidedText(tags_text, ElideRight, tags_rect.width())
painter.drawText(tags_rect, AlignVCenter | AlignLeft, drawn)
```

- 顺序保证：`sticker.tags` 已由 DB 导出排序（见现状事实 1），join 即最终展示顺序；
- 性量评估：paint 每帧只对**可见行**（几十个）执行一次字符串 join，微秒级，**不做缓存**；将来若引入虚拟化极端场景再考虑缓存到自定义 role。

#### 4.4 标签渲染——第二档：彩色圆角片（Phase 2，可选增强）

布局算法提取为可单测的纯函数：

```python
@dataclass
class ChipLayout:
    chips: list[tuple[QRect, str]]   # (矩形, 标签名)
    hidden_count: int                # 放不下而被折叠的数量

def _layout_tag_chips(text_rect, tags, metrics) -> ChipLayout:
    x = text_rect.left()
    chip_h = metrics.height() + 2 * CHIP_PAD_Y      # 垂直居中于行
    y = text_rect.center().y() - chip_h // 2
    chips = []
    for i, tag in enumerate(tags):
        w = metrics.horizontalAdvance(tag.name) + 2 * CHIP_PAD_X
        if x + w > text_rect.right() and chips:
            hidden_count = len(tags) - len(chips)
            break
        chips.append((QRect(x, y, w, chip_h), tag.name))
        x += w + CHIP_GAP
    return ChipLayout(chips, hidden_count)
```

绘制样式参照现成的 `TagItemDelegate.paint`（custom_tag_widget.py）：边框与文字用 `tag.color_rgb`，背景用 `color_rgb` 叠 alpha≈35；无有效颜色时回退默认蓝灰配色。溢出时在行尾画 `+N` 灰色徽标（可直接复用 `_draw_badge` 的画法换中性色）。

### 5. 入口 UI（StickerListPage）

在基类新增 `_setup_display_mode_toggle()`，由各子类在 `_setup_display_size_slider()` 旁调用一次（与滑块同样的装配模式）：

```python
def _setup_display_mode_toggle(self) -> None:
    self._ensure_toolbar_spacer()
    button = QToolButton(self)
    button.setObjectName("displayModeToggle")
    button.setText("详细信息")
    button.setToolTip("切换图标/详细信息显示")
    button.setCheckable(True)
    button.setToolButtonStyle(Qt.ToolButtonStyle.ToolButtonTextOnly)
    button.toggled.connect(self._on_display_mode_toggled)
    self.insert_toolbar_widget_right_of_spacer(button)   # 位于滑块左侧

def _on_display_mode_toggled(self, checked: bool) -> None:
    mode = (commons.constants.LIST_DISPLAY_MODE_LIST if checked
            else commons.constants.LIST_DISPLAY_MODE_ICON)
    view = self.listViewStickerList
    view.set_display_mode(mode)
    # 同步滑块范围与位置到新模式的记忆值
    slider = self.display_size_slider
    if mode == commons.constants.LIST_DISPLAY_MODE_LIST:
        slider.setRange(view.DETAIL_ROW_HEIGHT_MIN, view.DETAIL_ROW_HEIGHT_MAX)
    else:
        slider.setRange(48, commons.constants.THUMBNAIL_SIZE)
    slider.setValue(view.item_size())
```

- 按钮放在 spacer 右侧、滑块左边（`insert_toolbar_widget_right_of_spacer` 已有现成方法）；
- `slider.setValue` 会触发 `valueChanged → set_display_size`，恰好完成"把新模式记忆值装载到 delegate/gridSize"的闭环，不需要额外信号屏蔽；
- 相似图片页、搜索结果页、高级搜索页、全库浏览页全部继承基类，一处装配处处生效。

### 6. 图片查看器编辑标签后的刷新补丁

现状缺口（两种模式共有，但列表模式下肉眼可见）：在查看器里增删标签后，`ImageViewerDialog._save_tags` 只更新了自己持有的 DTO 引用。由于模型存的本来就是同一个 Python 对象，数据其实已同步，但没有 emit dataChanged，视图不知道要重绘。

顺手修复（一行）：`StickerListPage._open_image_viewer_for_index` 中 `dialog.exec()` 返回后：

```python
model = self.listViewStickerList.model()
if model is not None and index.isValid():
    model.dataChanged.emit(index, index, [ROLE_STICKER_IMAGE])
```

`index.isValid()` 兜底模态期间发生的删除广播。图标模式下这也是正确行为（tooltip 不含标签所以无感知，但重绘无害）。

### 7. 边界情况与降级矩阵

| 场景 | 行为 |
| --- | --- |
| item 只有 DisplayRole（debug 服务模型） | 显示 DisplayRole 文件名，无标签区 |
| `ROLE_STICKER_IMAGE` 存在但 `tags == []` | 标签区留空 |
| 标签总宽超出剩余空间 | Phase 1 文字省略号；Phase 2 折叠为 `+N` |
| 缩略图未就绪 / 加载失败 | 现有占位图机制照常；空 pixmap 时跳过绘制，文字照画 |
| 模式切换时选中状态 | 选择基于 QModelIndex，与布局无关，自动保留 |
| 模式切换时滚动位置 | QListView 自行处理；如需回到顶部由页面层决定（本期不做） |
| 视口极窄（< 缩略图 + 最小文字宽） | elide 保证不越界；极端情况下文件名/标签收缩为省略号 |
| `setModel` 替换模型 / 无限加载追加行 | 与模式无关（行号与角色机制），回归测试覆盖 |

### 8. 性能分析

- 详细模式每行额外成本：1 次 `elidedText` + 1 次 `drawText`（Phase 1），或 N 次 `horizontalAdvance` + 圆角矩形绘制（Phase 2，N = 可见标签数，通常 < 10）；
- 只作用于可见行（Qt 视图裁剪），万行模型无可感知差异；
- 缩略图异步管线、内存/磁盘缓存、定点重绘路径零改动。

## 改动清单

| 文件 | 改动 |
| --- | --- |
| `src/ui/widgets/sticker_list_view_widget.py` | 核心：`StickerItemDelegate` 双模式分支（`set_display_mode` / `_paint_detail` / `_layout_tag_chips`）；`StickerListView` 双尺寸记忆、`set_display_mode`、`set_display_size` 改造、`resizeEvent`、`item_size()` 语义 |
| `src/ui/widgets/sticker_list_page.py` | `_setup_display_mode_toggle` + `_on_display_mode_toggled`；查看器关闭后的 dataChanged 补丁 |
| `src/ui/page_finite_sticker_collection.py` / `page_infinite_sticker_collection.py` | 各调用一行 `_setup_display_mode_toggle()` |
| `tests/test_sticker_list_view.py` | 新增测试（见测试计划） |

不改动：`commons/constants.py`（复用既有常量）、`commons/roles.py`、服务层、DB 层、`.ui` 文件。

预计产品代码约 180–230 行（Phase 1 约 150 行，Phase 2 再加 60–80 行），不含测试。

## 实施阶段划分

**Phase 1 — 模式框架 + 纯文字标签**（独立可交付）

1. `StickerItemDelegate`：抽出 `_paint_icon`；新增 `_display_mode`、`_paint_detail`（文字标签版）；
2. `StickerListView`：双尺寸记忆、`set_display_mode`、`set_display_size` 改造、`resizeEvent`、`item_size()`；
3. 页面层：切换按钮装配 + 滑块联动 + 查看器刷新补丁；
4. 测试全绿。

**Phase 2 — 彩色圆角片标签**（可选，独立提交）

1. `_layout_tag_chips` 纯函数 + 绘制；
2. `+N` 溢出徽标；
3. 补充测试。

## 测试计划

挂进现有 `tests/test_sticker_list_view.py`，沿用其离屏 QApplication、FakeThumbnailProvider、像素采样（`_yellow_pixel_bounds` 等）与 mock 风格：

**模式切换（视图层）**

- `test_set_display_mode_switches_view_and_grid`：切到 LIST 后 `viewMode()==ListMode`、gridSize 为 `(视口宽, 72)`；切回 ICON 恢复 `(160,160)`；
- `test_display_sizes_are_remembered_per_mode`：ICON下调到 96 → 切 LIST 调到 72 → 切回 ICON 仍是 96；
- `test_resize_updates_detail_grid_width`：LIST 模式下 resize 视图，gridSize 宽度跟随；
- `test_item_size_reflects_current_mode`；
- 回归：`test_uses_large_image_only_grid` 等现有测试必须原样通过（默认仍是图标模式）。

**委托绘制（像素采样）**

- `test_detail_paint_draws_thumbnail_on_left`：白色缩略图像素的 x 坐标集中在前 1/3；
- `test_detail_paint_draws_filename_and_tags_text`：构造已知文件名/标签，黑色像素统计出现在预期区域；顺序用提取出的纯函数直接断言（乱序 order/id 输入 → join 结果按 `(order, id)` 排列）；
- `test_detail_paint_keeps_gif_badge_on_small_thumbnail`：粉色像素集中在左侧小缩略图左上角；
- `test_detail_paint_without_dto_falls_back_to_display_role`；
- `test_empty_tags_leave_blank_region`；
- Phase 2：`test_chip_overflow_shows_plus_n`（`_layout_tag_chips` 纯函数断言 + 像素验证）。

**链路回归**

- `test_batch_tag_update_repaints_detail_rows`：`_update_sticker_dtos` 后 LIST 模式下标签文本变化（可通过重绘后的画布像素或 spy delegate.paint 验证）；
- `test_mode_switch_preserves_load_more_and_hash_index`：切换模式后滚动仍触发 `load_more_requested`、`thumbnail_ready` 仍定点重绘；
- `test_viewer_close_emits_data_changed`：mock ImageViewerDialog，断言 exec 返回后收到 `dataChanged`；
- `test_toolbar_has_display_mode_toggle`：按钮存在、checkable、位于滑块左侧（仿照 `test_insert_toolbar_widgets_around_spacer` 的 actions 顺序断言）。

## 开放问题（实施前需拍板）

1. 详细模式默认行高 72px 是否合适（可在联调时目测调整，常量已收敛在一处）；
2. 切换按钮文案用"详细信息"还是图标化（当前按文字按钮设计，与工具栏 ToolButtonTextOnly 风格一致）；
3. 模式持久化（settings 记住上次选择）是否列入本期 —— 已按非目标处理，接口上 `set_display_mode` 天然支持将来接入。

## 测试注意

项目使用 Python 标准库 unittest，依赖全部位于 `.venv` 虚拟环境；GUI 相关测试以 `QT_QPA_PLATFORM=offscreen` 运行（测试文件内已设置）。运行示例：

```bash
.venv/Scripts/python -m unittest tests.test_sticker_list_view -v
```
