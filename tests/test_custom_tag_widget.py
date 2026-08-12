import os
import unittest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PyQt6.QtWidgets import QApplication

from ui.widgets.custom_tag_widget import CustomTagWidget


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


if __name__ == "__main__":
    unittest.main()
