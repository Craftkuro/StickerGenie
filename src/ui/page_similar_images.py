# coding=utf-8
from __future__ import annotations

import logging
from pathlib import Path

import apppath
from PyQt6.QtCore import Qt
from PyQt6.QtGui import QIcon
from PyQt6.QtWidgets import QMenu, QToolButton, QWidgetAction

import services.global_instances
import services.similarity_result_filter as similarity_filter
from services.similarity_result_filter import (
    SimilarityFilterConfig,
    SimilarityResultFilter,
)

from .page_finite_sticker_collection import FiniteStickerCollectionPage
from .widgets.filter_popup_widget import SimilarityFilterPopupWidget

logger = logging.getLogger(__name__)


def _resolve_resource_path(filename: str) -> Path:
    if apppath.app_path is not None:
        return apppath.app_path / "resources" / filename
    return Path(__file__).resolve().parents[1] / "resources" / filename


class SimilarImagesPage(FiniteStickerCollectionPage):
    """Similar-image tab: finite result set with similarity data.

    Raw vector query results are cached on the page instance. The toolbar
    filter button lets the user toggle filtering and adjust parameters
    without re-querying; the model is rebuilt from cached data.
    """

    def __init__(self, *, auto_refresh: bool = False):
        super().__init__(auto_refresh=auto_refresh)

        self._cached_search_results: list | None = None
        self._cached_sticker_map: dict[int, object] | None = None

        self._filter_enabled: bool = True
        self._filter_config: SimilarityFilterConfig | None = None

        self._setup_filter_popup()

    def set_similar_data(
        self, search_results: list, sticker_map: dict[int, object]
    ) -> None:
        self._cached_search_results = list(search_results)
        self._cached_sticker_map = dict(sticker_map)

    def set_filter_config(
        self, enabled: bool, config: SimilarityFilterConfig
    ) -> None:
        self._filter_enabled = enabled
        self._filter_config = config
        if hasattr(self, "_filter_popup"):
            self._filter_popup.update_state(enabled, config)

    def apply_filter_and_refresh(self) -> None:
        if self._cached_search_results is None or self._cached_sticker_map is None:
            return

        from services.sticker_library_viewer_service import (
            build_similar_matches,
            build_sticker_model,
        )

        result_filter = None
        if self._filter_enabled and self._filter_config is not None:
            result_filter = SimilarityResultFilter(self._filter_config)

        matches = build_similar_matches(
            self._cached_search_results,
            self._cached_sticker_map,
            result_filter=result_filter,
        )
        similarities = {
            sticker.id: similarity for sticker, similarity in matches
        }
        model = build_sticker_model(
            (sticker for sticker, _ in matches),
            similarities,
        )
        self.refresh_content(model)

    def _setup_filter_popup(self) -> None:
        settings_manager = services.global_instances.current_settings_manager
        if settings_manager is None:
            initial_config = SimilarityFilterConfig()
        else:
            initial_config = similarity_filter.create_filter_from_settings(
                settings_manager
            ).config

        self._filter_enabled = True
        self._filter_config = initial_config

        self._filter_popup = SimilarityFilterPopupWidget(
            initial_config=initial_config,
            initial_enabled=True,
            parent=self,
        )
        self._filter_menu = QMenu(self)
        self._filter_menu.setObjectName("filterPopupMenu")
        action = QWidgetAction(self._filter_menu)
        action.setDefaultWidget(self._filter_popup)
        self._filter_menu.addAction(action)

        self._filter_button = QToolButton(self)
        self._filter_button.setObjectName("filterButton")
        self._filter_button.setToolTip("过滤")
        self._filter_button.setAccessibleName("过滤")
        self._filter_button.setIcon(
            QIcon(str(_resolve_resource_path("funnel.svg")))
        )
        self._filter_button.setMenu(self._filter_menu)
        self._filter_button.setPopupMode(
            QToolButton.ToolButtonPopupMode.InstantPopup
        )
        self._filter_button.setToolButtonStyle(
            Qt.ToolButtonStyle.ToolButtonIconOnly
        )

        spacer_action = self.toolbar.actions()[0]
        self.toolbar.insertWidget(spacer_action, self._filter_button)

        self._filter_popup.filter_applied.connect(self._on_filter_applied)

    def _on_filter_applied(
        self, enabled: bool, config: SimilarityFilterConfig
    ) -> None:
        self._filter_enabled = enabled
        self._filter_config = config
        self._filter_menu.close()
        self.apply_filter_and_refresh()
