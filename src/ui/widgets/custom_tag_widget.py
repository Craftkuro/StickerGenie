"""
CustomTagWidget - 自定义标签组件

基于 QListView 的圆角矩形标签展示组件，支持自动换行和垂直滚动
"""

from typing import Optional

from PyQt6.QtCore import Qt, QSize, pyqtSlot
from PyQt6.QtWidgets import QListView, QStyledItemDelegate, QStyleOptionViewItem, QStyle, QSizePolicy
from PyQt6.QtGui import QPainter, QStandardItemModel, QPen, QBrush, QColor


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
        
        # 设置背景颜色
        if is_selected:
            bg_color = self._border_color.lighter(130)
        elif is_hovered:
            bg_color = self._bg_color.lighter(110)
        else:
            bg_color = self._bg_color
            
        # 绘制圆角矩形背景
        painter.setBrush(QBrush(bg_color))
        painter.setPen(QPen(self._border_color, 1))
        painter.drawRoundedRect(tag_rect, self._corner_radius, self._corner_radius)
        
        # 获取文本
        text = index.data(Qt.ItemDataRole.DisplayRole)
        if not text:
            text = index.data(Qt.ItemDataRole.EditRole) or ""
        
        # 绘制文本
        painter.setPen(self._text_color)
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


class CustomTagWidget(QListView):
    """
    自定义标签组件
    
    基于 QListView，展示从左到右排列的圆角矩形标签列表，
    当标签数量超过一行时自动换行显示。
    当组件高度超过 200px 时，不再增加高度，显示垂直滚动条。
    
    Example:
        >>> model = QStandardItemModel()
        >>> model.appendRow(QStandardItem("Tag1"))
        >>> model.appendRow(QStandardItem("Tag2"))
        >>> model.appendRow(QStandardItem("Tag3"))
        >>> 
        >>> tag_widget = CustomTagWidget(model=model)
    """
    
    MAX_HEIGHT = 60
    
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
        self.setItemDelegate(self._item_delegate)
        self.setWrapping(True)
        
        # 配置视图
        self._setup_view()
        
        # 设置模型
        if model is not None:
            self.setModel(model)
    
    def _setup_view(self):
        """
        配置 QListView 的视图属性
        """
        # 设置为流式布局模式（从左到右，自动换行）
        self.setFlow(QListView.Flow.LeftToRight)
        
        # 设置视图的调整模式为 Adjust，允许动态调整
        self.setResizeMode(QListView.ResizeMode.Adjust)
        
        # 隐藏水平滚动条（允许自动换行）
        self.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        
        # 垂直滚动条需要时显示
        self.setVerticalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAsNeeded)
        
        # 设置边框样式
        self.setFrameShape(QListView.Shape.NoFrame)
        
        # 设置最大高度为 200px
        self.setMaximumHeight(self.MAX_HEIGHT)
        
        # 设置尺寸策略
        # 水平方向可以拉伸，垂直方向遵循最大高度限制
        self.setSizePolicy(
            QSizePolicy.Policy.Expanding,
            QSizePolicy.Policy.Maximum
        )
    
    def setModel(self, model: QStandardItemModel):
        """
        重写 setModel，设置模型并连接信号
        
        Args:
            model: QStandardItemModel 实例
        """
        super().setModel(model)
        
        # 连接模型变化信号
        if model:
            model.dataChanged.connect(self._on_model_data_changed)
            model.rowsInserted.connect(self._on_model_rows_changed)
            model.rowsRemoved.connect(self._on_model_rows_changed)
    
    @pyqtSlot()
    def _on_model_data_changed(self):
        """模型数据变化时的处理"""
        self.updateGeometry()
        
    @pyqtSlot()
    def _on_model_rows_changed(self):
        """模型行变化时的处理"""
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
        viewport = self.viewport()
        if viewport is not None:
            viewport.update()
    
    def set_corner_radius(self, radius: int):
        """
        设置圆角半径
        
        Args:
            radius: 圆角半径（像素）
        """
        self._item_delegate._corner_radius = radius
        viewport = self.viewport()
        if viewport is not None:
            viewport.update()
    
    def set_max_height(self, height: int):
        """
        设置最大高度
        
        Args:
            height: 最大高度值（像素）
        """
        self.MAX_HEIGHT = height
        self.setMaximumHeight(height)
        self.updateGeometry()
