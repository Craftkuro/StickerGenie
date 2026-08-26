# 图库列表快捷键方案

## 背景与需求

所有图库页（全库浏览、搜索结果、高级搜索、相似图片）共用 `StickerListPage` 基类 + `StickerListView` 视图，目前没有任何键盘快捷键，全部操作依赖右键菜单。需求是为图库列表补充一组高频操作的快捷键。

已确认的范围（用户拍板）：

| 快捷键 | 行为 |
|---|---|
| Ctrl+C | 单选：复制到剪贴板（GIF 复制动图）；多选：复制为文件 |
| Enter | 打开图片查看器 |
| Del | 移动到图库回收站（含确认对话框） |
| F5 | 刷新——**仅无限列表页**有此功能 |
| Ctrl+A | 全选 |
| Ctrl+S | 另存为（单选/多选均可） |
| Ctrl+F | 聚焦搜索框 |
| Ctrl+滚轮 | 调整缩略图显示大小 |

明确不做：撤销/重做、重命名；Esc 清除选择；查找相似图片、批量编辑标签的快捷键；Space 预览；Shift+Del 永久删除；Alt+Enter 属性；查看器内翻页导航。

## 现状梳理（关键既有条件）

1. **处理器全部现成**：右键菜单的 `_copy_sticker_for_index`、`_save_as_for_indexes`、`_open_image_viewer_for_index`、`_delete_stickers_for_indexes` 都在 `StickerListPage` 上且不依赖菜单上下文（src/ui/widgets/sticker_list_page.py），快捷键直接调用即可。
2. **单选复制的 GIF 语义已正确**：`image_clipboard_service.copy_image_to_clipboard` 默认 `anim_as_static_image=False`，GIF 走 HTML file:// 分支，QQ/微信粘贴为动图。
3. **多选复制可以零 staging**：blob 本身就是磁盘上的真实文件（`<sha1>.<ext>`，src/blob_storage/blob_storage.py），每条目通过 `ROLE_FILE_PATH` 携带磁盘路径。把路径列表直接放进 `QMimeData.setUrls()` 即可让资源管理器粘贴出文件，无需暂存改名。**已确认接受粘贴出的文件是 SHA1 哈希名而非原始文件名**（想保留原名用"另存为"）。附带好处：哈希名天然唯一，多选粘贴不会撞名。
4. **缩略图大小管线现成**：滑块 → `set_display_size()`（视图记忆两种模式各自的尺寸并重排）。Ctrl+滚轮只需驱动同一条链路。
5. **无限页刷新入口现成**：`InfiniteStickerCollectionPage.refresh_action`（工具栏刷新按钮，触发 `signal_refresh_content`）。其他页面没有刷新语义，F5 不安装。
6. **搜索框在主窗口**：`main_window.ui` 的 `customSearchBox` 是唯一的搜索框实例，各页面没有自己的搜索框。Ctrl+F 的落点在主窗口层。

## 目标与非目标

### 目标

1. 上表 8 个快捷键在全部基于 `StickerListPage` 的标签页生效；
2. 焦点在搜索框、标签编辑器等输入控件时，列表快捷键不误触发；
3. 多选复制零 staging，即时完成；
4. Ctrl+滚轮与工具栏滑块双向联动（任一方式改变大小，另一处同步）；
5. 右键菜单行为完全不变（回归红线）。

### 非目标

- 不做快捷键自定义/设置项；
- 不改 `.ui` 文件（QShortcut 全部代码安装）;
- 不动数据库层与服务层查询逻辑；
- 图片查看器内部快捷键不在本轮范围。

## 总体方案

### 安装位置与作用域

- **列表类快捷键**（Ctrl+C/A/S、Enter、Del）统一在 `StickerListPage.__init__` 安装：`QShortcut(QKeySequence(...), self.listViewStickerList)`，context 用默认的 `Qt.ShortcutContext.WidgetShortcut`——仅当视图或其子控件持有焦点时触发，天然避免输入框内按 Del/Ctrl+C 被劫持。一处实现覆盖全部 5 个页面。
- **F5** 只在 `InfiniteStickerCollectionPage.__init__` 里安装，绑定到现有 `refresh_action.triggered` 同款处理。
- **Ctrl+F** 在主窗口安装（`QShortcut` parent 设为 `customSearchBox` 或直接 `QKeySequence` 绑到 action），聚焦 `customSearchBox.line_edit` 并全选现有文本。主窗口级安装意味着焦点在任何地方都生效，这与"聚焦搜索框"的意图一致。

