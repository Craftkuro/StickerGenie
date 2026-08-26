import os
import unittest
from unittest.mock import patch

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PyQt6.QtCore import QRect, Qt
from PyQt6.QtGui import QImage, QPainter, QStandardItem, QStandardItemModel
from PyQt6.QtWidgets import QApplication, QStyleOptionViewItem

from ui.widgets.custom_tag_widget import CustomTagWidget, TagItemDelegate


class CustomTagWidgetLayoutTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = QApplication.instance() or QApplication([])

    def test_list_uses_minimum_height_instead_of_fixed_maximum(self):
        widget = CustomTagWidget()
        try:
            list_view = widget._list_view
            self.assertEqual(40, list_view.minimumHeight())
            self.assertGreater(list_view.maximumHeight(), 100)
            self.assertEqual(40, widget.MIN_HEIGHT)
        finally:
            widget.close()

    def test_set_min_height_updates_list_minimum(self):
        widget = CustomTagWidget()
        try:
            widget.set_min_height(56)
            self.assertEqual(56, widget._list_view.minimumHeight())
            self.assertEqual(56, widget.MIN_HEIGHT)
        finally:
            widget.close()

    def test_toolbar_uses_svg_icons_at_16x16(self):
        widget = CustomTagWidget()
        try:
            icon_size = widget.toolbar.iconSize()
            self.assertEqual(16, icon_size.width())
            self.assertEqual(16, icon_size.height())
            self.assertFalse(widget.add_action.icon().isNull())
            self.assertFalse(widget.delete_action.icon().isNull())
        finally:
            widget.close()


class TagItemDelegatePaintTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = QApplication.instance() or QApplication([])

    def test_paint_enables_antialiasing_for_rounded_corners(self):
        """高 DPI 下圆角四角对称依赖抗锯齿：绘制期间必须开启该提示。"""
        item = QStandardItem("标签")
        model = QStandardItemModel()
        model.appendRow(item)
        image = QImage(80, 32, QImage.Format.Format_ARGB32)
        image.fill(Qt.GlobalColor.white)

        option = QStyleOptionViewItem()
        option.rect = QRect(0, 0, 80, 32)

        hints_seen = []
        original = QPainter.drawRoundedRect

        def spy(painter, *args, **kwargs):
            hints_seen.append(painter.renderHints())
            return original(painter, *args, **kwargs)

        delegate = TagItemDelegate()
        painter = QPainter(image)
        try:
            with patch.object(QPainter, "drawRoundedRect", spy):
                delegate.paint(painter, option, model.index(0, 0))
        finally:
            painter.end()

        self.assertTrue(hints_seen)
        self.assertTrue(
            all(
                hint & QPainter.RenderHint.Antialiasing
                for hint in hints_seen
            )
        )


if __name__ == "__main__":
    unittest.main()
