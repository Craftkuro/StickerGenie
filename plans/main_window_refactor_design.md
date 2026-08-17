# 主窗口重构设计方案（控制器抽取）

状态：实施已完成（2026-08-17）

日期：2026-08-17

## 1. 目标与范围

主窗口 `src/ui/main_window.py` 目前为 772 行，类主体约 717 行。它同时承担了窗口装配、
搜索控制、四个长时间运行任务的完整流程（入口、对话框生命周期、进度、终态处理、
消息弹窗）、标签页管理和调试视图，复杂度已超出单一类的合理范围。

本次重构目标：

- 把四个后台任务流程（图片导入、图库导出、图库备份导入、数据库维护）从
  `MainWindow` 中整体抽离为独立控制器类；
- `MainWindow` 只保留窗口装配、菜单/按钮接线、搜索、标签页、调试视图；
- 纯结构调整：不改任何用户可见行为、文案、信号语义，不引入新功能；
- 保持代码结构精简易读，优先简单设计。

## 2. 现状梳理

`MainWindow` 当前关注点分布（行号为 `src/ui/main_window.py` 现状）：

| 关注点 | 行范围 | 约行数 | 说明 |
| --- | --- | --- | --- |
| 初始化与服务装配 | 50–177 | 128 | 创建 4 个 service、信号连线、菜单槽位 |
| 搜索控制 | 178–235、358–391 | 92 | 搜索类型、建议、触发、历史保存 |
| 设置/标签管理入口 | 236–251 | 16 | 两个普通对话框 |
| 数据库维护流程 | 252–357 | 106 | 对话框生命周期 + 服务 + 进度 + 终态 |
| 图片导入流程 | 392–494 | 103 | 对话框 + 服务 + 进度 + 终态 |
| 图库导出流程 | 495–538 | 44 | 选目录 + 服务 + 进度 + 终态（无进度对话框） |
| 图库备份导入流程 | 539–680 | 142 | 选文件 + 预检 + 确认框 + 互斥 + 进度 + 终态 |
| 标签页管理 | 681–728 | 48 | add/close tab |
| 调试视图 | 729–760 | 32 | 开发工具、测试视图 |

四个后台流程合计约 **395 行**，占类主体约 55%。它们结构高度一致：

1. 菜单/按钮入口（文件选择对话框或配置对话框）；
2. `__init__` 中创建 service 并连接 4 个信号（finished/cancelled/failed/progress）；
3. 启动：禁用写入口、状态栏提示、`start_xxx()`，异常转失败处理；
4. 进度：刷新进度对话框 + 状态栏；
5. 终态（完成/中止/失败）：关闭对话框、恢复入口、刷新图库视图/搜索建议、
   状态栏消息 + `QMessageBox`。

## 3. 重构方案

### 3.1 新增控制器包 `src/ui/operations/`

每个后台流程一个控制器类，文件与类一一对应：

| 文件 | 类 | 承接的现有方法 |
| --- | --- | --- |
| `image_import_controller.py` | `ImageImportController` | `basic_import_files`、`handle_import_images_request`、`_on_import_images_*`、`_close_image_import_progress_dialog` |
| `library_export_controller.py` | `LibraryExportController` | `export_library`、`_on_export_library_*` |
| `library_import_controller.py` | `LibraryImportController` | `import_library_backup`、`_confirm_library_import`、`_on_import_library_*`、`_on_import_cancel_requested`、`_finish_library_import`、`_library_import_summary`、`_refresh_after_library_import`、`_close_library_import_progress_dialog` |
| `database_maintenance_controller.py` | `DatabaseMaintenanceController` | `open_database_maintenance`、`start_database_maintenance`、`_on_database_maintenance_*`、`_close/_release_database_maintenance_dialog`、`_database_maintenance_summary` |

控制器职责：

