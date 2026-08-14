"""
CustomTagWidget - 自定义标签组件

基于 QWidget + QListView 的圆角矩形标签展示组件，支持自动换行和垂直滚动，
顶部带有添加/删除按钮的小型工具栏
"""

from typing import Optional

from PyQt6.QtCore import Qt, QSize, pyqtSlot
from PyQt6.QtWidgets import (
    QWidget,
    QVBoxLayout,
    QToolBar,
    QListView,
    QStyledItemDelegate,
    QStyleOptionViewItem,
    QStyle,
    QSizePolicy,
)
from PyQt6.QtGui import QAction, QPainter, QStandardItemModel, QPen, QBrush, QColor


TAG_ACCENT_COLOR_ROLE = Qt.ItemDataRole.UserRole + 1


class TagItemDelegate(QStyledItemDelegate):
    """
    标签项的自定义委托
    
    将每个 Item 渲染为圆角矩形的样式
    """
    
    def __init__(self, parent=None, bg_color: Optional[QColor] = None, 
                 text_color: Optional[QColor] = None, 
                 border_color: Optional[QColor] = None, 
                 corner_radius: int = 5):
        """
        初始化标签委托
        
        Args:
            parent: 父对象
            bg_color: 背景颜色，默认为浅蓝色 #E3F2FD
            text_color: 文本颜色，默认为深蓝色 #1565C0
            border_color: 边框颜色，默认为蓝色 #2196F3
            corner_radius: 圆角半径
        """
        super().__init__(parent)
        self._bg_color = bg_color or QColor("#E3F2FD")
        self._text_color = text_color or QColor("#1565C0")
        self._border_color = border_color or QColor("#2196F3")
        self._corner_radius = corner_radius
        
    @property
    def bg_color(self) -> QColor:
        return self._bg_color
    
    @bg_color.setter
    def bg_color(self, color: QColor):
        self._bg_color = color
        
    @property
    def text_color(self) -> QColor:
        return self._text_color
    
    @text_color.setter
    def text_color(self, color: QColor):
        self._text_color = color
        
    @property
    def border_color(self) -> QColor:
        return self._border_color
    
    @border_color.setter
    def border_color(self, color: QColor):
        self._border_color = color
    
    def paint(self, painter: QPainter, option: QStyleOptionViewItem, index):
        """
        重写 paint 方法，绘制圆角矩形标签
        
        Args:
            painter: 绘图器
            option: 视图项样式选项
            index: 模型索引
        """
        painter.save()
        
        # 获取绘制区域
        rect = option.rect
        
        # 调整内边距
        padding = 4
        tag_rect = rect.adjusted(padding, padding, -padding, -padding)
        
        # 判断是否被选中
        is_selected = option.state & QStyle.StateFlag.State_Selected
        is_hovered = option.state & QStyle.StateFlag.State_MouseOver
        
        accent_value = index.data(TAG_ACCENT_COLOR_ROLE)
        accent_color = QColor(accent_value) if accent_value else QColor()
        has_accent = accent_color.isValid()

        border_color = QColor(accent_color) if has_accent else self._border_color
        text_color = option.palette.text().color() if has_accent else self._text_color

        # 设置背景颜色
        if is_selected:
            bg_color = QColor(border_color)
            bg_color.setAlpha(90)
        elif is_hovered:
            bg_color = QColor(border_color)
            bg_color.setAlpha(55)
        elif has_accent:
            bg_color = QColor(border_color)
            bg_color.setAlpha(35)
        else:
            bg_color = self._bg_color
            
        # 绘制圆角矩形背景
        painter.setBrush(QBrush(bg_color))
        painter.setPen(QPen(border_color, 1))
        painter.drawRoundedRect(tag_rect, self._corner_radius, self._corner_radius)
        
        # 获取文本
        text = index.data(Qt.ItemDataRole.DisplayRole)
        if not text:
            text = index.data(Qt.ItemDataRole.EditRole) or ""
        
        # 绘制文本
        painter.setPen(text_color)
        painter.setBrush(Qt.BrushStyle.NoBrush)
        
        # 文本在标签内部居中
        text_rect = tag_rect.adjusted(8, 0, -8, 0)
        painter.drawText(
            text_rect,
            Qt.AlignmentFlag.AlignHCenter | Qt.AlignmentFlag.AlignVCenter,
            text
        )
        
        painter.restore()
    
    def sizeHint(self, option: QStyleOptionViewItem, index):
        """
        重写 sizeHint，计算每个标签的合适大小
        
        Args:
            option: 视图项样式选项
            index: 模型索引
            
        Returns:
            标签的推荐尺寸
        """
        # 获取文本内容
        text = index.data(Qt.ItemDataRole.DisplayRole)
        if not text:
            text = index.data(Qt.ItemDataRole.EditRole) or ""
        
        # 计算文本宽度
        font_metrics = option.fontMetrics
        text_width = font_metrics.horizontalAdvance(text)
        font_height = font_metrics.height()
        
        # 计算尺寸
        min_width = 60
        horizontal_padding = 24  # 左右各12px
        width = max(min_width, text_width + horizontal_padding)
        vertical_padding = 8  # 上下各4px
        height = max(28, font_height + vertical_padding)
        
        return QSize(width, height)


