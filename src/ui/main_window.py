import logging
import time
from traceback import format_tb
from typing import Optional

from PyQt6.QtCore import pyqtSignal, pyqtSlot, QPoint, QEvent, Qt, QSize
from PyQt6.QtWidgets import QMainWindow, QPushButton, QDialog, QMessageBox, QWidget, QLabel, QVBoxLayout, \
    QHBoxLayout, QListWidget, QListWidgetItem, QFrame, QLineEdit, QComboBox, QLayout, QCompleter, \
    QStyledItemDelegate, QStyleOptionViewItem, QListView, QStyle
from PyQt6 import uic
from PyQt6.QtGui import QFont, QPainter, QStandardItemModel, QStandardItem

import apppath
from commons.signal_objects import MainWindowNewTabRequest
import services.global_instances
import services.sticker_view_service_debug
import services.sticker_library_viewer_service

from .widgets.custom_search_box import CustomSearchBox
from .widgets.custom_tag_widget import CustomTagWidget
from .sticker_list_view_widget import StickerListView
from .dialog_image_import import ImageImportDialog

logger = logging.getLogger(__name__)

# 配置常用关键词按钮个数
QUICK_LAUNCH_BUTTON_COUNT = 6


class MainWindow(QMainWindow):
    signal_send_user_error_alert = pyqtSignal(str)
    signal_add_new_tab = pyqtSignal(MainWindowNewTabRequest)
    """
    应用程序主窗体
    """

    def __init__(self):
        super().__init__()

        services.global_instances.main_window = self

        # 加载基础 UI
        ui_file_path = apppath.app_path / 'ui' / 'main_window.ui'
        uic.loadUi(ui_file_path, self)

        self.setup_base_slots()

        # 初始化自定义搜索框，替换原有的 comboBox
        self._init_custom_search_box()

        # 加载启动时需要准备的视图
        # self.setup_startup_views()
        # self.debug_start_test_repo_view()

        # 配置快速查询按钮

        # self.populate_quick_launch_buttons(QUICK_LAUNCH_BUTTON_COUNT)
        self.debug_start_test_view()

    def _init_custom_search_box(self):
        """初始化自定义搜索框，替换原有的 comboBox"""
        # 查找原有的 comboBox 控件
        # old_combo_box = self.findChild(QComboBox, "comboBox")

        # 创建自定义搜索框
        self.comboBox = CustomSearchBox(self)

        # if old_combo_box:
        # 获取原有 comboBox 的父布局和索引
        self.widgetUnifiedBar.layout().insertWidget(3, self.comboBox)
        # parent_widget = old_combo_box.parentWidget()
        # if parent_widget:
        #    layout = parent_widget.layout()
        #    if layout:
        #        # 在布局中查找原有的 comboBox 位置
        #        for i in range(layout.count()):
        #            item = layout.itemAt(i)
        #            if item and item.widget() == old_combo_box:
        #                # 找到原有 comboBox，记录其位置
        #                # 隐藏原有 comboBox
        #                old_combo_box.hide()
        #                # 在相同位置插入新的自定义搜索框
        #                layout.insertWidget(i, self.comboBox)
        #                break

        # 连接搜索信号
        self.comboBox.searched.connect(self.on_search_triggered)

    def setup_base_slots(self):
        self.pushButtonAddSticker.clicked.connect(self.basic_import_files)
        self.actionImportImages.triggered.connect(self.basic_import_files)

        self.signal_add_new_tab.connect(self.add_new_tab)

    def on_search_triggered(self, query):
        """处理搜索触发事件"""
        logger.info(f"用户触发搜索：{query}")
        # TODO: 实现实际的搜索逻辑
        # 这里可以添加搜索结果显示、标签过滤等功能

    def basic_import_files(self):
        dialog = ImageImportDialog(self)
        if dialog.exec() == QDialog.DialogCode.Accepted:
            services.sticker_library_viewer_service.wiring.slot_refresh_content()

    def add_new_tab(self, request: MainWindowNewTabRequest):
        self.tabWidget.addTab(request.widget, request.title)

    def add_new_tab_debug(self, center_widget, tab_title: Optional[str] = None):
        """
        在主窗口中打开新的标签页，并将指定的widget放置于此标签中心。
        :param center_widget:
        :return:
        """
        #self.tabWidget.addTab(QLabel('testaaaa'), 'test')
        container = TabWidgetContainer()
        #container.layout = QVBoxLayout()
        container.layout().addWidget(center_widget)
        #container.setLayout(container.layout)

        self.tabWidget.addTab(container, tab_title)


    def debug_start_test_view(self):
        #model = services.sticker_view_service_debug.start_debug_view()
        #debug_view_widget = StickerListView(model)
        #self.add_new_tab_debug(debug_view_widget, "测试")

        services.sticker_library_viewer_service.open_sticker_library_view_tab()

        self.custom_tag_widget_test()

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


