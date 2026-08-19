import logging
import sys
from typing import Optional

from PyQt6.QtCore import Qt, pyqtSignal, pyqtSlot
from PyQt6.QtWidgets import QMainWindow, QMessageBox, QWidget, QVBoxLayout, \
    QComboBox, QSizePolicy, QTabBar, QMenu
from PyQt6 import uic
from PyQt6.QtGui import QAction, QCloseEvent, QStandardItemModel, QStandardItem

import apppath
from commons.signal_objects import MainWindowNewTabRequest
import services.export_library
import services.import_library
import services.global_instances
import services.sticker_library_viewer_service
import services.search

from .widgets.custom_tag_widget import CustomTagWidget
from .dialog_about import AboutDialog
from .dialog_settings import SettingsDialog
from .dialog_tag_manager import TagManagerDialog
from .operations.database_maintenance_controller import DatabaseMaintenanceController
from .operations.image_import_controller import ImageImportController
from .operations.library_export_controller import LibraryExportController
from .operations.library_import_controller import LibraryImportController
from services.database_maintenance_service import DatabaseMaintenanceService
from services.image_import_service import ImageImportService
from services.settings import create_settings_manager

logger = logging.getLogger(__name__)


class MainWindow(QMainWindow):
    signal_send_user_error_alert = pyqtSignal(str)
    signal_add_new_tab = pyqtSignal(MainWindowNewTabRequest)
    """
    应用程序主窗体
    """

    def __init__(self, settings_manager=None):
        super().__init__()

        services.global_instances.main_window = self

        # 加载基础 UI
        ui_file_path = apppath.app_path / 'ui' / 'main_window.ui'
        uic.loadUi(ui_file_path, self)
        self._setup_developer_tools()
        self._setup_main_menu_button()

        self._settings_manager = settings_manager or create_settings_manager()
        self._search_history = services.search.SearchHistory(
            self._settings_manager.get("recent_searches")
        )
        self._init_search_controls()

        self._image_import_service = ImageImportService(self)
        self._image_import_controller = ImageImportController(
            self, self._image_import_service
        )

        self._library_export_service = services.export_library.LibraryExportService(
            self
        )
        self._library_export_controller = LibraryExportController(
            self, self._library_export_service
        )

        self._library_import_service = services.import_library.LibraryImportService(
            self
        )
        self._library_import_controller = LibraryImportController(
            self, self._library_import_service
        )

        self._database_maintenance_service = DatabaseMaintenanceService(self)
        self._database_maintenance_controller = DatabaseMaintenanceController(
            self, self._database_maintenance_service
        )

        self.setup_base_slots()

        # 加载启动时需要准备的视图
        # self.setup_startup_views()
        # self.debug_start_test_repo_view()

        # 配置快速查询按钮

        # self.populate_quick_launch_buttons(QUICK_LAUNCH_BUTTON_COUNT)
        self.debug_start_test_view()

    def _setup_developer_tools(self):
        if getattr(sys, "frozen", False):
            return

        self.menu_6 = self.menuBar().addMenu("开发工具")
        self.menu_6.setObjectName("menu_6")

        self.actionCustomDebug = QAction("自定义调试操作", self)
        self.actionCustomDebug.setObjectName("actionCustomDebug")
        self.actionCustomDebug.setMenuRole(QAction.MenuRole.NoRole)
        self.menu_6.addAction(self.actionCustomDebug)

    def _setup_main_menu_button(self):
        """隐藏菜单栏，并把同一组菜单挂到 Menu 按钮上。"""
        self.menuBar().hide()
        main_menu = QMenu(self)
        main_menu.setObjectName("mainMenuPopup")
        for menu_action in self.menuBar().actions():
            submenu = menu_action.menu()
            if submenu is not None:
                main_menu.addMenu(submenu)
        self.pushButtonMainMenu.setMenu(main_menu)

    def setup_base_slots(self):
        self.pushButtonAddSticker.clicked.connect(
            self._image_import_controller.basic_import_files
        )
        self.pushButtonTagManager.clicked.connect(self.open_tag_manager)
        self.customSearchBox.searched.connect(self.on_search_triggered)
        self.actionImportImages.triggered.connect(
            self._image_import_controller.basic_import_files
        )
        self.actionImportRepoBackup.triggered.connect(
            self._library_import_controller.import_library_backup
        )
        self.actionExportLibrary.triggered.connect(
            self._library_export_controller.export_library
        )
        self.actionOpenSettings.triggered.connect(self.open_settings)
        self.actionOpenAbout.triggered.connect(self.open_about)
        self.actionOpenTagManager.triggered.connect(self.open_tag_manager)
        self.actionStartDatabaseMaintenance.triggered.connect(
            self._database_maintenance_controller.open_database_maintenance
        )
        self.actionQuit.triggered.connect(self.close)

        self.signal_add_new_tab.connect(self.add_new_tab)
        self.tabWidget.tabCloseRequested.connect(self._on_tab_close_requested)

    def raise_and_activate(self) -> None:
        """Restore this window and bring it to the foreground."""
        if self.windowState() & Qt.WindowState.WindowMinimized:
            self.setWindowState(Qt.WindowState.WindowNoState)
        self.show()
        self.raise_()
        self.activateWindow()

    def _init_search_controls(self):
        self.searchTypeComboBox = QComboBox(self.widgetUnifiedBar)
        self.searchTypeComboBox.setObjectName("searchTypeComboBox")
        self.searchTypeComboBox.setAccessibleName("搜索类型")
        self.searchTypeComboBox.addItem("标签", services.search.SearchType.TAG.value)
        self.searchTypeComboBox.addItem("文本", services.search.SearchType.TEXT.value)
        self.searchTypeComboBox.addItem("原名", services.search.SearchType.FILENAME.value)
        self.searchTypeComboBox.addItem(
            "高级",
            services.search.SearchType.ADVANCED.value,
        )
        self.searchTypeComboBox.setSizeAdjustPolicy(
            QComboBox.SizeAdjustPolicy.AdjustToContents
        )
        self.searchTypeComboBox.setSizePolicy(
            QSizePolicy.Policy.Fixed,
            QSizePolicy.Policy.Fixed,
        )

        unified_bar_layout = self.widgetUnifiedBar.layout()
        search_box_index = unified_bar_layout.indexOf(self.customSearchBox)
        unified_bar_layout.insertWidget(search_box_index, self.searchTypeComboBox)

        self._search_suggestions_provider = (
            services.search.SearchSuggestionsProvider(
                self._search_history,
                self._settings_manager,
                self._current_search_type,
                self,
            )
        )
        self.customSearchBox.set_suggestions_provider(
            self._search_suggestions_provider
        )
        self.searchTypeComboBox.currentIndexChanged.connect(
            self._on_search_type_changed
        )
        self._on_search_type_changed(self.searchTypeComboBox.currentIndex())

    def _current_search_type(self):
        return services.search.SearchType(
            self.searchTypeComboBox.currentData()
        )

    @pyqtSlot(int)
    def _on_search_type_changed(self, _index: int):
        current_search_type = self._current_search_type()
        is_tag_search = (
            current_search_type is services.search.SearchType.TAG
        )
        self.customSearchBox.set_submit_first_suggestion_when_unselected(
            is_tag_search
        )
        if current_search_type is services.search.SearchType.TAG:
            placeholder = "搜索标签..."
        elif current_search_type is services.search.SearchType.FILENAME:
            placeholder = "搜索图片文件名..."
        elif current_search_type is services.search.SearchType.ADVANCED:
            placeholder = "搜索高级表达式..."
        else:
            placeholder = "搜索图片文本..."
        self.customSearchBox.line_edit.setPlaceholderText(placeholder)
        self.customSearchBox.refresh_suggestions()

    def open_settings(self):
        SettingsDialog(
            self,
            config_manager=self._settings_manager,
        ).exec()
        self.customSearchBox.refresh_suggestions()

    def open_about(self):
        AboutDialog(self).exec()

    def open_tag_manager(self):
        database = services.global_instances.current_library_db
        if database is None:
            QMessageBox.warning(self, "无法打开", "仓库数据库尚未初始化。")
            return

        TagManagerDialog(self, database=database).exec()
        self.customSearchBox.refresh_suggestions()

    def set_write_actions_enabled(self, enabled: bool) -> None:
        self.actionImportRepoBackup.setEnabled(enabled)
        self.actionImportImages.setEnabled(enabled)
        self.actionExportLibrary.setEnabled(enabled)
        self.actionStartDatabaseMaintenance.setEnabled(enabled)
        self.pushButtonAddSticker.setEnabled(enabled)

    def on_search_triggered(self, query):
        """处理搜索触发事件"""
        search_type = self._current_search_type()
        logger.info("用户触发%s搜索：%s", search_type.value, query)
        self._search_history.record(query)
        try:
            result_count = services.search.open_search_results(
                search_type,
                query,
            )
        except Exception as exc:
            logger.exception("搜索失败")
            QMessageBox.warning(self, "搜索失败", str(exc))
            return

        if result_count:
            message = f"找到 {result_count} 张匹配图片"
        else:
            message = "未找到匹配图片"
        self.statusBar().showMessage(message, 8000)

    def closeEvent(self, event: QCloseEvent):
        super().closeEvent(event)
        if not event.isAccepted():
            return
        try:
            self._settings_manager.set(
                "recent_searches",
                self._search_history.values(),
            )
            self._settings_manager.save()
        except Exception:
            logger.exception("保存最近搜索失败")

    def add_new_tab(self, request: MainWindowNewTabRequest):
        index = self.tabWidget.addTab(request.widget, request.title)
        tab_bar = self.tabWidget.tabBar()
        tab_bar.setTabData(index, request.closable)
        if not request.closable:
            self._remove_tab_close_button(index)
        self.tabWidget.setCurrentIndex(index)

    def _remove_tab_close_button(self, index: int):
        tab_bar = self.tabWidget.tabBar()
        for position in (
            QTabBar.ButtonPosition.LeftSide,
            QTabBar.ButtonPosition.RightSide,
        ):
            button = tab_bar.tabButton(index, position)
            if button is None:
                continue
            tab_bar.setTabButton(index, position, None)
            button.deleteLater()

    @pyqtSlot(int)
    def _on_tab_close_requested(self, index: int):
        if index < 0 or index >= self.tabWidget.count():
            return

        if not bool(self.tabWidget.tabBar().tabData(index)):
            return

        page = self.tabWidget.widget(index)
        self.tabWidget.removeTab(index)
        if page is not None:
            page.deleteLater()

    def add_new_tab_debug(self, center_widget, tab_title: Optional[str] = None):
        """
        在主窗口中打开新的标签页，并将指定的widget放置于此标签中心。
        :param center_widget:
        :return:
        """
        container = TabWidgetContainer()
        container.layout().addWidget(center_widget)

        self.tabWidget.addTab(container, tab_title)

    def debug_start_test_view(self):
        services.sticker_library_viewer_service.open_sticker_library_view_tab()

        # self.custom_tag_widget_test()

    def custom_tag_widget_test(self):
        """
        在 MainWindow 的 centralwidget 的 layout 中添加 CustomTagWidget 测试组件
        """
        # 创建包含水果名称的硬编码 Model
        model = QStandardItemModel()
        fruits = ["苹果", "香蕉", "橙子", "葡萄", "草莓", "西瓜", "芒果", "猕猴桃", "菠萝", "蓝莓", "我是五个字"]
        for fruit in fruits:
            model.appendRow(QStandardItem(fruit))
        
        # 创建 CustomTagWidget
        tag_widget = CustomTagWidget(model=model)
        
        # 获取 centralwidget 并添加到其 layout
        central_widget = self.centralwidget
        if central_widget:
            layout = central_widget.layout()
            if layout:
                layout.insertWidget(2, tag_widget)
            else:
                # 如果没有 layout，创建一个
                central_widget.setLayout(QVBoxLayout())
                central_widget.layout().addWidget(tag_widget)
        else:
            logger.warning("MainWindow 没有 centralwidget 属性")


class TabWidgetContainer(QWidget):
    """
    一个用来包装内部中心Widget的容器，
    用于将这个widget插入到tabWidget中作为一个新标签页的内容。
    """
    def __init__(self):
        super().__init__()

        layout = QVBoxLayout()
        self.setLayout(layout)
