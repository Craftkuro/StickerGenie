import os
import unittest
from pathlib import Path

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PyQt6.QtCore import QEvent, QPoint, QPointF, Qt
from PyQt6.QtGui import QImage, QMouseEvent, QPainter, QPixmap, QWheelEvent
from PyQt6.QtTest import QTest
from PyQt6.QtWidgets import QApplication

import apppath
from ui.widgets.pan_zoom_image_view import PanZoomImageView


def make_pixmap(width=800, height=400) -> QPixmap:
    image = QImage(width, height, QImage.Format.Format_RGB32)
    image.fill(0xFFFFFFFF)
    return QPixmap.fromImage(image)


class PanZoomImageViewTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = QApplication.instance() or QApplication([])
        source_dir = Path(__file__).resolve().parents[1] / "src"
        apppath.setup_data_path(source_dir)

    def _make_view(self, width=400, height=300):
        view = PanZoomImageView()
        view.resize(width, height)
        view.show()
        QApplication.processEvents()
        self.addCleanup(view.close)
        return view

    def _set_image(self, view, width=800, height=400):
        view.set_image(make_pixmap(width, height))
        QApplication.processEvents()

    def _send_wheel(self, view, pos, delta):
        event = QWheelEvent(
            QPointF(pos),
            QPointF(view.viewport().mapToGlobal(pos)),
            QPoint(0, delta),
            QPoint(0, delta),
            Qt.MouseButton.NoButton,
            Qt.KeyboardModifier.NoModifier,
            Qt.ScrollPhase.NoScrollPhase,
            False,
        )
        QApplication.sendEvent(view.viewport(), event)

    def _drag(self, view, start, end):
        QTest.mousePress(
            view.viewport(),
            Qt.MouseButton.LeftButton,
            pos=start,
        )
        move = QMouseEvent(
            QEvent.Type.MouseMove,
            QPointF(end),
            QPointF(view.viewport().mapToGlobal(end)),
            Qt.MouseButton.NoButton,
            Qt.MouseButton.LeftButton,
            Qt.KeyboardModifier.NoModifier,
        )
        QApplication.sendEvent(view.viewport(), move)
        QTest.mouseRelease(
            view.viewport(),
            Qt.MouseButton.LeftButton,
            pos=end,
        )
        QApplication.processEvents()

    def test_set_image_fits_to_window(self):
        view = self._make_view()
        self._set_image(view)

        expected = min(
            view.viewport().width() / 800,
            view.viewport().height() / 400,
        )
        self.assertAlmostEqual(expected, view.transform().m11(), places=6)
        self.assertAlmostEqual(1.0, view.zoom_factor(), places=6)
        self.assertFalse(view.is_pannable())

    def test_uses_smooth_rendering(self):
        view = self._make_view()
        self.assertEqual(
            Qt.TransformationMode.SmoothTransformation,
            view._image_item.transformationMode(),
        )
        self.assertTrue(
            view.renderHints() & QPainter.RenderHint.SmoothPixmapTransform
        )

    def test_wheel_zoom_in_and_out(self):
        view = self._make_view()
        self._set_image(view)
        fit_scale = view.transform().m11()
        center = view.viewport().rect().center()

        self._send_wheel(view, center, 120)

        self.assertGreater(view.transform().m11(), fit_scale)
        self.assertGreater(view.zoom_factor(), 1.0)

        self._send_wheel(view, center, -120)

        self.assertAlmostEqual(1.0, view.zoom_factor(), places=6)
        self.assertAlmostEqual(fit_scale, view.transform().m11(), places=6)

    def test_wheel_zoom_keeps_cursor_scene_point_stable(self):
        view = self._make_view()
        self._set_image(view)
        # Keep the anchor away from the image edge; near the top/bottom the
        # scrollbar range clamps and Qt cannot keep the exact scene point.
        pos = QPoint(200, 150)
        before = view.mapToScene(pos)

        self._send_wheel(view, pos, 480)

        after = view.mapToScene(pos)
        self.assertLess((before - after).manhattanLength(), 2.0)

    def test_wheel_zoom_clamps_at_fit_and_max(self):
        view = self._make_view()
        self._set_image(view)
        center = view.viewport().rect().center()

        for _ in range(30):
            self._send_wheel(view, center, -120)
        self.assertAlmostEqual(1.0, view.zoom_factor(), places=6)

        fit_scale = view.transform().m11()
        for _ in range(60):
            self._send_wheel(view, center, 120)

        self.assertAlmostEqual(
            PanZoomImageView.MAX_ZOOM_FACTOR,
            view.zoom_factor(),
            places=6,
        )
        self.assertAlmostEqual(
            fit_scale * PanZoomImageView.MAX_ZOOM_FACTOR,
            view.transform().m11(),
            places=6,
        )

    def test_is_pannable_reflects_overflow(self):
        view = self._make_view()
        self._set_image(view)
        self.assertFalse(view.is_pannable())

        self._send_wheel(view, view.viewport().rect().center(), 120)

        self.assertTrue(view.is_pannable())

    def test_drag_pans_only_when_pannable(self):
        view = self._make_view()
        self._set_image(view)
        horizontal = view.horizontalScrollBar()
        vertical = view.verticalScrollBar()

        self._drag(view, QPoint(200, 150), QPoint(150, 100))
        self.assertEqual(0, horizontal.value())
        self.assertEqual(0, vertical.value())

        self._send_wheel(view, view.viewport().rect().center(), 240)
        self.assertTrue(view.is_pannable())
        before_drag = horizontal.value()

        QTest.mouseMove(view.viewport(), QPoint(250, 200))
        QApplication.processEvents()
        self._drag(view, QPoint(200, 150), QPoint(150, 100))
        self.assertNotEqual(before_drag, horizontal.value())

    def test_fit_to_window_resets_zoom(self):
        view = self._make_view()
        self._set_image(view)
        self._send_wheel(view, view.viewport().rect().center(), 240)
        self.assertGreater(view.zoom_factor(), 1.0)

        view.fit_to_window()

        self.assertAlmostEqual(1.0, view.zoom_factor(), places=6)
        self.assertFalse(view.is_pannable())

    def test_double_click_resets_zoom(self):
        view = self._make_view()
        self._set_image(view)
        self._send_wheel(view, view.viewport().rect().center(), 240)
        self.assertGreater(view.zoom_factor(), 1.0)

        QTest.mouseDClick(
            view.viewport(),
            Qt.MouseButton.LeftButton,
            pos=QPoint(200, 150),
        )
        QApplication.processEvents()

        self.assertAlmostEqual(1.0, view.zoom_factor(), places=6)

    def test_set_image_resets_zoom(self):
        view = self._make_view()
        self._set_image(view)
        self._send_wheel(view, view.viewport().rect().center(), 240)
        self.assertGreater(view.zoom_factor(), 1.0)

        self._set_image(view, 200, 100)

        self.assertAlmostEqual(1.0, view.zoom_factor(), places=6)
        self.assertFalse(view.is_pannable())

    def test_resize_preserves_zoom_factor(self):
        view = self._make_view()
        self._set_image(view)
        self._send_wheel(view, view.viewport().rect().center(), 240)
        factor = view.zoom_factor()
        scale_before = view.transform().m11()

        view.resize(520, 420)
        QApplication.processEvents()

        self.assertAlmostEqual(factor, view.zoom_factor(), places=6)
        self.assertNotAlmostEqual(scale_before, view.transform().m11(), places=6)


if __name__ == "__main__":
    unittest.main()