### 各项实现要点

#### Ctrl+C：单选复制 / 多选复制

```
选中数 == 0 → no-op
选中数 == 1 → _copy_sticker_for_index(index)   # GIF 默认动图，现状语义
选中数 >= 2 → copy_file_paths_to_clipboard(paths)
```

`image_clipboard_service` 新增：

```python
def copy_file_paths_to_clipboard(paths: Sequence[str | Path]) -> None:
    mime_data = QMimeData()
    mime_data.setUrls([QUrl.fromLocalFile(str(Path(p).resolve())) for p in paths])
    QGuiApplication.clipboard().setMimeData(mime_data)
```

Windows 下 `setUrls` 映射为 `CF_HDROP`，资源管理器/聊天窗口粘贴即为文件复制，全程无 IO、无暂存目录。路径来自 `ROLE_FILE_PATH`，跳过无效索引。

#### Enter：打开查看器

用 `QShortcut(Key_Return)` + `QShortcut(Key_Enter)` 双绑定到 `_open_image_viewer_for_index(view.currentIndex())`。**不要**再连 `activated()` 信号：Windows 平台样式下双击也会发 `activated()`，会与已有的 `doubleClicked` 连接叠加成开两次查看器；QShortcut 方案则完全不触碰 Qt 内建按键分发。

#### Del：删除

绑定到 `_delete_stickers_for_indexes(self._selected_indexes())`，确认对话框逻辑原样复用（多选显示数量文案）。

#### Ctrl+A / Ctrl+S

- Ctrl+A → `view.selectAll()`；
- Ctrl+S → `_save_as_for_indexes(self._selected_indexes())`（单选出文件对话框、多选出目录对话框的逻辑已在其中）。

#### F5（仅无限列表页）

绑定到 `self.refresh_action.triggered.emit()` 等价路径（直接调 `self._on_refresh_clicked`）。基类不安装 F5，其他页面按了没反应即预期行为。

#### Ctrl+滚轮：缩略图大小

`StickerListView` override `wheelEvent`：

- `event.modifiers() & ControlModifier` 时：按角度增量换算步长（建议每格 ±8，与滑块 `SingleStep(8)` 一致），调 `set_display_size(clamp(item_size ± step))`，`event.accept()` 返回，不再滚动列表；
- 无 Ctrl 时走 `super().wheelEvent(event)` 原样滚动。

联动滑块：视图新增信号 `display_size_changed = pyqtSignal(int)`，Ctrl+滚轮改变尺寸后发射；`StickerListPage.__init__` 把它连到滑块 `setValue`。闭环检查：滑块 `valueChanged` → `view.set_display_size`，`set_display_size` 不发该信号，无回环。钳位边界（图标模式 48–512 / 详细模式行高范围）由 `set_display_size` 内部现有 clamp 保证。

## 边界与风险

1. **Return 与 activated 的平台差异**：采用 QShortcut 后不依赖 activated；若后续有人改动双击链路需保持"双击只走 doubleClicked"的现状。
2. **多选复制的剪贴板覆盖语义**：放入 CF_HDROP 会替换剪贴板全部内容（与资源管理器复制行为一致），QQ/微信等支持文件粘贴的目标可直接使用；不支持文件粘贴的目标（如纯文本框）粘贴结果为空，属预期。
3. **WidgetShortcut 与模态对话框**：图片查看器是模态 `exec()`，打开期间列表快捷键自然失活，无需额外处理。
4. **PyQt6 槽内异常**：所有新增槽函数对外部调用（剪贴板、文件系统）沿用现有 try/except + QMessageBox 模式（参见 AGENTS.md 中 PyQt6 qFatal 陷阱）。

## 测试计划（unittest）

沿用 tests/test_sticker_list_view.py、test_custom_search_box.py 的构造方式：

1. 单元测试 `copy_file_paths_to_clipboard`：QMimeData urls 数量、顺序、本地路径正确性（可用 QClipboard 读回验证，离屏环境跳过）；
2. 快捷键触发测试：`QTest.keyClick(view, Key.Key.C, ControlModifier)` 等，断言对应处理器被调用（对 handler 打桩）；
3. 单选/多选分支：选中 1 个时剪贴板走图片分支、选中 ≥2 时走文件分支；
4. Ctrl+滚轮：`QTest` 构造 wheelEvent 验证 item_size 变化与滑块同步；无修饰键时不改变尺寸；
5. F5 仅无限页响应：其他页面实例上按 F5 断言无副作用；
6. 回归：现有右键菜单相关测试全部保持绿色。
