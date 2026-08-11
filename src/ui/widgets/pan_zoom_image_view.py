# coding=utf-8
from PyQt6.QtCore import QRectF, Qt
from PyQt6.QtGui import QPainter, QPixmap, QTransform
from PyQt6.QtWidgets import (
    QGraphicsPixmapItem,
    QGraphicsScene,
    QGraphicsView,
)


class PanZoomImageView(QGraphicsView):
    """
    Image viewer that fits images to the viewport and supports wheel zoom
    plus mouse-drag panning once the image is larger than the viewport.
    """

    MAX_ZOOM_FACTOR = 8.0
    WHEEL_ZOOM_STEP = 1.15
    _FIT_EPSILON = 1e-9
    _PAN_TOLERANCE = 1

    def __init__(self, parent=None):
        super().__init__(parent)
        self._scene = QGraphicsScene(self)
        self._image_item = QGraphicsPixmapItem()
        self._image_item.setTransformationMode(
            Qt.TransformationMode.SmoothTransformation
        )
        self._scene.addItem(self._image_item)
        self.setScene(self._scene)
        self.setRenderHints(QPainter.RenderHint.SmoothPixmapTransform)
        self.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.setDragMode(QGraphicsView.DragMode.ScrollHandDrag)
        self.setTransformationAnchor(QGraphicsView.ViewportAnchor.NoAnchor)
        self.setResizeAnchor(QGraphicsView.ViewportAnchor.NoAnchor)

        self._fit_scale = 0.0
        self._zoom_factor = 1.0
        self._last_widget_size = self.size()

    def set_image(self, pixmap: QPixmap) -> None:
        """Replace the displayed image and return to fit-to-window state."""
        self._image_item.setPixmap(pixmap)
        if pixmap.isNull():
            self._scene.setSceneRect(QRectF())
        else:
            self._scene.setSceneRect(self._image_item.boundingRect())
        self._zoom_factor = 1.0
        self._fit_scale = 0.0
        self._refit()

    def fit_to_window(self) -> None:
        """Reset zoom so the whole image fits inside the viewport."""
        self._zoom_factor = 1.0
        self._refit()

    def reset_zoom(self) -> None:
        self.fit_to_window()

    def zoom_factor(self) -> float:
        """Current zoom relative to the fit-to-window scale (1.0 = fitted)."""
        return self._zoom_factor

    def is_pannable(self) -> bool:
        """Whether the rendered image exceeds the viewport in either axis."""
        if self._image_item.pixmap().isNull():
            return False
        view_rect = self.viewport().rect()
        image_rect = self.mapFromScene(
            self._image_item.sceneBoundingRect()
        ).boundingRect()
        return (
            image_rect.width() > view_rect.width() + self._PAN_TOLERANCE
            or image_rect.height() > view_rect.height() + self._PAN_TOLERANCE
        )

    def wheelEvent(self, event):
        if self._image_item.pixmap().isNull():
            super().wheelEvent(event)
            return

        delta = event.angleDelta().y()
        if delta == 0:
            super().wheelEvent(event)
            return

        fit_scale = self._fit_scale or self.transform().m11()
        current = self.transform().m11()
        target = current * (self.WHEEL_ZOOM_STEP ** (delta / 120.0))
        target = min(max(target, fit_scale), fit_scale * self.MAX_ZOOM_FACTOR)
        if abs(target - current) <= self._FIT_EPSILON:
            event.accept()
            return

        factor = target / current
        pos = event.position().toPoint()
        scene_pos = self.mapToScene(pos)
        self.scale(factor, factor)
        new_scene_pos = self.mapToScene(pos)
        delta = scene_pos - new_scene_pos
        scale_now = self.transform().m11()
        self.horizontalScrollBar().setValue(
            self.horizontalScrollBar().value() + round(delta.x() * scale_now)
        )
        self.verticalScrollBar().setValue(
            self.verticalScrollBar().value() + round(delta.y() * scale_now)
        )
        self._zoom_factor = target / fit_scale
        event.accept()

    def mouseDoubleClickEvent(self, event):
        self.fit_to_window()
        event.accept()

    def mouseMoveEvent(self, event):
        super().mouseMoveEvent(event)
        if not (event.buttons() & Qt.MouseButton.LeftButton):
            self._update_cursor()

    def enterEvent(self, event):
        super().enterEvent(event)
        self._update_cursor()

    def leaveEvent(self, event):
        super().leaveEvent(event)
        self.viewport().unsetCursor()

    def resizeEvent(self, event):
        super().resizeEvent(event)
        # Scrollbars appearing/disappearing also resize the viewport; only refit
        # when the outer widget itself changes size.
        if self.size() == self._last_widget_size:
            return
        self._last_widget_size = self.size()
        self._refit()

    def _refit(self):
        if self._image_item.pixmap().isNull() or self.viewport().rect().isEmpty():
            return

        zoom_factor = self._zoom_factor
        image_rect = self._image_item.sceneBoundingRect()
        if image_rect.isEmpty():
            return
        fit_scale = min(
            self.viewport().width() / image_rect.width(),
            self.viewport().height() / image_rect.height(),
        )
        if fit_scale <= self._FIT_EPSILON:
            self._fit_scale = 0.0
            return
        transform = QTransform()
        transform.scale(fit_scale * zoom_factor, fit_scale * zoom_factor)
        self.setTransform(transform)
        self._fit_scale = fit_scale
        self.centerOn(image_rect.center())

    def _update_cursor(self):
        if self.is_pannable():
            self.viewport().setCursor(Qt.CursorShape.OpenHandCursor)
        else:
            self.viewport().setCursor(Qt.CursorShape.ArrowCursor)
