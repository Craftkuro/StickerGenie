# 设置对话框 Schema 自动装配设计计划

## 文档状态

- 状态：等待实施
- 基线日期：2026-08-23
- 目标范围：`ConfigField` UI 元数据扩展、设置对话框动态装配、`dialog_settings.ui` 精简、相关测试
- 核心原则：设置界面由配置 schema 单一事实来源驱动生成；保存只操作配置文件和 `ConfigManager`，不触发其他组件刷新；特殊页面（如颜色预设管理器）保持独立管理

## 已确认的设计决策

1. UI 元数据直接放在 `ConfigField` 上（`ui` 可选字段），而不是 services 层平行注册表。
2. 自动生成的控件 `objectName` 统一为 `field_<key>`（如 `field_recent_search_limit`），不再延续旧的驼峰命名。
3. 第一版同时实现 `COMBO_BOX` 和 `CHECK_BOX` 绑定，即使当前 schema 尚未用到。
4. 两位小数的数值输入框控件类型命名为 `SPIN_BOX_2P`（英文含义 `spinbox_2_digit_fractions`），需在枚举处加注释说明；不引入通用的任意精度 double spinbox。

## 结论摘要

在 `config_manager/schema.py` 中新增无 Qt 依赖的 `WidgetKind` 枚举和 `FieldUI` 冻结数据类，并给 `ConfigField` 增加可选字段 `ui: FieldUI | None = None`。`services/settings.py` 用页面常量和控件描述标注各配置项；无 `ui` 或 `page=None` 的配置项只在配置文件中存在，不出现在界面。

设置对话框改为通用装配器：遍历 schema 字段，按 `ui.page` 分组生成列表项、页面、GroupBox 表单和控件，通过按 `WidgetKind` 查表的绑定适配器完成取值、赋值和变更信号接线。`dialog_settings.ui` 精简为外壳（splitter + 空列表 + 空 stacked + buttonBox）。颜色预设等特殊页面通过静态注册表接入，仍自行管理内容。

保存流程不变：收集可见字段值写入 `ConfigManager` 并落盘；失败时用 `get_all()` 快照回滚。不新增配置键、不改 TOML 格式、不升级 `SETTINGS_VERSION`。

## 当前实现基线

- `src/services/settings.py`：`SETTINGS_SCHEMA` 为 `ConfigField` 列表，仅含 key/type/default/comment 四要素。
- `src/config_manager/schema.py`：`ConfigField` 为冻结 dataclass，`ConfigSchema` 提供字段查找与类型验证。
- `src/ui/dialog_settings.py`：手写三段逐 key 代码——`_load_settings`、`_connect_signals`（逐控件接 `_mark_dirty`）、`_values_from_controls`；`apply_settings` 收集值 → `config.set` → `colorPresetManager.save_settings()` → `config.save()`，失败时 `_restore_manager` + `colorPresetManager.reload_presets()`。
- `src/ui/dialog_settings.ui`：硬编码三个页面（常规/搜索/颜色预设）、全部 GroupBox 与控件及其 min/max/step/tooltip。
- `ColorPresetManagerWidget`：提供 `changed` 信号、`reload_presets()`、`save_settings()`，独立维护预设编辑状态。

## 目标

- 新增一个配置项时，只需在 `SETTINGS_SCHEMA` 中声明 key/type/default/comment 和 `ui` 描述，界面自动出现对应控件、范围、提示和页面归属。
- 支持 `SPIN_BOX`、`SPIN_BOX_2P`、`COMBO_BOX`、`CHECK_BOX` 四种可展示控件及 `HIDDEN`（不展示）。
- 页面归属用字符串常量定义（如 `PAGE_GENERAL`、`PAGE_SEARCH`）；`None` 表示仅在配置文件中存在。
- 页面与分组顺序完全由 schema 中字段的出现顺序决定。
- 保存和应用只对配置文件与 `ConfigManager` 操作，不 reload 其他组件。
- 特殊页面（颜色预设管理器）内容仍独立管理，仅通过统一的小接口参与脏标记与保存。
- 失败回滚行为与现状一致：恢复 `ConfigManager` 并重载特殊页面。

## 非目标

- 不做保存后的组件热更新/信号广播（主窗口等不感知设置变化）。
- 不做国际化、主题化布局或可视化页面编排。
- 不支持任意精度的 double spinbox、LINE_EDIT、路径选择器等未用到的控件类型；需要时再扩枚举。
- 不改变 `ConfigManager` 的读写、验证、迁移逻辑和 TOML 文件格式。
- 不为特殊页面设计通用插件机制；第一版用静态注册表。