class CustomTagWidget(QWidget):
    """
    自定义标签组件
    
    容器组件：顶部为小型工具栏（添加/删除按钮），下方为 QListView，
    标签从左到右排列并自动换行显示。
    列表区域至少保留 MIN_HEIGHT 高度，并随内容自适应。
    
    Example:
        >>> model = QStandardItemModel()
        >>> model.appendRow(QStandardItem("Tag1"))
        >>> model.appendRow(QStandardItem("Tag2"))
        >>> model.appendRow(QStandardItem("Tag3"))
        >>> 
        >>> tag_widget = CustomTagWidget(model=model)
    """
    
    MIN_HEIGHT = 40
    
    def __init__(self, model: Optional[QStandardItemModel] = None, parent=None):
        """
        初始化自定义标签组件
        
        Args:
            model: QStandardItemModel 实例，包含要显示的标签数据
            parent: 父对象
        """
        super().__init__(parent)
        
        # 创建并设置自定义委托
        self._item_delegate = TagItemDelegate(self)
        self._list_view = QListView(self)
        self._list_view.setItemDelegate(self._item_delegate)
        self._list_view.setWrapping(True)
        
        # 创建工具栏并配置视图
        self._setup_toolbar()
        self._setup_view()
        
        # 设置模型
        if model is not None:
            self.setModel(model)
    
    def _setup_toolbar(self):
        """
        创建顶部的添加/删除工具栏
        """
        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(2)
        
        self.toolbar = QToolBar(self)
        self.toolbar.setObjectName("tagToolBar")
        self.toolbar.setMovable(False)
        self.toolbar.setFloatable(False)
        self.toolbar.setToolButtonStyle(Qt.ToolButtonStyle.ToolButtonTextOnly)
        
        # 添加按钮
        self.add_action = QAction("➕", self)
        self.add_action.setObjectName("addTagAction")
        self.add_action.setToolTip("添加标签")
        self.toolbar.addAction(self.add_action)
        
        # 删除按钮
        self.delete_action = QAction("🗑️", self)
        self.delete_action.setObjectName("deleteTagAction")
        self.delete_action.setToolTip("删除标签")
        self.toolbar.addAction(self.delete_action)
        
        layout.addWidget(self.toolbar)
        layout.addWidget(self._list_view, 1)
        
    def _setup_view(self):
        """
        配置 QListView 的视图属性
        """
        # 设置为流式布局模式（从左到右，自动换行）
        self._list_view.setFlow(QListView.Flow.LeftToRight)
        
        # 设置视图的调整模式为 Adjust，允许动态调整
        self._list_view.setResizeMode(QListView.ResizeMode.Adjust)
        
        # 隐藏水平滚动条（允许自动换行）
        self._list_view.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        
        # 垂直滚动条需要时显示
        self._list_view.setVerticalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAsNeeded)
        
        # 设置边框样式
        self._list_view.setFrameShape(QListView.Shape.NoFrame)
        
        # 设置列表区域的最小高度，避免内容较少时被压缩
        self._list_view.setMinimumHeight(self.MIN_HEIGHT)
        
        # 设置尺寸策略
        # 水平方向可以拉伸，垂直方向按内容所需高度自适应
        self.setSizePolicy(
            QSizePolicy.Policy.Expanding,
            QSizePolicy.Policy.Expanding
        )
        self._list_view.setSizePolicy(
            QSizePolicy.Policy.Expanding,
            QSizePolicy.Policy.Expanding
        )
    
    def setModel(self, model: QStandardItemModel):
        """
        重写 setModel，设置模型并连接信号
        
        Args:
            model: QStandardItemModel 实例
        """
        self._list_view.setModel(model)
        
        # 连接模型变化信号
        if model:
            model.dataChanged.connect(self._on_model_data_changed)
            model.rowsInserted.connect(self._on_model_rows_changed)
            model.rowsRemoved.connect(self._on_model_rows_changed)
    
    def model(self) -> Optional[QStandardItemModel]:
        """
        返回内部 QListView 当前使用的模型
        """
        return self._list_view.model()

    def selectedIndexes(self):
        """返回当前选中的标签索引。"""
        return self._list_view.selectedIndexes()
    
    @pyqtSlot()
    def _on_model_data_changed(self):
        """模型数据变化时的处理"""
        self._list_view.updateGeometry()
        self.updateGeometry()
        
    @pyqtSlot()
    def _on_model_rows_changed(self):
        """模型行变化时的处理"""
        self._list_view.updateGeometry()
        self.updateGeometry()
    
    def set_tag_colors(self, bg_color: Optional[QColor] = None, 
                       text_color: Optional[QColor] = None, 
                       border_color: Optional[QColor] = None):
        """
        设置标签的颜色方案
        
        Args:
            bg_color: 背景颜色
            text_color: 文本颜色
            border_color: 边框颜色
        """
        if bg_color is not None:
            self._item_delegate.bg_color = bg_color
        if text_color is not None:
            self._item_delegate.text_color = text_color
        if border_color is not None:
            self._item_delegate.border_color = border_color
            
        # 触发视图重绘
        viewport = self._list_view.viewport()
        if viewport is not None:
            viewport.update()
    
    def set_corner_radius(self, radius: int):
        """
        设置圆角半径
        
        Args:
            radius: 圆角半径（像素）
        """
        self._item_delegate._corner_radius = radius
        viewport = self._list_view.viewport()
        if viewport is not None:
            viewport.update()
    
    def set_min_height(self, height: int):
        """
        设置最小高度
        
        Args:
            height: 最小高度值（像素）
        """
        self.MIN_HEIGHT = height
        self._list_view.setMinimumHeight(height)
        self.updateGeometry()