- 持有 service 与进度对话框引用（如 `self._dialog`）；
- 在 `__init__` 中连接 service 的 4 个信号；
- 实现入口方法（文件选择、预检、确认、启动）与全部进度/终态槽；
- 通过构造时传入的 `window` 引用与主窗口交互，只使用以下稳定接口：
  - `window.statusBar()`（Qt 公共 API）；
  - `window.set_write_actions_enabled(enabled)`（保留在主窗口，见 3.2）；
  - `window.customSearchBox.refresh_suggestions()`；
  - `services.sticker_library_viewer_service.wiring.slot_refresh_content()`（模块级）。

控制器为普通类即可，不需要继承 `QObject`：服务信号已承载跨线程回传，
控制器自身不需要定义信号。

### 3.2 主窗口保留的职责

瘦身后的 `MainWindow` 只保留：

- `__init__`：创建 4 个 service 与 4 个控制器，把菜单/按钮 action 连接到控制器入口；
- 搜索控制（`_init_search_controls`、`_on_search_type_changed`、`on_search_triggered`、
  `closeEvent` 保存历史）；
- 设置/标签管理入口（`open_settings`、`open_tag_manager`）；
- 标签页管理（`add_new_tab`、`_remove_tab_close_button`、`_on_tab_close_requested`、
  `add_new_tab_debug`）；
- 开发者工具与调试视图（`_setup_developer_tools`、`_setup_main_menu_button`、
  `debug_start_test_view`、`custom_tag_widget_test`）；
- `set_write_actions_enabled`：操作 5 个写入口的启用/禁用，属于窗口自己的 action
  集合，保留在主窗口并作为控制器的公共接口。

预计主窗口降到约 250～300 行。

### 3.3 不抽通用基类

四个流程的共性（对话框生命周期 + 终态收尾）可以用一个基类收敛，但：

- 图库导出没有进度对话框，行为与其他三个流程不同；
- 各流程的信号名、进度格式、终态文案、刷新动作均不同；
- 抽基类预计每控制器只省 20～30 行，却引入一层继承间接性。

按本项目“尽可能简单的设计”原则，**先不抽基类**，四个控制器独立实现、直接对照
现有方法迁移；若实施后发现重复明显，再评估收敛。

## 4. 测试影响与迁移

现有测试通过 `MainWindow.xxx(window, ...)` 静态方式直接调用流程方法，重构后这些
方法移到控制器，测试需同步迁移：

| 现有测试文件 | 迁移方式 |
| --- | --- |
| `test_main_window_image_import.py` | 改名 `test_image_import_controller.py`，改为调用 `ImageImportController` |
| `test_main_window_library_export.py` | 改名 `test_library_export_controller.py` |
| `test_main_window_library_import.py` | 大部分迁到 `test_library_import_controller.py`；保留少量主窗口集成用例（action 接线到控制器入口、`set_write_actions_enabled`） |
| `test_main_window_database_maintenance.py` | 改名 `test_database_maintenance_controller.py` |
| `test_main_window_search_box.py`、`test_main_window_tabs.py`、`test_main_window_main_menu.py`、`test_main_window_developer_tools.py` | 基本不动，仅适配接线变化 |

迁移后新增少量集成断言：菜单 action 触发后调用的是对应控制器入口方法。

## 5. 实施步骤

1. 新建 `src/ui/operations/` 与四个控制器，按“导出 → 数据库维护 → 图片导入 →
   图库备份导入”的顺序逐个迁移（每个流程迁移完立即跑对应测试）；
2. 迁移/新增测试文件，补充 action 接线集成用例；
3. 瘦身 `MainWindow`：删除已迁移方法，把 `setup_base_slots` 改为连接控制器入口；
4. 全量回归测试；
5. 代码走查：确认无重复逻辑残留、无未使用 import、行为与文案零变化。

## 6. 确认结果

1. 控制器位置与命名：采用 `src/ui/operations/` + `XxxController`（已确认）。
2. 不抽通用基类，四个控制器独立实现（已确认，见 3.3）。
3. 测试按第 4 节迁移（已确认）。
4. 本次为纯结构调整，不改变任何用户可见行为（已确认）。