## 实现方案

### 1. schema 扩展（`src/config_manager/schema.py`）

```python
class WidgetKind(Enum):
    """设置界面控件类型。

    SPIN_BOX_2P（spinbox_2_digit_fractions）：两位小数的数值微调框，
    底层为 QDoubleSpinBox(decimals=2)；用于以字符串形式存储的
    数值配置（如 "0.50"），读取时转 float，写回时格式化为
    两位小数字符串。
    """
    HIDDEN = "hidden"                       # 不在界面展示
    SPIN_BOX = "spin_box"                   # QSpinBox，整数
    SPIN_BOX_2P = "spinbox_2_digit_fractions"         # 两位小数数值框，见类注释
    COMBO_BOX = "combo_box"                 # QComboBox，choices 提供 (文本, 存储值)
    CHECK_BOX = "check_box"                 # QCheckBox，布尔


@dataclass(frozen=True)
class FieldUI:
    """配置项的界面展示描述；全部字段可选，缺省即最简外观。"""
    page: str | None = None      # 页面常量；None 表示不在任何页面展示
    label: str = ""              # 表单行标签；空则回退使用 key
    group: str = ""              # GroupBox 标题；空则不加组，行直接进页级表单
    suffix: str = ""             # SPIN_BOX 的后缀（如 “ 张”）
    minimum: float | None = None # 数值下限；None 用 Qt 默认
    maximum: float | None = None # 数值上限；None 用 Qt 默认
    step: float | None = None    # singleStep；None 用 Qt 默认
    choices: tuple[tuple[str, Any], ...] = ()  # COMBO_BOX 必填


@dataclass(frozen=True)
class ConfigField:
    ...                          # 原 key/type/default/comment 不变
    ui: FieldUI | None = None    # 默认 None，向后兼容
```

- `__post_init__` 增加 `ui` 类型检查（None 或 `FieldUI`）；不做跨字段组合校验（如 SPIN_BOX 必须配 INT），错误在装配期 fail-fast 报清晰异常。
- `FieldUI` 全部成员不可变且可 deepcopy，兼容 `ConfigSchema.get_field()` 的深拷贝语义。
- 从 `config_manager/__init__.py` 导出 `WidgetKind`、`FieldUI`。

### 2. 设置 schema 标注（`src/services/settings.py`）

```python
PAGE_GENERAL = "general"
PAGE_SEARCH = "search"

ConfigField(
    "thumbnail_memory_cache_size", ConfigType.INT, 2000,
    "……",
    ui=FieldUI(page=PAGE_GENERAL, label="缩略图内存缓存大小",
               group="缩略图缓存", suffix=" 张",
               minimum=100, maximum=100000, step=100),
),
ConfigField("recent_searches", ConfigType.LIST_STR, [], "…"),   # 无 ui → 隐藏
ConfigField("library_base_path", ConfigType.STRING, "…", "…"),  # 无 ui → 隐藏
```

要点：

- `comment` 继续承担双重职责：TOML 注释 + 界面 tooltip。
- `similar_image_target_drop_ratio`、`similar_image_min_similarity` 为 STRING 存储、`SPIN_BOX_2P` 编辑，转换规则固定在绑定层（读 `float(v)`，写 `f"{v:.2f}"`），保持现有落盘格式。
- `color_presets` 不标 `ui`，由颜色预设特殊页面接管。
- 现有 `.ui` 中硬编码的范围/步长/后缀原样迁入 `FieldUI`。

### 3. 绑定适配表（新文件 `src/ui/settings_field_bindings.py`）

按 `WidgetKind` 查表的适配器集合，每个绑定提供四个能力：

| Kind | 控件 | 读（get_value） | 写（set_value） | 变更信号 |
|---|---|---|---|---|
| SPIN_BOX | QSpinBox | `value()` → int | `setValue(int)` | `valueChanged` |
| SPIN_BOX_2P | QDoubleSpinBox(decimals=2) | `f"{value():.2f}"` → str | `setValue(float(s))`，解析失败回退默认值并记日志 | `valueChanged` |
| COMBO_BOX | QComboBox | `currentData()` | `setCurrentIndex(findData(v))`，找不到报装配错误 | `currentIndexChanged` |
| CHECK_BOX | QCheckBox | `isChecked()` → bool | `setChecked(bool)` | `toggled` |

