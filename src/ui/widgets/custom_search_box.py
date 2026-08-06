import logging
import time

from PyQt6.QtCore import pyqtSignal, pyqtSlot, Qt, QSize, QSignalBlocker, QModelIndex, QEvent
from PyQt6.QtWidgets import QPushButton, QWidget, QLabel, QVBoxLayout, \
    QHBoxLayout, QListWidget, QListWidgetItem, QFrame, QLineEdit, QCompleter, \
    QStyledItemDelegate, QStyleOptionViewItem, QStyle
from PyQt6.QtGui import QFont, QPainter, QStandardItemModel, QStandardItem

logger = logging.getLogger(__name__)


class RichCompleter(QCompleter):
    """
    自定义 QCompleter子类，用于支持富媒体下拉列表（标题+副标题）
    解决了 setModel() 会重置 itemDelegate 的问题
    """

    ###  Notes  #################
    """
    # 在 CustomSearchBox 创建后设置提供者
search_box = CustomSearchBox()

# 方式1：使用异步数据库查询
def db_suggestions_provider(text):
    # 显示加载状态
    search_box.updateSuggestions([("正在加载...", "请稍候")])
    # 异步查询数据库，查询完成后调用 updateSuggestions 更新结果
    async_query_db(text, lambda results: search_box.updateSuggestions(results))

search_box.setSuggestionsProvider(db_suggestions_provider)

# 方式2：直接调用 updateSuggestions 更新（数据库已完成查询时）
search_box.updateSuggestions([("结果1", "副标题1"), ("结果2", "副标题2")])

    """

    def __init__(self, parent=None):
        super().__init__(parent)
        self._custom_item_delegate = None
        self.setCompletionMode(QCompleter.CompletionMode.UnfilteredPopupCompletion)
        self._configure_popup()

    def _configure_popup(self):
        popup = self.popup()
        if not popup:
            return

        # 让 popup 在鼠标移动时向 delegate 传递 State_MouseOver 状态。
        popup.setMouseTracking(True)
        popup.viewport().setMouseTracking(True)
        popup.viewport().setAttribute(Qt.WidgetAttribute.WA_Hover, True)

        if self._custom_item_delegate:
            popup.setItemDelegate(self._custom_item_delegate)

    def setItemDelegate(self, delegate):
        """设置自定义委托"""
        self._custom_item_delegate = delegate
        self._configure_popup()

    def setModel(self, model):
        """重写 setModel，在设置模型后恢复自定义委托"""
        super().setModel(model)
        # 设置模型后会触发 setPopup，需要重新设置委托
        self._configure_popup()

    def complete(self):
        """重写 complete，在显示 popup 前恢复自定义委托"""
        self._configure_popup()
        super().complete()


