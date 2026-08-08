import logging
import time
from traceback import format_tb
from typing import Optional

from PyQt6.QtCore import pyqtSignal, pyqtSlot, QPoint, QEvent, Qt, QSize
from PyQt6.QtWidgets import QMainWindow, QPushButton, QMessageBox, QWidget, QLabel, QVBoxLayout, \
    QHBoxLayout, QListWidget, QListWidgetItem, QFrame, QLineEdit, QLayout, QCompleter, \
    QStyledItemDelegate, QStyleOptionViewItem, QListView, QStyle, QFileDialog
from PyQt6 import uic
from PyQt6.QtGui import QFont, QPainter, QStandardItemModel, QStandardItem

import apppath
from commons.signal_objects import ImportImagesRequest, MainWindowNewTabRequest
import services.export_library
import services.global_instances
import services.import_images
import services.sticker_view_service_debug
import services.sticker_library_viewer_service

from .widgets.custom_tag_widget import CustomTagWidget
from .sticker_list_view_widget import StickerListView
from .dialog_image_import import ImageImportDialog
from .dialog_image_import_progress import ImageImportProgressDialog
from .dialog_settings import SettingsDialog

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

        self._image_import_service = services.import_images.ImageImportService(
            self
        )
        self._image_import_service.import_finished.connect(
            self._on_import_images_finished
        )
        self._image_import_service.import_failed.connect(
            self._on_import_images_failed
        )
        self._image_import_service.import_progress_changed.connect(
            self._on_import_images_progress_changed
        )
        self._image_import_progress_dialog = None

        self._library_export_service = services.export_library.LibraryExportService(
            self
        )
        self._library_export_service.export_finished.connect(
            self._on_export_library_finished
        )
        self._library_export_service.export_failed.connect(
            self._on_export_library_failed
        )
        self._library_export_service.export_progress_changed.connect(
            self._on_export_library_progress_changed
        )

        self.setup_base_slots()

        # 加载启动时需要准备的视图
        # self.setup_startup_views()
        # self.debug_start_test_repo_view()

        # 配置快速查询按钮

        # self.populate_quick_launch_buttons(QUICK_LAUNCH_BUTTON_COUNT)
        self.debug_start_test_view()

    def setup_base_slots(self):
        self.pushButtonAddSticker.clicked.connect(self.basic_import_files)
        self.customSearchBox.searched.connect(self.on_search_triggered)
        self.actionImportImages.triggered.connect(self.basic_import_files)
        self.actionExportLibrary.triggered.connect(self.export_library)
        self.actionOpenSettings.triggered.connect(self.open_settings)

        self.signal_add_new_tab.connect(self.add_new_tab)

    def open_settings(self):
        SettingsDialog(self).exec()

    def on_search_triggered(self, query):
        """处理搜索触发事件"""
        logger.info(f"用户触发搜索：{query}")
        # TODO: 实现实际的搜索逻辑
        # 这里可以添加搜索结果显示、标签过滤等功能

    def basic_import_files(self):
        dialog = ImageImportDialog(self)
        dialog.signal_import_requested.connect(
            self.handle_import_images_request,
            type=Qt.ConnectionType.QueuedConnection,
        )
        dialog.exec()

    @pyqtSlot(ImportImagesRequest)
    def handle_import_images_request(self, request: ImportImagesRequest):
        progress_dialog = ImageImportProgressDialog(self)
        self._image_import_progress_dialog = progress_dialog
        progress_dialog.open()
        self._image_import_service.start_import(request)
        self.statusBar().showMessage("正在导入图片…")

    @pyqtSlot(object)
    def _on_import_images_progress_changed(self, progress):
        dialog = self._image_import_progress_dialog
        if dialog is not None:
            dialog.update_progress(progress)

    def _close_image_import_progress_dialog(self):
        dialog = self._image_import_progress_dialog
        self._image_import_progress_dialog = None
        if dialog is not None:
            dialog.finish()
            dialog.deleteLater()

    @pyqtSlot(object)
    def _on_import_images_finished(self, result):
        self._close_image_import_progress_dialog()
        imported_count = len(result.imported_stickers)
        if imported_count:
            services.sticker_library_viewer_service.wiring.slot_refresh_content()

        message = f"已导入 {imported_count} 张图片"
        if result.vectorized_count:
            message += f"，生成 {result.vectorized_count} 个向量"
        self.statusBar().showMessage(message, 8000)

        QMessageBox.information(
            self,
            "导入完成",
            f"已导入 {imported_count} 张图片，"
            f"另有 {result.duplicate_count} 个重复图片未导入。",
        )

        if result.vector_errors:
            details = "\n".join(result.vector_errors[:10])
            remaining = len(result.vector_errors) - 10
            if remaining > 0:
                details += f"\n另有 {remaining} 项未显示。"
            QMessageBox.warning(
                self,
                "部分向量未生成",
                details,
            )

    @pyqtSlot(str)
    def _on_import_images_failed(self, error_message: str):
        self._close_image_import_progress_dialog()
        self.statusBar().clearMessage()
        QMessageBox.critical(self, "导入失败", error_message)

    def export_library(self):
        destination = QFileDialog.getExistingDirectory(
            self,
            "选择导出目录",
            "",
        )
        if not destination:
            return

        self.actionExportLibrary.setEnabled(False)
        self.statusBar().showMessage("正在导出图库…")
        try:
            self._library_export_service.start_export(destination)
        except Exception as exc:
            self.actionExportLibrary.setEnabled(True)
            self.statusBar().clearMessage()
            QMessageBox.critical(self, "导出失败", str(exc))

    @pyqtSlot(object)
    def _on_export_library_progress_changed(self, progress):
        message = progress.status
        if progress.total:
            message += f"（{progress.completed}/{progress.total}）"
        self.statusBar().showMessage(message)

    @pyqtSlot(object)
    def _on_export_library_finished(self, result):
        self.actionExportLibrary.setEnabled(True)
        self.statusBar().showMessage(
            f"已导出 {result.image_count} 个图片和 {result.tag_count} 个标签",
            8000,
        )
        QMessageBox.information(
            self,
            "导出完成",
            f"导出完成，已导出{result.image_count}个图片和{result.tag_count}个标签。",
        )

    @pyqtSlot(str)
    def _on_export_library_failed(self, error_message: str):
        self.actionExportLibrary.setEnabled(True)
        self.statusBar().clearMessage()
        QMessageBox.critical(self, "导出失败", error_message)

    def add_new_tab(self, request: MainWindowNewTabRequest):
        index = self.tabWidget.addTab(request.widget, request.title)
        self.tabWidget.setCurrentIndex(index)

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