- 构建控件时应用 `suffix`/`minimum`/`maximum`/`step`/`choices`，并设 `objectName = f"field_{key}"`、tooltip 取 `comment`。
- COMBO_BOX 的 `choices` 为空时在装配期抛出明确异常。
- 该模块只依赖 PyQt6 Widgets 与 schema 数据结构，可在 offscreen 下单测。

### 4. 对话框装配（重写 `src/ui/dialog_settings.py`）

1. 遍历 `config_manager.schema.fields`，跳过 `ui is None` 或 `ui.page is None` 的字段，按 `page` 分组，组间保持首次出现顺序。
2. 追加自定义页面注册表：

   ```python
   CUSTOM_PAGES = (
       PageSpec(page_id="color_presets", title="颜色预设",
                factory=ColorPresetManagerWidget),
   )
   ```

   自定义页面契约：构造参数 `(parent, config_manager=...)`，暴露 `changed` 信号、`reload_settings()`、`save_settings()`。为此将 `ColorPresetManagerWidget.reload_presets` 改名 `reload_settings`（`save_settings`、`changed` 名称已符合契约）。
3. 每个 schema 页面动态创建：`QListWidgetItem(标题)` 加入 `listWidget`；stacked 页为 QVBoxLayout + 标题 QLabel + QScrollArea（NoFrame、widgetResizable）包裹的内容区；内容区内按 `group` 生成 QGroupBox + QFormLayout（`ExpandingFieldsGrow`、verticalSpacing=12），无组的字段进页级 QFormLayout；末尾放垂直 spacer。样式与现页面一致。
4. 每个字段经绑定表创建 label（buddy）+ 控件，存入 `self._field_widgets[key]`，并暴露 `dialog.field_widget(key)` 访问器供测试使用。
5. 接线：所有字段变更信号 → `_mark_dirty`；自定义页面 `changed` → `_mark_dirty`；`listWidget.currentRowChanged` → `stackedWidget.setCurrentIndex`；buttonBox 三按钮逻辑不变。
6. `_load_settings` = 循环 `binding.set(widget, config.get(key))`；`apply_settings` = 循环 `binding.get` → `config.set(key, value)` → 各自定义页 `save_settings()` → `config.save()`；异常时 `_restore_manager(previous_values)` + 各自定义页 `reload_settings()`，与现状一致。
7. 隐藏字段不进入收集循环，保存时不会被触碰。

### 5. `.ui` 精简（`src/ui/dialog_settings.ui`）

仅保留外壳：QDialog 几何/最小尺寸、splitter + `listWidget`（删除静态项）、空 `stackedWidget`（删除全部 page）、`buttonBox`。所有页面、GroupBox、控件均由代码生成。

## 文件改动范围

### 修改

- `src/config_manager/schema.py`
  - 新增 `WidgetKind`、`FieldUI`；`ConfigField` 增加 `ui` 可选字段与类型检查。
- `src/config_manager/__init__.py`
  - 导出 `WidgetKind`、`FieldUI`。
- `src/services/settings.py`
  - 新增页面常量 `PAGE_GENERAL`、`PAGE_SEARCH`；为可见配置项补 `ui=` 标注（含从 `.ui` 迁移的范围/步长/后缀）。
- `src/ui/dialog_settings.py`
  - 重写为 schema 驱动装配；新增 `CUSTOM_PAGES` 注册表与 `field_widget(key)` 访问器；保留 `apply_settings` 对外签名与回滚逻辑。
- `src/ui/dialog_settings.ui`
  - 删除静态页面与控件，保留对话框外壳。
- `src/ui/settings_page_color_preset_manager.py`
  - `reload_presets` 改名 `reload_settings` 以符合自定义页面契约；其余不动。
- `tests/test_settings_dialog.py`
  - 控件访问改用 `dialog.field_widget(key)`；
  - “declared_in_ui_file” 测试改为断言装配结果（字段存在、类型正确、隐藏字段不生成、objectName 规范）；
  - 行为测试（加载/应用/取消/确定/失败回滚/页面切换）逻辑基本沿用。
- `tests/test_settings_color_preset_manager.py`
  - 跟随 `reload_settings` 改名更新调用点。

### 新增

- `src/ui/settings_field_bindings.py`
  - WidgetKind → 控件构建/取值/赋值/信号绑定适配表。
- `tests/test_settings_field_bindings.py`
  - 各绑定往返转换、范围与 choices 应用、SPIN_BOX_2P 字符串格式化、非法值兜底、装配期错误。

### 不修改