class RichSearchCompleterItemDelegate(QStyledItemDelegate):
    """QCompleter 的自定义委托，用于在下拉列表中渲染两行文本（标题+副标题）"""

    def __init__(self, parent=None):
        super().__init__(parent)

    def paint(self, painter: QPainter, option: QStyleOptionViewItem, index):
        # 获取存储的数据
        title = index.data(Qt.ItemDataRole.UserRole + 1)
        subtitle = index.data(Qt.ItemDataRole.UserRole + 2)

        if not title:
            super().paint(painter, option, index)
            return

        is_selected = bool(option.state & QStyle.StateFlag.State_Selected)
        is_hovered = bool(option.state & QStyle.StateFlag.State_MouseOver)
        is_highlighted = is_selected or is_hovered

        # 绘制背景（选中/鼠标悬停状态）
        if is_highlighted:
            painter.fillRect(option.rect, option.palette.highlight())

        # 计算文本区域
        rect = option.rect.adjusted(5, 5, -5, -5)

        # 绘制标题（粗体）
        font_title = QFont()
        font_title.setBold(True)
        title_rect = rect.adjusted(0, 0, 0, -rect.height() // 2)
        painter.setFont(font_title)
        text_color = option.palette.highlightedText().color() if is_highlighted else option.palette.text().color()
        painter.setPen(text_color)
        painter.drawText(title_rect, Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter, title)

        # 绘制副标题（斜体）
        if subtitle:
            font_subtitle = QFont()
            font_subtitle.setItalic(True)
            subtitle_rect = rect.adjusted(0, rect.height() // 2, 0, 0)
            painter.setFont(font_subtitle)
            painter.drawText(subtitle_rect, Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter, subtitle)

    def sizeHint(self, option: QStyleOptionViewItem, index):
        return QSize(200, 50)





class CustomSearchBox(QWidget):
    """
    一个带有富媒体推荐下拉菜单的自定义搜索框
    使用 QCompleter 实现自动补全，不干扰键盘输入焦点
    """
    # 当用户确认搜索时（回车或点击按钮），发射此信号
    searched = pyqtSignal(str)

    def __init__(self, parent=None):
        super().__init__(parent)

        # --- 数据模型 ---
        # 硬编码的最近搜索记录
        self.recent_searches = ["萝卜🥕", "纸巾🧻", "真棒👍"]
        # 硬编码的预设标签（知名城市）
        self.predefined_tags = ["北京", "上海", "广州", "深圳", "成都", "杭州", "南京", "武汉", "西安", "重庆"]

        # --- UI 组件 ---
        layout = QHBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(0)

        self.line_edit = QLineEdit(self)
        self.line_edit.setPlaceholderText("搜索...")

        self.search_button = QPushButton(self)
        # 使用 Unicode 字符作为搜索图标
        self.search_button.setText("🔍")
        self.search_button.setCursor(Qt.CursorShape.PointingHandCursor)

        layout.addWidget(self.line_edit)
        layout.addWidget(self.search_button)

        # --- QCompleter 设置 ---
        # 创建模型存储候选项（标题, 副标题）
        self.completer_model = QStandardItemModel()
        # 区分“明确选择候选项”和“popup 自动高亮第一项”；直接回车时应搜索输入框原文。
        self._completion_selection_explicit = False

        # 创建 RichCompleter（自定义 QCompleter 子类，解决委托被重置的问题）
        self.completer = RichCompleter(self)
        self.completer.setModel(self.completer_model)  # 先设置模型
        self.completer.setCaseSensitivity(Qt.CaseSensitivity.CaseInsensitive)
        self.completer.setFilterMode(Qt.MatchFlag.MatchContains)  # 包含匹配
        self.completer.setWrapAround(False)  # 不循环选择

        # 设置自定义委托（RichCompleter 会自动在 setModel 和 complete 时保持委托）
        delegate = RichSearchCompleterItemDelegate(self.completer.popup())
        self.completer.setItemDelegate(delegate)

        # 将 completer 设置到 lineEdit
        self.line_edit.setCompleter(self.completer)
        self._install_completion_event_filters()

        # 连接信号
        self.search_button.clicked.connect(self._trigger_search)
        self.line_edit.returnPressed.connect(self._on_return_pressed)

        # QCompleter 的 activated 信号当用户选择一项时触发
        self.completer.activated[QModelIndex].connect(self._on_completer_activated)

        # 当文本改变时调用提供者的查询函数
        self.line_edit.textEdited.connect(self._on_search_text_changed)

        # 设置默认的候选项提供者（硬编码实现）
        self.setSuggestionsProvider(self._default_suggestions_provider)

    def _install_completion_event_filters(self):
        """监听用户是否明确导航/点击了 completer 候选项。"""
        self.line_edit.installEventFilter(self)

        popup = self.completer.popup()
        if popup:
            popup.installEventFilter(self)
            popup.viewport().installEventFilter(self)

    def setSuggestionsProvider(self, provider_func):
        """
        设置自定义的候选项提供者函数

        provider_func(text: str) -> list of (title, subtitle) tuples
        返回空列表表示无候选项
        """
        self._suggestions_provider = provider_func

    def _default_suggestions_provider(self, text: str):
        """默认的候选项提供者（硬编码实现）"""
        logger.debug("default suggest provider")
        suggestions = []

        # 如果没有输入，显示最近搜索
        if not text:
            for term in self.recent_searches:
                suggestions.append((term, "最近搜索"))
        else:
            # 输入不为空，先显示匹配的最近搜索
            for term in self.recent_searches:
                suggestions.append((term, "最近搜索"))

            # 再添加匹配的预设标签
            for tag in self.predefined_tags:
                if text in tag and tag not in [s[0] for s in suggestions]:
                    suggestions.append((tag, "预设标签"))

        return suggestions

    def _show_loading_state(self):
        """显示加载状态"""
        self.completer_model.clear()
        item = QStandardItem("正在加载...")
        item.setData("正在加载...", Qt.ItemDataRole.UserRole + 1)
        item.setData("请稍候", Qt.ItemDataRole.UserRole + 2)
        self.completer_model.appendRow(item)

    @pyqtSlot(str)
    def updateSuggestions(self, suggestions):
        """
        公共槽函数，用于外部模块（如数据库查询完成后）更新候选项

        suggestions: list of (title, subtitle) tuples
        """
        self.completer_model.clear()
        for title, subtitle in suggestions:
            item = QStandardItem(title)
            item.setData(title, Qt.ItemDataRole.UserRole + 1)
            item.setData(subtitle, Qt.ItemDataRole.UserRole + 2)
            self.completer_model.appendRow(item)

    def _on_search_text_changed(self, text: str):
        """当搜索文本改变时，调用提供者获取候选项"""
        # 输入变化后，之前的候选导航状态失效，避免回车误用旧的高亮候选项。
        self._completion_selection_explicit = False

        if self._suggestions_provider:
            # 显示加载状态
            #self._show_loading_state()
            #time.sleep(0.5)
            # 调用提供者获取候选项
            suggestions = self._suggestions_provider(text)
            self.updateSuggestions(suggestions)

    def _completion_text_from_index(self, index: QModelIndex):
        """从 completer 激活的模型索引读取真实展示的候选标题。"""
        if not index.isValid():
            return ""

        title = index.data(Qt.ItemDataRole.UserRole + 1) or index.data(Qt.ItemDataRole.DisplayRole)
        return str(title).strip() if title else ""

    def _on_completer_activated(self, index: QModelIndex):
        """当用户从 completer popup 中选择一项时调用"""
        # QCompleter 会自动高亮首个候选项；没有方向键/鼠标选择时忽略 activated。
        if not self._completion_selection_explicit:
            return

        completion_text = self._completion_text_from_index(index) or self.line_edit.text()
        self._search_for_text(completion_text)
        self._completion_selection_explicit = False

    def _on_return_pressed(self):
        """处理回车；popup 打开时先交给 eventFilter 判断是否使用候选项。"""
        popup = self.completer.popup()
        if popup and popup.isVisible():
            return

        self._trigger_search()

    def _trigger_search(self):
        """执行搜索操作"""
        text = self.line_edit.text().strip()
        self._search_for_text(text)

    def _search_for_text(self, text: str):
        """按指定文本执行搜索，避免补全激活时读取到输入框里的旧文本。"""
        text = text.strip()
        if not text:
            return

        # 将新搜索词添加到历史记录（如果不存在）
        if text not in self.recent_searches:
            self.recent_searches.insert(0, text)
            # 保持历史记录列表的大小，例如最多 10 条
            self.recent_searches = self.recent_searches[:10]

        # 清空输入框；搜索完成后的程序化清空不应再次刷新 completer 模型。
        #with QSignalBlocker(self.line_edit):
        #    self.line_edit.clear()

        self.searched.emit(text)
        logger.info(f"信号已发射：搜索 '{text}'")

    def eventFilter(self, obj, event):
        popup = self.completer.popup()
        popup_visible = popup and popup.isVisible()

        if popup_visible and event.type() == QEvent.Type.KeyPress:
            key = event.key()
            navigation_keys = {
                Qt.Key.Key_Up,
                Qt.Key.Key_Down,
                Qt.Key.Key_PageUp,
                Qt.Key.Key_PageDown,
                Qt.Key.Key_Home,
                Qt.Key.Key_End,
            }

            if key in navigation_keys:
                # 只有用户主动导航候选列表后，回车才按当前候选项搜索。
                self._completion_selection_explicit = True
            elif key in (Qt.Key.Key_Return, Qt.Key.Key_Enter) and not self._completion_selection_explicit:
                # popup 可见但未明确选候选项时，拦截 completer 默认激活并搜索原输入。
                popup.hide()
                self._trigger_search()
                return True

        if popup_visible and event.type() == QEvent.Type.MouseButtonPress:
            if obj in (popup, popup.viewport()):
                # 鼠标点击候选项属于明确选择，允许 activated 使用候选文本。
                self._completion_selection_explicit = True

        return super().eventFilter(obj, event)