- `src/config_manager/config_manager.py`（get/set/save/reload 流程不变）
- `src/ui/main_window.py`（`SettingsDialog(parent, config_manager=...)` 调用不变）
- 配置文件格式、`SETTINGS_VERSION`、迁移逻辑
- 向量数据库与其他业务服务

## 实施顺序

### 阶段 0：锁定基线

1. `git status` 确认不覆盖并行会话改动；记录当时 HEAD。
2. 用 `.venv/Scripts/python.exe -m unittest` 运行设置相关测试（`test_settings_dialog`、`test_settings_color_preset_manager`、`test_config_manager`），记录因并行开发导致的前置失败，避免误判回归。

### 阶段 1：schema 扩展

1. 实现 `WidgetKind`、`FieldUI`、`ConfigField.ui` 及校验。
2. 导出新符号；补充/运行 `src/config_manager/tests/test_config_manager.py`。

### 阶段 2：schema 标注

1. 在 `services/settings.py` 增加页面常量并标注 `ui`。
2. 断言所有可见字段的 label/范围与现 `.ui` 一致（对照迁移清单）。

### 阶段 3：绑定模块

1. 实现 `settings_field_bindings.py` 四种绑定的构建/读写/信号。
2. 完成 `tests/test_settings_field_bindings.py`。

### 阶段 4：对话框重写与 `.ui` 精简

1. 精简 `.ui` 外壳。
2. 重写 `dialog_settings.py`：页面/分组装配、字段接线、自定义页面注册表、`field_widget()`。
3. `ColorPresetManagerWidget.reload_presets` → `reload_settings`，同步其测试。

### 阶段 5：测试更新与回归

1. 更新 `tests/test_settings_dialog.py` 至新访问方式与新断言。
2. 运行设置相关定向测试，再跑全量 `unittest`。
3. 手工冒烟：打开设置 → 修改各类控件 → 应用 → 重开对话框确认持久化；取消不落盘；模拟磁盘错误验证回滚提示。

## 测试矩阵

| 场景 | 预期 |
|---|---|
| 加载已存值 | 各 `field_widget(key)` 显示对应值（int/str/bool） |
| SPIN_BOX_2P 往返 | 配置 "0.42" → 显示 0.42 → 保存回 "0.42"；"0.33" 同理 |
| SPIN_BOX 范围 | min/max/step/suffix 按 `FieldUI` 生效 |
| COMBO_BOX | choices 文本/存储值正确；切换后保存 `currentData` |
| CHECK_BOX | 布尔往返；`toggled` 触发脏标记 |
| 隐藏字段 | 无 `field_widget`；保存前后值不变 |
| 页面与顺序 | 列表项数量/标题、页内分组与行序等于 schema 出现顺序 |
| objectName | 形如 `field_<key>` |
| 修改任一控件 | Apply 按钮启用 |
| Apply | 写入 manager 并落盘，按钮禁用，对话框保持打开 |
| Cancel | 不落盘，对话框拒绝关闭码 Rejected |
| Ok | 落盘并 Accepted |
| 保存失败 | 弹窗提示、manager 回滚、自定义页 reload、对话框保持打开 |
| 自定义页 changed | 触发脏标记；Apply 时 `save_settings` 被调用 |
| choices 缺失 / 组合非法 | 装配期抛出明确异常 |

## 验收标准

1. 新增带 `ui` 的 schema 字段无需改动对话框代码即可出现在正确页面、分组和位置。
2. 四种控件类型与 HIDDEN 语义符合本方案；`field_<key>` 命名与 `field_widget(key)` 可用。
3. 保存/应用仅操作 `ConfigManager` 与配置文件；失败回滚行为与现状一致。
4. 颜色预设页面功能与现状等同，仅方法名对齐契约。
5. `dialog_settings.ui` 不再包含具体设置控件。
6. 定向测试与全量 `unittest` 通过（不含并行会话导致的既有失败）。

## 已接受的权衡

- `SPIN_BOX_2P` 固定两位小数，不支持任意精度；将来需要时再增枚举值。
- UI 元数据进入通用 `config_manager` 层，接受轻微的关注点混合，换取单一事实来源。
- 旧的驼峰控件名不再兼容，测试与外部引用一律改走 `field_widget(key)`。
- 页面/分组的视觉结构由代码生成，牺牲 `.ui` 可视化编辑能力，换取零重复声明。
- 保存后不做组件级刷新，依赖“重启或既有机制生效”（与现状相同）。

## 实施起点

从阶段 0 开始：先在并行会话合并后锁定基线并记录前置失败，再按 schema 扩展 → 标注 → 绑定 → 对话框重写 → 测试回归的顺序推进。
